from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.workflow.models import RunRequest, RunStatus, WorkflowEntryPoint
from claw_trade.workflow.report_request_factory import build_report_run_request


@dataclass(frozen=True)
class WorkflowRunRecord:
    run_id: str
    status: str
    created_at: str


class WorkflowRunnerPort(Protocol):
    def create_run(self, request: RunRequest) -> str | dict[str, Any]: ...

    def load_state(self, run_id: str) -> Any: ...


class ReportWorkflowBridge:
    def __init__(self, runner: WorkflowRunnerPort) -> None:
        self._runner = runner
        self._runs: dict[str, WorkflowRunRecord] = {}

    def build_run_request(self, task: dict[str, Any]) -> RunRequest:
        settings = task["workflowSettings"]
        return build_report_run_request(
            ticker=str(task["instrumentCode"]),
            company_name=str(task.get("companyName") or task.get("instrumentName") or ""),
            market=str(task["market"]),
            profile=str(settings.get("defaultProfile") or task["market"]),
            currency=str(settings.get("defaultCurrency") or ""),
            currency_symbol=str(settings.get("defaultCurrencySymbol") or ""),
            current_date=str(task["currentDate"]),
            start_date=str(task["startDate"]),
            end_date=str(task["endDate"]),
            settings=_settings_from_task(settings),
            entry_point=WorkflowEntryPoint.REPORT_COMMAND,
        )

    def create_workflow_run(self, task: dict[str, Any]) -> WorkflowRunRecord:
        request = self.build_run_request(task)
        raw = self._runner.create_run(request)
        if isinstance(raw, dict):
            run_id = str(raw.get("runId") or raw.get("run_id") or "").strip()
        else:
            run_id = str(raw).strip()
        if not run_id:
            raise ValueError("workflow_failed")
        record = WorkflowRunRecord(run_id=run_id, status=RunStatus.CREATED.value, created_at=_now_iso())
        self._runs[run_id] = record
        return record

    def poll_workflow_run(self, run_id: str) -> dict[str, str]:
        state = self._runner.load_state(run_id)
        if isinstance(state, dict):
            status = str(state.get("status") or "").strip() or "unknown"
        else:
            status = str(getattr(state, "status", "unknown"))
        return {"runId": run_id, "status": status}

    def load_workflow_state(self, run_id: str) -> Any:
        return self._runner.load_state(run_id)


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _settings_from_task(settings: dict[str, Any]) -> ReportWorkflowSettings:
    return ReportWorkflowSettings(
        max_debate_rounds=int(settings["maxDebateRounds"]),
        max_risk_discuss_rounds=int(settings["maxRiskDiscussRounds"]),
        frontline_execution_mode=str(settings["frontlineExecutionMode"]),
        default_profile=str(settings.get("defaultProfile") or "CN_A"),
        default_market=str(settings.get("defaultMarket") or "CN_A"),
        default_currency=str(settings.get("defaultCurrency") or "CNY"),
        default_currency_symbol=str(settings.get("defaultCurrencySymbol") or "\u00a5"),
    )
