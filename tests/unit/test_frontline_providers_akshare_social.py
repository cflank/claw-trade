from __future__ import annotations

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
from frontline_data_pack.provider_executor import PROVIDER_SCHEMA_INVALID, execute_provider_attempt  # noqa: E402
from frontline_data_pack.provider_specs import load_social_provider_specs  # noqa: E402
from frontline_data_pack.providers_akshare_social import (  # noqa: E402
    call_akshare_stock_hot_keyword_em,
    call_akshare_stock_hot_rank_em,
    call_akshare_stock_hot_rank_latest_em,
)
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


QUERY_FINGERPRINT = "sha256:" + ("b" * 64)


def test_t_pvd_007_hot_rank_latest_maps_rank_and_heat(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _stock_hot_rank_latest_em(symbol: str, timeout: float) -> pd.DataFrame:
        captured["symbol"] = symbol
        captured["timeout"] = timeout
        return pd.DataFrame(
            [
                {
                    "代码": "600519",
                    "股票名称": "贵州茅台",
                    "排名": 1,
                    "热度": 12345.6,
                }
            ]
        )

    fake_akshare = SimpleNamespace(stock_hot_rank_latest_em=_stock_hot_rank_latest_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_social._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _social_spec(provider="eastmoney_akshare", endpoint="hot_rank_latest", role="target_attention")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_hot_rank_latest_em},
    )

    assert result.attempt.status == "success"
    assert result.attempt.accepted_count == 1
    row = result.normalized_rows[0]
    assert row["signal_type"] == "attention"
    assert row["rank"] == 1
    assert math.isclose(float(row["heat_value"]), 12345.6, rel_tol=0.0, abs_tol=1e-9)
    assert row["provider"] == "eastmoney_akshare"
    assert row["endpoint"] == "hot_rank_latest"
    assert row["source_platform"] == "eastmoney"
    assert captured["symbol"] == "SH600519"


def test_t_pvd_007_hot_rank_latest_item_value_schema_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    def _stock_hot_rank_latest_em(symbol: str, timeout: float) -> pd.DataFrame:
        _ = symbol, timeout
        return pd.DataFrame(
            [
                {"item": "srcSecurityCode", "value": "SH600519"},
                {"item": "rank", "value": 7},
                {"item": "rankChange", "value": -1},
                {"item": "calcTime", "value": "2026-05-08 10:05:00"},
            ]
        )

    fake_akshare = SimpleNamespace(stock_hot_rank_latest_em=_stock_hot_rank_latest_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_social._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _social_spec(provider="eastmoney_akshare", endpoint="hot_rank_latest", role="target_attention")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_hot_rank_latest_em},
    )

    assert result.attempt.status == "success"
    assert result.attempt.accepted_count == 1
    assert result.normalized_rows[0]["rank"] == 7
    assert result.normalized_rows[0]["rank_change"] == -1


def test_t_pvd_007_hot_keyword_signal_type_is_topic_keyword(monkeypatch: pytest.MonkeyPatch) -> None:
    def _stock_hot_keyword_em(symbol: str, timeout: float) -> pd.DataFrame:
        _ = symbol, timeout
        return pd.DataFrame(
            [
                {
                    "关键词": "白酒龙头",
                    "排名": 2,
                    "热度": 980.0,
                }
            ]
        )

    fake_akshare = SimpleNamespace(stock_hot_keyword_em=_stock_hot_keyword_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_social._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _social_spec(provider="eastmoney_akshare", endpoint="hot_keyword", role="target_keyword")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_hot_keyword_em},
    )

    assert result.attempt.status == "success"
    assert result.attempt.accepted_count == 1
    assert result.normalized_rows[0]["signal_type"] == "topic_keyword"
    assert result.normalized_rows[0]["keyword"] == "白酒龙头"


def test_t_pvd_007_hot_keyword_concept_name_schema_is_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    def _stock_hot_keyword_em(symbol: str, timeout: float) -> pd.DataFrame:
        _ = symbol, timeout
        return pd.DataFrame(
            [
                {
                    "时间": "2026-05-08 11:00:00",
                    "股票代码": "SH600519",
                    "概念名称": "高端白酒",
                    "热度": 321.0,
                }
            ]
        )

    fake_akshare = SimpleNamespace(stock_hot_keyword_em=_stock_hot_keyword_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_social._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _social_spec(provider="eastmoney_akshare", endpoint="hot_keyword", role="target_keyword")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_hot_keyword_em},
    )

    assert result.attempt.status == "success"
    assert result.attempt.accepted_count == 1
    assert result.normalized_rows[0]["signal_type"] == "topic_keyword"
    assert result.normalized_rows[0]["keyword"] == "高端白酒"


def test_t_pvd_007_full_board_without_target_keeps_background_with_not_accepted_target(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _stock_hot_rank_em(timeout: float) -> pd.DataFrame:
        _ = timeout
        return pd.DataFrame(
            [
                {"代码": "000001", "股票名称": "平安银行", "排名": 3, "热度": 678.0},
                {"代码": "000333", "股票名称": "美的集团", "排名": 5, "热度": 456.0},
            ]
        )

    fake_akshare = SimpleNamespace(stock_hot_rank_em=_stock_hot_rank_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_social._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _social_spec(provider="eastmoney_akshare", endpoint="stock_hot_rank_em", role="board_check")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_hot_rank_em},
    )

    assert result.attempt.status == "success"
    assert result.attempt.raw_count == 2
    assert result.attempt.accepted_count == 2
    assert all(row["matched_target"] is False for row in result.normalized_rows)
    assert all(row["accepted_target"] is False for row in result.normalized_rows)


