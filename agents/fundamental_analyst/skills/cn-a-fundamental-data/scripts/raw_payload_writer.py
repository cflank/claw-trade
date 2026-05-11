from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import hashlib
import io
import json
import os
from threading import Lock
import time
from typing import Callable
from urllib import error, parse, request
import uuid
import zipfile

from boundary import FUNDAMENTAL_WORKER_ID, assert_data_service_boundary
from security import sanitize_error

_DEFAULT_DOWNLOAD_PATH = "/api/v1/content/download"
_DEFAULT_STAT_PATH = "/api/v1/fs/stat"
_DEFAULT_TEMP_UPLOAD_PATH = "/api/v1/resources/temp_upload"
_DEFAULT_PACK_IMPORT_PATH = "/api/v1/pack/import"
_DEFAULT_TIMEOUT_RETRY_DELAY_SEC = 0.3
RAW_PAYLOAD_WRITER_TIMEOUT_MS_V1 = 5000
RAW_PAYLOAD_WRITER_RETRY_LIMIT_V1 = 1
_SEQUENCE_LOCK = Lock()
_SEQUENCE_COUNTERS: dict[tuple[str, str, str, str], int] = {}


@dataclass(frozen=True)
class RawPayloadWriteInput:
    provider: str
    api_name: str
    run_id: str
    dispatch_id: str
    worker_id: str
    attempt_seq: int
    payload: object
    timeout_ms: int
    retry_limit: int


@dataclass(frozen=True)
class RawPayloadWriteResult:
    ok: bool
    uri: str | None
    attempt_seq: int | None
    content_hash: str | None
    bytes_written: int
    started_at: str
    ended_at: str
    duration_ms: int
    retry_count: int
    error_type: str | None
    error_message_redacted: str | None


@dataclass(frozen=True)
class OpenVikingCreateOnlyPutResult:
    ok: bool
    ended_at: str
    duration_ms: int
    retry_count: int
    error_type: str | None
    error_message_redacted: str | None


def reserve_provider_attempt_seq_v1(run_id: str, dispatch_id: str, provider: str, api_name: str) -> int:
    key = (run_id.strip(), dispatch_id.strip(), provider.strip(), api_name.strip())
    with _SEQUENCE_LOCK:
        current = _SEQUENCE_COUNTERS.get(key, 0)
        next_value = current + 1
        _SEQUENCE_COUNTERS[key] = next_value
    return next_value


def allocate_raw_attempt_seq_v1(
    run_id: str,
    dispatch_id: str,
    provider: str,
    api_name: str,
    expected_min_seq: int,
) -> int:
    reserved = reserve_provider_attempt_seq_v1(run_id, dispatch_id, provider, api_name)
    if expected_min_seq <= reserved:
        return reserved
    _mark_attempt_seq_used_v1(
        run_id=run_id,
        dispatch_id=dispatch_id,
        provider=provider,
        api_name=api_name,
        used_seq=expected_min_seq,
    )
    key = (run_id.strip(), dispatch_id.strip(), provider.strip(), api_name.strip())
    with _SEQUENCE_LOCK:
        return _SEQUENCE_COUNTERS[key]


