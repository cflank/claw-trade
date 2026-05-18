from __future__ import annotations

from types import SimpleNamespace
import sys

from claw_trade.data_gateway.providers.tushare_client import (
    DEFAULT_TUSHARE_HTTP_URL,
    call_tushare_pro_bar,
    create_tushare_pro,
)


def test_create_tushare_pro_uses_proxy_http_url_by_default(monkeypatch) -> None:
    created: dict[str, object] = {}

    class _Pro:
        pass

    def _pro_api(token: str):
        created["token"] = token
        return _Pro()

    monkeypatch.setitem(sys.modules, "tushare", SimpleNamespace(pro_api=_pro_api))

    pro = create_tushare_pro(token="secret-token", env={})

    assert created == {"token": "secret-token"}
    assert pro._DataApi__http_url == DEFAULT_TUSHARE_HTTP_URL


def test_create_tushare_pro_allows_env_proxy_override(monkeypatch) -> None:
    class _Pro:
        pass

    monkeypatch.setitem(sys.modules, "tushare", SimpleNamespace(pro_api=lambda token: _Pro()))

    pro = create_tushare_pro(token="secret-token", env={"TUSHARE_HTTP_URL": "http://127.0.0.1:8010/"})

    assert pro._DataApi__http_url == "http://127.0.0.1:8010/"


def test_call_tushare_pro_bar_passes_api_and_non_empty_options(monkeypatch) -> None:
    seen: dict[str, object] = {}

    def _pro_bar(**kwargs):
        seen.update(kwargs)
        return "frame"

    api = object()
    monkeypatch.setitem(sys.modules, "tushare", SimpleNamespace(pro_bar=_pro_bar))

    result = call_tushare_pro_bar(
        api=api,
        ts_code="000001.SZ",
        start_date="20260501",
        end_date="20260517",
        adj="qfq",
        limit=3,
    )

    assert result == "frame"
    assert seen == {
        "api": api,
        "ts_code": "000001.SZ",
        "start_date": "20260501",
        "end_date": "20260517",
        "adj": "qfq",
        "limit": 3,
    }
