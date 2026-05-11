from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import import_module
import re
import time
from typing import Any, Callable, Iterable, Mapping, Sequence

from claw_trade.providers.tushare_client import create_tushare_pro

from config import FundamentalDataConfig
from field_mapping_v1 import CN_A_FUNDAMENTAL_FIELD_MAPPING_V1
from models import ApiCallSpec, NormalizedInput, ProviderAttempt, ProviderResult
from provider_specs import list_tushare_v1_api_names
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

FND_TUSHARE_API_NOT_APPROVED = "FND_TUSHARE_API_NOT_APPROVED"
FND_TUSHARE_TOKEN_MISSING = "FND_TUSHARE_TOKEN_MISSING"
FND_TUSHARE_TIMEOUT_INVALID = "FND_TUSHARE_TIMEOUT_INVALID"

_PERMISSION_HINTS = (
    "permission",
    "权限",
    "access denied",
    "forbidden",
    "denied",
)
_QUOTA_HINTS = (
    "quota",
    "rate limit",
    "too many request",
    "frequency",
    "积分",
    "额度",
    "频次",
    "次数",
    "超限",
)
_REQUEST_PARAM_ALLOWLIST = frozenset(
    {
        "ts_code",
        "trade_date",
        "period",
        "start_date",
        "end_date",
        "exchange",
        "api_name",
    }
)
_SHA256_HASH_RE = re.compile(r"^sha256:[a-f0-9]{64}$")

TUSHARE_EXPECTED_COLUMNS: dict[str, frozenset[str]] = {
    "stock_company": frozenset({"ts_code", "name", "province", "city", "introduction", "main_business"}),
    "stock_basic": frozenset({"ts_code", "name", "industry", "market", "list_date"}),
    "daily_basic": frozenset({"ts_code", "trade_date", "close", "pe_ttm", "pb", "total_mv", "float_mv"}),
    "fina_indicator": frozenset(
        {"ts_code", "end_date", "ann_date", "roe", "roa", "grossprofit_margin", "netprofit_margin", "debt_to_assets"}
    ),
    "income": frozenset({"ts_code", "end_date", "ann_date", "revenue", "n_income", "basic_eps"}),
    "balancesheet": frozenset(
        {"ts_code", "end_date", "ann_date", "total_assets", "total_liab", "total_hldr_eqy_exc_min_int"}
    ),
    "cashflow": frozenset({"ts_code", "end_date", "ann_date", "n_cashflow_act"}),
    "fina_mainbz": frozenset({"ts_code", "end_date", "bz_item", "bz_sales", "bz_profit"}),
    "dividend": frozenset({"ts_code", "ann_date", "end_date", "record_date", "ex_date", "stk_div", "cash_div_tax"}),
    "top10_holders": frozenset({"ts_code", "ann_date", "end_date", "holder_name", "hold_amount", "hold_ratio"}),
    "top10_floatholders": frozenset({"ts_code", "ann_date", "end_date", "holder_name", "hold_amount", "hold_ratio"}),
}


class TushareFetcherError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class TushareColumnsValidation:
    is_schema_changed: bool
    missing_columns: tuple[str, ...]


def ValidateTushareColumns(api_name: str, columns: Iterable[str]) -> TushareColumnsValidation:
    expected = TUSHARE_EXPECTED_COLUMNS.get(api_name)
    if expected is None:
        raise TushareFetcherError(FND_TUSHARE_API_NOT_APPROVED, f"api not approved: {api_name}")
    observed = {str(item).strip() for item in columns if str(item).strip()}
    missing = tuple(sorted(expected - observed))
    return TushareColumnsValidation(
        is_schema_changed=bool(missing),
        missing_columns=missing,
    )


def invoke_tushare_api_by_dispatch(token: str, api_name: str, parameters: dict[str, str], timeout_ms: int) -> Any:
    if token.strip() == "":
        raise TushareFetcherError(FND_TUSHARE_TOKEN_MISSING, "tushare token missing")
    if timeout_ms < 1:
        raise TushareFetcherError(FND_TUSHARE_TIMEOUT_INVALID, "timeout_ms must be >= 1")
    if api_name not in set(list_tushare_v1_api_names()):
        raise TushareFetcherError(FND_TUSHARE_API_NOT_APPROVED, f"api not approved: {api_name}")

    pro = create_tushare_pro(token=token, tushare_module=import_module("tushare"))
    call = _resolve_tushare_api(pro=pro, api_name=api_name)
    return _run_with_timeout(lambda: call(**parameters), timeout_ms=timeout_ms)


