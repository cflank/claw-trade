from __future__ import annotations

import hashlib
import importlib.util
import json
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


MODELS_MODULE = _load_script_module("models")
PACK_SCHEMA_MODULE = _load_script_module("pack_schema")
BUILDER_MODULE = _load_script_module("data_pack_builder")


def test_t_fnd_036_build_composes_required_sections_and_collects_sorted_raw_refs() -> None:
    normalized = _normalized_input()
    mapped = _mapped_ok_fixture()
    diagnostics = {
        "ok": True,
        "quality": {"status": "partial", "is_partial": True, "warnings": ["trend_not_ready"]},
        "missing_fields": [],
        "diagnostic_flags": [],
    }
    derived_summary_result = {
        "valid": True,
        "items": [
            PACK_SCHEMA_MODULE.DerivedSummaryItem(
                template_id="valuation_snapshot_obtained",
                text="pe_ttm来自daily_basic，日期为2026-05-07。",
                source_ref="field_sources.valuation.pe_ttm",
                inputs={"metric_name": "pe_ttm", "as_of_date": "2026-05-07", "provider_api": "daily_basic"},
            )
        ],
        "diagnostic_flags": [],
    }
    pack = BUILDER_MODULE.DataPackBuilder.Build(
        normalized_input=normalized,
        mapped=mapped,
        freshness={"valuation.pe_ttm": {"is_stale": False, "as_of": "2026-05-07"}},
        diagnostics=diagnostics,
        evidence_capabilities={"valuation_snapshot": {"status": "available", "blocked_claims": []}},
        derived_summary_result=derived_summary_result,
        provider_results=[_provider_result_attempt_seq_1()],
        cache_result=SimpleNamespace(attempt=_cache_attempt()),
    )

    payload = pack.to_dict()
    required_sections = {
        "profile",
        "query",
        "facts",
        "field_sources",
        "provider_attempts",
        "missing_fields",
        "freshness",
        "evidence_capabilities",
        "diagnostic_flags",
        "derived_summary",
        "quality",
        "evidence",
        "schema_version",
    }
    assert required_sections.issubset(set(payload.keys()))
    assert pack.ok is True
    assert pack.quality.status == "partial"
    assert pack.evidence.raw_payload_refs == [
        _raw_ref("akshare", "stock_zh_a_hist"),
        _raw_ref("cache", "mongodb/inspect"),
        _raw_ref("tushare", "balancesheet"),
        _raw_ref("tushare", "cashflow"),
        _raw_ref("tushare", "daily_basic"),
        _raw_ref("tushare", "fina_indicator"),
        _raw_ref("tushare", "income"),
        _raw_ref("tushare", "stock_basic"),
        _raw_ref("tushare", "stock_company"),
    ]
    assert pack.evidence.content_hash.startswith("sha256:")
    assert len(pack.evidence.content_hash) == 71
    assert pack.facts.company_profile.main_business == "高端白酒生产与销售"
    assert pack.facts.price_context.trade_date == "20260507"
    assert pack.facts.price_context.volume == 1234567.0


def test_t_fnd_036_non_empty_fact_without_field_source_forces_failed() -> None:
    normalized = _normalized_input()
    mapped = _mapped_ok_fixture()
    mapped.facts_by_path["valuation.pb"] = 5.8
    pack = BUILDER_MODULE.DataPackBuilder().build(
        normalized_input=normalized,
        mapped=mapped,
        freshness={},
        diagnostics={
            "ok": True,
            "quality": {"status": "ok", "is_partial": False, "warnings": []},
            "missing_fields": [],
            "diagnostic_flags": [],
        },
        evidence_capabilities={},
        derived_summary_result={"valid": True, "items": [], "diagnostic_flags": []},
        provider_results=[_provider_result_attempt_seq_1()],
        cache_result=SimpleNamespace(attempt=_cache_attempt()),
    )

    assert pack.ok is False
    assert pack.quality.status == "failed"
    assert any(item.get("reason") == "field_source_missing" for item in pack.missing_fields)
    assert any(item.get("code") == "field_source_missing" for item in pack.diagnostic_flags)


def test_t_fnd_036_invalid_ok_status_combo_forces_failed() -> None:
    pack = BUILDER_MODULE.DataPackBuilder().build(
        normalized_input=_normalized_input(),
        mapped=_mapped_ok_fixture(),
        freshness={},
        diagnostics={
            "ok": False,
            "quality": {"status": "ok", "is_partial": False, "warnings": []},
            "missing_fields": [],
            "diagnostic_flags": [],
        },
        evidence_capabilities={},
        derived_summary_result={"valid": True, "items": [], "diagnostic_flags": []},
        provider_results=[_provider_result_attempt_seq_1()],
        cache_result=SimpleNamespace(attempt=_cache_attempt()),
    )

    assert pack.ok is False
    assert pack.quality.status == "failed"
    assert any(item.get("code") == "invalid_quality_combo" for item in pack.diagnostic_flags)


