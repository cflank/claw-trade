from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha1
import os
from pathlib import Path
from typing import Any, Mapping

from claw_trade.ui_backend.settings_service import EnvLocalAllowlistWriter, UiBoundaryError


_EMBEDDING_ENV_KEYS = (
    "OPENVIKING_EMBEDDING_PROVIDER",
    "OPENVIKING_EMBEDDING_MODEL",
    "OPENVIKING_EMBEDDING_API_KEY",
    "OPENVIKING_EMBEDDING_API_BASE",
    "OPENVIKING_EMBEDDING_DIMENSION",
)


class LlmSettingsBridge:
    def __init__(
        self,
        openclaw_gateway_client: Any,
        *,
        embedding_env_path: Path = Path(".env.local"),
        embedding_env_writer: EnvLocalAllowlistWriter | None = None,
    ) -> None:
        self._client = openclaw_gateway_client
        self._embedding_env_path = embedding_env_path
        self._embedding_env_writer = embedding_env_writer or EnvLocalAllowlistWriter(
            embedding_env_path,
            allowed_keys=_EMBEDDING_ENV_KEYS,
        )
        self._idempotency: dict[str, Any] = {}

    def load_llm_settings(self, provider: str | None = None) -> dict[str, Any]:
        config = self._client.config_get(paths=("agents.defaults.model", "models.providers"))
        status: Mapping[str, Any] = {}
        draft = self._map_openclaw_config_to_draft(config=config, provider=provider, status=status)
        draft["embedding"] = self._load_embedding_draft()
        return {
            "draft": draft,
            "schemaVersion": _opaque_version("llm-settings-v2"),
            "settingsVersion": _opaque_settings_version(config.get("revision")),
        }

    def save_llm_config_via_openclaw(
        self,
        draft: Mapping[str, Any],
        expected_settings_version: str,
        request_id: str,
    ) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        provider = str(draft.get("provider", "")).strip()
        default_model = str(draft.get("defaultModel", "")).strip()
        if not provider:
            raise UiBoundaryError("INVALID_INPUT", "请选择模型服务商。")
        if not default_model:
            raise UiBoundaryError("INVALID_INPUT", "请选择默认模型。")
        _ = self._client.config_schema_lookup(path="models.providers")
        _ = self._client.config_schema_lookup(path="agents.defaults.model")
        self._validate_embedding_draft(draft.get("embedding"))
        patch = self._build_config_patch(draft)
        result = self._client.config_patch(
            expected_settings_version=expected_settings_version,
            patch=patch,
        )
        if "embedding" in draft:
            self._save_embedding_config(draft.get("embedding"))
        out = {
            "status": "saved",
            "updatedAt": _now_iso(),
            "settingsVersion": _opaque_settings_version(result.get("newHash") or result.get("new_hash")),
        }
        self._idempotency[request_id] = out
        return out

    def test_llm_via_openclaw(self, input_data: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        provider = str(input_data.get("provider", "")).strip()
        if not provider:
            raise UiBoundaryError("INVALID_INPUT", "请选择模型服务商。")
        status = self._client.models_auth_status(
            provider=provider,
            model=input_data.get("model"),
            endpoint_url=input_data.get("endpointUrl"),
            probe=True,
        )
        ok = bool(status.get("ok"))
        out = {
            "ok": ok,
            "userMessage": "连接测试通过。" if ok else _normalize_failure_message(status),
            "checkedAt": _now_iso(),
        }
        self._idempotency[request_id] = out
        return out

    def _map_openclaw_config_to_draft(
        self,
        *,
        config: Mapping[str, Any],
        provider: str | None,
        status: Mapping[str, Any],
    ) -> dict[str, Any]:
        root = _config_root(config)
        resolved_provider = str(provider or root.get("active_provider") or "deepseek")
        provider_item = _provider_config(root, resolved_provider)
        model = _model_name_from_config(root, provider_item)
        api_key = str(provider_item.get("api_key", "")).strip()
        return {
            "provider": resolved_provider,
            "apiKeyMasked": _mask_secret(api_key),
            "endpointUrl": _optional_str(provider_item.get("endpoint_url") or provider_item.get("base_url")),
            "defaultModel": model or str(status.get("defaultModel", "unknown")),
            "status": "saved",
            "lastTestMessage": _optional_str(status.get("message")),
            "updatedAt": _now_iso(),
        }

    def _build_config_patch(self, draft: Mapping[str, Any]) -> dict[str, Any]:
        provider = str(draft["provider"]).strip()
        provider_patch: dict[str, Any] = {
            "endpoint_url": _optional_str(draft.get("endpointUrl")),
            "default_model": str(draft.get("defaultModel", "")).strip(),
        }
        api_key_replacement = str(draft.get("apiKeyReplacement", "")).strip()
        if api_key_replacement:
            provider_patch["api_key"] = api_key_replacement
        return {
            "agents": {"defaults": {"model": str(draft.get("defaultModel", "")).strip()}},
            "models": {"providers": {provider: provider_patch}},
        }

    def _load_embedding_draft(self) -> dict[str, Any]:
        env_values = _read_env_file_values(self._embedding_env_path)
        provider = _configured_value(env_values, "OPENVIKING_EMBEDDING_PROVIDER")
        model = _configured_value(env_values, "OPENVIKING_EMBEDDING_MODEL")
        api_key = _configured_value(env_values, "OPENVIKING_EMBEDDING_API_KEY")
        return {
            "provider": provider,
            "model": model,
            "apiKeyMasked": _mask_secret(api_key),
            "endpointUrl": _configured_value(env_values, "OPENVIKING_EMBEDDING_API_BASE"),
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
            "OPENVIKING_EMBEDDING_API_BASE": str(raw.get("endpointUrl", "")).strip(),
            "OPENVIKING_EMBEDDING_DIMENSION": str(raw.get("dimension", "")).strip(),
        }
        if api_key_replacement or not provider:
            updates["OPENVIKING_EMBEDDING_API_KEY"] = api_key_replacement
        self._embedding_env_writer.write_allowed_env_keys(updates)


def _provider_config(config: Mapping[str, Any], provider: str) -> Mapping[str, Any]:
    models = config.get("models")
    if not isinstance(models, Mapping):
        return {}
    providers = models.get("providers")
    if not isinstance(providers, Mapping):
        return {}
    entry = providers.get(provider)
    return entry if isinstance(entry, Mapping) else {}


def _config_root(config: Mapping[str, Any]) -> Mapping[str, Any]:
    parsed = config.get("parsed")
    if isinstance(parsed, Mapping):
        return parsed
    nested = config.get("config")
    if isinstance(nested, Mapping):
        return nested
    return config


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
    return _optional_str(provider_item.get("default_model")) or ""


def _normalize_failure_message(status: Mapping[str, Any]) -> str:
    raw = _optional_str(status.get("userMessage")) or _optional_str(status.get("message"))
    return raw or "连接测试失败，请检查模型服务商配置后重试。"


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


def _strip_env_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def allowed_llm_embedding_env_keys() -> tuple[str, ...]:
    return _EMBEDDING_ENV_KEYS
