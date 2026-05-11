from __future__ import annotations

from dataclasses import replace
import sys
from pathlib import Path
from threading import Barrier, Lock
from typing import Any

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cache import CacheInspectionResult  # noqa: E402
from config import SocialDataConfig  # noqa: E402
from evidence import RawPayloadEvidence, RawPayloadEvidenceResult  # noqa: E402
from orchestrator import (  # noqa: E402
    SOCIAL_INVALID_TICKER,
    SOCIAL_PROVIDER_PLAN_EMPTY,
    SocialToolRuntimeContext,
    build_social_sentiment_pack,
    _execute_provider_plan,
)
from pack_schema import ProviderAttempt, ProviderExecutionResult  # noqa: E402
from profile import DateWindow, SocialToolInput  # noqa: E402
from providers import ProviderQuery, RawProviderResult  # noqa: E402


def test_build_social_sentiment_pack_for_valid_cn_a_input_builds_profile_window_and_non_empty_plan() -> None:
    pack = build_social_sentiment_pack(
        tool_input=SocialToolInput(
            ticker="600519",
            market="CN_A",
            company_name="贵州茅台",
            industry="白酒",
        ),
        context=_build_context(),
    )

    assert pack.profile.ticker == "600519.SH"
    assert pack.query_plan.provider_endpoints
    assert "600519.SH" in pack.query_plan.keywords


def test_build_social_sentiment_pack_returns_failed_pack_for_invalid_ticker() -> None:
    pack = build_social_sentiment_pack(
        tool_input=SocialToolInput(
            ticker="12345",
            market="CN_A",
        ),
        context=_build_context(),
    )

    assert pack.ok is False
    assert pack.quality.status == "failed"
    assert _first_warning_code(pack) == SOCIAL_INVALID_TICKER


def test_build_social_sentiment_pack_returns_plan_empty_when_provider_plan_is_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("orchestrator.build_provider_plan", lambda profile, date_window, config: [])

    pack = build_social_sentiment_pack(
        tool_input=SocialToolInput(
            ticker="600519",
            market="CN_A",
        ),
        context=_build_context(),
    )

    assert pack.ok is False
    assert pack.quality.status == "failed"
    assert _first_warning_code(pack) == SOCIAL_PROVIDER_PLAN_EMPTY


def test_build_social_sentiment_pack_keeps_query_window_equal_to_input_dates() -> None:
    pack = build_social_sentiment_pack(
        tool_input=SocialToolInput(
            ticker="600519",
            market="CN_A",
            start_date="2026-05-01",
            end_date="2026-05-07",
        ),
        context=_build_context(),
    )

    assert pack.query_plan.start_date == "2026-05-01"
    assert pack.query_plan.end_date == "2026-05-07"


