from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_module(module_name: str, relative_path: str):
    repo_root = Path(__file__).resolve().parents[5]
    module_path = repo_root / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


models = _load_module(
    "cn_a_news_data_models",
    "agents/news_analyst/skills/cn-a-news-data/scripts/models.py",
)


def _build_pack():
    observation = models.KeywordObservations(
        matched_terms=["经营"],
        keyword_categories=["经营"],
        method="keyword_match",
        is_sentiment_judgment=False,
    )
    item = models.NewsItem(
        news_id="n-1",
        title="标题",
        summary="摘要",
        source="来源",
        publish_time="2026-05-07T10:00:00+08:00",
        url="https://example.com/1",
        data_source="akshare.stock_news_em",
        matched_keywords=["600519"],
        match_type="ticker_exact",
        match_evidence_span="600519",
        match_confidence="high",
        bucket="company_news",
        keyword_observations=observation,
        source_fetch_time="2026-05-07T10:01:00+08:00",
        content_hash="hash-1",
        is_primary_source=True,
        merged_from=[],
        evidence_gap=None,
    )
    plan = models.QueryPlan(
        ticker="600519",
        exchange_ticker="600519.SH",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-05-01",
        end_date="2026-05-07",
        company_keywords=["600519", "600519.SH", "贵州茅台"],
        industry_keywords=["白酒"],
        macro_keywords=["消费"],
    )
    attempt = models.ProviderAttempt(
        provider="akshare",
        endpoint="stock_news_em",
        query="symbol=600519",
        ok=True,
        elapsed_ms=23,
        raw_count=1,
        accepted_count=1,
        empty_reason=None,
        error=None,
        cancelled=False,
    )
    quality = models.Quality(
        status="complete",
        company_direct_news_count=1,
        industry_background_count=0,
        policy_macro_count=0,
        total_raw_count=1,
        after_dedup_count=1,
        accepted_count=1,
        missing_fields=[],
        directional_judgment_allowed=True,
        warnings=[],
    )
    return models.NewsDataPack(
        ok=True,
        profile={"industry": "白酒", "company_name": "贵州茅台"},
        query_plan=plan,
        provider_attempts=[attempt],
        data={"company_news": [item], "policy_macro_news": []},
        quality=quality,
        reader_brief="测试摘要",
        evidence={"pack_path": "runs/abc/pack.json"},
    )


def test_news_data_pack_stable_json_bytes_are_identical() -> None:
    pack = _build_pack()
    first = pack.to_stable_json_bytes()
    second = pack.to_stable_json_bytes()
    assert first == second


@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("run_id", ""),
        ("stage", ""),
        ("worker_id", ""),
        ("call_id", ""),
        ("tool_name", ""),
        ("evidence_root", ""),
    ],
)
def test_tool_runtime_context_required_fields(field_name: str, value: str) -> None:
    payload = {
        "run_id": "run-1",
        "stage": "frontline",
        "worker_id": "news_analyst",
        "call_id": "call-1",
        "tool_name": "news_news_data_pack",
        "evidence_root": "runs/run-1",
    }
    payload[field_name] = value
    with pytest.raises(ValueError, match=field_name):
        models.ToolRuntimeContext(**payload)


def test_news_data_pack_schema_version_is_fixed() -> None:
    pack = _build_pack()
    assert pack.schema_version == "cn_a_news_pack.v1"
    with pytest.raises(TypeError):
        models.NewsDataPack(schema_version="custom.v2", **pack.to_dict())  # type: ignore[arg-type]
    with pytest.raises(AttributeError):
        pack.schema_version = "custom.v2"
    assert pack.schema_version == "cn_a_news_pack.v1"
    payload = pack.to_dict()
    assert payload["schema_version"] == "cn_a_news_pack.v1"


def test_provider_attempt_timeout_serialization_contains_required_fields() -> None:
    attempt = models.ProviderAttempt(
        provider="akshare",
        endpoint="stock_news_em",
        query="symbol=600519",
        ok=False,
        elapsed_ms=1000,
        raw_count=0,
        accepted_count=0,
        empty_reason="timeout",
        error="timeout",
        cancelled=True,
    )
    payload = json.loads(models.stable_json_dumps(attempt))
    assert payload["elapsed_ms"] >= 0
    assert payload["empty_reason"] == "timeout"
    assert "cancelled" in payload
    assert payload["cancelled"] is True
