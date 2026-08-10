from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

import pytest

from claw_trade.ui_backend.pdf_export_service import PdfExportService
from claw_trade.ui_backend.pdf_runtime_capabilities import (
    PdfRuntimeCapabilities,
    PdfRuntimeCapability,
)
from claw_trade.ui_backend.report_notification_service import ReportNotificationService
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.report_repository import ReportRepository
from claw_trade.ui_backend.scheduled_work_store import InMemoryScheduledWorkStore
from claw_trade.ui_backend.scheduler_service import SchedulerService
from claw_trade.ui_backend.summary_builder import CompletionSummaryBuilder
from claw_trade.ui_backend.wechat_delivery_store import WechatDeliveryStore
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge
from claw_trade.ui_contracts.enums import MarketProfile


class _ChannelBridge:
    def __init__(
        self,
        *,
        connected: bool,
        can_send_text: bool,
        can_send_file: bool = False,
        default_target: tuple[str, str | None] | None = None,
        send_results: list[dict[str, object]] | None = None,
        unique_account: str | None = "account-new",
    ) -> None:
        self._connected = connected
        self._can_send_text = can_send_text
        self._can_send_file = can_send_file
        self._default_target = default_target
        self._send_results = list(send_results or [{"sent": True, "messageId": "message-1"}])
        self._unique_account = unique_account
        self.last_text = ""
        self.last_target = ""
        self.last_account_id: str | None = None
        self.last_send_guarded = False
        self._delivery_guard_active = False
        self.delivery_guard_entries = 0
        self.send_calls = 0

    def get_channel_status(self, *, probe: bool = False) -> dict[str, object]:
        return {
            "state": "connected" if self._connected else "disconnected",
            "canSendText": self._can_send_text,
            "canSendFile": self._can_send_file,
        }

    def send_text(
        self,
        *,
        channel_kind: str,
        text: str,
        dedupe_key: str,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        self.last_text = text
        self.last_target = target or ""
        self.last_account_id = account_id
        self.last_send_guarded = self._delivery_guard_active
        self.send_calls += 1
        assert channel_kind == "wechat_clawbot"
        assert dedupe_key
        return self._send_results.pop(0) if self._send_results else {"sent": True, "messageId": "message-repeat"}

    def send_report_file_via_channel(
        self,
        *,
        request_id: str,
        report_id: str,
        channel_kind: str,
        file_name: str,
        payload: bytes | None = None,
        file_path=None,
        target: str | None = None,
        account_id: str | None = None,
    ) -> dict[str, object]:
        return {"sent": True, "messageId": "m1"}

    def resolve_default_report_file_target(self, *, channel_kind: str) -> tuple[str, str | None] | None:
        assert channel_kind == "wechat_clawbot"
        return self._default_target

    @contextmanager
    def wechat_delivery_account(self):  # type: ignore[no-untyped-def]
        self.delivery_guard_entries += 1
        self._delivery_guard_active = True
        try:
            yield self._unique_account
        finally:
            self._delivery_guard_active = False


class _PassRenderer:
    def __init__(self) -> None:
        self.calls = 0

    def render(self, markdown: str, *, report_asset_dir=None) -> bytes:  # type: ignore[no-untyped-def]
        _ = (markdown, report_asset_dir)
        self.calls += 1
        return b"%PDF-1.7\n" + (b"A" * 700)


def _ready_capabilities() -> PdfRuntimeCapabilities:
    return PdfRuntimeCapabilities(
        items=(
            PdfRuntimeCapability(name="python:markdown", category="primary", available=True, required=True),
            PdfRuntimeCapability(name="python:pdfkit", category="primary", available=True, required=True),
            PdfRuntimeCapability(name="bin:wkhtmltopdf", category="primary", available=True, required=True),
            PdfRuntimeCapability(name="bin:fontconfig(fc-match)", category="primary", available=True, required=True),
            PdfRuntimeCapability(name="font:noto-cjk", category="primary", available=True, required=True),
        )
    )


def _build_service(channel: _ChannelBridge, notifications: list[tuple[str, str]]) -> ReportNotificationService:
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-notify",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 报告\n## 核心理由\n- 资金回流",
        pm_final_conclusion="可继续跟踪",
    )
    summary_builder = CompletionSummaryBuilder(repo)
    pdf_service = PdfExportService(repo)
    return ReportNotificationService(
        repo,
        summary_builder,
        pdf_service,
        channel,
        in_app_notifier=lambda report_id, text: notifications.append((report_id, text)),
    )


