from __future__ import annotations

import importlib
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from urllib.error import HTTPError, URLError

from observability import emit_json_log, record_metric, trace_span

SOCIAL_EVIDENCE_TARGET_INVALID = "SOCIAL_EVIDENCE_TARGET_INVALID"
SOCIAL_RAW_PAYLOAD_WRITE_FAILED = "SOCIAL_RAW_PAYLOAD_WRITE_FAILED"
SOCIAL_PACK_EVIDENCE_WRITE_FAILED = "SOCIAL_PACK_EVIDENCE_WRITE_FAILED"
SOCIAL_SIGNAL_EVIDENCE_MISSING = "SOCIAL_SIGNAL_EVIDENCE_MISSING"
SOCIAL_EVIDENCE_REDACTION_FAILED = "SOCIAL_EVIDENCE_REDACTION_FAILED"
SOCIAL_OPENVIKING_TIMEOUT = "SOCIAL_OPENVIKING_TIMEOUT"
SOCIAL_OPENVIKING_AUTH_FAILED = "SOCIAL_OPENVIKING_AUTH_FAILED"
SOCIAL_OPENVIKING_RECEIPT_HASH_MISMATCH = "SOCIAL_OPENVIKING_RECEIPT_HASH_MISMATCH"
SOCIAL_OPENVIKING_SERVICE_ERROR = "SOCIAL_OPENVIKING_SERVICE_ERROR"
SOCIAL_LOCAL_AUDIT_COPY_WRITE_FAILED = "SOCIAL_LOCAL_AUDIT_COPY_WRITE_FAILED"
_SOCIAL_WORKER_ID = "social_analyst"
_VIKING_WORKFLOW_PREFIX = "viking://resources/workflow"
_OPENVIKING_BACKEND_ENV = "CLAW_TRADE_OPENVIKING_BACKEND"
_OPENVIKING_EVIDENCE_WRITER_ENV = "CN_A_SOCIAL_OPENVIKING_EVIDENCE_WRITER"
_REDACTED_VALUE = "***REDACTED***"
_SENSITIVE_KEYWORDS = (
    "credential",
    "cookie",
    "token",
    "authorization",
    "session",
    "user_id",
    "userid",
    "uid",
)


@dataclass(frozen=True)
class EvidenceWriteTarget:
    run_id: str
    stage: str
    worker_id: str
    call_id: str
    evidence_root: str
    openviking_l2_write_target_root: str | None = None


class SocialEvidenceError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class RawPayloadEvidence:
    provider: str
    endpoint: str
    raw_index: int
    raw_payload_ref: str
    local_audit_path: str
    payload_hash: str
    redacted: bool
    row_count: int


@dataclass(frozen=True)
class RawPayloadEvidenceResult:
    ok: bool
    evidence: RawPayloadEvidence | None
    error_code: str | None


@dataclass(frozen=True)
class SocialEvidence:
    pack_path: str
    provider_attempts_path: str
    cache_inspection_path: str
    raw_payload_refs: list[str]
    content_hash: str


@dataclass(frozen=True)
class PackEvidenceWriteResult:
    ok: bool
    evidence: SocialEvidence | None
    error_code: str | None
    cause_error_code: str | None
    warnings: tuple[str, ...]


@dataclass(frozen=True)
class OpenVikingWriteRequest:
    uri: str
    content_type: str
    content_sha256: str
    body: bytes
    run_id: str
    stage: str
    worker_id: str
    call_id: str
    timeout_ms: int


@dataclass(frozen=True)
class OpenVikingWriteReceipt:
    ok: bool
    uri: str
    receipt_id: str | None
    persisted_sha256: str | None
    status_code: int | None
    error_code: str | None
    error_message: str | None
    retryable: bool


class OpenVikingEvidenceWriter(Protocol):
    def write_json(self, req: OpenVikingWriteRequest) -> OpenVikingWriteReceipt: ...


def validate_evidence_target(target: EvidenceWriteTarget) -> None:
    if not target.run_id.strip():
        raise SocialEvidenceError(SOCIAL_EVIDENCE_TARGET_INVALID, "run_id 不能为空")
    if not target.stage.strip():
        raise SocialEvidenceError(SOCIAL_EVIDENCE_TARGET_INVALID, "stage 不能为空")
    if target.worker_id != _SOCIAL_WORKER_ID:
        raise SocialEvidenceError(
            SOCIAL_EVIDENCE_TARGET_INVALID,
            f"worker_id 必须为 {_SOCIAL_WORKER_ID}",
        )
    if not target.call_id.strip():
        raise SocialEvidenceError(SOCIAL_EVIDENCE_TARGET_INVALID, "call_id 不能为空")
    if not target.evidence_root.strip():
        raise SocialEvidenceError(SOCIAL_EVIDENCE_TARGET_INVALID, "evidence_root 不能为空")


