from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
SRC_ROOT = REPO_ROOT / "src"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

import frontline_data_pack.hk_data_pack as hk_data_pack  # noqa: E402


def test_hk_fundamental_uses_tushare_only_when_primary_covers_core(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(hk_data_pack, "_call_tushare_hk_basic", lambda _ts_code: _tushare_basic_frame())
    monkeypatch.setattr(
        hk_data_pack,
        "_call_tushare_hk_fina_indicator",
        lambda _ts_code, start_date, end_date: _tushare_fina_frame(pe_ttm=19.8, pb=4.2),
    )
    monkeypatch.setattr(hk_data_pack, "_call_akshare_hk_indicator", _unexpected_free_source)
    monkeypatch.setattr(hk_data_pack, "_call_akshare_hk_report_income", _unexpected_free_source)
    monkeypatch.setattr(hk_data_pack, "_call_yahoo_hk_fundamentals", _unexpected_free_source)

    pack = hk_data_pack.run_hk_fundamentals_data_pack(_tool_input(), _context(tmp_path))

    assert pack["quality"]["status"] == "complete"
    attempts = pack["provider_attempts"]
    assert [(item["provider"], item["endpoint"], item["status"]) for item in attempts] == [
        ("tushare_pro_hk", "hk_basic", "success"),
        ("tushare_pro_hk", "hk_fina_indicator", "success"),
    ]
    assert {item["provider_symbol"] for item in attempts} == {"00700.HK"}
    fields = pack["domain_data"]["fields"]
    assert fields["company_profile.company_name"]["provider"] == "tushare_pro_hk"
    assert fields["financial_indicators.roe"]["provider"] == "tushare_pro_hk"
    assert fields["valuation.pe_ttm"]["provider"] == "tushare_pro_hk"


def test_hk_fundamental_records_tushare_permission_failure_before_free_supplement(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(hk_data_pack, "_call_tushare_hk_basic", lambda _ts_code: _tushare_basic_frame())

    def _permission_denied(_ts_code: str, *, start_date: str, end_date: str) -> pd.DataFrame:
        _ = start_date, end_date
        raise RuntimeError("您没有该接口权限")

    monkeypatch.setattr(hk_data_pack, "_call_tushare_hk_fina_indicator", _permission_denied)
    monkeypatch.setattr(hk_data_pack, "_call_akshare_hk_indicator", lambda _symbol: _akshare_indicator_frame())
    monkeypatch.setattr(hk_data_pack, "_call_akshare_hk_report_income", lambda _symbol: _akshare_income_frame())
    monkeypatch.setattr(hk_data_pack, "_call_yahoo_hk_fundamentals", lambda _symbol: _yahoo_payload())

    pack = hk_data_pack.run_hk_fundamentals_data_pack(_tool_input(), _context(tmp_path))

    attempts = pack["provider_attempts"]
    hk_fina_attempt = next(item for item in attempts if item["endpoint"] == "hk_fina_indicator")
    assert hk_fina_attempt["provider"] == "tushare_pro_hk"
    assert hk_fina_attempt["status"] == "error"
    assert hk_fina_attempt["error_code"] == "PROVIDER_PERMISSION_DENIED"
    assert any(
        item["provider"] == "akshare_hk"
        and item["endpoint"] == "stock_financial_hk_analysis_indicator_em"
        and item["provider_symbol"] == "00700"
        and item["status"] == "success"
        for item in attempts
    )
    fields = pack["domain_data"]["fields"]
    assert fields["financial_indicators.roe"]["provider"] == "akshare_hk"
    assert fields["income_statement.revenue"]["provider"] == "akshare_hk"
    assert pack["quality"]["status"] == "complete"


def test_hk_fundamental_uses_yahoo_only_after_tushare_and_akshare_leave_gaps(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(hk_data_pack, "_call_tushare_hk_basic", lambda _ts_code: _tushare_basic_frame())
    monkeypatch.setattr(hk_data_pack, "_call_tushare_hk_fina_indicator", lambda *_args, **_kwargs: pd.DataFrame())
    monkeypatch.setattr(hk_data_pack, "_call_akshare_hk_indicator", lambda _symbol: pd.DataFrame())
    monkeypatch.setattr(hk_data_pack, "_call_akshare_hk_report_income", lambda _symbol: pd.DataFrame())
    monkeypatch.setattr(hk_data_pack, "_call_yahoo_hk_fundamentals", lambda _symbol: _yahoo_payload())

    pack = hk_data_pack.run_hk_fundamentals_data_pack(_tool_input(), _context(tmp_path))

    attempts = pack["provider_attempts"]
    assert attempts[-1]["provider"] == "yahoo_finance_hk"
    assert attempts[-1]["endpoint"] == "Ticker.info+financials"
    assert attempts[-1]["provider_symbol"] == "0700.HK"
    assert attempts[-1]["status"] == "success"
    fields = pack["domain_data"]["fields"]
    assert fields["valuation.pe_ttm"]["provider"] == "yahoo_finance_hk"
    assert fields["financial_indicators.roe"]["provider"] == "yahoo_finance_hk"


def _tool_input(**overrides: Any) -> dict[str, Any]:
    payload = {
        "ticker": "00700.HK",
        "market": "HK",
        "company_name": "腾讯控股",
        "start_date": "2025-01-01",
        "end_date": "2026-05-14",
    }
    payload.update(overrides)
    return payload


def _context(tmp_path: Path) -> dict[str, Any]:
    return {
        "run_id": "run-hk-fnd",
        "stage": "frontline",
        "worker_id": "fundamental_analyst",
        "call_id": "call-hk-fnd",
        "dispatch_id": "dispatch-hk-fnd",
        "tool_name": "fundamental_fundamentals_data_pack",
        "evidence_root": str(tmp_path),
        "current_time": "2026-05-14T16:00:00Z",
        "current_date": "2026-05-14",
    }


def _tushare_basic_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "00700.HK",
                "name": "腾讯控股",
                "fullname": "腾讯控股有限公司",
                "market": "主板",
                "list_date": "20040616",
                "trade_unit": 100,
                "curr_type": "HKD",
                "isin": "KYG875721634",
            }
        ]
    )


