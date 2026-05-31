from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass, replace
from datetime import UTC, date, datetime
from enum import Enum
from pathlib import Path
from typing import Any

from claw_trade.data_gateway.models import (
    ConsumerType,
    DataRequirement,
    Market,
    PackDomain,
    ProviderCallSpec,
    ReportDataPlan,
    RequestKind,
    RequiredLevel,
    RunProviderPlan,
    SourceRole,
)
from claw_trade.data_gateway.requirements import (
    DataRequirementConsumer,
    collect_data_requirements,
    merge_duplicate_requirements,
)
from claw_trade.workflow.models import RunRequest


def build_report_data_plan(
    *,
    request: RunRequest,
    run_id: str,
    run_plan: RunProviderPlan,
    workers: tuple[str, ...],
    now: datetime | None = None,
) -> ReportDataPlan:
    start_date = date.fromisoformat(request.start_date)
    end_date = date.fromisoformat(request.end_date)
    current_date = date.fromisoformat(request.current_date)
    created_at = now or _parse_datetime_or_now(run_plan.generated_at)

    consumers = _report_consumers_from_worker_needs(
        run_id=run_id,
        request=request,
        run_plan=run_plan,
        workers=workers,
        start_date=start_date,
        end_date=end_date,
        current_date=current_date,
    )
    requirement_batch = merge_duplicate_requirements(
        collect_data_requirements(
            request_id=run_id,
            request_kind=RequestKind.REPORT,
            consumers=consumers,
            batch_id=f"{run_id}:report:requirements",
            profile=request.profile,
            created_at=created_at,
        )
    )
    provider_call_specs = _bind_provider_specs_to_requirements(
        specs=run_plan.call_specs,
        requirements=requirement_batch.original_requirements,
    )
    return ReportDataPlan(
        plan_id=f"{run_id}:report-data-plan",
        report_run_id=run_id,
        market=Market(request.market),
        ticker=request.ticker,
        company_name=request.company_name,
        date_range=(start_date, end_date),
        current_date=current_date,
        profile=request.profile,
        workers=workers,
        requirement_batch=requirement_batch,
        provider_call_specs=provider_call_specs,
        domain_pack_ids=tuple(_domain_pack_id(run_id=run_id, domain=domain.value) for domain in run_plan.domains),
        data_gap_ids=tuple(gap.gap_id for gap in run_plan.initial_gaps),
        created_at=created_at,
    )


def report_data_plan_snapshot(
    *,
    report_data_plan: ReportDataPlan,
    run_plan: RunProviderPlan,
    entry_point: str,
    data_gateway: str,
) -> dict[str, Any]:
    return {
        "schema_version": "report_data_plan.v1",
        "report_data_plan": _to_jsonable(report_data_plan),
        "requirement_batch": _to_jsonable(report_data_plan.requirement_batch),
        "provider_call_specs": _to_jsonable(report_data_plan.provider_call_specs),
        "domain_pack_generation": {
            "mode": "deferred_to_pack_runtime",
            "reason": "report entry writes plan-only evidence; runtime pack tool call materializes DomainPack",
            "planned_pack_ids": list(report_data_plan.domain_pack_ids),
        },
        "planned_domain_packs": [
            {
                "domain": domain.value,
                "pack_id": _domain_pack_id(run_id=report_data_plan.report_run_id, domain=domain.value),
            }
            for domain in run_plan.domains
        ],
        "data_gap_ids": list(report_data_plan.data_gap_ids),
        "run_provider_plan_ref": f"mongo://openbb_run_provider_plans/{run_plan.run_id}",
        "entry_point_cutover": {
            "entry_point": entry_point,
            "data_gateway": data_gateway,
            "legacy_provider_path_blocked": True,
        },
    }


def write_report_data_plan_snapshot(path: Path, payload: dict[str, Any]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_json_dumps(payload), encoding="utf-8")
    return path


def _json_dumps(payload: dict[str, Any]) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _parse_datetime_or_now(value: str) -> datetime:
    raw = str(value).strip()
    if not raw:
        return datetime.now(UTC)
    text = raw.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(UTC)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


@dataclass(frozen=True)
class _ReportWorkerNeed:
    worker_id: str
    domain: PackDomain
    data_type: str
    granularity: str
    source_role_required: SourceRole
    field_set: tuple[str, ...]
    freshness_policy: str = "report_default"
    required_level: RequiredLevel = RequiredLevel.REQUIRED
    allow_search_discovery: bool = False


