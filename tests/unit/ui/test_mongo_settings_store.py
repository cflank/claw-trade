from __future__ import annotations

from claw_trade.ui_backend.mongo_settings_store import (
    MongoDataSourceStore,
    MongoEmbeddingConfigStore,
    MongoReportModelConfigStore,
    MongoReportModelStatusStore,
    MongoSecretStore,
)


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict[str, object]] = {}

    def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        return self.docs.get(str(query["_id"]))

    def find(self, query: dict[str, object]):  # type: ignore[no-untyped-def]
        _ = query
        return list(self.docs.values())

    def replace_one(self, query: dict[str, object], doc: dict[str, object], *, upsert: bool) -> None:
        _ = upsert
        self.docs[str(query["_id"])] = doc

    def delete_many(self, query: dict[str, object]) -> None:
        _ = query
        self.docs.clear()

    def delete_one(self, query: dict[str, object]) -> None:
        self.docs.pop(str(query["_id"]), None)


def test_mongo_data_source_store_persists_records_by_instance_id() -> None:
    collection = _FakeCollection()
    store = MongoDataSourceStore(collection)

    saved = store.upsert({"id": "builtin-tushare", "supported_type": "tushare", "enabled": True})

    assert saved["id"] == "builtin-tushare"
    assert store.get("builtin-tushare") == saved
    assert store.list_instances() == (saved,)


def test_mongo_secret_store_replaces_and_reads_secret_value() -> None:
    store = MongoSecretStore(_FakeCollection())

    ref = store.replace(old_ref=None, value="secret-token", scope="data_source")

    assert ref.startswith("data_source:")
    assert store.get(ref) == "secret-token"


def test_mongo_embedding_config_store_merges_partial_updates() -> None:
    store = MongoEmbeddingConfigStore(_FakeCollection())

    store.write({"OPENVIKING_EMBEDDING_API_KEY": "sk-old", "OPENVIKING_EMBEDDING_MODEL": "m1"})
    store.write({"OPENVIKING_EMBEDDING_MODEL": "m2"})

    assert store.read()["OPENVIKING_EMBEDDING_API_KEY"] == "sk-old"
    assert store.read()["OPENVIKING_EMBEDDING_MODEL"] == "m2"


def test_mongo_report_model_status_store_reads_written_status() -> None:
    store = MongoReportModelStatusStore(_FakeCollection())

    store.write({"state": "ready", "fingerprint": "fp-1"})

    assert store.read()["state"] == "ready"
    assert store.read()["fingerprint"] == "fp-1"


def test_mongo_report_model_config_store_reads_written_config() -> None:
    store = MongoReportModelConfigStore(_FakeCollection())

    store.write({"provider": "deepseek", "model": "deepseek/deepseek-chat", "apiKey": "sk-real"})

    assert store.read()["provider"] == "deepseek"
    assert store.read()["apiKey"] == "sk-real"