def _tushare_fina_frame(*, pe_ttm: float | None = None, pb: float | None = None) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "ts_code": "00700.HK",
                "name": "腾讯控股",
                "end_date": "20251231",
                "ann_date": "20260320",
                "currency": "HKD",
                "roe_avg": 21.13,
                "roa": 11.77,
                "grossprofit_margin": 56.21,
                "netprofit_margin": 30.57,
                "debt_to_assets": 39.13,
                "total_revenue": 751766000000,
                "n_income_attr_p": 224842000000,
                "basic_eps": 24.749,
                "eps_ttm": 24.653,
                "bps": 126.58,
                "pe_ttm": pe_ttm,
                "pb": pb,
            }
        ]
    )


def _akshare_indicator_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "SECUCODE": "00700.HK",
                "SECURITY_CODE": "00700",
                "SECURITY_NAME_ABBR": "腾讯控股",
                "REPORT_DATE": "2025-12-31 00:00:00",
                "CURRENCY": "HKD",
                "BASIC_EPS": 24.749,
                "EPS_TTM": 24.653,
                "BPS": 126.58,
                "OPERATE_INCOME": 751766000000,
                "HOLDER_PROFIT": 224842000000,
                "ROE_AVG": 21.13,
                "ROA": 11.77,
                "GROSS_PROFIT_RATIO": 56.21,
                "NET_PROFIT_RATIO": 30.57,
                "DEBT_ASSET_RATIO": 39.13,
            }
        ]
    )


def _akshare_income_frame() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"REPORT_DATE": "2025-12-31 00:00:00", "STD_ITEM_NAME": "营业额", "AMOUNT": 751766000000},
            {"REPORT_DATE": "2025-12-31 00:00:00", "STD_ITEM_NAME": "公司股东应占溢利", "AMOUNT": 224842000000},
        ]
    )


def _yahoo_payload() -> dict[str, Any]:
    return {
        "info": {
            "longName": "Tencent Holdings Limited",
            "currency": "HKD",
            "trailingPE": 18.5,
            "priceToBook": 4.1,
            "returnOnEquity": 0.211,
            "marketCap": 5200000000000,
        },
        "income_stmt": [
            {"metric": "Total Revenue", "2025-12-31": 751766000000},
            {"metric": "Net Income", "2025-12-31": 224842000000},
            {"metric": "Basic EPS", "2025-12-31": 24.749},
        ],
        "cashflow": [{"metric": "Operating Cash Flow", "2025-12-31": 303000000000}],
        "balance_sheet": [],
    }


def _unexpected_free_source(*_args: Any, **_kwargs: Any) -> pd.DataFrame:
    raise AssertionError("free supplement source must not be called when Tushare covers the core fields")
