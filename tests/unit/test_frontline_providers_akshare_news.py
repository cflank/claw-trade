from __future__ import annotations

from datetime import datetime, timezone
import math
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.config import load_frontline_provider_config  # noqa: E402
from frontline_data_pack.models import ProviderQuery, ProviderQueryParameter, ProviderSpec  # noqa: E402
from frontline_data_pack.provider_executor import (  # noqa: E402
    PROVIDER_SCHEMA_INVALID,
    execute_provider_attempt,
)
from frontline_data_pack.provider_specs import load_news_provider_specs  # noqa: E402
from frontline_data_pack.providers_akshare_news import (  # noqa: E402
    call_akshare_news_cctv,
    call_akshare_stock_info_global_em,
    call_akshare_stock_info_global_cls,
    call_akshare_stock_news_em,
)
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


QUERY_FINGERPRINT = "sha256:" + ("c" * 64)


def test_t_pvd_006_stock_news_em_success_rows_are_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _FakeRequests:
        @staticmethod
        def get(_url: str, **kwargs: object) -> object:
            _ = kwargs
            return SimpleNamespace(text="")

    def _stock_news_em(**kwargs: object) -> pd.DataFrame:
        captured.update(kwargs)
        return pd.DataFrame(
            [
                {
                    "新闻标题": "贵州茅台公告更新",
                    "文章来源": "财联社",
                    "发布时间": "2026-05-07 09:31:00",
                    "新闻链接": "https://example.com/news/1",
                    "新闻内容": "公司发布公告",
                }
            ]
        )

    _stock_news_em.__globals__["requests"] = _FakeRequests()
    fake_akshare = SimpleNamespace(stock_news_em=_stock_news_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_news._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _news_spec(endpoint="stock_news_em")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_news_em},
    )

    assert result.attempt.status == "success"
    assert result.attempt.raw_count == 1
    assert result.attempt.accepted_count == 1
    assert result.normalized_rows[0]["title"] == "贵州茅台公告更新"
    assert result.normalized_rows[0]["source"] == "财联社"
    assert result.normalized_rows[0]["publish_time"] == "2026-05-07T09:31:00"
    assert result.normalized_rows[0]["url"] == "https://example.com/news/1"
    assert result.raw_payload is not None
    assert result.raw_payload["rows"][0]["title"] == "贵州茅台公告更新"
    assert captured["symbol"] == "600519"


def test_t_pvd_006_stock_news_em_missing_title_returns_schema_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeRequests:
        @staticmethod
        def get(_url: str, **kwargs: object) -> object:
            _ = kwargs
            return SimpleNamespace(text="")

    def _stock_news_em(**kwargs: object) -> pd.DataFrame:
        _ = kwargs
        return pd.DataFrame(
            [
                {
                    "文章来源": "财联社",
                    "发布时间": "2026-05-07 09:31:00",
                    "新闻链接": "https://example.com/news/1",
                    "新闻内容": "公司发布公告",
                }
            ]
        )

    _stock_news_em.__globals__["requests"] = _FakeRequests()
    fake_akshare = SimpleNamespace(
        stock_news_em=_stock_news_em
    )
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_news._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _news_spec(endpoint="stock_news_em")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_news_em},
    )

    assert result.attempt.status == "schema_invalid"
    assert result.attempt.error_code == PROVIDER_SCHEMA_INVALID
    assert result.attempt.raw_count == 1
    assert result.normalized_rows == []


