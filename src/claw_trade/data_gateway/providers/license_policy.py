from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    DeclarativeProviderManifest,
    LicenseCheckResult,
)

ALLOWED_RAW_EXPORT_POLICIES = frozenset({"metadata_only", "redacted", "full"})


@dataclass(frozen=True)
class LicensePolicyStore:
    policies: Mapping[str, LicenseCheckResult]
    default_cost_tier: str = "unknown"

    def evaluate(self, manifest: DeclarativeProviderManifest) -> tuple[AdmissionCheckStatus, LicenseCheckResult | None, tuple[str, ...]]:
        errors: list[str] = []
        if manifest.raw_export_policy not in ALLOWED_RAW_EXPORT_POLICIES:
            errors.append(f"raw_export_policy_not_allowed:{manifest.raw_export_policy}")
            return (AdmissionCheckStatus.FAIL, None, tuple(errors))

        policy = self.policies.get(manifest.license_policy_id)
        if policy is None:
            errors.append(f"license_policy_missing:{manifest.license_policy_id}")
            return (AdmissionCheckStatus.MISSING, None, tuple(errors))

        if policy.status != AdmissionCheckStatus.PASS:
            errors.append(f"license_policy_blocked:{manifest.license_policy_id}")
            return (AdmissionCheckStatus.BLOCKED, policy, tuple(errors))

        if policy.raw_export_policy != manifest.raw_export_policy:
            errors.append(
                "raw_export_policy_mismatch:"
                f"manifest={manifest.raw_export_policy},policy={policy.raw_export_policy}"
            )
            return (AdmissionCheckStatus.FAIL, policy, tuple(errors))

        return (AdmissionCheckStatus.PASS, policy, ())
