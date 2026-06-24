from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import is_dataclass
from typing import Any
from urllib.parse import urlencode, urlsplit, urlunsplit

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from claw_trade.selection.confirmation import SelectionConfirmationError, SelectionConfirmRequest
from claw_trade.selection.models import SelectionMarket, SelectionProfile
from claw_trade.ui_backend.channel_text_inbound import ChannelTextMessage
from claw_trade.ui_backend.error_translator import translate_internal_error_for_user
from claw_trade.ui_backend.price_alert_service import UiServiceError as PriceAlertServiceError
from claw_trade.ui_backend.report_queue import QueueError
from claw_trade.ui_backend.report_repository import UiProductError
from claw_trade.ui_backend.scheduled_work_runner import ScheduledWorkRunnerError
from claw_trade.ui_backend.scheduler_service import UiServiceError as SchedulerServiceError
from claw_trade.ui_backend.settings_service import UiBoundaryError
from claw_trade.ui_backend.worker_chat_catalog import list_worker_chat_menu
from claw_trade.ui_backend.worker_chat_models import WorkerChatReplyForUser, WorkerChatRequest
from claw_trade.ui_contracts.user_dto import to_user_payload
from claw_trade.web.session import resolve_context_id
from claw_trade.web.state import UiHttpServices, build_report_detail_payload

router = APIRouter()


class SendChatMessageRequest(BaseModel):
    requestId: str
    contextId: str
    text: str


class ClearChatSessionRequest(BaseModel):
    requestId: str
    contextId: str


class AskReportQuestionRequest(BaseModel):
    requestId: str
    reportId: str
    text: str


class DeleteSavedReportRequest(BaseModel):
    requestId: str
    reportId: str


class DeleteSavedReportsRequest(BaseModel):
    requestId: str
    reportIds: list[str] = Field(min_length=1, max_length=500)


class CreateIntentDraftRequest(BaseModel):
    requestId: str
    sourceMessageId: str
    text: str


class ConfirmIntentDraftRequest(BaseModel):
    requestId: str
    draftId: str
    decision: str
    overrides: dict[str, Any] | None = None
    contextId: str | None = None


class ConfirmSelectionReportRequest(BaseModel):
    requestId: str
    selectWorkflowRunId: str
    ticker: str
    originContextId: str | None = None


class CancelReportTaskRequest(BaseModel):
    requestId: str
    taskId: str


class CancelSelectionProgressRequest(BaseModel):
    requestId: str
    workflowRunId: str


class CreateScheduledReportRequest(BaseModel):
    requestId: str
    instrumentCode: str
    market: str
    frequency: str
    timeOfDay: str
    weekday: int | None = None
    notification: dict[str, Any] | None = None
    instrumentName: str | None = None
    workflowSettings: dict[str, Any] | None = None


class ScheduledReportActionRequest(BaseModel):
    requestId: str
    scheduledReportId: str


class CreatePriceAlertRequest(BaseModel):
    requestId: str
    instrumentCode: str
    market: str
    condition: dict[str, Any]
    notification: dict[str, Any] | None = None
    instrumentName: str | None = None


class PriceAlertActionRequest(BaseModel):
    requestId: str
    priceAlertId: str


class TestDataSourceRequest(BaseModel):
    requestId: str
    instanceDraft: dict[str, Any]


class SaveDataSourceInstanceRequest(BaseModel):
    requestId: str
    instance: dict[str, Any]


class SaveLlmConfigRequest(BaseModel):
    requestId: str
    draft: dict[str, Any]
    expectedSettingsVersion: str


class SaveChannelConfigRequest(BaseModel):
    requestId: str
    channelKind: str = "wechat_clawbot"
    configPatch: dict[str, Any] = Field(default_factory=dict)
    expectedSettingsVersion: str | None = None


class TestLlmRequest(BaseModel):
    requestId: str
    provider: str
    model: str | None = None
    endpointUrl: str | None = None
    apiKeyReplacement: str | None = None


class TestEmbeddingRequest(BaseModel):
    requestId: str
    embedding: dict[str, Any]


class SaveEmbeddingConfigRequest(BaseModel):
    requestId: str
    embedding: dict[str, Any]


class ReportCleanupSettingsForUser(BaseModel):
    reportRetentionDays: int


class SelectionAutoRefreshSettingsForUser(BaseModel):
    enabled: bool


class GetReportCleanupSettingsResponse(BaseModel):
    reportCleanup: ReportCleanupSettingsForUser


