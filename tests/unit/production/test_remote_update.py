from __future__ import annotations

import json
import hashlib
import multiprocessing
import os
import tarfile
from io import BytesIO
from pathlib import Path

import requests
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from claw_trade.production.maintenance_lock import ProductionMaintenanceLock
from claw_trade.production import host_locks, remote_update
from claw_trade.production.host_locks import hold_host_lock
from claw_trade.production.remote_update import RemoteUpdateService
from claw_trade.production.updater_state import UpdaterStateStore


@pytest.fixture(autouse=True)
def local_lock_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(host_locks, "_expected_identity", lambda: (os.getuid(), os.getgid()))


def _install_host_lock(install_root: Path) -> Path:
    install_root.mkdir(parents=True, exist_ok=True)
    path = install_root / "host-operations.lock"
    path.write_bytes(b"")
    path.chmod(0o660)
    return path


def _hold_host_lock_until_released(path: Path, ready, release) -> None:
    host_locks._expected_identity = lambda: (os.getuid(), os.getgid())
    with hold_host_lock(path, exclusive=True, blocking=False):
        ready.set()
        release.wait()


def test_remote_update_check_verifies_signed_manifest_and_writes_state(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    key = Ed25519PrivateKey.generate()
    public_key_path = install_root / "shared" / "updates" / "update-signing-public.pem"
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    manifest = json.dumps(
        {
            "product": "claw-trade",
            "channel": "stable",
            "version": "1.2.3",
            "arch": "linux-x86_64",
            "archive": "claw-trade-production-1.2.3-20260626T120000Z.tar.gz",
            "sha256": "a" * 64,
            "created_at": "2026-06-26T00:00:00Z",
            "min_current_version": "1.0.0",
        }
    ).encode()
    objects = {
        "https://updates.example.com/stable/manifest.json": manifest,
        "https://updates.example.com/stable/manifest.json.sig": key.sign(manifest),
    }

    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=public_key_path,
        fetch_bytes=lambda url: objects[url],
    )

    result = service.check_manifest()

    assert result.status == "update_available"
    assert result.manifest is not None
    assert result.manifest.version == "1.2.3"
    state = UpdaterStateStore(install_root=install_root).read()
    assert state["status"] == "update_available"
    assert state["latestVersion"] == "1.2.3"


def test_remote_update_check_treats_current_archive_as_up_to_date(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    release = install_root / "releases" / "claw-trade-production-1.0.0-20260707T014943Z"
    release.mkdir(parents=True)
    (install_root / "current").symlink_to(release)
    key = Ed25519PrivateKey.generate()
    public_key_path = install_root / "shared" / "updates" / "update-signing-public.pem"
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    manifest = json.dumps(
        {
            "product": "claw-trade",
            "channel": "stable",
            "version": "1.0.0",
            "arch": "linux-x86_64",
            "archive": "claw-trade-production-1.0.0-20260707T014943Z.tar.gz",
            "sha256": "a" * 64,
            "created_at": "2026-07-07T00:00:00Z",
            "min_current_version": "1.0.0",
        }
    ).encode()
    objects = {
        "https://updates.example.com/stable/manifest.json": manifest,
        "https://updates.example.com/stable/manifest.json.sig": key.sign(manifest),
    }
    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.0.0",
        public_key_path=public_key_path,
        fetch_bytes=lambda url: objects[url],
    )

    result = service.check_manifest()

    assert result.status == "up_to_date"
    assert result.manifest is None
    assert UpdaterStateStore(install_root=install_root).read()["status"] == "up_to_date"


def test_remote_update_check_reports_incomplete_when_current_link_moved_but_process_is_old(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    release = install_root / "releases" / "claw-trade-production-1.2.3-20260626T120000Z"
    release.mkdir(parents=True)
    (install_root / "current").symlink_to(release)
    key = Ed25519PrivateKey.generate()
    public_key_path = install_root / "shared" / "updates" / "update-signing-public.pem"
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    manifest = json.dumps(
        {
            "product": "claw-trade",
            "channel": "stable",
            "version": "1.2.3",
            "arch": "linux-x86_64",
            "archive": "claw-trade-production-1.2.3-20260626T120000Z.tar.gz",
            "sha256": "a" * 64,
            "created_at": "2026-06-26T00:00:00Z",
            "min_current_version": "1.0.0",
        }
    ).encode()
    objects = {
        "https://updates.example.com/stable/manifest.json": manifest,
        "https://updates.example.com/stable/manifest.json.sig": key.sign(manifest),
    }
    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=public_key_path,
        fetch_bytes=lambda url: objects[url],
    )

    result = service.check_manifest()

    assert result.status == "install_incomplete"
    assert result.to_user_dict() == {
        "status": "install_incomplete",
        "currentVersion": "1.2.2",
        "latestVersion": "1.2.3",
        "archive": "claw-trade-production-1.2.3-20260626T120000Z.tar.gz",
        "userMessage": "版本 1.2.3 已安装但当前运行版本仍是 1.2.2，请重启服务。",
    }
    state = UpdaterStateStore(install_root=install_root).read()
    assert state["status"] == "install_incomplete"
    assert state["latestVersion"] == "1.2.3"


