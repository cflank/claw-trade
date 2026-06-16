from __future__ import annotations

from datetime import UTC, datetime

from claw_trade.data_gateway.analysis.crypto_lens.input_contract import CryptoLensDomainStatus, CryptoLensInput
from claw_trade.data_gateway.models import DataResult, DataResultStatus


def _result(data_type: str, rows: tuple[dict[str, object], ...]) -> DataResult:
    return DataResult(
        request_id=f"data_need:call:market:0:{data_type}",
        status=DataResultStatus.READY,
        rows=rows,
        dataset_refs=(f"dataset:{data_type}",),
        raw_refs=(f"raw:{data_type}",),
        attempt_refs=(f"attempt:{data_type}",),
        as_of=datetime(2026, 6, 12, tzinfo=UTC),
    )


def _crypto_lens_input(*results: DataResult) -> CryptoLensInput:
    return CryptoLensInput.from_data_results(
        results,
        ticker="BTC",
        quote="USDT",
        run_id="run-test",
        call_id="call-test",
        as_of="2026-06-12T00:00:00Z",
        start_date="2026-06-01",
        end_date="2026-06-12",
    )


def test_crypto_lens_derivatives_rows_do_not_imply_liquidation_map_or_cvd() -> None:
    data = _crypto_lens_input(
        _result(
            "crypto_derivative_metric",
            (
                {
                    "timestamp": "2026-06-12",
                    "funding_rate": 0.01,
                    "open_interest": 1000.0,
                    "long_liquidation": 50.0,
                    "short_liquidation": 25.0,
                },
            ),
        )
    )

    assert data.domain_status["derivatives"] == CryptoLensDomainStatus.READY
    assert data.domain_status["liquidation_map"] == CryptoLensDomainStatus.MISSING
    assert data.domains.liquidation_map is None
    assert data.domains.derivatives is not None
    assert "cvd" not in data.domains.derivatives
    assert "cvd_proxy" not in data.domains.derivatives


def test_crypto_lens_uses_real_cvd_without_taker_proxy() -> None:
    data = _crypto_lens_input(
        _result(
            "crypto_derivative_metric",
            (
                {
                    "timestamp": "2026-06-12",
                    "cvd": -2500.0,
                    "taker_buy_volume": 100.0,
                    "taker_sell_volume": 2600.0,
                    "taker_volume_unit": "USD",
                },
            ),
        )
    )

    assert data.domains.derivatives is not None
    assert data.domains.derivatives["cvd"] == -2500.0
    assert "cvd_proxy" not in data.domains.derivatives


def test_crypto_lens_does_not_compute_cvd_from_taker_volumes() -> None:
    data = _crypto_lens_input(
        _result(
            "crypto_derivative_metric",
            (
                {
                    "timestamp": "2026-06-12",
                    "taker_buy_volume": 100.0,
                    "taker_sell_volume": 80.0,
                    "taker_volume_unit": "USD",
                },
            ),
        )
    )

    assert data.domains.derivatives is not None
    assert data.domains.derivatives["taker_buy_volume"] == 100.0
    assert data.domains.derivatives["taker_sell_volume"] == 80.0
    assert "cvd" not in data.domains.derivatives
    assert "cvd_proxy" not in data.domains.derivatives


def test_crypto_lens_liquidation_heatmap_keeps_source_points_without_aggregates() -> None:
    data = _crypto_lens_input(
        _result(
            "crypto_derivative_metric",
            (
                {
                    "timestamp": "2026-06-12",
                    "liquidation_price": 100000.0,
                    "liquidation_size": 2500.0,
                    "long_liquidation": 50.0,
                    "short_liquidation": 25.0,
                    "symbol_id": "BTCUSDT",
                },
            ),
        )
    )

    assert data.domains.liquidation_map is not None
    assert data.domains.liquidation_map["heatmap_sample_count"] == 1
    assert data.domains.liquidation_map["heatmap_points"][0]["price"] == 100000.0
    assert "largest_cluster" not in data.domains.liquidation_map
    assert "liquidation_value_total" not in data.domains.liquidation_map
    assert "long_liquidation_total" not in data.domains.liquidation_map
    assert "short_liquidation_total" not in data.domains.liquidation_map


def test_crypto_lens_onchain_rows_do_not_imply_ahr999() -> None:
    data = _crypto_lens_input(
        _result(
            "crypto_onchain_metric",
            (
                {
                    "timestamp": "2026-06-12",
                    "metric": "exchange_balance",
                    "value": 2516453.0,
                },
            ),
        )
    )

    assert data.domain_status["onchain"] == CryptoLensDomainStatus.READY
    assert data.domain_status["ahr999"] == CryptoLensDomainStatus.MISSING
    assert data.domains.ahr999 is None


def test_crypto_lens_onchain_rows_include_coinglass_index_metrics() -> None:
    data = _crypto_lens_input(
        _result(
            "crypto_onchain_metric",
            (
                {
                    "timestamp": "2026-06-12",
                    "metric": "active_addresses",
                    "value": 912345.0,
                },
                {
                    "timestamp": "2026-06-12",
                    "metric": "sth_sopr",
                    "value": 0.98,
                },
                {
                    "timestamp": "2026-06-12",
                    "metric": "lth_sopr",
                    "value": 1.21,
                },
                {
                    "timestamp": "2026-06-12",
                    "metric": "nupl",
                    "value": 0.52,
                },
                {
                    "timestamp": "2026-06-12",
                    "metric": "stablecoin_market_cap",
                    "value": 165000000000.0,
                },
            ),
        )
    )

    assert data.domain_status["onchain"] == CryptoLensDomainStatus.READY
    assert data.domains.onchain is not None
    assert data.domains.onchain["active_addresses"] == 912345.0
    assert data.domains.onchain["sth_sopr"] == 0.98
    assert data.domains.onchain["lth_sopr"] == 1.21
    assert data.domains.onchain["nupl"] == 0.52
    assert data.domains.onchain["stablecoin_market_cap"] == 165000000000.0


def test_crypto_lens_macro_and_events_are_separate_domains() -> None:
    company_only = _crypto_lens_input(
        _result(
            "company_news",
            (
                {
                    "title": "Bitcoin project update",
                    "published_at": "2026-06-12",
                    "source": "project",
                },
            ),
        )
    )
    macro = _crypto_lens_input(
        _result(
            "macro_news",
            (
                {
                    "title": "Crypto macro liquidity update",
                    "published_at": "2026-06-12",
                    "source": "Google News",
                },
            ),
        )
    )

    assert company_only.domain_status["events"] == CryptoLensDomainStatus.READY
    assert company_only.domain_status["macro"] == CryptoLensDomainStatus.MISSING
    assert macro.domain_status["macro"] == CryptoLensDomainStatus.READY
