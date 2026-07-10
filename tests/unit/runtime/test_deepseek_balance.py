from __future__ import annotations

from decimal import Decimal

import pytest

from claw_trade.runtime.deepseek_balance import _deepseek_balance_url, parse_deepseek_cny_balance_payload


def test_parse_deepseek_balance_payload_returns_cny_total_balance() -> None:
    balance = parse_deepseek_cny_balance_payload(
        {
            "is_available": True,
            "balance_infos": [
                {"currency": "USD", "total_balance": "1.00"},
                {"currency": "CNY", "total_balance": "110.00"},
            ],
        }
    )

    assert balance.currency == "CNY"
    assert balance.total_balance == Decimal("110.00")
    assert balance.is_available is True


def test_parse_deepseek_balance_payload_requires_cny_balance() -> None:
    with pytest.raises(ValueError, match="deepseek_cny_balance_missing"):
        parse_deepseek_cny_balance_payload(
            {
                "is_available": True,
                "balance_infos": [{"currency": "USD", "total_balance": "1.00"}],
            }
        )


def test_deepseek_balance_url_only_allows_official_https_host() -> None:
    assert _deepseek_balance_url("https://api.deepseek.com/v1") == "https://api.deepseek.com/user/balance"

    with pytest.raises(ValueError, match="deepseek_balance_endpoint_unsupported"):
        _deepseek_balance_url("http://api.deepseek.com")

    with pytest.raises(ValueError, match="deepseek_balance_endpoint_unsupported"):
        _deepseek_balance_url("https://example.test")
