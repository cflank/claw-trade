from __future__ import annotations

from dataclasses import replace
import math
import sys
from pathlib import Path
from types import SimpleNamespace

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
from frontline_data_pack.provider_specs import load_fundamental_provider_specs  # noqa: E402
from frontline_data_pack.providers_akshare_fundamental import (  # noqa: E402
    call_akshare_stock_financial_abstract_ths,
    call_akshare_stock_individual_info_em,
    call_akshare_stock_zh_a_spot_em,
)
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


QUERY_FINGERPRINT = "sha256:" + ("f" * 64)


def test_t_pvd_008_stock_individual_info_em_maps_company_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _stock_individual_info_em(symbol: str, timeout: float) -> pd.DataFrame:
        captured["symbol"] = symbol
        captured["timeout"] = timeout
        return pd.DataFrame(
            [
                {"item": "股票简称", "value": "贵州茅台"},
                {"item": "所属行业", "value": "酿酒行业"},
                {"item": "主营业务", "value": "白酒生产与销售"},
                {"item": "上市时间", "value": "2001-08-27"},
            ]
        )

    fake_akshare = SimpleNamespace(stock_individual_info_em=_stock_individual_info_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_fundamental._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _fundamental_spec(endpoint="stock_individual_info_em")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_individual_info_em},
    )

    assert result.attempt.status == "success"
    assert result.attempt.accepted_count > 0
    assert any(row.get("field_name") == "company_profile.industry" for row in result.normalized_rows)
    assert any(row.get("field_name") == "company_profile.company_name" for row in result.normalized_rows)
    assert captured["symbol"] == "600519"
    assert isinstance(captured["timeout"], float)
    assert float(captured["timeout"]) > 0.0


def test_t_pvd_008_financial_abstract_maps_required_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def _stock_financial_abstract_ths(symbol: str, indicator: str, timeout: float) -> pd.DataFrame:
        captured["symbol"] = symbol
        captured["indicator"] = indicator
        captured["timeout"] = timeout
        return pd.DataFrame(
            [
                {
                    "报告期": "2025-12-31",
                    "净利润": 123456789.0,
                    "营业总收入": 321654987.0,
                    "每股收益": 8.88,
                    "净资产收益率": 31.2,
                }
            ]
        )

    fake_akshare = SimpleNamespace(stock_financial_abstract_ths=_stock_financial_abstract_ths)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_fundamental._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _fundamental_spec(endpoint="stock_financial_abstract_ths")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_financial_abstract_ths},
    )

    assert result.attempt.status == "success"
    assert result.attempt.accepted_count > 0
    row = result.normalized_rows[0]
    assert row["report_period"] == "2025-12-31"
    assert row["income_statement.net_profit"] == 123456789.0
    assert row["income_statement.revenue"] == 321654987.0
    assert row["income_statement.eps"] == 8.88
    assert row["financial_indicators.roe"] == 31.2
    assert captured["symbol"] == "600519"
    assert captured["indicator"] == "20260508"


def test_t_pvd_008_stock_zh_a_spot_em_filters_target_and_maps_valuation(monkeypatch: pytest.MonkeyPatch) -> None:
    def _stock_zh_a_spot_em(timeout: float) -> pd.DataFrame:
        _ = timeout
        return pd.DataFrame(
            [
                {"代码": "000001", "最新价": 10.1, "总市值": 1000000000.0, "市盈率-动态": 12.1, "市净率": 1.1},
                {"代码": "600519", "最新价": 1688.88, "总市值": 2123000000000.0, "市盈率-动态": 25.2, "市净率": 8.8},
            ]
        )

    fake_akshare = SimpleNamespace(stock_zh_a_spot_em=_stock_zh_a_spot_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_fundamental._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _fundamental_spec(endpoint="stock_zh_a_spot_em")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_zh_a_spot_em},
    )

    assert result.attempt.status == "success"
    assert result.attempt.accepted_count == 1
    row = result.normalized_rows[0]
    assert row["ticker_code"] == "600519"
    assert row["price_context.close"] == 1688.88
    assert row["valuation.total_mv"] == 2123000000000.0
    assert row["valuation.pe_ttm"] == 25.2
    assert row["valuation.pb"] == 8.8


@pytest.mark.parametrize(
    ("provider", "endpoint"),
    [
        ("eastmoney_direct", "quote_valuation_snapshot"),
        ("baostock", "candidate_financials"),
        ("efinance", "candidate_enrichment"),
        ("tushare", "stock_basic"),
    ],
)
def test_t_pvd_008_disabled_fundamental_sources_are_config_blocked_without_network(
    provider: str,
    endpoint: str,
) -> None:
    called = False
    config = load_frontline_provider_config(_base_env())
    enabled_spec = next(
        spec
        for spec in load_fundamental_provider_specs(config)
        if spec.provider == provider and spec.endpoint == endpoint
    )
    disabled_spec = replace(enabled_spec, enabled=False)

    def _never_call(
        spec: ProviderSpec,
        query: ProviderQuery,
        context: ToolRuntimeContext,
        call_context: object,
    ) -> object:
        nonlocal called
        _ = spec, query, context, call_context
        called = True
        return {"rows": [{"unexpected": True}]}

    result = execute_provider_attempt(
        disabled_spec,
        _query(),
        _context(),
        call_registry={(disabled_spec.provider, disabled_spec.endpoint): _never_call},
    )

    assert called is False
    assert result.attempt.status == "config_blocked"
    assert result.attempt.error_code == "PROVIDER_NOT_ENABLED"


