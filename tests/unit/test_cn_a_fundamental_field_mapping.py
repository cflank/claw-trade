from __future__ import annotations

import importlib.util
import sys
from dataclasses import replace
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


FIELD_MAPPING_MODULE = _load_script_module("field_mapping_v1")


def test_cn_a_fundamental_field_mapping_v1_matches_dld_minimum_table() -> None:
    rules = FIELD_MAPPING_MODULE.CN_A_FUNDAMENTAL_FIELD_MAPPING_V1
    assert len(rules) == 44
    assert all(len(rule.__dataclass_fields__) == 8 for rule in rules)

    providers = {rule.provider for rule in rules}
    assert providers == {"tushare", "akshare"}

    assert all(rule.source_ref_kind == "raw" for rule in rules)
    assert all(rule.source_ref_path.startswith("field_sources.") for rule in rules)

    expected_paths = {
        "company_profile.name",
        "company_profile.industry",
        "company_profile.main_business",
        "price_context.close",
        "price_context.trade_date",
        "price_context.volume",
    }
    mapped_paths = {rule.pack_field_path for rule in rules}
    assert expected_paths.issubset(mapped_paths)


def test_dld_453_required_paths_are_covered_by_tushare_or_akshare() -> None:
    rules = FIELD_MAPPING_MODULE.CN_A_FUNDAMENTAL_FIELD_MAPPING_V1
    rule_set = {(rule.provider, rule.api_name, rule.source_column, rule.pack_field_path) for rule in rules}

    assert ("akshare", "stock_individual_info_em", "行业", "company_profile.industry") in rule_set
    assert ("akshare", "stock_individual_info_em", "主营业务", "company_profile.main_business") in rule_set
    assert ("akshare", "stock_zh_a_hist", "日期", "price_context.trade_date") in rule_set
    assert ("akshare", "stock_zh_a_hist", "成交量", "price_context.volume") in rule_set
    assert ("tushare", "stock_basic", "industry", "company_profile.industry") in rule_set
    assert ("tushare", "stock_company", "main_business", "company_profile.main_business") in rule_set
    assert ("tushare", "daily_basic", "trade_date", "price_context.trade_date") in rule_set
    assert ("tushare", "daily_basic", "close", "price_context.close") in rule_set


def test_bz_profit_maps_only_to_approved_business_segments_profit_path() -> None:
    rules = FIELD_MAPPING_MODULE.CN_A_FUNDAMENTAL_FIELD_MAPPING_V1
    approved_paths = FIELD_MAPPING_MODULE.APPROVED_PACK_FIELD_PATHS
    bz_profit_rules = [rule for rule in rules if rule.source_column == "bz_profit"]

    assert len(bz_profit_rules) == 1
    assert bz_profit_rules[0].provider == "tushare"
    assert bz_profit_rules[0].api_name == "fina_mainbz"
    assert bz_profit_rules[0].pack_field_path == "business_segments.profit"
    assert bz_profit_rules[0].pack_field_path in approved_paths


def test_validate_field_mapping_rules_rejects_unapproved_or_incomplete_rows() -> None:
    base = FIELD_MAPPING_MODULE.CN_A_FUNDAMENTAL_FIELD_MAPPING_V1[0]

    bad_provider = replace(base, provider="baostock")
    try:
        FIELD_MAPPING_MODULE.validate_field_mapping_rules((bad_provider,))
    except ValueError as exc:
        assert "provider" in str(exc)
    else:
        raise AssertionError("provider check must fail")

    bad_ref_kind = replace(base, source_ref_kind="derived")
    try:
        FIELD_MAPPING_MODULE.validate_field_mapping_rules((bad_ref_kind,))
    except ValueError as exc:
        assert "source_ref_kind" in str(exc)
    else:
        raise AssertionError("source_ref_kind check must fail")

    bad_ref_path = replace(base, source_ref_path="facts.valuation.pe_ttm")
    try:
        FIELD_MAPPING_MODULE.validate_field_mapping_rules((bad_ref_path,))
    except ValueError as exc:
        assert "source_ref_path" in str(exc)
    else:
        raise AssertionError("source_ref_path prefix check must fail")

    bad_pack_path = replace(base, pack_field_path="valuation.unapproved_field")
    try:
        FIELD_MAPPING_MODULE.validate_field_mapping_rules((bad_pack_path,))
    except ValueError as exc:
        assert "pack_field_path" in str(exc)
    else:
        raise AssertionError("pack_field_path allowlist check must fail")

    bad_empty_column = replace(base, source_column="")
    try:
        FIELD_MAPPING_MODULE.validate_field_mapping_rules((bad_empty_column,))
    except ValueError as exc:
        assert "source_column" in str(exc)
    else:
        raise AssertionError("empty source_column check must fail")
