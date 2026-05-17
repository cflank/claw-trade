from claw_trade.config.profiles import is_profile_approved, require_profile


def test_us_and_cn_a_profiles_are_approved():
    us = require_profile("US")
    cn_a = require_profile("CN_A")
    hk = require_profile("HK")
    crypto = require_profile("CRYPTO")
    assert us.ok is True and us.name == "US"
    assert cn_a.ok is True and cn_a.name == "CN_A"
    assert hk.ok is True and hk.name == "HK"
    assert crypto.ok is True and crypto.name == "CRYPTO"
    assert is_profile_approved("US") is True
    assert is_profile_approved("CN_A") is True
    assert is_profile_approved("HK") is True
    assert is_profile_approved("CRYPTO") is True


def validate_profile_then_call_openclaw(
    profile_name: str,
    openclaw_callable,
):
    profile = require_profile(profile_name)
    if profile.ok:
        openclaw_callable()
    return profile


def test_approved_profile_calls_openclaw_after_validation():
    called = False

    def openclaw_spy() -> None:
        nonlocal called
        called = True

    profile = validate_profile_then_call_openclaw("US", openclaw_spy)
    assert profile.name == "US"
    assert called is True


def test_hk_profile_calls_openclaw_after_validation():
    called = False

    def openclaw_spy() -> None:
        nonlocal called
        called = True

    result = validate_profile_then_call_openclaw("HK", openclaw_spy)
    assert result.ok is True
    assert called is True


def test_crypto_profile_calls_openclaw_after_validation():
    called = False

    def openclaw_spy() -> None:
        nonlocal called
        called = True

    result = validate_profile_then_call_openclaw("CRYPTO", openclaw_spy)
    assert result.ok is True
    assert called is True
