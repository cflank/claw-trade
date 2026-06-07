from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha1
from typing import Any, Mapping
from urllib.parse import urlparse

UI_DATA_SOURCE_SETTINGS_COLLECTION = "ui_data_source_settings"
UI_EMBEDDING_SETTINGS_COLLECTION = "ui_embedding_settings"
UI_REPORT_MODEL_CONFIG_COLLECTION = "ui_report_model_config"
UI_REPORT_MODEL_STATUS_COLLECTION = "ui_report_model_status"
UI_SECRET_SETTINGS_COLLECTION = "ui_secret_settings"


@dataclass(frozen=True)
class DataSourceSettingsStores:
    data_source_store: MongoDataSourceStore | None
    secret_store: MongoSecretStore | None


def open_ui_settings_database_from_env() -> Any | None:
    uri = (
        os.environ.get("DATA_GATEWAY_MONGODB_URI", "").strip()
        or os.environ.get("CN_A_MONGODB_URI", "").strip()
    )
    if not uri:
        return None
    from pymongo import MongoClient

    database_name = (
        os.environ.get("DATA_GATEWAY_MONGODB_DATABASE", "").strip()
        or os.environ.get("CN_A_MONGODB_DATABASE", "").strip()
        or _database_name_from_uri(uri)
    )
    client = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return client[database_name]


def build_data_source_settings_stores(database: Any | None) -> DataSourceSettingsStores:
    if database is None:
        return DataSourceSettingsStores(data_source_store=None, secret_store=None)
    return DataSourceSettingsStores(
        data_source_store=MongoDataSourceStore(database[UI_DATA_SOURCE_SETTINGS_COLLECTION]),
        secret_store=MongoSecretStore(database[UI_SECRET_SETTINGS_COLLECTION]),
    )


class MongoDataSourceStore:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def get(self, instance_id: str) -> dict[str, Any] | None:
        doc = self._collection.find_one({"_id": instance_id})
        return _record_from_doc(doc)

    def list_instances(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            item
            for item in (_record_from_doc(doc) for doc in self._collection.find({}))
            if item is not None
        )

    def upsert(self, record: Mapping[str, Any]) -> dict[str, Any]:
        instance_id = str(record.get("id") or record.get("instance_id") or record.get("supported_type") or "").strip()
        if not instance_id:
            raise ValueError("data source instance id is required for Mongo settings store")
        saved = dict(record)
        saved["id"] = instance_id
        self._collection.replace_one(
            {"_id": instance_id},
            {
                "_id": instance_id,
                "record": saved,
                "schemaVersion": "ui-data-source-settings-v1",
                "updatedAt": _now_iso(),
            },
            upsert=True,
        )
        return saved

    def clear(self) -> None:
        self._collection.delete_many({})


class MongoSecretStore:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def replace(self, *, old_ref: str | None, value: str, scope: str) -> str:
        ref = f"{scope}:{sha1(value.encode('utf-8')).hexdigest()[:12]}"
        self._collection.replace_one(
            {"_id": ref},
            {
                "_id": ref,
                "scope": scope,
                "value": value,
                "schemaVersion": "ui-secret-settings-v1",
                "updatedAt": _now_iso(),
            },
            upsert=True,
        )
        if old_ref and old_ref != ref:
            self._collection.delete_one({"_id": old_ref})
        return ref

    def get(self, secret_ref: str | None) -> str | None:
        if not secret_ref:
            return None
        doc = self._collection.find_one({"_id": secret_ref})
        if not isinstance(doc, Mapping):
            return None
        value = doc.get("value")
        return str(value) if isinstance(value, str) else None

    def clear(self) -> None:
        self._collection.delete_many({})


class MongoEmbeddingConfigStore:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def read(self) -> dict[str, str]:
        doc = self._collection.find_one({"_id": "embedding"})
        payload = doc.get("payload") if isinstance(doc, Mapping) else None
        if not isinstance(payload, Mapping):
            return {}
        return {str(key): str(value) for key, value in payload.items()}

    def write(self, updates: Mapping[str, str]) -> None:
        current = self.read()
        current.update({str(key): str(value).strip() for key, value in updates.items()})
        self._collection.replace_one(
            {"_id": "embedding"},
            {
                "_id": "embedding",
                "payload": current,
                "schemaVersion": "ui-embedding-settings-v1",
                "updatedAt": _now_iso(),
            },
            upsert=True,
        )

    def clear(self) -> None:
        self._collection.delete_one({"_id": "embedding"})


class MongoReportModelConfigStore:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def read(self) -> dict[str, Any]:
        doc = self._collection.find_one({"_id": "report_model_config"})
        payload = doc.get("payload") if isinstance(doc, Mapping) else None
        return dict(payload) if isinstance(payload, Mapping) else {}

    def write(self, payload: Mapping[str, Any]) -> None:
        self._collection.replace_one(
            {"_id": "report_model_config"},
            {
                "_id": "report_model_config",
                "payload": dict(payload),
                "schemaVersion": "ui-report-model-config-v1",
                "updatedAt": _now_iso(),
            },
            upsert=True,
        )

    def clear(self) -> None:
        self._collection.delete_one({"_id": "report_model_config"})


class MongoReportModelStatusStore:
    def __init__(self, collection: Any) -> None:
        self._collection = collection

    def read(self) -> dict[str, Any]:
        doc = self._collection.find_one({"_id": "report_model_status"})
        payload = doc.get("payload") if isinstance(doc, Mapping) else None
        return dict(payload) if isinstance(payload, Mapping) else {}

    def write(self, payload: Mapping[str, Any]) -> None:
        body = dict(payload)
        body["schemaVersion"] = "report-model-status-v1"
        self._collection.replace_one(
            {"_id": "report_model_status"},
            {
                "_id": "report_model_status",
                "payload": body,
                "schemaVersion": "ui-report-model-status-v1",
                "updatedAt": _now_iso(),
            },
            upsert=True,
        )


def _record_from_doc(doc: object) -> dict[str, Any] | None:
    if not isinstance(doc, Mapping):
        return None
    record = doc.get("record")
    if isinstance(record, Mapping):
        return dict(record)
    return None


def _database_name_from_uri(uri: str) -> str:
    parsed = urlparse(uri)
    path_name = parsed.path.strip("/")
    if path_name:
        return path_name.split("/", 1)[0]
    return "claw_trade"


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
