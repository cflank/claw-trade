from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from claw_trade.data_gateway.models import (
    DeclarativeProviderManifest,
    HttpVisibility,
    Market,
    PackDomain,
    PrioritySource,
    ProviderAdmissionStatus,
    ProviderCapability,
    ProviderKind,
    SourceRole,
    source_role_is_discovery_only,
)
from claw_trade.data_gateway.providers.catalog import ProviderCatalog


def _source_role_rank(role: SourceRole) -> int:
    if role == SourceRole.OFFICIAL_ORIGINAL:
        return 0
    return 1


def _normalized_data_type(capability: ProviderCapability) -> str:
    data_type = (capability.data_type or "").strip()
    if data_type:
        return data_type
    coverage_group = (capability.coverage_group or "").strip()
    if coverage_group:
        return coverage_group
    schema_id = capability.expected_schema_id.strip()
    if schema_id:
        base, marker, version = schema_id.rpartition(".v")
        if marker and version.isdigit() and base:
            return base
        return schema_id
    return f"{capability.domain.value}.{capability.endpoint}"


def _default_coverage_fields(capability: ProviderCapability) -> tuple[str, ...]:
    if capability.domain == PackDomain.MARKET and capability.market == Market.CN_A and capability.endpoint.startswith("stock_board_"):
        return ("板块名称", "板块代码")
    if capability.domain == PackDomain.MARKET:
        return ("open", "high", "low", "close", "volume")
    if capability.domain == PackDomain.FUNDAMENTAL:
        return ("as_of",)
    if capability.domain in {PackDomain.NEWS, PackDomain.SOCIAL, PackDomain.POLICY}:
        return ("title", "url", "published_at")
    if capability.domain in {PackDomain.HOT_MONEY, PackDomain.LOCKUP}:
        return ("as_of",)
    return ()


def _default_coverage_symbols(capability: ProviderCapability) -> str:
    if capability.endpoint.endswith("_batch") or "selection_batch" in capability.endpoint:
        return "configured_universe"
    if capability.market == Market.CRYPTO:
        return "single_symbol_or_pair"
    return "single_symbol"


def _default_freshness_supported(capability: ProviderCapability) -> tuple[str, ...]:
    if capability.domain in {PackDomain.MARKET, PackDomain.HOT_MONEY, PackDomain.LOCKUP}:
        return ("trading_day",)
    if capability.domain in {PackDomain.NEWS, PackDomain.SOCIAL, PackDomain.POLICY}:
        return ("intraday",)
    if capability.domain == PackDomain.FUNDAMENTAL:
        return ("filing_cycle",)
    return ()


def _default_http_visibility(capability: ProviderCapability) -> HttpVisibility:
    normalized_hint = f"{capability.provider}:{capability.adapter_id}:{capability.endpoint}".lower()
    if "mootdx" in normalized_hint:
        return HttpVisibility.NO_HTTP
    return HttpVisibility.SDK_INTERNAL_UNKNOWN


def normalize_provider_capability_policy(capability: ProviderCapability) -> ProviderCapability:
    discovery_only = source_role_is_discovery_only(capability.source_role)
    can_be_formal_fact_source = capability.can_be_formal_fact_source
    if can_be_formal_fact_source is None:
        can_be_formal_fact_source = not discovery_only
    if discovery_only:
        can_be_formal_fact_source = False

    can_enter_worker_pack = capability.can_enter_worker_pack
    if can_enter_worker_pack is None:
        can_enter_worker_pack = not discovery_only
    if discovery_only:
        can_enter_worker_pack = False

    http_visibility = capability.http_visibility
    if http_visibility is None:
        http_visibility = _default_http_visibility(capability)

    return replace(
        capability,
        data_type=_normalized_data_type(capability),
        coverage_fields=capability.coverage_fields or _default_coverage_fields(capability),
        coverage_symbols=capability.coverage_symbols or _default_coverage_symbols(capability),
        freshness_supported=capability.freshness_supported or _default_freshness_supported(capability),
        user_config_key=capability.user_config_key or (capability.credential_requirements[0] if capability.credential_requirements else None),
        can_be_formal_fact_source=can_be_formal_fact_source,
        can_enter_worker_pack=can_enter_worker_pack,
        http_visibility=http_visibility,
        live_fresh_required_for_ui=False if discovery_only else capability.live_fresh_required_for_ui,
    )


def _manifest_to_capability(manifest) -> ProviderCapability:
    return ProviderCapability(
        provider=manifest.provider_id,
        adapter_id=manifest.adapter_id,
        provider_kind=ProviderKind.USER_DECLARATIVE,
        market=manifest.markets[0],
        domain=manifest.domains[0],
        endpoint=manifest.endpoints[0],
        source_role=manifest.source_role,
        expected_schema_id=manifest.expected_schema_id,
        license_policy_id=manifest.license_policy_id,
        credential_requirements=manifest.credential_requirements,
        rate_limit_policy_id=manifest.rate_limit_policy_id,
        cache_ttl_seconds=manifest.cache_ttl_seconds,
        required=True,
        attempt_required=True,
        coverage_group=manifest.coverage_group,
        coverage_quorum=manifest.coverage_quorum,
        priority=manifest.priority,
        priority_source=manifest.priority_source,
        raw_export_policy=manifest.raw_export_policy,
    )


