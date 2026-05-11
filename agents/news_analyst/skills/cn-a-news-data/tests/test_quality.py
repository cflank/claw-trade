from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from models import (  # noqa: E402
    KeywordObservations,
    NewsDataPackRequest,
    NewsItem,
    ProviderAttempt,
    QualityInput,
)
from quality import QualityGate  # noqa: E402


def _build_request(
    *,
    company_name: str | None = "贵州茅台",
    industry: str | None = "白酒",
    profile_missing_fields: list[str] | None = None,
) -> NewsDataPackRequest:
    return NewsDataPackRequest(
        ticker="600519",
        exchange_ticker="600519.SH",
        market="CN_A",
        company_name=company_name,
        industry=industry,
        start_date="2026-05-01",
        end_date="2026-05-07",
        approved_aliases=["茅台"],
        approved_historical_names=[],
        profile_missing_fields=[] if profile_missing_fields is None else profile_missing_fields,
    )


def _build_attempt(
    *,
    provider: str,
    endpoint: str,
    ok: bool,
    raw_count: int,
    accepted_count: int = 0,
    empty_reason: str | None = None,
) -> ProviderAttempt:
    return ProviderAttempt(
        provider=provider,
        endpoint=endpoint,
        query=f"provider={provider};endpoint={endpoint}",
        ok=ok,
        elapsed_ms=8,
        raw_count=raw_count,
        accepted_count=accepted_count,
        empty_reason=empty_reason,
        error=None if ok else "provider_error",
        cancelled=False,
    )


def _build_news_item(
    news_id: str,
    *,
    bucket: str,
    summary: str = "摘要",
    is_sentiment_judgment: bool = False,
) -> NewsItem:
    return NewsItem(
        news_id=news_id,
        title=f"标题-{news_id}",
        summary=summary,
        source="来源",
        publish_time="2026-05-07 10:00:00",
        url=f"https://example.com/{news_id}",
        data_source="akshare.stock_news_em",
        matched_keywords=["600519"],
        match_type="ticker_exact",
        match_evidence_span="600519",
        match_confidence="high",
        bucket=bucket,
        keyword_observations=KeywordObservations(
            matched_terms=[],
            keyword_categories=[],
            method="keyword_match",
            is_sentiment_judgment=is_sentiment_judgment,
        ),
        source_fetch_time="2026-05-07T10:01:00+08:00",
        content_hash=f"hash-{news_id}",
        is_primary_source=True,
        merged_from=[],
        evidence_gap=None,
    )


def _build_quality_input(
    *,
    provider_attempts: list[ProviderAttempt],
    items: list[NewsItem],
    request: NewsDataPackRequest | None = None,
    total_raw_count: int = 10,
    after_dedup_count: int = 3,
    rejected_count: int = 0,
) -> QualityInput:
    return QualityInput(
        provider_attempts=provider_attempts,
        items=items,
        total_raw_count=total_raw_count,
        after_dedup_count=after_dedup_count,
        rejected_count=rejected_count,
        request=_build_request() if request is None else request,
    )


def test_evaluate_failed_when_both_p0_attempts_fail() -> None:
    quality_input = _build_quality_input(
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_news_em", ok=False, raw_count=0, empty_reason="provider_error"),
            _build_attempt(
                provider="akshare",
                endpoint="stock_info_global_cls",
                ok=False,
                raw_count=0,
                empty_reason="timeout",
            ),
        ],
        items=[_build_news_item("industry-1", bucket="industry_news")],
    )

    decision = QualityGate().evaluate(quality_input)

    assert decision.ok is False
    assert decision.quality.status == "failed"
    assert decision.quality.directional_judgment_allowed is False
    assert any("P0 数据源均失败" in warning for warning in decision.quality.warnings)


def test_evaluate_partial_when_p0_has_success_and_only_background_news() -> None:
    quality_input = _build_quality_input(
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=5, accepted_count=2),
            _build_attempt(
                provider="akshare",
                endpoint="stock_info_global_cls",
                ok=False,
                raw_count=0,
                empty_reason="provider_error",
            ),
        ],
        items=[
            _build_news_item("industry-1", bucket="industry_news"),
            _build_news_item("macro-1", bucket="policy_macro_news"),
        ],
    )

    decision = QualityGate().evaluate(quality_input)

    assert decision.ok is True
    assert decision.quality.status == "partial"
    assert decision.quality.directional_judgment_allowed is False
    assert any("不可用于公司方向性新闻判断" in warning for warning in decision.quality.warnings)


def test_evaluate_complete_when_p0_has_success_and_company_direct_exists() -> None:
    quality_input = _build_quality_input(
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=3, accepted_count=1),
            _build_attempt(provider="akshare", endpoint="stock_info_global_cls", ok=False, raw_count=0, empty_reason="timeout"),
        ],
        items=[
            _build_news_item("company-1", bucket="company_news"),
            _build_news_item("announcement-1", bucket="announcements"),
            _build_news_item("industry-1", bucket="industry_news"),
        ],
        total_raw_count=12,
        after_dedup_count=5,
    )

    decision = QualityGate().evaluate(quality_input)

    assert decision.ok is True
    assert decision.quality.status == "complete"
    assert decision.quality.directional_judgment_allowed is True
    assert decision.quality.company_direct_news_count == 2
    assert decision.quality.industry_background_count == 1
    assert decision.quality.policy_macro_count == 0
    assert decision.quality.total_raw_count == 12
    assert decision.quality.after_dedup_count == 5
    assert decision.quality.accepted_count == 3


