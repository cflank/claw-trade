from __future__ import annotations

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


CAPABILITY_MODULE = _load_script_module("evidence_capabilities")


def test_t_fnd_034_outputs_required_capabilities_and_never_emits_investment_conclusion_types() -> None:
    mapped = _build_mapped_base()
    freshness = _freshness_all_fresh(mapped)
    diagnostics = _build_diagnostics(
        core_thresholds={
            "valuation": True,
            "financial_indicators": True,
            "income_statement": True,
            "balance_sheet": True,
            "cash_flow": True,
        },
        trend_observation={
            "trend_ready": True,
            "reason": "",
            "comparable_period_count": 2,
            "annual_period_count": 2,
        },
    )

    capabilities = CAPABILITY_MODULE.EvidenceCapabilityClassifier.Classify(mapped, freshness, diagnostics)
    required = {
        "company_profile",
        "financial_snapshot",
        "financial_trend",
        "valuation_snapshot",
        "valuation_judgment",
        "target_price",
        "rating",
    }
    assert required.issubset(set(capabilities.keys()))
    assert capabilities["financial_snapshot"]["status"] == "available"
    assert capabilities["financial_trend"]["status"] == "available"
    assert capabilities["valuation_snapshot"]["status"] == "available"
    assert capabilities["valuation_judgment"]["status"] == "blocked"
    assert capabilities["target_price"]["status"] == "blocked"
    assert capabilities["rating"]["status"] == "blocked"
    assert capabilities["target_price"]["reason"] == "data_service_never_supports_target_price"
    assert capabilities["rating"]["reason"] == "data_service_never_supports_buy_hold_sell_rating"


def test_t_fnd_034_financial_snapshot_available_only_when_core_financial_threshold_passes() -> None:
    mapped = _build_mapped_base()
    freshness = _freshness_all_fresh(mapped)
    diagnostics = _build_diagnostics(
        core_thresholds={
            "valuation": True,
            "financial_indicators": True,
            "income_statement": True,
            "balance_sheet": True,
            "cash_flow": False,
        },
        trend_observation={
            "trend_ready": True,
            "reason": "",
            "comparable_period_count": 2,
            "annual_period_count": 2,
        },
        hints={
            "financial_snapshot": {"status": "available", "blocked_reasons": []},
            "financial_trend": {"status": "available", "blocked_reasons": []},
            "valuation_snapshot": {"status": "available", "blocked_reasons": []},
        },
    )

    capabilities = CAPABILITY_MODULE.EvidenceCapabilityClassifier.Classify(mapped, freshness, diagnostics)
    assert capabilities["financial_snapshot"]["status"] == "blocked"
    assert "core_financial_fields_incomplete" in capabilities["financial_snapshot"]["blocked_reasons"]


def test_t_fnd_034_financial_trend_requires_period_and_conflict_gates() -> None:
    mapped = _build_mapped_base()
    mapped.periods = ["2024-12-31", "2025-12-31"]
    mapped.missing_fields = [
        {
            "field_path": "financial_indicators.roe",
            "reason": "cross_provider_conflict",
            "domain": "financial_indicators",
            "is_core_field": True,
        }
    ]
    freshness = _freshness_all_fresh(mapped)
    diagnostics = _build_diagnostics(
        core_thresholds={
            "valuation": True,
            "financial_indicators": True,
            "income_statement": True,
            "balance_sheet": True,
            "cash_flow": True,
        },
        trend_observation={
            "trend_ready": True,
            "reason": "",
            "comparable_period_count": 2,
            "annual_period_count": 2,
        },
    )

    capabilities = CAPABILITY_MODULE.EvidenceCapabilityClassifier.Classify(mapped, freshness, diagnostics)
    assert capabilities["financial_trend"]["status"] == "blocked"
    assert "cross_provider_conflict" in capabilities["financial_trend"]["blocked_reasons"]


def test_t_fnd_034_valuation_snapshot_available_only_when_valuation_threshold_passes() -> None:
    mapped = _build_mapped_base()
    mapped.facts_by_path.pop("valuation.pb")
    mapped.facts_by_path.pop("valuation.total_mv")
    freshness = _freshness_all_fresh(mapped)
    diagnostics = _build_diagnostics(
        core_thresholds={
            "valuation": False,
            "financial_indicators": True,
            "income_statement": True,
            "balance_sheet": True,
            "cash_flow": True,
        },
        trend_observation={
            "trend_ready": True,
            "reason": "",
            "comparable_period_count": 2,
            "annual_period_count": 2,
        },
    )

    capabilities = CAPABILITY_MODULE.EvidenceCapabilityClassifier.Classify(mapped, freshness, diagnostics)
    assert capabilities["valuation_snapshot"]["status"] == "blocked"
    assert "valuation_core_fields_incomplete" in capabilities["valuation_snapshot"]["blocked_reasons"]


