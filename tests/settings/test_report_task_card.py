from __future__ import annotations

from dataclasses import dataclass

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge


@dataclass
class _FakeWorkflowState:
    status: str = "frontline_running"


class _FakeWorkflowRunner:
    def __init__(self) -> None:
        self.calls = 0

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeWorkflowState:
        _ = run_id
        return _FakeWorkflowState()


class _FakeChatTransport:
    def __init__(self) -> None:
        self.calls = 0

    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        self.calls += 1
        if text.strip().lower().startswith(("/help", "help")) or text.strip().lower() == "帮助":
            raise AssertionError("help command must not call normal chat")
        _ = (context_id, request_id)
        return {"text": f"chat:{text}"}


def _controller() -> ChatController:
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeWorkflowRunner()))
    transport = _FakeChatTransport()
    return ChatController(
        openclaw_client=OpenClawGatewayClient(transport),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue),
        queue=queue,
        settings=ReportWorkflowSettings(),
        maintenance_status_provider=lambda: "维护状态摘要\n- 运行服务：健康",
    )


def test_report_command_builds_three_field_confirmation_card_for_us_name() -> None:
    controller = _controller()
    result = controller.send_chat_message(request_id="s06-card-1", context_id="ctx-1", text="/report AAPL")
    card = result["confirmationCard"]
    assert card["summaryLines"] == ["标的：AAPL", "名称：Apple Inc.", "市场：US"]
    assert card["instrumentCode"] == "AAPL"
    assert card["instrumentName"] == "Apple Inc."
    assert card["market"] == "US"
    card_text = "\n".join(card["summaryLines"])
    for forbidden in ("报告方案", "计价单位", "profile", "默认币种", "worker", "debate", "risk"):
        assert forbidden not in card_text


def test_non_report_chat_does_not_enter_report_workflow() -> None:
    controller = _controller()
    result = controller.send_chat_message(request_id="s06-chat-1", context_id="ctx-1", text="帮我做一份 BTC 报告")
    assert "confirmationCard" not in result
    assert result["assistantReply"] == "chat:帮我做一份 BTC 报告"
    assert result["context"]["kind"] == "normal_chat"


def test_bare_report_command_returns_help_instead_of_falling_through_to_normal_chat() -> None:
    controller = _controller()
    result = controller.send_chat_message(request_id="s06-help-1", context_id="ctx-1", text="/report")
    assert "assistantReply" not in result
    assert result["messages"][-1]["actor"] == "system"
    assert result["messages"][-1]["text"] == "请输入完整的 /report 指令，例如：/report TSLA。"


def test_help_command_returns_command_usage_without_normal_chat() -> None:
    controller = _controller()
    replies = []
    for index, text in enumerate(("/help", "help", "帮助", "help $superpowers:using-superpowers"), start=1):
        result = controller.send_chat_message(
            request_id=f"s06-help-command-{index}",
            context_id=f"ctx-help-{index}",
            text=text,
        )
        replies.append(result["messages"][-1]["text"])
        assert "assistantReply" not in result
        assert result["messages"][-1]["actor"] == "system"
        assert "/report <标的>" in result["messages"][-1]["text"]
        assert "/sched <标的> 每天 HH:MM" in result["messages"][-1]["text"]
        assert "/alert <标的> 高于/低于 <价格>" in result["messages"][-1]["text"]
        assert "/select [市场] [refresh|刷新] [YYYY-MM-DD]" in result["messages"][-1]["text"]
        assert "带 refresh/刷新 时强制刷新数据" in result["messages"][-1]["text"]
        assert "市场放前面，刷新放后面" in result["messages"][-1]["text"]
        assert "1 / cn_a / A股" in result["messages"][-1]["text"]
        assert "2 / crypto / 加密" in result["messages"][-1]["text"]
        assert "/select 2\n  /select 刷新\n  /select 2 刷新" in result["messages"][-1]["text"]
        assert "$superpowers:using-superpowers" not in result["messages"][-1]["text"]
        assert "/maint" in result["messages"][-1]["text"]
        assert max(len(line) for line in result["messages"][-1]["text"].splitlines()) <= 42
    assert replies[0] == replies[1] == replies[2] == replies[3]


