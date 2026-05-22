from __future__ import annotations

import pytest

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.settings_service import SettingsService, UiBoundaryError


def _settings_loader() -> ReportWorkflowSettings:
    return ReportWorkflowSettings(
        max_debate_rounds=1,
        max_risk_discuss_rounds=1,
        max_rounds_hard_limit=3,
        frontline_execution_mode="parallel",
        default_profile="CN_A",
        default_market="CN_A",
        default_currency="CNY",
        default_currency_symbol="¥",
    )


def test_current_report_workflow_settings_returns_required_snapshot_fields() -> None:
    service = SettingsService(settings_loader=_settings_loader)
    snapshot = service.current_report_workflow_settings().to_user_dict()
    assert snapshot["maxDebateRounds"] == 1
    assert snapshot["maxRiskDiscussRounds"] == 1
    assert snapshot["frontlineExecutionMode"] == "parallel"
    assert snapshot["defaultProfile"] == "CN_A"
    assert snapshot["defaultMarket"] == "CN_A"
    assert snapshot["defaultCurrency"] == "CNY"
    assert snapshot["defaultCurrencySymbol"] == "¥"


def test_confirm_report_settings_fails_over_hard_limit_and_does_not_create_task() -> None:
    service = SettingsService(settings_loader=_settings_loader)
    called = False

    def _create_task(_snapshot):
        nonlocal called
        called = True
        return {"ok": True}

    with pytest.raises(UiBoundaryError) as exc:
        service.confirm_report_settings_and_create(
            {"maxDebateRounds": 4},
            _create_task,
        )
    assert exc.value.code == "INVALID_INPUT"
    assert called is False


def test_confirm_report_settings_passes_frozen_snapshot_to_run_request_builder() -> None:
    service = SettingsService(settings_loader=_settings_loader)
    received = None

    def _create_task(snapshot):
        nonlocal received
        received = snapshot
        return {"created": True}

    out = service.confirm_report_settings_and_create(
        {"maxDebateRounds": 2, "maxRiskDiscussRounds": 3, "defaultMarket": "US"},
        _create_task,
    )
    assert out == {"created": True}
    assert received is not None
    assert received.max_debate_rounds == 2
    assert received.max_risk_discuss_rounds == 3
    assert received.default_market == "US"
