from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any, Mapping, Sequence

try:
    import fcntl
except ImportError:  # pragma: no cover - non-Unix fallback
    fcntl = None  # type: ignore[assignment]

from claw_trade.data_gateway.models import DataGap, DataResult, DataResultStatus, GapReason, Market
from claw_trade.data_gateway.granularity import canonical_granularity
from claw_trade.data_gateway.public_api import forbidden_public_payload_keys
from claw_trade.data_gateway.report_evidence import (
    CRYPTO_QUOTE_ASSETS,
    _provider_attempt_summary,
    analyze_crypto_lens_report_data_results,
    map_crypto_asset_to_symbol,
    run_data_need_tool_request,
)
from claw_trade.reports import market_charts, market_indicators

_DATA_LAYER_ATTEMPT_SUMMARY_KEY = "provider_" + "attempts_summary"

_ROW_FIELD_EXCLUDE = {
    "dataset",
    "dataset_ref",
    "market",
    "granularity",
    "exchange",
    "currency",
    "timezone",
    "calendar",
    "base_asset",
    "quote_asset",
    "provider_lineage",
    "source_raw_refs",
    "quality_flags",
    "schema_id",
    "field_set",
    "source_roles",
    "source_raw_ref",
    "source_dataset_ref",
    "timestamp_utc",
    "raw_payload",
    "payload",
}
_AUDIT_ROW_MAX_FIELDS = 24
_AUDIT_ROW_MAX_STRING_CHARS = 500

_ROW_LEVEL_LIMIT_REASONS = {
    "date_range_missing",
    "empty_result",
    "provider_error",
    "rate_limited",
    "warehouse_missing",
    "warehouse_stale",
    "cached_empty",
    "cooldown_skipped",
    "granularity_mismatch",
}
_MATERIAL_RESULT_GAP_REASONS = frozenset(
    {
        GapReason.FIELD_MISSING,
        GapReason.DATE_RANGE_MISSING,
        GapReason.DATA_INTEGRITY_FAILED,
        GapReason.GRANULARITY_MISMATCH,
    }
)
_READER_SOURCE_LABELS = {
    "paid_data": "付费数据源",
    "free_data": "公共数据源",
    "discovery": "公开搜索线索",
    "sentiment": "情绪数据源",
    "coinglass_aggregated": "衍生品与链上聚合数据源",
}

_MARKET_DEFAULTS: dict[Market, dict[str, str | None]] = {
    Market.CN_A: {
        "exchange": "SSE",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
    },
    Market.US: {
        "exchange": "NASDAQ",
        "currency": "USD",
        "timezone": "America/New_York",
        "calendar": "US_NYSE_NASDAQ",
    },
    Market.HK: {
        "exchange": "XHKG",
        "currency": "HKD",
        "timezone": "Asia/Hong_Kong",
        "calendar": "HK_XHKG",
    },
    Market.CRYPTO: {
        "exchange": "BINANCE",
        "currency": "USDT",
        "timezone": "UTC",
        "calendar": "CRYPTO_24_7",
    },
}
_IN_PROCESS_DATA_NEED_LOCKS: dict[str, threading.Lock] = {}
_IN_PROCESS_DATA_NEED_LOCKS_GUARD = threading.Lock()


@dataclass(frozen=True)
class _SymbolParts:
    symbol_id: str
    exchange: str | None
    currency: str
    base_asset: str | None = None
    quote_asset: str | None = None


