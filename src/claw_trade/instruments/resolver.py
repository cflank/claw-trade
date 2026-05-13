from __future__ import annotations

from dataclasses import dataclass
import re


_CN_A_HINTS = {"CN_A", "SH", "SZ", "BJ"}
_CRYPTO_HINTS = {"CRYPTO", "BINANCE", "OKX", "COINBASE"}
_EXPLICIT_MARKETS = _CN_A_HINTS | {"HK", "US"} | _CRYPTO_HINTS
_CRYPTO_BASE_SYMBOLS = {
    "BTC",
    "ETH",
    "SOL",
    "XRP",
    "DOGE",
    "BNB",
    "ADA",
    "AVAX",
    "DOT",
    "LTC",
    "TRX",
    "MATIC",
    "LINK",
}
_IDENTITY_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class InstrumentResolveError(ValueError):
    pass


@dataclass(frozen=True)
class InstrumentIdentity:
    raw_ticker: str
    ticker: str
    market: str
    profile: str
    currency: str
    currency_symbol: str
    input_market: str | None = None


def resolve_instrument_identity(ticker: str, *, market_hint: str | None = None) -> InstrumentIdentity:
    raw_ticker = str(ticker or "").strip()
    if not raw_ticker:
        raise InstrumentResolveError("ticker is required")
    if any(char.isspace() for char in raw_ticker) or not _IDENTITY_TOKEN_RE.fullmatch(raw_ticker):
        raise InstrumentResolveError("ticker must be one token containing only letters, numbers, dot, dash, or underscore")

    normalized_ticker = _normalize_ticker(raw_ticker)
    normalized_hint = str(market_hint or "").strip().upper() or None
    if normalized_hint is not None and normalized_hint not in _EXPLICIT_MARKETS:
        raise InstrumentResolveError(f"unknown market: {normalized_hint}")

    input_code, suffix = _split_explicit_suffix(normalized_ticker)
    explicit_market = normalized_hint or suffix
    if explicit_market:
        profile = _profile_from_market(explicit_market)
    else:
        profile = _infer_profile_from_code(input_code)

    currency, currency_symbol = _currency_for_profile(profile)
    return InstrumentIdentity(
        raw_ticker=raw_ticker,
        ticker=normalized_ticker,
        market=profile,
        profile=profile,
        currency=currency,
        currency_symbol=currency_symbol,
        input_market=explicit_market,
    )


def _normalize_ticker(value: str) -> str:
    return value.upper() if any(char.isalpha() for char in value) else value


def _split_explicit_suffix(value: str) -> tuple[str, str | None]:
    if "." not in value:
        return value, None
    code, suffix = value.rsplit(".", 1)
    if not code or not suffix:
        raise InstrumentResolveError("ticker must be CODE or CODE.MARKET")
    normalized_suffix = suffix.upper()
    if normalized_suffix not in _EXPLICIT_MARKETS:
        return value, None
    return code, normalized_suffix


def _profile_from_market(market: str) -> str:
    if market in _CN_A_HINTS:
        return "CN_A"
    if market == "HK":
        return "HK"
    if market == "US":
        return "US"
    if market in _CRYPTO_HINTS:
        return "CRYPTO"
    raise InstrumentResolveError(f"unknown market: {market}")


def _infer_profile_from_code(code: str) -> str:
    if code.isdigit():
        if len(code) == 6:
            return "CN_A"
        if len(code) in {4, 5}:
            return "HK"

    compact = code.replace("-", "").replace("_", "")
    if code in _CRYPTO_BASE_SYMBOLS:
        return "CRYPTO"
    if code.endswith(("USDT", "USDC", "USD", "BTC", "ETH")) and len(code) >= 5:
        return "CRYPTO"
    if compact.isalnum() and any(char.isalpha() for char in compact) and any(char.isdigit() for char in compact):
        return "CRYPTO"
    if code.isalpha() and 1 <= len(code) <= 5:
        return "US"
    return "US"


def _currency_for_profile(profile: str) -> tuple[str, str]:
    if profile == "CN_A":
        return "CNY", "\u00a5"
    if profile == "HK":
        return "HKD", "HK$"
    if profile == "US":
        return "USD", "$"
    if profile == "CRYPTO":
        return "USDT", "USDT"
    raise InstrumentResolveError(f"unknown profile: {profile}")
