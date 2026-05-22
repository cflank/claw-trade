from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha1
from typing import Any, Callable, Mapping
from uuid import uuid4

from claw_trade.ui_backend.settings_service import EnvLocalAllowlistWriter, UiBoundaryError
from claw_trade.ui_contracts.scope_guard import FirstVersionScopeError
from claw_trade.ui_contracts.scope_guard import assert_data_source_type_supported

SUPPORTED_DATA_SOURCE_TYPES: tuple[str, ...] = (
    "tushare",
    "bocha_news",
    "tavily_news",
    "jina_news",
    "newsnow_news",
    "minimax_news",
    "coingecko",
    "coinglass",
    "fred",
    "defillama",
    "cmc",
    "cryptoquant",
    "etherscan",
    "thegraph",
    "tavily_search",
    "exa_search",
    "brave_search",
    "bocha_search",
    "newsapi",
    "serpapi",
)

_DATA_SOURCE_ENV_KEY_MAP: dict[str, dict[str, str]] = {
    "tushare": {"api_key": "TUSHARE_TOKEN", "endpoint_url": "TUSHARE_HTTP_URL"},
    "coingecko": {"api_key": "COINGECKO_PRO_API_KEY", "proxy_url": "COINGECKO_PROXY_URL"},
    "coinglass": {
        "api_key": "COINGLASS_API_KEY",
        "endpoint_url": "COINGLASS_API_BASE",
        "header_name": "COINGLASS_API_HEADER_NAME",
    },
    "bocha_news": {"api_key": "CN_A_NEWS_BOCHA_API_KEY"},
    "tavily_news": {"api_key": "CN_A_NEWS_TAVILY_API_KEY"},
    "jina_news": {"api_key": "JINA_API_KEY"},
    "newsnow_news": {"api_key": "CN_A_NEWS_NEWSNOW_API_KEY"},
    "minimax_news": {
        "api_key": "CN_A_NEWS_MINIMAX_API_KEY",
        "endpoint_url": "CN_A_NEWS_MINIMAX_BASE_URL",
    },
    "tavily_search": {"api_key": "TAVILY_API_KEY"},
    "exa_search": {"api_key": "EXA_API_KEY"},
    "brave_search": {"api_key": "BRAVE_SEARCH_API_KEY"},
    "bocha_search": {"api_key": "BOCHA_API_KEY"},
    "newsapi": {"api_key": "NEWSAPI_API_KEY"},
    "serpapi": {"api_key": "SERPAPI_API_KEY"},
}

_DATA_SOURCE_ENV_ALLOWLIST: tuple[str, ...] = tuple(
    sorted({value for item in _DATA_SOURCE_ENV_KEY_MAP.values() for value in item.values()})
)


@dataclass(frozen=True)
class DataSourceInstanceForUser:
    instance_id: str
    supported_type: str
    group: str
    display_name: str
    enabled: bool
    api_key_masked: str | None
    endpoint_url: str | None
    proxy_url: str | None
    header_name: str | None
    priority: int
    state: str
    last_success_at: str | None
    last_test_at: str | None

    def to_user_dict(self) -> dict[str, Any]:
        return {
            "instanceId": self.instance_id,
            "supportedType": self.supported_type,
            "group": self.group,
            "displayName": self.display_name,
            "enabled": self.enabled,
            "apiKeyMasked": self.api_key_masked,
            "endpointUrl": self.endpoint_url,
            "proxyUrl": self.proxy_url,
            "headerName": self.header_name,
            "priority": self.priority,
            "state": self.state,
            "lastSuccessAt": self.last_success_at,
            "lastTestAt": self.last_test_at,
        }


@dataclass(frozen=True)
class DataSourceTestResult:
    state: str
    health_event: dict[str, Any]
    can_enable: bool

    def to_user_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "healthEvent": self.health_event,
            "canEnable": self.can_enable,
        }


class InMemoryDataSourceStore:
    def __init__(self) -> None:
        self._items: dict[str, dict[str, Any]] = {}

    def get(self, instance_id: str) -> dict[str, Any] | None:
        return self._items.get(instance_id)

    def list_instances(self) -> tuple[dict[str, Any], ...]:
        return tuple(self._items.values())

    def upsert(self, record: Mapping[str, Any]) -> dict[str, Any]:
        instance_id = str(record.get("id") or record.get("instance_id") or uuid4().hex)
        saved = dict(record)
        saved["id"] = instance_id
        self._items[instance_id] = saved
        return saved


class InMemorySecretStore:
    def __init__(self) -> None:
        self._values: dict[str, str] = {}

    def replace(self, *, old_ref: str | None, value: str, scope: str) -> str:
        _ = old_ref
        ref = f"{scope}:{sha1(value.encode('utf-8')).hexdigest()[:12]}"
        self._values[ref] = value
        return ref


