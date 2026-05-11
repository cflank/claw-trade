from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Mapping

import pytest


REPO_ROOT = Path(__file__).resolve().parents[2]
SHARED_PYTHON_ROOT = REPO_ROOT / "openclaw_plugins" / "claw-trade-frontline-tools" / "python"
if str(SHARED_PYTHON_ROOT) not in sys.path:
    sys.path.insert(0, str(SHARED_PYTHON_ROOT))

from frontline_data_pack.config import OpenVikingConfig  # noqa: E402
from frontline_data_pack.errors import (  # noqa: E402
    L2_HASH_MISMATCH,
    L2_WRITE_FAILED,
    OPENVIKING_AUTH_FAILED,
    FrontlineValidationError,
)
from frontline_data_pack.evidence import (  # noqa: E402
    OpenVikingClientError,
    OpenVikingHttpEvidenceClient,
    OpenVikingStatResult,
    OpenVikingWriteResult,
    build_l2_uri,
    write_l2_evidence,
)
from frontline_data_pack.models import L2WriteRequest, L2WriteTarget  # noqa: E402


def test_t_l2_001_build_l2_uri_contains_required_dimensions() -> None:
    target = L2WriteTarget(
        run_id="run-20260508",
        stage="frontline",
        worker_id="market_analyst",
        call_id="call-7",
        relative_path="provider_raw/akshare/stock_zh_a_hist/1.json",
        content_type="application/json",
    )

    uri = build_l2_uri(target)

    assert uri.startswith("viking://resources/workflow/run-20260508/frontline/market_analyst/call-7/evidence/")
    assert uri.endswith("provider_raw/akshare/stock_zh_a_hist/1.json")


def test_t_l2_001_write_l2_evidence_success_returns_verified_receipt() -> None:
    request = _sample_request()
    client = _InMemoryClient(
        stat=OpenVikingStatResult(size_bytes=len(request.content_bytes), sha256=_sha(request.content_bytes), exists=True),
        read_content=request.content_bytes,
    )

    receipt = write_l2_evidence(request, client=client)

    assert receipt.readback_verified is True
    assert receipt.stat_verified is True
    assert receipt.sha256 == _sha(request.content_bytes)
    assert receipt.size_bytes == len(request.content_bytes)


def test_t_l2_001_write_l2_evidence_size_mismatch_raises_l2_hash_mismatch() -> None:
    request = _sample_request()
    client = _InMemoryClient(
        stat=OpenVikingStatResult(size_bytes=len(request.content_bytes) + 1, sha256=_sha(request.content_bytes), exists=True),
        read_content=request.content_bytes,
    )

    with pytest.raises(FrontlineValidationError) as exc_info:
        write_l2_evidence(request, client=client)

    assert exc_info.value.code == L2_HASH_MISMATCH


def test_t_l2_001_bearer_auth_sets_authorization_header_and_never_leaks_token() -> None:
    token = "secret_token_value_for_test"
    captured_headers: list[Mapping[str, str]] = []

    def transport(method: str, url: str, headers: Mapping[str, str], body: bytes, timeout_sec: float) -> tuple[int, bytes]:
        _ = method, url, body, timeout_sec
        captured_headers.append(dict(headers))
        raise OpenVikingClientError(f"401 unauthorized {headers.get('Authorization')!r}", status_code=401)

    client = OpenVikingHttpEvidenceClient(
        OpenVikingConfig(base_uri="https://openviking.internal", auth_mode="bearer", token=token),
        transport=transport,
    )

    with pytest.raises(FrontlineValidationError) as exc_info:
        write_l2_evidence(_sample_request(), client=client)

    assert captured_headers
    assert captured_headers[0].get("Authorization") == f"Bearer {token}"
    assert exc_info.value.code == OPENVIKING_AUTH_FAILED
    assert token not in exc_info.value.message
    assert "Bearer ***" in exc_info.value.message


