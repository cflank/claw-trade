from __future__ import annotations

from claw_trade.data_gateway.provider_user_settings import merge_runtime_data_source_settings
from claw_trade.data_gateway.providers import defaults
from claw_trade.data_gateway.providers.defaults import build_default_provider_adapters


class _FakeCollection:
    def __init__(self, docs: list[dict[str, object]] | None = None) -> None:
        self._docs = docs or []

    def find(self, query: dict[str, object]):  # type: ignore[no-untyped-def]
        _ = query
        return list(self._docs)

    def find_one(self, query: dict[str, object]) -> dict[str, object] | None:
        key = str(query["_id"])
        for doc in self._docs:
            if doc.get("_id") == key:
                return doc
        return None


class _FakeDatabase:
    def __init__(self) -> None:
        self.collections = {
            "ui_data_source_settings": _FakeCollection(
                [
                    {
                        "_id": "builtin-coinglass",
                        "record": {
                            "id": "builtin-coinglass",
                            "supported_type": "coinglass",
                            "enabled": True,
                            "credential_ref": "data_source:cg",
                            "endpoint_url": "https://coinglass.example",
                            "header_name": "X-Test-Key",
                        },
                    },
                    {
                        "_id": "builtin-tushare",
                        "record": {
                            "id": "builtin-tushare",
                            "supported_type": "tushare",
                            "enabled": False,
                            "credential_ref": "data_source:ts",
                        },
                    },
                ]
            ),
            "ui_secret_settings": _FakeCollection(
                [
                    {"_id": "data_source:cg", "value": "mongo-cg-key"},
                    {"_id": "data_source:ts", "value": "mongo-ts-token"},
                ]
            ),
        }

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections[name]


def test_merge_runtime_data_source_settings_uses_enabled_mongo_records_as_authority() -> None:
    env = merge_runtime_data_source_settings(
        {
            "COINGLASS_API_KEY": "env-cg-key",
            "TUSHARE_TOKEN": "env-ts-token",
            "KEEP_ME": "1",
        },
        _FakeDatabase(),
    )

    assert env["COINGLASS_API_KEY"] == "mongo-cg-key"
    assert env["COINGLASS_API_BASE"] == "https://coinglass.example"
    assert env["COINGLASS_API_HEADER_NAME"] == "X-Test-Key"
    assert "TUSHARE_TOKEN" not in env
    assert env["KEEP_ME"] == "1"


def test_default_provider_adapters_read_runtime_data_source_settings_when_env_not_explicit(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        defaults,
        "build_runtime_data_source_env",
        lambda env=None: {"COINGLASS_API_KEY": "mongo-cg-key"} if env is None else dict(env),
    )

    adapters = build_default_provider_adapters(provider_config_version="cfg")
    coinglass = next(item for item in adapters if item.adapter_id == "project.crypto.derivatives")
    assert coinglass.validate_credentials().ok

    explicit_env_adapters = build_default_provider_adapters(provider_config_version="cfg", env={})
    explicit_coinglass = next(item for item in explicit_env_adapters if item.adapter_id == "project.crypto.derivatives")
    assert explicit_coinglass.validate_credentials().missing
