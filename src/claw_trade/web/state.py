from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from claw_trade.cli.run_control import _build_runner
from claw_trade.config.report_workflow_settings import (
    ReportWorkflowSettings,
    ReportWorkflowSettingsError,
    load_report_workflow_settings,
)
from claw_trade.runtime.openclaw_client import OpenClawClient, ProbeResult
from claw_trade.selection.confirmation import SelectionConfirmationController
from claw_trade.selection.controller import SelectionController
from claw_trade.selection.data_job import SelectionDataJob
from claw_trade.selection.provider_batch import (
    build_selection_provider_batch_plan,
    fetch_selection_batch_from_data_gateway,
    load_cn_a_selection_v1_strategy,
    load_cn_a_selection_v1_strategy_config_ref,
    resolve_cn_a_closed_trade_date_for_scheduler,
)
from claw_trade.selection.refresh import SelectionDataRefreshService
from claw_trade.selection.store import restore_selection_run_store
from claw_trade.ui_backend.channel_bridge import ChannelBridge
from claw_trade.ui_backend.channel_text_inbound import ChannelTextInboundController
from claw_trade.ui_backend.chart_evidence import get_report_chart_evidence
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.data_source_settings import (
    DataSourceSettingsService,
)
from claw_trade.ui_backend.data_source_runtime_checks import (
    build_data_source_health_tester,
    build_price_alert_quote_provider,
)
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge
from claw_trade.ui_backend.mongo_settings_store import (
    MongoDataSourceStore,
    MongoEmbeddingConfigStore,
    MongoReportModelConfigStore,
    MongoReportModelStatusStore,
    MongoSecretStore,
    UI_DATA_SOURCE_SETTINGS_COLLECTION,
    UI_EMBEDDING_SETTINGS_COLLECTION,
    UI_REPORT_MODEL_CONFIG_COLLECTION,
    UI_REPORT_MODEL_STATUS_COLLECTION,
    UI_SECRET_SETTINGS_COLLECTION,
    open_ui_settings_database_from_env,
)
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.pdf_export_service import PdfExportService, to_pdf_export_for_user
from claw_trade.ui_backend.price_alert_service import PriceAlertService
from claw_trade.ui_backend.report_context import ReportContextRetriever
from claw_trade.ui_backend.report_notification_service import ReportNotificationService
from claw_trade.ui_backend.report_qa import ReportQaContextPolicy, ReportQuestionService
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError
from claw_trade.ui_backend.scheduler_service import SchedulerService
from claw_trade.ui_backend.settings_service import SettingsService
from claw_trade.ui_backend.summary_builder import CompletionSummaryBuilder
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge
from claw_trade.web.openclaw_gateway import OpenClawGatewayRpcClient
from claw_trade.web.settings import ResearchUiServerSettings

_DEFAULT_WORKFLOW_CREATE_TIMEOUT_SECONDS = 30.0


class _ControlWorkflowRunner:
    def __init__(self, *, run_dir: Path) -> None:
        self._run_dir = run_dir
        self._runner = None
        self._runner_lock = Lock()
        self._create_run_lock = Lock()
        self._run_threads: dict[str, Thread] = {}

    def create_run(self, request):  # type: ignore[no-untyped-def]
        runner = self._require_runner()
        created_event = Event()
        created_state: dict[str, object] = {}
        original_create = runner.store.create_run

        def _capture_create(req):  # type: ignore[no-untyped-def]
            state = original_create(req)
            created_state["value"] = state
            created_event.set()
            return state

        with self._create_run_lock:
            runner.store.create_run = _capture_create

            def _run_workflow() -> None:
                try:
                    runner.run(request)
                finally:
                    runner.store.create_run = original_create

            thread = Thread(target=_run_workflow, daemon=True, name="claw-trade-ui-workflow")
            thread.start()

        if not created_event.wait(timeout=_workflow_create_timeout_seconds()):
            raise RuntimeError("assistant_unavailable")
        state = created_state.get("value")
        run_id = str(getattr(state, "run_id", "")).strip()
        if not run_id:
            raise RuntimeError("assistant_unavailable")
        self._run_threads[run_id] = thread
        return run_id

    def load_state(self, run_id: str):  # type: ignore[no-untyped-def]
        try:
            runner = self._require_runner()
            return runner.store.load_state(run_id)
        except Exception as exc:
            raise RuntimeError("assistant_unavailable") from exc

    def selection_openclaw_client(self) -> OpenClawClient:
        return OpenClawClient(_SelectionOpenClawRunner(self))

    def _require_runner(self):
        if self._runner is not None:
            return self._runner
        with self._runner_lock:
            if self._runner is None:
                try:
                    self._runner = _build_runner(self._run_dir)
                except Exception as exc:
                    raise RuntimeError("assistant_unavailable") from exc
        return self._runner

    def _require_openclaw_runner(self):  # type: ignore[no-untyped-def]
        runner = self._require_runner()
        openclaw = getattr(runner, "openclaw", None)
        if not isinstance(openclaw, OpenClawClient):
            raise RuntimeError("assistant_unavailable")
        openclaw_runner = getattr(openclaw, "_runner", None)
        if openclaw_runner is None:
            raise RuntimeError("assistant_unavailable")
        return openclaw_runner


