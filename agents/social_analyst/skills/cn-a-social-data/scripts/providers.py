from __future__ import annotations

from dataclasses import asdict
from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import importlib
import json
import math
import os
import re
import time
from typing import Any, Literal, Mapping

from config import DEFAULT_PROVIDER_TIMEOUT_SECONDS, SocialDataConfig
from observability import emit_json_log, record_metric, trace_span
from profile import DateWindow

SOCIAL_PROVIDER_FORBIDDEN_SOURCE = "SOCIAL_PROVIDER_FORBIDDEN_SOURCE"
SOCIAL_PROVIDER_PLAN_INVALID_PROFILE = "SOCIAL_PROVIDER_PLAN_INVALID_PROFILE"
SOCIAL_PROVIDER_TIMEOUT = "SOCIAL_PROVIDER_TIMEOUT"
SOCIAL_PROVIDER_EXCEPTION = "SOCIAL_PROVIDER_EXCEPTION"
SOCIAL_PROVIDER_EMPTY = "SOCIAL_PROVIDER_EMPTY"
SOCIAL_RAW_REF_NOT_READABLE = "SOCIAL_RAW_REF_NOT_READABLE"
SOCIAL_RAW_HASH_MISMATCH = "SOCIAL_RAW_HASH_MISMATCH"
SOCIAL_ROW_EVIDENCE_INCOMPLETE = "SOCIAL_ROW_EVIDENCE_INCOMPLETE"

ProviderStatus = Literal["success", "empty", "error", "timeout", "cancelled"]

_SYMBOL_ENDPOINTS = frozenset(
    {
        "stock_hot_rank_latest_em",
        "stock_hot_keyword_em",
        "stock_hot_rank_relate_em",
    }
)
_NO_PARAM_ENDPOINTS = frozenset({"stock_hot_rank_em", "stock_hot_up_em"})
_APPROVED_ENDPOINTS = _SYMBOL_ENDPOINTS | _NO_PARAM_ENDPOINTS
_FORBIDDEN_SOURCE_ENDPOINTS = frozenset(
    {
        "stock_hot_follow_xq",
        "stock_hot_tweet_xq",
        "stock_hot_deal_xq",
        "xueqiu_heat",
    }
)
_VIKING_WORKFLOW_PREFIX = "viking://resources/workflow/"
_OPENVIKING_BACKEND_ENV = "CLAW_TRADE_OPENVIKING_BACKEND"
_SHA256_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")


@dataclass(frozen=True)
class ProviderSpec:
    provider: str
    endpoint: str
    priority: Literal["P0", "P1"]
    role: str
    enabled: bool
    required_for_complete: bool
    timeout_seconds: int
    max_rows: int


@dataclass(frozen=True)
class ProviderQuery:
    provider: str
    endpoint: str
    priority: Literal["P0", "P1"]
    query: dict[str, Any]
    query_fingerprint: str
    date_window: DateWindow
    timeout_seconds: int


@dataclass
class RawProviderResult:
    provider: str
    endpoint: str
    query: dict[str, Any]
    ok: bool
    status: ProviderStatus
    raw_payload: Any | None
    raw_count: int
    elapsed_ms: int
    empty_reason: str | None
    error: dict[str, str] | None
    payload_hash: str | None
    row_count: int
    fields: dict[str, Any]
    as_of_date: str | None
    fetched_at: str


NormalizedSourceKind = Literal["heat_rank", "heat_keyword", "related_symbol", "narrative_text"]


@dataclass(frozen=True)
class NormalizedProviderRow:
    provider: str
    endpoint: str
    platform: str
    source_kind: NormalizedSourceKind
    raw_index: int
    fields: dict[str, Any]
    payload_hash: str
    raw_payload_ref: str


@dataclass
class ProviderExecutionResult:
    query: ProviderQuery
    attempt: Any
    raw_rows: list[NormalizedProviderRow]
    raw_payload_ref: str | None
    payload_hash: str | None
    cache_inspection: Any