def test_build_social_sentiment_pack_cache_hit_skips_live_fetch_and_emits_hit_attempt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _provider_query(endpoint="stock_hot_rank_latest_em", query_fingerprint="fingerprint-hit")
    monkeypatch.setattr("orchestrator.build_provider_plan", lambda profile, date_window, config: [query])
    monkeypatch.setattr(
        "orchestrator.inspect_provider_cache",
        lambda key, now_iso, config, call_state=None: CacheInspectionResult(
            status="hit",
            cache_key="cache-hit-key",
            record={
                "fields": {"symbol": "100.600519"},
                "as_of_date": "2026-05-07",
                "fetched_at": "2026-05-07T09:30:00+08:00",
            },
            reason=None,
            payload_hash="sha256:" + ("1" * 64),
            raw_payload_ref=(
                "viking://resources/workflow/run-1/frontline/social_analyst/call-1/"
                "evidence/provider_raw/hit.json"
            ),
        ),
    )
    monkeypatch.setattr(
        "orchestrator.load_rows_by_raw_payload_ref",
        lambda ref, expected_hash: [{"代码": "600519", "热度": 999}],
    )
    monkeypatch.setattr(
        "orchestrator.normalize_provider_payload",
        lambda query, raw_result, raw_payload_ref: [
            {
                "provider": query.provider,
                "endpoint": query.endpoint,
                "platform": "东方财富",
                "source_kind": "heat_rank",
                "raw_index": 0,
                "fields": {"代码": "600519", "热度": 999},
                "payload_hash": "sha256:" + ("1" * 64),
                "raw_payload_ref": raw_payload_ref,
            }
        ],
    )
    fetch_calls = {"count": 0}

    def _capture_fetch(query: ProviderQuery, ticker_plain: str | None = None) -> RawProviderResult:
        fetch_calls["count"] += 1
        return _success_raw_result(query, payload=[{"代码": "600519"}])

    monkeypatch.setattr("orchestrator.fetch_provider_payload", _capture_fetch)

    pack = build_social_sentiment_pack(
        tool_input=SocialToolInput(ticker="600519", market="CN_A"),
        context=_build_context(),
        config=_build_config(),
    )

    assert fetch_calls["count"] == 0
    assert len(pack.provider_attempts) == 1
    assert pack.provider_attempts[0].cache_status == "hit"
    assert pack.provider_attempts[0].status == "success"
    assert pack.provider_attempts[0].ok is True


def test_build_social_sentiment_pack_cache_stale_calls_live_provider_and_keeps_stale_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    query = _provider_query(endpoint="stock_hot_rank_latest_em", query_fingerprint="fingerprint-stale")
    monkeypatch.setattr("orchestrator.build_provider_plan", lambda profile, date_window, config: [query])
    monkeypatch.setattr(
        "orchestrator.inspect_provider_cache",
        lambda key, now_iso, config, call_state=None: CacheInspectionResult(
            status="stale",
            cache_key="cache-stale-key",
            record=None,
            reason="SOCIAL_CACHE_STALE",
            payload_hash=None,
            raw_payload_ref=None,
        ),
    )
    fetch_calls = {"count": 0}

    def _capture_fetch(query: ProviderQuery, ticker_plain: str | None = None) -> RawProviderResult:
        fetch_calls["count"] += 1
        return _success_raw_result(query, payload=[{"代码": "600519", "热度": 888}])

    monkeypatch.setattr("orchestrator.fetch_provider_payload", _capture_fetch)
    monkeypatch.setattr(
        "orchestrator.try_write_raw_payload_evidence",
        lambda target, provider, endpoint, raw_payload: RawPayloadEvidenceResult(
            ok=True,
            evidence=RawPayloadEvidence(
                provider=provider,
                endpoint=endpoint,
                raw_index=-1,
                raw_payload_ref=(
                    "viking://resources/workflow/run-1/frontline/social_analyst/call-1/"
                    "evidence/provider_raw/live-stale.json"
                ),
                local_audit_path="runs/run-1/frontline/social_analyst/call-1/provider_raw/live-stale.json",
                payload_hash="sha256:" + ("2" * 64),
                redacted=True,
                row_count=1,
            ),
            error_code=None,
        ),
    )
    monkeypatch.setattr(
        "orchestrator.upsert_provider_cache",
        lambda key, raw_result, raw_payload_ref, ttl_seconds, now_iso, config: CacheInspectionResult(
            status="miss",
            cache_key=key.cache_id(),
            record=None,
            reason=None,
            payload_hash="sha256:" + ("2" * 64),
            raw_payload_ref=raw_payload_ref,
        ),
    )
    monkeypatch.setattr(
        "orchestrator.normalize_provider_payload",
        lambda query, raw_result, raw_payload_ref: [
            {
                "provider": query.provider,
                "endpoint": query.endpoint,
                "platform": "东方财富",
                "source_kind": "heat_rank",
                "raw_index": 0,
                "fields": {"代码": "600519", "热度": 888},
                "payload_hash": "sha256:" + ("2" * 64),
                "raw_payload_ref": raw_payload_ref,
            }
        ],
    )

    pack = build_social_sentiment_pack(
        tool_input=SocialToolInput(ticker="600519", market="CN_A"),
        context=_build_context(),
        config=_build_config(),
    )

    assert fetch_calls["count"] == 1
    assert len(pack.provider_attempts) == 1
    assert pack.provider_attempts[0].cache_status == "stale"
    assert pack.provider_attempts[0].status == "success"
    assert pack.provider_attempts[0].ok is True


