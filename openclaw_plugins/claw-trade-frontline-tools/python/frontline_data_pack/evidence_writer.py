from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
from time import perf_counter
import re
from typing import Any, Mapping

from .errors import L2_READBACK_FAILED, L2_TARGET_INVALID, FrontlineValidationError
from .evidence import OpenVikingEvidenceClient, write_l2_evidence
from .models import (
    ChartRef,
    EvidenceRef,
    L2WriteReceipt,
    L2WriteRequest,
    L2WriteTarget,
    PackEnvelope,
    ProviderAttempt,
    ProviderResult,
    replace_attempt_evidence,
    stable_json_bytes,
)
from .runtime_context import ToolRuntimeContext
from .observability import emit_structured_log, record_l2_readback_failed, record_l2_write, record_l2_write_bytes, record_span
from .security import validate_l2_target_path


PROVIDER_ATTEMPTS_WRITE_FAILED_FLAG = "l2_write_failed:provider_attempts"
NORMALIZED_PACK_WRITE_FAILED_FLAG = "l2_write_failed:normalized_pack"
CHART_WRITE_FAILED_FLAG_PREFIX = "l2_write_failed:chart:"
CHART_MANIFEST_CLEANUP_FAILED_FLAG_PREFIX = "l2_write_failed:chart_manifest_cleanup:"
DUPLICATE_TARGET_WRITE_FLAG = "l2_duplicate_target_write"

_SAFE_PATH_SEGMENT_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class EvidenceWriteError:
    code: str
    message: str
    diagnostic_flag: str


@dataclass(frozen=True)
class EvidenceWriteResult:
    ok: bool
    receipt: L2WriteReceipt | None
    evidence_ref: EvidenceRef | None
    error: EvidenceWriteError | None


@dataclass(frozen=True)
class RawPayloadWriteResult:
    ok: bool
    provider_result: ProviderResult
    receipt: L2WriteReceipt | None
    evidence_ref: EvidenceRef | None
    error: EvidenceWriteError | None

    @property
    def can_populate_field_sources(self) -> bool:
        if not self.ok:
            return False
        return (
            self.provider_result.attempt.payload_hash is not None
            and self.provider_result.attempt.raw_payload_ref is not None
            and self.evidence_ref is not None
        )


@dataclass(frozen=True)
class ChartEvidenceWriteResult:
    ok: bool
    chart_ref: ChartRef | None
    receipt: L2WriteReceipt | None
    evidence_ref: EvidenceRef | None
    error: EvidenceWriteError | None


@dataclass(frozen=True)
class ChartManifestCleanupResult:
    ok: bool
    receipt: L2WriteReceipt | None
    evidence_ref: EvidenceRef | None
    error: EvidenceWriteError | None


@dataclass
class L2WriteSessionState:
    written_targets: set[str] = field(default_factory=set)


def write_raw_payload(
    *,
    result: ProviderResult,
    context: ToolRuntimeContext,
    attempt_seq: int,
    state: L2WriteSessionState,
    client: OpenVikingEvidenceClient | None = None,
) -> RawPayloadWriteResult:
    if attempt_seq < 1:
        raise FrontlineValidationError(L2_TARGET_INVALID, "attempt_seq 必须 >= 1")
    if result.raw_payload is None:
        return RawPayloadWriteResult(
            ok=False,
            provider_result=result,
            receipt=None,
            evidence_ref=None,
            error=EvidenceWriteError(
                code=L2_TARGET_INVALID,
                message="raw_payload 缺失，无法写入 provider_raw",
                diagnostic_flag="l2_target_invalid:provider_raw_payload_missing",
            ),
        )

    provider = _safe_path_segment("provider", result.attempt.provider)
    endpoint = _safe_path_segment("endpoint", result.attempt.endpoint)
    relative_path = f"provider_raw/{provider}/{endpoint}/{attempt_seq}.json"
    write_result = _write_bytes(
        context=context,
        relative_path=relative_path,
        content_bytes=stable_json_bytes(result.raw_payload),
        content_type="application/json",
        metadata={
            "kind": "provider_raw",
            "provider": result.attempt.provider,
            "endpoint": result.attempt.endpoint,
            "attempt_seq": str(attempt_seq),
        },
        evidence_kind="provider_raw",
        state=state,
        client=client,
        failure_flag="l2_write_failed:provider_raw",
    )
    if not write_result.ok:
        return RawPayloadWriteResult(
            ok=False,
            provider_result=result,
            receipt=None,
            evidence_ref=None,
            error=write_result.error,
        )

    assert write_result.receipt is not None
    updated_result = replace_attempt_evidence(
        result,
        payload_hash=write_result.receipt.sha256,
        raw_payload_ref=write_result.receipt.uri,
    )
    return RawPayloadWriteResult(
        ok=True,
        provider_result=updated_result,
        receipt=write_result.receipt,
        evidence_ref=write_result.evidence_ref,
        error=None,
    )


