from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any, Mapping, Sequence

import pytest

from claw_trade.data_gateway.models import DataResultStatus
from claw_trade.data_gateway.price_quote_provider import PriceAlertQuoteProvider
from claw_trade.data_gateway.source_probe import build_price_alert_quote_provider
from claw_trade.ui_contracts.enums import MarketProfile


def _fixed_now() -> datetime:
    return datetime(2026, 6, 17, 12, 0, tzinfo=UTC)


class _FakeDataAPI:
    def __init__(self, result: Any) -> None:
        self.results = tuple(result) if isinstance(result, (list, tuple)) else (result,)
        self.requests: list[Mapping[str, Any]] = []

    def request_data(self, requests: Sequence[Mapping[str, Any]]) -> Sequence[Any]:
        self.requests.extend(requests)
        return list(self.results)


def _ready_result(row: Mapping[str, Any] | Sequence[Mapping[str, Any]], *, dataset_refs: tuple[str, ...] = ("dataset://quote/BTC",)) -> Any:
    rows = tuple(dict(item) for item in row) if isinstance(row, list | tuple) else (dict(row),)
    return SimpleNamespace(
        status=DataResultStatus.READY,
        rows=rows,
        dataset_refs=dataset_refs,
    )


def _partial_result(row: Mapping[str, Any], *, dataset_refs: tuple[str, ...] = ("dataset://quote/BTC",)) -> Any:
    return SimpleNamespace(
        status=DataResultStatus.PARTIAL,
        rows=(dict(row),),
        dataset_refs=dataset_refs,
        gaps=(
            SimpleNamespace(
                gap_type="warehouse_stale",
                message="cached quote is older than the realtime freshness target",
            ),
        ),
    )


def test_source_probe_quote_provider_fails_closed_without_evidence_chain() -> None:
    provider = build_price_alert_quote_provider(now_provider=_fixed_now)

    with pytest.raises(RuntimeError, match="evidence_chain_unavailable"):
        provider("BTC", MarketProfile.CRYPTO)


def test_source_probe_quote_provider_fails_closed_without_data_api() -> None:
    provider = build_price_alert_quote_provider(
        now_provider=_fixed_now,
        evidence_helper=object(),
        cache_store=object(),
        rate_limit_store=object(),
        single_flight=object(),
        attempt_store=object(),
    )

    with pytest.raises(RuntimeError, match="price_alert_quote_unavailable"):
        provider("BTC", MarketProfile.CRYPTO)


def test_price_alert_quote_provider_requests_realtime_quote_and_returns_valid_payload() -> None:
    result = (
        _ready_result(
            {
                "symbol_id": "BTC/USDT",
                "price": 71000.0,
                "timestamp": "2026-06-17T11:59:30Z",
                "percent_change_24h": 2.5,
                "percent_change_intraday": 1.2,
                "evidence_ref": "dataset://normalized/quote_snapshot/BTC",
                "source_market_session": "continuous",
            }
        ),
        _ready_result(
            [
                {
                    "symbol_id": "BTC/USDT",
                    "period_end": datetime(2026, 6, 17, 11, 59, tzinfo=UTC),
                    "high": 71200.0,
                    "low": 70850.0,
                },
                {
                    "symbol_id": "BTC/USDT",
                    "period_start": datetime(2026, 6, 17, 11, 59, 30, tzinfo=UTC),
                    "period_end": datetime(2026, 6, 17, 12, 0, 29, tzinfo=UTC),
                    "high": 71300.0,
                    "low": 70700.0,
                },
            ]
        ),
    )
    api = _FakeDataAPI(result)
    provider = PriceAlertQuoteProvider(data_api=api, now_provider=_fixed_now)

    quote = provider("BTC/USDT", MarketProfile.CRYPTO)

    assert quote == {
        "current_price": 71000.0,
        "quote_timestamp": "2026-06-17T11:59:30Z",
        "evidence_ref": "dataset://normalized/quote_snapshot/BTC",
        "source_market_session": "continuous",
        "percent_change_24h": 2.5,
        "percent_change_intraday": 1.2,
        "window_low": 70700.0,
        "window_high": 71300.0,
        "window_start": "2026-06-17T11:57:00Z",
        "window_end": "2026-06-17T12:00:00Z",
        "window_granularity": "1m",
    }
    assert [request["item"] for request in api.requests] == ["realtime_quote", "intraday_bar"]
    assert api.requests[1]["granularity"] == "1m"
    assert api.requests[1]["time_range_start"] == datetime(2026, 6, 17, 11, 57, tzinfo=UTC)
    assert api.requests[1]["time_range_end"] == _fixed_now()


