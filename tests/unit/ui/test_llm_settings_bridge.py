from __future__ import annotations

import json
from hashlib import sha1
from pathlib import Path

import pytest
from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge
from claw_trade.ui_backend.settings_service import UiBoundaryError


class _FakeOpenClawLlmClient:
    def __init__(self) -> None:
        self.config_patch_calls = []
        self.config_schema_lookup_calls = []

    def config_schema_lookup(self, *, path: str):
        self.config_schema_lookup_calls.append(path)
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

    def models_probe_status(self, *, provider, model=None, endpoint_url=None):
        _ = endpoint_url
        return {
            "auth": {
                "probes": {
                    "results": [
                        {
                            "provider": provider,
                            "model": model or "deepseek/deepseek-chat",
                            "status": "auth",
                        }
                    ]
                }
            }
        }


class _FakeReportModelConfigStore:
    def __init__(self) -> None:
        self.payload: dict[str, object] = {}
        self.cleared = False

    def read(self) -> dict[str, object]:
        return dict(self.payload)

    def write(self, payload):  # type: ignore[no-untyped-def]
        self.payload = dict(payload)

    def clear(self) -> None:
        self.cleared = True
        self.payload = {}


def _bridge(
    client: _FakeOpenClawLlmClient,
    tmp_path: Path,
    *,
    env_path: Path | None = None,
    embedding_probe=None,
    report_model_config_store=None,
) -> LlmSettingsBridge:
    return LlmSettingsBridge(
        client,
        embedding_env_path=env_path or tmp_path / ".env.local",
        report_model_status_path=tmp_path / "report-model-status.json",
        embedding_probe=embedding_probe,
        report_model_config_store=report_model_config_store,
    )


def test_load_llm_settings_masks_key_and_hides_internal_fields(tmp_path: Path) -> None:
    bridge = _bridge(_FakeOpenClawLlmClient(), tmp_path)
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

    bridge = _bridge(_SlowModelListClient(), tmp_path)

    payload = bridge.load_llm_settings("deepseek")

    assert payload["draft"]["defaultModel"] == "deepseek/deepseek-chat"
    assert payload["draft"]["apiKeyMasked"] == "***9999"
    assert payload["draft"]["status"] == "saved"


def test_save_llm_config_uses_openclaw_config_patch(tmp_path: Path) -> None:
    client = _FakeOpenClawLlmClient()
    bridge = _bridge(client, tmp_path)
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
    assert client.config_patch_calls[0][0] == "rev-1"
    patch = client.config_patch_calls[0][1]
    assert patch["agents"]["defaults"]["model"] == {"primary": "deepseek/deepseek-chat"}
    assert patch["agents"]["defaults"]["models"] == {"deepseek/deepseek-chat": {}}
    provider_patch = patch["models"]["providers"]["deepseek"]
    assert provider_patch["baseUrl"] == "https://new-endpoint"
    assert provider_patch["api"] == "openai-completions"
    assert provider_patch["apiKey"] == "sk-new-5678"
    assert provider_patch["models"] == [
        {
            "id": "deepseek-chat",
            "name": "deepseek-chat",
            "reasoning": False,
            "input": ["text"],
        }
    ]
    assert client.config_schema_lookup_calls == []


def test_save_llm_config_skips_openclaw_patch_when_unchanged(tmp_path: Path) -> None:
    client = _FakeOpenClawLlmClient()
    bridge = _bridge(client, tmp_path)

    out = bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek/deepseek-chat",
            "endpointUrl": "https://api.example",
        },
        expected_settings_version="v_1",
        request_id="req-save-noop",
    )

    assert out["status"] == "saved"
    assert client.config_patch_calls == []


def test_save_llm_config_maps_gateway_runtime_errors_to_save_message(tmp_path: Path) -> None:
    class _FailingSaveClient(_FakeOpenClawLlmClient):
        def config_patch(self, *, expected_settings_version, patch):  # type: ignore[no-untyped-def]
            _ = (expected_settings_version, patch)
            raise RuntimeError("rate_limit 429")

    bridge = _bridge(_FailingSaveClient(), tmp_path)

    with pytest.raises(UiBoundaryError) as exc:
        bridge.save_llm_config_via_openclaw(
            draft={
                "provider": "deepseek",
                "defaultModel": "deepseek-chat",
                "endpointUrl": "https://api.deepseek.com",
                "apiKeyReplacement": "sk-new-5678",
            },
            expected_settings_version="v_1",
            request_id="req-save-rate-limited",
        )

    assert exc.value.code == "ASSISTANT_UNAVAILABLE"
    assert exc.value.user_message == "模型配置暂不可保存，请稍后重试。"


