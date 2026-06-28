from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class LicenseStatus(StrEnum):
    ACTIVATED = "activated"
    NOT_ACTIVATED = "not_activated"
    GRACE_PERIOD = "grace_period"
    EXPIRED = "expired"
    REVOKED = "revoked"
    RUNTIME_UNAVAILABLE = "runtime_unavailable"
    NETWORK_UNAVAILABLE = "network_unavailable"


class LicenseFeature(StrEnum):
    REPORT = "report"
    DATA_REFRESH = "data_refresh"


_ALLOWED_STATUSES = {LicenseStatus.ACTIVATED, LicenseStatus.GRACE_PERIOD}


@dataclass(frozen=True)
class LicenseSnapshot:
    status: LicenseStatus
    features: frozenset[LicenseFeature]
    expires_at: str | None = None
    grace_until: str | None = None
    device_id_hash: str | None = None
    license_suffix: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def allows_report_generation(self) -> bool:
        return self.status in _ALLOWED_STATUSES and LicenseFeature.REPORT in self.features

    def allows_data_refresh(self) -> bool:
        return self.status in _ALLOWED_STATUSES and LicenseFeature.DATA_REFRESH in self.features

    @property
    def user_message(self) -> str:
        if self.status == LicenseStatus.NOT_ACTIVATED:
            return "设备尚未激活，请在授权页激活后重试。"
        if self.status == LicenseStatus.GRACE_PERIOD:
            return "设备授权处于离线宽限期，请尽快联网续期。"
        if self.status in {LicenseStatus.EXPIRED, LicenseStatus.REVOKED}:
            return "设备授权已失效，请在授权页修复后重试。"
        if self.status == LicenseStatus.NETWORK_UNAVAILABLE:
            return "授权续期网络不可用，请检查网络后重试。"
        if self.status == LicenseStatus.RUNTIME_UNAVAILABLE:
            return "授权运行时不可用，请联系支持人员。"
        return "授权状态正常。"
