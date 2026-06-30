from __future__ import annotations

import copy
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
from claw_trade.web.settings import ResearchUiServerSettings
from claw_trade.web.state import build_ui_http_services


class _FakeGateway:
    def __init__(self) -> None:
        self._revision = 0
        self._probe_status = "auth"
        self._probe_calls: list[dict[str, object | None]] = []
        self._config: dict[str, object] = {
            "active_provider": "",
            "agents": {"defaults": {"model": ""}},
            "models": {"providers": {}},
        }

    def set_probe_result(self, *, status: str) -> None:
        self._probe_status = status

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

    def models_probe_status(self, *, provider, model=None, endpoint_url=None):  # type: ignore[no-untyped-def]
        self._probe_calls.append({"provider": provider, "model": model, "endpoint_url": endpoint_url})
        return {
            "auth": {
                "probes": {
                    "results": [
                        {
                            "provider": provider,
                            "model": model or "",
                            "status": self._probe_status,
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


def test_production_ui_services_wire_report_model_gate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("CLAW_TRADE_REPORT_RUN_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("DATA_GATEWAY_MONGODB_URI", raising=False)
    monkeypatch.delenv("CN_A_MONGODB_URI", raising=False)

    services = build_ui_http_services(ResearchUiServerSettings(frontend_dist=tmp_path / "dist"))

    chat_checker = services.chat_controller._report_model_ready_checker  # type: ignore[attr-defined]
    confirmation = services.chat_controller._confirmation  # type: ignore[attr-defined]
    confirmation_checker = confirmation._report_model_ready_checker  # type: ignore[attr-defined]
    assert getattr(chat_checker, "__self__", None) is services.llm_bridge
    assert getattr(confirmation_checker, "__self__", None) is services.llm_bridge


def test_unconfigured_model_blocks_report_generation(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path, _FakeGateway())
    readiness = bridge.get_report_model_readiness()
    assert readiness.state == "unconfigured"
    assert readiness.blocked is True
    with pytest.raises(UiBoundaryError) as exc:
        bridge.assert_report_model_ready()
    assert exc.value.code == "REPORT_MODEL_NOT_READY"


def test_camel_case_openclaw_config_is_recognized_as_configured(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    gateway._config = {
        "agents": {"defaults": {"model": {"primary": "deepseek/deepseek-chat"}}},
        "models": {
            "providers": {
                "deepseek": {
                    "baseUrl": "https://api.deepseek.com",
                    "apiKey": "sk-camel-case",
                    "models": [{"id": "deepseek-chat"}],
                }
            }
        },
    }
    bridge = _bridge(tmp_path, gateway)
    readiness = bridge.get_report_model_readiness()
    assert readiness.state == "saved_unverified"
    loaded = bridge.load_llm_settings()["draft"]
    assert loaded["provider"] == "deepseek"
    assert loaded["endpointUrl"] == "https://api.deepseek.com"
    assert loaded["defaultModel"] == "deepseek/deepseek-chat"
    assert loaded["apiKeyMasked"] == "***case"
    assert loaded["reportModelStatus"]["state"] == "saved_unverified"


def test_saved_model_is_not_ready_until_real_test_passes(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "http://127.0.0.1:1",
            "apiKeyReplacement": "sk-user-1",
        },
        expected_settings_version="v_1",
        request_id="req-save-1",
    )
    readiness = bridge.get_report_model_readiness()
    assert readiness.state == "saved_unverified"
    with pytest.raises(UiBoundaryError):
        bridge.assert_report_model_ready()


def test_model_test_uses_current_form_key_before_prior_save(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    bridge = _bridge(tmp_path, gateway)
    gateway.set_probe_result(status="ok")

    tested = bridge.test_llm_via_openclaw(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "endpointUrl": "https://api.deepseek.com",
            "apiKeyReplacement": "sk-from-form",
        },
        request_id="req-test-current-form",
    )

    assert tested["ok"] is True
    assert gateway._config["models"]["providers"]["deepseek"]["apiKey"] == "sk-from-form"  # type: ignore[index]
    assert gateway._probe_calls[-1]["provider"] == "deepseek"
    assert gateway._probe_calls[-1]["model"] == "deepseek/deepseek-chat"
    assert bridge.get_report_model_readiness().state == "ready"


def test_model_test_survives_gateway_restart_after_config_patch(tmp_path: Path) -> None:
    class _RestartingGateway(_FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self._patched = False

        def config_patch(self, *, expected_settings_version, patch):  # type: ignore[no-untyped-def]
            self._patched = True
            return super().config_patch(expected_settings_version=expected_settings_version, patch=patch)

        def config_get(self, *, paths):  # type: ignore[no-untyped-def]
            if self._patched:
                raise RuntimeError("gateway restarting")
            return super().config_get(paths=paths)

    gateway = _RestartingGateway()
    gateway.set_probe_result(status="ok")
    bridge = _bridge(tmp_path, gateway)

    tested = bridge.test_llm_via_openclaw(
        {
            "provider": "deepseek",
            "model": "deepseek-chat",
            "endpointUrl": "https://api.deepseek.com",
            "apiKeyReplacement": "sk-from-form",
        },
        request_id="req-test-gateway-restart",
    )

    assert tested["ok"] is True
    assert gateway._probe_calls[-1]["provider"] == "deepseek"


def test_failed_model_test_returns_actionable_error_and_keeps_user_model(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "http://127.0.0.1:1",
            "apiKeyReplacement": "sk-user-2",
        },
        expected_settings_version="v_1",
        request_id="req-save-2",
    )
    gateway.set_probe_result(status="auth")
    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "http://127.0.0.1:1"},
        request_id="req-test-failed",
    )
    assert tested["ok"] is False
    assert tested["error"]["code"] == "REPORT_MODEL_TEST_FAILED"
    assert tested["userMessage"] == "报告模型认证失败，API Key 无效或无法认证，请到设置更新后重新测试。"
    assert gateway._probe_calls[-1]["provider"] == "deepseek"
    readiness = bridge.get_report_model_readiness()
    assert readiness.state == "failed"
    loaded = bridge.load_llm_settings()["draft"]
    assert loaded["defaultModel"] == "deepseek/deepseek-chat"
    assert loaded["reportModelStatus"]["state"] == "failed"


@pytest.mark.parametrize(
    ("status", "message"),
    [
        ("expired", "报告模型认证失败，API Key 已过期，请到设置更新后重新测试。"),
        ("billing", "报告模型额度不足或账户计费异常，请到服务商后台处理后重新测试。"),
        ("rate_limit", "报告模型请求被服务商限流，请稍后重试或降低并发。"),
    ],
)
def test_model_probe_failure_messages_are_plain(tmp_path: Path, status: str, message: str) -> None:
    gateway = _FakeGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "http://127.0.0.1:1",
            "apiKeyReplacement": "sk-user-plain",
        },
        expected_settings_version="v_1",
        request_id=f"req-save-{status}",
    )
    gateway.set_probe_result(status=status)

    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "http://127.0.0.1:1"},
        request_id=f"req-test-{status}",
    )

    assert tested["ok"] is False
    assert tested["userMessage"] == message
    assert bridge.get_report_model_status()["userMessage"] == message


