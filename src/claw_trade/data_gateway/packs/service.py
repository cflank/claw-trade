from __future__ import annotations

from dataclasses import dataclass, field
import time
from typing import Any, Mapping

from pymongo import MongoClient

from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    DataGap,
    DataGapReason,
    DomainPackResult,
    FreshnessStatus,
    GapSeverity,
    PackDomain,
    PackRequest,
    ProviderAttempt,
    ProviderCallSpec,
    ProviderKind,
    ProviderResult,
    ProviderStatus,
    RunProviderPlan,
    utc_now_iso,
)
from claw_trade.data_gateway.packs.fundamental import FundamentalPackService
from claw_trade.data_gateway.packs.market import MarketPackBuilder
from claw_trade.data_gateway.packs.news import NewsPackBuilder
from claw_trade.data_gateway.packs.social import SocialPackBuilder
from claw_trade.data_gateway.providers.base import ProviderAdapter
from claw_trade.data_gateway.providers.execution import ProviderExecutionEvidenceHelper
from claw_trade.data_gateway.store.attempts import MongoAttemptStore
from claw_trade.data_gateway.store.http_evidence import MongoProviderHttpEvidenceStore
from claw_trade.data_gateway.store.mongo import (
    OPENBB_NORMALIZED,
    OPENBB_PROVIDER_ATTEMPTS,
    OPENBB_PROVIDER_HTTP_EVIDENCE,
    OPENBB_RAW_PAYLOADS,
)
from claw_trade.data_gateway.store.normalized import MongoNormalizedStore
from claw_trade.data_gateway.store.raw_payloads import MongoRawPayloadStore