@dataclass
class ProviderRegistry:
    _capabilities: list[ProviderCapability]

    def __init__(self, capabilities: Iterable[ProviderCapability] = ()) -> None:
        self._capabilities = [normalize_provider_capability_policy(item) for item in capabilities]

    @classmethod
    def from_catalog(
        cls,
        catalog: ProviderCatalog,
        *,
        system_capabilities: Iterable[ProviderCapability] = (),
    ) -> "ProviderRegistry":
        return cls.from_enabled_manifests(
            enabled_manifests=catalog.enabled_candidates(),
            system_capabilities=system_capabilities,
        )

    @classmethod
    def from_enabled_manifests(
        cls,
        *,
        enabled_manifests: Iterable[DeclarativeProviderManifest],
        system_capabilities: Iterable[ProviderCapability] = (),
    ) -> "ProviderRegistry":
        caps = list(system_capabilities)
        for manifest in enabled_manifests:
            if manifest.admission_status != ProviderAdmissionStatus.ENABLED_CANDIDATE:
                continue
            if not manifest.enabled:
                continue
            caps.append(_manifest_to_capability(manifest))
        return cls(capabilities=caps)

    def register_capability(self, capability: ProviderCapability) -> None:
        self._capabilities.append(normalize_provider_capability_policy(capability))

    def all_capabilities(self) -> tuple[ProviderCapability, ...]:
        return tuple(self._capabilities)

    def capabilities_for(self, *, market: Market, domain: PackDomain) -> tuple[ProviderCapability, ...]:
        candidates = [item for item in self._capabilities if item.market == market and item.domain == domain]
        return tuple(self._ordered(candidates))

    def capabilities_for_data_type(self, *, market: Market, domain: PackDomain, data_type: str) -> tuple[ProviderCapability, ...]:
        normalized_data_type = data_type.strip()
        if not normalized_data_type:
            raise ValueError("data_type must not be empty")
        candidates = [
            item
            for item in self._capabilities
            if item.market == market and item.domain == domain and _normalized_data_type(item) == normalized_data_type
        ]
        return tuple(self._ordered(candidates))

    def _ordered(self, candidates: list[ProviderCapability]) -> list[ProviderCapability]:
        buckets: dict[tuple[str, SourceRole, str | None], list[ProviderCapability]] = {}
        for item in candidates:
            buckets.setdefault((_normalized_data_type(item), item.source_role, item.coverage_group), []).append(item)

        ordered: list[ProviderCapability] = []
        for bucket_key in sorted(
            buckets,
            key=lambda key: (
                key[0],
                _source_role_rank(key[1]),
                key[1].value,
                key[2] or "",
            ),
        ):
            items = buckets[bucket_key]
            items.sort(key=lambda cap: (cap.priority, cap.adapter_id, cap.endpoint))
            preferred = [item for item in items if item.priority_source == PrioritySource.USER_PREFERRED]
            defaults = [item for item in items if item.priority_source != PrioritySource.USER_PREFERRED]
            ordered.extend(preferred + defaults)
        return ordered

    def apply_user_preferred(
        self,
        *,
        adapter_id: str,
        market: Market,
        domain: PackDomain,
        source_role: SourceRole,
        coverage_group: str | None,
        data_type: str | None = None,
    ) -> None:
        selected_data_type = (data_type or "").strip()
        if selected_data_type and not any(
            cap.adapter_id == adapter_id
            and cap.market == market
            and cap.domain == domain
            and cap.source_role == source_role
            and cap.coverage_group == coverage_group
            and _normalized_data_type(cap) == selected_data_type
            for cap in self._capabilities
        ):
            raise ValueError(f"capability_not_found_for_data_type:{adapter_id}:{selected_data_type}")

        target_data_types = {
            _normalized_data_type(cap)
            for cap in self._capabilities
            if cap.adapter_id == adapter_id
            and cap.market == market
            and cap.domain == domain
            and cap.source_role == source_role
            and cap.coverage_group == coverage_group
        }
        if selected_data_type:
            target_data_types = {selected_data_type}
        elif len(target_data_types) > 1:
            raise ValueError(f"user_preferred_requires_data_type:{adapter_id}")

        updated: list[ProviderCapability] = []
        for cap in self._capabilities:
            if (
                cap.adapter_id == adapter_id
                and cap.market == market
                and cap.domain == domain
                and cap.source_role == source_role
                and cap.coverage_group == coverage_group
                and _normalized_data_type(cap) in target_data_types
                and cap.source_role != SourceRole.OFFICIAL_ORIGINAL
            ):
                updated.append(replace(cap, priority_source=PrioritySource.USER_PREFERRED))
            else:
                updated.append(cap)
        self._capabilities = updated
