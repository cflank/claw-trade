from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from claw_trade.cli.run_control import _build_runner
from claw_trade.config.report_workflow_settings import (
    ReportWorkflowSettings,
    ReportWorkflowSettingsError,
    load_report_workflow_settings,
)
from claw_trade.data_gateway.maintenance import CollectionMaintenanceJobRepository, ScheduledDataMaintenanceRunner
from claw_trade.data_gateway.maintenance.scheduled_runner import (
    _BINANCE_SPOT_USDT_SYMBOL_RE,
    _CRYPTO_HISTORY_MIN_SPOT_USDT_SYMBOLS,
    _CRYPTO_HISTORY_MIN_TRADING_SYMBOL_COVERAGE_RATIO,
    _crypto_history_rows_are_complete,
    _read_crypto_history_latest_rows,
)
from claw_trade.data_gateway.price_quote_provider import PriceAlertQuoteProvider
from claw_trade.data_gateway.runtime import build_data_api_from_env, build_data_gateway_runtime_from_env
from claw_trade.data_gateway.selection_api import (
    build_selection_data_need_audit,
    fetch_selection_batch_from_data_gateway,
    load_cn_a_selection_v1_strategy,
    load_cn_a_selection_v1_strategy_config_ref,
)
from claw_trade.data_gateway.selection_api import (
    resolve_cn_a_selection_trade_date_for_scheduler as resolve_cn_a_closed_trade_date_for_scheduler,
)
from claw_trade.data_gateway.settings_store import (
    UI_EMBEDDING_SETTINGS_COLLECTION,
    UI_REPORT_CLEANUP_SETTINGS_COLLECTION,
    UI_REPORT_MODEL_CONFIG_COLLECTION,
    UI_REPORT_MODEL_STATUS_COLLECTION,
    UI_SELECTION_AUTO_REFRESH_SETTINGS_COLLECTION,
    MongoEmbeddingConfigStore,
    MongoReportCleanupSettingsStore,
    MongoReportModelConfigStore,
    MongoReportModelStatusStore,
    MongoSelectionAutoRefreshSettingsStore,
    build_data_source_settings_stores,
    open_ui_settings_database_from_env,
)
from claw_trade.data_gateway.source_probe import (
    build_data_source_health_tester,
)
from claw_trade.licensing.service import LicenseService, build_license_service
from claw_trade.runtime.openclaw_client import OpenClawClient, ProbeResult
from claw_trade.selection.confirmation import SelectionConfirmationController
from claw_trade.selection.controller import SelectionController
from claw_trade.selection.data_job import SelectionDataJob
from claw_trade.selection.refresh import SelectionDataRefreshService
from claw_trade.selection.models import SelectionMarket
from claw_trade.selection.store import restore_selection_run_store
from claw_trade.ui_backend.channel_bridge import ChannelBridge
from claw_trade.ui_backend.channel_text_inbound import (
    ChannelReplyTarget,
    ChannelTextInboundController,
)
from claw_trade.ui_backend.chart_evidence import get_report_chart_evidence
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.data_source_settings import (
    DataSourceSettingsService,
)
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.openclaw_cron_adapter import OpenClawCronAdapter
from claw_trade.ui_backend.pdf_export_service import PdfExportService, to_pdf_export_for_user
from claw_trade.ui_backend.pdf_renderer import PdfKitWithPandocFallbackRenderer
from claw_trade.ui_backend.pdf_runtime_capabilities import detect_pdf_runtime_capabilities
from claw_trade.ui_backend.pdf_validation import validate_pdf_bytes
from claw_trade.ui_backend.price_alert_scan_service import PriceAlertScanScheduler, PriceAlertScanService
from claw_trade.ui_backend.price_alert_service import PriceAlertService
from claw_trade.ui_backend.report_cleanup import ReportCleanupScheduler, ReportCleanupService, ReportFileSendTracker
from claw_trade.ui_backend.report_cleanup_settings import ReportCleanupSettingsService
from claw_trade.ui_backend.selection_auto_refresh_settings import SelectionAutoRefreshSettingsService
from claw_trade.ui_backend.report_context import ReportContextRetriever
from claw_trade.ui_backend.report_notification_service import ReportNotificationService
from claw_trade.ui_backend.report_qa import ReportQaContextPolicy, ReportQuestionService
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.report_repository import ReportRepository, UiProductError
from claw_trade.ui_backend.scheduled_work_runner import ScheduledWorkRunner
from claw_trade.ui_backend.scheduled_work_store import JsonScheduledWorkStore
from claw_trade.ui_backend.scheduler_service import SchedulerService
from claw_trade.ui_backend.settings_service import SettingsService
from claw_trade.ui_backend.summary_builder import (
    CompletionSummaryBuilder,
    render_completion_summary_text,
)
from claw_trade.ui_backend.worker_chat import WorkerChatController
from claw_trade.ui_backend.worker_chat_openclaw import OpenClawWorkerChatClient
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge
from claw_trade.web.openclaw_gateway import OpenClawGatewayRpcClient
from claw_trade.web.settings import ResearchUiServerSettings
from claw_trade.workflow.models import RunStatus

