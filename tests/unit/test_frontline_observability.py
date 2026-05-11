from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.observability import (  # noqa: E402
    emit_structured_log,
    record_brief_build,
    record_brief_validation_failed,
    record_fundamental_conflict_total,
    record_fundamental_field_mapped_total,
    record_fundamental_missing_core_total,
    record_l2_write_bytes,
    record_market_chart,
    record_market_provider_attempt,
    record_market_rows,
    record_mongo_upsert,
    record_l2_write,
    record_mongo_cache_inspect,
    record_mongo_latency,
    record_news_accepted_total,
    record_news_context_error,
    record_news_raw_total,
    record_pack_build,
    record_pack_schema_error,
    record_provider_attempt,
    record_provider_rows,
    record_social_evidence_write_failed_total,
    record_social_signal_accepted_total,
    record_social_signal_rejected_total,
    record_span,
    record_techlab_chart_total,
    record_techlab_indicator_total,
    reset_observability_state,
    snapshot_observability_state,
)


def setup_function() -> None:
    reset_observability_state()


def _metric_key(name: str, **labels: str) -> str:
    if not labels:
        return name
    payload = ",".join(f"{key}={value}" for key, value in sorted(labels.items()))
    return f"{name}|{payload}"


def test_t_obs_001_provider_attempt_metric_increments_with_labels() -> None:
    record_provider_attempt(
        domain="news",
        provider="akshare",
        endpoint="stock_news_em",
        status="success",
        elapsed_ms=123,
    )
    snapshot = snapshot_observability_state()
    assert snapshot["counters"][
        _metric_key(
            "provider_attempt_total",
            domain="news",
            provider="akshare",
            endpoint="stock_news_em",
            status="success",
        )
    ] == 1


def test_t_obs_001_l2_write_failure_metric_increments() -> None:
    record_l2_write(kind="provider_raw", status="failure")
    snapshot = snapshot_observability_state()
    assert snapshot["counters"][_metric_key("l2_write_total", kind="provider_raw", status="failure")] == 1


def test_t_obs_001_structured_log_contains_required_fields_and_masks_secrets() -> None:
    payload = {
        "run_id": "run-1",
        "dispatch_id": "dispatch-1",
        "call_id": "call-1",
        "stage": "frontline",
        "worker_id": "news_analyst",
        "tool_name": "news_news_data_pack",
        "domain": "news",
        "ticker": "600519.SH",
        "provider": "akshare",
        "endpoint": "stock_news_em",
        "role": "primary",
        "attempt_status": "error",
        "quality_status": "partial",
        "status": "error",
        "elapsed_ms": 456,
        "timeout_ms": 1000,
        "raw_count": 9,
        "accepted_count": 3,
        "error_code": "PROVIDER_ERROR",
        "evidence_kind": "provider_raw",
        "l2_ref_present": False,
        "mongo_ref_present": True,
        "Authorization": "Bearer really-secret-token",
        "token": "token-abc-123",
        "api_key": "api-key-abcdef-123456",
        "openviking_token": "ovk-super-secret-token",
        "mongodb_uri": "mongodb://user:pass@host/db?authSource=admin",
        "signed_url": "https://a.b/path?token=signed-token&signature=sig-1",
    }
    sanitized = emit_structured_log(payload)
    for field in (
        "run_id",
        "dispatch_id",
        "call_id",
        "stage",
        "worker_id",
        "tool_name",
        "domain",
        "ticker",
        "provider",
        "endpoint",
        "role",
        "attempt_status",
        "quality_status",
        "elapsed_ms",
        "timeout_ms",
        "raw_count",
        "accepted_count",
        "error_code",
        "evidence_kind",
        "l2_ref_present",
        "mongo_ref_present",
    ):
        assert field in sanitized
    raw_json = json.dumps(sanitized, ensure_ascii=False)
    assert "really-secret-token" not in raw_json
    assert "token-abc-123" not in raw_json
    assert "api-key-abcdef-123456" not in raw_json
    assert "ovk-super-secret-token" not in raw_json
    assert "user:pass" not in raw_json
    assert "signed-token" not in raw_json
    assert "sig-1" not in raw_json
    assert "Authorization" in sanitized
    assert sanitized["Authorization"] == "***"


