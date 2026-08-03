"""Private CN_A selection batch implementation for the unified data layer.

Public callers should use :mod:`claw_trade.data_gateway.selection_api`.
This module remains data-layer implementation code, not an upper-layer entrypoint.
"""

from __future__ import annotations

import ctypes
import gc
import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from itertools import groupby
from pathlib import Path
from time import monotonic
from typing import Any, Callable, Iterable, Mapping, Sequence

from claw_trade.data_gateway.coordination.scheduler import DataRunScheduleContext, DataRunScheduler
from claw_trade.data_gateway.models import (
    DataGap,
    DataRequest,
    DataResult,
    DataResultStatus,
    FetchResult,
    GapReason,
    GapSeverity,
    Market,
)
from claw_trade.data_gateway.needs import DataNeed
from claw_trade.data_gateway.planner import plan_public_data_requests
from claw_trade.data_gateway.public_api import PublicDataRequest, PublicRequestPriority
from claw_trade.data_gateway.report_evidence import (
    _DataNeedProviderTimeout,
    _data_need_result_from_fetch,
    _provider_call_batch,
    _provider_call_timer,
)
from claw_trade.data_gateway.refs import normalize_normalized_dataset_ref
from claw_trade.data_gateway.runtime import build_data_gateway_runtime_from_env
from claw_trade.data_gateway.warehouse import DatasetRecord, DatasetRepository
from claw_trade.data_gateway.warehouse.selection_columnar import (
    SelectionColumnarManifest,
    SelectionColumnarWarehouse,
)
from claw_trade.data_gateway.warehouse.trading_calendar import is_expected_daily_date
from claw_trade.data_gateway.warehouse.warehouse import _source_role_rank
from claw_trade.production.data_work_lock import data_work_lock_enabled, hold_data_work_lock
from claw_trade.selection.data_job import SelectionDataFetchProgress, SelectionDataNeedResult
from claw_trade.selection.engine import ApprovedSelectionStrategy
from claw_trade.selection.features import (
    SelectionFeatureError,
    build_feature_snapshot,
    normalize_selection_inputs,
)
from claw_trade.selection.models import (
    DataGapRef,
    DataGapSeverity,
    SelectionBatchScope,
    SelectionMarket,
    SelectionProfile,
    SelectionDataNeedAudit,
    SelectionRunPlan,
)
from claw_trade.selection.strategy_config import (
    load_selection_strategy,
    load_selection_strategy_config_ref,
)

_CN_A_SELECTION_COVERAGE_GROUP = "cn_a_selection_batch"
_CRYPTO_SELECTION_COVERAGE_GROUP = "crypto_selection_batch"
_CN_A_SELECTION_TIMEZONE = "Asia/Shanghai"
_CN_A_SELECTION_CALENDAR = "CN_A_SSE_SZSE"
_CRYPTO_SELECTION_TIMEZONE = "UTC"
_CRYPTO_SELECTION_CALENDAR = "CRYPTO_24_7"
_CN_A_DEFAULT_UNIVERSE_SCOPE = "all_a_shares"
_CRYPTO_DEFAULT_UNIVERSE_SCOPE = "spot_usdt"
_CRYPTO_HISTORY_COLUMNAR_ROOT = Path(
    os.environ.get("CLAW_TRADE_CRYPTO_HISTORY_COLUMNAR_ROOT") or "data/crypto-history-full/normalized-columnar-usdt-only"
)
_DEFAULT_LOOKBACK_TRADING_DAYS = 260
_MIN_HISTORY_DAYS = 250
_UNIVERSE_REFRESH_CHUNK_SIZE = 5
_LOCAL_FEATURE_REF_SAMPLE_LIMIT = 200
_SELECTION_FEATURE_ROW_LIMIT = 12_000
_FULL_MARKET_MIN_TICKERS = 5_000
_FULL_MARKET_MIN_FEATURE_COVERAGE_RATIO = 0.8
_SELECT_COMMAND_REFRESH_DEADLINE_SECONDS = 60
_BACKGROUND_REFRESH_DEADLINE_SECONDS = 60 * 60

