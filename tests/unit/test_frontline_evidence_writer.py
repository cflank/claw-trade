from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Mapping


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.errors import L2_TARGET_INVALID, L2_WRITE_FAILED  # noqa: E402
from frontline_data_pack.evidence import OpenVikingStatResult, OpenVikingWriteResult  # noqa: E402
from frontline_data_pack.evidence_writer import (  # noqa: E402
    CHART_MANIFEST_CLEANUP_FAILED_FLAG_PREFIX,
    DUPLICATE_TARGET_WRITE_FLAG,
    L2WriteSessionState,
    cleanup_chart_manifest_reference,
    commit_l2_write_session,
    PROVIDER_ATTEMPTS_WRITE_FAILED_FLAG,
    write_chart_evidence,
    write_pack_evidence,
    write_provider_attempts,
    write_raw_payload,
)
from frontline_data_pack.models import (  # noqa: E402
    EvidenceRef,
    PackInput,
    ProviderAttempt,
    ProviderQueryParameter,
    ProviderResult,
    ProviderSpec,
    Quality,
)
from frontline_data_pack.pack_builder import build_pack_envelope  # noqa: E402
from frontline_data_pack.runtime_context import ToolRuntimeContext  # noqa: E402


SHA_A = "sha256:" + ("a" * 64)
QUERY_FP = "sha256:" + ("b" * 64)


def test_t_l2_002_write_raw_payload_success_updates_attempt_evidence() -> None:
    context = _context()
    state = L2WriteSessionState()
    result = _provider_result_without_evidence()
    client = _InMemoryL2Client()

    write_result = write_raw_payload(
        result=result,
        context=context,
        attempt_seq=1,
        state=state,
        client=client,
    )

    assert write_result.ok is True
    assert write_result.receipt is not None
    assert write_result.provider_result.attempt.payload_hash == write_result.receipt.sha256
    assert write_result.provider_result.attempt.raw_payload_ref == write_result.receipt.uri
    assert write_result.can_populate_field_sources is True


def test_t_l2_002_write_raw_payload_failure_never_marks_field_sources_usable() -> None:
    context = _context()
    state = L2WriteSessionState()
    result = _provider_result_without_evidence()
    client = _InMemoryL2Client(fail_all_writes=True)

    write_result = write_raw_payload(
        result=result,
        context=context,
        attempt_seq=1,
        state=state,
        client=client,
    )

    assert write_result.ok is False
    assert write_result.error is not None
    assert write_result.error.code == L2_WRITE_FAILED
    assert write_result.provider_result.attempt.payload_hash is None
    assert write_result.provider_result.attempt.raw_payload_ref is None
    assert write_result.can_populate_field_sources is False


def test_t_l2_002_provider_attempts_write_failure_can_downgrade_pack_to_partial() -> None:
    context = _context()
    state = L2WriteSessionState()
    attempts = [_attempt_with_evidence()]
    client = _InMemoryL2Client(fail_paths={"provider_attempts.json"})

    attempts_write_result = write_provider_attempts(
        context=context,
        attempts=attempts,
        state=state,
        client=client,
    )

    assert attempts_write_result.ok is False
    assert attempts_write_result.error is not None
    assert attempts_write_result.error.diagnostic_flag == PROVIDER_ATTEMPTS_WRITE_FAILED_FLAG

    raw_ref = attempts[0].raw_payload_ref
    assert raw_ref is not None
    pack = build_pack_envelope(
        domain="market",
        context=context,
        input=_pack_input(),
        quality=Quality(
            status="complete",
            coverage_score=0.95,
            freshness_status="fresh",
            warnings=[],
        ),
        provider_attempts=attempts,
        field_sources={},
        raw_payload_refs=[
            EvidenceRef(
                uri=raw_ref,
                sha256=SHA_A,
                size_bytes=128,
                kind="provider_raw",
                readback_verified=True,
            )
        ],
        mongo_cache_refs=[],
        openviking_l2_refs=[],
        diagnostic_flags=[attempts_write_result.error.diagnostic_flag],
        reader_brief="核心证据可审计，但 attempts 文件写入失败。",
        domain_data={"schema_version": "cn_a_market_pack.v1"},
    )

    assert pack.quality.status == "partial"


