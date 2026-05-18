from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from claw_trade.data_gateway.models import AdmissionCheckStatus, CredentialStatus, DeclarativeProviderManifest


@dataclass(frozen=True)
class SecretStore:
    secrets: Mapping[str, str]

    def credential_status(self, manifest: DeclarativeProviderManifest) -> CredentialStatus:
        missing_keys: list[str] = []
        invalid_keys: list[str] = []
        for key in manifest.credential_requirements:
            value = self.secrets.get(key)
            if value is None:
                missing_keys.append(key)
                continue
            if not str(value).strip():
                invalid_keys.append(key)
        if missing_keys:
            return CredentialStatus(
                status=AdmissionCheckStatus.MISSING,
                provider=manifest.provider_id,
                adapter_id=manifest.adapter_id,
                missing_keys=tuple(missing_keys),
                invalid_keys=tuple(invalid_keys),
                root_cause="credential missing",
            )
        if invalid_keys:
            return CredentialStatus(
                status=AdmissionCheckStatus.FAIL,
                provider=manifest.provider_id,
                adapter_id=manifest.adapter_id,
                missing_keys=(),
                invalid_keys=tuple(invalid_keys),
                root_cause="credential invalid",
            )
        return CredentialStatus(
            status=AdmissionCheckStatus.PASS,
            provider=manifest.provider_id,
            adapter_id=manifest.adapter_id,
        )
