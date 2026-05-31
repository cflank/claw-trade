from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.selection.models import (
    ReportHandoffRequest,
    SelectionConfirmation,
    SelectionDecision,
    SelectionMarket,
    SelectionProfile,
    SelectionStage,
)
from claw_trade.ui_backend.report_queue import QueueError, ReportTaskQueue
from claw_trade.workflow.models import WorkflowEntryPoint
from claw_trade.workflow.report_request_factory import build_report_run_request


@dataclass(frozen=True)
class ReportHandoffStartResult:
    task_id: str
    run_id: str | None
    queue_payload: dict[str, Any]


def build_report_handoff_request(
    *,
    confirmation: SelectionConfirmation,
    decision: SelectionDecision,
    settings: ReportWorkflowSettings,
    current_date: str,
    company_name: str,
    selection_context_ref: str,
) -> ReportHandoffRequest:
    if decision.approved_material_id != selection_context_ref:
        raise ValueError("selection_context_ref must match approved selection context")
    report_request = build_report_run_request(
        ticker=confirmation.ticker,
        company_name=company_name,
        market=SelectionMarket.CN_A.value,
        profile=SelectionProfile.CN_A.value,
        current_date=current_date,
        settings=settings,
        entry_point=WorkflowEntryPoint.REPORT_COMMAND,
    )
    return ReportHandoffRequest(
        confirmation_id=confirmation.confirmation_id,
        ticker=confirmation.ticker,
        company_name=company_name,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        current_date=current_date,
        selection_context_ref=selection_context_ref,
        selection_stage_marker=SelectionStage.SELECTION_REPORT_HANDOFF,
        report_request=report_request,
    )


def enqueue_report_handoff(
    *,
    queue: ReportTaskQueue,
    request_id: str,
    handoff: ReportHandoffRequest,
    origin_context_id: str | None = None,
) -> ReportHandoffStartResult:
    queue_payload = queue.enqueue_report_task(
        request_id=request_id,
        task_input=_task_input_from_handoff(handoff),
        source="manual",
        origin_context_id=origin_context_id,
    )
    task_payload = queue_payload.get("task")
    if not isinstance(task_payload, dict):
        raise QueueError("WORKFLOW_FAILED", "workflow_failed", "报告任务创建失败。")
    task_id = str(task_payload.get("taskId") or "").strip()
    if not task_id:
        raise QueueError("WORKFLOW_FAILED", "workflow_failed", "报告任务创建失败。")
    queued_task = queue.get_task_for_testing(task_id)
    run_id = queued_task.run_id if queued_task is not None else None
    return ReportHandoffStartResult(
        task_id=task_id,
        run_id=run_id,
        queue_payload=queue_payload,
    )


def _task_input_from_handoff(handoff: ReportHandoffRequest) -> dict[str, Any]:
    report_request = handoff.report_request
    return {
        "instrumentCode": report_request.ticker,
        "instrumentName": report_request.company_name,
        "market": report_request.market,
        "companyName": report_request.company_name,
        "currencySymbol": report_request.currency_symbol,
        "startDate": report_request.start_date,
        "endDate": report_request.end_date,
        "currentDate": report_request.current_date,
        "workflowSettings": {
            "maxDebateRounds": report_request.max_debate_rounds,
            "maxRiskDiscussRounds": report_request.max_risk_discuss_rounds,
            "frontlineExecutionMode": report_request.frontline_execution_mode,
            "defaultProfile": report_request.profile,
            "defaultMarket": report_request.market,
            "defaultCurrency": report_request.currency,
            "defaultCurrencySymbol": report_request.currency_symbol,
        },
    }


def utc_today_iso() -> str:
    return datetime.now(tz=UTC).date().isoformat()