_CN_A_REPORT_WORKER_NEEDS: tuple[_ReportWorkerNeed, ...] = (
    _ReportWorkerNeed(
        worker_id="market_analyst",
        domain=PackDomain.MARKET,
        data_type="cn_a_market_quote",
        granularity="realtime",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("last_price", "change", "pct_change", "volume", "amount", "timestamp"),
    ),
    _ReportWorkerNeed(
        worker_id="market_analyst",
        domain=PackDomain.MARKET,
        data_type="cn_a_market_kline",
        granularity="daily",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("trade_date", "open", "high", "low", "close", "volume"),
    ),
    _ReportWorkerNeed(
        worker_id="market_analyst",
        domain=PackDomain.MARKET,
        data_type="cn_a_market_orderbook",
        granularity="realtime",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("bid", "ask", "bid_size", "ask_size", "timestamp"),
    ),
    _ReportWorkerNeed(
        worker_id="fundamental_analyst",
        domain=PackDomain.FUNDAMENTAL,
        data_type="cn_a_fundamental_financials",
        granularity="structured",
        source_role_required=SourceRole.FUNDAMENTAL_DATA,
        field_set=("valuation.pe", "valuation.pb", "financial_indicators.roe"),
    ),
    _ReportWorkerNeed(
        worker_id="fundamental_analyst",
        domain=PackDomain.FUNDAMENTAL,
        data_type="cn_a_fundamental_estimates",
        granularity="structured",
        source_role_required=SourceRole.FUNDAMENTAL_DATA,
        field_set=("estimate_date", "consensus_eps", "source"),
    ),
    _ReportWorkerNeed(
        worker_id="fundamental_analyst",
        domain=PackDomain.FUNDAMENTAL,
        data_type="cn_a_fundamental_research",
        granularity="event",
        source_role_required=SourceRole.FUNDAMENTAL_DATA,
        field_set=("title", "published_at", "source", "url"),
    ),
    _ReportWorkerNeed(
        worker_id="news_analyst",
        domain=PackDomain.NEWS,
        data_type="cn_a_news_announcement",
        granularity="event",
        source_role_required=SourceRole.OFFICIAL_ORIGINAL,
        field_set=("title", "published_at", "source", "url"),
    ),
    _ReportWorkerNeed(
        worker_id="news_analyst",
        domain=PackDomain.NEWS,
        data_type="cn_a_news_company",
        granularity="event",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("title", "published_at", "source", "url"),
    ),
    _ReportWorkerNeed(
        worker_id="news_analyst",
        domain=PackDomain.NEWS,
        data_type="cn_a_news_macro_global",
        granularity="event",
        source_role_required=SourceRole.MACRO_DATA,
        field_set=("title", "published_at", "source", "url"),
    ),
    _ReportWorkerNeed(
        worker_id="social_analyst",
        domain=PackDomain.SOCIAL,
        data_type="cn_a_social_concept",
        granularity="event",
        source_role_required=SourceRole.SOCIAL_AGGREGATE_METRIC,
        field_set=("title", "source", "sample_time"),
    ),
    _ReportWorkerNeed(
        worker_id="social_analyst",
        domain=PackDomain.SOCIAL,
        data_type="cn_a_social_search_discovery",
        granularity="event",
        source_role_required=SourceRole.SEARCH_DISCOVERY,
        field_set=("title", "source", "sample_time"),
        required_level=RequiredLevel.OPTIONAL,
        allow_search_discovery=True,
    ),
)


