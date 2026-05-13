from __future__ import annotations

import argparse
import importlib
import os
import sys
from pathlib import Path

from claw_trade.artifacts.manifest import ManifestStore
from claw_trade.artifacts.openviking_client import OpenVikingClient
from claw_trade.config.report_workflow_settings import (
    ReportWorkflowSettings,
    ReportWorkflowSettingsError,
    load_report_workflow_settings,
)
from claw_trade.instruments.resolver import InstrumentResolveError, resolve_instrument_identity
from claw_trade.reports.exporter import FinalReportExporter
from claw_trade.runtime.openclaw_client import OpenClawClient
from claw_trade.workflow.models import RunRequest, RunStatus, Stage, StopPoint, WorkflowEntryPoint
from claw_trade.workflow.runner import ControlRunner
from claw_trade.workflow.store import WorkflowStore

_ENV_OPENCLAW_RUNNER = "CLAW_TRADE_OPENCLAW_RUNNER"
_ENV_OPENVIKING_BACKEND = "CLAW_TRADE_OPENVIKING_BACKEND"


class CliBlockedError(RuntimeError):
    pass


def _build_parser(settings: ReportWorkflowSettings | None = None) -> argparse.ArgumentParser:
    resolved_settings = settings or ReportWorkflowSettings()
    parser = argparse.ArgumentParser(description="Run claw-trade control workflow.")
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--company-name", required=True)
    parser.add_argument("--market")
    parser.add_argument("--profile")
    parser.add_argument("--currency")
    parser.add_argument("--currency-symbol")
    parser.add_argument("--current-date", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument(
        "--stop-point",
        choices=[point.value for point in StopPoint],
        default=StopPoint.NONE.value,
    )
    parser.add_argument("--target-worker-id")
    parser.add_argument(
        "--target-stage",
        choices=[stage.value for stage in Stage],
    )
    parser.add_argument("--run-dir", default=resolved_settings.run_dir)
    return parser


def _namespace_to_request(namespace: argparse.Namespace, settings: ReportWorkflowSettings | None = None) -> RunRequest:
    resolved_settings = settings or ReportWorkflowSettings()
    target_stage = Stage(namespace.target_stage) if namespace.target_stage else None
    identity = resolve_instrument_identity(namespace.ticker, market_hint=namespace.market)
    profile = _profile_from_args(namespace.profile, identity.profile)
    return RunRequest(
        ticker=identity.ticker,
        company_name=namespace.company_name,
        market=identity.market,
        profile=profile,
        currency=_value_or_default(namespace.currency, identity.currency),
        currency_symbol=_value_or_default(namespace.currency_symbol, identity.currency_symbol),
        current_date=namespace.current_date,
        start_date=namespace.start_date,
        end_date=namespace.end_date,
        stop_point=StopPoint(namespace.stop_point),
        target_worker_id=namespace.target_worker_id,
        target_stage=target_stage,
        # run_control 是 UI /report workflow 的运维/集成测试入口，不代表普通聊天。
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
        max_debate_rounds=resolved_settings.max_debate_rounds,
        max_risk_discuss_rounds=resolved_settings.max_risk_discuss_rounds,
        frontline_execution_mode=resolved_settings.frontline_execution_mode,
    )


def _profile_from_args(raw_profile: str | None, resolved_profile: str) -> str:
    profile = str(raw_profile or "").strip().upper()
    if not profile:
        return resolved_profile
    if profile != resolved_profile:
        raise InstrumentResolveError(f"profile {profile} does not match resolved market profile {resolved_profile}")
    return profile


def _value_or_default(raw_value: str | None, default: str) -> str:
    value = str(raw_value or "").strip()
    return value or default


def parse_args(argv: list[str]) -> RunRequest:
    settings = load_report_workflow_settings()
    namespace = _build_parser(settings).parse_args(argv)
    return _namespace_to_request(namespace, settings)


def _load_runtime_object(spec: str, *, env_key: str) -> object:
    # 运行时对象必须显式由环境变量指定，避免 CLI 隐式回退到本地假实现。
    module_name, separator, attr_name = spec.partition(":")
    if not module_name or separator != ":" or not attr_name:
        raise CliBlockedError(f"{env_key} 格式错误，必须是 module:attr")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise CliBlockedError(f"{env_key} 导入模块失败: {exc}") from exc
    if not hasattr(module, attr_name):
        raise CliBlockedError(f"{env_key} 指向的属性不存在: {spec}")
    symbol = getattr(module, attr_name)
    if isinstance(symbol, type):
        try:
            return symbol()
        except Exception as exc:
            raise CliBlockedError(f"{env_key} 类实例化失败: {exc}") from exc
    if callable(symbol):
        try:
            return symbol()
        except TypeError:
            return symbol
        except Exception as exc:
            raise CliBlockedError(f"{env_key} 工厂调用失败: {exc}") from exc
    return symbol


def _require_callable(obj: object, name: str, *, env_key: str) -> None:
    if not callable(getattr(obj, name, None)):
        raise CliBlockedError(f"{env_key} 对象缺少方法: {name}")


def _build_runner(run_dir: Path) -> ControlRunner:
    # 控制迁移硬边界：缺真实 OpenClaw/OpenViking 依赖时直接 BLOCKED，禁止 CLI 自行兜底成功。
    openclaw_runner_spec = os.environ.get(_ENV_OPENCLAW_RUNNER, "").strip()
    if not openclaw_runner_spec:
        raise CliBlockedError(f"缺少真实依赖配置: {_ENV_OPENCLAW_RUNNER}")
    openviking_backend_spec = os.environ.get(_ENV_OPENVIKING_BACKEND, "").strip()
    if not openviking_backend_spec:
        raise CliBlockedError(f"缺少真实依赖配置: {_ENV_OPENVIKING_BACKEND}")

    openclaw_runner = _load_runtime_object(openclaw_runner_spec, env_key=_ENV_OPENCLAW_RUNNER)
    _require_callable(openclaw_runner, "probe", env_key=_ENV_OPENCLAW_RUNNER)
    _require_callable(openclaw_runner, "run_worker", env_key=_ENV_OPENCLAW_RUNNER)

    openviking_backend = _load_runtime_object(openviking_backend_spec, env_key=_ENV_OPENVIKING_BACKEND)
    _require_callable(openviking_backend, "ensure_namespace", env_key=_ENV_OPENVIKING_BACKEND)
    _require_callable(openviking_backend, "fetch_receipt_by_path", env_key=_ENV_OPENVIKING_BACKEND)
    _require_callable(openviking_backend, "fetch_stat_by_uri", env_key=_ENV_OPENVIKING_BACKEND)
    _require_callable(openviking_backend, "fetch_content_by_uri", env_key=_ENV_OPENVIKING_BACKEND)
    _require_callable(openviking_backend, "fetch_l2_index_by_uri", env_key=_ENV_OPENVIKING_BACKEND)

    openviking_client = OpenVikingClient(backend=openviking_backend)
    store = WorkflowStore(run_dir)
    manifest_store = ManifestStore(run_dir)
    return ControlRunner(
        store=store,
        manifest_store=manifest_store,
        openclaw=OpenClawClient(runner=openclaw_runner),
        openviking=openviking_client,
        exporter=FinalReportExporter(openviking=openviking_client),
    )


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    try:
        settings = load_report_workflow_settings()
    except ReportWorkflowSettingsError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    parser = _build_parser(settings)
    namespace = parser.parse_args(args)
    try:
        request = _namespace_to_request(namespace, settings)
    except InstrumentResolveError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2
    run_dir = Path(namespace.run_dir)

    try:
        runner = _build_runner(run_dir)
    except CliBlockedError as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2

    state = runner.run(request)
    # CLI 只回传控制面终态，不在这里改写 workflow 决策或失败归因。
    if state.status == RunStatus.COMPLETED:
        if request.stop_point == StopPoint.NONE:
            report_path = state.run_dir / "reports" / "final-report.md"
            print(f"COMPLETED run_id={state.run_id} status={state.status.value} report_path={report_path}")
        else:
            print(
                f"COMPLETED run_id={state.run_id} status={state.status.value} "
                f"stop_point={request.stop_point.value} run_dir={state.run_dir}"
            )
        return 0

    failure = state.failure_reason or "unknown"
    print(f"FAILED run_id={state.run_id} status={state.status.value} reason={failure}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