def test_sched_alias_builds_scheduled_report_confirmation_card() -> None:
    controller = _controller()
    result = controller.send_chat_message(request_id="s06-sched-1", context_id="ctx-1", text="/sched AAPL 每天 08:00")
    card = result["confirmationCard"]
    assert card["title"] == "请确认是否创建定时报告"
    assert card["summaryLines"] == ["标的：AAPL", "名称：Apple Inc.", "市场：US"]


def test_alert_alias_builds_price_alert_confirmation_card() -> None:
    controller = _controller()
    result = controller.send_chat_message(
        request_id="s06-alert-1",
        context_id="ctx-1",
        text="/alert BTC 高于 70000",
    )
    card = result["confirmationCard"]
    assert card["title"] == "请确认是否创建价格提醒"
    assert card["summaryLines"] == ["标的：BTC", "名称：Bitcoin", "市场：CRYPTO"]


def test_maint_command_returns_maintenance_summary() -> None:
    controller = _controller()
    result = controller.send_chat_message(request_id="s06-maint-1", context_id="ctx-1", text="/maint")
    assert "assistantReply" not in result
    assert result["messages"][-1]["actor"] == "system"
    assert "维护状态摘要" in result["messages"][-1]["text"]


def test_confirm_with_symbol_override_reidentifies_name_and_market() -> None:
    controller = _controller()
    send = controller.send_chat_message(request_id="s06-card-2", context_id="ctx-1", text="/report BTC")
    card = send["confirmationCard"]
    confirmed = controller.confirm_intent_draft(
        request_id="s06-confirm-1",
        draft_id=card["draftId"],
        decision="confirm",
        overrides={"instrumentCode": "AAPL"},
    )
    task = confirmed["task"]
    assert task["instrumentCode"] == "AAPL"
    assert task["market"] == "US"
    assert task["companyName"] == "Apple Inc."


def test_confirm_rejects_symbol_market_mismatch() -> None:
    controller = _controller()
    send = controller.send_chat_message(request_id="s06-card-3", context_id="ctx-1", text="/report AAPL")
    card = send["confirmationCard"]
    rejected = controller.confirm_intent_draft(
        request_id="s06-confirm-2",
        draft_id=card["draftId"],
        decision="confirm",
        overrides={"market": "CRYPTO"},
    )
    assert rejected["error"]["code"] == "INVALID_INPUT"
    assert "不匹配" in rejected["error"]["message"]


def test_report_command_builds_confirmation_card_for_cn_a_name() -> None:
    controller = _controller()
    result = controller.send_chat_message(request_id="s06-cn-a", context_id="ctx-cn-a", text="/report 600519.SH")
    card = result["confirmationCard"]
    assert card["summaryLines"] == ["标的：600519.SH", "名称：贵州茅台", "市场：CN_A"]


def test_report_command_builds_confirmation_card_for_crypto_pair_name() -> None:
    controller = _controller()
    result = controller.send_chat_message(request_id="s06-crypto", context_id="ctx-crypto", text="/report BTC/USDT")
    card = result["confirmationCard"]
    assert card["summaryLines"] == ["标的：BTC/USDT", "名称：Bitcoin", "市场：CRYPTO"]


def test_report_command_builds_confirmation_card_for_unknown_crypto_pair_base() -> None:
    controller = _controller()
    result = controller.send_chat_message(request_id="s06-crypto-wif", context_id="ctx-crypto-wif", text="/report WIF/USDT")
    card = result["confirmationCard"]
    assert card["summaryLines"] == ["标的：WIF/USDT", "名称：WIF", "市场：CRYPTO"]


def test_report_command_builds_confirmation_card_for_crypto_base_symbol_name() -> None:
    controller = _controller()
    result = controller.send_chat_message(request_id="s06-crypto-xrp", context_id="ctx-crypto-xrp", text="/report XRP")
    card = result["confirmationCard"]
    assert card["summaryLines"] == ["标的：XRP", "名称：XRP", "市场：CRYPTO"]


def test_report_command_builds_confirmation_card_for_hk_name() -> None:
    controller = _controller()
    result = controller.send_chat_message(request_id="s06-hk", context_id="ctx-hk", text="/report 00700.HK")
    card = result["confirmationCard"]
    assert card["summaryLines"] == ["标的：00700.HK", "名称：腾讯控股", "市场：HK"]