_CN_A_REPORT_PACK_NEEDS: tuple[_ReportWorkerNeed, ...] = (
    _ReportWorkerNeed(
        worker_id="report_pack_policy",
        domain=PackDomain.POLICY,
        data_type="cn_a_policy_official",
        granularity="event",
        source_role_required=SourceRole.OFFICIAL_ORIGINAL,
        field_set=("title", "published_at", "source", "url"),
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_policy",
        domain=PackDomain.POLICY,
        data_type="cn_a_policy_news",
        granularity="event",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("title", "published_at", "source", "url"),
        required_level=RequiredLevel.OPTIONAL,
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_policy",
        domain=PackDomain.POLICY,
        data_type="cn_a_policy_macro",
        granularity="event",
        source_role_required=SourceRole.MACRO_DATA,
        field_set=("title", "published_at", "source", "url"),
        required_level=RequiredLevel.OPTIONAL,
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_policy",
        domain=PackDomain.POLICY,
        data_type="cn_a_policy_discovery",
        granularity="event",
        source_role_required=SourceRole.SEARCH_DISCOVERY,
        field_set=("title", "published_at", "source", "url"),
        required_level=RequiredLevel.OPTIONAL,
        allow_search_discovery=True,
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_hot_money",
        domain=PackDomain.HOT_MONEY,
        data_type="cn_a_hot_money_dragon_tiger",
        granularity="event",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("as_of", "name", "amount", "unit"),
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_hot_money",
        domain=PackDomain.HOT_MONEY,
        data_type="cn_a_hot_money_fund_flow",
        granularity="daily",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("as_of", "name", "amount", "unit"),
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_hot_money",
        domain=PackDomain.HOT_MONEY,
        data_type="cn_a_hot_money_northbound",
        granularity="daily",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("as_of", "name", "amount", "unit"),
        required_level=RequiredLevel.OPTIONAL,
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_hot_money",
        domain=PackDomain.HOT_MONEY,
        data_type="cn_a_hot_money_sector_flow",
        granularity="daily",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("as_of", "name", "amount", "unit"),
        required_level=RequiredLevel.OPTIONAL,
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_lockup",
        domain=PackDomain.LOCKUP,
        data_type="cn_a_lockup_unlock",
        granularity="event",
        source_role_required=SourceRole.OFFICIAL_ORIGINAL,
        field_set=("unlock_date", "name", "value", "value_label"),
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_lockup",
        domain=PackDomain.LOCKUP,
        data_type="cn_a_lockup_shareholder_count",
        granularity="structured",
        source_role_required=SourceRole.FUNDAMENTAL_DATA,
        field_set=("as_of", "name", "value", "value_label"),
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_lockup",
        domain=PackDomain.LOCKUP,
        data_type="cn_a_lockup_block_trade",
        granularity="event",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("as_of", "name", "value", "value_label"),
        required_level=RequiredLevel.OPTIONAL,
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_lockup",
        domain=PackDomain.LOCKUP,
        data_type="cn_a_lockup_margin_financing",
        granularity="daily",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("as_of", "name", "value", "value_label"),
        required_level=RequiredLevel.OPTIONAL,
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_lockup",
        domain=PackDomain.LOCKUP,
        data_type="cn_a_lockup_dividend",
        granularity="event",
        source_role_required=SourceRole.OFFICIAL_ORIGINAL,
        field_set=("as_of", "name", "value", "value_label"),
        required_level=RequiredLevel.OPTIONAL,
    ),
    _ReportWorkerNeed(
        worker_id="report_pack_lockup",
        domain=PackDomain.LOCKUP,
        data_type="cn_a_lockup_120d_flow",
        granularity="daily",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("as_of", "name", "value", "value_label"),
        required_level=RequiredLevel.OPTIONAL,
    ),
)


_GENERIC_REPORT_WORKER_NEEDS: tuple[_ReportWorkerNeed, ...] = (
    _ReportWorkerNeed(
        worker_id="market_analyst",
        domain=PackDomain.MARKET,
        data_type="core_market",
        granularity="daily",
        source_role_required=SourceRole.MARKET_DATA,
        field_set=("trade_date", "open", "high", "low", "close", "volume"),
    ),
    _ReportWorkerNeed(
        worker_id="fundamental_analyst",
        domain=PackDomain.FUNDAMENTAL,
        data_type="core_fundamental",
        granularity="structured",
        source_role_required=SourceRole.FUNDAMENTAL_DATA,
        field_set=("valuation.pe", "valuation.pb", "financial_indicators.roe"),
    ),
    _ReportWorkerNeed(
        worker_id="news_analyst",
        domain=PackDomain.NEWS,
        data_type="company_news",
        granularity="event",
        source_role_required=SourceRole.OFFICIAL_ORIGINAL,
        field_set=("title", "published_at", "source", "url"),
    ),
    _ReportWorkerNeed(
        worker_id="social_analyst",
        domain=PackDomain.SOCIAL,
        data_type="social_sentiment",
        granularity="event",
        source_role_required=SourceRole.SOCIAL_ORIGINAL_SAMPLE,
        field_set=("title", "source", "sample_time"),
    ),
)


