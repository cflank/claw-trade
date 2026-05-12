from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.errors import (  # noqa: E402
    BRIEF_MACHINE_NOISE,
    BRIEF_UNSUPPORTED_CONCLUSION,
    FrontlineValidationError,
)
from frontline_data_pack.models import (  # noqa: E402
    BriefInput,
    EvidenceRef,
    FieldSource,
    PackEnvelope,
    PackInput,
    ProviderAttempt,
    Quality,
)
from frontline_data_pack.reader_brief import (  # noqa: E402
    build_gate_input,
    build_reader_brief,
    validate_reader_brief,
)


SHA = "sha256:" + ("a" * 64)
QUERY_FINGERPRINT = "sha256:" + ("b" * 64)


def test_t_brf_001_build_reader_brief_complete_contains_required_sections_and_length() -> None:
    brief = build_reader_brief(
        BriefInput(
            domain="market",
            input=PackInput(
                ticker="600519.SH",
                market="CN_A",
                company_name="贵州茅台",
                industry="白酒",
                start_date="2026-03-01",
                end_date="2026-05-08",
            ),
            quality=Quality(
                status="complete",
                coverage_score=0.92,
                freshness_status="fresh",
                warnings=[],
            ),
            provider_attempts=[
                _build_attempt(
                    provider="akshare",
                    endpoint="stock_zh_a_hist",
                    raw_count=60,
                    accepted_count=60,
                ),
                _build_attempt(
                    provider="alpha_vantage",
                    endpoint="china_macro_events",
                    raw_count=18,
                    accepted_count=12,
                ),
            ],
            accepted_counts={"price_history": 60, "macro_news": 12},
            missing_items=["图表缺口：成交量分布图未回写"],
            conflict_diagnostics=["收盘价快照与宏观事件窗口存在 1 天错位"],
            evidence_summary=[
                "行情来源 2 类，共 72 条 accepted",
                "宏观来源 1 类，共 12 条 accepted",
                "技术指标来源 1 类，覆盖 MA/MACD/RSI",
            ],
        )
    )

    assert "贵州茅台（600519.SH）行情与技术面资料" in brief
    assert "事实材料：" in brief
    assert "来源：" in brief
    assert "数据可用性：" not in brief
    assert "来源统计：" not in brief
    assert "资料范围：" not in brief
    assert "材料正文：" not in brief
    assert "质量状态：" not in brief
    assert "来源概况：" not in brief
    assert "使用边界：" not in brief
    assert "证据缺口：" not in brief
    assert "冲突诊断：" not in brief
    assert "accepted=" not in brief
    assert '"provider_attempts"' not in brief
    assert 1 <= len(brief) <= 3000


def test_t_brf_001_build_reader_brief_truncates_without_exceeding_schema_limit() -> None:
    brief = build_reader_brief(
        BriefInput(
            domain="news",
            input=PackInput(
                ticker="600519.SH",
                market="CN_A",
                company_name="贵州茅台",
                industry="白酒",
                start_date="2026-03-01",
                end_date="2026-05-08",
            ),
            quality=Quality(
                status="complete",
                coverage_score=0.92,
                freshness_status="fresh",
                warnings=[],
            ),
            provider_attempts=[
                _build_attempt(
                    provider="akshare",
                    endpoint="stock_news_em",
                    raw_count=20,
                    accepted_count=20,
                ),
            ],
            accepted_counts={"company_news": 20},
            missing_items=[],
            conflict_diagnostics=[],
            evidence_summary=["新闻材料" + ("较长正文" * 900)],
        )
    )

    assert len(brief) <= 3000
    assert brief.endswith(("。", "！", "？"))
    assert '"provider_attempts"' not in brief
    validate_reader_brief(brief)


def test_t_brf_001_validate_reader_brief_rejects_unsupported_conclusion() -> None:
    with pytest.raises(FrontlineValidationError) as error:
        validate_reader_brief("资料完整，建议买入并给出目标价 2200。")
    assert error.value.code == BRIEF_UNSUPPORTED_CONCLUSION