def _build_durable_service(
    tmp_path: Path,
    channel: _ChannelBridge,
    *,
    unique_account: str | None = "account-new",
    now_provider: Callable[[], datetime] | None = None,
) -> tuple[ReportNotificationService, WechatDeliveryStore]:
    channel._unique_account = unique_account
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-notify",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 报告\n## 核心理由\n- 资金回流",
        pm_final_conclusion="可继续跟踪",
    )
    store = WechatDeliveryStore(
        tmp_path / "wechat-delivery.json",
        now_provider=lambda: "2026-08-07T09:00:00Z",
    )
    return (
        ReportNotificationService(
            repo,
            CompletionSummaryBuilder(repo),
            PdfExportService(repo),
            channel,
            delivery_store=store,
            now_provider=now_provider or (lambda: datetime(2026, 8, 7, 9, 0, tzinfo=UTC)),
        ),
        store,
    )


def test_report_delivery_moves_from_waiting_report_to_pending_before_send(tmp_path: Path) -> None:
    service, store = _build_durable_service(
        tmp_path,
        _ChannelBridge(connected=False, can_send_text=False),
        unique_account=None,
    )
    service.create_report_delivery_intent("intent-1")
    assert store.get_delivery_status("intent-1")["state"] == "waiting_report"

    result = service.notify_report_completion("r-notify", delivery_intent_id="intent-1")

    assert result["delivery"] == "pending"
    assert store.get_delivery_status("intent-1")["state"] == "pending"


def test_pending_delivery_is_sent_after_connection_recovers(tmp_path: Path) -> None:
    channel = _ChannelBridge(connected=False, can_send_text=False)
    service, store = _build_durable_service(tmp_path, channel)
    challenge = service.create_binding_code(request_id="binding-request")
    service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-new",
        sender_id="sender-new",
    )
    service.create_report_delivery_intent("intent-reconnect")
    service.notify_report_completion("r-notify", delivery_intent_id="intent-reconnect")

    channel._connected = True
    channel._can_send_text = True
    retried = service.retry_pending()

    assert retried == {"attempted": 1, "sent": 1}
    assert store.get_delivery_status("intent-reconnect")["state"] == "sent"


def test_durable_delivery_invites_pdf_reply_when_channel_can_send_files(tmp_path: Path) -> None:
    channel = _ChannelBridge(connected=True, can_send_text=True, can_send_file=True)
    service, store = _build_durable_service(tmp_path, channel)
    store.set_binding(account_id="account-new", sender_id="sender-new")
    service.create_report_delivery_intent("intent-pdf-reply")

    result = service.notify_report_completion("r-notify", delivery_intent_id="intent-pdf-reply")

    assert result["delivery"] == "sent"
    assert "需要 PDF 时，回复“发送完整报告”。" in channel.last_text


def test_latest_delivered_report_only_resolves_for_the_binding_that_received_it(tmp_path: Path) -> None:
    now = ["2026-08-07T09:00:00Z"]
    store = WechatDeliveryStore(tmp_path / "wechat-delivery.json", now_provider=lambda: now[0])
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="report-old-binding",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 报告",
        pm_final_conclusion="继续观察",
    )
    service = ReportNotificationService(
        repo,
        CompletionSummaryBuilder(repo),
        PdfExportService(repo),
        _ChannelBridge(connected=True, can_send_text=True),
        delivery_store=store,
    )
    store.set_binding(account_id="account-old", sender_id="sender-old")
    store.create_waiting_report("intent-old-binding")
    store.mark_report_pending("intent-old-binding", report_id="report-old-binding")
    now[0] = "2026-08-07T09:01:00Z"
    store.mark_sent(
        "intent-old-binding",
        message_id="message-1",
        account_id="account-old",
        sender_id="sender-old",
    )

    assert service.latest_delivered_report_for_recipient(
        account_id="account-old", sender_id="sender-old"
    ) == "report-old-binding"

    now[0] = "2026-08-07T09:02:00Z"
    store.set_binding(account_id="account-new", sender_id="sender-new")
    assert service.latest_delivered_report_for_recipient(
        account_id="account-new", sender_id="sender-new"
    ) is None