class SocialProviderPlanError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


class SocialProviderNormalizationError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


def build_provider_plan(
    profile: Any,
    date_window: DateWindow,
    config: SocialDataConfig,
    *,
    provider_specs_override: tuple[ProviderSpec, ...] | None = None,
) -> list[ProviderQuery]:
    eastmoney_symbol = _resolve_profile_string(profile, "eastmoney_symbol")
    specs = provider_specs_override or _load_approved_provider_specs(config)
    plan: list[ProviderQuery] = []
    for spec in specs:
        _ensure_allowed_endpoint(spec.endpoint)
        if not spec.enabled:
            continue
        query = _build_endpoint_query(spec.endpoint, eastmoney_symbol=eastmoney_symbol)
        plan.append(
            ProviderQuery(
                provider=spec.provider,
                endpoint=spec.endpoint,
                priority=spec.priority,
                query=query,
                query_fingerprint=_build_query_fingerprint(query),
                date_window=date_window,
                timeout_seconds=spec.timeout_seconds,
            )
        )
    return plan


def fetch_provider_payload(query: ProviderQuery, *, ticker_plain: str | None = None) -> RawProviderResult:
    started_at = time.perf_counter()
    fetched_at = datetime.now(UTC).isoformat()
    record_metric("social.provider.call.count", tags={"endpoint": query.endpoint, "provider": query.provider})
    try:
        _ensure_allowed_endpoint(query.endpoint)
    except SocialProviderPlanError as exc:
        return _error_result(
            query,
            code=exc.code,
            message=exc.message,
            started_at=started_at,
            fetched_at=fetched_at,
        )

    timeout_seconds = _resolve_timeout_seconds(query.timeout_seconds)
    with trace_span(
        f"social.provider.fetch.{query.endpoint}",
        endpoint=query.endpoint,
        attrs={"provider": query.provider},
    ):
        try:
            payload = _call_with_timeout(
                timeout_seconds=timeout_seconds,
                call=lambda: _call_akshare_endpoint(query.endpoint, query.query),
            )
            rows = _rows_from_provider_payload(payload)
        except TimeoutError:
            emit_json_log(
                level="ERROR",
                event="social.provider.timeout",
                code=SOCIAL_PROVIDER_TIMEOUT,
                endpoint=query.endpoint,
                fields={"provider": query.provider},
            )
            record_metric("social.provider.status.count", tags={"endpoint": query.endpoint, "status": "timeout"})
            return RawProviderResult(
                provider=query.provider,
                endpoint=query.endpoint,
                query=dict(query.query),
                ok=False,
                status="timeout",
                raw_payload=None,
                raw_count=0,
                elapsed_ms=_elapsed_ms(started_at),
                empty_reason=None,
                error={
                    "code": SOCIAL_PROVIDER_TIMEOUT,
                    "message": f"endpoint 调用超时（{timeout_seconds}s）",
                },
                payload_hash=None,
                row_count=0,
                fields=extract_required_fields(
                    endpoint=query.endpoint,
                    rows=[],
                    query=query.query,
                    ticker_plain=ticker_plain,
                    as_of_date=None,
                ),
                as_of_date=None,
                fetched_at=fetched_at,
            )
        except Exception as exc:
            error_message = _sanitize_error_message(exc)
            emit_json_log(
                level="ERROR",
                event="social.provider.error",
                code=SOCIAL_PROVIDER_EXCEPTION,
                endpoint=query.endpoint,
                fields={"provider": query.provider, "error": error_message},
            )
            record_metric("social.provider.status.count", tags={"endpoint": query.endpoint, "status": "error"})
            return _error_result(
                query,
                code=SOCIAL_PROVIDER_EXCEPTION,
                message=error_message,
                started_at=started_at,
                fetched_at=fetched_at,
            )

    as_of_date = _extract_as_of_date(rows)
    fields = extract_required_fields(
        endpoint=query.endpoint,
        rows=rows,
        query=query.query,
        ticker_plain=ticker_plain,
        as_of_date=as_of_date,
    )
    if not rows:
        record_metric("social.provider.status.count", tags={"endpoint": query.endpoint, "status": "empty"})
        return RawProviderResult(
            provider=query.provider,
            endpoint=query.endpoint,
            query=dict(query.query),
            ok=False,
            status="empty",
            raw_payload=[],
            raw_count=0,
            elapsed_ms=_elapsed_ms(started_at),
            empty_reason=SOCIAL_PROVIDER_EMPTY,
            error=None,
            payload_hash=None,
            row_count=0,
            fields=fields,
            as_of_date=as_of_date,
            fetched_at=fetched_at,
        )

    payload_hash = _build_payload_hash(rows)
    record_metric("social.provider.status.count", tags={"endpoint": query.endpoint, "status": "success"})
    record_metric("social.provider.rows.count", len(rows), metric_type="gauge", tags={"endpoint": query.endpoint})
    return RawProviderResult(
        provider=query.provider,
        endpoint=query.endpoint,
        query=dict(query.query),
        ok=True,
        status="success",
        raw_payload=rows,
        raw_count=len(rows),
        elapsed_ms=_elapsed_ms(started_at),
        empty_reason=None,
        error=None,
        payload_hash=payload_hash,
        row_count=len(rows),
        fields=fields,
        as_of_date=as_of_date,
        fetched_at=fetched_at,
    )