class DataSourceSettingsService:
    def __init__(
        self,
        *,
        data_source_store: InMemoryDataSourceStore | None = None,
        secret_store: InMemorySecretStore | None = None,
        env_writer: EnvLocalAllowlistWriter | None = None,
        health_tester: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> None:
        self._store = data_source_store or InMemoryDataSourceStore()
        self._secret_store = secret_store or InMemorySecretStore()
        self._env_writer = env_writer
        self._health_tester = health_tester or _default_health_tester
        self._idempotency: dict[str, Any] = {}

    def list_data_sources(self) -> dict[str, Any]:
        instances = [to_data_source_instance_for_user(item).to_user_dict() for item in self._store.list_instances()]
        return {
            "supportedTypes": SUPPORTED_DATA_SOURCE_TYPES,
            "instances": instances,
        }

    def test_data_source_instance(self, instance_draft: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        normalized = self._validate_supported_input(instance_draft)
        existing = self._store.get(normalized["instanceId"]) if normalized.get("instanceId") else None
        out = self._probe_health(
            normalized,
            requires_key=self._resolve_requires_key(normalized=normalized, existing=existing),
            has_existing_credential=self._has_credential_ref(existing),
        )
        self._idempotency[request_id] = out
        return out

    def save_data_source_instance(self, data: Mapping[str, Any], request_id: str) -> dict[str, Any]:
        if request_id in self._idempotency:
            return self._idempotency[request_id]
        normalized = self._validate_supported_input(data)
        existing = self._store.get(normalized["instanceId"]) if normalized.get("instanceId") else None
        credential_ref = str(existing.get("credential_ref")) if existing and existing.get("credential_ref") else None
        api_key_replacement = str(data.get("apiKeyReplacement", "")).strip()
        if api_key_replacement:
            credential_ref = self._secret_store.replace(
                old_ref=credential_ref,
                value=api_key_replacement,
                scope="data_source",
            )
        enabled = bool(normalized["enabled"])
        state = str(normalized["state"])
        requires_key = self._resolve_requires_key(normalized=normalized, existing=existing)
        if enabled:
            self._probe_health(
                normalized,
                requires_key=requires_key,
                has_existing_credential=bool(credential_ref),
            )
            state = "validated"
        saved = self._store.upsert(
            {
                "id": normalized.get("instanceId") or None,
                "supported_type": normalized["supportedType"],
                "group": normalized["group"],
                "display_name": normalized["displayName"],
                "enabled": enabled,
                "credential_ref": credential_ref,
                "endpoint_url": normalized["endpointUrl"],
                "proxy_url": normalized["proxyUrl"],
                "header_name": normalized["headerName"],
                "priority": int(normalized["priority"]),
                "state": state,
                "last_success_at": normalized["lastSuccessAt"],
                "last_test_at": normalized["lastTestAt"] or _now_iso(),
                "requires_key": requires_key,
            }
        )
        self._write_env_updates(saved, api_key_replacement)
        out = to_data_source_instance_for_user(saved).to_user_dict()
        self._idempotency[request_id] = out
        return out

    def _validate_supported_input(self, data: Mapping[str, Any]) -> dict[str, Any]:
        supported_type = str(data.get("supportedType", "")).strip()
        if not supported_type:
            raise UiBoundaryError("INVALID_INPUT", "数据源类型不能为空。")
        try:
            assert_data_source_type_supported(supported_type)
        except FirstVersionScopeError as exc:
            raise UiBoundaryError("INVALID_INPUT", str(exc)) from exc
        if supported_type not in SUPPORTED_DATA_SOURCE_TYPES:
            raise UiBoundaryError("INVALID_INPUT", "当前只支持已内置的数据源类型。")
        if data.get("customJsonMapping") or data.get("custom_json_mapping") or data.get("customScript"):
            raise UiBoundaryError("INVALID_INPUT", "当前不支持自定义数据映射或脚本。")
        return {
            "instanceId": str(data.get("instanceId", "")).strip() or None,
            "supportedType": supported_type,
            "group": str(data.get("group", "custom")).strip() or "custom",
            "displayName": str(data.get("displayName", supported_type)).strip() or supported_type,
            "enabled": bool(data.get("enabled", False)),
            "endpointUrl": _optional_str(data.get("endpointUrl")),
            "proxyUrl": _optional_str(data.get("proxyUrl")),
            "headerName": _optional_str(data.get("headerName")),
            "priority": int(data.get("priority", 100)),
            "state": str(data.get("state", "draft")).strip() or "draft",
            "lastSuccessAt": _optional_str(data.get("lastSuccessAt")),
            "lastTestAt": _optional_str(data.get("lastTestAt")),
            "requiresKey": bool(data.get("requiresKey", True)),
            "apiKeyReplacement": _optional_str(data.get("apiKeyReplacement")),
        }

    def _probe_health(
        self,
        normalized: Mapping[str, Any],
        *,
        requires_key: bool,
        has_existing_credential: bool,
    ) -> dict[str, Any]:
        self._require_probe_key(
            normalized=normalized,
            requires_key=requires_key,
            has_existing_credential=has_existing_credential,
        )
        try:
            health = self._health_tester(normalized)
            status = str(health.get("status", "")).strip().lower()
            if status not in {"validated", "enabled_candidate", "enabled"}:
                message = str(health.get("message", "数据源测试未通过。")).strip() or "数据源测试未通过。"
                raise UiBoundaryError("DATASOURCE_TEST_FAILED", message)
            return DataSourceTestResult(
                state="validated",
                health_event=to_data_source_health_event_for_user(
                    {
                        "displayName": normalized["displayName"],
                        "status": status,
                        "userMessage": str(health.get("message", "连接测试通过。")),
                        "impact": str(health.get("impact", "low")),
                        "occurredAt": _now_iso(),
                    }
                ),
                can_enable=True,
            ).to_user_dict()
        except UiBoundaryError:
            raise
        except Exception as exc:  # pragma: no cover - 防御性路径
            raise UiBoundaryError("DATASOURCE_TEST_FAILED", _probe_error_message(exc)) from exc

    @staticmethod
    def _resolve_requires_key(*, normalized: Mapping[str, Any], existing: Mapping[str, Any] | None) -> bool:
        if existing is not None and "requires_key" in existing:
            return bool(existing.get("requires_key"))
        if existing is not None and "requiresKey" in existing:
            return bool(existing.get("requiresKey"))
        return bool(normalized.get("requiresKey", True))

    @staticmethod
    def _has_credential_ref(existing: Mapping[str, Any] | None) -> bool:
        if existing is None:
            return False
        return _optional_str(existing.get("credential_ref")) is not None

    @staticmethod
    def _require_probe_key(
        *,
        normalized: Mapping[str, Any],
        requires_key: bool,
        has_existing_credential: bool,
    ) -> None:
        if not requires_key:
            return
        if has_existing_credential:
            return
        if _optional_str(normalized.get("apiKeyReplacement")):
            return
        raise UiBoundaryError("INVALID_INPUT", "需要先填写密钥，再进行测试或启用。")

    def _write_env_updates(self, saved: Mapping[str, Any], api_key_replacement: str) -> None:
        if self._env_writer is None:
            return
        mapping = _DATA_SOURCE_ENV_KEY_MAP.get(str(saved["supported_type"]), {})
        updates: dict[str, str] = {}
        if api_key_replacement and mapping.get("api_key"):
            updates[mapping["api_key"]] = api_key_replacement
        endpoint = _optional_str(saved.get("endpoint_url"))
        proxy = _optional_str(saved.get("proxy_url"))
        header = _optional_str(saved.get("header_name"))
        if endpoint and mapping.get("endpoint_url"):
            updates[mapping["endpoint_url"]] = endpoint
        if proxy and mapping.get("proxy_url"):
            updates[mapping["proxy_url"]] = proxy
        if header and mapping.get("header_name"):
            updates[mapping["header_name"]] = header
        if updates:
            self._env_writer.write_allowed_env_keys(updates)


def to_data_source_instance_for_user(instance: Mapping[str, Any]) -> DataSourceInstanceForUser:
    credential_ref = _optional_str(instance.get("credential_ref"))
    return DataSourceInstanceForUser(
        instance_id=str(instance.get("id", "")),
        supported_type=str(instance.get("supported_type", "")),
        group=str(instance.get("group", "custom")),
        display_name=str(instance.get("display_name", "")),
        enabled=bool(instance.get("enabled", False)),
        api_key_masked=mask_secret_ref(credential_ref),
        endpoint_url=_optional_str(instance.get("endpoint_url")),
        proxy_url=_optional_str(instance.get("proxy_url")),
        header_name=_optional_str(instance.get("header_name")),
        priority=int(instance.get("priority", 100)),
        state=str(instance.get("state", "draft")),
        last_success_at=_optional_str(instance.get("last_success_at")),
        last_test_at=_optional_str(instance.get("last_test_at")),
    )


def to_data_source_health_event_for_user(event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "displayName": str(event.get("displayName", "")),
        "status": str(event.get("status", "unknown")),
        "userMessage": str(event.get("userMessage", "")),
        "impact": str(event.get("impact", "medium")),
        "occurredAt": _optional_str(event.get("occurredAt")) or _now_iso(),
    }


def mask_secret_ref(secret_ref: str | None) -> str | None:
    if not secret_ref:
        return None
    suffix = secret_ref[-4:] if len(secret_ref) >= 4 else secret_ref
    return f"***{suffix}"


def allowed_data_source_env_keys() -> tuple[str, ...]:
    return _DATA_SOURCE_ENV_ALLOWLIST


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _default_health_tester(_instance: Mapping[str, Any]) -> Mapping[str, Any]:
    raise UiBoundaryError("DATASOURCE_TEST_FAILED", "当前环境未接入可用的数据源测试能力。")


def _probe_error_message(exc: Exception) -> str:
    text = str(exc).lower()
    if "credential_missing" in text:
        return "密钥未配置，请先补全后再测试。"
    if "probe_not_supported" in text:
        return "当前数据源类型暂不支持实时测试，请先保持禁用。"
    return "数据源连接或鉴权失败，请到设置页更新后重试。"