_DEFAULT_WORKFLOW_CREATE_TIMEOUT_SECONDS = 30.0
_CRYPTO_HISTORY_BOOTSTRAP_START_DATE = date(2025, 6, 1)
_CRYPTO_HISTORY_BOOTSTRAP_DIR = Path(".runtime/crypto-history-full/bootstrap")
_CRYPTO_HISTORY_FACTORY_DATA_ROOT = Path("data/crypto-history-full")
_CRYPTO_HISTORY_FACTORY_COLUMNAR_ROOT = _CRYPTO_HISTORY_FACTORY_DATA_ROOT / "normalized-columnar-usdt-only"
_CRYPTO_HISTORY_FACTORY_RAW_ROOT = _CRYPTO_HISTORY_FACTORY_DATA_ROOT / "binance"
_CRYPTO_HISTORY_RUNTIME_RAW_ROOT = Path(".runtime/crypto-history-full/binance")
_CRYPTO_HISTORY_SEED_DATABASE = "claw_trade_crypto_history_usdt_20260608"
_BINANCE_SPOT_EXCHANGE_INFO_URL = "https://api.binance.com/api/v3/exchangeInfo"
_SELECTION_REPORT_HANDOFF_MARKER = "selection_report_handoff"
_SELECT_TRIGGERED_REPORT_NOTICE = (
    "> 本报告由 select 候选股票触发生成。select 仅表示该股票具备进一步研究价值，"
    "不代表买入建议。报告结论由完整研究流程独立生成，可能与 select 候选方向不同。"
)


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

    def cancel_run(self, run_id: str) -> bool:
        try:
            runner = self._require_runner()
            state = runner.store.load_state(run_id)
            if state.status in (RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED):
                return True
            cancelled = replace(
                state,
                status=RunStatus.CANCELLED,
                active_stage=None,
                updated_at=datetime.now(tz=UTC).isoformat(),
                failure_reason="user_cancelled",
            )
            runner.store.save_state(cancelled)
            return True
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


class _LazyDataMaintenanceRunner:
    def __init__(self) -> None:
        self._runner: ScheduledDataMaintenanceRunner | None = None
        self._lock = Lock()

    def run(
        self,
        *,
        market: str,
        job_kind: str,
        cron_run_id: str | None,
        maintenance_job_id: str | None,
    ) -> object:
        return self._require_runner().run(
            market=market,
            job_kind=job_kind,
            cron_run_id=cron_run_id,
            maintenance_job_id=maintenance_job_id,
        )

    def _require_runner(self) -> ScheduledDataMaintenanceRunner:
        if self._runner is not None:
            return self._runner
        with self._lock:
            if self._runner is None:
                runtime = build_data_gateway_runtime_from_env()
                self._runner = ScheduledDataMaintenanceRunner(
                    data_api=runtime.data_api,
                    job_repository=CollectionMaintenanceJobRepository(runtime.repository),
                    dataset_repository=runtime.repository,
                    crypto_history_columnar_root=_crypto_history_columnar_root_for_maintenance(),
                    crypto_history_initializer=_bootstrap_crypto_history_columnar_root,
                    crypto_trading_symbol_loader=_load_binance_spot_trading_usdt_symbols,
                )
        return self._runner


def _load_binance_spot_trading_usdt_symbols() -> tuple[str, ...]:
    request = urllib.request.Request(
        _BINANCE_SPOT_EXCHANGE_INFO_URL,
        headers={"accept": "application/json", "user-agent": "claw-trade/crypto-maintenance"},
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"binance_exchange_info_unavailable:{exc}") from exc
    symbols: list[str] = []
    for item in tuple(payload.get("symbols", ()) or ()):
        if not isinstance(item, Mapping):
            continue
        if str(item.get("status") or "").upper() != "TRADING":
            continue
        if item.get("isSpotTradingAllowed") is False:
            continue
        symbol = str(item.get("symbol") or "").strip().upper()
        quote = str(item.get("quoteAsset") or "").strip().upper()
        if symbol and quote == "USDT":
            symbols.append(symbol)
    result = tuple(sorted(dict.fromkeys(symbols)))
    if not result:
        raise RuntimeError("binance_exchange_info_has_no_trading_usdt_symbols")
    return result


