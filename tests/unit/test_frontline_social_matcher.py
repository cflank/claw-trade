from __future__ import annotations

import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.models import NewsTargetProfile  # noqa: E402
from frontline_data_pack.social_matcher import (  # noqa: E402
    bucket_social_signals,
    compute_social_evidence_strength,
    match_and_bucket_social_signals,
    match_social_signal,
)


def test_t_soc_001_match_600519_sets_matched_target_and_evidence_span() -> None:
    target = _target_profile()
    signal = match_social_signal(
        {
            "signal_type": "attention",
            "symbol": "100.600519",
            "rank": 1,
            "heat_value": 999.0,
            "provider": "akshare",
            "endpoint": "stock_hot_rank_latest_em",
            "observed_at": "2026-05-08T10:00:00+08:00",
            "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/raw/provider.json",
            "payload_hash": "sha256:" + ("a" * 64),
        },
        target,
    )

    assert signal is not None
    assert signal["matched_target"] is True
    assert signal["match_evidence_span"] == "600519"


def test_t_soc_001_global_hot_board_without_target_returns_none_and_rejected_count_increases() -> None:
    target = _target_profile()
    raw_signal = {
        "signal_type": "attention",
        "symbol": "000001",
        "name": "平安银行",
        "rank": 3,
        "heat_value": 420.0,
        "provider": "akshare",
        "endpoint": "stock_hot_rank_em",
        "observed_at": "2026-05-08T10:00:00+08:00",
        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/raw/board.json",
        "payload_hash": "sha256:" + ("b" * 64),
    }

    assert match_social_signal(raw_signal, target) is None

    buckets = match_and_bucket_social_signals([raw_signal], target)
    assert buckets.rejected_count == 1
    assert buckets.attention_signals == []


def test_t_soc_001_narrative_without_link_and_text_excerpt_must_be_rejected() -> None:
    target = _target_profile()
    raw_signal = {
        "signal_type": "narrative",
        "title": "600519 舆情变化",
        "provider": "bocha",
        "endpoint": "cn_web_search",
        "observed_at": "2026-05-08T10:00:00+08:00",
        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/raw/narrative.json",
        "payload_hash": "sha256:" + ("c" * 64),
    }

    assert match_social_signal(raw_signal, target) is None

    buckets = match_and_bucket_social_signals([raw_signal], target)
    assert buckets.rejected_count == 1
    assert buckets.narrative_signals == []


def test_t_soc_001_narrative_with_target_and_url_without_text_excerpt_must_be_accepted() -> None:
    target = _target_profile()
    raw_signal = {
        "signal_type": "narrative",
        "title": "600519 舆情变化",
        "url": "https://example.com/social/traceable-source",
        "provider": "bocha",
        "endpoint": "cn_web_search",
        "observed_at": "2026-05-08T10:00:00+08:00",
        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/raw/narrative-url.json",
        "payload_hash": "sha256:" + ("1" * 64),
    }

    matched = match_social_signal(raw_signal, target)
    assert matched is not None
    assert matched["signal_type"] == "narrative"
    assert matched["match_evidence_span"] == "600519"
    assert matched["url"] == "https://example.com/social/traceable-source"

    buckets = match_and_bucket_social_signals([raw_signal], target)
    assert buckets.rejected_count == 0
    assert len(buckets.narrative_signals) == 1


def test_t_soc_001_search_narrative_summary_only_without_source_link_must_be_rejected() -> None:
    target = _target_profile()
    raw_signal = {
        "signal_type": "narrative",
        "title": "600519 舆情变化",
        "summary": "某平台摘要提到贵州茅台走势，但未提供原文页面。",
        "provider": "bocha",
        "endpoint": "cn_web_search",
        "observed_at": "2026-05-08T10:00:00+08:00",
        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/raw/narrative-summary-only.json",
        "payload_hash": "sha256:" + ("2" * 64),
    }

    assert match_social_signal(raw_signal, target) is None

    buckets = match_and_bucket_social_signals([raw_signal], target)
    assert buckets.rejected_count == 1
    assert buckets.narrative_signals == []


def test_t_soc_001_only_platform_heat_without_text_evidence_disables_social_judgment() -> None:
    target = _target_profile()
    matched = match_social_signal(
        {
            "signal_type": "attention",
            "symbol": "600519",
            "rank": 2,
            "heat_value": 688.0,
            "provider": "akshare",
            "endpoint": "stock_hot_rank_latest_em",
            "observed_at": "2026-05-08T10:00:00+08:00",
            "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/raw/attention.json",
            "payload_hash": "sha256:" + ("d" * 64),
        },
        target,
    )
    assert matched is not None

    buckets = bucket_social_signals([matched])
    strength = compute_social_evidence_strength(buckets)

    assert strength["target_signal_count"] == 1
    assert strength["has_text_evidence"] is False
    assert strength["social_judgment_allowed"] is False


def test_t_soc_001_industry_or_market_terms_must_not_be_treated_as_target_match() -> None:
    target = _target_profile()
    raw_signal = {
        "signal_type": "topic_keyword",
        "title": "白酒板块热度上行",
        "keyword": "白酒",
        "provider": "akshare",
        "endpoint": "stock_hot_keyword_em",
        "observed_at": "2026-05-08T10:00:00+08:00",
        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/raw/keyword.json",
        "payload_hash": "sha256:" + ("e" * 64),
    }

    assert match_social_signal(raw_signal, target) is None


def test_t_soc_002_approved_alias_can_hard_match_social_signal() -> None:
    target = _target_profile()
    raw_signal = {
        "signal_type": "topic_keyword",
        "title": "茅台 热点关键词",
        "keyword": "茅台",
        "provider": "akshare",
        "endpoint": "stock_hot_keyword_em",
        "observed_at": "2026-05-08T10:00:00+08:00",
        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/raw/alias-hit.json",
        "payload_hash": "sha256:" + ("1" * 64),
    }

    matched = match_social_signal(raw_signal, target)
    assert matched is not None
    assert matched["match_evidence_span"] == "茅台"


def test_t_soc_002_without_approved_alias_alias_token_must_not_match() -> None:
    target = NewsTargetProfile(
        ticker="600519.SH",
        company_name="贵州茅台",
        approved_aliases=[],
        industry="白酒",
    )
    raw_signal = {
        "signal_type": "topic_keyword",
        "title": "茅台 热点关键词",
        "keyword": "茅台",
        "provider": "akshare",
        "endpoint": "stock_hot_keyword_em",
        "observed_at": "2026-05-08T10:00:00+08:00",
        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/raw/alias-no-approval.json",
        "payload_hash": "sha256:" + ("2" * 64),
    }

    assert match_social_signal(raw_signal, target) is None


def test_t_soc_001_output_is_json_serializable() -> None:
    target = _target_profile()
    matched = match_social_signal(
        {
            "signal_type": "attention",
            "symbol": "600519",
            "provider": "akshare",
            "endpoint": "stock_hot_rank_latest_em",
            "observed_at": "2026-05-08T10:00:00+08:00",
            "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/raw/serialize.json",
            "payload_hash": "sha256:" + ("f" * 64),
        },
        target,
    )
    assert matched is not None

    serialized = json.dumps(bucket_social_signals([matched]).to_dict(), ensure_ascii=False)
    assert "attention_signals" in serialized


def _target_profile() -> NewsTargetProfile:
    return NewsTargetProfile(
        ticker="600519.SH",
        company_name="贵州茅台",
        approved_aliases=["茅台"],
        industry="白酒",
    )
