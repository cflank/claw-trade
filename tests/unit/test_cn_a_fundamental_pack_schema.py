from __future__ import annotations

import importlib.util
import sys
from dataclasses import fields
from pathlib import Path


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


MODELS_MODULE = _load_script_module("models")
PACK_SCHEMA_MODULE = _load_script_module("pack_schema")


def test_pack_schema_has_required_fixed_fields() -> None:
    profile_currency_field = next(item for item in fields(PACK_SCHEMA_MODULE.ProfileInfo) if item.name == "currency")
    pack_schema_field = next(item for item in fields(PACK_SCHEMA_MODULE.DataPack) if item.name == "schema_version")

    assert profile_currency_field.init is False
    assert pack_schema_field.init is False
    assert pack_schema_field.default == "cn_a_fundamental_pack.v1"


def test_pack_schema_dataclasses_to_dict_and_forbidden_fields() -> None:
    attempt = MODELS_MODULE.ProviderAttempt(
        provider="tushare",
        role="primary",
        api_name="fina_indicator",
        attempt_seq=1,
        status="success",
        reason=None,
        started_at="2026-05-07T00:00:00+00:00",
        ended_at="2026-05-07T00:00:01+00:00",
        duration_ms=1000,
        retry_count=0,
        request_params_redacted={"ts_code": "600519.SH"},
        response_row_count=1,
        response_col_count=3,
        field_coverage=["facts.financial_indicators.roe"],
        report_period="20251231",
        announce_date="2026-03-31",
        as_of="2026-05-07",
        fetched_at="2026-05-07T00:00:01+00:00",
        raw_payload_hash="sha256:abc",
        raw_payload_ref="viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/tushare/fina_indicator/1.json",
        error_type=None,
        error_message_redacted=None,
    )
    pack = PACK_SCHEMA_MODULE.DataPack(
        ok=True,
        profile=PACK_SCHEMA_MODULE.ProfileInfo(
            ticker="600519",
            canonical_code="600519.SH",
            market="CN_A",
            company_name="贵州茅台",
        ),
        query=PACK_SCHEMA_MODULE.QueryInfo(
            start_date="2026-01-01",
            end_date="2026-05-07",
            current_date="2026-05-07",
        ),
        facts=PACK_SCHEMA_MODULE.Facts(
            company_profile=PACK_SCHEMA_MODULE.CompanyProfileFacts(
                name="贵州茅台",
                industry="白酒",
                main_business="茅台酒及系列酒生产销售",
            ),
            price_context=PACK_SCHEMA_MODULE.PriceContextFacts(
                close=1700.0,
                trade_date="20260507",
                volume=1234567.0,
            ),
            valuation=PACK_SCHEMA_MODULE.ValuationFacts(pe_ttm=21.2, pb=5.8, total_mv=2.4e12),
            financial_indicators=PACK_SCHEMA_MODULE.FinancialIndicatorsFacts(
                roe=0.31,
                roa=0.18,
                gross_margin=0.9,
                netprofit_margin=0.52,
                debt_to_assets=0.2,
            ),
            income_statement=PACK_SCHEMA_MODULE.IncomeStatementFacts(revenue=1500.0, net_profit=700.0, eps=30.2),
            balance_sheet=PACK_SCHEMA_MODULE.BalanceSheetFacts(
                total_assets=3000.0,
                total_liabilities=600.0,
                total_equity=2400.0,
            ),
            cash_flow=PACK_SCHEMA_MODULE.CashFlowFacts(operating_cash_flow=780.0),
            business_segments=[{"bz_item": "白酒", "bz_sales": 1200.0, "bz_profit": 650.0}],
            dividend=[{"ann_date": "2026-04-01", "cash_div_tax": 27.6}],
            shareholders={"top10_holders": [{"holder_name": "A", "hold_ratio": 12.0}]},
        ),
        field_sources={
            "facts.valuation.pe_ttm": {
                "provider": "tushare",
                "api_name": "daily_basic",
                "source_ref": "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/tushare/daily_basic/1.json",
            }
        },
        provider_attempts=[attempt],
        missing_fields=[],
        freshness={
            "facts.valuation.pe_ttm": {
                "is_fresh": True,
                "as_of": "2026-05-07",
                "window_days": "7",
            }
        },
        evidence_capabilities={"valuation_snapshot": {"status": "available", "blocked_claims": []}},
        diagnostic_flags=[],
        derived_summary=[
            PACK_SCHEMA_MODULE.DerivedSummaryItem(
                template_id="valuation_snapshot_obtained",
                text="pe_ttm来自daily_basic，日期为2026-05-07。",
                source_ref="field_sources.facts.valuation.pe_ttm",
                inputs={"metric_name": "pe_ttm", "as_of_date": "2026-05-07", "provider_api": "daily_basic"},
            )
        ],
        quality=PACK_SCHEMA_MODULE.DataPackQuality(status="ok", is_partial=False, warnings=[]),
        evidence=PACK_SCHEMA_MODULE.DataPackEvidence(
            raw_payload_refs=[
                "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/tushare/daily_basic/1.json"
            ],
            content_hash="sha256:def",
        ),
    )

    payload = pack.to_dict()

    assert payload["schema_version"] == "cn_a_fundamental_pack.v1"
    assert payload["profile"]["currency"] == "CNY"
    assert payload["facts"]["company_profile"]["industry"] == "白酒"
    assert payload["facts"]["price_context"]["trade_date"] == "20260507"
    assert "analysis_permissions" not in payload
    assert "rating" not in payload
    assert "target_price" not in payload
    assert "investment_advice" not in payload


def test_to_canonical_value_sorts_dict_keys() -> None:
    canonical = PACK_SCHEMA_MODULE.to_canonical_value({"b": 2, "a": {"d": 4, "c": 3}})
    assert list(canonical.keys()) == ["a", "b"]
    assert list(canonical["a"].keys()) == ["c", "d"]