def test_t_fnd_034_respects_impacts_and_freshness_degrade_paths() -> None:
    mapped = _build_mapped_base()
    mapped.evidence_capability_impacts = [
        {
            "domain": "financial_indicators",
            "capability": "financial_indicators_evidence",
            "status": "degraded",
            "reason": "cross_provider_conflict",
            "affected_fields": ["financial_indicators.gross_margin"],
            "is_core_impact": False,
        }
    ]
    freshness = _freshness_all_fresh(mapped)
    freshness["company_profile.name"]["is_stale"] = True
    freshness["company_profile.name"]["reason"] = "stale"
    diagnostics = _build_diagnostics(
        core_thresholds={
            "valuation": True,
            "financial_indicators": True,
            "income_statement": True,
            "balance_sheet": True,
            "cash_flow": True,
        },
        trend_observation={
            "trend_ready": True,
            "reason": "",
            "comparable_period_count": 2,
            "annual_period_count": 2,
        },
    )

    capabilities = CAPABILITY_MODULE.EvidenceCapabilityClassifier.Classify(mapped, freshness, diagnostics)
    assert capabilities["company_profile"]["status"] == "degraded"
    assert "stale" in capabilities["company_profile"]["degraded_reasons"]
    assert capabilities["financial_snapshot"]["status"] == "degraded"
    assert "cross_provider_conflict" in capabilities["financial_snapshot"]["degraded_reasons"]


def test_t_fnd_034_output_is_stable_dict_and_json_serializable() -> None:
    mapped = _build_mapped_base()
    freshness = _freshness_all_fresh(mapped)
    diagnostics = _build_diagnostics(
        core_thresholds={
            "valuation": True,
            "financial_indicators": True,
            "income_statement": True,
            "balance_sheet": True,
            "cash_flow": True,
        },
        trend_observation={
            "trend_ready": True,
            "reason": "",
            "comparable_period_count": 2,
            "annual_period_count": 2,
        },
    )

    capabilities = CAPABILITY_MODULE.EvidenceCapabilityClassifier.Classify(mapped, freshness, diagnostics)
    rendered = json.dumps(capabilities, ensure_ascii=False)
    assert rendered.startswith("{")
    for capability_name, payload in capabilities.items():
        assert "status" in payload
        assert "blocked_claims" in payload
        assert "blocked_reasons" in payload
        assert "degraded_reasons" in payload
        assert "reason" in payload
        assert isinstance(payload["blocked_claims"], list), capability_name
        assert isinstance(payload["blocked_reasons"], list), capability_name
        assert isinstance(payload["degraded_reasons"], list), capability_name


def _build_mapped_base() -> SimpleNamespace:
    facts_by_path: dict[str, object] = {
        "company_profile.name": "贵州茅台",
        "valuation.pe_ttm": 21.2,
        "valuation.pb": 5.8,
        "valuation.total_mv": 2.4e12,
        "financial_indicators.roe": 0.31,
        "financial_indicators.roa": 0.18,
        "financial_indicators.gross_margin": 0.9,
        "financial_indicators.netprofit_margin": 0.52,
        "financial_indicators.debt_to_assets": 0.2,
        "income_statement.revenue": 1500.0,
        "balance_sheet.total_assets": 3000.0,
        "cash_flow.operating_cash_flow": 780.0,
    }
    return SimpleNamespace(
        facts_by_path=facts_by_path,
        periods=["2024-12-31", "2025-12-31"],
        missing_fields=[],
        evidence_capability_impacts=[],
    )


def _freshness_all_fresh(mapped: SimpleNamespace) -> dict[str, dict[str, object]]:
    freshness: dict[str, dict[str, object]] = {}
    for field_path in mapped.facts_by_path.keys():
        freshness[field_path] = {
            "is_stale": False,
            "reason": None,
            "as_of": "2026-05-07",
            "age_days": 0,
            "domain": field_path.split(".", 1)[0],
            "diagnostic_flags": [],
        }
    return freshness


def _build_diagnostics(
    *,
    core_thresholds: dict[str, bool],
    trend_observation: dict[str, object],
    hints: dict[str, dict[str, object]] | None = None,
) -> SimpleNamespace:
    capability_hints = hints or {
        "financial_snapshot": {"status": "available", "blocked_reasons": []},
        "valuation_snapshot": {"status": "available", "blocked_reasons": []},
        "financial_trend": {"status": "available", "blocked_reasons": []},
    }
    completeness = SimpleNamespace(
        core_thresholds=core_thresholds,
        trend_observation=trend_observation,
    )
    return SimpleNamespace(
        capability_degradation_hints=capability_hints,
        completeness=completeness,
        missing_fields=[],
    )
