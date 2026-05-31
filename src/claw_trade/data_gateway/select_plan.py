from __future__ import annotations

from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

from claw_trade.data_gateway.models import (
    ConsumerType,
    DataGap,
    DataGapReason,
    DataRequirement,
    FreshnessDecision,
    FreshnessDecisionStatus,
    GapSeverity,
    Market,
    PackDomain,
    PrioritySource,
    ProviderCallSpec,
    ProviderKind,
    RequestKind,
    RequiredLevel,
    SelectDataPlan,
    SelectSupportStatus,
    SourceRole,
)
from claw_trade.data_gateway.requirements import (
    DataRequirementConsumer,
    collect_data_requirements,
    merge_duplicate_requirements,
)
from claw_trade.selection.models import SelectionMarket, SelectionRunPlan
from claw_trade.selection.strategy_config import load_cn_a_selection_v1_strategy

if TYPE_CHECKING:
    from claw_trade.selection.data_job import SelectionProviderBatchResult


def build_select_data_plan(
    *,
    plan: SelectionRunPlan,
    provider_result: Any | None = None,
    created_at: datetime | None = None,
) -> SelectDataPlan:
    del created_at
    market = _market(plan.market)
    current_date = date.fromisoformat(plan.trade_date)
    start_date = current_date - timedelta(days=plan.lookback_trading_days)
    requirement = DataRequirement(
        requirement_id=f"{plan.selection_run_id}:select:{market.value}:warehouse_features",
        market=market,
        data_type=_select_data_type(plan.market),
        granularity=_select_granularity(plan.market),
        ticker=None,
        universe_ref=f"selection-universe://{market.value}/{plan.universe_scope}",
        date_range=(start_date, current_date),
        lookback_window_days=plan.lookback_trading_days,
        current_date=current_date,
        freshness_policy="select_warehouse",
        required_level=RequiredLevel.REQUIRED,
        consumer_type=ConsumerType.SELECT_STRATEGY,
        consumer_id=plan.approved_strategy_config_ref,
        domain=PackDomain.SELECT_FEATURE,
        source_role_required=SourceRole.MARKET_DATA,
        field_set=_select_field_set(plan),
        allow_search_discovery=False,
    )
    requirement_batch = merge_duplicate_requirements(
        collect_data_requirements(
            request_id=plan.selection_run_id,
            request_kind=RequestKind.SELECT,
            consumers=(
                DataRequirementConsumer(
                    consumer_type=ConsumerType.SELECT_STRATEGY,
                    consumer_id=plan.approved_strategy_config_ref,
                    declared_data_needs=(requirement,),
                ),
            ),
            batch_id=f"{plan.selection_run_id}:select:requirements",
            profile=plan.profile.value,
        )
    )

    support_status = _support_status(plan.market, provider_result)
    warehouse_check = _warehouse_check(
        plan=plan,
        requirement=requirement,
        support_status=support_status,
        provider_result=provider_result,
    )
    data_gap_ids = tuple(gap.gap_id for check in warehouse_check for gap in check.data_gaps)
    provider_call_specs = _select_provider_call_specs(
        plan=plan,
        requirement=requirement,
        warehouse_check=warehouse_check,
        provider_result=provider_result,
    )
    return SelectDataPlan(
        plan_id=_select_data_plan_ref(plan),
        select_run_id=plan.selection_run_id,
        market=market,
        universe_ref=requirement.universe_ref or "",
        strategy_refs=(plan.approved_strategy_config_ref,),
        current_date=current_date,
        lookback_window_days=plan.lookback_trading_days,
        requirement_batch=requirement_batch,
        warehouse_checks=warehouse_check,
        provider_call_specs=provider_call_specs,
        feature_refs=(),
        data_gap_ids=data_gap_ids,
        support_status=support_status,
    )


def select_data_plan_snapshot(select_data_plan: SelectDataPlan) -> dict[str, Any]:
    return {
        "schema_version": "select_data_plan.v1",
        "select_data_plan": _to_jsonable(select_data_plan),
        "requirement_batch": _to_jsonable(select_data_plan.requirement_batch),
        "warehouse_checks": _to_jsonable(select_data_plan.warehouse_checks),
        "provider_call_specs": _to_jsonable(select_data_plan.provider_call_specs),
        "store_contract": {
            "persistence": "selection_data_run_evidence",
            "mongo_collection": None,
            "no_select_data_plans_collection": True,
        },
    }


