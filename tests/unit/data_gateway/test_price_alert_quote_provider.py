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
        self.result = result
        self.requests: list[Mapping[str, Any]] = []

    def request_data(self, requests: Sequence[Mapping[str, Any]]) -> Sequence[Any]:
        self.requests.extend(requests)
        return [self.result]


def _ready_result(row: Mapping[str, Any], *, dataset_refs: tuple[str, ...] = ("dataset://quote/BTC",)) -> Any:
    return SimpleNamespace(
        status=DataResultStatus.READY,
        rows=(dict(row),),
        dataset_refs=dataset_refs,
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
    result = _ready_result(
        {
            "symbol_id": "BTC/USDT",
            "price": 71000.0,
            "timestamp": "2026-06-17T11:59:30Z",
            "percent_change_24h": 2.5,
            "percent_change_intraday": 1.2,
            "evidence_ref": "dataset://normalized/quote_snapshot/BTC",
            "source_market_session": "continuous",
        }
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
    }
    assert api.requests == [
        {
            "request_id": "price-alert:CRYPTO:BTC/USDT:20260617120000",
            "item": "realtime_quote",
            "market": "CRYPTO",
            "instrument": "BTC/USDT",
            "granularity": "realtime",
            "priority": "required",
            "requested_by_worker": "price_alert_quote_provider",
            "purpose": "price_alert_quote",
            "freshness_policy": "realtime",
            "deadline_at": "2026-06-17T12:00:10+00:00",
            "consumer": "price_alert",
        }
    ]


@pytest.mark.parametrize(
    "row",
    (
        {"timestamp": "2026-06-17T11:59:30Z", "evidence_ref": "dataset://quote/BTC"},
        {"price": 71000.0, "evidence_ref": "dataset://quote/BTC"},
        {"price": 71000.0, "timestamp": "2026-06-17T11:59:30Z"},
    ),
)
def test_price_alert_quote_provider_rejects_invalid_quote_payload(row: Mapping[str, Any]) -> None:
    api = _FakeDataAPI(_ready_result(row, dataset_refs=()))
    provider = PriceAlertQuoteProvider(data_api=api, now_provider=_fixed_now)

    with pytest.raises(RuntimeError, match="invalid_quote_payload"):
        provider("BTC/USDT", MarketProfile.CRYPTO)


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
