from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shutil
import subprocess
import tarfile
import time
from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

import requests

from claw_trade.production import paths
from claw_trade.production.maintenance_lock import ProductionMaintenanceLock
from claw_trade.production.signature import verify_ed25519_signature
from claw_trade.production.update_manifest import UpdateManifest
from claw_trade.production.updater_state import UpdaterStateStore


FetchBytes = Callable[[str], bytes]
PreflightRunner = Callable[[Path], None]
ApplyUpdateStarter = Callable[[Path], None]
UPDATE_APPLY_ACTIVE_STATUSES = {"restart_scheduled", "restarting", "health_checking", "rollback_started"}
ARCHIVE_DOWNLOAD_ATTEMPTS = 5
ARCHIVE_DOWNLOAD_CHUNK_SIZE = 8 * 1024 * 1024


@dataclass(frozen=True)
class RemoteManifestCheck:
    status: str
    manifest: UpdateManifest | None
    user_message: str
    manifest_bytes: bytes | None = None
    manifest_signature: bytes | None = None

    def to_user_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "latestVersion": self.manifest.version if self.manifest else None,
            "archive": self.manifest.archive if self.manifest else None,
            "userMessage": self.user_message,
        }


@dataclass(frozen=True)
class RemoteUpdateInstall:
    status: str
    version: str | None
    user_message: str

    def to_user_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "version": self.version,
            "userMessage": self.user_message,
        }


