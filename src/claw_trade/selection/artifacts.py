from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path


class SelectionArtifactError(ValueError):
    def __init__(self, code: str, reason: str) -> None:
        super().__init__(f"{code}: {reason}")
        self.code = code
        self.reason = reason


@dataclass(frozen=True)
class SelectionArtifactWriteResult:
    uri: str
    path: Path
    content_sha256: str
    size_bytes: int
    readback_status: str
    verification_log_ref: str


class SelectionFileArtifactBackend:
    def __init__(self, *, root: Path | None = None) -> None:
        self._root = root or Path("runs/selection/artifacts")

    def write_text_verified(self, *, uri: str, content: str) -> SelectionArtifactWriteResult:
        path = self.uri_to_path(uri)
        path.parent.mkdir(parents=True, exist_ok=True)
        expected = content.encode("utf-8")
        expected_sha = sha256(expected).hexdigest()
        path.write_bytes(expected)

        readback = path.read_bytes()
        readback_sha = sha256(readback).hexdigest()
        if len(readback) != len(expected):
            raise SelectionArtifactError(
                "candidate_pack_integrity_failed",
                "artifact readback size mismatch",
            )
        if readback_sha != expected_sha:
            raise SelectionArtifactError(
                "candidate_pack_integrity_failed",
                "artifact readback sha256 mismatch",
            )

        verification_path = self._verification_log_path(path)
        verification_payload = {
            "uri": uri,
            "path": str(path),
            "status": "verified",
            "expected_sha256": expected_sha,
            "readback_sha256": readback_sha,
            "size_bytes": len(readback),
            "verified_at": _isoformat(_utc_now()),
        }
        verification_path.write_text(
            f"{json.dumps(verification_payload, ensure_ascii=False, indent=2)}\n",
            encoding="utf-8",
        )
        return SelectionArtifactWriteResult(
            uri=uri,
            path=path,
            content_sha256=readback_sha,
            size_bytes=len(readback),
            readback_status="verified",
            verification_log_ref=self.path_to_uri(verification_path),
        )

    def write_json_verified(self, *, uri: str, payload: dict[str, object]) -> SelectionArtifactWriteResult:
        return self.write_text_verified(
            uri=uri,
            content=f"{json.dumps(payload, ensure_ascii=False, indent=2)}\n",
        )

    def uri_to_path(self, uri: str) -> Path:
        prefix = "local://selection/"
        if not uri.startswith(prefix):
            raise SelectionArtifactError(
                "candidate_pack_integrity_failed",
                f"unsupported artifact uri: {uri}",
            )
        relative = uri[len(prefix) :].strip("/")
        if not relative or ".." in relative.split("/"):
            raise SelectionArtifactError(
                "candidate_pack_integrity_failed",
                f"unsafe artifact uri path: {uri}",
            )
        return self._root / relative

    def path_to_uri(self, path: Path) -> str:
        try:
            relative = path.resolve().relative_to(self._root.resolve())
        except ValueError as exc:
            raise SelectionArtifactError(
                "candidate_pack_integrity_failed",
                f"path outside artifact root: {path}",
            ) from exc
        return f"local://selection/{relative.as_posix()}"

    def _verification_log_path(self, path: Path) -> Path:
        suffix = path.suffix
        if not suffix:
            return path.with_name(f"{path.name}.readback-verify.json")
        return path.with_suffix(f"{suffix}.readback-verify.json")


def _utc_now() -> datetime:
    return datetime.now(tz=UTC)


def _isoformat(value: datetime) -> str:
    utc = value.astimezone(UTC) if value.tzinfo is not None else value.replace(tzinfo=UTC)
    return utc.isoformat().replace("+00:00", "Z")
