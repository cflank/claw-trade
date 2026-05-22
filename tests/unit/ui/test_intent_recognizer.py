from __future__ import annotations

import pytest

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_contracts.scope_guard import FirstVersionScopeError


def _settings() -> ReportWorkflowSettings:
    return ReportWorkflowSettings()


def test_report_intent_creates_confirmation_draft() -> None:
    recognizer = IntentRecognizer()
    draft = recognizer.classify_user_intent(text="/report BTC", source_message_id="m-1", settings=_settings())
    assert draft is not None
    assert draft.kind.value == "report"
    assert draft.instrument_code == "BTC"


def test_daily_schedule_intent_supported() -> None:
    recognizer = IntentRecognizer()
    draft = recognizer.classify_user_intent(
        text="每天 08:00 给我 BTC 报告",
        source_message_id="m-2",
        settings=_settings(),
    )
    assert draft is not None
    assert draft.kind.value == "scheduled_report"
    assert draft.schedule is not None
    assert draft.schedule["frequency"] == "daily"


def test_hourly_schedule_rejected() -> None:
    recognizer = IntentRecognizer()
    with pytest.raises(FirstVersionScopeError) as exc:
        recognizer.classify_user_intent(
            text="每小时给我 BTC 报告",
            source_message_id="m-3",
            settings=_settings(),
        )
    assert exc.value.item_code == "hourly_full_report_schedule"


def test_price_alert_intent_supported() -> None:
    recognizer = IntentRecognizer()
    draft = recognizer.classify_user_intent(
        text="BTC 高于 70000 提醒我",
        source_message_id="m-4",
        settings=_settings(),
    )
    assert draft is not None
    assert draft.kind.value == "price_alert"
    assert draft.price_condition is not None
    assert draft.price_condition["operator"] == "above"


def test_normal_chat_returns_none() -> None:
    recognizer = IntentRecognizer()
    draft = recognizer.classify_user_intent(text="今天天气怎么样", source_message_id="m-5", settings=_settings())
    assert draft is None