def _bootstrap_crypto_history_columnar_root(root: Path, as_of: datetime | date) -> None:
    target_root = root
    writes_factory_seed = _path_is_under(root, _CRYPTO_HISTORY_FACTORY_DATA_ROOT)
    if writes_factory_seed:
        if os.environ.get("CLAW_TRADE_ALLOW_FACTORY_COLUMNAR_WRITE") != "1":
            raise RuntimeError("CRYPTO history bootstrap requires CLAW_TRADE_ALLOW_FACTORY_COLUMNAR_WRITE=1")
        if os.environ.get("CLAW_TRADE_ALLOW_FACTORY_MONGO_WRITE") != "1":
            raise RuntimeError("CRYPTO history bootstrap requires CLAW_TRADE_ALLOW_FACTORY_MONGO_WRITE=1")
    as_of_day = as_of.date() if isinstance(as_of, datetime) else as_of
    if as_of_day < _CRYPTO_HISTORY_BOOTSTRAP_START_DATE:
        raise RuntimeError(f"invalid CRYPTO bootstrap end date: {as_of_day.isoformat()}")
    _CRYPTO_HISTORY_BOOTSTRAP_DIR.mkdir(parents=True, exist_ok=True)
    download_manifest = _CRYPTO_HISTORY_BOOTSTRAP_DIR / f"download-spot-1d-{as_of_day.isoformat()}.json"
    import_result = _CRYPTO_HISTORY_BOOTSTRAP_DIR / f"import-spot-1d-{as_of_day.isoformat()}.json"
    raw_root = _CRYPTO_HISTORY_FACTORY_RAW_ROOT if writes_factory_seed else _CRYPTO_HISTORY_RUNTIME_RAW_ROOT
    mongo_uri, mongo_database = _crypto_history_bootstrap_mongo_target(writes_factory_seed=writes_factory_seed)
    if not mongo_uri:
        raise RuntimeError("missing DATA_GATEWAY_MONGODB_URI/CN_A_MONGODB_URI for CRYPTO history bootstrap")

    _run_crypto_history_bootstrap_command(
        [
            sys.executable,
            str(_runtime_script_path("scripts/crypto/download_binance_public_data.py")),
            "--output-root",
            str(raw_root),
            "--market-segment",
            "spot",
            "--interval",
            "1d",
            "--start-date",
            _CRYPTO_HISTORY_BOOTSTRAP_START_DATE.isoformat(),
            "--end-date",
            as_of_day.isoformat(),
            "--all-symbols",
            "--checksum-required",
            "--ignore-missing",
            "--progress-every",
            "100",
            "--output-json",
            str(download_manifest),
        ]
    )
    import_cmd = [
        sys.executable,
        str(_runtime_script_path("scripts/crypto/import_crypto_prepackaged_to_mongo.py")),
        "--spot-root",
        str(raw_root / "data"),
        "--trade-date",
        as_of_day.isoformat(),
        "--import-run-id",
        f"crypto-usdt-bootstrap-{as_of_day.isoformat()}",
        "--start-date",
        _CRYPTO_HISTORY_BOOTSTRAP_START_DATE.isoformat(),
        "--end-date",
        as_of_day.isoformat(),
        "--symbol-manifest",
        str(download_manifest),
        "--market-segment",
        "spot",
        "--interval",
        "1d",
        "--mongo-uri",
        mongo_uri,
        "--columnar-root",
        str(target_root),
        "--output-json",
        str(import_result),
    ]
    if mongo_database:
        import_cmd.extend(["--mongo-database", mongo_database])
    _run_crypto_history_bootstrap_command(import_cmd)


def _crypto_history_columnar_root_for_maintenance() -> Path:
    configured_crypto = os.environ.get("CLAW_TRADE_CRYPTO_HISTORY_COLUMNAR_ROOT", "").strip()
    if configured_crypto:
        return Path(configured_crypto)
    if _crypto_history_seed_is_complete(_CRYPTO_HISTORY_FACTORY_COLUMNAR_ROOT):
        return _CRYPTO_HISTORY_FACTORY_COLUMNAR_ROOT
    configured = os.environ.get("DATA_GATEWAY_COLUMNAR_ROOT", "").strip()
    if configured:
        return Path(configured)
    return Path(".runtime/dev-services/data-gateway/normalized")


def _crypto_history_seed_is_complete(root: Path) -> bool:
    daily_dir = root / "market=CRYPTO" / "dataset=daily_bar" / "granularity=daily"
    if not daily_dir.is_dir() or next(daily_dir.glob("*.parquet"), None) is None:
        return False
    try:
        rows = _read_crypto_history_latest_rows(root)
        if not _crypto_history_rows_are_complete(rows):
            return False
        return _crypto_history_seed_covers_current_trading_symbols(rows)
    except Exception:
        return False


def _crypto_history_seed_covers_current_trading_symbols(rows: Sequence[tuple[Any, Any]]) -> bool:
    try:
        trading_symbols = _load_binance_spot_trading_usdt_symbols()
    except Exception:
        return False
    current = {
        str(symbol or "").strip().upper()
        for symbol in trading_symbols
        if _BINANCE_SPOT_USDT_SYMBOL_RE.fullmatch(str(symbol or "").strip().upper())
    }
    if len(current) < _CRYPTO_HISTORY_MIN_SPOT_USDT_SYMBOLS:
        return False
    covered = {
        str(symbol or "").strip().upper()
        for symbol, _latest_day in rows
        if _BINANCE_SPOT_USDT_SYMBOL_RE.fullmatch(str(symbol or "").strip().upper())
    } & current
    coverage_ratio = len(covered) / len(current)
    return len(covered) >= _CRYPTO_HISTORY_MIN_SPOT_USDT_SYMBOLS and coverage_ratio >= _CRYPTO_HISTORY_MIN_TRADING_SYMBOL_COVERAGE_RATIO