def test_build_social_sentiment_pack_single_provider_timeout_does_not_stop_other_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timeout_query = _provider_query(endpoint="stock_hot_rank_latest_em", query_fingerprint="fingerprint-timeout")
    success_query = _provider_query(endpoint="stock_hot_keyword_em", query_fingerprint="fingerprint-success")
    monkeypatch.setattr("orchestrator.build_provider_plan", lambda profile, date_window, config: [timeout_query, success_query])
    monkeypatch.setattr(
        "orchestrator.inspect_provider_cache",
        lambda key, now_iso, config, call_state=None: CacheInspectionResult(
            status="miss",
            cache_key=key.cache_id(),
            record=None,
            reason="SOCIAL_CACHE_MISS",
            payload_hash=None,
            raw_payload_ref=None,
        ),
    )

    def _fetch_by_endpoint(query: ProviderQuery, ticker_plain: str | None = None) -> RawProviderResult:
        if query.endpoint == timeout_query.endpoint:
            return RawProviderResult(
                provider=query.provider,
                endpoint=query.endpoint,
                query=dict(query.query),
                ok=False,
                status="timeout",
                raw_payload=None,
                raw_count=0,
                elapsed_ms=10_000,
                empty_reason=None,
                error={"code": "SOCIAL_PROVIDER_TIMEOUT", "message": "endpoint 调用超时（10s）"},
                payload_hash=None,
                row_count=0,
                fields={"symbol": "100.600519"},
                as_of_date="2026-05-07",
                fetched_at="2026-05-07T09:30:00+08:00",
            )
        return _success_raw_result(query, payload=[{"关键词": "白酒"}])

    monkeypatch.setattr("orchestrator.fetch_provider_payload", _fetch_by_endpoint)
    monkeypatch.setattr(
        "orchestrator.try_write_raw_payload_evidence",
        lambda target, provider, endpoint, raw_payload: RawPayloadEvidenceResult(
            ok=True,
            evidence=RawPayloadEvidence(
                provider=provider,
                endpoint=endpoint,
                raw_index=-1,
                raw_payload_ref=(
                    "viking://resources/workflow/run-1/frontline/social_analyst/call-1/"
                    "evidence/provider_raw/live-timeout-suite.json"
                ),
                local_audit_path="runs/run-1/frontline/social_analyst/call-1/provider_raw/live-timeout-suite.json",
                payload_hash="sha256:" + ("3" * 64),
                redacted=True,
                row_count=1,
            ),
            error_code=None,
        ),
    )
    monkeypatch.setattr(
        "orchestrator.upsert_provider_cache",
        lambda key, raw_result, raw_payload_ref, ttl_seconds, now_iso, config: CacheInspectionResult(
            status="miss",
            cache_key=key.cache_id(),
            record=None,
            reason=None,
            payload_hash="sha256:" + ("3" * 64),
            raw_payload_ref=raw_payload_ref,
        ),
    )
    monkeypatch.setattr(
        "orchestrator.normalize_provider_payload",
        lambda query, raw_result, raw_payload_ref: [
            {
                "provider": query.provider,
                "endpoint": query.endpoint,
                "platform": "东方财富",
                "source_kind": "heat_keyword",
                "raw_index": 0,
                "fields": {"关键词": "白酒"},
                "payload_hash": "sha256:" + ("3" * 64),
                "raw_payload_ref": raw_payload_ref,
            }
        ],
    )

    pack = build_social_sentiment_pack(
        tool_input=SocialToolInput(ticker="600519", market="CN_A"),
        context=_build_context(),
        config=_build_config(),
    )

    assert len(pack.provider_attempts) == 2
    attempts_by_endpoint = {attempt.endpoint: attempt for attempt in pack.provider_attempts}
    assert attempts_by_endpoint[timeout_query.endpoint].status == "timeout"
    assert attempts_by_endpoint[timeout_query.endpoint].ok is False
    assert attempts_by_endpoint[success_query.endpoint].status == "success"
    assert attempts_by_endpoint[success_query.endpoint].ok is True


