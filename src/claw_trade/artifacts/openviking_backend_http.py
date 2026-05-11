from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import io
import json
import os
from pathlib import Path
from typing import Any
from urllib import error, parse, request
import uuid
import zipfile

from claw_trade.artifacts.openviking_client import OpenVikingAccessError, OpenVikingStat
from claw_trade.artifacts.refs import L2Entry, L2Index, MaterialReceipt, MaterialReceiptVerification
from claw_trade.workflow.models import Stage

_DEFAULT_ENDPOINT = "http://127.0.0.1:1933"
_DEFAULT_TIMEOUT_SEC = 10.0
_DEFAULT_DOWNLOAD_PATH = "/api/v1/content/download"
_DEFAULT_READ_PATH = "/api/v1/content/read"
_DEFAULT_STAT_PATH = "/api/v1/fs/stat"
_DEFAULT_MKDIR_PATH = "/api/v1/fs/mkdir"
_DEFAULT_WRITE_PATH = "/api/v1/content/write"
_DEFAULT_TEMP_UPLOAD_PATH = "/api/v1/resources/temp_upload"
_DEFAULT_PACK_IMPORT_PATH = "/api/v1/pack/import"


def create_default_backend() -> OpenVikingHttpBackend:
    endpoint = os.environ.get("OPENVIKING_ENDPOINT", _DEFAULT_ENDPOINT).strip() or _DEFAULT_ENDPOINT
    api_key = os.environ.get("OPENVIKING_API_KEY", "").strip() or None
    timeout_raw = os.environ.get("OPENVIKING_HTTP_TIMEOUT_SEC", "").strip()
    timeout_sec = _DEFAULT_TIMEOUT_SEC if not timeout_raw else float(timeout_raw)
    download_path = os.environ.get("OPENVIKING_DOWNLOAD_PATH", _DEFAULT_DOWNLOAD_PATH).strip() or _DEFAULT_DOWNLOAD_PATH
    return OpenVikingHttpBackend(
        endpoint=endpoint,
        api_key=api_key,
        timeout_sec=timeout_sec,
        download_path=download_path,
    )


