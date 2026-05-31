from __future__ import annotations

from claw_trade.runtime.settings_projection import export_runtime_settings_from_mongo
from claw_trade.runtime import settings_projection


class _FakeCollection:
    def __init__(self, docs: dict[str, dict[str, object]]) -> None:
        self.docs = docs

    def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        return self.docs.get(str(query["_id"]))


class _FakeDatabase:
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
        raise KeyError(name)


def test_export_runtime_settings_from_mongo_projects_report_model_and_embedding(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(settings_projection, "_open_settings_database_from_env", lambda: _FakeDatabase())

    values = export_runtime_settings_from_mongo()

    assert values["CLAW_TRADE_RUNTIME_REPORT_MODEL_PROVIDER"] == "anthropic"
    assert values["CLAW_TRADE_RUNTIME_REPORT_MODEL_API"] == "anthropic-messages"
    assert values["CLAW_TRADE_RUNTIME_REPORT_MODEL_API_KEY"] == "sk-ant-1234"
    assert values["OPENVIKING_EMBEDDING_PROVIDER"] == "openai"
    assert values["OPENVIKING_EMBEDDING_API_KEY"] == "sk-embed"
