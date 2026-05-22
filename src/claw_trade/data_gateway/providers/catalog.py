from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from typing import Mapping

from claw_trade.data_gateway.models import (
    DeclarativeProviderManifest,
    ProviderAdmissionStatus,
    ProviderValidationReceipt,
    utc_now_iso,
)
from claw_trade.data_gateway.providers.admission import ProviderAdmissionValidator

_TRANSITIONS: dict[ProviderAdmissionStatus, frozenset[ProviderAdmissionStatus]] = {
    ProviderAdmissionStatus.DRAFT: frozenset({ProviderAdmissionStatus.VALIDATING}),
    ProviderAdmissionStatus.VALIDATING: frozenset(
        {
            ProviderAdmissionStatus.VALIDATED,
            ProviderAdmissionStatus.REJECTED,
            ProviderAdmissionStatus.QUARANTINED,
        }
    ),
    ProviderAdmissionStatus.VALIDATED: frozenset(
        {
            ProviderAdmissionStatus.ENABLED_CANDIDATE,
            ProviderAdmissionStatus.DISABLED,
            ProviderAdmissionStatus.REJECTED,
            ProviderAdmissionStatus.QUARANTINED,
            ProviderAdmissionStatus.VALIDATING,
        }
    ),
    ProviderAdmissionStatus.ENABLED_CANDIDATE: frozenset(
        {
            ProviderAdmissionStatus.DISABLED,
            ProviderAdmissionStatus.QUARANTINED,
            ProviderAdmissionStatus.REJECTED,
            ProviderAdmissionStatus.VALIDATING,
        }
    ),
    ProviderAdmissionStatus.DISABLED: frozenset(
        {
            ProviderAdmissionStatus.VALIDATING,
            ProviderAdmissionStatus.ENABLED_CANDIDATE,
        }
    ),
    ProviderAdmissionStatus.REJECTED: frozenset({ProviderAdmissionStatus.VALIDATING}),
    ProviderAdmissionStatus.QUARANTINED: frozenset({ProviderAdmissionStatus.VALIDATING}),
}