def test_test_llm_config_skips_openclaw_patch_when_unchanged(tmp_path: Path) -> None:
    client = _FakeOpenClawLlmClient()
    bridge = _bridge(client, tmp_path)

    out = bridge.test_llm_via_openclaw(
        {
            "provider": "deepseek",
            "model": "deepseek/deepseek-chat",
            "endpointUrl": "https://api.example",
            "apiKeyReplacement": "",
        },
        request_id="req-test-noop",
    )

    assert out["ok"] is False
    assert client.config_patch_calls == []


def test_save_llm_config_rejects_direct_openai_provider(tmp_path: Path) -> None:
    client = _FakeOpenClawLlmClient()
    bridge = _bridge(client, tmp_path)

    with pytest.raises(UiBoundaryError) as exc:
        bridge.save_llm_config_via_openclaw(
            draft={
                "provider": "openai",
                "defaultModel": "blocked-model",
                "endpointUrl": "https://blocked.example/v1",
                "apiKeyReplacement": "sk-test",
            },
            expected_settings_version="v_1",
            request_id="req-openai",
        )

    assert exc.value.code == "INVALID_INPUT"
    assert client.config_patch_calls == []


def test_save_llm_config_rejects_case_variant_and_unknown_provider(tmp_path: Path) -> None:
    client = _FakeOpenClawLlmClient()
    bridge = _bridge(client, tmp_path)

    for provider in ("OpenAI", "unknown_vendor"):
        with pytest.raises(UiBoundaryError) as exc:
            bridge.save_llm_config_via_openclaw(
                draft={
                    "provider": provider,
                    "defaultModel": "custom-model",
                    "endpointUrl": "https://blocked.example/v1",
                    "apiKeyReplacement": "sk-test",
                },
                expected_settings_version="v_1",
                request_id=f"req-{provider}",
            )
        assert exc.value.code == "INVALID_INPUT"

    assert client.config_patch_calls == []


def test_load_llm_settings_hides_stale_direct_openai_config(tmp_path: Path) -> None:
    class _StaleOpenAiClient(_FakeOpenClawLlmClient):
        def config_get(self, *, paths):  # type: ignore[no-untyped-def]
            _ = paths
            return {
                "revision": "rev-openai",
                "parsed": {
                    "agents": {"defaults": {"model": {"primary": "openai/blocked-model"}}},
                    "models": {
                        "providers": {
                            "openai": {
                                "apiKey": "sk-old",
                                "baseUrl": "https://blocked.example/v1",
                                "models": [{"id": "blocked-model"}],
                            }
                        }
                    },
                },
            }

    bridge = _bridge(_StaleOpenAiClient(), tmp_path)

    payload = bridge.load_llm_settings()

    assert payload["draft"]["provider"] == "deepseek"
    assert payload["draft"]["reportModelStatus"]["state"] == "unconfigured"
    assert payload["draft"]["reportModelStatus"]["ready"] is False


def test_save_llm_config_writes_runtime_mongo_config(tmp_path: Path) -> None:
    client = _FakeOpenClawLlmClient()
    store = _FakeReportModelConfigStore()
    bridge = _bridge(client, tmp_path, report_model_config_store=store)

    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "glm",
            "defaultModel": "glm/glm-4.5",
            "endpointUrl": "https://open.bigmodel.cn/api/paas/v4",
            "apiKeyReplacement": "sk-glm-1234",
        },
        expected_settings_version="v_1",
        request_id="req-save-runtime-config",
    )

    assert store.payload["provider"] == "glm"
    assert store.payload["model"] == "glm/glm-4.5"
    assert store.payload["api"] == "openai-completions"
    assert store.payload["apiKey"] == "sk-glm-1234"


