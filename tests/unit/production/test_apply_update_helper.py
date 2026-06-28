from __future__ import annotations

import importlib.machinery
import importlib.util
import hashlib
import json
import shutil
import tarfile
from io import BytesIO
from pathlib import Path
from types import ModuleType

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "packaging" / "production" / "root-helper" / "claw-trade-apply-update"


def test_apply_update_helper_switches_current_after_health_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    install_root, request_path, old_release, new_release = _apply_request(tmp_path)
    (install_root / "current").symlink_to(old_release)
    restart_calls: list[str] = []
    monkeypatch.setattr(module, "restart_main_services", lambda: restart_calls.append("restart") or True)
    monkeypatch.setattr(module, "wait_for_ui_health", lambda: True)

    result = module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))

    assert result == 0
    assert restart_calls == ["restart"]
    assert (install_root / "current").resolve(strict=False) == new_release
    assert json.loads((install_root / "shared" / "updates" / "updater-state.json").read_text())["status"] == "installed"
    assert request_path.exists() is False
    assert not (install_root / "shared" / "updates" / "apply.lock").exists()


def test_apply_update_helper_rolls_back_when_health_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    install_root, request_path, old_release, new_release = _apply_request(tmp_path)
    (install_root / "current").symlink_to(old_release)
    health_results = iter([False, True])
    restart_calls: list[str] = []
    monkeypatch.setattr(module, "restart_main_services", lambda: restart_calls.append("restart") or True)
    monkeypatch.setattr(module, "wait_for_ui_health", lambda: next(health_results))
    real_run = module.subprocess.run

    def fake_run(args, **kwargs):
        if args == ["/bin/systemctl", "start", "claw-trade-rescue-trigger.service"]:
            return None
        return real_run(args, **kwargs)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    result = module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))

    assert result == 1
    assert restart_calls == ["restart", "restart"]
    assert (install_root / "current").resolve(strict=False) == old_release
    assert json.loads((install_root / "shared" / "updates" / "updater-state.json").read_text())["status"] == "rollback_succeeded"
    assert request_path.exists() is False
    assert not (install_root / "shared" / "updates" / "apply.lock").exists()


def test_apply_update_helper_ignores_request_previous_for_rollback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    install_root, request_path, old_release, _new_release = _apply_request(tmp_path)
    other_release = install_root / "releases" / "claw-trade-production-1.2.1-20260501T000000Z"
    other_release.mkdir(parents=True)
    payload = json.loads(request_path.read_text(encoding="utf-8"))
    payload["previous"] = str(other_release)
    request_path.write_text(json.dumps(payload), encoding="utf-8")
    (install_root / "current").symlink_to(old_release)
    health_results = iter([False, True])
    monkeypatch.setattr(module, "restart_main_services", lambda: True)
    monkeypatch.setattr(module, "wait_for_ui_health", lambda: next(health_results))

    result = module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))

    assert result == 1
    assert (install_root / "current").resolve(strict=False) == old_release


def test_apply_update_helper_adopts_pending_apply_lock(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    module = _load_helper()
    install_root, request_path, old_release, new_release = _apply_request(tmp_path)
    (install_root / "current").symlink_to(old_release)
    lock_path = install_root / "shared" / "updates" / "apply.lock"
    lock_path.write_text("pending:123", encoding="utf-8")
    monkeypatch.setattr(module, "restart_main_services", lambda: True)
    monkeypatch.setattr(module, "wait_for_ui_health", lambda: True)

    result = module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))

    assert result == 0
    assert (install_root / "current").resolve(strict=False) == new_release
    assert request_path.exists() is False
    assert lock_path.exists() is False


def test_apply_update_helper_rejects_existing_apply_lock(tmp_path: Path) -> None:
    module = _load_helper()
    install_root, request_path, _old_release, _new_release = _apply_request(tmp_path)
    lock_path = install_root / "shared" / "updates" / "apply.lock"
    lock_path.write_text("busy", encoding="utf-8")

    with pytest.raises(ValueError, match="已有更新应用任务"):
        module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))


