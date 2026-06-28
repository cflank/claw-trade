from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any


_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class UpdateManifest:
    product: str
    channel: str
    version: str
    arch: str
    archive: str
    sha256: str
    created_at: str
    min_current_version: str
    notes: str = ""

    @classmethod
    def from_json(cls, raw: str | bytes) -> "UpdateManifest":
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("manifest JSON 格式错误。") from exc
        if not isinstance(payload, dict):
            raise ValueError("manifest 必须是 JSON object。")
        return cls.from_mapping(payload)

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "UpdateManifest":
        required = ("product", "channel", "version", "arch", "archive", "sha256", "created_at", "min_current_version")
        values: dict[str, str] = {}
        for key in required:
            value = payload.get(key)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"manifest 缺少字段: {key}")
            values[key] = value.strip()
        notes = payload.get("notes", "")
        if not isinstance(notes, str):
            raise ValueError("manifest notes 必须是字符串。")
        manifest = cls(notes=notes.strip(), **values)
        manifest.validate()
        return manifest

    def validate(self) -> None:
        if not _VERSION_RE.fullmatch(self.version):
            raise ValueError("manifest version 必须是 x.y.z。")
        if not _VERSION_RE.fullmatch(self.min_current_version):
            raise ValueError("manifest min_current_version 必须是 x.y.z。")
        if not _SHA256_RE.fullmatch(self.sha256):
            raise ValueError("manifest sha256 必须是 64 位小写十六进制。")
        if self.archive != PurePosixPath(self.archive).name:
            raise ValueError("manifest archive 必须是同目录文件名。")
        if "/" in self.archive or "\\" in self.archive or "\x00" in self.archive:
            raise ValueError("manifest archive 不能包含路径。")

    def assert_installable(
        self,
        *,
        current_version: str,
        product: str = "claw-trade",
        channel: str = "stable",
        arch: str = "linux-x86_64",
    ) -> None:
        if self.product != product:
            raise ValueError("manifest product 不匹配。")
        if self.channel != channel:
            raise ValueError("manifest channel 不匹配。")
        if self.arch != arch:
            raise ValueError("manifest arch 不匹配。")
        if _version_tuple(self.version) <= _version_tuple(current_version):
            raise ValueError("manifest version 不高于当前版本。")
        if _version_tuple(current_version) < _version_tuple(self.min_current_version):
            raise ValueError("当前版本低于 manifest 最低可升级版本。")


def _version_tuple(value: str) -> tuple[int, int, int]:
    if not _VERSION_RE.fullmatch(value):
        raise ValueError("版本号必须是 x.y.z。")
    return tuple(int(part) for part in value.split("."))  # type: ignore[return-value]
