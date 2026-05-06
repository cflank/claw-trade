from __future__ import annotations

from http.server import BaseHTTPRequestHandler, HTTPServer
import hashlib
import io
import json
from pathlib import Path
import threading
from urllib import parse
import zipfile

import pytest

from claw_trade.artifacts.openviking_backend_http import (
    OpenVikingHttpBackend,
    create_default_backend,
)
from claw_trade.artifacts.openviking_client import OpenVikingAccessError


class _RecordingHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, str]] = []
    write_payloads: list[dict[str, object]] = []
    pack_import_payloads: list[dict[str, object]] = []
    temp_upload_payloads: list[bytes] = []
    stat_result: dict[str, object] = {"uri": "viking://resources/workflow/run-1/", "size": 1, "sha256": "a" * 64}
    read_result: object = "# report"
    mkdir_status: int = 200
    mkdir_error_payload: dict[str, object] | None = None
    stat_status: int = 200
    missing_stat_returns_404: bool = False
    missing_stat_returns_exists_false: bool = False
    download_status: int = 404
    dynamic_content: dict[str, str] = {}
    temp_upload_files: dict[str, bytes] = {}

    def do_GET(self) -> None:  # noqa: N802
        self.requests.append(("GET", self.path))
        query = parse.parse_qs(parse.urlsplit(self.path).query)
        uri = query.get("uri", [""])[0]
        if self.path.startswith("/api/v1/fs/stat"):
            if self.stat_status != 200:
                payload = self.mkdir_error_payload or {"status": "error", "error": "stat unavailable"}
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(self.stat_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            if uri in self.dynamic_content:
                content_bytes = self.dynamic_content[uri].encode("utf-8")
                self._json_response(
                    {
                        "status": "ok",
                        "result": {
                            "uri": uri,
                            "name": uri.rstrip("/").split("/")[-1],
                            "exists": True,
                            "isDir": False,
                            "size": len(content_bytes),
                            "sha256": hashlib.sha256(content_bytes).hexdigest(),
                        },
                    }
                )
                return
            if self.stat_result.get("uri") == uri and ("exists" in self.stat_result or "isDir" in self.stat_result):
                self._json_response({"status": "ok", "result": dict(self.stat_result)})
                return
            if uri.endswith("/") and uri.startswith("viking://resources/workflow/"):
                self._json_response({"status": "ok", "result": {"uri": uri, "exists": True, "isDir": True}})
                return
            if self.missing_stat_returns_exists_false:
                self._json_response({"status": "ok", "result": {"uri": uri, "exists": False, "isDir": False}})
                return
            if self.missing_stat_returns_404:
                self.send_response(404)
                self.end_headers()
                return
            self._json_response({"status": "ok", "result": dict(self.stat_result)})
            return
        if self.path.startswith("/api/v1/content/download"):
            if self.download_status != 200:
                self.send_response(self.download_status)
                self.end_headers()
                return
            if uri in self.dynamic_content:
                body = self.dynamic_content[uri].encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            self.send_response(404)
            self.end_headers()
            return
        if self.path.startswith("/api/v1/content/read"):
            if uri in self.dynamic_content:
                self._json_response({"status": "ok", "result": self.dynamic_content[uri]})
                return
            self._json_response({"status": "ok", "result": self.read_result})
            return
        self.send_response(500)
        self.end_headers()

    def do_POST(self) -> None:  # noqa: N802
        self.requests.append(("POST", self.path))
        if self.path == "/api/v1/fs/mkdir":
            if self.mkdir_status != 200:
                payload = self.mkdir_error_payload or {"status": "error", "error": "already exists"}
                encoded = json.dumps(payload).encode("utf-8")
                self.send_response(self.mkdir_status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
                return
            self._json_response({"status": "ok", "result": {"uri": "viking://resources/workflow/run-1/"}})
            return
        if self.path == "/api/v1/content/write":
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            uri = payload["uri"]
            content = payload["content"]
            if not isinstance(uri, str) or not isinstance(content, str):
                self.send_response(400)
                self.end_headers()
                return
            self.write_payloads.append(payload)
            self.dynamic_content[uri] = content
            self._json_response({"status": "ok", "result": {"uri": uri}})
            return
        if self.path == "/api/v1/resources/temp_upload":
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length)
            self.temp_upload_payloads.append(body)
            file_bytes = self._extract_multipart_file_bytes(body)
            temp_file_id = f"tmp-{len(self.temp_upload_payloads)}.ovpack"
            self.temp_upload_files[temp_file_id] = file_bytes
            self._json_response({"status": "ok", "result": {"temp_file_id": temp_file_id}})
            return
        if self.path == "/api/v1/pack/import":
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            self.pack_import_payloads.append(payload)
            temp_file_id = payload.get("temp_file_id")
            parent = payload.get("parent")
            if not isinstance(temp_file_id, str) or not isinstance(parent, str):
                self.send_response(400)
                self.end_headers()
                return
            pack_bytes = self.temp_upload_files.get(temp_file_id)
            if pack_bytes is None:
                self.send_response(404)
                self.end_headers()
                return
            imported_uri = self._import_ovpack(parent=parent, pack_bytes=pack_bytes)
            self._json_response({"status": "ok", "result": {"uri": imported_uri}})
            return
        self.send_response(500)
        self.end_headers()

    def _extract_multipart_file_bytes(self, body: bytes) -> bytes:
        marker = b'name="file"'
        if marker not in body:
            return b""
        file_start = body.find(b"\r\n\r\n", body.find(marker))
        if file_start < 0:
            return b""
        file_start += 4
        file_end = body.find(b"\r\n--", file_start)
        if file_end < 0:
            return b""
        return body[file_start:file_end]

    def _import_ovpack(self, *, parent: str, pack_bytes: bytes) -> str:
        with zipfile.ZipFile(io.BytesIO(pack_bytes), "r") as zf:
            names = zf.namelist()
            if not names:
                return parent.rstrip("/") + "/"
            base_name = names[0].split("/")[0]
            root_uri = f"{parent.rstrip('/')}/{base_name}"
            for name in names:
                if name.endswith("/") or not name.startswith(f"{base_name}/"):
                    continue
                rel = name[len(base_name) + 1 :]
                if rel == "_._meta.json":
                    continue
                content = zf.read(name).decode("utf-8")
                self.dynamic_content[f"{root_uri}/{rel}"] = content
            return f"{root_uri}/"

    def _json_response(self, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        _ = format, args


@pytest.fixture
def http_server() -> tuple[str, type[_RecordingHandler]]:
    _RecordingHandler.requests = []
    _RecordingHandler.write_payloads = []
    _RecordingHandler.pack_import_payloads = []
    _RecordingHandler.temp_upload_payloads = []
    _RecordingHandler.stat_result = {"uri": "viking://resources/workflow/run-1/", "size": 1, "sha256": "a" * 64}
    _RecordingHandler.read_result = "# report"
    _RecordingHandler.mkdir_status = 200
    _RecordingHandler.mkdir_error_payload = None
    _RecordingHandler.stat_status = 200
    _RecordingHandler.missing_stat_returns_404 = False
    _RecordingHandler.missing_stat_returns_exists_false = False
    _RecordingHandler.download_status = 404
    _RecordingHandler.dynamic_content = {}
    _RecordingHandler.temp_upload_files = {}
    server = HTTPServer(("127.0.0.1", 0), _RecordingHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    endpoint = f"http://127.0.0.1:{server.server_port}"
    try:
        yield endpoint, _RecordingHandler
    finally:
        server.shutdown()
        thread.join(timeout=2)
        server.server_close()


def test_factory_returns_backend_with_required_methods(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENVIKING_ENDPOINT", "http://127.0.0.1:1933")
    backend = create_default_backend()

    assert callable(getattr(backend, "ensure_namespace", None))
    assert callable(getattr(backend, "fetch_receipt_by_path", None))
    assert callable(getattr(backend, "fetch_stat_by_uri", None))
    assert callable(getattr(backend, "fetch_content_by_uri", None))
    assert callable(getattr(backend, "fetch_l2_index_by_uri", None))
    assert callable(getattr(backend, "prepare_probe_receipt", None))


def test_ensure_namespace_uses_mkdir_and_stat_without_latest_list_compact(
    http_server: tuple[str, type[_RecordingHandler]],
) -> None:
    endpoint, handler_cls = http_server
    handler_cls.stat_result = {"uri": "viking://resources/workflow/run-1/", "exists": True, "isDir": True}
    backend = OpenVikingHttpBackend(endpoint=endpoint)

    backend.ensure_namespace("workflow/run-1")

    paths = [path for _, path in handler_cls.requests]
    assert any(path.startswith("/api/v1/fs/mkdir") for path in paths)
    assert any(path.startswith("/api/v1/fs/stat") for path in paths)
    assert all("/latest" not in path for path in paths)
    assert all("/list" not in path for path in paths)
    assert all("/compact" not in path for path in paths)


def test_fetch_receipt_by_path_fails_when_required_field_missing(tmp_path: Path) -> None:
    backend = OpenVikingHttpBackend(endpoint="http://127.0.0.1:1933")
    receipt_path = tmp_path / "openviking-receipt.json"
    receipt_path.write_text(
        json.dumps(
            {
                "uri": "viking://resources/workflow/run-1/frontline/market_analyst/call-1/report.md",
                "run_id": "run-1",
                "call_id": "call-1",
                "worker_id": "market_analyst",
                "stage": "frontline",
                "target_name": "report",
                "sha256": "a" * 64,
                "size_bytes": 1,
                "written_at": "2026-05-04T12:00:00Z",
                "receipt_id": "r-1",
                "source": "openviking_adapter_verified_receipt",
                "receipt_label": "verified_openviking_write_receipt",
                # 故意缺失 receipt_origin
                "is_openviking_native_receipt": False,
                "verification": {"verified": True, "method": "m"},
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(OpenVikingAccessError, match="receipt_origin"):
        backend.fetch_receipt_by_path(receipt_path)


def test_fetch_content_by_uri_fails_when_download_endpoint_unavailable(
    http_server: tuple[str, type[_RecordingHandler]],
) -> None:
    endpoint, handler_cls = http_server
    handler_cls.read_result = "# markdown"
    backend = OpenVikingHttpBackend(endpoint=endpoint)

    with pytest.raises(OpenVikingAccessError, match="HTTP 404"):
        backend.fetch_content_by_uri("viking://resources/workflow/run-1/frontline/market_analyst/call-1/report.md")
    assert any(path.startswith("/api/v1/content/download") for _, path in handler_cls.requests)
    assert all(not path.startswith("/api/v1/content/read") for _, path in handler_cls.requests)


def test_fetch_stat_directory_does_not_download_content(
    http_server: tuple[str, type[_RecordingHandler]],
) -> None:
    endpoint, handler_cls = http_server
    handler_cls.stat_result = {"uri": "viking://resources/workflow/run-1/", "exists": True, "isDir": True}
    backend = OpenVikingHttpBackend(endpoint=endpoint)

    stat = backend.fetch_stat_by_uri("viking://resources/workflow/run-1/")

    assert stat.ok
    assert stat.exists
    assert stat.is_dir
    assert stat.sha256 is None
    assert stat.size_bytes is None
    assert all(not path.startswith("/api/v1/content/download") for _, path in handler_cls.requests)


def test_ensure_namespace_passes_when_mkdir_500_but_stat_proves_directory(
    http_server: tuple[str, type[_RecordingHandler]],
) -> None:
    endpoint, handler_cls = http_server
    handler_cls.mkdir_status = 500
    handler_cls.mkdir_error_payload = {"status": "error", "error": "directory already exists"}
    handler_cls.stat_result = {"uri": "viking://resources/workflow/run-1/", "exists": True, "isDir": True}
    backend = OpenVikingHttpBackend(endpoint=endpoint)

    backend.ensure_namespace("workflow/run-1")

    paths = [path for _, path in handler_cls.requests]
    assert any(path.startswith("/api/v1/fs/mkdir") for path in paths)
    assert any(path.startswith("/api/v1/fs/stat") for path in paths)
    assert all(not path.startswith("/api/v1/content/download") for path in paths)
    assert all(not path.startswith("/api/v1/content/read") for path in paths)


def test_ensure_namespace_fails_when_mkdir_500_and_stat_not_directory(
    http_server: tuple[str, type[_RecordingHandler]],
) -> None:
    endpoint, handler_cls = http_server
    handler_cls.mkdir_status = 500
    handler_cls.mkdir_error_payload = {"status": "error", "error": "mkdir failed"}
    handler_cls.stat_result = {"uri": "viking://resources/workflow/run-1/", "exists": True, "isDir": False, "size": 1, "sha256": "a" * 64}
    backend = OpenVikingHttpBackend(endpoint=endpoint)

    with pytest.raises(OpenVikingAccessError, match="HTTP 500"):
        backend.ensure_namespace("workflow/run-1")


def test_ensure_namespace_fails_when_mkdir_500_and_stat_unavailable(
    http_server: tuple[str, type[_RecordingHandler]],
) -> None:
    endpoint, handler_cls = http_server
    handler_cls.mkdir_status = 500
    handler_cls.mkdir_error_payload = {"status": "error", "error": "mkdir failed"}
    handler_cls.stat_status = 500
    backend = OpenVikingHttpBackend(endpoint=endpoint)

    with pytest.raises(OpenVikingAccessError, match="HTTP 500"):
        backend.ensure_namespace("workflow/run-1")


def test_prepare_probe_receipt_creates_missing_uri_via_pack_import_and_verifies_without_replace_write(
    http_server: tuple[str, type[_RecordingHandler]],
    tmp_path: Path,
) -> None:
    endpoint, handler_cls = http_server
    handler_cls.download_status = 200
    handler_cls.missing_stat_returns_404 = True
    backend = OpenVikingHttpBackend(endpoint=endpoint)
    receipt_path = tmp_path / "runs" / "probe" / "openviking" / "receipt.json"
    stat_uri = "viking://resources/workflow/probe/frontline/probe_worker/probe_call/report.md"

    backend.prepare_probe_receipt(receipt_path=receipt_path, stat_uri=stat_uri)

    assert receipt_path.exists()
    payload = json.loads(receipt_path.read_text(encoding="utf-8"))
    expected_bytes = b"# openviking probe report\n"
    expected_sha = hashlib.sha256(expected_bytes).hexdigest()
    expected_size = len(expected_bytes)
    assert payload["source"] == "openviking_adapter_verified_receipt"
    assert payload["receipt_origin"] == "adapter_verified_non_native"
    assert payload["is_openviking_native_receipt"] is False
    assert payload["run_id"] == "probe"
    assert payload["stage"] == "frontline"
    assert payload["worker_id"] == "probe_worker"
    assert payload["call_id"] == "probe_call"
    assert payload["uri"] == stat_uri
    assert payload["sha256"] == expected_sha
    assert payload["size_bytes"] == expected_size
    assert payload["verification"]["expected_write_sha256"] == expected_sha
    assert payload["verification"]["expected_write_size_bytes"] == expected_size
    assert payload["verification"]["readback_sha256"] == expected_sha
    assert payload["verification"]["readback_size_bytes"] == expected_size
    assert payload["verification"]["verified"] is True
    assert isinstance(payload["http_operations"], list)
    assert handler_cls.dynamic_content[stat_uri].startswith("# openviking probe report")
    assert all("probe-read.json" not in uri for uri in handler_cls.dynamic_content)
    paths = [path for _, path in handler_cls.requests]
    assert all(not path.startswith("/api/v1/content/write") for path in paths)
    assert any(path.startswith("/api/v1/resources/temp_upload") for path in paths)
    assert any(path.startswith("/api/v1/pack/import") for path in paths)
    assert handler_cls.write_payloads == []
    assert all(payload.get("mode") != "create" for payload in handler_cls.write_payloads)
    assert all(payload.get("vectorize") is False for payload in handler_cls.pack_import_payloads)
    assert any(path.startswith("/api/v1/content/download") for path in paths)
    assert all(not path.startswith("/api/v1/content/read") for path in paths)
    assert any(path.startswith("/api/v1/fs/stat") for path in paths)


def test_prepare_probe_receipt_treats_stat_exists_false_as_missing_and_uses_pack_import_only(
    http_server: tuple[str, type[_RecordingHandler]],
    tmp_path: Path,
) -> None:
    endpoint, handler_cls = http_server
    handler_cls.download_status = 200
    handler_cls.missing_stat_returns_exists_false = True
    backend = OpenVikingHttpBackend(endpoint=endpoint)
    receipt_path = tmp_path / "runs" / "probe" / "openviking" / "receipt.json"
    stat_uri = "viking://resources/workflow/probe/frontline/probe_worker/probe_call/report.md"

    backend.prepare_probe_receipt(receipt_path=receipt_path, stat_uri=stat_uri)

    paths = [path for _, path in handler_cls.requests]
    assert any(path.startswith("/api/v1/resources/temp_upload") for path in paths)
    assert any(path.startswith("/api/v1/pack/import") for path in paths)
    assert all(not path.startswith("/api/v1/content/write") for path in paths)
    assert handler_cls.write_payloads == []
    assert any(path.startswith("/api/v1/fs/stat") for path in paths)
    assert any(path.startswith("/api/v1/content/download") for path in paths)
    assert all(not path.startswith("/api/v1/content/read") for path in paths)
    assert receipt_path.exists()


def test_prepare_probe_receipt_replaces_existing_files_without_wait_parameter(
    http_server: tuple[str, type[_RecordingHandler]],
    tmp_path: Path,
) -> None:
    endpoint, handler_cls = http_server
    handler_cls.download_status = 200
    backend = OpenVikingHttpBackend(endpoint=endpoint)
    receipt_path = tmp_path / "runs" / "probe" / "openviking" / "receipt.json"
    stat_uri = "viking://resources/workflow/probe/frontline/probe_worker/probe_call/report.md"
    handler_cls.dynamic_content[stat_uri] = "old report"

    backend.prepare_probe_receipt(receipt_path=receipt_path, stat_uri=stat_uri)

    assert receipt_path.exists()
    assert all(payload.get("mode") == "replace" for payload in handler_cls.write_payloads)
    assert all("wait" not in payload for payload in handler_cls.write_payloads)
    paths = [path for _, path in handler_cls.requests]
    assert any(path.startswith("/api/v1/fs/stat") for path in paths)
    assert any(path.startswith("/api/v1/content/download") for path in paths)
    assert all(not path.startswith("/api/v1/content/read") for path in paths)
    assert all(not path.startswith("/api/v1/resources/temp_upload") for path in paths)
    assert all(not path.startswith("/api/v1/pack/import") for path in paths)


def test_prepare_probe_receipt_does_not_leave_receipt_when_write_readback_verification_fails(
    http_server: tuple[str, type[_RecordingHandler]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    endpoint, handler_cls = http_server
    handler_cls.download_status = 200
    backend = OpenVikingHttpBackend(endpoint=endpoint)
    receipt_path = tmp_path / "runs" / "probe" / "openviking" / "receipt.json"
    stat_uri = "viking://resources/workflow/probe/frontline/probe_worker/probe_call/report.md"

    def _fail_write_verified_content(**_: object) -> dict[str, object]:
        raise OpenVikingAccessError("openviking probe verification sha mismatch after write", category="hash_mismatch")

    monkeypatch.setattr(backend, "_write_verified_content", _fail_write_verified_content)

    with pytest.raises(OpenVikingAccessError, match="probe verification sha mismatch"):
        backend.prepare_probe_receipt(receipt_path=receipt_path, stat_uri=stat_uri)
    assert not receipt_path.exists()
