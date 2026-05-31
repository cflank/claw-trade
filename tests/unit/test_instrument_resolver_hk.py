import pytest

from claw_trade.instruments.resolver import InstrumentResolveError, resolve_hk_identity, resolve_instrument_identity


@pytest.mark.parametrize(
    ("raw_ticker", "market_hint"),
    (
        ("700", None),
        ("0700", None),
        ("00700", None),
        ("0700.HK", None),
        ("00700.HK", None),
        ("HK00700", None),
        ("700", "HK"),
        ("0700", "HK"),
        ("00700", "HK"),
    ),
)
def test_hk_identity_normalizes_display_canonical_and_provider_symbols(
    raw_ticker: str,
    market_hint: str | None,
) -> None:
    identity = resolve_instrument_identity(raw_ticker, market_hint=market_hint)

    assert identity.raw_ticker == raw_ticker
    assert identity.ticker == "00700.HK"
    assert identity.display_ticker == "00700.HK"
    assert identity.canonical_ticker == "00700.HK"
    assert identity.market == "HK"
    assert identity.profile == "HK"
    assert identity.currency == "HKD"
    assert identity.currency_symbol == "HK$"
    assert identity.counter_currency is None
    assert identity.provider_symbols.tushare_ts_code == "00700.HK"
    assert identity.provider_symbols.akshare_code == "00700"
    assert identity.provider_symbols.yahoo_symbol == "0700.HK"


def test_hk_identity_does_not_reclassify_explicit_hk_as_us() -> None:
    identity = resolve_hk_identity("700")

    assert identity.profile == "HK"
    assert identity.canonical_ticker == "00700.HK"


def test_hk_identity_resolver_rejects_non_hk_market_hint() -> None:
    with pytest.raises(InstrumentResolveError, match="HK identity resolver"):
        resolve_hk_identity("700", market_hint="US")


def test_hk_identity_rejects_non_hk_suffix_for_hk_hint() -> None:
    with pytest.raises(InstrumentResolveError, match="conflicts"):
        resolve_instrument_identity("0700.US", market_hint="HK")


def test_hk_identity_rejects_invalid_code_length() -> None:
    with pytest.raises(InstrumentResolveError, match="3, 4, or 5 digits"):
        resolve_instrument_identity("000700", market_hint="HK")


@pytest.mark.parametrize("raw_ticker", ("AR/USDT", "AR.USDT", "AR-USDT", "AR_USDT", "ARUSDT"))
def test_crypto_pair_identity_normalizes_user_input_to_slash_pair(raw_ticker: str) -> None:
    identity = resolve_instrument_identity(raw_ticker)

    assert identity.ticker == "AR/USDT"
    assert identity.display_ticker == "AR/USDT"
    assert identity.profile == "CRYPTO"
    assert identity.currency == "USDT"
    assert identity.provider_symbols.crypto_base_symbol == "AR"
    assert identity.provider_symbols.crypto_quote_symbol == "USDT"
    assert identity.provider_symbols.crypto_openbb_symbol == "ARUSDT"
    assert identity.provider_symbols.coinglass_asset_symbol == "AR"
    assert identity.provider_symbols.coinglass_contract_symbol == "ARUSDT"
    assert identity.provider_symbols.coingecko_coin_id == "arweave"
    assert identity.provider_symbols.defillama_protocol_slug == "arweave"
    assert identity.provider_symbols.official_repo == "ArweaveTeam/arweave"


def test_bare_ar_without_crypto_pair_remains_us_equity() -> None:
    identity = resolve_instrument_identity("AR")

    assert identity.ticker == "AR"
    assert identity.profile == "US"


@pytest.mark.parametrize(
    ("raw_ticker", "expected_ticker", "expected_tushare", "expected_akshare"),
    (
        ("600519", "600519.SH", "600519.SH", "600519"),
        ("600519.SH", "600519.SH", "600519.SH", "600519"),
        ("SH600519", "600519.SH", "600519.SH", "600519"),
        ("000001", "000001.SZ", "000001.SZ", "000001"),
        ("000001.SZ", "000001.SZ", "000001.SZ", "000001"),
        ("SZ000001", "000001.SZ", "000001.SZ", "000001"),
        ("430047.BJ", "430047.BJ", "430047.BJ", "430047"),
        ("BJ430047", "430047.BJ", "430047.BJ", "430047"),
    ),
)
def test_cn_a_identity_normalizes_common_user_formats(
    raw_ticker: str,
    expected_ticker: str,
    expected_tushare: str,
    expected_akshare: str,
) -> None:
    identity = resolve_instrument_identity(raw_ticker)

    assert identity.ticker == expected_ticker
    assert identity.profile == "CN_A"
    assert identity.currency == "CNY"
    assert identity.provider_symbols.tushare_ts_code == expected_tushare
    assert identity.provider_symbols.akshare_code == expected_akshare


@pytest.mark.parametrize(
    ("raw_ticker", "expected_ticker"),
    (
        ("AAPL", "AAPL"),
        ("AAPL.US", "AAPL"),
        ("BRK.B", "BRK.B"),
        ("BRK.B.US", "BRK.B"),
    ),
)
def test_us_identity_normalizes_explicit_us_suffix(raw_ticker: str, expected_ticker: str) -> None:
    identity = resolve_instrument_identity(raw_ticker)

    assert identity.ticker == expected_ticker
    assert identity.profile == "US"
    assert identity.currency == "USD"
    assert identity.provider_symbols.yahoo_symbol == expected_ticker


def test_crypto_hint_normalizes_ambiguous_ar_to_slash_pair() -> None:
    identity = resolve_instrument_identity("AR", market_hint="CRYPTO")

    assert identity.ticker == "AR/USDT"
    assert identity.profile == "CRYPTO"