def test_remote_update_check_validates_manifest_before_reporting_incomplete(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    release = install_root / "releases" / "claw-trade-production-1.2.3-20260626T120000Z"
    release.mkdir(parents=True)
    (install_root / "current").symlink_to(release)
    key = Ed25519PrivateKey.generate()
    public_key_path = install_root / "shared" / "updates" / "update-signing-public.pem"
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    manifest = json.dumps(
        {
            "product": "wrong-product",
            "channel": "stable",
            "version": "1.2.3",
            "arch": "linux-x86_64",
            "archive": "claw-trade-production-1.2.3-20260626T120000Z.tar.gz",
            "sha256": "a" * 64,
            "created_at": "2026-06-26T00:00:00Z",
            "min_current_version": "1.0.0",
        }
    ).encode()
    objects = {
        "https://updates.example.com/stable/manifest.json": manifest,
        "https://updates.example.com/stable/manifest.json.sig": key.sign(manifest),
    }
    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=public_key_path,
        fetch_bytes=lambda url: objects[url],
    )

    result = service.check_manifest()

    assert result.status == "check_failed"
    assert "manifest product 不匹配" in result.user_message


def test_remote_update_check_rejects_manifest_version_archive_mismatch_before_reporting_incomplete(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    release = install_root / "releases" / "claw-trade-production-1.2.2-20260626T120000Z"
    release.mkdir(parents=True)
    (install_root / "current").symlink_to(release)
    key = Ed25519PrivateKey.generate()
    public_key_path = install_root / "shared" / "updates" / "update-signing-public.pem"
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    manifest = json.dumps(
        {
            "product": "claw-trade",
            "channel": "stable",
            "version": "1.2.3",
            "arch": "linux-x86_64",
            "archive": "claw-trade-production-1.2.2-20260626T120000Z.tar.gz",
            "sha256": "a" * 64,
            "created_at": "2026-06-26T00:00:00Z",
            "min_current_version": "1.0.0",
        }
    ).encode()
    objects = {
        "https://updates.example.com/stable/manifest.json": manifest,
        "https://updates.example.com/stable/manifest.json.sig": key.sign(manifest),
    }
    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=public_key_path,
        fetch_bytes=lambda url: objects[url],
    )

    result = service.check_manifest()

    assert result.status == "check_failed"
    assert "archive 版本与 manifest version 不匹配" in result.user_message


def test_remote_update_check_rejects_bad_manifest_signature(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    key = Ed25519PrivateKey.generate()
    public_key_path = install_root / "shared" / "updates" / "update-signing-public.pem"
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    objects = {
        "https://updates.example.com/stable/manifest.json": b'{"product":"claw-trade"}',
        "https://updates.example.com/stable/manifest.json.sig": b"bad-signature",
    }

    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=public_key_path,
        fetch_bytes=lambda url: objects[url],
    )

    result = service.check_manifest()

    assert result.status == "verify_failed"
    assert UpdaterStateStore(install_root=install_root).read()["status"] == "verify_failed"


def test_remote_update_check_treats_missing_manifest_as_up_to_date_without_leaking_url(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    key = Ed25519PrivateKey.generate()
    public_key_path = install_root / "shared" / "updates" / "update-signing-public.pem"
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    response = requests.Response()
    response.status_code = 404
    error = requests.HTTPError(
        "404 Client Error: Not Found for url: https://updates.example.com/stable/manifest.json",
        response=response,
    )
    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=public_key_path,
        fetch_bytes=lambda _url: (_ for _ in ()).throw(error),
    )

    result = service.check_manifest()

    assert result.status == "up_to_date"
    assert result.user_message == "当前已是最新版本。"
    assert "https://" not in result.user_message
    assert "updates.example.com" not in UpdaterStateStore(install_root=install_root).read()["userMessage"]