def build_openviking_l2_evidence_uri(target: EvidenceWriteTarget, kind: str, name: str) -> str:
    validate_evidence_target(target)
    _validate_path_segment(kind, field_name="kind")
    _validate_path_segment(name, field_name="name")
    l2_root = _openviking_l2_root(target)
    return (
        f"{l2_root}/{target.run_id}/{target.stage}/{target.worker_id}/{target.call_id}"
        f"/evidence/{kind}/{name}"
    )


def build_local_audit_call_dir(target: EvidenceWriteTarget) -> Path:
    validate_evidence_target(target)
    return Path(target.evidence_root) / target.run_id / target.stage / target.worker_id / target.call_id


def build_local_audit_path(target: EvidenceWriteTarget, kind: str, name: str) -> Path:
    _validate_path_segment(kind, field_name="kind")
    _validate_path_segment(name, field_name="name")
    return build_local_audit_call_dir(target) / kind / name


def canonical_json_sha256(body: Any) -> str:
    canonical = _canonical_json_bytes(body)
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def write_raw_payload_evidence(
    target: EvidenceWriteTarget,
    provider: str,
    endpoint: str,
    raw_payload: Any,
    *,
    writer: OpenVikingEvidenceWriter | None = None,
) -> RawPayloadEvidence:
    record_metric("social.evidence.write.count", tags={"kind": "provider_raw", "endpoint": endpoint})
    evidence: RawPayloadEvidence | None = None
    trapped_error: SocialEvidenceError | None = None
    with trace_span(
        "social.evidence.write_raw",
        run_id=target.run_id,
        call_id=target.call_id,
        worker_id=target.worker_id,
        endpoint=endpoint,
    ):
        try:
            validate_evidence_target(target)
            try:
                redacted_payload = redact_sensitive_fields(raw_payload)
            except Exception as exc:  # pragma: no cover - 防御性兜底，逻辑错误应由测试提前发现
                raise SocialEvidenceError(SOCIAL_EVIDENCE_REDACTION_FAILED, f"脱敏失败: {exc}") from exc

            payload_hash = canonical_json_sha256(redacted_payload)
            payload_hash_token = payload_hash.replace(":", "_")
            name = f"{provider}_{endpoint}_{payload_hash_token}.json"
            l2_uri = build_openviking_l2_evidence_uri(target, kind="provider_raw", name=name)

            req = OpenVikingWriteRequest(
                uri=l2_uri,
                content_type="application/json",
                content_sha256=payload_hash,
                body=_canonical_json_bytes(redacted_payload),
                run_id=target.run_id,
                stage=target.stage,
                worker_id=target.worker_id,
                call_id=target.call_id,
                timeout_ms=3000,
            )
            receipt = _write_raw_payload_via_openviking(req=req, writer=writer)
            if receipt.persisted_sha256 != payload_hash:
                raise SocialEvidenceError(
                    SOCIAL_OPENVIKING_RECEIPT_HASH_MISMATCH,
                    (
                        "OpenViking receipt hash 不匹配: "
                        f"expected={payload_hash} actual={receipt.persisted_sha256!r}"
                    ),
                )

            local_path = build_local_audit_path(target, kind="provider_raw", name=name)
            try:
                _write_local_audit_copy(local_path, redacted_payload)
            except OSError:
                # OpenViking L2 已成功时，本地审计副本失败不改变权威写入事实。
                pass

            evidence = RawPayloadEvidence(
                provider=provider,
                endpoint=endpoint,
                raw_index=-1,
                raw_payload_ref=l2_uri,
                local_audit_path=str(local_path),
                payload_hash=payload_hash,
                redacted=True,
                row_count=_count_rows(redacted_payload),
            )
        except SocialEvidenceError as exc:
            trapped_error = exc
            raise
    if trapped_error is not None:
        raise trapped_error
    if evidence is None:
        raise SocialEvidenceError(SOCIAL_RAW_PAYLOAD_WRITE_FAILED, "raw payload 证据写入返回值为空")
    return evidence