class TushareFetcher:
    def __init__(
        self,
        config: FundamentalDataConfig,
        *,
        invoke_api: Callable[[str, str, dict[str, str], int], Any] = invoke_tushare_api_by_dispatch,
        sleep_fn: Callable[[float], None] = time.sleep,
        raw_writer: Callable[[RawPayloadWriteInput], RawPayloadWriteResult] = write_raw_payload_to_openviking,
    ) -> None:
        self.config = config
        self._invoke_api = invoke_api
        self._sleep_fn = sleep_fn
        self._raw_writer = raw_writer

    def Fetch(self, input_data: NormalizedInput, spec: ApiCallSpec) -> ProviderResult:
        started_perf = time.perf_counter()
        started_at = _now_iso()
        request_params = _sanitize_request_params(spec.api_name, spec.parameters)

        if self.config.tushare_token is None or self.config.tushare_token.strip() == "":
            return _build_result(
                attempt=_build_attempt(
                    spec=spec,
                    started_at=started_at,
                    started_perf=started_perf,
                    status="skipped",
                    reason="missing_token",
                    retry_count=0,
                    request_params=request_params,
                    input_data=input_data,
                    response_row_count=0,
                    response_col_count=0,
                    error_type="missing_token",
                    error_message="TUSHARE_TOKEN missing",
                ),
                schema_changed=False,
            )

        dataframe: Any | None = None
        retry_used = 0
        last_error: Exception | None = None
        for retry_used in range(max(0, spec.retry_limit) + 1):
            try:
                dataframe = self._invoke_api(
                    self.config.tushare_token,
                    spec.api_name,
                    dict(spec.parameters),
                    spec.timeout_ms,
                )
                last_error = None
                break
            except TimeoutError as err:
                last_error = err
                if retry_used < max(0, spec.retry_limit):
                    self._sleep_fn(0.5)
                    continue
                return _build_result(
                    attempt=_build_attempt(
                        spec=spec,
                        started_at=started_at,
                        started_perf=started_perf,
                        status="timeout",
                        reason="timeout",
                        retry_count=retry_used,
                        request_params=request_params,
                        input_data=input_data,
                        response_row_count=0,
                        response_col_count=0,
                        error_type="timeout",
                        error_message=str(err),
                    ),
                    schema_changed=False,
                )
            except Exception as err:  # noqa: BLE001
                last_error = err
                error_type, reason = _classify_tushare_error(err)
                return _build_result(
                    attempt=_build_attempt(
                        spec=spec,
                        started_at=started_at,
                        started_perf=started_perf,
                        status="error",
                        reason=reason,
                        retry_count=retry_used,
                        request_params=request_params,
                        input_data=input_data,
                        response_row_count=0,
                        response_col_count=0,
                        error_type=error_type,
                        error_message=str(err),
                    ),
                    schema_changed=False,
                )

        if dataframe is None:
            provider_error_message = "tushare fetch failed without dataframe"
            if last_error is not None:
                provider_error_message = str(last_error)
            return _build_result(
                attempt=_build_attempt(
                    spec=spec,
                    started_at=started_at,
                    started_perf=started_perf,
                    status="error",
                    reason="provider_error",
                    retry_count=retry_used,
                    request_params=request_params,
                    input_data=input_data,
                    response_row_count=0,
                    response_col_count=0,
                    error_type="provider_error",
                    error_message=provider_error_message,
                ),
                schema_changed=False,
            )

        row_count, col_count = _shape_of_dataframe(dataframe)
        if bool(getattr(dataframe, "empty", False)):
            return _build_result(
                attempt=_build_attempt(
                    spec=spec,
                    started_at=started_at,
                    started_perf=started_perf,
                    status="empty",
                    reason="empty_response",
                    retry_count=retry_used,
                    request_params=request_params,
                    input_data=input_data,
                    response_row_count=row_count,
                    response_col_count=col_count,
                    error_type=None,
                    error_message=None,
                ),
                schema_changed=False,
            )

        columns = _extract_columns(dataframe)
        validation = ValidateTushareColumns(spec.api_name, columns)
        if validation.is_schema_changed:
            missing = ",".join(validation.missing_columns)
            return _build_result(
                attempt=_build_attempt(
                    spec=spec,
                    started_at=started_at,
                    started_perf=started_perf,
                    status="schema_invalid",
                    reason="schema_changed",
                    retry_count=retry_used,
                    request_params=request_params,
                    input_data=input_data,
                    response_row_count=row_count,
                    response_col_count=col_count,
                    error_type="schema_invalid",
                    error_message=f"missing_columns={missing}",
                ),
                schema_changed=True,
            )

        planned_seq = reserve_provider_attempt_seq_v1(
            run_id=input_data.run_id,
            dispatch_id=input_data.dispatch_id,
            provider="tushare",
            api_name=spec.api_name,
        )
        raw_write = self._raw_writer(
            RawPayloadWriteInput(
                provider="tushare",
                api_name=spec.api_name,
                run_id=input_data.run_id,
                dispatch_id=input_data.dispatch_id,
                worker_id="fundamental_analyst",
                attempt_seq=planned_seq,
                payload=_dataframe_to_records(dataframe),
                timeout_ms=RAW_PAYLOAD_WRITER_TIMEOUT_MS_V1,
                retry_limit=RAW_PAYLOAD_WRITER_RETRY_LIMIT_V1,
            )
        )
        if not raw_write.ok:
            return _build_result(
                attempt=_build_attempt(
                    spec=spec,
                    started_at=started_at,
                    started_perf=started_perf,
                    status="error",
                    reason="raw_payload_write_failed",
                    retry_count=retry_used,
                    request_params=request_params,
                    input_data=input_data,
                    response_row_count=row_count,
                    response_col_count=col_count,
                    error_type=raw_write.error_type,
                    error_message=raw_write.error_message_redacted,
                    attempt_seq=planned_seq,
                ),
                schema_changed=False,
            )
        if not _is_valid_raw_write_receipt(raw_write):
            return _build_result(
                attempt=_build_attempt(
                    spec=spec,
                    started_at=started_at,
                    started_perf=started_perf,
                    status="error",
                    reason="raw_payload_write_invalid_receipt",
                    retry_count=retry_used,
                    request_params=request_params,
                    input_data=input_data,
                    response_row_count=row_count,
                    response_col_count=col_count,
                    error_type="raw_payload_write_invalid_receipt",
                    error_message="raw writer returned invalid success receipt",
                    attempt_seq=planned_seq,
                ),
                schema_changed=False,
            )

        extracted_fields = map_tushare_columns_to_pack_fields_v1(
            spec.api_name,
            dataframe,
            raw_write.content_hash or "",
            raw_write.uri or "",
        )
        latest_row = _select_latest_relevant_row(_dataframe_to_records(dataframe))
        report_period = _extract_text_from_row(latest_row, "end_date") or spec.parameters.get("period")
        announce_date = _extract_text_from_row(latest_row, "ann_date")
        as_of = _extract_text_from_row(latest_row, "trade_date") or input_data.current_date
        field_coverage = sorted({field_path for field_path, _payload, _src in extracted_fields})
        return _build_result(
            attempt=_build_attempt(
                spec=spec,
                started_at=started_at,
                started_perf=started_perf,
                status="success",
                reason=None,
                retry_count=retry_used,
                request_params=request_params,
                input_data=input_data,
                response_row_count=row_count,
                response_col_count=col_count,
                error_type=None,
                error_message=None,
                attempt_seq=raw_write.attempt_seq,
                field_coverage=field_coverage,
                report_period=report_period,
                announce_date=announce_date,
                as_of=as_of,
                fetched_at=_now_iso(),
                raw_payload_hash=raw_write.content_hash,
                raw_payload_ref=raw_write.uri,
            ),
            schema_changed=False,
            raw_payload_hash=raw_write.content_hash,
            raw_payload_ref=raw_write.uri,
            extracted_fields=extracted_fields,
        )


