from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest

from claw_trade.data_gateway.execution import GateDecision, ResultRefs
from claw_trade.data_gateway.execution.fetch_engine import FetchResult
from claw_trade.data_gateway.models import DataGap
from claw_trade.data_gateway.ingest import IngestResult
from claw_trade.data_gateway.ingest.attempt_log import AttemptLog
from claw_trade.data_gateway.ingest.normalized_store import NormalizedStore
from claw_trade.data_gateway.ingest.normalizer import Normalizer
from claw_trade.data_gateway.ingest.pipeline import IngestPipeline
from claw_trade.data_gateway.ingest.raw_store import RawStore


@dataclass(frozen=True)
class _Batch:
    provider_id: str = "provider"
    endpoint_id: str = "endpoint"
    market: str = "CN_A"
    data_type: str = "daily_bar"
    granularity: str = "daily"
    fields_union: tuple[str, ...] = ("close",)
    capability_fields: tuple[str, ...] = ()
    exchange: str | None = "SSE"
    currency: str | None = "CNY"
    timezone: str | None = "Asia/Shanghai"
    calendar: str | None = "CN_A_SSE_SZSE"
    base_asset: str | None = None
    quote_asset: str | None = None
    universe_ref: str | None = None
    request_ids: tuple[str, ...] = ()
    symbol_ids: tuple[str, ...] = ()
    raw_storage_mode: str = "store_full"
    http_visibility: str = "managed_http"
    parser_status: str | None = None
    date_range_start: date | datetime | None = None
    date_range_end: date | datetime | None = None
    license_policy: dict[str, object] | None = None


class _BrokenAttemptLog(AttemptLog):
    def record(self, **kwargs: object) -> tuple[str, ...]:  # type: ignore[override]
        raise RuntimeError("write failed")


def test_attempt_log_rejects_credential_missing_remote_success() -> None:
    attempt_log = AttemptLog()
    batch = _Batch(provider_id="paid-provider", endpoint_id="daily")
    fetch_result = SimpleNamespace(status="credential_missing", fetched_at=datetime(2026, 6, 9, tzinfo=UTC))

    with pytest.raises(ValueError, match="credential_missing must not be marked remote_success"):
        attempt_log.record(batch=batch, fetch_result=fetch_result, remote_success=True)


def test_attempt_log_records_http_rate_limit_origin() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    attempt_log = AttemptLog(repository=repo)
    batch = _Batch(provider_id="crypto_coinglass_derivatives", endpoint_id="futures_funding_rate")
    sent_at = datetime(2026, 6, 9, 12, 0, tzinfo=UTC)
    fetch_result = SimpleNamespace(
        status="rate_limited",
        fetched_at=datetime(2026, 6, 9, tzinfo=UTC),
        error_code="RuntimeError",
        error_message="http_429",
        http_observations=(
            SimpleNamespace(
                request_key="http:req",
                sent_at=sent_at,
                method="GET",
                host="proxy.keystore.com.cn",
                path="/api/futures/funding-rate/history",
                status_code=429,
                quota_signal="http_429",
                error_code=None,
                elapsed_ms=120,
                response_headers_redacted={"retry-after": "60"},
            ),
        ),
    )

    (attempt_ref,) = attempt_log.record(
        batch=batch,
        fetch_result=fetch_result,
        gaps=(DataGap.by_reason("rate_limited", evidence_refs=("http:req",)),),
    )
    record = repo.get_provider_attempt(attempt_ref)

    assert record is not None
    assert record["http_status_codes"] == (429,)
    assert record["quota_signals"] == ("http_429",)
    assert record["rate_limit_origin"] == "remote"
    assert record["http_observations"][0]["sent_at"] == sent_at
    assert record["http_observations"][0]["response_headers_redacted"] == {"retry-after": "60"}


def test_attempt_log_treats_tool_budget_rate_limit_as_local() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    attempt_log = AttemptLog(repository=repo)
    batch = _Batch(provider_id="crypto_coinglass_derivatives", endpoint_id="futures_funding_rate")
    fetch_result = SimpleNamespace(
        status="rate_limited",
        fetched_at=datetime(2026, 6, 9, tzinfo=UTC),
        error_code="RuntimeError",
        error_message="rate_limited_by_tool_budget",
        http_observations=(
            SimpleNamespace(
                request_key="http:req",
                sent_at=datetime(2026, 6, 9, 12, 0, tzinfo=UTC),
                method="GET",
                host="proxy.keystore.com.cn",
                path="/api/futures/funding-rate/history",
                status_code=None,
                quota_signal="rate_limited_by_tool_budget",
                error_code=None,
                elapsed_ms=0,
                response_headers_redacted={},
            ),
        ),
    )

    (attempt_ref,) = attempt_log.record(
        batch=batch,
        fetch_result=fetch_result,
        gaps=(DataGap.by_reason("rate_limited_by_tool_budget", evidence_refs=("http:req",)),),
    )
    record = repo.get_provider_attempt(attempt_ref)

    assert record is not None
    assert record["quota_signals"] == ("rate_limited_by_tool_budget",)
    assert record["rate_limit_origin"] == "local"
    assert record["remote_attempted"] is False


def test_ingest_pipeline_success_writes_raw_normalized_attempt_refs() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch()
    result = FetchResult.from_success(batch, payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}])
    ingest = pipeline.ingest(result, batch)
    assert ingest.status == "ingested"
    assert ingest.remote_success is True
    assert ingest.raw_refs
    assert ingest.dataset_refs
    assert ingest.attempt_refs


