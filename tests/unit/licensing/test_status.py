from __future__ import annotations

from claw_trade.licensing.status import LicenseFeature, LicenseSnapshot, LicenseStatus


def test_activated_license_allows_report_and_data_refresh() -> None:
    snapshot = LicenseSnapshot(
        status=LicenseStatus.ACTIVATED,
        features=frozenset({LicenseFeature.REPORT, LicenseFeature.DATA_REFRESH}),
    )

    assert snapshot.allows_report_generation()
    assert snapshot.allows_data_refresh()


def test_revoked_license_blocks_report_and_data_refresh() -> None:
    snapshot = LicenseSnapshot(status=LicenseStatus.REVOKED, features=frozenset())

    assert not snapshot.allows_report_generation()
    assert not snapshot.allows_data_refresh()
    assert snapshot.user_message == "设备授权已失效，请在授权页修复后重试。"


def test_runtime_unavailable_fails_closed() -> None:
    snapshot = LicenseSnapshot(status=LicenseStatus.RUNTIME_UNAVAILABLE, features=frozenset())

    assert not snapshot.allows_report_generation()
    assert not snapshot.allows_data_refresh()