def test_t_pvd_006_stock_info_global_cls_filters_by_date_window(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "data": {
                    "roll_data": [
                        {
                            "title": "窗口内快讯",
                            "content": "窗口内",
                            "ctime": int(datetime(2026, 5, 6, 1, 31, tzinfo=timezone.utc).timestamp()),
                            "level": "A",
                        },
                        {
                            "title": "窗口外快讯",
                            "content": "窗口外",
                            "ctime": int(datetime(2026, 4, 1, 2, 0, tzinfo=timezone.utc).timestamp()),
                            "level": "A",
                        },
                    ]
                }
            }

    class _FakeRequests:
        @staticmethod
        def get(_url: str, **kwargs: object) -> _FakeResponse:
            _ = kwargs
            return _FakeResponse()

    class _FakeTime:
        @staticmethod
        def sleep(_seconds: float) -> None:
            return None

    def _fake_make_request_with_retry_json(
        url: str,
        params: object = None,
        headers: object = None,
        proxies: object = None,
        max_retries: int = 3,
        retry_delay: int = 1,
    ) -> dict[str, Any]:
        _ = headers, proxies
        for _attempt in range(max_retries):
            response = requests.get(url, params=params)  # type: ignore[name-defined]
            if response.status_code == 200:
                return response.json()
            time.sleep(retry_delay)  # type: ignore[name-defined]
        raise RuntimeError("request failed")

    _fake_make_request_with_retry_json.__globals__["requests"] = _FakeRequests()
    _fake_make_request_with_retry_json.__globals__["time"] = _FakeTime()

    def _stock_info_global_cls(symbol: str = "全部") -> pd.DataFrame:
        _ = symbol
        data_json = make_request_with_retry_json(  # type: ignore[name-defined]
            "https://example.com/telegraph",
            max_retries=2,
            headers=headers,  # type: ignore[name-defined]
        )
        temp_df = pd.DataFrame(data_json["data"]["roll_data"])
        big_df = temp_df.copy()
        big_df = big_df[["title", "content", "ctime", "level"]]
        big_df["ctime"] = pd.to_datetime(big_df["ctime"], unit="s", utc=True).dt.tz_convert("Asia/Shanghai")
        big_df.columns = ["标题", "内容", "发布时间", "等级"]
        big_df["发布日期"] = big_df["发布时间"].dt.date
        big_df["发布时间"] = big_df["发布时间"].dt.time
        return big_df[["标题", "内容", "发布日期", "发布时间"]]

    _stock_info_global_cls.__globals__["make_request_with_retry_json"] = _fake_make_request_with_retry_json
    _stock_info_global_cls.__globals__["headers"] = {"User-Agent": "pytest"}

    fake_akshare = SimpleNamespace(
        stock_info_global_cls=_stock_info_global_cls
    )
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_news._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _news_spec(endpoint="stock_info_global_cls")
    result = execute_provider_attempt(
        spec,
        _query(start_date="2026-05-01", end_date="2026-05-08"),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_info_global_cls},
    )

    assert result.attempt.status == "success"
    assert result.attempt.accepted_count == 1
    assert result.normalized_rows[0]["title"] == "窗口内快讯"
    assert result.normalized_rows[0]["source"] == "财联社"
    assert result.normalized_rows[0]["publish_time"] == "2026-05-06T09:31:00"
    assert all(row["title"] != "窗口外快讯" for row in result.normalized_rows)


def test_stock_info_global_em_success_rows_are_mapped_and_windowed(monkeypatch: pytest.MonkeyPatch) -> None:
    def _stock_info_global_em() -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "标题": "窗口内全球市场快讯",
                    "摘要": "海外流动性变化",
                    "发布时间": "2026-05-07 08:00:00",
                    "来源": "东方财富",
                    "链接": "https://example.com/global/1",
                },
                {
                    "标题": "窗口外全球市场快讯",
                    "摘要": "旧新闻",
                    "发布时间": "2026-04-01 08:00:00",
                    "来源": "东方财富",
                    "链接": "https://example.com/global/old",
                },
            ]
        )

    fake_akshare = SimpleNamespace(stock_info_global_em=_stock_info_global_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_news._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _news_spec(endpoint="stock_info_global_em")
    result = execute_provider_attempt(
        spec,
        _query(start_date="2026-05-01", end_date="2026-05-08"),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_info_global_em},
    )

    assert result.attempt.status == "success"
    assert result.attempt.accepted_count == 1
    assert result.normalized_rows[0]["title"] == "窗口内全球市场快讯"
    assert result.normalized_rows[0]["source"] == "东方财富"
    assert result.normalized_rows[0]["publish_time"] == "2026-05-07T08:00:00"
    assert result.normalized_rows[0]["url"] == "https://example.com/global/1"
    assert result.normalized_rows[0]["content"] == "海外流动性变化"


