from __future__ import annotations

import hashlib
import math
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Iterable

import akshare as ak

from claw_trade.providers.tushare_client import create_tushare_pro

from config_loader import load_cn_a_news_config
from models import EmptyReason, ProviderAttempt, ProviderFetchResult, ProviderQuery, QueryPlan, RawNewsItem
from security import sanitize_error, validate_external_url

_PERMISSION_PATTERNS: tuple[str, ...] = (
    "permission denied",
    "permission_missing",
    "permission",
    "denied",
    "forbidden",
    "unauthorized",
    "insufficient privilege",
    "insufficient scope",
    "积分不足",
    "无权限",
    "权限",
    "未授权",
    "403",
)
_RATE_LIMIT_PATTERNS: tuple[str, ...] = (
    "rate limit",
    "rate_limited",
    "too many requests",
    "429",
    "limit exceeded",
    "流控",
    "限流",
    "频率限制",
)
_TIMEOUT_PATTERNS: tuple[str, ...] = (
    "timeout",
    "timed out",
    "time out",
    "deadline exceeded",
    "超时",
)
_SCHEMA_PATTERNS: tuple[str, ...] = (
    "schema",
    "column",
    "field",
    "missing key",
    "missing column",
    "字段缺失",
    "列缺失",
)


def build_provider_query(
    *,
    endpoint: str,
    query_plan: QueryPlan,
    keywords: Iterable[str] | None = None,
) -> ProviderQuery:
    return ProviderQuery(
        endpoint=endpoint,
        ticker=query_plan.ticker,
        exchange_ticker=query_plan.exchange_ticker,
        company_name=query_plan.company_name,
        keywords=_normalize_keywords(keywords),
        start_date=query_plan.start_date,
        end_date=query_plan.end_date,
    )


def classify_empty_reason(error: BaseException | str) -> EmptyReason:
    if isinstance(error, TimeoutError):
        return "timeout"
    if isinstance(error, PermissionError):
        return "permission_missing"

    text = str(error).lower()
    if _contains_any(text, _PERMISSION_PATTERNS):
        return "permission_missing"
    if _contains_any(text, _RATE_LIMIT_PATTERNS):
        return "rate_limited"
    if _contains_any(text, _TIMEOUT_PATTERNS):
        return "timeout"
    if _contains_any(text, _SCHEMA_PATTERNS):
        return "schema_changed"
    return "provider_error"


def derive_raw_id(
    *,
    endpoint: str,
    url: str | None,
    title: str,
    publish_time: str | None,
) -> str:
    key = "|".join(
        [
            endpoint.strip(),
            (url or "").strip(),
            title.strip(),
            (publish_time or "").strip(),
        ]
    )
    digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
    return f"{endpoint}:{digest[:24]}"


def build_raw_news_item(
    *,
    endpoint: str,
    raw_id: str | None,
    title: str,
    summary: str | None,
    source: str | None,
    publish_time: str | None,
    url: str | None,
    data_source: str,
    raw_payload_ref: str | None,
    source_fetch_time: str,
) -> RawNewsItem:
    normalized_raw_id = (raw_id or "").strip()
    if normalized_raw_id == "":
        normalized_raw_id = derive_raw_id(
            endpoint=endpoint,
            url=url,
            title=title,
            publish_time=publish_time,
        )
    return RawNewsItem(
        raw_id=normalized_raw_id,
        title=title,
        summary=summary,
        source=source,
        publish_time=publish_time,
        url=url,
        data_source=data_source,
        raw_payload_ref=raw_payload_ref,
        source_fetch_time=source_fetch_time,
    )


