from __future__ import annotations

from claw_trade.runtime.settings_projection import export_runtime_settings_from_mongo
from claw_trade.runtime import settings_projection


class _FakeCollection:
    def __init__(self, docs: dict[str, dict[str, object]]) -> None:
        self.docs = docs

    def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        return self.docs.get(str(query["_id"]))

    def replace_one(self, query: dict[str, object], doc: dict[str, object], *, upsert: bool) -> None:
        _ = upsert
        self.docs[str(query["_id"])] = doc

    def delete_one(self, query: dict[str, object]) -> None:
        self.docs.pop(str(query["_id"]), None)


class _FakeDatabase:
    def __init__(self) -> None:
        self.data_source_docs: dict[str, dict[str, object]] = {}
        self.secret_docs: dict[str, dict[str, object]] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        if name == "ui_report_model_config":
            return _FakeCollection(
                {
                    "report_model_config": {
                        "_id": "report_model_config",
                        "payload": {
                            "provider": "anthropic",
                            "model": "anthropic/claude-sonnet-4-20250514",
                            "api": "anthropic-messages",
                            "apiKey": "sk-ant-1234",
                            "endpointUrl": "https://api.anthropic.com",
                            "providerModelId": "claude-sonnet-4-20250514",
                        },
                    }
                }
            )
        if name == "ui_embedding_settings":
            return _FakeCollection(
                {
                    "embedding": {
                        "_id": "embedding",
                        "payload": {
                            "OPENVIKING_EMBEDDING_PROVIDER": "openai",
                            "OPENVIKING_EMBEDDING_MODEL": "text-embedding-3-small",
                            "OPENVIKING_EMBEDDING_API_KEY": "sk-embed",
                            "OPENVIKING_EMBEDDING_API_BASE": "https://api.openai.com/v1",
                            "OPENVIKING_EMBEDDING_DIMENSION": "1536",
                        },
                    }
                }
            )
        if name == "ui_data_source_settings":
            return _FakeCollection(self.data_source_docs)
        if name == "ui_secret_settings":
            return _FakeCollection(self.secret_docs)
        raise KeyError(name)


def test_export_runtime_settings_from_mongo_projects_report_model_and_embedding(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings_projection, "_open_settings_database_from_env", lambda: _FakeDatabase())

    values = export_runtime_settings_from_mongo()

    assert values["CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER"] == "anthropic"
    assert values["CLAW_TRADE_RUNTIME_REPORT_MODEL_API"] == "anthropic-messages"
    assert values["CLAW_TRADE_RUNTIME_REPORT_MODEL_API_KEY"] == "sk-ant-1234"
    assert values["OPENVIKING_EMBEDDING_PROVIDER"] == "openai"
    assert values["OPENVIKING_EMBEDDING_API_KEY"] == "sk-embed"


def test_export_runtime_settings_imports_finnhub_env_to_mongo_without_exporting_secret(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    database = _FakeDatabase()
    monkeypatch.setattr(settings_projection, "_open_settings_database_from_env", lambda: database)
    monkeypatch.setenv("FINNHUB_API_KEY", "fh-real-key")
    monkeypatch.setenv("FINNHUB_BASE_URL", "https://finnhub.example/api/v1")

    values = export_runtime_settings_from_mongo()

    source_doc = database.data_source_docs["finnhub"]
    source_record = source_doc["record"]
    assert isinstance(source_record, dict)
    assert source_record["supported_type"] == "finnhub"
    assert source_record["enabled"] is True
    assert source_record["endpoint_url"] == "https://finnhub.example/api/v1"
    credential_ref = source_record["credential_ref"]
    assert isinstance(credential_ref, str)
    assert database.secret_docs[credential_ref]["value"] == "fh-real-key"
    assert "FINNHUB_API_KEY" not in values


def test_export_runtime_settings_imports_provider_rate_limit_env_to_mongo(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    database = _FakeDatabase()
    monkeypatch.setattr(settings_projection, "_open_settings_database_from_env", lambda: database)
    monkeypatch.setenv("COINGLASS_API_KEY", "cg-real-key")
    monkeypatch.setenv("COINGLASS_RATE_LIMIT_MAX_CALLS", "10")
    monkeypatch.setenv("COINGLASS_RATE_LIMIT_WINDOW_SECONDS", "60")
    monkeypatch.setenv("COINGLASS_RATE_LIMIT_SAFETY_MARGIN", "1")
    monkeypatch.setenv("COINGLASS_RATE_LIMIT_OVERFLOW", "wait")
    monkeypatch.setenv("COINGLASS_RATE_LIMIT_WAIT_TIMEOUT_SECONDS", "75")

    export_runtime_settings_from_mongo()

    source_record = database.data_source_docs["coinglass"]["record"]
    assert isinstance(source_record, dict)
    assert source_record["rate_limit_max_calls"] == 10
    assert source_record["rate_limit_window_seconds"] == 60
    assert source_record["rate_limit_safety_margin"] == 1
    assert source_record["rate_limit_overflow"] == "wait"
    assert source_record["rate_limit_wait_timeout_seconds"] == 75


def test_export_runtime_settings_imports_all_catalog_data_source_env_keys(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    database = _FakeDatabase()
    monkeypatch.setattr(settings_projection, "_open_settings_database_from_env", lambda: database)
    monkeypatch.setenv("POLYGON_API_KEY", "polygon-key")
    monkeypatch.setenv("POLYGON_BASE_URL", "https://polygon.example")
    monkeypatch.setenv("SEC_EDGAR_BASE_URL", "https://sec.example")

    export_runtime_settings_from_mongo()

    polygon = database.data_source_docs["polygon"]["record"]
    sec = database.data_source_docs["sec_edgar"]["record"]
    assert isinstance(polygon, dict)
    assert isinstance(sec, dict)
    assert polygon["enabled"] is True
    assert polygon["endpoint_url"] == "https://polygon.example"
    assert database.secret_docs[str(polygon["credential_ref"])]["value"] == "polygon-key"
    assert sec["enabled"] is True
    assert sec["endpoint_url"] == "https://sec.example"


def test_export_runtime_settings_skips_env_data_source_import_for_ui_start(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    database = _FakeDatabase()
    monkeypatch.setattr(settings_projection, "_open_settings_database_from_env", lambda: database)
    monkeypatch.setenv("CLAW_TRADE_SKIP_ENV_DATA_SOURCE_IMPORT", "1")
    monkeypatch.setenv("FINNHUB_API_KEY", "fh-real-key")

    export_runtime_settings_from_mongo()

    assert "finnhub" not in database.data_source_docs
