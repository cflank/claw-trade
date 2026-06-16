from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Iterable

from .base import (
    CapabilityError,
    ProviderCapabilityView,
    ProviderPlugin,
    validate_provider_capabilities,
)


def _read_attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
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


@dataclass(frozen=True)
class CapabilitySnapshot:
    _capabilities: tuple[ProviderCapabilityView, ...]
    _index: dict[tuple[str, str, str, str], ProviderCapabilityView]

    @classmethod
    def from_capabilities(cls, capabilities: Iterable[ProviderCapabilityView]) -> "CapabilitySnapshot":
        caps_tuple = tuple(capabilities)
        index: dict[tuple[str, str, str, str], ProviderCapabilityView] = {}
        for cap in caps_tuple:
            key = (cap.provider_id, cap.endpoint_id, cap.market, cap.data_type)
            index[key] = cap
        return cls(_capabilities=caps_tuple, _index=index)

    def get(
        self,
        provider_id: str,
        endpoint_id: str,
        *,
        market: str | None = None,
        data_type: str | None = None,
    ) -> ProviderCapabilityView:
        matches = [
            cap
            for cap in self._capabilities
            if cap.provider_id == provider_id
            and cap.endpoint_id == endpoint_id
            and (market is None or cap.market == market)
            and (data_type is None or cap.data_type == data_type)
        ]
        if not matches:
            raise CapabilityError(f"capability_not_found:{provider_id}:{endpoint_id}:{market}:{data_type}")
        if len(matches) > 1:
            raise CapabilityError(f"capability_ambiguous:{provider_id}:{endpoint_id}:{market}:{data_type}")
        return matches[0]

    def list(self) -> tuple[ProviderCapabilityView, ...]:
        return self._capabilities


class ProviderRegistry:
    def __init__(self) -> None:
        self._plugins: dict[str, ProviderPlugin] = {}
        self._capabilities: list[ProviderCapabilityView] = []
        self._market_data_index: dict[tuple[str, str], list[ProviderCapabilityView]] = defaultdict(list)
        self._endpoint_index: set[tuple[str, str, str, str]] = set()

    def register(self, plugin: ProviderPlugin) -> None:
        caps = plugin.capabilities()
        validate_provider_capabilities(caps)

        provider_id = _read_attr(caps, "provider_id")
        plugin_version = _read_attr(caps, "plugin_version")
        default_priority_rank = int(_read_attr(caps, "default_priority_rank", 100))
        credentials = _read_attr(caps, "credentials")
        if credentials is None:
            credentials = _read_attr(caps, "credential_policy")
        credential_required = bool(_read_attr(credentials, "credential_required", False))
        credential_names = tuple(str(name) for name in _as_tuple(_read_attr(credentials, "credential_names", ())))
        credential_scope = _read_attr(credentials, "credential_scope", None)
        provider_license_policy = _read_attr(caps, "license_policy")
        default_rate_limit_policy = _read_attr(caps, "default_rate_limit_policy")
        endpoints = _as_tuple(_read_attr(caps, "endpoints", ()))

        if provider_id in self._plugins:
            raise CapabilityError(f"duplicate_provider:{provider_id}")

        flattened: list[ProviderCapabilityView] = []
        for endpoint in endpoints:
            endpoint_id = _read_attr(endpoint, "endpoint_id")
            market = _read_attr(endpoint, "market")
            data_type = _read_attr(endpoint, "data_type")
            source_role = _read_attr(endpoint, "source_role")
            priority_rank = _read_attr(endpoint, "priority_rank", None)
            if priority_rank is None:
                priority_rank = default_priority_rank
            priority_rank = int(priority_rank)
            if priority_rank < 0:
                raise CapabilityError(f"priority_rank 必须非负: {provider_id}/{endpoint_id}")

            can_be_formal_fact_source = _read_attr(endpoint, "can_be_formal_fact_source", None)
            if can_be_formal_fact_source is None:
                can_be_formal_fact_source = source_role not in {"discovery", "event_expectation"}

            dedupe_key = (provider_id, endpoint_id, market, data_type)
            if dedupe_key in self._endpoint_index:
                raise CapabilityError(f"duplicate endpoint capability: {dedupe_key}")

            supported_granularities = _as_tuple(_read_attr(endpoint, "supported_granularities", ()))
            if not supported_granularities:
                supported_granularities = _as_tuple(_read_attr(endpoint, "granularity", ()))
            fields = _as_tuple(_read_attr(endpoint, "fields", ()))

            cap = ProviderCapabilityView(
                provider_id=provider_id,
                plugin_version=plugin_version,
                endpoint_id=endpoint_id,
                market=market,
                data_type=data_type,
                source_role=source_role,
                supported_granularities=tuple(supported_granularities),
                fields=tuple(fields),
                priority_rank=priority_rank,
                credential_required=credential_required,
                credential_names=credential_names,
                credential_scope=credential_scope,
                http_visibility=str(_read_attr(endpoint, "http_visibility")),
                can_be_formal_fact_source=bool(can_be_formal_fact_source),
                license_policy=_read_attr(endpoint, "license_policy", provider_license_policy),
                rate_limit_policy=_read_attr(endpoint, "rate_limit_policy", default_rate_limit_policy),
                batch_policy=_read_attr(endpoint, "batch_policy"),
            )
            flattened.append(cap)

        self._plugins[provider_id] = plugin
        for cap in flattened:
            self._capabilities.append(cap)
            self._market_data_index[(cap.market, cap.data_type)].append(cap)
            self._endpoint_index.add((cap.provider_id, cap.endpoint_id, cap.market, cap.data_type))

    def get(self, provider_id: str) -> ProviderPlugin:
        try:
            return self._plugins[provider_id]
        except KeyError as exc:
            raise CapabilityError(f"provider_not_registered:{provider_id}") from exc

    def list_capabilities(self, market: str, data_type: str) -> tuple[ProviderCapabilityView, ...]:
        caps = self._market_data_index.get((market, data_type), [])
        ordered = sorted(caps, key=lambda cap: (cap.priority_rank, cap.provider_id, cap.endpoint_id))
        return tuple(ordered)

    def list_all_capabilities(self) -> tuple[ProviderCapabilityView, ...]:
        return tuple(
            sorted(
                self._capabilities,
                key=lambda cap: (
                    cap.market,
                    cap.provider_id,
                    cap.data_type,
                    cap.endpoint_id,
                    cap.priority_rank,
                ),
            )
        )

    def read_capabilities(self, provider_ids: Iterable[str]) -> CapabilitySnapshot:
        provider_id_set = set(provider_ids)
        caps = [cap for cap in self._capabilities if cap.provider_id in provider_id_set]
        return CapabilitySnapshot.from_capabilities(caps)