def map_tushare_columns_to_pack_fields_v1(
    api_name: str,
    dataframe: Any,
    ref_hash: str,
    ref_uri: str,
) -> list[tuple[str, Any, str]]:
    records = _dataframe_to_records(dataframe)
    if not records:
        return []
    rules = tuple(
        item for item in CN_A_FUNDAMENTAL_FIELD_MAPPING_V1 if item.provider == "tushare" and item.api_name == api_name
    )
    if not rules:
        return []

    target_row = _select_latest_relevant_row(records)
    if target_row is None:
        return []

    extracted: list[tuple[str, Any, str]] = []
    for rule in rules:
        if rule.source_column not in target_row:
            continue
        normalized_value = _normalize_tushare_value(target_row.get(rule.source_column))
        if normalized_value is None:
            continue
        value_payload = {
            "value": normalized_value,
            "unit": rule.unit_scale,
            "scale": "x",
            "provider": "tushare",
            "api_name": api_name,
            "payload_hash": ref_hash,
            "raw_payload_ref": ref_uri,
        }
        extracted.append((rule.pack_field_path, value_payload, rule.source_ref_path))
    return extracted


def _build_result(
    *,
    attempt: ProviderAttempt,
    schema_changed: bool,
    raw_payload_hash: str | None = None,
    raw_payload_ref: str | None = None,
    extracted_fields: list[tuple[str, Any, str]] | None = None,
) -> ProviderResult:
    return ProviderResult(
        attempt=attempt,
        raw_payload_hash=raw_payload_hash,
        raw_payload_ref=raw_payload_ref,
        extracted_fields=extracted_fields or [],
        schema_changed=schema_changed,
    )