@dataclass(frozen=True)
class FetchTimer:
    started_ns: int

    @classmethod
    def start(cls) -> "FetchTimer":
        return cls(started_ns=time.monotonic_ns())

    def elapsed_ms(self) -> int:
        return max(0, (time.monotonic_ns() - self.started_ns) // 1_000_000)


class ProviderFetchResultFactory:
    @staticmethod
    def success(
        *,
        provider: str,
        endpoint: str,
        query: str,
        elapsed_ms: int,
        raw_items: list[RawNewsItem],
        cancelled: bool = False,
    ) -> ProviderFetchResult:
        return ProviderFetchResult(
            ok=True,
            attempt=ProviderAttempt(
                provider=provider,
                endpoint=endpoint,
                query=query,
                ok=True,
                elapsed_ms=elapsed_ms,
                raw_count=len(raw_items),
                accepted_count=0,
                empty_reason=None if raw_items else "no_result",
                error=None,
                cancelled=cancelled,
            ),
            raw_items=raw_items,
        )

    @staticmethod
    def empty(
        *,
        provider: str,
        endpoint: str,
        query: str,
        elapsed_ms: int,
        reason: EmptyReason = "no_result",
        error: str | None = None,
        cancelled: bool = False,
    ) -> ProviderFetchResult:
        return ProviderFetchResult(
            ok=False,
            attempt=ProviderAttempt(
                provider=provider,
                endpoint=endpoint,
                query=query,
                ok=False,
                elapsed_ms=elapsed_ms,
                raw_count=0,
                accepted_count=0,
                empty_reason=reason,
                error=error,
                cancelled=cancelled,
            ),
            raw_items=[],
        )

    @staticmethod
    def from_exception(
        *,
        provider: str,
        endpoint: str,
        query: str,
        elapsed_ms: int,
        error: BaseException,
        cancelled: bool = False,
    ) -> ProviderFetchResult:
        return ProviderFetchResultFactory.empty(
            provider=provider,
            endpoint=endpoint,
            query=query,
            elapsed_ms=elapsed_ms,
            reason=classify_empty_reason(error),
            error=sanitize_error(error),
            cancelled=cancelled,
        )

    @staticmethod
    def timeout_cancelled(
        *,
        provider: str,
        endpoint: str,
        query: str,
        elapsed_ms: int,
    ) -> ProviderFetchResult:
        return ProviderFetchResultFactory.empty(
            provider=provider,
            endpoint=endpoint,
            query=query,
            elapsed_ms=elapsed_ms,
            reason="timeout",
            error="total_timeout_cancelled",
            cancelled=True,
        )


class NewsProvider(ABC):
    name: str
    endpoint: str
    priority: str

    def fetch(self, query: ProviderQuery) -> ProviderFetchResult:
        timer = FetchTimer.start()
        query_summary = self._build_query_summary(query)
        try:
            raw_table = self._call_remote(query)
            if _is_empty_table_signal(raw_table):
                return ProviderFetchResultFactory.empty(
                    provider=self.name,
                    endpoint=self.endpoint,
                    query=query_summary,
                    elapsed_ms=timer.elapsed_ms(),
                    reason="no_result",
                )

            raw_items = self._normalize_rows(raw_table, query)
            if not raw_items:
                return ProviderFetchResultFactory.empty(
                    provider=self.name,
                    endpoint=self.endpoint,
                    query=query_summary,
                    elapsed_ms=timer.elapsed_ms(),
                    reason="no_result",
                )

            return ProviderFetchResultFactory.success(
                provider=self.name,
                endpoint=self.endpoint,
                query=query_summary,
                elapsed_ms=timer.elapsed_ms(),
                raw_items=raw_items,
            )
        except Exception as error:
            return ProviderFetchResultFactory.from_exception(
                provider=self.name,
                endpoint=self.endpoint,
                query=query_summary,
                elapsed_ms=timer.elapsed_ms(),
                error=error,
                cancelled=False,
            )

    def _build_query_summary(self, query: ProviderQuery) -> str:
        return (
            f"ticker={query.ticker};exchange_ticker={query.exchange_ticker};"
            f"start_date={query.start_date};end_date={query.end_date}"
        )

    @abstractmethod
    def _call_remote(self, query: ProviderQuery) -> object:
        raise NotImplementedError

    @abstractmethod
    def _normalize_rows(self, raw_table: object, query: ProviderQuery) -> list[RawNewsItem]:
        raise NotImplementedError

    @staticmethod
    def default_source_fetch_time() -> str:
        return datetime.now(tz=UTC).isoformat()


class AkshareStockNewsEmProvider(NewsProvider):
    name = "akshare"
    endpoint = "stock_news_em"
    priority = "P0"
    data_source = "akshare.stock_news_em"

    _TITLE_COLUMNS: tuple[str, ...] = ("新闻标题", "标题", "title")
    _SUMMARY_COLUMNS: tuple[str, ...] = ("新闻内容", "内容", "summary")
    _PUBLISH_TIME_COLUMNS: tuple[str, ...] = ("发布时间", "publish_time", "time")
    _SOURCE_COLUMNS: tuple[str, ...] = ("文章来源", "来源", "source")
    _URL_COLUMNS: tuple[str, ...] = ("新闻链接", "链接", "url")
    _KEYWORD_COLUMNS: tuple[str, ...] = ("关键词", "keyword", "keywords")

    def _call_remote(self, query: ProviderQuery) -> object:
        return ak.stock_news_em(symbol=query.ticker)

    def _normalize_rows(self, raw_table: object, query: ProviderQuery) -> list[RawNewsItem]:
        rows = _to_row_dicts(raw_table)
        title_column = _resolve_column_name(rows, self._TITLE_COLUMNS)
        if title_column is None:
            raise ValueError("missing column: 新闻标题")

        fetch_time = self.default_source_fetch_time()
        result: list[RawNewsItem] = []
        for row in rows:
            title = _get_text(row, (title_column,))
            if title is None:
                raise ValueError("field 新闻标题 is empty")

            summary = _merge_summary_with_keyword(
                _get_text(row, self._SUMMARY_COLUMNS),
                _get_text(row, self._KEYWORD_COLUMNS),
            )

            candidate_url = _get_text(row, self._URL_COLUMNS)
            validated_url = validate_external_url(candidate_url)
            normalized_url = validated_url.sanitized_url if validated_url.valid else None

            result.append(
                build_raw_news_item(
                    endpoint=query.endpoint,
                    raw_id=None,
                    title=title,
                    summary=summary,
                    source=_get_text(row, self._SOURCE_COLUMNS),
                    publish_time=_get_text(row, self._PUBLISH_TIME_COLUMNS),
                    url=normalized_url,
                    data_source=self.data_source,
                    raw_payload_ref=None,
                    source_fetch_time=fetch_time,
                )
            )
        return result


class AkshareStockInfoGlobalClsProvider(NewsProvider):
    name = "akshare"
    endpoint = "stock_info_global_cls"
    priority = "P0"
    data_source = "akshare.stock_info_global_cls"

    _TITLE_COLUMNS: tuple[str, ...] = ("标题", "title")
    _SUMMARY_COLUMNS: tuple[str, ...] = ("内容", "summary")
    _PUBLISH_DATE_COLUMNS: tuple[str, ...] = ("发布日期", "date", "publish_date")
    _PUBLISH_CLOCK_COLUMNS: tuple[str, ...] = ("发布时间", "time", "publish_time")
    _SOURCE_COLUMNS: tuple[str, ...] = ("来源", "source")
    _DEFAULT_SOURCE = "财联社电报"

    def _call_remote(self, query: ProviderQuery) -> object:
        return ak.stock_info_global_cls(symbol="全部")

    def _normalize_rows(self, raw_table: object, query: ProviderQuery) -> list[RawNewsItem]:
        rows = _to_row_dicts(raw_table)
        title_column = _resolve_column_name(rows, self._TITLE_COLUMNS)
        if title_column is None:
            raise ValueError("missing column: 标题")

        summary_column = _resolve_column_name(rows, self._SUMMARY_COLUMNS)
        if summary_column is None:
            raise ValueError("missing column: 内容")

        fetch_time = self.default_source_fetch_time()
        result: list[RawNewsItem] = []
        for row in rows:
            title = _get_text(row, (title_column,))
            if title is None:
                raise ValueError("field 标题 is empty")

            summary = _get_text(row, (summary_column,))
            if summary is None:
                raise ValueError("field 内容 is empty")

            publish_date = _get_text(row, self._PUBLISH_DATE_COLUMNS)
            publish_clock = _get_text(row, self._PUBLISH_CLOCK_COLUMNS)
            publish_time = _compose_publish_time(publish_date=publish_date, publish_clock=publish_clock)
            source = _get_text(row, self._SOURCE_COLUMNS) or self._DEFAULT_SOURCE

            result.append(
                build_raw_news_item(
                    endpoint=query.endpoint,
                    raw_id=None,
                    title=title,
                    summary=summary,
                    source=source,
                    publish_time=publish_time,
                    url=None,
                    data_source=self.data_source,
                    raw_payload_ref=None,
                    source_fetch_time=fetch_time,
                )
            )
        return result


class AkshareStockInfoGlobalEmProvider(NewsProvider):
    name = "akshare"
    endpoint = "stock_info_global_em"
    priority = "P1"
    data_source = "akshare.stock_info_global_em"

    _TITLE_COLUMNS: tuple[str, ...] = ("标题", "title")
    _SUMMARY_COLUMNS: tuple[str, ...] = ("摘要", "内容", "summary", "content")
    _PUBLISH_TIME_COLUMNS: tuple[str, ...] = ("发布时间", "publish_time", "time")
    _SOURCE_COLUMNS: tuple[str, ...] = ("来源", "source")
    _URL_COLUMNS: tuple[str, ...] = ("链接", "url")
    _DEFAULT_SOURCE = "东方财富快讯"

    def _call_remote(self, query: ProviderQuery) -> object:
        return ak.stock_info_global_em()

    def _normalize_rows(self, raw_table: object, query: ProviderQuery) -> list[RawNewsItem]:
        rows = _to_row_dicts(raw_table)
        title_column = _resolve_column_name(rows, self._TITLE_COLUMNS)
        if title_column is None:
            raise ValueError("missing column: 标题")

        if _resolve_column_name(rows, self._SUMMARY_COLUMNS) is None:
            raise ValueError("missing column: 摘要")

        fetch_time = self.default_source_fetch_time()
        result: list[RawNewsItem] = []
        for row in rows:
            title = _get_text(row, (title_column,))
            if title is None:
                raise ValueError("field 标题 is empty")

            summary = _get_text(row, self._SUMMARY_COLUMNS)
            if summary is None:
                raise ValueError("field 摘要 is empty")

            source = _get_text(row, self._SOURCE_COLUMNS) or self._DEFAULT_SOURCE
            candidate_url = _get_text(row, self._URL_COLUMNS)
            validated_url = validate_external_url(candidate_url)
            normalized_url = validated_url.sanitized_url if validated_url.valid else None

            result.append(
                build_raw_news_item(
                    endpoint=query.endpoint,
                    raw_id=None,
                    title=title,
                    summary=summary,
                    source=source,
                    publish_time=_get_text(row, self._PUBLISH_TIME_COLUMNS),
                    url=normalized_url,
                    data_source=self.data_source,
                    raw_payload_ref=None,
                    source_fetch_time=fetch_time,
                )
            )
        return result


class AkshareNewsCctvProvider(NewsProvider):
    name = "akshare"
    endpoint = "news_cctv"
    priority = "P1"
    data_source = "akshare.news_cctv"

    _PUBLISH_TIME_COLUMNS: tuple[str, ...] = ("date", "日期", "publish_time")
    _TITLE_COLUMNS: tuple[str, ...] = ("title", "标题")
    _SUMMARY_COLUMNS: tuple[str, ...] = ("content", "内容", "summary")
    _FIXED_SOURCE = "新闻联播"

    def _call_remote(self, query: ProviderQuery) -> object:
        return ak.news_cctv(date=_to_yyyymmdd(query.end_date))

    def _normalize_rows(self, raw_table: object, query: ProviderQuery) -> list[RawNewsItem]:
        rows = _to_row_dicts(raw_table)
        publish_time_column = _resolve_column_name(rows, self._PUBLISH_TIME_COLUMNS)
        if publish_time_column is None:
            raise ValueError("missing column: date")
        title_column = _resolve_column_name(rows, self._TITLE_COLUMNS)
        if title_column is None:
            raise ValueError("missing column: title")
        summary_column = _resolve_column_name(rows, self._SUMMARY_COLUMNS)
        if summary_column is None:
            raise ValueError("missing column: content")

        fetch_time = self.default_source_fetch_time()
        result: list[RawNewsItem] = []
        for row in rows:
            publish_time = _get_text(row, (publish_time_column,))
            if publish_time is None:
                raise ValueError("field date is empty")
            title = _get_text(row, (title_column,))
            if title is None:
                raise ValueError("field title is empty")
            summary = _get_text(row, (summary_column,))
            if summary is None:
                raise ValueError("field content is empty")

            result.append(
                build_raw_news_item(
                    endpoint=query.endpoint,
                    raw_id=None,
                    title=title,
                    summary=summary,
                    source=self._FIXED_SOURCE,
                    publish_time=publish_time,
                    url=None,
                    data_source=self.data_source,
                    raw_payload_ref=None,
                    source_fetch_time=fetch_time,
                )
            )
        return result


class TushareAnnouncementsProvider(NewsProvider):
    name = "tushare"
    endpoint = "anns_d"
    priority = "P1"
    data_source = "tushare.anns_d"

    _TITLE_COLUMNS: tuple[str, ...] = ("title", "标题")
    _ANN_DATE_COLUMNS: tuple[str, ...] = ("ann_date",)
    _REC_TIME_COLUMNS: tuple[str, ...] = ("rec_time",)
    _URL_COLUMNS: tuple[str, ...] = ("url",)
    _TS_CODE_COLUMNS: tuple[str, ...] = ("ts_code",)
    _NAME_COLUMNS: tuple[str, ...] = ("name",)

    def __init__(
        self,
        *,
        token_loader=load_cn_a_news_config,
        pro_api_factory=None,
    ) -> None:
        self._token_loader = token_loader
        self._pro_api_factory = pro_api_factory
        self._active_token: str | None = None

    def fetch(self, query: ProviderQuery) -> ProviderFetchResult:
        timer = FetchTimer.start()
        query_summary = self._build_query_summary(query)
        token = self._resolve_tushare_token()
        if token is None:
            return ProviderFetchResultFactory.empty(
                provider=self.name,
                endpoint=self.endpoint,
                query=query_summary,
                elapsed_ms=timer.elapsed_ms(),
                reason="not_configured",
            )

        self._active_token = token
        try:
            return super().fetch(query)
        finally:
            self._active_token = None

    def _resolve_tushare_token(self) -> str | None:
        token = self._token_loader().tushare_token
        if token is None:
            return None
        cleaned = token.strip()
        if cleaned == "":
            return None
        return cleaned

    def _call_remote(self, query: ProviderQuery) -> object:
        token = self._active_token
        if token is None:
            raise RuntimeError("tushare token missing for remote call")

        if self._pro_api_factory is not None:
            pro = self._pro_api_factory(token)
        else:
            pro = create_tushare_pro(token=token)

        return pro.anns_d(
            ts_code=query.exchange_ticker,
            start_date=_to_yyyymmdd(query.start_date),
            end_date=_to_yyyymmdd(query.end_date),
        )

    def _normalize_rows(self, raw_table: object, query: ProviderQuery) -> list[RawNewsItem]:
        rows = _to_row_dicts(raw_table)
        title_column = _resolve_column_name(rows, self._TITLE_COLUMNS)
        if title_column is None:
            raise ValueError("missing column: title")

        ann_date_column = _resolve_column_name(rows, self._ANN_DATE_COLUMNS)
        rec_time_column = _resolve_column_name(rows, self._REC_TIME_COLUMNS)
        if ann_date_column is None and rec_time_column is None:
            raise ValueError("missing column: ann_date/rec_time")

        fetch_time = self.default_source_fetch_time()
        result: list[RawNewsItem] = []
        for row in rows:
            title = _get_text(row, (title_column,))
            if title is None:
                raise ValueError("field title is empty")

            publish_time = _get_text(row, (ann_date_column,)) if ann_date_column is not None else None
            if publish_time is None and rec_time_column is not None:
                publish_time = _get_text(row, (rec_time_column,))
            if publish_time is None:
                raise ValueError("field ann_date/rec_time is empty")

            ts_code = _get_text(row, self._TS_CODE_COLUMNS)
            name = _get_text(row, self._NAME_COLUMNS)
            summary = self._build_audit_summary(ts_code=ts_code, name=name)

            candidate_url = _get_text(row, self._URL_COLUMNS)
            validated_url = validate_external_url(candidate_url)
            normalized_url = validated_url.sanitized_url if validated_url.valid else None

            result.append(
                build_raw_news_item(
                    endpoint=query.endpoint,
                    raw_id=None,
                    title=title,
                    summary=summary,
                    source=self.data_source,
                    publish_time=publish_time,
                    url=normalized_url,
                    data_source=self.data_source,
                    raw_payload_ref=None,
                    source_fetch_time=fetch_time,
                )
            )
        return result

    @staticmethod
    def _build_audit_summary(*, ts_code: str | None, name: str | None) -> str:
        ts_code_value = ts_code if ts_code is not None else ""
        name_value = name if name is not None else ""
        return f"可审计字段:\n- ts_code: {ts_code_value}\n- name: {name_value}"


def _normalize_keywords(keywords: Iterable[str] | None) -> list[str]:
    if keywords is None:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for keyword in keywords:
        cleaned = keyword.strip()
        if cleaned == "" or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _contains_any(text: str, patterns: tuple[str, ...]) -> bool:
    return any(pattern in text for pattern in patterns)


def _is_empty_table_signal(raw_table: object) -> bool:
    if raw_table is None:
        return True
    empty_attr = getattr(raw_table, "empty", None)
    if isinstance(empty_attr, bool):
        return empty_attr
    if isinstance(raw_table, (list, tuple, dict, set)):
        return len(raw_table) == 0
    return False


def _to_row_dicts(raw_table: object) -> list[dict[str, object]]:
    if hasattr(raw_table, "to_dict"):
        records = raw_table.to_dict(orient="records")
        normalized_rows: list[dict[str, object]] = []
        for record in records:
            if not isinstance(record, dict):
                raise ValueError("schema_changed: row is not a mapping")
            normalized_rows.append({str(key).strip(): value for key, value in record.items()})
        return normalized_rows

    if isinstance(raw_table, list):
        normalized_rows: list[dict[str, object]] = []
        for record in raw_table:
            if not isinstance(record, dict):
                raise ValueError("schema_changed: row is not a mapping")
            normalized_rows.append({str(key).strip(): value for key, value in record.items()})
        return normalized_rows

    raise ValueError("schema_changed: unsupported table type")


def _resolve_column_name(
    rows: list[dict[str, object]],
    candidates: tuple[str, ...],
) -> str | None:
    if not rows:
        return None
    first_row_keys = set(rows[0].keys())
    for candidate in candidates:
        if candidate in first_row_keys:
            return candidate
    return None


def _get_text(row: dict[str, object], candidates: tuple[str, ...]) -> str | None:
    for candidate in candidates:
        if candidate not in row:
            continue
        text = _normalize_text(row[candidate])
        if text is not None:
            return text
    return None


def _normalize_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None

    text = str(value).strip()
    if text == "" or text.lower() in {"nan", "<na>", "none"}:
        return None
    return text


def _merge_summary_with_keyword(summary: str | None, keyword: str | None) -> str | None:
    if keyword is None:
        return summary
    if summary is None:
        return f"关键词：{keyword}"
    return f"{summary}\n关键词：{keyword}"


def _compose_publish_time(*, publish_date: str | None, publish_clock: str | None) -> str | None:
    if publish_date and publish_clock:
        if publish_date in publish_clock:
            return publish_clock
        return f"{publish_date} {publish_clock}"
    return publish_clock or publish_date


def _to_yyyymmdd(date_text: str) -> str:
    cleaned = date_text.strip()
    if len(cleaned) == 8 and cleaned.isdigit():
        return cleaned
    return datetime.strptime(cleaned, "%Y-%m-%d").strftime("%Y%m%d")
