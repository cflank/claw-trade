from __future__ import annotations

from pathlib import Path
from typing import Any

from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge


class _FakeGateway:
    def __init__(self) -> None:
        self._revision = 0
        self._auth_payload: dict[str, Any] = {"ok": True, "message": "ready"}
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
        _ = provider, model, endpoint_url, probe
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


def _bridge(
    tmp_path: Path,
    gateway: _FakeGateway,
    *,
    runtime_health_probe,
) -> LlmSettingsBridge:
    return LlmSettingsBridge(
        gateway,
        embedding_env_path=tmp_path / ".env.local",
        report_model_status_path=tmp_path / "report-model-status.json",
        runtime_health_probe=runtime_health_probe,
    )


def test_runtime_service_status_summary_reads_real_health_probe(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    bridge_healthy = _bridge(
        tmp_path,
        gateway,
        runtime_health_probe=lambda: {"state": "healthy", "failedServices": [], "errorKinds": []},
    )
    healthy = bridge_healthy.get_runtime_service_status_summary()
    assert healthy["state"] == "healthy"
    assert healthy["severity"] == "success"
    assert healthy["source"] == "runtime.health.http"

    bridge_degraded = _bridge(
        tmp_path,
        gateway,
        runtime_health_probe=lambda: {
            "state": "degraded",
            "failedServices": ["session_service"],
            "errorKinds": ["connection_refused"],
        },
    )
    degraded = bridge_degraded.get_runtime_service_status_summary()
    assert degraded["state"] == "degraded"
    assert degraded["severity"] == "warning"
    assert degraded["userMessage"] != "运行服务健康检查通过，当前可继续执行报告相关操作。"


def test_runtime_service_status_summary_hides_internal_runtime_keywords(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    bridge = _bridge(
        tmp_path,
        gateway,
        runtime_health_probe=lambda: {
            "state": "degraded",
            "failedServices": ["session_service", "data_service"],
            "errorKinds": ["timeout", "network_error"],
        },
    )
    summary = bridge.get_runtime_service_status_summary()
    text = str(summary["userMessage"]).lower()
    assert "18789" not in text
    assert "1933" not in text
    assert "gateway" not in text
    assert "scope" not in text
    assert "openviking" not in text
    assert "/runtime/dev-services" not in text


def test_runtime_service_status_summary_does_not_create_new_report_hard_block(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    bridge = _bridge(
        tmp_path,
        gateway,
        runtime_health_probe=lambda: {
            "state": "degraded",
            "failedServices": ["data_service"],
            "errorKinds": ["connection_refused"],
        },
    )
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "https://api.example.com",
            "apiKeyReplacement": "sk-user-1",
        },
        expected_settings_version="v_1",
        request_id="s09-save-1",
    )
    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "https://api.example.com"},
        request_id="s09-test-1",
    )
    assert tested["ok"] is True
    readiness_before = bridge.get_report_model_readiness()
    assert readiness_before.ready is True

    summary = bridge.get_runtime_service_status_summary()
    readiness_after = bridge.get_report_model_readiness()

    assert summary["state"] == "degraded"
    assert readiness_after.ready is True
    bridge.assert_report_model_ready()