def normalize_provider_payload(
    query: ProviderQuery,
    raw_result: RawProviderResult,
    raw_payload_ref: str,
) -> list[NormalizedProviderRow]:
    ref = _normalize_raw_payload_ref(raw_payload_ref)
    rows = _rows_from_provider_payload(raw_result.raw_payload)
    if not rows:
        return []

    payload_hash = _normalize_sha256(raw_result.payload_hash) or _build_payload_hash(rows)
    source_kind = _resolve_source_kind(query.endpoint)
    normalized: list[NormalizedProviderRow] = []
    for raw_index, row in enumerate(rows):
        normalized.append(
            NormalizedProviderRow(
                provider=query.provider,
                endpoint=query.endpoint,
                platform="东方财富",
                source_kind=source_kind,
                raw_index=raw_index,
                fields=dict(row),
                payload_hash=payload_hash,
                raw_payload_ref=ref,
            )
        )
    return normalized


def load_rows_by_raw_payload_ref(ref: str, expected_hash: str | None) -> list[dict[str, Any]]:
    normalized_ref = _normalize_raw_payload_ref(ref)
    try:
        content = _read_openviking_content_by_uri(normalized_ref)
        payload = json.loads(content.decode("utf-8"))
    except Exception as exc:
        raise SocialProviderNormalizationError(
            SOCIAL_RAW_REF_NOT_READABLE,
            f"raw_payload_ref 无法读取: {exc}",
        ) from exc

    rows = _rows_from_openviking_payload(payload)
    actual_hash = _build_payload_hash(rows)
    expected = _normalize_sha256(expected_hash)
    if expected is not None and actual_hash != expected:
        raise SocialProviderNormalizationError(
            SOCIAL_RAW_HASH_MISMATCH,
            f"raw payload hash 不匹配: expected={expected} actual={actual_hash}",
        )
    return rows


def normalize_provider_rows(execution_results: list[ProviderExecutionResult]) -> list[NormalizedProviderRow]:
    normalized_rows: list[NormalizedProviderRow] = []
    for result in execution_results:
        for row in result.raw_rows:
            normalized = _coerce_normalized_row(row)
            if (
                _normalize_raw_payload_ref_optional(normalized.raw_payload_ref) is None
                or _normalize_sha256(normalized.payload_hash) is None
                or not isinstance(normalized.raw_index, int)
                or normalized.raw_index < 0
            ):
                raise SocialProviderNormalizationError(
                    SOCIAL_ROW_EVIDENCE_INCOMPLETE,
                    "normalized row 缺少 raw_payload_ref/payload_hash/raw_index",
                )
            normalized_rows.append(normalized)
    return normalized_rows


