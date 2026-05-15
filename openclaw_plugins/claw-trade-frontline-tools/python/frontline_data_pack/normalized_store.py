from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping

from pymongo.errors import PyMongoError

from .errors import (
    MONGO_CONFIG_INVALID,
    MONGO_SCHEMA_INVALID,
    MONGO_WRITE_FAILED,
    FrontlineValidationError,
)
from .models import FundamentalField, MarketPriceRow, NewsItem, SocialSignal
from .mongo_store import (
    COLLECTION_NORMALIZED_FUNDAMENTAL_FIELDS,
    COLLECTION_NORMALIZED_MARKET_PRICES,
    COLLECTION_NORMALIZED_NEWS_ITEMS,
    COLLECTION_NORMALIZED_SOCIAL_SIGNALS,
)
from .security import summarize_provider_error


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_VIKING_URI_PREFIX = "viking://"


@dataclass(frozen=True)
class NormalizedUpsertResult:
    collection: str
    attempted_count: int
    success_count: int
    refs: list[str]
    diagnostic_flags: list[str]

    @property
    def write_failed(self) -> bool:
        return bool(self.diagnostic_flags)


def upsert_market_prices(
    *,
    ticker: str,
    rows: list[MarketPriceRow],
    provider: str,
    endpoint: str,
    payload_hash: str,
    raw_payload_ref: str,
    fetched_at: str,
    expires_at: str,
    collection: Any | None,
    market: str = "CN_A",
) -> NormalizedUpsertResult:
    normalized_ticker = _ensure_non_empty("ticker", ticker)
    normalized_market = _ensure_market(market)
    normalized_provider = _ensure_non_empty("provider", provider)
    normalized_endpoint = _ensure_non_empty("endpoint", endpoint)
    normalized_payload_hash = _ensure_sha256("payload_hash", payload_hash)
    normalized_raw_payload_ref = _ensure_viking_uri("raw_payload_ref", raw_payload_ref)
    normalized_fetched_at = _ensure_iso_datetime("fetched_at", fetched_at)
    normalized_expires_at = _ensure_iso_datetime("expires_at", expires_at)

    documents: list[dict[str, Any]] = []
    for row in rows:
        document_id = build_market_price_document_id(
            market=normalized_market,
            ticker=normalized_ticker,
            adjust=row.adjust,
            trade_date=row.trade_date,
        )
        documents.append(
            {
                "_id": document_id,
                "market": normalized_market,
                "ticker": normalized_ticker,
                "trade_date": row.trade_date,
                "adjust": row.adjust,
                "open": row.open,
                "high": row.high,
                "low": row.low,
                "close": row.close,
                "volume": row.volume,
                "amount": row.amount,
                "provider": normalized_provider,
                "endpoint": normalized_endpoint,
                "payload_hash": normalized_payload_hash,
                "raw_payload_ref": normalized_raw_payload_ref,
                "fetched_at": normalized_fetched_at,
                "expires_at": normalized_expires_at,
            }
        )
    return _upsert_documents(
        collection_name=COLLECTION_NORMALIZED_MARKET_PRICES,
        collection=collection,
        documents=documents,
        build_filter=lambda doc: {
            "market": doc["market"],
            "ticker": doc["ticker"],
            "adjust": doc["adjust"],
            "trade_date": doc["trade_date"],
        },
    )


def upsert_news_items(
    *,
    ticker: str,
    items: list[NewsItem],
    fetched_at: str,
    expires_at: str,
    collection: Any | None,
    market: str = "CN_A",
) -> NormalizedUpsertResult:
    normalized_ticker = _ensure_non_empty("ticker", ticker)
    normalized_market = _ensure_market(market)
    normalized_fetched_at = _ensure_iso_datetime("fetched_at", fetched_at)
    normalized_expires_at = _ensure_iso_datetime("expires_at", expires_at)

    documents: list[dict[str, Any]] = []
    for item in items:
        document_id = build_news_item_document_id(
            title=item.title,
            url=item.url,
            publish_time=item.publish_time,
            provider=item.provider,
        )
        documents.append(
            {
                "_id": document_id,
                "market": normalized_market,
                "ticker": normalized_ticker,
                "bucket": item.bucket,
                "title": item.title,
                "summary": item.summary,
                "source": item.source,
                "publish_time": item.publish_time,
                "url": item.url,
                "match_type": item.match_type,
                "match_evidence_span": item.match_evidence_span,
                "provider": _ensure_non_empty("news.provider", item.provider),
                "endpoint": _ensure_non_empty("news.endpoint", item.endpoint),
                "payload_hash": _ensure_sha256("news.payload_hash", item.payload_hash),
                "raw_payload_ref": _ensure_viking_uri("news.raw_payload_ref", item.raw_payload_ref),
                "fetched_at": normalized_fetched_at,
                "expires_at": normalized_expires_at,
            }
        )

    return _upsert_documents(
        collection_name=COLLECTION_NORMALIZED_NEWS_ITEMS,
        collection=collection,
        documents=documents,
        build_filter=lambda doc: {"_id": doc["_id"]},
    )


