from __future__ import annotations

import pytest

from claw_trade.licensing.service import LicenseService, build_license_service
from claw_trade.licensing.status import LicenseFeature, LicenseSnapshot, LicenseStatus


def test_dev_mode_without_license_required_allows_work() -> None:
    service = build_license_service({})

    assert service.current_snapshot().allows_report_generation()
    assert service.current_snapshot().allows_data_refresh()


def test_production_mode_without_virbox_command_fails_closed() -> None:
    service = build_license_service({"CLAW_TRADE_LICENSE_REQUIRED": "1"})

    snapshot = service.current_snapshot()

    assert snapshot.status == LicenseStatus.RUNTIME_UNAVAILABLE
    assert not snapshot.allows_report_generation()


def test_assert_report_generation_raises_user_error_when_blocked() -> None:
    service = LicenseService(lambda: LicenseSnapshot(status=LicenseStatus.REVOKED, features=frozenset()))

    with pytest.raises(PermissionError, match="设备授权已失效"):
        service.assert_report_generation_allowed()


def test_reader_error_fails_closed(tmp_path) -> None:
    command = tmp_path / "broken-probe"
    command.write_text("#!/usr/bin/env bash\nexit 2\n", encoding="utf-8")
    command.chmod(0o755)
    service = build_license_service(
        {
            "CLAW_TRADE_LICENSE_REQUIRED": "1",
            "CLAW_TRADE_VIRBOX_STATUS_COMMAND": str(command),
        }
    )

    snapshot = service.current_snapshot()

    assert snapshot.status == LicenseStatus.RUNTIME_UNAVAILABLE
    assert not snapshot.allows_report_generation()
    assert not snapshot.allows_data_refresh()


def test_dev_mode_default_snapshot_includes_expected_features() -> None:
    service = build_license_service({})

    snapshot = service.current_snapshot()

    assert snapshot.features == frozenset({LicenseFeature.REPORT, LicenseFeature.DATA_REFRESH})