def run_claw_request_data(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    cached_result = _cached_data_need_tool_result_for_request(tool_input=tool_input, runtime_context=runtime_context)
    if cached_result is not None:
        return cached_result

    with _same_worker_data_need_cache_lock(tool_input=tool_input, runtime_context=runtime_context):
        cached_result = _cached_data_need_tool_result_for_request(tool_input=tool_input, runtime_context=runtime_context)
        if cached_result is not None:
            return cached_result
        return _run_claw_request_data_uncached(tool_input=tool_input, runtime_context=runtime_context)


def _cached_data_need_tool_result_for_request(
    *,
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
) -> dict[str, Any] | None:
    cached_payload = _cached_ready_data_need_tool_payload(
        tool_input=tool_input,
        runtime_context=runtime_context,
        include_request_shape=True,
        ready_only=True,
    )
    if cached_payload is not None:
        return _cached_data_need_tool_result(
            runtime_context=runtime_context,
            cached_payload=cached_payload,
            cache_scope="same_worker_call",
        )
    cached_payload = _cached_ready_data_need_tool_payload(
        tool_input=tool_input,
        runtime_context=runtime_context,
        include_request_shape=False,
        ready_only=True,
    )
    if cached_payload is not None:
        return _cached_data_need_tool_result(
            runtime_context=runtime_context,
            cached_payload=cached_payload,
            cache_scope="same_worker_call_business_need",
        )
    cached_payload = _cached_ready_data_need_tool_payload(
        tool_input=tool_input,
        runtime_context=runtime_context,
        include_request_shape=True,
        ready_only=False,
    )
    if cached_payload is not None:
        return _cached_data_need_tool_result(
            runtime_context=runtime_context,
            cached_payload=cached_payload,
            cache_scope="same_worker_call_terminal_result",
        )
    cached_payload = _cached_ready_data_need_tool_payload(
        tool_input=tool_input,
        runtime_context=runtime_context,
        include_request_shape=False,
        ready_only=False,
    )
    if cached_payload is not None:
        return _cached_data_need_tool_result(
            runtime_context=runtime_context,
            cached_payload=cached_payload,
            cache_scope="same_worker_call_business_need_terminal_result",
        )
    return None


def _run_claw_request_data_uncached(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    payload = run_data_need_tool_request(tool_input, runtime_context)
    if payload.get("ok") is not True:
        return payload
    request_payload = payload.get("request") if isinstance(payload.get("request"), Mapping) else {}
    need_domain = str(payload.get("need_domain") or "")
    data_results = tuple(_data_result_from_payload(item) for item in _as_sequence(payload.get("data_results")))
    refs_payload = payload.get("refs") if isinstance(payload.get("refs"), Mapping) else {}
    refs = _NeedResultRefs()
    refs.dataset_refs = tuple(str(item) for item in _as_sequence(refs_payload.get("dataset_refs") if isinstance(refs_payload, Mapping) else ()))
    refs.raw_refs = tuple(str(item) for item in _as_sequence(refs_payload.get("raw_refs") if isinstance(refs_payload, Mapping) else ()))
    refs.attempt_refs = tuple(str(item) for item in _as_sequence(refs_payload.get("attempt_refs") if isinstance(refs_payload, Mapping) else ()))
    refs.gaps = tuple(_gap_from_payload(item) for item in _as_sequence(refs_payload.get("gaps") if isinstance(refs_payload, Mapping) else ()))
    gaps = tuple(_gap_from_payload(item) for item in _as_sequence(payload.get("gaps")))
    attempts = tuple(item for item in _as_sequence(payload.get("provider" + "_attempts_summary")) if isinstance(item, Mapping))
    status = str(payload.get("status") or "missing")
    need_satisfied = _payload_need_satisfied(payload=payload, data_results=data_results, status=status)
    chart_tool_input = payload.get("chart_tool_input") if isinstance(payload.get("chart_tool_input"), Mapping) else {}
    chart_payload = (
        _market_chart_payload(tool_input=chart_tool_input, runtime_context=runtime_context, results=data_results)
        if _should_build_market_chart(need_domain=need_domain, data_results=data_results)
        else {}
    )
    market = _market_from_request_payload(request_payload)
    crypto_lens_payload = (
        _crypto_lens_payload(tool_input=chart_tool_input or tool_input, runtime_context=runtime_context, results=data_results)
        if _should_build_crypto_lens(market=market, data_results=data_results)
        else {}
    )
    model_visible_text = _data_need_model_visible_text(
        request=request_payload,
        need_domain=need_domain,
        status=status,
        planned_count=int(payload.get("planned_calls_count") or 0),
        scheduled_count=int(payload.get("scheduled_calls_count") or 0),
        refs=refs,
        gaps=gaps,
        attempts=attempts,
        data_results=data_results,
        chart_payload=chart_payload,
        crypto_lens_payload=crypto_lens_payload,
        crypto_base_asset=_crypto_base_asset_from_results(
            results=data_results,
            tool_input=chart_tool_input or tool_input,
            market=market,
        ),
        need_satisfied=need_satisfied,
    )
    latest_value_summary = _latest_value_summaries(data_results)
    result_payload = {**payload, **chart_payload, **crypto_lens_payload}
    result_payload["tool_input"] = dict(tool_input)
    result_payload["model_visible_text"] = model_visible_text
    result_payload["readable_summary"] = model_visible_text
    result_payload["latest_value_summary"] = latest_value_summary
    if not need_satisfied:
        cached_payload = _cached_ready_data_need_tool_payload(
            tool_input=tool_input,
            runtime_context=runtime_context,
            include_request_shape=False,
            ready_only=True,
        )
        if cached_payload is not None:
            return _cached_data_need_tool_result(
                runtime_context=runtime_context,
                cached_payload=cached_payload,
                cache_scope="same_worker_call_prior_ready_after_unsatisfied",
                extra_fields={
                    "unsatisfied_request_status": status,
                    "unsatisfied_request_gaps": payload.get("gaps") or (),
                    "unsatisfied_request_attempts_summary": payload.get(_DATA_LAYER_ATTEMPT_SUMMARY_KEY) or (),
                },
            )
    evidence_paths = _write_data_need_result_evidence(runtime_context=runtime_context, payload=result_payload)
    if evidence_paths:
        result_payload["data_need_evidence_paths"] = evidence_paths
    return result_payload


@contextmanager
def _same_worker_data_need_cache_lock(*, tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> Any:
    lock_path = _same_worker_data_need_lock_path(tool_input=tool_input, runtime_context=runtime_context)
    if lock_path is None:
        yield
        return
    lock_key = str(lock_path)
    with _IN_PROCESS_DATA_NEED_LOCKS_GUARD:
        process_lock = _IN_PROCESS_DATA_NEED_LOCKS.setdefault(lock_key, threading.Lock())
    with process_lock:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with lock_path.open("a+", encoding="utf-8") as handle:
            if fcntl is not None:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _same_worker_data_need_lock_path(*, tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> Path | None:
    current_path = _data_need_result_evidence_path(runtime_context)
    if current_path is None:
        return None
    worker_prefix = _worker_call_prefix(runtime_context)
    if not worker_prefix:
        return None
    cache_key = _data_need_tool_cache_key(tool_input, include_request_shape=False)
    if cache_key is None:
        return None
    digest = sha256(json.dumps(cache_key, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
    return current_path.parent.parent / ".locks" / f"{worker_prefix}-{digest}.lock"


def _cached_data_need_tool_result(
    *,
    runtime_context: Mapping[str, Any],
    cached_payload: "_CachedDataNeedToolPayload",
    cache_scope: str,
    extra_fields: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result_payload = dict(cached_payload.payload)
    result_payload["cache_reused_from"] = cached_payload.path
    result_payload["cache_scope"] = cache_scope
    if extra_fields:
        result_payload.update(extra_fields)
    if result_payload.get("model_visible_text") and not result_payload.get("readable_summary"):
        result_payload["readable_summary"] = result_payload["model_visible_text"]
    attempts = result_payload.get("data_attempts_summary")
    if attempts is not None and _DATA_LAYER_ATTEMPT_SUMMARY_KEY not in result_payload:
        result_payload[_DATA_LAYER_ATTEMPT_SUMMARY_KEY] = attempts
    evidence_paths = _write_cached_data_need_result_evidence(
        runtime_context=runtime_context,
        payload=result_payload,
        reused_from=cached_payload.path,
        cache_scope=cache_scope,
    )
    if evidence_paths:
        result_payload["data_need_evidence_paths"] = evidence_paths
    return result_payload


class _NeedResultRefs:
    def __init__(self) -> None:
        self.dataset_refs: tuple[str, ...] = ()
        self.raw_refs: tuple[str, ...] = ()
        self.attempt_refs: tuple[str, ...] = ()
        self.gaps: tuple[DataGap, ...] = ()

    def add_ingest(self, ingest: Any) -> None:
        self.dataset_refs = tuple(_dedupe((*self.dataset_refs, *tuple(getattr(ingest, "dataset_refs", ()) or ()))))
        self.raw_refs = tuple(_dedupe((*self.raw_refs, *tuple(getattr(ingest, "raw_refs", ()) or ()))))
        self.attempt_refs = tuple(_dedupe((*self.attempt_refs, *tuple(getattr(ingest, "attempt_refs", ()) or ()))))
        self.gaps = (*self.gaps, *tuple(getattr(ingest, "gaps", ()) or ()))


@dataclass(frozen=True)
class _CachedDataNeedToolPayload:
    path: str
    payload: Mapping[str, Any]


def _cached_ready_data_need_tool_payload(
    *,
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
    include_request_shape: bool,
    ready_only: bool,
) -> _CachedDataNeedToolPayload | None:
    cache_key = _data_need_tool_cache_key(tool_input, include_request_shape=include_request_shape)
    if cache_key is None:
        return None
    for path in _prior_data_need_result_paths(runtime_context):
        payload = _read_json_object(path)
        if payload is None:
            continue
        if ready_only and not _cached_data_need_payload_is_ready(payload):
            continue
        if not ready_only and not _cached_data_need_payload_is_terminal(payload):
            continue
        if _data_need_payload_cache_key(payload, include_request_shape=include_request_shape) != cache_key:
            continue
        return _CachedDataNeedToolPayload(path=str(path), payload=payload)
    return None


def _data_need_tool_cache_key(tool_input: Mapping[str, Any], *, include_request_shape: bool) -> tuple[Any, ...] | None:
    if forbidden_public_payload_keys(tool_input):
        return None
    try:
        market = _market_from_request_payload({"market": tool_input.get("market")})
        instrument = _normalize_need_instrument(
            _required_text(tool_input.get("instrument") or tool_input.get("ticker"), "instrument"),
            market=market,
        )
        item = _required_text(tool_input.get("item"), "item")
        purpose = _required_text(tool_input.get("purpose"), "purpose")
    except Exception:
        return None
    base_key = (
        market.value,
        instrument,
        _normalized_cache_text(item),
        _normalized_cache_text(purpose),
    )
    if not include_request_shape:
        return base_key
    return (
        *base_key,
        canonical_granularity(_optional_text(tool_input.get("granularity"))),
        _request_time_range_cache_key(tool_input),
    )


def _data_need_payload_cache_key(payload: Mapping[str, Any], *, include_request_shape: bool) -> tuple[Any, ...] | None:
    request = payload.get("tool_input") if isinstance(payload.get("tool_input"), Mapping) else payload.get("request")
    if not isinstance(request, Mapping):
        return None
    return _data_need_tool_cache_key(request, include_request_shape=include_request_shape)


def _cached_data_need_payload_is_ready(payload: Mapping[str, Any]) -> bool:
    if payload.get("ok") is not True or str(payload.get("status") or "") != "ready":
        return False
    if payload.get("need_satisfied") is not True:
        return False
    refs = payload.get("refs")
    if isinstance(refs, Mapping) and (refs.get("dataset_refs") or refs.get("raw_refs")):
        return True
    for item in _as_sequence(payload.get("data_results")):
        if not isinstance(item, Mapping):
            continue
        if int(item.get("row_count") or 0) > 0:
            return True
        rows = tuple(row for row in _as_sequence(item.get("rows")) if isinstance(row, Mapping))
        if rows:
            return True
    return False


def _cached_data_need_payload_is_terminal(payload: Mapping[str, Any]) -> bool:
    if payload.get("ok") is not True:
        return False
    if str(payload.get("status") or "") not in {"ready", "partial", "missing"}:
        return False
    return bool(payload.get("model_visible_text") or payload.get("readable_summary"))


def _prior_data_need_result_paths(runtime_context: Mapping[str, Any]) -> tuple[Path, ...]:
    current_path = _data_need_result_evidence_path(runtime_context)
    if current_path is None:
        return ()
    result_dir = current_path.parent.parent
    if not result_dir.exists():
        return ()
    worker_prefix = _worker_call_prefix(runtime_context)
    if not worker_prefix:
        return ()
    current_call_id = current_path.parent.name
    paths: list[Path] = []
    for call_dir in result_dir.iterdir():
        if not call_dir.is_dir() or call_dir.name == current_call_id:
            continue
        if worker_prefix and not call_dir.name.startswith(worker_prefix):
            continue
        path = call_dir / "result.json"
        if path.exists():
            paths.append(path)
    return tuple(sorted(paths, key=lambda item: (item.stat().st_mtime_ns, item.as_posix()), reverse=True))


def _worker_call_prefix(runtime_context: Mapping[str, Any]) -> str:
    worker_call_id = str(runtime_context.get("worker_call_id") or "").strip()
    if worker_call_id:
        return _safe_path_token(worker_call_id)
    call_id = str(runtime_context.get("call_id") or "").strip()
    if "__tool-call_" in call_id:
        return _safe_path_token(call_id.split("__tool-call_", 1)[0])
    return ""


def _read_json_object(path: Path) -> Mapping[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, Mapping) else None


def _normalized_cache_text(value: str) -> str:
    return " ".join(value.strip().casefold().split())


def _time_range_cache_key(value: Any) -> tuple[Any, ...] | None:
    if not isinstance(value, Mapping):
        return None
    start = _optional_text(value.get("start") or value.get("time_range_start"))
    end = _optional_text(value.get("end") or value.get("time_range_end"))
    lookback = _positive_int(value.get("lookback_days"))
    return (start, end, lookback)


def _request_time_range_cache_key(value: Mapping[str, Any]) -> tuple[Any, ...] | None:
    nested = value.get("time_range")
    if isinstance(nested, Mapping):
        return _time_range_cache_key(nested)
    return _time_range_cache_key(value)


def _data_result_from_payload(value: Any) -> DataResult:
    if isinstance(value, DataResult):
        return value
    if isinstance(value, Mapping):
        return DataResult.model_validate(value)
    raise ValueError("data_result payload must be object")


def _gap_from_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        return SimpleGap(reason=value.get("reason"))
    return value


@dataclass(frozen=True)
class SimpleGap:
    reason: Any


def _as_sequence(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def _data_need_status(*, refs: _NeedResultRefs, gap_count: int, attempts: Sequence[Mapping[str, Any]]) -> str:
    has_data_refs = bool(refs.dataset_refs or refs.raw_refs)
    if has_data_refs and gap_count:
        return "partial"
    if has_data_refs:
        return "partial"
    if gap_count:
        return "missing"
    return "missing"


def _data_need_model_visible_text(
    *,
    request: Mapping[str, Any],
    need_domain: str,
    status: str,
    planned_count: int,
    scheduled_count: int,
    refs: _NeedResultRefs,
    gaps: Sequence[Any],
    attempts: Sequence[Mapping[str, Any]],
    data_results: Sequence[DataResult] = (),
    chart_payload: Mapping[str, Any] | None = None,
    crypto_lens_payload: Mapping[str, Any] | None = None,
    crypto_base_asset: str = "",
    need_satisfied: bool = False,
) -> str:
    result_gaps = _data_result_gaps(data_results)
    has_body_usable_result = _has_body_usable_result(data_results)
    display_status = "ready" if need_satisfied and has_body_usable_result else status
    market = _market_from_request_payload(request)
    instrument = str(request.get("instrument") or "标的")
    lines = [
        f"数据结果：{market.value} {instrument} 的{_business_data_label_from_request(request)}，状态：{_status_zh(display_status)}。",
    ]
    if data_results:
        lines.extend(_data_need_row_summary_lines(data_results=data_results, market=market, domain=need_domain))
    if chart_payload:
        lines.extend(_chart_brief_lines(chart_payload))
    if crypto_lens_payload:
        lines.extend(
            _crypto_lens_brief_lines(
                crypto_lens_payload,
                chart_payload=chart_payload or {},
                base_asset=crypto_base_asset,
            )
        )
    if data_results:
        if need_satisfied and has_body_usable_result:
            lines.append("本次数据结果已经可用于正文；不要重复请求同一数据需求。")
        elif result_gaps:
            lines.append("本次数据结果只有部分可用于正文；正文必须说明下列数据缺口，不要重复请求同一数据需求。")
        else:
            lines.append("本次返回了结构化数据，但没有满足当前数据需求；只能作为缺口证据，不要当成正文可引用结论。")
            lines.append("不要重复请求同一数据需求；如需补充，只能提出更具体的新数据需求。")
    elif attempts:
        if need_satisfied:
            lines.append("本次真实查询未返回事件记录；可在正文中表述为当前查询范围内未发现该类事件。")
            lines.append("不要重复请求同一数据需求。")
        else:
            lines.append("本次数据需求已经完成真实数据尝试；不要重复请求同一数据需求。")
            lines.append("当前没有返回可直接引用的结构化数值或明细行；原始材料不等于正文可引用指标。")
            lines.append("不得用模型记忆、公开常识或工具外资料补写营收、利润、ROE、PE/PB、目标价、新闻事实或情绪结论。")
    if need_satisfied and has_body_usable_result:
        all_gap_text: list[str] = []
    else:
        all_gap_text = _visible_gap_labels((*tuple(gaps), *refs.gaps))
    if need_satisfied and all_gap_text:
        result_gap_text = _visible_gap_labels(result_gaps)
        candidate_gap_text = [label for label in all_gap_text if label not in set(result_gap_text)]
        if result_gap_text:
            if has_body_usable_result:
                lines.append("补充说明：" + "；".join(result_gap_text[:6]) + "。当前数据需求已有可用结论。")
            else:
                lines.append("数据缺口：" + "；".join(result_gap_text[:6]))
        if candidate_gap_text:
            lines.append("补充说明：" + "；".join(candidate_gap_text[:6]) + "。当前数据需求已有可用结论。")
        all_gap_text = []
    if all_gap_text:
        lines.append("数据缺口：" + "；".join(all_gap_text[:6]))
    if attempts and not need_satisfied and not (refs.dataset_refs or refs.raw_refs):
        lines.append("已执行真实数据尝试，但没有产生可引用的结构化或原始材料。")
        lines.append("没有可引用数据时，不得写具体价格、技术指标数值、支撑压力、评级、目标价或止损位。")
    if not attempts and planned_count == 0:
        lines.append("当前没有可用的数据材料；这是数据层覆盖缺口，不代表所有数据来源永久不可用。")
    return "\n".join(lines)


def _business_data_label_from_request(request: Mapping[str, Any]) -> str:
    item = str(request.get("item") or "").strip()
    return item or "请求数据"


def _market_from_request_payload(request: Mapping[str, Any]) -> Market:
    raw = request.get("market")
    if isinstance(raw, Market):
        return raw
    if isinstance(raw, str):
        try:
            return Market(raw.strip().upper())
        except ValueError:
            return Market.CN_A
    return Market.CN_A


def _payload_need_satisfied(*, payload: Mapping[str, Any], data_results: Sequence[DataResult], status: str) -> bool:
    if payload.get("need_satisfied") is True:
        return True
    if any(_gap_reason(gap) in _MATERIAL_RESULT_GAP_REASONS for gap in _data_result_gaps(data_results)):
        return False
    return _has_body_usable_result(data_results)


def _has_gap_free_body_result(data_results: Sequence[DataResult]) -> bool:
    return any(result.rows and not _data_result_has_material_gap(result) for result in data_results)


def _has_body_usable_result(data_results: Sequence[DataResult]) -> bool:
    return any(
        result.rows
        and not _data_result_has_material_gap(result)
        and any(isinstance(row, Mapping) and not _row_is_discovery_only(row) for row in result.rows)
        for result in data_results
    )


def _row_is_discovery_only(row: Mapping[str, Any]) -> bool:
    source_roles = _string_values(row.get("source_roles"))
    if "discovery" in source_roles:
        return True
    if row.get("can_be_formal_fact_source") is False:
        return True
    quality_flags = _string_values(row.get("quality_flags"))
    return "discovery_not_formal_fact_source" in quality_flags


def _string_values(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        items = (value,)
    elif isinstance(value, Sequence):
        items = tuple(value)
    else:
        items = (value,)
    return tuple(str(item).strip().lower() for item in items if str(item).strip())


def _data_need_row_summary_lines(*, data_results: Sequence[DataResult], market: Market, domain: str) -> list[str]:
    lines = ["已返回的数据摘要："]
    seen_blocks: set[str] = set()
    for result in data_results[:8]:
        if not result.rows:
            continue
        dataset = _result_dataset(result.request_id) or result.request_id.split(":")[-1]
        block_key = f"{dataset}:{_row_date_range(result.rows)}:{len(result.rows)}:{_row_block_identity(result.rows)}"
        if block_key in seen_blocks:
            continue
        seen_blocks.add(block_key)
        fields = ", ".join(_row_fields(result.rows)) or "未声明"
        date_range = _row_date_range(result.rows)
        latest_value_summary = _latest_value_summary(rows=result.rows, dataset=dataset)
        stats_summary = _dataset_stats_summary(rows=result.rows, dataset=dataset)
        lines.append(f"- {_dataset_count_summary(dataset=dataset, rows=result.rows, market=market)}")
        if latest_value_summary:
            lines.append(f"  - 最新读数：{latest_value_summary}")
        if stats_summary:
            lines.append(f"  - 历史/样本统计：{stats_summary}")
        lines.append(f"  - 字段覆盖 {fields}。")
        if date_range:
            lines.append(f"  - 时间覆盖 {date_range[0]} 至 {date_range[1]}，下列摘要优先展示最新记录。")
        seen_summaries: set[str] = set()
        for row in _recent_rows(result.rows):
            summary = _row_summary(row, domain=domain, dataset=dataset)
            if not summary or summary in seen_summaries:
                continue
            seen_summaries.add(summary)
            lines.append(f"  - {summary}")
    return lines


def _dataset_stats_summary(*, rows: Sequence[Mapping[str, Any]], dataset: str) -> str:
    if dataset == "crypto_derivative_metric":
        return _crypto_derivative_stats_summary(rows)
    if dataset == "crypto_onchain_metric":
        return _ahr999_stats_summary(rows)
    if dataset == "event_calendar":
        return _event_calendar_source_summary(rows)
    if dataset == "social_signal":
        return _social_signal_source_summary(rows)
    return ""


def _dataset_count_summary(*, dataset: str, rows: Sequence[Mapping[str, Any]], market: Market) -> str:
    label = _dataset_label(dataset, market=market)
    if dataset == "order_book_snapshot" and rows:
        latest = _recent_rows(rows, limit=1)[0]
        bid_levels = latest.get("bid_levels")
        ask_levels = latest.get("ask_levels")
        if bid_levels is not None or ask_levels is not None:
            level_parts: list[str] = []
            if bid_levels is not None:
                level_parts.append(f"买盘 {bid_levels} 档")
            if ask_levels is not None:
                level_parts.append(f"卖盘 {ask_levels} 档")
            level_text = "，".join(level_parts)
            return f"{label}：{len(rows)} 个快照，含{level_text}；这不是单点买一/卖一。"
        return f"{label}：{len(rows)} 个快照。"
    return f"{label}：{len(rows)} 行。"


def _crypto_derivative_stats_summary(rows: Sequence[Mapping[str, Any]]) -> str:
    parts: list[str] = []
    cvd_rows = [row for row in _recent_rows(rows, limit=len(rows)) if _numeric_value(row.get("cvd")) is not None]
    if cvd_rows:
        latest = cvd_rows[0]
        oldest = cvd_rows[-1]
        latest_value = _numeric_value(latest.get("cvd"))
        oldest_value = _numeric_value(oldest.get("cvd"))
        delta = None if latest_value is None or oldest_value is None else latest_value - oldest_value
        granularity = str(latest.get("granularity") or "").strip().lower()
        granularity_text = "小时级样本" if granularity in {"hourly", "1h"} else f"{granularity} 样本" if granularity else "样本"
        if latest_value is not None:
            text = f"CVD 最新值 {_value_with_unit(_display_value(latest_value), latest.get('taker_volume_unit'))}，{granularity_text} {len(cvd_rows)} 条"
            if delta is not None and len(cvd_rows) > 1:
                text += f"，样本变化 {_value_with_unit(_display_value(delta), latest.get('taker_volume_unit'))}"
            text += "；阈值按本次样本解释，不能当成回测胜率。"
            parts.append(text)
    oi_rows = [row for row in _recent_rows(rows, limit=len(rows)) if _numeric_value(row.get("open_interest")) is not None]
    if oi_rows:
        quote_types = {_oi_quote_structure_label(row) for row in oi_rows}
        quote_types.discard("")
        if quote_types:
            parts.append("OI 计价结构：" + "、".join(sorted(quote_types)) + "。")
    return " ".join(parts)


def _oi_quote_structure_label(row: Mapping[str, Any]) -> str:
    quote_asset = str(row.get("quote_asset") or "").strip().upper()
    unit = str(row.get("open_interest_unit") or "").strip().upper()
    symbol = str(row.get("symbol_id") or "").strip().upper()
    if quote_asset in {"USDT", "USDC", "BUSD", "USD"} or unit in {"USD", "USDT", "USDC", "BUSD"} or symbol.endswith(("USDT", "USDC", "BUSD", "USD")):
        return "U 本位/稳定币计价"
    base_asset = str(row.get("base_asset") or "").strip().upper()
    if base_asset and (unit == base_asset or symbol.endswith(("USD_PERP", "USD"))):
        return "币本位"
    return ""


def _ahr999_stats_summary(rows: Sequence[Mapping[str, Any]]) -> str:
    values: list[float] = []
    current_value: float | None = None
    for row in rows:
        metric = str(row.get("metric") or "").strip().lower()
        value = row.get("ahr999")
        if value is None and metric == "ahr999":
            value = row.get("value") or row.get("metric_value")
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        values.append(numeric)
    for row in _recent_rows(rows, limit=len(rows)):
        metric = str(row.get("metric") or "").strip().lower()
        value = row.get("ahr999")
        if value is None and metric == "ahr999":
            value = row.get("value") or row.get("metric_value")
        try:
            current_value = float(value)
            break
        except (TypeError, ValueError):
            continue
    if not values:
        return ""
    current = current_value if current_value is not None else values[0]
    ordered = sorted(values)
    below_or_equal = sum(1 for value in ordered if value <= current)
    percentile = below_or_equal / len(ordered) * 100
    return f"AHR999 最新值 {current:.6g}，样本 {len(values)} 条，历史分位约 {percentile:.1f}%（按本次返回样本计算，不代表回测胜率）。"


def _event_calendar_source_summary(rows: Sequence[Mapping[str, Any]]) -> str:
    sources = _sample_source_counts(rows)
    parts: list[str] = []
    if sources:
        top = "、".join(f"{source} {count} 条" for source, count in sources[:5])
        parts.append(f"事件来源分布：{top}。")
    event_types = _event_type_counts(rows)
    if event_types:
        rendered = "、".join(f"{label} {count} 条" for label, count in event_types[:5])
        parts.append(f"事件类型分布：{rendered}。")
    high_impact = _high_impact_event_count(rows)
    if high_impact:
        parts.append(f"高影响事件 {high_impact} 条；方向和价格影响仍需结合正文证据确认。")
    if not parts:
        return ""
    parts.append("宏观经济日历、项目/协议事件和搜索线索需分开解读。")
    return "".join(parts)


def _event_type_counts(rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for row in rows:
        label = _event_type_label(row.get("event_type") or row.get("source") or row.get("dataset"))
        counts[label] = counts.get(label, 0) + 1
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _event_type_label(value: Any) -> str:
    text = str(value or "").strip().lower()
    if any(token in text for token in ("economic", "macro", "cpi", "fomc", "fed", "interest", "calendar")):
        return "宏观经济日历"
    if any(token in text for token in ("release", "github", "protocol", "project", "core")):
        return "项目/协议事件"
    if any(token in text for token in ("news", "google", "rss", "media")):
        return "媒体/搜索线索"
    if any(token in text for token in ("regulat", "policy", "sec", "cftc")):
        return "监管/政策事件"
    return "其它事件"


def _high_impact_event_count(rows: Sequence[Mapping[str, Any]]) -> int:
    total = 0
    for row in rows:
        value = row.get("importance_level") or row.get("importance") or row.get("impact_level")
        numeric = _numeric_value(value)
        if numeric is not None and numeric >= 3:
            total += 1
            continue
        text = str(value or "").strip().lower()
        if text in {"high", "important", "major"}:
            total += 1
    return total


def _social_signal_source_summary(rows: Sequence[Mapping[str, Any]]) -> str:
    sources = _sample_source_counts(rows)
    if not sources:
        return ""
    top = "、".join(f"{source} {count} 条" for source, count in sources[:5])
    scale_note = _social_score_scale_note(rows)
    platform_rows = [
        row
        for row in rows
        if any(str(row.get(key) or "").strip() for key in ("message", "text", "topic", "url"))
    ]
    if platform_rows:
        base = f"舆情来源分布：{top}；其中 {len(platform_rows)} 条含可引用文本/话题样本。"
        return f"{base}{scale_note}"
    base = f"舆情来源分布：{top}；本次返回的是市场级情绪/热度指标，不是社交平台原文样本。"
    return f"{base}{scale_note}"


def _social_score_scale_note(rows: Sequence[Mapping[str, Any]]) -> str:
    for row in rows:
        source = str(row.get("source") or row.get("provider_id") or row.get("source_type") or "").strip().lower()
        has_score = row.get("score") is not None or row.get("sentiment") is not None or row.get("value") is not None
        explicit_min = _numeric_value(row.get("score_min"))
        explicit_max = _numeric_value(row.get("score_max"))
        if explicit_min is not None and explicit_max is not None:
            return f"；分数范围 {_compact_number(explicit_min)}-{_compact_number(explicit_max)}，分数越高代表越贪婪/乐观。"
        if has_score and ("fear" in source or "greed" in source or "coinglass" in source or "alternative.me" in source):
            return "；分数范围 0-100，分数越高代表越贪婪/乐观；这是市场级情绪刻度，不是社交平台原文样本。"
    return ""


def _compact_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return _display_value(value)


def _sample_source_counts(rows: Sequence[Mapping[str, Any]]) -> tuple[tuple[str, int], ...]:
    counts: dict[str, int] = {}
    for row in rows:
        source = str(row.get("source") or row.get("provider_id") or row.get("source_type") or "").strip()
        if not source:
            source = "未声明来源"
        else:
            source = _reader_source_label(source)
        counts[source] = counts.get(source, 0) + 1
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _compact_profile_description(value: Any) -> str:
    text = " ".join(str(value or "").split())
    if not text:
        return ""
    markers = (
        "fixed supply cap",
        "supply cap",
        "halving",
        "proof of work",
        "decentralized",
    )
    lower = text.lower()
    if not any(marker in lower for marker in markers):
        return text[:360]
    return text[:520]


def _visible_gap_labels(gaps: Sequence[Any]) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for item in gaps:
        raw_reason = getattr(item, "reason", None)
        if raw_reason is None:
            label = "未知缺口"
        else:
            reason = str(getattr(raw_reason, "value", raw_reason))
            label = _gap_reason_label(reason)
        if label not in seen:
            seen.add(label)
            labels.append(label)
    return labels


def _latest_value_summary(*, rows: Sequence[Mapping[str, Any]], dataset: str) -> str:
    records = _latest_value_records(rows=rows, dataset=dataset)
    if not records:
        return ""
    return "；".join(_latest_value_record_text(record) for record in records[:5])


def _latest_value_record_text(record: Mapping[str, Any]) -> str:
    parts: list[str] = []
    label = str(record.get("label") or "").strip()
    value = record.get("value")
    unit_or_scope = str(record.get("unit_or_scope") or "").strip()
    if label and value is not None:
        parts.append(f"{label} {_value_with_unit(value, unit_or_scope or None)}")
    time_text = str(record.get("time") or "").strip()
    if time_text:
        parts.append(f"时间 {time_text}")
    source_text = str(record.get("source") or "").strip()
    if source_text:
        parts.append(f"来源 {_reader_source_label(source_text)}")
    granularity = str(record.get("granularity") or "").strip()
    if granularity:
        parts.append(f"粒度 {granularity}")
    sample_range = record.get("sample_range")
    if isinstance(sample_range, (list, tuple)) and len(sample_range) == 2:
        parts.append(f"样本 {sample_range[0]} 至 {sample_range[1]}")
    return "，".join(parts)


def _latest_value_summaries(data_results: Sequence[DataResult]) -> tuple[dict[str, Any], ...]:
    summaries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for result in data_results:
        if not result.rows:
            continue
        dataset = _result_dataset(result.request_id) or result.request_id.split(":")[-1]
        for record in _latest_value_records(rows=result.rows, dataset=dataset):
            record["request_id"] = result.request_id
            record["dataset"] = dataset
            key = (str(record.get("dataset")), str(record.get("label")), str(record.get("time")))
            if key in seen:
                continue
            seen.add(key)
            summaries.append(record)
    return tuple(summaries)


def _latest_value_records(*, rows: Sequence[Mapping[str, Any]], dataset: str) -> tuple[dict[str, Any], ...]:
    if dataset == "crypto_derivative_metric":
        return _latest_crypto_derivative_value_records(rows=rows)
    records: list[dict[str, Any]] = []
    base = _latest_value_record(rows=rows, dataset=dataset)
    if base:
        _append_latest_value_record(records, base)
    return tuple(records)


def _latest_crypto_derivative_value_records(*, rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    records: list[dict[str, Any]] = []
    seen_labels: set[str] = set()
    sample_range = _row_date_range(rows)
    for row in _recent_rows(rows, limit=len(rows)):
        for value_key, unit_key, label in _CRYPTO_DERIVATIVE_VALUE_FIELDS:
            value = row.get(value_key)
            if value is None:
                continue
            record_label = _long_short_metric_label(row.get("metric")) if value_key == "long_short_ratio" else label
            if not record_label or record_label in seen_labels:
                continue
            seen_labels.add(record_label)
            _append_latest_value_record(
                records,
                {
                    "label": record_label,
                    "value": value,
                    "unit_or_scope": _unit_text(row, unit_key),
                    "time": _latest_time_text(row),
                    "source": _latest_source_text(row),
                    "granularity": _optional_text(row.get("granularity")) or "",
                    "sample_range": sample_range or (),
                },
            )
    return tuple(records)


def _latest_value_record(*, rows: Sequence[Mapping[str, Any]], dataset: str) -> dict[str, Any] | None:
    recent_rows = _recent_rows(rows, limit=1)
    if not recent_rows:
        return None
    return _latest_value_record_from_row(row=recent_rows[0], dataset=dataset, sample_range=_row_date_range(rows))


def _append_latest_value_record(records: list[dict[str, Any]], record: dict[str, Any]) -> None:
    key = (str(record.get("label")), str(record.get("time")), str(record.get("value")))
    if key not in {(str(item.get("label")), str(item.get("time")), str(item.get("value"))) for item in records}:
        records.append(record)


def _latest_value_record_from_row(*, row: Mapping[str, Any], dataset: str, sample_range: tuple[str, str] | None) -> dict[str, Any] | None:
    value = _latest_value_record_value(row=row, dataset=dataset)
    if not value:
        return None
    label, raw_value, unit_or_scope = value
    source_text = _latest_source_text(row)
    if dataset == "macro_series" and not source_text:
        macro_source = str(row.get("unit") or "").strip()
        macro_region = str(row.get("region") or "").strip()
        source_text = "/".join(item for item in (macro_source, macro_region) if item)
    return {
        "label": label,
        "value": raw_value,
        "unit_or_scope": unit_or_scope,
        "time": _latest_time_text(row),
        "source": source_text,
        "granularity": _optional_text(row.get("granularity")) or "",
        "sample_range": sample_range or (),
    }


def _latest_value_text(*, row: Mapping[str, Any], dataset: str) -> str:
    value = _latest_value_record_value(row=row, dataset=dataset)
    if not value:
        return ""
    label, raw_value, unit_or_scope = value
    return f"{label} {_value_with_unit(raw_value, unit_or_scope or None)}"


def _long_short_metric_label(value: Any) -> str:
    metric = str(value or "").strip().lower()
    return {
        "global_account_long_short_ratio": "全局账户多空比",
        "top_account_long_short_ratio": "大户账户多空比",
        "top_position_long_short_ratio": "大户持仓多空比",
        "bitfinex_margin_long_short": "Bitfinex 保证金多空比",
        "hyperliquid_global_long_short_account_ratio": "Hyperliquid 全局账户多空比",
    }.get(metric, "")


_CRYPTO_DERIVATIVE_VALUE_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("funding_rate", "funding_rate_unit", "资金费率"),
    ("open_interest", "open_interest_unit", "OI"),
    ("long_short_ratio", "", "多空比"),
    ("cvd", "taker_volume_unit", "CVD"),
    ("net_inflow", "net_inflow_unit", "交易所净流量"),
    ("netflow", "netflow_unit", "交易所净流量"),
    ("liquidation_value", "liquidation_value_unit", "清算规模"),
    ("long_liquidation", "liquidation_value_unit", "多头清算"),
    ("short_liquidation", "liquidation_value_unit", "空头清算"),
    ("liquidation_price", "liquidation_price_unit", "清算价位"),
    ("liquidation_size", "liquidation_size_unit", "清算规模"),
    ("options_open_interest", "options_open_interest_unit", "期权持仓"),
    ("options_volume", "options_volume_unit", "期权成交"),
    ("etf_flow_usd", "etf_flow_usd_unit", "ETF资金流"),
)


def _latest_value_record_value(*, row: Mapping[str, Any], dataset: str) -> tuple[str, Any, str] | None:
    if dataset == "macro_series":
        value = row.get("value")
        if value is not None:
            series_id = str(row.get("series_id") or "").strip()
            label = f"宏观序列 {series_id}" if series_id else "宏观序列"
            return label, value, _unit_text(row, "unit")
    if dataset == "crypto_derivative_metric":
        for value_key, unit_key, label in _CRYPTO_DERIVATIVE_VALUE_FIELDS:
            value = row.get(value_key)
            if value is not None:
                if value_key == "long_short_ratio":
                    label = _long_short_metric_label(row.get("metric")) or label
                return label, value, _unit_text(row, unit_key)
    if dataset == "crypto_onchain_metric":
        metric = str(row.get("metric") or "").strip()
        for value_key, unit_key, label in (
            ("ahr999", "", "AHR999"),
            ("exchange_balance", "balance_unit", "交易所余额"),
            ("balance", "balance_unit", "交易所余额"),
            ("value", "value_unit", metric or "链上指标"),
            ("metric_value", "value_unit", metric or "链上指标"),
        ):
            value = row.get(value_key)
            if value is not None:
                return label, value, _unit_text(row, unit_key)
    if dataset == "company_profile":
        name = row.get("name")
        rank = row.get("market_cap_rank")
        if name is not None:
            suffix = f"（市值排名 {rank}）" if rank is not None else ""
            return "项目", f"{name}{suffix}", ""
    for value_key, unit_key, label in (
        ("price", "price_unit", "最新价"),
        ("last_price", "price_unit", "最新价"),
        ("close", "price_unit", "收盘价"),
        ("main_net", "amount_unit", "主力净流入"),
        ("net_amount", "amount_unit", "净流入"),
        ("pe", "", "PE"),
        ("pb", "", "PB"),
        ("market_cap", "market_cap_unit", "市值"),
        ("revenue", "currency", "营收"),
        ("net_income", "currency", "净利润"),
    ):
        value = row.get(value_key)
        if value is not None:
            return label, value, _unit_text(row, unit_key)
    title = row.get("title")
    if title is not None:
        return "事件", str(title)[:80], ""
    return None


def _unit_text(row: Mapping[str, Any], unit_key: str) -> str:
    if not unit_key:
        return ""
    value = row.get(unit_key)
    return "" if value is None else str(value)


def _latest_time_text(row: Mapping[str, Any]) -> str:
    for key in ("timestamp", "timestamp_utc", "published_at", "event_date", "date", "period_start", "period"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return str(value)[:19]
    return ""


def _latest_source_text(row: Mapping[str, Any]) -> str:
    for key in ("source", "provider_id", "source_type", "exchange"):
        value = row.get(key)
        if value is not None and str(value).strip():
            return _reader_source_label(str(value).strip())
    return ""


def _reader_source_label(raw: str) -> str:
    return _READER_SOURCE_LABELS.get(raw.strip().lower(), raw.strip())


def _data_result_gaps(data_results: Sequence[DataResult]) -> tuple[DataGap, ...]:
    return tuple(gap for result in data_results for gap in tuple(result.gaps or ()))


def _data_result_has_material_gap(result: DataResult) -> bool:
    ignored = _non_material_gap_reasons_for_result(result)
    return any(_gap_reason(gap) in _MATERIAL_RESULT_GAP_REASONS and _gap_reason(gap) not in ignored for gap in tuple(result.gaps or ()))


def _non_material_gap_reasons_for_result(result: DataResult) -> set[GapReason]:
    dataset = _data_result_dataset(result)
    if dataset in {"macro_series", "crypto_derivative_metric", "crypto_onchain_metric"}:
        return {GapReason.DATE_RANGE_MISSING}
    return set()


def _data_result_dataset(result: DataResult) -> str:
    for row in result.rows:
        if isinstance(row, Mapping) and str(row.get("dataset") or "").strip():
            return str(row.get("dataset")).strip()
    request_id = str(result.request_id or "")
    return request_id.rsplit(":", 1)[-1] if ":" in request_id else ""


def _gap_reason(gap: Any) -> GapReason | None:
    raw_reason = getattr(gap, "reason", None)
    if isinstance(raw_reason, GapReason):
        return raw_reason
    try:
        return GapReason(str(getattr(raw_reason, "value", raw_reason)))
    except ValueError:
        return None


def _normalize_need_instrument(value: str, *, market: Market) -> str:
    text = value.strip().upper()
    if market != Market.CRYPTO:
        return text
    if "/" in text:
        base, _quote = text.split("/", 1)
        return f"{base}/USDT"
    for quote in sorted(CRYPTO_QUOTE_ASSETS, key=len, reverse=True):
        if text.endswith(quote) and len(text) > len(quote):
            return f"{text[:-len(quote)]}/USDT"
    return f"{text}/USDT"


def _crypto_asset_pair(instrument: str) -> tuple[str | None, str | None]:
    if "/" not in instrument:
        return None, None
    base, quote = instrument.split("/", 1)
    return base.strip().upper() or None, quote.strip().upper() or None


def _required_text(value: Any, field_name: str) -> str:
    text = _optional_text(value)
    if text is None:
        raise ValueError(f"{field_name} 不能为空")
    return text


def _optional_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _positive_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        parsed = int(str(value))
    except ValueError:
        return None
    return parsed if parsed > 0 else None


def _safe_identifier(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in "_.-" else "-" for ch in value.strip())
    return text.strip("-")[:96] or "unknown"


def _result_dataset(request_id: str) -> str:
    parts = request_id.split(":")
    if len(parts) < 5:
        return ""
    return parts[4]


def run_report_data_prefetch(request: Any, *, run_id: str, evidence_root: Path) -> dict[str, Any]:
    evidence_path = evidence_root / "data-layer" / "report-prefetch.json"
    payload = {
        "ok": False,
        "schema_version": "report_data_prefetch.v1",
        "run_id": run_id,
        "category": "data_prefetch",
        "reason": "report_prefetch_disabled_use_data_need",
        "request_count": 0,
        "status": "missing",
        "domain_statuses": (),
        "dataset_refs": (),
        "raw_refs": (),
        "attempt_refs": (),
        "data_results": (),
        "evidence_paths": (str(evidence_path),),
    }
    _write_json(evidence_path, payload)
    return payload


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def _write_data_need_result_evidence(*, runtime_context: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[str, ...]:
    path = _data_need_result_evidence_path(runtime_context)
    if path is None:
        return ()
    _write_json(path, _data_need_audit_payload(payload))
    return (str(path),)


def _write_cached_data_need_result_evidence(
    *,
    runtime_context: Mapping[str, Any],
    payload: Mapping[str, Any],
    reused_from: str,
    cache_scope: str,
) -> tuple[str, ...]:
    path = _data_need_result_evidence_path(runtime_context)
    if path is None:
        return ()
    audit_payload = dict(payload)
    audit_payload["cache_reused_from"] = reused_from
    audit_payload["cache_scope"] = cache_scope
    _write_json(path, audit_payload)
    return (str(path),)


def _data_need_result_evidence_path(runtime_context: Mapping[str, Any]) -> Path | None:
    evidence_root_value = str(runtime_context.get("evidence_root") or "").strip()
    if not evidence_root_value:
        return None
    evidence_root = Path(evidence_root_value).expanduser()
    run_id = _safe_path_token(str(runtime_context.get("run_id") or "run"))
    call_id = _safe_path_token(str(runtime_context.get("call_id") or "call"))
    return evidence_root / "data-layer" / "data-need-results" / run_id / call_id / "result.json"


def _data_need_audit_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": "data_need_tool_evidence.v1",
        "ok": payload.get("ok"),
        "status": payload.get("status"),
        "need_satisfied": payload.get("need_satisfied"),
        "tool_input": payload.get("tool_input"),
        "request": payload.get("request"),
        "need": payload.get("need"),
        "planned_calls_count": payload.get("planned_calls_count"),
        "scheduled_calls_count": payload.get("scheduled_calls_count"),
        "data_attempts_summary": payload.get(_DATA_LAYER_ATTEMPT_SUMMARY_KEY) or (),
        "refs": payload.get("refs"),
        "gaps": payload.get("gaps") or (),
        "merge_evidence": payload.get("merge_evidence") or (),
        "rate_limit_evidence": payload.get("rate_limit_evidence") or (),
        "data_results": _compact_data_results(payload.get("data_results")),
        "latest_value_summary": payload.get("latest_value_summary") or (),
        "model_visible_text": payload.get("model_visible_text"),
        "chart_status": payload.get("chart_status"),
        "chart_files": payload.get("chart_files") or (),
        "crypto_lens_status": payload.get("crypto_lens_status"),
        "crypto_lens_evidence_paths": payload.get("crypto_lens_evidence_paths") or (),
    }


def _compact_data_results(value: Any) -> tuple[dict[str, Any], ...]:
    compact: list[dict[str, Any]] = []
    for item in _as_sequence(value):
        if not isinstance(item, Mapping):
            continue
        rows = tuple(row for row in _as_sequence(item.get("rows")) if isinstance(row, Mapping))
        compact.append(
            {
                "request_id": item.get("request_id"),
                "status": item.get("status"),
                "dataset_refs": item.get("dataset_refs") or (),
                "raw_refs": item.get("raw_refs") or (),
                "attempt_refs": item.get("attempt_refs") or (),
                "gaps": item.get("gaps") or (),
                "row_count": len(rows),
                "field_set": _row_fields(rows),
                "sample_rows": _compact_sample_rows(rows),
            }
        )
    return tuple(compact)


def _compact_sample_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    return tuple(_compact_audit_row(row) for row in _recent_rows(rows, limit=10))


def _compact_audit_row(row: Mapping[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, value in row.items():
        key_text = str(key)
        if key_text in _ROW_FIELD_EXCLUDE:
            continue
        compact_value = _compact_audit_value(value)
        if compact_value is None and value is not None:
            continue
        compact[key_text] = compact_value
        if len(compact) >= _AUDIT_ROW_MAX_FIELDS:
            break
    return compact


def _compact_audit_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, str):
        return value if len(value) <= _AUDIT_ROW_MAX_STRING_CHARS else value[: _AUDIT_ROW_MAX_STRING_CHARS - 3].rstrip() + "..."
    if isinstance(value, Mapping):
        compact: dict[str, Any] = {}
        for key, item in value.items():
            compact_item = _compact_audit_value(item)
            if compact_item is not None or item is None:
                compact[str(key)] = compact_item
            if len(compact) >= 12:
                break
        return compact or None
    if isinstance(value, (list, tuple)):
        compact_items = tuple(_compact_audit_value(item) for item in value[:8])
        return tuple(item for item in compact_items if item is not None) or None
    return None


def _normalize_symbol(*, tool_input: Mapping[str, Any], market: Market) -> _SymbolParts:
    ticker = str(tool_input.get("ticker") or "").strip().upper()
    currency = str(tool_input.get("currency") or _MARKET_DEFAULTS[market]["currency"] or "").strip().upper()
    if not ticker:
        raise RuntimeError("ticker is required")

    if market == Market.CN_A:
        if "." not in ticker and len(ticker) == 6 and ticker.isdigit():
            suffix = ".SH" if ticker.startswith("6") else ".SZ"
            ticker = f"{ticker}{suffix}"
        exchange = "SSE" if ticker.endswith(".SH") else "SZSE" if ticker.endswith(".SZ") else "BSE" if ticker.endswith(".BJ") else None
        return _SymbolParts(symbol_id=ticker, exchange=exchange, currency=currency or "CNY")

    if market == Market.HK:
        raw = ticker.removesuffix(".HK")
        if raw.isdigit():
            ticker = f"{raw.zfill(5)}.HK"
        return _SymbolParts(symbol_id=ticker, exchange="XHKG", currency=currency or "HKD")

    if market == Market.US:
        return _SymbolParts(symbol_id=ticker, exchange=_MARKET_DEFAULTS[market]["exchange"], currency=currency or "USD")

    base, quote = _normalize_crypto_pair(ticker=ticker, currency=currency)
    return _SymbolParts(
        symbol_id=f"{base}{quote}",
        exchange=str(_MARKET_DEFAULTS[market]["exchange"]),
        currency=quote,
        base_asset=base,
        quote_asset=quote,
    )


def _normalize_crypto_pair(*, ticker: str, currency: str) -> tuple[str, str]:
    if _has_explicit_crypto_quote(ticker):
        base, _quote = _crypto_pair(ticker=ticker, currency=currency or "USDT")
        mapping = map_crypto_asset_to_symbol(base, quote_asset="USDT")
        return mapping.base_asset, mapping.quote_asset
    mapping = map_crypto_asset_to_symbol(ticker, quote_asset="USDT")
    return mapping.base_asset, mapping.quote_asset


def _has_explicit_crypto_quote(ticker: str) -> bool:
    normalized = ticker.replace("-", "/").replace(".", "/")
    if "/" in normalized:
        left, right = normalized.split("/", 1)
        return bool(left.strip()) and right.strip().upper() in set(CRYPTO_QUOTE_ASSETS)
    return any(ticker.endswith(quote) and len(ticker) > len(quote) for quote in CRYPTO_QUOTE_ASSETS)


def _crypto_pair(*, ticker: str, currency: str) -> tuple[str, str]:
    normalized = ticker.replace("-", "/").replace(".", "/")
    if "/" in normalized:
        base, quote = normalized.split("/", 1)
        base = base.strip().upper()
        quote = quote.strip().upper()
        if base and quote:
            return base, quote
    for quote in CRYPTO_QUOTE_ASSETS:
        if ticker.endswith(quote) and len(ticker) > len(quote):
            return ticker[: -len(quote)], quote
    return ticker, "USDT"


def _crypto_base_asset_from_tool_input(*, tool_input: Mapping[str, Any], market: Market) -> str:
    if market != Market.CRYPTO:
        return ""
    ticker = str(tool_input.get("ticker") or "").strip().upper()
    currency = str(tool_input.get("currency") or "USDT").strip().upper()
    if not ticker:
        return ""
    base_asset, _quote_asset = _normalize_crypto_pair(ticker=ticker, currency=currency or "USDT")
    return base_asset


def _sample_limit_lines(results: Sequence[DataResult], *, market: Market, crypto_base_asset: str = "") -> list[str]:
    lines: list[str] = []
    for result in results:
        if not result.rows:
            continue
        reasons: list[str] = []
        subject_gap: DataGap | None = None
        for gap in result.gaps:
            reason = str(getattr(gap.reason, "value", gap.reason))
            if reason not in _ROW_LEVEL_LIMIT_REASONS:
                continue
            if subject_gap is None:
                subject_gap = gap
            reasons.append(_gap_reason_label(reason))
        if reasons:
            subject = _gap_subject_label(result, gap=subject_gap, market=market)
            if _is_non_btc_crypto_base(crypto_base_asset) and _is_crypto_liquidation_subject(subject):
                continue
            date_range = _row_date_range(result.rows)
            range_text = f"，覆盖 {date_range[0]} 至 {date_range[1]}" if date_range else ""
            reason_text = "、".join(_dedupe(reasons))
            lines.append(
                f"{subject}：已有 {len(result.rows)} 行{range_text}，"
                f"样本或来源限制：{reason_text}；先分析现有样本，把限制作为置信度限制。"
            )
    return list(_dedupe(lines))


def _is_non_btc_crypto_base(base_asset: str) -> bool:
    return bool(base_asset) and base_asset.upper() != "BTC"


def _is_crypto_liquidation_subject(subject: str) -> bool:
    return subject in {"清算热力图", "强平统计", "清算地图"}


def _is_crypto_liquidation_fields(fields: Sequence[str]) -> bool:
    return bool(
        {
            "long_liquidation",
            "short_liquidation",
            "liquidation_value",
            "liquidation_price",
            "liquidation_size",
        }
        & {str(field) for field in fields}
    )


def _domain_timeout_results(results: Sequence[DataResult]) -> bool:
    if not results:
        return False
    for result in results:
        if result.status != DataResultStatus.ERROR:
            return False
        if not any(
            marker in str(gap.human_readable or "")
            for gap in result.gaps
            for marker in ("report_prefetch_domain_timeout",)
        ):
            return False
    return True


def _chart_brief_lines(chart_payload: Mapping[str, Any]) -> list[str]:
    status = str(chart_payload.get("chart_status") or "missing")
    if status == "ready":
        files = [str(item) for item in chart_payload.get("chart_files", ()) if str(item)]
        lines = ["图表资产：已生成。"]
        if files:
            lines.append("图表文件：")
            lines.extend(f"- {item}" for item in files[:4])
        analysis = chart_payload.get("technical_analysis")
        if isinstance(analysis, Mapping):
            summary = analysis.get("summary")
            indicators = analysis.get("indicators")
            if isinstance(summary, Mapping):
                lines.append(
                    "本地技术摘要："
                    f"趋势 {_display_value(summary.get('trend'))}，"
                    f"成交量状态 {_display_value(summary.get('volume_state'))}，"
                    f"支撑 {_display_value(summary.get('support_levels'))}，"
                    f"压力 {_display_value(summary.get('resistance_levels'))}。"
                )
            if isinstance(indicators, Mapping):
                lines.append(f"本地指标快照：{_compact_json(indicators)}")
                lines.extend(_chart_indicator_interpretation_lines(summary if isinstance(summary, Mapping) else {}, indicators))
        return lines
    reason = _human_error(str(chart_payload.get("chart_error") or "market data result did not generate chart assets"))
    return [f"图表资产：缺失，原因：{reason}。"]


def _chart_indicator_interpretation_lines(summary: Mapping[str, Any], indicators: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    latest_close = _numeric_value(summary.get("latest_close"))
    ma = indicators.get("ma")
    if latest_close is not None and isinstance(ma, Mapping):
        ma_positions: list[str] = []
        for key in ("ma5", "ma10", "ma20"):
            value = _numeric_value(ma.get(key))
            if value is None:
                continue
            position = "高于" if latest_close > value else "低于" if latest_close < value else "等于"
            ma_positions.append(f"{position} {key.upper()}({_display_value(value)})")
        if ma_positions:
            lines.append(f"本地均线位置：最新收盘价 {_display_value(latest_close)}，" + "，".join(ma_positions) + "。")
        ma20 = _numeric_value(ma.get("ma20"))
        if ma20 is not None:
            relation = "高于" if latest_close > ma20 else "低于" if latest_close < ma20 else "等于"
            lines.append(
                f"MA20 关系结论：最新收盘价 {_display_value(latest_close)} {relation} "
                f"MA20({_display_value(ma20)})；正文必须按这个关系写。"
            )
    kdj = indicators.get("kdj")
    if isinstance(kdj, Mapping):
        k = _numeric_value(kdj.get("k"))
        d = _numeric_value(kdj.get("d"))
        if k is not None or d is not None:
            lines.append(
                "本地 KD 状态："
                f"K={_display_value(k)}({_oscillator_zone(k)})，"
                f"D={_display_value(d)}({_oscillator_zone(d)})；"
                "超卖阈值为低于20，接近阈值不能写成已经低于阈值。"
            )
    return lines


def _numeric_value(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _indicator_available(indicators: Mapping[str, Any], key: str) -> bool:
    payload = indicators.get(key)
    if isinstance(payload, Mapping):
        return any(value is not None for value in payload.values())
    return payload is not None


def _indicator_text(indicators: Mapping[str, Any], key: str) -> str:
    payload = indicators.get(key)
    return _compact_json(payload) if _indicator_available(indicators, key) else "未取得"


def _oscillator_zone(value: float | None) -> str:
    if value is None:
        return "未提供"
    if value < 20:
        return "超卖"
    if value <= 25:
        return "接近超卖"
    if value > 80:
        return "超买"
    if value >= 75:
        return "接近超买"
    return "中性"


def _crypto_lens_brief_lines(
    crypto_lens_payload: Mapping[str, Any],
    *,
    chart_payload: Mapping[str, Any],
    base_asset: str = "",
    include_context_rows: bool = False,
) -> list[str]:
    if not crypto_lens_payload:
        return ["CryptoLens 指标材料：缺失，原因：市场数据结果没有生成结构化指标分析。"]
    if crypto_lens_payload.get("crypto_lens_status") == "error":
        return [f"CryptoLens 指标材料：缺失，原因：{_human_error(str(crypto_lens_payload.get('crypto_lens_error') or 'analysis failed'))}。"]

    analysis = crypto_lens_payload.get("crypto_lens_analysis")
    if not isinstance(analysis, Mapping):
        return ["CryptoLens 指标材料：缺失，原因：分析结果结构无效。"]
    local_technical_analysis = crypto_lens_payload.get("crypto_lens_local_technical_analysis")
    if not isinstance(local_technical_analysis, Mapping):
        local_technical_analysis = analysis.get("local_technical_analysis")
    if not isinstance(local_technical_analysis, Mapping):
        local_technical_analysis = {}

    lines = [
        f"CryptoLens 指标材料：{_status_zh(str(analysis.get('status') or 'partial'))}。",
        "指标覆盖：",
        "| 模块 | 状态 | 已返回材料 | 对结论的影响 |",
        "|---|---|---|---|",
    ]
    for item in _crypto_lens_coverage_rows(
        analysis,
        chart_payload=chart_payload,
        local_technical_analysis=local_technical_analysis,
        base_asset=base_asset,
        include_context_rows=include_context_rows,
    ):
        lines.append(f"| {item[0]} | {item[1]} | {item[2]} | {item[3]} |")
    return lines


def _crypto_lens_coverage_rows(
    analysis: Mapping[str, Any],
    *,
    chart_payload: Mapping[str, Any],
    local_technical_analysis: Mapping[str, Any] | None = None,
    base_asset: str = "",
    include_context_rows: bool = False,
) -> list[tuple[str, str, str, str]]:
    technical = _analysis_section(analysis, "technical_patterns")
    derivatives = _analysis_section(analysis, "derivatives_context")
    liquidation = _analysis_section(analysis, "liquidation_context")
    onchain = _analysis_section(analysis, "onchain_context")
    ahr999 = _analysis_section(analysis, "ahr999_context")
    technical_evidence = _section_evidence(technical)
    technical_patterns = technical_evidence.get("patterns")
    pattern_evidence = technical_patterns if isinstance(technical_patterns, Mapping) else {}
    derivatives_evidence = _section_evidence(derivatives)
    liquidation_evidence = _section_evidence(liquidation)
    onchain_evidence = _section_evidence(onchain)
    ahr999_evidence = _section_evidence(ahr999)
    chart_indicators = {}
    chart_analysis = chart_payload.get("technical_analysis")
    if isinstance(chart_analysis, Mapping) and isinstance(chart_analysis.get("indicators"), Mapping):
        chart_indicators = dict(chart_analysis["indicators"])
    if isinstance(local_technical_analysis, Mapping) and isinstance(local_technical_analysis.get("indicators"), Mapping):
        for key, value in local_technical_analysis["indicators"].items():
            if not _indicator_available(chart_indicators, str(key)):
                chart_indicators[str(key)] = value

    rows = [
        _coverage_row("布林带", _indicator_available(chart_indicators, "boll"), "technical_analysis.indicators.boll", _indicator_text(chart_indicators, "boll"), "可用于描述当前通道位置；不要编造目标价或回测结论"),
        _coverage_row("维加斯通道", bool(_non_empty_mapping(technical_evidence.get("vegas"))), "technical_patterns.evidence.vegas", _vegas_evidence_text(technical_evidence.get("vegas")), "可用于描述当前趋势结构；不要编造统计结论或回测结论"),
        _coverage_row("双线反转", bool(_non_empty_mapping(technical_evidence.get("double_line_reversal"))), "technical_patterns.evidence.double_line_reversal", _double_line_evidence_text(technical_evidence.get("double_line_reversal")), "可用于描述 EMA20/EMA50 当前结构；不要编造反转统计或回测结论"),
        _coverage_row("AMD/SMC", _pattern_ready(pattern_evidence.get("amd")), "technical_patterns.evidence.patterns.amd", _pattern_evidence_text("amd", pattern_evidence.get("amd")), "输出当前摆动结构和流动性池代理；不包含逐笔订单流确认"),
        _coverage_row("123 突破", _pattern_ready(pattern_evidence.get("rule_123")), "technical_patterns.evidence.patterns.rule_123", _pattern_evidence_text("rule_123", pattern_evidence.get("rule_123")), "输出三点结构状态和突破位；只反映最近摆动结构"),
        _coverage_row("FVG", bool(_non_empty_mapping(technical_evidence.get("fvg"))), "technical_patterns.evidence.fvg", _fvg_evidence_text(technical_evidence.get("fvg")), "可用于描述已返回缺口位置；不要编造回补目标或交易动作"),
        _coverage_row("OB 订单块", _pattern_ready(pattern_evidence.get("order_block")), "technical_patterns.evidence.patterns.order_block", _pattern_evidence_text("order_block", pattern_evidence.get("order_block")), "输出候选订单块区间和结构突破位；不包含成交确认或支撑强度评级"),
        _coverage_row("RSI", _indicator_available(chart_indicators, "rsi"), "technical_analysis.indicators.rsi", _indicator_text(chart_indicators, "rsi"), "可用于描述当前动量读数；不要编造反转统计或交易动作"),
        _coverage_row("MACD", _indicator_available(chart_indicators, "macd"), "technical_analysis.indicators.macd", _indicator_text(chart_indicators, "macd"), "可用于描述当前动能读数；不要编造趋势统计或交易动作"),
        _coverage_row("KD", bool(_non_empty_mapping(technical_evidence.get("kd_9_3_3"))), "technical_patterns.evidence.kd_9_3_3", _kd_evidence_text(technical_evidence.get("kd_9_3_3")), "可用于描述当前摆动读数；不要编造交易动作"),
        _coverage_row("TD 9/13", bool(_non_empty_mapping(technical_evidence.get("td_sequential"))), "technical_patterns.evidence.td_sequential", _td_evidence_text(technical_evidence.get("td_sequential")), "可用于描述当前计数；不要编造衰竭统计或交易动作"),
        _coverage_row("谐波形态", _pattern_ready(pattern_evidence.get("harmonic")), "technical_patterns.evidence.patterns.harmonic", _pattern_evidence_text("harmonic", pattern_evidence.get("harmonic")), "输出最近摆动比例筛选状态和比例值"),
        _coverage_row("交易密集带/成交量分布", _pattern_ready(pattern_evidence.get("volume_profile")), "technical_patterns.evidence.patterns.volume_profile", _pattern_evidence_text("volume_profile", pattern_evidence.get("volume_profile")), "输出 POC 和价值区间；基于 OHLCV 近似分桶"),
    ]
    if not include_context_rows:
        return rows
    return [
        *rows,
        _liquidation_coverage_row(liquidation_evidence, base_asset=base_asset),
        _coverage_row("CVD", derivatives_evidence.get("cvd") is not None, "derivatives_context.evidence.cvd", _value_with_unit(derivatives_evidence.get("cvd"), derivatives_evidence.get("cvd_unit")), "只使用数据源直接返回的 CVD；未返回就写数据缺失"),
        _coverage_row("主动买卖量", derivatives_evidence.get("taker_buy_volume") is not None or derivatives_evidence.get("taker_sell_volume") is not None or derivatives_evidence.get("taker_buy_sell_ratio") is not None, "derivatives_context.evidence.taker_buy_volume / taker_sell_volume", _taker_buy_sell_text(derivatives_evidence), "只使用数据源直接返回的买卖量和比例；不自行计算 CVD 或比例"),
        _coverage_row("资金费率", derivatives_evidence.get("funding") is not None, "derivatives_context.evidence.funding", _value_with_unit(derivatives_evidence.get("funding"), derivatives_evidence.get("funding_unit")), "可用于描述当前费率水平和单位；不要编造历史分位或统计结论"),
        _coverage_row("OI/多空比", derivatives_evidence.get("oi") is not None or derivatives_evidence.get("long_short_ratio") is not None, "derivatives_context.evidence.oi / long_short_ratio", _oi_long_short_text(derivatives_evidence), "可用于描述当前持仓结构；数据结果会明示已知未平仓量单位；不要编造历史分位或统计结论"),
        _coverage_row("宏观", True, "macro_context.evidence", "宏观/新闻由新闻数据结果负责，行情数据结果不重复判断", "不要写成宏观缺失；应回看新闻/宏观数据结果"),
        _coverage_row("链上", bool(_non_empty_mapping(onchain_evidence)), "onchain_context.evidence", _onchain_evidence_text(onchain_evidence), "只保留当前读数；单位或样本不足时不推导流入/流出含义"),
        _ahr999_coverage_row(ahr999_evidence, base_asset=base_asset),
    ]


def _liquidation_coverage_row(evidence: Mapping[str, Any], *, base_asset: str = "") -> tuple[str, str, str, str]:
    if _is_non_btc_crypto_base(base_asset):
        return (
            "清算地图",
            "不适用",
            f"{base_asset.upper()} 不写清算地图；当前清算地图按 BTC 专用口径使用",
            "不作为本标的市场结构或交易结论依据",
        )
    return _coverage_row(
        "清算地图",
        bool(_non_empty_mapping(evidence)),
        "liquidation_context.evidence",
        _liquidation_evidence_text(evidence),
        "只描述数据源返回的热图价格点、规模、单位和样本限制；不能作为方向性价格依据",
    )


def _ahr999_coverage_row(evidence: Mapping[str, Any], *, base_asset: str = "") -> tuple[str, str, str, str]:
    if _is_non_btc_crypto_base(base_asset):
        return (
            "AHR999",
            "不适用",
            f"{base_asset.upper()} 不写 AHR999；该指标按 BTC 估值指数使用",
            "不作为本标的估值或交易结论依据",
        )
    if _non_empty_mapping(evidence):
        return _coverage_row(
            "AHR999",
            True,
            "ahr999_context.evidence",
            _ahr999_evidence_text(evidence),
            "BTC 估值指数读数；不要编造历史分位、定投或抄底结论",
        )
    return (
        "AHR999",
        "由基本面/链上数据结果负责",
        "行情数据结果未取得 AHR999；如基本面数据结果有读数，以基本面数据结果为准",
        "不要写成全局缺失；不要编造历史分位、定投或抄底结论",
    )


def _analysis_section(analysis: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    section = analysis.get(key)
    return section if isinstance(section, Mapping) else {}


def _liquidation_evidence_text(evidence: Mapping[str, Any]) -> str:
    if not _non_empty_mapping(evidence):
        return "未取得"
    parts: list[str] = []
    heatmap_count = evidence.get("heatmap_sample_count")
    invalid_count = evidence.get("invalid_heatmap_sample_count")
    if heatmap_count is not None:
        parts.append(f"清算热力图样本 {_display_value(heatmap_count)}")
        valid_count = _number_delta(heatmap_count, invalid_count)
        if valid_count is not None:
            parts.append(f"清算热力图有效样本 {valid_count:g}")
    heatmap_points = evidence.get("heatmap_points")
    if isinstance(heatmap_points, Sequence) and heatmap_points:
        first = heatmap_points[0]
        if isinstance(first, Mapping):
            price = first.get("price")
            size = first.get("size")
            if price is not None:
                parts.append(f"热图首个价格点 {_value_with_unit(price, first.get('price_unit'))}")
            if size is not None:
                parts.append(f"热图首个规模 {_value_with_unit(size, first.get('size_unit'))}")
    if invalid_count is not None:
        parts.append(f"剔除异常热力图价格点 {_display_value(invalid_count)}")
    return "；".join(parts) or "未取得"


def _oi_long_short_text(evidence: Mapping[str, Any]) -> str:
    parts: list[str] = []
    oi = evidence.get("oi")
    oi_unit = str(evidence.get("oi_unit") or "").strip()
    if oi is not None:
        rendered = _value_with_unit(oi, oi_unit)
        if oi_unit:
            parts.append(f"未平仓量 {rendered}（单位已明示：{oi_unit}）")
        else:
            parts.append(f"未平仓量 {rendered}（单位未确认）")
    long_short_ratio = evidence.get("long_short_ratio")
    if long_short_ratio is not None:
        parts.append(f"多空比 {_display_value(long_short_ratio)}")
    return "；".join(parts) or "未取得"


def _taker_buy_sell_text(evidence: Mapping[str, Any]) -> str:
    parts: list[str] = []
    unit = str(evidence.get("taker_volume_unit") or "").strip()
    buy = evidence.get("taker_buy_volume")
    sell = evidence.get("taker_sell_volume")
    ratio = evidence.get("taker_buy_sell_ratio")
    if buy is not None:
        parts.append(f"主动买量 {_value_with_unit(buy, unit)}")
    if sell is not None:
        parts.append(f"主动卖量 {_value_with_unit(sell, unit)}")
    if ratio is not None:
        parts.append(f"买卖比 {_display_value(ratio)}")
    return "；".join(parts) or "未取得"


def _number_delta(value: object, minus: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    if minus is None:
        return float(value)
    if not isinstance(minus, (int, float)):
        return None
    return float(value) - float(minus)


def _section_evidence(section: Mapping[str, Any]) -> Mapping[str, Any]:
    evidence = section.get("evidence")
    return evidence if isinstance(evidence, Mapping) else {}


def _coverage_row(module: str, has_data: bool, path: str, value: str, impact: str) -> tuple[str, str, str, str]:
    if has_data:
        status = "可分析"
    elif value.startswith("当前统一数据层未提供结构化"):
        status = "未覆盖/未实现"
    else:
        status = "缺失/不可用"
    _ = path
    return (module, status, value, impact)


def _pattern_ready(value: object) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    status = str(value.get("status") or "")
    return not status.startswith("insufficient")


def _reader_status(value: object) -> str:
    raw = str(value or "").strip()
    labels = {
        "ready": "可用",
        "candidate": "有候选",
        "none": "无信号",
        "open": "未回补",
        "filled": "已回补",
        "partially_filled": "部分回补",
        "unknown": "未确认",
        "no_pattern": "未识别到有效结构",
        "no_candidate": "未匹配到有效候选",
        "no_recent_break_of_structure": "近期没有确认的结构突破",
        "no_source_candle": "未找到结构突破前的来源K线",
        "insufficient": "样本不足",
        "insufficient_ohlc": "K线样本不足",
        "insufficient_swings": "摆动点样本不足",
        "insufficient_swing_types": "摆动点类型不足",
        "invalid_pivot_geometry": "转折点结构无效",
        "pending_breakout": "上破待确认",
        "confirmed_breakout": "上破已确认",
        "pending_breakdown": "下破待确认",
        "confirmed_breakdown": "下破已确认",
        "bullish_cross": "向上交叉",
        "bearish_cross": "向下交叉",
        "above_lines": "价格在双线上方",
        "below_lines": "价格在双线下方",
        "between_lines": "价格位于双线之间",
        "above_blue_band": "价格在蓝带上方",
        "below_blue_band": "价格在蓝带下方",
        "inside_blue_band": "价格位于蓝带内",
        "major_trend_bullish": "长期趋势偏多",
        "major_trend_bearish": "长期趋势偏空",
        "bullish": "看涨",
        "bearish": "看跌",
        "bullish_reversal": "看涨反转",
        "bearish_reversal": "看跌反转",
        "bullish_reversal_countdown_13": "TD 看涨反转 13 计数完成",
        "bearish_reversal_countdown_13": "TD 看跌反转 13 计数完成",
        "bullish_reversal_setup_9": "TD 看涨反转 9 计数完成",
        "bearish_reversal_setup_9": "TD 看跌反转 9 计数完成",
        "near_oversold": "接近超卖",
        "oversold": "超卖",
        "near_overbought": "接近超买",
        "overbought": "超买",
        "neutral": "中性",
        "low": "低",
        "medium": "中等",
        "high": "高",
        "higher_high_higher_low": "高点抬高、低点抬高",
        "lower_high_lower_low": "高点下移、低点下移",
        "mixed_range": "区间震荡或结构转换",
        "bullish_structure": "偏多结构",
        "bearish_structure": "偏空结构",
        "range_or_transition": "区间或转换结构",
        "markup": "上行推进阶段",
        "markdown": "下跌推进阶段",
        "accumulation_or_distribution_unconfirmed": "结构阶段未确认",
    }
    if raw in labels:
        return labels[raw]
    if "_" in raw:
        return "未识别状态"
    return raw or "未取得"


def _vegas_evidence_text(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "未取得"
    return (
        f"位置 {_reader_status(value.get('band_position'))}，"
        f"主趋势 {_reader_status(value.get('major_trend'))}，"
        f"EMA144 {_display_value(value.get('ema144'))}，"
        f"EMA169 {_display_value(value.get('ema169'))}，"
        f"距蓝带中线 {_display_value(value.get('distance_to_blue_band_pct'))}%"
    )


def _double_line_evidence_text(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "未取得"
    return (
        f"状态 {_reader_status(value.get('state'))}，"
        f"EMA20 {_display_value(value.get('fast_value'))}，"
        f"EMA50 {_display_value(value.get('slow_value'))}，"
        f"置信度 {_reader_status(value.get('confidence'))}"
    )


def _fvg_evidence_text(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "未取得"
    candidates = value.get("candidates")
    candidate_count = len(candidates) if isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes, bytearray)) else value.get("candidate_count")
    parts = [
        f"候选 {_display_value(candidate_count)} 个",
        f"未回补 {_display_value(value.get('open_count'))} 个",
    ]
    nearest_above = value.get("nearest_above")
    nearest_below = value.get("nearest_below")
    if isinstance(nearest_above, Mapping):
        parts.append(f"最近上方缺口中线 {_display_value(nearest_above.get('mid'))}")
    if isinstance(nearest_below, Mapping):
        parts.append(f"最近下方缺口中线 {_display_value(nearest_below.get('mid'))}")
    return "，".join(parts)


def _kd_evidence_text(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "未取得"
    return (
        f"K {_display_value(value.get('k'))}（{_reader_status(value.get('k_state'))}），"
        f"D {_display_value(value.get('d'))}（{_reader_status(value.get('d_state'))}），"
        f"J {_display_value(value.get('j'))}"
    )


def _td_evidence_text(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "未取得"
    return (
        f"方向 {_reader_status(value.get('setup_direction'))}，"
        f"启动计数 {_display_value(value.get('setup_count'))}，"
        f"倒数计数 {_display_value(value.get('countdown_count'))}，"
        f"信号 {_reader_status(value.get('signal'))}，"
        f"置信度 {_reader_status(value.get('confidence'))}"
    )


def _onchain_evidence_text(evidence: Mapping[str, Any]) -> str:
    if not _non_empty_mapping(evidence):
        return "未取得"
    parts: list[str] = []
    if evidence.get("exchange_netflow") is not None:
        parts.append(f"交易所净流量 {_value_with_unit(evidence.get('exchange_netflow'), evidence.get('exchange_netflow_unit'))}")
    if evidence.get("exchange_balance") is not None:
        parts.append(f"交易所余额 {_value_with_unit(evidence.get('exchange_balance'), evidence.get('exchange_balance_unit'))}")
    if evidence.get("active_addresses") is not None:
        parts.append(f"活跃地址 {_value_with_unit(evidence.get('active_addresses'), evidence.get('active_addresses_unit'))}")
    if evidence.get("stablecoin_exchange_netflow") is not None:
        parts.append(
            f"稳定币交易所净流量 {_value_with_unit(evidence.get('stablecoin_exchange_netflow'), evidence.get('stablecoin_exchange_netflow_unit'))}"
        )
    if evidence.get("stablecoin_market_cap") is not None:
        parts.append(f"稳定币市值 {_value_with_unit(evidence.get('stablecoin_market_cap'), evidence.get('stablecoin_market_cap_unit'))}")
    return "；".join(parts) or "已返回链上读数"


def _ahr999_evidence_text(evidence: Mapping[str, Any]) -> str:
    if not _non_empty_mapping(evidence):
        return "未取得"
    value = evidence.get("ahr999") or evidence.get("value")
    if value is None:
        return "已返回 AHR999 材料，但当前读数未取得"
    return f"AHR999 当前读数 {_display_value(value)}（无量纲/指数读数）"


def _pattern_evidence_text(kind: str, value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "未取得"
    status = str(value.get("status") or "unknown")
    if kind == "volume_profile":
        if status != "ready":
            return f"状态 {_reader_status(status)}，样本 {_display_value(value.get('sample_count'))}"
        return (
            f"POC {_display_value(value.get('poc'))}，"
            f"价值区间 {_display_value(value.get('value_area_low'))}-{_display_value(value.get('value_area_high'))}，"
            f"样本 {_display_value(value.get('sample_count'))}"
        )
    if kind == "order_block":
        if status != "candidate":
            return f"状态 {_reader_status(status)}"
        return (
            f"状态 {_reader_status(status)}，"
            f"方向 {_reader_status(value.get('direction'))}，"
            f"候选区间 {_display_value(value.get('zone_low'))}-{_display_value(value.get('zone_high'))}，"
            f"结构突破位 {_display_value(value.get('bos_level'))}"
        )
    if kind == "rule_123":
        if status in {"no_pattern", "insufficient_swings"}:
            return f"状态 {_reader_status(status)}，摆动点数量 {_display_value(value.get('swing_count'))}"
        return (
            f"状态 {_reader_status(status)}，"
            f"方向 {_reader_status(value.get('direction'))}，"
            f"突破位 {_display_value(value.get('breakout_level'))}"
        )
    if kind == "amd":
        if status != "ready":
            return f"状态 {_reader_status(status)}，摆动点数量 {_display_value(value.get('swing_count'))}"
        return (
            f"结构 {_reader_status(value.get('structure'))}，"
            f"阶段 {_reader_status(value.get('phase_proxy'))}，"
            f"买侧流动性 {_pivot_price_text(value.get('buy_side_liquidity'))}，"
            f"卖侧流动性 {_pivot_price_text(value.get('sell_side_liquidity'))}"
        )
    if kind == "harmonic":
        ratios = value.get("ratios") if isinstance(value.get("ratios"), Mapping) else {}
        ratio_text = (
            f"AB/XA {_display_value(ratios.get('ab_xa'))}，"
            f"BC/AB {_display_value(ratios.get('bc_ab'))}，"
            f"CD/BC {_display_value(ratios.get('cd_bc'))}"
            if ratios
            else "比例未取得"
        )
        return f"状态 {_reader_status(status)}，形态 {_reader_status(value.get('pattern'))}，方向 {_reader_status(value.get('direction'))}，{ratio_text}"
    return _compact_json(value)


def _pivot_price_text(value: object) -> str:
    if not isinstance(value, Mapping):
        return "未取得"
    return f"{_display_value(value.get('price'))}@{_display_value(value.get('time'))}"


def _non_empty_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) and any(nested is not None for nested in value.values()) else {}


def _has_nested(value: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    current: object = value
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return False
        current = current[key]
    return current is not None


def _crypto_lens_payload(
    *,
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
    results: Sequence[DataResult],
) -> dict[str, Any]:
    try:
        ticker = _chart_ticker(tool_input=tool_input, rows=[row for result in results for row in result.rows])
        _base, quote = _crypto_pair(ticker=str(tool_input.get("ticker") or ticker), currency=str(tool_input.get("currency") or "USDT"))
        local_indicator_payload = _market_indicator_payload(tool_input=tool_input, results=results)
        analysis = analyze_crypto_lens_report_data_results(
            results,
            ticker=ticker,
            quote=quote,
            run_id=str(runtime_context.get("run_id") or "run"),
            call_id=str(runtime_context.get("call_id") or "call"),
            as_of=_resolve_as_of(tool_input.get("current_date")).isoformat(),
            start_date=str(tool_input.get("start_date") or ""),
            end_date=str(tool_input.get("end_date") or ""),
        )
        payload = analysis.as_dict()
        local_technical_analysis = local_indicator_payload.get("technical_analysis")
        if isinstance(local_technical_analysis, Mapping):
            payload["local_technical_analysis"] = local_technical_analysis
        evidence_paths = _write_crypto_lens_evidence(runtime_context=runtime_context, payload=payload)
        result = {
            "crypto_lens_status": analysis.status,
            "crypto_lens_analysis": payload,
            "crypto_lens_evidence_paths": evidence_paths,
        }
        if isinstance(local_technical_analysis, Mapping):
            result["crypto_lens_local_technical_analysis"] = local_technical_analysis
        return result
    except Exception as exc:  # noqa: BLE001
        return {"crypto_lens_status": "error", "crypto_lens_error": str(exc)}


def _write_crypto_lens_evidence(*, runtime_context: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[str, ...]:
    evidence_root_value = str(runtime_context.get("evidence_root") or "").strip()
    if not evidence_root_value:
        return ()
    evidence_root = Path(evidence_root_value).expanduser()
    run_id = _safe_path_token(str(runtime_context.get("run_id") or "run"))
    call_id = _safe_path_token(str(runtime_context.get("call_id") or "call"))
    path = evidence_root / "data-layer" / "crypto-lens" / run_id / call_id / "analysis.json"
    _write_json(path, payload)
    return (str(path),)


def _market_chart_payload(
    *,
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
    results: Sequence[DataResult],
) -> dict[str, Any]:
    candidate_results = _chart_daily_bar_candidates(results)
    if not candidate_results:
        return {"chart_status": "missing", "chart_error": "no usable OHLCV rows"}

    try:
        import pandas as pd
    except ModuleNotFoundError:
        return {"chart_status": "missing", "chart_error": "chart_dependency_missing:pandas"}

    last_error = "no usable OHLCV rows"
    for result in candidate_results:
        rows = [dict(row) for row in result.rows if isinstance(row, Mapping)]
        frame = pd.DataFrame(rows)
        if "date" not in frame.columns and "period_start" in frame.columns:
            frame["date"] = frame["period_start"]
        required = {"date", "open", "high", "low", "close", "volume"}
        missing = sorted(required - set(frame.columns))
        if missing:
            last_error = f"ohlcv_fields_missing:{','.join(missing)}"
            continue

        try:
            ticker = _chart_ticker(tool_input=tool_input, rows=rows)
            bundle = market_indicators.analyze_market_frame(frame, ticker=ticker)
            chart_dir = _chart_output_dir(runtime_context)
            chart_paths = market_charts.render_market_charts(bundle.chart_frame, ticker=ticker, output_dir=chart_dir)
        except Exception as exc:
            last_error = str(exc)
            continue

        return {
            "chart_status": "ready",
            "chart_files": tuple(str(path) for path in chart_paths),
            "technical_analysis": {
                "summary": bundle.summary,
                "indicators": bundle.indicators,
                "warnings": bundle.warnings,
            },
        }

    return {"chart_status": "missing", "chart_error": last_error}


def _market_indicator_payload(
    *,
    tool_input: Mapping[str, Any],
    results: Sequence[DataResult],
) -> dict[str, Any]:
    candidate_results = _chart_daily_bar_candidates(results)
    if not candidate_results:
        return {}

    try:
        import pandas as pd
    except ModuleNotFoundError:
        return {}

    for result in candidate_results:
        rows = [dict(row) for row in result.rows if isinstance(row, Mapping)]
        frame = pd.DataFrame(rows)
        if "date" not in frame.columns and "period_start" in frame.columns:
            frame["date"] = frame["period_start"]
        if {"date", "open", "high", "low", "close", "volume"} - set(frame.columns):
            continue
        try:
            ticker = _chart_ticker(tool_input=tool_input, rows=rows)
            bundle = market_indicators.analyze_market_frame(frame, ticker=ticker)
        except Exception:
            continue
        return {
            "technical_analysis": {
                "summary": bundle.summary,
                "indicators": bundle.indicators,
                "warnings": bundle.warnings,
            }
        }
    return {}


def _should_build_market_chart(*, need_domain: str, data_results: Sequence[DataResult]) -> bool:
    return need_domain == "market" and bool(_chart_daily_bar_candidates(data_results))


def _should_build_crypto_lens(*, market: Market, data_results: Sequence[DataResult]) -> bool:
    return market == Market.CRYPTO and bool(_chart_daily_bar_candidates(data_results))


def _chart_daily_bar_candidates(results: Sequence[DataResult]) -> tuple[DataResult, ...]:
    candidates = tuple(result for result in results if result.request_id.endswith(":daily_bar") and result.rows)
    return tuple(sorted(candidates, key=_chart_daily_bar_sort_key, reverse=True))


def _chart_daily_bar_sort_key(result: DataResult) -> tuple[int, int, date, int]:
    rows = tuple(row for row in result.rows if isinstance(row, Mapping))
    latest = max((_chart_row_date(row) for row in rows), default=None)
    return (
        0 if _data_result_has_material_gap(result) else 1,
        1 if result.status == DataResultStatus.READY else 0,
        latest or date.min,
        len(rows),
    )


def _chart_row_date(row: Mapping[str, Any]) -> date | None:
    value = row.get("date") or row.get("period_start") or row.get("timestamp") or row.get("open_time")
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
        except ValueError:
            try:
                return date.fromisoformat(text[:10])
            except ValueError:
                return None
    return None


def _chart_output_dir(runtime_context: Mapping[str, Any]) -> Path:
    evidence_root = Path(str(runtime_context.get("evidence_root") or "")).expanduser()
    if not str(evidence_root):
        raise RuntimeError("chart_evidence_root_missing")
    run_id = _safe_path_token(str(runtime_context.get("run_id") or "run"))
    call_id = _safe_path_token(str(runtime_context.get("call_id") or "call"))
    return evidence_root / "techlab" / "charts-local" / "runs" / run_id / call_id / "charts"


def _chart_ticker(*, tool_input: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    for row in reversed(rows):
        value = row.get("symbol_id")
        if value:
            return _safe_path_token(str(value))
    return _safe_path_token(str(tool_input.get("ticker") or "instrument"))


def _crypto_base_asset_from_results(
    *,
    results: Sequence[DataResult],
    tool_input: Mapping[str, Any],
    market: Market,
) -> str:
    if market != Market.CRYPTO:
        return ""
    rows = [row for result in results for row in result.rows if isinstance(row, Mapping)]
    for row in reversed(rows):
        value = row.get("base_asset")
        if value:
            return str(value).strip().upper()
    ticker = str(tool_input.get("ticker") or tool_input.get("instrument") or "").strip().upper()
    if not ticker and rows:
        ticker = str(rows[-1].get("symbol_id") or "").strip().upper()
    if not ticker:
        return ""
    try:
        base, _quote = _normalize_crypto_pair(ticker=ticker, currency=str(tool_input.get("currency") or "USDT"))
    except Exception:  # noqa: BLE001
        return ""
    return base


def _safe_path_token(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in value.strip())
    return safe.strip("-._") or "instrument"


def _compact_json(value: object) -> str:
    import json

    text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return text if len(text) <= 600 else text[:597].rstrip() + "..."


def _gap_lines(results: Sequence[DataResult], *, market: Market, crypto_base_asset: str = "") -> list[str]:
    lines: list[str] = []
    for result in results:
        for gap in result.gaps:
            dataset = _gap_subject_label(result, gap=gap, market=market)
            reason = str(getattr(gap.reason, "value", gap.reason))
            if result.rows and reason in _ROW_LEVEL_LIMIT_REASONS:
                continue
            if _is_non_btc_crypto_base(crypto_base_asset) and _is_crypto_liquidation_subject(dataset):
                continue
            required = "、".join(_field_label(field) for field in gap.required_fields)
            detail = f"{dataset}：{_gap_reason_label(reason)}"
            if required:
                detail = f"{detail}，缺少 {required}"
            human = _human_error(gap.human_readable)
            if human and human != _gap_reason_label(reason):
                detail = f"{detail}，说明：{human}"
            lines.append(detail)
    return list(_dedupe(lines))


def _gap_subject_label(result: DataResult, *, market: Market, gap: DataGap | None = None) -> str:
    dataset = _result_dataset(result.request_id) or result.request_id.split(":")[-1]
    default = _dataset_label(dataset, market=market)
    if market != Market.CRYPTO:
        return default

    data_type = str(gap.data_type if gap is not None else dataset)
    fields = _gap_context_fields(result, gap=gap)
    granularity = str(gap.granularity if gap is not None else "")
    if data_type == "crypto_derivative_metric":
        if {"options_open_interest", "options_volume"} & fields:
            return "期权未平仓量/成交量"
        if "cvd" in fields:
            return "标准 CVD"
        if {"liquidation_price", "liquidation_size"} & fields:
            return "清算热力图"
        if {"long_liquidation", "short_liquidation", "liquidation_value"} & fields:
            return "强平统计"
        if {"taker_buy_volume", "taker_sell_volume", "taker_buy_sell_ratio"} & fields:
            return "主动买卖量"
        if "long_short_ratio" in fields:
            return "多空比"
        if "funding_rate" in fields:
            return "资金费率"
        if "open_interest" in fields:
            return "未平仓量"
        if "net_inflow" in fields:
            return "交易所净流入"
        if "etf_flow_usd" in fields:
            return "ETF 资金流"
    if data_type == "order_book_snapshot" and granularity == "1h":
        return "订单簿聚合深度"
    if data_type == "crypto_onchain_metric":
        row_metrics = {str(row.get("metric") or "") for row in result.rows}
        if "ahr999" in row_metrics:
            return "AHR999/链上日频指标"
        if "whale_transfer" in row_metrics:
            return "大额转账/链上事件"
        if "spot_coin_netflow" in row_metrics:
            return "交易所余额/链上净流入"
        if granularity == "event":
            return "链上事件指标"
        if granularity == "realtime":
            return "链上实时指标"
        return "链上日频指标"
    return default


def _gap_context_fields(result: DataResult, *, gap: DataGap | None = None) -> set[str]:
    fields = set(gap.required_fields if gap is not None else ())
    fields.update(_result_spec_fields(result))
    for row in result.rows:
        fields.update(str(key) for key in row)
    return fields


def _result_spec_fields(result: DataResult) -> tuple[str, ...]:
    return ()


def _row_fields(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields and key not in _ROW_FIELD_EXCLUDE:
                fields.append(str(key))
    return tuple(_field_label(field) for field in fields[:12])


def _row_block_identity(rows: Sequence[Mapping[str, Any]]) -> str:
    metrics = sorted({str(row.get("metric") or "").strip() for row in rows if str(row.get("metric") or "").strip()})
    if metrics:
        return "metric=" + ",".join(metrics[:12])
    sources = sorted({str(row.get("source") or row.get("exchange") or "").strip() for row in rows if str(row.get("source") or row.get("exchange") or "").strip()})
    if sources:
        return "source=" + ",".join(sources[:12])
    return ""


def _recent_rows(rows: Sequence[Mapping[str, Any]], *, limit: int = 5) -> tuple[Mapping[str, Any], ...]:
    dated_rows = [(sort_text, row) for row in rows if (sort_text := _row_date_sort_text(row))]
    if not dated_rows:
        return tuple(rows[-limit:])
    return tuple(row for _, row in sorted(dated_rows, key=lambda item: item[0], reverse=True)[:limit])


def _row_date_range(rows: Sequence[Mapping[str, Any]]) -> tuple[str, str] | None:
    dates = [_row_date_sort_text(row) for row in rows]
    dates = [item for item in dates if item]
    if not dates:
        return None
    return (min(dates)[:10], max(dates)[:10])


def _row_date_sort_text(row: Mapping[str, Any]) -> str:
    for key in ("timestamp", "published_at", "period_start", "date", "event_date", "period"):
        value = row.get(key)
        if value is not None and str(value).strip():
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, date):
                return value.isoformat()
            return str(value).strip()
    return ""


def _row_summary(row: Mapping[str, Any], *, domain: str, dataset: str = "") -> str:
    date_value = row.get("date") or row.get("period_start") or row.get("published_at") or row.get("timestamp")
    parts: list[str] = []
    if date_value:
        parts.append(f"时间 {str(date_value)[:19]}")
    if dataset == "company_profile":
        for key in ("name", "symbol", "market_cap_rank", "homepage"):
            value = row.get(key)
            if value is not None and str(value).strip():
                parts.append(f"{_field_label(key)} {str(value).strip()[:120]}")
        for value_key in ("circulating_supply", "total_supply", "max_supply"):
            value = row.get(value_key)
            if value is not None:
                parts.append(f"{_field_label(value_key)} {_value_with_unit(value, row.get('supply_unit'))}")
        description = _compact_profile_description(row.get("description"))
        if description:
            parts.append(f"项目说明 {description}")
        return "，".join(parts[:10])
    if dataset == "financial_metric":
        for key in ("roe", "roa", "gross_margin", "debt_ratio", "eps", "revenue", "net_income"):
            value = row.get(key)
            if value is not None:
                parts.append(f"{_field_label(key)} {value}")
        return "，".join(parts[:8])
    if any(key in row for key in ("revenue", "net_income", "assets", "liabilities", "cash_flow")):
        for key in ("revenue", "net_income", "assets", "liabilities", "cash_flow"):
            value = row.get(key)
            if value is not None:
                parts.append(f"{_field_label(key)} {value}")
        basis_note = _financial_statement_basis_note(row)
        if basis_note:
            parts.append(basis_note)
        return "，".join(parts[:8])
    if any(key in row for key in ("pe", "pb", "ps")):
        for key in ("pe", "pb", "ps", "market_cap", "market_cap_unit", "price"):
            value = row.get(key)
            if value is not None:
                parts.append(f"{_field_label(key)} {value}")
        return "，".join(parts[:8])
    if dataset == "valuation_metric" and any(
        key in row for key in ("price", "market_cap", "fdv", "circulating_supply", "total_supply", "max_supply", "volume")
    ):
        for value_key, unit_key in (
            ("price", "price_unit"),
            ("market_cap", "market_cap_unit"),
            ("fdv", "fdv_unit"),
            ("circulating_supply", "supply_unit"),
            ("total_supply", "supply_unit"),
            ("max_supply", "supply_unit"),
            ("volume", "volume_unit"),
        ):
            value = row.get(value_key)
            if value is not None:
                parts.append(f"{_field_label(value_key)} {_value_with_unit(value, row.get(unit_key))}")
        return "，".join(parts[:8])
    if dataset == "crypto_derivative_metric":
        metric_label = _long_short_metric_label(row.get("metric"))
        if metric_label:
            parts.append(f"口径 {metric_label}")
        for value_key, unit_key in (
            ("open_interest", "open_interest_unit"),
            ("funding_rate", "funding_rate_unit"),
            ("long_short_ratio", ""),
            ("cvd", "taker_volume_unit"),
            ("taker_buy_volume", "taker_volume_unit"),
            ("taker_sell_volume", "taker_volume_unit"),
            ("taker_buy_sell_ratio", ""),
            ("long_liquidation", "liquidation_value_unit"),
            ("short_liquidation", "liquidation_value_unit"),
            ("liquidation_value", "liquidation_value_unit"),
            ("liquidation_price", "liquidation_price_unit"),
            ("liquidation_size", "liquidation_size_unit"),
            ("net_inflow", "net_inflow_unit"),
            ("options_open_interest", "options_open_interest_unit"),
            ("options_volume", "options_volume_unit"),
            ("etf_flow_usd", "price_unit"),
        ):
            value = row.get(value_key)
            if value is not None:
                parts.append(f"{_field_label(value_key)} {_value_with_unit(value, row.get(unit_key) if unit_key else None)}")
        side = row.get("side")
        if side is not None:
            parts.append(f"{_field_label('side')} {side}")
        return "，".join(parts[:8])
    if dataset == "order_book_snapshot":
        for value_key, unit_key in (
            ("bid_price", "price_unit"),
            ("bid_size", "size_unit"),
            ("ask_price", "price_unit"),
            ("ask_size", "size_unit"),
            ("bid_levels", ""),
            ("ask_levels", ""),
            ("bids_quantity", ""),
            ("asks_quantity", ""),
            ("bids_usd", "amount_unit"),
            ("asks_usd", "amount_unit"),
        ):
            value = row.get(value_key)
            if value is not None:
                parts.append(f"{_field_label(value_key)} {_value_with_unit(value, row.get(unit_key) if unit_key else None)}")
        source = row.get("source") or row.get("exchange")
        if source is not None:
            parts.append(f"来源 {_reader_source_label(str(source))}")
        return "，".join(parts[:8])
    if dataset == "crypto_onchain_metric" and any(key in row for key in ("metric", "value", "chain")):
        metric = row.get("metric")
        value = row.get("value")
        chain = row.get("chain")
        if metric is not None:
            parts.append(f"{_field_label('metric')} {metric}")
        if value is not None:
            parts.append(f"{_field_label('value')} {_value_with_unit(value, row.get('value_unit'))}")
        if chain is not None:
            parts.append(f"{_field_label('chain')} {chain}")
        return "，".join(parts[:8])
    if dataset == "macro_series":
        for key in ("series_id", "region", "unit"):
            value = row.get(key)
            if value is not None:
                parts.append(f"{_field_label(key)} {value}")
        value = row.get("value")
        if value is not None:
            parts.append(f"{_field_label('value')} {_value_with_unit(value, row.get('unit'))}")
        return "，".join(parts[:8])
    if dataset == "event_calendar":
        source = row.get("source")
        title = row.get("title")
        summary = row.get("summary")
        parts.append("事件材料已返回")
        if source is not None:
            parts.append(f"来源 {_reader_source_label(str(source))}")
        if title is not None:
            parts.append(f"事件 {str(title)[:140]}")
        if summary is not None and str(summary).strip():
            parts.append(f"摘要 {str(summary).strip()[:160]}")
        return "，".join(parts[:8])
    if domain == "news":
        source = row.get("source")
        title = row.get("title")
        summary = row.get("summary")
        source_roles = tuple(str(item) for item in row.get("source_roles") or ())
        quality_flags = tuple(str(item) for item in row.get("quality_flags") or ())
        is_official_filing = dataset == "official_filing" or str(source or "").strip().lower() in {"cninfo", "巨潮资讯"}
        discovery_line = (
            "discovery" in source_roles
            or "discovery_not_formal_fact_source" in quality_flags
            or str(source).strip().lower() == "google news"
        )
        parts.append("官方公告材料已返回" if is_official_filing else "媒体/搜索线索已返回")
        if source is not None:
            source_label = "媒体聚合搜索线索" if discovery_line else _reader_source_label(str(source))
            parts.append(f"来源 {source_label}")
        if dataset == "event_calendar" and title is not None:
            parts.append(f"事件 {str(title)[:140]}")
        elif title is not None and (is_official_filing or not discovery_line):
            parts.append(f"标题 {title}")
        if dataset == "event_calendar" and summary is not None:
            parts.append(f"摘要 {str(summary)[:160]}")
        elif summary is not None and not discovery_line:
            parts.append(f"摘要 {str(summary)[:160]}")
        if is_official_filing and row.get("body_ref"):
            parts.append("正文索引已返回")
        if discovery_line:
            parts.append("公开搜索线索，不能单独作为正式事实源")
        if "symbol_relevance_low" in quality_flags:
            parts.append("疑似非标的结果")
        return "，".join(parts[:6])
    if domain == "social":
        source = row.get("source")
        title = row.get("title")
        parts.append("舆情/互动线索已返回")
        if source is not None:
            parts.append(f"来源 {_reader_source_label(str(source))}")
        metrics = row.get("metrics")
        if isinstance(metrics, Mapping):
            for key, value in metrics.items():
                if value is None or isinstance(value, (Mapping, list, tuple)):
                    continue
                parts.append(f"{_field_label(str(key))} {str(value)[:120]}")
                if len(parts) >= 7:
                    break
        for key in ("score", "sentiment", "keyword", "topic", "reason", "question", "answer", "name"):
            value = row.get(key)
            if value is not None:
                rendered = str(value)
                parts.append(f"{_field_label(key)} {rendered[:120]}")
        if title is not None:
            parts.append(f"标题 {title}")
        return "，".join(parts[:8])
    for value_key, unit_key in (
        ("open", ""),
        ("high", ""),
        ("low", ""),
        ("close", ""),
        ("price", ""),
        ("change_pct", ""),
        ("volume", "volume_unit"),
        ("amount", "amount_unit"),
        ("market_cap", "market_cap_unit"),
        ("float_market_cap", ""),
        ("fdv", "fdv_unit"),
        ("circulating_supply", "supply_unit"),
        ("total_supply", "supply_unit"),
        ("max_supply", "supply_unit"),
        ("tvl", ""),
        ("revenue", ""),
        ("revenue_basis", ""),
        ("net_income", ""),
        ("net_income_basis", ""),
        ("assets", ""),
        ("assets_basis", ""),
        ("liabilities", ""),
        ("liabilities_basis", ""),
        ("cash_flow", ""),
        ("cash_flow_basis", ""),
        ("roe", ""),
        ("roa", ""),
        ("gross_margin", ""),
        ("debt_ratio", ""),
        ("eps", ""),
        ("pe", ""),
        ("pb", ""),
        ("ps", ""),
        ("main_net", ""),
        ("small_net", ""),
        ("mid_net", ""),
        ("large_net", ""),
        ("super_net", ""),
        ("hgt_net", "amount_unit"),
        ("sgt_net", "amount_unit"),
        ("northbound_net", "amount_unit"),
        ("financing_balance", ""),
        ("margin_balance", ""),
        ("security_lending_volume", ""),
        ("security_lending_balance", ""),
        ("financing_buy_amount", ""),
        ("financing_repayment_amount", ""),
        ("sector_name", ""),
        ("turnover_rate", ""),
        ("large_order_net", ""),
        ("open_interest", "open_interest_unit"),
        ("funding_rate", "funding_rate_unit"),
        ("long_short_ratio", ""),
        ("long_liquidation", "liquidation_value_unit"),
        ("short_liquidation", "liquidation_value_unit"),
        ("liquidation_value", "liquidation_value_unit"),
        ("net_inflow", "net_inflow_unit"),
        ("metric", ""),
        ("value", "value_unit"),
        ("chain", ""),
        ("source", ""),
        ("sentiment", ""),
        ("score", ""),
    ):
        value = row.get(value_key)
        if value is not None:
            unit = row.get(unit_key) if unit_key else None
            rendered = _value_with_unit(value, unit) if unit is not None else value
            parts.append(f"{_field_label(value_key)} {rendered}")
    return "，".join(parts[:8])


def _financial_statement_basis_note(row: Mapping[str, Any]) -> str:
    basis = {
        str(row.get("revenue_basis") or ""),
        str(row.get("net_income_basis") or ""),
        str(row.get("cash_flow_basis") or ""),
    }
    point = {str(row.get("assets_basis") or ""), str(row.get("liabilities_basis") or "")}
    if "period_cumulative" in basis and "period_end_point_in_time" in point:
        return "口径：收入/净利润/现金流为报告期累计流量，资产/负债为期末时点值"
    return ""


def _dataset_label(value: str, *, market: Market | None = None) -> str:
    if market == Market.CRYPTO:
        crypto_labels = {
            "daily_bar": "日线行情",
            "intraday_bar": "小时行情",
            "quote_snapshot": "实时行情快照",
            "order_book_snapshot": "盘口快照",
            "valuation_metric": "币种市值与供应数据",
            "defi_metric": "DeFi 经营数据",
            "crypto_derivative_metric": "衍生品数据",
            "crypto_onchain_metric": "链上数据",
            "company_news": "项目新闻",
            "macro_news": "宏观新闻",
            "social_signal": "舆情信号",
            "event_calendar": "事件日历",
        }
        if value in crypto_labels:
            return crypto_labels[value]
    return {
        "daily_bar": "日线行情",
        "intraday_bar": "日内分时",
        "quote_snapshot": "实时行情快照",
        "order_book_snapshot": "盘口快照",
        "capital_flow": "资金流",
        "northbound_flow": "北向资金",
        "margin_trading": "融资融券",
        "sector_snapshot": "行业/板块资金快照",
        "financial_statement": "财务报表",
        "financial_metric": "财务指标",
        "valuation_metric": "估值指标",
        "company_news": "公司新闻",
        "macro_news": "宏观新闻",
        "social_signal": "舆情信号",
        "event_calendar": "事件日历",
        "hot_money_event": "龙虎榜/资金事件",
        "lockup_event": "解禁事件",
    }.get(value, value.replace("_", " "))


def _field_label(value: str) -> str:
    return {
        "date": "日期",
        "period_start": "开始日期",
        "period_end": "结束日期",
        "published_at": "发布时间",
        "timestamp": "时间戳",
        "open": "开盘价",
        "high": "最高价",
        "low": "最低价",
        "close": "收盘价",
        "volume": "成交量",
        "title": "标题",
        "source": "来源",
        "summary": "摘要",
        "url": "链接",
        "sentiment": "情绪",
        "score": "分数",
        "mentions": "提及量",
        "price": "价格",
        "price_unit": "价格单位",
        "change": "涨跌额",
        "change_pct": "涨跌幅",
        "amount": "成交额",
        "amount_unit": "金额单位",
        "fdv": "完全稀释估值",
        "fdv_unit": "完全稀释估值单位",
        "circulating_supply": "流通供应量",
        "total_supply": "总供应量",
        "max_supply": "最大供应量",
        "supply_unit": "供应量单位",
        "tvl": "总锁仓量",
        "volume_unit": "成交量单位",
        "chains": "链",
        "category": "类别",
        "open_interest": "未平仓量",
        "funding_rate": "资金费率",
        "long_short_ratio": "多空比",
        "long_liquidation": "多头清算额",
        "short_liquidation": "空头清算额",
        "liquidation_value": "清算总额",
        "liquidation_price": "清算热图价格",
        "liquidation_size": "清算热图规模",
        "liquidation_price_unit": "清算热图价格单位",
        "liquidation_size_unit": "清算热图规模单位",
        "liquidation_value_unit": "清算金额单位",
        "net_inflow": "净流入",
        "net_inflow_unit": "净流入单位",
        "open_interest_unit": "未平仓量单位",
        "funding_rate_unit": "资金费率单位",
        "taker_volume_unit": "主动买卖量单位",
        "options_open_interest": "期权未平仓量",
        "options_volume": "期权成交量",
        "cvd": "CVD",
        "side": "方向",
        "metric": "指标",
        "series_id": "序列",
        "region": "地区",
        "unit": "来源/单位",
        "value": "数值",
        "value_unit": "数值单位",
        "chain": "链",
        "bid_price": "买一价",
        "bid_size": "买一量",
        "ask_price": "卖一价",
        "ask_size": "卖一量",
        "bid_levels": "买盘档数",
        "ask_levels": "卖盘档数",
        "bids_usd": "买盘聚合金额",
        "bids_quantity": "买盘聚合数量",
        "asks_usd": "卖盘聚合金额",
        "asks_quantity": "卖盘聚合数量",
        "revenue": "收入",
        "revenue_basis": "收入口径",
        "net_income": "净利润",
        "net_income_basis": "净利润口径",
        "assets": "资产",
        "assets_basis": "资产口径",
        "liabilities": "负债",
        "liabilities_basis": "负债口径",
        "cash_flow": "现金流",
        "cash_flow_basis": "现金流口径",
        "roe": "净资产收益率",
        "roa": "资产收益率",
        "gross_margin": "毛利率",
        "debt_ratio": "负债率",
        "eps": "每股收益",
        "pe": "市盈率",
        "pb": "市净率",
        "ps": "市销率",
        "market_cap": "市值",
        "market_cap_unit": "市值单位",
        "float_market_cap": "流通市值",
        "ev_ebitda": "企业价值倍数",
        "event_type": "事件类型",
        "event_date": "事件日期",
        "trade_date": "交易日期",
        "seat": "席位",
        "buy_amount": "买入金额",
        "sell_amount": "卖出金额",
        "main_net": "主力净流入",
        "small_net": "小单净流入",
        "mid_net": "中单净流入",
        "large_net": "大单净流入",
        "super_net": "超大单净流入",
        "hgt_net": "沪股通净流入",
        "sgt_net": "深股通净流入",
        "northbound_net": "北向合计净流入",
        "financing_balance": "融资余额",
        "margin_balance": "融资融券余额",
        "security_lending_volume": "融券余量",
        "security_lending_balance": "融券余额",
        "financing_buy_amount": "融资买入额",
        "financing_repayment_amount": "融资偿还额",
        "sector_name": "行业/板块名称",
        "keyword": "关键词",
        "topic": "主题",
        "reason": "原因",
        "question": "问题",
        "answer": "回答",
        "name": "名称",
        "turnover_rate": "换手率",
        "large_order_net": "大单净额",
        "symbol_id": "标的代码",
    }.get(value, value.replace("_", " "))


def _gap_reason_label(value: str) -> str:
    return {
        "credential_missing": "接口凭证缺失",
        "rate_limited": "来源限流",
        "rate_limited_by_tool_budget": "任务预算内无法等待限流窗口",
        "permission_denied": "接口权限不足",
        "provider_error": "来源调用失败",
        "provider_empty": "来源返回为空",
        "parser_missing": "解析器缺失",
        "empty_result": "来源返回为空",
        "warehouse_missing": "仓库没有可用记录",
        "warehouse_stale": "仓库记录已过期",
        "field_missing": "必需字段缺失",
        "date_range_missing": "未覆盖完整分析区间",
        "data_integrity_failed": "本地数据校验失败",
        "granularity_mismatch": "数据粒度不匹配",
        "license_blocked": "许可限制",
        "evidence_write_failed": "证据写入失败",
        "cache_hit": "复用已有缓存结果",
        "shared_result": "复用同批共享结果",
        "cached_empty": "缓存记录为空",
        "cooldown_skipped": "冷却期内跳过远端调用",
        "catalog_match_missing": "项目数据项未绑定可调用接口",
        "catalog match missing": "项目数据项未绑定可调用接口",
        "not_applicable": "不适用",
        "invalid_request": "请求无效",
        "sdk_http_unknown": "SDK 内部请求不可审计",
    }.get(value, value.replace("_", " "))


def _human_error(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    replacements = {
        "date_range_missing": "未覆盖完整分析区间",
        "warehouse_missing": "仓库没有可用记录",
        "field_missing": "必需字段缺失",
        "data_integrity_failed": "本地数据校验失败",
        "provider_error": "来源调用失败",
        "empty_result": "来源返回为空",
        "credential_missing": "接口凭证缺失",
        "sdk_http_unknown": "SDK 内部请求不可审计",
        "normalized rows do not expose period_start/period_end": "标准化数据没有提供完整起止日期",
        "chart_dependency_missing:pandas": "图表依赖缺失",
        "market data did not generate chart assets": "市场数据结果没有生成图表资产",
        "market data result did not generate chart assets": "市场数据结果没有生成图表资产",
        "no usable OHLCV rows": "没有可用于画图的开高低收量数据",
        "ohlcv_fields_missing": "画图必需字段缺失",
        "warehouse_recheck_missing_for_request": "取数后仓库仍未形成当前请求的可用记录",
        "report_data_result_dataset_mismatch": "数据结果返回的数据类型与请求不一致",
        "report_data_result_field_mismatch": "数据结果返回字段与请求不一致",
        "report_data_result_request_id_mismatch": "数据请求标识无法识别",
        "report_data_result_unknown_dataset": "数据请求的数据类型未登记",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.replace("_", " ")


def _display_value(value: object) -> str:
    if value is None:
        return "未提供"
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value) or "未提供"
    return str(value)


def _value_with_unit(value: object, unit: object) -> str:
    rendered = _display_value(value)
    unit_text = str(unit or "").strip()
    return f"{rendered} {unit_text}" if unit_text and rendered != "未提供" else rendered


def _status_zh(status: str) -> str:
    return {
        "ready": "可用",
        "partial": "部分可用",
        "insufficient": "不足",
        "missing": "缺失",
        "error": "错误",
    }.get(status, status)


def _error_payload(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _parse_datetime(value: Any) -> datetime | None:
    parsed = _parse_date(value)
    if parsed is None:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def _resolve_as_of(value: Any) -> datetime:
    now = datetime.now(tz=UTC)
    parsed = _parse_date(value)
    if parsed is None:
        return now
    if parsed == now.date():
        return now
    return datetime(parsed.year, parsed.month, parsed.day, 23, 59, 59, tzinfo=UTC)

def _dedupe(values: Sequence[str] | Any) -> tuple[str, ...]:
    output: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in output:
            output.append(text)
    return tuple(output)