def write_provider_attempts(
    *,
    context: ToolRuntimeContext,
    attempts: list[ProviderAttempt],
    state: L2WriteSessionState,
    client: OpenVikingEvidenceClient | None = None,
) -> EvidenceWriteResult:
    return _write_bytes(
        context=context,
        relative_path="provider_attempts.json",
        content_bytes=stable_json_bytes(attempts),
        content_type="application/json",
        metadata={"kind": "provider_attempts"},
        evidence_kind="provider_attempts",
        state=state,
        client=client,
        failure_flag=PROVIDER_ATTEMPTS_WRITE_FAILED_FLAG,
    )


def write_pack_evidence(
    *,
    pack: PackEnvelope,
    context: ToolRuntimeContext,
    state: L2WriteSessionState,
    client: OpenVikingEvidenceClient | None = None,
) -> EvidenceWriteResult:
    return _write_bytes(
        context=context,
        relative_path="normalized_pack.json",
        content_bytes=pack.to_stable_json_bytes(),
        content_type="application/json",
        metadata={"kind": "normalized_pack", "domain": pack.domain},
        evidence_kind="normalized_pack",
        state=state,
        client=client,
        failure_flag=NORMALIZED_PACK_WRITE_FAILED_FLAG,
    )


def write_chart_evidence(
    *,
    context: ToolRuntimeContext,
    chart_kind: str,
    chart_bytes: bytes,
    chart_local_ref: str,
    state: L2WriteSessionState,
    client: OpenVikingEvidenceClient | None = None,
) -> ChartEvidenceWriteResult:
    if not isinstance(chart_bytes, bytes) or len(chart_bytes) == 0:
        raise FrontlineValidationError(L2_TARGET_INVALID, "chart_bytes 必须是非空 bytes")
    if not isinstance(chart_local_ref, str) or chart_local_ref.strip() == "":
        raise FrontlineValidationError(L2_TARGET_INVALID, "chart_local_ref 必须是非空字符串")
    chart_name = _safe_path_segment("chart_kind", chart_kind)
    relative_path = f"charts/{chart_name}.manifest.json"
    image_sha256 = f"sha256:{hashlib.sha256(chart_bytes).hexdigest()}"
    manifest = {
        "schema_version": "chart_manifest.v1",
        "chart_kind": chart_name,
        "local_asset_ref": chart_local_ref.strip(),
        "image_sha256": image_sha256,
        "image_size_bytes": len(chart_bytes),
        "lifecycle": "ephemeral",
        "render_status": "generated",
    }
    write_result = _write_bytes(
        context=context,
        relative_path=relative_path,
        content_bytes=stable_json_bytes(manifest),
        content_type="application/json",
        metadata={"kind": "chart_manifest", "chart_kind": chart_name},
        evidence_kind="chart_manifest",
        state=state,
        client=client,
        failure_flag=f"{CHART_WRITE_FAILED_FLAG_PREFIX}{chart_name}",
    )
    if not write_result.ok:
        return ChartEvidenceWriteResult(
            ok=False,
            chart_ref=None,
            receipt=None,
            evidence_ref=None,
            error=write_result.error,
        )
    assert write_result.receipt is not None
    chart_ref = ChartRef(
        kind=_to_chart_ref_kind(chart_name),
        path=chart_local_ref.strip(),
        openviking_ref=write_result.receipt.uri,
        sha256=image_sha256,
    )
    return ChartEvidenceWriteResult(
        ok=True,
        chart_ref=chart_ref,
        receipt=write_result.receipt,
        evidence_ref=write_result.evidence_ref,
        error=None,
    )