def extract_required_fields(
    *,
    endpoint: str,
    rows: list[dict[str, Any]],
    query: Mapping[str, Any],
    ticker_plain: str | None,
    as_of_date: str | None,
) -> dict[str, Any]:
    if endpoint == "stock_hot_rank_latest_em":
        rank_row = rows[0] if rows else {}
        return {
            "symbol": query.get("symbol"),
            "rank": _coerce_int(_first_of(rank_row, ("rank", "排名"))),
            "heat": _coerce_float(_first_of(rank_row, ("heat", "热度"))),
            "as_of_date": as_of_date,
        }
    if endpoint == "stock_hot_keyword_em":
        top_keywords = _extract_keyword_list(rows)
        return {
            "symbol": query.get("symbol"),
            "top_keywords": top_keywords,
            "keyword_count": len(top_keywords),
            "as_of_date": as_of_date,
        }
    if endpoint == "stock_hot_rank_relate_em":
        related_symbols = _extract_related_symbols(rows)
        return {
            "symbol": query.get("symbol"),
            "related_symbols": related_symbols,
            "related_count": len(related_symbols),
            "as_of_date": as_of_date,
        }
    if endpoint == "stock_hot_rank_em":
        target_row = _find_target_row(rows, ticker_plain)
        return {
            "target_present": target_row is not None,
            "target_rank": _extract_rank_value(target_row),
            "sample_size": len(rows),
            "as_of_date": as_of_date,
        }
    if endpoint == "stock_hot_up_em":
        target_row = _find_target_row(rows, ticker_plain)
        return {
            "target_present": target_row is not None,
            "target_rank_change": _extract_rank_change_value(target_row),
            "sample_size": len(rows),
            "as_of_date": as_of_date,
        }
    raise SocialProviderPlanError(
        SOCIAL_PROVIDER_FORBIDDEN_SOURCE,
        f"endpoint 不在批准清单: {endpoint}",
    )


def _load_approved_provider_specs(config: SocialDataConfig) -> tuple[ProviderSpec, ...]:
    return (
        ProviderSpec(
            provider="akshare",
            endpoint="stock_hot_rank_latest_em",
            priority="P0",
            role="target_latest_heat",
            enabled=True,
            required_for_complete=True,
            timeout_seconds=config.provider_timeout_seconds,
            max_rows=20,
        ),
        ProviderSpec(
            provider="akshare",
            endpoint="stock_hot_keyword_em",
            priority="P0",
            role="target_hot_keywords",
            enabled=True,
            required_for_complete=True,
            timeout_seconds=config.provider_timeout_seconds,
            max_rows=20,
        ),
        ProviderSpec(
            provider="akshare",
            endpoint="stock_hot_rank_relate_em",
            priority="P0",
            role="target_related_symbols",
            enabled=True,
            required_for_complete=True,
            timeout_seconds=config.provider_timeout_seconds,
            max_rows=20,
        ),
        ProviderSpec(
            provider="akshare",
            endpoint="stock_hot_rank_em",
            priority="P0",
            role="market_rank_validation",
            enabled=True,
            required_for_complete=True,
            timeout_seconds=config.provider_timeout_seconds,
            max_rows=100,
        ),
        ProviderSpec(
            provider="akshare",
            endpoint="stock_hot_up_em",
            priority="P1",
            role="market_hot_up_enhancement",
            enabled=config.p1_hot_up_enabled,
            required_for_complete=False,
            timeout_seconds=config.provider_timeout_seconds,
            max_rows=100,
        ),
        # P1 雪球增强在 HLD 未补 endpoint/字段前不进入真实 provider 规格。
    )