class GetSelectionAutoRefreshSettingsResponse(BaseModel):
    selectionAutoRefresh: SelectionAutoRefreshSettingsForUser


class SaveReportCleanupSettingsRequest(BaseModel):
    requestId: str
    reportRetentionDays: int


class SaveSelectionAutoRefreshSettingsRequest(BaseModel):
    requestId: str
    enabled: bool


class SaveReportCleanupSettingsResponse(BaseModel):
    reportCleanup: ReportCleanupSettingsForUser


class SaveSelectionAutoRefreshSettingsResponse(BaseModel):
    selectionAutoRefresh: SelectionAutoRefreshSettingsForUser


class ResetSettingsToDefaultsRequest(BaseModel):
    requestId: str


class SendReportFileRequest(BaseModel):
    requestId: str
    reportId: str
    channelKind: str = "wechat_clawbot"


class ChannelInboundMessageRequest(BaseModel):
    requestId: str
    channelKind: str = "wechat_clawbot"
    accountId: str | None = None
    senderId: str
    text: str
    messageId: str | None = None
    receivedAt: str | None = None


class ExportReportPdfRequest(BaseModel):
    requestId: str
    reportId: str
    force: bool = False


class ScheduledWorkCronWakeRequest(BaseModel):
    kind: str
    bucketKey: str | None = None
    cronRunId: str | None = None
    requestId: str | None = None
    scheduledReportId: str | None = None
    market: str | None = None
    jobKind: str | None = None
    reason: str | None = None
    maintenanceJobId: str | None = None