def test_execute_provider_plan_uses_provider_max_concurrency(monkeypatch: pytest.MonkeyPatch) -> None:
    query_a = _provider_query(endpoint="stock_hot_rank_latest_em", query_fingerprint="fingerprint-concurrency-a")
    query_b = _provider_query(endpoint="stock_hot_keyword_em", query_fingerprint="fingerprint-concurrency-b")
    barrier = Barrier(2, timeout=2)
    lock = Lock()
    active_count = 0
    max_active_count = 0

    def _fake_execute_provider_query(**kwargs: Any) -> ProviderExecutionResult:
        nonlocal active_count, max_active_count
        query = kwargs["query"]
        with lock:
            active_count += 1
            max_active_count = max(max_active_count, active_count)
        try:
            barrier.wait()
        finally:
            with lock:
                active_count -= 1
        return ProviderExecutionResult(
            query={"endpoint": query.endpoint},
            attempt=ProviderAttempt(
                provider=query.provider,
                endpoint=query.endpoint,
                priority=query.priority,
                query=query.query_fingerprint,
                ok=True,
                status="success",
                elapsed_ms=1,
                raw_count=0,
                accepted_count=0,
                cache_status="not_configured",
                cache_key=None,
                payload_hash=None,
                raw_payload_ref=None,
                empty_reason=None,
                error=None,
                cancelled=False,
            ),
            raw_rows=[],
            raw_payload_ref=None,
            payload_hash=None,
            cache_inspection={"endpoint": query.endpoint, "status": "not_configured"},
        )

    monkeypatch.setattr("orchestrator._execute_provider_query", _fake_execute_provider_query)

    results, cache_inspections, processed_count = _execute_provider_plan(
        provider_plan=[query_a, query_b],
        target_ticker="600519.SH",
        target_ticker_plain="600519",
        date_window=query_a.date_window,
        context=_build_context(),
        config=replace(_build_config(), provider_max_concurrency=2),
        cache_call_state=None,  # type: ignore[arg-type]
        deadline_at=999999999.0,
        started_at=0.0,
    )

    assert processed_count == 2
    assert [result.attempt.endpoint for result in results] == [query_a.endpoint, query_b.endpoint]
    assert [inspection["endpoint"] for inspection in cache_inspections] == [query_a.endpoint, query_b.endpoint]
    assert max_active_count == 2