def _build_attempt(
    *,
    spec: ApiCallSpec,
    started_at: str,
    started_perf: float,
    status: str,
    reason: str | None,
    retry_count: int,
    request_params: dict[str, str],
    input_data: NormalizedInput,
    response_row_count: int,
    response_col_count: int,
    error_type: str | None,
    error_message: str | None,
    attempt_seq: int | None = None,
    field_coverage: list[str] | None = None,
    report_period: str | None = None,
    announce_date: str | None = None,
    as_of: str | None = None,
    fetched_at: str | None = None,
    raw_payload_hash: str | None = None,
    raw_payload_ref: str | None = None,
) -> ProviderAttempt:
    return ProviderAttempt(
        provider="tushare",
        role=spec.role,
        api_name=spec.api_name,
        attempt_seq=attempt_seq,
        status=status,  # type: ignore[arg-type]
        reason=reason,
        started_at=started_at,
        ended_at=_now_iso(),
        duration_ms=max(0, int((time.perf_counter() - started_perf) * 1000)),
        retry_count=max(0, retry_count),
        request_params_redacted=request_params,
        response_row_count=max(0, response_row_count),
        response_col_count=max(0, response_col_count),
        field_coverage=field_coverage or [],
        report_period=report_period if report_period is not None else spec.parameters.get("period"),
        announce_date=announce_date,
        as_of=as_of if as_of is not None else input_data.current_date,
        fetched_at=fetched_at if fetched_at is not None else _now_iso(),
        raw_payload_hash=raw_payload_hash,
        raw_payload_ref=raw_payload_ref,
        error_type=error_type,
        error_message_redacted=sanitize_error(error_message) if error_message else None,
    )


def _resolve_tushare_api(pro: Any, api_name: str) -> Callable[..., Any]:
    dispatch_table: dict[str, Callable[..., Any]] = {
        "stock_company": pro.stock_company,
        "stock_basic": pro.stock_basic,
        "daily_basic": pro.daily_basic,
        "fina_indicator": pro.fina_indicator,
        "income": pro.income,
        "balancesheet": pro.balancesheet,
        "cashflow": pro.cashflow,
        "fina_mainbz": pro.fina_mainbz,
        "dividend": pro.dividend,
        "top10_holders": pro.top10_holders,
        "top10_floatholders": pro.top10_floatholders,
    }
    if api_name not in dispatch_table:
        raise TushareFetcherError(FND_TUSHARE_API_NOT_APPROVED, f"api not approved: {api_name}")
    return dispatch_table[api_name]


def _shape_of_dataframe(dataframe: Any) -> tuple[int, int]:
    shape = getattr(dataframe, "shape", None)
    if isinstance(shape, tuple) and len(shape) == 2:
        row_count = int(shape[0]) if shape[0] is not None else 0
        col_count = int(shape[1]) if shape[1] is not None else 0
        return max(0, row_count), max(0, col_count)
    columns = _extract_columns(dataframe)
    records = _dataframe_to_records(dataframe)
    return len(records), len(columns)


