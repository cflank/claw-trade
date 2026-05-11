from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from cache import CacheInspectionResult  # noqa: E402
from evidence import PackEvidenceWriteResult, SocialEvidence  # noqa: E402
from observability import (  # noqa: E402
    emit_json_log,
    get_recorded_logs,
    get_recorded_metrics,
    get_recorded_spans,
    reset_observability_state,
)
from orchestrator import SocialToolRuntimeContext, build_social_sentiment_pack  # noqa: E402
from profile import DateWindow, SocialToolInput  # noqa: E402
from providers import ProviderQuery, RawProviderResult, fetch_provider_payload  # noqa: E402
from quality import QualityInput, evaluate_social_quality  # noqa: E402


def test_pack_success_records_call_count_and_duration_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_observability_state()
    query = ProviderQuery(
        provider="akshare",
        endpoint="stock_hot_rank_latest_em",
        priority="P0",
        query={"symbol": "100.600519"},
        query_fingerprint="sha256:" + ("1" * 64),
        date_window=DateWindow(start_date="2026-05-01", end_date="2026-05-07", as_of_date="2026-05-07"),
        timeout_seconds=10,
    )
    monkeypatch.setattr("orchestrator.build_provider_plan", lambda profile, date_window, config: [query])
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
            ok=True,
            status="success",
            raw_payload=[{"代码": "600519", "热度": 999}],
            raw_count=1,
            elapsed_ms=30,
            empty_reason=None,
            error=None,
            payload_hash="sha256:" + ("2" * 64),
            row_count=1,
            fields={"symbol": "100.600519"},
            as_of_date="2026-05-07",
            fetched_at="2026-05-07T09:30:00+08:00",
        ),
    )
    monkeypatch.setattr(
        "orchestrator.try_write_raw_payload_evidence",
        lambda target, provider, endpoint, raw_payload: SimpleNamespace(
            ok=True,
            evidence=SimpleNamespace(
                provider=provider,
                endpoint=endpoint,
                raw_payload_ref=(
                    "viking://resources/workflow/run-1/frontline/social_analyst/call-1/"
                    "evidence/provider_raw/ok.json"
                ),
                payload_hash="sha256:" + ("3" * 64),
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
                "source_kind": "heat_rank",
                "raw_index": 0,
                "fields": {"代码": "600519", "热度": 999},
                "payload_hash": "sha256:" + ("3" * 64),
                "raw_payload_ref": raw_payload_ref,
            }
        ],
    )
    monkeypatch.setattr(
        "orchestrator.match_deduplicate_and_bucket",
        lambda rows, profile, config: SimpleNamespace(
            attention_signals=[
                {
                    "provider": "akshare",
                    "platform": "东方财富",
                    "endpoint": "stock_hot_rank_latest_em",
                    "raw_payload_ref": (
                        "viking://resources/workflow/run-1/frontline/social_analyst/call-1/"
                        "evidence/provider_raw/ok.json"
                    ),
                    "content_hash": "sha256:" + ("4" * 64),
                    "source_time": "2026-05-07T09:30:00+08:00",
                }
            ],
            topic_keyword_signals=[],
            related_symbol_signals=[],
            narrative_signals=[],
            rejected_signals=[],
        ),
    )
    monkeypatch.setattr(
        "orchestrator.build_reader_brief",
        lambda input: SimpleNamespace(text="brief"),
    )
    monkeypatch.setattr(
        "orchestrator.write_pack_evidence",
        lambda target, pack_body, attempts, cache_inspections, pack_body_hash: PackEvidenceWriteResult(
            ok=True,
            evidence=SocialEvidence(
                pack_path="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/pack/social_sentiment_pack.json",
                provider_attempts_path="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/pack/provider_attempts.json",
                cache_inspection_path="viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/pack/cache_inspection.json",
                raw_payload_refs=[
                    "viking://resources/workflow/run-1/frontline/social_analyst/call-1/evidence/provider_raw/ok.json"
                ],
                content_hash="sha256:" + ("5" * 64),
            ),
            error_code=None,
            cause_error_code=None,
            warnings=(),
        ),
    )

    pack = build_social_sentiment_pack(
        tool_input=SocialToolInput(ticker="600519", market="CN_A"),
        context=_context(),
    )

    assert pack.ok is True
    metric_names = [metric["name"] for metric in get_recorded_metrics()]
    assert "social.pack.call.count" in metric_names
    assert "social.pack.duration_ms" in metric_names


