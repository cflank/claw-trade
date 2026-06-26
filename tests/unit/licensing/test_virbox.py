from __future__ import annotations

from pathlib import Path

import pytest

from claw_trade.licensing.status import LicenseStatus
from claw_trade.licensing.virbox import VirboxLicenseReader, VirboxReadError


def test_virbox_reader_maps_activated_json(tmp_path: Path) -> None:
    command = tmp_path / "probe.sh"
    command.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '{\"normalized_status\":\"activated\",\"features\":[\"report\",\"data_refresh\"],\"license_suffix\":\"1234\"}'\n",
        encoding="utf-8",
    )
    command.chmod(0o755)

    snapshot = VirboxLicenseReader(str(command)).read()

    assert snapshot.status == LicenseStatus.ACTIVATED
    assert snapshot.allows_report_generation()
    assert snapshot.allows_data_refresh()
    assert snapshot.license_suffix == "1234"


def test_virbox_reader_fails_closed_when_command_missing() -> None:
    with pytest.raises(VirboxReadError):
        VirboxLicenseReader("/no/such/command").read()


def test_virbox_reader_rejects_unknown_status_as_read_error(tmp_path: Path) -> None:
    command = tmp_path / "probe.sh"
    command.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '{\"normalized_status\":\"mystery\",\"features\":[\"report\"]}'\n",
        encoding="utf-8",
    )
    command.chmod(0o755)

    with pytest.raises(VirboxReadError, match="unknown virbox status"):
        VirboxLicenseReader(str(command)).read()


def test_virbox_reader_rejects_unknown_feature_as_read_error(tmp_path: Path) -> None:
    command = tmp_path / "probe.sh"
    command.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' '{\"normalized_status\":\"activated\",\"features\":[\"unknown_feature\"]}'\n",
        encoding="utf-8",
    )
    command.chmod(0o755)

    with pytest.raises(VirboxReadError, match="unknown virbox feature"):
        VirboxLicenseReader(str(command)).read()
