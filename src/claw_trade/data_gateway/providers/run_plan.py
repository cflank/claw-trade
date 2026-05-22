from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from typing import Callable, Iterable, Mapping, Protocol

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    DataGap,
    DataGapReason,
    GapSeverity,
    Market,
    PackDomain,
    PrioritySource,
    ProviderCallSpec,
    RateLimitPlanItem,
    RunProviderPlan,
    utc_now_iso,
)
from claw_trade.data_gateway.providers.market_adapters import (
    _crypto_technical_start_date,
    _normalize_crypto_symbol_for_openbb,
    normalize_hk_symbol_for_stock_hk_daily,
)
from claw_trade.data_gateway.providers.registry import ProviderRegistry
from claw_trade.workflow.models import RunRequest, WorkflowEntryPoint


class RunProviderPlanStoreLike(Protocol):
    def write(self, plan: RunProviderPlan) -> str: ...

    def load(self, run_id: str) -> RunProviderPlan: ...


@dataclass(frozen=True)
class RateLimitPlanMetadata:
    window_seconds: int = 60
    estimated_cost: int = 1
    hard_reserved: bool = False

    def __post_init__(self) -> None:
        if self.window_seconds <= 0:
            raise ValueError("window_seconds must be > 0")
        if self.estimated_cost <= 0:
            raise ValueError("estimated_cost must be > 0")


class PlannerAdapterLike(Protocol):
    adapter_id: str
    provider_id: str

    def validate_credentials(self): ...


@dataclass
class RunProviderPlanner:
    adapters_by_id: Mapping[str, PlannerAdapterLike] = field(default_factory=dict)
    rate_limit_metadata: Mapping[str, RateLimitPlanMetadata] = field(default_factory=dict)
    now_text: Callable[[], str] = utc_now_iso

    def build_run_plan(
        self,
        *,
        run_id: str,
        market: Market,
        ticker: str,
        company_name: str,
        currency: str,
        profile: str,
        current_date: str,
        start_date: str,
        end_date: str,
        domains: tuple[PackDomain, ...],
        registry: ProviderRegistry,
        provider_config_version: str,
    ) -> RunProviderPlan:
        call_specs: list[ProviderCallSpec] = []
        initial_gaps: list[DataGap] = []

        for domain in domains:
            capabilities = registry.capabilities_for(market=market, domain=domain)
            if not capabilities:
                initial_gaps.append(
                    DataGap(
                        gap_id=f"{run_id}:{domain.value}:source_not_configured",
                        domain=domain,
                        severity=GapSeverity.WARN,
                        reason=DataGapReason.SOURCE_NOT_CONFIGURED,
                        field_path=domain.value,
                        provider_candidates=(),
                        attempt_ids=(),
                        root_cause=f"{domain.value} capabilities missing in registry",
                        next_action="configure provider capabilities",
                    )
                )
                continue

            for capability in capabilities:
                call_key = _call_key(capability.domain, capability.adapter_id, capability.endpoint)
                call_specs.append(
                    ProviderCallSpec(
                        call_key=call_key,
                        provider=capability.provider,
                        adapter_id=capability.adapter_id,
                        provider_kind=capability.provider_kind,
                        provider_config_version=provider_config_version,
                        endpoint=capability.endpoint,
                        source_role=capability.source_role,
                        market=market,
                        domain=domain,
                        required=capability.required,
                        attempt_required=capability.attempt_required,
                        coverage_group=capability.coverage_group,
                        coverage_quorum=capability.coverage_quorum,
                        params=_provider_params(
                            market=market,
                            domain=domain,
                            endpoint=capability.endpoint,
                            ticker=ticker,
                            company_name=company_name,
                            currency=currency,
                            profile=profile,
                            start_date=start_date,
                            end_date=end_date,
                            current_date=current_date,
                        ),
                        cache_ttl_seconds=capability.cache_ttl_seconds,
                        license_policy_id=capability.license_policy_id,
                        expected_schema_id=capability.expected_schema_id,
                        priority=capability.priority,
                        priority_source=capability.priority_source,
                        user_preferred=capability.priority_source == PrioritySource.USER_PREFERRED,
                        raw_export_policy=capability.raw_export_policy,
                    )
                )

                adapter = self.adapters_by_id.get(capability.adapter_id)
                if adapter is None:
                    continue
                credential = adapter.validate_credentials()
                if getattr(credential, "status", None) != AdmissionCheckStatus.MISSING:
                    continue
                missing_keys = tuple(getattr(credential, "missing_keys", ()))
                detail = ", ".join(missing_keys) if missing_keys else "unknown key"
                initial_gaps.append(
                    DataGap(
                        gap_id=f"{run_id}:{domain.value}:{capability.adapter_id}:credential_missing",
                        domain=domain,
                        severity=GapSeverity.FAIL,
                        reason=DataGapReason.CREDENTIAL_MISSING,
                        field_path="credentials",
                        provider_candidates=(capability.provider,),
                        attempt_ids=(),
                        root_cause=f"{capability.adapter_id} missing credentials: {detail}",
                        next_action="configure required credential keys",
                    )
                )

        shared_call_keys = tuple(_dedup(spec.call_key for spec in call_specs))
        cache_keys = tuple(
            _cache_key(
                provider_config_version=provider_config_version,
                market=market,
                spec=spec,
                ticker=ticker,
                start_date=start_date,
                end_date=end_date,
                current_date=current_date,
            )
            for spec in call_specs
        )
        rate_limit_plan = tuple(self._rate_limit_item(spec) for spec in call_specs)

        return RunProviderPlan(
            run_id=run_id,
            provider_config_version=provider_config_version,
            market=market,
            ticker=ticker,
            domains=domains,
            call_specs=tuple(call_specs),
            shared_call_keys=shared_call_keys,
            cache_keys=cache_keys,
            rate_limit_plan=rate_limit_plan,
            initial_gaps=tuple(initial_gaps),
            generated_at=self.now_text(),
            remote_prefetch_allowed=False,
        )

    def _rate_limit_item(self, spec: ProviderCallSpec) -> RateLimitPlanItem:
        metadata = self.rate_limit_metadata.get(spec.call_key)
        if metadata is None:
            metadata = self.rate_limit_metadata.get(spec.endpoint)
        if metadata is None:
            metadata = self.rate_limit_metadata.get(spec.provider)
        if metadata is None:
            metadata = self.rate_limit_metadata.get("default", RateLimitPlanMetadata())
        return RateLimitPlanItem(
            provider=spec.provider,
            endpoint=spec.endpoint,
            call_key=spec.call_key,
            window_seconds=metadata.window_seconds,
            estimated_cost=metadata.estimated_cost,
            hard_reserved=metadata.hard_reserved,
        )


