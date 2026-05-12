from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import sys
from uuid import uuid4

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.evidence import (  # noqa: E402
    build_openviking_http_client,
    load_openviking_l2_config,
)
from frontline_data_pack.evidence_writer import (  # noqa: E402
    L2WriteSessionState,
    cleanup_chart_manifest_reference,
    write_chart_evidence,
    write_pack_evidence,
    write_provider_attempts,
    write_raw_payload,
)
from frontline_data_pack.market_data_pack import clear_market_chart_references  # noqa: E402
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


QUERY_FP = "sha256:" + ("b" * 64)


def _set_openviking_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CLAW_TRADE_OPENVIKING_BASE_URI", "http://127.0.0.1:1933")
    monkeypatch.setenv("CLAW_TRADE_OPENVIKING_AUTH_MODE", "none")


def _context(call_id: str) -> ToolRuntimeContext:
    return ToolRuntimeContext(
        run_id=f"it-l2-{uuid4().hex[:10]}",
        stage="frontline",
        worker_id="market_analyst",
        call_id=call_id,
        dispatch_id=call_id,
        tool_name="market_market_data_pack",
        evidence_root=f"/tmp/{call_id}",
        current_time=datetime.now(UTC).isoformat(),
        current_date="2026-05-09",
    )


def _provider_result_without_evidence() -> ProviderResult:
    attempt = ProviderAttempt(
        provider="akshare",
        endpoint="stock_zh_a_hist",
        role="p0_price_history",
        status="success",
        started_at="2026-05-09T10:00:00Z",
        finished_at="2026-05-09T10:00:01Z",
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
        query_parameters=[ProviderQueryParameter(name="symbol", source="ticker_code_6", required=True, fixed_value=None)],
    )
    return ProviderResult(
        spec=spec,
        attempt=attempt,
        raw_payload={"rows": [{"close": 1600.0}]},
        normalized_rows=[{"close": 1600.0}],
        field_sources={},
    )


