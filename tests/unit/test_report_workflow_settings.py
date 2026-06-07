from __future__ import annotations

import pytest
from claw_trade.config.report_workflow_settings import (
    ReportWorkflowSettingsError,
    load_report_workflow_settings,
)


def test_load_report_workflow_settings_reads_env_values() -> None:
    settings = load_report_workflow_settings(
        {
            "CLAW_TRADE_REPORT_MAX_ROUNDS_HARD_LIMIT": "3",
            "CLAW_TRADE_REPORT_MAX_DEBATE_ROUNDS": "2",
            "CLAW_TRADE_REPORT_MAX_RISK_DISCUSS_ROUNDS": "3",
            "CLAW_TRADE_REPORT_FRONTLINE_EXECUTION_MODE": "parallel",
            "CLAW_TRADE_REPORT_RUN_DIR": "custom-runs",
            "CLAW_TRADE_REPORT_DEFAULT_PROFILE": "CN_A",
            "CLAW_TRADE_REPORT_DEFAULT_MARKET": "CN_A",
            "CLAW_TRADE_REPORT_DEFAULT_CURRENCY": "CNY",
            "CLAW_TRADE_REPORT_DEFAULT_CURRENCY_SYMBOL": "¥",
        }
    )

    assert settings.max_rounds_hard_limit == 3
    assert settings.max_debate_rounds == 2
    assert settings.max_risk_discuss_rounds == 3
    assert settings.frontline_execution_mode == "parallel"
    assert settings.run_dir == "custom-runs"
    assert settings.default_profile == "CN_A"
    assert settings.default_currency_symbol == "¥"


def test_load_report_workflow_settings_defaults_frontline_mode_to_serial() -> None:
    settings = load_report_workflow_settings({})

    assert settings.frontline_execution_mode == "serial"


def test_load_report_workflow_settings_rejects_rounds_above_hard_limit() -> None:
    with pytest.raises(ReportWorkflowSettingsError, match="不能超过"):
        load_report_workflow_settings(
            {
                "CLAW_TRADE_REPORT_MAX_ROUNDS_HARD_LIMIT": "2",
                "CLAW_TRADE_REPORT_MAX_DEBATE_ROUNDS": "3",
            }
        )


def test_load_report_workflow_settings_rejects_hard_limit_above_product_cap() -> None:
    with pytest.raises(ReportWorkflowSettingsError, match="产品上限 3"):
        load_report_workflow_settings({"CLAW_TRADE_REPORT_MAX_ROUNDS_HARD_LIMIT": "4"})


def test_load_report_workflow_settings_rejects_invalid_frontline_mode() -> None:
    with pytest.raises(ReportWorkflowSettingsError, match="只允许 serial 或 parallel"):
        load_report_workflow_settings({"CLAW_TRADE_REPORT_FRONTLINE_EXECUTION_MODE": "burst"})
