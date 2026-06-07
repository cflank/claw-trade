from __future__ import annotations

import pytest
from claw_trade.data_gateway.warehouse import REQUIRED_MULTI_MARKET_FIELDS, DatasetRepository


def _record(market: str, **overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "dataset": "daily_bar",
        "market": market,
        "symbol_id": "600519.SH" if market == "CN_A" else "AAPL",
        "universe_ref": None,
        "granularity": "daily",
        "period_start": None,
        "period_end": None,
        "field_set": ("date", "close"),
        "as_of": None,
        "fresh_until": None,
        "source_roles": ("official",),
        "exchange": "SSE" if market == "CN_A" else "NASDAQ",
        "currency": "CNY" if market == "CN_A" else "USD",
        "timezone": "Asia/Shanghai" if market == "CN_A" else "America/New_York",
        "calendar": "CN_A_SSE_SZSE" if market == "CN_A" else "US_NYSE_NASDAQ",
        "base_asset": None,
        "quote_asset": None,
        "provider_lineage": {"provider": "test", "endpoint": "daily"},
        "schema_id": "daily_bar.v1",
        "quality_flags": (),
    }
    payload.update(overrides)
    return payload


def test_multi_market_required_fields_are_locked() -> None:
    assert REQUIRED_MULTI_MARKET_FIELDS == (
        "market",
        "exchange",
        "currency",
        "timezone",
        "calendar",
        "base_asset",
        "quote_asset",
        "provider_lineage",
        "schema_id",
        "quality_flags",
    )


def test_cn_us_hk_records_can_be_inserted_with_fieldized_market_dimensions() -> None:
    repo = DatasetRepository()
    repo.insert_normalized(_record("CN_A"))
    repo.insert_normalized(_record("US"))
    repo.insert_normalized(
        _record(
            "HK",
            symbol_id="00700.HK",
            exchange="HKEX",
            currency="HKD",
            timezone="Asia/Hong_Kong",
            calendar="HK_HKEX",
        )
    )
    assert len(repo.query_normalized(dataset="daily_bar", market="CN_A", symbol_id="600519.SH", universe_ref=None)) == 1
    assert len(repo.query_normalized(dataset="daily_bar", market="US", symbol_id="AAPL", universe_ref=None)) == 1
    assert len(repo.query_normalized(dataset="daily_bar", market="HK", symbol_id="00700.HK", universe_ref=None)) == 1


def test_crypto_record_requires_base_and_quote_asset() -> None:
    repo = DatasetRepository()
    with pytest.raises(ValueError, match="CRYPTO record requires both base_asset and quote_asset"):
        repo.insert_normalized(
            _record(
                "CRYPTO",
                symbol_id="BTC/USDT",
                exchange="BINANCE",
                currency="USDT",
                timezone="UTC",
                calendar="CRYPTO_24_7",
                base_asset="BTC",
                quote_asset=None,
            )
        )

    repo.insert_normalized(
        _record(
            "CRYPTO",
            symbol_id="BTC/USDT",
            exchange="BINANCE",
            currency="USDT",
            timezone="UTC",
            calendar="CRYPTO_24_7",
            base_asset="BTC",
            quote_asset="USDT",
        )
    )


def test_non_crypto_record_must_not_carry_base_or_quote_asset() -> None:
    repo = DatasetRepository()
    with pytest.raises(ValueError, match="non-CRYPTO record must not include base_asset or quote_asset"):
        repo.insert_normalized(
            _record(
                "US",
                symbol_id="AAPL",
                exchange="NASDAQ",
                currency="USD",
                timezone="America/New_York",
                calendar="US_NYSE_NASDAQ",
                base_asset="BTC",
                quote_asset="USDT",
            )
        )
