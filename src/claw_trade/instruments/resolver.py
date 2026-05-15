from __future__ import annotations

from dataclasses import dataclass, field
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
class ProviderSymbols:
    tushare_ts_code: str | None = None
    akshare_code: str | None = None
    yahoo_symbol: str | None = None


@dataclass(frozen=True)
class InstrumentIdentity:
    raw_ticker: str
    ticker: str
    display_ticker: str
    canonical_ticker: str
    market: str
    profile: str
    currency: str
    currency_symbol: str
    input_market: str | None = None
    counter_currency: str | None = None
    provider_symbols: ProviderSymbols = field(default_factory=ProviderSymbols)


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
    if normalized_hint is not None and suffix is not None and _profile_from_market(normalized_hint) != _profile_from_market(suffix):
        raise InstrumentResolveError(
            f"market hint {normalized_hint} conflicts with ticker suffix {suffix}"
        )
    explicit_market = normalized_hint or suffix
    if explicit_market:
        profile = _profile_from_market(explicit_market)
    else:
        profile = _infer_profile_from_code(input_code)

    canonical_ticker, display_ticker, provider_symbols = _normalize_profile_ticker(input_code, normalized_ticker, profile)
    currency, currency_symbol = _currency_for_profile(profile)
    return InstrumentIdentity(
        raw_ticker=raw_ticker,
        ticker=canonical_ticker,
        display_ticker=display_ticker,
        canonical_ticker=canonical_ticker,
        market=profile,
        profile=profile,
        currency=currency,
        currency_symbol=currency_symbol,
        input_market=explicit_market,
        counter_currency=None,
        provider_symbols=provider_symbols,
    )


def resolve_hk_identity(ticker: str, *, market_hint: str | None = "HK") -> InstrumentIdentity:
    resolved_hint = str(market_hint or "").strip().upper() or "HK"
    if resolved_hint != "HK":
        raise InstrumentResolveError(f"market hint {resolved_hint} conflicts with HK identity resolver")
    return resolve_instrument_identity(ticker, market_hint="HK")


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


def _normalize_profile_ticker(code: str, normalized_ticker: str, profile: str) -> tuple[str, str, ProviderSymbols]:
    if profile == "HK":
        return _normalize_hk_ticker(code)
    return normalized_ticker, normalized_ticker, ProviderSymbols()


def _normalize_hk_ticker(code: str) -> tuple[str, str, ProviderSymbols]:
    if not code.isdigit():
        raise InstrumentResolveError("HK ticker code must contain only digits")
    if len(code) not in {3, 4, 5}:
        raise InstrumentResolveError("HK ticker code must contain 3, 4, or 5 digits")
    five_digit_code = code.zfill(5)
    canonical = f"{five_digit_code}.HK"
    yahoo_code = five_digit_code.lstrip("0")
    if len(yahoo_code) < 4:
        yahoo_code = five_digit_code[-4:]
    provider_symbols = ProviderSymbols(
        tushare_ts_code=canonical,
        akshare_code=five_digit_code,
        yahoo_symbol=f"{yahoo_code}.HK",
    )
    return canonical, canonical, provider_symbols


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