def test_ingest_pipeline_normalizes_official_rows_even_when_batch_uses_raw_shape() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        provider_id="official_api_coinglass",
        endpoint_id="coinglass.futures_open_interest",
        market="CRYPTO",
        data_type="official_api_response",
        granularity="realtime",
        fields_union=("open_interest", "open_interest_unit", "timestamp", "symbol_id"),
        capability_fields=("open_interest", "open_interest_unit", "timestamp", "symbol_id"),
        exchange="COINGLASS",
        currency="USDT",
        timezone="UTC",
        calendar="CRYPTO_24_7",
        base_asset="BTC",
        quote_asset="USDT",
        symbol_ids=("BTC/USDT",),
        parser_status="parser_missing",
    )
    result = FetchResult.from_success(
        batch,
        payload={
            "rows": [
                {
                    "dataset": "crypto_derivative_metric",
                    "market": "CRYPTO",
                    "symbol_id": "BTC/USDT",
                    "granularity": "realtime",
                    "timestamp": datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
                    "open_interest": 123.4,
                    "open_interest_unit": "USD",
                    "base_asset": "BTC",
                    "quote_asset": "USDT",
                }
            ]
        },
    )

    ingest = pipeline.ingest(result, batch)

    assert ingest.dataset_refs
    assert not any(gap.reason == "parser_missing" for gap in ingest.gaps)
    stored = repo.get_normalized_document_for_maintenance(ingest.dataset_refs[0])
    assert stored is not None
    assert stored["dataset"] == "crypto_derivative_metric"


def test_ingest_pipeline_normalizes_tuple_rows_from_provider_payload() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        provider_id="crypto_binance_spot_market",
        endpoint_id="futures_open_interest",
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="realtime",
        fields_union=("open_interest", "open_interest_unit", "timestamp", "symbol_id"),
        capability_fields=("open_interest", "open_interest_unit", "timestamp", "symbol_id"),
        exchange="BINANCE",
        currency="USDT",
        timezone="UTC",
        calendar="CRYPTO_24_7",
        base_asset="BTC",
        quote_asset="USDT",
        symbol_ids=("BTCUSDT",),
    )
    result = FetchResult.from_success(
        batch,
        payload={
            "rows": (
                {
                    "dataset": "crypto_derivative_metric",
                    "market": "CRYPTO",
                    "symbol_id": "BTCUSDT",
                    "granularity": "realtime",
                    "timestamp": datetime(2026, 6, 12, 12, 0, tzinfo=UTC),
                    "open_interest": 123.4,
                    "open_interest_unit": "BTC",
                    "exchange": "BINANCE",
                    "currency": "USDT",
                    "timezone": "UTC",
                    "calendar": "CRYPTO_24_7",
                    "base_asset": "BTC",
                    "quote_asset": "USDT",
                },
            )
        },
    )

    ingest = pipeline.ingest(result, batch)

    assert ingest.dataset_refs
    assert not any(gap.reason == "field_missing" for gap in ingest.gaps)
    stored = repo.get_normalized_document_for_maintenance(ingest.dataset_refs[0])
    assert stored is not None
    assert stored["open_interest"] == 123.4


def test_ingest_pipeline_allows_project_company_profile_rows() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        provider_id="crypto_coingecko_market",
        endpoint_id="coins_id",
        market="CRYPTO",
        data_type="company_profile",
        granularity="event",
        fields_union=("name", "symbol"),
        capability_fields=("name", "symbol", "description", "homepage", "market_cap_rank", "symbol_id"),
        exchange="BINANCE",
        currency="USDT",
        timezone="UTC",
        calendar="CRYPTO_24_7",
        base_asset="BTC",
        quote_asset="USDT",
        symbol_ids=("BTC/USDT",),
        request_ids=("request:company-profile",),
    )
    result = FetchResult.from_success(
        batch,
        payload={
            "rows": [
                {
                    "dataset": "company_profile",
                    "market": "CRYPTO",
                    "symbol_id": "BTC/USDT",
                    "granularity": "event",
                    "name": "Bitcoin",
                    "symbol": "btc",
                    "description": "Bitcoin profile",
                    "homepage": "https://bitcoin.org",
                    "market_cap_rank": 1,
                    "timestamp": datetime(2026, 6, 15, tzinfo=UTC),
                    "base_asset": "BTC",
                    "quote_asset": "USDT",
                }
            ]
        },
    )

    ingest = pipeline.ingest(result, batch)

    assert ingest.status == "ingested"
    assert ingest.remote_success is True
    assert ingest.raw_refs
    assert ingest.dataset_refs
    stored = repo.get_normalized_document_for_maintenance(ingest.dataset_refs[0])
    assert stored is not None
    assert stored["dataset"] == "company_profile"
    assert stored["row"]["name"] == "Bitcoin"


def test_ingest_pipeline_keeps_raw_only_official_response_as_parser_missing() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        provider_id="official_api_coinglass",
        endpoint_id="coinglass.raw_user_account_subscription",
        market="CRYPTO",
        data_type="official_api_response",
        granularity="event",
        fields_union=("provider_endpoint", "raw_payload"),
        parser_status="parser_missing",
    )
    result = FetchResult.from_success(
        batch,
        payload={
            "rows": [
                {
                    "provider_id": "official_api_coinglass",
                    "provider_endpoint": "/api/user/account/subscription",
                    "raw_payload": {"code": "0", "data": {"plan": "pro"}},
                }
            ]
        },
    )

    ingest = pipeline.ingest(result, batch)

    assert ingest.status == "partial"
    assert ingest.remote_success is True
    assert ingest.dataset_refs == ()
    assert ingest.raw_refs
    assert ingest.attempt_refs
    assert any(gap.reason == "parser_missing" for gap in ingest.gaps)


