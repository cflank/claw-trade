from __future__ import annotations

import os
from typing import Any, Mapping

DEFAULT_TUSHARE_HTTP_URL = "http://118.89.66.41:8010/"


def create_tushare_pro(*, token: str, env: Mapping[str, str] | None = None) -> Any:
    import tushare as ts

    pro = ts.pro_api(token)
    http_url = _tushare_http_url(env=env)
    if http_url:
        pro._DataApi__http_url = http_url
    return pro


def call_tushare_pro_bar(
    *,
    api: Any,
    ts_code: str,
    start_date: str | None = None,
    end_date: str | None = None,
    adj: str | None = None,
    limit: int | None = None,
) -> Any:
    import tushare as ts

    kwargs: dict[str, Any] = {
        "api": api,
        "ts_code": ts_code,
    }
    if start_date:
        kwargs["start_date"] = start_date
    if end_date:
        kwargs["end_date"] = end_date
    if adj:
        kwargs["adj"] = adj
    if limit is not None:
        kwargs["limit"] = limit
    return ts.pro_bar(**kwargs)


def _tushare_http_url(*, env: Mapping[str, str] | None = None) -> str:
    values = os.environ if env is None else env
    return (
        str(values.get("TUSHARE_HTTP_URL", "")).strip()
        or str(values.get("TUSHARE_PROXY_URL", "")).strip()
        or DEFAULT_TUSHARE_HTTP_URL
    )