def test_remote_update_check_fails_when_manifest_signature_is_missing(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    key = Ed25519PrivateKey.generate()
    public_key_path = install_root / "shared" / "updates" / "update-signing-public.pem"
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    manifest = b'{"product":"claw-trade"}'
    response = requests.Response()
    response.status_code = 404
    error = requests.HTTPError(
        "404 Client Error: Not Found for url: https://updates.example.com/stable/manifest.json.sig",
        response=response,
    )

    def fetch(url: str) -> bytes:
        if url.endswith("manifest.json.sig"):
            raise error
        return manifest

    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=public_key_path,
        fetch_bytes=fetch,
    )

    result = service.check_manifest()

    assert result.status == "check_failed"
    assert result.user_message == "远程更新清单检查失败：远程文件不存在或更新源尚未发布。"
    assert "https://" not in result.user_message
    assert "updates.example.com" not in UpdaterStateStore(install_root=install_root).read()["userMessage"]


def test_remote_update_check_records_not_configured(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    service = RemoteUpdateService(install_root=install_root, base_url=None, current_version="1.2.2")

    result = service.check_manifest()

    assert result.status == "not_configured"
    assert UpdaterStateStore(install_root=install_root).read()["status"] == "not_configured"


def test_remote_update_rejects_symlinked_shared_directory_without_external_writes(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    outside = tmp_path / "outside"
    install_root.mkdir(parents=True)
    outside.mkdir()
    (install_root / "shared").symlink_to(outside, target_is_directory=True)
    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        fetch_bytes=lambda _: (_ for _ in ()).throw(AssertionError("fetch should not run")),
        preflight_runner=lambda _: (_ for _ in ()).throw(AssertionError("preflight should not run")),
    )

    check = service.check_manifest()
    install = service.install_checked_update()

    assert check.status == "check_failed"
    assert install.status == "install_failed"
    assert "符号链接" in check.user_message
    assert "符号链接" in install.user_message
    assert not (outside / "updates").exists()


def test_remote_update_installs_signed_archive_and_schedules_apply(tmp_path: Path) -> None:
    install_root, objects = _signed_update_objects(tmp_path)
    apply_requests: list[Path] = []
    maintenance_lock = ProductionMaintenanceLock(install_root=install_root)
    seen_locked: list[bool] = []

    def apply_starter(path: Path) -> None:
        seen_locked.append(maintenance_lock.is_locked())
        apply_requests.append(path)

    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=_update_public_key_path(install_root),
        fetch_bytes=lambda url: objects[url],
        apply_update_starter=apply_starter,
        maintenance_lock=maintenance_lock,
    )

    result = service.install_checked_update()

    release_dir = install_root / "releases" / "claw-trade-production-1.2.3-20260626T120000Z"
    assert result.status == "restart_scheduled"
    assert not release_dir.exists()
    assert not (install_root / "current").exists()
    assert seen_locked == [True]
    assert apply_requests == [install_root / "shared" / "updates" / "apply-request.json"]
    apply_payload = json.loads(apply_requests[0].read_text(encoding="utf-8"))
    assert apply_payload["archive"] == "claw-trade-production-1.2.3-20260626T120000Z.tar.gz"
    assert apply_payload["archiveSha256"] == hashlib.sha256(objects["https://updates.example.com/stable/claw-trade-production-1.2.3-20260626T120000Z.tar.gz"]).hexdigest()
    assert apply_payload["target"] == str(release_dir)
    assert apply_payload["previous"] is None
    assert (install_root / "shared" / "updates" / "downloads" / "manifest.json").exists()
    assert (install_root / "shared" / "updates" / "downloads" / "manifest.json.sig").exists()
    assert (install_root / "shared" / "updates" / "downloads" / "claw-trade-production-1.2.3-20260626T120000Z.tar.gz.sig").exists()
    assert (install_root / "shared" / "updates" / "apply.lock").read_text(encoding="utf-8").startswith("pending:")
    assert maintenance_lock.is_locked() is False
    assert UpdaterStateStore(install_root=install_root).read()["status"] == "restart_scheduled"


