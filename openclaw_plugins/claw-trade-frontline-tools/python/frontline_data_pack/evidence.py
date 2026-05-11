from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import io
import json
import os
import re
import time
from typing import Callable, Mapping, Protocol
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request
import uuid
import zipfile

from .config import OpenVikingConfig
from .errors import (
    L2_HASH_MISMATCH,
    L2_READBACK_FAILED,
    L2_TARGET_INVALID,
    L2_WRITE_FAILED,
    OPENVIKING_AUTH_FAILED,
    OPENVIKING_CONFIG_INVALID,
    FrontlineConfigError,
    FrontlineValidationError,
)
from .models import L2WriteReceipt, L2WriteRequest, L2WriteTarget
from .security import redact_secret, summarize_provider_error, validate_l2_target_path


_DEFAULT_TIMEOUT_SEC = 10.0
_WRITE_RETRY_LIMIT = 5
_WRITE_RETRY_BASE_SEC = 0.1
_WRITE_PATH = "/api/v1/content/write"
_TEMP_UPLOAD_PATH = "/api/v1/resources/temp_upload"
_PACK_IMPORT_PATH = "/api/v1/pack/import"
_STAT_PATH = "/api/v1/fs/stat"
_TREE_PATH = "/api/v1/fs/tree"
_READ_PATH = "/api/v1/content/read"
_DOWNLOAD_PATH = "/api/v1/content/download"
_VALID_AUTH_MODES = frozenset({"none", "bearer"})
_BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/\-=]+")
_NOT_FOUND_TOKEN_RE = re.compile(r"(?i)\bnot[_\s-]?found\b")
_DERIVED_TEXT_FILE_RE = re.compile(r"^(overview|summary|abstract)([._-].+)?$")
_SEMANTIC_NAME_RE = re.compile(r"(^|[._-])semantic([._-]|$)")


@dataclass(frozen=True)
class OpenVikingWriteResult:
    receipt_id: str | None
    verification_uri: str | None = None


@dataclass(frozen=True)
class OpenVikingStatResult:
    size_bytes: int | None
    sha256: str | None
    exists: bool = True


class OpenVikingEvidenceClient(Protocol):
    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult: ...

    def stat(self, *, uri: str) -> OpenVikingStatResult: ...

    def download(self, *, uri: str) -> bytes: ...

    def read(self, *, uri: str) -> bytes: ...


Transport = Callable[[str, str, Mapping[str, str], bytes, float], tuple[int, bytes]]