def _warehouse_check(
    *,
    plan: SelectionRunPlan,
    requirement: DataRequirement,
    support_status: SelectSupportStatus,
    provider_result: SelectionProviderBatchResult | None,
) -> tuple[FreshnessDecision, ...]:
    if support_status == SelectSupportStatus.UNSUPPORTED:
        gap = _gap(
            plan=plan,
            requirement=requirement,
            reason=DataGapReason.NOT_APPLICABLE,
            root_cause=f"{plan.market.value} /select 当前未支持",
            next_action="先批准该市场的 universe、历史范围、清洗规则和入库任务",
        )
        return (
            FreshnessDecision(
                decision_id=f"{plan.selection_run_id}:select:unsupported",
                requirement_id=requirement.requirement_id,
                status=FreshnessDecisionStatus.MISSING,
                mongo_query_ref=f"mongo-query://selection/{plan.selection_run_id}/unsupported",
                matched_normalized_refs=(),
                missing_fields=requirement.field_set,
                missing_symbols=(),
                latest_data_time=None,
                required_data_time=plan.trade_date,
                should_call_provider=False,
                data_gaps=(gap,),
            ),
        )

    if plan.market == SelectionMarket.CRYPTO and provider_result is None:
        gap = _gap(
            plan=plan,
            requirement=requirement,
            reason=DataGapReason.MONGO_MISSING,
            root_cause="Crypto /select 历史包尚未批准下载并入 Mongo，不能生成可用选币仓库计划",
            next_action="先批准 Crypto universe/source/range/interval/license，并完成历史包导入 openbb_normalized",
        )
        return (
            FreshnessDecision(
                decision_id=f"{plan.selection_run_id}:select:crypto-history-missing",
                requirement_id=requirement.requirement_id,
                status=FreshnessDecisionStatus.MISSING,
                mongo_query_ref=f"mongo-query://selection/{plan.selection_run_id}/crypto-history",
                matched_normalized_refs=(),
                missing_fields=requirement.field_set,
                missing_symbols=(),
                latest_data_time=None,
                required_data_time=date.fromisoformat(plan.trade_date),
                should_call_provider=False,
                data_gaps=(gap,),
            ),
        )

    if (
        provider_result is not None
        and provider_result.warehouse_check_ref
        and provider_result.normalized_refs
    ):
        return (
            FreshnessDecision(
                decision_id=f"{plan.selection_run_id}:select:warehouse:fresh",
                requirement_id=requirement.requirement_id,
                status=FreshnessDecisionStatus.FRESH,
                mongo_query_ref=provider_result.warehouse_check_ref,
                matched_normalized_refs=provider_result.normalized_refs,
                missing_fields=(),
                missing_symbols=(),
                latest_data_time=date.fromisoformat(plan.trade_date),
                required_data_time=date.fromisoformat(plan.trade_date),
                should_call_provider=False,
                data_gaps=(),
            ),
        )

    return (
        FreshnessDecision(
            decision_id=f"{plan.selection_run_id}:select:warehouse:missing",
            requirement_id=requirement.requirement_id,
            status=FreshnessDecisionStatus.MISSING,
            mongo_query_ref=f"mongo-query://selection/{plan.selection_run_id}/warehouse",
            matched_normalized_refs=(),
            missing_fields=requirement.field_set,
            missing_symbols=(),
            latest_data_time=None,
            required_data_time=date.fromisoformat(plan.trade_date),
            should_call_provider=True,
            data_gaps=(),
        ),
    )


_CN_A_SELECT_PROVIDER_CANDIDATES: tuple[dict[str, object], ...] = (
    {
        "provider": "mootdx_selection_batch",
        "adapter_id": "project.cn_a.mootdx_selection_batch",
        "endpoint": "stock_zh_a_spot_mootdx_batch",
        "priority": 0,
    },
    {
        "provider": "tencent_selection_batch",
        "adapter_id": "project.cn_a.tencent_selection_batch",
        "endpoint": "stock_zh_a_spot_tencent_batch",
        "priority": 1,
    },
    {
        "provider": "sina_selection_batch",
        "adapter_id": "project.cn_a.sina_selection_batch",
        "endpoint": "stock_zh_a_spot_sina_batch",
        "priority": 2,
    },
    {
        "provider": "baostock_selection_batch",
        "adapter_id": "project.cn_a.baostock_selection_batch",
        "endpoint": "stock_zh_a_daily_baostock_batch",
        "priority": 3,
    },
    {
        "provider": "eastmoney_selection_batch",
        "adapter_id": "project.cn_a.eastmoney_selection_batch",
        "endpoint": "stock_zh_a_spot_em_batch",
        "priority": 10,
    },
    {
        "provider": "akshare_selection_batch",
        "adapter_id": "project.cn_a.akshare_selection_batch",
        "endpoint": "stock_zh_a_spot_em_batch",
        "priority": 11,
    },
    {
        "provider": "tushare_selection_batch",
        "adapter_id": "project.cn_a.tushare_selection_batch",
        "endpoint": "stock_zh_a_spot_em_batch",
        "priority": 12,
    },
)