class _SelectionOpenClawRunner:
    def __init__(self, workflow_runner: _ControlWorkflowRunner) -> None:
        self._workflow_runner = workflow_runner

    def probe(self) -> ProbeResult:
        try:
            openclaw_runner = self._workflow_runner._require_openclaw_runner()  # noqa: SLF001
        except Exception as exc:
            return ProbeResult.failed(f"openclaw probe 失败: {exc}")
        return openclaw_runner.probe()

    def run_worker(self, payload: dict[str, object]) -> dict[str, object]:
        openclaw_runner = self._workflow_runner._require_openclaw_runner()  # noqa: SLF001
        return openclaw_runner.run_worker(payload)


class _ReportQaGatewayAdapter:
    def __init__(self, rpc_client: OpenClawGatewayRpcClient) -> None:
        self._rpc = rpc_client

    def sessions_create(self, *, metadata: dict[str, object]) -> str:
        return self._rpc.sessions_create(metadata=metadata)

    def chat_send(
        self,
        *,
        request_id: str,
        context_id: str,
        session_id: str,
        text: str,
    ) -> dict[str, object]:
        return self._rpc.report_qa_chat_send(
            request_id=request_id,
            context_id=context_id,
            session_id=session_id,
            text=text,
        )


@dataclass(frozen=True)
class UiHttpServices:
    report_settings: ReportWorkflowSettings
    chat_controller: ChatController
    queue: ReportTaskQueue
    repository: ReportRepository
    summary_builder: CompletionSummaryBuilder
    pdf_export_service: PdfExportService
    report_question_service: ReportQuestionService
    data_source_settings: DataSourceSettingsService
    channel_bridge: ChannelBridge
    channel_text_inbound: ChannelTextInboundController
    llm_bridge: LlmSettingsBridge
    report_notification_service: ReportNotificationService
    scheduler_service: SchedulerService
    price_alert_service: PriceAlertService
    settings_service: SettingsService
    selection_confirmation: SelectionConfirmationController


