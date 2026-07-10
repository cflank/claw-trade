from __future__ import annotations

import pytest

from claw_trade.production.update_manifest import UpdateManifest


def test_update_manifest_accepts_installable_stable_release() -> None:
    manifest = UpdateManifest.from_mapping(
        {
            "product": "claw-trade",
            "channel": "stable",
            "version": "1.2.3",
            "arch": "linux-x86_64",
            "archive": "claw-trade-production-1.2.3-20260626T120000Z.tar.gz",
            "sha256": "a" * 64,
            "created_at": "2026-06-26T00:00:00Z",
            "min_current_version": "1.0.0",
            "notes": "release note",
        }
    )

    manifest.assert_installable(current_version="1.2.2")
    assert manifest.archive == "claw-trade-production-1.2.3-20260626T120000Z.tar.gz"


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"archive": "../release.tar.gz"}, "archive 必须是同目录文件名"),
        ({"archive": "release.tar.gz"}, "archive 文件名非法"),
        ({"archive": "claw-trade-production-1.2.2-20260626T120000Z.tar.gz"}, "archive 版本与 manifest version 不匹配"),
        ({"sha256": "ABC"}, "sha256"),
        ({"version": "1.2"}, "version"),
    ],
)
def test_update_manifest_rejects_invalid_payload_fields(patch: dict[str, str], message: str) -> None:
    payload = {
        "product": "claw-trade",
        "channel": "stable",
        "version": "1.2.3",
        "arch": "linux-x86_64",
        "archive": "claw-trade-production-1.2.3-20260626T120000Z.tar.gz",
        "sha256": "a" * 64,
        "created_at": "2026-06-26T00:00:00Z",
        "min_current_version": "1.0.0",
        **patch,
    }

    with pytest.raises(ValueError, match=message):
        UpdateManifest.from_mapping(payload)


def test_update_manifest_rejects_wrong_target_or_downgrade() -> None:
    manifest = UpdateManifest.from_mapping(
        {
            "product": "claw-trade",
            "channel": "stable",
            "version": "1.2.3",
            "arch": "linux-x86_64",
            "archive": "claw-trade-production-1.2.3-20260626T120000Z.tar.gz",
            "sha256": "a" * 64,
            "created_at": "2026-06-26T00:00:00Z",
            "min_current_version": "1.2.0",
        }
    )

    with pytest.raises(ValueError, match="不高于当前版本"):
        manifest.assert_installable(current_version="1.2.3")
    with pytest.raises(ValueError, match="最低可升级版本"):
        manifest.assert_installable(current_version="1.1.9")
    with pytest.raises(ValueError, match="arch"):
        manifest.assert_installable(current_version="1.2.2", arch="darwin-arm64")