def cleanup_chart_manifest_reference(
    *,
    chart_ref: ChartRef,
    state: L2WriteSessionState,
    client: OpenVikingEvidenceClient | None = None,
    cleanup_reason: str = "report_generated",
) -> ChartManifestCleanupResult:
    chart_name = _safe_path_segment("chart_kind", chart_ref.kind)
    relative_path = f"charts/{chart_name}.manifest.json"
    target = _target_from_l2_uri(chart_ref.openviking_ref)
    normalized_relative_path = validate_l2_target_path(target.relative_path)
    if normalized_relative_path != relative_path:
        return ChartManifestCleanupResult(
            ok=False,
            receipt=None,
            evidence_ref=None,
            error=EvidenceWriteError(
                code=L2_TARGET_INVALID,
                message=(
                    "chart_ref.openviking_ref 与 chart kind 不一致: "
                    f"expected={relative_path} actual={normalized_relative_path}"
                ),
                diagnostic_flag=f"{CHART_MANIFEST_CLEANUP_FAILED_FLAG_PREFIX}{chart_name}",
            ),
        )
    duplicate_error = _reserve_once(state, target)
    if duplicate_error is not None:
        return ChartManifestCleanupResult(
            ok=False,
            receipt=None,
            evidence_ref=None,
            error=duplicate_error,
        )

    manifest = {
        "schema_version": "chart_manifest.v1",
        "chart_kind": chart_name,
        "lifecycle": "cleared",
        "render_status": "cleared",
        "local_asset_ref": None,
        "image_sha256": chart_ref.sha256,
        "cleanup_reason": cleanup_reason,
    }
    try:
        receipt = write_l2_evidence(
            L2WriteRequest(
                target=target,
                content_bytes=stable_json_bytes(manifest),
                metadata={"kind": "chart_manifest_cleanup", "chart_kind": chart_name},
            ),
            client=client,
        )
    except FrontlineValidationError as exc:
        return ChartManifestCleanupResult(
            ok=False,
            receipt=None,
            evidence_ref=None,
            error=EvidenceWriteError(
                code=exc.code,
                message=exc.message,
                diagnostic_flag=f"{CHART_MANIFEST_CLEANUP_FAILED_FLAG_PREFIX}{chart_name}",
            ),
        )

    evidence_ref = EvidenceRef(
        uri=receipt.uri,
        sha256=receipt.sha256,
        size_bytes=receipt.size_bytes,
        kind="chart_manifest_cleanup",
        readback_verified=receipt.readback_verified,
    )
    return ChartManifestCleanupResult(
        ok=True,
        receipt=receipt,
        evidence_ref=evidence_ref,
        error=None,
    )