@pytest.mark.parametrize("method,path", [("POST", "/api/v1/content/write"), ("GET", "/api/v1/fs/stat?uri=x")])
def test_openviking_http_client_none_auth_does_not_send_authorization(method: str, path: str) -> None:
    captured_headers: list[Mapping[str, str]] = []
    captured_post_payloads: list[dict[str, object]] = []

    def transport(http_method: str, url: str, headers: Mapping[str, str], body: bytes, timeout_sec: float) -> tuple[int, bytes]:
        _ = timeout_sec
        assert http_method == method
        assert url == f"https://openviking.internal{path}"
        captured_headers.append(dict(headers))
        if http_method == "POST":
            captured_post_payloads.append(json.loads(body.decode("utf-8")))
            return 200, json.dumps({"receipt_id": "receipt-1"}).encode("utf-8")
        return 200, json.dumps({"size_bytes": 3, "sha256": _sha(b"abc")}).encode("utf-8")

    client = OpenVikingHttpEvidenceClient(
        OpenVikingConfig(base_uri="https://openviking.internal", auth_mode="none", token=None),
        transport=transport,
    )
    if method == "POST":
        result = client.write(
            uri="viking://resources/workflow/run/frontline/market_analyst/call/evidence/provider_attempts.json",
            content_bytes=b"abc",
            content_type="application/json",
            metadata={},
        )
        assert result.receipt_id == "receipt-1"
    else:
        result = client.stat(uri="x")
        assert result.size_bytes == 3

    assert captured_headers
    assert "Authorization" not in captured_headers[0]
    if method == "POST":
        assert captured_post_payloads == [
            {
                "content": "abc",
                "mode": "replace",
                "uri": "viking://resources/workflow/run/frontline/market_analyst/call/evidence/provider_attempts.json",
            }
        ]


def test_openviking_http_client_write_non_utf8_binary_raises_l2_write_failed() -> None:
    transport_called = False

    def transport(http_method: str, url: str, headers: Mapping[str, str], body: bytes, timeout_sec: float) -> tuple[int, bytes]:
        nonlocal transport_called
        transport_called = True
        _ = http_method, url, headers, body, timeout_sec
        return 200, b"{}"

    client = OpenVikingHttpEvidenceClient(
        OpenVikingConfig(base_uri="https://openviking.internal", auth_mode="none", token=None),
        transport=transport,
    )
    request = L2WriteRequest(
        target=L2WriteTarget(
            run_id="run-1",
            stage="frontline",
            worker_id="market_analyst",
            call_id="call-1",
            relative_path="charts/price.png",
            content_type="image/png",
        ),
        content_bytes=b"\x89PNG\r\n\x1a\nbinary",
        metadata={"kind": "chart"},
    )

    with pytest.raises(FrontlineValidationError) as exc_info:
        write_l2_evidence(request, client=client)

    assert exc_info.value.code == L2_WRITE_FAILED
    assert transport_called is False


def test_openviking_http_client_write_not_found_creates_missing_file_via_ovpack_import() -> None:
    captured_calls: list[tuple[str, str, Mapping[str, str], bytes]] = []

    def transport(http_method: str, url: str, headers: Mapping[str, str], body: bytes, timeout_sec: float) -> tuple[int, bytes]:
        _ = timeout_sec
        captured_calls.append((http_method, url, dict(headers), body))
        if url.endswith("/api/v1/content/write"):
            raise OpenVikingClientError("NOT_FOUND: resource does not exist")
        if url.endswith("/api/v1/resources/temp_upload"):
            return 200, json.dumps({"result": {"temp_file_id": "temp-123"}}).encode("utf-8")
        if url.endswith("/api/v1/pack/import"):
            return 200, json.dumps({"result": {"id": "import-456"}}).encode("utf-8")
        raise AssertionError(f"unexpected request: {http_method} {url}")

    client = OpenVikingHttpEvidenceClient(
        OpenVikingConfig(base_uri="https://openviking.internal", auth_mode="none", token=None),
        transport=transport,
    )
    uri = "viking://resources/workflow/run/frontline/market_analyst/call/evidence/provider_attempts.json"

    result = client.write(
        uri=uri,
        content_bytes=b'{"hello":"world"}',
        content_type="application/json",
        metadata={},
    )

    assert result.receipt_id == "import-456"
    assert result.verification_uri == uri
    assert [call[1] for call in captured_calls] == [
        "https://openviking.internal/api/v1/content/write",
        "https://openviking.internal/api/v1/resources/temp_upload",
        "https://openviking.internal/api/v1/pack/import",
    ]
    assert "Authorization" not in captured_calls[0][2]
    upload_body = captured_calls[1][3]
    assert b'Content-Disposition: form-data; name="file"; filename="seed-' in upload_body
    assert b'.ovpack"' in upload_body
    assert b"PK" in upload_body
    pack_import_payload = json.loads(captured_calls[2][3].decode("utf-8"))
    assert pack_import_payload == {
        "force": True,
        "parent": "viking://resources/workflow/run/frontline/market_analyst",
        "temp_file_id": "temp-123",
        "vectorize": False,
    }