def _crypto_history_bootstrap_mongo_target(*, writes_factory_seed: bool) -> tuple[str, str]:
    if writes_factory_seed:
        uri = (
            os.environ.get("DATA_GATEWAY_SEED_MONGODB_URI")
            or os.environ.get("DATA_GATEWAY_MONGODB_URI")
            or os.environ.get("CN_A_MONGODB_URI")
            or os.environ.get("CRYPTO_MONGODB_URI")
            or ""
        )
        database = os.environ.get("CRYPTO_MONGODB_DATABASE") or _CRYPTO_HISTORY_SEED_DATABASE
        return uri, database
    uri = (
        os.environ.get("DATA_GATEWAY_MONGODB_URI")
        or os.environ.get("CN_A_MONGODB_URI")
        or os.environ.get("CRYPTO_MONGODB_URI")
        or ""
    )
    database = (
        os.environ.get("DATA_GATEWAY_MONGODB_DATABASE")
        or os.environ.get("CN_A_MONGODB_DATABASE")
        or os.environ.get("CRYPTO_MONGODB_DATABASE")
        or _mongo_database_from_uri(uri)
        or ""
    )
    if database.startswith("claw_trade_crypto_history_"):
        raise RuntimeError(
            "CRYPTO runtime history bootstrap refuses to write seed Mongo database: "
            f"{database}; use DATA_GATEWAY_MONGODB_DATABASE for runtime writes and DATA_GATEWAY_SEED_MONGODB_DATABASE for seed reads"
        )
    return uri, database


def _mongo_database_from_uri(uri: str) -> str:
    try:
        parsed = urllib.parse.urlparse(uri)
    except Exception:
        return ""
    path_name = parsed.path.strip("/")
    if not path_name:
        return ""
    return path_name.split("/", 1)[0]


