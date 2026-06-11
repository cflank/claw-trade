from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol


class DataSourceStoreLike(Protocol):
    def list_instances(self) -> tuple[dict[str, Any], ...]: ...


class SecretStoreLike(Protocol):
    def get(self, secret_ref: str | None) -> str | None: ...


@dataclass(frozen=True)
class DataSourceCredentialStatus:
    name: str
    supported_type: str | None
    configured: bool
    enabled: bool
    available: bool
    reason: str
    credential_ref: str | None = None


class DataSourceCredentialResolver:
    def __init__(self, *, data_source_store: DataSourceStoreLike, secret_store: SecretStoreLike) -> None:
        self._data_source_store = data_source_store
        self._secret_store = secret_store

    def get_credential(self, name: str) -> str | None:
        status = self.get_credential_status(name)
        if not status.available:
            return None
        return self._secret_store.get(status.credential_ref)

    def get_credential_status(self, name: str) -> DataSourceCredentialStatus:
        supported_type = _supported_type_from_name(name)
        if supported_type is None:
            return DataSourceCredentialStatus(
                name=name,
                supported_type=None,
                configured=False,
                enabled=False,
                available=False,
                reason="invalid_credential_name",
            )
        records = tuple(self._records_for_supported_type(supported_type))
        if not records:
            return DataSourceCredentialStatus(
                name=name,
                supported_type=supported_type,
                configured=False,
                enabled=False,
                available=False,
                reason="source_not_configured",
            )
        enabled_records = tuple(record for record in records if bool(record.get("enabled", False)))
        if not enabled_records:
            return DataSourceCredentialStatus(
                name=name,
                supported_type=supported_type,
                configured=True,
                enabled=False,
                available=False,
                reason="source_disabled",
            )
        record = enabled_records[0]
        credential_ref = _optional_str(record.get("credential_ref"))
        if self._secret_store.get(credential_ref):
            return DataSourceCredentialStatus(
                name=name,
                supported_type=supported_type,
                configured=True,
                enabled=True,
                available=True,
                reason="available",
                credential_ref=credential_ref,
            )
        return DataSourceCredentialStatus(
            name=name,
            supported_type=supported_type,
            configured=True,
            enabled=True,
            available=False,
            reason="credential_missing",
            credential_ref=credential_ref,
        )

    def _records_for_supported_type(self, supported_type: str) -> tuple[Mapping[str, Any], ...]:
        return tuple(
            record
            for record in self._data_source_store.list_instances()
            if str(record.get("supported_type", "")).strip() == supported_type
        )

    def get_instance(self, name: str) -> Mapping[str, Any] | None:
        supported_type = _supported_type_from_name(name)
        if supported_type is None:
            return None
        for record in self._records_for_supported_type(supported_type):
            if not bool(record.get("enabled", False)):
                continue
            return record
        return None

    def get_endpoint_url(self, name: str) -> str | None:
        record = self.get_instance(name)
        if record is None:
            return None
        return _optional_str(record.get("endpoint_url") or record.get("endpointUrl"))

    def get_header_name(self, name: str) -> str | None:
        record = self.get_instance(name)
        if record is None:
            return None
        return _optional_str(record.get("header_name") or record.get("headerName"))


def _supported_type_from_name(name: str) -> str | None:
    prefix = "data_source:"
    if not name.startswith(prefix):
        return None
    supported_type = name[len(prefix) :].strip()
    return supported_type or None


def _optional_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
