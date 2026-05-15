from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any, Mapping
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .errors import (
    DATE_INVALID,
    L2_TARGET_INVALID,
    MARKET_INVALID,
    MONGO_CONFIG_INVALID,
    TICKER_INVALID,
    TOOL_CONTEXT_INCOMPLETE,
    TOOL_PARAMS_INVALID,
    URL_QUERY_INVALID,
    FrontlineValidationError,
)
from .runtime_context import ToolRuntimeContext


MAX_PROVIDER_ERROR_SUMMARY_LENGTH = 500
REDACTED = "***"

_TICKER_PLAIN_RE = re.compile(r"^\d{6}$")
_TICKER_SUFFIX_RE = re.compile(r"^(?P<code>\d{6})\.(?P<exchange>SH|SZ)$")
_TICKER_PREFIX_RE = re.compile(r"^(?P<exchange>SH|SZ)(?P<code>\d{6})$")
_HK_TICKER_SUFFIX_RE = re.compile(r"^(?P<code>\d{5})\.HK$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_URL_RE = re.compile(r"(?P<url>[a-zA-Z][a-zA-Z0-9+.-]*://[^\s'\"<>]+)")
_REDACTED_URL_WITH_SCHEME_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://<redacted-url>")
_LONG_ALNUM_RE = re.compile(r"\b(?P<value>[A-Za-z0-9]{20,})\b")

_SENSITIVE_INLINE_KEYS = (
    "authorization",
    "api_key",
    "apikey",
    "token",
    "password",
    "passwd",
    "signature",
    "sign",
)
_SENSITIVE_QUERY_KEYS = frozenset(
    {
        "api_key",
        "apikey",
        "token",
        "password",
        "passwd",
        "key",
        "signature",
        "sign",
    }
)
_KEY_PATTERN = "|".join(sorted(_SENSITIVE_INLINE_KEYS, key=len, reverse=True))
_KV_EQUALS_RE = re.compile(
    rf"(?i)\b(?P<key>{_KEY_PATTERN})\b(?P<sep>\s*=\s*)(?P<value>[^\s,;&]+)"
)
_KV_COLON_RE = re.compile(
    rf"(?i)\b(?P<key>{_KEY_PATTERN})\b(?P<sep>\s*:\s*)(?P<value>[^\s,;\n\r]+)"
)


@dataclass(frozen=True)
class ProviderQuery:
    ticker: str
    market: str
    start_date: str
    end_date: str
    company_name: str | None = None
    industry: str | None = None
    aliases: tuple[str, ...] = ()


def validate_tool_params(params: object) -> dict[str, Any]:
    if not isinstance(params, Mapping):
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, "tool params 必须是 JSON object")
    return dict(params)


def validate_runtime_context(payload: Mapping[str, Any]) -> ToolRuntimeContext:
    try:
        return ToolRuntimeContext.from_mapping(payload)
    except ValueError as exc:
        raise FrontlineValidationError(TOOL_CONTEXT_INCOMPLETE, str(exc)) from exc


def normalize_ticker(ticker: str) -> str:
    if not isinstance(ticker, str) or not ticker.strip():
        raise FrontlineValidationError(TICKER_INVALID, "ticker 不能为空")
    text = ticker.strip().upper()

    if _TICKER_PLAIN_RE.fullmatch(text):
        code = text
        exchange = _infer_cn_a_exchange(code)
        return f"{code}.{exchange}"

    suffix_match = _TICKER_SUFFIX_RE.fullmatch(text)
    if suffix_match is not None:
        code = suffix_match.group("code")
        exchange = suffix_match.group("exchange")
        expected_exchange = _infer_cn_a_exchange(code)
        if exchange != expected_exchange:
            raise FrontlineValidationError(
                TICKER_INVALID,
                f"ticker 交易所与代码段不匹配，期望 {expected_exchange}",
            )
        return f"{code}.{exchange}"

    hk_suffix_match = _HK_TICKER_SUFFIX_RE.fullmatch(text)
    if hk_suffix_match is not None:
        return f"{hk_suffix_match.group('code')}.HK"

    prefix_match = _TICKER_PREFIX_RE.fullmatch(text)
    if prefix_match is not None:
        code = prefix_match.group("code")
        exchange = prefix_match.group("exchange")
        expected_exchange = _infer_cn_a_exchange(code)
        if exchange != expected_exchange:
            raise FrontlineValidationError(
                TICKER_INVALID,
                f"ticker 交易所与代码段不匹配，期望 {expected_exchange}",
            )
        return f"{code}.{exchange}"

    raise FrontlineValidationError(
        TICKER_INVALID,
        "ticker 只接受 6 位代码、NNNNNN.SH/SZ、SH/SZNNNNNN 或 NNNNN.HK",
    )


def validate_market(market: str) -> str:
    if not isinstance(market, str) or not market.strip():
        raise FrontlineValidationError(MARKET_INVALID, "market 不能为空")
    normalized = market.strip().upper()
    if normalized not in {"CN_A", "HK"}:
        raise FrontlineValidationError(MARKET_INVALID, "当前只支持 market=CN_A 或 market=HK")
    return normalized


def validate_date(date_text: str, *, field_name: str) -> str:
    if not isinstance(date_text, str) or not date_text.strip():
        raise FrontlineValidationError(DATE_INVALID, f"{field_name} 不能为空")
    text = date_text.strip()
    if _DATE_RE.fullmatch(text) is None:
        raise FrontlineValidationError(DATE_INVALID, f"{field_name} 必须是 YYYY-MM-DD")
    try:
        datetime.strptime(text, "%Y-%m-%d")
    except ValueError as exc:
        raise FrontlineValidationError(DATE_INVALID, f"{field_name} 不是合法日期") from exc
    return text


def validate_date_range(start_date: str, end_date: str) -> tuple[str, str]:
    start = validate_date(start_date, field_name="start_date")
    end = validate_date(end_date, field_name="end_date")
    if end < start:
        raise FrontlineValidationError(DATE_INVALID, "end_date 不能早于 start_date")
    return start, end


def build_cn_a_provider_query(params: object) -> ProviderQuery:
    payload = validate_tool_params(params)
    ticker_raw = _require_string(payload, "ticker")
    market_raw = _require_string(payload, "market")
    start_date_raw = _require_string(payload, "start_date")
    end_date_raw = _require_string(payload, "end_date")
    company_name = _read_optional_string(payload, "company_name")
    industry = _read_optional_string(payload, "industry")
    aliases = _read_aliases(payload.get("aliases"))

    ticker = normalize_ticker(ticker_raw)
    market = validate_market(market_raw)
    start_date, end_date = validate_date_range(start_date_raw, end_date_raw)
    return ProviderQuery(
        ticker=ticker,
        market=market,
        start_date=start_date,
        end_date=end_date,
        company_name=company_name,
        industry=industry,
        aliases=aliases,
    )


def validate_mongodb_uri(uri: str) -> str:
    if not isinstance(uri, str) or not uri.strip():
        raise FrontlineValidationError(MONGO_CONFIG_INVALID, "MongoDB URI 不能为空")
    text = uri.strip()
    try:
        parsed = urlsplit(text)
    except ValueError as exc:
        raise FrontlineValidationError(MONGO_CONFIG_INVALID, "MongoDB URI 非法") from exc
    if parsed.scheme not in {"mongodb", "mongodb+srv"}:
        raise FrontlineValidationError(
            MONGO_CONFIG_INVALID,
            "MongoDB URI 必须以 mongodb:// 或 mongodb+srv:// 开头",
        )
    database = parsed.path.strip("/")
    if not database or "/" in database:
        raise FrontlineValidationError(
            MONGO_CONFIG_INVALID,
            "MongoDB URI 必须在 path 中提供 database 名称",
        )
    if parsed.username is not None or parsed.password is not None:
        query_pairs = dict(parse_qsl(parsed.query, keep_blank_values=True))
        auth_source = query_pairs.get("authSource")
        if auth_source is None or not auth_source.strip():
            raise FrontlineValidationError(
                MONGO_CONFIG_INVALID,
                "MongoDB URI 含认证信息时必须显式提供 authSource",
            )
    return database


def validate_l2_target_path(relative_path: str) -> str:
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise FrontlineValidationError(L2_TARGET_INVALID, "L2 target path 不能为空")
    candidate = relative_path.strip()
    if "\\" in candidate:
        raise FrontlineValidationError(L2_TARGET_INVALID, "L2 target path 不允许反斜杠")
    if candidate.startswith("/"):
        raise FrontlineValidationError(L2_TARGET_INVALID, "L2 target path 必须为相对路径")
    path = PurePosixPath(candidate)
    if any(part in {"", ".", ".."} for part in path.parts):
        raise FrontlineValidationError(L2_TARGET_INVALID, "L2 target path 不允许 . 或 ..")
    return path.as_posix()


def validate_url_query(query: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(query, str):
        raise FrontlineValidationError(URL_QUERY_INVALID, "URL query 必须是字符串")
    text = query.strip()
    if text == "":
        return ()
    if any(ord(char) < 32 or ord(char) == 127 for char in text):
        raise FrontlineValidationError(URL_QUERY_INVALID, "URL query 含控制字符")
    try:
        pairs = parse_qsl(text, keep_blank_values=True, strict_parsing=False)
    except ValueError as exc:
        raise FrontlineValidationError(URL_QUERY_INVALID, "URL query 非法") from exc
    for key, _ in pairs:
        if key.strip() == "":
            raise FrontlineValidationError(URL_QUERY_INVALID, "URL query 不允许空 key")
    return tuple(pairs)


def redact_secret(text: object) -> str:
    redacted = str(text)
    redacted = _URL_RE.sub(_replace_url, redacted)
    redacted = _KV_EQUALS_RE.sub(r"\g<key>\g<sep>***", redacted)
    redacted = _KV_COLON_RE.sub(r"\g<key>\g<sep>***", redacted)
    redacted = _LONG_ALNUM_RE.sub(_mask_long_alnum, redacted)
    return redacted


def summarize_provider_error(text: object) -> str:
    summary = redact_secret(text)
    summary = _REDACTED_URL_WITH_SCHEME_RE.sub("<redacted-url>", summary)
    if len(summary) > MAX_PROVIDER_ERROR_SUMMARY_LENGTH:
        return summary[:MAX_PROVIDER_ERROR_SUMMARY_LENGTH]
    return summary


def _infer_cn_a_exchange(code: str) -> str:
    if code.startswith("6"):
        return "SH"
    if code.startswith(("0", "3")):
        return "SZ"
    raise FrontlineValidationError(TICKER_INVALID, "ticker 目前仅支持以 0/3/6 开头的 CN_A 股票代码")


def _require_string(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value.strip()
    raise FrontlineValidationError(TOOL_PARAMS_INVALID, f"params.{key} 必须是非空字符串")


def _read_optional_string(payload: Mapping[str, Any], key: str) -> str | None:
    value = payload.get(key)
    if value is None:
        return None
    if isinstance(value, str):
        trimmed = value.strip()
        return trimmed or None
    raise FrontlineValidationError(TOOL_PARAMS_INVALID, f"params.{key} 必须是字符串")


def _read_aliases(raw_value: object) -> tuple[str, ...]:
    if raw_value is None:
        return ()
    if not isinstance(raw_value, list):
        raise FrontlineValidationError(TOOL_PARAMS_INVALID, "params.aliases 必须是字符串数组")
    aliases: list[str] = []
    for item in raw_value:
        if not isinstance(item, str) or not item.strip():
            raise FrontlineValidationError(TOOL_PARAMS_INVALID, "params.aliases 只允许非空字符串")
        aliases.append(item.strip())
    return tuple(aliases)


def _replace_url(match: re.Match[str]) -> str:
    raw_url = match.group("url")
    try:
        split = urlsplit(raw_url)
    except ValueError:
        return "<redacted-url>"
    if split.scheme:
        return f"{split.scheme}://<redacted-url>"
    return "<redacted-url>"


def _redact_query_string(query: str) -> str:
    if query == "":
        return query
    pairs = parse_qsl(query, keep_blank_values=True)
    if not pairs:
        return query
    replaced_pairs: list[tuple[str, str]] = []
    changed = False
    for key, value in pairs:
        if _is_sensitive_query_key(key):
            replaced_pairs.append((key, REDACTED))
            changed = True
        else:
            replaced_pairs.append((key, value))
    if not changed:
        return query
    return urlencode(replaced_pairs, doseq=True)


def _is_sensitive_query_key(key: str) -> bool:
    normalized = key.strip().lower()
    if normalized in _SENSITIVE_QUERY_KEYS:
        return True
    if normalized.endswith("_token"):
        return True
    return False


def _mask_long_alnum(match: re.Match[str]) -> str:
    value = match.group("value")
    return f"{value[:4]}***{value[-2:]}"
