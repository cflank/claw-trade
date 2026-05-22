from __future__ import annotations

from pathlib import Path

import pytest

from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge
from claw_trade.ui_backend.settings_service import UiBoundaryError


class _FakeOpenClawLlmClient:
    def __init__(self) -> None:
        self.config_patch_calls = []

    def config_schema_lookup(self, *, path: str):
        return {"path": path, "version": "v1"}

    def config_get(self, *, paths):
        _ = paths
        return {
            "revision": "rev-1",
            "active_provider": "deepseek",
            "agents": {"defaults": {"model": "deepseek-chat"}},
            "models": {"providers": {"deepseek": {"api_key": "sk-test-1234", "endpoint_url": "https://api.example"}}},
        }

    def models_list(self, *, provider=None):
        _ = provider
        return [{"id": "deepseek-chat"}]

    def models_status(self, *, provider=None, as_json=True):
        _ = (provider, as_json)
        return {"ok": True, "message": "ready"}

    def config_patch(self, *, expected_settings_version, patch):
        self.config_patch_calls.append((expected_settings_version, patch))
        return {"newHash": "hash-new"}

    def models_auth_status(self, *, provider, model=None, endpoint_url=None, probe=True):
        _ = (provider, model, endpoint_url, probe)
        return {"ok": False, "message": "auth failed"}


def test_load_llm_settings_masks_key_and_hides_internal_fields(tmp_path: Path) -> None:
    bridge = LlmSettingsBridge(_FakeOpenClawLlmClient(), embedding_env_path=tmp_path / ".env.local")
    payload = bridge.load_llm_settings("deepseek")
    assert payload["draft"]["apiKeyMasked"] == "***1234"
    assert "apiKeyReplacement" not in payload["draft"]
    assert "path" not in payload
    assert "hash" not in payload


def test_load_llm_settings_keeps_form_available_when_model_list_probe_fails(tmp_path: Path) -> None:
    class _SlowModelListClient(_FakeOpenClawLlmClient):
        def config_get(self, *, paths):  # type: ignore[no-untyped-def]
            _ = paths
            return {
                "revision": "rev-2",
                "parsed": {
                    "agents": {"defaults": {"model": {"primary": "deepseek/deepseek-chat"}}},
                    "models": {
                        "providers": {
                            "deepseek": {
                                "api_key": "sk-live-9999",
                                "endpoint_url": "https://api.example",
                            }
                        }
                    },
                },
            }

        def models_list(self, *, provider=None):  # type: ignore[no-untyped-def]
            _ = provider
            raise TimeoutError("gateway timeout")

    bridge = LlmSettingsBridge(_SlowModelListClient(), embedding_env_path=tmp_path / ".env.local")

    payload = bridge.load_llm_settings("deepseek")

    assert payload["draft"]["defaultModel"] == "deepseek/deepseek-chat"
    assert payload["draft"]["apiKeyMasked"] == "***9999"
    assert payload["draft"]["status"] == "saved"


def test_save_llm_config_uses_openclaw_config_patch_only(tmp_path: Path) -> None:
    client = _FakeOpenClawLlmClient()
    bridge = LlmSettingsBridge(client, embedding_env_path=tmp_path / ".env.local")
    out = bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "https://new-endpoint",
            "apiKeyReplacement": "sk-new-5678",
        },
        expected_settings_version="v_1",
        request_id="req-save",
    )
    assert out["status"] == "saved"
    assert client.config_patch_calls
    patch = client.config_patch_calls[0][1]
    assert patch["models"]["providers"]["deepseek"]["api_key"] == "sk-new-5678"


def test_load_llm_settings_reads_embedding_config_from_env_file(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.local"
    env_path.write_text(
        "\n".join(
            (
                "OPENVIKING_EMBEDDING_PROVIDER=openai",
                "OPENVIKING_EMBEDDING_MODEL=text-embedding-3-small",
                "OPENVIKING_EMBEDDING_API_KEY=sk-embed-1234",
                "OPENVIKING_EMBEDDING_API_BASE=https://embedding.example/v1",
                "OPENVIKING_EMBEDDING_DIMENSION=1536",
            )
        ),
        encoding="utf-8",
    )
    bridge = LlmSettingsBridge(_FakeOpenClawLlmClient(), embedding_env_path=env_path)

    payload = bridge.load_llm_settings("deepseek")

    embedding = payload["draft"]["embedding"]
    assert embedding["enabled"] is True
    assert embedding["provider"] == "openai"
    assert embedding["model"] == "text-embedding-3-small"
    assert embedding["apiKeyMasked"] == "***1234"
    assert embedding["endpointUrl"] == "https://embedding.example/v1"
    assert embedding["dimension"] == "1536"


def test_save_llm_config_writes_embedding_config_to_env_file(tmp_path: Path) -> None:
    client = _FakeOpenClawLlmClient()
    env_path = tmp_path / ".env.local"
    bridge = LlmSettingsBridge(client, embedding_env_path=env_path)

    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "embedding": {
                "provider": "openai",
                "model": "text-embedding-3-small",
                "endpointUrl": "https://embedding.example/v1",
                "dimension": "1536",
                "apiKeyReplacement": "sk-embed",
            },
        },
        expected_settings_version="v_1",
        request_id="req-embed-save",
    )

    text = env_path.read_text(encoding="utf-8")
    assert "OPENVIKING_EMBEDDING_PROVIDER=openai" in text
    assert "OPENVIKING_EMBEDDING_MODEL=text-embedding-3-small" in text
    assert "OPENVIKING_EMBEDDING_API_KEY=sk-embed" in text
    assert "OPENVIKING_EMBEDDING_API_BASE=https://embedding.example/v1" in text
    assert "OPENVIKING_EMBEDDING_DIMENSION=1536" in text


def test_save_llm_config_rejects_partial_embedding_config(tmp_path: Path) -> None:
    bridge = LlmSettingsBridge(_FakeOpenClawLlmClient(), embedding_env_path=tmp_path / ".env.local")

    with pytest.raises(UiBoundaryError) as exc:
        bridge.save_llm_config_via_openclaw(
            draft={
                "provider": "deepseek",
                "defaultModel": "deepseek-chat",
                "embedding": {"provider": "openai", "model": ""},
            },
            expected_settings_version="v_1",
            request_id="req-embed-partial",
        )

    assert exc.value.code == "INVALID_INPUT"


def test_test_llm_requires_provider() -> None:
    bridge = LlmSettingsBridge(_FakeOpenClawLlmClient())
    with pytest.raises(UiBoundaryError) as exc:
        bridge.test_llm_via_openclaw({}, request_id="req-test")
    assert exc.value.code == "INVALID_INPUT"


def test_test_llm_returns_user_message_without_internal_terms() -> None:
    bridge = LlmSettingsBridge(_FakeOpenClawLlmClient())
    out = bridge.test_llm_via_openclaw({"provider": "deepseek"}, request_id="req-test-2")
    assert out["ok"] is False
    assert out["userMessage"]
