from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
import re
import time
from time import monotonic
from typing import Any, Callable, Mapping, Sequence

import akshare as ak
import pandas as pd

from field_mapping_v1 import CN_A_FUNDAMENTAL_FIELD_MAPPING_V1
from models import ApiCallSpec, NormalizedInput, ProviderAttempt, ProviderResult
from provider_specs import list_akshare_v1_api_names
from raw_payload_writer import (
    RAW_PAYLOAD_WRITER_RETRY_LIMIT_V1,
    RAW_PAYLOAD_WRITER_TIMEOUT_MS_V1,
    RawPayloadWriteInput,
    RawPayloadWriteResult,
    parse_raw_uri_attempt_seq,
    reserve_provider_attempt_seq_v1,
    write_raw_payload_to_openviking,
)
from security import sanitize_error

FND_AKSHARE_API_NOT_APPROVED = "FND_AKSHARE_API_NOT_APPROVED"

AKSHARE_EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "stock_individual_info_em": ("item", "value"),
    "stock_zh_a_spot_em": ("代码", "最新价", "总市值", "市盈率-动态", "市净率"),
    "stock_zh_a_hist": ("日期", "收盘", "成交量"),
    "stock_financial_abstract_ths": ("报告期", "净利润", "营业总收入", "每股收益", "净资产收益率"),
    "stock_history_dividend_detail": ("公告日期", "除权除息日", "每股分红"),
}

_AKSHARE_V1_ALLOWLIST = frozenset(list_akshare_v1_api_names())
_PERMISSION_MARKERS = ("permission", "forbidden", "denied", "unauthorized", "access", "401", "403")
_SHA256_HASH_RE = re.compile(r"^sha256:[a-f0-9]{64}$")


class AkShareFetcherError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class AkShareColumnValidationResult:
    ok: bool
    status: str | None
    reason: str | None
    schema_changed: bool
    filtered_dataframe: pd.DataFrame
    missing_columns: tuple[str, ...]


def invoke_akshare_api_by_dispatch(api_name: str, parameters: dict[str, str], timeout_ms: int):
    if api_name not in _AKSHARE_V1_ALLOWLIST:
        raise AkShareFetcherError(FND_AKSHARE_API_NOT_APPROVED, f"akshare api not approved: {api_name}")
    fn = getattr(ak, api_name, None)
    if not callable(fn):
        raise AkShareFetcherError(FND_AKSHARE_API_NOT_APPROVED, f"akshare api not callable: {api_name}")

    timeout_sec = max(timeout_ms, 1) / 1000.0
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(fn, **parameters)
        try:
            return future.result(timeout=timeout_sec)
        except FutureTimeoutError as exc:
            raise TimeoutError(f"akshare call timeout: {api_name}") from exc


def ValidateAkShareColumns(
    api_name: str,
    columns: Sequence[str],
    target_code: str,
    dataframe: pd.DataFrame | None = None,
) -> AkShareColumnValidationResult:
    expected = AKSHARE_EXPECTED_COLUMNS.get(api_name)
    resolved_df = _ensure_dataframe(dataframe)
    if expected is None:
        return AkShareColumnValidationResult(
            ok=False,
            status="schema_invalid",
            reason="schema_changed",
            schema_changed=True,
            filtered_dataframe=resolved_df,
            missing_columns=(),
        )

    column_set = {str(column).strip() for column in columns}
    missing = tuple(column for column in expected if column not in column_set)
    if missing:
        return AkShareColumnValidationResult(
            ok=False,
            status="schema_invalid",
            reason="schema_changed",
            schema_changed=True,
            filtered_dataframe=resolved_df,
            missing_columns=missing,
        )

    if api_name == "stock_zh_a_spot_em":
        filtered = _filter_spot_rows_by_target_code(resolved_df, target_code)
        if filtered.empty:
            return AkShareColumnValidationResult(
                ok=False,
                status="empty",
                reason="empty_response",
                schema_changed=False,
                filtered_dataframe=filtered,
                missing_columns=(),
            )
        return AkShareColumnValidationResult(
            ok=True,
            status=None,
            reason=None,
            schema_changed=False,
            filtered_dataframe=filtered,
            missing_columns=(),
        )

    if resolved_df.empty:
        return AkShareColumnValidationResult(
            ok=False,
            status="empty",
            reason="empty_response",
            schema_changed=False,
            filtered_dataframe=resolved_df,
            missing_columns=(),
        )

    return AkShareColumnValidationResult(
        ok=True,
        status=None,
        reason=None,
        schema_changed=False,
        filtered_dataframe=resolved_df,
        missing_columns=(),
    )


