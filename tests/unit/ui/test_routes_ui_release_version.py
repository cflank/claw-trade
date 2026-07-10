from __future__ import annotations

from claw_trade.web import routes_ui


def test_routes_ui_current_release_version_reads_process_release_root(monkeypatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_RELEASE_ROOT", "/opt/claw-trade/releases/claw-trade-production-1.2.3-20260626T120000Z")

    assert routes_ui._current_release_version() == "1.2.3"


def test_routes_ui_current_release_version_falls_back_for_invalid_release_root(monkeypatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_RELEASE_ROOT", "/opt/claw-trade/current")

    assert routes_ui._current_release_version() == "0.1.0"