def _write_bytes(
    *,
    context: ToolRuntimeContext,
    relative_path: str,
    content_bytes: bytes,
    content_type: str,
    metadata: Mapping[str, str],
    evidence_kind: str,
    state: L2WriteSessionState,
    client: OpenVikingEvidenceClient | None,
    failure_flag: str,
) -> EvidenceWriteResult:
    kind = str(metadata.get("kind") or evidence_kind)
    started = perf_counter()
    target = L2WriteTarget(
        run_id=context.run_id,
        stage="frontline",
        worker_id=context.worker_id,
        call_id=context.call_id,
        relative_path=relative_path,
        content_type=content_type,
    )
    duplicate_error = _reserve_once(state, target)
    if duplicate_error is not None:
        elapsed_ms = max(0, int((perf_counter() - started) * 1000))
        record_l2_write(kind=kind, status="failure")
        record_l2_write_bytes(kind=kind, status="failure", size_bytes=0)
        emit_structured_log(
            {
                "run_id": context.run_id,
                "dispatch_id": context.dispatch_id,
                "call_id": context.call_id,
                "stage": context.stage,
                "worker_id": context.worker_id,
                "tool_name": context.tool_name,
                "domain": None,
                "ticker": None,
                "provider": None,
                "endpoint": None,
                "role": None,
                "attempt_status": "failure",
                "quality_status": None,
                "status": "failure",
                "elapsed_ms": elapsed_ms,
                "timeout_ms": None,
                "raw_count": None,
                "accepted_count": None,
                "error_code": duplicate_error.code,
                "evidence_kind": kind,
                "l2_ref_present": False,
                "mongo_ref_present": None,
            }
        )
        record_span(
            "l2.write",
            status="failure",
            elapsed_ms=elapsed_ms,
            fields={
                "run_id": context.run_id,
                "call_id": context.call_id,
                "worker_id": context.worker_id,
                "tool_name": context.tool_name,
                "status": "failure",
                "elapsed_ms": elapsed_ms,
                "evidence_kind": kind,
            },
        )
        return EvidenceWriteResult(
            ok=False,
            receipt=None,
            evidence_ref=None,
            error=duplicate_error,
        )

    try:
        receipt = write_l2_evidence(
            L2WriteRequest(
                target=target,
                content_bytes=content_bytes,
                metadata=dict(metadata),
            ),
            client=client,
        )
    except FrontlineValidationError as exc:
        elapsed_ms = max(0, int((perf_counter() - started) * 1000))
        record_l2_write(kind=kind, status="failure")
        record_l2_write_bytes(kind=kind, status="failure", size_bytes=0)
        if exc.code == L2_READBACK_FAILED:
            record_l2_readback_failed(kind=kind)
        emit_structured_log(
            {
                "run_id": context.run_id,
                "dispatch_id": context.dispatch_id,
                "call_id": context.call_id,
                "stage": context.stage,
                "worker_id": context.worker_id,
                "tool_name": context.tool_name,
                "domain": None,
                "ticker": None,
                "provider": None,
                "endpoint": None,
                "role": None,
                "attempt_status": "failure",
                "quality_status": None,
                "status": "failure",
                "elapsed_ms": elapsed_ms,
                "timeout_ms": None,
                "raw_count": None,
                "accepted_count": None,
                "error_code": exc.code,
                "evidence_kind": kind,
                "l2_ref_present": False,
                "mongo_ref_present": None,
            }
        )
        record_span(
            "l2.write",
            status="failure",
            elapsed_ms=elapsed_ms,
            fields={
                "run_id": context.run_id,
                "call_id": context.call_id,
                "worker_id": context.worker_id,
                "tool_name": context.tool_name,
                "status": "failure",
                "elapsed_ms": elapsed_ms,
                "error_code": exc.code,
                "evidence_kind": kind,
            },
        )
        return EvidenceWriteResult(
            ok=False,
            receipt=None,
            evidence_ref=None,
            error=EvidenceWriteError(
                code=exc.code,
                message=exc.message,
                diagnostic_flag=failure_flag,
            ),
        )

    evidence_ref = EvidenceRef(
        uri=receipt.uri,
        sha256=receipt.sha256,
        size_bytes=receipt.size_bytes,
        kind=evidence_kind,
        readback_verified=receipt.readback_verified,
    )
    elapsed_ms = max(0, int((perf_counter() - started) * 1000))
    record_l2_write(kind=kind, status="success")
    record_l2_write_bytes(kind=kind, status="success", size_bytes=receipt.size_bytes)
    if not receipt.readback_verified:
        record_l2_readback_failed(kind=kind)
    emit_structured_log(
        {
            "run_id": context.run_id,
            "dispatch_id": context.dispatch_id,
            "call_id": context.call_id,
            "stage": context.stage,
            "worker_id": context.worker_id,
            "tool_name": context.tool_name,
            "domain": None,
            "ticker": None,
            "provider": None,
            "endpoint": None,
            "role": None,
            "attempt_status": "success",
            "quality_status": None,
            "status": "success",
            "elapsed_ms": elapsed_ms,
            "timeout_ms": None,
            "raw_count": None,
            "accepted_count": None,
            "error_code": None,
            "evidence_kind": kind,
            "l2_ref_present": True,
            "mongo_ref_present": None,
        }
    )
    record_span(
        "l2.write",
        status="success",
        elapsed_ms=elapsed_ms,
        fields={
            "run_id": context.run_id,
            "call_id": context.call_id,
            "worker_id": context.worker_id,
            "tool_name": context.tool_name,
            "status": "success",
            "elapsed_ms": elapsed_ms,
            "evidence_kind": kind,
            "l2_ref_present": True,
        },
    )
    return EvidenceWriteResult(
        ok=True,
        receipt=receipt,
        evidence_ref=evidence_ref,
        error=None,
    )


