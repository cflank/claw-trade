from __future__ import annotations

from pathlib import Path

import pytest
from claw_trade.web.state import _ControlWorkflowRunner, _workflow_create_timeout_seconds


class _FailingRunner:
    def __init__(self) -> None:
        self.store = type("Store", (), {"create_run": lambda _self, _request: None})()

    def run(self, _request: object) -> None:
        raise PermissionError("[Errno 13] Permission denied: 'runs'")


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


def test_workflow_create_preserves_storage_permission_failure(monkeypatch) -> None:
    monkeypatch.setattr("claw_trade.web.state._build_runner", lambda _run_dir: _FailingRunner())
    runner = _ControlWorkflowRunner(run_dir=Path("runs"))

    with pytest.raises(RuntimeError, match="workflow_storage_unavailable"):
        runner.create_run(object())
