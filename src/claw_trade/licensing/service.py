from __future__ import annotations

from collections.abc import Callable, Mapping

from claw_trade.licensing.status import LicenseFeature, LicenseSnapshot, LicenseStatus
from claw_trade.licensing.virbox import VirboxLicenseReader, VirboxReadError


class LicenseService:
    def __init__(self, reader: Callable[[], LicenseSnapshot]) -> None:
        self._reader = reader

    def current_snapshot(self) -> LicenseSnapshot:
        return self._reader()

    def assert_report_generation_allowed(self) -> None:
        snapshot = self.current_snapshot()
        if not snapshot.allows_report_generation():
            raise PermissionError(snapshot.user_message)

    def assert_data_refresh_allowed(self) -> None:
        snapshot = self.current_snapshot()
        if not snapshot.allows_data_refresh():
            raise PermissionError(snapshot.user_message)


def build_license_service(env: Mapping[str, str]) -> LicenseService:
    required = str(env.get("CLAW_TRADE_LICENSE_REQUIRED") or "").strip().lower() in {"1", "true", "yes", "on"}
    command = str(env.get("CLAW_TRADE_VIRBOX_STATUS_COMMAND") or "").strip()
    if command:
        reader = VirboxLicenseReader(command)
        return LicenseService(lambda: _read_or_fail_closed(reader))
    if required:
        return LicenseService(lambda: LicenseSnapshot(status=LicenseStatus.RUNTIME_UNAVAILABLE, features=frozenset()))
    return LicenseService(
        lambda: LicenseSnapshot(
            status=LicenseStatus.ACTIVATED,
            features=frozenset({LicenseFeature.REPORT, LicenseFeature.DATA_REFRESH}),
        )
    )


def _read_or_fail_closed(reader: VirboxLicenseReader) -> LicenseSnapshot:
    try:
        return reader.read()
    except VirboxReadError as exc:
        return LicenseSnapshot(
            status=LicenseStatus.RUNTIME_UNAVAILABLE,
            features=frozenset(),
            error_code=type(exc).__name__,
            error_message=str(exc),
        )