def test_openviking_http_client_write_not_found_never_calls_resources_add_resource() -> None:
    target_uri = "viking://resources/workflow/run/frontline/market_analyst/call/evidence/provider_raw/sina/1.json"
    captured_calls: list[tuple[str, str, Mapping[str, str], bytes]] = []

    def transport(http_method: str, url: str, headers: Mapping[str, str], body: bytes, timeout_sec: float) -> tuple[int, bytes]:
        _ = timeout_sec
        captured_calls.append((http_method, url, dict(headers), body))
        if url.endswith("/api/v1/content/write"):
            raise OpenVikingClientError("NOT_FOUND: resource does not exist")
        if url.endswith("/api/v1/resources/temp_upload"):
            return 200, json.dumps({"result": {"temp_file_id": "temp-123"}}).encode("utf-8")
        if url.endswith("/api/v1/pack/import"):
            return 200, json.dumps({"result": {"id": "import-789"}}).encode("utf-8")
        raise AssertionError(f"unexpected request: {http_method} {url}")

    client = OpenVikingHttpEvidenceClient(
        OpenVikingConfig(base_uri="https://openviking.internal", auth_mode="none", token=None),
        transport=transport,
    )

    result = client.write(
        uri=target_uri,
        content_bytes=b'{"hello":"world"}',
        content_type="application/json",
        metadata={},
    )

    assert result.receipt_id == "import-789"
    assert result.verification_uri == target_uri
    assert all("/api/v1/resources" not in call[1] or call[1].endswith("/temp_upload") for call in captured_calls)


def test_openviking_http_client_write_large_json_not_found_uses_pack_import_without_add_resource() -> None:
    captured_calls: list[tuple[str, str, Mapping[str, str], bytes]] = []

    def transport(http_method: str, url: str, headers: Mapping[str, str], body: bytes, timeout_sec: float) -> tuple[int, bytes]:
        _ = headers, timeout_sec
        captured_calls.append((http_method, url, dict(headers), body))
        if url.endswith("/api/v1/content/write"):
            raise OpenVikingClientError("NOT_FOUND: resource does not exist")
        if url.endswith("/api/v1/resources/temp_upload"):
            return 200, json.dumps({"result": {"temp_file_id": "temp-large"}}).encode("utf-8")
        if url.endswith("/api/v1/pack/import"):
            return 200, json.dumps({"result": {"id": "import-large"}}).encode("utf-8")
        raise AssertionError(f"unexpected request: {http_method} {url}")

    client = OpenVikingHttpEvidenceClient(
        OpenVikingConfig(base_uri="https://openviking.internal", auth_mode="none", token=None),
        transport=transport,
    )
    payload = {"rows": [{"index": index, "text": "x" * 256} for index in range(300)]}

    result = client.write(
        uri="viking://resources/workflow/run/frontline/market_analyst/call/evidence/provider_raw/akshare/large.json",
        content_bytes=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        content_type="application/json",
        metadata={},
    )

    assert result.receipt_id == "import-large"
    assert [call[1] for call in captured_calls] == [
        "https://openviking.internal/api/v1/content/write",
        "https://openviking.internal/api/v1/resources/temp_upload",
        "https://openviking.internal/api/v1/pack/import",
    ]
    assert all(not url.endswith("/api/v1/resources") for _, url, _, _ in captured_calls)


