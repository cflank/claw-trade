from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import is_dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from fastapi import APIRouter, Query, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from claw_trade.ui_backend.channel_text_inbound import ChannelTextMessage
from claw_trade.ui_backend.error_translator import translate_internal_error_for_user
from claw_trade.ui_backend.price_alert_service import UiServiceError as PriceAlertServiceError
from claw_trade.ui_backend.report_queue import QueueError
from claw_trade.ui_backend.report_repository import UiProductError
from claw_trade.ui_backend.scheduler_service import UiServiceError as SchedulerServiceError
from claw_trade.ui_backend.settings_service import UiBoundaryError
from claw_trade.ui_contracts.user_dto import to_user_payload
from claw_trade.web.session import resolve_context_id
from claw_trade.web.state import UiHttpServices, build_report_detail_payload

router = APIRouter()


class SendChatMessageRequest(BaseModel):
    requestId: str
    contextId: str
    text: str


class AskReportQuestionRequest(BaseModel):
    requestId: str
    reportId: str
    text: str


class DeleteSavedReportRequest(BaseModel):
    requestId: str
    reportId: str


class CreateIntentDraftRequest(BaseModel):
    requestId: str
    sourceMessageId: str
    text: str


class ConfirmIntentDraftRequest(BaseModel):
    requestId: str
    draftId: str
    decision: str
    overrides: dict[str, Any] | None = None


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


@router.get("/get-report-queue-snapshot")
def get_report_queue_snapshot(request: Request) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.queue.get_report_queue_snapshot_for_user())
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
        services.repository.delete_saved_report(payload.reportId)
        return _success_response({"deleted": True, "reportId": payload.reportId})
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
    return RedirectResponse(url=_device_interface_url(settings.gateway_ws_url), status_code=307)


@router.get("/load-llm-settings")
def load_llm_settings(request: Request, provider: str | None = Query(default=None)) -> JSONResponse:
    services = _services(request)
    try:
        return _success_response(services.llm_bridge.load_llm_settings(provider=provider))
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
            },
            request_id=payload.requestId,
        )
        return _success_response(result)
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


def _device_interface_url(gateway_ws_url: str) -> str:
    parsed = urlsplit(gateway_ws_url)
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme or "http")
    netloc = parsed.netloc
    path = "/"
    if not netloc and parsed.path:
        netloc = parsed.path
    return urlunsplit((scheme, netloc, path, "", ""))


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
        "NOTIFICATION_UNAVAILABLE": 503,
        "FILE_SEND_UNSUPPORTED": 409,
        "ASSISTANT_UNAVAILABLE": 503,
        "DATASOURCE_TEST_FAILED": 409,
        "REPORT_EXPORT_FAILED": 500,
        "PDF_EXPORT_FAILED": 500,
        "PROFILE_STRATEGY_UNAPPROVED": 409,
        "SCHEDULE_NOT_FOUND": 404,
        "ALERT_NOT_FOUND": 404,
        "UNAUTHORIZED": 401,
        "CONFLICT": 409,
    }
    return mapping.get(code, 500)
