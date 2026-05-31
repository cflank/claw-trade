from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CninfoStockQuery:
    code: str
    column: str
    plate: str
    stock: str


def cninfo_stock_query(ticker: str) -> CninfoStockQuery:
    token = ticker.strip().upper()
    prefix = token[:2] if token[:2] in {"SH", "SZ", "BJ"} else ""
    if prefix:
        token = token[2:]
    suffix = ""
    if "." in token:
        code, suffix = token.split(".", 1)
    else:
        code = token
    code = code.zfill(6) if code.isdigit() and len(code) < 6 else code
    market = suffix or prefix

    if market in {"SH", "SS"} or code.startswith(("6", "9")):
        return CninfoStockQuery(code=code, column="sse", plate="sh", stock=f"{code},gssh0{code}")
    if market == "SZ" or code.startswith(("0", "3")):
        return CninfoStockQuery(code=code, column="szse", plate="sz", stock=f"{code},gssz0{code}")
    if market == "BJ" or code.startswith(("4", "8")):
        return CninfoStockQuery(code=code, column="bj", plate="bj", stock=code)
    return CninfoStockQuery(code=code, column="szse", plate="", stock=code)
