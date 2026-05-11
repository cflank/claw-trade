from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPTS_ROOT = Path("agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts")


def test_t_fnd_014_provider_allowlist_is_fixed_and_excludes_forbidden_providers() -> None:
    tushare_names = PROVIDER_SPECS_MODULE.list_tushare_v1_api_names()
    akshare_names = PROVIDER_SPECS_MODULE.list_akshare_v1_api_names()
    assert len(tushare_names) == 11
    assert len(akshare_names) == 5
    assert "stock_company" in tushare_names
    assert "top10_floatholders" in tushare_names
    assert "stock_zh_a_hist" in akshare_names
    assert "stock_history_dividend_detail" in akshare_names

    lowered = {name.lower() for name in (*tushare_names, *akshare_names)}
    assert all("baostock" not in name for name in lowered)
    assert all("financemcp" not in name for name in lowered)


def test_t_fnd_014_disallow_dynamic_worker_api_name() -> None:
    with pytest.raises(PROVIDER_SPECS_MODULE.ProviderSpecError) as exc_info:
        PROVIDER_SPECS_MODULE.assert_no_dynamic_worker_api_name("daily_basic")
    assert exc_info.value.code == PROVIDER_SPECS_MODULE.FND_PROVIDER_DYNAMIC_API_FORBIDDEN


def test_t_fnd_014_render_tushare_and_akshare_specs_from_templates() -> None:
    normalized = MODELS_MODULE.NormalizedInput(
        raw_ticker="600519",
        canonical_code="600519.SH",
        tushare_code="600519.SH",
        akshare_symbol="600519",
        exchange="SH",
        market="CN_A",
        start_date="2025-05-07",
        end_date="2026-05-07",
        current_date="2026-05-07",
        run_id="run-1",
        dispatch_id="dispatch-1",
        latest_report_period="20260331",
    )
    tushare_spec = PROVIDER_SPECS_MODULE.render_tushare_spec("daily_basic", normalized)
    assert tushare_spec.api_name == "daily_basic"
    assert tushare_spec.parameters["trade_date"] == "20260507"
    assert tushare_spec.parameters["ts_code"] == "600519.SH"
    assert tushare_spec.field_family == "price_context,valuation"

    akshare_spec = PROVIDER_SPECS_MODULE.render_akshare_spec("stock_zh_a_hist", normalized)
    assert akshare_spec.parameters["symbol"] == "600519"
    assert akshare_spec.parameters["start_date"] == "20250507"
    assert akshare_spec.parameters["end_date"] == "20260507"
    assert akshare_spec.timeout_ms == 18000


def test_t_fnd_014_daily_basic_does_not_require_start_end_dates() -> None:
    normalized = MODELS_MODULE.NormalizedInput(
        raw_ticker="600519",
        canonical_code="600519.SH",
        tushare_code="600519.SH",
        akshare_symbol="600519",
        exchange="SH",
        market="CN_A",
        start_date=None,
        end_date=None,
        current_date="2026-05-07",
        run_id="run-1",
        dispatch_id="dispatch-1",
        latest_report_period="20260331",
    )
    spec = PROVIDER_SPECS_MODULE.render_tushare_spec("daily_basic", normalized)
    assert spec.parameters["trade_date"] == "20260507"
    assert spec.parameters["ts_code"] == "600519.SH"


def test_t_fnd_014_hist_requires_start_end_dates() -> None:
    normalized = MODELS_MODULE.NormalizedInput(
        raw_ticker="600519",
        canonical_code="600519.SH",
        tushare_code="600519.SH",
        akshare_symbol="600519",
        exchange="SH",
        market="CN_A",
        start_date=None,
        end_date=None,
        current_date="2026-05-07",
        run_id="run-1",
        dispatch_id="dispatch-1",
        latest_report_period="20260331",
    )
    with pytest.raises(PROVIDER_SPECS_MODULE.ProviderSpecError) as exc_info:
        PROVIDER_SPECS_MODULE.render_akshare_spec("stock_zh_a_hist", normalized)
    assert exc_info.value.code == PROVIDER_SPECS_MODULE.FND_PROVIDER_SPEC_INVALID


@pytest.mark.parametrize(
    ("raw_ticker", "expected_code", "expected_exchange"),
    [
        ("600519", "600519.SH", "SH"),
        ("600519.SH", "600519.SH", "SH"),
        ("SH600519", "600519.SH", "SH"),
        ("000001", "000001.SZ", "SZ"),
        ("000001.SZ", "000001.SZ", "SZ"),
        ("SZ000001", "000001.SZ", "SZ"),
    ],
)
def test_t_fnd_015_ticker_normalization_supports_required_formats(
    raw_ticker: str,
    expected_code: str,
    expected_exchange: str,
) -> None:
    normalized = PROFILE_MODULE.NormalizeCnATicker(raw_ticker)
    assert normalized.canonical_code == expected_code
    assert normalized.tushare_code == expected_code
    assert normalized.akshare_symbol == expected_code.split(".")[0]
    assert normalized.exchange == expected_exchange