class ProviderCatalog:
    def __init__(
        self,
        *,
        validator: ProviderAdmissionValidator,
        system_manifests: tuple[DeclarativeProviderManifest, ...] = (),
        user_manifests: tuple[DeclarativeProviderManifest, ...] = (),
    ) -> None:
        self._validator = validator
        self._system: dict[str, DeclarativeProviderManifest] = {item.adapter_id: item for item in system_manifests}
        self._user: dict[str, DeclarativeProviderManifest] = {item.adapter_id: item for item in user_manifests}
        self._receipts: dict[str, list[ProviderValidationReceipt]] = {adapter_id: [] for adapter_id in self._all().keys()}

    def _all(self) -> dict[str, DeclarativeProviderManifest]:
        return {**self._system, **self._user}

    def load_system_manifests(self) -> tuple[DeclarativeProviderManifest, ...]:
        return tuple(self._system.values())

    def load_user_manifests(self) -> tuple[DeclarativeProviderManifest, ...]:
        return tuple(self._user.values())

    def receipts_for(self, adapter_id: str) -> tuple[ProviderValidationReceipt, ...]:
        return tuple(self._receipts.get(adapter_id, ()))

    def latest_receipt(self, adapter_id: str) -> ProviderValidationReceipt | None:
        entries = self._receipts.get(adapter_id, [])
        return entries[-1] if entries else None

    def manifest(self, adapter_id: str) -> DeclarativeProviderManifest:
        return self._all()[adapter_id]

    def upsert_manifest(self, manifest: DeclarativeProviderManifest) -> None:
        self._user[manifest.adapter_id] = manifest
        self._receipts.setdefault(manifest.adapter_id, [])

    def _record_transition(
        self,
        *,
        manifest: DeclarativeProviderManifest,
        next_status: ProviderAdmissionStatus,
        actor: str,
        reason: str,
        baseline: ProviderValidationReceipt | None = None,
    ) -> ProviderValidationReceipt:
        current = manifest.admission_status
        if next_status not in _TRANSITIONS.get(current, frozenset()):
            raise ValueError(f"invalid_transition:{current.value}->{next_status.value}")

        previous = current
        updated = replace(manifest, admission_status=next_status)
        self._user[manifest.adapter_id] = updated
        base = baseline or self.latest_receipt(manifest.adapter_id)
        if base is None:
            base = self._validator.validate(
                updated,
                actor=actor,
                previous_status=previous,
                transition_reason=reason,
            )
        receipt = ProviderValidationReceipt(
            provider_id=manifest.provider_id,
            adapter_id=manifest.adapter_id,
            config_version=manifest.config_version,
            status=next_status,
            credential_status=base.credential_status,
            healthcheck_status=base.healthcheck_status,
            schema_status=base.schema_status,
            license_status=base.license_status,
            secret_status=base.secret_status,
            credential_detail=base.credential_detail,
            license_detail=base.license_detail,
            sample_raw_ref=base.sample_raw_ref,
            sample_normalized_ref=base.sample_normalized_ref,
            transition_actor=actor,
            previous_status=previous,
            transition_reason=reason,
            errors=(),
            validated_at=utc_now_iso(),
        )
        self._receipts.setdefault(manifest.adapter_id, []).append(receipt)
        return receipt

    def validate_manifest(self, manifest: DeclarativeProviderManifest, *, actor: str, reason: str) -> ProviderValidationReceipt:
        self.upsert_manifest(manifest)
        current = self._user[manifest.adapter_id]
        if current.admission_status in (ProviderAdmissionStatus.REJECTED, ProviderAdmissionStatus.QUARANTINED):
            current = replace(current, admission_status=ProviderAdmissionStatus.DRAFT)
            self._user[current.adapter_id] = current

        if current.admission_status == ProviderAdmissionStatus.DISABLED:
            current = replace(current, admission_status=ProviderAdmissionStatus.VALIDATED)
            self._user[current.adapter_id] = current

        if current.admission_status not in (ProviderAdmissionStatus.DRAFT, ProviderAdmissionStatus.VALIDATED):
            raise ValueError(f"validate_manifest_not_allowed_from:{current.admission_status.value}")

        self._record_transition(
            manifest=current,
            next_status=ProviderAdmissionStatus.VALIDATING,
            actor=actor,
            reason=f"{reason}:start_validating",
        )
        validating = self._user[current.adapter_id]
        receipt = self._validator.validate(
            validating,
            actor=actor,
            previous_status=ProviderAdmissionStatus.VALIDATING,
            transition_reason=reason,
        )
        final_manifest = replace(validating, admission_status=receipt.status)
        self._user[validating.adapter_id] = final_manifest
        self._receipts.setdefault(validating.adapter_id, []).append(receipt)
        if receipt.status == ProviderAdmissionStatus.VALIDATED and final_manifest.enabled:
            return self._record_transition(
                manifest=final_manifest,
                next_status=ProviderAdmissionStatus.ENABLED_CANDIDATE,
                actor=actor,
                reason=f"{reason}:enable_candidate",
                baseline=receipt,
            )
        return receipt

    def set_enabled(self, adapter_id: str, *, enabled: bool, actor: str, reason: str) -> ProviderValidationReceipt:
        manifest = self._user[adapter_id]
        updated = replace(manifest, enabled=enabled)
        self._user[adapter_id] = updated
        target = ProviderAdmissionStatus.ENABLED_CANDIDATE if enabled else ProviderAdmissionStatus.DISABLED
        if enabled:
            if updated.admission_status != ProviderAdmissionStatus.VALIDATED:
                raise ValueError("enable_requires_validated")
        return self._record_transition(manifest=updated, next_status=target, actor=actor, reason=reason)

    def quarantine(self, adapter_id: str, *, actor: str, reason: str) -> ProviderValidationReceipt:
        manifest = self._user[adapter_id]
        return self._record_transition(
            manifest=manifest,
            next_status=ProviderAdmissionStatus.QUARANTINED,
            actor=actor,
            reason=reason,
        )

    def reject(self, adapter_id: str, *, actor: str, reason: str) -> ProviderValidationReceipt:
        manifest = self._user[adapter_id]
        return self._record_transition(
            manifest=manifest,
            next_status=ProviderAdmissionStatus.REJECTED,
            actor=actor,
            reason=reason,
        )

    def enabled_candidates(self) -> tuple[DeclarativeProviderManifest, ...]:
        items = []
        for manifest in self._all().values():
            if manifest.admission_status == ProviderAdmissionStatus.ENABLED_CANDIDATE and manifest.enabled:
                items.append(manifest)
        return tuple(sorted(items, key=lambda item: (item.adapter_id, item.config_version)))

    def snapshot_version(self) -> str:
        payload = []
        for manifest in self.enabled_candidates():
            payload.append(
                {
                    "adapter_id": manifest.adapter_id,
                    "config_version": manifest.config_version,
                    "markets": tuple(item.value for item in manifest.markets),
                    "domains": tuple(item.value for item in manifest.domains),
                    "source_role": manifest.source_role.value,
                    "coverage_group": manifest.coverage_group,
                    "priority": manifest.priority,
                    "priority_source": manifest.priority_source.value,
                }
            )
        encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return f"sha256:{hashlib.sha256(encoded).hexdigest()}"

    def by_adapter(self) -> Mapping[str, DeclarativeProviderManifest]:
        return self._all()