def _path_is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def _runtime_script_path(relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute():
        return path
    release_root = os.environ.get("CLAW_TRADE_RELEASE_ROOT", "").strip()
    if release_root:
        return Path(release_root) / path
    return path


def _run_crypto_history_bootstrap_command(command: Sequence[str]) -> None:
    completed = subprocess.run(
        command,
        cwd=Path.cwd(),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode == 0:
        return
    script = Path(command[1]).name if len(command) > 1 else "unknown"
    raise RuntimeError(
        f"{script} exit={completed.returncode}; "
        f"stdout_tail={_tail(completed.stdout)}; stderr_tail={_tail(completed.stderr)}"
    )


def _tail(value: str, *, limit: int = 1000) -> str:
    text = str(value or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]


class _LazyPriceAlertQuoteProvider:
    def __init__(self) -> None:
        self._provider: PriceAlertQuoteProvider | None = None
        self._lock = Lock()

    def __call__(self, instrument_code: str, market_profile) -> dict[str, Any]:  # type: ignore[no-untyped-def]
        return self._require_provider()(instrument_code, market_profile)

    def _require_provider(self) -> PriceAlertQuoteProvider:
        if self._provider is not None:
            return self._provider
        with self._lock:
            if self._provider is None:
                runtime = build_data_gateway_runtime_from_env()
                self._provider = PriceAlertQuoteProvider(data_api=runtime.data_api)
        return self._provider


@dataclass(frozen=True)
class UiHttpServices:
    report_settings: ReportWorkflowSettings
    chat_controller: ChatController
    queue: ReportTaskQueue
    repository: ReportRepository
    summary_builder: CompletionSummaryBuilder
    pdf_export_service: PdfExportService
    report_question_service: ReportQuestionService
    worker_chat_controller: WorkerChatController
    data_source_settings: DataSourceSettingsService
    channel_bridge: ChannelBridge
    channel_text_inbound: ChannelTextInboundController
    llm_bridge: LlmSettingsBridge
    report_notification_service: ReportNotificationService
    report_cleanup_service: ReportCleanupService
    report_cleanup_scheduler: ReportCleanupScheduler
    scheduler_service: SchedulerService
    price_alert_service: PriceAlertService
    price_alert_scan_service: PriceAlertScanService
    price_alert_scan_scheduler: PriceAlertScanScheduler
    scheduled_work_runner: ScheduledWorkRunner
    settings_service: SettingsService
    report_cleanup_settings: ReportCleanupSettingsService
    selection_auto_refresh_settings: SelectionAutoRefreshSettingsService
    selection_confirmation: SelectionConfirmationController
    selection_controller: SelectionController
    selection_refresh_service: SelectionDataRefreshService
    raw_maintenance_status_provider: Callable[[SelectionMarket], Mapping[str, object] | None]
    license_service: LicenseService


def build_ui_http_services(settings: ResearchUiServerSettings) -> UiHttpServices:
    rpc_client = OpenClawGatewayRpcClient(
        gateway_call_bin=settings.gateway_call_bin,
        gateway_ws_url=settings.gateway_ws_url,
        timeout_ms=settings.gateway_timeout_ms,
        token=settings.gateway_token,
        password=settings.gateway_password,
    )
    rpc_client.prewarm_ui_chat()
    report_settings = _load_report_settings()
    ui_settings_db = open_ui_settings_database_from_env()
    data_source_settings_stores = build_data_source_settings_stores(ui_settings_db)
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
    openviking_data_dir = Path(
        os.environ.get("OPENVIKING_DATA_DIR", "").strip() or ".runtime/dev-services/openviking/data"
    ).resolve()
    openviking_workflow_root = openviking_data_dir / "viking" / "default" / "resources" / "workflow"
    openclaw_state_root = Path(
        os.environ.get("OPENCLAW_STATE_DIR", "").strip() or ".runtime/dev-services/openclaw-state"
    ).resolve()
    repository = ReportRepository(deletion_index_path=run_root / ".ui-deleted-reports.json")
    restore_completed_workflow_reports(repository, run_root)
    workflow_runner = _ControlWorkflowRunner(run_dir=run_root)
    license_service = build_license_service(os.environ)
    queue = ReportTaskQueue(
        ReportWorkflowBridge(workflow_runner, company_name_resolver=_resolve_company_names_from_data_layer),
        completed_report_writer=lambda task, workflow_state: _handle_completed_workflow_report(
            repository,
            report_notification_service,
            chat_controller,
            task=task,
            workflow_state=workflow_state,
        ),
        report_permission_checker=license_service.assert_report_generation_allowed,
    )
    scheduled_work_store = JsonScheduledWorkStore(run_root / ".ui-scheduled-work.json")
    cron_adapter = OpenClawCronAdapter(rpc_client)
    scheduler_service = SchedulerService(
        enqueue_report_task=lambda task, request_id: queue.enqueue_report_task(
            request_id=request_id,
            task_input=task,
            source="scheduled",
        ),
        queue_snapshot_provider=queue.get_report_queue_snapshot_for_user,
        store=scheduled_work_store,
        cron_adapter=cron_adapter,
    )
    price_alert_quote_provider = _LazyPriceAlertQuoteProvider()
    chat_controller_ref: dict[str, ChatController] = {}
    price_alert_service = PriceAlertService(
        quote_provider=price_alert_quote_provider,
        store=scheduled_work_store,
        cron_adapter=cron_adapter,
        notifier=lambda text, notification: _send_price_alert_channel_text(
            channel_bridge,
            text,
            notification,
        ),
        in_app_notifier=lambda alert_id, text: _append_price_alert_in_app(chat_controller_ref, alert_id, text),
    )
    price_alert_scan_service = PriceAlertScanService(
        store=scheduled_work_store,
        quote_provider=price_alert_quote_provider,
        trigger_notifier=lambda alert, quote: price_alert_service.deliver_triggered_notification(alert, quote=quote),
    )
    price_alert_scan_scheduler = PriceAlertScanScheduler(
        store=scheduled_work_store,
        scan_service=price_alert_scan_service,
        legacy_cron_disabler=price_alert_service.disable_openclaw_scan_crons,
    )
    confirmation = ConfirmationController(
        queue,
        scheduler_service=scheduler_service,
        price_alert_service=price_alert_service,
        report_model_ready_checker=llm_bridge.assert_report_model_ready,
        company_name_resolver=_resolve_company_names_from_data_layer,
        default_price_alert_notification=lambda: _default_price_alert_wechat_notification(channel_bridge),
    )
    selection_store = restore_selection_run_store(fail_interrupted_active=True)
    selection_data_job = SelectionDataJob(
        store=selection_store,
        provider_fetch_batch=fetch_selection_batch_from_data_gateway,
        strategy_config_loader=load_cn_a_selection_v1_strategy,
    )
    selection_refresh_service = SelectionDataRefreshService(
        store=selection_store,
        run_data_job=selection_data_job.run,
        run_data_check=selection_data_job.run,
        resolve_closed_trade_date=resolve_cn_a_closed_trade_date_for_scheduler,
        load_approved_strategy_config_ref=load_cn_a_selection_v1_strategy_config_ref,
        build_data_need_audit=build_selection_data_need_audit,
        data_refresh_permission_checker=license_service.assert_data_refresh_allowed,
    )
    scheduled_work_runner = ScheduledWorkRunner(
        price_alert_scan_service=price_alert_scan_service,
        scheduler_service=scheduler_service,
        selection_data_refresh_runner=selection_refresh_service,
        data_maintenance_runner=_LazyDataMaintenanceRunner(),
        data_refresh_permission_checker=license_service.assert_data_refresh_allowed,
    )
    selection_controller = SelectionController(
        store=selection_store,
        openclaw=workflow_runner.selection_openclaw_client(),
        scheduler_enqueue=selection_refresh_service.request_refresh,
        default_trade_date_resolver=resolve_cn_a_closed_trade_date_for_scheduler,
        raw_maintenance_status_provider=_select_raw_maintenance_status,
    )
    chat_controller = ChatController(
        openclaw_client=OpenClawGatewayClient(rpc_client),
        recognizer=IntentRecognizer(),
        confirmation=confirmation,
        queue=queue,
        settings=report_settings,
        report_model_ready_checker=llm_bridge.assert_report_model_ready,
        selection_controller=selection_controller,
        maintenance_status_provider=lambda: _format_maintenance_status_for_chat(llm_bridge),
    )
    chat_controller_ref["controller"] = chat_controller
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
    worker_chat_controller = WorkerChatController(
        repository,
        OpenClawWorkerChatClient(rpc_client),
    )
    data_source_settings = DataSourceSettingsService(
        data_source_store=data_source_settings_stores.data_source_store,
        secret_store=data_source_settings_stores.secret_store,
        env_writer=None,
        health_tester=build_data_source_health_tester(),
    )
    report_cleanup_settings = ReportCleanupSettingsService(
        store=MongoReportCleanupSettingsStore(ui_settings_db[UI_REPORT_CLEANUP_SETTINGS_COLLECTION])
        if ui_settings_db is not None
        else None,
        json_path=run_root / ".ui-report-cleanup-settings.json",
    )
    selection_auto_refresh_settings = SelectionAutoRefreshSettingsService(
        store=MongoSelectionAutoRefreshSettingsStore(ui_settings_db[UI_SELECTION_AUTO_REFRESH_SETTINGS_COLLECTION])
        if ui_settings_db is not None
        else None,
        json_path=run_root / ".ui-selection-auto-refresh-settings.json",
    )
    channel_bridge = ChannelBridge(rpc_client)
    file_send_tracker = ReportFileSendTracker()
    report_notification_service = ReportNotificationService(
        repository,
        summary_builder,
        pdf_export_service,
        channel_bridge,
        file_send_tracker=file_send_tracker,
    )
    report_cleanup_service = ReportCleanupService(
        run_root=run_root,
        openviking_workflow_root=openviking_workflow_root,
        openclaw_state_root=openclaw_state_root,
        repository=repository,
        protected_run_ids_provider=queue.protected_run_ids_for_cleanup,
        in_flight_report_ids_provider=file_send_tracker.active_report_ids,
    )
    report_cleanup_scheduler = ReportCleanupScheduler(
        cleanup_service=report_cleanup_service,
        settings_service=report_cleanup_settings,
    )
    channel_text_inbound = ChannelTextInboundController(
        chat_controller,
        request_full_report_file=lambda report_id, request_id, target: report_notification_service.request_full_report_file(
            report_id,
            request_id,
            channel_kind=target.channel_kind,
            target=target.sender_id,
            account_id=target.account_id,
        ),
        send_channel_text=lambda text, dedupe_key, target: channel_bridge.send_text(
            channel_kind=target.channel_kind,
            text=text,
            dedupe_key=dedupe_key,
            target=target.sender_id,
            account_id=target.account_id,
        ),
        request_selection_report_file=lambda workflow_run_id, markdown, request_id, target: _send_selection_report_file(
            channel_bridge=channel_bridge,
            workflow_run_id=workflow_run_id,
            markdown=markdown,
            request_id=request_id,
            target=target,
        ),
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
        worker_chat_controller=worker_chat_controller,
        data_source_settings=data_source_settings,
        channel_bridge=channel_bridge,
        channel_text_inbound=channel_text_inbound,
        llm_bridge=llm_bridge,
        report_notification_service=report_notification_service,
        report_cleanup_service=report_cleanup_service,
        report_cleanup_scheduler=report_cleanup_scheduler,
        scheduler_service=scheduler_service,
        price_alert_service=price_alert_service,
        price_alert_scan_service=price_alert_scan_service,
        price_alert_scan_scheduler=price_alert_scan_scheduler,
        scheduled_work_runner=scheduled_work_runner,
        settings_service=settings_service,
        report_cleanup_settings=report_cleanup_settings,
        selection_auto_refresh_settings=selection_auto_refresh_settings,
        selection_confirmation=selection_confirmation,
        selection_controller=selection_controller,
        selection_refresh_service=selection_refresh_service,
        raw_maintenance_status_provider=_select_raw_maintenance_status,
        license_service=license_service,
    )


def _resolve_company_names_from_data_layer(*, market: str, symbol_ids: Sequence[str]) -> Mapping[str, str]:
    return build_data_api_from_env().resolve_company_names(market=market, symbol_ids=symbol_ids)


def _save_completed_workflow_report(
    repository: ReportRepository,
    *,
    task: object,
    workflow_state: object,
) -> str:
    run_dir = Path(getattr(workflow_state, "run_dir"))
    report_path = run_dir / "reports" / "final-report.md"
    original_markdown = report_path.read_text(encoding="utf-8")
    markdown = _with_select_triggered_report_notice(original_markdown, task=task)
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
        summary_snippet=_report_summary_snippet(original_markdown),
        generated_at=generated_at or None,
        asset_dir=run_dir / "reports" / "assets",
        origin_context_id=getattr(task, "origin_context_id", None),
    )
    return report_id


def _with_select_triggered_report_notice(markdown: str, *, task: object) -> str:
    marker = str(getattr(task, "selection_stage_marker", "") or "").strip()
    if marker != _SELECTION_REPORT_HANDOFF_MARKER:
        return markdown
    text = markdown.strip()
    if not text or _SELECT_TRIGGERED_REPORT_NOTICE in text:
        return markdown
    lines = text.splitlines()
    if lines and lines[0].startswith("# "):
        return "\n".join((lines[0], "", _SELECT_TRIGGERED_REPORT_NOTICE, "", *lines[1:])).strip() + "\n"
    return f"{_SELECT_TRIGGERED_REPORT_NOTICE}\n\n{text}\n"


def _report_summary_snippet(markdown: str) -> str | None:
    for line in markdown.splitlines():
        text = line.strip()
        if not text or text.startswith("#"):
            continue
        return text[:120]
    return None


def _handle_completed_workflow_report(
    repository: ReportRepository,
    notification_service: object,
    chat_controller: object,
    *,
    task: object,
    workflow_state: object,
) -> None:
    report_id = _save_completed_workflow_report(
        repository,
        task=task,
        workflow_state=workflow_state,
    )
    target = _reply_target_from_origin_context(getattr(task, "origin_context_id", None))
    notify_kwargs: dict[str, object] = {}
    if target is not None:
        notify_kwargs = {
            "channel_kind": target.channel_kind,
            "target": target.sender_id,
            "account_id": target.account_id,
        }
    result = notification_service.notify_report_completion(report_id, **notify_kwargs)  # type: ignore[attr-defined]
    origin_context_id = str(getattr(task, "origin_context_id", "") or "").strip()
    if not origin_context_id:
        return
    text = _render_saved_completion_summary_text(repository, report_id)
    if isinstance(result, dict):
        result_text = str(result.get("text") or "").strip()
        if result_text:
            text = result_text
    append = getattr(chat_controller, "append_report_completed_message", None)
    if not callable(append):
        return
    task_id = str(getattr(task, "task_id", "") or "").strip() or None
    append(
        context_id=origin_context_id,
        report_id=report_id,
        task_id=task_id,
        text=text,
    )


def _render_saved_completion_summary_text(repository: ReportRepository, report_id: str) -> str:
    try:
        summary = CompletionSummaryBuilder(repository).build_completion_summary_from_saved_report(report_id)
    except UiProductError:
        return "报告已完成，但完成简报暂未生成；请查看完整报告。"
    return render_completion_summary_text(summary)


def _reply_target_from_origin_context(origin_context_id: object) -> ChannelReplyTarget | None:
    text = str(origin_context_id or "").strip()
    if not text:
        return None
    parts = text.split(":", 2)
    if len(parts) != 3:
        return None
    channel_kind, account_id, sender_id = (part.strip() for part in parts)
    if not channel_kind or not sender_id:
        return None
    return ChannelReplyTarget(
        channel_kind=channel_kind,
        account_id=account_id or None,
        sender_id=sender_id,
    )


def _send_price_alert_channel_text(
    channel_bridge: ChannelBridge,
    text: str,
    notification: Mapping[str, Any],
) -> dict[str, object]:
    channel_kind = str(notification.get("channel") or "wechat_clawbot")
    target = str(notification.get("target") or "").strip()
    account_id = notification.get("accountId", notification.get("account_id"))
    account_id_text = None if account_id is None else str(account_id).strip() or None
    if not target:
        default_target = channel_bridge.resolve_default_report_file_target(channel_kind=channel_kind)
        if default_target is not None:
            target, account_id_text = default_target
    if not target:
        return {"sent": False, "reason": "missing_wechat_target"}
    return channel_bridge.send_text(
        channel_kind=channel_kind,
        text=text,
        dedupe_key=str(notification.get("dedupeKey") or ""),
        target=target,
        account_id=account_id_text,
    )


def _default_price_alert_wechat_notification(channel_bridge: ChannelBridge) -> dict[str, object] | None:
    default_target = channel_bridge.resolve_default_report_file_target(channel_kind="wechat_clawbot")
    if default_target is None:
        return None
    target, account_id = default_target
    return {
        "channel": "wechat_clawbot",
        "enabled": True,
        "target": target,
        "accountId": account_id,
    }


def _append_price_alert_in_app(
    chat_controller_ref: Mapping[str, ChatController],
    alert_id: str,
    text: str,
) -> None:
    _ = alert_id
    chat_controller = chat_controller_ref.get("controller")
    if chat_controller is None:
        return
    chat_controller.append_channel_plain_message(context_id="normal-chat", actor="system", text=text)


def _send_selection_report_file(
    *,
    channel_bridge: ChannelBridge,
    workflow_run_id: str,
    markdown: str,
    request_id: str,
    target: ChannelReplyTarget,
) -> dict[str, object]:
    safe_workflow_run_id = workflow_run_id.strip()
    safe_markdown = markdown.strip()
    if not safe_workflow_run_id or not safe_markdown:
        return {
            "sent": False,
            "code": "REPORT_NOT_READY",
            "userMessage": "完整选股报告文件暂不可发送，请在设备界面查看。",
        }
    try:
        capabilities = detect_pdf_runtime_capabilities()
        if not capabilities.primary_ready:
            return {
                "sent": False,
                "code": "FILE_SEND_UNSUPPORTED",
                "userMessage": "完整选股报告文件暂不可发送，请在设备界面查看。",
            }
        pdf_bytes = PdfKitWithPandocFallbackRenderer().render(safe_markdown, report_asset_dir=None)
        validation = validate_pdf_bytes(pdf_bytes, required_keywords=("选股",))
        if not validation.valid:
            return {
                "sent": False,
                "code": "FILE_SEND_UNSUPPORTED",
                "userMessage": "完整选股报告文件暂不可发送，请在设备界面查看。",
            }
        result = channel_bridge.send_report_file_via_channel(
            request_id=request_id,
            report_id=safe_workflow_run_id,
            channel_kind=target.channel_kind,
            file_name=f"{safe_workflow_run_id}_selection.pdf",
            payload=pdf_bytes,
            target=target.sender_id,
            account_id=target.account_id,
        )
    except Exception:
        return {
            "sent": False,
            "code": "FILE_SEND_UNSUPPORTED",
            "userMessage": "完整选股报告文件暂不可发送，请在设备界面查看。",
        }
    if not isinstance(result, dict) or not bool(result.get("sent")):
        return {
            "sent": False,
            "code": "FILE_SEND_UNSUPPORTED",
            "userMessage": "完整选股报告文件暂不可发送，请在设备界面查看。",
        }
    return {
        "sent": True,
        "messageId": result.get("messageId") or result.get("message_id"),
        "userMessage": "完整选股报告已发送。",
    }


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
            asset_dir=run_dir / "reports" / "assets",
        )
        pdf_dir = run_dir / "reports" / "pdf"
        if pdf_dir.is_dir():
            for pdf_path in sorted(pdf_dir.glob("pdf_*.pdf"), key=lambda item: item.stat().st_mtime):
                repository.restore_pdf_artifact(report_id, pdf_path)
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