def try_write_raw_payload_evidence(
    target: EvidenceWriteTarget,
    provider: str,
    endpoint: str,
    raw_payload: Any,
    *,
    writer: OpenVikingEvidenceWriter | None = None,
) -> RawPayloadEvidenceResult:
    try:
        evidence = write_raw_payload_evidence(
            target=target,
            provider=provider,
            endpoint=endpoint,
            raw_payload=raw_payload,
            writer=writer,
        )
        if not isinstance(evidence, RawPayloadEvidence):
            raise SocialEvidenceError(SOCIAL_RAW_PAYLOAD_WRITE_FAILED, "raw payload 证据写入返回值非法")
    except SocialEvidenceError as exc:
        record_metric("social.evidence.write_error.count", tags={"kind": "provider_raw", "code": exc.code})
        emit_json_log(
            level="ERROR",
            event="social.evidence.write_raw.error",
            code=exc.code,
            run_id=target.run_id,
            call_id=target.call_id,
            worker_id=target.worker_id,
            endpoint=endpoint,
        )
        return RawPayloadEvidenceResult(ok=False, evidence=None, error_code=exc.code)
    return RawPayloadEvidenceResult(ok=True, evidence=evidence, error_code=None)


def write_pack_evidence(
    target: EvidenceWriteTarget,
    pack_body: dict[str, Any],
    attempts: list[Any],
    cache_inspections: list[Any],
    pack_body_hash: str,
    *,
    writer: OpenVikingEvidenceWriter | None = None,
) -> PackEvidenceWriteResult:
    record_metric("social.evidence.write.count", tags={"kind": "pack"})
    try:
        result: PackEvidenceWriteResult | None = None
        trapped_error: SocialEvidenceError | None = None
        with trace_span(
            "social.evidence.write_pack",
            run_id=target.run_id,
            call_id=target.call_id,
            worker_id=target.worker_id,
            endpoint="pack",
        ):
            try:
                validate_evidence_target(target)
                pack_hash = _normalize_sha256(pack_body_hash) or canonical_json_sha256(pack_body)
                attempts_hash = canonical_json_sha256(attempts)
                cache_hash = canonical_json_sha256(cache_inspections)

                warnings: list[str] = []
                pack_uri = _require_evidence_uri(
                    _write_json_evidence_artifact(
                        target=target,
                        kind="pack",
                        name="social_sentiment_pack.json",
                        body=pack_body,
                        content_hash=pack_hash,
                        warnings=warnings,
                        writer=writer,
                    ),
                    "social_sentiment_pack.json",
                )
                attempts_uri = _require_evidence_uri(
                    _write_json_evidence_artifact(
                        target=target,
                        kind="pack",
                        name="provider_attempts.json",
                        body=attempts,
                        content_hash=attempts_hash,
                        warnings=warnings,
                        writer=writer,
                    ),
                    "provider_attempts.json",
                )
                cache_uri = _require_evidence_uri(
                    _write_json_evidence_artifact(
                        target=target,
                        kind="pack",
                        name="cache_inspection.json",
                        body=cache_inspections,
                        content_hash=cache_hash,
                        warnings=warnings,
                        writer=writer,
                    ),
                    "cache_inspection.json",
                )

                content_hash = canonical_json_sha256(
                    {"pack": pack_hash, "attempts": attempts_hash, "cache": cache_hash}
                )
                evidence = SocialEvidence(
                    pack_path=pack_uri,
                    provider_attempts_path=attempts_uri,
                    cache_inspection_path=cache_uri,
                    raw_payload_refs=_extract_raw_payload_refs(attempts),
                    content_hash=content_hash,
                )
                result = PackEvidenceWriteResult(
                    ok=True,
                    evidence=evidence,
                    error_code=None,
                    cause_error_code=None,
                    warnings=tuple(warnings),
                )
            except SocialEvidenceError as exc:
                trapped_error = exc
                raise
        if trapped_error is not None:
            raise trapped_error
        if result is None:
            raise SocialEvidenceError(SOCIAL_PACK_EVIDENCE_WRITE_FAILED, "pack 证据写入返回值为空")
        return result
    except SocialEvidenceError as exc:
        record_metric("social.evidence.write_error.count", tags={"kind": "pack", "code": exc.code})
        emit_json_log(
            level="ERROR",
            event="social.evidence.write_pack.error",
            code=exc.code,
            run_id=target.run_id,
            call_id=target.call_id,
            worker_id=target.worker_id,
            endpoint="pack",
        )
        return PackEvidenceWriteResult(
            ok=False,
            evidence=None,
            error_code=SOCIAL_PACK_EVIDENCE_WRITE_FAILED,
            cause_error_code=exc.code,
            warnings=(),
        )