def test_t_l2_002_chart_write_failure_never_returns_chart_ref() -> None:
    context = _context()
    state = L2WriteSessionState()
    client = _InMemoryL2Client(fail_paths={"charts/indicator.manifest.json"})

    write_result = write_chart_evidence(
        context=context,
        chart_kind="indicator",
        chart_bytes=b"\x89PNG\r\n\x1a\nfake",
        chart_local_ref="/tmp/evidence/call-1/techlab/charts-local/indicator.png",
        state=state,
        client=client,
    )

    assert write_result.ok is False
    assert write_result.chart_ref is None


def test_t_l2_002_chart_write_persists_manifest_not_image_bytes() -> None:
    context = _context()
    state = L2WriteSessionState()
    client = _InMemoryL2Client()
    chart_bytes = b"\x89PNG\r\n\x1a\nfake"

    write_result = write_chart_evidence(
        context=context,
        chart_kind="indicator",
        chart_bytes=chart_bytes,
        chart_local_ref="/tmp/evidence/call-1/techlab/charts-local/indicator.png",
        state=state,
        client=client,
    )

    assert write_result.ok is True
    assert write_result.receipt is not None
    assert write_result.evidence_ref is not None
    assert write_result.chart_ref is not None
    assert write_result.evidence_ref.kind == "chart_manifest"
    assert write_result.receipt.uri.endswith("/charts/indicator.manifest.json")
    assert write_result.chart_ref.openviking_ref == write_result.receipt.uri
    assert write_result.chart_ref.path.endswith("/techlab/charts-local/indicator.png")
    assert write_result.chart_ref.sha256 == _sha(chart_bytes)

    stored = client._content_by_uri[write_result.receipt.uri]  # noqa: SLF001
    payload = json.loads(stored.decode("utf-8"))
    assert payload["schema_version"] == "chart_manifest.v1"
    assert payload["chart_kind"] == "indicator"
    assert payload["lifecycle"] == "ephemeral"
    assert payload["image_sha256"] == _sha(chart_bytes)
    assert payload["image_size_bytes"] == len(chart_bytes)
    assert "data" not in payload


def test_t_l2_002_cleanup_chart_manifest_overwrites_with_cleared_status() -> None:
    context = _context()
    write_state = L2WriteSessionState()
    cleanup_state = L2WriteSessionState()
    client = _InMemoryL2Client()
    chart_bytes = b"\x89PNG\r\n\x1a\nfake"
    write_result = write_chart_evidence(
        context=context,
        chart_kind="indicator",
        chart_bytes=chart_bytes,
        chart_local_ref="/tmp/evidence/call-1/techlab/charts-local/indicator.png",
        state=write_state,
        client=client,
    )
    assert write_result.ok is True
    assert write_result.chart_ref is not None

    cleanup_result = cleanup_chart_manifest_reference(
        chart_ref=write_result.chart_ref,
        state=cleanup_state,
        client=client,
    )

    assert cleanup_result.ok is True
    assert cleanup_result.receipt is not None
    assert cleanup_result.evidence_ref is not None
    assert cleanup_result.evidence_ref.kind == "chart_manifest_cleanup"
    assert cleanup_result.receipt.uri.endswith("/charts/indicator.manifest.json")
    payload = json.loads(client._content_by_uri[cleanup_result.receipt.uri].decode("utf-8"))  # noqa: SLF001
    assert payload["schema_version"] == "chart_manifest.v1"
    assert payload["chart_kind"] == "indicator"
    assert payload["lifecycle"] == "cleared"
    assert payload["render_status"] == "cleared"
    assert payload["local_asset_ref"] is None