def test_openviking_http_client_write_directory_error_updates_existing_materialized_file() -> None:
    captured_calls: list[tuple[str, str, Mapping[str, str], bytes]] = []
    verification_uri = (
        "viking://resources/workflow/run/frontline/market_analyst/call/evidence/"
        "provider_attempts.json/upload_abc123.md"
    )

    def transport(http_method: str, url: str, headers: Mapping[str, str], body: bytes, timeout_sec: float) -> tuple[int, bytes]:
        _ = headers, timeout_sec
        captured_calls.append((http_method, url, dict(headers), body))
        if url.endswith("/api/v1/content/write"):
            payload = json.loads(body.decode("utf-8"))
            if payload["uri"].endswith("/provider_attempts.json"):
                raise OpenVikingClientError("INVALID_ARGUMENT: write only supports existing files, got directory")
            if payload["uri"].endswith("/upload_abc123.md"):
                return 200, json.dumps({"result": {"id": "receipt-2"}}).encode("utf-8")
        if "/api/v1/fs/tree?" in url:
            return 200, json.dumps({"result": [{"uri": verification_uri, "isDir": False, "rel_path": "upload_abc123.md"}]}).encode(
                "utf-8"
            )
        raise AssertionError(f"unexpected request: {http_method} {url}")

    client = OpenVikingHttpEvidenceClient(
        OpenVikingConfig(base_uri="https://openviking.internal", auth_mode="none", token=None),
        transport=transport,
    )
    result = client.write(
        uri="viking://resources/workflow/run/frontline/market_analyst/call/evidence/provider_attempts.json",
        content_bytes=b'{"hello":"world"}',
        content_type="application/json",
        metadata={},
    )

    assert result.receipt_id == "receipt-2"
    assert result.verification_uri == verification_uri
    assert [call[1] for call in captured_calls] == [
        "https://openviking.internal/api/v1/content/write",
        "https://openviking.internal/api/v1/fs/tree?uri=viking%3A%2F%2Fresources%2Fworkflow%2Frun%2Ffrontline%2Fmarket_analyst%2Fcall%2Fevidence%2Fprovider_attempts.json",
        "https://openviking.internal/api/v1/content/write",
    ]


def test_write_l2_evidence_not_found_create_path_still_passes_stat_and_readback_verification() -> None:
    target_uri = "viking://resources/workflow/run-1/frontline/market_analyst/call-1/evidence/provider_attempts.json"
    captured_urls: list[str] = []

    def transport(http_method: str, url: str, headers: Mapping[str, str], body: bytes, timeout_sec: float) -> tuple[int, bytes]:
        _ = headers, timeout_sec
        captured_urls.append(url)
        if url.endswith("/api/v1/content/write"):
            raise OpenVikingClientError("NOT_FOUND: missing target file")
        if url.endswith("/api/v1/resources/temp_upload"):
            return 200, json.dumps({"result": {"temp_file_id": "temp-1"}}).encode("utf-8")
        if url.endswith("/api/v1/pack/import"):
            return 200, json.dumps({"result": {"id": "import-1"}}).encode("utf-8")
        if "/api/v1/fs/stat?" in url:
            return 200, json.dumps({"result": {"size": 11, "isDir": False}}).encode("utf-8")
        if "/api/v1/content/download?" in url:
            return 200, b'{"ok":true}'
        raise AssertionError(f"unexpected request: {http_method} {url}")

    client = OpenVikingHttpEvidenceClient(
        OpenVikingConfig(base_uri="https://openviking.internal", auth_mode="none", token=None),
        transport=transport,
    )
    receipt = write_l2_evidence(_sample_request(), client=client)

    assert receipt.uri == target_uri
    assert receipt.sha256 == _sha(b'{"ok":true}')
    assert receipt.size_bytes == 11
    assert receipt.readback_verified is True
    assert receipt.stat_verified is False
    assert any("/api/v1/fs/stat?" in url and "provider_attempts.json" in url for url in captured_urls)
    assert any("/api/v1/content/download?" in url and "provider_attempts.json" in url for url in captured_urls)
    assert all("/api/v1/resources" not in url or url.endswith("/temp_upload") for url in captured_urls)


class _InMemoryClient:
    def __init__(self, *, stat: OpenVikingStatResult, read_content: bytes) -> None:
        self._stat = stat
        self._read_content = read_content

    def write(
        self,
        *,
        uri: str,
        content_bytes: bytes,
        content_type: str,
        metadata: Mapping[str, str],
    ) -> OpenVikingWriteResult:
        _ = uri, content_bytes, content_type, metadata
        return OpenVikingWriteResult(receipt_id="receipt-1")

    def stat(self, *, uri: str) -> OpenVikingStatResult:
        _ = uri
        return self._stat

    def read(self, *, uri: str) -> bytes:
        _ = uri
        return self._read_content

    def download(self, *, uri: str) -> bytes:
        _ = uri
        return self._read_content


def _sample_request() -> L2WriteRequest:
    return L2WriteRequest(
        target=L2WriteTarget(
            run_id="run-1",
            stage="frontline",
            worker_id="market_analyst",
            call_id="call-1",
            relative_path="provider_attempts.json",
            content_type="application/json",
        ),
        content_bytes=b'{"ok":true}',
        metadata={"provider": "akshare"},
    )


def _sha(content: bytes) -> str:
    import hashlib

    return f"sha256:{hashlib.sha256(content).hexdigest()}"
