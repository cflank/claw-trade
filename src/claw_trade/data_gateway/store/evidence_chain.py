from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claw_trade.data_gateway.models import ProviderStatus

from .mongo import (
    OPENBB_PROVIDER_ATTEMPTS,
    OPENBB_PROVIDER_HTTP_EVIDENCE,
    OPENBB_RAW_PAYLOADS,
    parse_mongo_ref,
)


@dataclass(frozen=True)
class OpenBBEvidenceChainAudit:
    run_id: str
    attempt_count: int
    http_evidence_count: int
    raw_ref_count: int
    missing_raw_refs: tuple[str, ...]
    invalid_raw_refs: tuple[str, ...]
    remote_success_attempts_missing_raw_ref: tuple[str, ...]
    remote_success_attempts_missing_normalized_ref: tuple[str, ...]
    success_http_missing_source_url: tuple[str, ...]
    success_http_missing_response_status: tuple[str, ...]
    success_http_missing_headers: tuple[str, ...]
    success_http_missing_raw_ref: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not (
            self.missing_raw_refs
            or self.invalid_raw_refs
            or self.remote_success_attempts_missing_raw_ref
            or self.remote_success_attempts_missing_normalized_ref
            or self.success_http_missing_source_url
            or self.success_http_missing_response_status
            or self.success_http_missing_headers
            or self.success_http_missing_raw_ref
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "passed": self.passed,
            "attempt_count": self.attempt_count,
            "http_evidence_count": self.http_evidence_count,
            "raw_ref_count": self.raw_ref_count,
            "missing_raw_refs": list(self.missing_raw_refs),
            "invalid_raw_refs": list(self.invalid_raw_refs),
            "remote_success_attempts_missing_raw_ref": list(self.remote_success_attempts_missing_raw_ref),
            "remote_success_attempts_missing_normalized_ref": list(
                self.remote_success_attempts_missing_normalized_ref
            ),
            "success_http_missing_source_url": list(self.success_http_missing_source_url),
            "success_http_missing_response_status": list(self.success_http_missing_response_status),
            "success_http_missing_headers": list(self.success_http_missing_headers),
            "success_http_missing_raw_ref": list(self.success_http_missing_raw_ref),
        }


def audit_openbb_evidence_chain(database: Any, *, run_id: str) -> OpenBBEvidenceChainAudit:
    attempts = tuple(database[OPENBB_PROVIDER_ATTEMPTS].find({"run_id": run_id}))
    http_rows = tuple(database[OPENBB_PROVIDER_HTTP_EVIDENCE].find({"run_id": run_id}))

    remote_success_attempts_missing_raw_ref: list[str] = []
    remote_success_attempts_missing_normalized_ref: list[str] = []
    success_http_missing_source_url: list[str] = []
    success_http_missing_response_status: list[str] = []
    success_http_missing_headers: list[str] = []
    success_http_missing_raw_ref: list[str] = []
    raw_refs: set[str] = set()

    for attempt in attempts:
        status = str(attempt.get("status") or "")
        attempt_id = str(attempt.get("_id") or attempt.get("attempt_id") or "")
        raw_ref = _str_or_none(attempt.get("raw_ref"))
        normalized_ref = _str_or_none(attempt.get("normalized_ref"))
        if raw_ref:
            raw_refs.add(raw_ref)
        if status == ProviderStatus.REMOTE_SUCCESS.value:
            if not raw_ref:
                remote_success_attempts_missing_raw_ref.append(attempt_id)
            if not normalized_ref:
                remote_success_attempts_missing_normalized_ref.append(attempt_id)

    for row in http_rows:
        status = str(row.get("status") or "")
        evidence_id = str(row.get("_id") or row.get("evidence_id") or "")
        raw_ref = _str_or_none(row.get("raw_ref"))
        if raw_ref:
            raw_refs.add(raw_ref)
        if status != ProviderStatus.REMOTE_SUCCESS.value:
            continue
        if not _str_or_none(row.get("source_url")):
            success_http_missing_source_url.append(evidence_id)
        if row.get("response_status_code") is None:
            success_http_missing_response_status.append(evidence_id)
        if not dict(row.get("response_headers_summary") or {}):
            success_http_missing_headers.append(evidence_id)
        if not raw_ref:
            success_http_missing_raw_ref.append(evidence_id)

    missing_raw_refs: list[str] = []
    invalid_raw_refs: list[str] = []
    for raw_ref in sorted(raw_refs):
        document_id = _openbb_raw_payload_document_id(raw_ref)
        if document_id is None:
            invalid_raw_refs.append(raw_ref)
            continue
        if database[OPENBB_RAW_PAYLOADS].find_one({"_id": document_id}) is None:
            missing_raw_refs.append(raw_ref)

    return OpenBBEvidenceChainAudit(
        run_id=run_id,
        attempt_count=len(attempts),
        http_evidence_count=len(http_rows),
        raw_ref_count=len(raw_refs),
        missing_raw_refs=tuple(missing_raw_refs),
        invalid_raw_refs=tuple(invalid_raw_refs),
        remote_success_attempts_missing_raw_ref=tuple(remote_success_attempts_missing_raw_ref),
        remote_success_attempts_missing_normalized_ref=tuple(remote_success_attempts_missing_normalized_ref),
        success_http_missing_source_url=tuple(success_http_missing_source_url),
        success_http_missing_response_status=tuple(success_http_missing_response_status),
        success_http_missing_headers=tuple(success_http_missing_headers),
        success_http_missing_raw_ref=tuple(success_http_missing_raw_ref),
    )


def _openbb_raw_payload_document_id(raw_ref: str) -> str | None:
    try:
        collection, document_id = parse_mongo_ref(raw_ref)
    except ValueError:
        return None
    if collection != OPENBB_RAW_PAYLOADS:
        return None
    return document_id


def _str_or_none(value: object) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None
