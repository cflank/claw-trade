from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from quality import (  # noqa: E402
    COMPLETE_CONDITIONS_MET,
    EVIDENCE_REF_MISSING,
    ONLY_RELATED_SYMBOLS,
    P0_ALL_FAILED,
    QualityInput,
    evaluate_social_quality,
)


def test_evaluate_social_quality_fails_when_all_p0_attempts_failed() -> None:
    decision = evaluate_social_quality(
        QualityInput(
            attempts=(
                _attempt(priority="P0", ok=False, status="timeout"),
                _attempt(priority="P0", ok=False, status="error"),
            ),
            buckets=_buckets(),
            required_p0_endpoints=("stock_hot_rank_latest_em", "stock_hot_keyword_em"),
        )
    )

    assert decision.ok is False
    assert decision.status == "failed"
    assert P0_ALL_FAILED in decision.reason_codes


def test_evaluate_social_quality_returns_complete_for_heat_and_keyword_with_full_evidence() -> None:
    decision = evaluate_social_quality(
        QualityInput(
            attempts=(
                _attempt(priority="P0", ok=True, status="success"),
                _attempt(priority="P0", ok=True, status="success"),
            ),
            buckets=_buckets(
                attention_signals=[
                    _signal(
                        provider="akshare",
                        platform="eastmoney",
                        endpoint="stock_hot_rank_latest_em",
                        rank_change=2,
                        source_time="2026-05-07T10:00:00+08:00",
                    )
                ],
                topic_keyword_signals=[
                    _signal(
                        provider="akshare",
                        platform="eastmoney",
                        endpoint="stock_hot_keyword_em",
                        rank_change=1,
                        source_time="2026-05-07T10:00:00+08:00",
                    )
                ],
            ),
            required_p0_endpoints=("stock_hot_rank_latest_em", "stock_hot_keyword_em"),
        )
    )

    assert decision.ok is True
    assert decision.status == "complete"
    assert decision.social_judgment_allowed is True
    assert COMPLETE_CONDITIONS_MET in decision.reason_codes


def test_evaluate_social_quality_returns_partial_and_disables_judgment_for_related_only() -> None:
    decision = evaluate_social_quality(
        QualityInput(
            attempts=(_attempt(priority="P0", ok=True, status="success"),),
            buckets=_buckets(
                related_symbol_signals=[
                    _signal(
                        provider="akshare",
                        platform="eastmoney",
                        endpoint="stock_hot_rank_relate_em",
                        rank_change=3,
                        source_time="2026-05-07T10:00:00+08:00",
                    )
                ],
            ),
            required_p0_endpoints=("stock_hot_rank_latest_em",),
        )
    )

    assert decision.ok is True
    assert decision.status == "partial"
    assert decision.social_judgment_allowed is False
    assert ONLY_RELATED_SYMBOLS in decision.reason_codes


def test_evaluate_social_quality_fails_when_accepted_signal_missing_raw_ref_or_hash() -> None:
    decision = evaluate_social_quality(
        QualityInput(
            attempts=(_attempt(priority="P0", ok=True, status="success"),),
            buckets=_buckets(
                attention_signals=[
                    _signal(
                        provider="akshare",
                        platform="eastmoney",
                        endpoint="stock_hot_rank_latest_em",
                        raw_payload_ref="",
                    )
                ]
            ),
            required_p0_endpoints=("stock_hot_rank_latest_em",),
        )
    )

    assert decision.ok is False
    assert decision.status == "failed"
    assert EVIDENCE_REF_MISSING in decision.reason_codes


def _attempt(*, priority: str, ok: bool, status: str) -> dict[str, object]:
    return {
        "provider": "akshare",
        "endpoint": "stock_hot_rank_latest_em",
        "priority": priority,
        "query": "{}",
        "ok": ok,
        "status": status,
        "elapsed_ms": 10,
        "raw_count": 1,
        "accepted_count": 1,
        "cache_status": "miss",
        "cache_key": None,
        "payload_hash": "sha256:" + ("1" * 64),
        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/call/evidence/provider_raw/a.json",
        "empty_reason": None,
        "error": None,
        "cancelled": False,
    }


def _signal(
    *,
    provider: str,
    platform: str,
    endpoint: str,
    raw_payload_ref: str = "viking://resources/workflow/run/frontline/social_analyst/call/evidence/provider_raw/signal.json",
    content_hash: str = "sha256:" + ("a" * 64),
    rank_change: int | None = None,
    source_time: str | None = None,
) -> dict[str, object]:
    return {
        "provider": provider,
        "platform": platform,
        "endpoint": endpoint,
        "raw_payload_ref": raw_payload_ref,
        "content_hash": content_hash,
        "rank_change": rank_change,
        "source_time": source_time,
    }


def _buckets(
    *,
    attention_signals: list[dict[str, object]] | None = None,
    topic_keyword_signals: list[dict[str, object]] | None = None,
    related_symbol_signals: list[dict[str, object]] | None = None,
    narrative_signals: list[dict[str, object]] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        attention_signals=attention_signals or [],
        topic_keyword_signals=topic_keyword_signals or [],
        related_symbol_signals=related_symbol_signals or [],
        narrative_signals=narrative_signals or [],
        rejected_signals=[],
    )