def test_save_llm_config_prefers_current_openclaw_key_over_stale_runtime_store(tmp_path: Path) -> None:
    class _CurrentConfigClient(_FakeOpenClawLlmClient):
        def config_get(self, *, paths):  # type: ignore[no-untyped-def]
            _ = paths
            return {
                "revision": "rev-1",
                "agents": {"defaults": {"model": {"primary": "deepseek/deepseek-chat"}}},
                "models": {
                    "providers": {
                        "deepseek": {
                            "apiKey": "sk-current-openclaw",
                            "baseUrl": "https://api.deepseek.com",
                            "models": [{"id": "deepseek-chat"}],
                        }
                    }
                },
            }

    store = _FakeReportModelConfigStore()
    store.payload = {
        "provider": "deepseek",
        "model": "deepseek/deepseek-chat",
        "endpointUrl": "https://api.deepseek.com",
        "apiKey": "sk-stale-runtime-store",
    }
    bridge = _bridge(_CurrentConfigClient(), tmp_path, report_model_config_store=store)

    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek/deepseek-chat",
            "endpointUrl": "https://api.deepseek.com",
        },
        expected_settings_version="v_1",
        request_id="req-save-current-key",
    )

    assert store.payload["apiKey"] == "sk-current-openclaw"


def test_save_domestic_glm_llm_config_uses_openclaw_provider_schema(tmp_path: Path) -> None:
    client = _FakeOpenClawLlmClient()
    bridge = _bridge(client, tmp_path)

    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "glm",
            "defaultModel": "glm/glm-4.5",
            "endpointUrl": "https://open.bigmodel.cn/api/paas/v4",
            "apiKeyReplacement": "sk-glm-1234",
        },
        expected_settings_version="v_1",
        request_id="req-save-glm",
    )

    patch = client.config_patch_calls[0][1]
    assert patch["agents"]["defaults"]["model"] == {"primary": "glm/glm-4.5"}
    provider_patch = patch["models"]["providers"]["glm"]
    assert provider_patch["baseUrl"] == "https://open.bigmodel.cn/api/paas/v4"
    assert provider_patch["api"] == "openai-completions"
    assert provider_patch["apiKey"] == "sk-glm-1234"
    assert provider_patch["models"][0]["id"] == "glm-4.5"