def test_t_l2_002_cleanup_chart_manifest_rejects_mismatched_uri_kind() -> None:
    state = L2WriteSessionState()
    client = _InMemoryL2Client()
    bad_ref = write_chart_evidence(
        context=_context(),
        chart_kind="indicator",
        chart_bytes=b"\x89PNG\r\n\x1a\nfake",
        chart_local_ref="/tmp/evidence/call-1/techlab/charts-local/indicator.png",
        state=L2WriteSessionState(),
        client=client,
    )
    assert bad_ref.chart_ref is not None
    tampered = bad_ref.chart_ref
    tampered = tampered.__class__(
        kind="market_structure",
        path=tampered.path,
        openviking_ref=tampered.openviking_ref,
        sha256=tampered.sha256,
    )

    cleanup_result = cleanup_chart_manifest_reference(
        chart_ref=tampered,
        state=state,
        client=client,
    )

    assert cleanup_result.ok is False
    assert cleanup_result.error is not None
    assert cleanup_result.error.code == L2_TARGET_INVALID
    assert cleanup_result.error.diagnostic_flag.startswith(CHART_MANIFEST_CLEANUP_FAILED_FLAG_PREFIX)


def test_t_l2_002_same_target_in_one_call_can_only_be_written_once() -> None:
    context = _context()
    state = L2WriteSessionState()
    client = _InMemoryL2Client()

    first = write_provider_attempts(
        context=context,
        attempts=[_attempt_with_evidence()],
        state=state,
        client=client,
    )
    second = write_provider_attempts(
        context=context,
        attempts=[_attempt_with_evidence()],
        state=state,
        client=client,
    )

    assert first.ok is True
    assert second.ok is False
    assert second.error is not None
    assert second.error.code == L2_TARGET_INVALID
    assert second.error.diagnostic_flag == DUPLICATE_TARGET_WRITE_FLAG


def test_t_l2_002_write_pack_evidence_writes_normalized_pack_json() -> None:
    context = _context()
    state = L2WriteSessionState()
    client = _InMemoryL2Client()
    attempt = _attempt_with_evidence()
    raw_ref = attempt.raw_payload_ref
    assert raw_ref is not None
    pack = build_pack_envelope(
        domain="market",
        context=context,
        input=_pack_input(),
        quality=Quality(
            status="complete",
            coverage_score=0.96,
            freshness_status="fresh",
            warnings=[],
        ),
        provider_attempts=[attempt],
        field_sources={},
        raw_payload_refs=[
            EvidenceRef(
                uri=raw_ref,
                sha256=SHA_A,
                size_bytes=128,
                kind="provider_raw",
                readback_verified=True,
            )
        ],
        mongo_cache_refs=[],
        openviking_l2_refs=[],
        diagnostic_flags=[],
        reader_brief="资料完整，可支撑分析。",
        domain_data={"schema_version": "cn_a_market_pack.v1"},
    )

    write_result = write_pack_evidence(
        pack=pack,
        context=context,
        state=state,
        client=client,
    )

    assert write_result.ok is True
    assert write_result.receipt is not None
    assert write_result.receipt.uri.endswith("/normalized_pack.json")


def test_deferred_l2_writes_publish_only_after_commit() -> None:
    context = _context()
    state = L2WriteSessionState(defer_writes=True)
    client = _InMemoryL2Client()

    write_result = write_raw_payload(
        result=_provider_result_without_evidence(),
        context=context,
        attempt_seq=1,
        state=state,
        client=client,
    )

    assert write_result.ok is True
    assert client._content_by_uri == {}  # noqa: SLF001

    commit_result = commit_l2_write_session(state=state, client=client)

    assert commit_result.ok is True
    assert write_result.receipt is not None
    assert write_result.receipt.uri in client._content_by_uri  # noqa: SLF001