def test_remote_update_does_not_download_while_host_lock_is_held_and_recovers_after_holder_exit(
    tmp_path: Path,
) -> None:
    install_root, objects = _signed_update_objects(tmp_path)
    fetch_calls: list[str] = []

    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=_update_public_key_path(install_root),
        fetch_bytes=lambda url: fetch_calls.append(url) or objects[url],
        apply_update_starter=lambda _: None,
    )

    context = multiprocessing.get_context("spawn")
    ready = context.Event()
    release = context.Event()
    process = context.Process(
        target=_hold_host_lock_until_released,
        args=(install_root / "host-operations.lock", ready, release),
    )
    process.start()
    try:
        assert ready.wait(timeout=15), f"lock holder did not start; exitcode={process.exitcode}"
        result = service.install_checked_update()
    finally:
        release.set()
        process.join(timeout=2)

    assert result.status == "install_failed"
    assert "维护中" in result.user_message
    assert fetch_calls == []
    assert process.exitcode == 0
    assert service.install_checked_update().status == "restart_scheduled"
    assert fetch_calls


def test_archive_download_resumes_after_interrupted_stream(tmp_path: Path, monkeypatch) -> None:
    payload = b"release-archive-bytes" * 64
    calls: list[dict[str, str] | None] = []

    class FakeResponse:
        def __init__(
            self,
            *,
            status_code: int,
            body: bytes,
            headers: dict[str, str],
            fail_after_first_chunk: bool = False,
        ) -> None:
            self.status_code = status_code
            self._body = body
            self.headers = headers
            self._fail_after_first_chunk = fail_after_first_chunk

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_exc: object) -> None:
            return None

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise requests.HTTPError(f"HTTP {self.status_code}")

        def iter_content(self, *, chunk_size: int):
            first = self._body[:10]
            rest = self._body[10:]
            yield first
            if self._fail_after_first_chunk:
                raise requests.ConnectionError("connection dropped")
            if rest:
                yield rest

    def fake_get(url: str, **kwargs: object) -> FakeResponse:
        del url
        headers = kwargs.get("headers")
        calls.append(headers if isinstance(headers, dict) else None)
        if len(calls) == 1:
            return FakeResponse(
                status_code=200,
                body=payload,
                headers={"Content-Length": str(len(payload))},
                fail_after_first_chunk=True,
            )
        assert headers == {"Range": "bytes=10-"}
        return FakeResponse(
            status_code=206,
            body=payload[10:],
            headers={"Content-Range": f"bytes 10-{len(payload) - 1}/{len(payload)}"},
        )

    monkeypatch.setattr(remote_update.requests, "get", fake_get)
    monkeypatch.setattr(remote_update.time, "sleep", lambda _seconds: None)
    target = tmp_path / "downloads" / "claw-trade-production-1.2.3-20260626T120000Z.tar.gz"
    progress: list[tuple[int, int | None]] = []

    remote_update._requests_download_file(
        "https://updates.example.com/archive.tar.gz",
        target,
        max_attempts=2,
        progress=lambda received, total: progress.append((received, total)),
    )

    assert target.read_bytes() == payload
    assert calls == [None, {"Range": "bytes=10-"}]
    assert (10, len(payload)) in progress
    assert progress[-1] == (len(payload), len(payload))
    assert not target.with_name(f".{target.name}.part").exists()


def test_remote_update_status_exposes_download_progress(tmp_path: Path) -> None:
    install_root = tmp_path / "opt" / "claw-trade"
    service = RemoteUpdateService(install_root=install_root, base_url="https://updates.example.com/stable", current_version="1.2.2")
    writer = service._download_progress_writer(
        version="1.2.3",
        archive_name="claw-trade-production-1.2.3-20260626T120000Z.tar.gz",
    )

    writer(10 * 1024 * 1024, 100 * 1024 * 1024)

    status = service.status_for_user()
    assert status["currentVersion"] == "1.2.2"
    assert status["status"] == "downloading"
    assert status["progressPercent"] == 16
    assert status["progressLabel"] == "正在下载更新包"
    assert status["downloadReceivedBytes"] == 10 * 1024 * 1024
    assert status["downloadTotalBytes"] == 100 * 1024 * 1024
    assert status["userMessage"] == "正在下载更新包：10 MB / 100 MB。网络中断后会自动继续。"


