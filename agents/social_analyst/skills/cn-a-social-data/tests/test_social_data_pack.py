from __future__ import annotations

import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from orchestrator import SocialToolRuntimeContext  # noqa: E402
from pack_schema import ProviderAttempt, PublicSocialProfile, QueryPlanView, SocialEvidence, SocialQuality, SocialSentimentPack  # noqa: E402
from profile import SocialToolInput  # noqa: E402
import social_data_pack as social_data_pack_module  # noqa: E402


def test_run_social_data_pack_passes_complete_runtime_context_to_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def _capture_build_pack(tool_input: SocialToolInput, context: SocialToolRuntimeContext) -> SocialSentimentPack:
        captured["tool_input"] = tool_input
        captured["context"] = context
        return _build_partial_pack()

    monkeypatch.setattr(social_data_pack_module, "build_social_sentiment_pack", _capture_build_pack)

    result = social_data_pack_module.run_social_data_pack(
        tool_input={
            "ticker": "600519",
            "market": "CN_A",
            "company_name": "贵州茅台",
            "industry": "白酒",
            "start_date": "2026-05-01",
            "end_date": "2026-05-07",
            "approved_artifact_refs": [],
        },
        context={
            "run_id": "run-1",
            "stage": "frontline",
            "worker_id": "social_analyst",
            "call_id": "call-1",
            "tool_name": "social_social_sentiment_pack",
            "evidence_root": "runs",
        },
    )

    runtime_context = captured["context"]
    assert isinstance(runtime_context, SocialToolRuntimeContext)
    assert runtime_context.run_id == "run-1"
    assert runtime_context.stage == "frontline"
    assert runtime_context.worker_id == "social_analyst"
    assert runtime_context.call_id == "call-1"
    assert runtime_context.evidence_root == "runs"
    assert runtime_context.current_time
    assert result["quality"]["status"] == "partial"


def test_run_social_data_pack_returns_failed_pack_for_unsupported_market() -> None:
    result = social_data_pack_module.run_social_data_pack(
        tool_input={"ticker": "00700.HK", "market": "HK"},
        context=_runtime_context_dict(),
    )

    assert result["ok"] is False
    assert result["quality"]["status"] == "failed"
    assert _first_warning_code(result) == "SOCIAL_UNSUPPORTED_MARKET"


def test_run_social_data_pack_returns_failed_pack_for_invalid_stage() -> None:
    context = _runtime_context_dict()
    context["stage"] = "risk_debate"

    result = social_data_pack_module.run_social_data_pack(
        tool_input={"ticker": "600519", "market": "CN_A"},
        context=context,
    )

    assert result["ok"] is False
    assert result["quality"]["status"] == "failed"
    assert _first_warning_code(result) == "SOCIAL_INVALID_INPUT"


def test_run_social_data_pack_keeps_partial_quality_from_orchestrator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(social_data_pack_module, "build_social_sentiment_pack", lambda tool_input, context: _build_partial_pack())

    result = social_data_pack_module.run_social_data_pack(
        tool_input={"ticker": "600519", "market": "CN_A"},
        context=_runtime_context_dict(),
    )

    assert result["quality"]["status"] == "partial"


def _runtime_context_dict() -> dict[str, str]:
    return {
        "run_id": "run-1",
        "stage": "frontline",
        "worker_id": "social_analyst",
        "call_id": "call-1",
        "tool_name": "social_social_sentiment_pack",
        "evidence_root": "runs",
        "current_time": "2026-05-07T09:30:00+08:00",
    }


def _build_partial_pack() -> SocialSentimentPack:
    return SocialSentimentPack(
        ok=False,
        profile=PublicSocialProfile(
            ticker="600519.SH",
            company_name="贵州茅台",
            market="CN_A",
            industry="白酒",
        ),
        query_plan=QueryPlanView(
            start_date="2026-05-01",
            end_date="2026-05-07",
            keywords=("600519.SH", "贵州茅台"),
            provider_endpoints=("stock_hot_rank_latest_em",),
        ),
        provider_attempts=[
            ProviderAttempt(
                provider="akshare",
                endpoint="stock_hot_rank_latest_em",
                priority="P0",
                query='{"ticker":"600519"}',
                ok=False,
                status="timeout",
                elapsed_ms=10_000,
                raw_count=0,
                accepted_count=0,
                cache_status="miss",
                cache_key=None,
                payload_hash=None,
                raw_payload_ref=None,
                empty_reason=None,
                error={"code": "SOCIAL_PROVIDER_TIMEOUT", "message": "endpoint 调用超时（10s）"},
                cancelled=False,
            )
        ],
        data={
            "attention_signals": [],
            "topic_keyword_signals": [],
            "related_symbol_signals": [],
            "narrative_signals": [],
            "rejected_signals": [],
        },
        quality=SocialQuality(
            status="partial",
            attention_signal_count=0,
            topic_keyword_count=0,
            related_symbol_count=0,
            narrative_signal_count=0,
            accepted_count=0,
            missing_fields=[],
            social_judgment_allowed=False,
            warnings=[],
        ),
        reader_brief="provider 执行阶段已完成，后续阶段待处理",
        evidence=SocialEvidence(
            pack_path="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/pack/social_sentiment_pack.json",
            provider_attempts_path="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/pack/provider_attempts.json",
            cache_inspection_path="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/pack/cache_inspection.json",
            raw_payload_refs=[],
            content_hash="sha256:" + ("f" * 64),
        ),
    )


def _first_warning_code(payload: dict[str, object]) -> str | None:
    quality = payload.get("quality")
    if not isinstance(quality, dict):
        return None
    warnings = quality.get("warnings")
    if not isinstance(warnings, list) or not warnings:
        return None
    first = warnings[0]
    if not isinstance(first, dict):
        return None
    code = first.get("code")
    return code if isinstance(code, str) else None