def build_report_run_plan(
    *,
    request: RunRequest,
    run_id: str,
    provider_config_version: str,
    planner: RunProviderPlanner,
    registry: ProviderRegistry,
) -> RunProviderPlan | None:
    if request.entry_point != WorkflowEntryPoint.REPORT_COMMAND:
        return None
    market = Market(request.market)
    return planner.build_run_plan(
        run_id=run_id,
        market=market,
        ticker=request.ticker,
        company_name=request.company_name,
        currency=request.currency,
        profile=request.profile,
        current_date=request.current_date,
        start_date=request.start_date,
        end_date=request.end_date,
        domains=report_run_plan_domains(market),
        registry=registry,
        provider_config_version=provider_config_version,
    )


def report_run_plan_domains(market: Market) -> tuple[PackDomain, ...]:
    if market == Market.CN_A:
        return (
            PackDomain.MARKET,
            PackDomain.FUNDAMENTAL,
            PackDomain.NEWS,
            PackDomain.SOCIAL,
            PackDomain.POLICY,
            PackDomain.HOT_MONEY,
            PackDomain.LOCKUP,
        )
    return (
        PackDomain.MARKET,
        PackDomain.FUNDAMENTAL,
        PackDomain.NEWS,
        PackDomain.SOCIAL,
    )


def load_plan_for_pack_runtime(
    *,
    run_id: str,
    provider_config_version: str,
    store: RunProviderPlanStoreLike,
) -> RunProviderPlan:
    plan = store.load(run_id)
    if plan.provider_config_version != provider_config_version:
        raise DataGatewayError(
            DataGatewayErrorCode.CONFIG_VERSION_MISMATCH,
            f"run_id={run_id} provider_config_version mismatch: {plan.provider_config_version} != {provider_config_version}",
        )
    if plan.remote_prefetch_allowed:
        raise DataGatewayError(
            DataGatewayErrorCode.CONFIG_VERSION_MISMATCH,
            f"run_id={run_id} remote_prefetch_allowed must be false",
        )
    return plan


def _call_key(domain: PackDomain, adapter_id: str, endpoint: str) -> str:
    return f"{domain.value}:{adapter_id}:{endpoint}"


def _provider_params(
    *,
    market: Market,
    domain: PackDomain,
    endpoint: str,
    ticker: str,
    company_name: str,
    currency: str,
    profile: str,
    start_date: str,
    end_date: str,
    current_date: str,
) -> dict[str, str]:
    params = {
        "ticker": ticker,
        "company_name": company_name,
        "currency": currency,
        "profile": profile,
        "start_date": start_date,
        "end_date": end_date,
        "current_date": current_date,
    }
    if market == Market.HK and domain == PackDomain.MARKET and endpoint == "stock_hk_daily":
        params.update(
            {
                "symbol": normalize_hk_symbol_for_stock_hk_daily(ticker),
                "adjust": "qfq",
                "interval": "daily",
                "currency": "HKD",
                "timezone": "Asia/Hong_Kong",
            }
        )
    if market == Market.CRYPTO and domain == PackDomain.MARKET:
        params["symbol"] = _normalize_crypto_symbol_for_openbb(ticker)
        params["timezone"] = "UTC"
        if endpoint == "crypto_price_historical":
            technical_start_date = _crypto_technical_start_date(start_date, end_date)
            if technical_start_date != start_date:
                params["requested_start_date"] = start_date
                params["start_date"] = technical_start_date
                params["technical_lookback_reason"] = "vegas_purple_band_ema676_daily"
    return params


def _cache_key(
    *,
    provider_config_version: str,
    market: Market,
    spec: ProviderCallSpec,
    ticker: str,
    start_date: str,
    end_date: str,
    current_date: str,
) -> str:
    raw = "|".join(
        (
            provider_config_version,
            market.value,
            spec.domain.value,
            spec.adapter_id,
            spec.endpoint,
            ticker,
            start_date,
            end_date,
            current_date,
        )
    )
    return "sha256:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _dedup(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out