def _report_consumers_from_worker_needs(
    *,
    run_id: str,
    request: RunRequest,
    run_plan: RunProviderPlan,
    workers: tuple[str, ...],
    start_date: date,
    end_date: date,
    current_date: date,
) -> tuple[DataRequirementConsumer, ...]:
    consumers: list[DataRequirementConsumer] = []
    explicit_needs = _explicit_report_worker_needs(market=run_plan.market, workers=workers)
    needs_by_worker: dict[str, tuple[_ReportWorkerNeed, ...]] = {}
    for need in explicit_needs:
        needs_by_worker.setdefault(need.worker_id, ())
        needs_by_worker[need.worker_id] = (*needs_by_worker[need.worker_id], need)

    for worker in workers:
        declared: list[DataRequirement] = []
        for need in needs_by_worker.get(worker, ()):
            if need.domain not in run_plan.domains:
                continue
            declared.append(
                _requirement_from_worker_need(
                    run_id=run_id,
                    request=request,
                    market=run_plan.market,
                    need=need,
                    start_date=start_date,
                    end_date=end_date,
                    current_date=current_date,
                )
            )
        if declared:
            consumers.append(
                DataRequirementConsumer(
                    consumer_type=ConsumerType.REPORT_WORKER,
                    consumer_id=worker,
                    declared_data_needs=tuple(declared),
                )
            )
            continue

    pack_needs_by_domain: dict[PackDomain, tuple[_ReportWorkerNeed, ...]] = {}
    for need in _explicit_report_pack_needs(market=run_plan.market, domains=run_plan.domains):
        pack_needs_by_domain.setdefault(need.domain, ())
        pack_needs_by_domain[need.domain] = (*pack_needs_by_domain[need.domain], need)

    covered_domains = {need.domain for need in explicit_needs}
    for domain in run_plan.domains:
        if domain in covered_domains:
            continue
        pack_needs = pack_needs_by_domain.get(domain, ())
        if pack_needs:
            declared = tuple(
                _requirement_from_worker_need(
                    run_id=run_id,
                    request=request,
                    market=run_plan.market,
                    need=need,
                    start_date=start_date,
                    end_date=end_date,
                    current_date=current_date,
                )
                for need in pack_needs
            )
        else:
            declared = (
                _missing_provider_requirement(
                    run_id=run_id,
                    request=request,
                    market=run_plan.market,
                    worker=f"report_pack_{domain.value}",
                    domain=domain,
                    start_date=start_date,
                    end_date=end_date,
                    current_date=current_date,
                ),
            )
        if not declared:
            declared = (
                _missing_provider_requirement(
                    run_id=run_id,
                    request=request,
                    market=run_plan.market,
                    worker=f"report_pack_{domain.value}",
                    domain=domain,
                    start_date=start_date,
                    end_date=end_date,
                    current_date=current_date,
                ),
            )
        consumers.append(
            DataRequirementConsumer(
                consumer_type=ConsumerType.REPORT_WORKER,
                consumer_id=f"report_pack_{domain.value}",
                declared_data_needs=declared,
            )
        )

    if consumers:
        return tuple(consumers)

    return tuple(
        DataRequirementConsumer(
            consumer_type=ConsumerType.REPORT_WORKER,
            consumer_id=f"report_pack_{domain.value}:missing_provider_plan",
            declared_data_needs=(
                DataRequirement(
                    requirement_id=_fallback_requirement_id(
                        run_id=run_id,
                        market=run_plan.market,
                        domain=domain.value,
                        data_type=f"{domain.value}_missing_provider_plan",
                        ticker=request.ticker,
                    ),
                    market=run_plan.market,
                    data_type=f"{domain.value}_missing_provider_plan",
                    granularity="unknown",
                    ticker=request.ticker,
                    universe_ref=None,
                    date_range=(start_date, end_date),
                    lookback_window_days=(end_date - start_date).days + 1,
                    current_date=current_date,
                    freshness_policy="report_default",
                    required_level=RequiredLevel.REQUIRED,
                    consumer_type=ConsumerType.REPORT_WORKER,
                    consumer_id=f"report_pack_{domain.value}:missing_provider_plan",
                    domain=domain,
                ),
            ),
        )
        for domain in run_plan.domains
    )


