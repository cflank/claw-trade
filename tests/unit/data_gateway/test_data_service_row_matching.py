from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from claw_trade.data_gateway.coordination.service import DataService, _data_request_from_need
from claw_trade.data_gateway.execution.rate_limiter import RateLimitPolicy
from claw_trade.data_gateway.models import CoverageRequirement, DataRequest, IngestResult, Market, QueryPlan, WarehouseCheck, WarehouseResult
from claw_trade.data_gateway.needs import DataNeed, ProviderCallSpec


def _request(*, symbol_id: str, market: Market = Market.CRYPTO) -> DataRequest:
    return DataRequest(
        request_id="req-1",
        market=market,
        symbol_id=symbol_id,
        universe_ref=None,
        timezone="UTC",
        calendar="CRYPTO_24_7" if market == Market.CRYPTO else "US_NYSE",
        base_asset="BTC" if market == Market.CRYPTO else None,
        quote_asset="USDT" if market == Market.CRYPTO else None,
        data_type="quote_snapshot",
        granularity="realtime",
        fields=("price", "timestamp", "symbol_id"),
        freshness_policy="realtime",
        consumer="price_alert",
        consumer_id="price_alert",
        as_of=datetime(2026, 6, 25, 12, 24, tzinfo=UTC),
    )


def test_row_matches_crypto_default_usdt_alias() -> None:
    request = _request(symbol_id="BTC")

    assert DataService._row_matches_request(
        {
            "dataset": "quote_snapshot",
            "market": "CRYPTO",
            "symbol_id": "BTCUSDT",
            "granularity": "realtime",
            "price": 61102.0,
            "timestamp": "2026-06-25T12:24:48Z",
        },
        request,
    )
    assert not DataService._row_matches_request(
        {
            "dataset": "quote_snapshot",
            "market": "CRYPTO",
            "symbol_id": "ETHUSDT",
            "granularity": "realtime",
            "price": 2400.0,
            "timestamp": "2026-06-25T12:24:48Z",
        },
        request,
    )


def test_row_matching_keeps_non_crypto_symbols_strict() -> None:
    request = _request(symbol_id="BTC", market=Market.US)

    assert not DataService._row_matches_request(
        {
            "dataset": "quote_snapshot",
            "market": "US",
            "symbol_id": "BTCUSDT",
            "granularity": "realtime",
            "price": 61102.0,
            "timestamp": "2026-06-25T12:24:48Z",
        },
        request,
    )


def test_cache_hit_ingest_result_rehydrates_rows_from_warehouse() -> None:
    row = {
        "dataset": "quote_snapshot",
        "market": "CRYPTO",
        "symbol_id": "BTCUSDT",
        "granularity": "realtime",
        "price": 61102.0,
        "timestamp": "2026-06-25T12:24:48Z",
    }
    service = DataService(
        query_planner=_Planner(),
        warehouse=_Warehouse(row),
        execution_gate=object(),
        fetch_engine=object(),
        ingest=object(),
    )
    need = DataNeed(
        need_id="price-alert:CRYPTO:BTC:20260625122448",
        api_id="crypto.realtime_quote",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime",
        requested_by_worker="price_alert_quote_provider",
        purpose="price_alert_quote",
        freshness_policy="realtime",
        deadline_at=datetime(2026, 6, 25, 12, 24, 58, tzinfo=UTC),
        consumer="price_alert",
    )
    ingest = IngestResult.from_refs(
        dataset_refs=("dataset:quote_snapshot:CRYPTO:4422d139bfa4f92e",),
        raw_refs=(),
        attempt_refs=("attempt:cache-hit",),
        gaps=(),
        remote_success=False,
    )

    result = service._data_result_from_ingest(need=need, ingest=ingest)

    assert result.rows == (row,)


def test_price_alert_provider_batch_bypasses_provider_cache() -> None:
    service = DataService(
        query_planner=object(),
        warehouse=object(),
        execution_gate=object(),
        fetch_engine=object(),
        ingest=object(),
    )
    need = DataNeed(
        need_id="price-alert:CRYPTO:BTC:20260625122448",
        api_id="crypto.realtime_quote",
        market=Market.CRYPTO,
        instrument="BTC",
        granularity="realtime",
        requested_by_worker="price_alert_quote_provider",
        purpose="price_alert_quote",
        freshness_policy="realtime",
        deadline_at=datetime(2026, 6, 25, 12, 24, 58, tzinfo=UTC),
        consumer="price_alert",
    )
    call = ProviderCallSpec(
        call_id="call:binance:ticker",
        provider_id="crypto_binance_spot_market",
        catalog_endpoint_id="binance.ticker_24hr",
        official_path_or_api_name="/api/v3/ticker/24hr",
        params={"symbol": "BTCUSDT"},
        auth_scope="none",
        rate_limit_bucket="binance",
        http_visibility="managed_http",
        parser_status="normalized",
        batch_key="batch:binance:ticker:BTC",
        official_doc_ref="https://developers.binance.com/docs/binance-spot-api-docs/rest-api/market-data-endpoints",
        deadline_at=datetime(2026, 6, 25, 12, 24, 58, tzinfo=UTC),
        need_ids=(need.need_id,),
    )

    batch = service._provider_call_batch(call=call, need=need, policy=RateLimitPolicy(window_seconds=60, max_requests=None))

    assert batch.ignore_provider_cache is True
    assert batch.ignore_cached_empty is True


def test_all_a_shares_need_queries_warehouse_by_universe_ref_not_symbol() -> None:
    need = DataNeed(
        need_id="maintenance:CN_A:daily_bar:all_a_shares:2026-06-24:2026-06-30",
        api_id="cn_a.daily_bar",
        market=Market.CN_A,
        instrument="all_a_shares",
        granularity="daily",
        requested_by_worker="openclaw_cron",
        purpose="scheduled_data_maintenance",
        freshness_policy="trading_day",
        deadline_at=datetime(2026, 7, 1, 0, 21, tzinfo=UTC),
        consumer="maintenance",
    )

    request = _data_request_from_need(need)

    assert request.symbol_id is None
    assert request.universe_ref == "all_a_shares"


class _Planner:
    def validate_and_normalize(self, request: DataRequest) -> QueryPlan:
        return QueryPlan(
            normalized_requests=(request,),
            warehouse_checks=(
                WarehouseCheck(
                    request_id=request.request_id,
                    market=request.market,
                    symbol_id=request.symbol_id,
                    universe_ref=request.universe_ref,
                    data_type=request.data_type,
                    granularity=request.granularity,
                    fields=request.fields,
                    date_range_start=request.date_range_start,
                    date_range_end=request.date_range_end,
                    freshness_policy=request.freshness_policy,
                    timezone=request.timezone,
                    calendar=request.calendar,
                    as_of=request.as_of,
                ),
            ),
            required_coverage=CoverageRequirement(request_ids=(request.request_id,), expected_outputs=(), required_fields_by_request={}),
            expected_outputs=(),
        )


class _Warehouse:
    def __init__(self, row: dict[str, Any]) -> None:
        self._row = row

    def check(self, checks: object, _coverage: object) -> WarehouseResult:
        assert tuple(checks)[0].symbol_id == "BTCUSDT"
        return WarehouseResult(
            satisfied=True,
            rows=(self._row,),
            dataset_refs=("dataset:quote_snapshot:CRYPTO:4422d139bfa4f92e",),
            attempt_refs=("attempt:remote-success",),
        )
