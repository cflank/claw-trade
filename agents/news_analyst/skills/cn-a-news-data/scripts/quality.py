from __future__ import annotations

from dataclasses import asdict, is_dataclass
import re
from typing import Any

from models import NewsItem, ProviderAttempt, Quality, QualityDecision, QualityInput
from observability import record_event, record_metric, trace_span

_P0_PROVIDER_ENDPOINTS: tuple[tuple[str, str], ...] = (
    ("akshare", "stock_news_em"),
    ("akshare", "stock_info_global_cls"),
)

_DIRECTIONAL_WARNING = "发现方向性字段或方向性判断迹象，资料包判定失败。"
_P0_FAILED_WARNING = "P0 数据源均失败，不能形成正式新闻判断资料包。"
_PARTIAL_WARNING = "不可用于公司方向性新闻判断。"
_NO_ACCEPTED_WARNING = "没有可接受新闻。"
_ALL_PROVIDER_FAILED_WARNING = "所有 provider 均失败或未返回有效结果。"

_DIRECTIONAL_KEY_TOKENS: tuple[str, ...] = (
    "direction",
    "directional",
    "sentiment",
    "judgment",
    "judgement",
    "recommend",
    "rating",
    "targetprice",
    "target_price",
    "buy",
    "sell",
    "bullish",
    "bearish",
    "longshort",
    "tradingsignal",
    "trade_signal",
    "stoploss",
    "stop_loss",
    "takeprofit",
    "take_profit",
    "position",
)

_DIRECTIONAL_CN_TOKENS: tuple[str, ...] = (
    "方向",
    "情绪",
    "看涨",
    "看跌",
    "买入",
    "卖出",
    "增持",
    "减持",
    "做多",
    "做空",
    "目标价",
    "止损",
    "止盈",
    "评级",
    "建议",
)

_DIRECTIONAL_VALUE_PATTERN = re.compile(
    r"(看涨|看跌|买入|卖出|增持|减持|做多|做空|目标价|止损|止盈|上调评级|下调评级"
    r"|recommend(?:ation)?|buy|sell|overweight|underweight|bullish|bearish|target\s*price)",
    re.IGNORECASE,
)

_VALUE_SCAN_PATH_SKIP_SUFFIXES = {
    "news_id",
    "raw_id",
    "url",
    "data_source",
    "source_fetch_time",
    "publish_time",
    "content_hash",
    "provider",
    "endpoint",
    "query",
}


class QualityGate:
    def evaluate(self, quality_input: QualityInput) -> QualityDecision:
        with trace_span("news_data_pack.quality"):
            company_direct_count = _count_by_buckets(quality_input.items, ("company_news", "announcements"))
            industry_background_count = _count_by_buckets(quality_input.items, ("industry_news",))
            policy_macro_count = _count_by_buckets(quality_input.items, ("policy_macro_news",))
            accepted_count = company_direct_count + industry_background_count + policy_macro_count

            missing_fields = _build_missing_fields(quality_input=quality_input, company_direct_count=company_direct_count)
            warnings: list[str] = []

            has_directional_signal = _contains_directional_signal(quality_input.items)
            if has_directional_signal:
                warnings.append(_DIRECTIONAL_WARNING)
                return _decision(
                    status="failed",
                    directional_judgment_allowed=False,
                    warnings=warnings,
                    quality_input=quality_input,
                    company_direct_count=company_direct_count,
                    industry_background_count=industry_background_count,
                    policy_macro_count=policy_macro_count,
                    accepted_count=accepted_count,
                    missing_fields=missing_fields,
                )

            p0_success_count = _count_p0_effective_success(quality_input.provider_attempts)
            p0_all_failed = p0_success_count == 0
            if p0_all_failed:
                warnings.append(_P0_FAILED_WARNING)
                return _decision(
                    status="failed",
                    directional_judgment_allowed=False,
                    warnings=warnings,
                    quality_input=quality_input,
                    company_direct_count=company_direct_count,
                    industry_background_count=industry_background_count,
                    policy_macro_count=policy_macro_count,
                    accepted_count=accepted_count,
                    missing_fields=missing_fields,
                )

            all_providers_failed = _all_providers_failed(quality_input.provider_attempts)
            if all_providers_failed:
                warnings.append(_ALL_PROVIDER_FAILED_WARNING)
                return _decision(
                    status="failed",
                    directional_judgment_allowed=False,
                    warnings=warnings,
                    quality_input=quality_input,
                    company_direct_count=company_direct_count,
                    industry_background_count=industry_background_count,
                    policy_macro_count=policy_macro_count,
                    accepted_count=accepted_count,
                    missing_fields=missing_fields,
                )

            if accepted_count == 0:
                warnings.append(_NO_ACCEPTED_WARNING)
                return _decision(
                    status="failed",
                    directional_judgment_allowed=False,
                    warnings=warnings,
                    quality_input=quality_input,
                    company_direct_count=company_direct_count,
                    industry_background_count=industry_background_count,
                    policy_macro_count=policy_macro_count,
                    accepted_count=accepted_count,
                    missing_fields=missing_fields,
                )

            if company_direct_count == 0 and (industry_background_count > 0 or policy_macro_count > 0):
                warnings.append(_PARTIAL_WARNING)
                return _decision(
                    status="partial",
                    directional_judgment_allowed=False,
                    warnings=warnings,
                    quality_input=quality_input,
                    company_direct_count=company_direct_count,
                    industry_background_count=industry_background_count,
                    policy_macro_count=policy_macro_count,
                    accepted_count=accepted_count,
                    missing_fields=missing_fields,
                )

            return _decision(
                status="complete",
                directional_judgment_allowed=True,
                warnings=warnings,
                quality_input=quality_input,
                company_direct_count=company_direct_count,
                industry_background_count=industry_background_count,
                policy_macro_count=policy_macro_count,
                accepted_count=accepted_count,
                missing_fields=missing_fields,
            )


