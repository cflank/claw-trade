from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass

from claw_trade.licensing.status import LicenseFeature, LicenseSnapshot, LicenseStatus


class VirboxReadError(RuntimeError):
    pass


@dataclass(frozen=True)
class VirboxLicenseReader:
    status_command: str
    timeout_seconds: float = 5.0

    def read(self) -> LicenseSnapshot:
        try:
            completed = subprocess.run(
                [self.status_command],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
        except OSError as exc:
            raise VirboxReadError(str(exc)) from exc
        except subprocess.TimeoutExpired as exc:
            raise VirboxReadError("virbox status command timed out") from exc

        if completed.returncode != 0:
            message = (completed.stderr or completed.stdout or "virbox status command failed").strip()
            raise VirboxReadError(message)

        try:
            payload = json.loads(completed.stdout)
        except json.JSONDecodeError as exc:
            raise VirboxReadError("virbox status command returned invalid json") from exc

        return _snapshot_from_payload(payload)


def _snapshot_from_payload(payload: object) -> LicenseSnapshot:
    if not isinstance(payload, dict):
        raise VirboxReadError("virbox status payload must be an object")

    status = _parse_status(payload.get("normalized_status"))
    features = frozenset(_parse_feature(item) for item in payload.get("features", []) if str(item).strip())
    return LicenseSnapshot(
        status=status,
        features=features,
        expires_at=_optional_text(payload.get("expires_at")),
        grace_until=_optional_text(payload.get("grace_until")),
        device_id_hash=_optional_text(payload.get("device_id_hash")),
        license_suffix=_optional_text(payload.get("license_suffix")),
        error_code=_optional_text(payload.get("error_code")),
        error_message=_optional_text(payload.get("error_message")),
    )


def _parse_status(value: object) -> LicenseStatus:
    raw = str(value or "").strip()
    try:
        return LicenseStatus(raw)
    except ValueError as exc:
        raise VirboxReadError(f"unknown virbox status: {raw or '<empty>'}") from exc


def _parse_feature(value: object) -> LicenseFeature:
    raw = str(value or "").strip()
    try:
        return LicenseFeature(raw)
    except ValueError as exc:
        raise VirboxReadError(f"unknown virbox feature: {raw or '<empty>'}") from exc


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