def build_ui_http_services(settings: ResearchUiServerSettings) -> UiHttpServices:
    rpc_client = OpenClawGatewayRpcClient(
        gateway_call_bin=settings.gateway_call_bin,
        gateway_ws_url=settings.gateway_ws_url,
        timeout_ms=settings.gateway_timeout_ms,
        token=settings.gateway_token,
        password=settings.gateway_password,
    )
    report_settings = _load_report_settings()
    ui_settings_db = open_ui_settings_database_from_env()
    llm_bridge = LlmSettingsBridge(
        rpc_client,
        embedding_config_store=MongoEmbeddingConfigStore(ui_settings_db[UI_EMBEDDING_SETTINGS_COLLECTION])
        if ui_settings_db is not None
        else None,
        report_model_status_store=MongoReportModelStatusStore(ui_settings_db[UI_REPORT_MODEL_STATUS_COLLECTION])
        if ui_settings_db is not None
        else None,
        report_model_config_store=MongoReportModelConfigStore(ui_settings_db[UI_REPORT_MODEL_CONFIG_COLLECTION])
        if ui_settings_db is not None
        else None,
    )
    run_root = Path(report_settings.run_dir).resolve()
    repository = ReportRepository(deletion_index_path=run_root / ".ui-deleted-reports.json")
    restore_completed_workflow_reports(repository, run_root)
    workflow_runner = _ControlWorkflowRunner(run_dir=run_root)
    queue = ReportTaskQueue(
        ReportWorkflowBridge(workflow_runner),
        completed_report_writer=lambda task, workflow_state: _save_completed_workflow_report(
            repository,
            task=task,
            workflow_state=workflow_state,
        ),
    )
    scheduler_service = SchedulerService(
        enqueue_report_task=lambda task, request_id: queue.enqueue_report_task(
            request_id=request_id,
            task_input=task,
            source="scheduled",
        ),
        queue_snapshot_provider=queue.get_report_queue_snapshot_for_user,
    )
    price_alert_quote_provider = build_price_alert_quote_provider()
    price_alert_service = PriceAlertService(
        quote_provider=price_alert_quote_provider,
    )
    confirmation = ConfirmationController(
        queue,
        scheduler_service=scheduler_service,
        price_alert_service=price_alert_service,
        report_model_ready_checker=llm_bridge.assert_report_model_ready,
    )
    selection_store = restore_selection_run_store()
    selection_data_job = SelectionDataJob(
        store=selection_store,
        provider_fetch_batch=fetch_selection_batch_from_data_gateway,
        strategy_config_loader=load_cn_a_selection_v1_strategy,
    )
    selection_refresh_service = SelectionDataRefreshService(
        store=selection_store,
        run_data_job=selection_data_job.run,
        resolve_closed_trade_date=resolve_cn_a_closed_trade_date_for_scheduler,
        load_approved_strategy_config_ref=load_cn_a_selection_v1_strategy_config_ref,
        build_provider_batch_plan=build_selection_provider_batch_plan,
    )
    chat_controller = ChatController(
        openclaw_client=OpenClawGatewayClient(rpc_client),
        recognizer=IntentRecognizer(),
        confirmation=confirmation,
        queue=queue,
        settings=report_settings,
        report_model_ready_checker=llm_bridge.assert_report_model_ready,
        selection_controller=SelectionController(
            store=selection_store,
            openclaw=workflow_runner.selection_openclaw_client(),
            scheduler_enqueue=selection_refresh_service.request_refresh,
        ),
    )
    selection_confirmation = SelectionConfirmationController(
        store=selection_store,
        queue=queue,
        settings=report_settings,
    )
    summary_builder = CompletionSummaryBuilder(repository)
    pdf_export_service = PdfExportService(repository)
    report_question_service = ReportQuestionService(
        repository,
        _ReportQaGatewayAdapter(rpc_client),
        context_policy=ReportQaContextPolicy(max_total_chars=_report_qa_max_chars()),
        context_retriever=ReportContextRetriever(run_root=run_root),
    )
    data_source_settings = DataSourceSettingsService(
        data_source_store=MongoDataSourceStore(ui_settings_db[UI_DATA_SOURCE_SETTINGS_COLLECTION])
        if ui_settings_db is not None
        else None,
        secret_store=MongoSecretStore(ui_settings_db[UI_SECRET_SETTINGS_COLLECTION])
        if ui_settings_db is not None
        else None,
        env_writer=None,
        health_tester=build_data_source_health_tester(),
    )
    channel_bridge = ChannelBridge(rpc_client)
    channel_text_inbound = ChannelTextInboundController(chat_controller)
    report_notification_service = ReportNotificationService(
        repository,
        summary_builder,
        pdf_export_service,
        channel_bridge,
    )
    settings_service = SettingsService(env_writer=None)
    return UiHttpServices(
        report_settings=report_settings,
        chat_controller=chat_controller,
        queue=queue,
        repository=repository,
        summary_builder=summary_builder,
        pdf_export_service=pdf_export_service,
        report_question_service=report_question_service,
        data_source_settings=data_source_settings,
        channel_bridge=channel_bridge,
        channel_text_inbound=channel_text_inbound,
        llm_bridge=llm_bridge,
        report_notification_service=report_notification_service,
        scheduler_service=scheduler_service,
        price_alert_service=price_alert_service,
        settings_service=settings_service,
        selection_confirmation=selection_confirmation,
    )


