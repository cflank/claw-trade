from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import io
import importlib.util
import json
import sys
from pathlib import Path
import threading
from urllib import parse
import zipfile

import pytest


SCRIPTS_ROOT = Path("agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts")


def test_allocate_raw_attempt_seq_is_monotonic_and_respects_expected_min() -> None:
    seq1 = RAW_WRITER_MODULE.reserve_provider_attempt_seq_v1("run-1", "dispatch-1", "tushare", "fina_indicator")
    seq2 = RAW_WRITER_MODULE.reserve_provider_attempt_seq_v1("run-1", "dispatch-1", "tushare", "fina_indicator")
    seq3 = RAW_WRITER_MODULE.allocate_raw_attempt_seq_v1(
        run_id="run-1",
        dispatch_id="dispatch-1",
        provider="tushare",
        api_name="fina_indicator",
        expected_min_seq=10,
    )
    seq4 = RAW_WRITER_MODULE.reserve_provider_attempt_seq_v1("run-1", "dispatch-1", "tushare", "fina_indicator")
    assert seq1 == 1
    assert seq2 == 2
    assert seq3 == 10
    assert seq4 == 11


def test_write_raw_payload_success_canonical_hash_and_uri(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENVIKING_WORKSPACE", "workspace-a")

    calls: list[dict[str, object]] = []

    def fake_putter(**kwargs):
        calls.append(kwargs)
        return RAW_WRITER_MODULE.OpenVikingCreateOnlyPutResult(
            ok=True,
            ended_at="2026-05-07T00:00:01+00:00",
            duration_ms=12,
            retry_count=0,
            error_type=None,
            error_message_redacted=None,
        )

    request_input = RAW_WRITER_MODULE.RawPayloadWriteInput(
        provider="tushare",
        api_name="fina_indicator",
        run_id="run-2",
        dispatch_id="dispatch-2",
        worker_id="fundamental_analyst",
        attempt_seq=1,
        payload={"b": 2, "a": 1},
        timeout_ms=1000,
        retry_limit=1,
    )
    result = RAW_WRITER_MODULE.write_raw_payload_to_openviking(request_input, put_create_only=fake_putter)
    assert result.ok is True
    assert result.attempt_seq == 1
    assert result.uri == (
        "viking://resources/workflow/run-2/frontline/fundamental_analyst/"
        "dispatch-2/provider_raw/tushare/fina_indicator/1.json"
    )
    assert result.content_hash == "sha256:43258cff783fe7036d8a43033f830adfc60ec037382473548ac742b888292777"
    assert result.bytes_written == len(b'{"a":1,"b":2}')
    assert len(calls) == 1
    assert calls[0]["uri"] == result.uri


def test_write_raw_payload_conflict_then_retry_next_seq(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENVIKING_WORKSPACE", "workspace-b")

    sequence = [
        RAW_WRITER_MODULE.OpenVikingCreateOnlyPutResult(
            ok=False,
            ended_at="2026-05-07T00:00:01+00:00",
            duration_ms=3,
            retry_count=0,
            error_type="object_already_exists",
            error_message_redacted="exists",
        ),
        RAW_WRITER_MODULE.OpenVikingCreateOnlyPutResult(
            ok=True,
            ended_at="2026-05-07T00:00:01+00:00",
            duration_ms=8,
            retry_count=0,
            error_type=None,
            error_message_redacted=None,
        ),
    ]
    called_uris: list[str] = []

    def fake_putter(**kwargs):
        called_uris.append(str(kwargs["uri"]))
        return sequence.pop(0)

    request_input = RAW_WRITER_MODULE.RawPayloadWriteInput(
        provider="tushare",
        api_name="income",
        run_id="run-3",
        dispatch_id="dispatch-3",
        worker_id="fundamental_analyst",
        attempt_seq=1,
        payload={"x": 1},
        timeout_ms=1000,
        retry_limit=1,
    )
    result = RAW_WRITER_MODULE.write_raw_payload_to_openviking(request_input, put_create_only=fake_putter)
    assert result.ok is True
    assert result.attempt_seq == 2
    assert result.uri is not None
    assert result.uri.endswith("/2.json")
    assert called_uris[0].endswith("/1.json")
    assert called_uris[1].endswith("/2.json")
    seq_after_retry_success = RAW_WRITER_MODULE.reserve_provider_attempt_seq_v1(
        "run-3",
        "dispatch-3",
        "tushare",
        "income",
    )
    assert seq_after_retry_success == 3


def test_write_raw_payload_double_conflict_returns_conflict(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENVIKING_WORKSPACE", "workspace-c")

    def fake_putter(**_kwargs):
        return RAW_WRITER_MODULE.OpenVikingCreateOnlyPutResult(
            ok=False,
            ended_at="2026-05-07T00:00:01+00:00",
            duration_ms=5,
            retry_count=0,
            error_type="object_already_exists",
            error_message_redacted="exists",
        )

    request_input = RAW_WRITER_MODULE.RawPayloadWriteInput(
        provider="akshare",
        api_name="balance_sheet",
        run_id="run-4",
        dispatch_id="dispatch-4",
        worker_id="fundamental_analyst",
        attempt_seq=1,
        payload={"x": 1},
        timeout_ms=1000,
        retry_limit=1,
    )
    result = RAW_WRITER_MODULE.write_raw_payload_to_openviking(request_input, put_create_only=fake_putter)
    assert result.ok is False
    assert result.error_type == "raw_payload_write_conflict"
    assert result.uri is None
    assert result.attempt_seq is None
    assert result.content_hash is None


def test_write_raw_payload_failed_put_never_returns_fake_success(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("OPENVIKING_WORKSPACE", "workspace-d")

    def fake_putter(**_kwargs):
        return RAW_WRITER_MODULE.OpenVikingCreateOnlyPutResult(
            ok=False,
            ended_at="2026-05-07T00:00:01+00:00",
            duration_ms=7,
            retry_count=1,
            error_type="timeout",
            error_message_redacted="timeout",
        )

    request_input = RAW_WRITER_MODULE.RawPayloadWriteInput(
        provider="tushare",
        api_name="cashflow",
        run_id="run-5",
        dispatch_id="dispatch-5",
        worker_id="fundamental_analyst",
        attempt_seq=1,
        payload={"k": "v"},
        timeout_ms=1000,
        retry_limit=1,
    )
    result = RAW_WRITER_MODULE.write_raw_payload_to_openviking(request_input, put_create_only=fake_putter)
    assert result.ok is False
    assert result.uri is None
    assert result.attempt_seq is None
    assert result.content_hash is None
    assert result.bytes_written == 0
    assert result.error_type == "timeout"


def test_put_openviking_object_create_only_uses_real_pack_import_and_preserves_create_only(
    http_server,
    monkeypatch,
) -> None:  # type: ignore[no-untyped-def]
    endpoint, handler = http_server
    monkeypatch.setenv("OPENVIKING_ENDPOINT", endpoint)
    monkeypatch.setenv("OPENVIKING_API_KEY", "local-key")

    uri = (
        "viking://resources/workflow/run-http/frontline/fundamental_analyst/"
        "dispatch-http/provider_raw/tushare/daily_basic/1.json"
    )
    body = b'{"a":1}'
    result = RAW_WRITER_MODULE.put_openviking_object_create_only(
        endpoint_env="OPENVIKING_ENDPOINT",
        api_key_env="OPENVIKING_API_KEY",
        uri=uri,
        body=body,
        timeout_ms=1000,
        retry_limit=0,
    )
    assert result.ok is True
    assert handler.dynamic_content[uri] == body.decode("utf-8")
    assert ("POST", "/api/v1/resources/temp_upload") in handler.requests
    assert ("POST", "/api/v1/pack/import") in handler.requests
    assert handler.pack_import_payloads[0]["force"] is False
    assert all(method != "PUT" for method, _path in handler.requests)
    assert all(path != "/api/v1/content/write" for _method, path in handler.requests)

    conflict = RAW_WRITER_MODULE.put_openviking_object_create_only(
        endpoint_env="OPENVIKING_ENDPOINT",
        api_key_env="OPENVIKING_API_KEY",
        uri=uri,
        body=body,
        timeout_ms=1000,
        retry_limit=0,
    )
    assert conflict.ok is False
    assert conflict.error_type == "object_already_exists"


def _load_script_module(module_basename: str):
    scripts_path = str(SCRIPTS_ROOT.resolve())
    if scripts_path not in sys.path:
        sys.path.insert(0, scripts_path)
    module_path = SCRIPTS_ROOT / f"{module_basename}.py"
    spec = importlib.util.spec_from_file_location(f"cn_a_fundamental_{module_basename}", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"无法加载模块: {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"cn_a_fundamental_{module_basename}"] = module
    spec.loader.exec_module(module)
    return module


RAW_WRITER_MODULE = _load_script_module("raw_payload_writer")


class _RawWriterOpenVikingHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str]] = []
    dynamic_content: dict[str, str] = {}
    temp_files: dict[str, bytes] = {}
    pack_import_payloads: list[dict[str, object]] = []

    def do_GET(self) -> None:  # noqa: N802
        path = parse.urlsplit(self.path).path
        self.requests.append(("GET", path))
        query = parse.parse_qs(parse.urlsplit(self.path).query)
        uri = query.get("uri", [""])[0]
        if path == "/api/v1/fs/stat":
            if uri in self.dynamic_content:
                body = self.dynamic_content[uri].encode("utf-8")
                self._json_response(
                    {
                        "status": "ok",
                        "result": {
                            "uri": uri,
                            "name": uri.rstrip("/").split("/")[-1],
                            "exists": True,
                            "isDir": False,
                            "size": len(body),
                            "sha256": hashlib.sha256(body).hexdigest(),
                        },
                    }
                )
                return
            self._json_response(
                {
                    "status": "error",
                    "result": None,
                    "error": {"code": "INTERNAL", "message": "Internal server error", "details": None},
                    "telemetry": None,
                },
                status=500,
            )
            return
        if path == "/api/v1/content/download":
            if uri not in self.dynamic_content:
                self.send_response(404)
                self.end_headers()
                return
            body = self.dynamic_content[uri].encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        path = parse.urlsplit(self.path).path
        self.requests.append(("POST", path))
        if path == "/api/v1/resources/temp_upload":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            temp_file_id = f"tmp-{len(self.temp_files) + 1}.ovpack"
            self.temp_files[temp_file_id] = self._extract_multipart_file_bytes(body)
            self._json_response({"status": "ok", "result": {"temp_file_id": temp_file_id}})
            return
        if path == "/api/v1/pack/import":
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self.pack_import_payloads.append(payload)
            temp_file_id = payload.get("temp_file_id")
            parent = payload.get("parent")
            if not isinstance(temp_file_id, str) or not isinstance(parent, str):
                self.send_response(400)
                self.end_headers()
                return
            pack_bytes = self.temp_files[temp_file_id]
            self._import_ovpack(parent=parent, pack_bytes=pack_bytes)
            self._json_response({"status": "ok", "result": {"uri": parent}})
            return
        self.send_response(500)
        self.end_headers()

    def do_PUT(self) -> None:  # noqa: N802
        path = parse.urlsplit(self.path).path
        self.requests.append(("PUT", path))
        self.send_response(405)
        self.end_headers()

    def _extract_multipart_file_bytes(self, body: bytes) -> bytes:
        marker = b'name="file"'
        file_start = body.find(b"\r\n\r\n", body.find(marker))
        if file_start < 0:
            return b""
        file_start += 4
        file_end = body.find(b"\r\n--", file_start)
        if file_end < 0:
            return b""
        return body[file_start:file_end]

    def _import_ovpack(self, *, parent: str, pack_bytes: bytes) -> None:
        with zipfile.ZipFile(io.BytesIO(pack_bytes), "r") as zf:
            names = zf.namelist()
            if not names:
                return
            base_name = names[0].split("/")[0]
            root_uri = f"{parent.rstrip('/')}/{base_name}"
            for name in names:
                if name.endswith("/") or not name.startswith(f"{base_name}/"):
                    continue
                rel = name[len(base_name) + 1 :]
                if rel == "_._meta.json":
                    continue
                self.dynamic_content[f"{root_uri}/{rel}"] = zf.read(name).decode("utf-8")

    def _json_response(self, payload: dict[str, object], *, status: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        _ = format, args


@pytest.fixture
def http_server() -> tuple[str, type[_RawWriterOpenVikingHandler]]:
    _RawWriterOpenVikingHandler.requests = []
    _RawWriterOpenVikingHandler.dynamic_content = {}
    _RawWriterOpenVikingHandler.temp_files = {}
    _RawWriterOpenVikingHandler.pack_import_payloads = []
    server = HTTPServer(("127.0.0.1", 0), _RawWriterOpenVikingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", _RawWriterOpenVikingHandler
    finally:
        server.shutdown()
        thread.join(timeout=2)