def _require_evidence_uri(value: object, artifact_name: str) -> str:
    uri = _as_optional_string(value)
    if uri is None:
        raise SocialEvidenceError(SOCIAL_PACK_EVIDENCE_WRITE_FAILED, f"{artifact_name} evidence uri 为空")
    return uri


def validate_signal_evidence(signal: dict[str, Any]) -> bool:
    content_hash = _normalize_sha256(signal.get("content_hash"))
    if content_hash is None:
        return False
    raw_payload_ref = _as_optional_string(signal.get("raw_payload_ref"))
    payload_hash = _normalize_sha256(signal.get("payload_hash"))
    if raw_payload_ref and _is_openviking_l2_uri(raw_payload_ref):
        return True
    if payload_hash is not None:
        return True
    return False


def redact_sensitive_fields(payload: Any) -> Any:
    if isinstance(payload, dict):
        result: dict[str, Any] = {}
        for key, value in payload.items():
            if _is_sensitive_key(str(key)):
                result[key] = _REDACTED_VALUE
            else:
                result[key] = redact_sensitive_fields(value)
        return result
    if isinstance(payload, list):
        return [redact_sensitive_fields(item) for item in payload]
    if isinstance(payload, tuple):
        return [redact_sensitive_fields(item) for item in payload]
    return payload


def _validate_path_segment(value: str, *, field_name: str) -> None:
    if not value.strip():
        raise SocialEvidenceError(SOCIAL_EVIDENCE_TARGET_INVALID, f"{field_name} 不能为空")
    if "/" in value or value in {".", ".."}:
        raise SocialEvidenceError(SOCIAL_EVIDENCE_TARGET_INVALID, f"{field_name} 非法: {value}")


def resolve_openviking_evidence_writer(
    writer: OpenVikingEvidenceWriter | None = None,
) -> OpenVikingEvidenceWriter:
    if writer is not None:
        return writer

    explicit_writer_spec = os.environ.get(_OPENVIKING_EVIDENCE_WRITER_ENV, "").strip()
    if explicit_writer_spec:
        resolved = _load_runtime_object(explicit_writer_spec, env_key=_OPENVIKING_EVIDENCE_WRITER_ENV)
        if callable(getattr(resolved, "write_json", None)):
            return resolved  # type: ignore[return-value]
        raise SocialEvidenceError(
            SOCIAL_RAW_PAYLOAD_WRITE_FAILED,
            f"{_OPENVIKING_EVIDENCE_WRITER_ENV} 未提供 write_json 能力",
        )

    backend_spec = os.environ.get(_OPENVIKING_BACKEND_ENV, "").strip()
    if backend_spec:
        backend = _load_runtime_object(backend_spec, env_key=_OPENVIKING_BACKEND_ENV)
        return _OpenVikingBackendWriter(backend=backend)

    try:
        module = importlib.import_module("claw_trade.artifacts.openviking_backend_http")
        factory = getattr(module, "create_default_backend")
    except Exception as exc:
        raise SocialEvidenceError(
            SOCIAL_RAW_PAYLOAD_WRITE_FAILED,
            f"OpenViking writer 未绑定，且默认 backend 加载失败: {exc}",
        ) from exc
    backend = factory()
    return _OpenVikingBackendWriter(backend=backend)


def _write_raw_payload_via_openviking(
    *,
    req: OpenVikingWriteRequest,
    writer: OpenVikingEvidenceWriter | None,
) -> OpenVikingWriteReceipt:
    resolved_writer = resolve_openviking_evidence_writer(writer)
    try:
        receipt = resolved_writer.write_json(req)
    except TimeoutError as exc:
        raise SocialEvidenceError(SOCIAL_OPENVIKING_TIMEOUT, f"OpenViking 写入超时: {exc}") from exc
    except Exception as exc:
        raise _map_openviking_write_exception(exc) from exc

    if receipt.ok:
        return receipt
    code = _map_openviking_receipt_to_error_code(receipt)
    raise SocialEvidenceError(code, f"OpenViking 写入失败: {receipt.error_message or receipt.error_code or 'unknown'}")


