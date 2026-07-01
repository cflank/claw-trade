from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from claw_trade.ui_backend.settings_service import UiBoundaryError

DEFAULT_SELECTION_AUTO_REFRESH_SETTINGS = {"enabled": False}
_DEFAULT_JSON_PATH = Path("runs/.ui-selection-auto-refresh-settings.json")


class SelectionAutoRefreshSettingsService:
    def __init__(
        self,
        *,
        store: Any | None = None,
        json_path: Path = _DEFAULT_JSON_PATH,
    ) -> None:
        self._store = store
        self._json_path = json_path

    def load_settings(self) -> dict[str, bool]:
        payload = self._read_payload()
        if payload is None:
            return dict(DEFAULT_SELECTION_AUTO_REFRESH_SETTINGS)
        return _validate_payload(payload)

    def save_settings(self, enabled: bool) -> dict[str, bool]:
        payload = _payload_from_enabled(enabled)
        if self._store is not None:
            self._store.write(payload)
        else:
            self._write_json(payload)
        return payload

    def reset_to_defaults(self) -> dict[str, bool]:
        if self._store is not None:
            self._store.clear()
        else:
            self._json_path.unlink(missing_ok=True)
        return dict(DEFAULT_SELECTION_AUTO_REFRESH_SETTINGS)

    def _read_payload(self) -> Mapping[str, Any] | None:
        if self._store is not None:
            try:
                payload = self._store.read()
            except ValueError as exc:
                raise UiBoundaryError("INVALID_INPUT", "选股自动刷新设置格式不正确。") from exc
            if not payload:
                return None
            if not isinstance(payload, Mapping):
                raise UiBoundaryError("INVALID_INPUT", "选股自动刷新设置格式不正确。")
            return payload
        if not self._json_path.exists():
            return None
        try:
            data = json.loads(self._json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise UiBoundaryError("INVALID_INPUT", "选股自动刷新设置文件格式不正确。") from exc
        if not isinstance(data, Mapping):
            raise UiBoundaryError("INVALID_INPUT", "选股自动刷新设置文件格式不正确。")
        return data

    def _write_json(self, payload: Mapping[str, bool]) -> None:
        self._json_path.parent.mkdir(parents=True, exist_ok=True)
        self._json_path.write_text(
            json.dumps(dict(payload), ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )


def _payload_from_enabled(enabled: bool) -> dict[str, bool]:
    if type(enabled) is not bool:
        raise UiBoundaryError("INVALID_INPUT", "选股自动刷新只能开启或关闭。")
    return {"enabled": enabled}


def _validate_payload(payload: Mapping[str, Any]) -> dict[str, bool]:
    return _payload_from_enabled(payload.get("enabled", True))
