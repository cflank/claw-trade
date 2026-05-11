from __future__ import annotations

import re
from dataclasses import replace

from .errors import PACK_FIELD_SOURCE_INVALID, PACK_SCHEMA_INVALID, FrontlineValidationError
from .models import Domain, EvidenceRef, FieldSource, PackEnvelope, PackInput, ProviderAttempt, Quality
from .runtime_context import ToolRuntimeContext


_URI_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://")
_SECRET_RE = re.compile(
    r"(?i)\b(?:api[_-]?key|token|authorization|password|passwd|signature|sign)\b\s*[:=]\s*\S+"
)
_STATUS_ORDER = {"complete": 2, "partial": 1, "failed": 0}
_CORE_L2_ERROR_CODES = frozenset({"L2_WRITE_FAILED", "L2_READBACK_FAILED", "L2_HASH_MISMATCH"})
_MONGO_WRITE_FAILURE_MARKERS = (
    "MONGO_WRITE_FAILED",
    "mongo_write_failed",
    "mongo_upsert_failed",
    "cache_unavailable",
)


def build_pack_envelope(
    *,
    domain: Domain,
    context: ToolRuntimeContext,
    input: PackInput,
    quality: Quality,
    provider_attempts: list[ProviderAttempt],
    field_sources: dict[str, FieldSource],
    raw_payload_refs: list[EvidenceRef],
    mongo_cache_refs: list[str],
    openviking_l2_refs: list[EvidenceRef],
    diagnostic_flags: list[str],
    reader_brief: str,
    domain_data: dict[str, object],
) -> PackEnvelope:
    _validate_attempt_uniqueness(provider_attempts)
    _validate_field_source_refs(field_sources, raw_payload_refs)
    validate_pack_noise(
        reader_brief=reader_brief,
        warnings=quality.warnings,
        diagnostic_flags=diagnostic_flags,
        provider_attempts=provider_attempts,
    )
    adjusted_quality = _apply_quality_constraints(
        quality=quality,
        provider_attempts=provider_attempts,
        raw_payload_refs=raw_payload_refs,
        diagnostic_flags=diagnostic_flags,
    )
    return PackEnvelope(
        ok=True,
        schema_version="cn_a_frontline_pack.v1",
        domain=domain,
        run_id=context.run_id,
        stage="frontline",
        worker_id=context.worker_id,
        call_id=context.call_id,
        tool_name=context.tool_name,
        input=input,
        quality=adjusted_quality,
        provider_attempts=provider_attempts,
        field_sources=field_sources,
        raw_payload_refs=raw_payload_refs,
        mongo_cache_refs=mongo_cache_refs,
        openviking_l2_refs=openviking_l2_refs,
        diagnostic_flags=diagnostic_flags,
        reader_brief=reader_brief,
        domain_data=domain_data,
    )


def validate_pack_noise(
    *,
    reader_brief: str,
    warnings: list[str],
    diagnostic_flags: list[str],
    provider_attempts: list[ProviderAttempt],
) -> None:
    _assert_clean_text("reader_brief", reader_brief)
    for index, warning in enumerate(warnings):
        _assert_clean_text(f"quality.warnings[{index}]", warning)
    for index, flag in enumerate(diagnostic_flags):
        _assert_clean_text(f"diagnostic_flags[{index}]", flag)
    for index, attempt in enumerate(provider_attempts):
        if attempt.error_message_redacted is not None:
            _assert_clean_text(
                f"provider_attempts[{index}].error_message_redacted",
                attempt.error_message_redacted,
            )


def _validate_attempt_uniqueness(provider_attempts: list[ProviderAttempt]) -> None:
    seen: set[tuple[str, str, str, str]] = set()
    for attempt in provider_attempts:
        key = (
            attempt.provider,
            attempt.endpoint,
            attempt.query_fingerprint,
            attempt.started_at,
        )
        if key in seen:
            raise FrontlineValidationError(
                PACK_SCHEMA_INVALID,
                "provider_attempts 存在重复记录: (provider, endpoint, query_fingerprint, started_at)",
            )
        seen.add(key)


def _validate_field_source_refs(
    field_sources: dict[str, FieldSource],
    raw_payload_refs: list[EvidenceRef],
) -> None:
    known_refs = {item.uri for item in raw_payload_refs}
    for field_path, source in field_sources.items():
        if source.raw_payload_ref not in known_refs:
            raise FrontlineValidationError(
                PACK_FIELD_SOURCE_INVALID,
                f"field_sources[{field_path}] 指向不存在的 raw_payload_ref",
            )


def _apply_quality_constraints(
    *,
    quality: Quality,
    provider_attempts: list[ProviderAttempt],
    raw_payload_refs: list[EvidenceRef],
    diagnostic_flags: list[str],
) -> Quality:
    status = quality.status
    has_auditable_raw_ref = _has_auditable_raw_ref(raw_payload_refs)
    if _has_core_l2_failure(provider_attempts, diagnostic_flags):
        if not has_auditable_raw_ref:
            status = "failed"
        else:
            status = _cap_quality_status(status, "partial")
    if _has_mongo_upsert_failure(diagnostic_flags) and has_auditable_raw_ref:
        status = _cap_quality_status(status, "partial")
    if status == quality.status:
        return quality
    return replace(quality, status=status)


def _has_core_l2_failure(provider_attempts: list[ProviderAttempt], diagnostic_flags: list[str]) -> bool:
    if any(attempt.error_code in _CORE_L2_ERROR_CODES for attempt in provider_attempts):
        return True
    for flag in diagnostic_flags:
        normalized = flag.lower()
        if "l2_write_failed" in normalized or "l2_readback_failed" in normalized:
            return True
        if "l2_hash_mismatch" in normalized or "core_l2_write_failed" in normalized:
            return True
    return False


def _has_auditable_raw_ref(raw_payload_refs: list[EvidenceRef]) -> bool:
    for ref in raw_payload_refs:
        if ref.kind != "provider_raw":
            continue
        if ref.readback_verified:
            return True
    return False


def _has_mongo_upsert_failure(diagnostic_flags: list[str]) -> bool:
    for flag in diagnostic_flags:
        for marker in _MONGO_WRITE_FAILURE_MARKERS:
            if marker in flag:
                return True
    return False


def _cap_quality_status(status: str, max_status: str) -> str:
    if _STATUS_ORDER[status] > _STATUS_ORDER[max_status]:
        return max_status
    return status


def _assert_clean_text(field_name: str, text: str) -> None:
    if _URI_RE.search(text):
        raise FrontlineValidationError(PACK_SCHEMA_INVALID, f"{field_name} 包含 URI 噪音")
    if _SECRET_RE.search(text):
        raise FrontlineValidationError(PACK_SCHEMA_INVALID, f"{field_name} 包含敏感信息")
