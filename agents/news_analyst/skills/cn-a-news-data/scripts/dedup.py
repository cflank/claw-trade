from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from models import DedupStats, NewsDataPackRequest, NewsItem
from observability import record_metric, trace_span

DEFAULT_TITLE_SIMILARITY_THRESHOLD = 0.92
DEFAULT_TITLE_SIMILARITY_MAX_CANDIDATES = 100
DEFAULT_MAX_JSON_ITEMS = 100
DEFAULT_MAX_BRIEF_ITEMS = 20

_BUCKET_PRIORITY: dict[str, int] = {
    "company_news": 0,
    "announcements": 1,
    "industry_news": 2,
    "policy_macro_news": 3,
    "rejected": 4,
}


def _normalize_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def normalize_title(title: object) -> str | None:
    normalized = _normalize_text(title)
    if normalized is None:
        return None
    return " ".join(normalized.lower().split())


def _normalize_title_for_similarity(title: object) -> str | None:
    normalized = normalize_title(title)
    if normalized is None:
        return None
    return normalized.replace(" ", "")


def normalize_source(source: object) -> str:
    normalized = _normalize_text(source)
    if normalized is None:
        return ""
    return " ".join(normalized.lower().split())


def normalize_url(url: object) -> str | None:
    normalized = _normalize_text(url)
    if normalized is None:
        return None
    parsed = urlsplit(normalized)
    if not parsed.scheme and not parsed.netloc:
        return normalized.rstrip("/")
    scheme = parsed.scheme.lower()
    netloc = parsed.netloc.lower()
    if netloc.endswith(":80") and scheme == "http":
        netloc = netloc[:-3]
    if netloc.endswith(":443") and scheme == "https":
        netloc = netloc[:-4]
    path = parsed.path.rstrip("/")
    query = urlencode(sorted(parse_qsl(parsed.query, keep_blank_values=True)))
    return urlunsplit((scheme, netloc, path, query, ""))


def bucket_publish_time(publish_time: object) -> str:
    normalized = _normalize_text(publish_time)
    if normalized is None:
        return ""
    if len(normalized) == 8 and normalized.isdigit():
        return normalized
    if len(normalized) >= 10:
        date_prefix = normalized[:10]
        try:
            return datetime.strptime(date_prefix, "%Y-%m-%d").strftime("%Y%m%d")
        except ValueError:
            pass
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        return parsed.strftime("%Y%m%d")
    except ValueError:
        return normalized


def title_similarity(left_title: object, right_title: object) -> float:
    left = _normalize_title_for_similarity(left_title)
    right = _normalize_title_for_similarity(right_title)
    if left is None or right is None:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(a=left, b=right).ratio()


def _merge_sources(target: NewsItem, duplicate: NewsItem) -> None:
    merged = [*target.merged_from]
    for raw_id in [duplicate.news_id, *duplicate.merged_from]:
        if raw_id == target.news_id:
            continue
        if raw_id in merged:
            continue
        merged.append(raw_id)
    target.merged_from = merged


def _should_preserve_incoming_company_news(existing: NewsItem, incoming: NewsItem) -> bool:
    return existing.bucket != "company_news" and incoming.bucket == "company_news"


@dataclass(frozen=True)
class _NormalizedItem:
    item: NewsItem
    normalized_title: str | None


class Deduplicator:
    def __init__(
        self,
        title_similarity_threshold: float = DEFAULT_TITLE_SIMILARITY_THRESHOLD,
        title_similarity_max_candidates: int = DEFAULT_TITLE_SIMILARITY_MAX_CANDIDATES,
    ) -> None:
        self.title_similarity_threshold = title_similarity_threshold
        self.title_similarity_max_candidates = max(0, title_similarity_max_candidates)

    def deduplicate(self, items: list[NewsItem]) -> tuple[list[NewsItem], DedupStats]:
        with trace_span("news_data_pack.dedup"):
            seen_url: dict[str, NewsItem] = {}
            seen_title: dict[str, NewsItem] = {}
            seen_title_source_bucket: dict[str, NewsItem] = {}
            normalized_output: list[_NormalizedItem] = []
            output: list[NewsItem] = []
            merged_raw_ids: list[str] = []
            merge_reasons: list[str] = []

            for item in items:
                normalized_url = normalize_url(item.url)
                normalized_title = normalize_title(item.title)
                similarity_title = _normalize_title_for_similarity(item.title)
                normalized_source = normalize_source(item.source)
                time_bucket = bucket_publish_time(item.publish_time)

                if normalized_url and normalized_url in seen_url:
                    existing = seen_url[normalized_url]
                    if not _should_preserve_incoming_company_news(existing, item):
                        _merge_sources(existing, item)
                        merged_raw_ids.append(item.news_id)
                        merge_reasons.append("url_exact")
                        continue

                if normalized_title and normalized_title in seen_title:
                    existing = seen_title[normalized_title]
                    if not _should_preserve_incoming_company_news(existing, item):
                        _merge_sources(existing, item)
                        merged_raw_ids.append(item.news_id)
                        merge_reasons.append("title_exact")
                        continue

                if similarity_title:
                    title_source_key = f"{similarity_title}|{normalized_source}|{time_bucket}"
                    if title_source_key in seen_title_source_bucket:
                        existing = seen_title_source_bucket[title_source_key]
                        if not _should_preserve_incoming_company_news(existing, item):
                            _merge_sources(existing, item)
                            merged_raw_ids.append(item.news_id)
                            merge_reasons.append("title_source_time")
                            continue

                    similar_item = self._find_similar(normalized_output, similarity_title, item)
                    if similar_item is not None:
                        _merge_sources(similar_item, item)
                        merged_raw_ids.append(item.news_id)
                        merge_reasons.append("title_similarity")
                        continue

                output.append(item)
                normalized_output.append(_NormalizedItem(item=item, normalized_title=normalized_title))
                if normalized_url:
                    seen_url[normalized_url] = item
                if normalized_title:
                    seen_title[normalized_title] = item
                    title_source_key = f"{similarity_title}|{normalized_source}|{time_bucket}"
                    seen_title_source_bucket[title_source_key] = item

            stats = DedupStats(
                removed_count=len(merged_raw_ids),
                merged_raw_ids=merged_raw_ids,
                merge_reasons=merge_reasons,
            )
            record_metric("news_dedup_removed_total", labels=None, value=stats.removed_count)
            for reason in stats.merge_reasons:
                record_metric("news_dedup_reason_total", labels={"reason": reason}, value=1)
            return output, stats

    def _find_similar(
        self,
        output_items: list[_NormalizedItem],
        normalized_title: str,
        incoming_item: NewsItem,
    ) -> NewsItem | None:
        compared_candidates = 0
        for normalized_item in output_items:
            candidate_title = normalized_item.normalized_title
            if candidate_title is None:
                continue
            if compared_candidates >= self.title_similarity_max_candidates:
                break
            compared_candidates += 1
            if _should_preserve_incoming_company_news(normalized_item.item, incoming_item):
                continue
            ratio = title_similarity(candidate_title, normalized_title)
            if ratio >= self.title_similarity_threshold:
                return normalized_item.item
        return None


