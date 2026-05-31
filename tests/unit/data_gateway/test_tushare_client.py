from __future__ import annotations

from types import SimpleNamespace
import sys
import time

from claw_trade.data_gateway.providers.tushare_client import (
    DEFAULT_TUSHARE_HTTP_URL,
    call_tushare_pro_bar,
    create_tushare_pro,
)


def test_create_tushare_pro_does_not_force_proxy_http_url_by_default(monkeypatch) -> None:
    created: dict[str, object] = {}

    class _Pro:
        pass

    def _pro_api(token: str):
        created["token"] = token
        return _Pro()

    monkeypatch.setitem(sys.modules, "tushare", SimpleNamespace(pro_api=_pro_api))

    pro = create_tushare_pro(token="secret-token", env={})

    assert created == {"token": "secret-token"}
    assert DEFAULT_TUSHARE_HTTP_URL == ""
    assert not hasattr(pro, "_DataApi__http_url")


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


def test_call_tushare_pro_bar_applies_proxy_url_to_existing_api(monkeypatch) -> None:
    class _Pro:
        pass

    seen: dict[str, object] = {}

    def _pro_bar(**kwargs):
        seen.update(kwargs)
        return "frame"

    api = _Pro()
    monkeypatch.setitem(sys.modules, "tushare", SimpleNamespace(pro_bar=_pro_bar))

    result = call_tushare_pro_bar(
        api=api,
        ts_code="000001.SZ",
        limit=3,
        env={"TUSHARE_HTTP_URL": "http://127.0.0.1:8010/"},
    )

    assert result == "frame"
    assert api._DataApi__http_url == "http://127.0.0.1:8010/"
    assert seen["api"] is api


def test_call_tushare_pro_bar_times_out_instead_of_hanging(monkeypatch) -> None:
    def _pro_bar(**kwargs):
        del kwargs
        time.sleep(0.2)
        return "late"

    monkeypatch.setitem(sys.modules, "tushare", SimpleNamespace(pro_bar=_pro_bar))

    try:
        call_tushare_pro_bar(
            api=object(),
            ts_code="000001.SZ",
            start_date="20260501",
            end_date="20260517",
            adj="qfq",
            timeout_seconds=0.01,
        )
    except TimeoutError as exc:
        assert "tushare pro_bar timed out" in str(exc)
    else:
        raise AssertionError("expected Tushare pro_bar timeout")
