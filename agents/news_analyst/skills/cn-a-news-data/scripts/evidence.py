from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from errors import E_EVIDENCE_WRITE_FAILED, NewsDataError
from models import EvidenceRefs, EvidenceWriteRequest
from observability import record_event, trace_span
from security import sanitize_error

_MASKED_VALUE = "***"
_SENSITIVE_KEY_TOKENS = (
    "token",
    "api_key",
    "authorization",
    "cookie",
    "passwd",
    "secret",
    "password",
    "bearer",
    "key",
)
_SAFE_FILE_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(token in lowered for token in _SENSITIVE_KEY_TOKENS)


def _maybe_sanitize_text(value: str) -> str:
    # Secret-looking payloads in this project are mostly key/value or header-like text.
    # Also handle bare bearer tokens without "=" or ":".
    if "=" in value or ":" in value:
        return sanitize_error(value)
    if "b" not in value and "B" not in value:
        return value
    if "bearer" not in value.lower():
        return value
    return sanitize_error(value)


def _sanitize_value(value: Any, field_name: str | None = None) -> Any:
    if field_name is not None and _is_sensitive_key(field_name):
        return _MASKED_VALUE
    if isinstance(value, dict):
        return {
            str(item_key): _sanitize_value(item_value, field_name=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, str):
        return _maybe_sanitize_text(value)
    return value


def _ensure_path_under_root(raw_ref: str, evidence_root: Path) -> Path:
    raw_path = Path(raw_ref)
    try:
        resolved_path = raw_path.resolve(strict=True)
    except Exception as exc:
        raise NewsDataError(
            code=E_EVIDENCE_WRITE_FAILED,
            message="provider raw ref is not readable",
            details={"raw_ref": raw_ref, "error": sanitize_error(exc)},
        ) from exc
    if not resolved_path.is_relative_to(evidence_root):
        raise NewsDataError(
            code=E_EVIDENCE_WRITE_FAILED,
            message="provider raw ref is outside evidence root",
            details={
                "raw_ref": raw_ref,
                "evidence_root": str(evidence_root),
            },
        )
    return resolved_path


def _atomic_write(target_path: Path, content: bytes) -> None:
    if target_path.exists():
        raise FileExistsError(f"target evidence file already exists: {target_path}")
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="wb",
            delete=False,
            dir=str(target_path.parent),
            prefix=f".{target_path.name}.tmp-",
        ) as file_handle:
            file_handle.write(content)
            tmp_path = Path(file_handle.name)
        os.replace(tmp_path, target_path)
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


def _safe_file_name(name: str) -> str:
    normalized = _SAFE_FILE_NAME_RE.sub("_", name).strip("._")
    if normalized == "":
        return "provider_raw"
    return normalized[:64]


def _build_provider_raw_file_name(index: int, source_path: Path, suffix: str) -> str:
    source_name = _safe_file_name(source_path.stem or source_path.name)
    source_digest = hashlib.sha256(str(source_path).encode("utf-8")).hexdigest()[:12]
    return f"{index:03d}_{source_name}_{source_digest}{suffix}"


def _serialize_provider_raw_payload(raw_path: Path) -> tuple[bytes, str]:
    try:
        raw_text = raw_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise NewsDataError(
            code=E_EVIDENCE_WRITE_FAILED,
            message="provider raw ref is not readable",
            details={"raw_ref": str(raw_path), "error": sanitize_error(exc)},
        ) from exc
    try:
        parsed_payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return _maybe_sanitize_text(raw_text).encode("utf-8"), ".txt"
    sanitized_payload = _sanitize_value(parsed_payload)
    return (
        json.dumps(
            sanitized_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8"),
        ".json",
    )


class EvidenceWriter:
    def write_pack(self, request: EvidenceWriteRequest) -> EvidenceRefs:
        with trace_span("news_data_pack.evidence"):
            try:
                context = request.context
                evidence_root = Path(context.evidence_root).resolve()
                resolved_raw_paths = [
                    _ensure_path_under_root(raw_ref=raw_ref, evidence_root=evidence_root)
                    for raw_ref in request.provider_raw_refs
                ]

                call_dir = (
                    evidence_root
                    / context.run_id
                    / context.stage
                    / context.worker_id
                    / context.call_id
                )
                call_dir.mkdir(parents=True, exist_ok=False)

                sanitized_pack = _sanitize_value(request.pack.to_dict())
                if not isinstance(sanitized_pack, dict):
                    raise NewsDataError(
                        code=E_EVIDENCE_WRITE_FAILED,
                        message="sanitized pack payload must be object",
                    )

                pack_json = json.dumps(
                    sanitized_pack, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                pack_bytes = pack_json.encode("utf-8")
                pack_path = call_dir / "news_data_pack.json"
                _atomic_write(pack_path, pack_bytes)
                record_event(
                    "news_evidence_file_written",
                    fields={"status": "written", "path": str(pack_path), "size": len(pack_bytes)},
                )

                provider_attempts = sanitized_pack.get("provider_attempts", [])
                provider_attempts_json = json.dumps(
                    provider_attempts, ensure_ascii=False, sort_keys=True, separators=(",", ":")
                )
                provider_attempts_bytes = provider_attempts_json.encode("utf-8")
                provider_attempts_path = call_dir / "provider_attempts.json"
                _atomic_write(provider_attempts_path, provider_attempts_bytes)
                record_event(
                    "news_evidence_file_written",
                    fields={
                        "status": "written",
                        "path": str(provider_attempts_path),
                        "size": len(provider_attempts_bytes),
                    },
                )

                provider_raw_dir = call_dir / "provider_raw"
                provider_raw_dir.mkdir(parents=True, exist_ok=False)
                copied_provider_raw_paths: list[str] = []
                for index, raw_path in enumerate(resolved_raw_paths):
                    raw_payload, suffix = _serialize_provider_raw_payload(raw_path)
                    target_name = _build_provider_raw_file_name(
                        index=index, source_path=raw_path, suffix=suffix
                    )
                    target_path = provider_raw_dir / target_name
                    _atomic_write(target_path, raw_payload)
                    copied_provider_raw_paths.append(str(target_path))
                    record_event(
                        "news_evidence_file_written",
                        fields={"status": "written", "path": str(target_path), "size": len(raw_payload)},
                    )

                content_hash = hashlib.sha256(pack_bytes).hexdigest()
                return EvidenceRefs(
                    pack_path=str(pack_path),
                    provider_attempts_path=str(provider_attempts_path),
                    provider_raw_paths=copied_provider_raw_paths,
                    content_hash=content_hash,
                )
            except NewsDataError:
                raise
            except Exception as exc:
                raise NewsDataError(
                    code=E_EVIDENCE_WRITE_FAILED,
                    message="failed to write evidence",
                    details={
                        "run_id": request.context.run_id,
                        "stage": request.context.stage,
                        "worker_id": request.context.worker_id,
                        "call_id": request.context.call_id,
                        "error": sanitize_error(exc),
                    },
                ) from exc
