from __future__ import annotations

from types import SimpleNamespace

from claw_trade.data_gateway.providers.plugins.crypto import CoinglassCryptoPlugin
from claw_trade.data_gateway.providers.plugins.crypto import _coinglass_query


def test_futures_pairs_markets_query_sends_contract_symbol() -> None:
    spec = CoinglassCryptoPlugin._spec("futures_pairs_markets")
    assert spec is not None

    query = _coinglass_query(
        SimpleNamespace(params={}, granularity="realtime"),
        spec=spec,
        asset="BTC",
        contract="BTCUSDT",
    )

    assert query["symbol"] == "BTCUSDT"