def upsert_social_signals(
    *,
    ticker: str,
    signals: list[SocialSignal],
    fetched_at: str,
    expires_at: str,
    collection: Any | None,
    market: str = "CN_A",
) -> NormalizedUpsertResult:
    normalized_ticker = _ensure_non_empty("ticker", ticker)
    normalized_market = _ensure_market(market)
    normalized_fetched_at = _ensure_iso_datetime("fetched_at", fetched_at)
    normalized_expires_at = _ensure_iso_datetime("expires_at", expires_at)

    documents: list[dict[str, Any]] = []
    for signal in signals:
        signal_id = _ensure_non_empty("social.signal_id", signal.signal_id)
        documents.append(
            {
                "_id": signal_id,
                "market": normalized_market,
                "ticker": normalized_ticker,
                "signal_type": signal.signal_type,
                "signal_time": signal.observed_at,
                "rank": signal.rank,
                "heat_value": signal.heat_value,
                "keyword": signal.keyword,
                "related_ticker": signal.related_ticker,
                "match_evidence_span": signal.match_evidence_span,
                "provider": _ensure_non_empty("social.provider", signal.provider),
                "endpoint": _ensure_non_empty("social.endpoint", signal.endpoint),
                "payload_hash": _ensure_sha256("social.payload_hash", signal.payload_hash),
                "raw_payload_ref": _ensure_viking_uri("social.raw_payload_ref", signal.raw_payload_ref),
                "fetched_at": normalized_fetched_at,
                "expires_at": normalized_expires_at,
            }
        )

    return _upsert_documents(
        collection_name=COLLECTION_NORMALIZED_SOCIAL_SIGNALS,
        collection=collection,
        documents=documents,
        build_filter=lambda doc: {"_id": doc["_id"]},
    )


def upsert_fundamental_fields(
    *,
    ticker: str,
    fields: Iterable[FundamentalField],
    fetched_at: str,
    expires_at: str,
    collection: Any | None,
    market: str = "CN_A",
) -> NormalizedUpsertResult:
    normalized_ticker = _ensure_non_empty("ticker", ticker)
    normalized_market = _ensure_market(market)
    normalized_fetched_at = _ensure_iso_datetime("fetched_at", fetched_at)
    normalized_expires_at = _ensure_iso_datetime("expires_at", expires_at)

    documents: list[dict[str, Any]] = []
    for field in fields:
        document_id = build_fundamental_field_document_id(
            market=normalized_market,
            ticker=normalized_ticker,
            field_name=field.field_name,
            report_period=field.report_period,
            provider=field.provider,
            endpoint=field.endpoint,
            payload_hash=field.payload_hash,
        )
        documents.append(
            {
                "_id": document_id,
                "market": normalized_market,
                "ticker": normalized_ticker,
                "field_name": field.field_name,
                "field_value": field.value,
                "unit": field.unit,
                "report_period": field.report_period,
                "source_time": field.source_time,
                "provider": _ensure_non_empty("fundamental.provider", field.provider),
                "endpoint": _ensure_non_empty("fundamental.endpoint", field.endpoint),
                "payload_hash": _ensure_sha256("fundamental.payload_hash", field.payload_hash),
                "raw_payload_ref": _ensure_viking_uri(
                    "fundamental.raw_payload_ref",
                    field.raw_payload_ref,
                ),
                "fetched_at": normalized_fetched_at,
                "expires_at": normalized_expires_at,
            }
        )
    return _upsert_documents(
        collection_name=COLLECTION_NORMALIZED_FUNDAMENTAL_FIELDS,
        collection=collection,
        documents=documents,
        build_filter=lambda doc: {"_id": doc["_id"]},
    )


def build_market_price_document_id(
    *,
    market: str,
    ticker: str,
    adjust: str,
    trade_date: str,
) -> str:
    stable_text = "|".join((market, ticker, adjust, trade_date))
    return f"sha256:{hashlib.sha256(stable_text.encode('utf-8')).hexdigest()}"


