from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from claw_trade.config.report_workflow_settings import ReportWorkflowSettings
from claw_trade.ui_backend.chat_controller import ChatController
from claw_trade.ui_backend.confirmation_controller import ConfirmationController
from claw_trade.ui_backend.intent_recognizer import IntentRecognizer
from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge
from claw_trade.ui_backend.openclaw_client import OpenClawGatewayClient
from claw_trade.ui_backend.report_queue import ReportTaskQueue
from claw_trade.ui_backend.settings_service import UiBoundaryError
from claw_trade.ui_backend.workflow_bridge import ReportWorkflowBridge


class _FakeGateway:
    def __init__(self) -> None:
        self._revision = 0
        self._auth_ok = False
        self._auth_message = "auth failed"
        self._config: dict[str, object] = {
            "active_provider": "",
            "agents": {"defaults": {"model": ""}},
            "models": {"providers": {}},
        }

    def set_auth_result(self, *, ok: bool, message: str) -> None:
        self._auth_ok = ok
        self._auth_message = message

    def config_schema_lookup(self, *, path: str) -> dict[str, str]:
        return {"path": path}

    def config_get(self, *, paths):  # type: ignore[no-untyped-def]
        _ = paths
        self._revision += 1
        return {
            "revision": f"rev-{self._revision}",
            "parsed": self._config,
        }

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

    def models_auth_status(self, *, provider, model=None, endpoint_url=None, probe=True):  # type: ignore[no-untyped-def]
        _ = (provider, model, endpoint_url, probe)
        return {"ok": self._auth_ok, "message": self._auth_message}

    def models_probe_status(self, *, provider, model=None, endpoint_url=None):  # type: ignore[no-untyped-def]
        _ = endpoint_url
        status = "ok" if self._auth_ok else "auth"
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


@dataclass
class _FakeWorkflowState:
    status: str = "frontline_running"


class _FakeWorkflowRunner:
    def __init__(self) -> None:
        self.calls = 0

    def create_run(self, request):  # type: ignore[no-untyped-def]
        self.calls += 1
        return f"run-{self.calls}"

    def load_state(self, run_id: str) -> _FakeWorkflowState:
        _ = run_id
        return _FakeWorkflowState()


class _FakeChatTransport:
    def chat_send(self, *, context_id: str, text: str, request_id: str) -> dict[str, str]:
        _ = (context_id, text, request_id)
        return {"text": "ok"}


def _bridge(tmp_path: Path, gateway: _FakeGateway) -> LlmSettingsBridge:
    return LlmSettingsBridge(
        gateway,
        embedding_env_path=tmp_path / ".env.local",
        report_model_status_path=tmp_path / "report-model-status.json",
    )


def _ready_bridge(tmp_path: Path) -> LlmSettingsBridge:
    gateway = _FakeGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "http://127.0.0.1:1",
            "apiKeyReplacement": "sk-report-ready",
        },
        expected_settings_version="v_1",
        request_id="s04-save-model",
    )
    gateway.set_auth_result(ok=True, message="ready")
    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "http://127.0.0.1:1"},
        request_id="s04-test-model",
    )
    assert tested["ok"] is True
    return bridge


def test_unconfigured_embedding_is_optional_for_report_gate(tmp_path: Path) -> None:
    bridge = _ready_bridge(tmp_path)
    loaded = bridge.load_llm_settings()
    assert loaded["draft"]["embedding"]["enabled"] is False
    bridge.assert_report_model_ready()

    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeWorkflowRunner()))
    controller = ChatController(
        openclaw_client=OpenClawGatewayClient(_FakeChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue, report_model_ready_checker=bridge.assert_report_model_ready),
        queue=queue,
        settings=ReportWorkflowSettings(),
        report_model_ready_checker=bridge.assert_report_model_ready,
    )
    result = controller.send_chat_message(request_id="s04-chat-no-embedding", context_id="ctx-1", text="/report BTC")
    assert "error" not in result
    assert "confirmationCard" in result


def test_unavailable_embedding_does_not_block_ready_report_model(tmp_path: Path) -> None:
    bridge = _ready_bridge(tmp_path)
    env_path = tmp_path / ".env.local"
    env_path.write_text("OPENVIKING_EMBEDDING_PROVIDER=openai\n", encoding="utf-8")

    loaded = bridge.load_llm_settings()
    embedding = loaded["draft"]["embedding"]
    assert embedding["provider"] == "openai"
    assert embedding["enabled"] is False
    bridge.assert_report_model_ready()


def test_embedding_validation_failure_is_non_blocking_to_report_gate(tmp_path: Path) -> None:
    bridge = _ready_bridge(tmp_path)
    with pytest.raises(UiBoundaryError) as exc:
        bridge.save_embedding_config_via_openviking(
            {
                "provider": "openai",
                "model": "",
                "endpointUrl": "",
                "dimension": "",
                "apiKeyReplacement": "",
                "enabled": False,
            },
            request_id="s04-save-invalid-embedding",
        )
    assert exc.value.code == "INVALID_INPUT"
    assert "Embedding 服务商和模型必须同时填写" in exc.value.user_message
    bridge.assert_report_model_ready()