_SELECTION_DAILY_REQUEST: tuple[str, str, tuple[str, ...]] = (
    "daily_bar",
    "daily",
    ("date", "open", "high", "low", "close", "volume", "amount"),
)
_SELECTION_DAILY_REQUESTS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    _SELECTION_DAILY_REQUEST,
)
_SELECTION_DAILY_QUERY_FIELDS = (
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "symbol_id",
)
_SELECTION_HISTORY_QUERY_FIELDS = (
    *_SELECTION_DAILY_QUERY_FIELDS,
    "trade_date",
    "price",
    "turnover",
    "amount_unit",
    "p_change_pct",
    "pct_chg",
    "change_pct",
    "company_name",
    "name",
    "stock_name",
    "code_name",
    "security_name",
    "industry",
    "sector",
    "market",
    "provider_lineage",
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
_LOGGER = logging.getLogger("uvicorn.error")


def _selection_market_defaults(market: SelectionMarket) -> tuple[str, str, str]:
    if market == SelectionMarket.CRYPTO:
        return (
            _CRYPTO_SELECTION_COVERAGE_GROUP,
            _CRYPTO_SELECTION_TIMEZONE,
            _CRYPTO_SELECTION_CALENDAR,
        )
    return (
        _CN_A_SELECTION_COVERAGE_GROUP,
        _CN_A_SELECTION_TIMEZONE,
        _CN_A_SELECTION_CALENDAR,
    )


def _selection_universe_scope(market: SelectionMarket) -> str:
    return _CN_A_DEFAULT_UNIVERSE_SCOPE if market == SelectionMarket.CN_A else _CRYPTO_DEFAULT_UNIVERSE_SCOPE


def _selection_data_request_specs(market: SelectionMarket) -> tuple[tuple[str, str, tuple[str, ...]], ...]:
    if market == SelectionMarket.CRYPTO:
        return _SELECTION_DAILY_REQUESTS
    return _SELECTION_REQUESTS


def _selection_market_value(plan_market: SelectionMarket) -> Market:
    return Market(str(plan_market.value))


@dataclass(frozen=True)
class _SelectionGatewayContext:
    data_service: Any
    repository: DatasetRepository
    data_need_executor: Callable[[SelectionRunPlan, Sequence[PublicDataRequest]], tuple[DataResult, ...]]


@dataclass(frozen=True)
class _HistoryShortfall:
    ticker: str
    valid_days: int
    first_date: date | None
    list_date: date | None
    classification: str


@dataclass(frozen=True)
class _LocalFeatureRowsResult:
    rows: tuple[Mapping[str, object], ...]
    normalized_refs: tuple[str, ...]
    attempt_refs: tuple[str, ...]
    data_gaps: tuple[DataGapRef, ...]
    columnar_manifest_ref: str | None = None
    columnar_manifest_sha256: str | None = None


def build_selection_data_need_audit(
    *,
    market: SelectionMarket,
    profile: SelectionProfile,
    trade_date: str,
    plan_id: str | None = None,
) -> SelectionDataNeedAudit:
    if (market, profile) not in {
        (SelectionMarket.CN_A, SelectionProfile.CN_A),
        (SelectionMarket.CRYPTO, SelectionProfile.CRYPTO),
    }:
        raise ValueError("selection batch v1 仅支持 CN_A/CRYPTO")
    market_slug = market.value.lower()
    coverage_group, timezone, _calendar = _selection_market_defaults(market)
    resolved_plan_id = plan_id or f"plan://selection/{market_slug}/{trade_date}/batch-v1"
    return SelectionDataNeedAudit(
        plan_id=resolved_plan_id,
        scope=SelectionBatchScope.SELECTION_BATCH,
        market=market,
        profile=profile,
        trade_date=trade_date,
        lookback_trading_days=_DEFAULT_LOOKBACK_TRADING_DAYS,
        universe_scope=_selection_universe_scope(market),
        coverage_groups=(coverage_group, "universe", "daily", "fundamental"),
        ttl_policy_ref=f"ttl://selection/{market_slug}/batch-v1/900s",
        lineage_root_ref=f"lineage://selection/{market_slug}/{trade_date}/batch-v1",
    )


def fetch_selection_batch_from_data_gateway(
    plan: SelectionRunPlan,
    *,
    evidence_root: Path | None = None,
    progress_callback: Callable[[SelectionDataFetchProgress], None] | None = None,
) -> SelectionDataNeedResult:
    if data_work_lock_enabled():
        with hold_data_work_lock(exclusive=False, blocking=True):
            return _fetch_selection_batch_from_data_gateway_unlocked(
                plan,
                evidence_root=evidence_root,
                progress_callback=progress_callback,
            )
    return _fetch_selection_batch_from_data_gateway_unlocked(
        plan,
        evidence_root=evidence_root,
        progress_callback=progress_callback,
    )


def _fetch_selection_batch_from_data_gateway_unlocked(
    plan: SelectionRunPlan,
    *,
    evidence_root: Path | None = None,
    progress_callback: Callable[[SelectionDataFetchProgress], None] | None = None,
) -> SelectionDataNeedResult:
    data_need_audit = build_selection_data_need_audit(
        market=plan.market,
        profile=plan.profile,
        trade_date=plan.trade_date,
        plan_id=plan.data_need_audit_ref,
    )
    columnar_warehouse = SelectionColumnarWarehouse.default()
    columnar_manifest = columnar_warehouse.load_valid_manifest(plan=plan)
    if columnar_manifest is not None and not _columnar_manifest_has_sufficient_full_market_coverage(
        plan=plan,
        manifest=columnar_manifest,
    ):
        columnar_manifest = None
    if columnar_manifest is not None:
        _notify_fetch_progress(
            progress_callback,
            label="读取列式选股仓库",
            completed=1,
            total=1,
        )
        rows = columnar_warehouse.read_feature_rows(
            manifest=columnar_manifest,
            columns=_selection_feature_projection_columns(plan),
            row_limit=_SELECTION_FEATURE_ROW_LIMIT,
        )
        result = _provider_result_from_columnar_manifest(
            data_need_audit=data_need_audit,
            manifest=columnar_manifest,
            rows=rows,
        )
        _write_evidence(evidence_root=evidence_root, plan=plan, requests=(), results=(), provider_result=result)
        return result

    if plan.market == SelectionMarket.CRYPTO:
        result = _provider_result_from_crypto_seed_columnar(
            plan=plan,
            data_need_audit=data_need_audit,
            progress_callback=progress_callback,
        )
        _write_evidence(evidence_root=evidence_root, plan=plan, requests=(), results=(), provider_result=result)
        return result

    try:
        gateway = _build_selection_gateway_context()
    except Exception as exc:  # noqa: BLE001
        result = _failed_provider_result(
            plan=plan,
            data_need_audit=data_need_audit,
            gap_code="selection_data_api_unavailable",
            message=(
                "selection batch 无法连接统一 DataAPI 运行时，"
                f"任务按 fail closed 失败：{type(exc).__name__}: {exc}"
            ),
        )
        _write_evidence(evidence_root=evidence_root, plan=plan, requests=(), results=(), provider_result=result)
        return result

    requests = _selection_data_requests(
        plan,
        specs=(_SELECTION_DAILY_REQUEST,),
        freshness_policy="warehouse_only",
        consumer="select",
        consumer_id=f"{plan.selection_run_id}:coverage_check",
    )
    _notify_fetch_progress(
        progress_callback,
        label="检查本地全市场日线缓存",
        completed=0,
        total=len(requests),
    )
    try:
        raw_results = tuple(gateway.data_service.read_warehouse_batch(requests))
    except Exception as exc:  # noqa: BLE001
        result = _failed_provider_result(
            plan=plan,
            data_need_audit=data_need_audit,
            gap_code="selection_data_api_failed",
            message=f"selection batch 调用统一 DataAPI 失败，任务按 fail closed 失败：{type(exc).__name__}: {exc}",
        )
        _write_evidence(evidence_root=evidence_root, plan=plan, requests=requests, results=(), provider_result=result)
        return result
    raw_row_count = sum(len(result.rows) for result in raw_results)
    results = tuple(_data_result_without_rows(result) for result in raw_results)
    del raw_results
    _release_fetch_batch_memory()
    _LOGGER.info(
        "selection local daily cache check returned run_id=%s results=%s rows=%s dataset_refs=%s attempts=%s gaps=%s",
        plan.selection_run_id,
        len(results),
        raw_row_count,
        sum(len(result.dataset_refs) for result in results),
        sum(len(result.attempt_refs) for result in results),
        sum(len(result.gaps) for result in results),
    )
    _notify_fetch_progress(
        progress_callback,
        label="本地全市场日线检查完成",
        completed=len(requests),
        total=len(requests),
    )
    _LOGGER.info("selection local daily cache progress saved run_id=%s", plan.selection_run_id)

    universe_refresh_needs = _selection_universe_refresh_needs(plan=plan, results=results)
    _LOGGER.info(
        "selection universe refresh needs planned run_id=%s need_count=%s",
        plan.selection_run_id,
        len(universe_refresh_needs),
    )
    if universe_refresh_needs:
        universe_refresh_results: list[DataResult] = []
        completed_refresh_requests = 0
        total_refresh_requests = len(universe_refresh_needs)
        _notify_fetch_progress(
            progress_callback,
            label="补齐全市场日线数据",
            completed=completed_refresh_requests,
            total=total_refresh_requests,
        )
        try:
            for refresh_chunk in _refresh_need_chunks(universe_refresh_needs, _UNIVERSE_REFRESH_CHUNK_SIZE):
                chunk_started = monotonic()
                first_need = refresh_chunk[0]
                _LOGGER.info(
                    "selection universe refresh chunk start run_id=%s completed=%s total=%s need_id=%s start=%s end=%s",
                    plan.selection_run_id,
                    completed_refresh_requests,
                    total_refresh_requests,
                    first_need.request_id,
                    first_need.time_range_start,
                    first_need.time_range_end,
                )
                chunk_results = tuple(_execute_selection_data_needs(gateway=gateway, plan=plan, needs=refresh_chunk))
                _LOGGER.info(
                    "selection universe refresh chunk data_need_returned run_id=%s need_id=%s elapsed_ms=%s rows=%s attempts=%s gaps=%s",
                    plan.selection_run_id,
                    first_need.request_id,
                    int((monotonic() - chunk_started) * 1000),
                    sum(len(result.rows) for result in chunk_results),
                    sum(len(result.attempt_refs) for result in chunk_results),
                    sum(len(result.gaps) for result in chunk_results),
                )
                universe_refresh_results.extend(_data_result_without_rows(result) for result in chunk_results)
                del chunk_results
                _release_fetch_batch_memory()
                _LOGGER.info(
                    "selection universe refresh chunk memory_released run_id=%s need_id=%s elapsed_ms=%s",
                    plan.selection_run_id,
                    first_need.request_id,
                    int((monotonic() - chunk_started) * 1000),
                )
                completed_refresh_requests += len(refresh_chunk)
                _notify_fetch_progress(
                    progress_callback,
                    label="补齐全市场日线数据",
                    completed=completed_refresh_requests,
                    total=total_refresh_requests,
                )
        except Exception as exc:  # noqa: BLE001
            initial_result = _provider_result_from_check_results(
                plan=plan,
                data_need_audit=data_need_audit,
                results=results,
            )
            result = _append_data_gap(
                initial_result,
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-selection-batch-universe-refresh-failed",
                    gap_code="selection_batch_universe_refresh_failed",
                    attempt_refs=initial_result.attempt_refs or (data_need_audit.lineage_root_ref,),
                    reader_message=(
                        "selection batch 全市场日线补数已展开为 DataNeed，但统一数据层调用失败，"
                        f"任务按 fail closed 失败：{type(exc).__name__}: {exc}"
                    ),
                    source_metadata={
                        "need_count": len(universe_refresh_needs),
                        "completed_need_count": completed_refresh_requests,
                        "tickers_sample": tuple(
                            need.instrument for need in universe_refresh_needs[:20] if need.instrument != plan.universe_scope
                        ),
                        "trade_dates_sample": tuple(
                            str(need.time_range_start)
                            for need in universe_refresh_needs[:20]
                            if need.instrument == plan.universe_scope
                        ),
                    },
                ),
            )
            _write_evidence(
                evidence_root=evidence_root,
                plan=plan,
                requests=requests,
                results=(*results, *universe_refresh_results),
                provider_result=result,
            )
            return result
        recheck_requests = _selection_data_requests(
            plan,
            specs=(_SELECTION_DAILY_REQUEST,),
            freshness_policy="warehouse_only",
            consumer="select",
            consumer_id=f"{plan.selection_run_id}:coverage_check",
        )
        _notify_fetch_progress(
            progress_callback,
            label="重新读取本地全市场日线缓存",
            completed=0,
            total=len(recheck_requests),
        )
        try:
            raw_recheck_results = tuple(gateway.data_service.read_warehouse_batch(recheck_requests))
        except Exception as exc:  # noqa: BLE001
            initial_result = _provider_result_from_check_results(
                plan=plan,
                data_need_audit=data_need_audit,
                results=(*results, *universe_refresh_results),
            )
            result = _append_data_gap(
                initial_result,
                _blocker_gap(
                    gap_id=f"{plan.selection_run_id}-selection-batch-universe-refresh-recheck-failed",
                    gap_code="selection_batch_universe_refresh_recheck_failed",
                    attempt_refs=initial_result.attempt_refs or (data_need_audit.lineage_root_ref,),
                    reader_message=(
                        "selection batch 全市场日线补数已完成，但重新读取本地缓存失败，"
                        f"任务按 fail closed 失败：{type(exc).__name__}: {exc}"
                    ),
                    source_metadata={"request_count": len(recheck_requests)},
                ),
            )
            _write_evidence(
                evidence_root=evidence_root,
                plan=plan,
                requests=(*requests, *recheck_requests),
                results=(*results, *universe_refresh_results),
                provider_result=result,
            )
            return result
        recheck_results = tuple(_data_result_without_rows(result) for result in raw_recheck_results)
        del raw_recheck_results
        _release_fetch_batch_memory()
        _notify_fetch_progress(
            progress_callback,
            label="重新读取本地全市场日线缓存",
            completed=len(recheck_requests),
            total=len(recheck_requests),
        )
        requests = (*requests, *recheck_requests)
        results = (*results, *universe_refresh_results, *recheck_results)

    _notify_fetch_progress(
        progress_callback,
        label="流式计算本地选股特征",
        completed=0,
        total=1,
    )
    stream_started = monotonic()
    try:
        local_feature_rows = _selection_feature_rows_from_repository(
            plan=plan,
            repository=gateway.repository,
            progress_callback=progress_callback,
        )
    except Exception as exc:  # noqa: BLE001
        initial_result = _provider_result_from_check_results(
            plan=plan,
            data_need_audit=data_need_audit,
            results=results,
        )
        result = _append_data_gap(
            initial_result,
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-local-row-read-failed",
                gap_code="selection_batch_local_row_read_failed",
                attempt_refs=initial_result.attempt_refs or (data_need_audit.lineage_root_ref,),
                reader_message=(
                    "selection batch 本地覆盖检查已完成，但流式读取本地标准化行失败，"
                    f"任务按 fail closed 失败：{type(exc).__name__}: {exc}"
                ),
                source_metadata={"read_mode": "stream_by_symbol"},
            ),
        )
        _write_evidence(
            evidence_root=evidence_root,
            plan=plan,
            requests=requests,
            results=results,
            provider_result=result,
        )
        return result
    _notify_fetch_progress(
        progress_callback,
        label="流式计算本地选股特征",
        completed=1,
        total=1,
    )
    _LOGGER.info(
        "selection local feature rows built run_id=%s elapsed_ms=%s rows=%s normalized_refs=%s attempts=%s gaps=%s",
        plan.selection_run_id,
        int((monotonic() - stream_started) * 1000),
        len(local_feature_rows.rows),
        len(local_feature_rows.normalized_refs),
        len(local_feature_rows.attempt_refs),
        len(local_feature_rows.data_gaps),
    )
    result = _provider_result_from_local_feature_rows(
        plan=plan,
        data_need_audit=data_need_audit,
        results=results,
        local_feature_rows=local_feature_rows,
    )
    _write_evidence(evidence_root=evidence_root, plan=plan, requests=requests, results=results, provider_result=result)
    return result


def _refresh_need_chunks(items: Sequence[PublicDataRequest], size: int) -> tuple[tuple[PublicDataRequest, ...], ...]:
    if size <= 0:
        raise ValueError("chunk size must be positive")
    chunks: list[tuple[PublicDataRequest, ...]] = []
    current: list[PublicDataRequest] = []
    previous_date: date | None = None
    for item in items:
        item_date = _parse_date(item.time_range_start)
        should_split = (
            len(current) >= size
            or (previous_date is not None and item_date is not None and item_date != previous_date + timedelta(days=1))
            or (previous_date is not None and item_date is None)
        )
        if should_split and current:
            chunks.append(tuple(current))
            current = []
        current.append(item)
        previous_date = item_date
    if current:
        chunks.append(tuple(current))
    return tuple(chunks)


def _should_retry_selection_universe_cached_empty(*, plan: SelectionRunPlan, need: DataNeed) -> bool:
    return _should_force_selection_universe_provider_refresh(plan=plan, need=need)


def _should_force_selection_universe_provider_refresh(*, plan: SelectionRunPlan, need: DataNeed) -> bool:
    return (
        plan.market == SelectionMarket.CN_A
        and str(getattr(need, "consumer", "") or "").strip() == "select"
        and str(getattr(need, "instrument", "") or "").strip() == str(plan.universe_scope or "").strip()
        and ":selection:universe_refresh:" in str(getattr(need, "need_id", "") or "")
        and str(getattr(need, "freshness_policy", "") or "").strip() == "trading_day"
    )


def _requires_scoped_full_market_latest_rows(plan: SelectionRunPlan) -> bool:
    return bool(plan.universe_scope) and _full_market_expected_min_tickers(plan) > 0


def _notify_fetch_progress(
    callback: Callable[[SelectionDataFetchProgress], None] | None,
    *,
    label: str,
    completed: int,
    total: int,
) -> None:
    if callback is None:
        return
    callback(SelectionDataFetchProgress(label=label, completed=completed, total=total))


def _data_result_without_rows(result: DataResult) -> DataResult:
    return result.model_copy(update={"rows": ()})


def _release_fetch_batch_memory() -> None:
    gc.collect()
    if os.name != "posix":
        return
    try:
        libc = ctypes.CDLL("libc.so.6")
        trim = getattr(libc, "malloc_trim", None)
        if callable(trim):
            trim(0)
    except Exception:
        return


def _provider_result_from_data_results(
    *,
    plan: SelectionRunPlan,
    data_need_audit: SelectionDataNeedAudit,
    results: tuple[DataResult, ...],
) -> SelectionDataNeedResult:
    del plan, data_need_audit, results
    raise RuntimeError("selection_data_result_rows_direct_path_disabled")


def _provider_result_from_check_results(
    *,
    plan: SelectionRunPlan,
    data_need_audit: SelectionDataNeedAudit,
    results: tuple[DataResult, ...],
) -> SelectionDataNeedResult:
    attempt_refs = _dedupe(ref for item in results for ref in item.attempt_refs)
    dataset_refs = _dedupe(ref for item in results for ref in item.dataset_refs)
    normalized_refs = tuple(_normalized_ref(ref, market=plan.market) for ref in dataset_refs)
    data_gaps = tuple(
        _gap_from_data_gap(plan=plan, gap=gap, default_refs=attempt_refs or (data_need_audit.lineage_root_ref,))
        for item in results
        for gap in item.gaps
    )
    return SelectionDataNeedResult(
        data_need_audit=data_need_audit,
        attempt_refs=attempt_refs,
        normalized_refs=normalized_refs,
        rows=(),
        data_gaps=data_gaps,
        warehouse_check_ref=(
            f"warehouse-check://selection/{plan.selection_run_id}/{plan.trade_date}/coverage-check"
            if normalized_refs
            else None
        ),
    )


