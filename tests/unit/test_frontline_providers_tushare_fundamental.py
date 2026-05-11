from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
SRC_ROOT = REPO_ROOT / "src"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from claw_trade.providers.tushare_client import TushareClientConfigError  # noqa: E402
from frontline_data_pack.models import ProviderQuery, ProviderSpec  # noqa: E402
from frontline_data_pack.provider_executor import execute_provider_attempt  # noqa: E402
from frontline_data_pack.providers_tushare_fundamental import (  # noqa: E402
    call_tushare_cashflow,
    call_tushare_daily_basic,
    call_tushare_fina_indicator,
    call_tushare_income,
    call_tushare_stock_basic,
)
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


QUERY_FINGERPRINT = "sha256:" + ("f" * 64)


def test_tushare_fundamental_maps_core_fields(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pro = _FakePro()
    monkeypatch.setattr(
        "frontline_data_pack.providers_tushare_fundamental.create_tushare_pro",
        lambda: fake_pro,
    )

    cases = [
        (_spec("stock_basic", "company_profile"), call_tushare_stock_basic),
        (_spec("daily_basic", "valuation_market_cap"), call_tushare_daily_basic),
        (_spec("fina_indicator", "financial_indicators"), call_tushare_fina_indicator),
        (_spec("income", "income_statement"), call_tushare_income),
        (_spec("cashflow", "cash_flow"), call_tushare_cashflow),
    ]

    rows: list[dict[str, object]] = []
    for spec, caller in cases:
        result = execute_provider_attempt(
            spec,
            _query(),
            _context(),
            call_registry={(spec.provider, spec.endpoint): caller},
        )
        assert result.attempt.status == "success"
        assert result.attempt.accepted_count >= 1
        rows.extend(result.normalized_rows)

    fields = set()
    for row in rows:
        field_name = row.get("field_name")
        if isinstance(field_name, str):
            fields.add(field_name)
            continue
        fields.update(row.keys())
    assert "company_profile.industry" in fields
    assert "valuation.pe_ttm" in fields
    assert "valuation.pb" in fields
    assert "financial_indicators.roe" in fields
    assert "financial_indicators.roa" in fields
    assert "financial_indicators.gross_margin" in fields
    assert "financial_indicators.netprofit_margin" in fields
    assert "financial_indicators.debt_to_assets" in fields
    assert "income_statement.revenue" in fields
    assert "income_statement.net_profit" in fields
    assert "cash_flow.operating_cash_flow" in fields
    assert fake_pro.calls["daily_basic"][0]["start_date"] == "20260501"
    assert fake_pro.calls["daily_basic"][0]["end_date"] == "20260509"
    assert fake_pro.calls["income"][0]["period"] == "20260331"


def test_tushare_fundamental_missing_token_is_explicit_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "frontline_data_pack.providers_tushare_fundamental.create_tushare_pro",
        lambda: (_ for _ in ()).throw(TushareClientConfigError("TUSHARE_TOKEN missing")),
    )

    spec = _spec("daily_basic", "valuation_market_cap")
    result = execute_provider_attempt(
        spec,
        _query(),
        _context(),
        call_registry={(spec.provider, spec.endpoint): call_tushare_daily_basic},
    )
    assert result.attempt.status == "error"
    assert result.attempt.error_code == "PROVIDER_KEY_MISSING"


class _FakePro:
    def __init__(self) -> None:
        self.calls: dict[str, list[dict[str, object]]] = {}

    def stock_basic(self, **kwargs: object) -> pd.DataFrame:
        self._record("stock_basic", kwargs)
        return pd.DataFrame(
            [
                {
                    "ts_code": "600519.SH",
                    "name": "贵州茅台",
                    "industry": "白酒",
                    "market": "主板",
                    "list_date": "20010827",
                }
            ]
        )

    def daily_basic(self, **kwargs: object) -> pd.DataFrame:
        self._record("daily_basic", kwargs)
        return pd.DataFrame(
            [
                {"ts_code": "600519.SH", "trade_date": "20260507", "close": 1390.0, "pe_ttm": 20.1, "pb": 6.2, "total_mv": 170000000.0},
                {"ts_code": "600519.SH", "trade_date": "20260508", "close": 1372.99, "pe_ttm": 19.8, "pb": 6.1, "total_mv": 168000000.0},
            ]
        )

    def fina_indicator(self, **kwargs: object) -> pd.DataFrame:
        self._record("fina_indicator", kwargs)
        return pd.DataFrame(
            [
                {
                    "ts_code": "600519.SH",
                    "end_date": "20260331",
                    "ann_date": "20260425",
                    "roe": 8.5,
                    "roa": 4.2,
                    "grossprofit_margin": 89.7592,
                    "netprofit_margin": 52.2245,
                    "debt_to_assets": 12.1227,
                },
            ]
        )

    def income(self, **kwargs: object) -> pd.DataFrame:
        self._record("income", kwargs)
        return pd.DataFrame(
            [
                {
                    "ts_code": "600519.SH",
                    "end_date": "20260331",
                    "ann_date": "20260425",
                    "revenue": 54703000000.0,
                    "n_income": 27243000000.0,
                    "basic_eps": 21.69,
                },
            ]
        )

    def cashflow(self, **kwargs: object) -> pd.DataFrame:
        self._record("cashflow", kwargs)
        return pd.DataFrame(
            [
                {"ts_code": "600519.SH", "end_date": "20260331", "ann_date": "20260425", "n_cashflow_act": 30000000000.0},
            ]
        )

    def _record(self, api_name: str, kwargs: dict[str, object]) -> None:
        self.calls.setdefault(api_name, []).append(dict(kwargs))


def _spec(endpoint: str, role: str) -> ProviderSpec:
    return ProviderSpec(
        domain="fundamental",
        priority="P0",
        provider="tushare",
        endpoint=endpoint,
        role=role,
        enabled=True,
        mode="remote",
        timeout_ms=1000,
        required_for_complete=True,
        query_parameters=[],
    )


def _query() -> ProviderQuery:
    return ProviderQuery(
        market="CN_A",
        ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-09",
        adjust=None,
        query_fingerprint=QUERY_FINGERPRINT,
    )


def _context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-tushare-fundamental",
        stage="frontline",
        worker_id="fundamental_analyst",
        call_id="call-tushare-fundamental",
        dispatch_id="dispatch-tushare-fundamental",
        tool_name="fundamental_fundamentals_data_pack",
        evidence_root="/tmp/evidence",
        current_time="2026-05-09T12:00:00Z",
        current_date="2026-05-09",
    )