def map_akshare_columns_to_pack_fields_v1(
    api_name: str,
    dataframe: pd.DataFrame,
    ref_hash: str,
    ref_uri: str,
) -> list[tuple[str, Any, str]]:
    df = _ensure_dataframe(dataframe)
    if df.empty:
        return []

    rules = tuple(
        item
        for item in CN_A_FUNDAMENTAL_FIELD_MAPPING_V1
        if item.provider == "akshare" and item.api_name == api_name
    )
    if not rules:
        return []

    if api_name == "stock_individual_info_em":
        target_row = _flatten_item_value_rows(df)
    else:
        target_row = _select_target_row(api_name, df)
    if target_row is None:
        return []

    extracted: list[tuple[str, Any, str]] = []
    for rule in rules:
        if rule.source_column not in target_row:
            continue
        normalized_value = _normalize_field_value(target_row[rule.source_column], rule.unit_scale)
        if normalized_value is None:
            continue
        value_payload = {
            "value": normalized_value,
            "unit": rule.unit_scale,
            "scale": "x",
            "provider": "akshare",
            "api_name": api_name,
            "payload_hash": ref_hash,
            "raw_payload_ref": ref_uri,
        }
        extracted.append((rule.pack_field_path, value_payload, rule.source_ref_path))

    return extracted


@dataclass
class AkShareFetcher:
    raw_writer: Callable[[RawPayloadWriteInput], RawPayloadWriteResult] = write_raw_payload_to_openviking
    api_dispatcher: Callable[[str, dict[str, str], int], Any] = invoke_akshare_api_by_dispatch
    sleep_fn: Callable[[float], None] = time.sleep

    def Fetch(self, input_data: NormalizedInput, spec: ApiCallSpec) -> ProviderResult:
        started_at = _now_iso()
        started_perf = monotonic()
        retry_used = 0
        dataframe: pd.DataFrame | None = None
        retry_limit = max(0, spec.retry_limit)
        for retry_used in range(retry_limit + 1):
            try:
                dataframe = _ensure_dataframe(self.api_dispatcher(spec.api_name, spec.parameters, spec.timeout_ms))
                break
            except TimeoutError as exc:
                if retry_used < retry_limit:
                    self.sleep_fn(0.5)
                    continue
                return _build_provider_result(
                    spec=spec,
                    started_at=started_at,
                    started_perf=started_perf,
                    status="timeout",
                    reason="timeout",
                    error_type="timeout",
                    error_message=sanitize_error(exc),
                    retry_count=retry_used,
                )
            except Exception as exc:  # noqa: BLE001
                reason = _classify_provider_error_reason(exc)
                return _build_provider_result(
                    spec=spec,
                    started_at=started_at,
                    started_perf=started_perf,
                    status="error",
                    reason=reason,
                    error_type=reason,
                    error_message=sanitize_error(exc),
                    retry_count=retry_used,
                )

        if dataframe is None:
            return _build_provider_result(
                spec=spec,
                started_at=started_at,
                started_perf=started_perf,
                status="error",
                reason="provider_error",
                error_type="provider_error",
                error_message="akshare fetch failed without dataframe",
                retry_count=retry_used,
            )

        validation = ValidateAkShareColumns(
            spec.api_name,
            dataframe.columns.tolist(),
            input_data.akshare_symbol,
            dataframe=dataframe,
        )
        if not validation.ok:
            return _build_provider_result(
                spec=spec,
                started_at=started_at,
                started_perf=started_perf,
                status=validation.status or "error",
                reason=validation.reason,
                schema_changed=validation.schema_changed,
                response_dataframe=validation.filtered_dataframe,
                error_type=validation.reason,
                error_message=f"validation_failed:{validation.reason}",
                retry_count=retry_used,
            )

        planned_seq = reserve_provider_attempt_seq_v1(
            run_id=input_data.run_id,
            dispatch_id=input_data.dispatch_id,
            provider="akshare",
            api_name=spec.api_name,
        )
        raw_write = self.raw_writer(
            RawPayloadWriteInput(
                provider="akshare",
                api_name=spec.api_name,
                run_id=input_data.run_id,
                dispatch_id=input_data.dispatch_id,
                worker_id="fundamental_analyst",
                attempt_seq=planned_seq,
                payload=_dataframe_to_records(validation.filtered_dataframe),
                timeout_ms=RAW_PAYLOAD_WRITER_TIMEOUT_MS_V1,
                retry_limit=RAW_PAYLOAD_WRITER_RETRY_LIMIT_V1,
            )
        )
        if not raw_write.ok:
            return _build_provider_result(
                spec=spec,
                started_at=started_at,
                started_perf=started_perf,
                status="error",
                reason="raw_payload_write_failed",
                response_dataframe=validation.filtered_dataframe,
                attempt_seq=planned_seq,
                error_type=raw_write.error_type,
                error_message=raw_write.error_message_redacted,
                retry_count=retry_used,
            )
        if not _is_valid_raw_write_receipt(raw_write):
            return _build_provider_result(
                spec=spec,
                started_at=started_at,
                started_perf=started_perf,
                status="error",
                reason="raw_payload_write_invalid_receipt",
                response_dataframe=validation.filtered_dataframe,
                attempt_seq=planned_seq,
                error_type="raw_payload_write_invalid_receipt",
                error_message="raw writer returned invalid success receipt",
                retry_count=retry_used,
            )

        extracted_fields = map_akshare_columns_to_pack_fields_v1(
            spec.api_name,
            validation.filtered_dataframe,
            raw_write.content_hash or "",
            raw_write.uri or "",
        )
        return _build_provider_result(
            spec=spec,
            started_at=started_at,
            started_perf=started_perf,
            status="success",
            reason=None,
            response_dataframe=validation.filtered_dataframe,
            extracted_fields=extracted_fields,
            raw_payload_hash=raw_write.content_hash,
            raw_payload_ref=raw_write.uri,
            attempt_seq=raw_write.attempt_seq,
            report_period=_extract_first_non_empty_text(validation.filtered_dataframe, "报告期"),
            announce_date=_extract_first_non_empty_text(validation.filtered_dataframe, "公告日期"),
            as_of=_extract_as_of(validation.filtered_dataframe, default_as_of=input_data.current_date),
            fetched_at=_now_iso(),
            retry_count=retry_used,
        )