def test_ready_only_after_real_test_success(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "http://127.0.0.1:1",
            "apiKeyReplacement": "sk-user-3",
        },
        expected_settings_version="v_1",
        request_id="req-save-3",
    )
    gateway.set_probe_result(status="ok")
    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "http://127.0.0.1:1"},
        request_id="req-test-ok",
    )
    assert tested["ok"] is True
    readiness = bridge.get_report_model_readiness()
    assert readiness.state == "ready"
    bridge.assert_report_model_ready()


def test_ready_survives_masked_key_from_openclaw_config_get(tmp_path: Path) -> None:
    class _MaskingGateway(_FakeGateway):
        def config_get(self, *, paths):  # type: ignore[no-untyped-def]
            config = super().config_get(paths=paths)
            config["parsed"] = copy.deepcopy(config["parsed"])
            providers = config["parsed"]["models"]["providers"]  # type: ignore[index]
            for provider_config in providers.values():  # type: ignore[union-attr]
                if isinstance(provider_config, dict) and provider_config.get("apiKey"):
                    provider_config["apiKey"] = "***1234"
            return config

    gateway = _MaskingGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "https://api.deepseek.com",
            "apiKeyReplacement": "sk-user-1234",
        },
        expected_settings_version="v_1",
        request_id="req-save-masked",
    )
    gateway.set_probe_result(status="ok")
    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "https://api.deepseek.com"},
        request_id="req-test-masked",
    )

    assert tested["ok"] is True
    loaded = bridge.load_llm_settings()["draft"]
    assert loaded["reportModelStatus"]["state"] == "ready"
    assert loaded["reportModelStatus"]["ready"] is True
    bridge.assert_report_model_ready()