def _provider_result_from_local_feature_rows(
    *,
    plan: SelectionRunPlan,
    data_need_audit: SelectionDataNeedAudit,
    results: tuple[DataResult, ...],
    local_feature_rows: _LocalFeatureRowsResult,
) -> SelectionDataNeedResult:
    attempt_refs = _dedupe(
        (
            *(ref for item in results for ref in item.attempt_refs),
            *local_feature_rows.attempt_refs,
        )
    )
    normalized_refs = _normalize_normalized_refs(local_feature_rows.normalized_refs, market=plan.market)
    raw_data_gaps = tuple(
        gap
        for item in results
        for gap in item.gaps
        if not _is_repaired_universe_refresh_gap(
            plan=plan,
            gap=gap,
            rows=local_feature_rows.rows,
            results=results,
        )
    )
    data_gaps = [
        *(
            _gap_from_data_gap(plan=plan, gap=gap, default_refs=attempt_refs or (data_need_audit.lineage_root_ref,))
            for gap in raw_data_gaps
        ),
        *local_feature_rows.data_gaps,
    ]
    missing_strategy_fields = _selection_missing_strategy_required_fields(plan=plan, rows=local_feature_rows.rows)
    if missing_strategy_fields:
        data_gaps.append(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-strategy-fields-missing",
                gap_code="selection_batch_strategy_fields_missing",
                attempt_refs=attempt_refs or (data_need_audit.lineage_root_ref,),
                reader_message=(
                    "selection batch 标准化行缺少 approved strategy 的源字段，"
                    f"不能生成 approved candidate cache：{', '.join(missing_strategy_fields)}。"
                ),
                source_metadata={"missing_fields": missing_strategy_fields},
            )
        )
    return SelectionDataNeedResult(
        data_need_audit=data_need_audit,
        attempt_refs=attempt_refs,
        normalized_refs=normalized_refs,
        rows=local_feature_rows.rows,
        data_gaps=tuple(data_gaps),
        warehouse_check_ref=(
            f"warehouse-check://selection-columnar/{plan.market.value}/{plan.profile.value}/{plan.trade_date}"
            if normalized_refs
            else None
        ),
        columnar_manifest_ref=local_feature_rows.columnar_manifest_ref,
        columnar_manifest_sha256=local_feature_rows.columnar_manifest_sha256,
    )


def _provider_result_from_columnar_manifest(
    *,
    data_need_audit: SelectionDataNeedAudit,
    manifest: SelectionColumnarManifest,
    rows: tuple[Mapping[str, object], ...],
) -> SelectionDataNeedResult:
    normalized_refs = _normalize_normalized_refs(
        manifest.normalized_refs or tuple(
            str(row.get("source_ref") or "").strip()
            for row in rows
            if str(row.get("source_ref") or "").strip()
        ),
        market=data_need_audit.market,
    )
    return SelectionDataNeedResult(
        data_need_audit=data_need_audit,
        attempt_refs=manifest.provider_attempt_refs,
        normalized_refs=normalized_refs,
        rows=rows,
        data_gaps=(),
        warehouse_check_ref=manifest.warehouse_check_ref,
        columnar_manifest_ref=manifest.manifest_ref,
        columnar_manifest_sha256=SelectionColumnarWarehouse.default().manifest_sha256(manifest.manifest_ref),
    )


def resolve_crypto_selection_trade_date_for_scheduler(trade_date: str | None) -> str:
    if trade_date is not None and trade_date.strip():
        return date.fromisoformat(trade_date.strip()).isoformat()
    latest = _latest_crypto_selection_history_trade_date()
    if latest is None:
        raise ValueError("crypto selection history seed has no daily spot data")
    return latest.isoformat()


def _provider_result_from_crypto_seed_columnar(
    *,
    plan: SelectionRunPlan,
    data_need_audit: SelectionDataNeedAudit,
    progress_callback: Callable[[SelectionDataFetchProgress], None] | None,
) -> SelectionDataNeedResult:
    _notify_fetch_progress(progress_callback, label="读取本地加密日线历史", completed=0, total=1)
    rows_result = _crypto_seed_feature_rows(plan=plan, progress_callback=progress_callback)
    if not rows_result.rows:
        gap = _blocker_gap(
            gap_id=f"{plan.selection_run_id}-crypto-select-history-missing",
            gap_code="crypto_select_history_missing",
            attempt_refs=rows_result.attempt_refs or (f"seed://crypto-history/{plan.trade_date}",),
            reader_message="CRYPTO selection 未从本地列式历史仓库读到可用 spot USDT 日线，不能生成候选缓存。",
            source_metadata=rows_result.metadata,
        )
        return SelectionDataNeedResult(
            data_need_audit=data_need_audit,
            attempt_refs=rows_result.attempt_refs,
            normalized_refs=rows_result.normalized_refs,
            rows=(),
            data_gaps=(gap,),
        )
    _notify_fetch_progress(progress_callback, label="读取本地加密日线历史", completed=1, total=1)
    return SelectionDataNeedResult(
        data_need_audit=data_need_audit,
        attempt_refs=rows_result.attempt_refs,
        normalized_refs=rows_result.normalized_refs,
        rows=rows_result.rows,
        data_gaps=rows_result.data_gaps,
        warehouse_check_ref=f"warehouse-check://crypto-seed-columnar/{plan.trade_date}",
        columnar_manifest_ref=rows_result.columnar_manifest_ref,
        columnar_manifest_sha256=rows_result.columnar_manifest_sha256,
    )


@dataclass(frozen=True)
class _CryptoSeedRowsResult:
    rows: tuple[Mapping[str, object], ...]
    normalized_refs: tuple[str, ...]
    attempt_refs: tuple[str, ...]
    data_gaps: tuple[DataGapRef, ...]
    columnar_manifest_ref: str | None
    columnar_manifest_sha256: str | None
    metadata: Mapping[str, object]


def _crypto_seed_feature_rows(
    *,
    plan: SelectionRunPlan,
    progress_callback: Callable[[SelectionDataFetchProgress], None] | None,
) -> _CryptoSeedRowsResult:
    root = _CRYPTO_HISTORY_COLUMNAR_ROOT
    runtime_repository = _crypto_runtime_repository_or_none()
    files = _crypto_seed_daily_files(root)
    if not files and runtime_repository is None:
        return _CryptoSeedRowsResult(
            rows=(),
            normalized_refs=(),
            attempt_refs=(),
            data_gaps=(),
            columnar_manifest_ref=None,
            columnar_manifest_sha256=None,
            metadata={"seed_root": str(root), "reason": "daily_parquet_missing"},
        )
    trade_day = date.fromisoformat(plan.trade_date)
    history_start = _selection_history_start_date(plan=plan)
    documents = _read_crypto_selection_daily_documents(
        root=root,
        start=history_start,
        end=trade_day,
        repository=runtime_repository,
    )
    if not documents:
        latest = _latest_crypto_selection_history_trade_date(root=root, repository=runtime_repository)
        return _CryptoSeedRowsResult(
            rows=(),
            normalized_refs=(),
            attempt_refs=_crypto_seed_attempt_refs(root=root, trade_date=plan.trade_date) if files else (),
            data_gaps=(),
            columnar_manifest_ref=None,
            columnar_manifest_sha256=None,
            metadata={
                "seed_root": str(root),
                "latest_seed_trade_date": latest.isoformat() if latest else None,
                "requested_trade_date": plan.trade_date,
                "reason": "date_range_empty",
            },
        )
    rows_by_ticker: dict[str, list[Mapping[str, object]]] = {}
    refs_by_ticker: dict[str, list[str]] = {}
    for row in documents:
        ticker = _normalize_crypto_ticker(str(row.get("symbol_id") or row.get("ticker") or ""))
        if ticker is None:
            continue
        rows_by_ticker.setdefault(ticker, []).append(row)
        ref = str(row.get("dataset_ref") or "").strip()
        if ref:
            refs_by_ticker.setdefault(ticker, []).append(ref)
    feature_rows: list[Mapping[str, object]] = []
    lineage_refs: list[str] = []
    raw_lineage_refs: list[str] = []
    dropped: list[str] = []
    writer = SelectionColumnarWarehouse.default().begin_write(plan=plan)
    tickers = tuple(sorted(rows_by_ticker))
    _notify_fetch_progress(progress_callback, label="计算加密选币特征", completed=0, total=max(1, len(tickers)))
    for index, ticker in enumerate(tickers, start=1):
        history_source = tuple(sorted(rows_by_ticker[ticker], key=lambda item: str(_row_date(item) or "")))
        history = tuple(
            mapped
            for mapped in (_history_row(row) for row in history_source)
            if mapped is not None and (_parse_date(mapped.get("date")) or trade_day) <= trade_day
        )
        if not history:
            dropped.append(ticker)
            continue
        latest_history_date = _parse_date(history[-1].get("date"))
        if latest_history_date is None or latest_history_date < trade_day:
            dropped.append(ticker)
            continue
        source_ref = _normalized_ref(refs_by_ticker.get(ticker, ("",))[-1], market=plan.market)
        raw_row: dict[str, object] = {
            "ticker": ticker,
            "company_name": _crypto_company_name(history_source[-1], ticker),
            "industry": "Crypto",
            "history": history,
            "source_ref": source_ref,
        }
        try:
            feature_row = _materialized_feature_row(plan=plan, raw_row=raw_row, source_ref=source_ref)
        except SelectionFeatureError:
            dropped.append(ticker)
            continue
        feature_rows.append(feature_row)
        writer.add_feature_rows((feature_row,))
        lineage_refs.append(source_ref)
        raw_ref = str(refs_by_ticker.get(ticker, ("",))[-1]).strip()
        if raw_ref:
            raw_lineage_refs.append(raw_ref)
        if index % 200 == 0 or index == len(tickers):
            _notify_fetch_progress(progress_callback, label="计算加密选币特征", completed=index, total=max(1, len(tickers)))
    normalized_refs = tuple(dict.fromkeys(lineage_refs))[:_LOCAL_FEATURE_REF_SAMPLE_LIMIT]
    attempt_refs = _crypto_selection_attempt_refs(
        root=root,
        trade_date=plan.trade_date,
        repository=runtime_repository,
        dataset_refs=tuple(raw_lineage_refs),
    )
    gaps: list[DataGapRef] = []
    if dropped:
        gaps.append(
            _warn_gap(
                gap_id=f"{plan.selection_run_id}-crypto-seed-rows-dropped",
                gap_code="crypto_seed_rows_dropped",
                attempt_refs=attempt_refs,
                reader_message=f"CRYPTO seed 读取中部分交易对没有可用最新日线或特征不可用，已剔除。count={len(dropped)}。",
                source_metadata={"tickers_sample": tuple(dropped[:20])},
            )
        )
    manifest_ref: str | None = None
    manifest_sha256: str | None = None
    if feature_rows:
        manifest = writer.commit(
            provider_attempt_refs=attempt_refs,
            normalized_refs=normalized_refs,
            coverage_status="verified",
            coverage_gap_codes=tuple(gap.gap_code for gap in gaps),
        )
        manifest_ref = manifest.manifest_ref
        manifest_sha256 = SelectionColumnarWarehouse.default().manifest_sha256(manifest.manifest_ref)
    return _CryptoSeedRowsResult(
        rows=tuple(feature_rows),
        normalized_refs=normalized_refs,
        attempt_refs=attempt_refs,
        data_gaps=tuple(gaps),
        columnar_manifest_ref=manifest_ref,
        columnar_manifest_sha256=manifest_sha256,
        metadata={
            "seed_root": str(root),
            "seed_file_count": len(files),
            "row_count": len(documents),
            "feature_row_count": len(feature_rows),
            "dropped_count": len(dropped),
        },
    )


def _crypto_seed_daily_files(root: Path) -> tuple[Path, ...]:
    daily_dir = root / "market=CRYPTO" / "dataset=daily_bar" / "granularity=daily"
    if not daily_dir.exists():
        return ()
    return tuple(sorted(daily_dir.glob("*.parquet")))