def test_t_fnd_036_invalid_derived_summary_clears_items_and_fails_pack() -> None:
    pack = BUILDER_MODULE.DataPackBuilder().build(
        normalized_input=_normalized_input(),
        mapped=_mapped_ok_fixture(),
        freshness={},
        diagnostics={
            "ok": True,
            "quality": {"status": "ok", "is_partial": False, "warnings": []},
            "missing_fields": [],
            "diagnostic_flags": [],
        },
        evidence_capabilities={},
        derived_summary_result={
            "valid": False,
            "items": [
                {
                    "template_id": "valuation_snapshot_obtained",
                    "text": "无效项",
                    "source_ref": "field_sources.valuation.pe_ttm",
                    "inputs": {"metric_name": "pe_ttm"},
                }
            ],
            "diagnostic_flags": [{"code": "derived_summary_invalid", "severity": "fail", "reason": "forbidden_claim_word"}],
        },
        provider_results=[_provider_result_attempt_seq_1()],
        cache_result=SimpleNamespace(attempt=_cache_attempt()),
    )

    assert pack.ok is False
    assert pack.quality.status == "failed"
    assert pack.derived_summary == []
    assert any(item.get("code") == "derived_summary_invalid" for item in pack.diagnostic_flags)


def test_t_fnd_036_content_hash_uses_canonical_json_without_self_hash_input() -> None:
    pack = BUILDER_MODULE.DataPackBuilder().build(
        normalized_input=_normalized_input(),
        mapped=_mapped_ok_fixture(),
        freshness={"valuation.pe_ttm": {"is_stale": False, "as_of": "2026-05-07"}},
        diagnostics={
            "ok": True,
            "quality": {"status": "ok", "is_partial": False, "warnings": []},
            "missing_fields": [],
            "diagnostic_flags": [],
        },
        evidence_capabilities={"valuation_snapshot": {"status": "available", "blocked_claims": []}},
        derived_summary_result={"valid": True, "items": [], "diagnostic_flags": []},
        provider_results=[_provider_result_attempt_seq_1()],
        cache_result=SimpleNamespace(attempt=_cache_attempt()),
    )

    payload = pack.to_dict()
    payload["evidence"]["content_hash"] = ""
    expected = "sha256:" + hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert pack.evidence.content_hash == expected


def _normalized_input():
    return MODELS_MODULE.NormalizedInput(
        raw_ticker="600519",
        canonical_code="600519.SH",
        tushare_code="600519.SH",
        akshare_symbol="600519",
        exchange="SH",
        market="CN_A",
        start_date="2026-01-01",
        end_date="2026-05-07",
        current_date="2026-05-07",
        run_id="run-fnd-036",
        dispatch_id="dispatch-fnd-036",
    )


def _raw_ref(provider: str, api_name: str, seq: int = 1) -> str:
    return (
        "viking://resources/workflow/run-fnd-036/frontline/fundamental_analyst/"
        f"dispatch-fnd-036/provider_raw/{provider}/{api_name}/{seq}.json"
    )


def _provider_result_attempt_seq_1():
    attempt = MODELS_MODULE.ProviderAttempt(
        provider="tushare",
        role="primary",
        api_name="daily_basic",
        attempt_seq=1,
        status="success",
        reason=None,
        started_at="2026-05-07T00:00:00+00:00",
        ended_at="2026-05-07T00:00:01+00:00",
        duration_ms=1000,
        retry_count=0,
        request_params_redacted={"ts_code": "600519.SH"},
        response_row_count=1,
        response_col_count=5,
        field_coverage=["valuation.pe_ttm"],
        report_period="20251231",
        announce_date="2026-03-31",
        as_of="2026-05-07",
        fetched_at="2026-05-07T00:00:01+00:00",
        raw_payload_hash="sha256:" + ("1" * 64),
        raw_payload_ref=_raw_ref("tushare", "daily_basic"),
        error_type=None,
        error_message_redacted=None,
    )
    return MODELS_MODULE.ProviderResult(
        attempt=attempt,
        raw_payload_hash=attempt.raw_payload_hash,
        raw_payload_ref=attempt.raw_payload_ref,
        extracted_fields=[
            (
                "valuation.pe_ttm",
                {"value": 21.2, "unit": "x", "scale": "raw", "raw_payload_ref": attempt.raw_payload_ref},
                "field_sources.valuation.pe_ttm",
            )
        ],
        schema_changed=False,
    )


