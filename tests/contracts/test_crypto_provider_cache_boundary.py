from __future__ import annotations

from pathlib import Path


def test_crypto_provider_cache_stays_out_of_workflow_controller() -> None:
    for path in Path("src/claw_trade/workflow").rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert "crypto_provider_cache" not in text
        assert "crypto_provider_cache" not in path.name


def test_crypto_provider_cache_does_not_modify_runtime_guard_allowlist() -> None:
    text = Path("tests/contracts/test_guard_change_requires_approval.py").read_text(encoding="utf-8")

    assert "crypto_provider_cache" not in text
    assert "crypto_provider_rate_limits" not in text
