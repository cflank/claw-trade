from __future__ import annotations

from typing import Any

from claw_trade.data_gateway.store.evidence_chain import audit_openbb_evidence_chain
from claw_trade.data_gateway.store.mongo import (
    OPENBB_PROVIDER_ATTEMPTS,
    OPENBB_PROVIDER_HTTP_EVIDENCE,
    OPENBB_RAW_PAYLOADS,
)


class _Collection:
    def __init__(self, docs: tuple[dict[str, Any], ...] = ()) -> None:
        self.docs = tuple(docs)

    def find(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        return [doc for doc in self.docs if all(doc.get(key) == value for key, value in query.items())]

    def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        matches = self.find(query)
        return matches[0] if matches else None


class _Database:
    def __init__(self, collections: dict[str, _Collection]) -> None:
        self.collections = collections

    def __getitem__(self, name: str) -> _Collection:
        return self.collections.get(name, _Collection())


def test_evidence_chain_accepts_content_addressed_raw_payload_seen_by_later_run() -> None:
    run_id = "run-current"
    raw_ref = "mongo://openbb_raw_payloads/sha256:abc"
    db = _Database(
        {
            OPENBB_PROVIDER_ATTEMPTS: _Collection(
                (
                    {
                        "_id": "attempt-1",
                        "run_id": run_id,
                        "status": "remote_success",
                        "raw_ref": raw_ref,
                        "normalized_ref": "mongo://openbb_normalized/sha256:def",
                    },
                )
            ),
            OPENBB_PROVIDER_HTTP_EVIDENCE: _Collection(
                (
                    {
                        "_id": "attempt-1:http",
                        "run_id": run_id,
                        "status": "remote_success",
                        "source_url": "https://query1.finance.yahoo.com/v8/finance/chart/AAPL",
                        "response_status_code": 200,
                        "response_headers_summary": {"content-type": "application/json"},
                        "raw_ref": raw_ref,
                    },
                )
            ),
            OPENBB_RAW_PAYLOADS: _Collection(
                (
                    {
                        "_id": "sha256:abc",
                        "run_id": "run-original",
                        "last_seen_run_id": run_id,
                    },
                )
            ),
        }
    )

    audit = audit_openbb_evidence_chain(db, run_id=run_id)

    assert audit.passed is True
    assert audit.attempt_count == 1
    assert audit.http_evidence_count == 1
    assert audit.raw_ref_count == 1
    assert audit.missing_raw_refs == ()


def test_evidence_chain_reports_missing_raw_doc_and_success_http_gaps() -> None:
    run_id = "run-gaps"
    db = _Database(
        {
            OPENBB_PROVIDER_ATTEMPTS: _Collection(
                (
                    {
                        "_id": "attempt-missing-raw",
                        "run_id": run_id,
                        "status": "remote_success",
                        "raw_ref": None,
                        "normalized_ref": None,
                    },
                    {
                        "_id": "attempt-invalid-raw",
                        "run_id": run_id,
                        "status": "shared_result",
                        "raw_ref": "raw://legacy-ref",
                        "normalized_ref": "mongo://openbb_normalized/sha256:def",
                    },
                    {
                        "_id": "attempt-missing-doc",
                        "run_id": run_id,
                        "status": "remote_success",
                        "raw_ref": "mongo://openbb_raw_payloads/sha256:missing",
                        "normalized_ref": "mongo://openbb_normalized/sha256:ghi",
                    },
                )
            ),
            OPENBB_PROVIDER_HTTP_EVIDENCE: _Collection(
                (
                    {
                        "_id": "attempt-missing-doc:http",
                        "run_id": run_id,
                        "status": "remote_success",
                        "source_url": "",
                        "response_status_code": None,
                        "response_headers_summary": {},
                        "raw_ref": None,
                    },
                )
            ),
            OPENBB_RAW_PAYLOADS: _Collection(),
        }
    )

    audit = audit_openbb_evidence_chain(db, run_id=run_id)

    assert audit.passed is False
    assert audit.remote_success_attempts_missing_raw_ref == ("attempt-missing-raw",)
    assert audit.remote_success_attempts_missing_normalized_ref == ("attempt-missing-raw",)
    assert audit.invalid_raw_refs == ("raw://legacy-ref",)
    assert audit.missing_raw_refs == ("mongo://openbb_raw_payloads/sha256:missing",)
    assert audit.success_http_missing_source_url == ("attempt-missing-doc:http",)
    assert audit.success_http_missing_response_status == ("attempt-missing-doc:http",)
    assert audit.success_http_missing_headers == ("attempt-missing-doc:http",)
    assert audit.success_http_missing_raw_ref == ("attempt-missing-doc:http",)