def _coerce_normalized_row(row: Any) -> NormalizedProviderRow:
    if isinstance(row, NormalizedProviderRow):
        return row
    if not isinstance(row, Mapping):
        raise SocialProviderNormalizationError(
            SOCIAL_ROW_EVIDENCE_INCOMPLETE,
            "normalized row 类型非法",
        )

    provider = _require_text(row.get("provider"), SOCIAL_ROW_EVIDENCE_INCOMPLETE, "provider 缺失")
    endpoint = _require_text(row.get("endpoint"), SOCIAL_ROW_EVIDENCE_INCOMPLETE, "endpoint 缺失")
    platform = _require_text(row.get("platform"), SOCIAL_ROW_EVIDENCE_INCOMPLETE, "platform 缺失")
    source_kind = _require_source_kind(row.get("source_kind"))
    raw_index = row.get("raw_index")
    if not isinstance(raw_index, int):
        raise SocialProviderNormalizationError(
            SOCIAL_ROW_EVIDENCE_INCOMPLETE,
            "raw_index 缺失",
        )
    fields_raw = row.get("fields")
    fields = dict(fields_raw) if isinstance(fields_raw, Mapping) else {}
    payload_hash = _require_text(row.get("payload_hash"), SOCIAL_ROW_EVIDENCE_INCOMPLETE, "payload_hash 缺失")
    raw_payload_ref = _require_text(
        row.get("raw_payload_ref"),
        SOCIAL_ROW_EVIDENCE_INCOMPLETE,
        "raw_payload_ref 缺失",
    )
    return NormalizedProviderRow(
        provider=provider,
        endpoint=endpoint,
        platform=platform,
        source_kind=source_kind,
        raw_index=raw_index,
        fields=fields,
        payload_hash=payload_hash,
        raw_payload_ref=raw_payload_ref,
    )


def _require_source_kind(value: Any) -> NormalizedSourceKind:
    source_kind = _require_text(value, SOCIAL_ROW_EVIDENCE_INCOMPLETE, "source_kind 缺失")
    if source_kind not in {"heat_rank", "heat_keyword", "related_symbol", "narrative_text"}:
        raise SocialProviderNormalizationError(
            SOCIAL_ROW_EVIDENCE_INCOMPLETE,
            f"source_kind 非法: {source_kind}",
        )
    return source_kind


def _resolve_source_kind(endpoint: str) -> NormalizedSourceKind:
    if endpoint == "stock_hot_keyword_em":
        return "heat_keyword"
    if endpoint == "stock_hot_rank_relate_em":
        return "related_symbol"
    if endpoint in {"stock_hot_rank_latest_em", "stock_hot_rank_em", "stock_hot_up_em"}:
        return "heat_rank"
    return "narrative_text"


def _rows_from_openviking_payload(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, Mapping):
        rows = payload.get("rows")
        if isinstance(rows, list):
            return _canonicalize_rows(rows)
        inner_payload = payload.get("payload")
        if isinstance(inner_payload, list):
            return _canonicalize_rows(inner_payload)
    return _rows_from_provider_payload(payload)


def _read_openviking_content_by_uri(ref: str) -> bytes:
    backend = _resolve_openviking_read_backend()
    if not callable(getattr(backend, "fetch_content_by_uri", None)):
        raise SocialProviderNormalizationError(
            SOCIAL_RAW_REF_NOT_READABLE,
            "OpenViking backend 缺少 fetch_content_by_uri",
        )
    raw = backend.fetch_content_by_uri(ref)
    if isinstance(raw, bytes):
        return raw
    if isinstance(raw, bytearray):
        return bytes(raw)
    if isinstance(raw, str):
        return raw.encode("utf-8")
    raise SocialProviderNormalizationError(
        SOCIAL_RAW_REF_NOT_READABLE,
        "OpenViking 返回内容类型非法",
    )