def _map_openviking_receipt_to_error_code(receipt: OpenVikingWriteReceipt) -> str:
    if receipt.status_code in {401, 403}:
        return SOCIAL_OPENVIKING_AUTH_FAILED
    if receipt.error_code == "timeout" or receipt.status_code == 408:
        return SOCIAL_OPENVIKING_TIMEOUT
    if receipt.status_code is not None and receipt.status_code >= 500:
        return SOCIAL_OPENVIKING_SERVICE_ERROR
    return SOCIAL_RAW_PAYLOAD_WRITE_FAILED


def _map_openviking_write_exception(exc: Exception) -> SocialEvidenceError:
    category = getattr(exc, "category", None)
    if isinstance(exc, HTTPError):
        if exc.code in {401, 403}:
            return SocialEvidenceError(SOCIAL_OPENVIKING_AUTH_FAILED, f"OpenViking 认证失败: HTTP {exc.code}")
        if exc.code >= 500:
            return SocialEvidenceError(SOCIAL_OPENVIKING_SERVICE_ERROR, f"OpenViking 服务错误: HTTP {exc.code}")
    if isinstance(exc, URLError):
        return SocialEvidenceError(SOCIAL_OPENVIKING_TIMEOUT, f"OpenViking 网络异常: {exc}")
    if category == "permission_denied":
        return SocialEvidenceError(SOCIAL_OPENVIKING_AUTH_FAILED, f"OpenViking 认证失败: {exc}")
    if category == "hash_mismatch":
        return SocialEvidenceError(SOCIAL_OPENVIKING_RECEIPT_HASH_MISMATCH, str(exc))
    if category == "backend_unavailable":
        return SocialEvidenceError(SOCIAL_OPENVIKING_SERVICE_ERROR, f"OpenViking 服务不可用: {exc}")
    return SocialEvidenceError(SOCIAL_RAW_PAYLOAD_WRITE_FAILED, f"OpenViking 写入失败: {exc}")


def _load_runtime_object(spec: str, *, env_key: str) -> object:
    module_name, separator, attr_name = spec.partition(":")
    if not module_name or separator != ":" or not attr_name:
        raise SocialEvidenceError(SOCIAL_RAW_PAYLOAD_WRITE_FAILED, f"{env_key} 格式错误，必须是 module:attr")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        raise SocialEvidenceError(SOCIAL_RAW_PAYLOAD_WRITE_FAILED, f"{env_key} 导入模块失败: {exc}") from exc
    if not hasattr(module, attr_name):
        raise SocialEvidenceError(SOCIAL_RAW_PAYLOAD_WRITE_FAILED, f"{env_key} 指向属性不存在: {spec}")
    symbol = getattr(module, attr_name)
    if isinstance(symbol, type):
        try:
            return symbol()
        except Exception as exc:
            raise SocialEvidenceError(SOCIAL_RAW_PAYLOAD_WRITE_FAILED, f"{env_key} 类实例化失败: {exc}") from exc
    if callable(symbol):
        try:
            return symbol()
        except TypeError:
            return symbol
        except Exception as exc:
            raise SocialEvidenceError(SOCIAL_RAW_PAYLOAD_WRITE_FAILED, f"{env_key} 工厂调用失败: {exc}") from exc
    return symbol


def _canonical_json_bytes(body: Any) -> bytes:
    return json.dumps(
        body,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
        allow_nan=False,
    ).encode("utf-8")


def _count_rows(payload: Any) -> int:
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        rows = payload.get("rows")
        if isinstance(rows, list):
            return len(rows)
        return len(payload)
    return 1


def _is_sensitive_key(key: str) -> bool:
    lowered = key.strip().lower()
    return any(token in lowered for token in _SENSITIVE_KEYWORDS)


def _normalize_sha256(value: object) -> str | None:
    text = _as_optional_string(value)
    if text is None:
        return None
    if text.startswith("sha256:") and len(text) == 71:
        return text
    return None


def _extract_raw_payload_refs(attempts: list[Any]) -> list[str]:
    refs: list[str] = []
    for attempt in attempts:
        value: object
        if isinstance(attempt, dict):
            value = attempt.get("raw_payload_ref")
        else:
            value = getattr(attempt, "raw_payload_ref", None)
        ref = _as_optional_string(value)
        if ref and _is_openviking_l2_uri(ref):
            refs.append(ref)
    return sorted(set(refs))


def _openviking_l2_root(target: EvidenceWriteTarget) -> str:
    root = (target.openviking_l2_write_target_root or _VIKING_WORKFLOW_PREFIX).strip()
    if not root:
        raise SocialEvidenceError(SOCIAL_EVIDENCE_TARGET_INVALID, "OpenViking L2 root 不能为空")
    if not _is_openviking_l2_uri(root):
        raise SocialEvidenceError(SOCIAL_EVIDENCE_TARGET_INVALID, f"OpenViking L2 root 非法: {root}")
    return root.rstrip("/")


