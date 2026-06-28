#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Mapping
from typing import Any


OUTPUT_KEYS = (
    "raw_status",
    "normalized_status",
    "features",
    "expires_at",
    "grace_until",
    "device_id_hash",
    "license_suffix",
    "error_code",
    "error_message",
)


def main() -> int:
    command = os.environ.get("VIRBOX_STATUS_COMMAND", "").strip()
    if not command:
        _write_error("runtime_unavailable", "VIRBOX_STATUS_COMMAND is not set")
        return 2

    try:
        timeout_seconds = float(os.environ.get("VIRBOX_STATUS_TIMEOUT_SECONDS", "5"))
    except ValueError:
        _write_error("runtime_unavailable", "VIRBOX_STATUS_TIMEOUT_SECONDS must be a number")
        return 2

    try:
        completed = subprocess.run(
            [command],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except OSError as exc:
        _write_error("runtime_unavailable", str(exc))
        return 2
    except subprocess.TimeoutExpired:
        _write_error("runtime_unavailable", "Virbox status command timed out")
        return 2

    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "Virbox status command failed").strip()
        _write_error("runtime_unavailable", message)
        return completed.returncode

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        _write_error("runtime_unavailable", "Virbox status command returned invalid JSON")
        return 2

    if not isinstance(payload, Mapping):
        _write_error("runtime_unavailable", "Virbox status command must return a JSON object")
        return 2

    normalized_status = _optional_text(payload.get("normalized_status"))
    if not normalized_status:
        _write_error("runtime_unavailable", "Virbox status JSON missing normalized_status")
        return 2

    features = payload.get("features", [])
    if not isinstance(features, list) or not all(isinstance(item, str) for item in features):
        _write_error("runtime_unavailable", "Virbox status JSON features must be a string list")
        return 2

    output: dict[str, Any] = {key: payload.get(key) for key in OUTPUT_KEYS}
    output["features"] = features
    sys.stdout.write(json.dumps(output, ensure_ascii=False, sort_keys=True) + "\n")
    return 0


def _write_error(status: str, message: str) -> None:
    payload = {
        "raw_status": None,
        "normalized_status": status,
        "features": [],
        "expires_at": None,
        "grace_until": None,
        "device_id_hash": None,
        "license_suffix": None,
        "error_code": "virbox_probe_error",
        "error_message": message,
    }
    sys.stdout.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


if __name__ == "__main__":
    raise SystemExit(main())