def test_ingest_pipeline_marks_sdk_internal_success_as_unaudited_remote() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        provider_id="cn_a_tushare_realtime",
        endpoint_id="realtime_quote",
        data_type="quote_snapshot",
        granularity="realtime",
        fields_union=("price", "timestamp", "symbol_id"),
        symbol_ids=("600519.SH",),
        http_visibility="sdk_internal_unknown",
    )
    result = FetchResult.from_success(
        batch,
        payload=[
            {
                "dataset": "quote_snapshot",
                "market": "CN_A",
                "symbol_id": "600519.SH",
                "granularity": "realtime",
                "period_start": date(2026, 6, 12),
                "period_end": date(2026, 6, 12),
                "price": 1291.91,
                "timestamp": datetime(2026, 6, 12, 15, 0, tzinfo=UTC),
            }
        ],
    )

    ingest = pipeline.ingest(result, batch)

    assert ingest.status == "ingested"
    assert ingest.remote_success is False
    assert ingest.raw_refs
    assert ingest.dataset_refs
    assert ingest.attempt_refs
    attempt = repo.get_provider_attempt(ingest.attempt_refs[0])
    assert attempt is not None
    assert attempt["http_visibility"] == "sdk_internal_unknown"
    assert attempt["http_audit_status"] == "sdk_internal_unknown"
    assert attempt["remote_success"] is False


def test_ingest_pipeline_checks_only_fields_declared_by_endpoint_capability() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="1h",
        fields_union=("funding_rate", "taker_buy_volume", "taker_sell_volume"),
        capability_fields=("funding_rate", "timestamp", "symbol_id"),
        exchange="COINGLASS",
        currency="USDT",
        timezone="UTC",
        calendar="CRYPTO_24_7",
        base_asset="SOL",
        quote_asset="USDT",
        request_ids=("req-funding",),
        symbol_ids=("SOLUSDT",),
        date_range_start=date(2026, 6, 10),
        date_range_end=date(2026, 6, 10),
    )
    result = FetchResult.from_success(
        batch,
        payload=[
            {
                "dataset": "crypto_derivative_metric",
                "symbol_id": "SOLUSDT",
                "funding_rate": 0.0001,
                "timestamp": datetime(2026, 6, 10, 0, 0, tzinfo=UTC),
            }
        ],
    )

    ingest = pipeline.ingest(result, batch)

    assert ingest.status == "ingested"
    field_missing = [gap for gap in ingest.gaps if gap.reason == "field_missing"]
    assert not field_missing


def test_normalized_store_writes_one_checksum_for_batch() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    store = NormalizedStore(repository=repo)

    refs = store.upsert(
        (
            {
                "dataset": "daily_bar",
                "market": "CN_A",
                "symbol_id": "000001.SZ",
                "granularity": "daily",
                "period_start": date(2026, 5, 31),
                "period_end": date(2026, 5, 31),
                "close": 10.2,
                "exchange": "SZSE",
                "currency": "CNY",
                "timezone": "Asia/Shanghai",
                "calendar": "CN_A_SSE_SZSE",
                "base_asset": None,
                "quote_asset": None,
                "provider_lineage": {"provider_id": "test", "endpoint_id": "daily"},
                "schema_id": "daily_bar.v1",
                "quality_flags": (),
            },
            {
                "dataset": "daily_bar",
                "market": "CN_A",
                "symbol_id": "600519.SH",
                "granularity": "daily",
                "period_start": date(2026, 5, 31),
                "period_end": date(2026, 5, 31),
                "close": 1530.25,
                "exchange": "SSE",
                "currency": "CNY",
                "timezone": "Asia/Shanghai",
                "calendar": "CN_A_SSE_SZSE",
                "base_asset": None,
                "quote_asset": None,
                "provider_lineage": {"provider_id": "test", "endpoint_id": "daily"},
                "schema_id": "daily_bar.v1",
                "quality_flags": (),
            },
        )
    )

    documents = tuple(repo.get_normalized_document_for_maintenance(ref) for ref in refs)
    assert len(documents) == 2
    checksums = {str(document["dataset_checksum"]) for document in documents if document is not None}
    assert len(checksums) == 1
    assert all(document is not None and document["dataset_row_count"] == 2 for document in documents)
    assert all(
        document is not None and document["dataset_checksum_scope"] == "normalized-batch-v1"
        for document in documents
    )


