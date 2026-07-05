from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path
from typing import Any, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import urlopen

from claw_trade.ui_backend.settings_service import EnvLocalAllowlistWriter, UiBoundaryError

_EMBEDDING_ENV_KEYS = (
    "OPENVIKING_EMBEDDING_PROVIDER",
    "OPENVIKING_EMBEDDING_MODEL",
    "OPENVIKING_EMBEDDING_API_KEY",
    "OPENVIKING_EMBEDDING_API_BASE",
    "OPENVIKING_EMBEDDING_DIMENSION",
)

_PROVIDER_PRESETS: dict[str, dict[str, Any]] = {
    "deepseek": {
        "endpoint_url": "https://api.deepseek.com",
        "api": "openai-completions",
        "default_model": "deepseek-chat",
    },
    "qwen": {
        "endpoint_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "api": "openai-completions",
        "default_model": "qwen-plus",
    },
    "glm": {
        "endpoint_url": "https://open.bigmodel.cn/api/paas/v4",
        "api": "openai-completions",
        "default_model": "glm-4.5",
    },
    "kimi": {
        "endpoint_url": "https://api.moonshot.cn/v1",
        "api": "openai-completions",
        "default_model": "kimi-k2.5",
    },
    "minimax": {
        "endpoint_url": "https://api.minimax.io/v1",
        "api": "openai-completions",
        "default_model": "MiniMax-M2",
    },
    "doubao": {
        "endpoint_url": "https://ark.cn-beijing.volces.com/api/v3",
        "api": "openai-completions",
        "default_model": "doubao-seed-2-0-pro-260215",
    },
    "ernie": {
        "endpoint_url": "https://qianfan.baidubce.com/v2",
        "api": "openai-completions",
        "default_model": "ernie-4.5-turbo",
    },
    "hunyuan": {
        "endpoint_url": "https://api.hunyuan.cloud.tencent.com/v1",
        "api": "openai-completions",
        "default_model": "hunyuan-turbos-latest",
    },
    "openai_compatible": {
        "endpoint_url": "https://api.example.com/v1",
        "api": "openai-completions",
        "default_model": "custom-model",
    },
}
_DISABLED_REPORT_MODEL_PROVIDERS = {"openai", "anthropic", "google", "mistral", "openrouter", "xai"}


def _normalize_report_provider_id(provider: str) -> str:
    return provider.strip().lower()


def _is_allowed_report_provider(provider: str) -> bool:
    normalized = _normalize_report_provider_id(provider)
    return normalized in _PROVIDER_PRESETS and normalized not in _DISABLED_REPORT_MODEL_PROVIDERS


def _require_allowed_report_provider(provider: str) -> str:
    normalized = _normalize_report_provider_id(provider)
    if not normalized:
        raise UiBoundaryError("INVALID_INPUT", "请选择模型服务商。")
    if not _is_allowed_report_provider(normalized):
        raise UiBoundaryError("INVALID_INPUT", "该模型服务商未开放，请使用 DeepSeek、通义千问或国内兼容接口。")
    return normalized


@dataclass(frozen=True)
class ReportModelReadiness:
    state: str
    blocked: bool
    ready: bool
    user_message: str
    checked_at: str | None = None

    def to_user_payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "blocked": self.blocked,
            "ready": self.ready,
            "userMessage": self.user_message,
            "checkedAt": self.checked_at,
        }


@dataclass(frozen=True)
class ProviderHealthSummary:
    state: str
    severity: str
    user_message: str
    checked_at: str
    provider: str | None
    model: str | None
    source: str

    def to_user_payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "severity": self.severity,
            "userMessage": self.user_message,
            "checkedAt": self.checked_at,
            "provider": self.provider,
            "model": self.model,
            "source": self.source,
        }


@dataclass(frozen=True)
class RuntimeServiceStatusSummary:
    state: str
    severity: str
    user_message: str
    checked_at: str
    source: str

    def to_user_payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "severity": self.severity,
            "userMessage": self.user_message,
            "checkedAt": self.checked_at,
            "source": self.source,
        }


@dataclass(frozen=True)
class LiveRunGapSummary:
    state: str
    severity: str
    user_message: str
    checked_at: str
    source: str
    latest_run: Mapping[str, Any] | None
    recommended_action: str

    def to_user_payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "severity": self.severity,
            "userMessage": self.user_message,
            "checkedAt": self.checked_at,
            "source": self.source,
            "latestRun": dict(self.latest_run) if isinstance(self.latest_run, Mapping) else None,
            "recommendedAction": self.recommended_action,
        }


@dataclass(frozen=True)
class EvidenceFailureReasonSummary:
    state: str
    severity: str
    user_message: str
    checked_at: str
    source: str
    latest_run: Mapping[str, Any] | None
    recommended_action: str

    def to_user_payload(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "severity": self.severity,
            "userMessage": self.user_message,
            "checkedAt": self.checked_at,
            "source": self.source,
            "latestRun": dict(self.latest_run) if isinstance(self.latest_run, Mapping) else None,
            "recommendedAction": self.recommended_action,
        }


