import pytest

from claw_trade.instruments.resolver import InstrumentResolveError, resolve_hk_identity, resolve_instrument_identity


@pytest.mark.parametrize(
    ("raw_ticker", "market_hint"),
    (
        ("0700", None),
        ("00700", None),
        ("0700.HK", None),
        ("00700.HK", None),
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
