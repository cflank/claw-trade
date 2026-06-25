from __future__ import annotations

from dataclasses import dataclass, replace

import pytest
from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.price_alert_service import UiServiceError
from claw_trade.ui_backend.report_queue import QueueError, ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge
from claw_trade.ui_contracts.enums import MarketProfile
from claw_trade.ui_contracts.user_dto import PriceAlertForUser, ScheduledReportForUser


@dataclass
class _FakeState:
    status: str = "frontline_running"


class _FakeRunner:
    def __init__(self) -> None:
        self.calls = 0
        self.cancelled_runs: list[str] = []

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeState:
        _ = run_id
        return _FakeState()

    def cancel_run(self, run_id: str) -> bool:
        self.cancelled_runs.append(run_id)
        return True


def _build_controller() -> tuple[ConfirmationController, ReportTaskQueue, _FakeRunner, IntentRecognizer]:
    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    controller = ConfirmationController(queue)
    return controller, queue, runner, IntentRecognizer()


def test_confirmation_required_before_report_workflow_creation() -> None:
    controller, queue, runner, recognizer = _build_controller()
    draft = recognizer.classify_user_intent(
        text="/report BTC",
        source_message_id="m-1",
        settings=ReportWorkflowSettings(),
    )
    assert draft is not None
    controller.register_draft(draft)
    controller.build_confirmation_card(draft)
    assert runner.calls == 0

    result = controller.confirm_intent_draft(request_id="c-1", draft_id=draft.draft_id, decision="confirm")
    assert result["status"] == "confirmed"
    assert runner.calls == 1
    assert "runId" not in result["task"]
    assert "dedupeKey" not in result["task"]
    queued = queue.get_task_for_testing(result["task"]["taskId"])
    assert queued is not None
    assert queued.company_name == "Bitcoin"
    assert queued.currency_symbol == "USDT"
    assert queued.workflow_settings["defaultProfile"] == "CRYPTO"
    assert queued.workflow_settings["defaultCurrency"] == "USDT"
    assert queued.workflow_settings["defaultCurrencySymbol"] == "USDT"


def test_confirmation_card_uses_id_field_contract() -> None:
    controller, _queue, _runner, recognizer = _build_controller()
    draft = recognizer.classify_user_intent(
        text="/report BTC",
        source_message_id="m-card-1",
        settings=ReportWorkflowSettings(),
    )
    assert draft is not None
    card = controller.build_confirmation_card(draft)
    assert card["id"] == f"card-{draft.draft_id}"
    assert card["draftId"] == draft.draft_id
    assert "cardId" not in card


def test_confirmation_card_and_task_use_data_layer_company_name() -> None:
    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))

    def _resolver(*, market: str, symbol_ids: tuple[str, ...]):
        assert market == "CN_A"
        assert symbol_ids == ("688017.SH",)
        return {"688017.SH": "绿的谐波"}

    controller = ConfirmationController(queue, company_name_resolver=_resolver)
    recognizer = IntentRecognizer()
    draft = recognizer.classify_user_intent(
        text="/report 688017.SH",
        source_message_id="m-name",
        settings=ReportWorkflowSettings(),
    )
    assert draft is not None
    controller.register_draft(draft)

    card = controller.build_confirmation_card(draft)
    result = controller.confirm_intent_draft(request_id="c-name", draft_id=draft.draft_id, decision="confirm")
    queued = queue.get_task_for_testing(result["task"]["taskId"])

    assert card["instrumentName"] == "绿的谐波"
    assert "名称：绿的谐波" in card["summaryLines"]
    assert queued is not None
    assert queued.company_name == "绿的谐波"
    assert queued.instrument_name == "绿的谐波"


def test_confirmation_card_fails_when_name_lookup_has_no_name() -> None:
    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))

    def _resolver(*, market: str, symbol_ids: tuple[str, ...]):
        assert market == "CN_A"
        assert symbol_ids == ("688017.SH",)
        return {}

    controller = ConfirmationController(queue, company_name_resolver=_resolver)
    recognizer = IntentRecognizer()
    draft = recognizer.classify_user_intent(
        text="/report 688017.SH",
        source_message_id="m-name-missing",
        settings=ReportWorkflowSettings(),
    )
    assert draft is not None
    controller.register_draft(draft)

    with pytest.raises(QueueError) as exc:
        controller.build_confirmation_card(draft)

    assert exc.value.code == "INVALID_INPUT"
    assert "名称解析失败" in exc.value.user_message
    assert runner.calls == 0