def test_news_cctv_success_rows_are_mapped(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def _news_cctv(date: str) -> pd.DataFrame:
        captured["date"] = date
        return pd.DataFrame(
            [
                {
                    "date": "2026-05-07",
                    "title": "国务院常务会议部署稳就业",
                    "content": "会议强调落实存量政策并研究增量政策。",
                }
            ]
        )

    fake_akshare = SimpleNamespace(news_cctv=_news_cctv)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_news._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _news_spec(endpoint="news_cctv")
    result = execute_provider_attempt(
        spec,
        _query(start_date="2026-05-01", end_date="2026-05-08"),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_news_cctv},
    )

    assert captured["date"] == "20260508"
    assert result.attempt.status == "success"
    assert result.attempt.accepted_count == 1
    assert result.normalized_rows[0]["title"] == "国务院常务会议部署稳就业"
    assert result.normalized_rows[0]["source"] == "新闻联播"
    assert result.normalized_rows[0]["publish_time"] == "2026-05-07"
    assert result.normalized_rows[0]["content"] == "会议强调落实存量政策并研究增量政策。"


@pytest.mark.parametrize(
    ("provider", "endpoint"),
    [
        ("bocha", "cn_web_search"),
        ("tavily", "news_web_search"),
        ("jina", "search_and_reader"),
        ("newsnow", "hot_topics_aggregator"),
        ("minimax", "structured_search"),
        ("tushare", "anns_d"),
    ],
)
def test_t_pvd_006_disabled_enhanced_news_sources_return_config_blocked_without_network_call(
    provider: str,
    endpoint: str,
) -> None:
    config = load_frontline_provider_config(_base_env())
    matrix_spec = next(
        spec for spec in load_news_provider_specs(config) if spec.provider == provider and spec.endpoint == endpoint
    )
    result = execute_provider_attempt(
        matrix_spec,
        _query(),
        _context(),
        call_registry={},
    )
    assert matrix_spec.enabled is True
    assert result.attempt.status == "error"
    assert result.attempt.error_code == "PROVIDER_ERROR"


def test_t_pvd_006_stock_news_em_passes_remaining_budget_to_underlying_http(monkeypatch: pytest.MonkeyPatch) -> None:
    request_timeouts: list[float] = []
    captured_symbol: dict[str, str] = {}

    class _FakeRequests:
        def get(self, _url: str, **kwargs: object) -> object:
            timeout = kwargs.get("timeout")
            assert isinstance(timeout, float)
            request_timeouts.append(timeout)
            return SimpleNamespace(text="")

    def _fake_stock_news_em(symbol: str = "603777") -> pd.DataFrame:
        captured_symbol["symbol"] = symbol
        requests.get("https://example.com/news")  # type: ignore[name-defined]
        requests.get("https://example.com/news", timeout=99.0)  # type: ignore[name-defined]
        return pd.DataFrame(
            [
                {
                    "新闻标题": "预算测试",
                    "文章来源": "财联社",
                    "发布时间": "2026-05-07 09:31:00",
                    "新闻链接": "https://example.com/news/1",
                }
            ]
        )

    _fake_stock_news_em.__globals__["requests"] = _FakeRequests()
    fake_akshare = SimpleNamespace(stock_news_em=_fake_stock_news_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_news._load_akshare_module",
        lambda: fake_akshare,
    )

    call_akshare_stock_news_em(
        _news_spec(endpoint="stock_news_em"),
        _query(),
        _context(),
        _fixed_call_context(timeout_ms=2500),
    )

    assert captured_symbol["symbol"] == "600519"
    assert request_timeouts
    assert all(math.isclose(value, 2.5, rel_tol=0.0, abs_tol=1e-9) for value in request_timeouts)


