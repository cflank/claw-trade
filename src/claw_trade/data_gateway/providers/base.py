from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, runtime_checkable

SUPPORTED_MARKETS = frozenset({"CN_A", "US", "HK", "CRYPTO"})
NON_FORMAL_SOURCE_ROLES = frozenset({"discovery", "event_expectation"})


class CapabilityError(ValueError):
    """Raised when provider capability declaration violates contract."""


def _read_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, Mapping):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _as_tuple(value: Any) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    if isinstance(value, list):
        return tuple(value)
    return (value,)


def validate_batch_policy(policy: Any) -> None:
    supports_batch = bool(_read_attr(policy, "supports_batch", False))
    batch_by = _read_attr(policy, "batch_by", "none")
    mergeable_fields = _as_tuple(_read_attr(policy, "mergeable_fields", ()))

    if not supports_batch:
        if batch_by != "none" or mergeable_fields:
            raise CapabilityError("不支持批量时 batch_by 必须为 none 且 mergeable_fields 为空")
        return

    if batch_by == "none":
        raise CapabilityError("支持批量时必须声明 batch_by")


def validate_credential_policy(policy: Any) -> None:
    required = bool(_read_attr(policy, "credential_required", False))
    names = _as_tuple(_read_attr(policy, "credential_names", ()))
    missing_behavior = _read_attr(policy, "missing_behavior", None)

    if required and not names:
        raise CapabilityError("credential_required=True 时必须声明 credential_names")
    if required and missing_behavior != "credential_missing":
        raise CapabilityError("缺凭证行为必须是 credential_missing")


def validate_license_policy(policy: Any) -> None:
    raw_storage_mode = _read_attr(policy, "raw_storage_mode", None)
    if raw_storage_mode not in {"store_full", "metadata_only", "no_store"}:
        raise CapabilityError("license_policy.raw_storage_mode 非法")


def validate_provider_capabilities(caps: Any) -> None:
    provider_id = _read_attr(caps, "provider_id", "")
    plugin_version = _read_attr(caps, "plugin_version", "")
    endpoints = _as_tuple(_read_attr(caps, "endpoints", ()))
    credentials = _read_attr(caps, "credentials", None)
    if credentials is None:
        credentials = _read_attr(caps, "credential_policy", None)
    license_policy = _read_attr(caps, "license_policy", None)

    if not provider_id:
        raise CapabilityError("provider_id 不能为空")
    if not plugin_version:
        raise CapabilityError("plugin_version 不能为空")
    if not endpoints:
        raise CapabilityError("endpoints 不能为空")
    if credentials is None:
        raise CapabilityError("credentials 不能为空")
    if license_policy is None:
        raise CapabilityError("license_policy 不能为空")

    validate_credential_policy(credentials)
    validate_license_policy(license_policy)

    seen: set[tuple[str, str, str]] = set()
    for endpoint in endpoints:
        endpoint_id = _read_attr(endpoint, "endpoint_id", "")
        market = _read_attr(endpoint, "market", "")
        data_type = _read_attr(endpoint, "data_type", "")
        source_role = _read_attr(endpoint, "source_role", "")
        supported_granularities = _as_tuple(_read_attr(endpoint, "supported_granularities", ()))
        if not supported_granularities:
            supported_granularities = _as_tuple(_read_attr(endpoint, "granularity", ()))
        http_visibility = _read_attr(endpoint, "http_visibility", None)
        batch_policy = _read_attr(endpoint, "batch_policy", None)
        can_be_formal_fact_source = _read_attr(endpoint, "can_be_formal_fact_source", None)

        if not endpoint_id:
            raise CapabilityError("endpoint_id 不能为空")
        if market not in SUPPORTED_MARKETS:
            raise CapabilityError(f"endpoint {endpoint_id} market 非法: {market}")
        if not data_type:
            raise CapabilityError(f"endpoint {endpoint_id} data_type 不能为空")
        if not source_role:
            raise CapabilityError(f"endpoint {endpoint_id} source_role 不能为空")
        if not supported_granularities:
            raise CapabilityError(f"endpoint {endpoint_id} supported_granularities 不能为空")
        if http_visibility not in {"managed_http", "sdk_internal_unknown", "no_http"}:
            raise CapabilityError(f"endpoint {endpoint_id} http_visibility 非法: {http_visibility}")
        if batch_policy is None:
            raise CapabilityError(f"endpoint {endpoint_id} 缺少 batch_policy")

        validate_batch_policy(batch_policy)

        if can_be_formal_fact_source is True and source_role in NON_FORMAL_SOURCE_ROLES:
            raise CapabilityError("discovery/event_expectation 不能作正式事实源")

        dedupe_key = (endpoint_id, market, data_type)
        if dedupe_key in seen:
            raise CapabilityError(f"duplicate endpoint capability: {provider_id}/{dedupe_key}")
        seen.add(dedupe_key)


def ensure_remote_success_is_auditable(fetch_result: Any, capability: Any) -> None:
    status = _read_attr(fetch_result, "status", None)
    if status != "success":
        return

    http_visibility = _read_attr(capability, "http_visibility", None)
    observations = _as_tuple(_read_attr(fetch_result, "http_observations", ()))
    if http_visibility == "sdk_internal_unknown" and not observations:
        raise CapabilityError("SDK HTTP 不可审计时不能包装成可审计 remote_success")


@runtime_checkable
class ProviderPlugin(Protocol):
    plugin_id: str
    version: str

    def capabilities(self) -> Any: ...

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]: ...

    def fetch(self, task: Any, ctx: Any) -> Any: ...


@dataclass(frozen=True)
class ProviderCapabilityView:
    provider_id: str
    plugin_version: str
    endpoint_id: str
    market: str
    data_type: str
    source_role: str
    supported_granularities: tuple[str, ...]
    fields: tuple[str, ...]
    priority_rank: int
    credential_required: bool
    credential_names: tuple[str, ...]
    credential_scope: str | None
    http_visibility: str
    can_be_formal_fact_source: bool
    license_policy: Any
    rate_limit_policy: Any
    batch_policy: Any

    @property
    def granularity(self) -> tuple[str, ...]:
        return self.supported_granularities