def test_confirm_scheduled_and_price_alert_return_for_user_dto() -> None:
    controller, _queue, _runner, recognizer = _build_controller()

    schedule_draft = recognizer.classify_user_intent(
        text="每天 08:00 给我 BTC 报告",
        source_message_id="m-2",
        settings=ReportWorkflowSettings(),
    )
    assert schedule_draft is not None
    controller.register_draft(schedule_draft)
    schedule_result = controller.confirm_intent_draft(
        request_id="c-2",
        draft_id=schedule_draft.draft_id,
        decision="confirm",
    )
    assert schedule_result["status"] == "confirmed"
    assert isinstance(schedule_result["scheduledReport"], ScheduledReportForUser)

    alert_draft = recognizer.classify_user_intent(
        text="BTC 高于 70000 提醒我",
        source_message_id="m-3",
        settings=ReportWorkflowSettings(),
    )
    assert alert_draft is not None
    controller.register_draft(alert_draft)
    alert_result = controller.confirm_intent_draft(
        request_id="c-3",
        draft_id=alert_draft.draft_id,
        decision="confirm",
    )
    assert alert_result["status"] == "confirmed"
    assert isinstance(alert_result["priceAlert"], PriceAlertForUser)


def test_confirm_price_alert_from_wechat_context_targets_wechat_sender() -> None:
    controller, _queue, _runner, recognizer = _build_controller()
    draft = recognizer.classify_user_intent(
        text="BTC 低于 61250 提醒我",
        source_message_id="m-wechat-alert",
        settings=ReportWorkflowSettings(),
    )
    assert draft is not None
    controller.register_draft(draft)

    result = controller.confirm_intent_draft(
        request_id="c-wechat-alert",
        draft_id=draft.draft_id,
        decision="confirm",
        origin_context_id="wechat_clawbot:account-1:sender-1",
    )

    assert result["priceAlert"].notification.channel == "wechat_clawbot"
    stored = controller._price_alert_service._store.get_price_alert(result["priceAlert"].priceAlertId)  # noqa: SLF001
    assert stored is not None
    assert stored.notification == {
        "channel": "wechat_clawbot",
        "enabled": True,
        "target": "sender-1",
        "accountId": "account-1",
    }


def test_confirm_price_alert_uses_default_wechat_target_when_ui_chat_has_one() -> None:
    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    controller = ConfirmationController(
        queue,
        default_price_alert_notification=lambda: {
            "channel": "wechat_clawbot",
            "enabled": True,
            "target": "sender-default",
            "accountId": "account-default",
        },
    )
    recognizer = IntentRecognizer()
    draft = recognizer.classify_user_intent(
        text="BTC 高于 70000 提醒我",
        source_message_id="m-ui-alert",
        settings=ReportWorkflowSettings(),
    )
    assert draft is not None
    controller.register_draft(draft)

    result = controller.confirm_intent_draft(
        request_id="c-ui-alert",
        draft_id=draft.draft_id,
        decision="confirm",
    )

    stored = controller._price_alert_service._store.get_price_alert(result["priceAlert"].priceAlertId)  # noqa: SLF001
    assert stored is not None
    assert stored.notification == {
        "channel": "wechat_clawbot",
        "enabled": True,
        "target": "sender-default",
        "accountId": "account-default",
    }


def test_default_price_alert_provider_fails_instead_of_returning_zero_quote() -> None:
    controller, _queue, _runner, recognizer = _build_controller()
    draft = recognizer.classify_user_intent(
        text="BTC 高于 70000 提醒我",
        source_message_id="m-default-alert-provider",
        settings=ReportWorkflowSettings(),
    )
    assert draft is not None
    controller.register_draft(draft)
    result = controller.confirm_intent_draft(
        request_id="c-default-alert-provider",
        draft_id=draft.draft_id,
        decision="confirm",
    )

    with pytest.raises(UiServiceError, match="不能用默认价格完成检查"):
        controller._price_alert_service.evaluate_price_alert(
            price_alert_id=result["priceAlert"].priceAlertId,
            request_id="check-default-alert-provider",
        )