def test_reset_llm_settings_clears_report_model_status_and_embedding_env(tmp_path: Path) -> None:
    client = _FakeOpenClawLlmClient()
    env_path = tmp_path / ".env.local"
    env_path.write_text(
        "\n".join(
            [
                "OPENVIKING_EMBEDDING_PROVIDER=openai",
                "OPENVIKING_EMBEDDING_MODEL=text-embedding-3-small",
                "OPENVIKING_EMBEDDING_API_KEY=emb-secret",
                "OTHER_SETTING=keep",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    bridge = _bridge(client, tmp_path, env_path=env_path)
    bridge.save_llm_config_via_openclaw(
        draft={"provider": "deepseek", "defaultModel": "deepseek-chat", "apiKeyReplacement": "sk-test-1234"},
        expected_settings_version="v_1",
        request_id="req-save-before-reset",
    )

    out = bridge.reset_llm_settings_to_defaults("req-reset")

    assert out["status"] == "reset"
    reset_patch = client.config_patch_calls[-1][1]
    assert reset_patch["agents"]["defaults"]["model"] is None
    assert reset_patch["models"]["providers"]["deepseek"] is None
    env_text = env_path.read_text(encoding="utf-8")
    assert "OPENVIKING_EMBEDDING_PROVIDER" not in env_text
    assert "OPENVIKING_EMBEDDING_API_KEY" not in env_text
    assert "OTHER_SETTING=keep" in env_text
    status_payload = (tmp_path / "report-model-status.json").read_text(encoding="utf-8")
    assert '"state": "unconfigured"' in status_payload


def test_load_llm_settings_returns_unconfigured_draft_when_config_gateway_fails(tmp_path: Path) -> None:
    class _BrokenConfigClient(_FakeOpenClawLlmClient):
        def config_get(self, *, paths):  # type: ignore[no-untyped-def]
            _ = paths
            raise RuntimeError("gateway unavailable")

    bridge = _bridge(_BrokenConfigClient(), tmp_path)

    payload = bridge.load_llm_settings()

    assert payload["draft"]["provider"] == "deepseek"
    assert payload["draft"]["endpointUrl"] == "https://api.deepseek.com"
    assert payload["draft"]["defaultModel"] == "deepseek/deepseek-chat"
    assert payload["draft"]["reportModelStatus"]["state"] == "unconfigured"
    assert payload["draft"]["reportModelStatus"]["ready"] is False


def test_get_report_model_status_uses_current_openclaw_config_before_stale_runtime_store(tmp_path: Path) -> None:
    class _CurrentConfigClient(_FakeOpenClawLlmClient):
        def __init__(self) -> None:
            super().__init__()
            self.config_get_call_count = 0

        def config_get(self, *, paths):  # type: ignore[no-untyped-def]
            self.config_get_call_count += 1
            _ = paths
            return {
                "revision": "rev-current",
                "active_provider": "deepseek",
                "agents": {"defaults": {"model": {"primary": "deepseek/deepseek-chat"}}},
                "models": {
                    "providers": {
                        "deepseek": {
                            "api_key": "sk-current",
                            "endpoint_url": "https://api.example",
                        }
                    }
                },
            }

    store = _FakeReportModelConfigStore()
    store.payload = {
        "provider": "deepseek",
        "model": "deepseek/deepseek-chat",
        "endpointUrl": "https://api.example",
        "apiKey": "sk-stale",
    }
    fingerprint = sha1("deepseek|deepseek/deepseek-chat|https://api.example|sk-current".encode("utf-8")).hexdigest()
    (tmp_path / "report-model-status.json").write_text(
        json.dumps(
            {
                "state": "ready",
                "fingerprint": fingerprint,
                "checkedAt": "2026-05-20T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    client = _CurrentConfigClient()
    bridge = _bridge(client, tmp_path, report_model_config_store=store)

    payload = bridge.get_report_model_status()

    assert client.config_get_call_count == 1
    assert payload["state"] == "ready"
    assert payload["ready"] is True


def test_get_report_model_status_falls_back_to_saved_status_when_openclaw_config_fails(tmp_path: Path) -> None:
    class _BrokenConfigClient(_FakeOpenClawLlmClient):
        def config_get(self, *, paths):  # type: ignore[no-untyped-def]
            _ = paths
            raise RuntimeError("gateway unavailable")

    store = _FakeReportModelConfigStore()
    store.payload = {
        "provider": "deepseek",
        "model": "deepseek/deepseek-chat",
        "endpointUrl": "https://api.example",
        "apiKey": "sk-test-1234",
    }
    fingerprint = sha1("deepseek|deepseek/deepseek-chat|https://api.example|sk-test-1234".encode("utf-8")).hexdigest()
    (tmp_path / "report-model-status.json").write_text(
        json.dumps(
            {
                "state": "ready",
                "fingerprint": fingerprint,
                "checkedAt": "2026-05-20T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    bridge = _bridge(_BrokenConfigClient(), tmp_path, report_model_config_store=store)

    payload = bridge.get_report_model_status()

    assert payload["state"] == "ready"
    assert payload["ready"] is True


def test_get_report_model_status_falls_back_to_last_test_when_runtime_config_projection_is_empty(tmp_path: Path) -> None:
    class _BrokenConfigClient(_FakeOpenClawLlmClient):
        def config_get(self, *, paths):  # type: ignore[no-untyped-def]
            _ = paths
            raise RuntimeError("gateway unavailable")

    store = _FakeReportModelConfigStore()
    (tmp_path / "report-model-status.json").write_text(
        json.dumps({"state": "ready", "fingerprint": "last-known", "checkedAt": "2026-05-20T10:00:00Z"}),
        encoding="utf-8",
    )
    bridge = _bridge(_BrokenConfigClient(), tmp_path, report_model_config_store=store)

    payload = bridge.get_report_model_status()

    assert payload["state"] == "ready"
    assert payload["ready"] is True


def test_get_report_model_status_ignores_ready_status_for_disabled_provider(tmp_path: Path) -> None:
    class _OpenAiConfigClient(_FakeOpenClawLlmClient):
        def config_get(self, *, paths):  # type: ignore[no-untyped-def]
            _ = paths
            return {
                "revision": "rev-openai",
                "active_provider": "openai",
                "agents": {"defaults": {"model": {"primary": "openai/blocked-model"}}},
                "models": {
                    "providers": {
                        "openai": {
                            "api_key": "sk-old",
                            "endpoint_url": "https://blocked.example/v1",
                        }
                    }
                },
            }

    store = _FakeReportModelConfigStore()
    store.payload = {
        "provider": "openai",
        "model": "openai/blocked-model",
        "endpointUrl": "https://blocked.example/v1",
        "apiKey": "sk-old",
    }
    (tmp_path / "report-model-status.json").write_text(
        json.dumps(
            {
                "state": "ready",
                "provider": "openai",
                "fingerprint": "old-ready",
                "checkedAt": "2026-05-20T10:00:00Z",
            }
        ),
        encoding="utf-8",
    )
    bridge = _bridge(_OpenAiConfigClient(), tmp_path, report_model_config_store=store)

    payload = bridge.get_report_model_status()

    assert payload["state"] == "unconfigured"
    assert payload["ready"] is False


def test_get_report_model_status_requires_matching_test_fingerprint(tmp_path: Path) -> None:
    store = _FakeReportModelConfigStore()
    store.payload = {
        "provider": "deepseek",
        "model": "deepseek/deepseek-chat",
        "endpointUrl": "https://api.example",
        "apiKey": "sk-new-key",
    }
    (tmp_path / "report-model-status.json").write_text(
        json.dumps({"state": "ready", "fingerprint": "old", "checkedAt": "2026-05-20T10:00:00Z"}),
        encoding="utf-8",
    )
    bridge = _bridge(_FakeOpenClawLlmClient(), tmp_path, report_model_config_store=store)

    payload = bridge.get_report_model_status()

    assert payload["state"] == "saved_unverified"
    assert payload["ready"] is False


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
    bridge = _bridge(_FakeOpenClawLlmClient(), tmp_path, env_path=env_path)

    payload = bridge.load_llm_settings("deepseek")

    embedding = payload["draft"]["embedding"]
    assert embedding["enabled"] is True
    assert embedding["provider"] == "openai"
    assert embedding["model"] == "text-embedding-3-small"
    assert embedding["apiKeyMasked"] == "***1234"
    assert embedding["endpointUrl"] == "https://embedding.example/v1"
    assert embedding["dimension"] == "1536"


def test_save_embedding_config_writes_embedding_config_to_env_file(tmp_path: Path) -> None:
    client = _FakeOpenClawLlmClient()
    env_path = tmp_path / ".env.local"
    bridge = _bridge(client, tmp_path, env_path=env_path)

    out = bridge.save_embedding_config_via_openviking(
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "endpointUrl": "https://embedding.example/v1",
            "dimension": "1536",
            "apiKeyReplacement": "sk-embed",
        },
        request_id="req-embed-save",
    )

    assert out["status"] == "saved"
    text = env_path.read_text(encoding="utf-8")
    assert "OPENVIKING_EMBEDDING_PROVIDER=openai" in text
    assert "OPENVIKING_EMBEDDING_MODEL=text-embedding-3-small" in text
    assert "OPENVIKING_EMBEDDING_API_KEY=sk-embed" in text
    assert "OPENVIKING_EMBEDDING_API_BASE=https://embedding.example/v1" in text
    assert "OPENVIKING_EMBEDDING_DIMENSION=1536" in text


def test_save_embedding_config_stores_embedding_base_url_not_full_endpoint(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.local"
    bridge = _bridge(_FakeOpenClawLlmClient(), tmp_path, env_path=env_path)

    bridge.save_embedding_config_via_openviking(
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "endpointUrl": "https://api.openai.com/v1/embeddings",
            "dimension": "1536",
            "apiKeyReplacement": "sk-embed",
        },
        request_id="req-embed-save-full-endpoint",
    )

    text = env_path.read_text(encoding="utf-8")
    assert "OPENVIKING_EMBEDDING_API_BASE=https://api.openai.com/v1\n" in text
    assert "/embeddings" not in text


def test_save_embedding_config_rejects_partial_embedding_config(tmp_path: Path) -> None:
    bridge = _bridge(_FakeOpenClawLlmClient(), tmp_path)

    with pytest.raises(UiBoundaryError) as exc:
        bridge.save_embedding_config_via_openviking(
            {"provider": "openai", "model": ""},
            request_id="req-embed-partial",
        )

    assert exc.value.code == "INVALID_INPUT"


def test_save_llm_config_does_not_write_embedding_config(tmp_path: Path) -> None:
    client = _FakeOpenClawLlmClient()
    env_path = tmp_path / ".env.local"
    bridge = _bridge(client, tmp_path, env_path=env_path)

    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "embedding": {
                "provider": "openai",
                "model": "text-embedding-3-small",
                "apiKeyReplacement": "sk-embed",
            },
        },
        expected_settings_version="v_1",
        request_id="req-report-model-save-with-embedding",
    )

    assert not env_path.exists()


def test_test_embedding_uses_openviking_embedding_probe_with_saved_key(tmp_path: Path) -> None:
    env_path = tmp_path / ".env.local"
    env_path.write_text("OPENVIKING_EMBEDDING_API_KEY=sk-existing-embed\n", encoding="utf-8")
    captured = {}

    def _probe(payload):  # type: ignore[no-untyped-def]
        captured.update(payload)
        return {"ok": True, "denseDimension": 1536}

    bridge = _bridge(_FakeOpenClawLlmClient(), tmp_path, env_path=env_path, embedding_probe=_probe)

    out = bridge.test_embedding_via_openviking(
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "endpointUrl": "https://embedding.example/v1",
            "dimension": "1536",
        },
        request_id="req-embed-test",
    )

    assert out["ok"] is True
    assert out["userMessage"] == "Embedding 连接测试通过，已经能按意思生成检索向量。"
    assert captured["provider"] == "openai"
    assert captured["model"] == "text-embedding-3-small"
    assert captured["apiKey"] == "sk-existing-embed"
    assert captured["endpointUrl"] == "https://embedding.example/v1"
    assert captured["dimension"] == "1536"
    assert not (tmp_path / "report-model-status.json").exists()


def test_test_embedding_sends_embedding_base_url_not_full_endpoint(tmp_path: Path) -> None:
    captured = {}

    def _probe(payload):  # type: ignore[no-untyped-def]
        captured.update(payload)
        return {"ok": True, "denseDimension": 1536}

    bridge = _bridge(_FakeOpenClawLlmClient(), tmp_path, embedding_probe=_probe)

    bridge.test_embedding_via_openviking(
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "endpointUrl": "https://api.openai.com/v1/embeddings",
            "dimension": "1536",
            "apiKeyReplacement": "sk-embed",
        },
        request_id="req-embed-test-full-endpoint",
    )

    assert captured["endpointUrl"] == "https://api.openai.com/v1"


def test_test_embedding_returns_plain_failure_without_touching_report_model_status(tmp_path: Path) -> None:
    status_path = tmp_path / "report-model-status.json"
    status_path.write_text('{"state":"ready","model":"deepseek-chat"}', encoding="utf-8")

    def _probe(_payload):  # type: ignore[no-untyped-def]
        raise RuntimeError("OpenViking gateway internal traceback")

    bridge = _bridge(_FakeOpenClawLlmClient(), tmp_path, embedding_probe=_probe)

    out = bridge.test_embedding_via_openviking(
        {
            "provider": "openai",
            "model": "text-embedding-3-small",
            "apiKeyReplacement": "sk-embed",
        },
        request_id="req-embed-fail",
    )

    assert out["ok"] is False
    assert out["userMessage"] == "Embedding 连接测试失败，请检查服务商、模型、API Key 和接口地址后重试。"
    assert "OpenViking" not in out["userMessage"]
    assert status_path.read_text(encoding="utf-8") == '{"state":"ready","model":"deepseek-chat"}'


def test_test_embedding_rejects_partial_embedding_config(tmp_path: Path) -> None:
    bridge = _bridge(_FakeOpenClawLlmClient(), tmp_path)

    with pytest.raises(UiBoundaryError) as exc:
        bridge.test_embedding_via_openviking(
            {"provider": "openai", "model": ""},
            request_id="req-embed-partial",
        )

    assert exc.value.code == "INVALID_INPUT"


def test_test_llm_requires_provider(tmp_path: Path) -> None:
    bridge = _bridge(_FakeOpenClawLlmClient(), tmp_path)
    with pytest.raises(UiBoundaryError) as exc:
        bridge.test_llm_via_openclaw({}, request_id="req-test")
    assert exc.value.code == "INVALID_INPUT"


def test_test_llm_returns_user_message_without_internal_terms(tmp_path: Path) -> None:
    bridge = _bridge(_FakeOpenClawLlmClient(), tmp_path)
    out = bridge.test_llm_via_openclaw({"provider": "deepseek"}, request_id="req-test-2")
    assert out["ok"] is False
    assert out["userMessage"]
