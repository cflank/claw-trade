from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.errors import MONGO_WRITE_FAILED  # noqa: E402
from frontline_data_pack.models import FundamentalField, MarketPriceRow, NewsItem, SocialSignal  # noqa: E402
from frontline_data_pack.normalized_store import (  # noqa: E402
    build_fundamental_field_document_id,
    build_market_price_document_id,
    build_news_item_document_id,
    upsert_fundamental_fields,
    upsert_market_prices,
    upsert_news_items,
    upsert_social_signals,
)


@dataclass
class _WriteResult:
    acknowledged: bool = True


class _FakeCollection:
    def __init__(self) -> None:
        self.updates: list[dict[str, Any]] = []
        self.docs_by_filter: dict[tuple[tuple[str, Any], ...], dict[str, Any]] = {}

    def update_one(self, flt: dict[str, Any], update: dict[str, Any], *, upsert: bool) -> _WriteResult:
        update_copy = {
            key: (dict(value) if isinstance(value, dict) else value) for key, value in update.items()
        }
        self.updates.append({"filter": dict(flt), "update": update_copy, "upsert": upsert})
        key = tuple(sorted(flt.items()))
        existing = self.docs_by_filter.get(key)
        if existing is None:
            merged = dict(update["$setOnInsert"])
            merged.update(update["$set"])
        else:
            merged = dict(existing)
            merged.update(update["$set"])
        self.docs_by_filter[key] = merged
        return _WriteResult(acknowledged=True)


class _FailingCollection:
    def update_one(self, _flt: dict[str, Any], _update: dict[str, Any], *, upsert: bool) -> _WriteResult:  # noqa: ARG002
        raise RuntimeError("mongo write failed")


def test_t_mdb_003_market_duplicate_unique_key_keeps_single_current_record() -> None:
    collection = _FakeCollection()
    row_a = MarketPriceRow(
        trade_date="2026-05-08",
        open=100.0,
        high=102.0,
        low=99.5,
        close=101.2,
        volume=120000.0,
        amount=9000000.0,
        adjust="qfq",
        source_ref="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/provider_raw/a.json",
    )
    row_b = MarketPriceRow(
        trade_date="2026-05-08",
        open=100.0,
        high=103.0,
        low=99.0,
        close=102.8,
        volume=140000.0,
        amount=9800000.0,
        adjust="qfq",
        source_ref="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/provider_raw/b.json",
    )

    result = upsert_market_prices(
        ticker="600519.SH",
        rows=[row_a, row_b],
        provider="akshare",
        endpoint="stock_zh_a_hist",
        payload_hash=SHA_A,
        raw_payload_ref="viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/provider_raw/1.json",
        fetched_at=FETCHED_AT,
        expires_at=EXPIRES_AT,
        collection=collection,
    )

    assert result.success_count == 2
    assert result.diagnostic_flags == []
    assert len(collection.docs_by_filter) == 1
    only_doc = next(iter(collection.docs_by_filter.values()))
    expected_id = build_market_price_document_id(
        market="CN_A",
        ticker="600519.SH",
        adjust="qfq",
        trade_date="2026-05-08",
    )
    assert only_doc["_id"] == expected_id
    assert only_doc["close"] == 102.8
    assert only_doc["market"] == "CN_A"
    assert only_doc["ticker"] == "600519.SH"
    assert only_doc["provider"] == "akshare"
    assert only_doc["endpoint"] == "stock_zh_a_hist"
    assert only_doc["payload_hash"] == SHA_A
    assert only_doc["raw_payload_ref"].startswith("viking://")
    assert only_doc["fetched_at"] == FETCHED_AT
    assert only_doc["expires_at"] == EXPIRES_AT
    assert all(item["upsert"] is True for item in collection.updates)
    _assert_id_written_only_in_set_on_insert(collection)


def test_t_mdb_003_news_same_sha256_id_does_not_duplicate() -> None:
    collection = _FakeCollection()
    item_a = NewsItem(
        news_id="ignored-a",
        title="贵州茅台召开年度业绩说明会",
        summary="summary-a",
        source="上交所",
        publish_time="2026-05-08T10:00:00+08:00",
        url="https://example.com/news/1",
        bucket="company_news",
        match_type="company_name",
        match_evidence_span="贵州茅台",
        provider="akshare",
        endpoint="stock_news_em",
        raw_payload_ref="viking://resources/workflow/run-1/frontline/news_analyst/call-1/evidence/provider_raw/1.json",
        payload_hash=SHA_A,
    )
    item_b = NewsItem(
        news_id="ignored-b",
        title="贵州茅台召开年度业绩说明会",
        summary="summary-b",
        source="上交所",
        publish_time="2026-05-08T10:00:00+08:00",
        url="https://example.com/news/1",
        bucket="company_news",
        match_type="company_name",
        match_evidence_span="贵州茅台",
        provider="akshare",
        endpoint="stock_news_em",
        raw_payload_ref="viking://resources/workflow/run-1/frontline/news_analyst/call-1/evidence/provider_raw/2.json",
        payload_hash=SHA_B,
    )

    result = upsert_news_items(
        ticker="600519.SH",
        items=[item_a, item_b],
        fetched_at=FETCHED_AT,
        expires_at=EXPIRES_AT,
        collection=collection,
    )

    expected_id = build_news_item_document_id(
        title=item_a.title,
        url=item_a.url,
        publish_time=item_a.publish_time,
        provider=item_a.provider,
    )
    assert result.success_count == 2
    assert len(collection.docs_by_filter) == 1
    only_doc = next(iter(collection.docs_by_filter.values()))
    assert only_doc["_id"] == expected_id
    assert only_doc["payload_hash"] == SHA_B
    assert only_doc["expires_at"] == EXPIRES_AT
    _assert_id_written_only_in_set_on_insert(collection)