def test_provider_timeout_log_contains_endpoint_and_code(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_observability_state()
    monkeypatch.setattr(
        "providers._call_with_timeout",
        lambda timeout_seconds, call: (_ for _ in ()).throw(TimeoutError("timeout")),
    )
    query = ProviderQuery(
        provider="akshare",
        endpoint="stock_hot_rank_latest_em",
        priority="P0",
        query={"symbol": "100.600519"},
        query_fingerprint="sha256:" + ("6" * 64),
        date_window=DateWindow(start_date="2026-05-01", end_date="2026-05-07", as_of_date="2026-05-07"),
        timeout_seconds=10,
    )
    fetch_provider_payload(query)

    logs = get_recorded_logs()
    timeout_log = next(record for record in logs if record.get("event") == "social.provider.timeout")
    assert timeout_log["endpoint"] == "stock_hot_rank_latest_em"
    assert timeout_log["code"] == "SOCIAL_PROVIDER_TIMEOUT"


def test_log_sanitizer_redacts_mongodb_uri() -> None:
    reset_observability_state()
    log = emit_json_log(
        level="INFO",
        event="social.config.load",
        code="OK",
        endpoint="config",
        fields={"mongodb_uri": "mongodb://user:pass@localhost:27017/claw_trade"},
    )

    assert log["mongodb_uri"] != "mongodb://user:pass@localhost:27017/claw_trade"
    assert "mongodb://" not in str(log)


def test_quality_evaluate_creates_trace_span_when_trace_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_observability_state()
    monkeypatch.setenv("CN_A_SOCIAL_TRACE_ENABLED", "true")

    decision = evaluate_social_quality(
        QualityInput(
            attempts=(
                {"priority": "P0", "ok": True, "status": "success"},
                {"priority": "P0", "ok": True, "status": "success"},
            ),
            buckets=SimpleNamespace(
                attention_signals=[
                    {
                        "provider": "akshare",
                        "platform": "东方财富",
                        "endpoint": "stock_hot_rank_latest_em",
                        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/call/evidence/provider_raw/a.json",
                        "content_hash": "sha256:" + ("a" * 64),
                        "rank_change": 1,
                        "source_time": "2026-05-07T09:30:00+08:00",
                    }
                ],
                topic_keyword_signals=[
                    {
                        "provider": "akshare",
                        "platform": "东方财富",
                        "endpoint": "stock_hot_keyword_em",
                        "raw_payload_ref": "viking://resources/workflow/run/frontline/social_analyst/call/evidence/provider_raw/b.json",
                        "content_hash": "sha256:" + ("b" * 64),
                        "rank_change": 1,
                        "source_time": "2026-05-07T09:30:00+08:00",
                    }
                ],
                related_symbol_signals=[],
                narrative_signals=[],
                rejected_signals=[],
            ),
            required_p0_endpoints=("stock_hot_rank_latest_em", "stock_hot_keyword_em"),
        )
    )

    assert decision.status == "complete"
    spans = get_recorded_spans()
    assert any(span.get("name") == "social.quality.evaluate" for span in spans)


def _context() -> SocialToolRuntimeContext:
    return SocialToolRuntimeContext(
        run_id="run-1",
        stage="frontline",
        worker_id="social_analyst",
        call_id="call-1",
        tool_name="social_social_sentiment_pack",
        evidence_root="runs",
        current_time="2026-05-07T09:30:00+08:00",
    )
