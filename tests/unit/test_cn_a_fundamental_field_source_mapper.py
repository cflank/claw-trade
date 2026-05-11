from __future__ import annotations

import importlib.util
import sys
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
CACHE_MODULE = _load_script_module("cache")
MAPPER_MODULE = _load_script_module("field_source_mapper")


def test_mapper_merges_single_candidate_and_keeps_traceable_field_source() -> None:
    cache_result = _cache_result(
        reusable_fields={
            "income_statement.revenue": {
                "field_path": "income_statement.revenue",
                "value": 123456789.0,
                "unit": "cny",
                "scale": "x",
                "provider": "tushare",
                "api_name": "income",
                "payload_hash": "sha256:" + "1" * 64,
                "raw_payload_ref": "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/tushare/income/1.json",
                "source_ref_path": "field_sources.income_statement.revenue",
                "as_of": "2026-05-07",
                "fetched_at": "2026-05-07T09:00:00+00:00",
            }
        }
    )
    mapper = MAPPER_MODULE.FieldSourceMapper()
    result = mapper.Map(cache_result, [])
    assert result.facts["income_statement"]["revenue"] == 123456789.0
    source = result.field_sources["income_statement.revenue"]
    for required_key in (
        "provider",
        "api_name",
        "payload_hash",
        "raw_payload_ref",
        "source_ref_path",
        "as_of",
        "fetched_at",
        "unit",
        "scale",
        "value",
    ):
        assert required_key in source
    assert source["provider"] == "tushare"
    assert result.missing_fields == []
    assert result.diagnostic_flags == []


def test_mapper_keeps_all_refs_when_candidates_have_same_value_and_compatible_units() -> None:
    cache_result = _cache_result(reusable_fields={})
    provider_results = [
        _provider_result(
            provider="tushare",
            api_name="daily_basic",
            fetched_at="2026-05-07T09:00:00+00:00",
            fields=[
                (
                    "valuation.pe_ttm",
                    {
                        "value": 15.2,
                        "unit": "x",
                        "scale": "x",
                        "provider": "tushare",
                        "api_name": "daily_basic",
                        "payload_hash": "sha256:" + "2" * 64,
                        "raw_payload_ref": "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/tushare/daily_basic/1.json",
                    },
                    "field_sources.valuation.pe_ttm",
                )
            ],
        ),
        _provider_result(
            provider="akshare",
            api_name="stock_zh_a_spot_em",
            fetched_at="2026-05-07T10:00:00+00:00",
            fields=[
                (
                    "valuation.pe_ttm",
                    {
                        "value": 15.2,
                        "unit": "ratio",
                        "scale": "x",
                        "provider": "akshare",
                        "api_name": "stock_zh_a_spot_em",
                        "payload_hash": "sha256:" + "3" * 64,
                        "raw_payload_ref": "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/akshare/spot/1.json",
                    },
                    "field_sources.valuation.pe_ttm",
                )
            ],
        ),
    ]
    mapper = MAPPER_MODULE.FieldSourceMapper()
    result = mapper.Map(cache_result, provider_results)
    assert result.facts["valuation"]["pe_ttm"] == 15.2
    source = result.field_sources["valuation.pe_ttm"]
    assert source["provider"] == "akshare"
    refs = source["supporting_source_ref_paths"]
    assert "field_sources.valuation.pe_ttm" in refs
    supporting_sources = source["supporting_sources"]
    providers = {item["provider"] for item in supporting_sources}
    assert providers == {"tushare", "akshare"}
    assert result.missing_fields == []
    assert result.diagnostic_flags == []


def test_mapper_drops_core_field_when_cross_provider_conflict_exists() -> None:
    cache_result = _cache_result(reusable_fields={})
    provider_results = [
        _provider_result(
            provider="tushare",
            api_name="fina_indicator",
            fields=[
                (
                    "financial_indicators.roe",
                    {
                        "value": 18.1,
                        "unit": "%",
                        "scale": "x",
                        "provider": "tushare",
                        "api_name": "fina_indicator",
                        "payload_hash": "sha256:" + "4" * 64,
                        "raw_payload_ref": "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/tushare/fina_indicator/1.json",
                    },
                    "field_sources.financial_indicators.roe",
                )
            ],
        ),
        _provider_result(
            provider="akshare",
            api_name="stock_financial_abstract_ths",
            fields=[
                (
                    "financial_indicators.roe",
                    {
                        "value": 20.4,
                        "unit": "%",
                        "scale": "x",
                        "provider": "akshare",
                        "api_name": "stock_financial_abstract_ths",
                        "payload_hash": "sha256:" + "5" * 64,
                        "raw_payload_ref": "viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/akshare/ths/1.json",
                    },
                    "field_sources.financial_indicators.roe",
                )
            ],
        ),
    ]
    mapper = MAPPER_MODULE.FieldSourceMapper()
    result = mapper.Map(cache_result, provider_results)
    assert _path_in_nested_dict(result.facts, "financial_indicators.roe") is False
    assert any(
        item["field_path"] == "financial_indicators.roe" and item["reason"] == "cross_provider_conflict"
        for item in result.missing_fields
    )
    assert any(
        item["code"] == "cross_provider_conflict"
        and item["field_path"] == "financial_indicators.roe"
        and item["severity"] == "fail"
        for item in result.diagnostic_flags
    )
    assert any(
        item["domain"] == "financial_indicators"
        and item["status"] == "unavailable"
        and "financial_indicators.roe" in item["affected_fields"]
        for item in result.evidence_capability_impacts
    )