@dataclass
class DomainPackService:
    settings: Any
    adapters: tuple[ProviderAdapter, ...]
    market_builder: MarketPackBuilder = field(default_factory=MarketPackBuilder)
    news_builder: NewsPackBuilder = field(default_factory=NewsPackBuilder)
    social_builder: SocialPackBuilder = field(default_factory=SocialPackBuilder)
    provider_execution_helper: ProviderExecutionEvidenceHelper | None = None

    def __post_init__(self) -> None:
        self._adapters_by_id = {adapter.adapter_id: adapter for adapter in self.adapters}
        self._mongo_client: MongoClient[Any] | None = None
        if self.provider_execution_helper is None:
            self.provider_execution_helper = self._build_execution_helper()

    def get_pack(self, request: PackRequest, run_plan: RunProviderPlan) -> DomainPackResult:
        if request.domain == PackDomain.MARKET:
            return self._get_market_pack(request=request, run_plan=run_plan)
        if request.domain == PackDomain.FUNDAMENTAL:
            return FundamentalPackService(
                settings=self.settings,
                adapters=self.adapters,
                provider_execution_helper=self.provider_execution_helper,
            ).get_pack(request, run_plan)
        if request.domain == PackDomain.NEWS:
            return self.news_builder.build(
                request=request,
                run_plan=run_plan,
                adapters_by_id=self._adapters_by_id,
                provider_execution_helper=self.provider_execution_helper,
            )
        if request.domain == PackDomain.SOCIAL:
            return self.social_builder.build(
                request=request,
                run_plan=run_plan,
                adapters_by_id=self._adapters_by_id,
                provider_execution_helper=self.provider_execution_helper,
            )
        raise ValueError(f"unsupported pack domain: {request.domain}")

    def _get_market_pack(self, *, request: PackRequest, run_plan: RunProviderPlan) -> DomainPackResult:
        specs = tuple(
            spec
            for spec in run_plan.call_specs
            if spec.domain == PackDomain.MARKET and spec.market == request.market
        )
        if not specs:
            gap = DataGap(
                gap_id=f"{request.run_id}:{request.call_id}:market:source_not_configured",
                domain=PackDomain.MARKET,
                severity=GapSeverity.FAIL,
                reason=DataGapReason.SOURCE_NOT_CONFIGURED,
                field_path="market",
                provider_candidates=(),
                attempt_ids=(),
                root_cause="no market call specs in run plan",
                next_action="configure market providers",
            )
            return self.market_builder.build(
                request=request,
                run_plan=run_plan,
                results=(),
                data_gaps=(gap,),
            )

        results = tuple(self._execute_market_spec(request=request, spec=spec) for spec in specs)
        return self.market_builder.build(
            request=request,
            run_plan=run_plan,
            results=results,
            data_gaps=tuple(gap for gap in run_plan.initial_gaps if gap.domain == PackDomain.MARKET),
            chart_object_store_uri=_chart_object_store_uri(self.settings),
        )

    def _execute_market_spec(self, *, request: PackRequest, spec: ProviderCallSpec) -> ProviderResult:
        started = utc_now_iso()
        t0 = time.perf_counter()
        adapter = self._adapters_by_id.get(spec.adapter_id)
        if adapter is None:
            return self._market_result(
                request=request,
                spec=spec,
                started=started,
                status=ProviderStatus.SKIPPED_NOT_CONFIGURED,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                error_code="adapter_not_configured",
                error_message=f"adapter {spec.adapter_id} not configured",
                latency_ms=_elapsed_ms(t0),
                adapter_kind=spec.provider_kind.value,
                provider_kind=spec.provider_kind,
            )

        credential = adapter.validate_credentials()
        if credential.status == AdmissionCheckStatus.MISSING:
            return self._market_result(
                request=request,
                spec=spec,
                started=started,
                status=ProviderStatus.CREDENTIAL_MISSING,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                error_code="credential_missing",
                error_message=credential.root_cause or "credential missing",
                latency_ms=_elapsed_ms(t0),
                adapter_kind=adapter.adapter_kind,
                provider_kind=adapter.provider_kind,
            )

        helper = self.provider_execution_helper
        if helper is not None:
            return helper.execute(
                request=request,
                spec=spec,
                adapter=adapter,
                started_at=started,
            )

        try:
            fetch = adapter.fetch(spec, request)
            normalized = adapter.normalize(spec, fetch)
        except Exception as exc:
            return self._market_result(
                request=request,
                spec=spec,
                started=started,
                status=ProviderStatus.REMOTE_ERROR,
                freshness=FreshnessStatus.NOT_FETCHED,
                rows=(),
                row_count=0,
                raw_ref=None,
                normalized_ref=None,
                error_code=type(exc).__name__,
                error_message=str(exc),
                latency_ms=_elapsed_ms(t0),
                adapter_kind=adapter.adapter_kind,
                provider_kind=adapter.provider_kind,
            )

        status = normalized.status
        if status == ProviderStatus.REMOTE_SUCCESS and normalized.row_count <= 0:
            status = ProviderStatus.EMPTY
        success = status == ProviderStatus.REMOTE_SUCCESS
        return self._market_result(
            request=request,
            spec=spec,
            started=started,
            status=status,
            freshness=FreshnessStatus.FRESH_REMOTE if success else FreshnessStatus.NOT_FETCHED,
            rows=normalized.rows if success else (),
            row_count=normalized.row_count if success else 0,
            raw_ref=normalized.source_raw_ref,
            normalized_ref=None,
            error_code=normalized.error_code,
            error_message=normalized.error_message,
            latency_ms=_elapsed_ms(t0),
            adapter_kind=adapter.adapter_kind,
            provider_kind=adapter.provider_kind,
        )

    def _build_execution_helper(self) -> ProviderExecutionEvidenceHelper | None:
        mongo_uri = getattr(self.settings, "mongo_uri", None)
        if not isinstance(mongo_uri, str) or not mongo_uri.strip():
            return None
        inline_max = getattr(self.settings, "raw_payload_inline_max_bytes", 200_000)
        provider_settings = getattr(self.settings, "provider_settings", None)
        self._mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
        database = self._mongo_client.get_default_database("claw_trade_openbb")
        return ProviderExecutionEvidenceHelper(
            raw_store=MongoRawPayloadStore(database[OPENBB_RAW_PAYLOADS], inline_max_bytes=int(inline_max)),
            normalized_store=MongoNormalizedStore(database[OPENBB_NORMALIZED]),
            attempt_store=MongoAttemptStore(database[OPENBB_PROVIDER_ATTEMPTS]),
            http_evidence_store=MongoProviderHttpEvidenceStore(database[OPENBB_PROVIDER_HTTP_EVIDENCE]),
            provider_settings=provider_settings if isinstance(provider_settings, Mapping) else None,
        )

    def _market_result(
        self,
        *,
        request: PackRequest,
        spec: ProviderCallSpec,
        started: str,
        status: ProviderStatus,
        freshness: FreshnessStatus,
        rows: tuple[Mapping[str, Any], ...],
        row_count: int,
        raw_ref: str | None,
        normalized_ref: str | None,
        error_code: str | None,
        error_message: str | None,
        latency_ms: int,
        adapter_kind: str,
        provider_kind: ProviderKind,
    ) -> ProviderResult:
        attempt_id = f"{request.run_id}:{request.call_id}:{spec.adapter_id}:{spec.endpoint}"
        attempt = ProviderAttempt(
            attempt_id=attempt_id,
            run_id=request.run_id,
            call_id=request.call_id,
            worker_id=request.worker_id,
            pack=PackDomain.MARKET.value,
            provider=spec.provider,
            adapter_id=spec.adapter_id,
            adapter_kind=adapter_kind,
            provider_kind=provider_kind,
            provider_config_version=spec.provider_config_version,
            endpoint=spec.endpoint,
            source_role=spec.source_role,
            started_at=started,
            finished_at=utc_now_iso(),
            status=status,
            required=spec.required,
            attempt_required=spec.attempt_required,
            coverage_group=spec.coverage_group,
            coverage_quorum=spec.coverage_quorum,
            priority_source=spec.priority_source,
            user_preferred=spec.user_preferred,
            from_cache=False,
            cache_status=None,
            single_flight_role="none",
            shared_from_attempt_id=None,
            latency_ms=max(0, latency_ms),
            row_count=row_count,
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            error_code=error_code,
            error_message=error_message,
            schema_id=spec.expected_schema_id,
            license_note="approved",
        )
        return ProviderResult(
            spec=spec,
            status=status,
            request_id=None,
            requested_at=started,
            latency_ms=max(0, latency_ms),
            source_role=spec.source_role,
            freshness=freshness,
            license_note="approved",
            raw_ref=raw_ref,
            normalized_ref=normalized_ref,
            rows=rows,
            row_count=row_count,
            cache_receipt=None,
            attempt=attempt,
            error_code=error_code,
            error_message=error_message,
        )


def _elapsed_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _chart_object_store_uri(settings: Any) -> str | None:
    value = getattr(settings, "object_store_uri", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None
