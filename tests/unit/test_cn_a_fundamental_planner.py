from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace


SCRIPTS_ROOT = Path("agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts")


def test_t_fnd_016_build_provider_plan_keeps_deterministic_phase1_order() -> None:
    normalized = _normalized_input()
    cache_result = _cache_result(status="success", reason="cache_hit")

    plan = PLANNER_MODULE.BuildProviderPlan(normalized, cache_result)

    assert plan.requires_runtime_phase2 is True
    assert plan.diagnostics == ()

    phase1 = plan.phase1_calls
    assert len(phase1) == len(PROVIDER_SPECS_MODULE.list_tushare_v1_api_names())
    assert [call.provider for call in phase1] == ["tushare"] * len(phase1)
    assert [call.api_name for call in phase1] == list(PROVIDER_SPECS_MODULE.list_tushare_v1_api_names())
    assert plan.log_fields["cache_status"] == "success"
    assert plan.log_fields["cache_reason"] == "cache_hit"
    assert plan.log_fields["tushare_enabled"] == "true"

    for call in phase1:
        assert call.api_name != ""
        assert call.field_family != ""
        assert isinstance(call.required, bool)
        assert call.timeout_ms > 0
        assert call.retry_limit >= 0
        assert isinstance(call.parameters, dict)


def test_t_fnd_016_tushare_disabled_returns_missing_primary_source_diagnostic() -> None:
    normalized = _normalized_input()
    cache_result = _cache_result(status="miss", reason="cache_miss")

    plan = PLANNER_MODULE.BuildProviderPlan(normalized, cache_result, disable_tushare=True)

    assert plan.requires_runtime_phase2 is True
    assert plan.phase1_calls == ()
    assert "FND_PROVIDER_PLAN_MISSING_PRIMARY_SOURCE:tushare_disabled" in plan.diagnostics
    assert plan.log_fields["tushare_enabled"] == "false"


def test_t_fnd_016_planner_port_uses_disable_tushare_flag() -> None:
    planner = PLANNER_MODULE.DeterministicProviderPlanner(disable_tushare=True)
    plan = planner.build_plan(_normalized_input(), _cache_result(status="miss", reason="cache_miss"))
    assert "FND_PROVIDER_PLAN_MISSING_PRIMARY_SOURCE:tushare_disabled" in plan.diagnostics
    assert plan.log_fields["tushare_enabled"] == "false"


def test_t_fnd_042_phase2_uses_tushare_runtime_status_instead_of_phase1_plan_only() -> None:
    results = [_tushare_result(api_name="daily_basic", status="empty")]

    supplement_plan = PLANNER_MODULE.BuildAkshareSupplementPlanFromTushareResult(
        results,
        _cache_result(status="miss", reason="cache_miss"),
        _normalized_input(),
    )

    assert "valuation" in supplement_plan.unresolved_families
    assert "price_context" in supplement_plan.unresolved_families
    assert "stock_zh_a_spot_em" in {call.api_name for call in supplement_plan.phase2_calls}
    assert "stock_zh_a_hist" in {call.api_name for call in supplement_plan.phase2_calls}
    assert supplement_plan.log_fields["phase1_tushare_result_count"] == "1"
    assert supplement_plan.log_fields["phase2_call_count"] == str(len(supplement_plan.phase2_calls))


def test_t_fnd_042_phase2_treats_success_with_insufficient_coverage_as_unresolved() -> None:
    results = [_tushare_result(api_name="income", status="success", field_coverage=[], extracted_paths=[])]

    supplement_plan = PLANNER_MODULE.BuildAkshareSupplementPlanFromTushareResult(
        results,
        _cache_result(status="miss", reason="cache_miss"),
        _normalized_input(),
    )

    assert "income_statement" in supplement_plan.unresolved_families
    assert "stock_financial_abstract_ths" in {call.api_name for call in supplement_plan.phase2_calls}
    assert any(item.startswith("phase1_low_coverage:income_statement:") for item in supplement_plan.diagnostics)


def test_t_fnd_042_disable_akshare_returns_empty_calls_and_unresolved_diagnostics() -> None:
    results = [_tushare_result(api_name="daily_basic", status="timeout")]

    supplement_plan = PLANNER_MODULE.BuildAkshareSupplementPlanFromTushareResult(
        results,
        _cache_result(status="stale", reason="cache_stale"),
        _normalized_input(),
        disable_akshare=True,
    )

    assert supplement_plan.phase2_calls == ()
    assert "valuation" in supplement_plan.unresolved_families
    assert "price_context" in supplement_plan.unresolved_families
    assert "FND_AKSHARE_SUPPLEMENT_DISABLED:akshare_disabled" in supplement_plan.diagnostics
    assert any(item.startswith("FND_AKSHARE_SUPPLEMENT_UNRESOLVED:") for item in supplement_plan.diagnostics)
    assert supplement_plan.log_fields["phase2_akshare_enabled"] == "false"