def test_t_obs_001_required_metric_names_are_emitted() -> None:
    record_pack_build(domain="market", status="complete", elapsed_ms=10)
    record_pack_schema_error(domain="market")
    record_provider_attempt(
        domain="market",
        provider="akshare",
        endpoint="stock_zh_a_hist",
        status="success",
        elapsed_ms=20,
    )
    record_provider_rows(domain="market", provider="akshare", endpoint="stock_zh_a_hist", rows=8)
    record_mongo_cache_inspect(status="cache_hit")
    record_mongo_latency(operation="cache_inspect", elapsed_ms=30)
    record_mongo_upsert(kind="provider_cache", status="success")
    record_l2_write(kind="provider_attempts", status="success")
    record_l2_write_bytes(kind="provider_attempts", status="success", size_bytes=120)
    record_brief_build(domain="market", status="success")
    record_brief_validation_failed(domain="market")
    record_market_rows(count=32)
    record_market_chart(status="success", count=2)
    record_market_provider_attempt(status="success")
    record_news_raw_total(count=20)
    record_news_accepted_total(bucket="company_news", count=5)
    record_news_context_error()
    record_social_signal_accepted_total(count=6)
    record_social_signal_rejected_total(count=2)
    record_social_evidence_write_failed_total()
    record_fundamental_field_mapped_total(count=12)
    record_fundamental_missing_core_total(count=1)
    record_fundamental_conflict_total(count=0)
    record_techlab_indicator_total(status="success")
    record_techlab_chart_total(status="success", count=2)
    record_span("frontline_pack.build", status="success", elapsed_ms=1, fields={})
    snapshot = snapshot_observability_state()
    assert any(key.startswith("pack_build_total|") for key in snapshot["counters"])
    assert any(key.startswith("pack_schema_error_total|") for key in snapshot["counters"])
    assert any(key.startswith("pack_quality_status_total|") for key in snapshot["counters"])
    assert any(key.startswith("provider_attempt_total|") for key in snapshot["counters"])
    assert any(key.startswith("provider_rows_total|") for key in snapshot["counters"])
    assert any(key.startswith("mongo_cache_inspect_total|") for key in snapshot["counters"])
    assert any(key.startswith("mongo_upsert_total|") for key in snapshot["counters"])
    assert any(key.startswith("l2_write_total|") for key in snapshot["counters"])
    assert any(key.startswith("l2_write_bytes_total|") for key in snapshot["counters"])
    assert any(key.startswith("brief_build_total|") for key in snapshot["counters"])
    assert any(key.startswith("brief_validation_failed_total|") for key in snapshot["counters"])
    assert "market_rows_total" in snapshot["counters"]
    assert any(key.startswith("market_chart_total|") for key in snapshot["counters"])
    assert any(key.startswith("market_provider_attempt_total|") for key in snapshot["counters"])
    assert "news_raw_total" in snapshot["counters"]
    assert any(key.startswith("news_accepted_total|") for key in snapshot["counters"])
    assert "news_context_error_total" in snapshot["counters"]
    assert "social_signal_accepted_total" in snapshot["counters"]
    assert "social_signal_rejected_total" in snapshot["counters"]
    assert "social_evidence_write_failed_total" in snapshot["counters"]
    assert "fundamental_field_mapped_total" in snapshot["counters"]
    assert "fundamental_missing_core_total" in snapshot["counters"]
    assert "fundamental_conflict_total" in snapshot["counters"]
    assert any(key.startswith("techlab_indicator_total|") for key in snapshot["counters"])
    assert any(key.startswith("techlab_chart_total|") for key in snapshot["counters"])
    assert any(key.startswith("pack_latency_ms|") for key in snapshot["histograms"])
    assert any(key.startswith("provider_elapsed_ms|") for key in snapshot["histograms"])
    assert any(key.startswith("mongo_latency_ms|") for key in snapshot["histograms"])
    assert any(record.name == "frontline_pack.build" for record in snapshot["spans"])