def _format_maintenance_status_for_chat(llm_bridge: LlmSettingsBridge) -> str:
    provider = llm_bridge.get_provider_health_summary()
    runtime = llm_bridge.get_runtime_service_status_summary()
    gaps = llm_bridge.get_live_run_gap_summary()
    evidence = llm_bridge.get_evidence_failure_reason_summary()
    lines = [
        "维护状态摘要",
        f"- provider：{str(provider.get('summary') or provider.get('userMessage') or '').strip()}",
        f"- 运行服务：{str(runtime.get('summary') or runtime.get('userMessage') or '').strip()}",
        f"- 最近运行缺口：{str(gaps.get('summary') or gaps.get('userMessage') or '').strip()}",
        f"- 证据链：{str(evidence.get('summary') or evidence.get('userMessage') or '').strip()}",
        "- 需要更多细节请打开“高级诊断”。",
    ]
    return "\n".join(lines)


def _select_raw_maintenance_status(market: SelectionMarket) -> dict[str, object] | None:
    target_market = {
        SelectionMarket.CN_A: "CN_A",
        SelectionMarket.CRYPTO: "CRYPTO",
    }.get(market)
    if target_market is None:
        return None
    runtime = build_data_gateway_runtime_from_env()
    list_jobs = getattr(runtime.repository, "list_maintenance_jobs", None)
    if not callable(list_jobs):
        return None
    jobs = [
        job
        for job in list_jobs()
        if str(job.get("market") or "").upper() == target_market
        and str(job.get("dataset_scope") or "") == "daily_bar"
    ]
    if not jobs:
        return None
    now = datetime.now(tz=UTC)
    active = [job for job in jobs if _maintenance_job_is_active(job, now=now)]
    if active:
        return _maintenance_job_status_payload(max(active, key=_maintenance_job_sort_key), status="running")
    latest = max(jobs, key=_maintenance_job_sort_key)
    if _maintenance_job_is_failed(latest, now=now):
        return _maintenance_job_status_payload(
            latest,
            status="failed",
            reason=_maintenance_job_failure_reason(latest, now=now),
        )
    return None