def test_apply_update_helper_rejects_symlinked_updates_directory(tmp_path: Path) -> None:
    module = _load_helper()
    install_root, request_path, _old_release, _new_release = _apply_request(tmp_path)
    updates = install_root / "shared" / "updates"
    outside = tmp_path / "outside-updates"
    outside.mkdir()
    for child in updates.iterdir():
        if child.is_dir() and not child.is_symlink():
            shutil.rmtree(child)
        else:
            child.unlink()
    updates.rmdir()
    updates.symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="updates 不能是符号链接"):
        module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))


def test_apply_update_helper_rejects_request_manifest_mismatch(tmp_path: Path) -> None:
    module = _load_helper()
    install_root, request_path, old_release, _new_release = _apply_request(tmp_path)
    request_path.write_text(
        json.dumps(
            {
                "version": "9.9.9",
                "archive": "claw-trade-production-1.2.3-20260626T120000Z.tar.gz",
                "archiveSha256": "a" * 64,
                "target": str(install_root / "releases" / "claw-trade-production-9.9.9-20260626T120000Z"),
                "previous": str(old_release),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="apply request 与 manifest 不匹配"):
        module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))


def test_apply_update_helper_rejects_version_not_newer_than_current(tmp_path: Path) -> None:
    module = _load_helper()
    install_root, request_path, _old_release, new_release = _apply_request(tmp_path)
    new_release.mkdir(parents=True)
    (install_root / "current").symlink_to(new_release)

    with pytest.raises(ValueError, match="manifest version 不高于当前版本"):
        module.run(install_root=install_root, request_path=request_path, public_key_path=_public_key_path(install_root))


def _load_helper() -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("claw_trade_apply_update_helper", str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


def _apply_request(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    install_root = tmp_path / "opt" / "claw-trade"
    old_release = install_root / "releases" / "claw-trade-production-1.2.2-20260601T000000Z"
    new_release = install_root / "releases" / "claw-trade-production-1.2.3-20260626T120000Z"
    old_release.mkdir(parents=True)
    (install_root / "releases").mkdir(parents=True, exist_ok=True)
    request_path = install_root / "shared" / "updates" / "apply-request.json"
    request_path.parent.mkdir(parents=True)
    key = Ed25519PrivateKey.generate()
    public_key_path = _public_key_path(install_root)
    public_key_path.parent.mkdir(parents=True)
    public_key_path.write_bytes(
        key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )
    public_key_path.chmod(0o644)
    archive = _build_release_archive(new_release.name)
    archive_sha256 = hashlib.sha256(archive).hexdigest()
    manifest = json.dumps(
        {
            "product": "claw-trade",
            "channel": "stable",
            "version": "1.2.3",
            "arch": "linux-x86_64",
            "archive": f"{new_release.name}.tar.gz",
            "sha256": archive_sha256,
            "created_at": "2026-06-26T00:00:00Z",
            "min_current_version": "1.0.0",
        }
    ).encode()
    downloads = install_root / "shared" / "updates" / "downloads"
    downloads.mkdir(parents=True)
    (downloads / "manifest.json").write_bytes(manifest)
    (downloads / "manifest.json.sig").write_bytes(key.sign(manifest))
    (downloads / f"{new_release.name}.tar.gz").write_bytes(archive)
    (downloads / f"{new_release.name}.tar.gz.sig").write_bytes(key.sign(archive))
    request_path.write_text(
        json.dumps(
            {
                "version": "1.2.3",
                "archive": f"{new_release.name}.tar.gz",
                "archiveSha256": archive_sha256,
                "target": str(new_release),
                "previous": str(old_release),
            }
        ),
        encoding="utf-8",
    )
    return install_root, request_path, old_release, new_release


def _public_key_path(install_root: Path) -> Path:
    return install_root / "root-owned" / "update-signing-public.pem"


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