class RemoteUpdateService:
    def __init__(
        self,
        *,
        install_root: Path = paths.INSTALL_ROOT,
        base_url: str | None,
        current_version: str,
        public_key_path: Path | None = None,
        state_store: UpdaterStateStore | None = None,
        fetch_bytes: FetchBytes | None = None,
        preflight_runner: PreflightRunner | None = None,
        apply_update_starter: ApplyUpdateStarter | None = None,
        maintenance_lock: ProductionMaintenanceLock | None = None,
    ) -> None:
        self._install_root = install_root
        self._base_url = base_url.rstrip("/") + "/" if base_url else None
        self._current_version = current_version
        self._public_key_path = public_key_path or paths.UPDATE_PUBLIC_KEY_PATH
        self._state_store = state_store or UpdaterStateStore(install_root=install_root)
        self._fetch_bytes = fetch_bytes or _requests_fetch_bytes
        self._use_streaming_archive_download = fetch_bytes is None
        self._preflight_runner = preflight_runner or _run_preflight
        self._apply_update_starter = apply_update_starter or _start_apply_update_service
        self._maintenance_lock = maintenance_lock or ProductionMaintenanceLock(install_root=install_root)

    def status_for_user(self) -> dict[str, object]:
        layout_error = _install_layout_error(self._install_root)
        if layout_error is not None:
            return {
                "configured": bool(self._base_url),
                "publicKeyInstalled": False,
                "status": "check_failed",
                "userMessage": layout_error,
                "latestVersion": None,
                "updatedAt": None,
            }
        state = self._state_store.read()
        return {
            "configured": bool(self._base_url),
            "publicKeyInstalled": self._public_key_path.exists(),
            "status": state.get("status", "idle"),
            "userMessage": state.get("userMessage", "尚未检查远程更新。"),
            "latestVersion": state.get("latestVersion"),
            "updatedAt": state.get("updatedAt"),
        }

    def check_manifest(self) -> RemoteManifestCheck:
        layout_error = _install_layout_error(self._install_root)
        if layout_error is not None:
            return RemoteManifestCheck(status="check_failed", manifest=None, user_message=layout_error)
        apply_state = self._read_apply_in_progress_state()
        if apply_state is not None:
            return apply_state
        if not self._base_url:
            state = self._state_store.write(status="not_configured", user_message="远程更新源未配置。")
            return RemoteManifestCheck(status=str(state["status"]), manifest=None, user_message=str(state["userMessage"]))
        public_key_error = _public_key_layout_error(self._install_root, self._public_key_path)
        if public_key_error is not None:
            return RemoteManifestCheck(status="check_failed", manifest=None, user_message=public_key_error)
        if not self._public_key_path.exists():
            state = self._state_store.write(status="check_failed", user_message="更新公钥不存在。")
            return RemoteManifestCheck(status=str(state["status"]), manifest=None, user_message=str(state["userMessage"]))

        self._state_store.write(status="checking_manifest", user_message="正在检查远程更新清单。")
        try:
            manifest_bytes = self._fetch_bytes(urljoin(self._base_url, "manifest.json"))
            signature = self._fetch_bytes(urljoin(self._base_url, "manifest.json.sig"))
            public_key = self._public_key_path.read_bytes()
            signature_ok = verify_ed25519_signature(public_key_pem=public_key, data=manifest_bytes, signature=signature)
        except (OSError, requests.RequestException, ValueError) as exc:
            state = self._state_store.write(status="check_failed", user_message=f"远程更新清单检查失败：{exc}")
            return RemoteManifestCheck(status=str(state["status"]), manifest=None, user_message=str(state["userMessage"]))
        if not signature_ok:
            state = self._state_store.write(status="verify_failed", user_message="远程更新清单签名校验失败。")
            return RemoteManifestCheck(status=str(state["status"]), manifest=None, user_message=str(state["userMessage"]))

        try:
            manifest = UpdateManifest.from_json(manifest_bytes)
            manifest.assert_installable(current_version=self._current_version)
        except ValueError as exc:
            if str(exc) == "manifest version 不高于当前版本。":
                state = self._state_store.write(status="up_to_date", user_message="当前已是最新版本。")
                return RemoteManifestCheck(status=str(state["status"]), manifest=None, user_message=str(state["userMessage"]))
            state = self._state_store.write(status="check_failed", user_message=str(exc))
            return RemoteManifestCheck(status=str(state["status"]), manifest=None, user_message=str(state["userMessage"]))

        state = self._state_store.write(
            status="update_available",
            user_message=f"发现可安装版本 {manifest.version}。",
            latestVersion=manifest.version,
            archive=manifest.archive,
            archiveSha256=manifest.sha256,
        )
        return RemoteManifestCheck(
            status=str(state["status"]),
            manifest=manifest,
            user_message=str(state["userMessage"]),
            manifest_bytes=manifest_bytes,
            manifest_signature=signature,
        )

    def install_checked_update(self) -> RemoteUpdateInstall:
        layout_error = _install_layout_error(self._install_root)
        if layout_error is not None:
            return RemoteUpdateInstall(status="install_failed", version=None, user_message=layout_error)
        public_key_error = _public_key_layout_error(self._install_root, self._public_key_path)
        if public_key_error is not None:
            return RemoteUpdateInstall(status="install_failed", version=None, user_message=public_key_error)
        if (self._install_root / "shared" / "updates" / "updater.lock").exists():
            state = self._state_store.write(status="install_failed", user_message="已有更新任务正在运行。")
            return RemoteUpdateInstall(status=str(state["status"]), version=None, user_message=str(state["userMessage"]))
        if (self._install_root / "shared" / "updates" / "apply.lock").exists():
            state = self._state_store.read()
            status = str(state.get("status", "restart_scheduled"))
            message = str(state.get("userMessage", "已有更新应用任务正在运行。"))
            return RemoteUpdateInstall(status=status, version=None, user_message=message)
        if self._maintenance_lock.is_locked():
            state = self._state_store.write(status="install_failed", user_message="系统正在维护中，请稍后再试。")
            return RemoteUpdateInstall(status=str(state["status"]), version=None, user_message=str(state["userMessage"]))
        try:
            lock = _updater_lock(self._install_root)
            lock.__enter__()
        except ValueError as exc:
            state = self._state_store.write(status="install_failed", user_message=str(exc))
            return RemoteUpdateInstall(status=str(state["status"]), version=None, user_message=str(state["userMessage"]))
        try:
            with self._maintenance_lock.hold(reason="install_update", request_id="install-update"):
                check = self.check_manifest()
                if check.manifest is None:
                    return RemoteUpdateInstall(status=check.status, version=None, user_message=check.user_message)
                manifest = check.manifest
                public_key = self._public_key_path.read_bytes()
                if check.manifest_bytes is None or check.manifest_signature is None:
                    state = self._state_store.write(status="install_failed", user_message="更新清单原始数据缺失。")
                    return RemoteUpdateInstall(status=str(state["status"]), version=manifest.version, user_message=str(state["userMessage"]))
                self._state_store.write(
                    status="downloading",
                    user_message=f"正在下载版本 {manifest.version}。",
                    latestVersion=manifest.version,
                    archive=manifest.archive,
                )
                self._write_download("manifest.json", check.manifest_bytes)
                self._write_download("manifest.json.sig", check.manifest_signature)
                archive_path = self._download_archive(manifest.archive)
                archive_sig = self._fetch_bytes(urljoin(self._base_url or "", f"{manifest.archive}.sig"))
                if _sha256_file(archive_path) != manifest.sha256:
                    archive_path.unlink(missing_ok=True)
                    state = self._state_store.write(status="verify_failed", user_message="更新包 sha256 校验失败。")
                    return RemoteUpdateInstall(status=str(state["status"]), version=manifest.version, user_message=str(state["userMessage"]))
                if not verify_ed25519_signature(public_key_pem=public_key, data=archive_path.read_bytes(), signature=archive_sig):
                    archive_path.unlink(missing_ok=True)
                    state = self._state_store.write(status="verify_failed", user_message="更新包签名校验失败。")
                    return RemoteUpdateInstall(status=str(state["status"]), version=manifest.version, user_message=str(state["userMessage"]))

                self._write_download(f"{manifest.archive}.sig", archive_sig)
                release_name = _release_name_from_archive(manifest.archive)
                final_dir = self._install_root / "releases" / release_name
                if final_dir.exists() or final_dir.is_symlink():
                    state = self._state_store.write(status="install_failed", user_message="目标版本目录已存在，拒绝覆盖。")
                    return RemoteUpdateInstall(status=str(state["status"]), version=manifest.version, user_message=str(state["userMessage"]))
                previous_current = _read_current_target(self._install_root)
                apply_lock = _create_apply_pending_lock(self._install_root)
                apply_request = self._write_apply_request(
                    version=manifest.version,
                    archive=manifest.archive,
                    archive_sha256=manifest.sha256,
                    target=final_dir,
                    previous=Path(previous_current) if previous_current is not None else None,
                )
                state = self._state_store.write(
                    status="restart_scheduled",
                    user_message=f"已安装版本 {manifest.version}；正在重启服务并检查健康状态。",
                    latestVersion=manifest.version,
                    archive=manifest.archive,
                )
                try:
                    self._apply_update_starter(apply_request)
                except (OSError, subprocess.CalledProcessError, ValueError) as exc:
                    apply_request.unlink(missing_ok=True)
                    apply_lock.unlink(missing_ok=True)
                    state = self._state_store.write(
                        status="install_failed",
                        user_message=f"更新应用服务启动失败，当前版本未切换：{exc}",
                    )
                    return RemoteUpdateInstall(status=str(state["status"]), version=manifest.version, user_message=str(state["userMessage"]))
                return RemoteUpdateInstall(status=str(state["status"]), version=manifest.version, user_message=str(state["userMessage"]))
        except (OSError, tarfile.TarError, ValueError, subprocess.CalledProcessError, requests.RequestException) as exc:
            state = self._state_store.write(status="install_failed", user_message=f"更新安装失败：{exc}")
            return RemoteUpdateInstall(status=str(state["status"]), version=None, user_message=str(state["userMessage"]))
        finally:
            lock.__exit__(None, None, None)

    def _download_archive(self, archive_name: str) -> Path:
        if archive_name != Path(archive_name).name:
            raise ValueError("更新包文件名非法。")
        downloads = self._install_root / "shared" / "updates" / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        target = downloads / archive_name
        if self._use_streaming_archive_download:
            _requests_download_file(urljoin(self._base_url or "", archive_name), target)
            return target
        return self._write_download(archive_name, self._fetch_bytes(urljoin(self._base_url or "", archive_name)))

    def _read_apply_in_progress_state(self) -> RemoteManifestCheck | None:
        if not (self._install_root / "shared" / "updates" / "apply.lock").exists():
            return None
        state = self._state_store.read()
        status = str(state.get("status", "restart_scheduled"))
        if status not in UPDATE_APPLY_ACTIVE_STATUSES:
            status = "restart_scheduled"
        message = str(state.get("userMessage", "更新应用正在运行，请稍后查看结果。"))
        return RemoteManifestCheck(status=status, manifest=None, user_message=message)

    def _write_download(self, archive_name: str, archive_bytes: bytes) -> Path:
        if archive_name != Path(archive_name).name:
            raise ValueError("更新包文件名非法。")
        downloads = self._install_root / "shared" / "updates" / "downloads"
        downloads.mkdir(parents=True, exist_ok=True)
        target = downloads / archive_name
        temp = downloads / f".{archive_name}.{os.getpid()}.tmp"
        temp.write_bytes(archive_bytes)
        temp.replace(target)
        return target

    def _write_apply_request(
        self, *, version: str, archive: str, archive_sha256: str, target: Path, previous: Path | None
    ) -> Path:
        request_path = self._install_root / "shared" / "updates" / "apply-request.json"
        request_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": version,
            "archive": archive,
            "archiveSha256": archive_sha256,
            "target": str(target),
            "previous": str(previous) if previous is not None else None,
        }
        temp = request_path.with_name(f".{request_path.name}.{os.getpid()}.tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temp.replace(request_path)
        return request_path


def _install_layout_error(install_root: Path) -> str | None:
    if not install_root.is_absolute():
        return "远程更新安装根目录必须是绝对路径。"
    if install_root.name != "claw-trade":
        return "远程更新安装根目录必须指向 claw-trade。"
    if install_root.is_symlink():
        return "远程更新安装根目录不能是符号链接。"
    root = install_root.resolve(strict=False)
    critical_dirs = {
        "shared": install_root / "shared",
        "updates": install_root / "shared" / "updates",
        "downloads": install_root / "shared" / "updates" / "downloads",
        "releases": install_root / "releases",
    }
    for label, path in critical_dirs.items():
        if path.is_symlink():
            return f"远程更新安装目录不安全：{label} 不能是符号链接。"
        if path.exists() and not path.resolve(strict=False).is_relative_to(root):
            return f"远程更新安装目录不安全：{label} 超出安装根目录。"
    return None


def _public_key_layout_error(install_root: Path, public_key_path: Path) -> str | None:
    root = install_root.resolve(strict=False)
    if not public_key_path.is_absolute():
        return "更新公钥路径必须是绝对路径。"
    if public_key_path.is_symlink():
        return "更新公钥不能是符号链接。"
    resolved = public_key_path.resolve(strict=False)
    if not (resolved.is_relative_to(root) or resolved == paths.UPDATE_PUBLIC_KEY_PATH):
        return "更新公钥必须位于安装根目录内或 /etc/claw-trade。"
    return None


def _requests_fetch_bytes(url: str) -> bytes:
    response = requests.get(url, timeout=20)
    response.raise_for_status()
    return response.content


def _requests_download_file(
    url: str,
    target: Path,
    *,
    max_attempts: int = ARCHIVE_DOWNLOAD_ATTEMPTS,
    chunk_size: int = ARCHIVE_DOWNLOAD_CHUNK_SIZE,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(f".{target.name}.part")
    last_error: BaseException | None = None
    for attempt in range(1, max_attempts + 1):
        existing_size = partial.stat().st_size if partial.exists() else 0
        headers = {"Range": f"bytes={existing_size}-"} if existing_size else None
        try:
            with requests.get(url, timeout=(10, 60), stream=True, headers=headers) as response:
                if existing_size and response.status_code == 200:
                    partial.unlink(missing_ok=True)
                    existing_size = 0
                    mode = "wb"
                    expected_total = _content_length(response)
                else:
                    response.raise_for_status()
                    mode = "ab" if existing_size else "wb"
                    expected_total = _expected_download_size(response, existing_size=existing_size)
                with partial.open(mode + "") as handle:
                    for chunk in response.iter_content(chunk_size=chunk_size):
                        if chunk:
                            handle.write(chunk)
                downloaded_size = partial.stat().st_size
                if expected_total is not None and downloaded_size < expected_total:
                    raise requests.ConnectionError(
                        f"download incomplete: {downloaded_size} bytes read, {expected_total - downloaded_size} more expected"
                    )
                partial.replace(target)
                return
        except (OSError, requests.RequestException) as exc:
            last_error = exc
            if attempt < max_attempts:
                time.sleep(min(2 ** (attempt - 1), 8))
                continue
            raise
    if last_error is not None:
        raise last_error


def _content_length(response: requests.Response) -> int | None:
    raw = response.headers.get("Content-Length")
    if raw is None:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _expected_download_size(response: requests.Response, *, existing_size: int) -> int | None:
    if response.status_code == 206:
        content_range = response.headers.get("Content-Range", "")
        match = re.fullmatch(r"bytes\s+(\d+)-(\d+)/(\d+)", content_range)
        if match is None:
            return None
        start = int(match.group(1))
        if start != existing_size:
            raise requests.ConnectionError("remote server returned an unexpected resume offset")
        return int(match.group(3))
    length = _content_length(response)
    return existing_size + length if length is not None else None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(ARCHIVE_DOWNLOAD_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _release_name_from_archive(archive_name: str) -> str:
    if not re.fullmatch(paths.FORMAL_PACKAGE_ARCHIVE_REGEX, archive_name):
        raise ValueError("更新包名称不是正式生产包。")
    return archive_name[: -len(".tar.gz")]


def _normalize_member_name(raw_name: str) -> str:
    if "\x00" in raw_name or "\\" in raw_name or raw_name.startswith("/"):
        raise ValueError(f"更新包路径非法: {raw_name!r}")
    stripped = raw_name.strip("/")
    normalized = posixpath.normpath(stripped)
    if normalized in {"", "."} or normalized != stripped or "/../" in f"/{normalized}/" or normalized.startswith("../"):
        raise ValueError(f"更新包路径越界: {raw_name!r}")
    return normalized


def _safe_extract_release_archive(archive_path: Path, staging_dir: Path, *, expected_top_dir: str) -> None:
    staging_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir.mkdir(parents=True, exist_ok=True)
    seen = False
    with tarfile.open(archive_path, "r:gz") as archive:
        for member in archive:
            normalized = _normalize_member_name(member.name)
            parts = normalized.split("/", 1)
            if parts[0] != expected_top_dir:
                raise ValueError("更新包顶层目录和包名不一致。")
            if not (member.isdir() or member.isreg()):
                raise ValueError("更新包包含不支持的文件类型。")
            if member.mode & 0o6000:
                raise ValueError("更新包包含 setuid/setgid 权限。")
            relative = parts[1] if len(parts) == 2 else ""
            target = staging_dir / relative
            if not target.resolve(strict=False).is_relative_to(staging_dir.resolve(strict=False)):
                raise ValueError("更新包解压路径越界。")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
            elif relative:
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("更新包文件无法读取。")
                target.parent.mkdir(parents=True, exist_ok=True)
                with target.open("wb") as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o777)
            seen = True
    if not seen:
        raise ValueError("更新包为空。")


def _read_current_target(install_root: Path) -> str | None:
    current = install_root / "current"
    if current.is_symlink():
        return str(current.resolve(strict=False))
    return None


def _run_preflight(release_dir: Path) -> None:
    subprocess.run([str(release_dir / "bin" / "claw-trade-preflight")], check=True)


def _start_apply_update_service(_request_path: Path) -> None:
    subprocess.run(
        ["sudo", "-n", "/bin/systemctl", "start", "--no-block", paths.UPDATE_APPLY_SERVICE_NAME],
        check=True,
    )


def _create_apply_pending_lock(install_root: Path) -> Path:
    lock_path = install_root / "shared" / "updates" / "apply.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("已有更新应用任务正在运行。") from exc
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(f"pending:{os.getpid()}")
    return lock_path


@contextmanager
def _updater_lock(install_root: Path):
    lock_path = install_root / "shared" / "updates" / "updater.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise ValueError("已有更新任务正在运行。") from exc
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(str(os.getpid()))
        yield
    finally:
        lock_path.unlink(missing_ok=True)
