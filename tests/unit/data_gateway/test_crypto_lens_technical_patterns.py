from __future__ import annotations

from claw_trade.data_gateway.analysis.crypto_lens.engine.technical import analyze_technical_patterns
from claw_trade.data_gateway.analysis.crypto_lens.input_contract import (
    CRYPTO_LENS_DOMAIN_KEYS,
    CryptoLensDomainStatus,
    CryptoLensDomains,
    CryptoLensInput,
)
from claw_trade.data_gateway.models import Market


def test_crypto_lens_outputs_local_structure_pattern_modules() -> None:
    candles = []
    wave = (0.0, 10.0, 2.0, -9.0, 1.0, 12.0, 3.0, -8.0, 2.0, 11.0, 1.0, -10.0)
    prior_close = 100.0
    for index in range(90):
        close = 100.0 + index * 0.03 + wave[index % len(wave)]
        open_value = prior_close
        candles.append(
            {
                "time": f"2026-01-{(index % 28) + 1:02d}T00:00:00Z",
                "open": open_value,
                "high": close + 1.5,
                "low": close - 1.5,
                "close": close,
                "volume": 1000.0 + index * 10.0,
            }
        )
        prior_close = close

    data = CryptoLensInput(
        run_id="run-test",
        call_id="call-test",
        ticker="BTC",
        market=Market.CRYPTO,
        quote="USDT",
        as_of="2026-06-10T00:00:00Z",
        start_date="2026-01-01",
        end_date="2026-06-10",
        domains=CryptoLensDomains(ohlcv={"rows": len(candles), "candles": candles}),
        domain_status={key: CryptoLensDomainStatus.READY for key in CRYPTO_LENS_DOMAIN_KEYS},
        data_gaps=(),
        conflicts=(),
        attempt_refs=(),
        normalized_refs=(),
    )

    section = analyze_technical_patterns({"rows": len(candles), "candles": candles}, data)

    patterns = section.evidence["patterns"]
    assert set(patterns) == {"order_block", "volume_profile", "amd", "rule_123", "harmonic"}
    assert patterns["volume_profile"]["status"] == "ready"
    assert patterns["volume_profile"]["poc"] is not None
    assert patterns["amd"]["status"] == "ready"
    assert patterns["rule_123"]["status"] in {
        "confirmed_breakout",
        "confirmed_breakdown",
        "pending_breakout",
        "pending_breakdown",
        "no_pattern",
    }
    assert patterns["order_block"]["status"] in {"candidate", "no_recent_break_of_structure", "no_source_candle"}
    assert patterns["harmonic"]["status"] in {"candidate", "no_candidate", "invalid_pivot_geometry"}
    for value in patterns.values():
        assert _forbidden_keys(value).isdisjoint({"win_rate", "target_price", "trade_action", "action", "probability"})
    assert "order_block" in section.evidence["rule_versions"]
    assert "volume_profile" in section.evidence["rule_versions"]
    assert "amd_smc" in section.evidence["rule_versions"]
    assert "rule_123" in section.evidence["rule_versions"]
    assert "harmonic" in section.evidence["rule_versions"]


def _forbidden_keys(value: object) -> set[str]:
    if isinstance(value, dict):
        keys = {str(key) for key in value}
        for nested in value.values():
            keys.update(_forbidden_keys(nested))
        return keys
    if isinstance(value, (list, tuple)):
        keys: set[str] = set()
        for nested in value:
            keys.update(_forbidden_keys(nested))
        return keys
    return set()