def _is_openviking_l2_uri(value: str) -> bool:
    return value.startswith("viking://")


def _write_json_evidence_artifact(
    *,
    target: EvidenceWriteTarget,
    kind: str,
    name: str,
    body: Any,
    content_hash: str,
    warnings: list[str],
    writer: OpenVikingEvidenceWriter | None,
) -> str:
    uri = build_openviking_l2_evidence_uri(target, kind=kind, name=name)
    req = OpenVikingWriteRequest(
        uri=uri,
        content_type="application/json",
        content_sha256=content_hash,
        body=_canonical_json_bytes(body),
        run_id=target.run_id,
        stage=target.stage,
        worker_id=target.worker_id,
        call_id=target.call_id,
        timeout_ms=3000,
    )
    receipt = _write_raw_payload_via_openviking(req=req, writer=writer)
    if receipt.persisted_sha256 != content_hash:
        raise SocialEvidenceError(
            SOCIAL_OPENVIKING_RECEIPT_HASH_MISMATCH,
            f"OpenViking receipt hash 不匹配: expected={content_hash} actual={receipt.persisted_sha256!r}",
        )

    local_path = build_local_audit_path(target, kind=kind, name=name)
    try:
        _write_local_audit_copy(local_path, body)
    except OSError:
        warnings.append(f"{SOCIAL_LOCAL_AUDIT_COPY_WRITE_FAILED}:{name}")
    return uri


def _write_local_audit_copy(path: Path, body: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_json_bytes(body) + b"\n")


class _OpenVikingBackendWriter:
    def __init__(self, backend: object) -> None:
        self._backend = backend

    def write_json(self, req: OpenVikingWriteRequest) -> OpenVikingWriteReceipt:
        if callable(getattr(self._backend, "write_json", None)):
            direct_receipt = self._backend.write_json(req)
            if isinstance(direct_receipt, OpenVikingWriteReceipt):
                return direct_receipt
            return _coerce_receipt(direct_receipt, request_uri=req.uri)

        write_verified = getattr(self._backend, "_write_verified_content", None)
        if not callable(write_verified):
            raise SocialEvidenceError(
                SOCIAL_RAW_PAYLOAD_WRITE_FAILED,
                "OpenViking backend 缺少 write_json 或 _write_verified_content",
            )
        content_text = req.body.decode("utf-8")
        operations: list[dict[str, object]] = []
        result = write_verified(
            uri=req.uri,
            content=content_text,
            operations=operations,
            operation_prefix="social.evidence.raw",
        )
        persisted_raw = result.get("sha256")
        if isinstance(persisted_raw, str) and not persisted_raw.startswith("sha256:"):
            persisted_raw = f"sha256:{persisted_raw}"
        return OpenVikingWriteReceipt(
            ok=True,
            uri=req.uri,
            receipt_id=None,
            persisted_sha256=persisted_raw if isinstance(persisted_raw, str) else None,
            status_code=200,
            error_code=None,
            error_message=None,
            retryable=False,
        )


def _coerce_receipt(payload: object, *, request_uri: str) -> OpenVikingWriteReceipt:
    if isinstance(payload, OpenVikingWriteReceipt):
        return payload
    if not isinstance(payload, dict):
        raise SocialEvidenceError(SOCIAL_RAW_PAYLOAD_WRITE_FAILED, "write_json 返回值非法")
    persisted = payload.get("persisted_sha256")
    if isinstance(persisted, str) and not persisted.startswith("sha256:"):
        persisted = f"sha256:{persisted}"
    return OpenVikingWriteReceipt(
        ok=bool(payload.get("ok", False)),
        uri=str(payload.get("uri") or request_uri),
        receipt_id=_as_optional_string(payload.get("receipt_id")),
        persisted_sha256=persisted if isinstance(persisted, str) else None,
        status_code=_as_optional_int(payload.get("status_code")),
        error_code=_as_optional_string(payload.get("error_code")),
        error_message=_as_optional_string(payload.get("error_message")),
        retryable=bool(payload.get("retryable", False)),
    )


def _as_optional_string(value: object) -> str | None:
    if isinstance(value, str):
        text = value.strip()
        return text if text else None
    return None


def _as_optional_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    return None