def test_price_alert_quote_provider_accepts_partial_result_with_valid_quote_row() -> None:
    api = _FakeDataAPI(
        _partial_result(
            {
                "symbol_id": "BTCUSDT",
                "price": 71000.0,
                "timestamp": "2026-06-17T11:59:30Z",
            },
            dataset_refs=("dataset://quote/BTC",),
        )
    )
    provider = PriceAlertQuoteProvider(data_api=api, now_provider=_fixed_now)

    quote = provider("BTC", MarketProfile.CRYPTO)

    assert quote["current_price"] == 71000.0
    assert quote["quote_timestamp"] == "2026-06-17T11:59:30Z"
    assert quote["evidence_ref"] == "dataset://quote/BTC"


@pytest.mark.parametrize(
    "row",
    (
        {"timestamp": "2026-06-17T11:59:30Z", "evidence_ref": "dataset://quote/BTC"},
        {"price": 71000.0, "evidence_ref": "dataset://quote/BTC"},
    ),
)
def test_price_alert_quote_provider_rejects_invalid_quote_payload(row: Mapping[str, Any]) -> None:
    api = _FakeDataAPI(_ready_result(row, dataset_refs=()))
    provider = PriceAlertQuoteProvider(data_api=api, now_provider=_fixed_now)

    with pytest.raises(RuntimeError, match="invalid_quote_payload"):
        provider("BTC/USDT", MarketProfile.CRYPTO)


def test_price_alert_quote_provider_accepts_quote_without_evidence_ref() -> None:
    api = _FakeDataAPI(
        _ready_result(
            {
                "price": 71000.0,
                "timestamp": "2026-06-17T11:59:30Z",
            },
            dataset_refs=(),
        )
    )
    provider = PriceAlertQuoteProvider(data_api=api, now_provider=_fixed_now)

    quote = provider("BTC/USDT", MarketProfile.CRYPTO)

    assert quote["current_price"] == 71000.0
    assert quote["evidence_ref"] is None


def test_price_alert_quote_provider_rejects_stale_quote() -> None:
    api = _FakeDataAPI(
        _ready_result(
            {
                "price": 71000.0,
                "timestamp": "2026-06-17T11:30:00Z",
                "evidence_ref": "dataset://quote/BTC",
            }
        )
    )
    provider = PriceAlertQuoteProvider(data_api=api, now_provider=_fixed_now)

    with pytest.raises(RuntimeError, match="stale_quote_unavailable"):
        provider("BTC/USDT", MarketProfile.CRYPTO)


def test_price_alert_quote_provider_rejects_missing_required_percent_window() -> None:
    api = _FakeDataAPI(
        _ready_result(
            {
                "price": 71000.0,
                "timestamp": "2026-06-17T11:59:30Z",
                "evidence_ref": "dataset://quote/BTC",
            }
        )
    )
    provider = PriceAlertQuoteProvider(data_api=api, now_provider=_fixed_now)

    with pytest.raises(RuntimeError, match="invalid_quote_payload"):
        provider("BTC/USDT", MarketProfile.CRYPTO, required_percent_window="24h")


def test_price_alert_quote_provider_rejects_missing_or_error_result() -> None:
    api = _FakeDataAPI(SimpleNamespace(status=DataResultStatus.MISSING, rows=(), dataset_refs=()))
    provider = PriceAlertQuoteProvider(data_api=api, now_provider=_fixed_now)

    with pytest.raises(RuntimeError, match="price_alert_quote_unavailable"):
        provider("BTC/USDT", MarketProfile.CRYPTO)