def test_normalized_store_accepts_cn_a_northbound_and_margin_datasets() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    refs = NormalizedStore(repository=repo).upsert(
        (
            {
                "dataset": "northbound_flow",
                "market": "CN_A",
                "symbol_id": "600519.SH",
                "granularity": "realtime",
                "period_start": date(2026, 6, 14),
                "period_end": date(2026, 6, 14),
                "timestamp": datetime(2026, 6, 14, 10, 0, tzinfo=UTC),
                "hgt_net": 1.0,
                "sgt_net": 2.0,
                "northbound_net": 3.0,
                "exchange": "SSE",
                "currency": "CNY",
                "timezone": "Asia/Shanghai",
                "calendar": "CN_A_SSE_SZSE",
                "base_asset": None,
                "quote_asset": None,
                "provider_lineage": {"provider_id": "cn_a_astock_signal_social", "endpoint_id": "northbound_flow"},
                "schema_id": "northbound_flow.v1",
                "quality_flags": (),
            },
            {
                "dataset": "margin_trading",
                "market": "CN_A",
                "symbol_id": "600519.SH",
                "granularity": "daily",
                "period_start": date(2026, 6, 12),
                "period_end": date(2026, 6, 12),
                "financing_balance": 100.0,
                "margin_balance": 120.0,
                "exchange": "SSE",
                "currency": "CNY",
                "timezone": "Asia/Shanghai",
                "calendar": "CN_A_SSE_SZSE",
                "base_asset": None,
                "quote_asset": None,
                "provider_lineage": {"provider_id": "cn_a_eastmoney_market_data", "endpoint_id": "margin_trading_detail"},
                "schema_id": "margin_trading.v1",
                "quality_flags": (),
            },
        )
    )

    assert refs[0].startswith("dataset:northbound_flow:CN_A:")
    assert refs[1].startswith("dataset:margin_trading:CN_A:")


def test_ingest_pipeline_normalizes_trade_date_and_returns_auditable_dataset_ref() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        granularity="DAILY",
        date_range_start=date(2026, 5, 31),
        date_range_end=date(2026, 5, 31),
    )
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "trade_date": "20260531", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)

    assert ingest.dataset_refs
    stored = repo.get_normalized_document_for_maintenance(ingest.dataset_refs[0])
    assert stored is not None
    assert stored["period_start"] == date(2026, 5, 31)
    assert stored["period_end"] == date(2026, 5, 31)
    assert stored["source_raw_refs"] == ingest.raw_refs

    warehouse_result = Warehouse(repo).query(
        {
            "request_id": "req-daily",
            "dataset": "daily_bar",
            "market": "CN_A",
            "symbol_id": "000001.SZ",
            "granularity": "daily",
            "fields": ("close",),
            "date_range_start": date(2026, 5, 31),
            "date_range_end": date(2026, 5, 31),
            "freshness_policy": "trading_day",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
            "as_of": datetime(2026, 5, 31, 18, 0, tzinfo=UTC),
        }
    )
    assert warehouse_result.status == "ready"
    assert warehouse_result.dataset_refs == ingest.dataset_refs


def test_ingest_pipeline_preserves_universe_ref_for_warehouse_recheck() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        universe_ref="all_a_shares",
        date_range_start=date(2026, 6, 4),
        date_range_end=date(2026, 6, 4),
    )
    result = FetchResult.from_success(
        batch,
        payload=[
            {"dataset": "daily_bar", "symbol_id": "000001.SZ", "trade_date": "20260604", "close": 10.2},
            {"dataset": "daily_bar", "symbol_id": "430047.BJ", "trade_date": "20260604", "close": 15.3},
        ],
    )

    ingest = pipeline.ingest(result, batch)

    assert len(ingest.dataset_refs) == 2
    stored_documents = tuple(repo.get_normalized_document_for_maintenance(ref) for ref in ingest.dataset_refs)
    assert all(document is not None and document["universe_ref"] == "all_a_shares" for document in stored_documents)
    assert all(
        document is not None and document["row"]["universe_ref"] == "all_a_shares"
        for document in stored_documents
    )

    warehouse_result = Warehouse(repo).query(
        {
            "request_id": "req-universe",
            "dataset": "daily_bar",
            "market": "CN_A",
            "universe_ref": "all_a_shares",
            "granularity": "daily",
            "fields": ("close",),
            "date_range_start": date(2026, 6, 4),
            "date_range_end": date(2026, 6, 4),
            "freshness_policy": "warehouse_only",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
            "as_of": datetime(2026, 6, 4, 18, 0, tzinfo=UTC),
        }
    )

    assert warehouse_result.status == "ready"
    assert set(warehouse_result.dataset_refs) == set(ingest.dataset_refs)


def test_ingest_pipeline_requested_date_range_without_date_field_produces_datagap() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(date_range_start=date(2026, 5, 31), date_range_end=date(2026, 5, 31))
    result = FetchResult.from_success(batch, payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}])
    ingest = pipeline.ingest(result, batch)

    assert ingest.dataset_refs == ()
    assert any(gap.reason == "date_range_missing" for gap in ingest.gaps)


def test_ingest_pipeline_stores_partial_rows_when_provider_date_range_is_short() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        date_range_start=date(2026, 5, 1),
        date_range_end=date(2026, 5, 31),
    )
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "date": "2026-05-15", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)

    assert ingest.remote_success is False
    assert ingest.dataset_refs
    assert any(gap.reason == "date_range_missing" for gap in ingest.gaps)


def test_ingest_pipeline_does_not_apply_daily_range_gap_to_quarterly_financial_rows() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        data_type="financial_metric",
        granularity="quarterly",
        fields_union=("roe",),
        date_range_start=date(2025, 6, 13),
        date_range_end=date(2026, 6, 13),
    )
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "financial_metric", "symbol_id": "600519.SH", "period": "2026-03-31", "roe": 10.57}],
    )
    ingest = pipeline.ingest(result, batch)

    assert ingest.remote_success is True
    assert ingest.dataset_refs
    assert not any(gap.reason == "date_range_missing" for gap in ingest.gaps)


