from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable, Mapping

SOCIAL_INVALID_INPUT = "SOCIAL_INVALID_INPUT"
SOCIAL_INVALID_TICKER_MARKET_PREFIX = "SOCIAL_INVALID_TICKER_MARKET_PREFIX"
SOCIAL_PROFILE_REF_INVALID = "SOCIAL_PROFILE_REF_INVALID"
SOCIAL_ALIAS_UNAPPROVED = "SOCIAL_ALIAS_UNAPPROVED"


class SocialProfileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class NormalizedCnATicker:
    ticker_plain: str
    exchange: str
    exchange_suffix: str
    ticker: str


@dataclass(frozen=True)
class DateWindow:
    start_date: str
    end_date: str
    as_of_date: str


@dataclass(frozen=True)
class ApprovedAlias:
    alias: str
    source_ref: str
    source_hash: str


@dataclass(frozen=True)
class SocialToolInput:
    ticker: str
    market: str
    company_name: str | None = None
    industry: str | None = None
    start_date: str | None = None
    end_date: str | None = None
    approved_artifact_refs: tuple[object, ...] = ()
    aliases: tuple[str, ...] = ()


@dataclass(frozen=True)
class SocialTargetProfile:
    ticker: str
    ticker_plain: str
    eastmoney_symbol: str
    company_name: str | None
    market: str
    industry: str | None
    approved_aliases: tuple[ApprovedAlias, ...]
    warnings: tuple[str, ...]


def NormalizeCnATicker(raw_ticker: str) -> NormalizedCnATicker:
    ticker = raw_ticker.strip().upper()
    if ticker == "":
        raise SocialProfileError(SOCIAL_INVALID_TICKER_MARKET_PREFIX, "ticker 不能为空")

    if "." in ticker:
        plain, suffix = ticker.split(".", 1)
        if len(plain) == 6 and plain.isdigit() and suffix in {"SH", "SZ"}:
            exchange = suffix
        else:
            raise SocialProfileError(
                SOCIAL_INVALID_TICKER_MARKET_PREFIX,
                f"无法识别 ticker: {raw_ticker}",
            )
    elif len(ticker) == 8 and ticker[:2] in {"SH", "SZ"} and ticker[2:].isdigit():
        exchange = ticker[:2]
        plain = ticker[2:]
    elif len(ticker) == 6 and ticker.isdigit():
        plain = ticker
        exchange = _infer_exchange_from_plain_ticker(plain)
    else:
        raise SocialProfileError(
            SOCIAL_INVALID_TICKER_MARKET_PREFIX,
            f"无法识别 ticker: {raw_ticker}",
        )

    exchange_suffix = f".{exchange}"
    return NormalizedCnATicker(
        ticker_plain=plain,
        exchange=exchange,
        exchange_suffix=exchange_suffix,
        ticker=f"{plain}{exchange_suffix}",
    )


def ToEastmoneySymbol(ticker_norm: NormalizedCnATicker) -> str:
    if ticker_norm.exchange == "SH":
        return f"100.{ticker_norm.ticker_plain}"
    if ticker_norm.exchange == "SZ":
        return f"0.{ticker_norm.ticker_plain}"
    raise SocialProfileError(
        SOCIAL_INVALID_TICKER_MARKET_PREFIX,
        f"无法识别交易所: {ticker_norm.exchange}",
    )


def ResolveDateWindow(start_date: str | None, end_date: str | None, current_time: str) -> DateWindow:
    run_date = _parse_run_date(current_time)
    resolved_end = _parse_date_or_default(end_date, run_date, "end_date")
    resolved_start = _parse_date_or_default(start_date, resolved_end - timedelta(days=7), "start_date")
    if resolved_start > resolved_end:
        raise SocialProfileError(
            SOCIAL_INVALID_INPUT,
            f"start_date({resolved_start.isoformat()}) 不能晚于 end_date({resolved_end.isoformat()})",
        )
    return DateWindow(
        start_date=resolved_start.isoformat(),
        end_date=resolved_end.isoformat(),
        as_of_date=resolved_end.isoformat(),
    )


def resolve_social_target_profile(tool_input: SocialToolInput) -> SocialTargetProfile:
    ticker_norm = NormalizeCnATicker(tool_input.ticker)
    approved_aliases, approved_company_name = _load_approved_aliases_and_company(tool_input.approved_artifact_refs)
    company_name = _normalize_optional_text(tool_input.company_name) or approved_company_name
    industry = _normalize_optional_text(tool_input.industry)
    warnings: list[str] = []
    if company_name is None and not approved_aliases:
        warnings.append("company_name_and_alias_missing")
    return SocialTargetProfile(
        ticker=ticker_norm.ticker,
        ticker_plain=ticker_norm.ticker_plain,
        eastmoney_symbol=ToEastmoneySymbol(ticker_norm),
        company_name=company_name,
        market=tool_input.market.strip().upper(),
        industry=industry,
        approved_aliases=approved_aliases,
        warnings=tuple(warnings),
    )


