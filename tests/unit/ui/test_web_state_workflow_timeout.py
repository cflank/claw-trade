from __future__ import annotations

from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from claw_trade.web.state import _ControlWorkflowRunner, _workflow_create_timeout_seconds
from claw_trade.workflow.models import RunStatus


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


def test_cancel_run_succeeds_only_after_background_thread_exits() -> None:
    stop = Event()
    thread = Thread(target=stop.wait)
    thread.start()
    runner = _ControlWorkflowRunner(run_dir=Path("runs"))
    runner._runner = SimpleNamespace(  # noqa: SLF001
        store=SimpleNamespace(load_state=lambda _run_id: SimpleNamespace(status=RunStatus.CANCELLED))
    )
    runner._run_threads["run-1"] = thread  # noqa: SLF001

    try:
        assert runner.cancel_run("run-1") is False
        stop.set()
        assert runner.wait_run_exit("run-1") is True
        assert runner.cancel_run("run-1") is True
    finally:
        stop.set()
        thread.join(timeout=1)