def write_raw_payload_to_openviking(
    request_input: RawPayloadWriteInput,
    put_create_only: Callable[..., OpenVikingCreateOnlyPutResult] | None = None,
) -> RawPayloadWriteResult:
    assert_data_service_boundary("write_raw_payload")
    started_at = _now_iso()
    started_perf = time.perf_counter()

    if put_create_only is None:
        put_create_only = put_openviking_object_create_only

    try:
        _validate_request(request_input)
        canonical_bytes = to_canonical_json_bytes(request_input.payload)
        content_hash = f"sha256:{sha256_hex(canonical_bytes)}"
        resolved_attempt_seq = allocate_raw_attempt_seq_v1(
            run_id=request_input.run_id,
            dispatch_id=request_input.dispatch_id,
            provider=request_input.provider,
            api_name=request_input.api_name,
            expected_min_seq=request_input.attempt_seq,
        )
        uri = build_openviking_raw_uri(
            workspace_env="OPENVIKING_WORKSPACE",
            run_id=request_input.run_id,
            dispatch_id=request_input.dispatch_id,
            worker_id=request_input.worker_id,
            provider=request_input.provider,
            api_name=request_input.api_name,
            attempt_seq=resolved_attempt_seq,
        )
    except Exception as exc:  # noqa: BLE001
        return _failed_result(
            started_at=started_at,
            started_perf=started_perf,
            error_type="raw_payload_prepare_failed",
            error_message=sanitize_error(exc),
            retry_count=0,
        )

    write_result = put_create_only(
        endpoint_env="OPENVIKING_ENDPOINT",
        api_key_env="OPENVIKING_API_KEY",
        uri=uri,
        body=canonical_bytes,
        timeout_ms=request_input.timeout_ms,
        retry_limit=request_input.retry_limit,
    )
    if write_result.error_type == "object_already_exists":
        next_seq = resolved_attempt_seq + 1
        _mark_attempt_seq_used_v1(
            run_id=request_input.run_id,
            dispatch_id=request_input.dispatch_id,
            provider=request_input.provider,
            api_name=request_input.api_name,
            used_seq=next_seq,
        )
        retry_uri = build_openviking_raw_uri(
            workspace_env="OPENVIKING_WORKSPACE",
            run_id=request_input.run_id,
            dispatch_id=request_input.dispatch_id,
            worker_id=request_input.worker_id,
            provider=request_input.provider,
            api_name=request_input.api_name,
            attempt_seq=next_seq,
        )
        retry_result = put_create_only(
            endpoint_env="OPENVIKING_ENDPOINT",
            api_key_env="OPENVIKING_API_KEY",
            uri=retry_uri,
            body=canonical_bytes,
            timeout_ms=request_input.timeout_ms,
            retry_limit=request_input.retry_limit,
        )
        if retry_result.error_type == "object_already_exists":
            return RawPayloadWriteResult(
                ok=False,
                uri=None,
                attempt_seq=None,
                content_hash=None,
                bytes_written=0,
                started_at=started_at,
                ended_at=retry_result.ended_at,
                duration_ms=retry_result.duration_ms,
                retry_count=1,
                error_type="raw_payload_write_conflict",
                error_message_redacted=(
                    "CONFLICT: create-only write failed twice for same run/dispatch/provider/api attempt sequence"
                ),
            )
        write_result = retry_result
        uri = retry_uri
        resolved_attempt_seq = next_seq

    if write_result.ok:
        tail_seq = parse_raw_uri_attempt_seq(uri)
        if tail_seq != resolved_attempt_seq:
            return _failed_result(
                started_at=started_at,
                started_perf=started_perf,
                error_type="raw_payload_attempt_seq_mismatch",
                error_message="raw uri attempt sequence mismatch",
                retry_count=write_result.retry_count,
            )

    return RawPayloadWriteResult(
        ok=write_result.ok,
        uri=uri if write_result.ok else None,
        attempt_seq=resolved_attempt_seq if write_result.ok else None,
        content_hash=content_hash if write_result.ok else None,
        bytes_written=len(canonical_bytes) if write_result.ok else 0,
        started_at=started_at,
        ended_at=write_result.ended_at,
        duration_ms=write_result.duration_ms,
        retry_count=write_result.retry_count,
        error_type=write_result.error_type,
        error_message_redacted=write_result.error_message_redacted,
    )


def build_openviking_raw_uri(
    *,
    workspace_env: str,
    run_id: str,
    dispatch_id: str,
    worker_id: str,
    provider: str,
    api_name: str,
    attempt_seq: int,
) -> str:
    if os.environ.get(workspace_env, "").strip() == "":
        raise ValueError(f"{workspace_env} missing")
    if attempt_seq < 1:
        raise ValueError("attempt_seq must be >= 1")
    worker = worker_id.strip()
    if worker != FUNDAMENTAL_WORKER_ID:
        raise ValueError(f"worker_id must be {FUNDAMENTAL_WORKER_ID}")
    return (
        f"viking://resources/workflow/{run_id}/frontline/{worker}/{dispatch_id}/provider_raw/"
        f"{provider}/{api_name}/{attempt_seq}.json"
    )