def test_mapper_marks_non_core_conflict_as_degraded_impact() -> None:
    cache_result = _cache_result(reusable_fields={})
    provider_results = [
        _provider_result(
            provider="tushare",
            api_name="dividend",
            fields=[
                (
                    "dividend.ex_date",
                    {
                        "value": "2026-06-01",
                        "unit": "date",
                        "scale": "x",
                    },
                    "field_sources.dividend.ex_date",
                )
            ],
        ),
        _provider_result(
            provider="akshare",
            api_name="stock_history_dividend_detail",
            fields=[
                (
                    "dividend.ex_date",
                    {
                        "value": "2026-06-07",
                        "unit": "date",
                        "scale": "x",
                    },
                    "field_sources.dividend.ex_date",
                )
            ],
        ),
    ]
    mapper = MAPPER_MODULE.FieldSourceMapper()
    result = mapper.Map(cache_result, provider_results)
    assert _path_in_nested_dict(result.facts, "dividend.ex_date") is False
    assert any(
        item["code"] == "cross_provider_conflict"
        and item["field_path"] == "dividend.ex_date"
        and item["severity"] == "warn"
        for item in result.diagnostic_flags
    )
    assert any(
        item["domain"] == "dividend"
        and item["status"] == "degraded"
        and "dividend.ex_date" in item["affected_fields"]
        for item in result.evidence_capability_impacts
    )


def _cache_result(reusable_fields: dict[str, dict[str, object]]) -> object:
    return CACHE_MODULE.CacheInspectionResult(
        attempt=_provider_attempt(provider="mongodb", api_name=None, status="miss", reason="cache_miss"),
        reusable_fields=reusable_fields,
        diagnostics=(),
    )


def _provider_result(
    *,
    provider: str,
    api_name: str,
    fields: list[tuple[str, object, str]],
    fetched_at: str = "2026-05-07T08:00:00+00:00",
) -> object:
    attempt = _provider_attempt(
        provider=provider,
        api_name=api_name,
        status="success",
        reason=None,
        fetched_at=fetched_at,
        raw_payload_hash="sha256:" + "a" * 64,
        raw_payload_ref=(
            f"viking://resources/workflow/run/frontline/fundamental_analyst/dispatch/provider_raw/"
            f"{provider}/{api_name}/1.json"
        ),
    )
    return MODELS_MODULE.ProviderResult(
        attempt=attempt,
        raw_payload_hash=attempt.raw_payload_hash,
        raw_payload_ref=attempt.raw_payload_ref,
        extracted_fields=fields,
        schema_changed=False,
    )


def _provider_attempt(
    *,
    provider: str,
    api_name: str | None,
    status: str,
    reason: str | None,
    fetched_at: str = "2026-05-07T08:00:00+00:00",
    raw_payload_hash: str | None = None,
    raw_payload_ref: str | None = None,
) -> object:
    return MODELS_MODULE.ProviderAttempt(
        provider=provider,
        role="cache" if provider == "mongodb" else "primary",
        api_name=api_name,
        attempt_seq=1,
        status=status,
        reason=reason,
        started_at="2026-05-07T08:00:00+00:00",
        ended_at="2026-05-07T08:00:01+00:00",
        duration_ms=100,
        retry_count=0,
        request_params_redacted={},
        response_row_count=1,
        response_col_count=1,
        field_coverage=[],
        report_period="20251231",
        announce_date="2026-03-31",
        as_of="2026-05-07",
        fetched_at=fetched_at,
        raw_payload_hash=raw_payload_hash,
        raw_payload_ref=raw_payload_ref,
        error_type=None,
        error_message_redacted=None,
    )


def _path_in_nested_dict(root: dict[str, object], dotted_path: str) -> bool:
    cursor: object = root
    for segment in dotted_path.split("."):
        if not isinstance(cursor, dict):
            return False
        if segment not in cursor:
            return False
        cursor = cursor[segment]
    return True