def _parse_request_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def _parse_publish_datetime(publish_time: object) -> datetime | None:
    normalized = _normalize_text(publish_time)
    if normalized is None:
        return None
    if len(normalized) == 8 and normalized.isdigit():
        try:
            return datetime.strptime(normalized, "%Y%m%d")
        except ValueError:
            return None
    if len(normalized) >= 10:
        try:
            parsed_date = datetime.strptime(normalized[:10], "%Y-%m-%d")
        except ValueError:
            parsed_date = None
        else:
            if len(normalized) == 10:
                return parsed_date
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def _append_publish_time_missing_gap(item: NewsItem) -> None:
    if item.evidence_gap is None or item.evidence_gap.strip() == "":
        item.evidence_gap = "publish_time_missing"
        return
    existing_parts = [part.strip() for part in item.evidence_gap.split(",") if part.strip()]
    if "publish_time_missing" in existing_parts:
        return
    existing_parts.append("publish_time_missing")
    item.evidence_gap = ",".join(existing_parts)


@dataclass(frozen=True)
class _SortableItem:
    item: NewsItem
    original_index: int
    publish_datetime: datetime | None


class SortTrimProcessor:
    def __init__(
        self,
        max_json_items: int = DEFAULT_MAX_JSON_ITEMS,
        max_brief_items: int = DEFAULT_MAX_BRIEF_ITEMS,
    ) -> None:
        self.max_json_items = max(1, max_json_items)
        self.max_brief_items = max(1, max_brief_items)

    def sort_and_trim(self, items: list[NewsItem], request: NewsDataPackRequest) -> list[NewsItem]:
        start_date = _parse_request_date(request.start_date)
        end_date = _parse_request_date(request.end_date)

        in_window: list[_SortableItem] = []
        for index, item in enumerate(items):
            publish_datetime = _parse_publish_datetime(item.publish_time)
            if publish_datetime is None:
                _append_publish_time_missing_gap(item)
                in_window.append(_SortableItem(item=item, original_index=index, publish_datetime=None))
                continue

            publish_date = publish_datetime.date()
            if start_date <= publish_date <= end_date:
                in_window.append(_SortableItem(item=item, original_index=index, publish_datetime=publish_datetime))

        grouped_by_bucket: dict[str, list[_SortableItem]] = {}
        for sortable_item in in_window:
            grouped_by_bucket.setdefault(sortable_item.item.bucket, []).append(sortable_item)

        sorted_items: list[NewsItem] = []
        for bucket, _ in sorted(_BUCKET_PRIORITY.items(), key=lambda item: item[1]):
            bucket_items = grouped_by_bucket.pop(bucket, [])
            if not bucket_items:
                continue
            sorted_items.extend(self._sort_within_bucket(bucket_items))

        if grouped_by_bucket:
            for bucket in sorted(grouped_by_bucket.keys()):
                sorted_items.extend(self._sort_within_bucket(grouped_by_bucket[bucket]))

        trimmed_count = max(0, len(sorted_items) - self.max_json_items)
        record_metric("news_trimmed_total", labels=None, value=trimmed_count)
        return sorted_items[: self.max_json_items]

    def _sort_within_bucket(self, items: list[_SortableItem]) -> list[NewsItem]:
        parseable: list[_SortableItem] = []
        missing_or_unparseable: list[_SortableItem] = []
        for sortable_item in items:
            if sortable_item.publish_datetime is None:
                missing_or_unparseable.append(sortable_item)
                continue
            parseable.append(sortable_item)

        parseable_sorted = sorted(
            parseable,
            key=lambda sortable_item: (
                sortable_item.publish_datetime,
                -sortable_item.original_index,
            ),
            reverse=True,
        )
        return [sortable_item.item for sortable_item in parseable_sorted] + [
            sortable_item.item for sortable_item in missing_or_unparseable
        ]
