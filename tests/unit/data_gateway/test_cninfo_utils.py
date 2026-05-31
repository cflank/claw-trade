from __future__ import annotations

from claw_trade.data_gateway.providers.cninfo_utils import cninfo_stock_query


def test_cninfo_stock_query_uses_sse_profile_for_sh_ticker() -> None:
    profile = cninfo_stock_query("600519.SH")

    assert profile.code == "600519"
    assert profile.column == "sse"
    assert profile.plate == "sh"
    assert profile.stock == "600519,gssh0600519"


def test_cninfo_stock_query_keeps_sz_profile_for_sz_ticker() -> None:
    profile = cninfo_stock_query("000001.SZ")

    assert profile.code == "000001"
    assert profile.column == "szse"
    assert profile.plate == "sz"
    assert profile.stock == "000001,gssz0000001"