def test_confirmed_schedule_recipient_replaces_missing_binding_and_sends_pending(tmp_path: Path) -> None:
    channel = _ChannelBridge(connected=True, can_send_text=True)
    service, store = _build_durable_service(tmp_path, channel)
    service.create_report_delivery_intent("intent-scheduled")
    service.notify_report_completion("r-notify", delivery_intent_id="intent-scheduled")

    remembered = service.remember_current_notification_recipient(
        account_id="account-new",
        sender_id="sender-new",
    )

    assert remembered is True
    assert store.get_delivery_status("intent-scheduled")["state"] == "sent"
    assert channel.last_account_id == "account-new"
    assert channel.last_target == "sender-new"


def test_schedule_recipient_rejects_non_current_account(tmp_path: Path) -> None:
    service, store = _build_durable_service(
        tmp_path,
        _ChannelBridge(connected=True, can_send_text=True),
    )

    remembered = service.remember_current_notification_recipient(
        account_id="account-old",
        sender_id="sender-old",
    )

    assert remembered is False
    assert store.get_binding() is None


def test_unbound_pending_batch_does_not_probe_wechat_for_each_delivery(tmp_path: Path) -> None:
    channel = _ChannelBridge(connected=False, can_send_text=False)
    service, _store = _build_durable_service(tmp_path, channel, unique_account=None)
    for index in range(28):
        intent_id = f"intent-unbound-{index}"
        service.create_report_delivery_intent(intent_id)
        service.notify_report_completion("r-notify", delivery_intent_id=intent_id)

    result = service.retry_pending()

    assert result == {"attempted": 0, "sent": 0}
    assert channel.delivery_guard_entries == 0


def test_disconnected_pending_batch_checks_wechat_once(tmp_path: Path) -> None:
    channel = _ChannelBridge(connected=True, can_send_text=True)
    service, store = _build_durable_service(tmp_path, channel)
    challenge = service.create_binding_code(request_id="binding-request")
    service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-new",
        sender_id="sender-new",
    )
    channel.delivery_guard_entries = 0
    channel._connected = False
    channel._can_send_text = False
    for index in range(28):
        intent_id = f"intent-disconnected-{index}"
        store.create_waiting_report(intent_id)
        store.mark_report_pending(intent_id, report_id="r-notify")

    result = service.retry_pending()

    assert result == {"attempted": 0, "sent": 0}
    assert channel.delivery_guard_entries == 1


def test_completed_linked_delivery_is_recovered_after_service_restart(tmp_path: Path) -> None:
    channel = _ChannelBridge(connected=True, can_send_text=True)
    service, store = _build_durable_service(tmp_path, channel)
    challenge = service.create_binding_code(request_id="binding-request")
    service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-new",
        sender_id="sender-new",
    )
    service.create_report_delivery_intent("intent-restart")
    service.link_report_delivery_intent("intent-restart", "r-notify")

    restarted, _ = _build_durable_service(tmp_path, channel)
    recovered = restarted.recover_completed_deliveries()

    assert recovered == 1
    assert store.get_delivery_status("intent-restart")["state"] == "sent"
    assert channel.last_target == "sender-new"


def test_binding_code_requires_unique_current_account_and_transfers_pending(tmp_path: Path) -> None:
    channel = _ChannelBridge(connected=True, can_send_text=True)
    service, store = _build_durable_service(tmp_path, channel)
    service.create_report_delivery_intent("intent-1")
    service.notify_report_completion("r-notify", delivery_intent_id="intent-1")
    challenge = service.create_binding_code(request_id="binding-request", ttl_seconds=300)

    rejected = service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-old",
        sender_id="sender-1",
    )
    accepted = service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-new",
        sender_id="sender-1",
    )
    reused = service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-new",
        sender_id="sender-1",
    )

    assert rejected == {"bound": False, "message": "当前微信账号不唯一或与入站账号不一致，未绑定。"}
    assert accepted["bound"] is True
    assert accepted["message"] == "绑定成功，已将 1 条待发送报告转移到此微信并开始发送。"
    assert reused == {"bound": False, "message": "绑定码错误、已过期或已使用。"}
    assert store.get_delivery_status("intent-1")["state"] == "sent"
    assert channel.last_account_id == "account-new"
    assert channel.last_target == "sender-1"