def test_tool_call_id_scopes_l2_evidence_paths_for_retried_tool_calls() -> None:
    context = ToolRuntimeContext(
        run_id="run-1",
        stage="frontline",
        worker_id="market_analyst",
        call_id="worker-call-1",
        dispatch_id="dispatch-1",
        tool_name="market_market_data_pack",
        evidence_root="tool-evidence/run-1",
        current_time="2026-05-08T12:00:00Z",
        current_date="2026-05-08",
        tool_call_id="call_00_retryA",
    )
    state = L2WriteSessionState()
    client = _InMemoryL2Client()

    write_result = write_raw_payload(
        result=_provider_result_without_evidence(),
        context=context,
        attempt_seq=1,
        state=state,
        client=client,
    )

    assert write_result.receipt is not None
    assert "/evidence/tool_calls/call_00_retryA/provider_raw/" in write_result.receipt.uri


class _InMemoryL2Client:
    def __init__(self, *, fail_all_writes: bool = False, fail_paths: set[str] | None = None) -> None:
        self._content_by_uri: dict[str, bytes] = {}
        self._fail_all_writes = fail_all_writes
        self._fail_paths = fail_paths or set()

    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        _ = content_type, metadata
        if self._fail_all_writes or any(path in uri for path in self._fail_paths):
            raise RuntimeError("simulated_l2_write_failure")
        self._content_by_uri[uri] = content_bytes
        return OpenVikingWriteResult(receipt_id="receipt-1")

    def stat(self, *, uri: str) -> OpenVikingStatResult:
        content = self._content_by_uri[uri]
        return OpenVikingStatResult(
            size_bytes=len(content),
            sha256=_sha(content),
            exists=True,
        )

    def read(self, *, uri: str) -> bytes:
        return self._content_by_uri[uri]

    def download(self, *, uri: str) -> bytes:
        return self._content_by_uri[uri]


def _provider_result_without_evidence() -> ProviderResult:
    attempt = ProviderAttempt(
        provider="akshare",
        endpoint="stock_zh_a_hist",
        role="p0_price_history",
        status="success",
        started_at="2026-05-08T12:00:00Z",
        finished_at="2026-05-08T12:00:01Z",
        elapsed_ms=1000,
        timeout_ms=10000,
        query_fingerprint=QUERY_FP,
        raw_count=1,
        accepted_count=1,
        payload_hash=None,
        raw_payload_ref=None,
        error_code=None,
        error_message_redacted=None,
    )
    spec = ProviderSpec(
        domain="market",
        priority="P0",
        provider="akshare",
        endpoint="stock_zh_a_hist",
        role="p0_price_history",
        enabled=True,
        mode="remote",
        timeout_ms=10000,
        required_for_complete=True,
        query_parameters=[
            ProviderQueryParameter(
                name="symbol",
                source="ticker_code_6",
                required=True,
                fixed_value=None,
            )
        ],
    )
    return ProviderResult(
        spec=spec,
        attempt=attempt,
        raw_payload={"rows": [{"close": 1600.0}]},
        normalized_rows=[{"close": 1600.0}],
        field_sources={},
    )


def _attempt_with_evidence() -> ProviderAttempt:
    return ProviderAttempt(
        provider="akshare",
        endpoint="stock_zh_a_hist",
        role="p0_price_history",
        status="success",
        started_at="2026-05-08T12:00:00Z",
        finished_at="2026-05-08T12:00:01Z",
        elapsed_ms=1000,
        timeout_ms=10000,
        query_fingerprint=QUERY_FP,
        raw_count=1,
        accepted_count=1,
        payload_hash=SHA_A,
        raw_payload_ref=_provider_raw_ref(),
        error_code=None,
        error_message_redacted=None,
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


def _provider_raw_ref() -> str:
    return (
        "viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/"
        "provider_raw/akshare/stock_zh_a_hist/1.json"
    )


def _sha(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"
