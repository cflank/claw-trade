from __future__ import annotations

from typing import Any, Mapping, Protocol


class DataSourceStoreLike(Protocol):
    def list_instances(self) -> tuple[dict[str, Any], ...]: ...


class SecretStoreLike(Protocol):
    def get(self, secret_ref: str | None) -> str | None: ...


class DataSourceCredentialResolver:
    def __init__(self, *, data_source_store: DataSourceStoreLike, secret_store: SecretStoreLike) -> None:
        self._data_source_store = data_source_store
        self._secret_store = secret_store

    def get_credential(self, name: str) -> str | None:
        record = self.get_instance(name)
        if record is None:
            return None
        credential_ref = _optional_str(record.get("credential_ref"))
        value = self._secret_store.get(credential_ref)
        if value:
            return value
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

    def get_instance(self, name: str) -> Mapping[str, Any] | None:
        supported_type = _supported_type_from_name(name)
        if supported_type is None:
            return None
        for record in self._data_source_store.list_instances():
            if str(record.get("supported_type", "")).strip() != supported_type:
                continue
            if not bool(record.get("enabled", False)):
                continue
            return record
        return None


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