def _select_provider_call_specs(
    *,
    plan: SelectionRunPlan,
    requirement: DataRequirement,
    warehouse_check: tuple[FreshnessDecision, ...],
    provider_result: SelectionProviderBatchResult | None,
) -> tuple[ProviderCallSpec, ...]:
    del provider_result
    if plan.market != SelectionMarket.CN_A:
        return ()
    if not any(check.should_call_provider for check in warehouse_check):
        return ()
    specs: list[ProviderCallSpec] = []
    for candidate in _CN_A_SELECT_PROVIDER_CANDIDATES:
        provider = str(candidate["provider"])
        adapter_id = str(candidate["adapter_id"])
        endpoint = str(candidate["endpoint"])
        priority = int(candidate["priority"])
        specs.append(
            ProviderCallSpec(
                call_key=f"{PackDomain.SELECT_FEATURE.value}:{adapter_id}:{endpoint}",
                provider=provider,
                adapter_id=adapter_id,
                provider_kind=ProviderKind.PROJECT_EXTENSION,
                provider_config_version="select-data-plan.v1",
                endpoint=endpoint,
                source_role=SourceRole.MARKET_DATA,
                market=Market.CN_A,
                domain=PackDomain.SELECT_FEATURE,
                required=True,
                attempt_required=True,
                coverage_group="cn_a_selection_batch",
                coverage_quorum=1,
                params={
                    "trade_date": plan.trade_date,
                    "lookback_trading_days": plan.lookback_trading_days,
                    "universe_scope": plan.universe_scope,
                    "requirement_id": requirement.requirement_id,
                },
                cache_ttl_seconds=900,
                license_policy_id="personal_research",
                expected_schema_id="cn_a.selection.batch.v1",
                priority=priority,
                priority_source=PrioritySource.SYSTEM_DEFAULT,
                user_preferred=False,
                raw_export_policy="metadata_only",
                requirement_id=requirement.requirement_id,
                data_type=requirement.data_type,
            )
        )
    return tuple(specs)


def _gap(
    *,
    plan: SelectionRunPlan,
    requirement: DataRequirement,
    reason: DataGapReason,
    root_cause: str,
    next_action: str,
) -> DataGap:
    market = _market(plan.market)
    return DataGap(
        gap_id=f"{plan.selection_run_id}:select:{reason.value}",
        domain=PackDomain.SELECT_FEATURE,
        severity=GapSeverity.BLOCKER,
        reason=reason,
        field_path="select",
        provider_candidates=(),
        attempt_ids=(),
        root_cause=root_cause,
        next_action=next_action,
        requirement_id=requirement.requirement_id,
        market=market,
        data_type=requirement.data_type,
        evidence_refs=(),
        human_readable=root_cause,
    )


def _support_status(
    market: SelectionMarket,
    provider_result: SelectionProviderBatchResult | None,
) -> SelectSupportStatus:
    if market in {SelectionMarket.HK, SelectionMarket.US}:
        return SelectSupportStatus.UNSUPPORTED
    if market == SelectionMarket.CRYPTO:
        return SelectSupportStatus.TARGET_DESIGN
    if provider_result is not None and provider_result.warehouse_check_ref and provider_result.normalized_refs:
        return SelectSupportStatus.SUPPORTED
    return SelectSupportStatus.TARGET_DESIGN


def _select_data_type(market: SelectionMarket) -> str:
    if market == SelectionMarket.CN_A:
        return "cn_a_select_features"
    if market == SelectionMarket.CRYPTO:
        return "crypto_select_history"
    return f"{market.value.lower()}_select"


def _select_granularity(market: SelectionMarket) -> str:
    if market == SelectionMarket.CN_A:
        return "daily"
    if market == SelectionMarket.CRYPTO:
        return "daily_or_hourly"
    return "unknown"


def _select_field_set(plan: SelectionRunPlan) -> tuple[str, ...]:
    if plan.market == SelectionMarket.CN_A:
        strategy = load_cn_a_selection_v1_strategy(plan.approved_strategy_config_ref)
        if strategy is not None:
            fields: set[str] = {"history", "ticker", "company_name", "source_ref"}
            for rule in strategy.hard_filters:
                fields.add(rule.field)
            for rule in strategy.strategy_set:
                fields.update(field for field in rule.required_fields if field.strip())
                fields.update(condition.field for condition in rule.all_of if condition.field.strip())
            fields.add(strategy.stable_top20_rule.score_field)
            fields.update(field.field for field in strategy.stable_top20_rule.tie_break_fields if field.field.strip())
            return tuple(sorted(fields))
    return ("price_history", "strategy_fields", "event_features")


def _market(value: SelectionMarket) -> Market:
    return Market(value.value)


def _select_data_plan_ref(plan: SelectionRunPlan) -> str:
    return f"select-data-plan://selection/{plan.selection_run_id}/{plan.trade_date}"


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
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    return value