def build_news_item_document_id(
    *,
    title: str,
    url: str | None,
    publish_time: str | None,
    provider: str,
) -> str:
    normalized_title = title.strip()
    normalized_url = (url or "").strip()
    normalized_publish_time = (publish_time or "").strip()
    normalized_provider = provider.strip()
    stable_text = "|".join((normalized_title, normalized_url, normalized_publish_time, normalized_provider))
    return f"sha256:{hashlib.sha256(stable_text.encode('utf-8')).hexdigest()}"


def build_fundamental_field_document_id(
    *,
    market: str,
    ticker: str,
    field_name: str,
    report_period: str | None,
    provider: str,
    endpoint: str,
    payload_hash: str,
) -> str:
    stable_text = "|".join(
        (
            market,
            ticker,
            field_name,
            (report_period or "").strip(),
            provider,
            endpoint,
            payload_hash,
        )
    )
    return f"sha256:{hashlib.sha256(stable_text.encode('utf-8')).hexdigest()}"


def _upsert_documents(
    *,
    collection_name: str,
    collection: Any | None,
    documents: list[dict[str, Any]],
    build_filter: Any,
) -> NormalizedUpsertResult:
    attempted_count = len(documents)
    refs: list[str] = []
    diagnostic_flags: list[str] = []
    if attempted_count == 0:
        return NormalizedUpsertResult(
            collection=collection_name,
            attempted_count=0,
            success_count=0,
            refs=[],
            diagnostic_flags=[],
        )

    if collection is None:
        return NormalizedUpsertResult(
            collection=collection_name,
            attempted_count=attempted_count,
            success_count=0,
            refs=[],
            diagnostic_flags=[f"{MONGO_WRITE_FAILED}:{collection_name}:{MONGO_CONFIG_INVALID}"],
        )

    success_count = 0
    for document in documents:
        document_id = document["_id"]
        filter_doc = dict(build_filter(document))
        set_doc = {key: value for key, value in document.items() if key != "_id"}
        update_doc = {"$set": set_doc, "$setOnInsert": {"_id": document_id}}
        try:
            write_result = collection.update_one(filter_doc, update_doc, upsert=True)
        except PyMongoError as exc:
            diagnostic_flags.append(_build_write_failed_diagnostic(collection_name, document_id, exc))
            continue
        except Exception as exc:  # noqa: BLE001
            diagnostic_flags.append(_build_write_failed_diagnostic(collection_name, document_id, exc))
            continue
        if hasattr(write_result, "acknowledged") and write_result.acknowledged is False:
            diagnostic_flags.append(f"{MONGO_WRITE_FAILED}:{collection_name}:{document_id}:unacknowledged_write")
            continue
        success_count += 1
        refs.append(f"{collection_name}:{document_id}")

    return NormalizedUpsertResult(
        collection=collection_name,
        attempted_count=attempted_count,
        success_count=success_count,
        refs=refs,
        diagnostic_flags=diagnostic_flags,
    )


def _ensure_non_empty(field_name: str, value: object) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, f"{field_name} 必须是非空字符串")
    return value.strip()


def _ensure_market(value: object) -> str:
    market = _ensure_non_empty("market", value)
    if market not in {"CN_A", "HK"}:
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, "normalized 写入只支持 market=CN_A 或 market=HK")
    return market


def _ensure_sha256(field_name: str, value: object) -> str:
    text = _ensure_non_empty(field_name, value)
    if _SHA256_RE.fullmatch(text) is None:
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, f"{field_name} 必须是 sha256:<64位小写hex>")
    return text


def _ensure_viking_uri(field_name: str, value: object) -> str:
    text = _ensure_non_empty(field_name, value)
    if not text.startswith(_VIKING_URI_PREFIX):
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, f"{field_name} 必须是 viking:// URI")
    return text


def _ensure_iso_datetime(field_name: str, value: object) -> str:
    text = _ensure_non_empty(field_name, value)
    candidate = text[:-1] + "+00:00" if text.endswith("Z") else text
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as exc:
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, f"{field_name} 必须是合法 ISO 8601 时间") from exc
    if parsed.tzinfo is None:
        raise FrontlineValidationError(MONGO_SCHEMA_INVALID, f"{field_name} 必须带时区")
    return text


def _build_write_failed_diagnostic(collection_name: str, document_id: str, error: Exception) -> str:
    summary = summarize_provider_error(error)
    return f"{MONGO_WRITE_FAILED}:{collection_name}:{document_id}:{summary}"


__all__ = [
    "NormalizedUpsertResult",
    "build_fundamental_field_document_id",
    "build_market_price_document_id",
    "build_news_item_document_id",
    "upsert_fundamental_fields",
    "upsert_market_prices",
    "upsert_news_items",
    "upsert_social_signals",
]