def test_t_fnd_015_normalize_input_enforces_worker_and_market() -> None:
    bad_worker = MODELS_MODULE.DataPackRequest(
        ticker="600519",
        start_date=None,
        end_date=None,
        current_date="2026-05-07",
        run_id="run-1",
        dispatch_id="dispatch-1",
        worker_id="news_analyst",  # type: ignore[arg-type]
        market="CN_A",
    )
    with pytest.raises(PROFILE_MODULE.FundamentalProfileError) as worker_exc:
        PROFILE_MODULE.NormalizeInput(bad_worker)
    assert worker_exc.value.code == PROFILE_MODULE.FND_PROFILE_WORKER_MISMATCH

    bad_market = MODELS_MODULE.DataPackRequest(
        ticker="600519",
        start_date=None,
        end_date=None,
        current_date="2026-05-07",
        run_id="run-1",
        dispatch_id="dispatch-1",
        worker_id="fundamental_analyst",
        market="US",  # type: ignore[arg-type]
    )
    with pytest.raises(PROFILE_MODULE.FundamentalProfileError) as market_exc:
        PROFILE_MODULE.NormalizeInput(bad_market)
    assert market_exc.value.code == PROFILE_MODULE.FND_PROFILE_MARKET_MISMATCH


def test_t_fnd_015_date_window_reverse_range_must_fail() -> None:
    request = MODELS_MODULE.DataPackRequest(
        ticker="600519",
        start_date="2026-05-08",
        end_date="2026-05-07",
        current_date="2026-05-07",
        run_id="run-1",
        dispatch_id="dispatch-1",
        worker_id="fundamental_analyst",
        market="CN_A",
    )
    with pytest.raises(PROFILE_MODULE.FundamentalProfileError) as exc_info:
        PROFILE_MODULE.NormalizeInput(request)
    assert exc_info.value.code == PROFILE_MODULE.FND_PROFILE_DATE_RANGE_REVERSED


def test_t_fnd_015_preserves_missing_start_end_and_sets_latest_report_period() -> None:
    request = MODELS_MODULE.DataPackRequest(
        ticker="600519",
        start_date=None,
        end_date=None,
        current_date="2026-05-07",
        run_id="run-1",
        dispatch_id="dispatch-1",
        worker_id="fundamental_analyst",
        market="CN_A",
    )
    normalized = PROFILE_MODULE.NormalizeInput(request)
    assert normalized.canonical_code == "600519.SH"
    assert normalized.start_date is None
    assert normalized.end_date is None
    assert normalized.current_date == "2026-05-07"
    assert normalized.latest_report_period == "20260331"

    january_window = PROFILE_MODULE.ResolveDateWindow(
        start_date="2025-01-01",
        end_date="2026-01-10",
        current_date="2026-01-10",
    )
    assert january_window.latest_report_period == "20251231"


def test_t_fnd_015_keeps_explicit_start_and_end_dates() -> None:
    request = MODELS_MODULE.DataPackRequest(
        ticker="600519",
        start_date="2025-05-07",
        end_date="2026-05-07",
        current_date="2026-05-07",
        run_id="run-1",
        dispatch_id="dispatch-1",
        worker_id="fundamental_analyst",
        market="CN_A",
    )
    normalized = PROFILE_MODULE.NormalizeInput(request)
    assert normalized.start_date == "2025-05-07"
    assert normalized.end_date == "2026-05-07"

    hist_spec = PROVIDER_SPECS_MODULE.render_akshare_spec("stock_zh_a_hist", normalized)
    assert hist_spec.parameters["start_date"] == "20250507"
    assert hist_spec.parameters["end_date"] == "20260507"


def _load_script_module(module_basename: str):
    scripts_path = str(SCRIPTS_ROOT.resolve())
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    module_path = SCRIPTS_ROOT / f"{module_basename}.py"
    spec = importlib.util.spec_from_file_location(f"cn_a_fundamental_{module_basename}", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"cn_a_fundamental_{module_basename}"] = module
    spec.loader.exec_module(module)
    return module


MODELS_MODULE = _load_script_module("models")
PROFILE_MODULE = _load_script_module("profile")
PROVIDER_SPECS_MODULE = _load_script_module("provider_specs")