def test_ready_survives_openclaw_redacted_key_from_config_get(tmp_path: Path) -> None:
    class _RedactingGateway(_FakeGateway):
        def config_get(self, *, paths):  # type: ignore[no-untyped-def]
            config = super().config_get(paths=paths)
            config["parsed"] = copy.deepcopy(config["parsed"])
            providers = config["parsed"]["models"]["providers"]  # type: ignore[index]
            for provider_config in providers.values():  # type: ignore[union-attr]
                if isinstance(provider_config, dict) and provider_config.get("apiKey"):
                    provider_config["apiKey"] = "__OPENCLAW_REDACTED__"
            return config

    gateway = _RedactingGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "https://api.deepseek.com",
            "apiKeyReplacement": "sk-user-redacted",
        },
        expected_settings_version="v_1",
        request_id="req-save-redacted",
    )
    gateway.set_probe_result(status="ok")
    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "https://api.deepseek.com"},
        request_id="req-test-redacted",
    )

    assert tested["ok"] is True
    loaded = bridge.load_llm_settings()["draft"]
    assert loaded["apiKeyMasked"] == "***"
    assert loaded["reportModelStatus"]["state"] == "ready"
    assert loaded["reportModelStatus"]["ready"] is True
    bridge.assert_report_model_ready()


def test_model_test_does_not_patch_redacted_key_when_key_input_is_empty(tmp_path: Path) -> None:
    class _RedactingGateway(_FakeGateway):
        def __init__(self) -> None:
            super().__init__()
            self.patch_payloads: list[dict[str, object]] = []

        def config_get(self, *, paths):  # type: ignore[no-untyped-def]
            config = super().config_get(paths=paths)
            config["parsed"] = copy.deepcopy(config["parsed"])
            providers = config["parsed"]["models"]["providers"]  # type: ignore[index]
            for provider_config in providers.values():  # type: ignore[union-attr]
                if isinstance(provider_config, dict) and provider_config.get("apiKey"):
                    provider_config["apiKey"] = "__OPENCLAW_REDACTED__"
            return config

        def config_patch(self, *, expected_settings_version, patch):  # type: ignore[no-untyped-def]
            self.patch_payloads.append(copy.deepcopy(patch))
            return super().config_patch(expected_settings_version=expected_settings_version, patch=patch)

    gateway = _RedactingGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "https://api.deepseek.com",
            "apiKeyReplacement": "sk-real-secret",
        },
        expected_settings_version="v_1",
        request_id="req-save-no-redacted-patch",
    )
    gateway.set_probe_result(status="ok")
    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "https://api.deepseek.com"},
        request_id="req-test-no-redacted-patch",
    )

    assert tested["ok"] is True
    test_patch = gateway.patch_payloads[-1]
    provider_patch = test_patch["models"]["providers"]["deepseek"]  # type: ignore[index]
    assert "apiKey" not in provider_patch
    assert gateway._config["models"]["providers"]["deepseek"]["apiKey"] == "sk-real-secret"  # type: ignore[index]