class _ReportModelStatusStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> dict[str, Any]:
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        if not isinstance(payload, dict):
            return {}
        return payload

    def write(self, payload: Mapping[str, Any]) -> None:
        body = dict(payload)
        body["schemaVersion"] = "report-model-status-v1"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps(body, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


class _EnvEmbeddingConfigStore:
    def __init__(self, *, env_path: Path, env_writer: EnvLocalAllowlistWriter) -> None:
        self._env_path = env_path
        self._env_writer = env_writer

    def read(self) -> dict[str, str]:
        return _read_env_file_values(self._env_path)

    def write(self, updates: Mapping[str, str]) -> None:
        self._env_writer.write_allowed_env_keys(updates)

    def clear(self) -> None:
        self._env_writer.clear_allowed_env_keys(_EMBEDDING_ENV_KEYS)


class LlmSettingsBridge:
    def __init__(
        self,
        openclaw_gateway_client: Any,
        *,
        embedding_env_path: Path = Path(".env.local"),
        embedding_env_writer: EnvLocalAllowlistWriter | None = None,
        embedding_config_store: Any | None = None,
        report_model_status_path: Path = Path(".runtime/ui/report-model-status.json"),
        report_model_status_store: Any | None = None,
        report_model_config_store: Any | None = None,
        runtime_health_probe: Callable[[], Mapping[str, Any]] | None = None,
        embedding_probe: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        run_root: Path = Path("runs"),
    ) -> None:
        self._client = openclaw_gateway_client
        self._embedding_env_path = embedding_env_path
        self._embedding_env_writer = embedding_env_writer or EnvLocalAllowlistWriter(
            embedding_env_path,
            allowed_keys=_EMBEDDING_ENV_KEYS,
        )
        self._embedding_config_store = embedding_config_store or _EnvEmbeddingConfigStore(
            env_path=embedding_env_path,
            env_writer=self._embedding_env_writer,
        )
        self._report_model_status_store = report_model_status_store or _ReportModelStatusStore(report_model_status_path)
        self._report_model_config_store = report_model_config_store
        self._runtime_health_probe = runtime_health_probe or _probe_runtime_services
        self._embedding_probe = embedding_probe or _probe_openviking_embedding_runtime
        self._run_root = run_root
        self._idempotency: dict[str, Any] = {}

    def load_llm_settings(self, provider: str | None = None) -> dict[str, Any]:
        try:
            config = self._client.config_get(paths=("agents.defaults.model", "models.providers"))
        except Exception:
            config = _empty_llm_config()
        readiness = self._evaluate_report_model_readiness(config=config, provider_hint=provider)
        status: Mapping[str, Any] = {"message": readiness.user_message}
        draft = self._map_openclaw_config_to_draft(config=config, provider=provider, status=status)
        draft["reportModelStatus"] = readiness.to_user_payload()
        draft["status"] = _ui_draft_status_from_readiness(readiness.state)
        if readiness.state in {"saved_unverified", "failed"}:
            draft["lastTestMessage"] = readiness.user_message
        draft["embedding"] = self._load_embedding_draft()
        return {
            "draft": draft,
            "schemaVersion": _opaque_version("llm-settings-v2"),
            "settingsVersion": _opaque_settings_version(config.get("revision")),
        }

    def get_report_model_status(self) -> dict[str, Any]:
        try:
            config = self._client.config_get(paths=("agents.defaults.model", "models.providers"))
            return self._evaluate_report_model_readiness(config=config).to_user_payload()
        except Exception:
            pass
        status = self._report_model_status_store.read()
        runtime_config = self._report_model_config_store.read() if self._report_model_config_store is not None else {}
        runtime_fields = _report_model_fields_from_runtime_config(runtime_config)
        if not runtime_fields["configured"]:
            if status.get("state") in {"ready", "failed", "saved_unverified"} and _status_provider_allowed(status):
                return _report_model_status_payload(status)
            return ReportModelReadiness(
                state="unconfigured",
                blocked=True,
                ready=False,
                user_message="请先在设置中填写报告模型（服务商、模型、API Key），并完成测试。",
                checked_at=_optional_str(status.get("checkedAt")),
            ).to_user_payload()
        if str(status.get("fingerprint") or "") == str(runtime_fields["fingerprint"] or ""):
            return _report_model_status_payload(status)
        return ReportModelReadiness(
            state="saved_unverified",
            blocked=True,
            ready=False,
            user_message="报告模型已保存但尚未测试通过，请先执行模型测试。",
            checked_at=_optional_str(status.get("checkedAt") or status.get("savedAt")),
        ).to_user_payload()

    def save_llm_config_via_openclaw(
        self,
        draft: Mapping[str, Any],
        expected_settings_version: str,
        request_id: str,
    ) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        provider = _normalize_report_provider_id(str(draft.get("provider", "")))
        default_model = str(draft.get("defaultModel", "")).strip()
        provider = _require_allowed_report_provider(provider)
        if not default_model:
            raise UiBoundaryError("INVALID_INPUT", "请选择默认模型。")
        model_ref = _normalize_model_ref(provider, default_model)
        try:
            existing_config = self._client.config_get(paths=("agents.defaults.model", "models.providers"))
            patch = self._build_config_patch(draft)
            openclaw_base_hash = _optional_str(existing_config.get("revision")) or expected_settings_version
            current_fields = _resolve_report_model_fields(existing_config, provider_hint=provider)
            result = {"newHash": openclaw_base_hash}
            if _report_model_config_patch_needed(
                current_fields,
                provider=provider,
                model_ref=model_ref,
                endpoint_url=draft.get("endpointUrl"),
                api_key_replacement=draft.get("apiKeyReplacement"),
            ):
                result = self._client.config_patch(
                    expected_settings_version=openclaw_base_hash,
                    patch=patch,
                )
        except RuntimeError as exc:
            raise UiBoundaryError("ASSISTANT_UNAVAILABLE", "模型配置暂不可保存，请稍后重试。") from exc
        report_fields = _resolve_report_model_fields(
            existing_config,
            provider_hint=provider,
            model_hint=model_ref,
            endpoint_hint=draft.get("endpointUrl"),
            api_key_hint=draft.get("apiKeyReplacement"),
        )
        saved_at = _now_iso()
        current_status = self._report_model_status_store.read()
        status_is_current = str(current_status.get("fingerprint") or "") == str(report_fields["fingerprint"] or "")
        status_is_masked_current = _status_matches_current_public_model_fields(
            current_status,
            report_fields,
        ) and _looks_masked_secret(str(report_fields.get("api_key") or ""))
        if current_status.get("state") == "ready" and (status_is_current or status_is_masked_current):
            self._report_model_status_store.write({**current_status, "savedAt": saved_at})
        else:
            self._report_model_status_store.write(
                {
                    "state": "saved_unverified",
                    "provider": report_fields["provider"],
                    "model": report_fields["model"],
                    "endpointUrl": report_fields["endpoint_url"],
                    "fingerprint": report_fields["fingerprint"],
                    "savedAt": saved_at,
                    "lastErrorMessage": None,
                }
            )
        self._write_report_model_runtime_config(
            draft=draft,
            existing_config=existing_config,
            report_fields=report_fields,
        )
        out = {
            "status": "saved",
            "updatedAt": saved_at,
            "settingsVersion": _opaque_settings_version(result.get("newHash") or result.get("new_hash")),
            "reportModelStatus": _report_model_status_payload(self._report_model_status_store.read()),
        }
        self._idempotency[request_id] = out
        return out

    def save_embedding_config_via_openviking(self, input_data: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        self._validate_embedding_draft(input_data)
        self._save_embedding_config(input_data)
        out = {
            "status": "saved",
            "updatedAt": _now_iso(),
        }
        self._idempotency[request_id] = out
        return out

    def reset_llm_settings_to_defaults(self, request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        try:
            config = self._client.config_get(paths=("agents.defaults.model", "agents.defaults.models", "models.providers"))
        except Exception:
            config = _empty_llm_config()
        patch = _build_report_model_reset_patch(config)
        patch_result: Mapping[str, Any] = {}
        if patch:
            patch_result = self._client.config_patch(expected_settings_version=None, patch=patch)
        if self._report_model_config_store is not None:
            self._report_model_config_store.clear()
        self._embedding_config_store.clear()
        reset_at = _now_iso()
        self._report_model_status_store.write(
            {
                "state": "unconfigured",
                "checkedAt": reset_at,
                "lastErrorMessage": None,
            }
        )
        out = {
            "status": "reset",
            "updatedAt": reset_at,
            "settingsVersion": _opaque_settings_version(patch_result.get("newHash") or patch_result.get("new_hash")),
        }
        self._idempotency[request_id] = out
        return out

    def test_llm_via_openclaw(self, input_data: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        provider = _require_allowed_report_provider(str(input_data.get("provider", "")))
        config = _retry_transient_runtime_call(
            lambda: self._client.config_get(paths=("agents.defaults.model", "models.providers")),
            timeout_seconds=20.0,
        )
        model_hint_raw = _optional_str(input_data.get("model"))
        model_hint = _normalize_model_ref(provider, model_hint_raw) if model_hint_raw else None
        api_key_replacement = str(input_data.get("apiKeyReplacement", "") or "").strip()
        endpoint_hint = input_data.get("endpointUrl")
        if model_hint_raw or endpoint_hint is not None or api_key_replacement:
            current_fields = _resolve_report_model_fields(config, provider_hint=provider)
            draft_model = model_hint_raw or _optional_str(current_fields.get("model")) or str(
                _provider_preset(provider)["default_model"]
            )
            draft_model_ref = _normalize_model_ref(provider, draft_model)
            if _report_model_config_patch_needed(
                current_fields,
                provider=provider,
                model_ref=draft_model_ref,
                endpoint_url=endpoint_hint,
                api_key_replacement=api_key_replacement,
            ):
                self._client.config_patch(
                    expected_settings_version=None,
                    patch=self._build_config_patch(
                        {
                            "provider": provider,
                            "defaultModel": draft_model,
                            "endpointUrl": endpoint_hint,
                            "apiKeyReplacement": api_key_replacement,
                        }
                    ),
                )
        report_fields = _resolve_report_model_fields(
            config,
            provider_hint=provider,
            model_hint=model_hint,
            endpoint_hint=input_data.get("endpointUrl"),
            api_key_hint=api_key_replacement,
        )
        probe_status = _retry_transient_runtime_call(
            lambda: self._client.models_probe_status(
                provider=provider,
                model=model_hint,
                endpoint_url=input_data.get("endpointUrl"),
            ),
            timeout_seconds=45.0,
        )
        target_provider = _optional_str(report_fields.get("provider")) or provider
        target_model = _optional_str(report_fields.get("model")) or _optional_str(input_data.get("model"))
        probe_result = _select_provider_probe_result(
            probe_status,
            provider=target_provider,
            model=target_model,
        )
        ok = _probe_result_status(probe_result) == "ok"
        checked_at = _now_iso()
        user_message = "报告模型连接测试通过。" if ok else _probe_failure_user_message(probe_result)
        if ok:
            self._report_model_status_store.write(
                {
                    "state": "ready",
                    "provider": report_fields["provider"],
                    "model": report_fields["model"],
                    "endpointUrl": report_fields["endpoint_url"],
                    "fingerprint": report_fields["fingerprint"],
                    "checkedAt": checked_at,
                    "lastErrorMessage": None,
                }
            )
        else:
            self._report_model_status_store.write(
                {
                    "state": "failed",
                    "provider": report_fields["provider"],
                    "model": report_fields["model"],
                    "endpointUrl": report_fields["endpoint_url"],
                    "fingerprint": report_fields["fingerprint"],
                    "checkedAt": checked_at,
                    "lastErrorMessage": user_message,
                }
            )
        out = {
            "ok": ok,
            "userMessage": user_message,
            "checkedAt": checked_at,
            "error": None
            if ok
            else {
                "code": "REPORT_MODEL_TEST_FAILED",
                "action": "check_model_config_and_retry",
                "retryable": True,
            },
        }
        self._idempotency[request_id] = out
        return out

    def test_embedding_via_openviking(self, input_data: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        self._validate_embedding_draft(input_data)
        provider = str(input_data.get("provider", "")).strip()
        model = str(input_data.get("model", "")).strip()
        if not provider or not model:
            raise UiBoundaryError("INVALID_INPUT", "请先填写 Embedding 服务商和模型。")

        env_values = self._embedding_config_store.read()
        api_key = str(input_data.get("apiKeyReplacement", "") or "").strip()
        if not api_key:
            api_key = _configured_value(env_values, "OPENVIKING_EMBEDDING_API_KEY")
        if not api_key and provider.lower() == "jina":
            api_key = os.environ.get("JINA_API_KEY", "").strip()

        probe_input = {
            "provider": provider,
            "model": model,
            "apiKey": api_key,
            "endpointUrl": _normalize_embedding_api_base_url(str(input_data.get("endpointUrl", "") or "")),
            "dimension": str(input_data.get("dimension", "") or "").strip(),
        }
        checked_at = _now_iso()
        try:
            probe_result = self._embedding_probe(probe_input)
        except Exception as exc:
            probe_result = {"ok": False, "reason": str(exc)}
        ok = bool(probe_result.get("ok")) if isinstance(probe_result, Mapping) else False
        user_message = (
            "Embedding 连接测试通过，已经能按意思生成检索向量。"
            if ok
            else _embedding_failure_user_message(probe_result if isinstance(probe_result, Mapping) else {})
        )
        out = {
            "ok": ok,
            "userMessage": user_message,
            "checkedAt": checked_at,
            "error": None
            if ok
            else {
                "code": "EMBEDDING_TEST_FAILED",
                "action": "check_embedding_config_and_retry",
                "retryable": True,
            },
        }
        self._idempotency[request_id] = out
        return out

    def get_report_model_readiness(self) -> ReportModelReadiness:
        config = self._client.config_get(paths=("agents.defaults.model", "models.providers"))
        return self._evaluate_report_model_readiness(config=config)

    def assert_report_model_ready(self) -> None:
        readiness = self.get_report_model_readiness()
        if readiness.ready:
            return
        raise UiBoundaryError("REPORT_MODEL_NOT_READY", readiness.user_message)

    def get_provider_health_summary(self) -> dict[str, Any]:
        config = self._client.config_get(paths=("agents.defaults.model", "models.providers"))
        report_fields = _resolve_report_model_fields(config)
        checked_at = _now_iso()
        provider = _optional_str(report_fields.get("provider"))
        model = _optional_str(report_fields.get("model"))
        if not bool(report_fields.get("configured")):
            return ProviderHealthSummary(
                state="not_configured",
                severity="info",
                user_message="报告模型尚未完成配置或测试，暂无法执行 provider 健康检查。",
                checked_at=checked_at,
                provider=provider,
                model=model,
                source="openclaw.models.probeStatus",
            ).to_user_payload()
        probe_status = self._client.models_probe_status(
            provider=str(report_fields["provider"]),
            model=_optional_str(report_fields.get("model")),
            endpoint_url=_optional_str(report_fields.get("endpoint_url")),
        )
        probe_result = _select_provider_probe_result(
            probe_status,
            provider=str(report_fields["provider"]),
            model=_optional_str(report_fields.get("model")),
        )
        ok = _probe_result_status(probe_result) == "ok"
        if ok:
            user_message = "provider 健康检查通过。"
            return ProviderHealthSummary(
                state="healthy",
                severity="success",
                user_message=user_message,
                checked_at=checked_at,
                provider=provider,
                model=model,
                source="openclaw.models.probeStatus",
            ).to_user_payload()
        raw_message = _probe_failure_user_message(probe_result)
        return ProviderHealthSummary(
            state="degraded",
            severity="warning",
            user_message=_sanitize_provider_health_message(raw_message),
            checked_at=checked_at,
            provider=provider,
            model=model,
            source="openclaw.models.probeStatus",
        ).to_user_payload()

    def get_runtime_service_status_summary(self) -> dict[str, Any]:
        checked_at = _now_iso()
        try:
            probe = self._runtime_health_probe()
        except Exception:
            return RuntimeServiceStatusSummary(
                state="degraded",
                severity="warning",
                user_message="运行服务状态暂不可读，请先重启本地运行时后重试。",
                checked_at=checked_at,
                source="runtime.health.http",
            ).to_user_payload()
        if not isinstance(probe, Mapping):
            return RuntimeServiceStatusSummary(
                state="degraded",
                severity="warning",
                user_message="运行服务状态暂不可读，请先重启本地运行时后重试。",
                checked_at=checked_at,
                source="runtime.health.http",
            ).to_user_payload()
        state = str(probe.get("state", "")).strip().lower()
        if state == "healthy":
            return RuntimeServiceStatusSummary(
                state="healthy",
                severity="success",
                user_message="运行服务健康检查通过，当前可继续执行报告相关操作。",
                checked_at=checked_at,
                source="runtime.health.http",
            ).to_user_payload()
        failed_services = probe.get("failedServices")
        error_kinds = probe.get("errorKinds")
        user_message = _runtime_failure_message(
            failed_services if isinstance(failed_services, list) else [],
            error_kinds if isinstance(error_kinds, list) else [],
        )
        return RuntimeServiceStatusSummary(
            state="degraded",
            severity="warning",
            user_message=user_message,
            checked_at=checked_at,
            source="runtime.health.http",
        ).to_user_payload()

    def get_evidence_failure_reason_summary(self) -> dict[str, Any]:
        checked_at = _now_iso()
        latest_run = _latest_report_command_run(self._run_root)
        if latest_run is None:
            return EvidenceFailureReasonSummary(
                state="no_records",
                severity="info",
                user_message="暂无最近 live run 记录。请先在聊天窗口执行一次 /report，然后返回高级诊断查看证据链摘要。",
                checked_at=checked_at,
                source="workflow.evidence_chain",
                latest_run=None,
                recommended_action="执行一次 /report 生成真实运行记录。",
            ).to_user_payload()

        run_dir_raw = latest_run.get("_runDir")
        run_dir = Path(str(run_dir_raw)) if isinstance(run_dir_raw, str) and run_dir_raw.strip() else None
        latest_payload = _latest_run_identity_payload(latest_run)
        reports = latest_run.get("collectFirstReports")
        if not isinstance(reports, list) or not reports:
            guard_failure = _extract_export_guard_failure(run_dir)
            if guard_failure is None:
                return EvidenceFailureReasonSummary(
                    state="no_records",
                    severity="info",
                    user_message="最近一次 live run 缺少证据链检查记录。请重新执行 /report 后重试。",
                    checked_at=checked_at,
                    source="workflow.evidence_chain",
                    latest_run=latest_payload,
                    recommended_action="重新执行一次 /report，确保生成完整证据链记录。",
                ).to_user_payload()
            category, reason = guard_failure
            return EvidenceFailureReasonSummary(
                state="failure_detected",
                severity="warning",
                user_message=_evidence_failure_user_message(category, reason),
                checked_at=checked_at,
                source="workflow.evidence_chain",
                latest_run=latest_payload,
                recommended_action=_evidence_failure_recommended_action(category),
            ).to_user_payload()

        guard_failure = _extract_export_guard_failure(run_dir)
        collect_first_failure = _extract_collect_first_evidence_failure(reports)
        failure = guard_failure or collect_first_failure
        if failure is not None:
            category, reason = failure
            return EvidenceFailureReasonSummary(
                state="failure_detected",
                severity="warning",
                user_message=_evidence_failure_user_message(category, reason),
                checked_at=checked_at,
                source="workflow.evidence_chain",
                latest_run=latest_payload,
                recommended_action=_evidence_failure_recommended_action(category),
            ).to_user_payload()
        return EvidenceFailureReasonSummary(
            state="no_failures",
            severity="success",
            user_message="最近 live run 未发现证据链失败。",
            checked_at=checked_at,
            source="workflow.evidence_chain",
            latest_run=latest_payload,
            recommended_action="保持当前配置；后续若出现失败再回到高级诊断复查。",
        ).to_user_payload()

    def get_live_run_gap_summary(self) -> dict[str, Any]:
        checked_at = _now_iso()
        latest_run = _latest_report_command_run(self._run_root)
        if latest_run is None:
            return LiveRunGapSummary(
                state="no_records",
                severity="info",
                user_message="暂无最近 live run 记录。请先在聊天窗口执行一次 /report，然后返回高级诊断查看缺口摘要。",
                checked_at=checked_at,
                source="workflow.collect_first_report",
                latest_run=None,
                recommended_action="执行一次 /report 生成真实运行记录。",
            ).to_user_payload()

        run_id = str(latest_run.get("runId") or "").strip()
        reports = latest_run.get("collectFirstReports")
        if not isinstance(reports, list) or not reports:
            return LiveRunGapSummary(
                state="no_records",
                severity="info",
                user_message="最近一次 live run 缺少 collect-first 记录。请重新执行 /report 后重试。",
                checked_at=checked_at,
                source="workflow.collect_first_report",
                latest_run=_latest_run_identity_payload(latest_run),
                recommended_action="重新执行一次 /report，确保生成 collect-first 报告。",
            ).to_user_payload()

        summary = _build_collect_first_gap_summary(reports)
        latest_payload = {
            "runId": run_id,
            "finishedAt": latest_run.get("finishedAt"),
            "market": latest_run.get("market"),
            "entryPoint": latest_run.get("entryPoint"),
            "collectFirstReportCount": summary["collectFirstReportCount"],
            "gapCount": summary["gapCount"],
            "collectFirstCompliance": summary["collectFirstCompliance"],
        }
        if int(summary["gapCount"]) > 0:
            return LiveRunGapSummary(
                state="gaps_detected",
                severity="warning",
                user_message=f"最近 live run 共发现 {summary['gapCount']} 条缺口，请先修复后再看下一轮运行。",
                checked_at=checked_at,
                source="workflow.collect_first_report",
                latest_run=latest_payload,
                recommended_action="优先排查缺口涉及阶段，再执行一次 /report 验证。",
            ).to_user_payload()
        return LiveRunGapSummary(
            state="no_gaps",
            severity="success",
            user_message="最近 live run 未发现缺口。",
            checked_at=checked_at,
            source="workflow.collect_first_report",
            latest_run=latest_payload,
            recommended_action="可继续观察后续运行；若出现失败再回到高级诊断复查。",
        ).to_user_payload()

    def _map_openclaw_config_to_draft(
        self,
        *,
        config: Mapping[str, Any],
        provider: str | None,
        status: Mapping[str, Any],
    ) -> dict[str, Any]:
        root = _config_root(config)
        resolved_provider = _normalize_report_provider_id(
            str(provider or "").strip()
            or _optional_str(root.get("active_provider"))
            or _provider_from_default_model(root)
            or _single_provider_id(root)
            or "deepseek"
        )
        if not _is_allowed_report_provider(resolved_provider):
            resolved_provider = "deepseek"
            provider_item: Mapping[str, Any] = {}
        else:
            provider_item = _provider_config(root, resolved_provider)
        model = _model_name_from_config(root, provider_item)
        api_key = _provider_api_key(provider_item)
        preset = _provider_preset(resolved_provider)
        return {
            "provider": resolved_provider,
            "apiKeyMasked": _mask_secret(api_key),
            "endpointUrl": _provider_endpoint_url(provider_item) or str(preset["endpoint_url"]),
            "defaultModel": _normalize_model_ref(resolved_provider, model or str(preset["default_model"])),
            "status": "saved",
            "lastTestMessage": _optional_str(status.get("message")),
            "updatedAt": _now_iso(),
        }

    def _write_report_model_runtime_config(
        self,
        *,
        draft: Mapping[str, Any],
        existing_config: Mapping[str, Any],
        report_fields: Mapping[str, Any],
    ) -> None:
        if self._report_model_config_store is None:
            return
        provider = _normalize_report_provider_id(str(report_fields.get("provider") or draft.get("provider") or ""))
        model = str(report_fields.get("model") or draft.get("defaultModel") or "").strip()
        if not provider or not model or not _is_allowed_report_provider(provider):
            return
        root = _config_root(existing_config)
        provider_item = _provider_config(root, provider)
        stored = self._report_model_config_store.read()
        api_key = (
            _optional_str(draft.get("apiKeyReplacement"))
            or _provider_api_key(provider_item)
            or _optional_str(stored.get("apiKey"))
        )
        if not api_key or _looks_masked_secret(api_key):
            return
        preset = _provider_preset(provider)
        model_ref = _normalize_model_ref(provider, model)
        payload = {
            "provider": provider,
            "model": model_ref,
            "providerModelId": _model_id_for_provider(provider, model_ref),
            "api": str(preset["api"]),
            "apiKey": api_key,
            "endpointUrl": _optional_str(report_fields.get("endpoint_url") or draft.get("endpointUrl"))
            or str(preset["endpoint_url"]),
            "updatedAt": _now_iso(),
        }
        self._report_model_config_store.write(payload)

    def _build_config_patch(self, draft: Mapping[str, Any]) -> dict[str, Any]:
        provider = _require_allowed_report_provider(str(draft["provider"]))
        preset = _provider_preset(provider)
        model_ref = _normalize_model_ref(provider, str(draft.get("defaultModel", "")).strip())
        model_id = _model_id_for_provider(provider, model_ref)
        provider_patch: dict[str, Any] = {
            "baseUrl": _optional_str(draft.get("endpointUrl")) or str(preset["endpoint_url"]),
            "api": str(preset["api"]),
            "models": [
                {
                    "id": model_id,
                    "name": model_id,
                    "reasoning": False,
                    "input": ["text"],
                }
            ],
        }
        api_key_replacement = str(draft.get("apiKeyReplacement", "")).strip()
        if api_key_replacement:
            provider_patch["apiKey"] = api_key_replacement
        return {
            "agents": {
                "defaults": {
                    "model": {"primary": model_ref},
                    "models": {model_ref: {}},
                }
            },
            "models": {"providers": {provider: provider_patch}},
        }

    def _load_embedding_draft(self) -> dict[str, Any]:
        env_values = self._embedding_config_store.read()
        provider = _configured_value(env_values, "OPENVIKING_EMBEDDING_PROVIDER")
        model = _configured_value(env_values, "OPENVIKING_EMBEDDING_MODEL")
        api_key = _configured_value(env_values, "OPENVIKING_EMBEDDING_API_KEY")
        return {
            "provider": provider,
            "model": model,
            "apiKeyMasked": _mask_secret(api_key),
            "endpointUrl": _normalize_embedding_api_base_url(
                _configured_value(env_values, "OPENVIKING_EMBEDDING_API_BASE")
            ),
            "dimension": _configured_value(env_values, "OPENVIKING_EMBEDDING_DIMENSION"),
            "enabled": bool(provider and model),
        }

    def _validate_embedding_draft(self, raw: object) -> None:
        if raw is None:
            return
        if not isinstance(raw, Mapping):
            raise UiBoundaryError("INVALID_INPUT", "embedding 配置格式不正确。")
        provider = str(raw.get("provider", "")).strip()
        model = str(raw.get("model", "")).strip()
        if bool(provider) != bool(model):
            raise UiBoundaryError("INVALID_INPUT", "Embedding 服务商和模型必须同时填写，留空则关闭语义检索。")
        dimension = str(raw.get("dimension", "")).strip()
        if dimension and (not dimension.isdigit() or int(dimension) <= 0):
            raise UiBoundaryError("INVALID_INPUT", "Embedding 维度必须是正整数。")

    def _save_embedding_config(self, raw: object) -> None:
        if raw is None:
            return
        if not isinstance(raw, Mapping):
            raise UiBoundaryError("INVALID_INPUT", "embedding 配置格式不正确。")
        provider = str(raw.get("provider", "")).strip()
        model = str(raw.get("model", "")).strip()
        api_key_replacement = str(raw.get("apiKeyReplacement", "")).strip()
        updates = {
            "OPENVIKING_EMBEDDING_PROVIDER": provider,
            "OPENVIKING_EMBEDDING_MODEL": model,
            "OPENVIKING_EMBEDDING_API_BASE": _normalize_embedding_api_base_url(
                str(raw.get("endpointUrl", "") or "")
            ),
            "OPENVIKING_EMBEDDING_DIMENSION": str(raw.get("dimension", "")).strip(),
        }
        if api_key_replacement or not provider:
            updates["OPENVIKING_EMBEDDING_API_KEY"] = api_key_replacement
        self._embedding_config_store.write(updates)

    def _evaluate_report_model_readiness(
        self,
        *,
        config: Mapping[str, Any],
        provider_hint: str | None = None,
    ) -> ReportModelReadiness:
        report_fields = _resolve_report_model_fields(config, provider_hint=provider_hint)
        if not report_fields["configured"]:
            return ReportModelReadiness(
                state="unconfigured",
                blocked=True,
                ready=False,
                user_message="请先在设置中填写报告模型（服务商、模型、API Key），并完成测试。",
            )
        status = self._report_model_status_store.read()
        fingerprint = str(status.get("fingerprint") or "")
        fingerprint_matches = fingerprint == str(report_fields["fingerprint"] or "")
        masked_secret_matches = _status_matches_current_public_model_fields(status, report_fields) and _looks_masked_secret(
            str(report_fields.get("api_key") or "")
        )
        if not fingerprint_matches and not masked_secret_matches:
            return ReportModelReadiness(
                state="saved_unverified",
                blocked=True,
                ready=False,
                user_message="报告模型已保存但尚未测试通过，请先执行模型测试。",
                checked_at=_optional_str(status.get("checkedAt")),
            )
        state = str(status.get("state") or "").strip().lower()
        if state == "ready":
            return ReportModelReadiness(
                state="ready",
                blocked=False,
                ready=True,
                user_message="报告模型可用。",
                checked_at=_optional_str(status.get("checkedAt")),
            )
        if state == "failed":
            last_error = _optional_str(status.get("lastErrorMessage")) or "报告模型连接测试失败，请检查模型配置后重试。"
            user_message = last_error if last_error.startswith("报告模型") else f"报告模型测试失败：{last_error}"
            return ReportModelReadiness(
                state="failed",
                blocked=True,
                ready=False,
                user_message=user_message,
                checked_at=_optional_str(status.get("checkedAt")),
            )
        return ReportModelReadiness(
            state="saved_unverified",
            blocked=True,
            ready=False,
            user_message="报告模型已保存但尚未测试通过，请先执行模型测试。",
            checked_at=_optional_str(status.get("checkedAt")),
        )


def _provider_config(config: Mapping[str, Any], provider: str) -> Mapping[str, Any]:
    normalized_provider = _normalize_report_provider_id(provider)
    models = config.get("models")
    if not isinstance(models, Mapping):
        return {}
    providers = models.get("providers")
    if not isinstance(providers, Mapping):
        return {}
    entry = providers.get(normalized_provider)
    if not isinstance(entry, Mapping):
        entry = next(
            (
                value
                for key, value in providers.items()
                if _normalize_report_provider_id(str(key)) == normalized_provider
            ),
            {},
        )
    return entry if isinstance(entry, Mapping) else {}


def _config_root(config: Mapping[str, Any]) -> Mapping[str, Any]:
    parsed = config.get("parsed")
    if isinstance(parsed, Mapping):
        return parsed
    nested = config.get("config")
    if isinstance(nested, Mapping):
        return nested
    return config


def _empty_llm_config() -> dict[str, Any]:
    return {
        "revision": "unconfigured",
        "parsed": {
            "active_provider": "",
            "agents": {"defaults": {"model": ""}},
            "models": {"providers": {}},
        },
    }


def _build_report_model_reset_patch(config: Mapping[str, Any]) -> dict[str, Any]:
    report_fields = _resolve_report_model_fields(config)
    provider = _optional_str(report_fields.get("provider"))
    model = _optional_str(report_fields.get("model"))
    defaults_patch: dict[str, Any] = {"model": None}
    if provider and model:
        defaults_patch["models"] = {_normalize_model_ref(provider, model): None}
    patch: dict[str, Any] = {"agents": {"defaults": defaults_patch}}
    if provider:
        patch["models"] = {"providers": {provider: None}}
    return patch


def _provider_preset(provider: str) -> Mapping[str, Any]:
    normalized = _normalize_report_provider_id(provider)
    return _PROVIDER_PRESETS.get(normalized, _PROVIDER_PRESETS["deepseek"])


def _normalize_model_ref(provider: str, model: str) -> str:
    normalized_provider = _normalize_report_provider_id(provider) or "deepseek"
    normalized_model = model.strip() or str(_provider_preset(normalized_provider)["default_model"])
    if "/" in normalized_model:
        return normalized_model
    return f"{normalized_provider}/{normalized_model}"


def _model_id_for_provider(provider: str, model_ref: str) -> str:
    normalized_provider = _normalize_report_provider_id(provider)
    normalized = model_ref.strip()
    prefix = f"{normalized_provider}/"
    if normalized_provider and normalized.startswith(prefix):
        return normalized[len(prefix) :]
    return normalized


def _model_name_from_config(config: Mapping[str, Any], provider_item: Mapping[str, Any]) -> str:
    defaults = config.get("agents", {})
    if isinstance(defaults, Mapping):
        agent_defaults = defaults.get("defaults", {})
        if isinstance(agent_defaults, Mapping):
            raw_model = agent_defaults.get("model")
            if isinstance(raw_model, Mapping):
                primary = _optional_str(raw_model.get("primary"))
                if primary:
                    return primary
            model = _optional_str(raw_model)
            if model:
                return model
    return _provider_default_model(provider_item)


def _resolve_report_model_fields(
    config: Mapping[str, Any],
    *,
    provider_hint: Any = None,
    model_hint: Any = None,
    endpoint_hint: Any = None,
    api_key_hint: Any = None,
) -> dict[str, Any]:
    root = _config_root(config)
    provider = _normalize_report_provider_id(_optional_str(provider_hint) or _optional_str(root.get("active_provider")) or "")
    providers = root.get("models", {}).get("providers", {}) if isinstance(root.get("models"), Mapping) else {}
    if not provider:
        inferred = _provider_from_default_model(root)
        if inferred:
            provider = _normalize_report_provider_id(inferred)
    if not provider:
        provider = _normalize_report_provider_id(_single_provider_id(root) or "")
    if provider and not _is_allowed_report_provider(provider):
        return {
            "provider": provider,
            "model": "",
            "endpoint_url": "",
            "api_key": "",
            "configured": False,
            "fingerprint": "",
        }
    provider_item = _provider_config(root, provider) if provider else {}
    model = _optional_str(model_hint) or _model_name_from_config(root, provider_item)
    endpoint = _optional_str(endpoint_hint) or _provider_endpoint_url(provider_item)
    api_key = _optional_str(api_key_hint) or _provider_api_key(provider_item)
    configured = bool(provider and model and api_key)
    fingerprint = _report_model_fingerprint(
        provider=provider,
        model=model,
        endpoint_url=endpoint or "",
        api_key=api_key,
    )
    return {
        "provider": provider,
        "model": model,
        "endpoint_url": endpoint,
        "api_key": api_key,
        "configured": configured,
        "fingerprint": fingerprint if configured else "",
    }


def _report_model_fields_from_runtime_config(config: Mapping[str, Any]) -> dict[str, Any]:
    provider = _normalize_report_provider_id(_optional_str(config.get("provider")) or "")
    model = _optional_str(config.get("model"))
    endpoint = _optional_str(config.get("endpointUrl") or config.get("endpoint_url"))
    api_key = _optional_str(config.get("apiKey") or config.get("api_key"))
    configured = bool(provider and model and api_key and _is_allowed_report_provider(provider))
    fingerprint = _report_model_fingerprint(
        provider=provider,
        model=model,
        endpoint_url=endpoint or "",
        api_key=api_key,
    )
    return {
        "provider": provider,
        "model": model,
        "endpoint_url": endpoint,
        "api_key": api_key,
        "configured": configured,
        "fingerprint": fingerprint if configured else "",
    }


def _status_provider_allowed(status: Mapping[str, Any]) -> bool:
    provider = _optional_str(status.get("provider"))
    return not provider or _is_allowed_report_provider(provider)


def _report_model_status_payload(status: Mapping[str, Any]) -> dict[str, Any]:
    state = str(status.get("state") or "unconfigured")
    if state == "ready":
        return ReportModelReadiness(
            state="ready",
            blocked=False,
            ready=True,
            user_message="报告模型可用。",
            checked_at=_optional_str(status.get("checkedAt")),
        ).to_user_payload()
    if state == "failed":
        return ReportModelReadiness(
            state="failed",
            blocked=True,
            ready=False,
            user_message=_optional_str(status.get("lastErrorMessage")) or "报告模型连接测试失败，请检查配置后重试。",
            checked_at=_optional_str(status.get("checkedAt")),
        ).to_user_payload()
    if state == "saved_unverified":
        return ReportModelReadiness(
            state="saved_unverified",
            blocked=True,
            ready=False,
            user_message="报告模型已保存但尚未测试通过，请先执行模型测试。",
            checked_at=_optional_str(status.get("checkedAt") or status.get("savedAt")),
        ).to_user_payload()
    return ReportModelReadiness(
        state="unconfigured",
        blocked=True,
        ready=False,
        user_message="请先在设置中填写报告模型（服务商、模型、API Key），并完成测试。",
        checked_at=_optional_str(status.get("checkedAt")),
    ).to_user_payload()


def _single_provider_id(config: Mapping[str, Any]) -> str | None:
    models = config.get("models")
    if not isinstance(models, Mapping):
        return None
    providers = models.get("providers")
    if not isinstance(providers, Mapping):
        return None
    if len(providers) != 1:
        return None
    only_key = next(iter(providers.keys()))
    provider = str(only_key).strip()
    return provider or None


def _provider_endpoint_url(provider_item: Mapping[str, Any]) -> str:
    return _optional_str(
        provider_item.get("endpoint_url")
        or provider_item.get("base_url")
        or provider_item.get("endpointUrl")
        or provider_item.get("baseUrl")
    )


def _provider_api_key(provider_item: Mapping[str, Any]) -> str:
    return _optional_str(provider_item.get("api_key") or provider_item.get("apiKey")) or ""


def _provider_default_model(provider_item: Mapping[str, Any]) -> str:
    default_model = _optional_str(provider_item.get("default_model") or provider_item.get("defaultModel"))
    if default_model:
        return default_model
    models = provider_item.get("models")
    if isinstance(models, list):
        for item in models:
            if not isinstance(item, Mapping):
                continue
            model_id = _optional_str(item.get("id") or item.get("model"))
            if model_id:
                return model_id
    return ""


def _provider_from_default_model(config: Mapping[str, Any]) -> str | None:
    defaults = config.get("agents")
    if not isinstance(defaults, Mapping):
        return None
    agent_defaults = defaults.get("defaults")
    if not isinstance(agent_defaults, Mapping):
        return None
    raw_model = agent_defaults.get("model")
    model_name = ""
    if isinstance(raw_model, Mapping):
        model_name = _optional_str(raw_model.get("primary")) or ""
    else:
        model_name = _optional_str(raw_model) or ""
    if "/" not in model_name:
        return None
    provider = model_name.split("/", 1)[0].strip()
    return provider or None


def _select_provider_probe_result(
    probe_status: Any,
    *,
    provider: str,
    model: str | None,
) -> Mapping[str, Any] | None:
    target_provider = provider.strip().lower()
    if not target_provider:
        return None
    candidates: list[Mapping[str, Any]] = []
    for item in _probe_results(probe_status):
        candidate_provider = _optional_str(item.get("provider")).lower()
        if candidate_provider == target_provider:
            candidates.append(item)
    if not candidates:
        return None
    target_model = _optional_str(model)
    if target_model:
        for item in candidates:
            if _probe_model_matches(target_provider, target_model, _optional_str(item.get("model"))):
                return item
        return None
    return candidates[0]


def _probe_results(probe_status: Any) -> list[Mapping[str, Any]]:
    if isinstance(probe_status, list):
        return [item for item in probe_status if isinstance(item, Mapping)]
    if not isinstance(probe_status, Mapping):
        return []
    auth = probe_status.get("auth")
    if isinstance(auth, Mapping):
        probes = auth.get("probes")
        if isinstance(probes, Mapping):
            results = probes.get("results")
            if isinstance(results, list):
                return [item for item in results if isinstance(item, Mapping)]
    probes = probe_status.get("probes")
    if isinstance(probes, Mapping):
        results = probes.get("results")
        if isinstance(results, list):
            return [item for item in results if isinstance(item, Mapping)]
    results = probe_status.get("results")
    if isinstance(results, list):
        return [item for item in results if isinstance(item, Mapping)]
    return []


def _probe_model_matches(provider: str, expected_model: str, candidate_model: str) -> bool:
    expected = expected_model.strip().lower()
    candidate = candidate_model.strip().lower()
    if not expected or not candidate:
        return False
    if expected == candidate:
        return True
    provider_prefix = f"{provider}/"
    if expected.startswith(provider_prefix):
        expected = expected[len(provider_prefix) :]
    if candidate.startswith(provider_prefix):
        candidate = candidate[len(provider_prefix) :]
    return expected == candidate


def _probe_result_status(result: Mapping[str, Any] | None) -> str:
    if not isinstance(result, Mapping):
        return "missing"
    return _optional_str(result.get("status")).lower() or "unknown"


def _probe_failure_user_message(result: Mapping[str, Any] | None) -> str:
    status = _probe_result_status(result)
    if status == "missing_credential":
        return "报告模型连接测试失败，请填写 API Key 后重新测试。"
    if status in {"expired", "invalid_expires"}:
        return "报告模型认证失败，API Key 已过期，请到设置更新后重新测试。"
    if status in {"auth", "unresolved_ref"}:
        return "报告模型认证失败，API Key 无效或无法认证，请到设置更新后重新测试。"
    if status == "rate_limit":
        return "报告模型请求被服务商限流，请稍后重试或降低并发。"
    if status == "billing":
        return "报告模型额度不足或账户计费异常，请到服务商后台处理后重新测试。"
    if status == "timeout":
        return "报告模型连接测试失败，请稍后重试（连接超时）。"
    if status in {"no_model", "excluded_by_auth_order"}:
        return "报告模型连接测试失败，请检查模型配置后重试。"
    return "报告模型连接测试失败，请检查模型服务商配置后重试。"


def _probe_openviking_embedding_runtime(input_data: Mapping[str, Any]) -> dict[str, Any]:
    from openviking_cli.utils.config.embedding_config import EmbeddingConfig, EmbeddingModelConfig

    dimension_raw = _optional_str(input_data.get("dimension"))
    dense_config: dict[str, Any] = {
        "provider": _optional_str(input_data.get("provider")),
        "model": _optional_str(input_data.get("model")),
    }
    api_key = _optional_str(input_data.get("apiKey"))
    endpoint_url = _normalize_embedding_api_base_url(_optional_str(input_data.get("endpointUrl")))
    if api_key:
        dense_config["api_key"] = api_key
    if endpoint_url:
        dense_config["api_base"] = endpoint_url
    if dimension_raw:
        dense_config["dimension"] = int(dimension_raw)

    embedder = EmbeddingConfig(
        dense=EmbeddingModelConfig(**dense_config),
        max_concurrent=1,
        max_retries=0,
    ).get_embedder()
    result = embedder.embed("claw-trade embedding connection test", is_query=False)
    dense_vector = getattr(result, "dense_vector", None)
    sparse_vector = getattr(result, "sparse_vector", None)
    dense_ok = isinstance(dense_vector, list) and len(dense_vector) > 0
    sparse_ok = isinstance(sparse_vector, dict) and len(sparse_vector) > 0
    if not dense_ok and not sparse_ok:
        return {"ok": False, "reason": "empty_vector"}
    return {
        "ok": True,
        "denseDimension": len(dense_vector) if dense_ok else None,
        "hasSparseVector": sparse_ok,
    }


def _embedding_failure_user_message(result: Mapping[str, Any]) -> str:
    reason = str(result.get("reason") or result.get("message") or "").strip().lower()
    if any(token in reason for token in ("api_key", "api key", "key", "unauthorized", "auth")):
        return "Embedding 连接测试失败，请检查 API Key 后重试。"
    if any(token in reason for token in ("timeout", "timed out", "connect", "connection", "url", "dns", "network")):
        return "Embedding 连接测试失败，请检查接口地址或网络后重试。"
    if any(token in reason for token in ("provider", "model", "dimension", "unsupported", "invalid")):
        return "Embedding 连接测试失败，请检查服务商、模型和维度后重试。"
    return "Embedding 连接测试失败，请检查服务商、模型、API Key 和接口地址后重试。"


def _retry_transient_runtime_call(
    callback: Callable[[], Any],
    *,
    timeout_seconds: float,
    interval_seconds: float = 1.0,
) -> Any:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None
    while time.monotonic() <= deadline:
        try:
            return callback()
        except Exception as exc:  # pragma: no cover - exercised via caller tests
            last_error = exc
            time.sleep(interval_seconds)
    if last_error is not None:
        raise last_error
    return callback()


def _sanitize_provider_health_message(raw: str) -> str:
    message = raw.strip()
    if not message:
        return "provider 健康检查失败，请检查模型配置后重试。"
    lowered = message.lower()
    if "provider attempt" in lowered or "raw payload" in lowered or "request body" in lowered:
        return "provider 健康检查失败，网关调用返回异常。请检查模型配置后重试。"
    if "timeout" in lowered:
        return "provider 健康检查超时，请稍后重试。"
    if "unauthorized" in lowered or "auth" in lowered or "api key" in lowered or "401" in lowered:
        return "provider 鉴权失败，请检查 API Key 后重试。"
    if "rate limit" in lowered or "429" in lowered:
        return "provider 当前触发频率限制，请稍后重试。"
    return message


def _runtime_failure_message(failed_services: list[Any], error_kinds: list[Any]) -> str:
    failed = {str(item).strip() for item in failed_services if str(item).strip()}
    kinds = {str(item).strip() for item in error_kinds if str(item).strip()}
    if failed == {"session_service"}:
        return "会话服务暂未就绪，请先重启本地运行时后重试。"
    if failed == {"data_service"}:
        return "资料服务暂未就绪，请先检查本地运行时状态后重试。"
    if "timeout" in kinds:
        return "运行服务健康检查超时，请先确认本地运行时已启动，再重试。"
    if "connection_refused" in kinds:
        return "运行服务未就绪，请先启动本地运行时后重试。"
    return "运行服务存在异常，请先重启本地运行时并复查健康状态。"


def _latest_report_command_run(run_root: Path) -> dict[str, Any] | None:
    if not run_root.exists():
        return None
    candidates: list[dict[str, Any]] = []
    for run_dir in sorted(run_root.iterdir()):
        if not run_dir.is_dir():
            continue
        state_payload = _read_json_object(run_dir / "state.json")
        if state_payload is None:
            continue
        request = state_payload.get("request")
        if not isinstance(request, Mapping):
            continue
        entry_point = str(request.get("entry_point") or "").strip().lower()
        if entry_point != "report_command":
            continue
        reports_dir = run_dir / "reports"
        collect_reports = sorted(reports_dir.glob("collect-first-*.json"))
        collect_payloads: list[dict[str, Any]] = []
        for item in collect_reports:
            payload = _read_json_object(item)
            if payload is not None:
                collect_payloads.append(payload)
        updated_at = _optional_str(state_payload.get("updated_at") or state_payload.get("created_at")) or ""
        candidates.append(
            {
                "runId": str(state_payload.get("run_id") or run_dir.name),
                "finishedAt": updated_at,
                "market": _optional_str(request.get("market")) or "",
                "entryPoint": entry_point,
                "collectFirstReports": collect_payloads,
                "_runDir": str(run_dir.resolve()),
            }
        )
    if not candidates:
        return None
    candidates.sort(key=lambda item: (str(item.get("finishedAt") or ""), str(item.get("runId") or "")))
    return candidates[-1]


def _latest_run_identity_payload(latest_run: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "runId": str(latest_run.get("runId") or "").strip(),
        "finishedAt": latest_run.get("finishedAt"),
        "market": latest_run.get("market"),
        "entryPoint": latest_run.get("entryPoint"),
    }


def _extract_export_guard_failure(run_dir: Path | None) -> tuple[str, str] | None:
    if run_dir is None:
        return None
    payload = _read_json_object(run_dir / "reports" / "export-guard-results.json")
    if not isinstance(payload, Mapping):
        return None
    ok = bool(payload.get("ok"))
    category = str(payload.get("category") or "").strip()
    if ok and category == "ok":
        return None
    reason = str(payload.get("reason") or "").strip() or "导出阶段证据校验失败"
    return category or "export_guard", reason


def _extract_collect_first_evidence_failure(collect_first_reports: list[dict[str, Any]]) -> tuple[str, str] | None:
    for report in collect_first_reports:
        compliance = report.get("collect_first_compliance")
        if not isinstance(compliance, Mapping):
            continue
        failures = compliance.get("failures_collected")
        if isinstance(failures, list):
            for item in failures:
                if not isinstance(item, Mapping):
                    continue
                category = str(item.get("category") or "").strip()
                reason = str(item.get("reason") or "").strip()
                if _is_evidence_chain_failure(category, reason):
                    return category or "collect_first", reason or "证据链校验失败"
        exceptions = compliance.get("exception_evidence")
        if isinstance(exceptions, list):
            for item in exceptions:
                if not isinstance(item, Mapping):
                    continue
                category = str(item.get("early_stop_category") or "").strip()
                reason = str(item.get("reason") or "").strip()
                if _is_evidence_chain_failure(category, reason):
                    return category or "collect_first", reason or "证据链校验失败"
    return None


def _is_evidence_chain_failure(category: str, reason: str) -> bool:
    normalized = category.strip().lower()
    if normalized in {
        "provider_evidence",
        "provider_request",
        "visible_tools",
        "tool_calls",
        "workspace_evidence",
        "openviking_runtime_reads",
        "openviking_receipt",
        "openviking_integrity",
        "artifact_flow_overreach",
        "claim",
        "l1_l2",
        "export_guard",
    }:
        return True
    lowered = reason.lower()
    phrase_keywords = ("provider request", "visible tools", "tool calls")
    if any(keyword in lowered for keyword in phrase_keywords):
        return True
    token_keywords = {"evidence", "openviking", "receipt", "claim", "l1", "l2", "hash", "uri"}
    reason_tokens = {token for token in re.split(r"[^a-z0-9_]+", lowered) if token}
    return bool(reason_tokens.intersection(token_keywords))


def _evidence_failure_user_message(category: str, reason: str) -> str:
    lowered = reason.lower()
    if category in {"provider_evidence", "provider_request", "visible_tools", "tool_calls", "workspace_evidence"}:
        base = "模型调用证据校验失败。"
    elif category in {"openviking_runtime_reads", "openviking_receipt", "openviking_integrity", "export_guard"}:
        base = "证据回执或完整性校验失败。"
    elif category in {"artifact_flow_overreach", "claim", "l1_l2"}:
        base = "报告材料与证据一致性校验失败。"
    else:
        base = "证据链校验失败。"
    if "timeout" in lowered:
        return f"{base} 可能存在超时，请重试并复查。"
    if "missing" in lowered or "not found" in lowered or "缺失" in reason:
        return f"{base} 发现关键证据缺失，请按建议操作处理。"
    if "mismatch" in lowered or "不一致" in reason or "越权" in reason:
        return f"{base} 发现一致性异常，请按建议操作处理。"
    if "provider attempt" in lowered or "raw payload" in lowered or "receipt" in lowered:
        return f"{base} 运行产物校验未通过，请按建议操作处理。"
    return base


def _evidence_failure_recommended_action(category: str) -> str:
    if category in {"provider_evidence", "provider_request", "visible_tools", "tool_calls", "workspace_evidence"}:
        return "先重新执行一次 /report；若仍失败，请开发者检查模型调用证据采集与工具可见性记录。"
    if category in {"openviking_runtime_reads", "openviking_receipt", "openviking_integrity", "export_guard"}:
        return "先重新执行一次 /report；若仍失败，请开发者检查证据回执写入与完整性校验。"
    if category in {"artifact_flow_overreach", "claim", "l1_l2"}:
        return "先重新执行一次 /report；若仍失败，请开发者检查报告材料与证据映射一致性。"
    return "先重新执行一次 /report；若仍失败，请开发者检查最近一次运行的证据链记录。"


def _build_collect_first_gap_summary(collect_first_reports: list[dict[str, Any]]) -> dict[str, Any]:
    stages: set[str] = set()
    completed_items = 0
    failures_collected = 0
    early_stop_exception_used = False
    exception_evidence = 0
    batch_fix_grouping = 0
    for report in collect_first_reports:
        compliance = report.get("collect_first_compliance")
        if not isinstance(compliance, Mapping):
            continue
        batch_scope = compliance.get("batch_scope")
        if isinstance(batch_scope, Mapping):
            stage_name = str(batch_scope.get("stage") or "").strip()
            if stage_name:
                stages.add(stage_name)
        completed = compliance.get("completed_items")
        if isinstance(completed, list):
            completed_items += len(completed)
        failures = compliance.get("failures_collected")
        if isinstance(failures, list):
            failures_collected += len(failures)
        if bool(compliance.get("early_stop_exception_used")):
            early_stop_exception_used = True
        evidence = compliance.get("exception_evidence")
        if isinstance(evidence, list):
            exception_evidence += len(evidence)
        fix_grouping = compliance.get("batch_fix_grouping")
        if isinstance(fix_grouping, list):
            batch_fix_grouping += len(fix_grouping)
    return {
        "collectFirstReportCount": len(collect_first_reports),
        "gapCount": failures_collected,
        "collectFirstCompliance": {
            "batchScope": {
                "stageCount": len(stages),
                "stages": sorted(stages),
            },
            "completedItems": completed_items,
            "failuresCollected": failures_collected,
            "earlyStopExceptionUsed": early_stop_exception_used,
            "exceptionEvidence": exception_evidence,
            "batchFixGrouping": batch_fix_grouping,
        },
    }


def _probe_runtime_services() -> dict[str, Any]:
    runtime_env = _load_runtime_env_values(Path(".runtime/dev-services/runtime.env"))
    session_health_url = _openclaw_health_url(runtime_env)
    data_health_url = _openviking_health_url(runtime_env)
    checks = [
        ("session_service", _http_health_check(session_health_url)),
        ("data_service", _http_health_check(data_health_url)),
    ]
    failed_services = [name for name, result in checks if not bool(result.get("ok"))]
    if not failed_services:
        return {
            "state": "healthy",
            "failedServices": [],
            "errorKinds": [],
        }
    error_kinds = [
        str(result.get("errorKind"))
        for _, result in checks
        if not bool(result.get("ok")) and str(result.get("errorKind") or "").strip()
    ]
    return {
        "state": "degraded",
        "failedServices": failed_services,
        "errorKinds": error_kinds,
    }


def _load_runtime_env_values(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _openclaw_health_url(runtime_env: Mapping[str, str]) -> str:
    gateway_ws = os.environ.get("OPENCLAW_GATEWAY_URL", "").strip() or runtime_env.get("OPENCLAW_GATEWAY_URL", "")
    parsed = urlsplit(gateway_ws.strip())
    scheme = "http" if parsed.scheme == "ws" else "https" if parsed.scheme == "wss" else "http"
    netloc = parsed.netloc or parsed.path or "127.0.0.1:18789"
    return urlunsplit((scheme, netloc, "/health", "", ""))


def _openviking_health_url(runtime_env: Mapping[str, str]) -> str:
    base = (
        os.environ.get("OPENVIKING_BASE_URL", "").strip()
        or os.environ.get("OPENVIKING_ENDPOINT", "").strip()
        or runtime_env.get("OPENVIKING_BASE_URL", "").strip()
        or runtime_env.get("OPENVIKING_ENDPOINT", "").strip()
        or "http://127.0.0.1:1933"
    )
    parsed = urlsplit(base)
    scheme = parsed.scheme or "http"
    netloc = parsed.netloc or parsed.path or "127.0.0.1:1933"
    return urlunsplit((scheme, netloc, "/health", "", ""))


def _http_health_check(url: str) -> dict[str, Any]:
    try:
        with urlopen(url, timeout=2) as response:  # nosec B310: localhost runtime health probe
            status_code = int(getattr(response, "status", 0) or 0)
            if status_code == 200:
                return {"ok": True}
            return {"ok": False, "errorKind": "http_status"}
    except HTTPError:
        return {"ok": False, "errorKind": "http_status"}
    except URLError as exc:
        reason = str(getattr(exc, "reason", "")).lower()
        if "timed out" in reason or "timeout" in reason:
            return {"ok": False, "errorKind": "timeout"}
        if "refused" in reason:
            return {"ok": False, "errorKind": "connection_refused"}
        return {"ok": False, "errorKind": "network_error"}
    except TimeoutError:
        return {"ok": False, "errorKind": "timeout"}
    except Exception:
        return {"ok": False, "errorKind": "unknown"}


def _ui_draft_status_from_readiness(state: str) -> str:
    if state == "unconfigured":
        return "idle"
    if state == "failed":
        return "error"
    return "saved"


def _report_model_fingerprint(*, provider: str, model: str, endpoint_url: str, api_key: str) -> str:
    payload = f"{provider}|{model}|{endpoint_url}|{api_key}"
    return sha1(payload.encode("utf-8")).hexdigest()


def _looks_masked_secret(value: str) -> bool:
    text = value.strip()
    return text.startswith("***") or text.startswith("****") or text == "__OPENCLAW_REDACTED__"


def _status_matches_current_public_model_fields(status: Mapping[str, Any], report_fields: Mapping[str, Any]) -> bool:
    if not str(status.get("fingerprint") or ""):
        return False
    return (
        _optional_str(status.get("provider")) == _optional_str(report_fields.get("provider"))
        and _optional_str(status.get("model")) == _optional_str(report_fields.get("model"))
        and _optional_str(status.get("endpointUrl") or status.get("endpoint_url"))
        == _optional_str(report_fields.get("endpoint_url"))
    )


def _report_model_config_patch_needed(
    current_fields: Mapping[str, Any],
    *,
    provider: str,
    model_ref: str,
    endpoint_url: Any,
    api_key_replacement: Any,
) -> bool:
    if _optional_str(api_key_replacement):
        return True
    if _normalize_report_provider_id(_optional_str(current_fields.get("provider"))) != provider:
        return True
    if _normalize_model_ref(provider, _optional_str(current_fields.get("model"))) != model_ref:
        return True
    desired_endpoint = _optional_str(endpoint_url) or str(_provider_preset(provider)["endpoint_url"])
    return _optional_str(current_fields.get("endpoint_url")) != desired_endpoint


def _opaque_settings_version(raw: Any) -> str:
    text = str(raw or "unknown")
    return f"v_{sha1(text.encode('utf-8')).hexdigest()[:12]}"


def _opaque_version(*parts: Any) -> str:
    joined = "|".join(str(item) for item in parts)
    return f"s_{sha1(joined.encode('utf-8')).hexdigest()[:12]}"


def _mask_secret(value: str) -> str | None:
    text = value.strip()
    if not text:
        return None
    if _looks_masked_secret(text):
        return "***"
    return "***" + text[-4:]


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _read_env_file_values(path: Path) -> dict[str, str]:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return {}
    values: dict[str, str] = {}
    for raw in lines:
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if key in _EMBEDDING_ENV_KEYS:
            values[key] = _strip_env_quotes(value.strip())
    return values


def _configured_value(file_values: Mapping[str, str], key: str) -> str:
    if key in file_values:
        return file_values[key].strip()
    return os.environ.get(key, "").strip()


def _normalize_embedding_api_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        return ""
    parsed = urlsplit(value)
    path = parsed.path.rstrip("/")
    if path.lower().endswith("/embeddings"):
        path = path[: -len("/embeddings")].rstrip("/")
        return urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
    return value


def _strip_env_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def allowed_llm_embedding_env_keys() -> tuple[str, ...]:
    return _EMBEDDING_ENV_KEYS