def _reserve_once(state: L2WriteSessionState, target: L2WriteTarget) -> EvidenceWriteError | None:
    normalized_relative_path = validate_l2_target_path(target.relative_path)
    key = (
        f"{target.run_id}|{target.stage}|{target.worker_id}|{target.call_id}|"
        f"{normalized_relative_path}"
    )
    if key in state.written_targets:
        return EvidenceWriteError(
            code=L2_TARGET_INVALID,
            message=f"L2 target 在同一 call 内重复写入: {normalized_relative_path}",
            diagnostic_flag=DUPLICATE_TARGET_WRITE_FLAG,
        )
    state.written_targets.add(key)
    return None


def _safe_path_segment(field_name: str, value: str) -> str:
    if not isinstance(value, str):
        raise FrontlineValidationError(L2_TARGET_INVALID, f"{field_name} 必须是字符串")
    candidate = value.strip()
    if not candidate or _SAFE_PATH_SEGMENT_RE.fullmatch(candidate) is None:
        raise FrontlineValidationError(
            L2_TARGET_INVALID,
            f"{field_name} 含非法路径字符: {value!r}",
        )
    return candidate


def _to_chart_ref_kind(chart_kind: str) -> str:
    if chart_kind in {"market_structure", "volume", "indicator"}:
        return chart_kind
    raise FrontlineValidationError(L2_TARGET_INVALID, f"不支持的 chart_kind: {chart_kind}")


def _target_from_l2_uri(uri: str) -> L2WriteTarget:
    prefix = "viking://resources/workflow/"
    if not isinstance(uri, str) or not uri.startswith(prefix):
        raise FrontlineValidationError(L2_TARGET_INVALID, f"非法 chart manifest URI: {uri!r}")
    remainder = uri[len(prefix):]
    parts = remainder.split("/", 5)
    if len(parts) != 6 or parts[1] != "frontline" or parts[4] != "evidence":
        raise FrontlineValidationError(L2_TARGET_INVALID, f"非法 chart manifest URI: {uri!r}")
    run_id, _, worker_id, call_id, _, relative_path = parts
    return L2WriteTarget(
        run_id=run_id,
        stage="frontline",
        worker_id=worker_id,
        call_id=call_id,
        relative_path=relative_path,
        content_type="application/json",
    )


__all__ = [
    "CHART_WRITE_FAILED_FLAG_PREFIX",
    "CHART_MANIFEST_CLEANUP_FAILED_FLAG_PREFIX",
    "ChartManifestCleanupResult",
    "DUPLICATE_TARGET_WRITE_FLAG",
    "ChartEvidenceWriteResult",
    "EvidenceWriteError",
    "EvidenceWriteResult",
    "L2WriteSessionState",
    "NORMALIZED_PACK_WRITE_FAILED_FLAG",
    "PROVIDER_ATTEMPTS_WRITE_FAILED_FLAG",
    "RawPayloadWriteResult",
    "cleanup_chart_manifest_reference",
    "write_chart_evidence",
    "write_pack_evidence",
    "write_provider_attempts",
    "write_raw_payload",
]
