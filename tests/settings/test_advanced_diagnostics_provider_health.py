from __future__ import annotations

from pathlib import Path
from typing import Any

from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge


class _FakeGateway:
    def __init__(self) -> None:
        self._revision = 0
        self._auth_payload: dict[str, Any] = {"ok": False, "message": "auth failed"}
        self.auth_calls: list[dict[str, Any]] = []
        self._config: dict[str, object] = {
            "active_provider": "",
            "agents": {"defaults": {"model": ""}},
            "models": {"providers": {}},
        }

    def set_auth_result(self, *, ok: bool, message: str) -> None:
        self._auth_payload = {"ok": ok, "message": message}

    def config_schema_lookup(self, *, path: str) -> dict[str, str]:
        return {"path": path}

    def config_get(self, *, paths):  # type: ignore[no-untyped-def]
        _ = paths
        self._revision += 1
        return {"revision": f"rev-{self._revision}", "parsed": self._config}

    def config_patch(self, *, expected_settings_version, patch):  # type: ignore[no-untyped-def]
        _ = expected_settings_version
        provider, provider_patch = next(iter(patch["models"]["providers"].items()))
        providers = self._config["models"]["providers"]  # type: ignore[index]
        providers[provider] = {
            **providers.get(provider, {}),  # type: ignore[union-attr]
            **provider_patch,
        }
        self._config["active_provider"] = provider
        self._config["agents"]["defaults"]["model"] = patch["agents"]["defaults"]["model"]  # type: ignore[index]
        self._revision += 1
        return {"newHash": f"hash-{self._revision}"}

    def models_auth_status(
        self,
        *,
        provider: str,
        model: str | None = None,
        endpoint_url: str | None = None,
        probe: bool = True,
    ) -> dict[str, Any]:
        self.auth_calls.append(
            {
                "provider": provider,
                "model": model,
                "endpoint_url": endpoint_url,
                "probe": probe,
            }
        )
        return dict(self._auth_payload)

    def models_probe_status(
        self,
        *,
        provider: str,
        model: str | None = None,
        endpoint_url: str | None = None,
    ) -> dict[str, Any]:
        _ = endpoint_url
        status = "ok" if bool(self._auth_payload.get("ok")) else "auth"
        return {
            "auth": {
                "probes": {
                    "results": [
                        {
                            "provider": provider,
                            "model": model or "",
                            "status": status,
                        }
                    ]
                }
            }
        }


def _bridge(tmp_path: Path, gateway: _FakeGateway) -> LlmSettingsBridge:
    return LlmSettingsBridge(
        gateway,
        embedding_env_path=tmp_path / ".env.local",
        report_model_status_path=tmp_path / "report-model-status.json",
    )


def test_provider_health_summary_reads_real_status_and_is_not_hardcoded(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    gateway._config = {
        "active_provider": "deepseek",
        "agents": {"defaults": {"model": "deepseek-chat"}},
        "models": {"providers": {"deepseek": {"api_key": "sk-real", "endpoint_url": "https://api.example.com"}}},
    }
    bridge = _bridge(tmp_path, gateway)

    gateway.set_auth_result(ok=True, message="ok")
    healthy = bridge.get_provider_health_summary()
    assert healthy["state"] == "healthy"
    assert healthy["source"] == "openclaw.models.authStatus"

    gateway.set_auth_result(ok=False, message="gateway timeout")
    degraded = bridge.get_provider_health_summary()
    assert degraded["state"] == "degraded"
    assert degraded["severity"] == "warning"
    assert degraded["userMessage"] != "provider 健康检查通过。"

    assert len(gateway.auth_calls) == 2
    assert gateway.auth_calls[0]["provider"] == "deepseek"
    assert gateway.auth_calls[0]["model"] == "deepseek-chat"
    assert gateway.auth_calls[0]["probe"] is True


def test_provider_health_summary_sanitizes_internal_provider_detail(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    gateway._config = {
        "active_provider": "deepseek",
        "agents": {"defaults": {"model": "deepseek-chat"}},
        "models": {"providers": {"deepseek": {"api_key": "sk-real", "endpoint_url": "https://api.example.com"}}},
    }
    bridge = _bridge(tmp_path, gateway)
    gateway.set_auth_result(
        ok=False,
        message="gateway failed: provider attempt #2 raw payload leaked",
    )
    summary = bridge.get_provider_health_summary()
    text = str(summary["userMessage"]).lower()
    assert "provider attempt" not in text
    assert "raw payload" not in text
    assert summary["state"] == "degraded"


def test_provider_health_summary_does_not_create_new_report_hard_block(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "https://api.example.com",
            "apiKeyReplacement": "sk-user-1",
        },
        expected_settings_version="v_1",
        request_id="s08-save-1",
    )
    gateway.set_auth_result(ok=True, message="ready")
    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "https://api.example.com"},
        request_id="s08-test-1",
    )
    assert tested["ok"] is True
    readiness_before = bridge.get_report_model_readiness()
    assert readiness_before.ready is True

    gateway.set_auth_result(ok=False, message="provider attempt #3 timeout")
    summary = bridge.get_provider_health_summary()
    readiness_after = bridge.get_report_model_readiness()

    assert summary["state"] == "degraded"
    assert readiness_after.ready is True
    bridge.assert_report_model_ready()
