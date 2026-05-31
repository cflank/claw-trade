from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pymongo.errors import PyMongoError

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import OpenBBProviderHttpEvidence, ProviderKind, ProviderStatus, SourceRole
from claw_trade.data_gateway.providers.managed_http import redact_headers, redact_url

from .mongo import OPENBB_PROVIDER_HTTP_EVIDENCE


class MongoProviderHttpEvidenceStore:
    def __init__(self, collection: Any) -> None:
        self.collection = collection

    @property
    def collection_name(self) -> str:
        return OPENBB_PROVIDER_HTTP_EVIDENCE

    def write(self, evidence: OpenBBProviderHttpEvidence) -> str:
        doc = _serialize_http_evidence(evidence)
        try:
            self.collection.update_one(
                {"_id": evidence.evidence_id},
                {"$setOnInsert": doc},
                upsert=True,
            )
        except PyMongoError as exc:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"http evidence write failed: {exc}",
            ) from exc
        except Exception as exc:  # noqa: BLE001
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"http evidence write failed: {exc}",
            ) from exc
        return evidence.evidence_id

    def get(self, evidence_id: str) -> OpenBBProviderHttpEvidence | None:
        doc = self.collection.find_one({"_id": evidence_id})
        if doc is None:
            return None
        return _deserialize_http_evidence(doc)


def _serialize_http_evidence(evidence: OpenBBProviderHttpEvidence) -> dict[str, Any]:
    data = asdict(evidence)
    data["_id"] = evidence.evidence_id
    data["provider_kind"] = evidence.provider_kind.value
    data["source_role"] = evidence.source_role.value
    data["status"] = evidence.status.value
    data["source_url"] = redact_url(evidence.source_url)
    data["response_headers_summary"] = redact_headers(evidence.response_headers_summary)
    return data


def _deserialize_http_evidence(doc: dict[str, Any]) -> OpenBBProviderHttpEvidence:
    return OpenBBProviderHttpEvidence(
        evidence_id=str(doc["_id"]),
        run_id=str(doc["run_id"]),
        call_id=str(doc["call_id"]),
        worker_id=str(doc["worker_id"]),
        pack=str(doc["pack"]),
        provider=str(doc["provider"]),
        adapter_id=str(doc["adapter_id"]),
        provider_kind=ProviderKind(str(doc["provider_kind"])),
        provider_config_version=str(doc["provider_config_version"]),
        endpoint=str(doc["endpoint"]),
        source_role=SourceRole(str(doc["source_role"])),
        requested_at=str(doc["requested_at"]),
        finished_at=str(doc["finished_at"]),
        http_method=str(doc["http_method"]),
        source_url=redact_url(doc.get("source_url")),
        response_status_code=doc.get("response_status_code"),
        response_headers_summary=redact_headers(doc.get("response_headers_summary") or {}),
        provider_request_id=doc.get("provider_request_id"),
        latency_ms=int(doc["latency_ms"]),
        status=ProviderStatus(str(doc["status"])),
        error_code=doc.get("error_code"),
        error_message=doc.get("error_message"),
        raw_ref=doc.get("raw_ref"),
    )
