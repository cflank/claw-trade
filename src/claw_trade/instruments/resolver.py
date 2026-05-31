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
_CRYPTO_QUOTE_SYMBOLS = ("USDT", "USDC", "USD", "BTC", "ETH")
_CRYPTO_COINGECKO_IDS = {
    "AR": "arweave",
    "BTC": "bitcoin",
    "DOGE": "dogecoin",
    "ETH": "ethereum",
    "SOL": "solana",
}
_CRYPTO_DEFILLAMA_PROTOCOLS = {
    "AR": "arweave",
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
}
_CRYPTO_OFFICIAL_REPOS = {
    "AR": "ArweaveTeam/arweave",
    "BTC": "bitcoin/bitcoin",
    "DOGE": "dogecoin/dogecoin",
    "ETH": "ethereum/go-ethereum",
    "SOL": "solana-labs/solana",
}
_IDENTITY_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


class InstrumentResolveError(ValueError):
    pass


@dataclass(frozen=True)
class ProviderSymbols:
    tushare_ts_code: str | None = None
    akshare_code: str | None = None
    yahoo_symbol: str | None = None
    crypto_base_symbol: str | None = None
    crypto_quote_symbol: str | None = None
    crypto_pair_symbol: str | None = None
    crypto_openbb_symbol: str | None = None
    coinglass_asset_symbol: str | None = None
    coinglass_contract_symbol: str | None = None
    coingecko_coin_id: str | None = None
    defillama_protocol_slug: str | None = None
    official_repo: str | None = None


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
        raise InstrumentResolveError("ticker must be one token containing only letters, numbers, slash, dot, dash, or underscore")

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


def resolve_crypto_provider_symbols(ticker: str) -> ProviderSymbols:
    return _normalize_crypto_ticker(str(ticker or "").strip().upper())[2]


def crypto_display_name(ticker: str) -> str | None:
    symbols = resolve_crypto_provider_symbols(ticker)
    base = symbols.crypto_base_symbol
    if not base:
        return None
    names = {
        "AR": "Arweave",
        "BTC": "Bitcoin",
        "DOGE": "Dogecoin",
        "ETH": "Ethereum",
        "SOL": "Solana",
    }
    return names.get(base)


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
    if _looks_like_cn_a_prefixed_code(code):
        return "CN_A"
    if _looks_like_hk_prefixed_code(code):
        return "HK"
    if code.isdigit():
        if len(code) == 6:
            return "CN_A"
        if len(code) in {3, 4, 5}:
            return "HK"

    compact = code.replace("-", "").replace("_", "")
    if code in _CRYPTO_BASE_SYMBOLS:
        return "CRYPTO"
    if _looks_like_crypto_pair_code(code):
        return "CRYPTO"
    if compact.isalnum() and any(char.isalpha() for char in compact) and any(char.isdigit() for char in compact):
        return "CRYPTO"
    if code.isalpha() and 1 <= len(code) <= 5:
        return "US"
    return "US"


def _normalize_profile_ticker(code: str, normalized_ticker: str, profile: str) -> tuple[str, str, ProviderSymbols]:
    if profile == "CN_A":
        token = normalized_ticker if normalized_ticker.endswith((".SH", ".SZ", ".BJ")) else code
        return _normalize_cn_a_ticker(token)
    if profile == "HK":
        return _normalize_hk_ticker(code)
    if profile == "US":
        return _normalize_us_ticker(code, normalized_ticker)
    if profile == "CRYPTO":
        return _normalize_crypto_ticker(normalized_ticker)
    return normalized_ticker, normalized_ticker, ProviderSymbols()


def _looks_like_cn_a_prefixed_code(code: str) -> bool:
    token = code.strip().upper()
    return token.startswith(("SH", "SZ", "BJ")) and token[2:].isdigit()


def _looks_like_hk_prefixed_code(code: str) -> bool:
    token = code.strip().upper()
    return token.startswith("HK") and token[2:].isdigit() and len(token[2:]) in {3, 4, 5}


