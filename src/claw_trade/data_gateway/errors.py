from __future__ import annotations

from enum import StrEnum


class DataGatewayErrorCode(StrEnum):
    RUN_PLAN_MISSING = "run_plan_missing"
    CONFIG_VERSION_MISMATCH = "config_version_mismatch"
    CREDENTIAL_MISSING = "credential_missing"
    LICENSE_BLOCKED = "license_blocked"
    RATE_LIMITED = "rate_limited"
    REMOTE_ERROR = "remote_error"
    CACHE_ERROR = "cache_error"
    SCHEMA_INVALID = "schema_invalid"
    FIELD_MISSING = "field_missing"
    EVIDENCE_WRITE_FAILED = "evidence_write_failed"
    SINGLE_FLIGHT_TIMEOUT = "single_flight_timeout"
    SINGLE_FLIGHT_OWNER_FAILED = "single_flight_owner_failed"
    ADMISSION_REJECTED = "admission_rejected"


class DataGatewayError(RuntimeError):
    code: DataGatewayErrorCode
    root_cause: str

    def __init__(self, code: DataGatewayErrorCode, root_cause: str) -> None:
        self.code = code
        self.root_cause = root_cause
        super().__init__(f"{code.value}: {root_cause}")