def test_ingest_pipeline_allows_non_crypto_daily_end_date_to_land_on_non_trading_day() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        date_range_start=date(2026, 6, 12),
        date_range_end=date(2026, 6, 13),
    )
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "date": "2026-06-12", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)

    assert ingest.remote_success is True
    assert ingest.dataset_refs
    assert not any(gap.reason == "date_range_missing" for gap in ingest.gaps)


def test_ingest_pipeline_crypto_daily_keeps_strict_date_range_for_24_7_calendar() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        market="CRYPTO",
        data_type="daily_bar",
        exchange="BINANCE",
        currency="USDT",
        timezone="UTC",
        calendar="CRYPTO_24_7",
        base_asset="BTC",
        quote_asset="USDT",
        symbol_ids=("BTCUSDT",),
        date_range_start=date(2026, 6, 12),
        date_range_end=date(2026, 6, 13),
    )
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "BTCUSDT", "date": "2026-06-12", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)

    assert ingest.remote_success is False
    assert ingest.dataset_refs
    assert any(gap.reason == "date_range_missing" for gap in ingest.gaps)


def test_ingest_pipeline_crypto_onchain_allows_latest_confirmed_daily_lag() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        market="CRYPTO",
        data_type="crypto_onchain_metric",
        fields_union=("metric", "value", "timestamp"),
        exchange="COINGLASS",
        currency="USDT",
        timezone="UTC",
        calendar="CRYPTO_24_7",
        base_asset="BTC",
        quote_asset="USDT",
        symbol_ids=("BTC/USDT",),
        date_range_start=date(2025, 6, 16),
        date_range_end=date(2026, 6, 16),
    )
    result = FetchResult.from_success(
        batch,
        payload=[
            {
                "dataset": "crypto_onchain_metric",
                "symbol_id": "BTC/USDT",
                "metric": "exchange_balance",
                "value": 2510672.94,
                "timestamp": "2026-06-15T02:00:00Z",
            }
        ],
    )

    ingest = pipeline.ingest(result, batch)

    assert ingest.remote_success is True
    assert ingest.dataset_refs
    assert not any(gap.reason == "date_range_missing" for gap in ingest.gaps)


def test_ingest_pipeline_crypto_onchain_event_rows_do_not_fail_granularity() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        market="CRYPTO",
        data_type="crypto_onchain_metric",
        granularity="event",
        fields_union=("whale_transfer", "timestamp"),
        exchange="COINGLASS",
        currency="USDT",
        timezone="UTC",
        calendar="CRYPTO_24_7",
        base_asset="BTC",
        quote_asset="USDT",
        symbol_ids=("BTC/USDT",),
        date_range_start=date(2025, 6, 16),
        date_range_end=date(2026, 6, 16),
    )
    result = FetchResult.from_success(
        batch,
        payload=[
            {
                "dataset": "crypto_onchain_metric",
                "symbol_id": "BTC/USDT",
                "granularity": "event",
                "whale_transfer": 18403606.87,
                "timestamp": "2026-06-15T02:00:00Z",
            }
        ],
    )

    ingest = pipeline.ingest(result, batch)

    assert ingest.remote_success is True
    assert ingest.dataset_refs
    assert not any(gap.reason == "granularity_mismatch" for gap in ingest.gaps)


def test_crypto_provider_event_period_comes_from_published_at_not_fetch_day() -> None:
    from claw_trade.data_gateway.providers.plugins.crypto import _crypto_base_row
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        provider_id="crypto_project_official_news",
        endpoint_id="github_releases",
        market="CRYPTO",
        data_type="company_news",
        granularity="event",
        fields_union=("title", "published_at", "source", "summary", "url"),
        exchange="GITHUB",
        currency="USDT",
        timezone="UTC",
        calendar="CRYPTO_24_7",
        base_asset="SOL",
        quote_asset="USDT",
        date_range_start=date(2026, 2, 1),
        date_range_end=date(2026, 2, 28),
    )
    row = _crypto_base_row(
        dataset="company_news",
        symbol="SOLUSDT",
        base_asset="SOL",
        quote_asset="USDT",
        provider_id="crypto_project_official_news",
        endpoint_id="github_releases",
        source_role="official",
        granularity="event",
    )
    row.update(
        {
            "title": "Release",
            "published_at": "2026-02-04T08:00:00Z",
            "source": "GitHub:solana-labs/solana",
            "summary": "release notes",
            "url": "https://example.com/release",
        }
    )
    assert "period_start" not in row

    ingest = pipeline.ingest(FetchResult.from_success(batch, payload={"rows": [row]}), batch)

    assert ingest.dataset_refs
    assert not any(gap.reason == "date_range_missing" for gap in ingest.gaps)
    stored = repo.get_normalized_document_for_maintenance(ingest.dataset_refs[0])
    assert stored is not None
    assert stored["period_start"] == date(2026, 2, 4)
    assert stored["period_end"] == date(2026, 2, 4)