def test_t_brf_001_validate_reader_brief_rejects_uri_noise() -> None:
    with pytest.raises(FrontlineValidationError) as error:
        validate_reader_brief("证据路径为 viking://resources/workflow/run-1/frontline/market_analyst/call-1。")
    assert error.value.code == BRIEF_MACHINE_NOISE


def test_t_brf_001_build_gate_input_counts_verified_l2_refs() -> None:
    gate_input = build_gate_input(
        _build_pack(
            openviking_l2_refs=[
                _build_evidence_ref(index=1, verified=True),
                _build_evidence_ref(index=2, verified=True),
                _build_evidence_ref(index=3, verified=True),
                _build_evidence_ref(index=4, verified=False),
            ]
        )
    )

    assert gate_input.l2_verified_count == 3
    assert gate_input.missing_core_fields == ["valuation.pe_ttm", "financial_indicators.roe"]
    assert gate_input.unsupported_claim_risk_fields == ["valuation.pe_ttm", "financial_indicators.roe"]


def _build_pack(*, openviking_l2_refs: list[EvidenceRef]) -> PackEnvelope:
    attempt = _build_attempt(
        provider="akshare",
        endpoint="stock_a_lg_indicator",
        raw_count=3,
        accepted_count=2,
    )
    field_source = FieldSource(
        field_path="domain_data.valuation_fields.pe_ttm",
        provider="akshare",
        endpoint="stock_a_lg_indicator",
        payload_hash=SHA,
        raw_payload_ref="viking://resources/workflow/run-1/frontline/fundamental_analyst/call-1/evidence/provider_raw/akshare/stock_a_lg_indicator/1.json",
        observed_at="2026-05-08T10:00:01Z",
        source_time="2026-05-08",
    )
    evidence = _build_evidence_ref(index=0, verified=True)
    return PackEnvelope(
        ok=True,
        schema_version="cn_a_frontline_pack.v1",
        domain="fundamental",
        run_id="run-1",
        stage="frontline",
        worker_id="fundamental_analyst",
        call_id="call-1",
        tool_name="fundamental.fundamental_data_pack",
        input=PackInput(
            ticker="600519.SH",
            market="CN_A",
            company_name="贵州茅台",
            industry="白酒",
            start_date="2026-03-01",
            end_date="2026-05-08",
        ),
        quality=Quality(
            status="partial",
            coverage_score=0.67,
            freshness_status="fresh",
            warnings=[],
        ),
        provider_attempts=[attempt],
        field_sources={"valuation_pe_ttm": field_source},
        raw_payload_refs=[evidence],
        mongo_cache_refs=["cn_a_provider_cache:fundamental:run-1"],
        openviking_l2_refs=openviking_l2_refs,
        diagnostic_flags=[],
        reader_brief="本资料包仅用于事实摘要和证据审计，未包含投资判断。",
        domain_data={
            "schema_version": "cn_a_fundamental_pack.v1",
            "missing_core_fields": ["valuation.pe_ttm", "financial_indicators.roe"],
            "conflict_diagnostics": [],
        },
    )


def _build_attempt(
    *,
    provider: str,
    endpoint: str,
    raw_count: int,
    accepted_count: int,
) -> ProviderAttempt:
    return ProviderAttempt(
        provider=provider,
        endpoint=endpoint,
        role="core_data",
        status="success",
        started_at="2026-05-08T10:00:00Z",
        finished_at="2026-05-08T10:00:01Z",
        elapsed_ms=1000,
        timeout_ms=10000,
        query_fingerprint=QUERY_FINGERPRINT,
        raw_count=raw_count,
        accepted_count=accepted_count,
        payload_hash=SHA,
        raw_payload_ref="viking://resources/workflow/run-1/frontline/fundamental_analyst/call-1/evidence/provider_raw/akshare/stock_a_lg_indicator/1.json",
        error_code=None,
        error_message_redacted=None,
    )


def _build_evidence_ref(*, index: int, verified: bool) -> EvidenceRef:
    return EvidenceRef(
        uri=f"viking://resources/workflow/run-1/frontline/fundamental_analyst/call-1/evidence/provider_raw/akshare/stock_a_lg_indicator/{index}.json",
        sha256=SHA,
        size_bytes=256,
        kind="provider_raw",
        readback_verified=verified,
    )