def build_query_keywords(profile: SocialTargetProfile) -> tuple[str, ...]:
    exchange_ticker = f"{profile.ticker_plain}{profile.ticker[6:]}"
    prefixed_ticker = f"{profile.ticker[7:]}{profile.ticker_plain}"
    candidates: list[str | None] = [
        profile.ticker,
        profile.ticker_plain,
        exchange_ticker,
        prefixed_ticker,
        profile.company_name,
    ]
    candidates.extend(alias.alias for alias in profile.approved_aliases)
    keywords: list[str] = []
    seen: set[str] = set()
    for raw_value in candidates:
        value = _normalize_optional_text(raw_value)
        if value is None or value in seen:
            continue
        seen.add(value)
        keywords.append(value)
    return tuple(keywords)


def _infer_exchange_from_plain_ticker(ticker_plain: str) -> str:
    if ticker_plain[0] in {"5", "6", "9"}:
        return "SH"
    if ticker_plain[0] in {"0", "1", "2", "3"}:
        return "SZ"
    raise SocialProfileError(
        SOCIAL_INVALID_TICKER_MARKET_PREFIX,
        f"无法从代码前缀识别交易所: {ticker_plain}",
    )


def _parse_run_date(current_time: str) -> date:
    value = current_time.strip()
    if value.endswith("Z"):
        value = f"{value[:-1]}+00:00"
    try:
        return datetime.fromisoformat(value).date()
    except ValueError as exc:
        raise SocialProfileError(SOCIAL_INVALID_INPUT, "current_time 不是合法 ISO 时间") from exc


def _parse_date_or_default(raw_value: str | None, default_value: date, field_name: str) -> date:
    if raw_value is None:
        return default_value
    value = raw_value.strip()
    if value == "":
        return default_value
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise SocialProfileError(SOCIAL_INVALID_INPUT, f"{field_name} 不是合法日期") from exc


def _load_approved_aliases_and_company(
    approved_artifact_refs: Iterable[object],
) -> tuple[tuple[ApprovedAlias, ...], str | None]:
    aliases: list[ApprovedAlias] = []
    seen_aliases: set[str] = set()
    approved_company_name: str | None = None

    for index, raw_ref in enumerate(approved_artifact_refs):
        ref = _parse_approved_ref(raw_ref, index=index)
        material = _read_approved_material(ref["artifact_ref"])
        if material.get("approved") is not True:
            raise SocialProfileError(
                SOCIAL_PROFILE_REF_INVALID,
                f"approved ref 未通过批准状态校验: {ref['artifact_ref']}",
            )
        actual_hash = _canonical_json_sha256(material)
        if actual_hash != ref["content_hash"]:
            raise SocialProfileError(
                SOCIAL_PROFILE_REF_INVALID,
                (
                    "approved ref content hash 不匹配: "
                    f"ref={ref['artifact_ref']} expected={ref['content_hash']} actual={actual_hash}"
                ),
            )

        if ref["kind"] not in {"profile", "company_identity"}:
            continue

        if approved_company_name is None:
            approved_company_name = _extract_company_name(material)
        for alias_text in _extract_aliases(material):
            if alias_text in seen_aliases:
                continue
            seen_aliases.add(alias_text)
            aliases.append(
                ApprovedAlias(
                    alias=alias_text,
                    source_ref=ref["artifact_ref"],
                    source_hash=ref["content_hash"],
                )
            )

    return tuple(aliases), approved_company_name


def _parse_approved_ref(raw_ref: object, *, index: int) -> Mapping[str, str]:
    if not isinstance(raw_ref, dict):
        raise SocialProfileError(
            SOCIAL_PROFILE_REF_INVALID,
            f"approved ref 必须是对象: index={index}",
        )
    kind = _normalize_optional_text(raw_ref.get("kind"))
    artifact_ref = _normalize_optional_text(raw_ref.get("artifact_ref"))
    content_hash = _normalize_optional_text(raw_ref.get("content_hash"))
    if kind is None or artifact_ref is None or content_hash is None:
        raise SocialProfileError(
            SOCIAL_PROFILE_REF_INVALID,
            f"approved ref 缺少 kind/artifact_ref/content_hash: index={index}",
        )
    return {"kind": kind, "artifact_ref": artifact_ref, "content_hash": content_hash}


def _read_approved_material(artifact_ref: str) -> dict[str, Any]:
    path = Path(artifact_ref)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise SocialProfileError(
            SOCIAL_PROFILE_REF_INVALID,
            f"approved ref 读取失败: {artifact_ref} ({exc})",
        ) from exc
    if not isinstance(payload, dict):
        raise SocialProfileError(
            SOCIAL_PROFILE_REF_INVALID,
            f"approved ref 内容必须为 JSON object: {artifact_ref}",
        )
    return payload


def _extract_company_name(material: Mapping[str, Any]) -> str | None:
    for key in ("company_name", "company", "name"):
        value = _normalize_optional_text(material.get(key))
        if value is not None:
            return value
    return None


def _extract_aliases(material: Mapping[str, Any]) -> tuple[str, ...]:
    aliases: list[str] = []
    for key in ("approved_aliases", "aliases"):
        raw_aliases = material.get(key)
        if not isinstance(raw_aliases, list):
            continue
        for raw_alias in raw_aliases:
            alias = _normalize_optional_text(raw_alias)
            if alias is not None:
                aliases.append(alias)
    unique_aliases: list[str] = []
    seen: set[str] = set()
    for alias in aliases:
        if alias in seen:
            continue
        seen.add(alias)
        unique_aliases.append(alias)
    return tuple(unique_aliases)


def _canonical_json_sha256(payload: Any) -> str:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _normalize_optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized == "":
        return None
    return normalized
