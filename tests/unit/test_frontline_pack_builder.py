from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.errors import (  # noqa: E402
    PACK_FIELD_SOURCE_INVALID,
    PACK_SCHEMA_INVALID,
    FrontlineValidationError,
)
from frontline_data_pack.models import EvidenceRef, FieldSource, PackInput, ProviderAttempt, Quality  # noqa: E402
from frontline_data_pack.models import AttemptStatus, QualityStatus  # noqa: E402
from frontline_data_pack.pack_builder import build_pack_envelope  # noqa: E402
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


SHA_A = "sha256:" + ("a" * 64)
QUERY_FP = "sha256:" + ("c" * 64)


def test_t_pack_002_field_source_ref_must_exist_in_raw_payload_refs() -> None:
    context = _context()
    attempt = _attempt(status="success", error_code=None)
    raw_ref = _provider_raw_ref("1.json")
    with pytest.raises(FrontlineValidationError) as error:
        build_pack_envelope(
            domain="market",
            context=context,
            input=_pack_input(),
            quality=_quality(status="complete"),
            provider_attempts=[attempt],
            field_sources={
                "price_close_latest": _field_source(raw_payload_ref=_provider_raw_ref("missing.json"))
            },
            raw_payload_refs=[_evidence_ref(uri=raw_ref, sha=SHA_A, kind="provider_raw", readback_verified=True)],
            mongo_cache_refs=[],
            openviking_l2_refs=[],
            diagnostic_flags=[],
            reader_brief="已获得行情与指标证据。",
            domain_data={"schema_version": "cn_a_market_pack.v1"},
        )
    assert error.value.code == PACK_FIELD_SOURCE_INVALID


def test_t_pack_002_duplicate_attempt_identity_raises_schema_invalid() -> None:
    context = _context()
    attempt_a = _attempt(status="error", error_code="PROVIDER_TIMEOUT")
    attempt_b = _attempt(status="success", error_code=None)
    with pytest.raises(FrontlineValidationError) as error:
        build_pack_envelope(
            domain="market",
            context=context,
            input=_pack_input(),
            quality=_quality(status="partial"),
            provider_attempts=[attempt_a, attempt_b],
            field_sources={},
            raw_payload_refs=[_evidence_ref(uri=_provider_raw_ref("1.json"), sha=SHA_A, kind="provider_raw", readback_verified=True)],
            mongo_cache_refs=[],
            openviking_l2_refs=[],
            diagnostic_flags=[],
            reader_brief="尝试记录已归档。",
            domain_data={"schema_version": "cn_a_market_pack.v1"},
        )
    assert error.value.code == PACK_SCHEMA_INVALID


def test_t_pack_002_core_l2_failure_without_auditable_raw_ref_forces_failed() -> None:
    context = _context()
    attempt = _attempt(status="error", error_code="L2_WRITE_FAILED")
    pack = build_pack_envelope(
        domain="market",
        context=context,
        input=_pack_input(),
        quality=_quality(status="complete"),
        provider_attempts=[attempt],
        field_sources={},
        raw_payload_refs=[_evidence_ref(uri=_provider_raw_ref("1.json"), sha=SHA_A, kind="provider_raw", readback_verified=False)],
        mongo_cache_refs=[],
        openviking_l2_refs=[],
        diagnostic_flags=[],
        reader_brief="核心证据写入失败，已记录失败原因。",
        domain_data={"schema_version": "cn_a_market_pack.v1"},
    )
    assert pack.ok is True
    assert pack.quality.status == "failed"


def test_t_pack_002_mongo_upsert_failure_caps_quality_to_partial() -> None:
    context = _context()
    attempt = _attempt(status="success", error_code=None)
    raw_ref = _provider_raw_ref("1.json")
    pack = build_pack_envelope(
        domain="market",
        context=context,
        input=_pack_input(),
        quality=_quality(status="complete"),
        provider_attempts=[attempt],
        field_sources={"price_close_latest": _field_source(raw_payload_ref=raw_ref)},
        raw_payload_refs=[_evidence_ref(uri=raw_ref, sha=SHA_A, kind="provider_raw", readback_verified=True)],
        mongo_cache_refs=[],
        openviking_l2_refs=[],
        diagnostic_flags=["MONGO_WRITE_FAILED"],
        reader_brief="证据可审计，但缓存写入失败。",
        domain_data={"schema_version": "cn_a_market_pack.v1"},
    )
    assert pack.ok is True
    assert pack.quality.status == "partial"


def _context() -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id="run-1",
        stage="frontline",
        worker_id="market_analyst",
        call_id="call-1",
        dispatch_id="dispatch-1",
        tool_name="market_market_data_pack",
        evidence_root="tool-evidence/run-1",
        current_time="2026-05-08T12:00:00Z",
        current_date="2026-05-08",
    )


def _pack_input() -> PackInput:
    return PackInput(
        ticker="600519.SH",
        market="CN_A",
        company_name="贵州茅台",
        industry="白酒",
        start_date="2026-04-01",
        end_date="2026-05-08",
    )


def _quality(*, status: QualityStatus) -> Quality:
    return Quality(
        status=status,
        coverage_score=0.9,
        freshness_status="fresh",
        warnings=[],
    )


def _attempt(*, status: AttemptStatus, error_code: str | None) -> ProviderAttempt:
    return ProviderAttempt(
        provider="akshare",
        endpoint="stock_zh_a_hist",
        role="p0_price_history",
        status=status,
        started_at="2026-05-08T12:00:00Z",
        finished_at="2026-05-08T12:00:01Z",
        elapsed_ms=1000,
        timeout_ms=10000,
        query_fingerprint=QUERY_FP,
        raw_count=1 if status == "success" else 0,
        accepted_count=1 if status == "success" else 0,
        payload_hash=SHA_A if status == "success" else None,
        raw_payload_ref=_provider_raw_ref("1.json") if status == "success" else None,
        error_code=error_code,
        error_message_redacted=None,
    )


def _field_source(*, raw_payload_ref: str) -> FieldSource:
    return FieldSource(
        field_path="domain_data.price_history.recent_rows[0].close",
        provider="akshare",
        endpoint="stock_zh_a_hist",
        payload_hash=SHA_A,
        raw_payload_ref=raw_payload_ref,
        observed_at="2026-05-08T12:00:01Z",
        source_time="2026-05-08",
    )


def _evidence_ref(*, uri: str, sha: str, kind: str, readback_verified: bool) -> EvidenceRef:
    return EvidenceRef(
        uri=uri,
        sha256=sha,
        size_bytes=128,
        kind=kind,
        readback_verified=readback_verified,
    )


def _provider_raw_ref(suffix: str) -> str:
    return (
        "viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/"
        f"provider_raw/akshare/stock_zh_a_hist/{suffix}"
    )
