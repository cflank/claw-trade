from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from os import environ
from typing import Mapping


class SelectionSchedulerSettingsError(ValueError):
    pass


@dataclass(frozen=True)
class SelectionSchedulerSettings:
    schedule_time_local: time = time(hour=16, minute=0)
    timezone_name: str = "Asia/Shanghai"


def load_selection_scheduler_settings(env: Mapping[str, str] | None = None) -> SelectionSchedulerSettings:
    values = environ if env is None else env
    return SelectionSchedulerSettings(
        schedule_time_local=_parse_hhmm(values, "CLAW_TRADE_SELECTION_SCHEDULE_TIME_BJT", default="16:00"),
        timezone_name="Asia/Shanghai",
    )


def _parse_hhmm(env: Mapping[str, str], key: str, *, default: str) -> time:
    raw = str(env.get(key, default)).strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise SelectionSchedulerSettingsError(f"{key} 必须是 HH:MM，当前值: {raw}")
    try:
        hour = int(parts[0])
        minute = int(parts[1])
    except ValueError as exc:
        raise SelectionSchedulerSettingsError(f"{key} 必须是 HH:MM，当前值: {raw}") from exc
    if hour < 0 or hour > 23 or minute < 0 or minute > 59:
        raise SelectionSchedulerSettingsError(f"{key} 超出时间范围，当前值: {raw}")
    return time(hour=hour, minute=minute)