def _cache_attempt():
    return MODELS_MODULE.ProviderAttempt(
        provider="mongodb",
        role="cache",
        api_name=None,
        attempt_seq=None,
        status="miss",
        reason="cache_miss",
        started_at="2026-05-07T00:00:00+00:00",
        ended_at="2026-05-07T00:00:01+00:00",
        duration_ms=1000,
        retry_count=0,
        request_params_redacted={"ticker": "600519.SH"},
        response_row_count=0,
        response_col_count=0,
        field_coverage=[],
        report_period=None,
        announce_date=None,
        as_of=None,
        fetched_at=None,
        raw_payload_hash=None,
        raw_payload_ref=_raw_ref("cache", "mongodb/inspect"),
        error_type=None,
        error_message_redacted=None,
    )


def _mapped_ok_fixture():
    return SimpleNamespace(
        facts={
            "valuation": {"pe_ttm": 21.2},
            "price_context": {"close": 1700.0, "trade_date": "20260507", "volume": 1234567.0},
            "financial_indicators": {"roe": 0.31},
            "income_statement": {"revenue": 1500.0},
            "balance_sheet": {"total_assets": 3000.0},
            "cash_flow": {"operating_cash_flow": 780.0},
            "business_segments": [],
            "dividend": [],
            "shareholders": {},
            "company_profile": {"name": "贵州茅台", "industry": "白酒", "main_business": "高端白酒生产与销售"},
        },
        facts_by_path={
            "valuation.pe_ttm": 21.2,
            "price_context.close": 1700.0,
            "price_context.trade_date": "20260507",
            "price_context.volume": 1234567.0,
            "financial_indicators.roe": 0.31,
            "income_statement.revenue": 1500.0,
            "balance_sheet.total_assets": 3000.0,
            "cash_flow.operating_cash_flow": 780.0,
            "company_profile.name": "贵州茅台",
            "company_profile.industry": "白酒",
            "company_profile.main_business": "高端白酒生产与销售",
        },
        field_sources={
            "valuation.pe_ttm": {
                "provider": "tushare",
                "api_name": "daily_basic",
                "as_of": "2026-05-07",
                "raw_payload_ref": _raw_ref("tushare", "daily_basic"),
            },
            "financial_indicators.roe": {
                "provider": "tushare",
                "api_name": "fina_indicator",
                "as_of": "2026-05-07",
                "raw_payload_ref": _raw_ref("tushare", "fina_indicator"),
                "supporting_sources": [
                    {"raw_payload_ref": _raw_ref("tushare", "daily_basic")}
                ],
            },
            "income_statement.revenue": {
                "provider": "tushare",
                "api_name": "income",
                "as_of": "2026-05-07",
                "raw_payload_ref": _raw_ref("tushare", "income"),
            },
            "balance_sheet.total_assets": {
                "provider": "tushare",
                "api_name": "balancesheet",
                "as_of": "2026-05-07",
                "raw_payload_ref": _raw_ref("tushare", "balancesheet"),
            },
            "cash_flow.operating_cash_flow": {
                "provider": "tushare",
                "api_name": "cashflow",
                "as_of": "2026-05-07",
                "raw_payload_ref": _raw_ref("tushare", "cashflow"),
            },
            "company_profile.name": {
                "provider": "tushare",
                "api_name": "stock_company",
                "as_of": "2026-05-07",
                "raw_payload_ref": _raw_ref("tushare", "stock_company"),
            },
            "company_profile.industry": {
                "provider": "tushare",
                "api_name": "stock_basic",
                "as_of": "2026-05-07",
                "raw_payload_ref": _raw_ref("tushare", "stock_basic"),
            },
            "company_profile.main_business": {
                "provider": "tushare",
                "api_name": "stock_company",
                "as_of": "2026-05-07",
                "raw_payload_ref": _raw_ref("tushare", "stock_company"),
            },
            "price_context.close": {
                "provider": "tushare",
                "api_name": "daily_basic",
                "as_of": "20260507",
                "raw_payload_ref": _raw_ref("tushare", "daily_basic"),
            },
            "price_context.trade_date": {
                "provider": "tushare",
                "api_name": "daily_basic",
                "as_of": "20260507",
                "raw_payload_ref": _raw_ref("tushare", "daily_basic"),
            },
            "price_context.volume": {
                "provider": "akshare",
                "api_name": "stock_zh_a_hist",
                "as_of": "20260507",
                "raw_payload_ref": _raw_ref("akshare", "stock_zh_a_hist"),
            },
        },
        missing_fields=[],
        diagnostic_flags=[],
    )