def _latest_crypto_seed_trade_date(root: Path | None = None) -> date | None:
    root = root or _CRYPTO_HISTORY_COLUMNAR_ROOT
    files = _crypto_seed_daily_files(root)
    if not files:
        return None
    import duckdb

    glob_path = str(root / "market=CRYPTO" / "dataset=daily_bar" / "granularity=daily" / "*.parquet")
    with duckdb.connect(":memory:") as conn:
        value = conn.execute(
            """
            select max(period_end)
            from read_parquet(?)
            where dataset = 'daily_bar'
              and market = 'CRYPTO'
              and granularity = 'daily'
              and universe_ref = 'binance_spot_all_symbols'
              and json_extract_string(row_json, '$.quote_asset') = 'USDT'
              and json_extract_string(row_json, '$.market_segment') = 'spot'
            """,
            [glob_path],
        ).fetchone()[0]
    parsed = _parse_date(value)
    return parsed


def _latest_crypto_selection_history_trade_date(
    *,
    root: Path | None = None,
    repository: DatasetRepository | None = None,
) -> date | None:
    candidates = (
        _latest_crypto_seed_trade_date(root=root),
        _latest_crypto_runtime_trade_date(repository=repository),
    )
    return max((candidate for candidate in candidates if candidate is not None), default=None)


def _latest_crypto_runtime_trade_date(*, repository: DatasetRepository | None = None) -> date | None:
    repository = repository or _crypto_runtime_repository_or_none()
    if repository is None:
        return None
    latest: date | None = None
    for manifest in repository.list_dataset_manifests():
        if not _is_crypto_spot_usdt_daily_manifest(manifest):
            continue
        value = _parse_date(manifest.get("period_end_max"))
        if value is not None and (latest is None or value > latest):
            latest = value
    return latest


def _crypto_runtime_repository_or_none() -> DatasetRepository | None:
    if not (os.environ.get("DATA_GATEWAY_MONGODB_URI", "").strip() or os.environ.get("CN_A_MONGODB_URI", "").strip()):
        return None
    return _build_selection_gateway_context().repository


def _is_crypto_spot_usdt_daily_manifest(manifest: Mapping[str, Any]) -> bool:
    if str(manifest.get("storage") or "") != "parquet" or str(manifest.get("status") or "active") != "active":
        return False
    if str(manifest.get("market") or "") != "CRYPTO" or str(manifest.get("dataset") or "") != "daily_bar":
        return False
    if str(manifest.get("granularity") or "") != "daily":
        return False
    universe_refs = {str(item).strip() for item in tuple(manifest.get("universe_refs", ()) or ()) if str(item).strip()}
    if "binance_spot_all_symbols" not in universe_refs:
        return False
    symbol_ids = tuple(str(item).strip().upper() for item in tuple(manifest.get("symbol_ids", ()) or ()) if str(item).strip())
    return not symbol_ids or any(symbol.endswith("USDT") for symbol in symbol_ids)


def _read_crypto_selection_daily_documents(
    *,
    root: Path,
    start: date,
    end: date,
    repository: DatasetRepository | None,
) -> tuple[Mapping[str, object], ...]:
    documents_by_key: dict[tuple[str, str], Mapping[str, object]] = {}
    for row in _read_crypto_seed_daily_documents(root=root, start=start, end=end):
        key = _crypto_daily_document_key(row)
        if key is not None:
            documents_by_key[key] = row
    for row in _read_crypto_runtime_daily_documents(repository=repository, start=start, end=end):
        key = _crypto_daily_document_key(row)
        if key is not None:
            documents_by_key[key] = row
    return tuple(documents_by_key.values())


def _crypto_daily_document_key(row: Mapping[str, object]) -> tuple[str, str] | None:
    ticker = _normalize_crypto_ticker(str(row.get("symbol_id") or row.get("ticker") or ""))
    row_day = _row_date(row)
    if ticker is None or row_day is None:
        return None
    return ticker, row_day


def _read_crypto_seed_daily_documents(*, root: Path, start: date, end: date) -> tuple[Mapping[str, object], ...]:
    import duckdb

    if not _crypto_seed_daily_files(root):
        return ()
    glob_path = str(root / "market=CRYPTO" / "dataset=daily_bar" / "granularity=daily" / "*.parquet")
    with duckdb.connect(":memory:") as conn:
        rows = conn.execute(
            """
            select dataset_ref, symbol_id, universe_ref, period_start, period_end, row_json
            from read_parquet(?)
            where dataset = 'daily_bar'
              and market = 'CRYPTO'
              and granularity = 'daily'
              and universe_ref = 'binance_spot_all_symbols'
              and period_start >= ?
              and period_end <= ?
              and json_extract_string(row_json, '$.quote_asset') = 'USDT'
              and json_extract_string(row_json, '$.market_segment') = 'spot'
            order by symbol_id, period_start
            """,
            [glob_path, start.isoformat(), end.isoformat()],
        ).fetchall()
    output: list[Mapping[str, object]] = []
    for dataset_ref, symbol_id, universe_ref, period_start, period_end, row_json in rows:
        try:
            row = json.loads(str(row_json))
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        row.update(
            {
                "dataset_ref": str(dataset_ref),
                "symbol_id": str(symbol_id),
                "ticker": str(symbol_id),
                "universe_ref": str(universe_ref),
                "period_start": str(period_start),
                "period_end": str(period_end),
            }
        )
        output.append(row)
    return tuple(output)


def _read_crypto_runtime_daily_documents(
    *,
    repository: DatasetRepository | None,
    start: date,
    end: date,
) -> tuple[Mapping[str, object], ...]:
    if repository is None:
        return ()
    records = repository.query_normalized(
        dataset="daily_bar",
        market="CRYPTO",
        symbol_id=None,
        universe_ref="binance_spot_all_symbols",
        date_range_start=start,
        date_range_end=end,
        require_integrity_metadata=True,
        include_row=True,
        fields=(
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "amount",
            "base_asset",
            "quote_asset",
            "source_market_segment",
            "symbol_id",
        ),
    )
    output: list[Mapping[str, object]] = []
    for record in records:
        row = _crypto_runtime_daily_document(record)
        if row is not None:
            output.append(row)
    return tuple(output)


def _crypto_runtime_daily_document(record: DatasetRecord) -> Mapping[str, object] | None:
    row = dict(record.row)
    ticker = _normalize_crypto_ticker(str(record.symbol_id or row.get("symbol_id") or row.get("ticker") or ""))
    if ticker is None:
        return None
    quote_asset = str(row.get("quote_asset") or "").strip().upper()
    market_segment = str(row.get("market_segment") or row.get("source_market_segment") or "").strip().lower()
    if quote_asset != "USDT" or market_segment != "spot":
        return None
    row.update(
        {
            "dataset_ref": str(record.dataset_ref),
            "symbol_id": ticker,
            "ticker": ticker,
            "market_segment": market_segment,
            "universe_ref": str(record.universe_ref or "binance_spot_all_symbols"),
            "period_start": str(record.period_start),
            "period_end": str(record.period_end),
        }
    )
    return row


def _crypto_seed_attempt_refs(*, root: Path, trade_date: str) -> tuple[str, ...]:
    digest = hashlib.sha256(f"{root.resolve()}:{trade_date}".encode("utf-8")).hexdigest()[:12]
    return (f"attempt:local_crypto_prepackaged:binance_public_data_import:{digest}",)


def _crypto_selection_attempt_refs(
    *,
    root: Path,
    trade_date: str,
    repository: DatasetRepository | None,
    dataset_refs: Sequence[str],
) -> tuple[str, ...]:
    refs: list[str] = list(_crypto_seed_attempt_refs(root=root, trade_date=trade_date)) if _crypto_seed_daily_files(root) else []
    if repository is not None and dataset_refs:
        found = repository.find_provider_attempt_refs_by_dataset_ref(tuple(dataset_refs))
        for attempt_refs in found.values():
            refs.extend(str(ref) for ref in attempt_refs if str(ref).strip())
    return tuple(dict.fromkeys(refs))


def _crypto_company_name(row: Mapping[str, object], ticker: str) -> str:
    base = str(row.get("base_asset") or "").strip().upper()
    quote = str(row.get("quote_asset") or "").strip().upper()
    if base and quote:
        return f"{base}/{quote}"
    if ticker.endswith("USDT") and len(ticker) > 4:
        return f"{ticker[:-4]}/USDT"
    return ticker


def _provider_result_with_columnar_manifest(
    *,
    plan: SelectionRunPlan,
    provider_result: SelectionDataNeedResult,
) -> SelectionDataNeedResult:
    if provider_result.columnar_manifest_ref or not provider_result.rows:
        return provider_result
    try:
        inputs = normalize_selection_inputs(
            plan=plan,
            raw_rows=provider_result.rows,
            normalized_refs=provider_result.normalized_refs,
            attempt_refs=provider_result.attempt_refs,
            upstream_gaps=provider_result.data_gaps,
        )
        snapshot = build_feature_snapshot(plan=plan, inputs=inputs)
    except SelectionFeatureError:
        return provider_result

    writer = SelectionColumnarWarehouse.default().begin_write(plan=plan)
    writer.add_daily_rows(
        row
        for raw_row in provider_result.rows
        for row in _direct_daily_bar_columnar_rows(plan=plan, raw_row=raw_row)
    )
    writer.add_feature_rows(
        {
            "ticker": row.ticker,
            "company_name": row.company_name,
            "industry": row.industry,
            "source_ref": row.source_ref,
            "trade_date": plan.trade_date,
            "selection_features_materialized": True,
            **dict(row.feature_values),
        }
        for row in snapshot.rows
    )
    normalized_refs = _normalize_normalized_refs(provider_result.normalized_refs, market=plan.market)
    manifest = writer.commit(
        provider_attempt_refs=provider_result.attempt_refs,
        normalized_refs=normalized_refs,
        coverage_status=(
            "incomplete"
            if any(gap.severity == DataGapSeverity.BLOCKER for gap in provider_result.data_gaps)
            else "verified"
        ),
        coverage_gap_codes=tuple(gap.gap_code for gap in provider_result.data_gaps),
    )
    return SelectionDataNeedResult(
        data_need_audit=provider_result.data_need_audit,
        attempt_refs=provider_result.attempt_refs,
        normalized_refs=normalized_refs,
        rows=provider_result.rows,
        data_gaps=provider_result.data_gaps,
        warehouse_check_ref=manifest.warehouse_check_ref,
        columnar_manifest_ref=manifest.manifest_ref,
        columnar_manifest_sha256=SelectionColumnarWarehouse.default().manifest_sha256(manifest.manifest_ref),
    )