def _maintenance_job_is_active(job: Mapping[str, Any], *, now: datetime) -> bool:
    if str(job.get("status") or "").strip().lower() != "running":
        return False
    lock_expires_at = _parse_maintenance_job_datetime(job.get("lock_expires_at"))
    return lock_expires_at is None or lock_expires_at > now


def _maintenance_job_is_failed(job: Mapping[str, Any], *, now: datetime) -> bool:
    status = str(job.get("status") or "").strip().lower()
    if status in {"failed", "error"}:
        return True
    if status == "running":
        lock_expires_at = _parse_maintenance_job_datetime(job.get("lock_expires_at"))
        return lock_expires_at is not None and lock_expires_at <= now
    return False


def _maintenance_job_failure_reason(job: Mapping[str, Any], *, now: datetime) -> str:
    error = str(job.get("error") or "").strip()
    if error:
        return error
    if str(job.get("status") or "").strip().lower() == "running":
        lock_expires_at = _parse_maintenance_job_datetime(job.get("lock_expires_at"))
        if lock_expires_at is not None and lock_expires_at <= now:
            return f"raw_data_maintenance_lock_expired:{job.get('job_id')}"
    return f"raw_data_maintenance_failed:{job.get('job_id')}"


def _maintenance_job_status_payload(
    job: Mapping[str, Any],
    *,
    status: str,
    reason: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "status": status,
        "job_id": str(job.get("job_id") or ""),
        "market": str(job.get("market") or ""),
        "dataset_scope": str(job.get("dataset_scope") or ""),
        "started_at": _maintenance_job_payload_text(job.get("started_at")),
        "finished_at": _maintenance_job_payload_text(job.get("finished_at")),
        "lock_expires_at": _maintenance_job_payload_text(job.get("lock_expires_at")),
    }
    if reason:
        payload["reason"] = reason
        payload["error"] = reason
    elif job.get("error"):
        payload["reason"] = str(job.get("error"))
        payload["error"] = str(job.get("error"))
    return payload


def _maintenance_job_payload_text(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _maintenance_job_sort_key(job: Mapping[str, Any]) -> tuple[datetime, datetime, str]:
    scheduled_at = _parse_maintenance_job_datetime(job.get("scheduled_at")) or datetime.min.replace(tzinfo=UTC)
    started_at = _parse_maintenance_job_datetime(job.get("started_at")) or datetime.min.replace(tzinfo=UTC)
    return (scheduled_at, started_at, str(job.get("job_id") or ""))


def _parse_maintenance_job_datetime(value: object) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


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
