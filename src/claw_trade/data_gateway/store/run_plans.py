from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pymongo.errors import PyMongoError

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import (
    DataGap,
    DataGapReason,
    GapSeverity,
    Market,
    PackDomain,
    PrioritySource,
    ProviderCallSpec,
    ProviderKind,
    RateLimitPlanItem,
    RunProviderPlan,
    SourceRole,
)

from .mongo import OPENBB_RUN_PROVIDER_PLANS


class MongoRunProviderPlanStore:
    def __init__(self, collection: Any) -> None:
        self.collection = collection

    @property
    def collection_name(self) -> str:
        return OPENBB_RUN_PROVIDER_PLANS

    def write(self, plan: RunProviderPlan) -> str:
        existing = self.collection.find_one({"_id": plan.run_id})
        if existing is not None:
            existing_cfg = str(existing.get("provider_config_version") or "")
            if existing_cfg != plan.provider_config_version:
                raise DataGatewayError(
                    DataGatewayErrorCode.CONFIG_VERSION_MISMATCH,
                    f"run_id={plan.run_id} provider_config_version mismatch: {existing_cfg} != {plan.provider_config_version}",
                )
        doc = _serialize_plan(plan)
        try:
            self.collection.replace_one({"_id": plan.run_id}, doc, upsert=True)
        except PyMongoError as exc:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"run provider plan write failed: {exc}",
            ) from exc
        return plan.run_id

    def load(self, run_id: str) -> RunProviderPlan:
        doc = self.collection.find_one({"_id": run_id})
        if doc is None:
            raise DataGatewayError(
                DataGatewayErrorCode.RUN_PLAN_MISSING,
                f"run plan not found: {run_id}",
            )
        return _deserialize_plan(doc)


def _serialize_plan(plan: RunProviderPlan) -> dict[str, Any]:
    return {
        "_id": plan.run_id,
        "run_id": plan.run_id,
        "provider_config_version": plan.provider_config_version,
        "market": plan.market.value,
        "ticker": plan.ticker,
        "domains": [domain.value for domain in plan.domains],
        "call_specs": [_serialize_call_spec(spec) for spec in plan.call_specs],
        "shared_call_keys": list(plan.shared_call_keys),
        "cache_keys": list(plan.cache_keys),
        "rate_limit_plan": [asdict(item) for item in plan.rate_limit_plan],
        "initial_gaps": [_serialize_gap(gap) for gap in plan.initial_gaps],
        "generated_at": plan.generated_at,
        "remote_prefetch_allowed": False,
    }


def _deserialize_plan(doc: dict[str, Any]) -> RunProviderPlan:
    return RunProviderPlan(
        run_id=str(doc["run_id"]),
        provider_config_version=str(doc["provider_config_version"]),
        market=Market(str(doc["market"])),
        ticker=str(doc["ticker"]),
        domains=tuple(PackDomain(str(value)) for value in (doc.get("domains") or ())),
        call_specs=tuple(_deserialize_call_spec(entry) for entry in (doc.get("call_specs") or ())),
        shared_call_keys=tuple(str(value) for value in (doc.get("shared_call_keys") or ())),
        cache_keys=tuple(str(value) for value in (doc.get("cache_keys") or ())),
        rate_limit_plan=tuple(_deserialize_rate_plan(entry) for entry in (doc.get("rate_limit_plan") or ())),
        initial_gaps=tuple(_deserialize_gap(entry) for entry in (doc.get("initial_gaps") or ())),
        generated_at=str(doc["generated_at"]),
        remote_prefetch_allowed=bool(doc.get("remote_prefetch_allowed", False)),
    )


def _serialize_call_spec(spec: ProviderCallSpec) -> dict[str, Any]:
    return {
        "call_key": spec.call_key,
        "provider": spec.provider,
        "adapter_id": spec.adapter_id,
        "provider_kind": spec.provider_kind.value,
        "provider_config_version": spec.provider_config_version,
        "endpoint": spec.endpoint,
        "source_role": spec.source_role.value,
        "market": spec.market.value,
        "domain": spec.domain.value,
        "required": spec.required,
        "attempt_required": spec.attempt_required,
        "coverage_group": spec.coverage_group,
        "coverage_quorum": spec.coverage_quorum,
        "params": dict(spec.params),
        "cache_ttl_seconds": spec.cache_ttl_seconds,
        "license_policy_id": spec.license_policy_id,
        "expected_schema_id": spec.expected_schema_id,
        "priority": spec.priority,
        "priority_source": spec.priority_source.value,
        "user_preferred": spec.user_preferred,
    }


def _deserialize_call_spec(doc: dict[str, Any]) -> ProviderCallSpec:
    return ProviderCallSpec(
        call_key=str(doc["call_key"]),
        provider=str(doc["provider"]),
        adapter_id=str(doc["adapter_id"]),
        provider_kind=ProviderKind(str(doc["provider_kind"])),
        provider_config_version=str(doc["provider_config_version"]),
        endpoint=str(doc["endpoint"]),
        source_role=SourceRole(str(doc["source_role"])),
        market=Market(str(doc["market"])),
        domain=PackDomain(str(doc["domain"])),
        required=bool(doc["required"]),
        attempt_required=bool(doc["attempt_required"]),
        coverage_group=doc.get("coverage_group"),
        coverage_quorum=doc.get("coverage_quorum"),
        params=dict(doc.get("params") or {}),
        cache_ttl_seconds=int(doc["cache_ttl_seconds"]),
        license_policy_id=str(doc["license_policy_id"]),
        expected_schema_id=str(doc["expected_schema_id"]),
        priority=int(doc["priority"]),
        priority_source=PrioritySource(str(doc["priority_source"])),
        user_preferred=bool(doc["user_preferred"]),
    )


def _serialize_gap(gap: DataGap) -> dict[str, Any]:
    return {
        "gap_id": gap.gap_id,
        "domain": gap.domain.value,
        "severity": gap.severity.value,
        "reason": gap.reason.value,
        "field_path": gap.field_path,
        "provider_candidates": list(gap.provider_candidates),
        "attempt_ids": list(gap.attempt_ids),
        "root_cause": gap.root_cause,
        "next_action": gap.next_action,
    }


def _deserialize_gap(doc: dict[str, Any]) -> DataGap:
    return DataGap(
        gap_id=str(doc["gap_id"]),
        domain=PackDomain(str(doc["domain"])),
        severity=GapSeverity(str(doc["severity"])),
        reason=DataGapReason(str(doc["reason"])),
        field_path=str(doc["field_path"]),
        provider_candidates=tuple(str(item) for item in (doc.get("provider_candidates") or ())),
        attempt_ids=tuple(str(item) for item in (doc.get("attempt_ids") or ())),
        root_cause=str(doc["root_cause"]),
        next_action=str(doc["next_action"]),
    )


def _deserialize_rate_plan(doc: dict[str, Any]) -> RateLimitPlanItem:
    return RateLimitPlanItem(
        provider=str(doc["provider"]),
        endpoint=str(doc["endpoint"]),
        call_key=str(doc["call_key"]),
        window_seconds=int(doc["window_seconds"]),
        estimated_cost=int(doc["estimated_cost"]),
        hard_reserved=bool(doc.get("hard_reserved", False)),
    )