def _append_data_gap(result: SelectionDataNeedResult, gap: DataGapRef) -> SelectionDataNeedResult:
    return SelectionDataNeedResult(
        data_need_audit=result.data_need_audit,
        attempt_refs=result.attempt_refs,
        normalized_refs=result.normalized_refs,
        rows=result.rows,
        data_gaps=(*result.data_gaps, gap),
        warehouse_check_ref=result.warehouse_check_ref,
        columnar_manifest_ref=result.columnar_manifest_ref,
        columnar_manifest_sha256=result.columnar_manifest_sha256,
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
    provider_result: SelectionDataNeedResult,
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
    runtime = build_data_gateway_runtime_from_env()
    return _SelectionGatewayContext(
        data_service=runtime.data_service,
        repository=runtime.repository,
        data_need_executor=lambda plan, needs: _execute_selection_data_need_batch(runtime=runtime, plan=plan, needs=needs),
    )


def _execute_selection_data_needs(
    *,
    gateway: _SelectionGatewayContext,
    plan: SelectionRunPlan,
    needs: Sequence[PublicDataRequest],
) -> tuple[DataResult, ...]:
    return tuple(gateway.data_need_executor(plan, tuple(needs)))


def _execute_selection_data_need_batch(
    *,
    runtime: Any,
    plan: SelectionRunPlan,
    needs: Sequence[PublicDataRequest],
) -> tuple[DataResult, ...]:
    if not needs:
        return ()
    need_plan = plan_public_data_requests(tuple(needs))
    policies = {
        call.rate_limit_bucket: runtime.rate_limit_policy_resolver.resolve(
            provider_id=call.provider_id,
            rate_limit_bucket=call.rate_limit_bucket,
            default_policy=None,
        )
        for call in need_plan.planned_calls
    }
    scheduler = getattr(getattr(runtime, "data_service", None), "scheduler", None) or DataRunScheduler(
        rate_limit_policies=policies,
        rate_limiter=getattr(runtime, "rate_limiter", None),
    )
    policy_updater = getattr(scheduler, "update_rate_limit_policies", None)
    if callable(policy_updater):
        policy_updater(policies)
    scheduled_plan = scheduler.schedule(
        need_plan,
        DataRunScheduleContext.for_plan(
            run_id=plan.selection_run_id,
            run_started_at=datetime.now(tz=UTC),
        ),
    )
    need_by_id = {need.need_id: need for need in scheduled_plan.needs}
    call_by_id = {call.call_id: call for call in scheduled_plan.planned_calls}
    results: list[DataResult] = [
        _data_result_from_data_need_gap(need=need_by_id.get(gap.need_id), gap=gap)
        for gap in scheduled_plan.skipped_needs
    ]
    for scheduled in scheduled_plan.scheduled_calls:
        call = call_by_id.get(scheduled.call_id)
        if call is None:
            continue
        call_needs = tuple(need_by_id[need_id] for need_id in call.need_ids if need_id in need_by_id)
        if not call_needs:
            continue
        batch_need = call_needs[0]
        policy = runtime.rate_limit_policy_resolver.resolve(
            provider_id=call.provider_id,
            rate_limit_bucket=call.rate_limit_bucket,
            default_policy=None,
        )
        batch = _provider_call_batch(
            call=call,
            need=batch_need,
            policy=policy,
            runtime=runtime,
            earliest_start_at=scheduled.earliest_start_at,
            rate_limit_reserved_at=scheduled.rate_limit_reserved_at,
        )
        if _should_force_selection_universe_provider_refresh(plan=plan, need=batch_need):
            setattr(batch, "ignore_provider_cache", True)
            setattr(batch, "ignore_cached_empty", True)
        gate = runtime.data_service.execution_gate.enter(batch)
        if gate.kind in {"cache_hit", "cached_empty", "rate_limited", "shared_result", "cooldown_skipped"}:
            ingest = runtime.ingest.record_gate_result(batch, gate)
            for need in call_needs:
                results.append(_data_result_from_ingest(need=need, batch=batch, ingest=ingest))
            continue
        try:
            with _provider_call_timer(call.deadline_at):
                fetch_result = runtime.fetch_engine.fetch(batch)
        except _DataNeedProviderTimeout as exc:
            fetch_result = FetchResult.from_error(batch, status="error", error=exc)
        retry_waiter = getattr(runtime.data_service.execution_gate, "wait_after_rate_limited_fetch", None)
        if callable(retry_waiter) and retry_waiter(batch, fetch_result):
            try:
                with _provider_call_timer(call.deadline_at):
                    fetch_result = runtime.fetch_engine.fetch(batch)
            except _DataNeedProviderTimeout as exc:
                fetch_result = FetchResult.from_error(batch, status="error", error=exc)
        cooldown_marker = getattr(runtime.data_service.execution_gate, "mark_cooldown_after_fetch", None)
        if callable(cooldown_marker):
            cooldown_marker(batch, fetch_result)
        ingest = runtime.ingest.ingest(fetch_result, batch)
        if gate.kind == "owner" and gate.owner_token:
            runtime.data_service.execution_gate.publish_shared_result(batch.single_flight_key, gate.owner_token, ingest, batch=batch)
        for need in call_needs:
            data_result = _data_need_result_from_fetch(
                need=need,
                call=call,
                batch=batch,
                fetch_result=fetch_result,
                ingest=ingest,
                index=len(results),
                as_of=datetime.now(tz=UTC),
            )
            if data_result is not None:
                data_result = data_result.model_copy(update={"request_id": need.need_id})
            results.append(data_result or _data_result_from_ingest(need=need, batch=batch, ingest=ingest))
    return tuple(results)


def _data_result_from_ingest(*, need: DataNeed, batch: Any, ingest: Any) -> DataResult:
    gaps = tuple(getattr(ingest, "gaps", ()) or ())
    has_refs = bool(tuple(getattr(ingest, "dataset_refs", ()) or ()) or tuple(getattr(ingest, "raw_refs", ()) or ()))
    status = DataResultStatus.PARTIAL if has_refs and gaps else DataResultStatus.READY if has_refs else DataResultStatus.MISSING
    return DataResult(
        request_id=need.need_id,
        status=status,
        rows=(),
        dataset_refs=tuple(getattr(ingest, "dataset_refs", ()) or ()),
        raw_refs=tuple(getattr(ingest, "raw_refs", ()) or ()),
        attempt_refs=tuple(getattr(ingest, "attempt_refs", ()) or ()),
        gaps=gaps,
        as_of=datetime.now(tz=UTC),
    )


def _data_result_from_data_need_gap(*, need: DataNeed | None, gap: Any) -> DataResult:
    request_id = str(getattr(gap, "need_id", None) or getattr(need, "need_id", None) or "selection:data_need:unknown")
    reason = getattr(gap, "reason", GapReason.CATALOG_MATCH_MISSING)
    reason_value = getattr(reason, "value", str(reason))
    evidence_refs = tuple(getattr(gap, "evidence_refs", ()) or ())
    if reason_value in {"rate_limited", "rate_limited_by_tool_budget", "cooldown_skipped"} and not evidence_refs:
        evidence_refs = (f"data_need:{request_id}",)
    data_gap = DataGap.by_reason(
        reason_value,
        request_id=request_id,
        market=getattr(need, "market", Market.CN_A) if need is not None else Market.CN_A,
        data_type="daily_bar",
        granularity=str(getattr(need, "granularity", "daily") or "daily") if need is not None else "daily",
        severity=GapSeverity.BLOCKER,
        evidence_refs=evidence_refs,
        message=str(getattr(gap, "human_readable", None) or reason_value),
        symbol_id=getattr(need, "instrument", None) if need is not None else None,
        as_of=datetime.now(tz=UTC),
    )
    return DataResult(
        request_id=request_id,
        status=DataResultStatus.MISSING,
        rows=(),
        gaps=(data_gap,),
        as_of=datetime.now(tz=UTC),
    )


def _should_build_local_feature_rows(*, plan: SelectionRunPlan, results: tuple[DataResult, ...]) -> bool:
    if _universe_refresh_attempted(results=results):
        return True
    if _main_daily_result_needs_universe_refresh(plan=plan, results=results):
        return False
    if _main_daily_result_has_rows(plan=plan, results=results):
        return False
    return not _main_direct_selection_rows(plan=plan, results=results)


def _main_daily_result_has_rows(*, plan: SelectionRunPlan, results: tuple[DataResult, ...]) -> bool:
    main_request_id = f"{plan.selection_run_id}:selection:1:daily_bar"
    return any(result.request_id == main_request_id and bool(result.rows) for result in results)


def _selection_feature_rows_from_repository(
    *,
    plan: SelectionRunPlan,
    repository: DatasetRepository,
    progress_callback: Callable[[SelectionDataFetchProgress], None] | None = None,
) -> _LocalFeatureRowsResult:
    trade_day = date.fromisoformat(plan.trade_date)
    history_start = _selection_history_start_date(plan=plan)
    required_history_days = max(_MIN_HISTORY_DAYS, int(plan.lookback_trading_days or 0))
    columnar_writer = SelectionColumnarWarehouse.default().begin_write(plan=plan)
    latest_records = _query_daily_records(
        repository=repository,
        market=plan.market,
        plan=plan,
        symbol_id=None,
        universe_ref=plan.universe_scope,
        start=trade_day,
        end=trade_day,
    )
    allow_unscoped_latest_fallback = not _requires_scoped_full_market_latest_rows(plan)
    if not latest_records and plan.universe_scope and allow_unscoped_latest_fallback:
        latest_records = _query_daily_records(
            repository=repository,
            market=plan.market,
            plan=plan,
            symbol_id=None,
            universe_ref=None,
            start=trade_day,
            end=trade_day,
        )
    latest_rows_by_ticker: dict[str, Mapping[str, Any]] = {}
    for record in latest_records:
        row = _row_from_dataset_record(record)
        ticker = _ticker_from_row(row)
        if ticker is None:
            continue
        latest_rows_by_ticker[ticker] = row
    if not latest_rows_by_ticker and plan.universe_scope and allow_unscoped_latest_fallback:
        latest_records = _query_daily_records(
            repository=repository,
            market=plan.market,
            plan=plan,
            symbol_id=None,
            universe_ref=None,
            start=trade_day,
            end=trade_day,
        )
        for record in latest_records:
            row = _row_from_dataset_record(record)
            ticker = _ticker_from_row(row)
            if ticker is None:
                continue
            latest_rows_by_ticker[ticker] = row
    tickers = tuple(sorted(latest_rows_by_ticker))
    _notify_fetch_progress(
        progress_callback,
        label="读取本地全市场历史日线",
        completed=0,
        total=1,
    )
    ticker_set = set(tickers)
    _notify_fetch_progress(
        progress_callback,
        label="流式计算本地选股特征",
        completed=0,
        total=max(1, len(tickers)),
    )

    rows: list[Mapping[str, object]] = []
    dropped: list[str] = []
    drop_reason_counts: dict[str, int] = {}
    drop_samples: dict[str, list[Mapping[str, object]]] = {}
    feature_errors: list[str] = []
    lineage_dataset_refs: list[str] = []
    row_limit_exceeded = False
    processed_ticker_count = 0
    processed_tickers: set[str] = set()
    ordered_records = repository.iter_normalized(
        dataset="daily_bar",
        market=plan.market.value,
        symbol_id=None,
        universe_ref=None,
        date_range_start=history_start,
        date_range_end=trade_day,
        require_integrity_metadata=True,
        include_row=True,
        fields=_SELECTION_HISTORY_QUERY_FIELDS,
        order_by_symbol_id=True,
    )
    for ticker, ticker_records in groupby(ordered_records, key=lambda record: str(record.symbol_id or "")):
        if ticker not in ticker_set:
            continue
        if len(rows) >= _SELECTION_FEATURE_ROW_LIMIT:
            row_limit_exceeded = True
            break
        processed_tickers.add(ticker)
        processed_ticker_count += 1
        history_by_date: dict[str, tuple[tuple[int, float], Mapping[str, float | str], str]] = {}
        history_company_name: str | None = None
        history_industry: str | None = None
        for record in ticker_records:
            row = _row_from_dataset_record(record)
            mapped = _history_row(row)
            if mapped is None:
                continue
            row_date = str(mapped["date"])
            preference = (
                -_source_role_rank(record),
                record.as_of.timestamp() if record.as_of is not None else float("-inf"),
            )
            current = history_by_date.get(row_date)
            if current is None or preference >= current[0]:
                history_by_date[row_date] = (preference, mapped, record.dataset_ref)
            if history_company_name is None:
                history_company_name = _company_name_from_rows((row,))
            if history_industry is None:
                history_industry = _industry_from_rows((row,))
        history_days = tuple(day for day in sorted(history_by_date) if date.fromisoformat(day) <= trade_day)
        history = tuple(history_by_date[day][1] for day in history_days)
        if not history:
            dropped.append(ticker)
            _record_drop_reason(
                reason_counts=drop_reason_counts,
                samples=drop_samples,
                reason="history_empty",
                ticker=ticker,
                metadata={},
            )
            continue
        latest_history_date = _parse_date(history[-1].get("date"))
        if latest_history_date is None or latest_history_date < trade_day:
            dropped.append(ticker)
            _record_drop_reason(
                reason_counts=drop_reason_counts,
                samples=drop_samples,
                reason="latest_history_before_trade_day",
                ticker=ticker,
                metadata={"latest_history_date": latest_history_date.isoformat() if latest_history_date else None},
            )
            continue
        company_name = history_company_name or _company_name_from_rows((latest_rows_by_ticker[ticker],))
        if company_name is None:
            company_name = repository.find_company_names_by_symbol_ids(
                dataset="daily_bar",
                market=plan.market.value,
                symbol_ids=(ticker,),
            ).get(ticker)
        if company_name is None:
            dropped.append(ticker)
            _record_drop_reason(
                reason_counts=drop_reason_counts,
                samples=drop_samples,
                reason="company_name_missing",
                ticker=ticker,
                metadata={},
            )
            continue
        winning_dataset_ref = history_by_date[history_days[-1]][2]
        source_ref = _normalized_ref(winning_dataset_ref, market=plan.market)
        raw_row: dict[str, object] = {
            "ticker": ticker,
            "company_name": company_name,
            "industry": history_industry or _industry_from_rows((latest_rows_by_ticker[ticker],)),
            "history": history,
            "source_ref": source_ref,
        }
        try:
            feature_row = _materialized_feature_row(plan=plan, raw_row=raw_row, source_ref=source_ref)
        except SelectionFeatureError:
            feature_errors.append(ticker)
            _record_drop_reason(
                reason_counts=drop_reason_counts,
                samples=drop_samples,
                reason="feature_materialization_error",
                ticker=ticker,
                metadata={},
            )
            continue
        rows.append(feature_row)
        columnar_writer.add_feature_rows((feature_row,))
        lineage_dataset_refs.append(winning_dataset_ref)
        if processed_ticker_count % 200 == 0 or processed_ticker_count == len(tickers):
            _notify_fetch_progress(
                progress_callback,
                label="流式计算本地选股特征",
                completed=processed_ticker_count,
                total=max(1, len(tickers)),
            )

    if not row_limit_exceeded:
        for ticker in tickers:
            if ticker in processed_tickers:
                continue
            processed_ticker_count += 1
            dropped.append(ticker)
            _record_drop_reason(
                reason_counts=drop_reason_counts,
                samples=drop_samples,
                reason="history_empty",
                ticker=ticker,
                metadata={},
            )
    _notify_fetch_progress(
        progress_callback,
        label="读取本地全市场历史日线",
        completed=1,
        total=1,
    )
    _notify_fetch_progress(
        progress_callback,
        label="流式计算本地选股特征",
        completed=processed_ticker_count,
        total=max(1, len(tickers)),
    )

    sampled_dataset_refs = tuple(
        ref
        for ref in _dedupe(lineage_dataset_refs)
        if ref
    )[:_LOCAL_FEATURE_REF_SAMPLE_LIMIT]
    attempt_refs = tuple(
        ref
        for refs in repository.find_provider_attempt_refs_by_dataset_ref(sampled_dataset_refs).values()
        for ref in refs
    )
    normalized_refs = tuple(_normalized_ref(ref, market=plan.market) for ref in sampled_dataset_refs)
    gaps: list[DataGapRef] = []
    if dropped:
        gaps.append(
            _warn_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-stream-rows-dropped",
                gap_code="selection_batch_rows_dropped",
                attempt_refs=attempt_refs or (f"select-data-plan://selection/{plan.selection_run_id}/{plan.trade_date}",),
                reader_message=(
                    "selection batch 流式计算中部分股票缺少公司名或交易日日线，已剔除。"
                    f" count={len(dropped)}。"
                ),
                source_metadata={
                    "read_mode": "stream_by_symbol",
                    "tickers_sample": tuple(dropped[:20]),
                    "required_history_days": required_history_days,
                    "drop_reason_counts": dict(drop_reason_counts),
                    "drop_samples": {key: tuple(value) for key, value in drop_samples.items()},
                },
            )
        )
    if _full_market_feature_coverage_insufficient(plan=plan, ticker_count=len(tickers), row_count=len(rows)):
        gaps.append(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-universe-coverage-insufficient",
                gap_code="selection_batch_universe_coverage_insufficient",
                attempt_refs=attempt_refs or (f"select-data-plan://selection/{plan.selection_run_id}/{plan.trade_date}",),
                reader_message=(
                    "CN_A 全市场选股特征覆盖不足，不能生成候选缓存："
                    f"当天股票 {len(tickers)} 只，最低要求 {_full_market_expected_min_tickers(plan)} 只，"
                    f"可物化特征 {len(rows)} 只。"
                ),
                source_metadata={
                    "read_mode": "stream_by_symbol",
                    "ticker_count": len(tickers),
                    "minimum_ticker_count": _full_market_expected_min_tickers(plan),
                    "rows_returned": len(rows),
                    "dropped_count": len(dropped),
                    "required_history_days": required_history_days,
                    "minimum_feature_coverage_ratio": _FULL_MARKET_MIN_FEATURE_COVERAGE_RATIO,
                    "drop_reason_counts": dict(drop_reason_counts),
                    "drop_samples": {key: tuple(value) for key, value in drop_samples.items()},
                },
            )
        )
    if feature_errors:
        gaps.append(
            _warn_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-stream-feature-errors",
                gap_code="selection_batch_feature_rows_dropped",
                attempt_refs=attempt_refs or (f"select-data-plan://selection/{plan.selection_run_id}/{plan.trade_date}",),
                reader_message=(
                    "selection batch 流式特征计算中部分股票无法生成特征，已剔除。"
                    f" count={len(feature_errors)}。"
                ),
                source_metadata={
                    "tickers_sample": tuple(feature_errors[:20]),
                },
            )
        )
    if row_limit_exceeded:
        gaps.append(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-stream-row-limit-exceeded",
                gap_code="selection_batch_feature_row_limit_exceeded",
                attempt_refs=attempt_refs or (f"select-data-plan://selection/{plan.selection_run_id}/{plan.trade_date}",),
                reader_message=(
                    "selection batch 流式特征计算达到行数上限，已停止继续物化股票行。"
                    f" row_limit={_SELECTION_FEATURE_ROW_LIMIT}。"
                ),
                source_metadata={
                    "read_mode": "stream_by_symbol",
                    "row_limit": _SELECTION_FEATURE_ROW_LIMIT,
                    "rows_returned": len(rows),
                    "ticker_count": len(tickers),
                    "processed_ticker_count": processed_ticker_count,
                },
            )
        )
    if not rows:
        gaps.append(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-selection-batch-stream-rows-empty",
                gap_code="selection_batch_rows_empty",
                attempt_refs=attempt_refs or (f"select-data-plan://selection/{plan.selection_run_id}/{plan.trade_date}",),
                reader_message="本地日线覆盖检查通过，但流式特征计算没有产出可用股票行。",
                source_metadata={
                    "read_mode": "stream_by_symbol",
                    "latest_record_count": len(latest_records),
                    "ticker_count": len(tickers),
                    "required_history_days": required_history_days,
                },
            )
        )
    columnar_manifest_ref: str | None = None
    columnar_manifest_sha256: str | None = None
    if rows:
        coverage_gap_codes = tuple(gap.gap_code for gap in gaps)
        coverage_status = "incomplete" if any(gap.severity == DataGapSeverity.BLOCKER for gap in gaps) else "verified"
        manifest = columnar_writer.commit(
            provider_attempt_refs=_dedupe(attempt_refs),
            normalized_refs=normalized_refs,
            coverage_status=coverage_status,
            coverage_gap_codes=coverage_gap_codes,
        )
        columnar_manifest_ref = manifest.manifest_ref
        columnar_manifest_sha256 = SelectionColumnarWarehouse.default().manifest_sha256(manifest.manifest_ref)
    return _LocalFeatureRowsResult(
        rows=tuple(rows),
        normalized_refs=normalized_refs,
        attempt_refs=_dedupe(attempt_refs),
        data_gaps=tuple(gaps),
        columnar_manifest_ref=columnar_manifest_ref,
        columnar_manifest_sha256=columnar_manifest_sha256,
    )


