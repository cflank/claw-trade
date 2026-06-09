from __future__ import annotations

from datetime import UTC, datetime

from claw_trade.data_gateway.crypto_symbols import (
    PHASE1_CORE_ASSETS,
    PHASE1_UNIVERSE_REF,
    build_phase1_core_universe_manifest,
    map_asset_to_symbol,
    split_usdt_symbol,
    symbol_candidates_from_download_manifest,
)


def test_phase1_static_core_asset_mapping() -> None:
    assert map_asset_to_symbol("btc").symbol_id == "BTCUSDT"
    assert map_asset_to_symbol("ETH").symbol_id == "ETHUSDT"
    assert map_asset_to_symbol("sol").symbol_id == "SOLUSDT"


def test_unknown_and_non_usdt_assets_default_to_usdt_pair() -> None:
    assert map_asset_to_symbol("doge").symbol_id == "DOGEUSDT"
    assert map_asset_to_symbol("BTCUSD").symbol_id == "BTCUSDT"
    assert map_asset_to_symbol("ETH/BTC").symbol_id == "ETHUSDT"
    assert map_asset_to_symbol("SOLTRY").symbol_id == "SOLUSDT"


def test_manifest_candidate_overrides_static_mapping_when_active() -> None:
    mapping = map_asset_to_symbol(
        "SOL",
        candidates=(
            {"symbol": "SOLBUSD", "base_asset": "SOL", "quote_asset": "BUSD", "status": "active"},
            {
                "symbol": "SOLUSDT",
                "base_asset": "SOL",
                "quote_asset": "USDT",
                "status": "trading",
                "source": "binance_discovery",
            },
        ),
    )

    assert mapping.symbol_id == "SOLUSDT"
    assert mapping.source == "binance_discovery"
    assert mapping.status == "trading"


def test_inactive_manifest_candidate_is_ignored() -> None:
    mapping = map_asset_to_symbol(
        "BTC",
        candidates=({"symbol": "BTCUSDT", "base_asset": "BTC", "quote_asset": "USDT", "status": "break"},),
    )

    assert mapping.symbol_id == "BTCUSDT"
    assert mapping.source == "phase1_static_core"


def test_phase1_core_universe_manifest_contains_btc_eth_sol() -> None:
    manifest = build_phase1_core_universe_manifest(as_of=datetime(2026, 6, 7, tzinfo=UTC))

    assert manifest["schema_id"] == "crypto_core_universe_manifest.v1"
    assert manifest["universe_ref"] == PHASE1_UNIVERSE_REF
    assert manifest["assets"] == PHASE1_CORE_ASSETS
    assert manifest["symbol_ids"] == ("BTCUSDT", "ETHUSDT", "SOLUSDT")
    assert manifest["as_of"] == "2026-06-07T00:00:00+00:00"


def test_symbol_candidates_from_download_manifest() -> None:
    candidates = symbol_candidates_from_download_manifest(
        {
            "items": (
                {"symbol": "BTCUSDT"},
                {"symbol": "ETHUSDT"},
                {"symbol": "ETHUSDT"},
                {"symbol": "SOLBTC"},
            )
        }
    )

    assert candidates == (
        {
            "symbol": "BTCUSDT",
            "base_asset": "BTC",
            "quote_asset": "USDT",
            "status": "active",
            "source": "download_manifest",
        },
        {
            "symbol": "ETHUSDT",
            "base_asset": "ETH",
            "quote_asset": "USDT",
            "status": "active",
            "source": "download_manifest",
        },
    )


def test_split_usdt_symbol_rejects_non_usdt_symbols() -> None:
    assert split_usdt_symbol("SOLUSDT") == ("SOL", "USDT")
    assert split_usdt_symbol("SOLBTC") == (None, None)