def test_normalized_store_keeps_same_period_different_field_sets_separate() -> None:
    from claw_trade.data_gateway.models import WarehouseCheck
    from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse

    repo = DatasetRepository()
    store = NormalizedStore(repository=repo)

    open_interest_refs = store.upsert(
        (
            {
                "dataset": "crypto_derivative_metric",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "granularity": "realtime",
                "period_start": date(2026, 6, 1),
                "period_end": date(2026, 6, 1),
                "open_interest": 100.0,
                "timestamp": datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
                "exchange": "COINGLASS",
                "currency": "USDT",
                "timezone": "UTC",
                "calendar": "CRYPTO_24_7",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "provider_lineage": {"provider_id": "coinglass", "endpoint_id": "open_interest"},
                "schema_id": "crypto_derivative_metric.v1",
                "quality_flags": (),
                "as_of": datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
                "fresh_until": datetime(2026, 6, 1, 13, 0, tzinfo=UTC),
            },
        )
    )
    netflow_refs = store.upsert(
        (
            {
                "dataset": "crypto_derivative_metric",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "granularity": "realtime",
                "period_start": date(2026, 6, 1),
                "period_end": date(2026, 6, 1),
                "net_inflow": 25.0,
                "timestamp": datetime(2026, 6, 1, 12, 5, tzinfo=UTC),
                "exchange": "COINGLASS",
                "currency": "USDT",
                "timezone": "UTC",
                "calendar": "CRYPTO_24_7",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "provider_lineage": {"provider_id": "coinglass", "endpoint_id": "netflow"},
                "schema_id": "crypto_derivative_metric.v1",
                "quality_flags": (),
                "as_of": datetime(2026, 6, 1, 12, 5, tzinfo=UTC),
                "fresh_until": datetime(2026, 6, 1, 13, 5, tzinfo=UTC),
            },
        )
    )

    assert open_interest_refs != netflow_refs
    assert repo.get_normalized_document_for_maintenance(open_interest_refs[0])["row"]["open_interest"] == 100.0
    assert repo.get_normalized_document_for_maintenance(netflow_refs[0])["row"]["net_inflow"] == 25.0

    result = Warehouse(repo).check(
        (
            WarehouseCheck(
                request_id="req-open-interest",
                market="CRYPTO",
                symbol_id="BTCUSDT",
                data_type="crypto_derivative_metric",
                granularity="realtime",
                fields=("open_interest",),
                date_range_start=date(2026, 6, 1),
                date_range_end=date(2026, 6, 1),
                freshness_policy="immutable_seed",
                timezone="UTC",
                calendar="CRYPTO_24_7",
                as_of=datetime(2026, 6, 1, 12, 10, tzinfo=UTC),
            ),
        ),
        None,
    )

    assert result.status == "ready"
    assert not any(gap.reason == "field_missing" for gap in result.gaps)


def test_normalized_store_field_set_excludes_empty_values() -> None:
    from claw_trade.data_gateway.models import WarehouseCheck
    from claw_trade.data_gateway.warehouse import DatasetRepository, Warehouse

    repo = DatasetRepository()
    refs = NormalizedStore(repository=repo).upsert(
        (
            {
                "dataset": "defi_metric",
                "market": "CRYPTO",
                "symbol_id": "BTCUSDT",
                "granularity": "realtime",
                "period_start": date(2026, 6, 1),
                "period_end": date(2026, 6, 1),
                "tvl": None,
                "chains": ("bitcoin",),
                "category": "chain",
                "exchange": "DEFILLAMA",
                "currency": "USD",
                "timezone": "UTC",
                "calendar": "CRYPTO_24_7",
                "base_asset": "BTC",
                "quote_asset": "USDT",
                "provider_lineage": {"provider_id": "defillama", "endpoint_id": "protocol"},
                "schema_id": "defi_metric.v1",
                "quality_flags": (),
                "as_of": datetime(2026, 6, 1, 12, 0, tzinfo=UTC),
                "fresh_until": datetime(2026, 6, 1, 13, 0, tzinfo=UTC),
            },
        )
    )

    stored = repo.get_normalized_document_for_maintenance(refs[0])
    assert "tvl" not in stored["field_set"]

    result = Warehouse(repo).check(
        (
            WarehouseCheck(
                request_id="req-defi",
                market="CRYPTO",
                symbol_id="BTCUSDT",
                data_type="defi_metric",
                granularity="realtime",
                fields=("tvl",),
                freshness_policy="immutable_seed",
                timezone="UTC",
                calendar="CRYPTO_24_7",
                as_of=datetime(2026, 6, 1, 12, 10, tzinfo=UTC),
            ),
        ),
        None,
    )

    assert result.status == "partial"
    assert any(gap.reason == "field_missing" and gap.required_fields == ("tvl",) for gap in result.gaps)


def test_ingest_pipeline_rejects_provider_granularity_mismatch() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(granularity="daily")
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "granularity": "intraday", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)

    assert ingest.dataset_refs == ()
    assert any(gap.reason == "granularity_mismatch" for gap in ingest.gaps)


def test_ingest_accepts_hourly_crypto_metric_for_realtime_analysis_request() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="realtime",
        fields_union=("funding_rate", "timestamp", "symbol_id"),
        exchange="COINGLASS_AGGREGATED",
        currency="USDT",
        timezone="UTC",
        calendar="CRYPTO_24_7",
        base_asset="BTC",
        quote_asset="USDT",
        symbol_ids=("BTC/USDT",),
    )
    result = FetchResult.from_success(
        batch,
        payload=[
            {
                "dataset": "crypto_derivative_metric",
                "symbol_id": "BTC/USDT",
                "granularity": "hourly",
                "timestamp": datetime(2026, 6, 15, 12, 0, tzinfo=UTC),
                "funding_rate": 0.001,
            }
        ],
    )
    ingest = pipeline.ingest(result, batch)

    assert ingest.dataset_refs
    assert not any(gap.reason == "granularity_mismatch" for gap in ingest.gaps)