def _save_completed_workflow_report(
    repository: ReportRepository,
    *,
    task: object,
    workflow_state: object,
) -> None:
    run_dir = Path(getattr(workflow_state, "run_dir"))
    report_path = run_dir / "reports" / "final-report.md"
    markdown = report_path.read_text(encoding="utf-8")
    generated_at = str(getattr(workflow_state, "updated_at", "") or "")
    report_id = str(
        getattr(workflow_state, "run_id", "")
        or getattr(task, "run_id", "")
        or getattr(task, "task_id")
    )
    repository.save_succeeded_report(
        report_id=report_id,
        instrument_code=str(getattr(task, "instrument_code")),
        instrument_name=getattr(task, "instrument_name"),
        market=str(getattr(task, "market")),
        title=f"{getattr(task, 'instrument_code')} 报告",
        markdown=markdown,
        generated_at=generated_at or None,
        summary_snippet="完整报告已生成。",
        asset_dir=run_dir / "reports" / "assets",
    )


def restore_completed_workflow_reports(repository: ReportRepository, run_root: Path) -> int:
    if not run_root.exists():
        return 0
    restored = 0
    for run_dir in sorted(item for item in run_root.iterdir() if item.is_dir()):
        state_path = run_dir / "state.json"
        report_path = run_dir / "reports" / "final-report.md"
        if not state_path.exists() or not report_path.exists():
            continue
        state = _read_json_object(state_path)
        if not state or str(state.get("status") or "").lower() != "completed":
            continue
        report_id = str(state.get("run_id") or run_dir.name).strip()
        if not report_id or repository.is_deleted_report(report_id):
            continue
        request = state.get("request")
        request_payload = request if isinstance(request, dict) else {}
        ticker = str(request_payload.get("ticker") or report_id).strip()
        markdown = report_path.read_text(encoding="utf-8")
        repository.save_succeeded_report(
            report_id=report_id,
            instrument_code=ticker,
            instrument_name=_optional_text(request_payload.get("company_name")),
            market=str(request_payload.get("market") or request_payload.get("profile") or "US"),
            title=f"{ticker} 报告",
            markdown=markdown,
            generated_at=_optional_text(state.get("updated_at") or state.get("created_at")),
            summary_snippet="完整报告已生成。",
            asset_dir=run_dir / "reports" / "assets",
        )
        restored += 1
    return restored


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _optional_text(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def build_report_detail_payload(services: UiHttpServices, *, report_id: str) -> dict[str, object]:
    report = services.repository.get_report(report_id)
    if report is None:
        raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")
    markdown = report.markdown
    chart = get_report_chart_evidence(
        report_id=report_id,
        chart_assets=services.repository.list_markdown_image_assets(report_id),
        markdown=markdown,
    )
    latest_pdf = services.pdf_export_service.get_latest_record(report_id)
    pdf_payload = (
        {"state": "not_requested", "available": False, "userMessage": None, "updatedAt": None}
        if latest_pdf is None
        else to_pdf_export_for_user(latest_pdf)
    )
    summary = services.summary_builder.get_cached(report_id)
    if summary is None:
        summary = services.summary_builder.build_completion_summary_from_saved_report(
            report_id,
            pdf_available=bool(pdf_payload.get("available")),
        )
    return services.repository.get_report_detail(
        report_id,
        completion_summary=summary,
        chart_evidence=chart,
        data_source_events=[],
        pdf_export=pdf_payload,
    )


def default_frontend_dist(project_root: Path | None = None) -> Path:
    root = project_root or Path.cwd()
    return root / "web" / "research-ui" / "dist"


def _load_report_settings() -> ReportWorkflowSettings:
    try:
        return load_report_workflow_settings()
    except ReportWorkflowSettingsError:
        return ReportWorkflowSettings()


def _report_qa_max_chars() -> int | None:
    raw = os.environ.get("CLAW_TRADE_UI_REPORT_QA_MAX_CHARS", "").strip()
    if not raw:
        return 40_000
    try:
        value = int(raw)
    except ValueError:
        return 40_000
    if value <= 0:
        return None
    return value


def _workflow_create_timeout_seconds() -> float:
    raw = os.environ.get("CLAW_TRADE_UI_WORKFLOW_CREATE_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _DEFAULT_WORKFLOW_CREATE_TIMEOUT_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_WORKFLOW_CREATE_TIMEOUT_SECONDS
    if value <= 0:
        return _DEFAULT_WORKFLOW_CREATE_TIMEOUT_SECONDS
    return value
