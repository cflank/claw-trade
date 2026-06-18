from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from claw_trade.data_gateway._selection_batch import (
    build_selection_data_need_audit,
    fetch_selection_batch_from_data_gateway,
    resolve_crypto_selection_trade_date_for_scheduler,
)
from claw_trade.data_gateway.warehouse.trading_calendar import is_expected_daily_date
from claw_trade.selection.strategy_config import (
    load_selection_strategy as _load_selection_strategy,
    load_selection_strategy_config_ref as _load_selection_strategy_config_ref,
)

_CN_A_CALENDAR = "CN_A_SSE_SZSE"


def resolve_cn_a_selection_closed_trade_date(now: datetime) -> str:
    value = now if now.tzinfo is not None else now.replace(tzinfo=UTC)
    shanghai_now = value.astimezone(ZoneInfo("Asia/Shanghai"))
    close_cutoff = shanghai_now.replace(hour=16, minute=0, second=0, microsecond=0)
    if shanghai_now >= close_cutoff:
        candidate = shanghai_now.date()
    else:
        candidate = shanghai_now.date() - timedelta(days=1)
    return previous_cn_a_selection_trade_date(candidate).isoformat()


def resolve_cn_a_selection_trade_date_for_scheduler(trade_date: str | None, *, now: datetime | None = None) -> str:
    if trade_date is not None and trade_date.strip():
        return previous_cn_a_selection_trade_date(date.fromisoformat(trade_date.strip())).isoformat()
    return resolve_cn_a_selection_closed_trade_date(now or datetime.now(tz=UTC))


def previous_cn_a_selection_trade_date(candidate: date) -> date:
    current = candidate
    for _ in range(370):
        if is_expected_daily_date(current, _CN_A_CALENDAR):
            return current
        current = current - timedelta(days=1)
    raise ValueError(f"cannot resolve CN_A trading day from {candidate.isoformat()}")


def load_cn_a_selection_v1_strategy(config_ref: str):
    """Backward-compatible API kept for legacy call sites."""
    return _load_selection_strategy(config_ref)


def load_selection_strategy(config_ref: str):
    """General strategy loader for current multi-market callers."""
    return _load_selection_strategy(config_ref)


def load_cn_a_selection_v1_strategy_config_ref(
    market: object,
    profile: object,
) -> str | None:
    """Backward-compatible API kept for legacy call sites."""
    return _load_selection_strategy_config_ref(market, profile)


def load_selection_strategy_config_ref(market: object, profile: object) -> str | None:
    """General strategy config resolver for all supported markets."""
    return _load_selection_strategy_config_ref(market, profile)

__all__ = [
    "build_selection_data_need_audit",
    "fetch_selection_batch_from_data_gateway",
    "load_cn_a_selection_v1_strategy",
    "load_cn_a_selection_v1_strategy_config_ref",
    "load_selection_strategy",
    "load_selection_strategy_config_ref",
    "previous_cn_a_selection_trade_date",
    "resolve_cn_a_selection_closed_trade_date",
    "resolve_cn_a_selection_trade_date_for_scheduler",
    "resolve_crypto_selection_trade_date_for_scheduler",
]
