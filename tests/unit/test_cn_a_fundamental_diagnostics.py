from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_ROOT = Path("agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts")


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


DIAGNOSTICS_MODULE = _load_script_module("diagnostics")


def test_missing_reason_codes_cover_required_dld_and_task_items() -> None:
    required = {
        "missing_token",
        "missing_token_or_disabled",
        "empty_response",
        "timeout",
        "schema_changed",
        "provider_error",
        "provider_disabled",
        "cache_stale",
        "cross_provider_conflict",
        "field_source_missing",
        "provider_attempts_missing",
    }
    assert required.issubset(set(DIAGNOSTICS_MODULE.MISSING_REASON_CODES))


def test_price_and_valuation_mark_stale_when_as_of_older_than_seven_days() -> None:
    checker = DIAGNOSTICS_MODULE.FreshnessChecker()
    field_sources = {
        "valuation.pe_ttm": {
            "as_of": "2026-04-20",
            "provider": "mongodb",
            "raw_payload_ref": "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/mongodb/daily_basic/1.json",
        }
    }

    freshness = checker.check(field_sources, "2026-05-07")
    record = freshness["valuation.pe_ttm"]

    assert record["is_stale"] is True
    assert record["reason"] == "cache_stale"
    assert record["age_days"] == 17
    assert record["domain"] == "valuation"
    assert any(item["detail"] == "stale_window_exceeded" for item in record["diagnostic_flags"])
    assert (
        field_sources["valuation.pe_ttm"]["raw_payload_ref"]
        == "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/mongodb/daily_basic/1.json"
    )


def test_date_parsing_supports_yyyymmdd_and_iso_datetime() -> None:
    checker = DIAGNOSTICS_MODULE.FreshnessChecker()
    field_sources = {
        "valuation.pb": {"as_of": "20260501", "provider": "tushare"},
        "price_context.close": {"as_of": "2026-05-03T15:00:00+08:00", "provider": "akshare"},
    }

    freshness = checker.check(field_sources, "2026-05-07")

    assert freshness["valuation.pb"]["is_stale"] is False
    assert freshness["valuation.pb"]["age_days"] == 6
    assert freshness["price_context.close"]["is_stale"] is False
    assert freshness["price_context.close"]["age_days"] == 4


def test_unparseable_as_of_emits_diagnostic_and_never_defaults_to_fresh() -> None:
    checker = DIAGNOSTICS_MODULE.FreshnessChecker()
    field_sources = {
        "valuation.total_mv": {
            "as_of": "2026/05/01",
            "provider": "tushare",
        }
    }

    freshness = checker.check(field_sources, "2026-05-07")
    record = freshness["valuation.total_mv"]

    assert record["is_stale"] is True
    assert record["reason"] == "schema_changed"
    assert any(
        item["code"] == "schema_changed" and item["detail"] == "as_of_unparseable"
        for item in record["diagnostic_flags"]
    )


def test_report_domain_keeps_field_source_and_marks_missing_as_of_unknown() -> None:
    checker = DIAGNOSTICS_MODULE.FreshnessChecker()
    field_sources = {
        "financial_indicators.roe": {
            "provider": "tushare",
            "raw_payload_ref": "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/tushare/fina_indicator/1.json",
        }
    }

    freshness = DIAGNOSTICS_MODULE.FreshnessChecker.Check(field_sources, "2026-05-07")
    record = freshness["financial_indicators.roe"]

    assert record["is_stale"] is True
    assert record["reason"] == "field_source_missing"
    assert record["domain"] == "financial_indicators"
    assert any(item["detail"] == "as_of_missing" for item in record["diagnostic_flags"])
    assert (
        field_sources["financial_indicators.roe"]["raw_payload_ref"]
        == "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/tushare/fina_indicator/1.json"
    )


def test_t_fnd_032_provider_attempts_missing_forces_failed_status() -> None:
    mapped = _build_mapped_fixture(with_supplemental=True, with_periods=True)
    result = DIAGNOSTICS_MODULE.MissingFieldDiagnostics.Compute(mapped, _freshness_for(mapped), [])

    assert result.ok is False
    assert result.quality["status"] == "failed"
    assert any(item["reason"] == "provider_attempts_missing" for item in result.missing_fields)


def test_t_fnd_032_missing_field_source_for_non_empty_fact_forces_failed_status() -> None:
    mapped = _build_mapped_fixture(with_supplemental=True, with_periods=True)
    del mapped.field_sources["valuation.pb"]
    result = DIAGNOSTICS_MODULE.MissingFieldDiagnostics.Compute(
        mapped,
        _freshness_for(mapped),
        [_provider_attempt()],
    )

    assert result.ok is False
    assert result.quality["status"] == "failed"
    assert any(
        item["reason"] == "field_source_missing" and item["field_path"] == "valuation.pb"
        for item in result.missing_fields
    )


def test_t_fnd_032_stale_core_field_does_not_count_toward_threshold() -> None:
    mapped = _build_mapped_fixture(with_supplemental=True, with_periods=True)
    freshness = _freshness_for(mapped)
    freshness["valuation.pe_ttm"]["is_stale"] = True
    freshness["financial_indicators.roe"]["is_stale"] = True

    result = DIAGNOSTICS_MODULE.MissingFieldDiagnostics.Compute(
        mapped,
        freshness,
        [_provider_attempt()],
    )

    assert result.ok is False
    assert result.quality["status"] == "failed"
    assert result.completeness.core_thresholds["valuation"] is False


