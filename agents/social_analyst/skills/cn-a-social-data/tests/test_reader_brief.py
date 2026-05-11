from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from reader_brief import (  # noqa: E402
    SOCIAL_BRIEF_FORBIDDEN_CLAIM,
    ReaderBriefInput,
    build_reader_brief,
)


def test_build_reader_brief_complete_includes_endpoint_success_and_bucket_counts() -> None:
    brief = build_reader_brief(
        ReaderBriefInput(
            profile=SimpleNamespace(ticker="600519.SH"),
            attempts=(
                _attempt(provider="akshare", endpoint="stock_hot_rank_latest_em", ok=True, status="success", cache_status="hit"),
                _attempt(provider="akshare", endpoint="stock_hot_keyword_em", ok=True, status="success", cache_status="miss"),
                _attempt(provider="akshare", endpoint="stock_hot_rank_relate_em", ok=False, status="empty", cache_status="stale"),
                _attempt(provider="akshare", endpoint="stock_hot_rank_em", ok=False, status="error", cache_status="schema_invalid"),
            ),
            buckets=_buckets(
                attention_signals=[_signal(match_type="exact_ticker", source_time="2026-05-05T10:00:00+08:00")],
                topic_keyword_signals=[_signal(match_type="company_name", source_time="2026-05-06T10:00:00+08:00")],
                related_symbol_signals=[_signal(match_type="related_symbol_only", source_time="2026-05-07T10:00:00+08:00")],
                narrative_signals=[_signal(match_type="provider_target_symbol", source_time="2026-05-07T11:00:00+08:00")],
            ),
            quality=SimpleNamespace(
                status="complete",
                missing_fields=[],
                warnings=[],
                reason_codes=("COMPLETE_CONDITIONS_MET",),
            ),
            date_window=SimpleNamespace(start_date="2026-05-01", end_date="2026-05-07"),
        )
    )

    assert "覆盖 4 个 provider endpoint" in brief.text
    assert "成功 2 个" in brief.text
    assert "空结果 1 个" in brief.text
    assert "失败或超时 1 个" in brief.text
    assert "热度 1 条" in brief.text
    assert "关键词 1 条" in brief.text
    assert "相关标的 1 条" in brief.text
    assert "事实型叙事 1 条" in brief.text


def test_build_reader_brief_partial_contains_evidence_limitation_statement() -> None:
    brief = build_reader_brief(
        ReaderBriefInput(
            profile=SimpleNamespace(ticker="600519.SH"),
            attempts=(_attempt(provider="akshare", endpoint="stock_hot_rank_latest_em", ok=True, status="success", cache_status="miss"),),
            buckets=_buckets(
                attention_signals=[_signal(match_type="exact_ticker", source_time="2026-05-07T10:00:00+08:00")],
            ),
            quality=SimpleNamespace(
                status="partial",
                missing_fields=["rank_change_or_source_time"],
                warnings=[{"code": "SOURCE_CONCENTRATED", "message": "线索来源集中"}],
                reason_codes=("SOURCE_CONCENTRATED",),
            ),
            date_window=SimpleNamespace(start_date="2026-05-01", end_date="2026-05-07"),
        )
    )

    assert "证据限制：" in brief.text
    assert "限制性事实说明" in brief.text


def test_build_reader_brief_failed_states_insufficient_for_social_judgment() -> None:
    brief = build_reader_brief(
        ReaderBriefInput(
            profile=SimpleNamespace(ticker="600519.SH"),
            attempts=(_attempt(provider="akshare", endpoint="stock_hot_rank_latest_em", ok=False, status="timeout", cache_status="miss"),),
            buckets=_buckets(),
            quality=SimpleNamespace(
                status="failed",
                missing_fields=[],
                warnings=[],
                reason_codes=("P0_ALL_FAILED",),
            ),
            date_window=SimpleNamespace(start_date="2026-05-01", end_date="2026-05-07"),
        )
    )

    assert "不足以支持社交情绪判断" in brief.text


def test_build_reader_brief_returns_limited_summary_when_forbidden_claim_detected() -> None:
    brief = build_reader_brief(
        ReaderBriefInput(
            profile=SimpleNamespace(ticker="600519.SH"),
            attempts=(_attempt(provider="买入雷达", endpoint="stock_hot_rank_latest_em", ok=True, status="success", cache_status="hit"),),
            buckets=_buckets(
                attention_signals=[_signal(match_type="exact_ticker", source_time="2026-05-07T10:00:00+08:00")],
            ),
            quality=SimpleNamespace(
                status="complete",
                missing_fields=[],
                warnings=[],
                reason_codes=("COMPLETE_CONDITIONS_MET",),
            ),
            date_window=SimpleNamespace(start_date="2026-05-01", end_date="2026-05-07"),
        )
    )

    assert brief.text == "本摘要仅保留数据统计、来源与缺口信息，其他结论已限制输出。"
    assert brief.limitation_codes == (SOCIAL_BRIEF_FORBIDDEN_CLAIM,)


def _attempt(*, provider: str, endpoint: str, ok: bool, status: str, cache_status: str) -> dict[str, object]:
    return {
        "provider": provider,
        "endpoint": endpoint,
        "priority": "P0",
        "query": "{}",
        "ok": ok,
        "status": status,
        "elapsed_ms": 18,
        "raw_count": 1,
        "accepted_count": 1,
        "cache_status": cache_status,
        "cache_key": None,
        "payload_hash": "sha256:" + ("2" * 64),
        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/call/evidence/provider_raw/row.json",
        "empty_reason": None,
        "error": {"code": "SOCIAL_TIMEOUT", "message": "超时"} if status == "timeout" else None,
        "cancelled": False,
    }


def _signal(*, match_type: str, source_time: str) -> dict[str, object]:
    return {
        "provider": "akshare",
        "platform": "eastmoney",
        "endpoint": "stock_hot_rank_latest_em",
        "match_type": match_type,
        "source_time": source_time,
        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/call/evidence/provider_raw/signal.json",
        "content_hash": "sha256:" + ("a" * 64),
    }


def _buckets(
    *,
    attention_signals: list[dict[str, object]] | None = None,
    topic_keyword_signals: list[dict[str, object]] | None = None,
    related_symbol_signals: list[dict[str, object]] | None = None,
    narrative_signals: list[dict[str, object]] | None = None,
    rejected_signals: list[dict[str, object]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        attention_signals=attention_signals or [],
        topic_keyword_signals=topic_keyword_signals or [],
        related_symbol_signals=related_symbol_signals or [],
        narrative_signals=narrative_signals or [],
        rejected_signals=rejected_signals or [],
    )