def _resolve_openviking_read_backend() -> object:
    backend_spec = os.environ.get(_OPENVIKING_BACKEND_ENV, "").strip()
    if backend_spec:
        return _load_runtime_object(backend_spec, env_key=_OPENVIKING_BACKEND_ENV)
    try:
        module = importlib.import_module("claw_trade.artifacts.openviking_backend_http")
        factory = getattr(module, "create_default_backend")
        return factory()
    except Exception as exc:
        raise SocialProviderNormalizationError(
            SOCIAL_RAW_REF_NOT_READABLE,
            f"OpenViking backend 加载失败: {exc}",
        ) from exc


def _load_runtime_object(spec: str, *, env_key: str) -> object:
    module_name, separator, attr_name = spec.partition(":")
    if not module_name or separator != ":" or not attr_name:
        raise SocialProviderNormalizationError(
            SOCIAL_RAW_REF_NOT_READABLE,
            f"{env_key} 格式错误，必须是 module:attr",
        )
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise SocialProviderNormalizationError(
            SOCIAL_RAW_REF_NOT_READABLE,
            f"{env_key} 导入模块失败: {exc}",
        ) from exc
    if not hasattr(module, attr_name):
        raise SocialProviderNormalizationError(
            SOCIAL_RAW_REF_NOT_READABLE,
            f"{env_key} 指向属性不存在: {spec}",
        )
    symbol = getattr(module, attr_name)
    if isinstance(symbol, type):
        return symbol()
    if callable(symbol):
        try:
            return symbol()
        except TypeError:
            return symbol
    return symbol


def _normalize_sha256(value: Any) -> str | None:
    text = _normalize_text(value)
    if text is None:
        return None
    text = text.lower()
    if not _SHA256_PATTERN.match(text):
        return None
    return text


def _normalize_raw_payload_ref(ref: str) -> str:
    normalized = _normalize_raw_payload_ref_optional(ref)
    if normalized is None:
        raise SocialProviderNormalizationError(
            SOCIAL_RAW_REF_NOT_READABLE,
            "raw_payload_ref 非法或为空",
        )
    return normalized


def _normalize_raw_payload_ref_optional(ref: Any) -> str | None:
    text = _normalize_text(ref)
    if text is None:
        return None
    if not text.startswith(_VIKING_WORKFLOW_PREFIX):
        return None
    return text


def _require_text(value: Any, code: str, message: str) -> str:
    text = _normalize_text(value)
    if text is None:
        raise SocialProviderNormalizationError(code, message)
    return text


def _ensure_allowed_endpoint(endpoint: str) -> None:
    if endpoint in _FORBIDDEN_SOURCE_ENDPOINTS:
        raise SocialProviderPlanError(
            SOCIAL_PROVIDER_FORBIDDEN_SOURCE,
            f"endpoint 属于禁用来源: {endpoint}",
        )
    if endpoint not in _APPROVED_ENDPOINTS:
        raise SocialProviderPlanError(
            SOCIAL_PROVIDER_FORBIDDEN_SOURCE,
            f"endpoint 不在批准清单: {endpoint}",
        )


def _build_endpoint_query(endpoint: str, *, eastmoney_symbol: str) -> dict[str, Any]:
    if endpoint in _SYMBOL_ENDPOINTS:
        return {"symbol": eastmoney_symbol}
    if endpoint in _NO_PARAM_ENDPOINTS:
        return {}
    raise SocialProviderPlanError(
        SOCIAL_PROVIDER_FORBIDDEN_SOURCE,
        f"endpoint 不在批准清单: {endpoint}",
    )