def test_ingest_pipeline_missing_market_dimensions_produce_datagap() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(exchange=None, currency=None, timezone=None, calendar=None)
    result = FetchResult.from_success(batch, payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}])
    ingest = pipeline.ingest(result, batch)
    assert ingest.remote_success is False
    assert ingest.dataset_refs == ()
    field_missing = [gap for gap in ingest.gaps if gap.reason == "field_missing"]
    assert field_missing
    assert set(field_missing[0].required_fields) >= {"exchange", "currency", "timezone", "calendar"}


def test_ingest_pipeline_non_crypto_with_asset_fields_produces_invalid_request_gap() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(base_asset="BTC", quote_asset="USDT")
    result = FetchResult.from_success(batch, payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}])
    ingest = pipeline.ingest(result, batch)
    assert ingest.remote_success is False
    assert ingest.dataset_refs == ()
    assert any(gap.reason == "invalid_request" for gap in ingest.gaps)


def test_ingest_pipeline_license_metadata_only_keeps_auditable_raw_attempt_refs() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(raw_storage_mode="metadata_only")
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)
    assert ingest.remote_success is True
    assert ingest.raw_refs
    assert ingest.attempt_refs
    raw_doc = repo.get_raw_payload(ingest.raw_refs[0])
    assert raw_doc is not None
    assert raw_doc["storage_mode"] == "metadata_only"
    assert raw_doc["object_uri"] is None
    assert raw_doc["metadata_ref"]
    assert "payload" not in raw_doc
    assert "body" not in raw_doc
    assert "raw_payload" not in raw_doc


def test_ingest_pipeline_license_no_store_keeps_auditable_raw_attempt_refs() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(raw_storage_mode="no_store")
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)
    assert ingest.remote_success is True
    assert ingest.raw_refs
    assert ingest.attempt_refs
    raw_doc = repo.get_raw_payload(ingest.raw_refs[0])
    assert raw_doc is not None
    assert raw_doc["storage_mode"] == "no_store"
    assert raw_doc["object_uri"] is None
    assert raw_doc["metadata_ref"]
    assert "payload" not in raw_doc
    assert "body" not in raw_doc
    assert "raw_payload" not in raw_doc


def test_ingest_pipeline_license_blocked_normalized_storage_keeps_raw_audit_but_no_dataset_ref() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        raw_storage_mode="metadata_only",
        license_policy={"raw_storage_mode": "metadata_only", "normalized_storage_allowed": False},
    )
    result = FetchResult.from_success(
        batch,
        payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "trade_date": "20260531", "close": 10.2}],
    )
    ingest = pipeline.ingest(result, batch)

    assert ingest.remote_success is False
    assert ingest.raw_refs
    assert ingest.dataset_refs == ()
    assert any(gap.reason == "license_blocked" for gap in ingest.gaps)


def test_ingest_pipeline_non_remote_gate_is_not_remote_success() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch()
    gate = GateDecision(kind="cache_hit", refs=ResultRefs(dataset_refs=("dataset:cache",), raw_refs=("raw:cache",)))
    ingest = pipeline.record_gate_result(batch, gate)
    assert ingest.status == "non_remote_recorded"
    assert ingest.remote_success is False
    assert ingest.dataset_refs == ("dataset:cache",)


def test_ingest_pipeline_rate_limited_gate_does_not_crash_and_is_non_remote() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch()
    gate = GateDecision(kind="rate_limited", evidence_refs=("attempt:rate",))
    ingest = pipeline.record_gate_result(batch, gate)
    assert ingest.status == "non_remote_recorded"
    assert ingest.remote_success is False
    assert ingest.attempt_refs
    assert ingest.gaps
    assert ingest.gaps[0].reason == "rate_limited"


def test_ingest_pipeline_cached_empty_gate_uses_current_batch_gap_context() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        request_ids=("req-lockup",),
        symbol_ids=("600519.SH",),
        data_type="lockup_event",
        granularity="event",
    )
    gate = GateDecision.cached_empty(ResultRefs(attempt_refs=("attempt:empty",)))

    ingest = pipeline.record_gate_result(batch, gate)

    assert ingest.status == "non_remote_recorded"
    assert ingest.remote_success is False
    assert ingest.gaps
    gap = ingest.gaps[0]
    assert gap.reason == "cached_empty"
    assert gap.request_id == "req-lockup"
    assert gap.data_type == "lockup_event"
    assert gap.symbol_id == "600519.SH"


def test_ingest_pipeline_shared_error_result_uses_current_batch_gap_context() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        request_ids=("req-sector",),
        symbol_ids=("600519.SH",),
        data_type="sector_snapshot",
        granularity="event",
    )
    gate = GateDecision(kind="shared_result", refs=ResultRefs(attempt_refs=("attempt:failed",)), reason="provider_error")

    ingest = pipeline.record_gate_result(batch, gate)

    assert ingest.status == "non_remote_recorded"
    assert ingest.remote_success is False
    assert ingest.gaps
    gap = ingest.gaps[0]
    assert gap.reason == "provider_error"
    assert gap.request_id == "req-sector"
    assert gap.data_type == "sector_snapshot"
    assert gap.evidence_refs == ("attempt:failed",)


def test_ingest_pipeline_shared_result_with_refs_preserves_material_gap() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        request_ids=("req-cvd",),
        symbol_ids=("BTC/USDT",),
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="hourly",
    )
    gate = GateDecision(
        kind="shared_result",
        refs=ResultRefs(dataset_refs=("dataset:partial",), raw_refs=("raw:partial",), attempt_refs=("attempt:partial",)),
        reason="field_missing",
    )

    ingest = pipeline.record_gate_result(batch, gate)

    assert ingest.status == "non_remote_recorded"
    assert ingest.dataset_refs == ("dataset:partial",)
    assert ingest.gaps
    assert ingest.gaps[0].reason == "field_missing"


