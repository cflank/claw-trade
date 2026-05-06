from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from claw_trade.cli.run_control import main, parse_args
from claw_trade.workflow.models import RunRequest, RunStatus, Stage, StopPoint, WorkflowState


def _base_args() -> list[str]:
    return [
        "--ticker",
        "AAPL",
        "--company-name",
        "Apple",
        "--market",
        "US",
        "--profile",
        "US",
        "--currency",
        "USD",
        "--currency-symbol",
        "$",
        "--current-date",
        "2026-05-03",
        "--start-date",
        "2026-04-03",
        "--end-date",
        "2026-05-03",
    ]


def test_parse_args_builds_run_request_with_all_fields() -> None:
    request = parse_args(
        _base_args()
        + [
            "--stop-point",
            "frontline_ready",
            "--target-worker-id",
            "market_analyst",
            "--target-stage",
            "frontline",
            "--run-dir",
            "runs/custom",
        ]
    )

    assert request.ticker == "AAPL"
    assert request.company_name == "Apple"
    assert request.market == "US"
    assert request.profile == "US"
    assert request.currency == "USD"
    assert request.currency_symbol == "$"
    assert request.current_date == "2026-05-03"
    assert request.start_date == "2026-04-03"
    assert request.end_date == "2026-05-03"
    assert request.stop_point == StopPoint.FRONTLINE_READY
    assert request.target_worker_id == "market_analyst"
    assert request.target_stage == Stage.FRONTLINE


def test_parse_args_supports_single_worker_request_shape() -> None:
    request = parse_args(
        _base_args()
        + [
            "--stop-point",
            "single_worker_complete",
            "--target-worker-id",
            "market_analyst",
            "--target-stage",
            "frontline",
        ]
    )

    assert request.stop_point == StopPoint.SINGLE_WORKER_COMPLETE
    assert request.target_worker_id == "market_analyst"
    assert request.target_stage == Stage.FRONTLINE


def test_main_returns_non_zero_when_real_runtime_dependency_config_missing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("CLAW_TRADE_OPENCLAW_RUNNER", raising=False)
    monkeypatch.delenv("CLAW_TRADE_OPENVIKING_BACKEND", raising=False)
    run_dir = tmp_path / "runs"

    code = main(_base_args() + ["--run-dir", str(run_dir)])

    captured = capsys.readouterr()
    assert code != 0
    assert "BLOCKED:" in captured.err
    assert "CLAW_TRADE_OPENCLAW_RUNNER" in captured.err
    assert not run_dir.exists()


@dataclass
class _CompletedRunner:
    state: WorkflowState

    def run(self, request: RunRequest) -> WorkflowState:
        _ = request
        return self.state


def test_main_prints_completed_status_and_report_path(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    run_id = "run-cli-success"
    run_dir = tmp_path / "runs" / run_id
    (run_dir / "reports").mkdir(parents=True, exist_ok=True)
    completed_state = WorkflowState(
        run_id=run_id,
        request=parse_args(_base_args()),
        status=RunStatus.COMPLETED,
        run_dir=run_dir,
        openviking_namespace=f"workflow/{run_id}",
        created_at="2026-05-04T12:00:00Z",
        updated_at="2026-05-04T12:10:00Z",
    )
    monkeypatch.setattr("claw_trade.cli.run_control._build_runner", lambda _: _CompletedRunner(completed_state))

    code = main(_base_args() + ["--run-dir", str(tmp_path / "runs")])

    captured = capsys.readouterr()
    assert code == 0
    assert "COMPLETED" in captured.out
    assert f"run_id={run_id}" in captured.out
    assert "status=completed" in captured.out
    assert f"report_path={run_dir / 'reports' / 'final-report.md'}" in captured.out


@pytest.mark.parametrize(
    "forbidden_arg",
    ["--skip-openclaw", "--fake", "--dry-run-success"],
)
def test_cli_does_not_accept_forbidden_success_bypass_flags(forbidden_arg: str) -> None:
    with pytest.raises(SystemExit):
        parse_args(_base_args() + [forbidden_arg])