def _build_query_fingerprint(query: dict[str, Any]) -> str:
    canonical = json.dumps(query, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _resolve_profile_string(profile: Any, field: str) -> str:
    value: Any
    if isinstance(profile, Mapping):
        value = profile.get(field)
    else:
        value = getattr(profile, field, None)
    if not isinstance(value, str) or not value.strip():
        raise SocialProviderPlanError(
            SOCIAL_PROVIDER_PLAN_INVALID_PROFILE,
            f"profile 缺少有效字段: {field}",
        )
    return value


def _call_akshare_endpoint(endpoint: str, query: Mapping[str, Any]) -> Any:
    try:
        import akshare as ak  # type: ignore
    except Exception as exc:
        raise RuntimeError("akshare 导入失败") from exc

    if endpoint == "stock_hot_rank_latest_em":
        return ak.stock_hot_rank_latest_em(symbol=_resolve_symbol(query))
    if endpoint == "stock_hot_keyword_em":
        return ak.stock_hot_keyword_em(symbol=_resolve_symbol(query))
    if endpoint == "stock_hot_rank_relate_em":
        return ak.stock_hot_rank_relate_em(symbol=_resolve_symbol(query))
    if endpoint == "stock_hot_rank_em":
        return ak.stock_hot_rank_em()
    if endpoint == "stock_hot_up_em":
        return ak.stock_hot_up_em()
    raise SocialProviderPlanError(
        SOCIAL_PROVIDER_FORBIDDEN_SOURCE,
        f"endpoint 不在批准清单: {endpoint}",
    )


def _resolve_symbol(query: Mapping[str, Any]) -> str:
    symbol = query.get("symbol")
    if not isinstance(symbol, str) or not symbol.strip():
        raise SocialProviderPlanError(
            SOCIAL_PROVIDER_PLAN_INVALID_PROFILE,
            "query 缺少有效 symbol 参数",
        )
    return symbol


def _call_with_timeout(timeout_seconds: int, call: Any) -> Any:
    import concurrent.futures

    executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
    future = executor.submit(call)
    try:
        return future.result(timeout=timeout_seconds)
    except concurrent.futures.TimeoutError as exc:
        future.cancel()
        raise TimeoutError("provider endpoint timeout") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _rows_from_provider_payload(payload: Any) -> list[dict[str, Any]]:
    if payload is None:
        return []
    if hasattr(payload, "to_dict") and callable(payload.to_dict):
        rows = payload.to_dict(orient="records")
        return _canonicalize_rows(rows)
    if isinstance(payload, list):
        return _canonicalize_rows(payload)
    if isinstance(payload, tuple):
        return _canonicalize_rows(list(payload))
    if isinstance(payload, Mapping):
        return [dict(payload)]
    return [{"value": payload}]


def _canonicalize_rows(rows: list[Any]) -> list[dict[str, Any]]:
    canonical: list[dict[str, Any]] = []
    for row in rows:
        if isinstance(row, Mapping):
            canonical.append(dict(row))
            continue
        if hasattr(row, "__dataclass_fields__"):
            canonical.append(asdict(row))
            continue
        canonical.append({"value": row})
    return canonical


def _build_payload_hash(payload: list[dict[str, Any]]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def _elapsed_ms(started_at: float) -> int:
    elapsed = max(0.0, (time.perf_counter() - started_at) * 1000.0)
    return int(math.ceil(elapsed))


def _error_result(
    query: ProviderQuery,
    *,
    code: str,
    message: str,
    started_at: float,
    fetched_at: str,
) -> RawProviderResult:
    return RawProviderResult(
        provider=query.provider,
        endpoint=query.endpoint,
        query=dict(query.query),
        ok=False,
        status="error",
        raw_payload=None,
        raw_count=0,
        elapsed_ms=_elapsed_ms(started_at),
        empty_reason=None,
        error={"code": code, "message": message},
        payload_hash=None,
        row_count=0,
        fields={},
        as_of_date=None,
        fetched_at=fetched_at,
    )


def _resolve_timeout_seconds(timeout_seconds: Any) -> int:
    if not isinstance(timeout_seconds, int) or timeout_seconds <= 0:
        return DEFAULT_PROVIDER_TIMEOUT_SECONDS
    return timeout_seconds


def _sanitize_error_message(exc: Exception) -> str:
    text = str(exc).replace("\n", " ").strip()
    if text == "":
        return exc.__class__.__name__
    lowered = text.lower()
    if any(token in lowered for token in ("credential", "cookie", "token", "secret", "authorization")):
        return f"{exc.__class__.__name__}:sensitive details redacted"
    if len(text) > 200:
        text = text[:200]
    return f"{exc.__class__.__name__}:{text}"


def _extract_as_of_date(rows: list[dict[str, Any]]) -> str | None:
    date_fields = ("as_of_date", "日期", "时间", "更新时间", "latest_time", "calc_time")
    for row in rows:
        for field_name in date_fields:
            date_value = _to_iso_date(_normalize_text(row.get(field_name)))
            if date_value is not None:
                return date_value
    return None


def _to_iso_date(value: str | None) -> str | None:
    if value is None:
        return None
    candidate = value.replace("/", "-")
    if len(candidate) < 10:
        return None
    date_candidate = candidate[:10]
    try:
        return datetime.strptime(date_candidate, "%Y-%m-%d").date().isoformat()
    except ValueError:
        return None


def _extract_keyword_list(rows: list[dict[str, Any]]) -> list[str]:
    keywords: list[str] = []
    seen: set[str] = set()
    for row in rows:
        keyword = _normalize_text(_first_of(row, ("keyword", "关键词", "主题", "item")))
        if keyword is None or keyword in seen:
            continue
        seen.add(keyword)
        keywords.append(keyword)
    return keywords


def _extract_related_symbols(rows: list[dict[str, Any]]) -> list[str]:
    symbols: list[str] = []
    seen: set[str] = set()
    for row in rows:
        ticker = _normalize_ticker_plain(_first_of(row, ("symbol", "代码", "股票代码", "证券代码")))
        if ticker is None or ticker in seen:
            continue
        seen.add(ticker)
        symbols.append(ticker)
    return symbols


def _find_target_row(rows: list[dict[str, Any]], ticker_plain: str | None) -> dict[str, Any] | None:
    target = _normalize_ticker_plain(ticker_plain)
    if target is None:
        return None
    for row in rows:
        row_ticker = _normalize_ticker_plain(_first_of(row, ("symbol", "代码", "股票代码", "证券代码")))
        if row_ticker == target:
            return row
    return None


def _extract_rank_value(row: Mapping[str, Any] | None) -> int | None:
    if row is None:
        return None
    return _coerce_int(_first_of(row, ("rank", "排名", "当前排名")))


def _extract_rank_change_value(row: Mapping[str, Any] | None) -> int | None:
    if row is None:
        return None
    return _coerce_int(_first_of(row, ("rank_change", "排名变化", "排名较昨日变动", "排名变化值")))


def _first_of(row: Mapping[str, Any], field_names: tuple[str, ...]) -> Any:
    for field_name in field_names:
        if field_name in row:
            return row.get(field_name)
    return None


def _normalize_ticker_plain(value: Any) -> str | None:
    text = _normalize_text(value)
    if text is None:
        return None
    compact = text.replace("-", "").replace(".", "").upper()
    if compact.startswith("SH") or compact.startswith("SZ"):
        compact = compact[2:]
    if compact.endswith("SH") or compact.endswith("SZ"):
        compact = compact[:6]
    if len(compact) == 6 and compact.isdigit():
        return compact
    return None


def _normalize_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "":
        return None
    return text


def _coerce_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if math.isnan(value):
            return None
        return int(value)
    text = _normalize_text(value)
    if text is None:
        return None
    text = text.replace(",", "")
    if text.startswith("+"):
        text = text[1:]
    try:
        return int(float(text))
    except ValueError:
        return None


def _coerce_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if math.isnan(number):
            return None
        return number
    text = _normalize_text(value)
    if text is None:
        return None
    text = text.replace(",", "")
    try:
        number = float(text)
    except ValueError:
        return None
    if math.isnan(number):
        return None
    return number