def to_canonical_json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def sha256_hex(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def parse_raw_uri_attempt_seq(uri: str) -> int:
    tail = uri.rsplit("/", 1)[-1]
    if not tail.endswith(".json"):
        raise ValueError("raw uri tail must end with .json")
    return int(tail[:-5])


def put_openviking_object_create_only(
    *,
    endpoint_env: str,
    api_key_env: str,
    uri: str,
    body: bytes,
    timeout_ms: int,
    retry_limit: int,
) -> OpenVikingCreateOnlyPutResult:
    endpoint = os.environ.get(endpoint_env, "").strip()
    api_key = os.environ.get(api_key_env, "").strip()
    if endpoint == "" or api_key == "":
        return _create_only_failed("openviking_config_missing", f"{endpoint_env} or {api_key_env} missing", 0, 0)

    retries = max(0, retry_limit)
    attempt = 0
    started_perf = time.perf_counter()
    while True:
        attempt += 1
        try:
            content = body.decode("utf-8")
            existing = _openviking_stat_exists(
                endpoint=endpoint,
                api_key=api_key,
                uri=uri,
                timeout_ms=timeout_ms,
            )
            if existing:
                return _create_only_failed(
                    "object_already_exists",
                    "object already exists",
                    retry_count=attempt - 1,
                    duration_ms=_elapsed_ms(started_perf),
                )
            _create_openviking_file_via_pack_import(
                endpoint=endpoint,
                api_key=api_key,
                uri=uri,
                content=content,
                timeout_ms=timeout_ms,
            )
            _verify_openviking_file(
                endpoint=endpoint,
                api_key=api_key,
                uri=uri,
                expected_body=body,
                timeout_ms=timeout_ms,
            )
            return OpenVikingCreateOnlyPutResult(
                ok=True,
                ended_at=_now_iso(),
                duration_ms=_elapsed_ms(started_perf),
                retry_count=attempt - 1,
                error_type=None,
                error_message_redacted=None,
            )
        except error.HTTPError as exc:
            message = _http_error_message(exc)
            error_type = _http_error_type(exc, message)
            if error_type == "timeout" and attempt <= retries:
                time.sleep(_DEFAULT_TIMEOUT_RETRY_DELAY_SEC)
                continue
            return _create_only_failed(
                error_type,
                sanitize_error(message),
                retry_count=attempt - 1,
                duration_ms=_elapsed_ms(started_perf),
            )
        except TimeoutError as exc:
            if attempt <= retries:
                time.sleep(_DEFAULT_TIMEOUT_RETRY_DELAY_SEC)
                continue
            return _create_only_failed(
                "timeout",
                sanitize_error(exc),
                retry_count=attempt - 1,
                duration_ms=_elapsed_ms(started_perf),
            )
        except error.URLError as exc:
            reason = getattr(exc, "reason", exc)
            if isinstance(reason, TimeoutError) and attempt <= retries:
                time.sleep(_DEFAULT_TIMEOUT_RETRY_DELAY_SEC)
                continue
            return _create_only_failed(
                "openviking_unreachable",
                sanitize_error(reason),
                retry_count=attempt - 1,
                duration_ms=_elapsed_ms(started_perf),
            )
        except Exception as exc:  # noqa: BLE001
            return _create_only_failed(
                "openviking_write_failed",
                sanitize_error(exc),
                retry_count=attempt - 1,
                duration_ms=_elapsed_ms(started_perf),
            )


def _openviking_stat_exists(
    *,
    endpoint: str,
    api_key: str,
    uri: str,
    timeout_ms: int,
) -> bool:
    try:
        payload = _call_openviking_json(
            endpoint=endpoint,
            api_key=api_key,
            method="GET",
            path=_DEFAULT_STAT_PATH,
            query={"uri": uri},
            body=None,
            body_content_type=None,
            timeout_ms=timeout_ms,
        )
    except error.HTTPError as exc:
        if exc.code == 404:
            return False
        message = _http_error_message(exc)
        if _is_openviking_035_missing_stat_before_write(exc.code, message):
            return False
        raise
    if not isinstance(payload, dict):
        return True
    exists = payload.get("exists")
    if isinstance(exists, bool):
        return exists
    return True


def _create_openviking_file_via_pack_import(
    *,
    endpoint: str,
    api_key: str,
    uri: str,
    content: str,
    timeout_ms: int,
) -> None:
    parent_uri, pack_bytes = _build_single_file_ovpack_bytes(uri=uri, content=content)
    multipart_body, multipart_content_type = _encode_multipart_form_file(
        file_name=f"raw-{uuid.uuid4().hex}.ovpack",
        file_bytes=pack_bytes,
    )
    upload_result = _call_openviking_json(
        endpoint=endpoint,
        api_key=api_key,
        method="POST",
        path=_DEFAULT_TEMP_UPLOAD_PATH,
        query=None,
        body=multipart_body,
        body_content_type=multipart_content_type,
        timeout_ms=timeout_ms,
    )
    temp_file_id = _extract_temp_file_id(upload_result)
    _call_openviking_json(
        endpoint=endpoint,
        api_key=api_key,
        method="POST",
        path=_DEFAULT_PACK_IMPORT_PATH,
        query=None,
        body={"temp_file_id": temp_file_id, "parent": parent_uri, "force": False, "vectorize": False},
        body_content_type=None,
        timeout_ms=timeout_ms,
    )


def _verify_openviking_file(
    *,
    endpoint: str,
    api_key: str,
    uri: str,
    expected_body: bytes,
    timeout_ms: int,
) -> None:
    stat_payload = _call_openviking_json(
        endpoint=endpoint,
        api_key=api_key,
        method="GET",
        path=_DEFAULT_STAT_PATH,
        query={"uri": uri},
        body=None,
        body_content_type=None,
        timeout_ms=timeout_ms,
    )
    stat_size, stat_sha = _extract_stat_integrity(stat_payload, target_name=_target_name_from_uri(uri))
    actual_body = _call_openviking_bytes(
        endpoint=endpoint,
        api_key=api_key,
        method="GET",
        path=_DEFAULT_DOWNLOAD_PATH,
        query={"uri": uri},
        timeout_ms=timeout_ms,
    )
    expected_sha = sha256_hex(expected_body)
    actual_sha = sha256_hex(actual_body)
    actual_size = len(actual_body)
    if actual_sha != expected_sha:
        raise ValueError("openviking raw payload verification sha mismatch after write")
    if actual_size != len(expected_body):
        raise ValueError("openviking raw payload verification size mismatch after write")
    if stat_size != actual_size:
        raise ValueError("openviking raw payload verification stat size mismatch after readback")
    if stat_sha is not None and stat_sha != actual_sha:
        raise ValueError("openviking raw payload verification stat sha mismatch")


def _call_openviking_json(
    *,
    endpoint: str,
    api_key: str,
    method: str,
    path: str,
    query: dict[str, str] | None,
    body: dict[str, object] | bytes | None,
    body_content_type: str | None,
    timeout_ms: int,
) -> object:
    response_body, response_headers = _call_openviking(
        endpoint=endpoint,
        api_key=api_key,
        method=method,
        path=path,
        query=query,
        body=body,
        body_content_type=body_content_type,
        accept="application/json",
        timeout_ms=timeout_ms,
    )
    payload = _parse_json_payload(response_body)
    if isinstance(payload, dict) and payload.get("status") == "ok" and "result" in payload:
        return payload["result"]
    if isinstance(payload, dict) and payload.get("status") not in (None, "ok"):
        raise ValueError(f"OpenViking returned error: {payload.get('error') or payload.get('message') or payload}")
    _ = response_headers
    return payload


def _call_openviking_bytes(
    *,
    endpoint: str,
    api_key: str,
    method: str,
    path: str,
    query: dict[str, str] | None,
    timeout_ms: int,
) -> bytes:
    response_body, response_headers = _call_openviking(
        endpoint=endpoint,
        api_key=api_key,
        method=method,
        path=path,
        query=query,
        body=None,
        body_content_type=None,
        accept="application/octet-stream",
        timeout_ms=timeout_ms,
    )
    content_type = response_headers.get("Content-Type", "")
    if "application/json" in content_type:
        payload = _parse_json_payload(response_body)
        if isinstance(payload, dict) and payload.get("status") == "ok" and isinstance(payload.get("result"), str):
            return str(payload["result"]).encode("utf-8")
        raise ValueError("OpenViking download returned JSON error body")
    return response_body


def _call_openviking(
    *,
    endpoint: str,
    api_key: str,
    method: str,
    path: str,
    query: dict[str, str] | None,
    body: dict[str, object] | bytes | None,
    body_content_type: str | None,
    accept: str,
    timeout_ms: int,
) -> tuple[bytes, dict[str, str]]:
    url = _build_url(endpoint, path, query)
    headers = {"Accept": accept}
    if api_key:
        headers["X-API-Key"] = api_key
    data: bytes | None = None
    if isinstance(body, dict):
        headers["Content-Type"] = "application/json"
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    elif isinstance(body, bytes):
        headers["Content-Type"] = body_content_type or "application/octet-stream"
        data = body
    req = request.Request(url=url, method=method, headers=headers, data=data)
    with request.urlopen(req, timeout=max(timeout_ms, 1) / 1000.0) as resp:
        return resp.read(), {key: value for key, value in resp.headers.items()}


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
        raise ValueError(f"invalid uri: {uri}")
    parts = uri[len(prefix) :].split("/")
    if len(parts) < 5:
        raise ValueError(f"uri hierarchy too shallow: {uri}")
    run_id, stage, worker_id, call_id = parts[0], parts[1], parts[2], parts[3]
    relative_parts = parts[4:]
    if not run_id or not stage or not worker_id or not call_id:
        raise ValueError(f"uri missing identity: {uri}")
    if any(not item for item in relative_parts):
        raise ValueError(f"uri missing file path: {uri}")
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
        raise ValueError("OpenViking temp_upload missing temp_file_id")
    return temp_file_id


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
        raise ValueError("openviking raw payload verification stat target node not found")
    size_bytes = _to_non_negative_int(_first_non_none(node.get("size_bytes"), node.get("size"), node.get("file_size")))
    if size_bytes is None:
        raise ValueError("openviking raw payload verification missing stat size after write")
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
            raise ValueError("openviking raw payload verification stat sha field is invalid")
        sha256 = normalized
        break
    return size_bytes, sha256


def _target_name_from_uri(uri: str) -> str:
    normalized = uri.rstrip("/")
    cut = normalized.rfind("/")
    if cut <= len("viking://"):
        raise ValueError(f"invalid uri: {uri}")
    target_name = normalized[cut + 1 :]
    if not target_name:
        raise ValueError(f"invalid uri: {uri}")
    return target_name


def _parse_json_payload(raw: bytes) -> object:
    return json.loads(raw.decode("utf-8"))


def _as_record(value: object) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    return {}


def _first_non_none(*values: object) -> object | None:
    for value in values:
        if value is not None:
            return value
    return None


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


def _validate_request(request_input: RawPayloadWriteInput) -> None:
    if request_input.attempt_seq < 1:
        raise ValueError("attempt_seq must be >= 1")
    if request_input.timeout_ms < 1:
        raise ValueError("timeout_ms must be >= 1")
    if request_input.retry_limit < 0:
        raise ValueError("retry_limit must be >= 0")


def _mark_attempt_seq_used_v1(
    *,
    run_id: str,
    dispatch_id: str,
    provider: str,
    api_name: str,
    used_seq: int,
) -> None:
    if used_seq < 1:
        return
    key = (run_id.strip(), dispatch_id.strip(), provider.strip(), api_name.strip())
    with _SEQUENCE_LOCK:
        current = _SEQUENCE_COUNTERS.get(key, 0)
        if used_seq > current:
            _SEQUENCE_COUNTERS[key] = used_seq


def _create_only_failed(
    error_type: str,
    message: str,
    retry_count: int,
    duration_ms: int,
) -> OpenVikingCreateOnlyPutResult:
    return OpenVikingCreateOnlyPutResult(
        ok=False,
        ended_at=_now_iso(),
        duration_ms=duration_ms,
        retry_count=retry_count,
        error_type=error_type,
        error_message_redacted=message,
    )


def _failed_result(
    *,
    started_at: str,
    started_perf: float,
    error_type: str,
    error_message: str,
    retry_count: int,
) -> RawPayloadWriteResult:
    ended_at = _now_iso()
    return RawPayloadWriteResult(
        ok=False,
        uri=None,
        attempt_seq=None,
        content_hash=None,
        bytes_written=0,
        started_at=started_at,
        ended_at=ended_at,
        duration_ms=_elapsed_ms(started_perf),
        retry_count=retry_count,
        error_type=error_type,
        error_message_redacted=error_message,
    )


def _build_url(endpoint: str, path: str, query: dict[str, str] | None = None) -> str:
    base = endpoint.rstrip("/")
    fixed_path = path if path.startswith("/") else f"/{path}"
    url = parse.urljoin(f"{base}/", fixed_path.lstrip("/"))
    if query:
        url = f"{url}?{parse.urlencode(query)}"
    return url


def _http_error_type(exc: error.HTTPError, message: str) -> str:
    if message == "object_already_exists":
        return "object_already_exists"
    if exc.code in {409, 412}:
        return "object_already_exists"
    if exc.code in {401, 403}:
        return "permission_or_access"
    if exc.code == 408:
        return "timeout"
    return "openviking_http_error"


def _http_error_message(exc: error.HTTPError) -> str:
    try:
        raw = exc.read()
    except Exception:  # noqa: BLE001
        raw = b""
    details = ""
    if raw:
        try:
            parsed = json.loads(raw.decode("utf-8"))
            if isinstance(parsed, dict):
                details = str(parsed.get("error") or parsed.get("message") or parsed)
            else:
                details = str(parsed)
        except Exception:  # noqa: BLE001
            details = raw.decode("utf-8", errors="replace")
    if details:
        normalized = details.lower()
        if "already exist" in normalized or "exists" in normalized:
            return "object_already_exists"
        return f"HTTP {exc.code}: {details}"
    return f"HTTP {exc.code}: {exc.reason}"


def _is_openviking_035_missing_stat_before_write(status_code: int, message: str) -> bool:
    if status_code != 500:
        return False
    lower = message.lower()
    if "internal" not in lower:
        return False
    return "internal server error" in lower


def _elapsed_ms(started_perf: float) -> int:
    return max(0, int((time.perf_counter() - started_perf) * 1000))


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