def _record_drop_reason(
    *,
    reason_counts: dict[str, int],
    samples: dict[str, list[Mapping[str, object]]],
    reason: str,
    ticker: str,
    metadata: Mapping[str, object],
) -> None:
    reason_counts[reason] = reason_counts.get(reason, 0) + 1
    bucket = samples.setdefault(reason, [])
    if len(bucket) < 20:
        bucket.append({"ticker": ticker, **dict(metadata)})


def _full_market_feature_coverage_insufficient(
    *,
    plan: SelectionRunPlan,
    ticker_count: int,
    row_count: int,
) -> bool:
    expected_tickers = _full_market_expected_min_tickers(plan)
    if expected_tickers <= 0:
        return False
    if ticker_count < expected_tickers:
        return True
    return row_count / ticker_count < _FULL_MARKET_MIN_FEATURE_COVERAGE_RATIO


def _full_market_expected_min_tickers(plan: SelectionRunPlan) -> int:
    if plan.market != SelectionMarket.CN_A or plan.universe_scope != _CN_A_DEFAULT_UNIVERSE_SCOPE:
        return 0
    return _FULL_MARKET_MIN_TICKERS


def _columnar_manifest_has_sufficient_full_market_coverage(
    *,
    plan: SelectionRunPlan,
    manifest: SelectionColumnarManifest,
) -> bool:
    expected_tickers = _full_market_expected_min_tickers(plan)
    if expected_tickers <= 0:
        return True
    minimum_feature_rows = int(expected_tickers * _FULL_MARKET_MIN_FEATURE_COVERAGE_RATIO)
    return manifest.feature_row_count >= minimum_feature_rows


def _selection_history_start_date(*, plan: SelectionRunPlan) -> date:
    trade_day = date.fromisoformat(plan.trade_date)
    lookback_days = max(_DEFAULT_LOOKBACK_TRADING_DAYS, int(plan.lookback_trading_days or 0))
    calendar_days = lookback_days * 2
    return trade_day - timedelta(days=calendar_days)


def _query_daily_records(
    *,
    repository: DatasetRepository,
    market: SelectionMarket,
    plan: SelectionRunPlan,
    symbol_id: str | None,
    universe_ref: str | None,
    start: date,
    end: date,
) -> tuple[DatasetRecord, ...]:
    return repository.query_normalized(
        dataset="daily_bar",
        market=market.value,
        symbol_id=symbol_id,
        universe_ref=universe_ref,
        date_range_start=start,
        date_range_end=end,
        require_integrity_metadata=True,
        include_row=True,
        fields=_SELECTION_DAILY_QUERY_FIELDS,
    )


def _row_from_dataset_record(record: DatasetRecord) -> Mapping[str, Any]:
    row = dict(record.row)
    row.setdefault("dataset_ref", record.dataset_ref)
    row.setdefault("symbol_id", record.symbol_id)
    row.setdefault("universe_ref", record.universe_ref)
    row.setdefault("period_start", record.period_start)
    row.setdefault("period_end", record.period_end)
    row.setdefault("source_roles", record.source_roles)
    return row


def _daily_bar_columnar_rows(
    *,
    plan: SelectionRunPlan,
    ticker: str,
    rows: tuple[Mapping[str, Any], ...],
) -> tuple[Mapping[str, object], ...]:
    output: list[Mapping[str, object]] = []
    for row in rows:
        day = _row_date(row)
        day_text = day.isoformat() if hasattr(day, "isoformat") else str(day or "")
        output.append(
            {
                "market": plan.market.value,
                "profile": plan.profile.value,
                "selection_trade_date": plan.trade_date,
                "ticker": ticker,
                "date": day_text,
                "open": _numeric_or_none(row.get("open")),
                "high": _numeric_or_none(row.get("high")),
                "low": _numeric_or_none(row.get("low")),
                "close": _numeric_or_none(row.get("close")),
                "volume": _numeric_or_none(row.get("volume")),
                "amount": _numeric_or_none(row.get("amount")),
                "p_change_pct": _numeric_or_none(row.get("p_change_pct") or row.get("pct_chg")),
                "dataset_ref": str(row.get("dataset_ref") or ""),
                "source_ref": _row_source_ref(row),
            }
        )
    return tuple(output)


