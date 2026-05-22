from __future__ import annotations

from claw_trade.web.state import _workflow_create_timeout_seconds


def test_workflow_create_timeout_defaults_to_real_boot_window(monkeypatch) -> None:
    monkeypatch.delenv("CLAW_TRADE_UI_WORKFLOW_CREATE_TIMEOUT_SECONDS", raising=False)

    assert _workflow_create_timeout_seconds() >= 30


def test_workflow_create_timeout_can_be_configured(monkeypatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_UI_WORKFLOW_CREATE_TIMEOUT_SECONDS", "45.5")

    assert _workflow_create_timeout_seconds() == 45.5


def test_workflow_create_timeout_rejects_invalid_values(monkeypatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_UI_WORKFLOW_CREATE_TIMEOUT_SECONDS", "-1")
    fallback = _workflow_create_timeout_seconds()

    monkeypatch.setenv("CLAW_TRADE_UI_WORKFLOW_CREATE_TIMEOUT_SECONDS", "not-a-number")
    assert _workflow_create_timeout_seconds() == fallback