def _decision(
    *,
    status: str,
    directional_judgment_allowed: bool,
    warnings: list[str],
    quality_input: QualityInput,
    company_direct_count: int,
    industry_background_count: int,
    policy_macro_count: int,
    accepted_count: int,
    missing_fields: list[str],
) -> QualityDecision:
    quality = Quality(
        status=status,
        company_direct_news_count=company_direct_count,
        industry_background_count=industry_background_count,
        policy_macro_count=policy_macro_count,
        total_raw_count=quality_input.total_raw_count,
        after_dedup_count=quality_input.after_dedup_count,
        accepted_count=accepted_count,
        missing_fields=missing_fields,
        directional_judgment_allowed=directional_judgment_allowed,
        warnings=warnings,
    )
    record_metric("news_pack_status_total", labels={"status": status}, value=1)
    for field in missing_fields:
        record_metric("news_pack_missing_field_total", labels={"field": field}, value=1)
    record_metric("news_pack_company_direct_count", labels=None, value=company_direct_count)
    record_event(
        "news_quality_decision",
        fields={
            "status": status,
            "count": accepted_count,
            "error_code": None if status != "failed" else "quality_failed",
        },
    )
    return QualityDecision(ok=status != "failed", quality=quality)


def _count_by_buckets(items: list[NewsItem], buckets: tuple[str, ...]) -> int:
    return sum(1 for item in items if getattr(item, "bucket", None) in buckets)


def _count_p0_effective_success(provider_attempts: list[ProviderAttempt]) -> int:
    return sum(
        1
        for provider, endpoint in _P0_PROVIDER_ENDPOINTS
        if _is_effective_success(_find_provider_attempt(provider_attempts, provider=provider, endpoint=endpoint))
    )


def _find_provider_attempt(
    provider_attempts: list[ProviderAttempt],
    *,
    provider: str,
    endpoint: str,
) -> ProviderAttempt | None:
    for attempt in provider_attempts:
        if attempt.provider == provider and attempt.endpoint == endpoint:
            return attempt
    return None


def _is_effective_success(attempt: ProviderAttempt | None) -> bool:
    if attempt is None:
        return False
    if attempt.ok is not True:
        return False
    if attempt.raw_count <= 0:
        return False
    return True


def _all_providers_failed(provider_attempts: list[ProviderAttempt]) -> bool:
    if not provider_attempts:
        return True
    return not any(_is_effective_success(attempt) for attempt in provider_attempts)


def _build_missing_fields(*, quality_input: QualityInput, company_direct_count: int) -> list[str]:
    ordered: list[str] = []
    seen: set[str] = set()

    def append_missing(field: str | None) -> None:
        if field is None:
            return
        normalized = field.strip()
        if normalized == "" or normalized in seen:
            return
        seen.add(normalized)
        ordered.append(normalized)

    for field in quality_input.request.profile_missing_fields:
        append_missing(field)

    if _is_empty_text(quality_input.request.company_name):
        append_missing("company_name")
    if _is_empty_text(quality_input.request.industry):
        append_missing("industry")
    if company_direct_count == 0:
        append_missing("company_direct_news")

    return ordered


def _is_empty_text(value: str | None) -> bool:
    return value is None or value.strip() == ""


def _contains_directional_signal(items: list[NewsItem]) -> bool:
    for item in items:
        if _item_has_directional_signal(item):
            return True
    return False


def _item_has_directional_signal(item: Any) -> bool:
    for path, value in _iter_key_values(item):
        key_name = path.split(".")[-1]

        if key_name == "is_sentiment_judgment":
            if value is True:
                return True
            continue

        if _contains_directional_key_token(key_name):
            return True

        if isinstance(value, str) and _should_scan_value(path) and _DIRECTIONAL_VALUE_PATTERN.search(value):
            return True
    return False


def _iter_key_values(value: Any, *, path: str = ""):
    if is_dataclass(value):
        value = asdict(value)
    elif hasattr(value, "__dict__") and not isinstance(value, (str, bytes)):
        value = vars(value)

    if isinstance(value, dict):
        for key, child in value.items():
            key_str = str(key)
            next_path = f"{path}.{key_str}" if path else key_str
            yield next_path, child
            yield from _iter_key_values(child, path=next_path)
        return

    if isinstance(value, list):
        for index, child in enumerate(value):
            next_path = f"{path}[{index}]"
            yield next_path, child
            yield from _iter_key_values(child, path=next_path)


def _contains_directional_key_token(key_name: str) -> bool:
    normalized = re.sub(r"[\s_\-.]+", "", key_name).lower()
    for token in _DIRECTIONAL_KEY_TOKENS:
        if token in normalized:
            return True
    return any(token in key_name for token in _DIRECTIONAL_CN_TOKENS)


def _should_scan_value(path: str) -> bool:
    key_name = path.split(".")[-1]
    if key_name.endswith("]"):
        return True
    return key_name not in _VALUE_SCAN_PATH_SKIP_SUFFIXES
