from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from errors import (  # noqa: E402
    E_CONTEXT_MISMATCH,
    E_EVIDENCE_WRITE_FAILED,
    E_INVALID_INPUT,
    NewsDataError,
)
from models import (  # noqa: E402
    EvidenceRefs,
    EvidenceWriteRequest,
    KeywordObservations,
    NewsDataPack,
    NewsDataPackRequest,
    NewsItem,
    ProviderAttempt,
    Quality,
    QueryPlan,
    ResolvedProfile,
    ToolInput,
    ToolRuntimeContext,
)
import news_data_pack as news_data_pack_module  # noqa: E402


def _build_context(tmp_path: Path, **overrides: str) -> ToolRuntimeContext:
    payload = {
        "run_id": "run-1",
        "stage": "frontline",
        "worker_id": "news_analyst",
        "call_id": "call-1",
        "tool_name": "news_news_data_pack",
        "evidence_root": str(tmp_path / "evidence"),
    }
    payload.update(overrides)
    return ToolRuntimeContext(**payload)


def _build_success_pack() -> NewsDataPack:
    observation = KeywordObservations(
        matched_terms=["600519"],
        keyword_categories=["公司"],
        method="keyword_match",
        is_sentiment_judgment=False,
    )
    item = NewsItem(
        news_id="n-1",
        title="600519 公司新闻",
        summary="摘要",
        source="来源",
        publish_time="2026-05-07 10:00:00",
        url="https://example.com/news-1",
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
    return NewsDataPack(
        ok=True,
        profile={"company_name": "贵州茅台", "industry": "白酒"},
        query_plan=QueryPlan(
            ticker="600519",
            exchange_ticker="600519.SH",
            company_name="贵州茅台",
            industry="白酒",
            start_date="2026-05-01",
            end_date="2026-05-07",
            company_keywords=["600519", "600519.SH", "贵州茅台"],
            industry_keywords=["白酒"],
            macro_keywords=[],
        ),
        provider_attempts=[
            ProviderAttempt(
                provider="akshare",
                endpoint="stock_news_em",
                query="ticker=600519",
                ok=True,
                elapsed_ms=12,
                raw_count=1,
                accepted_count=1,
                empty_reason=None,
                error=None,
                cancelled=False,
            )
        ],
        data={
            "company_news": [item],
            "industry_news": [],
            "policy_macro_news": [],
            "announcements": [],
        },
        quality=Quality(
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
        ),
        reader_brief="成功资料包",
    )


class _ResolverHarness:
    def __init__(self, resolved: ResolvedProfile | None = None) -> None:
        self.resolved = resolved or ResolvedProfile(
            company_name="贵州茅台",
            industry="白酒",
            approved_aliases=[],
            approved_historical_names=[],
            missing_fields=[],
        )
        self.calls: list[dict[str, str | None]] = []

    def resolve(
        self,
        *,
        ticker: str,
        run_id: str,
        stage: str,
        profile_ref: str | None,
        fundamentals_ref: str | None,
        market_ref: str | None,
    ) -> ResolvedProfile:
        self.calls.append(
            {
                "ticker": ticker,
                "run_id": run_id,
                "stage": stage,
                "profile_ref": profile_ref,
                "fundamentals_ref": fundamentals_ref,
                "market_ref": market_ref,
            }
        )
        return self.resolved


class _ServiceHarness:
    def __init__(self, pack: NewsDataPack) -> None:
        self.pack = pack
        self.requests: list[NewsDataPackRequest] = []

    def build_pack(self, request: NewsDataPackRequest) -> NewsDataPack:
        self.requests.append(request)
        return self.pack


class _EvidenceHarness:
    def __init__(self, refs: EvidenceRefs | None = None, error: NewsDataError | None = None) -> None:
        self.refs = refs or EvidenceRefs(
            pack_path="/tmp/evidence/news_data_pack.json",
            provider_attempts_path="/tmp/evidence/provider_attempts.json",
            provider_raw_paths=[],
            content_hash="hash-pack",
        )
        self.error = error
        self.requests: list[EvidenceWriteRequest] = []

    def write_pack(self, request: EvidenceWriteRequest) -> EvidenceRefs:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return self.refs


def _patch_harness(
    monkeypatch: pytest.MonkeyPatch,
    *,
    resolver: _ResolverHarness | None = None,
    service: _ServiceHarness | None = None,
    evidence: _EvidenceHarness | None = None,
) -> tuple[_ResolverHarness, _ServiceHarness, _EvidenceHarness]:
    resolved_resolver = resolver or _ResolverHarness()
    resolved_service = service or _ServiceHarness(_build_success_pack())
    resolved_evidence = evidence or _EvidenceHarness()
    monkeypatch.setattr(news_data_pack_module, "ApprovedProfileResolver", lambda: resolved_resolver)
    monkeypatch.setattr(news_data_pack_module, "NewsDataService", lambda: resolved_service)
    monkeypatch.setattr(news_data_pack_module, "EvidenceWriter", lambda: resolved_evidence)
    return resolved_resolver, resolved_service, resolved_evidence


def test_run_news_data_pack_raises_context_mismatch_on_worker_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_harness(monkeypatch)
    context = _build_context(tmp_path, worker_id="fundamental_analyst")
    tool_input = ToolInput(ticker="600519", market="CN_A")

    with pytest.raises(NewsDataError) as exc_info:
        news_data_pack_module.run_news_data_pack(tool_input, context)

    assert exc_info.value.code == E_CONTEXT_MISMATCH


def test_run_news_data_pack_raises_context_mismatch_on_tool_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_harness(monkeypatch)
    context = _build_context(tmp_path, tool_name="news.other_tool")
    tool_input = ToolInput(ticker="600519", market="CN_A")

    with pytest.raises(NewsDataError) as exc_info:
        news_data_pack_module.run_news_data_pack(tool_input, context)

    assert exc_info.value.code == E_CONTEXT_MISMATCH


def test_run_news_data_pack_returns_failed_pack_when_market_not_cn_a(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, service, evidence = _patch_harness(monkeypatch)
    context = _build_context(tmp_path)
    tool_input = {"ticker": "600519", "market": "US"}

    result = news_data_pack_module.run_news_data_pack(tool_input, context.__dict__)

    assert result["ok"] is False
    assert result["quality"]["status"] == "failed"
    assert result["quality"]["directional_judgment_allowed"] is False
    assert result["provider_attempts"] == []
    assert result["data"] == {
        "company_news": [],
        "industry_news": [],
        "policy_macro_news": [],
        "announcements": [],
    }
    assert "E_UNSUPPORTED_MARKET" in result["reader_brief"]
    assert result["evidence"]["pack_path"] == "/tmp/evidence/news_data_pack.json"
    assert len(service.requests) == 0
    assert len(evidence.requests) == 1


def test_run_news_data_pack_returns_failed_pack_with_invalid_ticker_and_writes_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, service, evidence = _patch_harness(monkeypatch)
    context = _build_context(tmp_path)
    tool_input = ToolInput(ticker="BAD_TICKER", market="CN_A")

    result = news_data_pack_module.run_news_data_pack(tool_input, context)

    assert result["ok"] is False
    assert result["quality"]["status"] == "failed"
    assert "E_INVALID_INPUT" in result["reader_brief"]
    assert result["evidence"]["pack_path"] == "/tmp/evidence/news_data_pack.json"
    assert result["evidence"]["content_hash"] == "hash-pack"
    assert len(service.requests) == 0
    assert len(evidence.requests) == 1


def test_run_news_data_pack_returns_pack_with_evidence_refs_on_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _, _ = _patch_harness(monkeypatch)
    context = _build_context(tmp_path)
    tool_input = {
        "ticker": "600519.SH",
        "market": "CN_A",
        "start_date": "2026-05-01",
        "end_date": "2026-05-07",
    }

    result = news_data_pack_module.run_news_data_pack(tool_input, context)

    assert result["ok"] is True
    assert result["evidence"]["pack_path"] == "/tmp/evidence/news_data_pack.json"
    assert result["evidence"]["content_hash"] == "hash-pack"


def test_run_news_data_pack_raises_on_evidence_write_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    evidence = _EvidenceHarness(
        error=NewsDataError(
            code=E_EVIDENCE_WRITE_FAILED,
            message="evidence write failed",
        )
    )
    _patch_harness(monkeypatch, evidence=evidence)
    context = _build_context(tmp_path)
    tool_input = ToolInput(ticker="600519", market="CN_A")

    with pytest.raises(NewsDataError) as exc_info:
        news_data_pack_module.run_news_data_pack(tool_input, context)

    assert exc_info.value.code == E_EVIDENCE_WRITE_FAILED


def test_run_news_data_pack_passes_resolver_lists_to_request_but_not_pack_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    resolver = _ResolverHarness(
        resolved=ResolvedProfile(
            company_name="贵州茅台",
            industry="白酒",
            approved_aliases=["茅台", "贵州茅台"],
            approved_historical_names=["贵州茅台酒股份有限公司"],
            missing_fields=["industry"],
        )
    )
    service_pack = _build_success_pack()
    service = _ServiceHarness(service_pack)
    _patch_harness(monkeypatch, resolver=resolver, service=service)
    context = _build_context(tmp_path)
    tool_input = {
        "ticker": "000001",
        "market": "CN_A",
        "start_date": "2026-05-01",
        "end_date": "2026-05-07",
        "profile_artifact_ref": "/tmp/profile.json",
        "fundamentals_artifact_ref": "/tmp/fundamentals.json",
        "market_artifact_ref": "/tmp/market.json",
    }

    result = news_data_pack_module.run_news_data_pack(tool_input, context.__dict__)

    assert len(service.requests) == 1
    request = service.requests[0]
    assert request.ticker == "000001"
    assert request.exchange_ticker == "000001.SZ"
    assert request.approved_aliases == ["茅台", "贵州茅台"]
    assert request.approved_historical_names == ["贵州茅台酒股份有限公司"]
    assert request.profile_missing_fields == ["industry"]
    assert set(result["profile"].keys()) == {"company_name", "industry"}
    assert isinstance(result["profile"]["company_name"], str) or result["profile"]["company_name"] is None
    assert isinstance(result["profile"]["industry"], str) or result["profile"]["industry"] is None