def test_t_pvd_007_schema_change_returns_schema_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    def _stock_hot_rank_latest_em(symbol: str, timeout: float) -> pd.DataFrame:
        _ = symbol, timeout
        return pd.DataFrame(
            [
                {
                    "代码": "600519",
                    "股票名称": "贵州茅台",
                }
            ]
        )

    fake_akshare = SimpleNamespace(stock_hot_rank_latest_em=_stock_hot_rank_latest_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_social._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _social_spec(provider="eastmoney_akshare", endpoint="hot_rank_latest", role="target_attention")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_hot_rank_latest_em},
    )

    assert result.attempt.status == "schema_invalid"
    assert result.attempt.error_code == PROVIDER_SCHEMA_INVALID


def test_t_pvd_007_remaining_budget_is_passed_to_underlying_http(monkeypatch: pytest.MonkeyPatch) -> None:
    request_timeouts: list[float] = []
    captured_symbol: dict[str, str] = {}

    class _FakeRequests:
        def get(self, _url: str, **kwargs: object) -> object:
            timeout = kwargs.get("timeout")
            assert isinstance(timeout, float)
            request_timeouts.append(timeout)
            return SimpleNamespace(text="")

    def _stock_hot_keyword_em(symbol: str = "SH603777") -> pd.DataFrame:
        captured_symbol["symbol"] = symbol
        requests.get("https://example.com/social-a")  # type: ignore[name-defined]
        requests.get("https://example.com/social-b", timeout=99.0)  # type: ignore[name-defined]
        return pd.DataFrame(
            [
                {
                    "关键词": "预算测试",
                    "排名": 9,
                    "热度": 77.0,
                }
            ]
        )

    _stock_hot_keyword_em.__globals__["requests"] = _FakeRequests()
    fake_akshare = SimpleNamespace(stock_hot_keyword_em=_stock_hot_keyword_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_social._load_akshare_module",
        lambda: fake_akshare,
    )

    call_akshare_stock_hot_keyword_em(
        _social_spec(provider="eastmoney_akshare", endpoint="hot_keyword", role="target_keyword"),
        _query(),
        _context(),
        _fixed_call_context(timeout_ms=2500),
    )

    assert captured_symbol["symbol"] == "SH600519"
    assert request_timeouts
    assert all(math.isclose(value, 2.5, rel_tol=0.0, abs_tol=1e-9) for value in request_timeouts)


def test_t_pvd_007_matrix_social_sources_without_registered_caller_are_explicit_failures() -> None:
    config = load_frontline_provider_config(_base_env())
    specs = load_social_provider_specs(config)
    matrix_specs = [
        spec
        for spec in specs
        if spec.provider in {"bocha", "jina", "tavily", "alphaear_news_source_list", "xueqiu_guba"}
    ]

    for spec in matrix_specs:
        result = execute_provider_attempt(
            spec,
            _query(),
            _context(),
            call_registry={},
        )
        assert spec.enabled is True
        assert result.attempt.status == "error"
        assert result.attempt.error_code == "PROVIDER_ERROR"


def test_t_pvd_007_akshare_exception_is_redacted_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(**kwargs: object) -> pd.DataFrame:
        _ = kwargs
        raise RuntimeError("token=secrettokenvalue1234567890 https://user:pass@example.com/path?token=abc123")

    fake_akshare = SimpleNamespace(stock_hot_rank_latest_em=_raise)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_social._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _social_spec(provider="eastmoney_akshare", endpoint="hot_rank_latest", role="target_attention")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_hot_rank_latest_em},
    )

    assert result.attempt.status == "error"
    assert result.attempt.error_message_redacted is not None
    assert "***" in result.attempt.error_message_redacted
    assert "secrettokenvalue1234567890" not in result.attempt.error_message_redacted
    assert "user:pass" not in result.attempt.error_message_redacted


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


def _social_spec(
    *,
    provider: str,
    endpoint: str,
    role: str,
    timeout_ms: int = 1000,
) -> ProviderSpec:
    return ProviderSpec(
        domain="social",
        priority="P0",
        provider=provider,
        endpoint=endpoint,
        role=role,
        enabled=True,
        mode="remote",
        timeout_ms=timeout_ms,
        required_for_complete=True,
        query_parameters=[
            ProviderQueryParameter(
                name="symbol",
                source="eastmoney_symbol",
                required=True,
                fixed_value=None,
            )
        ],
    )


def _query() -> ProviderQuery:
    return ProviderQuery(
        market="CN_A",
        ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-08",
        adjust="qfq",
        query_fingerprint=QUERY_FINGERPRINT,
    )


def _context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-pvd-007",
        stage="frontline",
        worker_id="social_analyst",
        call_id="call-pvd-007",
        dispatch_id="dispatch-pvd-007",
        tool_name="social_social_sentiment_pack",
        evidence_root="viking://resources/workflow/run-pvd-007/frontline/social_analyst/call-pvd-007/evidence",
        current_time="2026-05-08T12:00:00Z",
        current_date="2026-05-08",
    )


def _base_env() -> dict[str, str]:
    return {
        "CN_A_MONGODB_URI": "mongodb://localhost:27017/claw_trade",
        "CLAW_TRADE_OPENVIKING_BASE_URI": "https://openviking.internal",
        "CLAW_TRADE_OPENVIKING_AUTH_MODE": "none",
    }
