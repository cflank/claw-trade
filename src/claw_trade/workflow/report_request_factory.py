from __future__ import annotations

from datetime import date, timedelta

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.instruments.resolver import InstrumentResolveError, resolve_instrument_identity
from claw_trade.workflow.models import RunRequest, Stage, StopPoint, WorkflowEntryPoint

DEFAULT_REPORT_LOOKBACK_DAYS = 365

_CRYPTO_DISPLAY_NAMES = {
    "BTC": "Bitcoin",
    "ETH": "Ethereum",
    "SOL": "Solana",
    "DOGE": "Dogecoin",
}


def build_report_run_request(
    *,
    ticker: str,
    settings: ReportWorkflowSettings,
    company_name: str | None = None,
    market: str | None = None,
    profile: str | None = None,
    currency: str | None = None,
    currency_symbol: str | None = None,
    current_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
    data_gateway: str = "openbb",
    stop_point: StopPoint = StopPoint.NONE,
    target_worker_id: str | None = None,
    target_stage: Stage | None = None,
    entry_point: WorkflowEntryPoint = WorkflowEntryPoint.REPORT_COMMAND,
) -> RunRequest:
    identity = resolve_instrument_identity(ticker, market_hint=market)
    resolved_profile = _profile_from_args(profile, identity.profile)
    resolved_current_date, resolved_start_date, resolved_end_date = resolve_report_dates(
        current_date=current_date,
        start_date=start_date,
        end_date=end_date,
    )
    return RunRequest(
        ticker=identity.ticker,
        company_name=_company_name_or_default(company_name, identity.ticker, identity.profile),
        market=identity.market,
        profile=resolved_profile,
        currency=_value_or_default(currency, identity.currency),
        currency_symbol=_value_or_default(currency_symbol, identity.currency_symbol),
        current_date=resolved_current_date,
        start_date=resolved_start_date,
        end_date=resolved_end_date,
        data_gateway=data_gateway,
        stop_point=stop_point,
        target_worker_id=target_worker_id,
        target_stage=target_stage,
        entry_point=entry_point,
        max_debate_rounds=settings.max_debate_rounds,
        max_risk_discuss_rounds=settings.max_risk_discuss_rounds,
        frontline_execution_mode=settings.frontline_execution_mode,
    )


def resolve_report_dates(
    *,
    current_date: str | None = None,
    start_date: str | None = None,
    end_date: str | None = None,
) -> tuple[str, str, str]:
    current = _parse_date(current_date) if current_date else date.today()
    end = _parse_date(end_date) if end_date else current
    start = _parse_date(start_date) if start_date else end - timedelta(days=DEFAULT_REPORT_LOOKBACK_DAYS)
    return current.isoformat(), start.isoformat(), end.isoformat()


def report_display_name(ticker: str, profile: str) -> str:
    if profile == "CRYPTO":
        return _CRYPTO_DISPLAY_NAMES.get(ticker, ticker)
    return ticker


def _company_name_or_default(raw_value: str | None, ticker: str, profile: str) -> str:
    value = str(raw_value or "").strip()
    if value and value.upper() != ticker.upper():
        return value
    return report_display_name(ticker, profile)


def _profile_from_args(raw_profile: str | None, resolved_profile: str) -> str:
    profile = str(raw_profile or "").strip().upper()
    if not profile:
        return resolved_profile
    if profile != resolved_profile:
        raise InstrumentResolveError(f"profile {profile} does not match resolved market profile {resolved_profile}")
    return profile


def _value_or_default(raw_value: str | None, default: str) -> str:
    value = str(raw_value or "").strip()
    return value or default


def _parse_date(value: str | None) -> date:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("date value is required")
    return date.fromisoformat(raw)
