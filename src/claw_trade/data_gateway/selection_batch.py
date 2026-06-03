"""CN_A selection batch bridge for the unified data layer.

This module is an explicit data-job fetcher. It is not a `/select` read-entry
recovery path: `/select` still reads only completed warehouse-marked selection runs.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Mapping, Sequence

from claw_trade.data_gateway.api import DataAPI
from claw_trade.data_gateway.coordination.batch_planner import ProviderBatchPlanner
from claw_trade.data_gateway.coordination.coalescer import RequestCoalescer
from claw_trade.data_gateway.coordination.provider_selector import ProviderSelector
from claw_trade.data_gateway.coordination.query_planner import QueryPlanner
from claw_trade.data_gateway.coordination.service import DataService
from claw_trade.data_gateway.execution import ProviderResultCache
from claw_trade.data_gateway.execution.fetch_engine import FetchEngine
from claw_trade.data_gateway.execution.gate import ExecutionGate
from claw_trade.data_gateway.execution.rate_limit_policy import RateLimitPolicyResolver
from claw_trade.data_gateway.execution.rate_limiter import RateLimiter
from claw_trade.data_gateway.execution.single_flight import SingleFlight
from claw_trade.data_gateway.ingest.attempt_log import AttemptLog
from claw_trade.data_gateway.ingest.normalized_store import NormalizedStore
from claw_trade.data_gateway.ingest.normalizer import Normalizer
from claw_trade.data_gateway.ingest.pipeline import IngestPipeline
from claw_trade.data_gateway.ingest.raw_store import RawStore
from claw_trade.data_gateway.models import DataGap, DataRequest, DataResult, DataResultStatus, Market
from claw_trade.data_gateway.providers import build_minimal_provider_registry
from claw_trade.data_gateway.providers.credentials import DataSourceCredentialResolver
from claw_trade.data_gateway.providers.plugins import iter_minimal_market_plugins
from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse
from claw_trade.selection.data_job import SelectionProviderBatchResult
from claw_trade.selection.engine import ApprovedSelectionStrategy
from claw_trade.selection.models import (
    DataGapRef,
    DataGapSeverity,
    SelectionBatchScope,
    SelectionMarket,
    SelectionProfile,
    SelectionProviderBatchPlan,
    SelectionRunPlan,
)
from claw_trade.selection.strategy_config import load_cn_a_selection_v1_strategy
from claw_trade.ui_backend.mongo_settings_store import (
    MongoDataSourceStore,
    MongoSecretStore,
    UI_DATA_SOURCE_SETTINGS_COLLECTION,
    UI_SECRET_SETTINGS_COLLECTION,
)

_CN_A_SELECTION_COVERAGE_GROUP = "cn_a_selection_batch"
_CN_A_SELECTION_TIMEZONE = "Asia/Shanghai"
_CN_A_SELECTION_CALENDAR = "CN_A_SSE_SZSE"
_DEFAULT_LOOKBACK_TRADING_DAYS = 260
_MIN_HISTORY_DAYS = 250

_SELECTION_DAILY_REQUEST: tuple[str, str, tuple[str, ...]] = (
    "daily_bar",
    "daily",
    ("date", "open", "high", "low", "close", "volume", "amount"),
)
_SELECTION_SUPPLEMENTAL_REQUESTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("corporate_action", "event", ("event_type", "event_date", "title", "source", "symbol_id")),
    ("valuation_metric", "daily", ("pe", "pb", "ps", "market_cap")),
)
_SELECTION_REQUESTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    _SELECTION_DAILY_REQUEST,
    *_SELECTION_SUPPLEMENTAL_REQUESTS,
)

_SELECTION_STRATEGY_DERIVED_FIELD_DEPENDENCIES: Mapping[str, tuple[str, ...]] = {
    "close_open_ratio": ("history",),
    "intraday_return_pct": ("history",),
    "p_change_pct": ("history",),
    "range_pct": ("history",),
    "vol_ratio": ("history",),
    "prev_close": ("history",),
    "prev_volume": ("history",),
    "ma5": ("history",),
    "ma10": ("history",),
    "ma20": ("history",),
    "ma30": ("history",),
    "ma60": ("history",),
    "ma120": ("history",),
    "ma250": ("history",),
    "prev_ma5": ("history",),
    "prev_ma20": ("history",),
    "prev_ma60": ("history",),
    "ma30_slope_5d": ("history",),
    "ma30_slope_10d": ("history",),
    "ma30_growth_30d": ("history",),
    "limit_up_recent": ("history",),
    "post_limit_up_range_pct": ("history",),
    "post_limit_up_return_abs_pct": ("history",),
    "post_limit_up_window_days": ("history",),
    "ma250_backtrace_days": ("history",),
    "ma250_back_ratio": ("history",),
    "platform_deviation_pct": ("history",),
    "return_40d": ("history",),
    "return_60d": ("history",),
    "return_120d": ("history",),
    "single_day_min_return_60d": ("history",),
    "highest_close_60d": ("history",),
    "limit_up_count_20d": ("history",),
    "limit_up_streak_2d": ("history",),
    "limit_down_today": ("history",),
    "atr_14": ("history",),
    "volume_ma20": ("history",),
    "high_20d": ("history",),
    "high_40d": ("history",),
    "low_10d": ("history",),
    "range_10d": ("history",),
    "limit_up_yesterday": ("history",),
    "rps60": ("history",),
    "rps120": ("history",),
    "roll_high_120d": ("history",),
    "score": (),
    "strategy_hit_count": (),
    "strategy_variant_count": (),
    "strategy_missing_field_count": (),
    "strategy_required_field_count": (),
    "strategy_hit_coverage_score": (),
    "strategy_inner_strength_score": (),
    "rps_trend_score": (),
    "liquidity_tradability_score": (),
    "industry_theme_score": (),
    "evidence_completeness_score": (),
    "risk_penalty_score": (),
    "data_gap_penalty_score": (),
}
_OPTIONAL_SELECTION_STRATEGY_SOURCE_FIELDS = frozenset(
    {
        "private_placement_event_date",
        "private_placement_days_since",
    }
)
_OPTIONAL_SELECTION_SUPPLEMENTAL_DATA_TYPES = frozenset({"corporate_action", "valuation_metric"})


@dataclass(frozen=True)
class _SelectionGatewayContext:
    data_api: DataAPI
    provider_candidates: tuple[str, ...]


@dataclass(frozen=True)
class _HistoryShortfall:
    ticker: str
    valid_days: int
    first_date: date | None
    list_date: date | None
    classification: str


def build_selection_provider_batch_plan(
    *,
    market: SelectionMarket,
    profile: SelectionProfile,
    trade_date: str,
    plan_id: str | None = None,
) -> SelectionProviderBatchPlan:
    if market != SelectionMarket.CN_A or profile != SelectionProfile.CN_A:
        raise ValueError("selection batch v1 仅支持 CN_A")
    resolved_plan_id = plan_id or f"plan://selection/cn_a/{trade_date}/batch-v1"
    return SelectionProviderBatchPlan(
        plan_id=resolved_plan_id,
        scope=SelectionBatchScope.SELECTION_BATCH,
        market=market,
        profile=profile,
        trade_date=trade_date,
        lookback_trading_days=_DEFAULT_LOOKBACK_TRADING_DAYS,
        universe_scope="all_a_shares",
        coverage_groups=(_CN_A_SELECTION_COVERAGE_GROUP, "universe", "daily", "fundamental"),
        provider_candidates=_cn_a_provider_candidates(),
        ttl_policy_ref="ttl://selection/cn_a/batch-v1/900s",
        lineage_root_ref=f"lineage://selection/cn_a/{trade_date}/batch-v1",
    )


def fetch_selection_batch_from_data_gateway(
    plan: SelectionRunPlan,
    *,
    evidence_root: Path | None = None,
) -> SelectionProviderBatchResult:
    provider_plan = build_selection_provider_batch_plan(
        market=plan.market,
        profile=plan.profile,
        trade_date=plan.trade_date,
        plan_id=plan.provider_batch_plan_ref,
    )
    try:
        gateway = _build_selection_gateway_context()
    except Exception as exc:  # noqa: BLE001
        result = _failed_provider_result(
            plan=plan,
            provider_plan=provider_plan,
            gap_code="selection_data_api_unavailable",
            message=(
                "selection batch 无法连接统一 DataAPI 运行时，"
                f"任务按 fail closed 失败：{type(exc).__name__}: {exc}"
            ),
        )
        _write_evidence(evidence_root=evidence_root, plan=plan, requests=(), results=(), provider_result=result)
        return result

    requests = _selection_data_requests(plan, specs=(_SELECTION_DAILY_REQUEST,))
    try:
        results = tuple(gateway.data_api.get_data_batch(requests))
    except Exception as exc:  # noqa: BLE001
        result = _failed_provider_result(
            plan=plan,
            provider_plan=provider_plan,
            gap_code="selection_data_api_failed",
            message=f"selection batch 调用统一 DataAPI 失败，任务按 fail closed 失败：{type(exc).__name__}: {exc}",
        )
        _write_evidence(evidence_root=evidence_root, plan=plan, requests=requests, results=(), provider_result=result)
        return result

    initial_result = _provider_result_from_data_results(
        plan=plan,
        provider_plan=provider_plan,
        results=results,
    )
    history_backfill_requests = _selection_history_backfill_requests(plan=plan, results=results)
    if history_backfill_requests:
        try:
            history_backfill_results = tuple(gateway.data_api.get_data_batch(history_backfill_requests))
        except Exception as exc:  # noqa: BLE001
            initial_result = _append_data_gap(
                initial_result,
                _warn_gap(
                    gap_id=f"{plan.selection_run_id}-selection-batch-history-backfill-failed",
                    gap_code="selection_batch_history_backfill_failed",
                    attempt_refs=(provider_plan.lineage_root_ref,),
                    reader_message=(
                        "部分老股票历史日线不足，统一数据层已尝试单票回补但调用失败，"
                        f"本轮继续剔除这些股票：{type(exc).__name__}: {exc}"
                    ),
                    source_metadata={
                        "request_ids": tuple(request.request_id for request in history_backfill_requests),
                        "tickers": tuple(request.symbol_id for request in history_backfill_requests if request.symbol_id),
                    },
                ),
            )
        else:
            requests = (*requests, *history_backfill_requests)
            results = (*results, *history_backfill_results)
            initial_result = _provider_result_from_data_results(
                plan=plan,
                provider_plan=provider_plan,
                results=results,
            )
    if _should_return_initial_daily_result(results=results, provider_result=initial_result):
        _write_evidence(evidence_root=evidence_root, plan=plan, requests=requests, results=results, provider_result=initial_result)
        return initial_result

    supplemental_requests = _selection_data_requests(plan, specs=_SELECTION_SUPPLEMENTAL_REQUESTS, start_index=2)
    try:
        supplemental_results = tuple(gateway.data_api.get_data_batch(supplemental_requests))
    except Exception as exc:  # noqa: BLE001
        result = _failed_provider_result(
            plan=plan,
            provider_plan=provider_plan,
            gap_code="selection_data_api_failed",
            message=f"selection batch 调用统一 DataAPI 补充数据失败，任务按 fail closed 失败：{type(exc).__name__}: {exc}",
        )
        _write_evidence(evidence_root=evidence_root, plan=plan, requests=(*requests, *supplemental_requests), results=results, provider_result=result)
        return result

    all_requests = (*requests, *supplemental_requests)
    results = (*results, *supplemental_results)
    result = _provider_result_from_data_results(
        plan=plan,
        provider_plan=provider_plan,
        results=results,
    )
    _write_evidence(evidence_root=evidence_root, plan=plan, requests=all_requests, results=results, provider_result=result)
    return result


def _provider_result_from_data_results(
    *,
    plan: SelectionRunPlan,
    provider_plan: SelectionProviderBatchPlan,
    results: tuple[DataResult, ...],
) -> SelectionProviderBatchResult:
    attempt_refs = _dedupe(ref for item in results for ref in item.attempt_refs)
    dataset_refs = _dedupe(ref for item in results for ref in item.dataset_refs)
    normalized_refs = tuple(_normalized_ref(ref) for ref in dataset_refs)
    rows, row_gaps = _selection_rows_from_results(plan=plan, results=results, attempt_refs=attempt_refs)
    repaired_history_tickers = _repaired_history_backfill_tickers(plan=plan, rows=rows, results=results)
    data_gaps = [
        *(
            _gap_from_data_gap(plan=plan, gap=gap, default_refs=attempt_refs or (provider_plan.lineage_root_ref,))
            for item in results
            for gap in item.gaps
            if not _is_repaired_history_backfill_gap(gap=gap, repaired_history_tickers=repaired_history_tickers)
        ),
        *row_gaps,
    ]
    missing_strategy_fields = _selection_missing_strategy_required_fields(plan=plan, rows=rows)
    if missing_strategy_fields:
        data_gaps.append(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-strategy-fields-missing",
                gap_code="selection_batch_strategy_fields_missing",
                attempt_refs=attempt_refs or (provider_plan.lineage_root_ref,),
                reader_message=(
                    "selection batch 标准化行缺少 approved strategy 的源字段，"
                    f"不能生成 approved candidate pack：{', '.join(missing_strategy_fields)}。"
                ),
                source_metadata={"missing_fields": missing_strategy_fields},
            )
        )

    result = SelectionProviderBatchResult(
        provider_batch_plan=provider_plan,
        attempt_refs=attempt_refs,
        normalized_refs=normalized_refs,
        rows=rows,
        data_gaps=tuple(data_gaps),
        warehouse_check_ref=(
            f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}/data-api"
            if normalized_refs
            else None
        ),
    )
    return result


def _append_data_gap(result: SelectionProviderBatchResult, gap: DataGapRef) -> SelectionProviderBatchResult:
    return SelectionProviderBatchResult(
        provider_batch_plan=result.provider_batch_plan,
        attempt_refs=result.attempt_refs,
        normalized_refs=result.normalized_refs,
        rows=result.rows,
        data_gaps=(*result.data_gaps, gap),
        warehouse_check_ref=result.warehouse_check_ref,
    )


def _repaired_history_backfill_tickers(
    *,
    plan: SelectionRunPlan,
    rows: tuple[Mapping[str, object], ...],
    results: tuple[DataResult, ...],
) -> tuple[str, ...]:
    backfill_tickers = {
        ticker
        for result in results
        if (ticker := _history_backfill_ticker_from_request_id(result.request_id)) is not None
    }
    if not backfill_tickers:
        return ()
    repaired = {
        str(row.get("ticker") or "").strip()
        for row in rows
        if str(row.get("ticker") or "").strip() in backfill_tickers and _row_has_required_history(row=row, plan=plan)
    }
    return tuple(sorted(repaired))


def _is_repaired_history_backfill_gap(
    *,
    gap: DataGap,
    repaired_history_tickers: tuple[str, ...],
) -> bool:
    if not repaired_history_tickers:
        return False
    ticker = gap.symbol_id or _history_backfill_ticker_from_request_id(gap.request_id)
    return ticker in set(repaired_history_tickers)


def _history_backfill_ticker_from_request_id(request_id: str) -> str | None:
    marker = ":selection:history_backfill:"
    if marker not in request_id:
        return None
    tail = request_id.split(marker, 1)[1]
    parts = tail.split(":")
    if len(parts) < 3:
        return None
    ticker = parts[1].strip()
    return ticker or None


def _should_return_initial_daily_result(
    *,
    results: tuple[DataResult, ...],
    provider_result: SelectionProviderBatchResult,
) -> bool:
    if _has_prepackaged_direct_rows(results):
        return True
    if not provider_result.rows:
        return True
    if any(gap.gap_code == "selection_private_placement_source_missing" for gap in provider_result.data_gaps):
        return False
    return not any(gap.severity == DataGapSeverity.BLOCKER for gap in provider_result.data_gaps)


def _has_prepackaged_direct_rows(results: tuple[DataResult, ...]) -> bool:
    return any(
        _result_data_type(result.request_id) == "daily_bar"
        and any(isinstance(row.get("history"), Sequence) and not isinstance(row.get("history"), (str, bytes, bytearray)) for row in result.rows)
        for result in results
    )


def _build_selection_gateway_context() -> _SelectionGatewayContext:
    database = _open_database()
    repository = DatasetRepository.from_database(database)
    registry = build_minimal_provider_registry()
    credential_resolver = DataSourceCredentialResolver(
        data_source_store=MongoDataSourceStore(database[UI_DATA_SOURCE_SETTINGS_COLLECTION]),
        secret_store=MongoSecretStore(database[UI_SECRET_SETTINGS_COLLECTION]),
    )
    ingest = IngestPipeline(
        raw_store=RawStore(repository=repository),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repository),
        attempt_log=AttemptLog(repository=repository),
    )
    service = DataService(
        query_planner=QueryPlanner(),
        warehouse=Warehouse(repository),
        provider_selector=ProviderSelector(registry),
        coalescer=RequestCoalescer(),
        batch_planner=ProviderBatchPlanner(
            rate_limit_policy_resolver=RateLimitPolicyResolver(data_source_settings=credential_resolver),
        ),
        execution_gate=ExecutionGate(
            cache=ProviderResultCache(repository),
            rate_limiter=RateLimiter(repository),
            single_flight=SingleFlight(repository),
        ),
        fetch_engine=FetchEngine(registry, credential_resolver=credential_resolver),
        ingest=ingest,
    )
    return _SelectionGatewayContext(data_api=DataAPI(service), provider_candidates=_cn_a_provider_candidates())


def _selection_data_requests(
    plan: SelectionRunPlan,
    *,
    specs: tuple[tuple[str, str, tuple[str, ...]], ...] = _SELECTION_REQUESTS,
    start_index: int = 1,
) -> tuple[DataRequest, ...]:
    as_of = datetime.now(tz=UTC)
    trade_date = date.fromisoformat(plan.trade_date)
    # Trading days are not natural days; use a wider calendar range for warehouse
    # matching and provider requests.
    start = trade_date - timedelta(days=max(plan.lookback_trading_days, _DEFAULT_LOOKBACK_TRADING_DAYS) * 2)
    return tuple(
        DataRequest(
            request_id=f"{plan.selection_run_id}:selection:{index}:{data_type}",
            market=Market.CN_A,
            universe_ref=plan.universe_scope,
            timezone=_CN_A_SELECTION_TIMEZONE,
            calendar=_CN_A_SELECTION_CALENDAR,
            data_type=data_type,
            granularity=granularity,
            fields=fields,
            date_range_start=start if granularity == "daily" else None,
            date_range_end=trade_date if granularity == "daily" else None,
            freshness_policy="trading_day" if granularity == "daily" else "event_time",
            consumer="select",
            consumer_id=plan.selection_run_id,
            as_of=as_of,
        )
        for index, (data_type, granularity, fields) in enumerate(specs, start=start_index)
    )


def _selection_history_backfill_requests(*, plan: SelectionRunPlan, results: tuple[DataResult, ...]) -> tuple[DataRequest, ...]:
    shortfalls = _direct_history_shortfalls(plan=plan, results=results)
    tickers = tuple(item.ticker for item in shortfalls if item.classification == "listed_old_history_missing_should_backfill")
    if not tickers:
        return ()

    as_of = datetime.now(tz=UTC)
    trade_date = date.fromisoformat(plan.trade_date)
    start = trade_date - timedelta(days=max(plan.lookback_trading_days, _DEFAULT_LOOKBACK_TRADING_DAYS) * 2)
    return tuple(
        DataRequest(
            request_id=f"{plan.selection_run_id}:selection:history_backfill:{index}:{ticker}:daily_bar",
            market=Market.CN_A,
            symbol_id=ticker,
            timezone=_CN_A_SELECTION_TIMEZONE,
            calendar=_CN_A_SELECTION_CALENDAR,
            data_type="daily_bar",
            granularity="daily",
            fields=_SELECTION_DAILY_REQUEST[2],
            date_range_start=start,
            date_range_end=trade_date,
            freshness_policy="trading_day",
            consumer="select",
            consumer_id=plan.selection_run_id,
            as_of=as_of,
        )
        for index, ticker in enumerate(tickers, start=1)
    )


def _direct_history_shortfalls(*, plan: SelectionRunPlan, results: tuple[DataResult, ...]) -> tuple[_HistoryShortfall, ...]:
    threshold = _history_threshold_date(plan=plan, results=results)
    candidates: list[tuple[str, Mapping[str, Any], tuple[date, ...], date | None]] = []
    shortfalls: list[_HistoryShortfall] = []
    seen_candidates: set[str] = set()
    for result in results:
        if _result_data_type(result.request_id) != "daily_bar":
            continue
        for row in result.rows:
            if not _row_has_direct_history(row):
                continue
            ticker = _ticker_from_row(row)
            if ticker is None or _row_has_required_history(row=row, plan=plan):
                continue
            if ticker in seen_candidates:
                continue
            seen_candidates.add(ticker)
            valid_dates = _valid_history_dates(row)
            candidates.append((ticker, row, valid_dates, _listing_date_from_row(row)))
    listing_dates = _listing_dates_for_tickers(tuple(ticker for ticker, _row, _dates, list_date in candidates if list_date is None))
    for ticker, row, valid_dates, row_list_date in candidates:
        first_date = valid_dates[0] if valid_dates else None
        list_date = row_list_date or listing_dates.get(ticker)
        classification = "listing_date_unknown"
        if threshold is None or list_date is None:
            classification = "listing_date_unknown"
        elif list_date > threshold:
            classification = "listing_too_recent_cannot_backfill"
        elif first_date is not None and first_date > threshold:
            classification = "listed_old_history_missing_should_backfill"
        else:
            classification = "listed_old_but_sparse_or_suspended_needs_check"
        shortfalls.append(
            _HistoryShortfall(
                ticker=ticker,
                valid_days=len(valid_dates),
                first_date=first_date,
                list_date=list_date,
                classification=classification,
            )
        )
    return tuple(shortfalls)


def _history_threshold_date(*, plan: SelectionRunPlan, results: tuple[DataResult, ...]) -> date | None:
    required = max(_MIN_HISTORY_DAYS, int(plan.lookback_trading_days or 0))
    dates: set[date] = set()
    trade_day = date.fromisoformat(plan.trade_date)
    for result in results:
        if _result_data_type(result.request_id) != "daily_bar":
            continue
        for row in result.rows:
            if not _row_has_direct_history(row):
                continue
            dates.update(day for day in _valid_history_dates(row) if day <= trade_day)
    if len(dates) < required:
        return None
    return sorted(dates)[-required]


def _row_has_direct_history(row: Mapping[str, Any]) -> bool:
    history = row.get("history")
    return isinstance(history, Sequence) and not isinstance(history, (str, bytes, bytearray))


def _valid_history_dates(row: Mapping[str, Any]) -> tuple[date, ...]:
    history = row.get("history")
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        return ()
    dates: list[date] = []
    for item in history:
        if not isinstance(item, Mapping):
            continue
        if not all(item.get(field) is not None for field in ("open", "high", "low", "close", "volume")):
            continue
        item_date = _parse_date(item.get("date") or item.get("trade_date"))
        if item_date is not None:
            dates.append(item_date)
    return tuple(sorted(dict.fromkeys(dates)))


def _selection_rows_from_results(
    *,
    plan: SelectionRunPlan,
    results: tuple[DataResult, ...],
    attempt_refs: tuple[str, ...],
) -> tuple[tuple[Mapping[str, object], ...], tuple[DataGapRef, ...]]:
    direct_rows, direct_gaps = _direct_selection_rows_from_results(plan=plan, results=results, attempt_refs=attempt_refs)
    if direct_rows:
        return direct_rows, direct_gaps

    daily_rows = [row for result in results if _result_data_type(result.request_id) == "daily_bar" for row in result.rows]
    if not daily_rows:
        return (), ()

    corporate_rows = tuple(
        row for result in results if _result_data_type(result.request_id) == "corporate_action" for row in result.rows
    )
    valuation_rows = tuple(
        row for result in results if _result_data_type(result.request_id) == "valuation_metric" for row in result.rows
    )
    has_private_placement_coverage = any(
        result.status in {DataResultStatus.READY, DataResultStatus.PARTIAL} and result.dataset_refs
        for result in results
        if _result_data_type(result.request_id) == "corporate_action"
    )

    grouped: dict[str, list[Mapping[str, Any]]] = {}
    for row in daily_rows:
        ticker = _ticker_from_row(row)
        if ticker is None:
            continue
        grouped.setdefault(ticker, []).append(row)

    rows: list[Mapping[str, object]] = []
    dropped: list[str] = []
    private_missing: list[str] = []
    required_history_days = max(_MIN_HISTORY_DAYS, int(plan.lookback_trading_days or 0))
    for ticker, history_source in sorted(grouped.items()):
        history = tuple(
            mapped
            for mapped in (_history_row(row) for row in sorted(history_source, key=lambda item: str(_row_date(item) or "")))
            if mapped is not None
        )
        if len(history) < required_history_days:
            dropped.append(ticker)
            continue
        latest = history[-1]
        company_name = _company_name_from_rows(history_source)
        if company_name is None:
            dropped.append(ticker)
            continue
        row: dict[str, object] = {
            "ticker": ticker,
            "company_name": company_name,
            "industry": _industry_from_rows(history_source),
            "history": history,
            "source_ref": _row_source_ref(history_source[-1]),
            "open": latest["open"],
            "high": latest["high"],
            "low": latest["low"],
            "close": latest["close"],
            "volume": latest["volume"],
        }
        if latest.get("amount") is not None:
            row["amount"] = latest["amount"]
        valuation_fields = _latest_valuation_fields(
            ticker=ticker,
            trade_date=plan.trade_date,
            rows=valuation_rows,
        )
        row.update(valuation_fields)
        private_event = _private_placement_fields(
            ticker=ticker,
            trade_date=plan.trade_date,
            rows=corporate_rows,
            coverage_available=has_private_placement_coverage,
        )
        if private_event is None:
            private_missing.append(ticker)
        else:
            row.update(private_event)
        rows.append(row)

    gaps: list[DataGapRef] = []
    if dropped:
        gaps.append(
            _warn_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-rows-dropped",
                gap_code="selection_batch_rows_dropped",
                attempt_refs=attempt_refs or (f"select-data-plan://selection/{plan.selection_run_id}/{plan.trade_date}",),
                reader_message=(
                    "selection batch 中部分股票缺少公司名或足够历史日线，已剔除。"
                    f" count={len(dropped)}。"
                ),
                source_metadata={"tickers_sample": tuple(dropped[:20]), "required_history_days": required_history_days},
            )
        )
    if private_missing:
        gaps.append(
            _warn_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-private-placement-missing",
                gap_code="selection_private_placement_source_missing",
                attempt_refs=attempt_refs or (f"select-data-plan://selection/{plan.selection_run_id}/{plan.trade_date}",),
                reader_message=(
                    "统一数据层没有覆盖 Sequoia-X 私募/定增事件字段，本次定增策略应禁用，其它策略继续。"
                ),
                source_metadata={
                    "missing_fields": ("private_placement_event_date", "private_placement_days_since"),
                    "missing_ticker_count": len(private_missing),
                    "missing_tickers_sample": tuple(private_missing[:20]),
                },
            )
        )
    if not rows and daily_rows:
        gaps.append(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-rows-empty",
                gap_code="selection_batch_rows_empty",
                attempt_refs=attempt_refs or (f"select-data-plan://selection/{plan.selection_run_id}/{plan.trade_date}",),
                reader_message="统一数据层返回了日线行，但没有可用的全市场选股标准化行。",
                source_metadata={"daily_row_count": len(daily_rows)},
            )
        )
    return tuple(rows), tuple(gaps)


def _direct_selection_rows_from_results(
    *,
    plan: SelectionRunPlan,
    results: tuple[DataResult, ...],
    attempt_refs: tuple[str, ...],
) -> tuple[tuple[Mapping[str, object], ...], tuple[DataGapRef, ...]]:
    mapped_rows_by_ticker: dict[str, Mapping[str, object]] = {}
    for result in results:
        for raw_row in result.rows:
            mapped = _map_direct_selection_row(raw_row)
            if mapped is None:
                continue
            ticker = str(mapped.get("ticker") or "").strip()
            if ticker and ticker not in mapped_rows_by_ticker:
                mapped_rows_by_ticker[ticker] = mapped
    daily_history_by_ticker = _individual_daily_history_by_ticker(results)
    shortfalls = _direct_history_shortfalls(plan=plan, results=results)
    shortfall_by_ticker = {item.ticker: item for item in shortfalls}
    rows: list[Mapping[str, object]] = []
    dropped: list[str] = []
    repaired: list[str] = []
    for row in mapped_rows_by_ticker.values():
        ticker = str(row.get("ticker") or "").strip()
        merged_row = _merge_repaired_history(row=row, repaired_history=daily_history_by_ticker.get(ticker, ()))
        if _row_has_required_history(row=merged_row, plan=plan):
            if ticker in shortfall_by_ticker:
                repaired.append(ticker)
            rows.append(merged_row)
            continue
        if not _row_has_required_history(row=merged_row, plan=plan):
            ticker = str(row.get("ticker") or "").strip()
            if ticker:
                dropped.append(ticker)
            continue
    gaps: list[DataGapRef] = []
    classification_counts: dict[str, int] = {}
    for item in shortfalls:
        classification_counts[item.classification] = classification_counts.get(item.classification, 0) + 1
    dropped_set = set(dropped)
    if dropped:
        gaps.append(
            _warn_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-direct-rows-dropped",
                gap_code="selection_batch_rows_dropped",
                attempt_refs=attempt_refs or (f"select-data-plan://selection/{plan.selection_run_id}/{plan.trade_date}",),
                reader_message=(
                    "selection batch 中部分本地预打包股票缺少足够历史日线，已剔除。"
                    f" count={len(dropped)}。"
                ),
                source_metadata={
                    "tickers_sample": tuple(dropped[:20]),
                    "required_history_days": max(_MIN_HISTORY_DAYS, int(plan.lookback_trading_days or 0)),
                    "history_shortfall_classification_counts": classification_counts,
                    "history_backfilled_tickers": tuple(repaired[:20]),
                    "listed_old_history_missing_tickers": tuple(
                        item.ticker
                        for item in shortfalls
                        if item.classification == "listed_old_history_missing_should_backfill"
                    )[:20],
                    "remaining_listed_old_history_missing_tickers": tuple(
                        item.ticker
                        for item in shortfalls
                        if item.classification == "listed_old_history_missing_should_backfill"
                        and item.ticker in dropped_set
                    )[:20],
                },
            )
        )
    return tuple(rows), tuple(gaps)


def _individual_daily_history_by_ticker(results: tuple[DataResult, ...]) -> Mapping[str, tuple[Mapping[str, float | str], ...]]:
    grouped: dict[str, dict[str, Mapping[str, float | str]]] = {}
    for result in results:
        if _result_data_type(result.request_id) != "daily_bar":
            continue
        for row in result.rows:
            if _row_has_direct_history(row):
                continue
            ticker = _ticker_from_row(row)
            history_row = _history_row(row)
            if ticker is None or history_row is None:
                continue
            grouped.setdefault(ticker, {})[str(history_row["date"])] = history_row
    return {
        ticker: tuple(sorted(rows_by_date.values(), key=lambda item: str(item["date"])))
        for ticker, rows_by_date in grouped.items()
    }


def _merge_repaired_history(
    *,
    row: Mapping[str, object],
    repaired_history: tuple[Mapping[str, float | str], ...],
) -> Mapping[str, object]:
    if not repaired_history:
        return row
    history = row.get("history")
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        return row
    by_date: dict[str, Mapping[str, object]] = {}
    for item in history:
        if not isinstance(item, Mapping):
            continue
        row_date = _row_date(item)
        if row_date is not None:
            by_date[row_date] = item
    for item in repaired_history:
        row_date = _row_date(item)
        if row_date is not None:
            by_date[row_date] = item
    if not by_date:
        return row
    merged = dict(row)
    merged["history"] = tuple(by_date[key] for key in sorted(by_date))
    latest = _history_row(merged["history"][-1])
    if latest is not None:
        for key in ("open", "high", "low", "close", "volume", "amount", "p_change_pct"):
            if key in latest:
                merged[key] = latest[key]
    return merged


def _latest_valuation_fields(
    *,
    ticker: str,
    trade_date: str,
    rows: tuple[Mapping[str, Any], ...],
) -> Mapping[str, object]:
    trade_day = date.fromisoformat(trade_date)
    latest_date: date | None = None
    latest_row: Mapping[str, Any] | None = None
    for row in rows:
        row_ticker = _ticker_from_row(row)
        if row_ticker is not None and row_ticker != ticker:
            continue
        row_date = _parse_date(row.get("date") or row.get("trade_date") or row.get("as_of"))
        if row_date is None or row_date > trade_day:
            continue
        if latest_date is None or row_date > latest_date:
            latest_date = row_date
            latest_row = row
    if latest_row is None:
        return {}
    result: dict[str, object] = {}
    for target, candidates in {
        "pe": ("pe", "pe_ttm"),
        "pb": ("pb",),
        "ps": ("ps", "ps_ttm"),
        "market_cap": ("market_cap", "total_mv", "circ_mv"),
    }.items():
        value = _first_float(latest_row, candidates)
        if value is not None:
            result[target] = value
    source_ref = _row_source_ref(latest_row)
    if source_ref:
        result["valuation_source_ref"] = source_ref
    return result


def _map_direct_selection_row(row: Mapping[str, Any]) -> Mapping[str, object] | None:
    history = row.get("history")
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        return None
    ticker = _ticker_from_row(row)
    company_name = _company_name_from_rows((row,))
    if ticker is None or company_name is None:
        return None
    mapped = {
        key: value
        for key, value in row.items()
        if key
        not in {
            "market",
            "dataset",
            "granularity",
            "provider_lineage",
            "source_raw_refs",
            "source_roles",
            "fresh_until",
            "field_set",
            "row",
        }
    }
    mapped["ticker"] = ticker
    mapped["company_name"] = company_name
    mapped.setdefault("source_ref", _row_source_ref(row))
    normalized_history = tuple(item for item in history if isinstance(item, Mapping))
    if normalized_history:
        mapped["history"] = normalized_history
        latest = _history_row(normalized_history[-1])
        if latest is not None:
            for key in ("open", "high", "low", "close", "volume", "amount", "p_change_pct"):
                if key in latest and mapped.get(key) is None:
                    mapped[key] = latest[key]
    return mapped


def _private_placement_fields(
    *,
    ticker: str,
    trade_date: str,
    rows: tuple[Mapping[str, Any], ...],
    coverage_available: bool,
) -> Mapping[str, object] | None:
    if not coverage_available:
        return None
    trade_day = date.fromisoformat(trade_date)
    latest_event_date: date | None = None
    latest_source_ref: str | None = None
    for row in rows:
        row_ticker = _ticker_from_row(row)
        if row_ticker is not None and row_ticker != ticker:
            continue
        if not _is_private_placement_event(row):
            continue
        event_date = _parse_date(row.get("event_date") or row.get("published_at"))
        if event_date is None or event_date > trade_day:
            continue
        if latest_event_date is None or event_date > latest_event_date:
            latest_event_date = event_date
            latest_source_ref = _row_source_ref(row)
    if latest_event_date is None:
        return {
            "private_placement_event_date": "none",
            "private_placement_days_since": 9999.0,
        }
    return {
        "private_placement_event_date": latest_event_date.isoformat(),
        "private_placement_days_since": float((trade_day - latest_event_date).days),
        "private_placement_source_ref": latest_source_ref or f"corporate_action://{ticker}/{latest_event_date.isoformat()}",
    }


def _selection_missing_strategy_required_fields(
    *,
    plan: SelectionRunPlan,
    rows: tuple[Mapping[str, object], ...],
) -> tuple[str, ...]:
    strategy = load_cn_a_selection_v1_strategy(plan.approved_strategy_config_ref)
    if strategy is None or not rows:
        return ()
    required_fields = _selection_strategy_required_source_fields(strategy=strategy)
    missing_fields = sorted(
        field
        for field in required_fields
        if field not in _OPTIONAL_SELECTION_STRATEGY_SOURCE_FIELDS
        if not all(
            _row_has_required_history(row=row, plan=plan) if field == "history" else row.get(field) is not None
            for row in rows
        )
    )
    return tuple(missing_fields)


def _selection_strategy_required_source_fields(*, strategy: ApprovedSelectionStrategy) -> tuple[str, ...]:
    required: set[str] = set()
    signal_dependencies = _selection_strategy_signal_source_dependencies(strategy=strategy)
    for rule in strategy.strategy_set:
        for field in rule.required_fields:
            required.update(_strategy_source_dependencies(field=field, signal_dependencies=signal_dependencies))
    return tuple(sorted(required))


def _selection_strategy_signal_source_dependencies(
    *,
    strategy: ApprovedSelectionStrategy,
) -> Mapping[str, tuple[str, ...]]:
    dependencies: dict[str, tuple[str, ...]] = {}
    for rule in strategy.strategy_set:
        signal_fields = tuple(field for field in rule.required_fields if field.startswith("strategy_signal_"))
        if not signal_fields:
            continue
        source_fields: set[str] = set()
        for field in rule.required_fields:
            if field in signal_fields:
                continue
            source_fields.update(_strategy_source_dependencies(field=field))
        for field in signal_fields:
            dependencies[field] = tuple(sorted(source_fields))
    return dependencies


def _strategy_source_dependencies(
    *,
    field: str,
    signal_dependencies: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[str, ...]:
    if signal_dependencies is not None and field in signal_dependencies:
        return signal_dependencies[field]
    dependencies = _SELECTION_STRATEGY_DERIVED_FIELD_DEPENDENCIES.get(field)
    if dependencies is not None:
        return dependencies
    return (field,)


def _row_has_required_history(*, row: Mapping[str, object], plan: SelectionRunPlan) -> bool:
    history = row.get("history")
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        return False
    required = max(_MIN_HISTORY_DAYS, int(plan.lookback_trading_days or 0))
    valid = 0
    for item in history:
        if not isinstance(item, Mapping):
            continue
        if all(item.get(field) is not None for field in ("open", "high", "low", "close", "volume")):
            valid += 1
    return valid >= required


def _failed_provider_result(
    *,
    plan: SelectionRunPlan,
    provider_plan: SelectionProviderBatchPlan,
    gap_code: str,
    message: str,
) -> SelectionProviderBatchResult:
    return SelectionProviderBatchResult(
        provider_batch_plan=provider_plan,
        attempt_refs=(),
        normalized_refs=(),
        rows=(),
        data_gaps=(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-{gap_code}",
                gap_code=gap_code,
                attempt_refs=(provider_plan.lineage_root_ref,),
                reader_message=message,
            ),
        ),
        warehouse_check_ref=None,
    )


def _gap_from_data_gap(
    *,
    plan: SelectionRunPlan,
    gap: DataGap,
    default_refs: tuple[str, ...],
) -> DataGapRef:
    reason = getattr(gap.reason, "value", str(gap.reason))
    data_type = str(gap.data_type)
    is_history_backfill = ":history_backfill:" in str(gap.request_id)
    severity = (
        DataGapSeverity.WARN
        if is_history_backfill or data_type in _OPTIONAL_SELECTION_SUPPLEMENTAL_DATA_TYPES
        else DataGapSeverity.BLOCKER
        if getattr(gap.severity, "value", str(gap.severity)) == "blocker"
        else DataGapSeverity.WARN
    )
    return DataGapRef(
        gap_id=f"{plan.selection_run_id}-{gap.request_id}-{reason}",
        domain="selection",
        gap_code=f"selection_data_api_{reason}",
        severity=severity,
        attempt_refs=tuple(gap.evidence_refs or default_refs),
        reader_message=gap.human_readable,
        source_metadata={
            "request_id": gap.request_id,
            "data_type": gap.data_type,
            "granularity": gap.granularity,
            "required_fields": tuple(gap.required_fields),
            "provider_ids_tried": tuple(gap.provider_ids_tried),
        },
    )


def _blocker_gap(
    *,
    gap_id: str,
    gap_code: str,
    attempt_refs: tuple[str, ...],
    reader_message: str,
    source_metadata: Mapping[str, Any] | None = None,
) -> DataGapRef:
    return DataGapRef(
        gap_id=gap_id,
        domain="selection",
        gap_code=gap_code,
        severity=DataGapSeverity.BLOCKER,
        attempt_refs=attempt_refs,
        reader_message=reader_message,
        source_metadata=dict(source_metadata) if source_metadata else None,
    )


def _warn_gap(
    *,
    gap_id: str,
    gap_code: str,
    attempt_refs: tuple[str, ...],
    reader_message: str,
    source_metadata: Mapping[str, Any] | None = None,
) -> DataGapRef:
    return DataGapRef(
        gap_id=gap_id,
        domain="selection",
        gap_code=gap_code,
        severity=DataGapSeverity.WARN,
        attempt_refs=attempt_refs,
        reader_message=reader_message,
        source_metadata=dict(source_metadata) if source_metadata else None,
    )


def _cn_a_provider_candidates() -> tuple[str, ...]:
    provider_ids: list[str] = []
    for plugin in iter_minimal_market_plugins():
        caps = plugin.capabilities()
        endpoints = tuple(getattr(caps, "endpoints", ()) or ())
        if any(_enum_value(getattr(endpoint, "market", "")) == Market.CN_A.value for endpoint in endpoints):
            provider_ids.append(str(getattr(caps, "provider_id")))
    return tuple(dict.fromkeys(provider_ids)) or ("cn_a_provider_unconfigured",)


def _enum_value(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _open_database() -> Any:
    uri = os.environ.get("DATA_GATEWAY_MONGODB_URI", "").strip() or os.environ.get("CN_A_MONGODB_URI", "").strip()
    if not uri:
        raise RuntimeError("DATA_GATEWAY_MONGODB_URI is required for selection data layer access")
    database_name = (
        os.environ.get("DATA_GATEWAY_MONGODB_DATABASE", "").strip()
        or os.environ.get("CN_A_MONGODB_DATABASE", "").strip()
        or _database_name_from_uri(uri)
    )
    from pymongo import MongoClient

    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return client[database_name]


def _database_name_from_uri(uri: str) -> str:
    tail = uri.rsplit("/", 1)[-1].split("?", 1)[0].strip()
    return tail or "claw_trade"


def _normalized_ref(dataset_ref: str) -> str:
    if dataset_ref.startswith("normalized://mongo/normalized_datasets/") or dataset_ref.startswith("mongo://normalized_datasets/"):
        return dataset_ref
    return f"normalized://mongo/normalized_datasets/{dataset_ref}"


def _result_data_type(request_id: str) -> str:
    return request_id.rsplit(":", 1)[-1]


def _ticker_from_row(row: Mapping[str, Any]) -> str | None:
    for key in ("ticker", "symbol_id", "ts_code", "code"):
        value = row.get(key)
        if value is None:
            continue
        ticker = _normalize_cn_a_ticker(str(value))
        if ticker is not None:
            return ticker
    return None


def _normalize_cn_a_ticker(value: str) -> str | None:
    text = value.strip().upper().replace("_", ".")
    if not text:
        return None
    if "." in text:
        code, suffix = text.split(".", 1)
        if len(code) == 6 and code.isdigit() and suffix in {"SH", "SZ", "BJ"}:
            return f"{code}.{suffix}"
        if suffix.isdigit() and len(suffix) == 6 and code in {"SH", "SZ", "BJ"}:
            return f"{suffix}.{code}"
    if len(text) == 8 and text[:2] in {"SH", "SZ", "BJ"} and text[2:].isdigit():
        return f"{text[2:]}.{text[:2]}"
    if len(text) == 6 and text.isdigit():
        if text.startswith(("600", "601", "603", "605", "688", "689")):
            return f"{text}.SH"
        if text.startswith(("000", "001", "002", "003", "300", "301")):
            return f"{text}.SZ"
        if text.startswith(("43", "83", "87", "88", "92")):
            return f"{text}.BJ"
    return None


def _listing_date_from_row(row: Mapping[str, Any]) -> date | None:
    for key in ("list_date", "listing_date", "ipo_date", "ipoDate", "listed_date", "上市日期"):
        parsed = _parse_date(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _listing_dates_for_tickers(tickers: Sequence[str]) -> Mapping[str, date]:
    needed = set(_dedupe(tickers))
    if not needed:
        return {}
    dates: dict[str, date] = {}
    dates.update(_baostock_listing_dates_for_tickers(needed))
    missing_bj = tuple(sorted(ticker for ticker in needed - set(dates) if ticker.endswith(".BJ")))
    if missing_bj:
        dates.update(_akshare_bj_listing_dates_for_tickers(missing_bj))
    return dates


def _baostock_listing_dates_for_tickers(tickers: set[str]) -> Mapping[str, date]:
    try:
        import baostock as bs  # type: ignore

        login = bs.login()
        if str(getattr(login, "error_code", "")) not in {"0", ""}:
            return {}
        try:
            result = bs.query_stock_basic()
            fields = tuple(getattr(result, "fields", ()) or ())
            output: dict[str, date] = {}
            while result.next():
                raw = dict(zip(fields, result.get_row_data(), strict=False))
                ticker = _normalize_cn_a_ticker(str(raw.get("code") or ""))
                if ticker not in tickers:
                    continue
                parsed = _parse_date(raw.get("ipoDate") or raw.get("ipo_date") or raw.get("listDate") or raw.get("list_date"))
                if parsed is not None:
                    output[ticker] = parsed
            return output
        finally:
            try:
                bs.logout()
            except Exception:
                pass
    except Exception:
        return {}


def _akshare_bj_listing_dates_for_tickers(tickers: Sequence[str]) -> Mapping[str, date]:
    needed = set(tickers)
    try:
        import akshare as ak  # type: ignore

        dataframe = ak.stock_info_bj_name_code()
        output: dict[str, date] = {}
        for raw in dataframe.to_dict(orient="records"):
            ticker = _normalize_cn_a_ticker(str(raw.get("证券代码") or raw.get("code") or ""))
            if ticker not in needed:
                continue
            parsed = _parse_date(raw.get("上市日期") or raw.get("list_date"))
            if parsed is not None:
                output[ticker] = parsed
        return output
    except Exception:
        return {}


def _company_name_from_rows(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for row in rows:
        for key in ("company_name", "name", "stock_name", "code_name", "security_name"):
            value = row.get(key)
            if value is None:
                continue
            text = str(value).strip()
            if text:
                return text
    return None


def _industry_from_rows(rows: Sequence[Mapping[str, Any]]) -> str | None:
    for row in rows:
        value = row.get("industry") or row.get("sector")
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _history_row(row: Mapping[str, Any]) -> Mapping[str, float | str] | None:
    row_date = _row_date(row)
    open_value = _float_value(row.get("open"))
    high_value = _float_value(row.get("high"))
    low_value = _float_value(row.get("low"))
    close_value = _float_value(row.get("close") or row.get("price"))
    volume_value = _float_value(row.get("volume"))
    if row_date is None or None in {open_value, high_value, low_value, close_value, volume_value}:
        return None
    mapped: dict[str, float | str] = {
        "date": row_date,
        "open": open_value,
        "high": high_value,
        "low": low_value,
        "close": close_value,
        "volume": volume_value,
    }
    for target, keys in {
        "amount": ("amount", "turnover"),
        "p_change_pct": ("p_change_pct", "pct_chg", "change_pct"),
    }.items():
        value = _first_float(row, keys)
        if value is not None:
            mapped[target] = value
    return mapped


def _row_date(row: Mapping[str, Any]) -> str | None:
    value = row.get("date") or row.get("trade_date") or row.get("period_start")
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed is not None else None


def _row_source_ref(row: Mapping[str, Any]) -> str:
    value = row.get("dataset_ref") or row.get("source_ref")
    if value is not None and str(value).strip():
        return _normalized_ref(str(value).strip())
    lineage = row.get("provider_lineage")
    if isinstance(lineage, Mapping):
        provider_id = lineage.get("provider_id") or lineage.get("provider")
        endpoint_id = lineage.get("endpoint_id") or lineage.get("endpoint")
        if provider_id and endpoint_id:
            return f"provider://{provider_id}/{endpoint_id}"
    return "normalized://mongo/normalized_datasets/unknown"


def _is_private_placement_event(row: Mapping[str, Any]) -> bool:
    values = " ".join(
        str(row.get(key) or "")
        for key in ("event_type", "title", "summary", "body", "body_ref")
    )
    return any(token in values for token in ("定向", "非公开", "私募", "定增", "private placement"))


def _parse_date(value: object) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        text = f"{text[:4]}-{text[4:6]}-{text[6:]}"
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _first_float(row: Mapping[str, Any], keys: tuple[str, ...]) -> float | None:
    for key in keys:
        value = _float_value(row.get(key))
        if value is not None:
            return value
    return None


def _float_value(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        text = str(value).strip()
        if not text:
            return None
        return float(text)
    except (TypeError, ValueError):
        return None


def _dedupe(values: Sequence[str] | Any) -> tuple[str, ...]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return tuple(result)


def _write_evidence(
    *,
    evidence_root: Path | None,
    plan: SelectionRunPlan,
    requests: tuple[DataRequest, ...],
    results: tuple[DataResult, ...],
    provider_result: SelectionProviderBatchResult,
) -> None:
    if evidence_root is None:
        return
    payload = {
        "schema_version": "selection_data_api_batch_bridge.v1",
        "selection_run_id": plan.selection_run_id,
        "trade_date": plan.trade_date,
        "requests": [item.model_dump(mode="json") for item in requests],
        "results": [item.model_dump(mode="json") for item in results],
        "attempt_refs": list(provider_result.attempt_refs),
        "normalized_refs": list(provider_result.normalized_refs),
        "rows": len(provider_result.rows),
        "data_gaps": [
            {
                "gap_id": gap.gap_id,
                "gap_code": gap.gap_code,
                "severity": gap.severity.value,
                "attempt_refs": list(gap.attempt_refs),
                "reader_message": gap.reader_message,
                "source_metadata": dict(gap.source_metadata or {}),
            }
            for gap in provider_result.data_gaps
        ],
    }
    target = evidence_root / plan.trade_date / f"{plan.selection_run_id}-data-api-batch.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