def test_build_social_sentiment_pack_marks_unfinished_queries_cancelled_after_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_query = _provider_query(endpoint="stock_hot_rank_latest_em", query_fingerprint="fingerprint-deadline-a")
    second_query = _provider_query(endpoint="stock_hot_keyword_em", query_fingerprint="fingerprint-deadline-b")
    monkeypatch.setattr("orchestrator.build_provider_plan", lambda profile, date_window, config: [first_query, second_query])
    deadline_flags = iter([False, True])
    monkeypatch.setattr("orchestrator._deadline_exceeded", lambda deadline_at: next(deadline_flags))
    monkeypatch.setattr(
        "orchestrator.inspect_provider_cache",
        lambda key, now_iso, config, call_state=None: CacheInspectionResult(
            status="miss",
            cache_key=key.cache_id(),
            record=None,
            reason="SOCIAL_CACHE_MISS",
            payload_hash=None,
            raw_payload_ref=None,
        ),
    )
    monkeypatch.setattr(
        "orchestrator.fetch_provider_payload",
        lambda query, ticker_plain=None: RawProviderResult(
            provider=query.provider,
            endpoint=query.endpoint,
            query=dict(query.query),
            ok=False,
            status="timeout",
            raw_payload=None,
            raw_count=0,
            elapsed_ms=10_000,
            empty_reason=None,
            error={"code": "SOCIAL_PROVIDER_TIMEOUT", "message": "endpoint 调用超时（10s）"},
            payload_hash=None,
            row_count=0,
            fields={"symbol": "100.600519"},
            as_of_date="2026-05-07",
            fetched_at="2026-05-07T09:30:00+08:00",
        ),
    )

    pack = build_social_sentiment_pack(
        tool_input=SocialToolInput(ticker="600519", market="CN_A"),
        context=_build_context(),
        config=_build_config(),
    )

    assert len(pack.provider_attempts) == 2
    attempts_by_endpoint = {attempt.endpoint: attempt for attempt in pack.provider_attempts}
    assert attempts_by_endpoint[first_query.endpoint].status == "timeout"
    assert attempts_by_endpoint[second_query.endpoint].status == "cancelled"
    assert attempts_by_endpoint[second_query.endpoint].cancelled is True


def _build_context() -> SocialToolRuntimeContext:
    return SocialToolRuntimeContext(
        run_id="run-1",
        stage="frontline",
        worker_id="social_analyst",
        call_id="call-1",
        tool_name="social_social_sentiment_pack",
        evidence_root="runs",
        current_time="2026-05-07T09:30:00+08:00",
    )


def _first_warning_code(pack: object) -> str | None:
    quality = getattr(pack, "quality", None)
    warnings = getattr(quality, "warnings", None)
    if not isinstance(warnings, list) or not warnings:
        return None
    first = warnings[0]
    if not isinstance(first, dict):
        return None
    code = first.get("code")
    if isinstance(code, str):
        return code
    return None


def _build_config() -> SocialDataConfig:
    return SocialDataConfig(
        schema_version="cn_a_social_pack.v1",
        provider_timeout_seconds=10,
        pack_timeout_seconds=20,
        provider_max_concurrency=3,
        max_signals_per_bucket=50,
        mongodb_uri=None,
        mongodb_database="claw_trade",
        mongodb_cache_collection="social_provider_cache",
        cache_required=False,
        ttl_by_endpoint={
            "stock_hot_rank_latest_em": 1800,
            "stock_hot_rank_em": 1800,
            "stock_hot_up_em": 1800,
            "stock_hot_keyword_em": 3600,
            "stock_hot_rank_relate_em": 3600,
        },
        p1_hot_up_enabled=True,
        p1_xueqiu_enabled=False,
        evidence_root="runs",
        openviking_l2_write_target_root=None,
    )


def _provider_query(*, endpoint: str, query_fingerprint: str) -> ProviderQuery:
    return ProviderQuery(
        provider="akshare",
        endpoint=endpoint,
        priority="P0",
        query={"symbol": "100.600519"},
        query_fingerprint=query_fingerprint,
        date_window=DateWindow(start_date="2026-05-01", end_date="2026-05-07", as_of_date="2026-05-07"),
        timeout_seconds=10,
    )


def _success_raw_result(query: ProviderQuery, *, payload: list[dict[str, Any]]) -> RawProviderResult:
    return RawProviderResult(
        provider=query.provider,
        endpoint=query.endpoint,
        query=dict(query.query),
        ok=True,
        status="success",
        raw_payload=payload,
        raw_count=len(payload),
        elapsed_ms=35,
        empty_reason=None,
        error=None,
        payload_hash="sha256:" + ("9" * 64),
        row_count=len(payload),
        fields={"symbol": "100.600519", "as_of_date": "2026-05-07"},
        as_of_date="2026-05-07",
        fetched_at="2026-05-07T09:30:00+08:00",
    )
