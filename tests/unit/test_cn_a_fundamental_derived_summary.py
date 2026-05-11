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


DERIVED_SUMMARY_MODULE = _load_script_module("derived_summary")


def test_t_fnd_035_build_uses_whitelist_templates_and_resolvable_source_refs() -> None:
    mapped = SimpleNamespace(
        field_sources={
            "financial_indicators.roe": {
                "provider": "tushare",
                "api_name": "fina_indicator",
                "report_period": "20251231",
                "as_of": "2026-05-07",
            },
            "valuation.pe_ttm": {"provider": "tushare", "api_name": "daily_basic", "as_of": "2026-05-07"},
            "valuation.pb": {"provider": "tushare", "api_name": "daily_basic", "as_of": "2026-05-07"},
            "valuation.total_mv": {"provider": "akshare", "api_name": "stock_zh_a_spot_em", "as_of": "2026-05-07"},
        }
    )
    diagnostics = SimpleNamespace(
        missing_fields=[
            {
                "field_path": "income_statement.revenue",
                "reason": "empty_response",
                "is_core_field": True,
            }
        ],
        provider_attempts=[
            {
                "provider": "tushare",
                "api_name": "income",
                "status": "error",
                "reason": "provider_error",
            }
        ],
        diagnostic_flags=[
            {
                "code": "cross_provider_conflict",
                "field_path": "valuation.pb",
                "providers": "tushare,akshare",
            }
        ],
    )

    result = DERIVED_SUMMARY_MODULE.DerivedSummaryBuilder.Build(mapped, diagnostics)

    assert result.valid is True
    assert len(result.items) <= 8
    assert result.diagnostic_flags == []
    for item in result.items:
        assert item.template_id in DERIVED_SUMMARY_MODULE.TEMPLATE_WHITELIST
        assert item.text != ""
        assert item.source_ref != ""
        assert item.inputs
        assert DERIVED_SUMMARY_MODULE.contains_forbidden_claim_words(item.text) is False
        assert DERIVED_SUMMARY_MODULE.is_resolvable_source_ref(
            item.source_ref,
            field_sources=mapped.field_sources,
            missing_fields=diagnostics.missing_fields,
            provider_attempts=diagnostics.provider_attempts,
            diagnostic_flags=diagnostics.diagnostic_flags,
        )


def test_t_fnd_035_returns_invalid_when_forbidden_claim_word_appears() -> None:
    mapped = SimpleNamespace(field_sources={})
    diagnostics = SimpleNamespace(
        missing_fields=[
            {
                "field_path": "建议字段",
                "reason": "empty_response",
                "is_core_field": True,
            }
        ],
        provider_attempts=[],
        diagnostic_flags=[],
    )

    result = DERIVED_SUMMARY_MODULE.DerivedSummaryBuilder.Build(mapped, diagnostics)

    assert result.valid is False
    assert result.items == []
    assert any(item["code"] == "derived_summary_invalid" for item in result.diagnostic_flags)


def test_t_fnd_035_returns_invalid_when_item_count_exceeds_limit() -> None:
    mapped = SimpleNamespace(field_sources={})
    diagnostics = SimpleNamespace(
        missing_fields=[
            {"field_path": f"financial_indicators.field_{index}", "reason": "empty_response", "is_core_field": True}
            for index in range(9)
        ],
        provider_attempts=[],
        diagnostic_flags=[],
    )

    result = DERIVED_SUMMARY_MODULE.DerivedSummaryBuilder.Build(mapped, diagnostics)

    assert result.valid is False
    assert result.items == []
    assert any(item.get("reason") == "item_limit_exceeded" for item in result.diagnostic_flags)


def test_t_fnd_035_field_source_ref_must_resolve_to_existing_field_path() -> None:
    resolvable = DERIVED_SUMMARY_MODULE.is_resolvable_source_ref(
        "field_sources.valuation.pe_ttm",
        field_sources={"valuation.pe_ttm": {"provider": "tushare"}},
        missing_fields=[],
        provider_attempts=[],
        diagnostic_flags=[],
    )
    missing = DERIVED_SUMMARY_MODULE.is_resolvable_source_ref(
        "field_sources.valuation.pb",
        field_sources={"valuation.pe_ttm": {"provider": "tushare"}},
        missing_fields=[],
        provider_attempts=[],
        diagnostic_flags=[],
    )

    assert resolvable is True
    assert missing is False
