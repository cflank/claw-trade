from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import UTC, datetime

from claw_trade.data_gateway.models import (
    ConsumerType,
    DataRequirement,
    RequestKind,
    RequiredLevel,
    RequirementBatch,
)


@dataclass(frozen=True)
class DataRequirementConsumer:
    consumer_type: ConsumerType
    consumer_id: str
    declared_data_needs: tuple[DataRequirement, ...]

    def __post_init__(self) -> None:
        if not self.consumer_id.strip():
            raise ValueError("consumer_id must not be empty")
        if not self.declared_data_needs:
            raise ValueError("declared_data_needs must not be empty")
        for requirement in self.declared_data_needs:
            if requirement.consumer_type != self.consumer_type:
                raise ValueError("consumer requirement consumer_type mismatch")
            if requirement.consumer_id != self.consumer_id:
                raise ValueError("consumer requirement consumer_id mismatch")


def collect_data_requirements(
    *,
    request_id: str,
    request_kind: RequestKind,
    consumers: tuple[DataRequirementConsumer, ...],
    batch_id: str | None = None,
    profile: str | None = None,
    created_at: datetime | None = None,
) -> RequirementBatch:
    if not request_id.strip():
        raise ValueError("request_id must not be empty")
    if not consumers:
        raise ValueError("collect_data_requirements requires consumers")
    requirements = tuple(
        requirement
        for consumer in consumers
        for requirement in consumer.declared_data_needs
    )
    duplicate_map = {requirement.requirement_id: (requirement.requirement_id,) for requirement in requirements}
    return RequirementBatch(
        batch_id=batch_id or f"{request_id}:{request_kind.value}:requirements",
        request_id=request_id,
        request_kind=request_kind,
        original_requirements=requirements,
        merged_requirements=requirements,
        duplicate_map=duplicate_map,
        created_at=created_at or datetime.now(UTC),
        profile=profile,
    )


def merge_duplicate_requirements(batch: RequirementBatch) -> RequirementBatch:
    groups: dict[tuple[object, ...], list[DataRequirement]] = {}
    for requirement in batch.original_requirements:
        groups.setdefault(_stable_requirement_key(requirement), []).append(requirement)

    merged: list[DataRequirement] = []
    duplicate_map: dict[str, tuple[str, ...]] = {}
    for requirements in groups.values():
        merged_requirement = _merge_group(tuple(requirements))
        merged.append(merged_requirement)
        duplicate_map[merged_requirement.requirement_id] = tuple(req.requirement_id for req in requirements)

    merged_sorted = tuple(sorted(merged, key=lambda req: req.requirement_id))
    return replace(batch, merged_requirements=merged_sorted, duplicate_map=duplicate_map)


def _stable_requirement_key(requirement: DataRequirement) -> tuple[object, ...]:
    return (
        requirement.market,
        requirement.data_type,
        requirement.granularity,
        requirement.ticker,
        requirement.universe_ref,
        requirement.date_range,
        requirement.lookback_window_days,
        requirement.current_date,
        requirement.domain,
        requirement.source_role_required,
        tuple(sorted(requirement.field_set)),
    )


def _merge_group(requirements: tuple[DataRequirement, ...]) -> DataRequirement:
    if not requirements:
        raise ValueError("cannot merge empty requirement group")
    base = requirements[0]
    field_set = tuple(
        dict.fromkeys(field for requirement in requirements for field in requirement.field_set if field.strip())
    )
    return replace(
        base,
        required_level=_strictest_required_level(requirements),
        freshness_policy=_strictest_freshness_policy(requirements),
        field_set=field_set,
        allow_search_discovery=all(requirement.allow_search_discovery for requirement in requirements),
    )


def _strictest_required_level(requirements: tuple[DataRequirement, ...]) -> RequiredLevel:
    order = {
        RequiredLevel.REQUIRED: 0,
        RequiredLevel.OPTIONAL: 1,
        RequiredLevel.EXPENSIVE: 2,
        RequiredLevel.NOT_APPLICABLE: 3,
    }
    return min((requirement.required_level for requirement in requirements), key=lambda item: order[item])


def _strictest_freshness_policy(requirements: tuple[DataRequirement, ...]) -> str:
    policies = tuple(requirement.freshness_policy for requirement in requirements)
    return min(policies, key=_freshness_rank)


def _freshness_rank(policy: str) -> tuple[int, int, str]:
    lowered = policy.lower()
    number_match = re.search(r"(\d+)", lowered)
    numeric = int(number_match.group(1)) if number_match else 999_999
    if "price_alert" in lowered or "short" in lowered:
        priority = 0
    elif "report" in lowered or "select" in lowered:
        priority = 1
    elif "ui_probe" in lowered:
        priority = 2
    else:
        priority = 3
    return (priority, numeric, lowered)