def _explicit_report_pack_needs(*, market: Market, domains: tuple[PackDomain, ...]) -> tuple[_ReportWorkerNeed, ...]:
    if market != Market.CN_A:
        return ()
    domain_set = set(domains)
    return tuple(need for need in _CN_A_REPORT_PACK_NEEDS if need.domain in domain_set)


def _explicit_report_worker_needs(*, market: Market, workers: tuple[str, ...]) -> tuple[_ReportWorkerNeed, ...]:
    source = _CN_A_REPORT_WORKER_NEEDS if market == Market.CN_A else _GENERIC_REPORT_WORKER_NEEDS
    worker_set = set(workers)
    return tuple(need for need in source if need.worker_id in worker_set)


def _requirement_from_worker_need(
    *,
    run_id: str,
    request: RunRequest,
    market: Market,
    need: _ReportWorkerNeed,
    start_date: date,
    end_date: date,
    current_date: date,
) -> DataRequirement:
    return DataRequirement(
        requirement_id=_fallback_requirement_id(
            run_id=run_id,
            market=market,
            domain=need.domain.value,
            data_type=need.data_type,
            ticker=request.ticker,
        ),
        market=market,
        data_type=need.data_type,
        granularity=need.granularity,
        ticker=request.ticker,
        universe_ref=None,
        date_range=(start_date, end_date),
        lookback_window_days=(end_date - start_date).days + 1,
        current_date=current_date,
        freshness_policy=need.freshness_policy,
        required_level=need.required_level,
        consumer_type=ConsumerType.REPORT_WORKER,
        consumer_id=need.worker_id,
        domain=need.domain,
        source_role_required=need.source_role_required,
        field_set=need.field_set,
        allow_search_discovery=need.allow_search_discovery,
    )


def _bind_provider_specs_to_requirements(
    *,
    specs: tuple[ProviderCallSpec, ...],
    requirements: tuple[DataRequirement, ...],
) -> tuple[ProviderCallSpec, ...]:
    by_domain_data_type = {
        (requirement.domain, requirement.data_type): requirement
        for requirement in requirements
    }
    bound: list[ProviderCallSpec] = []
    for spec in specs:
        data_type = spec.data_type or spec.expected_schema_id
        requirement = by_domain_data_type.get((spec.domain, data_type))
        if requirement is None:
            bound.append(spec)
            continue
        params = dict(spec.params)
        params["requirement_id"] = requirement.requirement_id
        params["granularity"] = requirement.granularity
        params["required_fields"] = requirement.field_set
        bound.append(
            replace(
                spec,
                requirement_id=requirement.requirement_id,
                data_type=requirement.data_type,
                params=params,
            )
        )
    return tuple(bound)


def _missing_provider_requirement(
    *,
    run_id: str,
    request: RunRequest,
    market: Market,
    worker: str,
    domain: PackDomain,
    start_date: date,
    end_date: date,
    current_date: date,
) -> DataRequirement:
    return DataRequirement(
        requirement_id=_fallback_requirement_id(
            run_id=run_id,
            market=market,
            domain=domain.value,
            data_type=f"{domain.value}_missing_provider_plan",
            ticker=request.ticker,
        ),
        market=market,
        data_type=f"{domain.value}_missing_provider_plan",
        granularity="unknown",
        ticker=request.ticker,
        universe_ref=None,
        date_range=(start_date, end_date),
        lookback_window_days=(end_date - start_date).days + 1,
        current_date=current_date,
        freshness_policy="report_default",
        required_level=RequiredLevel.REQUIRED,
        consumer_type=ConsumerType.REPORT_WORKER,
        consumer_id=worker,
        domain=domain,
    )


def _fallback_requirement_id(*, run_id: str, market: Market, domain: str, data_type: str, ticker: str) -> str:
    import hashlib

    raw = "|".join((run_id, market.value, domain, data_type, ticker.strip().upper()))
    return "req:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _domain_pack_id(*, run_id: str, domain: str) -> str:
    return f"{run_id}:{domain}:domain_pack"


def _to_jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC).isoformat()
        return value.astimezone(UTC).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    return value
