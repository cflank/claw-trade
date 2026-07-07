from __future__ import annotations

from dataclasses import dataclass, field
from os import environ
from typing import Mapping


class ReportWorkflowSettingsError(ValueError):
    pass


_PRODUCT_MAX_ROUNDS_HARD_LIMIT = 3


def _default_report_run_dir() -> str:
    return _report_run_dir(environ)


@dataclass(frozen=True)
class ReportWorkflowSettings:
    max_debate_rounds: int = 1
    max_risk_discuss_rounds: int = 1
    max_rounds_hard_limit: int = _PRODUCT_MAX_ROUNDS_HARD_LIMIT
    frontline_execution_mode: str = "parallel"
    run_dir: str = field(default_factory=_default_report_run_dir)
    default_profile: str = "CN_A"
    default_market: str = "CN_A"
    default_currency: str = "CNY"
    default_currency_symbol: str = "\u00a5"


def load_report_workflow_settings(env: Mapping[str, str] | None = None) -> ReportWorkflowSettings:
    values = environ if env is None else env
    hard_limit = _positive_int(
        values,
        "CLAW_TRADE_REPORT_MAX_ROUNDS_HARD_LIMIT",
        default=_PRODUCT_MAX_ROUNDS_HARD_LIMIT,
    )
    if hard_limit > _PRODUCT_MAX_ROUNDS_HARD_LIMIT:
        raise ReportWorkflowSettingsError(
            "CLAW_TRADE_REPORT_MAX_ROUNDS_HARD_LIMIT 不能超过产品上限 "
            f"{_PRODUCT_MAX_ROUNDS_HARD_LIMIT}"
        )
    max_debate_rounds = _bounded_positive_int(
        values,
        "CLAW_TRADE_REPORT_MAX_DEBATE_ROUNDS",
        default=1,
        hard_limit=hard_limit,
    )
    max_risk_discuss_rounds = _bounded_positive_int(
        values,
        "CLAW_TRADE_REPORT_MAX_RISK_DISCUSS_ROUNDS",
        default=1,
        hard_limit=hard_limit,
    )
    frontline_execution_mode = _plain(values, "CLAW_TRADE_REPORT_FRONTLINE_EXECUTION_MODE", default="parallel").lower()
    if frontline_execution_mode not in {"serial", "parallel"}:
        raise ReportWorkflowSettingsError(
            "CLAW_TRADE_REPORT_FRONTLINE_EXECUTION_MODE 只允许 serial 或 parallel"
        )
    run_dir = _report_run_dir(values)
    if not run_dir:
        raise ReportWorkflowSettingsError("CLAW_TRADE_REPORT_RUN_DIR 不能为空")
    return ReportWorkflowSettings(
        max_debate_rounds=max_debate_rounds,
        max_risk_discuss_rounds=max_risk_discuss_rounds,
        max_rounds_hard_limit=hard_limit,
        frontline_execution_mode=frontline_execution_mode,
        run_dir=run_dir,
        default_profile=_plain(values, "CLAW_TRADE_REPORT_DEFAULT_PROFILE", default="CN_A"),
        default_market=_plain(values, "CLAW_TRADE_REPORT_DEFAULT_MARKET", default="CN_A"),
        default_currency=_plain(values, "CLAW_TRADE_REPORT_DEFAULT_CURRENCY", default="CNY"),
        default_currency_symbol=_plain(values, "CLAW_TRADE_REPORT_DEFAULT_CURRENCY_SYMBOL", default="\u00a5"),
    )


def _plain(env: Mapping[str, str], key: str, *, default: str) -> str:
    return str(env.get(key, default)).strip()


def _report_run_dir(env: Mapping[str, str]) -> str:
    run_dir = _plain(env, "CLAW_TRADE_REPORT_RUN_DIR", default="runs")
    if _truthy(env.get("CLAW_TRADE_PRODUCTION_PACKAGE", "")) and (not run_dir or not run_dir.startswith("/")):
        shared_root = _plain(env, "CLAW_TRADE_SHARED_ROOT", default="/opt/claw-trade/shared")
        if not shared_root.startswith("/"):
            shared_root = "/opt/claw-trade/shared"
        return f"{shared_root.rstrip('/')}/runs"
    return run_dir


def _truthy(value: str) -> bool:
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _positive_int(env: Mapping[str, str], key: str, *, default: int) -> int:
    raw = str(env.get(key, str(default))).strip()
    try:
        value = int(raw)
    except ValueError as exc:
        raise ReportWorkflowSettingsError(f"{key} 必须是正整数，当前值: {raw}") from exc
    if value < 1:
        raise ReportWorkflowSettingsError(f"{key} 必须 >= 1，当前值: {value}")
    return value


def _bounded_positive_int(
    env: Mapping[str, str],
    key: str,
    *,
    default: int,
    hard_limit: int,
) -> int:
    value = _positive_int(env, key, default=default)
    if value > hard_limit:
        raise ReportWorkflowSettingsError(f"{key} 不能超过 CLAW_TRADE_REPORT_MAX_ROUNDS_HARD_LIMIT={hard_limit}")
    return value