def _looks_like_crypto_pair_code(code: str) -> bool:
    token = code.strip().upper()
    for separator in ("/", ".", "-", "_"):
        if separator not in token:
            continue
        left, right = token.split(separator, 1)
        return bool(left) and right in _CRYPTO_QUOTE_SYMBOLS
    return any(token.endswith(quote) and len(token) > len(quote) for quote in _CRYPTO_QUOTE_SYMBOLS)


def _normalize_cn_a_ticker(code: str) -> tuple[str, str, ProviderSymbols]:
    token = code.strip().upper()
    market: str | None = None
    if token.startswith(("SH", "SZ", "BJ")) and token[2:].isdigit():
        market = token[:2]
        token = token[2:]
    elif token.endswith((".SH", ".SZ", ".BJ")):
        token, market = token.rsplit(".", 1)
    if not token.isdigit():
        raise InstrumentResolveError("CN_A ticker code must contain only digits")
    code6 = token.zfill(6)
    if len(code6) != 6:
        raise InstrumentResolveError("CN_A ticker code must contain at most 6 digits")
    market = market or _infer_cn_a_exchange(code6)
    canonical = f"{code6}.{market}"
    provider_symbols = ProviderSymbols(
        tushare_ts_code=canonical,
        akshare_code=code6,
    )
    return canonical, canonical, provider_symbols


def _infer_cn_a_exchange(code6: str) -> str:
    if code6.startswith("92") or code6.startswith(("4", "8")):
        return "BJ"
    if code6.startswith(("5", "6", "9")):
        return "SH"
    return "SZ"


def _normalize_hk_ticker(code: str) -> tuple[str, str, ProviderSymbols]:
    if code.startswith("HK") and code[2:].isdigit():
        code = code[2:]
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


def _normalize_us_ticker(code: str, normalized_ticker: str) -> tuple[str, str, ProviderSymbols]:
    token = code.strip().upper()
    if normalized_ticker.endswith(".US") and token:
        symbol = token
    else:
        symbol = normalized_ticker.strip().upper()
    provider_symbols = ProviderSymbols(yahoo_symbol=symbol)
    return symbol, symbol, provider_symbols


def _normalize_crypto_ticker(token: str) -> tuple[str, str, ProviderSymbols]:
    base, quote, explicit_quote = _split_crypto_pair(token)
    display = f"{base}/{quote}" if explicit_quote or base not in _CRYPTO_BASE_SYMBOLS else base
    provider_symbols = ProviderSymbols(
        crypto_base_symbol=base,
        crypto_quote_symbol=quote,
        crypto_pair_symbol=f"{base}/{quote}",
        crypto_openbb_symbol=f"{base}{quote}",
        coinglass_asset_symbol=base,
        coinglass_contract_symbol=f"{base}{quote}",
        coingecko_coin_id=_CRYPTO_COINGECKO_IDS.get(base, base.lower()),
        defillama_protocol_slug=_CRYPTO_DEFILLAMA_PROTOCOLS.get(base, base.lower()),
        official_repo=_CRYPTO_OFFICIAL_REPOS.get(base),
    )
    return display, display, provider_symbols


def _split_crypto_pair(token: str) -> tuple[str, str, bool]:
    value = token.strip().upper()
    for separator in ("/", ".", "-", "_"):
        if separator not in value:
            continue
        left, right = value.split(separator, 1)
        if not left or not right or separator in right:
            raise InstrumentResolveError("crypto ticker pair must be BASE/QUOTE")
        if right not in _CRYPTO_QUOTE_SYMBOLS:
            raise InstrumentResolveError(f"unsupported crypto quote currency: {right}")
        return left, right, True
    for quote in _CRYPTO_QUOTE_SYMBOLS:
        if value.endswith(quote) and len(value) > len(quote):
            return value[: -len(quote)], quote, True
    return value, "USDT", False


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
