from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Mapping

from .models import MarketToolInput, ProviderQuery
from .provider_plan import build_provider_query
from .security import normalize_ticker, validate_date, validate_date_range, validate_market


DEFAULT_MARKET_WINDOW_DAYS = 60
MIN_MARKET_TECHNICAL_WINDOW_DAYS = 120
DEFAULT_MARKET_ADJUST = "qfq"


@dataclass(frozen=True)
class NormalizedMarketInput:
    ticker: str
    market: str
    company_name: str | None
    start_date: str
    end_date: str
    adjust: str


def normalize_market_input(
    raw_input: MarketToolInput | Mapping[str, Any],
    *,
    today: date | None = None,
    window_days: int = DEFAULT_MARKET_WINDOW_DAYS,
    adjust: str = DEFAULT_MARKET_ADJUST,
) -> NormalizedMarketInput:
    payload = _coerce_market_input(raw_input)
    ticker = normalize_ticker(payload["ticker"])
    market = validate_market(payload.get("market", "CN_A"))
    company_name = _to_optional_string(payload.get("company_name"))

    if window_days < 1:
        raise ValueError("window_days must be >= 1")

    normalized_adjust = (adjust or DEFAULT_MARKET_ADJUST).strip().lower()
    if normalized_adjust != DEFAULT_MARKET_ADJUST:
        raise ValueError("market adjust must be qfq")

    end_date_raw = _to_optional_string(payload.get("end_date"))
    start_date_raw = _to_optional_string(payload.get("start_date"))

    if end_date_raw is None:
        end_date = _resolve_today(today).isoformat()
    else:
        end_date = validate_date(end_date_raw, field_name="end_date")

    end_dt = datetime.strptime(end_date, "%Y-%m-%d").date()
    effective_window_days = max(window_days, MIN_MARKET_TECHNICAL_WINDOW_DAYS)
    technical_window_start = (end_dt - timedelta(days=effective_window_days)).isoformat()

    if start_date_raw is None:
        start_date = technical_window_start
    else:
        requested_start_date = validate_date(start_date_raw, field_name="start_date")
        start_date = min(requested_start_date, technical_window_start)

    start_date, end_date = validate_date_range(start_date, end_date)
    return NormalizedMarketInput(
        ticker=ticker,
        market=market,
        company_name=company_name,
        start_date=start_date,
        end_date=end_date,
        adjust=DEFAULT_MARKET_ADJUST,
    )


def build_market_provider_query(
    raw_input: NormalizedMarketInput | MarketToolInput | Mapping[str, Any],
    *,
    today: date | None = None,
    window_days: int = DEFAULT_MARKET_WINDOW_DAYS,
) -> ProviderQuery:
    normalized = (
        raw_input
        if isinstance(raw_input, NormalizedMarketInput)
        else normalize_market_input(
            raw_input,
            today=today,
            window_days=window_days,
            adjust=DEFAULT_MARKET_ADJUST,
        )
    )
    return build_provider_query(
        market=normalized.market,
        ticker=normalized.ticker,
        company_name=normalized.company_name,
        industry=None,
        start_date=normalized.start_date,
        end_date=normalized.end_date,
        adjust=normalized.adjust,
    )


def BuildMarketProviderQuery(
    raw_input: NormalizedMarketInput | MarketToolInput | Mapping[str, Any],
    *,
    today: date | None = None,
    window_days: int = DEFAULT_MARKET_WINDOW_DAYS,
) -> ProviderQuery:
    return build_market_provider_query(raw_input, today=today, window_days=window_days)


def _coerce_market_input(raw_input: MarketToolInput | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(raw_input, MarketToolInput):
        return {
            "ticker": raw_input.ticker,
            "market": raw_input.market,
            "company_name": raw_input.company_name,
            "start_date": raw_input.start_date,
            "end_date": raw_input.end_date,
        }
    if not isinstance(raw_input, Mapping):
        raise TypeError("market input must be MarketToolInput or mapping")
    if "ticker" not in raw_input:
        raise ValueError("ticker is required")
    return dict(raw_input)


def _to_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        return text or None
    return str(value)


def _resolve_today(today: date | None) -> date:
    if today is not None:
        return today
    return date.today()


__all__ = [
    "BuildMarketProviderQuery",
    "DEFAULT_MARKET_ADJUST",
    "DEFAULT_MARKET_WINDOW_DAYS",
    "MIN_MARKET_TECHNICAL_WINDOW_DAYS",
    "NormalizedMarketInput",
    "build_market_provider_query",
    "normalize_market_input",
    "normalize_ticker",
]
