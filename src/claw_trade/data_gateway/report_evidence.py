from __future__ import annotations

import os
from typing import Any

_ALLOWED_EVIDENCE_COLLECTIONS = {
    "provider_attempts",
    "raw_payloads",
    "normalized_datasets",
    "dataset_manifests",
}


def summarize_data_refs(
    refs: list[str],
    *,
    mongo_uri: str | None = None,
    mongo_database: str | None = None,
    limit: int = 8,
) -> list[str]:
    uri = (mongo_uri or os.environ.get("DATA_GATEWAY_MONGODB_URI") or os.environ.get("CN_A_MONGODB_URI") or "").strip()
    database = (
        mongo_database
        or os.environ.get("DATA_GATEWAY_MONGODB_DATABASE")
        or os.environ.get("CN_A_MONGODB_DATABASE")
        or "claw_trade"
    ).strip()
    if not uri or not refs:
        return []
    try:
        from pymongo import MongoClient
    except Exception:
        return []
    summaries: list[str] = []
    try:
        client: MongoClient[Any] = MongoClient(uri, serverSelectionTimeoutMS=500)
        db = client[database]
        for ref in refs[:limit]:
            collection, document_id = _parse_mongo_ref(ref)
            if collection not in _ALLOWED_EVIDENCE_COLLECTIONS:
                continue
            doc = db[collection].find_one({"_id": document_id})
            if not isinstance(doc, dict):
                summaries.append(ref)
                continue
            summaries.append(_summarize_data_doc(collection, document_id, doc))
    except Exception:
        return []
    return summaries


def _parse_mongo_ref(ref: str) -> tuple[str, str]:
    body = ref.removeprefix("mongo://")
    collection, _, document_id = body.partition("/")
    return collection, document_id


def _summarize_data_doc(collection: str, document_id: str, doc: dict[str, Any]) -> str:
    if collection == "provider_attempts":
        provider = str(doc.get("provider") or "")
        endpoint = str(doc.get("endpoint") or "")
        status = str(doc.get("status") or "")
        return f"{collection}/{document_id}: provider={provider} endpoint={endpoint} status={status}"
    if collection == "normalized_datasets":
        provider = str(doc.get("provider") or "")
        dataset = str(doc.get("dataset") or "")
        schema = str(doc.get("schema_id") or "")
        return f"{collection}/{document_id}: provider={provider} dataset={dataset} schema={schema}"
    return f"{collection}/{document_id}: raw payload stored in data layer"