def test_probe_missing_result_stays_failed(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "http://127.0.0.1:1",
            "apiKeyReplacement": "sk-user-5",
        },
        expected_settings_version="v_1",
        request_id="req-save-5",
    )

    def _missing_probe_status(*, provider, model=None, endpoint_url=None):  # type: ignore[no-untyped-def]
        _ = (provider, model, endpoint_url)
        return {"auth": {"probes": {"results": []}}}

    gateway.models_probe_status = _missing_probe_status  # type: ignore[method-assign]
    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "http://127.0.0.1:1"},
        request_id="req-test-missing-probe",
    )

    assert tested["ok"] is False
    assert tested["error"]["code"] == "REPORT_MODEL_TEST_FAILED"
    assert tested["userMessage"] == "报告模型连接测试失败，请检查模型服务商配置后重试。"
    assert bridge.get_report_model_readiness().state == "failed"


def test_auth_snapshot_payload_is_not_treated_as_probe_success(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "http://127.0.0.1:1",
            "apiKeyReplacement": "sk-user-snapshot",
        },
        expected_settings_version="v_1",
        request_id="req-save-snapshot",
    )

    def _auth_snapshot(*, provider, model=None, endpoint_url=None):  # type: ignore[no-untyped-def]
        _ = (provider, model, endpoint_url)
        return {"ts": "2026-05-23T00:00:00Z", "providers": {"deepseek": {"status": "configured"}}}

    gateway.models_probe_status = _auth_snapshot  # type: ignore[method-assign]
    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "http://127.0.0.1:1"},
        request_id="req-test-auth-snapshot",
    )

    assert tested["ok"] is False
    assert tested["error"]["code"] == "REPORT_MODEL_TEST_FAILED"
    assert bridge.get_report_model_readiness().state == "failed"


def test_probe_model_mismatch_stays_failed(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "http://127.0.0.1:1",
            "apiKeyReplacement": "sk-user-6",
        },
        expected_settings_version="v_1",
        request_id="req-save-6",
    )

    def _mismatched_probe_status(*, provider, model=None, endpoint_url=None):  # type: ignore[no-untyped-def]
        _ = (provider, model, endpoint_url)
        return {
            "auth": {
                "probes": {
                    "results": [
                        {
                            "provider": "deepseek",
                            "model": "deepseek-reasoner",
                            "status": "ok",
                        }
                    ]
                }
            }
        }

    gateway.models_probe_status = _mismatched_probe_status  # type: ignore[method-assign]
    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "http://127.0.0.1:1"},
        request_id="req-test-model-mismatch",
    )

    assert tested["ok"] is False
    assert tested["error"]["code"] == "REPORT_MODEL_TEST_FAILED"
    assert tested["userMessage"] == "报告模型连接测试失败，请检查模型服务商配置后重试。"
    assert bridge.get_report_model_readiness().state == "failed"


def test_report_command_is_hard_blocked_when_model_not_ready(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    bridge = _bridge(tmp_path, gateway)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "http://127.0.0.1:1",
            "apiKeyReplacement": "sk-user-4",
        },
        expected_settings_version="v_1",
        request_id="req-save-4",
    )
    queue = ReportTaskQueue(ReportWorkflowBridge(_FakeWorkflowRunner()))
    controller = ChatController(
        openclaw_client=OpenClawGatewayClient(_FakeChatTransport()),
        recognizer=IntentRecognizer(),
        confirmation=ConfirmationController(queue, report_model_ready_checker=bridge.assert_report_model_ready),
        queue=queue,
        settings=ReportWorkflowSettings(),
        report_model_ready_checker=bridge.assert_report_model_ready,
    )
    result = controller.send_chat_message(request_id="req-chat-block", context_id="ctx-1", text="/report BTC")
    assert result["error"]["code"] == "REPORT_MODEL_NOT_READY"
    assert "默认模型已自动启用" not in result["error"]["message"]
