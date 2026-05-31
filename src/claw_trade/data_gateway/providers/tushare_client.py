from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
import os
from typing import Any, Mapping

DEFAULT_TUSHARE_HTTP_URL = ""
DEFAULT_TUSHARE_TIMEOUT_SECONDS = 20.0


def create_tushare_pro(*, token: str, env: Mapping[str, str] | None = None) -> Any:
    """Create the official Tushare pro client and apply the approved proxy URL.

    Official proxy pattern:
        pro = ts.pro_api(token)
        pro._DataApi__http_url = "http://your-tushare-proxy"

    The token and URL must come from runtime settings, not hard-coded source.
    """
    import tushare as ts

    pro = ts.pro_api(token)
    configure_tushare_http_url(pro, env=env)
    return pro


def configure_tushare_http_url(pro: Any, *, env: Mapping[str, str] | None = None) -> Any:
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
    timeout_seconds: float | None = None,
    env: Mapping[str, str] | None = None,
) -> Any:
    import tushare as ts

    configure_tushare_http_url(api, env=env)
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
    timeout = _tushare_timeout_seconds(timeout_seconds=timeout_seconds, env=env)
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="tushare-pro-bar")
    future = executor.submit(ts.pro_bar, **kwargs)
    try:
        return future.result(timeout=timeout)
    except FutureTimeoutError as exc:
        future.cancel()
        raise TimeoutError(f"tushare pro_bar timed out after {timeout:g}s") from exc
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _tushare_http_url(*, env: Mapping[str, str] | None = None) -> str:
    values = os.environ if env is None else env
    return (
        str(values.get("TUSHARE_HTTP_URL", "")).strip()
        or str(values.get("TUSHARE_PROXY_URL", "")).strip()
        or DEFAULT_TUSHARE_HTTP_URL
    )


def _tushare_timeout_seconds(*, timeout_seconds: float | None, env: Mapping[str, str] | None = None) -> float:
    if timeout_seconds is not None:
        return max(0.1, float(timeout_seconds))
    values = os.environ if env is None else env
    raw = str(values.get("TUSHARE_TIMEOUT_SECONDS", "")).strip()
    if raw:
        try:
            return max(0.1, float(raw))
        except ValueError:
            return DEFAULT_TUSHARE_TIMEOUT_SECONDS
    return DEFAULT_TUSHARE_TIMEOUT_SECONDS