def _build_provider_result(
    *,
    spec: ApiCallSpec,
    started_at: str,
    started_perf: float,
    status: str,
    reason: str | None,
    schema_changed: bool = False,
    response_dataframe: pd.DataFrame | None = None,
    extracted_fields: list[tuple[str, Any, str]] | None = None,
    raw_payload_hash: str | None = None,
    raw_payload_ref: str | None = None,
    attempt_seq: int | None = None,
    error_type: str | None = None,
    error_message: str | None = None,
    report_period: str | None = None,
    announce_date: str | None = None,
    as_of: str | None = None,
    fetched_at: str | None = None,
    retry_count: int = 0,
) -> ProviderResult:
    df = _ensure_dataframe(response_dataframe)
    ended_at = _now_iso()
    resolved_fields = extracted_fields or []
    attempt = ProviderAttempt(
        provider="akshare",
        role=spec.role,
        api_name=spec.api_name,
        attempt_seq=attempt_seq,
        status=status,
        reason=reason,
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=max(0, int((monotonic() - started_perf) * 1000)),
        retry_count=max(0, retry_count),
        request_params_redacted=_redact_request_params(spec.parameters),
        response_row_count=len(df.index),
        response_col_count=len(df.columns),
        field_coverage=sorted({field_path for field_path, _, _ in resolved_fields}),
        report_period=report_period,
        announce_date=announce_date,
        as_of=as_of,
        fetched_at=fetched_at,
        raw_payload_hash=raw_payload_hash,
        raw_payload_ref=raw_payload_ref,
        error_type=error_type,
        error_message_redacted=sanitize_error(error_message) if error_message else None,
    )
    return ProviderResult(
        attempt=attempt,
        raw_payload_hash=raw_payload_hash,
        raw_payload_ref=raw_payload_ref,
        extracted_fields=resolved_fields,
        schema_changed=schema_changed,
    )