def test_remote_update_rejects_archive_hash_mismatch_without_switching_current(tmp_path: Path) -> None:
    install_root, objects = _signed_update_objects(tmp_path, archive_sha256="b" * 64)
    old_release = install_root / "releases" / "claw-trade-production-1.2.2-20260601T000000Z"
    old_release.mkdir(parents=True)
    (install_root / "current").symlink_to(old_release)
    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=_update_public_key_path(install_root),
        fetch_bytes=lambda url: objects[url],
        preflight_runner=lambda _: None,
    )

    result = service.install_checked_update()

    assert result.status == "verify_failed"
    assert (install_root / "current").resolve(strict=False) == old_release
    assert not (install_root / "releases" / "claw-trade-production-1.2.3-20260626T120000Z").exists()


def test_remote_update_install_hides_remote_url_from_archive_fetch_error(tmp_path: Path) -> None:
    install_root, objects = _signed_update_objects(tmp_path)
    response = requests.Response()
    response.status_code = 404
    error = requests.HTTPError(
        "404 Client Error: Not Found for url: https://updates.example.com/stable/claw-trade-production-1.2.3.tar.gz",
        response=response,
    )

    def fetch(url: str) -> bytes:
        if url.endswith(".tar.gz"):
            raise error
        return objects[url]

    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=_update_public_key_path(install_root),
        fetch_bytes=fetch,
        preflight_runner=lambda _: None,
    )

    result = service.install_checked_update()

    assert result.status == "install_failed"
    assert result.user_message == "更新安装失败：远程文件不存在或更新源尚未发布。"
    assert "https://" not in result.user_message
    assert "updates.example.com" not in UpdaterStateStore(install_root=install_root).read()["userMessage"]


def test_remote_update_rejects_existing_target_release_without_overwriting(tmp_path: Path) -> None:
    install_root, objects = _signed_update_objects(tmp_path)
    existing_release = install_root / "releases" / "claw-trade-production-1.2.3-20260626T120000Z"
    marker = existing_release / "marker.txt"
    existing_release.mkdir(parents=True)
    marker.write_text("keep", encoding="utf-8")
    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=_update_public_key_path(install_root),
        fetch_bytes=lambda url: objects[url],
        preflight_runner=lambda _: None,
    )

    result = service.install_checked_update()

    assert result.status == "install_failed"
    assert marker.read_text(encoding="utf-8") == "keep"


def test_remote_update_keeps_current_when_target_release_exists(tmp_path: Path) -> None:
    install_root, objects = _signed_update_objects(tmp_path)
    old_release = install_root / "releases" / "claw-trade-production-1.2.2-20260601T000000Z"
    target_release = install_root / "releases" / "claw-trade-production-1.2.3-20260626T120000Z"
    old_release.mkdir(parents=True)
    target_release.mkdir(parents=True)
    (install_root / "current").symlink_to(old_release)

    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=_update_public_key_path(install_root),
        fetch_bytes=lambda url: objects[url],
    )

    result = service.install_checked_update()

    assert result.status == "install_failed"
    assert (install_root / "current").resolve(strict=False) == old_release
    assert "目标版本目录已存在" in result.user_message


def test_remote_update_keeps_current_when_apply_service_cannot_start(tmp_path: Path) -> None:
    install_root, objects = _signed_update_objects(tmp_path)
    old_release = install_root / "releases" / "claw-trade-production-1.2.2-20260601T000000Z"
    old_release.mkdir(parents=True)
    (install_root / "current").symlink_to(old_release)

    def fail_start(_request_path: Path) -> None:
        raise ValueError("systemd start failed")

    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=_update_public_key_path(install_root),
        fetch_bytes=lambda url: objects[url],
        preflight_runner=lambda _: None,
        apply_update_starter=fail_start,
    )

    result = service.install_checked_update()

    assert result.status == "install_failed"
    assert (install_root / "current").resolve(strict=False) == old_release
    assert not (install_root / "releases" / "claw-trade-production-1.2.3-20260626T120000Z").exists()
    assert not (install_root / "shared" / "updates" / "apply-request.json").exists()
    assert not (install_root / "shared" / "updates" / "apply.lock").exists()
    assert "当前版本未切换" in result.user_message


def test_remote_update_reports_install_failed_without_previous_current_when_apply_cannot_start(tmp_path: Path) -> None:
    install_root, objects = _signed_update_objects(tmp_path)

    def fail_start(_request_path: Path) -> None:
        raise ValueError("systemd start failed")

    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=_update_public_key_path(install_root),
        fetch_bytes=lambda url: objects[url],
        preflight_runner=lambda _: None,
        apply_update_starter=fail_start,
    )

    result = service.install_checked_update()

    assert result.status == "install_failed"


