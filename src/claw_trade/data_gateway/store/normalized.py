from __future__ import annotations

import json
from typing import Any

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import NormalizedResult, PackRequest, ProviderCallSpec, ProviderStatus

from .mongo import OPENBB_NORMALIZED, mongo_ref, parse_mongo_ref, sha256_text, utc_now_iso


class MongoNormalizedStore:
    def __init__(self, collection: Any) -> None:
        self.collection = collection

    @property
    def collection_name(self) -> str:
        return OPENBB_NORMALIZED

    def write(self, *, request: PackRequest, spec: ProviderCallSpec, normalized: NormalizedResult) -> str:
        if not normalized.source_raw_ref:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                "normalized.source_raw_ref must not be empty",
            )
        normalized_hash = _normalized_hash(request=request, spec=spec, normalized=normalized)
        doc = {
            "_id": normalized_hash,
            "normalized_hash": normalized_hash,
            "run_id": request.run_id,
            "call_id": request.call_id,
            "provider": spec.provider,
            "adapter_id": spec.adapter_id,
            "adapter_kind": spec.provider_kind.value,
            "endpoint": spec.endpoint,
            "market": spec.market.value,
            "domain": spec.domain.value,
            "ticker": request.ticker,
            "schema_id": normalized.schema_id,
            "schema_version": 1,
            "schema_fingerprint": sha256_text(normalized.schema_id),
            "source_raw_ref": normalized.source_raw_ref,
            "rows": list(normalized.rows),
            "compact_facts": dict(normalized.compact_facts),
            "row_count": normalized.row_count,
            "field_units": dict(normalized.field_units),
            "currency": normalized.currency,
            "timezone": normalized.timezone,
            "period_start": request.start_date,
            "period_end": request.end_date,
            "status": normalized.status.value,
            "missing_fields": list(normalized.missing_fields),
            "error_code": normalized.error_code,
            "error_message": normalized.error_message,
            "created_at": utc_now_iso(),
        }
        try:
            self.collection.update_one({"_id": normalized_hash}, {"$setOnInsert": doc}, upsert=True)
        except Exception as exc:  # noqa: BLE001
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"normalized write failed: {exc}",
            ) from exc
        return mongo_ref(self.collection_name, normalized_hash)

    def read(self, normalized_ref: str) -> NormalizedResult:
        collection, document_id = parse_mongo_ref(normalized_ref)
        if collection != self.collection_name:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"normalized ref collection mismatch: {collection}",
            )
        doc = self.collection.find_one({"_id": document_id})
        if doc is None:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"normalized ref not found: {normalized_ref}",
            )
        try:
            status = ProviderStatus(str(doc.get("status") or ProviderStatus.REMOTE_ERROR.value))
            return NormalizedResult(
                status=status,
                schema_id=str(doc["schema_id"]),
                rows=tuple(doc.get("rows") or ()),
                compact_facts=dict(doc.get("compact_facts") or {}),
                row_count=int(doc.get("row_count") or 0),
                field_units=dict(doc.get("field_units") or {}),
                currency=doc.get("currency"),
                timezone=doc.get("timezone"),
                source_raw_ref=doc.get("source_raw_ref"),
                missing_fields=tuple(doc.get("missing_fields") or ()),
                error_code=doc.get("error_code"),
                error_message=doc.get("error_message"),
            )
        except Exception as exc:  # noqa: BLE001
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"normalized decode failed: {exc}",
            ) from exc


def _normalized_hash(*, request: PackRequest, spec: ProviderCallSpec, normalized: NormalizedResult) -> str:
    payload = {
        "schema_id": normalized.schema_id,
        "rows": list(normalized.rows),
        "compact_facts": dict(normalized.compact_facts),
        "field_units": dict(normalized.field_units),
        "currency": normalized.currency,
        "timezone": normalized.timezone,
        "period_start": request.start_date,
        "period_end": request.end_date,
        "provider": spec.provider,
        "endpoint": spec.endpoint,
    }
    rendered = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return sha256_text(rendered)
