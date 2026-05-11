from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from pack_schema import (  # noqa: E402
    REQUIRED_PACK_TOP_LEVEL_FIELDS,
    SOCIAL_PACK_SCHEMA_INVALID,
    SOCIAL_PROVIDER_ATTEMPTS_MISSING,
    DateWindow,
    ProviderAttempt,
    PublicSocialProfile,
    QueryPlanView,
    SocialEvidence,
    SocialPackSchemaError,
    SocialQuality,
    SocialSentimentPack,
    canonical_json_sha256,
    validate_social_pack_payload,
)


def test_social_pack_serialization_contains_required_top_level_fields() -> None:
    pack = _build_valid_pack()
    payload = pack.to_dict()

    assert set(REQUIRED_PACK_TOP_LEVEL_FIELDS) <= set(payload.keys())


def test_validate_social_pack_payload_rejects_non_v1_schema_version() -> None:
    pack = _build_valid_pack()
    payload = pack.to_dict()
    payload["schema_version"] = "cn_a_social_pack.v2"

    with pytest.raises(SocialPackSchemaError) as exc_info:
        validate_social_pack_payload(payload)

    assert exc_info.value.code == SOCIAL_PACK_SCHEMA_INVALID


def test_social_pack_rejects_empty_provider_attempts() -> None:
    with pytest.raises(SocialPackSchemaError) as exc_info:
        SocialSentimentPack(
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
            provider_attempts=[],
            data={
                "attention_signals": [],
                "topic_keyword_signals": [],
                "related_symbol_signals": [],
                "narrative_signals": [],
                "rejected_signals": [],
            },
            quality=SocialQuality(
                status="failed",
                attention_signal_count=0,
                topic_keyword_count=0,
                related_symbol_count=0,
                narrative_signal_count=0,
                accepted_count=0,
                missing_fields=[],
                social_judgment_allowed=False,
                warnings=[],
            ),
            reader_brief="",
            evidence=SocialEvidence(
                pack_path="viking://resources/workflow/run/frontline/social_analyst/call/evidence/pack/social_sentiment_pack.json",
                provider_attempts_path="viking://resources/workflow/run/frontline/social_analyst/call/evidence/pack/provider_attempts.json",
                cache_inspection_path="viking://resources/workflow/run/frontline/social_analyst/call/evidence/pack/cache_inspection.json",
                raw_payload_refs=[],
                content_hash="sha256:" + ("1" * 64),
            ),
        )

    assert exc_info.value.code == SOCIAL_PROVIDER_ATTEMPTS_MISSING


def test_canonical_serialization_hash_is_stable_for_same_pack_body() -> None:
    fixture_path = Path(__file__).parent / "fixtures" / "cn_a_social_pack_v1_contract.json"
    payload = json.loads(fixture_path.read_text(encoding="utf-8"))
    reordered = {
        "evidence": payload["evidence"],
        "reader_brief": payload["reader_brief"],
        "quality": payload["quality"],
        "data": payload["data"],
        "provider_attempts": payload["provider_attempts"],
        "query_plan": payload["query_plan"],
        "profile": payload["profile"],
        "ok": payload["ok"],
        "schema_version": payload["schema_version"],
    }

    validate_social_pack_payload(payload)
    validate_social_pack_payload(reordered)
    assert canonical_json_sha256(payload) == canonical_json_sha256(reordered)


def _build_valid_pack() -> SocialSentimentPack:
    _ = DateWindow(start_date="2026-05-01", end_date="2026-05-07", as_of_date="2026-05-07")
    return SocialSentimentPack(
        ok=True,
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
            provider_endpoints=("stock_hot_rank_latest_em", "stock_hot_keyword_em"),
        ),
        provider_attempts=[
            ProviderAttempt(
                provider="akshare",
                endpoint="stock_hot_rank_latest_em",
                priority="P0",
                query='{"symbol":"100.600519"}',
                ok=True,
                status="success",
                elapsed_ms=32,
                raw_count=1,
                accepted_count=1,
                cache_status="miss",
                cache_key=None,
                payload_hash="sha256:" + ("a" * 64),
                raw_payload_ref="viking://resources/workflow/run/frontline/social_analyst/call/evidence/provider_raw/akshare_stock_hot_rank_latest_em_sha256_a.json",
                empty_reason=None,
                error=None,
                cancelled=False,
            )
        ],
        data={
            "attention_signals": [{"signal_id": "a1", "provider": "akshare"}],
            "topic_keyword_signals": [],
            "related_symbol_signals": [],
            "narrative_signals": [],
            "rejected_signals": [],
        },
        quality=SocialQuality(
            status="partial",
            attention_signal_count=1,
            topic_keyword_count=0,
            related_symbol_count=0,
            narrative_signal_count=0,
            accepted_count=1,
            missing_fields=[],
            social_judgment_allowed=True,
            warnings=[],
        ),
        reader_brief="样本窗口内有可追溯热度信号，可用于有限情绪判断。",
        evidence=SocialEvidence(
            pack_path="viking://resources/workflow/run/frontline/social_analyst/call/evidence/pack/social_sentiment_pack.json",
            provider_attempts_path="viking://resources/workflow/run/frontline/social_analyst/call/evidence/pack/provider_attempts.json",
            cache_inspection_path="viking://resources/workflow/run/frontline/social_analyst/call/evidence/pack/cache_inspection.json",
            raw_payload_refs=[
                "viking://resources/workflow/run/frontline/social_analyst/call/evidence/provider_raw/akshare_stock_hot_rank_latest_em_sha256_a.json"
            ],
            content_hash="sha256:" + ("f" * 64),
        ),
    )