def _extract_columns(dataframe: Any) -> list[str]:
    columns_obj = getattr(dataframe, "columns", [])
    if hasattr(columns_obj, "tolist"):
        raw = columns_obj.tolist()
    else:
        raw = list(columns_obj)
    return [str(item) for item in raw]


def _dataframe_to_records(dataframe: Any) -> list[dict[str, Any]]:
    if dataframe is None:
        return []
    if hasattr(dataframe, "to_dict"):
        to_dict = getattr(dataframe, "to_dict")
        if callable(to_dict):
            try:
                records = to_dict("records")
                if isinstance(records, list):
                    normalized: list[dict[str, Any]] = []
                    for item in records:
                        if isinstance(item, Mapping):
                            normalized.append(dict(item))
                    return normalized
            except Exception:  # noqa: BLE001
                pass
    rows = getattr(dataframe, "_rows", None)
    if isinstance(rows, list):
        normalized_rows: list[dict[str, Any]] = []
        for item in rows:
            if isinstance(item, Mapping):
                normalized_rows.append(dict(item))
        return normalized_rows
    if isinstance(dataframe, Mapping):
        return [dict(dataframe)]
    if isinstance(dataframe, Sequence) and not isinstance(dataframe, (str, bytes, bytearray)):
        normalized_seq: list[dict[str, Any]] = []
        for item in dataframe:
            if isinstance(item, Mapping):
                normalized_seq.append(dict(item))
        return normalized_seq
    return []


def _select_latest_relevant_row(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not records:
        return None
    for key in ("end_date", "trade_date", "ann_date"):
        candidates: list[tuple[datetime, int, dict[str, Any]]] = []
        for idx, row in enumerate(records):
            parsed = _parse_date_value(row.get(key))
            if parsed is None:
                continue
            candidates.append((parsed, idx, row))
        if candidates:
            candidates.sort(key=lambda item: (item[0], -item[1]), reverse=True)
            return candidates[0][2]
    return records[0]


def _parse_date_value(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value

    text = str(value).strip()
    if text == "":
        return None
    for fmt in (
        "%Y%m%d",
        "%Y-%m-%d",
        "%Y/%m/%d",
        "%Y.%m.%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    digits = "".join(ch for ch in text if ch.isdigit())
    if len(digits) >= 8:
        try:
            return datetime.strptime(digits[:8], "%Y%m%d")
        except ValueError:
            return None
    return None


def _normalize_tushare_value(value: Any) -> Any | None:
    if value is None:
        return None
    scalar = value
    if hasattr(scalar, "item"):
        try:
            scalar = scalar.item()
        except Exception:  # noqa: BLE001
            pass
    if isinstance(scalar, bool):
        return None
    if isinstance(scalar, float) and scalar != scalar:
        return None
    if isinstance(scalar, str):
        trimmed = scalar.strip()
        return trimmed if trimmed != "" else None
    text = str(scalar).strip()
    if text == "" or text.lower() == "nan":
        return None
    return scalar


def _extract_text_from_row(row: Mapping[str, Any] | None, key: str) -> str | None:
    if row is None:
        return None
    value = _normalize_tushare_value(row.get(key))
    if value is None:
        return None
    text = str(value).strip()
    return text if text != "" else None


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


def _sanitize_request_params(api_name: str, params: dict[str, str]) -> dict[str, str]:
    redacted = {"api_name": api_name}
    for key, value in params.items():
        if key not in _REQUEST_PARAM_ALLOWLIST:
            continue
        redacted[key] = str(value)
    return redacted


def _classify_tushare_error(err: Exception) -> tuple[str, str]:
    text = str(err).lower()
    if any(hint in text for hint in _QUOTA_HINTS):
        return "quota_exceeded", "permission_or_quota"
    if any(hint in text for hint in _PERMISSION_HINTS):
        return "permission_denied", "permission_or_quota"
    return "provider_error", "provider_error"


def _run_with_timeout(call: Callable[[], Any], *, timeout_ms: int) -> Any:
    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(call)
        try:
            return future.result(timeout=max(timeout_ms, 1) / 1000.0)
        except FuturesTimeoutError as exc:
            future.cancel()
            raise TimeoutError("tushare call timeout") from exc


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