def test_scheduled_report_created_on_a_is_delivered_only_to_newly_bound_b(tmp_path: Path) -> None:
    class _Runner:
        def create_run(self, _request):  # type: ignore[no-untyped-def]
            return "run-scheduled-wechat"

        def load_state(self, _run_id):  # type: ignore[no-untyped-def]
            return type("State", (), {"status": "frontline_running", "completed_workers": ()})()

    @contextmanager
    def _report_lock(_path):  # type: ignore[no-untyped-def]
        yield 1

    channel = _ChannelBridge(connected=True, can_send_text=True, unique_account="account-a")
    service, store = _build_durable_service(tmp_path, channel, unique_account="account-a")
    queue = ReportTaskQueue(
        ReportWorkflowBridge(_Runner()),
        report_lock_opener=_report_lock,
        notification_intent_writer=lambda task: service.create_report_delivery_intent(
            str(task.notification_intent_id)
        ),
    )
    queued: list[dict[str, object]] = []

    def _enqueue(task_input, request_id):  # type: ignore[no-untyped-def]
        result = queue.enqueue_report_task(
            request_id=request_id,
            task_input=task_input,
            source="scheduled",
        )
        queued.append(result)
        return result["task"]

    scheduler = SchedulerService(
        enqueue_report_task=_enqueue,
        store=InMemoryScheduledWorkStore(),
        now_provider=lambda: datetime(2026, 8, 7, 9, 0, tzinfo=UTC),
    )
    schedule = scheduler.create_scheduled_report(
        request_id="schedule-created-by-a",
        instrument_code="BTC",
        market=MarketProfile.CRYPTO,
        frequency="daily",
        time_of_day="09:30",
        notification={"channel": "wechat_clawbot", "enabled": True},
    )
    scheduler.run_scheduled_report_now(
        request_id="scheduled-run-1",
        scheduled_report_id=schedule.scheduledReportId,
    )
    task_id = str(queued[0]["task"]["taskId"])
    task = queue.get_task_for_testing(task_id)
    assert task is not None
    intent_id = str(task.notification_intent_id)
    assert store.get_delivery_status(intent_id)["state"] == "waiting_report"

    channel._unique_account = "account-b"
    challenge = service.create_binding_code(request_id="binding-request", ttl_seconds=300)
    bound = service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-b",
        sender_id="sender-b",
    )
    result = service.notify_report_completion("r-notify", delivery_intent_id=intent_id)

    assert bound["bound"] is True
    assert result["delivery"] == "sent"
    assert channel.send_calls == 1
    assert channel.last_account_id == "account-b"
    assert channel.last_target == "sender-b"
    assert channel.last_send_guarded is True


def test_binding_code_rejects_wrong_or_expired_code(tmp_path: Path) -> None:
    clock = [datetime(2026, 8, 7, 9, 0, tzinfo=UTC)]
    service, _store = _build_durable_service(
        tmp_path,
        _ChannelBridge(connected=True, can_send_text=True),
        now_provider=lambda: clock[0],
    )
    challenge = service.create_binding_code(request_id="binding-request", ttl_seconds=60)

    wrong = service.bind_notification_recipient(
        "绑定通知 DEADBEEF",
        account_id="account-new",
        sender_id="sender-1",
    )
    clock[0] = datetime(2026, 8, 7, 9, 1, tzinfo=UTC)
    expired = service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-new",
        sender_id="sender-1",
    )

    assert wrong["bound"] is False
    assert expired == {"bound": False, "message": "绑定码错误、已过期或已使用。"}


