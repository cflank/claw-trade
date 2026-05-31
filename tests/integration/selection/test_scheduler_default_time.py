from __future__ import annotations

from datetime import datetime

import pytest
from claw_trade.config.selection_scheduler_settings import (
    SelectionSchedulerSettingsError,
    load_selection_scheduler_settings,
)
from claw_trade.selection.scheduler import resolve_default_schedule_time


@pytest.mark.integration
def test_selection_scheduler_defaults_to_beijing_1600() -> None:
    settings = load_selection_scheduler_settings(env={})

    assert settings.timezone_name == "Asia/Shanghai"
    assert settings.schedule_time_local.hour == 16
    assert settings.schedule_time_local.minute == 0

    trigger = resolve_default_schedule_time("2026-05-26", settings)
    assert trigger.isoformat() == "2026-05-26T16:00:00+08:00"


@pytest.mark.integration
def test_selection_scheduler_time_can_be_overridden() -> None:
    settings = load_selection_scheduler_settings(env={"CLAW_TRADE_SELECTION_SCHEDULE_TIME_BJT": "17:20"})

    trigger = resolve_default_schedule_time("2026-05-26", settings)
    assert trigger.isoformat() == "2026-05-26T17:20:00+08:00"


@pytest.mark.integration
def test_selection_scheduler_rejects_invalid_time_format() -> None:
    with pytest.raises(SelectionSchedulerSettingsError, match="HH:MM"):
        load_selection_scheduler_settings(env={"CLAW_TRADE_SELECTION_SCHEDULE_TIME_BJT": "1700"})
