from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pymongo.errors import PyMongoError

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import DeclarativeProviderManifest

from .mongo import OPENBB_PROVIDER_MANIFESTS, utc_now_iso


class MongoProviderManifestStore:
    def __init__(self, collection: Any) -> None:
        self.collection = collection

    @property
    def collection_name(self) -> str:
        return OPENBB_PROVIDER_MANIFESTS

    def upsert(self, manifest: DeclarativeProviderManifest, *, updated_by: str) -> str:
        doc = asdict(manifest)
        doc["_id"] = manifest.adapter_id
        doc["markets"] = [market.value for market in manifest.markets]
        doc["domains"] = [domain.value for domain in manifest.domains]
        doc["source_role"] = manifest.source_role.value
        doc["admission_status"] = manifest.admission_status.value
        doc["priority_source"] = manifest.priority_source.value
        doc["updated_by"] = updated_by
        doc["updated_at"] = utc_now_iso()
        try:
            self.collection.replace_one({"_id": manifest.adapter_id}, doc, upsert=True)
        except PyMongoError as exc:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"provider manifest write failed: {exc}",
            ) from exc
        return manifest.adapter_id