def test_cn_a_fundamental_complete_when_pe_pb_roe_and_revenue_net_profit_present() -> None:
    mapped = _build_mapped_fixture(with_supplemental=True, with_periods=True)
    completeness = DIAGNOSTICS_MODULE.EvaluateFundamentalCompleteness(mapped, _freshness_for(mapped))
    assert completeness.is_complete is True


def test_cn_a_fundamental_complete_when_pe_pb_roe_and_operating_cash_flow_present() -> None:
    mapped = _build_mapped_fixture(
        with_supplemental=True,
        with_periods=False,
        with_net_profit=False,
    )
    completeness = DIAGNOSTICS_MODULE.EvaluateFundamentalCompleteness(mapped, _freshness_for(mapped))
    assert completeness.is_complete is True


def test_cn_a_fundamental_not_complete_when_any_pe_pb_roe_missing() -> None:
    mapped = _build_mapped_fixture(with_supplemental=True, with_periods=True, with_pb=False)
    completeness = DIAGNOSTICS_MODULE.EvaluateFundamentalCompleteness(mapped, _freshness_for(mapped))
    assert completeness.is_complete is False


def test_cn_a_fundamental_not_complete_when_income_and_cash_flow_groups_both_missing() -> None:
    mapped = _build_mapped_fixture(
        with_supplemental=True,
        with_periods=True,
        with_revenue=False,
        with_net_profit=False,
        with_operating_cash_flow=False,
    )
    completeness = DIAGNOSTICS_MODULE.EvaluateFundamentalCompleteness(mapped, _freshness_for(mapped))
    assert completeness.is_complete is False


def test_t_fnd_045_tushare_skipped_marks_missing_token_or_disabled_in_missing_fields() -> None:
    mapped = _build_mapped_fixture(with_supplemental=False, with_periods=False)
    result = DIAGNOSTICS_MODULE.MissingFieldDiagnostics.Compute(
        mapped,
        _freshness_for(mapped),
        [_provider_attempt(provider="tushare", status="skipped", reason="missing_token_or_disabled")],
    )
    assert any(
        item["field_path"] == "provider.tushare" and item["reason"] == "missing_token_or_disabled"
        for item in result.missing_fields
    )


def test_t_fnd_045_akshare_disabled_marks_provider_disabled_in_missing_fields() -> None:
    mapped = _build_mapped_fixture(with_supplemental=False, with_periods=False)
    result = DIAGNOSTICS_MODULE.MissingFieldDiagnostics.Compute(
        mapped,
        _freshness_for(mapped),
        [_provider_attempt(provider="akshare", status="skipped", reason="provider_disabled")],
    )
    assert any(
        item["field_path"] == "provider.akshare" and item["reason"] == "provider_disabled"
        for item in result.missing_fields
    )


def _build_mapped_fixture(
    *,
    with_supplemental: bool,
    with_periods: bool,
    with_pe: bool = True,
    with_pb: bool = True,
    with_roe: bool = True,
    with_revenue: bool = True,
    with_net_profit: bool = True,
    with_operating_cash_flow: bool = True,
) -> SimpleNamespace:
    facts_by_path: dict[str, object] = {
        "company_profile.name": "贵州茅台",
        "price_context.close": 1500.0,
        "valuation.total_mv": 2.4e12,
        "financial_indicators.roa": 0.18,
        "financial_indicators.gross_margin": 0.90,
        "financial_indicators.netprofit_margin": 0.52,
        "financial_indicators.debt_to_assets": 0.20,
        "balance_sheet.total_assets": 3000.0,
    }
    if with_pe:
        facts_by_path["valuation.pe_ttm"] = 21.2
    if with_pb:
        facts_by_path["valuation.pb"] = 5.8
    if with_roe:
        facts_by_path["financial_indicators.roe"] = 0.31
    if with_revenue:
        facts_by_path["income_statement.revenue"] = 1500.0
    if with_net_profit:
        facts_by_path["income_statement.net_profit"] = 820.0
    if with_operating_cash_flow:
        facts_by_path["cash_flow.operating_cash_flow"] = 780.0
    if with_supplemental:
        facts_by_path["business_segments"] = [{"bz_item": "白酒", "bz_sales": 1200.0}]
        facts_by_path["dividend"] = [{"ann_date": "2026-04-01", "cash_div_tax": 27.6}]
        facts_by_path["shareholders.top10_holders"] = [{"holder_name": "A", "hold_ratio": 12.0}]
    field_sources = {path: {"provider": "tushare", "as_of": "2026-05-07"} for path in facts_by_path.keys()}
    mapped = SimpleNamespace(
        facts_by_path=facts_by_path,
        field_sources=field_sources,
        missing_fields=[],
        diagnostic_flags=[],
        evidence_capability_impacts=[],
    )
    if with_periods:
        mapped.periods = ["2024-12-31", "2025-12-31"]
    return mapped


def _freshness_for(mapped: SimpleNamespace) -> dict[str, dict[str, object]]:
    freshness: dict[str, dict[str, object]] = {}
    for field_path in mapped.field_sources.keys():
        freshness[field_path] = {
            "is_stale": False,
            "reason": None,
            "as_of": "2026-05-07",
            "age_days": 0,
            "domain": field_path.split(".", 1)[0],
            "diagnostic_flags": [],
        }
    return freshness


def _provider_attempt(
    *,
    provider: str = "tushare",
    status: str = "success",
    reason: str | None = None,
) -> dict[str, object]:
    return {
        "provider": provider,
        "role": "primary" if provider == "tushare" else "supplement",
        "api_name": "fina_indicator" if provider == "tushare" else "stock_financial_abstract_ths",
        "status": status,
        "reason": reason,
    }