@router.post("/send-chat-message")
def send_chat_message(payload: SendChatMessageRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.chat_controller.send_chat_message(
            request_id=payload.requestId,
            context_id=resolve_context_id(payload.contextId),
            text=payload.text,
        )
        service_error = _service_error(result)
        if service_error is not None:
            return _error_response(service_error["code"], service_error["message"])
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/create-intent-draft")
def create_intent_draft(payload: CreateIntentDraftRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.chat_controller.create_intent_draft(
            request_id=payload.requestId,
            source_message_id=payload.sourceMessageId,
            text=payload.text,
        )
        service_error = _service_error(result)
        if service_error is not None:
            return _error_response(service_error["code"], service_error["message"])
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/confirm-intent-draft")
def confirm_intent_draft(payload: ConfirmIntentDraftRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        if payload.contextId:
            result = services.chat_controller.confirm_intent_draft_from_chat(
                request_id=payload.requestId,
                context_id=resolve_context_id(payload.contextId),
                draft_id=payload.draftId,
                decision=payload.decision,
                text="确认" if payload.decision == "confirm" else "取消",
                overrides=payload.overrides,
            )
        else:
            result = services.chat_controller.confirm_intent_draft(
                request_id=payload.requestId,
                draft_id=payload.draftId,
                decision=payload.decision,
                overrides=payload.overrides,
            )
        service_error = _service_error(result)
        if service_error is not None:
            return _error_response(service_error["code"], service_error["message"])
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/confirm-selection-report")
def confirm_selection_report(payload: ConfirmSelectionReportRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.selection_confirmation.confirm(
            SelectionConfirmRequest(
                confirmation_id=payload.requestId,
                idempotency_key=f"{payload.selectWorkflowRunId}:{payload.ticker.strip().upper()}:{payload.requestId}",
                select_workflow_run_id=payload.selectWorkflowRunId,
                ticker=payload.ticker,
                origin_context_id=payload.originContextId,
            )
        )
        response: dict[str, Any] = {
            "code": result.code,
            "reportTaskId": result.report_task_id,
            "reportRunId": result.report_run_id,
            "reportHandoffDedupeKey": result.report_handoff_dedupe_key,
            "queuePayload": result.queue_payload,
            "deduped": result.deduped,
            "queueSnapshot": services.queue.get_report_queue_snapshot_for_user(),
        }
        task_id = str(result.report_task_id or "").strip()
        if task_id:
            snapshot = response["queueSnapshot"]
            tasks = []
            running = snapshot.get("runningTask") if isinstance(snapshot, dict) else None
            if running is not None:
                tasks.append(running)
            queued = snapshot.get("queuedTasks") if isinstance(snapshot, dict) else None
            if isinstance(queued, list):
                tasks.extend(queued)
            terminal = snapshot.get("lastTerminalTask") if isinstance(snapshot, dict) else None
            if terminal is not None:
                tasks.append(terminal)
            response["task"] = next(
                (task for task in tasks if isinstance(task, dict) and str(task.get("taskId") or "") == task_id),
                None,
            )
        return _success_response(response)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/channel-inbound-message")
def channel_inbound_message(payload: ChannelInboundMessageRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.channel_text_inbound.handle_message(
            ChannelTextMessage(
                request_id=payload.requestId,
                channel_kind=payload.channelKind,
                account_id=payload.accountId,
                sender_id=payload.senderId,
                text=payload.text,
                message_id=payload.messageId,
                received_at=payload.receivedAt,
            )
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/internal/scheduled-work/cron-wake")
def scheduled_work_cron_wake(payload: ScheduledWorkCronWakeRequest, request: Request) -> JSONResponse:
    if not _valid_internal_cron_token(request):
        return JSONResponse({"code": "FORBIDDEN", "message": "内部定时任务入口未授权。"}, status_code=403)
    services = _services(request)
    try:
        return _success_response(services.scheduled_work_runner.handle_wake(payload.model_dump(exclude_none=True)))
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-channel-chat-snapshot")
def get_channel_chat_snapshot(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.channel_text_inbound.latest_conversation_snapshot())
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-chat-session")
def get_chat_session(request: Request, contextId: str = Query(...)) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.chat_controller.get_chat_session(context_id=resolve_context_id(contextId)))
    except Exception as exc:
        return _exception_response(exc)


@router.post("/clear-chat-session")
def clear_chat_session(payload: ClearChatSessionRequest, request: Request) -> JSONResponse:
    _ = payload.requestId
    services = _services(request)
    try:
        return _success_response(services.chat_controller.clear_chat_session(context_id=resolve_context_id(payload.contextId)))
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-report-queue-snapshot")
def get_report_queue_snapshot(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.queue.get_report_queue_snapshot_for_user())
    except Exception as exc:
        return _exception_response(exc)


@router.post("/cancel-report-task")
def cancel_report_task(payload: CancelReportTaskRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(
            services.queue.cancel_report_task(
                request_id=payload.requestId,
                task_id=payload.taskId,
            )
        )
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-selection-refresh-snapshot")
def get_selection_refresh_snapshot(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        workflow_snapshot = services.selection_controller.latest_progress_for_user()
        if workflow_snapshot.get("selectionProgress"):
            return _success_response(workflow_snapshot)
        refresh_snapshot = services.selection_refresh_service.latest_progress_for_user(include_terminal=False)
        if refresh_snapshot.get("selectionProgress"):
            return _success_response(refresh_snapshot)
        crypto_refresh_snapshot = services.selection_refresh_service.latest_progress_for_user(
            market=SelectionMarket.CRYPTO,
            profile=SelectionProfile.CRYPTO,
            include_terminal=False,
        )
        if crypto_refresh_snapshot.get("selectionProgress"):
            return _success_response(crypto_refresh_snapshot)
        refresh_snapshot = services.selection_refresh_service.latest_progress_for_user()
        if refresh_snapshot.get("selectionProgress"):
            return _success_response(refresh_snapshot)
        return _success_response(
            services.selection_refresh_service.latest_progress_for_user(
                market=SelectionMarket.CRYPTO,
                profile=SelectionProfile.CRYPTO,
            )
        )
    except Exception as exc:
        return _exception_response(exc)


@router.post("/cancel-selection-progress")
def cancel_selection_progress(payload: CancelSelectionProgressRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        workflow_cancelled = services.selection_controller.cancel_progress(workflow_run_id=payload.workflowRunId)
        refresh_cancelled = services.selection_refresh_service.cancel_refresh(selection_run_id=payload.workflowRunId)
        return _success_response(
            {
                "cancelled": workflow_cancelled or refresh_cancelled,
                "selectionProgress": None,
                "message": "已停止选股任务。",
            }
        )
    except Exception as exc:
        return _exception_response(exc)


@router.post("/create-scheduled-report")
def create_scheduled_report(payload: CreateScheduledReportRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.scheduler_service.create_scheduled_report(
            request_id=payload.requestId,
            instrument_code=payload.instrumentCode,
            instrument_name=payload.instrumentName,
            market=payload.market,
            frequency=payload.frequency,
            time_of_day=payload.timeOfDay,
            weekday=payload.weekday,
            notification=payload.notification,
            workflow_settings=payload.workflowSettings,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.get("/list-scheduled-reports")
def list_scheduled_reports(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.scheduler_service.list_scheduled_reports_for_user())
    except Exception as exc:
        return _exception_response(exc)


@router.post("/pause-scheduled-report")
def pause_scheduled_report(payload: ScheduledReportActionRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.scheduler_service.pause_scheduled_report(
            request_id=payload.requestId,
            scheduled_report_id=payload.scheduledReportId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/resume-scheduled-report")
def resume_scheduled_report(payload: ScheduledReportActionRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.scheduler_service.resume_scheduled_report(
            request_id=payload.requestId,
            scheduled_report_id=payload.scheduledReportId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/delete-scheduled-report")
def delete_scheduled_report(payload: ScheduledReportActionRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.scheduler_service.delete_scheduled_report(
            request_id=payload.requestId,
            scheduled_report_id=payload.scheduledReportId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/run-scheduled-report-now")
def run_scheduled_report_now(payload: ScheduledReportActionRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.scheduler_service.run_scheduled_report_now(
            request_id=payload.requestId,
            scheduled_report_id=payload.scheduledReportId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/create-price-alert")
def create_price_alert(payload: CreatePriceAlertRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.price_alert_service.create_price_alert(
            request_id=payload.requestId,
            instrument_code=payload.instrumentCode,
            instrument_name=payload.instrumentName,
            market=payload.market,
            condition=payload.condition,
            notification=payload.notification,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.get("/list-price-alerts")
def list_price_alerts(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.price_alert_service.list_price_alerts_for_user())
    except Exception as exc:
        return _exception_response(exc)


@router.post("/pause-price-alert")
def pause_price_alert(payload: PriceAlertActionRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.price_alert_service.pause_price_alert(
            request_id=payload.requestId,
            price_alert_id=payload.priceAlertId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/resume-price-alert")
def resume_price_alert(payload: PriceAlertActionRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.price_alert_service.resume_price_alert(
            request_id=payload.requestId,
            price_alert_id=payload.priceAlertId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/delete-price-alert")
def delete_price_alert(payload: PriceAlertActionRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.price_alert_service.delete_price_alert(
            request_id=payload.requestId,
            price_alert_id=payload.priceAlertId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/run-price-alert-now")
def run_price_alert_now(payload: PriceAlertActionRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.price_alert_service.run_price_alert_now(
            request_id=payload.requestId,
            price_alert_id=payload.priceAlertId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.get("/list-saved-reports")
def list_saved_reports(
    request: Request,
    query: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> JSONResponse:
    services = _services(request)
    try:
        services.queue.get_report_queue_snapshot_for_user()
        items = services.repository.list_saved_reports()
        items = _apply_report_forward_capability(items, services)
        filtered = items
        if query:
            lowered = query.strip().lower()
            filtered = [
                item
                for item in items
                if lowered in str(item.get("instrumentCode", "")).lower()
                or lowered in str(item.get("instrumentName", "")).lower()
                or lowered in str(item.get("title", "")).lower()
            ]
        payload: dict[str, Any] = {"items": filtered[:limit]}
        return _success_response(payload)
    except Exception as exc:
        return _exception_response(exc)


def _apply_report_forward_capability(items: list[dict[str, Any]], services: UiHttpServices) -> list[dict[str, Any]]:
    can_forward_current_channel = False
    has_default_target = False
    try:
        status = services.channel_bridge.get_channel_status(probe=True)
        can_forward_current_channel = str(status.get("state")) == "connected" and bool(status.get("canSendFile"))
        if can_forward_current_channel:
            has_default_target = services.channel_bridge.resolve_default_report_file_target(
                channel_kind="wechat_clawbot"
            ) is not None
    except Exception:
        can_forward_current_channel = False
    forwarded_items: list[dict[str, Any]] = []
    for item in items:
        next_item = dict(item)
        next_item["canForwardToChannel"] = bool(
            can_forward_current_channel and (item.get("canForwardToChannel") or has_default_target)
        )
        forwarded_items.append(next_item)
    return forwarded_items


@router.get("/get-report-detail")
def get_report_detail(request: Request, reportId: str = Query(...)) -> JSONResponse:
    services = _services(request)
    try:
        payload = build_report_detail_payload(services, report_id=reportId)
        return _success_response(payload)
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-report-asset")
def get_report_asset(
    request: Request,
    reportId: str = Query(...),
    assetPath: str = Query(...),
) -> Any:
    services = _services(request)
    try:
        asset = services.repository.resolve_report_asset(reportId, assetPath)
        if asset is None or not asset.exists() or not asset.is_file():
            raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告图片。")
        return FileResponse(asset)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/delete-saved-report")
def delete_saved_report(payload: DeleteSavedReportRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        cleanup = services.report_cleanup_service.delete_report_runs([payload.reportId])
        return _success_response(
            {
                "deleted": payload.reportId in cleanup.deletedRunIds,
                "reportId": payload.reportId,
                "userMessage": cleanup.userMessage,
                "cleanup": {
                    "deletedRunIds": cleanup.deletedRunIds,
                    "skippedRunIds": cleanup.skippedRunIds,
                    "failedRunIds": cleanup.failedRunIds,
                    "deletedBytesApprox": cleanup.deletedBytesApprox,
                    "warnings": cleanup.warnings,
                },
            }
        )
    except Exception as exc:
        return _exception_response(exc)


@router.post("/delete-saved-reports")
def delete_saved_reports(payload: DeleteSavedReportsRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        cleanup = services.report_cleanup_service.delete_report_runs(payload.reportIds)
        return _success_response(
            {
                "deletedRunIds": cleanup.deletedRunIds,
                "skippedRunIds": cleanup.skippedRunIds,
                "failedRunIds": cleanup.failedRunIds,
                "deletedBytesApprox": cleanup.deletedBytesApprox,
                "warnings": cleanup.warnings,
                "userMessage": cleanup.userMessage,
                "runs": [
                    {
                        "reportId": item.runId,
                        "status": item.status,
                        "deletedBytesApprox": item.deletedBytesApprox,
                        "warnings": item.warnings,
                        "userMessage": item.userMessage,
                    }
                    for item in cleanup.runs
                ],
            }
        )
    except Exception as exc:
        return _exception_response(exc)


@router.post("/ask-report-question")
def ask_report_question(payload: AskReportQuestionRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        reply = services.report_question_service.ask_report_question(
            report_id=payload.reportId,
            text=payload.text,
            request_id=payload.requestId,
            context_id=f"report:{payload.reportId}",
        )
        return _success_response(reply)
    except Exception as exc:
        return _exception_response(exc)


@router.get("/list-worker-chat-workers")
def list_worker_chat_workers() -> JSONResponse:
    return _success_response({"workers": list_worker_chat_menu()})


@router.post("/send-worker-chat")
def send_worker_chat(payload: dict[str, object], request: Request) -> JSONResponse:
    services = _services(request)
    try:
        chat_request = WorkerChatRequest.from_api_payload(payload)
        reply = services.worker_chat_controller.send_worker_chat(
            request_id=chat_request.request_id,
            mode=chat_request.mode,
            worker_id=chat_request.worker_id,
            text=chat_request.text,
            conversation_id=chat_request.conversation_id,
            report_id=chat_request.report_id,
        )
        return _success_response(WorkerChatReplyForUser.from_controller_reply(reply).to_api_payload())
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-report-chart-evidence")
def get_report_chart_evidence(request: Request, reportId: str = Query(...)) -> JSONResponse:
    services = _services(request)
    try:
        report = services.repository.get_report(reportId)
        if report is None:
            return _error_response("REPORT_NOT_FOUND", "没有找到这份报告。")
        payload = {
            "reportId": reportId,
            **build_report_detail_payload(services, report_id=reportId)["chartEvidence"],
        }
        return _success_response(payload)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/export-report-pdf")
def export_report_pdf(payload: ExportReportPdfRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.pdf_export_service.export_report_pdf(
            payload.reportId,
            request_id=payload.requestId,
            force=payload.force,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/send-report-file-via-channel")
def send_report_file_via_channel(payload: SendReportFileRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.report_notification_service.request_full_report_file(
            report_id=payload.reportId,
            request_id=payload.requestId,
            channel_kind=payload.channelKind,
        )
        if not bool(result.get("sent")):
            code = str(result.get("code") or "FILE_SEND_UNSUPPORTED")
            message = str(result.get("userMessage") or "完整报告文件暂不可发送，请在设备界面查看。")
            return _error_response(code, message)
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-channel-status")
def get_channel_status(
    request: Request,
    probe: bool = Query(default=False),
    include_qr: bool = Query(default=False, alias="includeQr"),
    refresh_qr: bool = Query(default=False, alias="refreshQr"),
    poll_login: bool = Query(default=False, alias="pollLogin"),
) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(
            services.channel_bridge.get_channel_status(
                probe=probe,
                include_qr=include_qr,
                refresh_qr=refresh_qr,
                poll_login=poll_login,
            )
        )
    except Exception as exc:
        return _exception_response(exc)


@router.post("/save-channel-config-via-openclaw")
def save_channel_config_via_openclaw(payload: SaveChannelConfigRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.channel_bridge.save_channel_config_via_openclaw(
            request_id=payload.requestId,
            channel_kind=payload.channelKind,
            config_patch=payload.configPatch,
            expected_settings_version=payload.expectedSettingsVersion,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.get("/open-device-interface")
def open_device_interface(request: Request) -> RedirectResponse:
    settings = request.app.state.research_ui_settings
    return RedirectResponse(
        url=_device_interface_url(settings.gateway_ws_url, token=settings.gateway_token),
        status_code=307,
    )


@router.get("/load-llm-settings")
def load_llm_settings(request: Request, provider: str | None = Query(default=None)) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.llm_bridge.load_llm_settings(provider=provider))
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-advanced-diagnostics-provider-health")
def get_advanced_diagnostics_provider_health(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.llm_bridge.get_provider_health_summary())
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-advanced-diagnostics-runtime-service-status")
def get_advanced_diagnostics_runtime_service_status(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.llm_bridge.get_runtime_service_status_summary())
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-advanced-diagnostics-live-run-gap-summary")
def get_advanced_diagnostics_live_run_gap_summary(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.llm_bridge.get_live_run_gap_summary())
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-advanced-diagnostics-evidence-failure-reason-summary")
def get_advanced_diagnostics_evidence_failure_reason_summary(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.llm_bridge.get_evidence_failure_reason_summary())
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-maintenance-task-diagnostics")
def get_maintenance_task_diagnostics(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(_maintenance_task_diagnostics(services))
    except Exception as exc:
        return _exception_response(exc)


@router.post("/save-llm-config-via-openclaw")
def save_llm_config_via_openclaw(payload: SaveLlmConfigRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.llm_bridge.save_llm_config_via_openclaw(
            draft=payload.draft,
            expected_settings_version=payload.expectedSettingsVersion,
            request_id=payload.requestId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/test-llm-via-openclaw")
def test_llm_via_openclaw(payload: TestLlmRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.llm_bridge.test_llm_via_openclaw(
            {
                "provider": payload.provider,
                "model": payload.model,
                "endpointUrl": payload.endpointUrl,
                "apiKeyReplacement": payload.apiKeyReplacement,
            },
            request_id=payload.requestId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/test-embedding-via-openviking")
def test_embedding_via_openviking(payload: TestEmbeddingRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.llm_bridge.test_embedding_via_openviking(
            payload.embedding,
            request_id=payload.requestId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/save-embedding-config-via-openviking")
def save_embedding_config_via_openviking(payload: SaveEmbeddingConfigRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.llm_bridge.save_embedding_config_via_openviking(
            payload.embedding,
            request_id=payload.requestId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-report-cleanup-settings")
def get_report_cleanup_settings(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(_report_cleanup_settings_response(services.report_cleanup_settings.load_settings()))
    except Exception as exc:
        return _exception_response(exc)


@router.post("/save-report-cleanup-settings")
def save_report_cleanup_settings(payload: SaveReportCleanupSettingsRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        response = SaveReportCleanupSettingsResponse(
            reportCleanup=ReportCleanupSettingsForUser(
                **services.report_cleanup_settings.save_settings(payload.reportRetentionDays)
            )
        )
        return _success_response(response.model_dump())
    except Exception as exc:
        return _exception_response(exc)


@router.get("/get-selection-auto-refresh-settings")
def get_selection_auto_refresh_settings(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        response = GetSelectionAutoRefreshSettingsResponse(
            selectionAutoRefresh=SelectionAutoRefreshSettingsForUser(
                **services.selection_auto_refresh_settings.load_settings()
            )
        )
        return _success_response(response.model_dump())
    except Exception as exc:
        return _exception_response(exc)


@router.post("/save-selection-auto-refresh-settings")
def save_selection_auto_refresh_settings(
    payload: SaveSelectionAutoRefreshSettingsRequest,
    request: Request,
) -> JSONResponse:
    services = _services(request)
    try:
        response = SaveSelectionAutoRefreshSettingsResponse(
            selectionAutoRefresh=SelectionAutoRefreshSettingsForUser(
                **services.selection_auto_refresh_settings.save_settings(payload.enabled)
            )
        )
        if payload.enabled:
            services.selection_refresh_service.start_automatic_refresh_scheduler()
        else:
            services.selection_refresh_service.stop_automatic_refresh_scheduler()
        return _success_response(response.model_dump())
    except Exception as exc:
        return _exception_response(exc)


@router.post("/reset-settings-to-defaults")
def reset_settings_to_defaults(payload: ResetSettingsToDefaultsRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        llm_result = services.llm_bridge.reset_llm_settings_to_defaults(
            request_id=f"{payload.requestId}:llm",
        )
        data_sources_result = services.data_source_settings.reset_to_defaults(
            request_id=f"{payload.requestId}:data-sources",
        )
        channel_result = services.channel_bridge.save_channel_config_via_openclaw(
            request_id=f"{payload.requestId}:channel",
            channel_kind="wechat_clawbot",
            config_patch={"enabled": False},
        )
        report_cleanup_result = services.report_cleanup_settings.reset_to_defaults()
        selection_auto_refresh_result = services.selection_auto_refresh_settings.reset_to_defaults()
        return _success_response(
            {
                "status": "reset",
                "userMessage": "设置已恢复默认。",
                "llm": llm_result,
                "dataSources": data_sources_result,
                "channel": channel_result.get("status"),
                "reportCleanup": report_cleanup_result,
                "selectionAutoRefresh": selection_auto_refresh_result,
            }
        )
    except Exception as exc:
        return _exception_response(exc)


@router.get("/list-data-sources")
def list_data_sources(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.data_source_settings.list_data_sources())
    except Exception as exc:
        return _exception_response(exc)


@router.post("/test-data-source")
def test_data_source(payload: TestDataSourceRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.data_source_settings.test_data_source_instance(
            payload.instanceDraft,
            request_id=payload.requestId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


@router.post("/save-data-source-instance")
def save_data_source_instance(payload: SaveDataSourceInstanceRequest, request: Request) -> JSONResponse:
    services = _services(request)
    try:
        result = services.data_source_settings.save_data_source_instance(
            payload.instance,
            request_id=payload.requestId,
        )
        return _success_response(result)
    except Exception as exc:
        return _exception_response(exc)


def _services(request: Request) -> UiHttpServices:
    return request.app.state.ui_services


def _report_cleanup_settings_response(settings: dict[str, int]) -> dict[str, Any]:
    response = GetReportCleanupSettingsResponse(reportCleanup=ReportCleanupSettingsForUser(**settings))
    return response.model_dump()


def _maintenance_task_diagnostics(services: UiHttpServices) -> dict[str, Any]:
    cn_a_refresh = services.selection_refresh_service.latest_progress_for_user()
    crypto_refresh = services.selection_refresh_service.latest_progress_for_user(
        market=SelectionMarket.CRYPTO,
        profile=SelectionProfile.CRYPTO,
    )
    runner_results = services.scheduled_work_runner.latest_results_for_user().get("items", [])
    data_maintenance_items = [
        item for item in runner_results if isinstance(item, Mapping) and item.get("kind") == "data_maintenance"
    ]
    scheduled_report_wakes = [
        item for item in runner_results if isinstance(item, Mapping) and item.get("kind") == "scheduled_report"
    ]
    return {
        "checkedAt": _utc_now_iso(),
        "selectionRefresh": [
            {"kind": "selection_data_refresh", "market": "CN_A", **_selection_refresh_diag(cn_a_refresh)},
            {"kind": "selection_data_refresh", "market": "CRYPTO", **_selection_refresh_diag(crypto_refresh)},
        ],
        "scheduledReportWakes": scheduled_report_wakes,
        "priceAlertScanBuckets": services.price_alert_service.list_price_alert_scan_buckets_for_user()["items"],
        "dataMaintenance": data_maintenance_items,
        "reportCleanup": services.report_cleanup_scheduler.latest_status_for_user(),
    }


def _selection_refresh_diag(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    progress = snapshot.get("selectionProgress")
    if not isinstance(progress, Mapping):
        return {"status": "not_run", "runId": None, "lastResult": None, "failureReason": None}
    labels = progress.get("workerStatusLabels")
    failure_reason = None
    if isinstance(labels, Sequence) and labels and not isinstance(labels, (str, bytes, bytearray)):
        failure_reason = str(labels[0]) if str(progress.get("status") or "") == "failed" else None
    return {
        "status": str(progress.get("status") or "unknown"),
        "runId": progress.get("workflowRunId"),
        "lastResult": progress.get("currentAction"),
        "failureReason": failure_reason,
        "startedAt": progress.get("startedAt"),
        "finishedAt": progress.get("finishedAt"),
    }


def _utc_now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _valid_internal_cron_token(request: Request) -> bool:
    expected = str(
        getattr(request.app.state, "scheduled_work_internal_token", None)
        or os.environ.get("CLAW_TRADE_SCHEDULED_WORK_INTERNAL_TOKEN")
        or ""
    ).strip()
    provided = str(request.headers.get("x-claw-trade-internal-token") or "").strip()
    return bool(expected) and provided == expected


def _device_interface_url(gateway_ws_url: str, *, token: str | None = None) -> str:
    parsed = urlsplit(gateway_ws_url)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme or "http")
    netloc = parsed.netloc
    path = "/chat"
    if not netloc and parsed.path:
        netloc = parsed.path
    query = urlencode({"session": "agent:ui_chat:v2:ui:normal-chat"})
    fragment = urlencode({"token": token.strip()}) if token and token.strip() else ""
    return urlunsplit((scheme, netloc, path, query, fragment))


def _service_error(result: dict[str, Any]) -> dict[str, str] | None:
    error = result.get("error")
    if not isinstance(error, dict):
        return None
    code = str(error.get("code") or "ASSISTANT_UNAVAILABLE")
    message = str(error.get("message") or "助手服务暂不可用，请稍后重试。")
    return {"code": code, "message": message}


def _exception_response(exc: Exception) -> JSONResponse:
    if isinstance(exc, QueueError):
        return _error_response(exc.code, exc.user_message)
    if isinstance(exc, UiProductError):
        return _error_response(exc.code, exc.user_message)
    if isinstance(exc, UiBoundaryError):
        return _error_response(exc.code, exc.user_message)
    if isinstance(exc, SchedulerServiceError):
        return _error_response(exc.code, exc.message)
    if isinstance(exc, PriceAlertServiceError):
        return _error_response(exc.code, exc.message)
    if isinstance(exc, ScheduledWorkRunnerError):
        return _error_response(exc.code, exc.message)
    if isinstance(exc, SelectionConfirmationError):
        return _error_response("CONFIRMATION_REQUIRED", exc.user_message)
    if isinstance(exc, ValueError) and str(exc).strip() == "assistant_unavailable":
        return _error_response("ASSISTANT_UNAVAILABLE", "助手服务暂不可用，请稍后重试。")
    mapped = translate_internal_error_for_user(exc)
    return _error_response(mapped.code, mapped.user_message)


def _error_response(code: str, message: str) -> JSONResponse:
    payload = {"code": code, "message": message}
    return JSONResponse(payload, status_code=_status_code_for_error(code))


def _success_response(payload: Any) -> JSONResponse:
    user_payload = _normalize_user_payload(payload)
    return JSONResponse(user_payload)


def _normalize_user_payload(payload: Any) -> Any:
    if is_dataclass(payload):
        return to_user_payload(payload)
    if isinstance(payload, Mapping):
        return {str(key): _normalize_user_payload(value) for key, value in payload.items()}
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return [_normalize_user_payload(value) for value in payload]
    return payload


def _status_code_for_error(code: str) -> int:
    mapping = {
        "INVALID_INPUT": 400,
        "CONFIRMATION_REQUIRED": 400,
        "DRAFT_EXPIRED": 409,
        "QUEUE_FULL": 409,
        "DUPLICATE_TASK": 409,
        "TASK_NOT_FOUND": 404,
        "TASK_NOT_CANCELLABLE": 409,
        "REPORT_NOT_FOUND": 404,
        "REPORT_NOT_READY": 409,
        "REPORT_CONTEXT_TOO_LONG": 413,
        "REPORT_WORKER_MATERIAL_NOT_FOUND": 409,
        "WORKER_CHAT_IDEMPOTENCY_CONFLICT": 409,
        "WORKER_CHAT_WORKER_UNAVAILABLE": 400,
        "WORKER_CHAT_WORKER_CONFLICT": 400,
        "NOTIFICATION_UNAVAILABLE": 503,
        "FILE_SEND_UNSUPPORTED": 409,
        "ASSISTANT_UNAVAILABLE": 503,
        "REPORT_MODEL_NOT_READY": 409,
        "DATASOURCE_TEST_FAILED": 409,
        "REPORT_EXPORT_FAILED": 500,
        "PDF_EXPORT_FAILED": 500,
        "PROFILE_STRATEGY_UNAPPROVED": 409,
        "SCHEDULE_NOT_FOUND": 404,
        "ALERT_NOT_FOUND": 404,
        "UNAUTHORIZED": 401,
        "FORBIDDEN": 403,
        "CONFLICT": 409,
    }
    return mapping.get(code, 500)
