from __future__ import annotations

import json
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_S16_ARTIFACT_DIR = PROJECT_ROOT / ".runtime" / "test-artifacts" / "settings-s16"
SETTINGS_UI_TEXT_DIR = PROJECT_ROOT / ".runtime" / "test-artifacts" / "settings-ui-text"


def read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise AssertionError(f"expected object json: {path}")
    return payload


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def ensure_s16_dirs() -> None:
    SETTINGS_S16_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    SETTINGS_UI_TEXT_DIR.mkdir(parents=True, exist_ok=True)


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    write_text(path, json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def lowercase_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).lower()