@dataclass
class OpenVikingHttpBackend:
    endpoint: str
    api_key: str | None = None
    timeout_sec: float = _DEFAULT_TIMEOUT_SEC
    download_path: str = _DEFAULT_DOWNLOAD_PATH

    def ensure_namespace(self, namespace: str) -> None:
        run_id = _parse_workflow_namespace(namespace)
        uri = f"viking://resources/workflow/{run_id}/"
        mkdir_error: OpenVikingAccessError | None = None
        # 命名空间边界：这里只允许当前 run 的固定目录，禁止任何 latest/list 扫描当成功路径。
        try:
            self._call_json_api("POST", _DEFAULT_MKDIR_PATH, body={"uri": uri})
        except OpenVikingAccessError as exc:
            mkdir_error = exc
        try:
            stat = self.fetch_stat_by_uri(uri)
        except OpenVikingAccessError:
            if mkdir_error is not None:
                raise mkdir_error
            raise
        if stat.ok and stat.exists and stat.is_dir:
            return
        if mkdir_error is not None:
            raise mkdir_error
        if not stat.ok or not stat.exists:
            raise OpenVikingAccessError(f"namespace 不可用: {uri}", category="backend_unavailable")
        raise OpenVikingAccessError(f"namespace 不是目录: {uri}", category="backend_unavailable")

    def prepare_probe_receipt(self, *, receipt_path: Path, stat_uri: str, read_uri: str | None = None) -> None:
        self.ensure_namespace("workflow/probe")
        _ = read_uri
        operations: list[dict[str, object]] = []
        report_content = "# openviking probe report\n"

        write_verified = self._write_verified_content(
            uri=stat_uri,
            content=report_content,
            operations=operations,
            operation_prefix="probe.report",
        )

        run_id, stage, worker_id, call_id, target_name = _parse_workflow_file_identity(stat_uri)
        if target_name.endswith(".md"):
            target_name = target_name[:-3]
        receipt_payload = {
            "source": "openviking_adapter_verified_receipt",
            "receipt_label": "verified_openviking_write_receipt",
            "receipt_origin": "adapter_verified_non_native",
            "is_openviking_native_receipt": False,
            "uri": stat_uri,
            "sha256": write_verified["sha256"],
            "size_bytes": write_verified["size_bytes"],
            "run_id": run_id,
            "stage": stage.value,
            "worker_id": worker_id,
            "call_id": call_id,
            "target_name": target_name,
            "receipt_id": f"probe-{uuid.uuid4().hex}",
            "written_at": _now_text(),
            "verification": {
                "verified": True,
                "method": "openviking_write_then_stat_then_readback_sha_size_identity_check",
                "expected_write_sha256": write_verified["expected_write_sha256"],
                "expected_write_size_bytes": write_verified["expected_write_size_bytes"],
                "expected_sha256": write_verified["sha256"],
                "expected_size_bytes": write_verified["size_bytes"],
                "stat_size_bytes": write_verified["stat_size_bytes"],
                "stat_sha256": write_verified["stat_sha256"],
                "readback_sha256": write_verified["sha256"],
                "readback_size_bytes": write_verified["size_bytes"],
            },
            "http_operations": operations,
        }
        receipt_path.parent.mkdir(parents=True, exist_ok=True)
        receipt_path.write_text(f"{json.dumps(receipt_payload, ensure_ascii=False, indent=2)}\n", encoding="utf-8")

    def fetch_receipt_by_path(self, receipt_path: Path) -> MaterialReceipt:
        try:
            raw = json.loads(receipt_path.read_text(encoding="utf-8"))
        except FileNotFoundError as exc:
            raise OpenVikingAccessError(f"receipt 不存在: {receipt_path}", category="not_found") from exc
        except OSError as exc:
            raise OpenVikingAccessError(f"读取 receipt 失败: {exc}", category="backend_unavailable") from exc
        except json.JSONDecodeError as exc:
            raise OpenVikingAccessError(f"receipt JSON 非法: {exc}", category="backend_unavailable") from exc
        if not isinstance(raw, dict):
            raise OpenVikingAccessError("receipt 顶层必须是 JSON object", category="backend_unavailable")
        return _parse_receipt_payload(raw)

    def fetch_stat_by_uri(self, uri: str) -> OpenVikingStat:
        result = self._call_json_api("GET", _DEFAULT_STAT_PATH, query={"uri": uri})
        if not isinstance(result, dict):
            raise OpenVikingAccessError("fs/stat 返回体不是 object", category="backend_unavailable")
        exists = bool(result.get("exists", True))
        is_dir = bool(_first_non_none(result.get("isDir"), result.get("is_dir"), False))
        size_value = _first_non_none(result.get("size_bytes"), result.get("size"))
        sha_value = _first_non_none(result.get("sha256"), result.get("hash"), result.get("digest"))
        if not exists:
            return OpenVikingStat(uri=uri, ok=True, exists=False, sha256=None, size_bytes=None, is_dir=is_dir)
        size_bytes = _coerce_int(size_value, field_name="stat.size")
        sha256 = _coerce_sha256(sha_value)
        if is_dir:
            # 目录 stat 不应回退到内容下载；命名空间探针只需要证明目录存在。
            return OpenVikingStat(uri=uri, ok=True, exists=True, sha256=sha256, size_bytes=size_bytes, is_dir=True)
        if sha256 is None or size_bytes is None:
            # 兼容 stat 不回传指纹的后端：补一次字节读取计算 sha/size，仍以真实 OpenViking 读取结果为准。
            content = self.fetch_content_by_uri(uri)
            sha256 = hashlib.sha256(content).hexdigest()
            size_bytes = len(content)
        return OpenVikingStat(uri=uri, ok=True, exists=True, sha256=sha256, size_bytes=size_bytes, is_dir=False)

    def fetch_content_by_uri(self, uri: str) -> bytes:
        # 正式读取路径只允许 download 端点；端点不可用必须失败，禁止切换到 read 成功。
        return self._call_bytes_api("GET", self.download_path, query={"uri": uri})

    def fetch_l2_index_by_uri(self, uri: str | None) -> L2Index:
        if uri is None:
            return L2Index(
                entries=(),
                empty_reason="missing_l2_index_uri",
                index_uri=None,
                index_sha256=None,
                index_size_bytes=None,
            )
        content = self.fetch_content_by_uri(uri)
        digest = hashlib.sha256(content).hexdigest()
        try:
            payload = json.loads(content.decode("utf-8"))
        except UnicodeDecodeError as exc:
            raise OpenVikingAccessError(f"L2 index 不是 UTF-8 文本: {exc}", category="backend_unavailable") from exc
        except json.JSONDecodeError as exc:
            raise OpenVikingAccessError(f"L2 index JSON 非法: {exc}", category="backend_unavailable") from exc
        if payload is None:
            return L2Index(entries=(), empty_reason="no_evidence", index_uri=uri, index_sha256=digest, index_size_bytes=len(content))
        if not isinstance(payload, dict):
            raise OpenVikingAccessError("L2 index 顶层必须是 object", category="backend_unavailable")
        entries_raw = payload.get("entries")
        if entries_raw is None:
            entries_raw = payload.get("result", {}).get("entries") if isinstance(payload.get("result"), dict) else None
        if entries_raw is None:
            return L2Index(entries=(), empty_reason="no_evidence", index_uri=uri, index_sha256=digest, index_size_bytes=len(content))
        if not isinstance(entries_raw, list):
            raise OpenVikingAccessError("L2 index.entries 必须是数组", category="backend_unavailable")
        entries: list[L2Entry] = []
        for item in entries_raw:
            if not isinstance(item, dict):
                raise OpenVikingAccessError("L2 index entry 必须是 object", category="backend_unavailable")
            entries.append(
                L2Entry(
                    evidence_id=_required_str(item, "evidence_id"),
                    uri=_required_str(item, "uri"),
                    kind=_required_str(item, "kind"),
                    source=_required_str(item, "source"),
                    sha256=_required_sha256(item, "sha256"),
                    size_bytes=_required_int(item, "size_bytes"),
                )
            )
        return L2Index(
            entries=tuple(entries),
            empty_reason=None if entries else "no_evidence",
            index_uri=uri,
            index_sha256=digest,
            index_size_bytes=len(content),
        )

    def _write_verified_content(
        self,
        *,
        uri: str,
        content: str,
        operations: list[dict[str, object]],
        operation_prefix: str,
    ) -> dict[str, object]:
        expected_write_bytes = content.encode("utf-8")
        expected_write_sha = hashlib.sha256(expected_write_bytes).hexdigest()
        expected_write_size = len(expected_write_bytes)

        exists = False
        try:
            stat_before = self._call_json_api_with_audit(
                "GET",
                _DEFAULT_STAT_PATH,
                operation=f"{operation_prefix}.fs.stat.before_write",
                operations=operations,
                query={"uri": uri},
            )
            exists = _stat_result_exists(stat_before)
        except OpenVikingAccessError as exc:
            if exc.category != "not_found" and not _is_openviking_035_missing_stat_before_write(exc):
                raise

        if not exists:
            # OpenViking 0.3.5 的 content/write 不支持 create，这里必须先用 ovpack 导入创建目标文件。
            self._create_missing_uri_via_pack_import(
                uri=uri,
                content=content,
                operations=operations,
                operation_prefix=operation_prefix,
            )
        else:
            # busy 根因修复：仅在目标已存在时走 replace；缺失路径由 pack/import 作为正式写入，不再追加 replace。
            self._call_json_api_with_audit(
                "POST",
                _DEFAULT_WRITE_PATH,
                operation=f"{operation_prefix}.content.write.replace",
                operations=operations,
                body={"uri": uri, "content": content, "mode": "replace"},
            )

        stat_after = self._call_json_api_with_audit(
            "GET",
            _DEFAULT_STAT_PATH,
            operation=f"{operation_prefix}.fs.stat.after_write",
            operations=operations,
            query={"uri": uri},
        )
        stat_size_bytes, stat_sha256 = _extract_stat_integrity(stat_after, target_name=_target_name_from_uri(uri))

        # 字节口径必须与 OpenViking 实际存储一致：receipt 顶层 sha/size 只能绑定 download 精确字节。
        actual_bytes = self._call_bytes_api_with_audit(
            "GET",
            self.download_path,
            operation=f"{operation_prefix}.content.download.after_write",
            operations=operations,
            query={"uri": uri},
        )
        actual_sha = hashlib.sha256(actual_bytes).hexdigest()
        actual_size = len(actual_bytes)
        if actual_sha != expected_write_sha:
            raise OpenVikingAccessError("openviking probe verification sha mismatch after write", category="hash_mismatch")
        if actual_size != expected_write_size:
            raise OpenVikingAccessError("openviking probe verification size mismatch after write", category="size_mismatch")
        if stat_size_bytes != actual_size:
            raise OpenVikingAccessError("openviking probe verification stat size mismatch after readback", category="size_mismatch")
        if stat_sha256 is not None and stat_sha256 != actual_sha:
            raise OpenVikingAccessError("openviking probe verification stat sha mismatch", category="hash_mismatch")
        return {
            "expected_write_sha256": expected_write_sha,
            "expected_write_size_bytes": expected_write_size,
            "sha256": actual_sha,
            "size_bytes": actual_size,
            "stat_size_bytes": stat_size_bytes,
            "stat_sha256": stat_sha256,
        }

    def _create_missing_uri_via_pack_import(
        self,
        *,
        uri: str,
        content: str,
        operations: list[dict[str, object]],
        operation_prefix: str,
    ) -> None:
        parent_uri, ovpack_bytes = _build_single_file_ovpack_bytes(uri=uri, content=content)
        multipart_body, multipart_content_type = _encode_multipart_form_file(
            file_name=f"seed-{uuid.uuid4().hex}.ovpack",
            file_bytes=ovpack_bytes,
        )
        upload_result = self._call_json_api_with_audit(
            "POST",
            _DEFAULT_TEMP_UPLOAD_PATH,
            operation=f"{operation_prefix}.resources.temp_upload",
            operations=operations,
            body=multipart_body,
            body_content_type=multipart_content_type,
        )
        temp_file_id = _extract_temp_file_id(upload_result)
        self._call_json_api_with_audit(
            "POST",
            _DEFAULT_PACK_IMPORT_PATH,
            operation=f"{operation_prefix}.pack.import.vectorize_false",
            operations=operations,
            body={
                "temp_file_id": temp_file_id,
                "parent": parent_uri,
                "force": True,
                "vectorize": False,
            },
        )

    def _call_json_api_with_audit(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        operations: list[dict[str, object]],
        query: dict[str, str] | None = None,
        body: dict[str, object] | bytes | None = None,
        body_content_type: str | None = None,
    ) -> object:
        try:
            result = self._call_json_api(
                method,
                path,
                query=query,
                body=body,
                body_content_type=body_content_type,
            )
            operations.append(
                {
                    "operation": operation,
                    "method": method,
                    "endpoint": _build_url(self.endpoint, path, query),
                    "ok": True,
                }
            )
            return result
        except OpenVikingAccessError as exc:
            operations.append(
                {
                    "operation": operation,
                    "method": method,
                    "endpoint": _build_url(self.endpoint, path, query),
                    "ok": False,
                    "error_category": exc.category,
                    "error_message": str(exc),
                }
            )
            raise

    def _call_bytes_api_with_audit(
        self,
        method: str,
        path: str,
        *,
        operation: str,
        operations: list[dict[str, object]],
        query: dict[str, str] | None = None,
    ) -> bytes:
        try:
            result = self._call_bytes_api(method, path, query=query)
            operations.append(
                {
                    "operation": operation,
                    "method": method,
                    "endpoint": _build_url(self.endpoint, path, query),
                    "ok": True,
                }
            )
            return result
        except OpenVikingAccessError as exc:
            operations.append(
                {
                    "operation": operation,
                    "method": method,
                    "endpoint": _build_url(self.endpoint, path, query),
                    "ok": False,
                    "error_category": exc.category,
                    "error_message": str(exc),
                }
            )
            raise

    def _call_json_api(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
        body: dict[str, object] | bytes | None = None,
        body_content_type: str | None = None,
    ) -> object:
        response = self._request(
            method,
            path,
            query=query,
            body=body,
            body_content_type=body_content_type,
            accept="application/json",
        )
        payload = _parse_json_payload(response.body)
        if isinstance(payload, dict) and payload.get("status") == "ok" and "result" in payload:
            return payload["result"]
        if isinstance(payload, dict) and payload.get("status") not in (None, "ok"):
            error_info = payload.get("error")
            msg = str(error_info) if error_info is not None else str(payload.get("message") or payload.get("status"))
            raise OpenVikingAccessError(f"OpenViking 返回错误: {msg}", category="backend_unavailable")
        return payload

    def _call_bytes_api(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None = None,
    ) -> bytes:
        response = self._request(
            method,
            path,
            query=query,
            body=None,
            body_content_type=None,
            accept="application/octet-stream",
        )
        if not response.body:
            return b""
        content_type = response.headers.get("Content-Type", "")
        if "application/json" in content_type:
            payload = _parse_json_payload(response.body)
            if isinstance(payload, dict) and payload.get("status") == "ok" and isinstance(payload.get("result"), str):
                return str(payload["result"]).encode("utf-8")
            raise OpenVikingAccessError("download 接口返回 JSON 错误体", category="backend_unavailable")
        return response.body

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, str] | None,
        body: dict[str, object] | bytes | None,
        body_content_type: str | None,
        accept: str,
    ) -> _HttpResponse:
        url = _build_url(self.endpoint, path, query)
        headers = {"Accept": accept}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        data: bytes | None = None
        if isinstance(body, dict):
            headers["Content-Type"] = "application/json"
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        elif isinstance(body, bytes):
            headers["Content-Type"] = body_content_type or "application/octet-stream"
            data = body
        elif body is not None:
            raise OpenVikingAccessError("HTTP body 类型非法", category="backend_unavailable")
        req = request.Request(url=url, method=method, headers=headers, data=data)
        try:
            with request.urlopen(req, timeout=self.timeout_sec) as resp:
                return _HttpResponse(
                    status=resp.getcode(),
                    headers={k: v for k, v in resp.headers.items()},
                    body=resp.read(),
                )
        except error.HTTPError as exc:
            message = _build_http_error_message(exc)
            raise OpenVikingAccessError(message, category=_http_status_to_category(exc.code)) from exc
        except error.URLError as exc:
            raise OpenVikingAccessError(f"OpenViking 连接失败: {exc}", category="backend_unavailable") from exc