def test_missing_p0_attempt_record_participates_as_provider_error_failure() -> None:
    quality_input = _build_quality_input(
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_info_global_em", ok=True, raw_count=4, accepted_count=1),
        ],
        items=[_build_news_item("company-1", bucket="company_news")],
    )

    decision = QualityGate().evaluate(quality_input)

    assert decision.ok is False
    assert decision.quality.status == "failed"
    assert decision.quality.directional_judgment_allowed is False
    assert any("P0 数据源均失败" in warning for warning in decision.quality.warnings)


def test_directional_key_signal_forces_failed_quality() -> None:
    quality_input = _build_quality_input(
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=2, accepted_count=1),
            _build_attempt(provider="akshare", endpoint="stock_info_global_cls", ok=True, raw_count=2, accepted_count=1),
        ],
        items=[{"bucket": "company_news", "sentiment_score": 0.83}],  # type: ignore[list-item]
    )

    decision = QualityGate().evaluate(quality_input)

    assert decision.ok is False
    assert decision.quality.status == "failed"
    assert any("方向性字段" in warning for warning in decision.quality.warnings)


def test_directional_judgment_trace_forces_failed_quality() -> None:
    quality_input = _build_quality_input(
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=2, accepted_count=1),
            _build_attempt(provider="akshare", endpoint="stock_info_global_cls", ok=True, raw_count=2, accepted_count=1),
        ],
        items=[_build_news_item("company-1", bucket="company_news", summary="机构建议买入并上调评级")],
    )

    decision = QualityGate().evaluate(quality_input)

    assert decision.ok is False
    assert decision.quality.status == "failed"
    assert decision.quality.directional_judgment_allowed is False
    assert any("方向性判断迹象" in warning for warning in decision.quality.warnings)


def test_all_providers_failed_returns_failed_quality() -> None:
    quality_input = _build_quality_input(
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_news_em", ok=False, raw_count=0, empty_reason="timeout"),
            _build_attempt(
                provider="akshare",
                endpoint="stock_info_global_cls",
                ok=False,
                raw_count=0,
                empty_reason="provider_error",
            ),
            _build_attempt(provider="akshare", endpoint="stock_info_global_em", ok=False, raw_count=0, empty_reason="timeout"),
        ],
        items=[],
        total_raw_count=0,
        after_dedup_count=0,
    )

    decision = QualityGate().evaluate(quality_input)

    assert decision.ok is False
    assert decision.quality.status == "failed"
    assert decision.quality.directional_judgment_allowed is False


def test_provider_success_with_no_result_is_not_effective_success() -> None:
    quality_input = _build_quality_input(
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=0, empty_reason="no_result"),
            _build_attempt(
                provider="akshare",
                endpoint="stock_info_global_cls",
                ok=False,
                raw_count=0,
                empty_reason="provider_error",
            ),
        ],
        items=[_build_news_item("company-1", bucket="company_news")],
    )

    decision = QualityGate().evaluate(quality_input)

    assert decision.ok is False
    assert decision.quality.status == "failed"
    assert any("P0 数据源均失败" in warning for warning in decision.quality.warnings)


def test_no_accepted_news_returns_failed_with_explicit_warning() -> None:
    quality_input = _build_quality_input(
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=1, accepted_count=0),
            _build_attempt(provider="akshare", endpoint="stock_info_global_cls", ok=True, raw_count=1, accepted_count=0),
        ],
        items=[],
        total_raw_count=2,
        after_dedup_count=0,
    )

    decision = QualityGate().evaluate(quality_input)

    assert decision.ok is False
    assert decision.quality.status == "failed"
    assert decision.quality.directional_judgment_allowed is False
    assert any("没有可接受新闻" in warning for warning in decision.quality.warnings)


def test_missing_fields_consumes_profile_fields_and_appends_required_gap_fields() -> None:
    request = _build_request(
        company_name="  ",
        industry=None,
        profile_missing_fields=["industry", "company_name", "industry"],
    )
    quality_input = _build_quality_input(
        provider_attempts=[
            _build_attempt(provider="akshare", endpoint="stock_news_em", ok=True, raw_count=1, accepted_count=1),
            _build_attempt(provider="akshare", endpoint="stock_info_global_cls", ok=True, raw_count=1, accepted_count=1),
        ],
        items=[_build_news_item("macro-1", bucket="policy_macro_news")],
        request=request,
    )

    decision = QualityGate().evaluate(quality_input)

    assert decision.quality.status == "partial"
    assert decision.quality.missing_fields == ["industry", "company_name", "company_direct_news"]