def test_manual_retry_request_is_idempotent_across_service_restart(tmp_path: Path) -> None:
    channel = _ChannelBridge(
        connected=True,
        can_send_text=True,
        send_results=[
            {"sent": False, "resultKnown": False},
            {"sent": True, "messageId": "manual-retry"},
        ],
    )
    service, _store = _build_durable_service(tmp_path, channel)
    challenge = service.create_binding_code(request_id="binding-request")
    service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-new",
        sender_id="sender-1",
    )
    service.create_report_delivery_intent("intent-idempotent-retry")
    service.notify_report_completion("r-notify", delivery_intent_id="intent-idempotent-retry")

    first_retry = service.retry_pending(manual=True, request_id="retry-request")
    restarted_service, _ = _build_durable_service(tmp_path, channel)
    same_retry = restarted_service.retry_pending(manual=True, request_id="retry-request")

    assert same_retry == first_retry == {"attempted": 1, "sent": 1, "inProgress": False}
    assert channel.send_calls == 2


def test_ambiguous_send_becomes_unknown_and_is_not_automatically_retried(tmp_path: Path) -> None:
    channel = _ChannelBridge(
        connected=True,
        can_send_text=True,
        send_results=[{"sent": False, "resultKnown": False}],
    )
    service, store = _build_durable_service(tmp_path, channel)
    challenge = service.create_binding_code(request_id="binding-request", ttl_seconds=300)
    service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-new",
        sender_id="sender-1",
    )
    service.create_report_delivery_intent("intent-unknown")

    result = service.notify_report_completion("r-notify", delivery_intent_id="intent-unknown")
    service.retry_pending()
    restarted_channel = _ChannelBridge(connected=True, can_send_text=True)
    restarted_service, restarted_store = _build_durable_service(tmp_path, restarted_channel)
    restarted_service.retry_pending()

    assert result["delivery"] == "unknown"
    assert store.get_delivery_status("intent-unknown") == {
        "intentId": "intent-unknown",
        "reportId": "r-notify",
        "state": "unknown",
        "attemptCount": 1,
        "lastError": "send_result_unknown",
        "resultUnknown": True,
    }
    assert channel.send_calls == 1
    assert restarted_channel.send_calls == 0
    assert restarted_store.get_delivery_status("intent-unknown")["state"] == "unknown"


def test_explicit_send_failure_has_bounded_automatic_retries(tmp_path: Path) -> None:
    channel = _ChannelBridge(
        connected=True,
        can_send_text=True,
        send_results=[
            {"sent": False, "resultKnown": True},
            {"sent": False, "resultKnown": True},
            {"sent": True, "resultKnown": True},
        ],
    )
    service, store = _build_durable_service(tmp_path, channel)
    challenge = service.create_binding_code(request_id="binding-request", ttl_seconds=300)
    service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-new",
        sender_id="sender-1",
    )
    service.create_report_delivery_intent("intent-failed")

    service.notify_report_completion("r-notify", delivery_intent_id="intent-failed")
    service.retry_pending()
    service.retry_pending()
    service.notify_report_completion("r-notify", delivery_intent_id="intent-failed")

    assert store.get_delivery_status("intent-failed")["state"] == "pending"
    assert store.get_delivery_status("intent-failed")["attemptCount"] == 2
    assert channel.send_calls == 2


def test_unknown_delivery_can_only_be_retried_explicitly(tmp_path: Path) -> None:
    channel = _ChannelBridge(
        connected=True,
        can_send_text=True,
        send_results=[
            {"sent": False, "resultKnown": False},
            {"sent": True, "resultKnown": True, "messageId": "message-after-confirmation"},
        ],
    )
    service, store = _build_durable_service(tmp_path, channel)
    challenge = service.create_binding_code(request_id="binding-request", ttl_seconds=300)
    service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-new",
        sender_id="sender-1",
    )
    service.create_report_delivery_intent("intent-manual")
    service.notify_report_completion("r-notify", delivery_intent_id="intent-manual")

    public_status = service.get_delivery_status("intent-manual")
    service.retry_pending()
    retried = service.retry_delivery("intent-manual")

    assert "account-new" not in repr(public_status)
    assert "sender-1" not in repr(public_status)
    assert "资金回流" not in repr(public_status)
    assert retried["delivery"] == "sent"
    assert store.get_delivery_status("intent-manual")["state"] == "sent"
    assert channel.send_calls == 2