def test_remote_update_install_reports_existing_lock(tmp_path: Path) -> None:
    install_root, objects = _signed_update_objects(tmp_path)
    lock = install_root / "shared" / "updates" / "updater.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("busy", encoding="utf-8")
    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=_update_public_key_path(install_root),
        fetch_bytes=lambda url: objects[url],
        preflight_runner=lambda _: None,
    )

    result = service.install_checked_update()

    assert result.status == "install_failed"
    assert "已有更新任务" in result.user_message


def test_remote_update_install_reports_existing_apply_lock(tmp_path: Path) -> None:
    install_root, objects = _signed_update_objects(tmp_path)
    lock = install_root / "shared" / "updates" / "apply.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("busy", encoding="utf-8")
    UpdaterStateStore(install_root=install_root).write(status="health_checking", user_message="正在检查更新后服务健康状态。")
    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=_update_public_key_path(install_root),
        fetch_bytes=lambda url: objects[url],
        preflight_runner=lambda _: None,
    )

    result = service.install_checked_update()

    assert result.status == "health_checking"
    assert result.user_message == "正在检查更新后服务健康状态。"


def test_remote_update_check_does_not_overwrite_apply_status(tmp_path: Path) -> None:
    install_root, objects = _signed_update_objects(tmp_path)
    lock = install_root / "shared" / "updates" / "apply.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text("busy", encoding="utf-8")
    UpdaterStateStore(install_root=install_root).write(status="rollback_started", user_message="正在回滚上一版本。")
    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.2",
        public_key_path=_update_public_key_path(install_root),
        fetch_bytes=lambda url: objects[url],
        preflight_runner=lambda _: None,
    )

    result = service.check_manifest()

    assert result.status == "rollback_started"
    assert result.user_message == "正在回滚上一版本。"
    assert UpdaterStateStore(install_root=install_root).read()["status"] == "rollback_started"


def test_remote_update_check_reports_up_to_date_for_current_version(tmp_path: Path) -> None:
    install_root, objects = _signed_update_objects(tmp_path)
    service = RemoteUpdateService(
        install_root=install_root,
        base_url="https://updates.example.com/stable",
        current_version="1.2.3",
        public_key_path=_update_public_key_path(install_root),
        fetch_bytes=lambda url: objects[url],
        preflight_runner=lambda _: None,
    )

    result = service.check_manifest()

    assert result.status == "up_to_date"
    assert result.user_message == "当前已是最新版本。"


def _signed_update_objects(
    tmp_path: Path,
    *,
    archive_sha256: str | None = None,
) -> tuple[Path, dict[str, bytes]]:
    install_root = tmp_path / "opt" / "claw-trade"
    key = Ed25519PrivateKey.generate()
    public_key_path = _update_public_key_path(install_root)
    public_key_path.parent.mkdir(parents=True)
    _install_host_lock(install_root)
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    archive = _build_release_archive("claw-trade-production-1.2.3-20260626T120000Z")
    import hashlib

    manifest = json.dumps(
        {
            "product": "claw-trade",
            "channel": "stable",
            "version": "1.2.3",
            "arch": "linux-x86_64",
            "archive": "claw-trade-production-1.2.3-20260626T120000Z.tar.gz",
            "sha256": archive_sha256 or hashlib.sha256(archive).hexdigest(),
            "created_at": "2026-06-26T00:00:00Z",
            "min_current_version": "1.0.0",
        }
    ).encode()
    return install_root, {
        "https://updates.example.com/stable/manifest.json": manifest,
        "https://updates.example.com/stable/manifest.json.sig": key.sign(manifest),
        "https://updates.example.com/stable/claw-trade-production-1.2.3-20260626T120000Z.tar.gz": archive,
        "https://updates.example.com/stable/claw-trade-production-1.2.3-20260626T120000Z.tar.gz.sig": key.sign(archive),
    }


def _update_public_key_path(install_root: Path) -> Path:
    return install_root / "shared" / "updates" / "update-signing-public.pem"


def _build_release_archive(release_name: str) -> bytes:
    buffer = BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for name in (release_name, f"{release_name}/bin"):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        data = b"#!/usr/bin/env bash\nexit 0\n"
        info = tarfile.TarInfo(f"{release_name}/bin/claw-trade-preflight")
        info.mode = 0o755
        info.size = len(data)
        archive.addfile(info, BytesIO(data))
    return buffer.getvalue()
