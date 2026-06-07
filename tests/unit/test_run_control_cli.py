from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import pytest
from claw_trade.artifacts.manifest import ManifestStore
from claw_trade.cli.run_control import _build_runner, main, parse_args
from claw_trade.guards.common import BootResult
from claw_trade.workflow.models import (
    RunRequest,
    RunStatus,
    Stage,
    StopPoint,
    WorkflowEntryPoint,
    WorkflowState,
)
from claw_trade.workflow.runner import ControlRunner
from claw_trade.workflow.store import WorkflowStore


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


def test_parse_args_builds_run_request_with_all_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_REPORT_MAX_DEBATE_ROUNDS", "2")
    monkeypatch.setenv("CLAW_TRADE_REPORT_MAX_RISK_DISCUSS_ROUNDS", "3")
    monkeypatch.setenv("CLAW_TRADE_REPORT_FRONTLINE_EXECUTION_MODE", "parallel")
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
    assert request.data_gateway == "data_gateway"
    assert request.stop_point == StopPoint.FRONTLINE_READY
    assert request.target_worker_id == "market_analyst"
    assert request.target_stage == Stage.FRONTLINE
    assert request.entry_point == WorkflowEntryPoint.REPORT_COMMAND
    assert request.max_debate_rounds == 2
    assert request.max_risk_discuss_rounds == 3
    assert request.frontline_execution_mode == "parallel"


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


def test_parse_args_ignores_legacy_rollback_env_and_records_data_gateway(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_LEGACY_ROLLBACK_ENABLED", "true")

    request = parse_args(_base_args())

    assert request.data_gateway == "data_gateway"


def test_parse_args_normalizes_hk_ticker_without_approving_profile() -> None:
    request = parse_args(
        [
            "--ticker",
            "700",
            "--company-name",
            "Tencent",
            "--market",
            "HK",
            "--current-date",
            "2026-05-14",
            "--start-date",
            "2026-01-01",
            "--end-date",
            "2026-05-14",
        ]
    )

    assert request.ticker == "00700.HK"
    assert request.market == "HK"
    assert request.profile == "HK"
    assert request.currency == "HKD"
    assert request.currency_symbol == "HK$"
    assert request.entry_point == WorkflowEntryPoint.REPORT_COMMAND


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


class _RuntimeObject:
    def probe(self) -> object:
        return object()

    def run_worker(self, command: object) -> object:
        _ = command
        return object()

    def ensure_namespace(self, namespace: str) -> None:
        _ = namespace

    def fetch_receipt_by_path(self, receipt_path: Path) -> object:
        _ = receipt_path
        return object()

    def fetch_stat_by_uri(self, uri: str) -> object:
        _ = uri
        return object()

    def fetch_content_by_uri(self, uri: str) -> bytes:
        _ = uri
        return b""

    def fetch_l2_index_by_uri(self, uri: str | None) -> object:
        _ = uri
        return object()


class _FakeRunner:
    def __init__(self, **kwargs: object) -> None:
        self.kwargs = kwargs


class _FakeRunnerModule:
    ControlRunner = _FakeRunner


class _RunnerOpenClaw:
    def probe(self) -> object:
        return type("_Probe", (), {"ok": True})()

    def run_worker(self, command: object) -> object:
        _ = command
        return object()


class _RunnerOpenViking:
    def probe_read_stat_receipt(self) -> object:
        return type("_Probe", (), {"ok": True})()

    def probe_namespace_stat(self) -> object:
        return type("_Probe", (), {"ok": True})()

    def ensure_namespace(self, namespace: str) -> None:
        _ = namespace

    def read_approved_l1(self, material: object) -> object:
        _ = material
        return object()


class _RunnerToolRegistryProbe:
    def probe(self) -> BootResult:
        return BootResult.ok_result()


def test_build_runner_does_not_require_legacy_data_gateway_mongo_config(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CLAW_TRADE_OPENCLAW_RUNNER", "tests.fake:openclaw")
    monkeypatch.setenv("CLAW_TRADE_OPENVIKING_BACKEND", "tests.fake:openviking")
    monkeypatch.delenv("DATA_GATEWAY_MONGODB_URI", raising=False)
    monkeypatch.delenv("CN_A_MONGODB_URI", raising=False)
    monkeypatch.setattr("claw_trade.cli.run_control._load_runtime_object", lambda *_args, **_kwargs: _RuntimeObject())
    monkeypatch.setattr("claw_trade.cli.run_control._load_module", lambda *_args, **_kwargs: _FakeRunnerModule)

    runner = _build_runner(tmp_path / "runs")

    assert isinstance(runner, _FakeRunner)
    assert "run_" + "provider_" + "planner" not in runner.kwargs
    assert "run_" + "provider_" + "plan_store" not in runner.kwargs
    assert "run_" + "provider_registry" not in runner.kwargs
    assert "provider_config_version_resolver" not in runner.kwargs
    assert "lineage_writer" not in runner.kwargs


def test_build_runner_only_loads_workflow_runner_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    loaded_modules: list[str] = []

    monkeypatch.setenv("CLAW_TRADE_OPENCLAW_RUNNER", "tests.fake:openclaw")
    monkeypatch.setenv("CLAW_TRADE_OPENVIKING_BACKEND", "tests.fake:openviking")
    monkeypatch.setattr("claw_trade.cli.run_control._load_runtime_object", lambda *_args, **_kwargs: _RuntimeObject())
    monkeypatch.setattr(
        "claw_trade.cli.run_control._load_module",
        lambda module_name, **_kwargs: loaded_modules.append(module_name) or _FakeRunnerModule,
    )

    _build_runner(tmp_path / "runs")

    assert loaded_modules == ["claw_trade.workflow.runner"]


def test_control_runner_fails_closed_when_data_gateway_mode_is_legacy(tmp_path: Path) -> None:
    request = replace(parse_args(_base_args()), data_gateway="open" + "bb")
    runner = ControlRunner(
        store=WorkflowStore(tmp_path / "runs"),
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_RunnerOpenClaw(),
        openviking=_RunnerOpenViking(),
        tool_registry_probe=_RunnerToolRegistryProbe(),
    )

    state = runner.run(request)

    assert state.status == RunStatus.FAILED
    assert state.failure_reason is not None
    assert state.failure_reason.startswith("data_gateway_mode:")


def test_control_runner_blocks_legacy_provider_plan_injection_kwargs(tmp_path: Path) -> None:
    request = parse_args(_base_args())
    runner = ControlRunner(
        store=WorkflowStore(tmp_path / "runs"),
        manifest_store=ManifestStore(tmp_path / "runs"),
        openclaw=_RunnerOpenClaw(),
        openviking=_RunnerOpenViking(),
        tool_registry_probe=_RunnerToolRegistryProbe(),
        legacy_provider_path=object(),
    )

    state = runner.run(request)

    assert state.status == RunStatus.FAILED
    assert state.failure_reason is not None
    assert state.failure_reason.startswith("data_gateway_cutover:")


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