def test_stock_info_global_em_passes_remaining_budget_to_underlying_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_timeouts: list[float] = []

    class _FakeRequests:
        def get(self, _url: str, **kwargs: object) -> object:
            timeout = kwargs.get("timeout")
            assert isinstance(timeout, float)
            request_timeouts.append(timeout)
            return object()

    def _fake_stock_info_global_em() -> pd.DataFrame:
        requests.get("https://example.com/global")  # type: ignore[name-defined]
        return pd.DataFrame(
            [
                {
                    "标题": "预算测试",
                    "摘要": "内容",
                    "发布时间": "2026-05-07 08:00:00",
                    "链接": "https://example.com/global/1",
                }
            ]
        )

    _fake_stock_info_global_em.__globals__["requests"] = _FakeRequests()
    fake_akshare = SimpleNamespace(stock_info_global_em=_fake_stock_info_global_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_news._load_akshare_module",
        lambda: fake_akshare,
    )

    call_akshare_stock_info_global_em(
        _news_spec(endpoint="stock_info_global_em"),
        _query(),
        _context(),
        _fixed_call_context(timeout_ms=2500),
    )

    assert request_timeouts
    assert all(math.isclose(value, 2.5, rel_tol=0.0, abs_tol=1e-9) for value in request_timeouts)


def test_news_cctv_passes_remaining_budget_to_underlying_http(monkeypatch: pytest.MonkeyPatch) -> None:
    request_timeouts: list[float] = []

    class _FakeRequests:
        def get(self, _url: str, **kwargs: object) -> object:
            timeout = kwargs.get("timeout")
            assert isinstance(timeout, float)
            request_timeouts.append(timeout)
            return object()

    def _fake_news_cctv(date: str) -> pd.DataFrame:
        _ = date
        requests.get("https://example.com/cctv")  # type: ignore[name-defined]
        return pd.DataFrame(
            [
                {
                    "date": "2026-05-07",
                    "title": "预算测试",
                    "content": "内容",
                }
            ]
        )

    _fake_news_cctv.__globals__["requests"] = _FakeRequests()
    fake_akshare = SimpleNamespace(news_cctv=_fake_news_cctv)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_news._load_akshare_module",
        lambda: fake_akshare,
    )

    call_akshare_news_cctv(
        _news_spec(endpoint="news_cctv"),
        _query(),
        _context(),
        _fixed_call_context(timeout_ms=2500),
    )

    assert request_timeouts
    assert all(math.isclose(value, 2.5, rel_tol=0.0, abs_tol=1e-9) for value in request_timeouts)


