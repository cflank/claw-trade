from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Iterable

from claw_trade.data_gateway.models import (
    Market,
    PackDomain,
    PrioritySource,
    ProviderAdmissionStatus,
    ProviderCapability,
    ProviderKind,
    SourceRole,
)
from claw_trade.data_gateway.providers.catalog import ProviderCatalog


def _source_role_rank(role: SourceRole) -> int:
    if role == SourceRole.OFFICIAL_ORIGINAL:
        return 0
    return 1


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
    )


@dataclass
class ProviderRegistry:
    _capabilities: list[ProviderCapability]

    def __init__(self, capabilities: Iterable[ProviderCapability] = ()) -> None:
        self._capabilities = list(capabilities)

    @classmethod
    def from_catalog(
        cls,
        catalog: ProviderCatalog,
        *,
        system_capabilities: Iterable[ProviderCapability] = (),
    ) -> "ProviderRegistry":
        caps = list(system_capabilities)
        for manifest in catalog.enabled_candidates():
            if manifest.admission_status != ProviderAdmissionStatus.ENABLED_CANDIDATE:
                continue
            caps.append(_manifest_to_capability(manifest))
        return cls(capabilities=caps)

    def register_capability(self, capability: ProviderCapability) -> None:
        self._capabilities.append(capability)

    def capabilities_for(self, *, market: Market, domain: PackDomain) -> tuple[ProviderCapability, ...]:
        candidates = [item for item in self._capabilities if item.market == market and item.domain == domain]
        return tuple(self._ordered(candidates))

    def _ordered(self, candidates: list[ProviderCapability]) -> list[ProviderCapability]:
        buckets: dict[tuple[SourceRole, str | None], list[ProviderCapability]] = {}
        for item in candidates:
            buckets.setdefault((item.source_role, item.coverage_group), []).append(item)

        ordered: list[ProviderCapability] = []
        for bucket_key in sorted(buckets, key=lambda key: (_source_role_rank(key[0]), key[0].value, key[1] or "")):
            items = buckets[bucket_key]
            items.sort(key=lambda cap: (cap.priority, cap.adapter_id))
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
    ) -> None:
        updated: list[ProviderCapability] = []
        for cap in self._capabilities:
            if (
                cap.adapter_id == adapter_id
                and cap.market == market
                and cap.domain == domain
                and cap.source_role == source_role
                and cap.coverage_group == coverage_group
                and cap.source_role != SourceRole.OFFICIAL_ORIGINAL
            ):
                updated.append(replace(cap, priority_source=PrioritySource.USER_PREFERRED))
            else:
                updated.append(cap)
        self._capabilities = updated