def test_ingest_pipeline_rate_limited_gate_preserves_tool_budget_reason() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch()
    gate = GateDecision(kind="rate_limited", reason="rate_limited_by_tool_budget", evidence_refs=("attempt:rate",))

    ingest = pipeline.record_gate_result(batch, gate)

    assert ingest.status == "non_remote_recorded"
    assert ingest.remote_success is False
    assert ingest.gaps
    assert ingest.gaps[0].reason == "rate_limited_by_tool_budget"


def test_ingest_pipeline_fetch_rate_limit_preserves_tool_budget_reason() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch()
    result = SimpleNamespace(
        status="rate_limited",
        fetched_at=datetime(2026, 6, 9, tzinfo=UTC),
        error_message="rate_limited_by_tool_budget",
        http_observations=(
            SimpleNamespace(request_key="http:req", quota_signal="rate_limited_by_tool_budget"),
        ),
    )

    ingest = pipeline.ingest(result, batch)

    assert ingest.remote_success is False
    assert ingest.gaps
    assert ingest.gaps[0].reason == "rate_limited_by_tool_budget"


def test_ingest_pipeline_rate_limited_gate_keeps_batch_context() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=AttemptLog(),
    )
    batch = _Batch(
        market="CRYPTO",
        data_type="crypto_derivative_metric",
        granularity="realtime",
        request_ids=("run:test:report-prefetch:market:13:crypto_derivative_metric",),
        symbol_ids=("BNBUSDT",),
    )
    gate = GateDecision(kind="cooldown_skipped", evidence_refs=("rate_limit:ratelimit:coinglass",))

    ingest = pipeline.record_gate_result(batch, gate)

    assert ingest.gaps
    gap = ingest.gaps[0]
    assert gap.reason == "cooldown_skipped"
    assert gap.market == "CRYPTO"
    assert gap.symbol_id == "BNBUSDT"
    assert gap.request_id == "run:test:report-prefetch:market:13:crypto_derivative_metric"
    assert gap.data_type == "crypto_derivative_metric"


def test_ingest_pipeline_records_selector_credential_skip_as_non_remote_attempt() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        provider_id="paid-provider",
        endpoint_id="daily",
        market="US",
        request_ids=("req-paid",),
        symbol_ids=("AAPL",),
    )
    selector_skip = SimpleNamespace(
        reason="credential_missing",
        remote_attempted=False,
        credential_names=("data_source:paid",),
    )

    ingest = pipeline.record_selector_skip(batch, selector_skip)

    assert ingest.status == "non_remote_recorded"
    assert ingest.remote_success is False
    assert ingest.attempt_refs
    assert [gap.reason for gap in ingest.gaps] == ["credential_missing"]
    assert ingest.gaps[0].human_readable == "credential_missing:data_source:paid"
    attempt = repo.get_provider_attempt(ingest.attempt_refs[0])
    assert attempt is not None
    assert attempt["provider"] == "paid-provider"
    assert attempt["endpoint"] == "daily"
    assert attempt["status"] == "credential_missing"
    assert attempt["remote_attempted"] is False
    assert attempt["remote_success"] is False
    assert attempt["gap_codes"] == ("credential_missing",)


def test_ingest_pipeline_records_unconfigured_selector_skip_without_user_gap() -> None:
    from claw_trade.data_gateway.warehouse import DatasetRepository

    repo = DatasetRepository()
    pipeline = IngestPipeline(
        raw_store=RawStore(repository=repo),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(repository=repo),
        attempt_log=AttemptLog(repository=repo),
    )
    batch = _Batch(
        provider_id="crypto_glassnode_onchain",
        endpoint_id="deep_onchain_metrics",
        market="CRYPTO",
        data_type="crypto_onchain_metric",
        granularity="daily",
        request_ids=("req-glassnode",),
        symbol_ids=("BTCUSDT",),
    )
    selector_skip = SimpleNamespace(
        reason="source_not_configured",
        remote_attempted=False,
        credential_names=("data_source:glassnode",),
    )

    ingest = pipeline.record_selector_skip(batch, selector_skip)

    assert ingest.status == "non_remote_recorded"
    assert ingest.remote_success is False
    assert ingest.gaps == ()
    attempt = repo.get_provider_attempt(ingest.attempt_refs[0])
    assert attempt is not None
    assert attempt["status"] == "source_not_configured"
    assert attempt["remote_attempted"] is False
    assert attempt["remote_success"] is False
    assert attempt["gap_codes"] == ()


def test_ingest_pipeline_evidence_write_failed_is_fail_closed() -> None:
    pipeline = IngestPipeline(
        raw_store=RawStore(),
        normalizer=Normalizer(),
        normalized_store=NormalizedStore(),
        attempt_log=_BrokenAttemptLog(),
    )
    batch = _Batch()
    result = FetchResult.from_success(batch, payload=[{"dataset": "daily_bar", "symbol_id": "000001.SZ", "close": 10.2}])
    ingest = pipeline.ingest(result, batch)
    assert isinstance(ingest, IngestResult)
    assert ingest.status == "failed"
    assert ingest.remote_success is False
    assert ingest.gaps
    assert ingest.gaps[0].reason == "evidence_write_failed"