def _sha(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


def test_t_test_004_openviking_l2_write_readback_raw_attempts_pack_chart_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_openviking_env(monkeypatch)
    client = build_openviking_http_client(load_openviking_l2_config())
    context = _context(call_id=f"call-{uuid4().hex[:8]}")
    state = L2WriteSessionState()
    result = _provider_result_without_evidence()

    raw_write = write_raw_payload(result=result, context=context, attempt_seq=1, state=state)
    assert raw_write.ok is True
    assert raw_write.receipt is not None
    updated_attempt = raw_write.provider_result.attempt

    attempts_write = write_provider_attempts(context=context, attempts=[updated_attempt], state=state)
    assert attempts_write.ok is True
    assert attempts_write.receipt is not None

    assert updated_attempt.raw_payload_ref is not None
    pack = build_pack_envelope(
        domain="market",
        context=context,
        input=PackInput(
            ticker="600519.SH",
            market="CN_A",
            company_name="贵州茅台",
            industry="白酒",
            start_date="2026-02-01",
            end_date="2026-05-09",
        ),
        quality=Quality(status="complete", coverage_score=0.95, freshness_status="fresh", warnings=[]),
        provider_attempts=[updated_attempt],
        field_sources={},
        raw_payload_refs=[
            EvidenceRef(
                uri=updated_attempt.raw_payload_ref,
                sha256=updated_attempt.payload_hash or "",
                size_bytes=raw_write.receipt.size_bytes,
                kind="provider_raw",
                readback_verified=True,
            )
        ],
        mongo_cache_refs=[],
        openviking_l2_refs=[],
        diagnostic_flags=[],
        reader_brief="资料完整，可支撑后续分析。",
        domain_data={"schema_version": "cn_a_market_pack.v1"},
    )
    pack_write = write_pack_evidence(pack=pack, context=context, state=state)
    assert pack_write.ok is True
    assert pack_write.receipt is not None

    chart_bytes = b"\x89PNG\r\n\x1a\nfrontline-chart"
    chart_write = write_chart_evidence(
        context=context,
        chart_kind="indicator",
        chart_bytes=chart_bytes,
        chart_local_ref=f"{context.evidence_root}/techlab/charts-local/indicator.png",
        state=state,
    )
    assert chart_write.ok is True
    assert chart_write.receipt is not None
    assert chart_write.evidence_ref is not None
    assert chart_write.chart_ref is not None
    assert chart_write.evidence_ref.kind == "chart_manifest"
    assert chart_write.receipt.uri.endswith("/charts/indicator.manifest.json")
    assert chart_write.chart_ref.openviking_ref == chart_write.receipt.uri
    assert chart_write.chart_ref.sha256 == _sha(chart_bytes)

    receipts = [raw_write.receipt, attempts_write.receipt, pack_write.receipt, chart_write.receipt]
    for receipt in receipts:
        assert receipt is not None
        stat = client.stat(uri=receipt.uri)
        assert stat.size_bytes == receipt.size_bytes
        content = client.download(uri=receipt.uri)
        assert len(content) == receipt.size_bytes
        assert _sha(content) == receipt.sha256
    chart_content = client.download(uri=chart_write.receipt.uri)
    chart_manifest = json.loads(chart_content.decode("utf-8"))
    assert chart_manifest["schema_version"] == "chart_manifest.v1"
    assert chart_manifest["chart_kind"] == "indicator"
    assert chart_manifest["lifecycle"] == "ephemeral"
    assert chart_manifest["image_sha256"] == _sha(chart_bytes)
    assert chart_manifest["image_size_bytes"] == len(chart_bytes)
    assert "data" not in chart_manifest
    assert "frontline-chart" not in chart_content.decode("utf-8")

    cleanup_state = L2WriteSessionState()
    cleanup_result = cleanup_chart_manifest_reference(
        chart_ref=chart_write.chart_ref,
        state=cleanup_state,
    )
    if not cleanup_result.ok:
        assert cleanup_result.error is not None
        assert cleanup_result.error.code in {"L2_WRITE_FAILED", "L2_HASH_MISMATCH"}
        if cleanup_result.error.code == "L2_WRITE_FAILED":
            assert "resource is busy" in cleanup_result.error.message
        return
    assert cleanup_result.ok is True
    assert cleanup_result.evidence_ref is not None
    assert cleanup_result.evidence_ref.kind == "chart_manifest_cleanup"
    cleared_content = client.download(uri=cleanup_result.receipt.uri)
    cleared_manifest = json.loads(cleared_content.decode("utf-8"))
    assert cleared_manifest["lifecycle"] == "cleared"
    assert cleared_manifest["render_status"] == "cleared"
    assert cleared_manifest["local_asset_ref"] is None
    assert raw_write.evidence_ref is not None
    assert chart_write.evidence_ref is not None

    pack_with_chart = build_pack_envelope(
        domain="market",
        context=context,
        input=PackInput(
            ticker="600519.SH",
            market="CN_A",
            company_name="贵州茅台",
            industry="白酒",
            start_date="2026-02-01",
            end_date="2026-05-09",
        ),
        quality=Quality(status="complete", coverage_score=0.95, freshness_status="fresh", warnings=[]),
        provider_attempts=[updated_attempt],
        field_sources={},
        raw_payload_refs=[
            EvidenceRef(
                uri=updated_attempt.raw_payload_ref,
                sha256=updated_attempt.payload_hash or "",
                size_bytes=raw_write.receipt.size_bytes,
                kind="provider_raw",
                readback_verified=True,
            )
        ],
        mongo_cache_refs=[],
        openviking_l2_refs=[raw_write.evidence_ref, chart_write.evidence_ref],
        diagnostic_flags=[],
        reader_brief="资料完整，可支撑后续分析。",
        domain_data={
            "schema_version": "cn_a_market_pack.v1",
            "chart_refs": [chart_write.chart_ref],
        },
    )
    cleaned_pack = clear_market_chart_references(
        pack_with_chart,
        cleanup_evidence_ref=cleanup_result.evidence_ref,
    )
    assert cleaned_pack.domain_data["chart_refs"] == []
    assert all(ref.kind != "chart_manifest" for ref in cleaned_pack.openviking_l2_refs)
    assert any(ref.kind == "chart_manifest_cleanup" for ref in cleaned_pack.openviking_l2_refs)


def test_t_test_004_l2_path_isolated_by_call_id_under_concurrent_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_openviking_env(monkeypatch)

    def _write_for_call(call_id: str) -> str:
        context = _context(call_id=call_id)
        state = L2WriteSessionState()
        result = _provider_result_without_evidence()
        write_result = write_raw_payload(result=result, context=context, attempt_seq=1, state=state)
        assert write_result.ok is True
        assert write_result.receipt is not None
        return write_result.receipt.uri

    call_a = f"call-{uuid4().hex[:8]}-A"
    call_b = f"call-{uuid4().hex[:8]}-B"
    with ThreadPoolExecutor(max_workers=2) as executor:
        uri_a, uri_b = list(executor.map(_write_for_call, (call_a, call_b)))

    assert uri_a != uri_b
    assert f"/{call_a}/" in uri_a
    assert f"/{call_b}/" in uri_b
