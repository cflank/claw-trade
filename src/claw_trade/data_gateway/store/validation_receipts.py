from __future__ import annotations

from dataclasses import asdict
from typing import Any

from pymongo.errors import PyMongoError

from claw_trade.data_gateway.errors import DataGatewayError, DataGatewayErrorCode
from claw_trade.data_gateway.models import (
    AdmissionCheckStatus,
    CredentialStatus,
    LicenseCheckResult,
    ProviderAdmissionStatus,
    ProviderValidationReceipt,
)

from .mongo import OPENBB_PROVIDER_VALIDATION_RECEIPTS, utc_now_iso


class MongoValidationReceiptStore:
    def __init__(self, collection: Any) -> None:
        self.collection = collection

    @property
    def collection_name(self) -> str:
        return OPENBB_PROVIDER_VALIDATION_RECEIPTS

    def write(self, receipt: ProviderValidationReceipt) -> str:
        validation_id = f"{receipt.provider_id}:{receipt.config_version}:{receipt.validated_at}"
        doc = _serialize_receipt(receipt)
        doc["_id"] = validation_id
        try:
            self.collection.replace_one({"_id": validation_id}, doc, upsert=True)
        except PyMongoError as exc:
            raise DataGatewayError(
                DataGatewayErrorCode.EVIDENCE_WRITE_FAILED,
                f"validation receipt write failed: {exc}",
            ) from exc
        return validation_id

    def latest(self, *, provider_id: str, adapter_id: str, config_version: str | None = None) -> ProviderValidationReceipt | None:
        query: dict[str, Any] = {"provider_id": provider_id, "adapter_id": adapter_id}
        if config_version is not None:
            query["config_version"] = config_version
        doc = self.collection.find_one(query, sort=[("validated_at", -1)])
        if doc is None:
            return None
        return _deserialize_receipt(doc)


def _serialize_receipt(receipt: ProviderValidationReceipt) -> dict[str, Any]:
    return {
        "provider_id": receipt.provider_id,
        "adapter_id": receipt.adapter_id,
        "config_version": receipt.config_version,
        "status": receipt.status.value,
        "credential_status": receipt.credential_status.value,
        "healthcheck_status": receipt.healthcheck_status.value,
        "schema_status": receipt.schema_status.value,
        "license_status": receipt.license_status.value,
        "secret_status": receipt.secret_status.value,
        "credential_detail": _serialize_credential(receipt.credential_detail),
        "license_detail": _serialize_license(receipt.license_detail),
        "sample_raw_ref": receipt.sample_raw_ref,
        "sample_normalized_ref": receipt.sample_normalized_ref,
        "transition_actor": receipt.transition_actor,
        "previous_status": receipt.previous_status.value if receipt.previous_status is not None else None,
        "transition_reason": receipt.transition_reason,
        "errors": list(receipt.errors),
        "validated_at": receipt.validated_at,
        "updated_at": utc_now_iso(),
    }


def _deserialize_receipt(doc: dict[str, Any]) -> ProviderValidationReceipt:
    return ProviderValidationReceipt(
        provider_id=str(doc["provider_id"]),
        adapter_id=str(doc["adapter_id"]),
        config_version=str(doc["config_version"]),
        status=ProviderAdmissionStatus(str(doc["status"])),
        credential_status=AdmissionCheckStatus(str(doc["credential_status"])),
        healthcheck_status=AdmissionCheckStatus(str(doc["healthcheck_status"])),
        schema_status=AdmissionCheckStatus(str(doc["schema_status"])),
        license_status=AdmissionCheckStatus(str(doc["license_status"])),
        secret_status=AdmissionCheckStatus(str(doc["secret_status"])),
        credential_detail=_deserialize_credential(doc.get("credential_detail")),
        license_detail=_deserialize_license(doc.get("license_detail")),
        sample_raw_ref=doc.get("sample_raw_ref"),
        sample_normalized_ref=doc.get("sample_normalized_ref"),
        transition_actor=str(doc["transition_actor"]),
        previous_status=ProviderAdmissionStatus(str(doc["previous_status"])) if doc.get("previous_status") else None,
        transition_reason=str(doc["transition_reason"]),
        errors=tuple(str(item) for item in (doc.get("errors") or ())),
        validated_at=str(doc["validated_at"]),
    )


def _serialize_credential(detail: CredentialStatus | None) -> dict[str, Any] | None:
    if detail is None:
        return None
    doc = asdict(detail)
    doc["status"] = detail.status.value
    return doc


def _deserialize_credential(doc: dict[str, Any] | None) -> CredentialStatus | None:
    if doc is None:
        return None
    return CredentialStatus(
        status=AdmissionCheckStatus(str(doc["status"])),
        provider=str(doc["provider"]),
        adapter_id=str(doc["adapter_id"]),
        missing_keys=tuple(str(item) for item in (doc.get("missing_keys") or ())),
        invalid_keys=tuple(str(item) for item in (doc.get("invalid_keys") or ())),
        root_cause=doc.get("root_cause"),
    )


def _serialize_license(detail: LicenseCheckResult | None) -> dict[str, Any] | None:
    if detail is None:
        return None
    doc = asdict(detail)
    doc["status"] = detail.status.value
    return doc


def _deserialize_license(doc: dict[str, Any] | None) -> LicenseCheckResult | None:
    if doc is None:
        return None
    return LicenseCheckResult(
        status=AdmissionCheckStatus(str(doc["status"])),
        license_policy_id=str(doc["license_policy_id"]),
        cost_tier=str(doc["cost_tier"]),
        raw_export_policy=str(doc["raw_export_policy"]),
        commercial_use_allowed=bool(doc["commercial_use_allowed"]),
        note=str(doc["note"]),
    )