def test_confirm_request_id_is_idempotent() -> None:
    controller, _queue, runner, recognizer = _build_controller()
    draft = recognizer.classify_user_intent(
        text="/report TSLA",
        source_message_id="m-4",
        settings=ReportWorkflowSettings(),
    )
    assert draft is not None
    controller.register_draft(draft)

    first = controller.confirm_intent_draft(request_id="same-1", draft_id=draft.draft_id, decision="confirm")
    second = controller.confirm_intent_draft(request_id="same-1", draft_id=draft.draft_id, decision="confirm")
    assert first == second
    assert runner.calls == 1


def test_confirmation_override_resolves_code_with_selected_market_first() -> None:
    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    controller = ConfirmationController(queue, approved_profiles={"CN_A", "US", "HK", "CRYPTO"})
    recognizer = IntentRecognizer()
    draft = recognizer.classify_user_intent(
        text="/report AAPL",
        source_message_id="m-override-market",
        settings=ReportWorkflowSettings(),
    )
    assert draft is not None
    controller.register_draft(draft)

    result = controller.confirm_intent_draft(
        request_id="override-market",
        draft_id=draft.draft_id,
        decision="confirm",
        overrides={"instrumentCode": "AR", "market": "CRYPTO"},
    )

    assert result["status"] == "confirmed"
    queued = queue.get_task_for_testing(result["task"]["taskId"])
    assert queued is not None
    assert queued.instrument_code == "AR/USDT"
    assert queued.market == "CRYPTO"


def test_confirmation_override_rejects_numeric_code_for_us_market() -> None:
    controller, queue, runner, recognizer = _build_controller()
    draft = recognizer.classify_user_intent(
        text="/report AAPL",
        source_message_id="m-override-us",
        settings=ReportWorkflowSettings(),
    )
    assert draft is not None
    controller.register_draft(draft)

    with pytest.raises(QueueError) as exc:
        controller.confirm_intent_draft(
            request_id="override-us",
            draft_id=draft.draft_id,
            decision="confirm",
            overrides={"instrumentCode": "700", "market": "US"},
        )

    assert exc.value.code == "INVALID_INPUT"
    assert runner.calls == 0
    assert queue.get_report_queue_snapshot_for_user()["queuedCount"] == 0


@pytest.mark.parametrize("profile", ["HK", "CRYPTO"])
def test_unapproved_profile_strategy_blocks_report_confirmation(profile: str) -> None:
    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    controller = ConfirmationController(queue, approved_profiles={"CN_A", "US"})
    recognizer = IntentRecognizer()
    settings = ReportWorkflowSettings(default_profile=profile, default_market=profile)
    draft = recognizer.classify_user_intent(
        text="/report BTC" if profile == "CRYPTO" else "/report AAPL",
        source_message_id="m-profile-1",
        settings=settings,
    )
    assert draft is not None
    draft = replace(
        draft,
        market=MarketProfile(profile),
        workflow_settings=replace(draft.workflow_settings, defaultProfile=profile, defaultMarket=profile),
    )
    controller.register_draft(draft)

    with pytest.raises(QueueError) as exc:
        controller.confirm_intent_draft(request_id=f"profile-{profile}", draft_id=draft.draft_id, decision="confirm")
    assert exc.value.code == "PROFILE_STRATEGY_UNAPPROVED"
    if profile == "HK":
        assert exc.value.user_message == "港股报告暂未启用，请先配置 HK 报告策略。"
    assert runner.calls == 0
    snapshot = queue.get_report_queue_snapshot_for_user()
    assert snapshot["runningTask"] is None
    assert snapshot["queuedCount"] == 0
    assert snapshot["queuedTasks"] == []


def test_running_task_cancel_stops_workflow_and_marks_task_cancelled() -> None:
    runner = _FakeRunner()
    queue = ReportTaskQueue(ReportWorkflowBridge(runner))
    controller = ConfirmationController(queue)
    recognizer = IntentRecognizer()
    draft = recognizer.classify_user_intent(
        text="/report AAPL",
        source_message_id="m-5",
        settings=ReportWorkflowSettings(),
    )
    assert draft is not None
    controller.register_draft(draft)
    confirmed = controller.confirm_intent_draft(request_id="c-4", draft_id=draft.draft_id, decision="confirm")
    task_id = confirmed["task"]["taskId"]

    cancelled = queue.cancel_report_task(request_id="cancel-1", task_id=task_id)

    assert cancelled["task"]["status"] == "cancelled"
    assert runner.cancelled_runs == ["run-1"]
    task = queue.get_task_for_testing(task_id)
    assert task is not None
    assert task.status.value == "cancelled"