def test_t_pvd_006_stock_info_global_cls_passes_remaining_budget_to_underlying_http(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    request_timeouts: list[float] = []

    class _FakeResponse:
        status_code = 200

        def json(self) -> dict[str, Any]:
            return {
                "data": {
                    "roll_data": [
                        {
                            "title": "预算窗口内",
                            "content": "内容",
                            "ctime": int(datetime(2026, 5, 7, 1, 31, tzinfo=timezone.utc).timestamp()),
                            "level": "A",
                        }
                    ]
                }
            }

    class _FakeRequests:
        def get(self, _url: str, **kwargs: object) -> _FakeResponse:
            timeout = kwargs.get("timeout")
            assert isinstance(timeout, float)
            request_timeouts.append(timeout)
            return _FakeResponse()

    class _FakeTime:
        @staticmethod
        def sleep(_seconds: float) -> None:
            return None

    def _fake_make_request_with_retry_json(
        url: str,
        params: object = None,
        headers: object = None,
        proxies: object = None,
        max_retries: int = 3,
        retry_delay: int = 1,
    ) -> dict[str, Any]:
        _ = headers, proxies
        for _attempt in range(max_retries):
            response = requests.get(url, params=params)  # type: ignore[name-defined]
            if response.status_code == 200:
                return response.json()
            time.sleep(retry_delay)  # type: ignore[name-defined]
        raise RuntimeError("request failed")

    _fake_make_request_with_retry_json.__globals__["requests"] = _FakeRequests()
    _fake_make_request_with_retry_json.__globals__["time"] = _FakeTime()

    def _fake_stock_info_global_cls(symbol: str = "全部") -> pd.DataFrame:
        _ = symbol
        data_json = make_request_with_retry_json(  # type: ignore[name-defined]
            "https://example.com/telegraph",
            max_retries=2,
            headers=headers,  # type: ignore[name-defined]
        )
        temp_df = pd.DataFrame(data_json["data"]["roll_data"])
        big_df = temp_df.copy()
        big_df = big_df[["title", "content", "ctime", "level"]]
        big_df["ctime"] = pd.to_datetime(big_df["ctime"], unit="s", utc=True).dt.tz_convert("Asia/Shanghai")
        big_df.columns = ["标题", "内容", "发布时间", "等级"]
        big_df["发布日期"] = big_df["发布时间"].dt.date
        big_df["发布时间"] = big_df["发布时间"].dt.time
        return big_df[["标题", "内容", "发布日期", "发布时间"]]

    _fake_stock_info_global_cls.__globals__["make_request_with_retry_json"] = _fake_make_request_with_retry_json
    _fake_stock_info_global_cls.__globals__["headers"] = {"User-Agent": "pytest"}

    fake_akshare = SimpleNamespace(stock_info_global_cls=_fake_stock_info_global_cls)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_news._load_akshare_module",
        lambda: fake_akshare,
    )

    call_akshare_stock_info_global_cls(
        _news_spec(endpoint="stock_info_global_cls"),
        _query(),
        _context(),
        _fixed_call_context(timeout_ms=2500),
    )

    assert request_timeouts
    assert all(math.isclose(value, 2.5, rel_tol=0.0, abs_tol=1e-9) for value in request_timeouts)


class _FixedCallContext:
    def __init__(self, timeout_ms: int) -> None:
        self._timeout_ms = timeout_ms
        self.cancel_checks = 0

    def remaining_timeout_ms(self) -> int:
        return self._timeout_ms

    def raise_if_cancelled(self) -> None:
        self.cancel_checks += 1


def _fixed_call_context(*, timeout_ms: int) -> _FixedCallContext:
    return _FixedCallContext(timeout_ms)


def _news_spec(
    *,
    endpoint: str,
    timeout_ms: int = 1000,
) -> ProviderSpec:
    return ProviderSpec(
        domain="news",
        priority="P0",
        provider="akshare",
        endpoint=endpoint,
        role="company_news" if endpoint == "stock_news_em" else "macro_flash",
        enabled=True,
        mode="remote",
        timeout_ms=timeout_ms,
        required_for_complete=endpoint == "stock_news_em",
        query_parameters=[
            ProviderQueryParameter(
                name="symbol",
                source="ticker_code_6",
                required=True,
                fixed_value=None,
            )
        ],
    )


def _query(
    *,
    start_date: str = "2026-05-01",
    end_date: str = "2026-05-08",
) -> ProviderQuery:
    return ProviderQuery(
        market="CN_A",
        ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
        query_fingerprint=QUERY_FINGERPRINT,
    )


def _context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-pvd-006",
        stage="frontline",
        worker_id="news_analyst",
        call_id="call-pvd-006",
        dispatch_id="dispatch-pvd-006",
        tool_name="news_news_data_pack",
        evidence_root="viking://resources/workflow/run-pvd-006/frontline/news_analyst/call-pvd-006/evidence",
        current_time="2026-05-08T12:00:00Z",
        current_date="2026-05-08",
    )


def _base_env() -> dict[str, str]:
    return {
        "CN_A_MONGODB_URI": "mongodb://localhost:27017/claw_trade",
        "CLAW_TRADE_OPENVIKING_BASE_URI": "https://openviking.internal",
        "CLAW_TRADE_OPENVIKING_AUTH_MODE": "none",
    }
