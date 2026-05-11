from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from boundary import FUNDAMENTAL_MARKET_PROFILE, FUNDAMENTAL_WORKER_ID
from models import DataPackRequest, NormalizedInput

FND_PROFILE_WORKER_MISMATCH = "FND_PROFILE_WORKER_MISMATCH"
FND_PROFILE_MARKET_MISMATCH = "FND_PROFILE_MARKET_MISMATCH"
FND_PROFILE_INVALID_TICKER = "FND_PROFILE_INVALID_TICKER"
FND_PROFILE_INVALID_TICKER_MARKET_PREFIX = "FND_PROFILE_INVALID_TICKER_MARKET_PREFIX"
FND_PROFILE_INVALID_DATE = "FND_PROFILE_INVALID_DATE"
FND_PROFILE_DATE_RANGE_REVERSED = "FND_PROFILE_DATE_RANGE_REVERSED"


class FundamentalProfileError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class NormalizedCnATicker:
    raw_ticker: str
    symbol: str
    exchange: str
    canonical_code: str
    tushare_code: str
    akshare_symbol: str


@dataclass(frozen=True)
class DateWindow:
    start_date: str | None
    end_date: str | None
    current_date: str
    latest_report_period: str


def NormalizeInput(request: DataPackRequest) -> NormalizedInput:
    if request.worker_id != FUNDAMENTAL_WORKER_ID:
        raise FundamentalProfileError(
            FND_PROFILE_WORKER_MISMATCH,
            f"worker mismatch: expected={FUNDAMENTAL_WORKER_ID}, actual={request.worker_id}",
        )
    if request.market != FUNDAMENTAL_MARKET_PROFILE:
        raise FundamentalProfileError(
            FND_PROFILE_MARKET_MISMATCH,
            f"market mismatch: expected={FUNDAMENTAL_MARKET_PROFILE}, actual={request.market}",
        )

    ticker = NormalizeCnATicker(request.ticker)
    date_window = ResolveDateWindow(
        start_date=request.start_date,
        end_date=request.end_date,
        current_date=request.current_date,
    )
    return NormalizedInput(
        raw_ticker=ticker.raw_ticker,
        canonical_code=ticker.canonical_code,
        tushare_code=ticker.tushare_code,
        akshare_symbol=ticker.akshare_symbol,
        exchange=ticker.exchange,  # type: ignore[arg-type]
        market=FUNDAMENTAL_MARKET_PROFILE,
        start_date=date_window.start_date,
        end_date=date_window.end_date,
        current_date=date_window.current_date,
        run_id=request.run_id,
        dispatch_id=request.dispatch_id,
        latest_report_period=date_window.latest_report_period,
    )


def NormalizeCnATicker(raw_ticker: str) -> NormalizedCnATicker:
    text = raw_ticker.strip().upper()
    if text == "":
        raise FundamentalProfileError(FND_PROFILE_INVALID_TICKER, "ticker 不能为空")

    symbol: str
    exchange: str
    if "." in text:
        symbol, suffix = text.split(".", 1)
        if _is_six_digits(symbol) and suffix in {"SH", "SZ"}:
            exchange = suffix
        else:
            raise FundamentalProfileError(
                FND_PROFILE_INVALID_TICKER_MARKET_PREFIX,
                f"无法识别 ticker: {raw_ticker}",
            )
    elif len(text) == 8 and text[:2] in {"SH", "SZ"} and _is_six_digits(text[2:]):
        exchange = text[:2]
        symbol = text[2:]
    elif _is_six_digits(text):
        symbol = text
        exchange = _infer_exchange(symbol)
    else:
        raise FundamentalProfileError(
            FND_PROFILE_INVALID_TICKER_MARKET_PREFIX,
            f"无法识别 ticker: {raw_ticker}",
        )

    canonical_code = f"{symbol}.{exchange}"
    return NormalizedCnATicker(
        raw_ticker=raw_ticker,
        symbol=symbol,
        exchange=exchange,
        canonical_code=canonical_code,
        tushare_code=canonical_code,
        akshare_symbol=symbol,
    )


def ResolveDateWindow(*, start_date: str | None, end_date: str | None, current_date: str) -> DateWindow:
    current = _parse_date_like("current_date", current_date)
    resolved_start = _parse_optional_date("start_date", start_date)
    resolved_end = _parse_optional_date("end_date", end_date)

    if resolved_start is not None and resolved_end is not None and resolved_start > resolved_end:
        raise FundamentalProfileError(
            FND_PROFILE_DATE_RANGE_REVERSED,
            f"start_date({resolved_start.isoformat()}) 不能晚于 end_date({resolved_end.isoformat()})",
        )

    return DateWindow(
        start_date=resolved_start.isoformat() if resolved_start is not None else None,
        end_date=resolved_end.isoformat() if resolved_end is not None else None,
        current_date=current.isoformat(),
        latest_report_period=ResolveLatestReportPeriod(current),
    )


def ResolveLatestReportPeriod(current: date) -> str:
    quarter_candidates = (
        date(current.year, 3, 31),
        date(current.year, 6, 30),
        date(current.year, 9, 30),
        date(current.year, 12, 31),
    )
    for candidate in reversed(quarter_candidates):
        if current >= candidate:
            return candidate.strftime("%Y%m%d")
    return date(current.year - 1, 12, 31).strftime("%Y%m%d")


def _is_six_digits(text: str) -> bool:
    return len(text) == 6 and text.isdigit()


def _infer_exchange(symbol: str) -> str:
    if symbol[0] in {"5", "6", "9"}:
        return "SH"
    if symbol[0] in {"0", "1", "2", "3"}:
        return "SZ"
    raise FundamentalProfileError(
        FND_PROFILE_INVALID_TICKER_MARKET_PREFIX,
        f"无法从代码前缀识别交易所: {symbol}",
    )


def _parse_optional_date(field_name: str, raw_value: str | None) -> date | None:
    if raw_value is None:
        return None
    stripped = raw_value.strip()
    if stripped == "":
        return None
    return _parse_date_like(field_name, stripped)


def _parse_date_like(field_name: str, raw_value: str) -> date:
    stripped = raw_value.strip()
    try:
        if len(stripped) == 10:
            return date.fromisoformat(stripped)
        value = stripped
        if value.endswith("Z"):
            value = f"{value[:-1]}+00:00"
        return datetime.fromisoformat(value).date()
    except ValueError as exc:
        raise FundamentalProfileError(
            FND_PROFILE_INVALID_DATE,
            f"{field_name} 不是合法日期或时间: {raw_value}",
        ) from exc