def test_t_fnd_042_phase2_includes_family_outside_tushare_allowlist() -> None:
    patched_templates = (
        SimpleNamespace(api_name="stock_zh_a_hist", field_family="non_tushare_family"),
    )
    previous_templates = PLANNER_MODULE.AKSHARE_V1_API_TEMPLATES
    try:
        PLANNER_MODULE.AKSHARE_V1_API_TEMPLATES = patched_templates
        supplement_plan = PLANNER_MODULE.BuildAkshareSupplementPlanFromTushareResult(
            _successful_tushare_results_covering_all_whitelisted_families(),
            _cache_result(status="success", reason="cache_hit"),
            _normalized_input(),
        )
    finally:
        PLANNER_MODULE.AKSHARE_V1_API_TEMPLATES = previous_templates

    assert supplement_plan.unresolved_families == ()
    assert [call.api_name for call in supplement_plan.phase2_calls] == ["stock_zh_a_hist"]


def _normalized_input():
    return MODELS_MODULE.NormalizedInput(
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


def _cache_result(*, status: str, reason: str):
    attempt = MODELS_MODULE.ProviderAttempt(
        provider="mongodb",
        role="cache",
        api_name=None,
        attempt_seq=None,
        status=status,  # type: ignore[arg-type]
        reason=reason,
        started_at="2026-05-07T00:00:00+00:00",
        ended_at="2026-05-07T00:00:01+00:00",
        duration_ms=1000,
        retry_count=0,
        request_params_redacted={},
        response_row_count=0,
        response_col_count=0,
        field_coverage=[],
        report_period=None,
        announce_date=None,
        as_of=None,
        fetched_at=None,
        raw_payload_hash=None,
        raw_payload_ref=None,
        error_type=None,
        error_message_redacted=None,
    )
    return CACHE_MODULE.CacheInspectionResult(
        attempt=attempt,
        reusable_fields={},
        diagnostics=(),
    )


def _tushare_result(
    *,
    api_name: str,
    status: str,
    field_coverage: list[str] | None = None,
    extracted_paths: list[str] | None = None,
):
    coverage = field_coverage if field_coverage is not None else []
    extracted = (
        [(path, {"value": 1}, f"field_sources.{path}") for path in extracted_paths]
        if extracted_paths is not None
        else []
    )
    attempt = MODELS_MODULE.ProviderAttempt(
        provider="tushare",
        role="primary",
        api_name=api_name,
        attempt_seq=1,
        status=status,  # type: ignore[arg-type]
        reason=None,
        started_at="2026-05-07T00:00:00+00:00",
        ended_at="2026-05-07T00:00:01+00:00",
        duration_ms=1000,
        retry_count=0,
        request_params_redacted={"api_name": api_name},
        response_row_count=1,
        response_col_count=1,
        field_coverage=coverage,
        report_period="20260331",
        announce_date="20260507",
        as_of="20260507",
        fetched_at="2026-05-07T00:00:01+00:00",
        raw_payload_hash="sha256:0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
        raw_payload_ref="viking://resources/workflow/run-1/frontline/fundamental_analyst/dispatch-1/provider_raw/tushare/x/1.json",
        error_type=None,
        error_message_redacted=None,
    )
    return MODELS_MODULE.ProviderResult(
        attempt=attempt,
        raw_payload_hash=attempt.raw_payload_hash,
        raw_payload_ref=attempt.raw_payload_ref,
        extracted_fields=extracted,
        schema_changed=False,
    )


def _successful_tushare_results_covering_all_whitelisted_families():
    family_to_paths = {
        "company_profile": ["company_profile.name"],
        "price_context": ["price_context.close"],
        "valuation": ["valuation.pe_ttm"],
        "financial_indicators": ["financial_indicators.roe"],
        "income_statement": ["income_statement.revenue"],
        "balance_sheet": ["balance_sheet.total_assets"],
        "cash_flow": ["cash_flow.n_cashflow_act"],
        "business_segments": ["business_segments.items"],
        "dividend": ["dividend.cash_div_tax"],
        "shareholders": ["shareholders.top10"],
    }
    results = []
    for template in PROVIDER_SPECS_MODULE.TUSHARE_V1_API_TEMPLATES:
        coverage: list[str] = []
        for family in template.field_family.split(","):
            normalized_family = family.strip()
            if normalized_family == "":
                continue
            coverage.extend(family_to_paths[normalized_family])
        results.append(
            _tushare_result(
                api_name=template.api_name,
                status="success",
                field_coverage=coverage,
                extracted_paths=coverage,
            )
        )
    return results


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
CACHE_MODULE = _load_script_module("cache")
PROVIDER_SPECS_MODULE = _load_script_module("provider_specs")
PLANNER_MODULE = _load_script_module("planner")