def _ensure_dataframe(dataframe: Any) -> pd.DataFrame:
    if isinstance(dataframe, pd.DataFrame):
        return dataframe.copy()
    if dataframe is None:
        return pd.DataFrame()
    if isinstance(dataframe, Mapping):
        return pd.DataFrame([dict(dataframe)])
    if isinstance(dataframe, Sequence) and not isinstance(dataframe, (str, bytes, bytearray)):
        return pd.DataFrame(list(dataframe))
    raise TypeError(f"akshare payload is not dataframe-like: {type(dataframe).__name__}")


def _filter_spot_rows_by_target_code(dataframe: pd.DataFrame, target_code: str) -> pd.DataFrame:
    if "代码" not in dataframe.columns:
        return pd.DataFrame(columns=dataframe.columns)
    normalized_target = target_code.strip()
    series = dataframe["代码"].astype(str).str.strip()
    mask = series == normalized_target
    return dataframe.loc[mask].copy()


def _select_target_row(api_name: str, dataframe: pd.DataFrame) -> Mapping[str, Any] | None:
    if dataframe.empty:
        return None
    if api_name == "stock_zh_a_hist" and "日期" in dataframe.columns:
        date_series = pd.to_datetime(dataframe["日期"], errors="coerce")
        if date_series.notna().any():
            idx = date_series.idxmax()
            return dataframe.loc[idx].to_dict()
    return dataframe.iloc[0].to_dict()


def _flatten_item_value_rows(dataframe: pd.DataFrame) -> dict[str, Any] | None:
    if dataframe.empty or "item" not in dataframe.columns or "value" not in dataframe.columns:
        return None
    flattened: dict[str, Any] = {}
    for _, row in dataframe.iterrows():
        item = row.get("item")
        if item is None or pd.isna(item):
            continue
        item_key = str(item).strip()
        if item_key == "":
            continue
        flattened[item_key] = row.get("value")
    return flattened if flattened else None


def _normalize_field_value(value: Any, unit_scale: str) -> Any | None:
    if value is None or pd.isna(value):
        return None
    if unit_scale in {"text", "date"}:
        text = str(value).strip()
        return text if text != "" else None

    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value).strip().replace(",", "")
    if text == "":
        return None
    if text.endswith("%"):
        text = text[:-1]
    try:
        return float(text)
    except ValueError:
        return None


def _dataframe_to_records(dataframe: pd.DataFrame) -> list[dict[str, Any]]:
    if dataframe.empty:
        return []
    normalized = dataframe.where(pd.notna(dataframe), None)
    return normalized.to_dict("records")


def _classify_provider_error_reason(exc: Exception) -> str:
    if isinstance(exc, PermissionError):
        return "permission_or_access"
    message = str(exc).lower()
    if any(marker in message for marker in _PERMISSION_MARKERS):
        return "permission_or_access"
    return "provider_error"


def _is_valid_raw_write_receipt(raw_write: RawPayloadWriteResult) -> bool:
    uri = raw_write.uri
    content_hash = raw_write.content_hash
    attempt_seq = raw_write.attempt_seq
    if not isinstance(uri, str) or uri.strip() == "":
        return False
    if not isinstance(content_hash, str) or _SHA256_HASH_RE.match(content_hash) is None:
        return False
    if not isinstance(attempt_seq, int) or attempt_seq < 1:
        return False
    try:
        uri_attempt_seq = parse_raw_uri_attempt_seq(uri)
    except Exception:  # noqa: BLE001
        return False
    return uri_attempt_seq == attempt_seq


def _redact_request_params(parameters: Mapping[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in parameters.items():
        key_text = str(key).strip()
        if key_text == "":
            continue
        value_text = str(value).strip()
        redacted[key_text] = sanitize_error(value_text)
    return redacted


def _extract_first_non_empty_text(dataframe: pd.DataFrame, column: str) -> str | None:
    if dataframe.empty or column not in dataframe.columns:
        return None
    series = dataframe[column]
    for value in series.tolist():
        if value is None or pd.isna(value):
            continue
        text = str(value).strip()
        if text != "":
            return text
    return None


def _extract_as_of(dataframe: pd.DataFrame, *, default_as_of: str | None) -> str | None:
    for column_name in ("日期", "trade_date"):
        value = _extract_first_non_empty_text(dataframe, column_name)
        if value is not None:
            return value
    return default_as_of


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