def test_sent_result_with_persistence_failure_remains_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    channel = _ChannelBridge(connected=True, can_send_text=True)
    service, store = _build_durable_service(tmp_path, channel)
    challenge = service.create_binding_code(request_id="binding-request", ttl_seconds=300)
    service.bind_notification_recipient(
        f"绑定通知 {challenge['code']}",
        account_id="account-new",
        sender_id="sender-1",
    )
    service.create_report_delivery_intent("intent-write-failed")
    monkeypatch.setattr(store, "mark_sent", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    result = service.notify_report_completion("r-notify", delivery_intent_id="intent-write-failed")

    assert result["delivery"] == "unknown"
    assert store.get_delivery_status("intent-write-failed")["state"] == "unknown"


def test_notify_report_completion_pushes_summary_not_full_report() -> None:
    notifications: list[tuple[str, str]] = []
    channel = _ChannelBridge(connected=True, can_send_text=True, can_send_file=True)
    service = _build_service(channel, notifications)
    result = service.notify_report_completion("r-notify", target="sender-1", account_id="account-1")
    assert result["sent"] is True
    assert "报告已完成" in channel.last_text
    assert "查看完整报告" in channel.last_text
    assert "回复“发送完整报告”" in channel.last_text
    assert "# 报告" not in channel.last_text
    assert channel.last_target == "sender-1"
    assert channel.last_account_id == "account-1"


def test_notify_report_completion_uses_the_only_wechat_target() -> None:
    notifications: list[tuple[str, str]] = []
    channel = _ChannelBridge(
        connected=True,
        can_send_text=True,
        default_target=("sender-only", "account-only"),
    )
    service = _build_service(channel, notifications)

    result = service.notify_report_completion("r-notify")

    assert result["sent"] is True
    assert result["delivery"] == "channel"
    assert channel.last_target == "sender-only"
    assert channel.last_account_id == "account-only"


def test_notify_report_completion_appends_footer_to_channel_text() -> None:
    notifications: list[tuple[str, str]] = []
    channel = _ChannelBridge(connected=True, can_send_text=True, can_send_file=True)
    service = _build_service(channel, notifications)

    result = service.notify_report_completion(
        "r-notify",
        target="sender-1",
        account_id="account-1",
        text_footer="期间扣费估算：¥0.03",
    )

    assert result["sent"] is True
    assert channel.last_text.endswith("期间扣费估算：¥0.03")
    assert result["text"] == channel.last_text


def test_notify_report_completion_does_not_generate_pdf_by_default(tmp_path) -> None:  # type: ignore[no-untyped-def]
    repo = ReportRepository()
    repo.save_succeeded_report(
        report_id="r-notify",
        instrument_code="BTC",
        market="CRYPTO",
        title="BTC 报告",
        markdown="# 报告\n## 核心理由\n- 资金回流",
        asset_dir=tmp_path / "reports" / "assets",
    )
    summary_builder = CompletionSummaryBuilder(repo)
    renderer = _PassRenderer()
    pdf_service = PdfExportService(
        repo,
        renderer=renderer,
        runtime_capabilities_provider=_ready_capabilities,
    )
    channel = _ChannelBridge(connected=True, can_send_text=True, can_send_file=True)
    service = ReportNotificationService(repo, summary_builder, pdf_service, channel)

    result = service.notify_report_completion("r-notify", target="sender-1", account_id="account-1")

    assert result["sent"] is True
    assert renderer.calls == 0
    assert not (tmp_path / "reports" / "pdf").exists()


def test_notify_report_completion_does_not_ask_for_file_when_channel_cannot_send_file() -> None:
    notifications: list[tuple[str, str]] = []
    channel = _ChannelBridge(connected=True, can_send_text=True, can_send_file=False)
    service = _build_service(channel, notifications)

    result = service.notify_report_completion("r-notify", target="sender-1", account_id="account-1")

    assert result["sent"] is True
    assert "当前微信只能发送文字通知" in channel.last_text
    assert "回复“发送完整报告”" not in channel.last_text
    assert "PDF 暂不能从该通道发送" in channel.last_text


def test_notify_report_completion_falls_back_to_in_app_when_channel_unavailable() -> None:
    notifications: list[tuple[str, str]] = []
    channel = _ChannelBridge(connected=False, can_send_text=False)
    service = _build_service(channel, notifications)
    result = service.notify_report_completion("r-notify")
    assert result["sent"] is False
    assert any("微信通知暂不可用" in item[1] for item in notifications)