@dataclass(frozen=True)
class _HttpResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def _parse_workflow_namespace(namespace: str) -> str:
    raw = namespace.strip().rstrip("/")
    if not raw.startswith("workflow/"):
        raise OpenVikingAccessError("namespace 必须是 workflow/<run_id>", category="invalid_uri")
    parts = raw.split("/")
    if len(parts) != 2 or not parts[1]:
        raise OpenVikingAccessError("namespace 必须是 workflow/<run_id>", category="invalid_uri")
    run_id = parts[1]
    if run_id in {".", ".."} or "/" in run_id:
        raise OpenVikingAccessError("run_id 非法", category="invalid_uri")
    return run_id


def _build_url(endpoint: str, path: str, query: dict[str, str] | None) -> str:
    base = endpoint.rstrip("/")
    fixed_path = path if path.startswith("/") else f"/{path}"
    url = f"{base}{fixed_path}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"
    return url


def _parse_json_payload(raw: bytes) -> object:
    try:
        return json.loads(raw.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise OpenVikingAccessError(f"响应不是 UTF-8 JSON: {exc}", category="backend_unavailable") from exc
    except json.JSONDecodeError as exc:
        raise OpenVikingAccessError(f"响应不是合法 JSON: {exc}", category="backend_unavailable") from exc


def _now_text() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _as_record(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    return {}


def _to_non_negative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value >= 0:
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _to_sha256(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip().lower()
    if len(text) != 64:
        return None
    for ch in text:
        if ch not in "0123456789abcdef":
            return None
    return text


def _extract_stat_integrity(raw: object, *, target_name: str) -> tuple[int, str | None]:
    root = _as_record(raw)
    candidates = (
        root,
        _as_record(root.get("result")),
        _as_record(root.get("file")),
        _as_record(root.get("node")),
        _as_record(root.get("entry")),
    )
    node: dict[str, object] | None = None
    for candidate in candidates:
        if not candidate:
            continue
        name = candidate.get("name")
        if isinstance(name, str) and name and name != target_name:
            continue
        if candidate.get("isDir") is True or candidate.get("is_dir") is True:
            continue
        node = candidate
        break
    if node is None:
        raise OpenVikingAccessError("openviking probe verification stat target node not found", category="backend_unavailable")
    size_bytes = _to_non_negative_int(_first_non_none(node.get("size_bytes"), node.get("size"), node.get("file_size")))
    if size_bytes is None:
        raise OpenVikingAccessError("openviking probe verification missing stat size after write", category="backend_unavailable")
    checksums = _as_record(node.get("checksums"))
    sha_candidates = (
        node.get("sha256"),
        node.get("content_sha256"),
        node.get("file_sha256"),
        checksums.get("sha256"),
        checksums.get("content_sha256"),
    )
    sha256: str | None = None
    for value in sha_candidates:
        if value in (None, ""):
            continue
        normalized = _to_sha256(value)
        if normalized is None:
            raise OpenVikingAccessError("openviking probe verification stat sha field is invalid", category="backend_unavailable")
        sha256 = normalized
        break
    return size_bytes, sha256


def _target_name_from_uri(uri: str) -> str:
    normalized = uri.rstrip("/")
    cut = normalized.rfind("/")
    if cut <= len("viking://"):
        raise OpenVikingAccessError(f"非法 URI: {uri}", category="invalid_uri")
    target_name = normalized[cut + 1 :]
    if not target_name:
        raise OpenVikingAccessError(f"非法 URI: {uri}", category="invalid_uri")
    return target_name


def _parse_workflow_file_identity(uri: str) -> tuple[str, Stage, str, str, str]:
    prefix = "viking://resources/workflow/"
    if not uri.startswith(prefix):
        raise OpenVikingAccessError(f"非法 URI: {uri}", category="invalid_uri")
    parts = uri[len(prefix) :].split("/")
    if len(parts) < 5:
        raise OpenVikingAccessError(f"URI 层级不足: {uri}", category="invalid_uri")
    run_id = parts[0]
    stage_text = parts[1]
    worker_id = parts[2]
    call_id = parts[3]
    target_name = parts[4]
    try:
        stage = Stage(stage_text)
    except ValueError as exc:
        raise OpenVikingAccessError(f"未知 stage: {stage_text}", category="invalid_uri") from exc
    if not run_id or not worker_id or not call_id or not target_name:
        raise OpenVikingAccessError(f"URI 信息缺失: {uri}", category="invalid_uri")
    return run_id, stage, worker_id, call_id, target_name


def _build_single_file_ovpack_bytes(*, uri: str, content: str) -> tuple[str, bytes]:
    parent_uri, call_id, relative_path = _parse_pack_import_target(uri)
    meta_uri = f"{parent_uri.rstrip('/')}/{call_id}"
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{call_id}/", "")
        zf.writestr(f"{call_id}/_._meta.json", json.dumps({"uri": meta_uri}, ensure_ascii=False))
        zf.writestr(f"{call_id}/{relative_path}", content.encode("utf-8"))
    return parent_uri, buffer.getvalue()


def _parse_pack_import_target(uri: str) -> tuple[str, str, str]:
    prefix = "viking://resources/workflow/"
    if not uri.startswith(prefix):
        raise OpenVikingAccessError(f"非法 URI: {uri}", category="invalid_uri")
    parts = uri[len(prefix) :].split("/")
    if len(parts) < 5:
        raise OpenVikingAccessError(f"URI 层级不足: {uri}", category="invalid_uri")
    run_id, stage, worker_id, call_id = parts[0], parts[1], parts[2], parts[3]
    relative_parts = parts[4:]
    if not run_id or not stage or not worker_id or not call_id:
        raise OpenVikingAccessError(f"URI 信息缺失: {uri}", category="invalid_uri")
    if any(not p for p in relative_parts):
        raise OpenVikingAccessError(f"URI 信息缺失: {uri}", category="invalid_uri")
    parent_uri = f"viking://resources/workflow/{run_id}/{stage}/{worker_id}"
    relative_path = "/".join(relative_parts)
    return parent_uri, call_id, relative_path


def _encode_multipart_form_file(*, file_name: str, file_bytes: bytes) -> tuple[bytes, str]:
    boundary = f"----clawtrade-{uuid.uuid4().hex}"
    header = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{file_name}"\r\n'
        "Content-Type: application/octet-stream\r\n\r\n"
    ).encode("utf-8")
    footer = f"\r\n--{boundary}--\r\n".encode("utf-8")
    body = header + file_bytes + footer
    return body, f"multipart/form-data; boundary={boundary}"


def _extract_temp_file_id(raw: object) -> str:
    record = _as_record(raw)
    temp_file_id = record.get("temp_file_id")
    if not isinstance(temp_file_id, str) or not temp_file_id:
        raise OpenVikingAccessError("temp_upload 返回缺少 temp_file_id", category="backend_unavailable")
    return temp_file_id


def _stat_result_exists(raw: object) -> bool:
    record = _as_record(raw)
    exists = record.get("exists")
    if isinstance(exists, bool):
        return exists
    return True


def _build_http_error_message(exc: error.HTTPError) -> str:
    try:
        body = exc.read().decode("utf-8", errors="replace").strip()
    except Exception:
        body = ""
    if body:
        return f"HTTP {exc.code}: {body}"
    return f"HTTP {exc.code}: {exc.reason}"


def _http_status_to_category(status_code: int) -> str:
    if status_code == 404:
        return "not_found"
    if status_code in (401, 403):
        return "permission_denied"
    if status_code == 400:
        return "invalid_uri"
    if status_code >= 500:
        return "backend_unavailable"
    return "unknown"


def _is_openviking_035_missing_stat_before_write(exc: OpenVikingAccessError) -> bool:
    # OpenViking 0.3.5 对缺失文件的写入前 stat 会返回 500/internal；只在这个探测点放行。
    if exc.category != "backend_unavailable":
        return False
    lower = str(exc).lower()
    if "http 500" not in lower:
        return False
    if '"code":"internal"' not in lower and '"code": "internal"' not in lower:
        return False
    return '"message":"internal server error"' in lower or '"message": "internal server error"' in lower


def _parse_receipt_payload(raw: dict[str, object]) -> MaterialReceipt:
    verification_raw = raw.get("verification")
    if not isinstance(verification_raw, dict):
        raise OpenVikingAccessError("receipt.verification 缺失或非法", category="backend_unavailable")
    verification = MaterialReceiptVerification(
        verified=_required_bool(verification_raw, "verified"),
        method=_required_str(verification_raw, "method"),
    )
    stage_text = _required_str(raw, "stage")
    try:
        stage = Stage(stage_text)
    except ValueError as exc:
        raise OpenVikingAccessError(f"receipt.stage 非法: {stage_text}", category="backend_unavailable") from exc
    return MaterialReceipt(
        uri=_required_str(raw, "uri"),
        run_id=_required_str(raw, "run_id"),
        call_id=_required_str(raw, "call_id"),
        worker_id=_required_str(raw, "worker_id"),
        stage=stage,
        target_name=_required_str(raw, "target_name"),
        sha256=_required_sha256(raw, "sha256"),
        size_bytes=_required_int(raw, "size_bytes"),
        written_at=_required_str(raw, "written_at"),
        receipt_id=_required_str(raw, "receipt_id"),
        source=_required_str(raw, "source"),
        receipt_label=_required_str(raw, "receipt_label"),
        receipt_origin=_required_str(raw, "receipt_origin"),
        is_openviking_native_receipt=_required_bool(raw, "is_openviking_native_receipt"),
        verification=verification,
    )


def _required_str(raw: dict[str, object], key: str) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise OpenVikingAccessError(f"字段缺失或非法: {key}", category="backend_unavailable")
    return value


def _required_sha256(raw: dict[str, object], key: str) -> str:
    value = _required_str(raw, key)
    if len(value) != 64:
        raise OpenVikingAccessError(f"字段不是 SHA256: {key}", category="backend_unavailable")
    return value


def _required_int(raw: dict[str, object], key: str) -> int:
    value = raw.get(key)
    return _coerce_int(value, field_name=key, required=True)


def _required_bool(raw: dict[str, object], key: str) -> bool:
    value = raw.get(key)
    if not isinstance(value, bool):
        raise OpenVikingAccessError(f"字段缺失或非法: {key}", category="backend_unavailable")
    return value


def _coerce_int(value: object, *, field_name: str, required: bool = False) -> int | None:
    if value is None and not required:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise OpenVikingAccessError(f"字段缺失或非法: {field_name}", category="backend_unavailable")
    if value < 0:
        raise OpenVikingAccessError(f"字段必须非负: {field_name}", category="backend_unavailable")
    return value


def _coerce_sha256(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or len(value) != 64:
        raise OpenVikingAccessError("stat.sha256 非法", category="backend_unavailable")
    return value


def _first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None