def test_t_mdb_003_social_same_unique_id_does_not_duplicate() -> None:
    collection = _FakeCollection()
    signal_a = SocialSignal(
        signal_id="sha256:1111111111111111111111111111111111111111111111111111111111111111",
        signal_type="attention",
        source_platform="eastmoney",
        observed_at="2026-05-08T10:00:00+08:00",
        target_ticker="600519.SH",
        matched_target=True,
        match_evidence_span="600519",
        rank=1,
        heat_value=99.0,
        keyword=None,
        related_ticker=None,
        text_excerpt=None,
        provider="akshare",
        endpoint="stock_hot_rank_latest_em",
        raw_payload_ref="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/provider_raw/1.json",
        payload_hash=SHA_A,
    )
    signal_b = SocialSignal(
        signal_id=signal_a.signal_id,
        signal_type="attention",
        source_platform="eastmoney",
        observed_at="2026-05-08T10:00:00+08:00",
        target_ticker="600519.SH",
        matched_target=True,
        match_evidence_span="600519",
        rank=1,
        heat_value=101.0,
        keyword=None,
        related_ticker=None,
        text_excerpt=None,
        provider="akshare",
        endpoint="stock_hot_rank_latest_em",
        raw_payload_ref="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/provider_raw/2.json",
        payload_hash=SHA_B,
    )

    result = upsert_social_signals(
        ticker="600519.SH",
        signals=[signal_a, signal_b],
        fetched_at=FETCHED_AT,
        expires_at=EXPIRES_AT,
        collection=collection,
    )

    assert result.success_count == 2
    assert len(collection.docs_by_filter) == 1
    only_doc = next(iter(collection.docs_by_filter.values()))
    assert only_doc["_id"] == signal_a.signal_id
    assert only_doc["heat_value"] == 101.0
    assert only_doc["provider"] == "akshare"
    assert only_doc["raw_payload_ref"].endswith("/2.json")
    _assert_id_written_only_in_set_on_insert(collection)


def test_t_mdb_003_fundamental_same_unique_id_keeps_single_current_record() -> None:
    collection = _FakeCollection()
    field_a = FundamentalField(
        field_name="valuation.pe_ttm",
        value=16.8,
        unit="ratio",
        report_period="20260331",
        source_time="2026-05-08T10:30:00+08:00",
        provider="akshare",
        endpoint="stock_zh_a_spot_em",
        raw_payload_ref="viking://resources/workflow/run-1/frontline/fundamental_analyst/call-1/evidence/provider_raw/1.json",
        payload_hash=SHA_A,
        conflict_group=None,
    )
    field_b = FundamentalField(
        field_name="valuation.pe_ttm",
        value=17.1,
        unit="ratio",
        report_period="20260331",
        source_time="2026-05-08T10:35:00+08:00",
        provider="akshare",
        endpoint="stock_zh_a_spot_em",
        raw_payload_ref="viking://resources/workflow/run-1/frontline/fundamental_analyst/call-1/evidence/provider_raw/2.json",
        payload_hash=SHA_A,
        conflict_group=None,
    )

    result = upsert_fundamental_fields(
        ticker="600519.SH",
        fields=[field_a, field_b],
        fetched_at=FETCHED_AT,
        expires_at=EXPIRES_AT,
        collection=collection,
    )

    expected_id = build_fundamental_field_document_id(
        market="CN_A",
        ticker="600519.SH",
        field_name=field_a.field_name,
        report_period=field_a.report_period,
        provider=field_a.provider,
        endpoint=field_a.endpoint,
        payload_hash=field_a.payload_hash,
    )
    assert result.success_count == 2
    assert len(collection.docs_by_filter) == 1
    only_doc = next(iter(collection.docs_by_filter.values()))
    assert only_doc["_id"] == expected_id
    assert only_doc["field_value"] == 17.1
    assert only_doc["raw_payload_ref"].endswith("/2.json")
    _assert_id_written_only_in_set_on_insert(collection)


def test_t_mdb_003_mongo_write_failure_returns_diagnostic_flag() -> None:
    field = FundamentalField(
        field_name="valuation.pe_ttm",
        value=16.8,
        unit="ratio",
        report_period="20260331",
        source_time="2026-05-08T10:30:00+08:00",
        provider="akshare",
        endpoint="stock_zh_a_spot_em",
        raw_payload_ref="viking://resources/workflow/run-1/frontline/fundamental_analyst/call-1/evidence/provider_raw/1.json",
        payload_hash=SHA_A,
        conflict_group=None,
    )

    result = upsert_fundamental_fields(
        ticker="600519.SH",
        fields=[field],
        fetched_at=FETCHED_AT,
        expires_at=EXPIRES_AT,
        collection=_FailingCollection(),
    )

    assert result.attempted_count == 1
    assert result.success_count == 0
    assert result.write_failed is True
    assert result.diagnostic_flags
    assert any(MONGO_WRITE_FAILED in item for item in result.diagnostic_flags)


FETCHED_AT = "2026-05-08T12:00:00+00:00"
EXPIRES_AT = "2026-05-08T18:00:00+00:00"
SHA_A = "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
SHA_B = "sha256:bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


def _assert_id_written_only_in_set_on_insert(collection: _FakeCollection) -> None:
    for item in collection.updates:
        set_doc = item["update"]["$set"]
        set_on_insert_doc = item["update"]["$setOnInsert"]
        assert "_id" not in set_doc
        assert "_id" in set_on_insert_doc