def _direct_daily_bar_columnar_rows(
    *,
    plan: SelectionRunPlan,
    raw_row: Mapping[str, object],
) -> tuple[Mapping[str, object], ...]:
    ticker = str(raw_row.get("ticker") or raw_row.get("symbol_id") or "").strip()
    history = raw_row.get("history")
    if isinstance(history, Sequence) and not isinstance(history, (str, bytes, bytearray)):
        rows = tuple(item for item in history if isinstance(item, Mapping))
        return _daily_bar_columnar_rows(plan=plan, ticker=ticker, rows=rows)
    return _daily_bar_columnar_rows(plan=plan, ticker=ticker, rows=(raw_row,))


def _numeric_or_none(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    try:
        return float(str(value))
    except ValueError:
        return None


def _materialized_feature_row(
    *,
    plan: SelectionRunPlan,
    raw_row: Mapping[str, object],
    source_ref: str,
) -> Mapping[str, object]:
    inputs = normalize_selection_inputs(
        plan=plan,
        raw_rows=(raw_row,),
        normalized_refs=(source_ref,),
        attempt_refs=(source_ref,),
    )
    snapshot = build_feature_snapshot(plan=plan, inputs=inputs)
    feature_row = snapshot.rows[0]
    return {
        "ticker": feature_row.ticker,
        "company_name": feature_row.company_name,
        "industry": feature_row.industry,
        "source_ref": feature_row.source_ref,
        "trade_date": plan.trade_date,
        "selection_features_materialized": True,
        **dict(feature_row.feature_values),
    }


def _selection_data_requests(
    plan: SelectionRunPlan,
    *,
    specs: tuple[tuple[str, str, tuple[str, ...]], ...] | None = None,
    start_index: int = 1,
    freshness_policy: str | None = None,
    consumer: str = "select",
    consumer_id: str | None = None,
) -> tuple[DataRequest, ...]:
    resolved_specs = specs if specs is not None else _selection_data_request_specs(plan.market)
    _, timezone, calendar = _selection_market_defaults(plan.market)
    as_of = datetime.now(tz=UTC)
    trade_date = date.fromisoformat(plan.trade_date)
    # Trading days are not natural days; use a wider calendar range for warehouse
    # matching and provider requests.
    start = trade_date - timedelta(days=max(plan.lookback_trading_days, _DEFAULT_LOOKBACK_TRADING_DAYS) * 2)
    base_asset = "BTC" if plan.market == SelectionMarket.CRYPTO else None
    quote_asset = "USDT" if plan.market == SelectionMarket.CRYPTO else None
    return tuple(
        DataRequest(
            request_id=f"{plan.selection_run_id}:selection:{index}:{data_type}",
            market=_selection_market_value(plan.market),
            universe_ref=plan.universe_scope,
            timezone=timezone,
            calendar=calendar,
            base_asset=base_asset,
            quote_asset=quote_asset,
            data_type=data_type,
            granularity=granularity,
            fields=fields,
            date_range_start=start if granularity == "daily" else None,
            date_range_end=trade_date if granularity == "daily" else None,
            freshness_policy=freshness_policy or ("trading_day" if granularity == "daily" else "event_time"),
            consumer=consumer,
            consumer_id=consumer_id or plan.selection_run_id,
            as_of=as_of,
        )
        for index, (data_type, granularity, fields) in enumerate(resolved_specs, start=start_index)
    )


def _selection_universe_refresh_needs(*, plan: SelectionRunPlan, results: tuple[DataResult, ...]) -> tuple[PublicDataRequest, ...]:
    has_low_full_market_coverage = _main_daily_result_has_insufficient_full_market_latest_coverage(
        plan=plan,
        results=results,
    )
    if not _main_daily_result_needs_universe_refresh(plan=plan, results=results) and not has_low_full_market_coverage:
        return ()
    trade_date = date.fromisoformat(plan.trade_date)
    start_fallback = trade_date - timedelta(days=max(plan.lookback_trading_days, _DEFAULT_LOOKBACK_TRADING_DAYS) * 2)
    refresh_dates = _selection_universe_refresh_dates(plan=plan, results=results)
    if has_low_full_market_coverage:
        refresh_dates = tuple(dict.fromkeys((*refresh_dates, trade_date)))
    if not refresh_dates and _main_daily_result_has_integrity_gap_without_usable_rows(plan=plan, results=results):
        refresh_dates = (trade_date,)
    if not refresh_dates and _main_daily_result_needs_universe_refresh(plan=plan, results=results):
        refresh_dates = (trade_date,)
    if refresh_dates:
        return tuple(
            _selection_data_need(
                plan=plan,
                market=plan.market,
                need_id=f"{plan.selection_run_id}:selection:universe_refresh:{index}:{plan.universe_scope}:daily_bar",
                instrument=plan.universe_scope,
                start=refresh_date,
                end=refresh_date,
                granularity="daily",
            )
            for index, refresh_date in enumerate(refresh_dates, start=1)
        )

    needs: list[PublicDataRequest] = []
    seen: set[str] = set()
    for row in _main_direct_selection_rows(plan=plan, results=results):
        ticker = _ticker_from_row(row)
        if ticker is None or ticker in seen:
            continue
        latest = _latest_history_date(row)
        if latest is not None and latest >= trade_date:
            continue
        seen.add(ticker)
        start = start_fallback if latest is None else min(latest + timedelta(days=1), trade_date)
        needs.append(
            _selection_data_need(
                plan=plan,
                market=plan.market,
                need_id=f"{plan.selection_run_id}:selection:universe_refresh:{len(needs) + 1}:{ticker}:daily_bar",
                instrument=ticker,
                start=start,
                end=trade_date,
                granularity="daily",
            )
        )
    return tuple(needs)


def _selection_data_need(
    *,
    plan: SelectionRunPlan,
    market: SelectionMarket,
    need_id: str,
    instrument: str,
    start: date,
    end: date,
    granularity: str,
) -> PublicDataRequest:
    return PublicDataRequest(
        request_id=need_id,
        item="日线",
        market=_selection_market_value(market),
        instrument=instrument,
        time_range_start=start,
        time_range_end=end,
        granularity=granularity,
        priority=PublicRequestPriority.REQUIRED,
        requested_by_worker="selection_data_job",
        purpose="selection_candidate_cache",
        freshness_policy="trading_day",
        deadline_at=datetime.now(tz=UTC) + timedelta(seconds=_selection_refresh_deadline_seconds(plan)),
        consumer="select",
    )


def _selection_refresh_deadline_seconds(plan: SelectionRunPlan) -> int:
    if _is_select_command_refresh(plan):
        return _SELECT_COMMAND_REFRESH_DEADLINE_SECONDS
    return _BACKGROUND_REFRESH_DEADLINE_SECONDS


def _selection_universe_refresh_dates(*, plan: SelectionRunPlan, results: tuple[DataResult, ...]) -> tuple[date, ...]:
    trade_date = date.fromisoformat(plan.trade_date)
    metadata_refresh_dates = _selection_universe_refresh_dates_from_metadata(plan=plan, results=results)
    if metadata_refresh_dates:
        return metadata_refresh_dates
    latest_dates = tuple(
        latest
        for row in _main_direct_selection_rows(plan=plan, results=results)
        if (latest := _latest_history_date(row)) is not None
    )
    if not latest_dates:
        return ()
    latest_available = max(latest_dates)
    if latest_available >= trade_date:
        return ()
    refresh_dates: list[date] = []
    current = latest_available + timedelta(days=1)
    while current <= trade_date:
        refresh_dates.append(current)
        current += timedelta(days=1)
    return _runtime_universe_refresh_dates(plan=plan, refresh_dates=refresh_dates)


def _main_daily_result_needs_universe_refresh(*, plan: SelectionRunPlan, results: tuple[DataResult, ...]) -> bool:
    main_request_id = f"{plan.selection_run_id}:selection:1:daily_bar"
    for result in results:
        if result.request_id != main_request_id:
            continue
        return any(_is_universe_refresh_trigger_gap(gap) for gap in result.gaps)
    return False


def _main_daily_result_has_insufficient_full_market_latest_coverage(
    *,
    plan: SelectionRunPlan,
    results: tuple[DataResult, ...],
) -> bool:
    expected_tickers = _full_market_expected_min_tickers(plan)
    if expected_tickers <= 0:
        return False
    main_request_id = f"{plan.selection_run_id}:selection:1:daily_bar"
    for result in results:
        if result.request_id != main_request_id:
            continue
        coverage = _freshness_coverage_for_request(result.freshness, request_id=main_request_id)
        if coverage is None:
            return False
        record_count = _int_metadata_value(coverage.get("record_count"))
        return record_count is not None and record_count < expected_tickers
    return False


def _main_daily_result_has_integrity_gap_without_usable_rows(
    *,
    plan: SelectionRunPlan,
    results: tuple[DataResult, ...],
) -> bool:
    main_request_id = f"{plan.selection_run_id}:selection:1:daily_bar"
    for result in results:
        if result.request_id != main_request_id:
            continue
        if not any(getattr(gap.reason, "value", str(gap.reason)) == "data_integrity_failed" for gap in result.gaps):
            return False
        return not _main_direct_selection_rows(plan=plan, results=(result,))
    return False


def _selection_universe_refresh_dates_from_metadata(*, plan: SelectionRunPlan, results: tuple[DataResult, ...]) -> tuple[date, ...]:
    main_request_id = f"{plan.selection_run_id}:selection:1:daily_bar"
    trade_date = date.fromisoformat(plan.trade_date)
    for result in results:
        if result.request_id != main_request_id:
            continue
        coverage = _freshness_coverage_for_request(result.freshness, request_id=main_request_id)
        if coverage is None:
            return ()
        integrity_ranges = _metadata_integrity_mismatch_ranges(coverage)
        missing_ranges = _metadata_missing_ranges(coverage)
        if integrity_ranges or missing_ranges:
            refresh_dates = tuple(
                day
                for start, end in (*integrity_ranges, *missing_ranges)
                for day in _daily_dates_between(start, min(end, trade_date), market=plan.market)
                if day <= trade_date
            )
            return _runtime_universe_refresh_dates(plan=plan, refresh_dates=refresh_dates)
        actual_end = _parse_date(coverage.get("actual_end"))
        expected_end = _parse_date(coverage.get("expected_end")) or trade_date
        expected_end = min(expected_end, trade_date)
        if actual_end is None or actual_end >= expected_end:
            return ()
        return _runtime_universe_refresh_dates(
            plan=plan,
            refresh_dates=_daily_dates_between(actual_end + timedelta(days=1), expected_end, market=plan.market),
        )
    return ()


def _runtime_universe_refresh_dates(*, plan: SelectionRunPlan, refresh_dates: Sequence[date]) -> tuple[date, ...]:
    if _is_select_command_refresh(plan):
        trade_day = date.fromisoformat(plan.trade_date)
        return (trade_day,) if trade_day in set(refresh_dates) else ()
    return _prioritize_universe_refresh_dates(refresh_dates)


def _is_select_command_refresh(plan: SelectionRunPlan) -> bool:
    return str(getattr(plan.trigger_source, "value", plan.trigger_source)) == "select_command_refresh"


def _prioritize_universe_refresh_dates(refresh_dates: Sequence[date]) -> tuple[date, ...]:
    # Provider/rate limits are finite; /select must fill the latest trade dates
    # before spending calls on old lookback gaps already covered by the seed.
    return tuple(sorted(set(refresh_dates), reverse=True))


def _freshness_coverage_for_request(freshness: Mapping[str, Any], *, request_id: str) -> Mapping[str, Any] | None:
    coverage_items = freshness.get("coverage_by_request")
    if not isinstance(coverage_items, Sequence) or isinstance(coverage_items, (str, bytes, bytearray)):
        return None
    for item in coverage_items:
        if isinstance(item, Mapping) and str(item.get("request_id") or "") == request_id:
            return item
    return None


def _int_metadata_value(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _metadata_missing_ranges(coverage: Mapping[str, Any]) -> tuple[tuple[date, date], ...]:
    raw_ranges = coverage.get("missing_ranges")
    if not isinstance(raw_ranges, Sequence) or isinstance(raw_ranges, (str, bytes, bytearray)):
        return ()
    ranges: list[tuple[date, date]] = []
    for raw in raw_ranges:
        if not isinstance(raw, Mapping):
            continue
        start = _parse_date(raw.get("start"))
        end = _parse_date(raw.get("end"))
        if start is None:
            continue
        ranges.append((start, end or start))
    return tuple(ranges)


def _metadata_integrity_mismatch_ranges(coverage: Mapping[str, Any]) -> tuple[tuple[date, date], ...]:
    raw_ranges = coverage.get("integrity_mismatch_ranges")
    if not isinstance(raw_ranges, Sequence) or isinstance(raw_ranges, (str, bytes, bytearray)):
        return ()
    ranges: list[tuple[date, date]] = []
    for raw in raw_ranges:
        if not isinstance(raw, Mapping):
            continue
        start = _parse_date(raw.get("start"))
        end = _parse_date(raw.get("end"))
        if start is None:
            continue
        ranges.append((start, end or start))
    return tuple(ranges)


def _daily_dates_between(start: date, end: date, market: SelectionMarket = SelectionMarket.CN_A) -> tuple[date, ...]:
    if start > end:
        return ()
    dates: list[date] = []
    current = start
    while current <= end:
        _, _, calendar = _selection_market_defaults(market)
        if is_expected_daily_date(current, calendar):
            dates.append(current)
        current += timedelta(days=1)
    return tuple(dates)


def _is_universe_refresh_trigger_gap(gap: DataGap) -> bool:
    reason = getattr(gap.reason, "value", str(gap.reason))
    if reason in {"date_range_missing", "warehouse_stale", "data_integrity_failed", "warehouse_missing"}:
        return True
    return reason == "provider_error" and str(gap.human_readable).strip() == "symbol_required"


def _main_direct_selection_rows(*, plan: SelectionRunPlan, results: tuple[DataResult, ...]) -> tuple[Mapping[str, Any], ...]:
    main_request_id = f"{plan.selection_run_id}:selection:1:daily_bar"
    return tuple(
        row
        for result in results
        if result.request_id == main_request_id
        for row in result.rows
        if _row_has_direct_history(row)
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
        if not history:
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
            "source_ref": _row_source_ref(history_source[-1], market=plan.market),
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
            market=plan.market,
        )
        row.update(valuation_fields)
        private_event = _private_placement_fields(
            ticker=ticker,
            trade_date=plan.trade_date,
            rows=corporate_rows,
            coverage_available=has_private_placement_coverage,
            market=plan.market,
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
            if ticker:
                mapped_rows_by_ticker[ticker] = mapped
    daily_history_by_ticker = _individual_daily_history_by_ticker(results)
    shortfalls = _direct_history_shortfalls(plan=plan, results=results)
    shortfall_by_ticker = {item.ticker: item for item in shortfalls}
    universe_refresh_attempted = _universe_refresh_attempted(results=results)
    rows: list[Mapping[str, object]] = []
    dropped: list[str] = []
    repaired: list[str] = []
    stale_after_universe_refresh: list[str] = []
    for row in mapped_rows_by_ticker.values():
        ticker = str(row.get("ticker") or "").strip()
        merged_row = _merge_repaired_history(row=row, repaired_history=daily_history_by_ticker.get(ticker, ()))
        if universe_refresh_attempted and not _row_covers_trade_date(row=merged_row, plan=plan):
            if ticker:
                dropped.append(ticker)
                stale_after_universe_refresh.append(ticker)
            continue
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
                    "selection batch 中部分本地预打包股票缺少足够历史日线或交易日日线，已剔除。"
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
                    "stale_after_universe_refresh_tickers": tuple(stale_after_universe_refresh[:20]),
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
    market: SelectionMarket = SelectionMarket.CN_A,
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
    source_ref = _row_source_ref(latest_row, market=market)
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
    mapped.setdefault("source_ref", _row_source_ref(row, market=SelectionMarket.CN_A))
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
    market: SelectionMarket = SelectionMarket.CN_A,
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
            latest_source_ref = _row_source_ref(row, market=market)
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
    strategy = _load_selection_strategy(plan)
    if strategy is None or not rows:
        return ()
    required_fields = _selection_strategy_required_source_fields(strategy=strategy)
    missing_fields = sorted(
        field
        for field in required_fields
        if field not in _OPTIONAL_SELECTION_STRATEGY_SOURCE_FIELDS
        if not all(
            _row_has_required_history(row=row, plan=plan)
            if field == "history"
            else row.get(field) is not None
            for row in rows
        )
    )
    return tuple(missing_fields)


def _selection_feature_projection_columns(plan: SelectionRunPlan) -> tuple[str, ...]:
    columns: list[str] = [
        "ticker",
        "company_name",
        "industry",
        "source_ref",
        "trade_date",
        "selection_features_materialized",
        "private_placement_event_date",
        "private_placement_source_ref",
    ]
    strategy = _load_selection_strategy(plan)
    if strategy is not None:
        columns.extend(rule.field for rule in strategy.hard_filters)
        columns.append(strategy.stable_top20_rule.score_field)
        columns.extend(field.field for field in strategy.stable_top20_rule.tie_break_fields)
        columns.extend(strategy.weights.keys())
        columns.extend(_selection_strategy_required_source_fields(strategy=strategy))
        for rule in strategy.strategy_set:
            columns.extend(rule.required_fields)
            columns.extend(condition.field for condition in rule.all_of)
    columns.extend(
        (
            "score",
            "amount",
            "rps120",
            "rps_120",
            "rps60",
            "rps_60",
            "strategy_hit_count",
            "strategy_variant_count",
            "strategy_missing_field_count",
            "strategy_required_field_count",
        )
    )
    return tuple(dict.fromkeys(column for column in columns if str(column).strip()))


def _load_selection_strategy(plan: SelectionRunPlan) -> ApprovedSelectionStrategy | None:
    strategy = load_selection_strategy(plan.approved_strategy_config_ref)
    if strategy is not None:
        return strategy
    fallback_ref = load_selection_strategy_config_ref(plan.market, plan.profile)
    if fallback_ref is None:
        return None
    return load_selection_strategy(fallback_ref)


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
    if row.get("selection_features_materialized") is True:
        return True
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


def _row_covers_trade_date(*, row: Mapping[str, object], plan: SelectionRunPlan) -> bool:
    if row.get("selection_features_materialized") is True:
        row_trade_date = _parse_date(row.get("trade_date") or row.get("date"))
        return row_trade_date is not None and row_trade_date >= date.fromisoformat(plan.trade_date)
    latest = _latest_history_date(row)
    if latest is None:
        return False
    return latest >= date.fromisoformat(plan.trade_date)


def _latest_history_date(row: Mapping[str, object]) -> date | None:
    history = row.get("history")
    if not isinstance(history, Sequence) or isinstance(history, (str, bytes, bytearray)):
        return None
    dates = tuple(
        parsed
        for item in history
        if isinstance(item, Mapping)
        if (parsed := _parse_date(item.get("date") or item.get("trade_date"))) is not None
    )
    if not dates:
        return None
    return max(dates)


def _failed_provider_result(
    *,
    plan: SelectionRunPlan,
    data_need_audit: SelectionDataNeedAudit,
    gap_code: str,
    message: str,
) -> SelectionDataNeedResult:
    return SelectionDataNeedResult(
        data_need_audit=data_need_audit,
        attempt_refs=(),
        normalized_refs=(),
        rows=(),
        data_gaps=(
            _blocker_gap(
                gap_id=f"{plan.selection_run_id}-{gap_code}",
                gap_code=gap_code,
                attempt_refs=(data_need_audit.lineage_root_ref,),
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
    is_universe_refresh = ":universe_refresh:" in str(gap.request_id)
    severity = (
        DataGapSeverity.WARN
        if is_history_backfill or is_universe_refresh or data_type in _OPTIONAL_SELECTION_SUPPLEMENTAL_DATA_TYPES
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


def _is_repaired_universe_refresh_gap(
    *,
    plan: SelectionRunPlan,
    gap: DataGap,
    rows: tuple[Mapping[str, object], ...],
    results: tuple[DataResult, ...],
) -> bool:
    if not _universe_refresh_attempted(results=results):
        return False
    if str(gap.request_id) != f"{plan.selection_run_id}:selection:1:daily_bar":
        return False
    if not rows or not all(_row_covers_trade_date(row=row, plan=plan) for row in rows):
        return False
    return _is_universe_refresh_trigger_gap(gap)


def _universe_refresh_attempted(*, results: tuple[DataResult, ...]) -> bool:
    return any(":selection:universe_refresh:" in result.request_id for result in results)


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


def _enum_value(value: Any) -> str:
    enum_value = getattr(value, "value", None)
    if isinstance(enum_value, str):
        return enum_value
    return str(value)


def _normalized_ref(
    dataset_ref: str,
    market: SelectionMarket = SelectionMarket.CN_A,
) -> str:
    return normalize_normalized_dataset_ref(dataset_ref, market=_enum_value(market))


def _normalize_normalized_refs(
    refs: Iterable[str],
    market: SelectionMarket = SelectionMarket.CN_A,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(_normalized_ref(str(ref), market=market) for ref in refs if str(ref).strip())
    )


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
        ticker = _normalize_crypto_ticker(str(value))
        if ticker is not None:
            return ticker
    return None


def _normalize_crypto_ticker(value: str) -> str | None:
    text = value.strip().upper()
    if not text:
        return None
    cleaned = text.replace("-", "/").replace("_", "/")
    if "/" in cleaned:
        base, quote = cleaned.split("/", 1)
        if base and quote:
            return f"{base}{quote}"
    for quote in ("USD", "USDT", "USDC", "FDUSD", "BUSD", "TUSD", "BTC", "ETH", "BNB"):
        if cleaned.endswith(quote) and len(cleaned) > len(quote):
            base = cleaned[: -len(quote)]
            if base:
                return f"{base}{quote}"
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
            if target == "amount":
                value = _normalized_amount_value(row=row, value=value)
            mapped[target] = value
    return mapped


def _normalized_amount_value(*, row: Mapping[str, Any], value: float) -> float:
    amount_unit = str(row.get("amount_unit") or "").strip().upper()
    if amount_unit in {"CNY", "RMB", "YUAN"}:
        return value
    if amount_unit in {"CNY_1000", "RMB_1000", "THOUSAND_CNY"}:
        return value * 1000.0
    if _is_legacy_tushare_cn_a_daily_row(row):
        return value * 1000.0
    return value


def _is_legacy_tushare_cn_a_daily_row(row: Mapping[str, Any]) -> bool:
    if str(row.get("market") or "").strip().upper() not in {"", "CN_A"}:
        return False
    lineage = row.get("provider_lineage")
    if not isinstance(lineage, Mapping):
        return False
    provider_id = str(lineage.get("provider_id") or lineage.get("provider") or "").strip()
    endpoint_id = str(lineage.get("endpoint_id") or lineage.get("endpoint") or "").strip()
    return provider_id in {"cn_a_primary", "tushare"} and endpoint_id in {"daily_bar", "daily_bar_by_trade_date", "daily"}


def _row_date(row: Mapping[str, Any]) -> str | None:
    value = row.get("date") or row.get("trade_date") or row.get("period_start")
    parsed = _parse_date(value)
    return parsed.isoformat() if parsed is not None else None


def _row_source_ref(
    row: Mapping[str, Any],
    *,
    market: SelectionMarket = SelectionMarket.CN_A,
) -> str:
    value = row.get("dataset_ref") or row.get("source_ref")
    if value is not None and str(value).strip():
        return _normalized_ref(str(value).strip(), market=market)
    lineage = row.get("provider_lineage")
    if isinstance(lineage, Mapping):
        provider_id = lineage.get("provider_id") or lineage.get("provider")
        endpoint_id = lineage.get("endpoint_id") or lineage.get("endpoint")
        if provider_id and endpoint_id:
            return f"provider://{provider_id}/{endpoint_id}"
    return normalize_normalized_dataset_ref("unknown", market=_enum_value(market))


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
    provider_result: SelectionDataNeedResult,
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