class OpenVikingClientError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class OpenVikingHttpEvidenceClient:
    def __init__(
        self,
        config: OpenVikingConfig,
        *,
        timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
        transport: Transport | None = None,
    ) -> None:
        parsed = urllib_parse.urlsplit(config.base_uri)
        if parsed.scheme not in {"http", "https"}:
            raise FrontlineConfigError(
                OPENVIKING_CONFIG_INVALID,
                "HTTP OpenViking client 只支持 http/https base URI",
            )
        self._config = config
        self._base_uri = config.base_uri.rstrip("/")
        self._timeout_sec = timeout_sec
        self._transport = transport or _urllib_transport

    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        _ = metadata
        try:
            content_text = content_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise OpenVikingClientError(
                "OpenViking content.write 仅支持 UTF-8 文本内容，当前 payload 含非 UTF-8 二进制",
            ) from exc
        payload = {
            "uri": uri,
            "mode": "replace",
            "content": content_text,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        try:
            _, raw = self._write_content_with_retry(body=body)
            return OpenVikingWriteResult(receipt_id=_extract_receipt_id(raw))
        except OpenVikingClientError as exc:
            is_not_found = _is_not_found_error(exc)
            is_directory_write = _is_directory_write_error(exc)
            if not (is_not_found or is_directory_write):
                raise
            if is_directory_write:
                existing_uri = self._try_resolve_materialized_file_uri(uri=uri)
                if existing_uri is not None:
                    update_payload = {
                        "uri": existing_uri,
                        "mode": "replace",
                        "content": content_text,
                    }
                    update_body = json.dumps(update_payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
                        "utf-8"
                    )
                    _, raw = self._write_content_with_retry(body=update_body)
                    return OpenVikingWriteResult(
                        receipt_id=_extract_receipt_id(raw),
                        verification_uri=existing_uri,
                    )
            _, raw = self._create_missing_file_via_pack_import(uri=uri, content_text=content_text)
            return OpenVikingWriteResult(
                receipt_id=_extract_receipt_id(raw),
                verification_uri=uri,
            )

    def _temp_upload(
        self,
        *,
        content_bytes: bytes,
        content_type: str,
        filename: str,
    ) -> str:
        multipart_body, boundary = _build_temp_upload_multipart_with_name(
            content_bytes=content_bytes,
            content_type=content_type,
            filename=filename,
        )
        _, raw = self._call(
            "POST",
            _TEMP_UPLOAD_PATH,
            body=multipart_body,
            content_type=f"multipart/form-data; boundary={boundary}",
        )
        decoded = _decode_json_object(raw)
        temp_file_id = _read_optional_text(decoded, "temp_file_id")
        if temp_file_id is None:
            result = decoded.get("result")
            if isinstance(result, Mapping):
                temp_file_id = _read_optional_text(result, "temp_file_id")
        if temp_file_id is None:
            raise OpenVikingClientError("OpenViking temp_upload 响应缺少 temp_file_id")
        return temp_file_id

    def _pack_import(self, *, temp_file_id: str, parent_uri: str) -> tuple[int, bytes]:
        payload = {
            "temp_file_id": temp_file_id,
            "parent": parent_uri,
            "force": True,
            "vectorize": False,
        }
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        return self._call("POST", _PACK_IMPORT_PATH, body=body, content_type="application/json")

    def _create_missing_file_via_pack_import(self, *, uri: str, content_text: str) -> tuple[int, bytes]:
        parent_uri, ovpack_bytes = _build_single_file_ovpack_bytes(uri=uri, content_text=content_text)
        temp_file_id = self._temp_upload(
            content_bytes=ovpack_bytes,
            content_type="application/octet-stream",
            filename=f"seed-{uuid.uuid4().hex}.ovpack",
        )
        return self._pack_import(temp_file_id=temp_file_id, parent_uri=parent_uri)

    def _resolve_verification_uri(self, *, uri: str) -> str:
        path = f"{_TREE_PATH}?{urllib_parse.urlencode({'uri': uri})}"
        _, raw = self._call("GET", path)
        decoded = _decode_json_object(raw)
        nodes = decoded.get("result")
        if not isinstance(nodes, list):
            raise OpenVikingClientError("OpenViking fs.tree 返回体缺少 result 列表，无法定位 add_resource 文件")
        candidates: list[tuple[str, Mapping[str, object]]] = []
        for node in nodes:
            if not isinstance(node, Mapping):
                continue
            if bool(node.get("isDir", False)):
                continue
            node_uri = _read_optional_text(node, "uri")
            if not node_uri:
                continue
            candidates.append((node_uri, node))

        exact_candidates = [candidate_uri for candidate_uri, _ in candidates if candidate_uri == uri]
        if len(exact_candidates) == 1:
            return exact_candidates[0]
        if len(exact_candidates) > 1:
            raise OpenVikingClientError(
                f"OpenViking add_resource 返回多个候选文件，无法唯一定位: count={len(exact_candidates)}"
            )

        filtered_candidates = [
            candidate_uri
            for candidate_uri, node in candidates
            if not _is_openviking_derived_node(node=node, node_uri=candidate_uri)
        ]
        if len(filtered_candidates) == 1:
            return filtered_candidates[0]
        if len(filtered_candidates) > 1:
            raise OpenVikingClientError(
                f"OpenViking add_resource 返回多个候选文件，无法唯一定位: count={len(filtered_candidates)}"
            )
        raise OpenVikingClientError("OpenViking add_resource 未生成可校验文件，无法执行 readback/stat 校验")

    def _try_resolve_materialized_file_uri(self, *, uri: str) -> str | None:
        try:
            return self._resolve_verification_uri(uri=uri)
        except OpenVikingClientError:
            return None

    def _write_content_with_retry(self, *, body: bytes) -> tuple[int, bytes]:
        for attempt in range(_WRITE_RETRY_LIMIT):
            try:
                return self._call("POST", _WRITE_PATH, body=body, content_type="application/json")
            except OpenVikingClientError as exc:
                if not _is_resource_busy_error(exc) or attempt + 1 >= _WRITE_RETRY_LIMIT:
                    raise
                time.sleep(_WRITE_RETRY_BASE_SEC * (attempt + 1))
        raise OpenVikingClientError("OpenViking content.write 重试失败")

    def stat(self, *, uri: str) -> OpenVikingStatResult:
        path = f"{_STAT_PATH}?{urllib_parse.urlencode({'uri': uri})}"
        _, raw = self._call("GET", path)
        decoded = _decode_json_object(raw)
        node = decoded.get("result")
        if isinstance(node, Mapping):
            payload = node
        else:
            payload = decoded
        exists = bool(payload.get("exists", True))
        size_bytes = _read_optional_int(payload, "size_bytes")
        if size_bytes is None:
            size_bytes = _read_optional_int(payload, "size")
        sha256 = _normalize_sha256(_read_optional_text(payload, "sha256"))
        if sha256 is None:
            sha256 = _normalize_sha256(_read_optional_text(payload, "hash"))
        if sha256 is None:
            sha256 = _normalize_sha256(_read_optional_text(payload, "digest"))
        return OpenVikingStatResult(size_bytes=size_bytes, sha256=sha256, exists=exists)

    def read(self, *, uri: str) -> bytes:
        path = f"{_READ_PATH}?{urllib_parse.urlencode({'uri': uri})}"
        _, raw = self._call("GET", path)
        decoded = _decode_json_object(raw)
        result = decoded.get("result")
        if isinstance(result, str):
            return result.encode("utf-8")
        raise OpenVikingClientError("OpenViking content.read 返回体缺少文本 result")

    def download(self, *, uri: str) -> bytes:
        path = f"{_DOWNLOAD_PATH}?{urllib_parse.urlencode({'uri': uri})}"
        _, raw = self._call("GET", path)
        return raw

    def _call(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
    ) -> tuple[int, bytes]:
        headers = {"Accept": "application/json"}
        if body is not None and content_type is not None:
            headers["Content-Type"] = content_type
        if self._config.auth_mode == "bearer":
            token = self._config.token or ""
            headers["Authorization"] = f"Bearer {token}"
        url = f"{self._base_uri}{path}"
        try:
            return self._transport(method, url, headers, body or b"", self._timeout_sec)
        except OpenVikingClientError:
            raise
        except Exception as exc:
            raise OpenVikingClientError(summarize_provider_error(exc)) from exc


def build_l2_uri(target: L2WriteTarget) -> str:
    _validate_l2_segment("run_id", target.run_id)
    if target.stage != "frontline":
        raise FrontlineValidationError(L2_TARGET_INVALID, "l2 target stage 必须为 frontline")
    _validate_l2_segment("worker_id", target.worker_id)
    _validate_l2_segment("call_id", target.call_id)
    relative_path = validate_l2_target_path(target.relative_path)
    return (
        "viking://resources/workflow/"
        f"{target.run_id}/frontline/{target.worker_id}/{target.call_id}/evidence/{relative_path}"
    )


def write_l2_evidence(
    request: L2WriteRequest,
    *,
    client: OpenVikingEvidenceClient | None = None,
    env: Mapping[str, str] | None = None,
) -> L2WriteReceipt:
    uri = build_l2_uri(request.target)
    content = request.content_bytes
    size_bytes = len(content)
    expected_sha256 = _sha256_text(content)
    if size_bytes <= 0:
        raise FrontlineValidationError(L2_TARGET_INVALID, "content_bytes 必须非空")
    writer = client or build_openviking_http_client(load_openviking_l2_config(env))

    write_result = _write_once(
        writer=writer,
        uri=uri,
        request=request,
    )
    verification_uri = write_result.verification_uri or uri
    stat = _read_stat_once(writer=writer, uri=verification_uri)

    size_ok = stat.size_bytes is not None and stat.size_bytes == size_bytes
    if stat.size_bytes is not None and stat.size_bytes != size_bytes:
        raise FrontlineValidationError(
            L2_HASH_MISMATCH,
            f"L2 size mismatch expected={size_bytes} actual={stat.size_bytes}",
        )
    sha_ok = stat.sha256 is not None and stat.sha256 == expected_sha256
    if stat.sha256 is not None and stat.sha256 != expected_sha256:
        raise FrontlineValidationError(
            L2_HASH_MISMATCH,
            f"L2 sha256 mismatch expected={expected_sha256} actual={stat.sha256}",
        )

    stat_verified = bool(size_ok and sha_ok)
    if stat_verified:
        readback_verified = True
    else:
        _readback_verify(
            writer=writer,
            uri=verification_uri,
            expected_sha256=expected_sha256,
            expected_size=size_bytes,
        )
        readback_verified = True

    return L2WriteReceipt(
        uri=uri,
        sha256=expected_sha256,
        size_bytes=size_bytes,
        write_receipt_id=write_result.receipt_id,
        readback_verified=readback_verified,
        stat_verified=stat_verified,
        written_at=_utc_now_text(),
    )


def load_openviking_l2_config(env: Mapping[str, str] | None = None) -> OpenVikingConfig:
    source = os.environ if env is None else env
    base_uri = (source.get("CLAW_TRADE_OPENVIKING_BASE_URI") or "").strip()
    if not base_uri:
        raise FrontlineConfigError(OPENVIKING_CONFIG_INVALID, "CLAW_TRADE_OPENVIKING_BASE_URI 缺失")
    try:
        parsed = urllib_parse.urlsplit(base_uri)
    except ValueError as exc:
        raise FrontlineConfigError(OPENVIKING_CONFIG_INVALID, "CLAW_TRADE_OPENVIKING_BASE_URI 非法") from exc
    if parsed.scheme not in {"http", "https", "viking"}:
        raise FrontlineConfigError(OPENVIKING_CONFIG_INVALID, "CLAW_TRADE_OPENVIKING_BASE_URI 只允许 http/https/viking")

    auth_mode_raw = (source.get("CLAW_TRADE_OPENVIKING_AUTH_MODE") or "none").strip().lower()
    if auth_mode_raw not in _VALID_AUTH_MODES:
        raise FrontlineConfigError(OPENVIKING_CONFIG_INVALID, "CLAW_TRADE_OPENVIKING_AUTH_MODE 只允许 none 或 bearer")
    auth_mode = auth_mode_raw  # 保持字面值，后续由 dataclass 类型约束
    token = (source.get("CLAW_TRADE_OPENVIKING_TOKEN") or "").strip()
    if auth_mode == "bearer" and not token:
        raise FrontlineConfigError(
            OPENVIKING_CONFIG_INVALID,
            "CLAW_TRADE_OPENVIKING_AUTH_MODE=bearer 时必须提供 CLAW_TRADE_OPENVIKING_TOKEN",
        )

    return OpenVikingConfig(
        base_uri=base_uri,
        auth_mode=auth_mode,  # type: ignore[arg-type]
        token=token or None,
    )


def build_openviking_http_client(
    config: OpenVikingConfig,
    *,
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC,
    transport: Transport | None = None,
) -> OpenVikingHttpEvidenceClient:
    return OpenVikingHttpEvidenceClient(config, timeout_sec=timeout_sec, transport=transport)


def _write_once(
    *,
    writer: OpenVikingEvidenceClient,
    uri: str,
    request: L2WriteRequest,
) -> OpenVikingWriteResult:
    try:
        return writer.write(
            uri=uri,
            content_bytes=request.content_bytes,
            content_type=request.target.content_type,
            metadata=request.metadata,
        )
    except Exception as exc:
        _raise_l2_client_error(L2_WRITE_FAILED, "OpenViking 写入失败", exc)
    raise FrontlineValidationError(L2_WRITE_FAILED, "OpenViking 写入失败: unknown")


def _read_stat_once(*, writer: OpenVikingEvidenceClient, uri: str) -> OpenVikingStatResult:
    try:
        stat = writer.stat(uri=uri)
    except Exception as exc:
        _raise_l2_client_error(L2_READBACK_FAILED, "OpenViking stat 失败", exc)
    if not stat.exists:
        raise FrontlineValidationError(L2_READBACK_FAILED, "OpenViking stat 显示目标不存在")
    return stat


def _readback_verify(
    *,
    writer: OpenVikingEvidenceClient,
    uri: str,
    expected_sha256: str,
    expected_size: int,
) -> None:
    try:
        content = _readback_content_bytes(writer=writer, uri=uri)
    except Exception as exc:
        _raise_l2_client_error(L2_READBACK_FAILED, "OpenViking read-back 失败", exc)
    actual_sha256 = _sha256_text(content)
    actual_size = len(content)
    if actual_size != expected_size:
        raise FrontlineValidationError(
            L2_HASH_MISMATCH,
            f"L2 read-back size mismatch expected={expected_size} actual={actual_size}",
        )
    if actual_sha256 != expected_sha256:
        raise FrontlineValidationError(
            L2_HASH_MISMATCH,
            f"L2 read-back sha256 mismatch expected={expected_sha256} actual={actual_sha256}",
        )


def _raise_l2_client_error(code: str, message: str, exc: Exception) -> None:
    status_code = getattr(exc, "status_code", None)
    if isinstance(exc, urllib_error.HTTPError):
        status_code = exc.code
    mapped_code = OPENVIKING_AUTH_FAILED if status_code in {401, 403} else code
    detail = _sanitize_error_detail(exc)
    raise FrontlineValidationError(mapped_code, f"{message}: {detail}") from exc


def _validate_l2_segment(field_name: str, value: str) -> None:
    text = value.strip()
    if not text:
        raise FrontlineValidationError(L2_TARGET_INVALID, f"l2 target {field_name} 不能为空")
    if "/" in text or "\\" in text:
        raise FrontlineValidationError(L2_TARGET_INVALID, f"l2 target {field_name} 不允许包含路径分隔符")
    if text in {".", ".."}:
        raise FrontlineValidationError(L2_TARGET_INVALID, f"l2 target {field_name} 非法")


def _read_optional_text(payload: Mapping[str, object], key: str) -> str | None:
    raw = payload.get(key)
    if raw is None:
        return None
    text = str(raw).strip()
    return text or None


def _read_optional_int(payload: Mapping[str, object], key: str) -> int | None:
    raw = payload.get(key)
    if raw is None:
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return int(raw.strip())
        except ValueError:
            return None
    return None


def _basename_from_uri(uri: str) -> str:
    path = urllib_parse.urlsplit(uri).path
    if not path:
        return ""
    return path.rsplit("/", 1)[-1].strip()


def _is_openviking_derived_node(*, node: Mapping[str, object], node_uri: str) -> bool:
    name_candidates: list[str] = []
    node_name = _read_optional_text(node, "name")
    if node_name is not None:
        name_candidates.append(node_name)
    rel_path = _read_optional_text(node, "rel_path")
    if rel_path is not None:
        name_candidates.append(rel_path.rsplit("/", 1)[-1])
    basename = _basename_from_uri(node_uri)
    if basename:
        name_candidates.append(basename)
    for name in name_candidates:
        normalized = name.strip().lower()
        if not normalized:
            continue
        if _DERIVED_TEXT_FILE_RE.match(normalized) is not None:
            return True
        if normalized.startswith(".") and _SEMANTIC_NAME_RE.search(normalized) is not None:
            return True
        if normalized.startswith("semantic") or normalized.startswith(".semantic"):
            return True
    return False


def _normalize_sha256(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip().lower()
    if text.startswith("sha256:"):
        text = text[7:]
    if len(text) != 64 or any(char not in "0123456789abcdef" for char in text):
        return None
    return f"sha256:{text}"


def _decode_json_object(body: bytes) -> Mapping[str, object]:
    if not body:
        return {}
    try:
        decoded = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenVikingClientError(f"OpenViking 返回非法 JSON: {summarize_provider_error(exc)}") from exc
    if not isinstance(decoded, Mapping):
        raise OpenVikingClientError("OpenViking 返回体必须是 JSON object")
    return decoded


def _extract_receipt_id(body: bytes) -> str | None:
    if not body:
        return None
    decoded = _decode_json_object(body)
    write_receipt_id = _read_optional_text(decoded, "receipt_id")
    if write_receipt_id is None:
        write_receipt_id = _read_optional_text(decoded, "id")
    result = decoded.get("result")
    if isinstance(result, Mapping):
        write_receipt_id = write_receipt_id or _read_optional_text(result, "receipt_id")
        write_receipt_id = write_receipt_id or _read_optional_text(result, "id")
    return write_receipt_id


def _is_not_found_error(exc: OpenVikingClientError) -> bool:
    if exc.status_code == 404:
        return True
    return _NOT_FOUND_TOKEN_RE.search(str(exc)) is not None


def _is_directory_write_error(exc: OpenVikingClientError) -> bool:
    message = str(exc).lower()
    return "write only supports existing files, got directory" in message or "got directory" in message


def _is_resource_busy_error(exc: OpenVikingClientError) -> bool:
    return "resource is busy" in str(exc).lower()


def _build_temp_upload_multipart(*, content_bytes: bytes, content_type: str) -> tuple[bytes, str]:
    return _build_temp_upload_multipart_with_name(
        content_bytes=content_bytes,
        content_type=content_type,
        filename="l2-evidence.json",
    )


def _build_temp_upload_multipart_with_name(*, content_bytes: bytes, content_type: str, filename: str) -> tuple[bytes, str]:
    boundary = f"----clawtrade-l2-{hashlib.sha256(content_bytes).hexdigest()[:16]}"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {content_type or 'application/octet-stream'}\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    return header + content_bytes + footer, boundary


def _readback_content_bytes(*, writer: OpenVikingEvidenceClient, uri: str) -> bytes:
    raw = writer.download(uri=uri)
    if isinstance(raw, bytes):
        if raw:
            return raw
        return b""
    raise OpenVikingClientError("OpenViking content.download 返回非 bytes")


def _build_single_file_ovpack_bytes(*, uri: str, content_text: str) -> tuple[str, bytes]:
    parent_uri, call_id, relative_path = _parse_pack_import_target(uri)
    meta_uri = f"{parent_uri.rstrip('/')}/{call_id}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{call_id}/", "")
        archive.writestr(f"{call_id}/_._meta.json", json.dumps({"uri": meta_uri}, ensure_ascii=False))
        archive.writestr(f"{call_id}/{relative_path}", content_text.encode("utf-8"))
    return parent_uri, buffer.getvalue()


def _parse_pack_import_target(uri: str) -> tuple[str, str, str]:
    prefix = "viking://resources/workflow/"
    if not uri.startswith(prefix):
        raise OpenVikingClientError(f"OpenViking uri 非法，无法构造 pack.import parent: {uri}")
    parts = uri[len(prefix) :].split("/")
    if len(parts) < 5:
        raise OpenVikingClientError(f"OpenViking uri 层级不足，无法构造 pack.import parent: {uri}")
    run_id, stage, worker_id, call_id = parts[0], parts[1], parts[2], parts[3]
    relative_parts = parts[4:]
    if not run_id or not stage or not worker_id or not call_id:
        raise OpenVikingClientError(f"OpenViking uri 身份字段缺失，无法构造 pack.import parent: {uri}")
    if any(not part for part in relative_parts):
        raise OpenVikingClientError(f"OpenViking uri 目标文件路径缺失: {uri}")
    parent_uri = f"viking://resources/workflow/{run_id}/{stage}/{worker_id}"
    relative_path = "/".join(relative_parts)
    return parent_uri, call_id, relative_path


def _sha256_text(content: bytes) -> str:
    return f"sha256:{hashlib.sha256(content).hexdigest()}"


def _sanitize_error_detail(text: object) -> str:
    summary = summarize_provider_error(text)
    return _BEARER_TOKEN_RE.sub("Bearer ***", summary)


def _utc_now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _urllib_transport(
    method: str,
    url: str,
    headers: Mapping[str, str],
    body: bytes,
    timeout_sec: float,
) -> tuple[int, bytes]:
    request = urllib_request.Request(url=url, data=body if method != "GET" else None, method=method)
    for key, value in headers.items():
        request.add_header(key, value)
    try:
        with urllib_request.urlopen(request, timeout=timeout_sec) as response:
            return int(getattr(response, "status", 200)), response.read()
    except urllib_error.HTTPError as exc:
        response_body = exc.read()
        message = summarize_provider_error(response_body.decode("utf-8", errors="ignore") or str(exc))
        raise OpenVikingClientError(message, status_code=exc.code) from exc
    except urllib_error.URLError as exc:
        message = summarize_provider_error(exc.reason or exc)
        raise OpenVikingClientError(message) from exc
    except TimeoutError as exc:
        raise OpenVikingClientError(redact_secret(exc)) from exc