def test_t_pvd_008_company_info_schema_change_returns_schema_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    def _stock_individual_info_em(symbol: str, timeout: float) -> pd.DataFrame:
        _ = symbol, timeout
        return pd.DataFrame([{"字段A": "X", "字段B": "Y"}])

    fake_akshare = SimpleNamespace(stock_individual_info_em=_stock_individual_info_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_fundamental._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _fundamental_spec(endpoint="stock_individual_info_em")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_individual_info_em},
    )

    assert result.attempt.status == "schema_invalid"
    assert result.attempt.error_code == PROVIDER_SCHEMA_INVALID


def test_t_pvd_008_remaining_budget_passed_to_underlying_http(monkeypatch: pytest.MonkeyPatch) -> None:
    request_timeouts: list[float] = []

    class _FakeRequests:
        def get(self, _url: str, **kwargs: object) -> object:
            timeout = kwargs.get("timeout")
            assert isinstance(timeout, float)
            request_timeouts.append(timeout)
            return SimpleNamespace(text="")

    def _fake_stock_zh_a_spot_em() -> pd.DataFrame:
        requests.get("https://example.com/spot/a")  # type: ignore[name-defined]
        requests.get("https://example.com/spot/b", timeout=99.0)  # type: ignore[name-defined]
        return pd.DataFrame(
            [
                {"代码": "600519", "最新价": 1688.88, "总市值": 2123000000000.0, "市盈率-动态": 25.2, "市净率": 8.8}
            ]
        )

    _fake_stock_zh_a_spot_em.__globals__["requests"] = _FakeRequests()
    fake_akshare = SimpleNamespace(stock_zh_a_spot_em=_fake_stock_zh_a_spot_em)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_fundamental._load_akshare_module",
        lambda: fake_akshare,
    )

    call_akshare_stock_zh_a_spot_em(
        _fundamental_spec(endpoint="stock_zh_a_spot_em"),
        _query(),
        _context(),
        _fixed_call_context(timeout_ms=2500),
    )

    assert request_timeouts
    assert all(math.isclose(timeout, 2.5, rel_tol=0.0, abs_tol=1e-9) for timeout in request_timeouts)


def test_t_pvd_008_akshare_exception_returns_redacted_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _raise(symbol: str, timeout: float) -> pd.DataFrame:
        _ = symbol, timeout
        raise RuntimeError("token=secrettokenvalue1234567890 https://user:pass@example.com/path?token=abc123")

    fake_akshare = SimpleNamespace(stock_individual_info_em=_raise)
    monkeypatch.setattr(
        "frontline_data_pack.providers_akshare_fundamental._load_akshare_module",
        lambda: fake_akshare,
    )

    spec = _fundamental_spec(endpoint="stock_individual_info_em")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_akshare_stock_individual_info_em},
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


def _fundamental_spec(
    *,
    endpoint: str,
    timeout_ms: int = 1000,
) -> ProviderSpec:
    return ProviderSpec(
        domain="fundamental",
        priority="P0",
        provider="akshare",
        endpoint=endpoint,
        role="fundamental_role",
        enabled=True,
        mode="remote",
        timeout_ms=timeout_ms,
        required_for_complete=True,
        query_parameters=[
            ProviderQueryParameter(
                name="symbol",
                source="ticker_code_6",
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
        start_date="2026-04-01",
        end_date="2026-05-08",
        adjust=None,
        query_fingerprint=QUERY_FINGERPRINT,
    )


def _context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-pvd-008",
        stage="frontline",
        worker_id="fundamental_analyst",
        call_id="call-pvd-008",
        dispatch_id="dispatch-pvd-008",
        tool_name="fundamental_fundamentals_data_pack",
        evidence_root="viking://resources/workflow/run-pvd-008/frontline/fundamental_analyst/call-pvd-008/evidence",
        current_time="2026-05-08T12:00:00Z",
        current_date="2026-05-08",
    )


def _base_env() -> dict[str, str]:
    return {
        "CN_A_MONGODB_URI": "mongodb://localhost:27017/claw_trade",
        "CLAW_TRADE_OPENVIKING_BASE_URI": "https://openviking.internal",
        "CLAW_TRADE_OPENVIKING_AUTH_MODE": "none",
    }
