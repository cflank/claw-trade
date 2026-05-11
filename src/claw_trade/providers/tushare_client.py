from __future__ import annotations

import os
from importlib import import_module
from typing import Any, Mapping


TUSHARE_TOKEN_ENV = "TUSHARE_TOKEN"
TUSHARE_HTTP_URL_ENV = "TUSHARE_HTTP_URL"
CN_A_TUSHARE_HTTP_URL_ENV = "CN_A_TUSHARE_HTTP_URL"


class TushareClientConfigError(ValueError):
    pass


def create_tushare_pro(
    *,
    token: str | None = None,
    http_url: str | None = None,
    env: Mapping[str, str] | None = None,
    tushare_module: Any | None = None,
) -> Any:
    resolved_env = os.environ if env is None else env
    resolved_token = _resolve_token(token=token, env=resolved_env)
    resolved_http_url = _resolve_http_url(http_url=http_url, env=resolved_env)

    ts = tushare_module if tushare_module is not None else import_module("tushare")
    pro = ts.pro_api(resolved_token)
    if resolved_http_url is not None:
        setattr(pro, "_DataApi__http_url", resolved_http_url)
    return pro


def _resolve_token(*, token: str | None, env: Mapping[str, str]) -> str:
    candidate = token if token is not None else env.get(TUSHARE_TOKEN_ENV)
    if candidate is None or candidate.strip() == "":
        raise TushareClientConfigError(f"{TUSHARE_TOKEN_ENV} missing")
    return candidate.strip()


def _resolve_http_url(*, http_url: str | None, env: Mapping[str, str]) -> str | None:
    candidate = http_url
    source_name = TUSHARE_HTTP_URL_ENV
    if candidate is None:
        if TUSHARE_HTTP_URL_ENV in env:
            candidate = env[TUSHARE_HTTP_URL_ENV]
            source_name = TUSHARE_HTTP_URL_ENV
        elif CN_A_TUSHARE_HTTP_URL_ENV in env:
            candidate = env[CN_A_TUSHARE_HTTP_URL_ENV]
            source_name = CN_A_TUSHARE_HTTP_URL_ENV
        else:
            return None
    cleaned = candidate.strip()
    if cleaned == "":
        raise TushareClientConfigError(f"{source_name} must not be empty")
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        raise TushareClientConfigError(f"{source_name} must start with http:// or https://")
    return cleaned


__all__ = [
    "CN_A_TUSHARE_HTTP_URL_ENV",
    "TUSHARE_HTTP_URL_ENV",
    "TUSHARE_TOKEN_ENV",
    "TushareClientConfigError",
    "create_tushare_pro",
]
