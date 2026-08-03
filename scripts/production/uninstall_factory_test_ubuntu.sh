#!/usr/bin/env bash
set -euo pipefail

install_root="/opt/claw-trade"
purge_shared=0
ui_pid_file="${CLAW_TRADE_UI_PID_FILE:-/tmp/claw-trade-ui.pid}"

if [[ "${1:-}" == "--purge-shared" ]]; then
  purge_shared=1
fi

log() {
  printf '[INFO] %s\n' "$*"
}

fail() {
  printf '[ERROR] %s\n' "$*" >&2
  exit 1
}

if [[ -n "${CLAW_TRADE_INSTALL_ROOT:-}" && "${CLAW_TRADE_INSTALL_ROOT}" != "${install_root}" ]]; then
  fail "CLAW_TRADE_INSTALL_ROOT must be exactly ${install_root}"
fi

stop_ui() {
  if [[ -f "${ui_pid_file}" ]]; then
    pid="$(cat "${ui_pid_file}" 2>/dev/null || true)"
    if [[ "${pid}" =~ ^[0-9]+$ ]] && ps -p "${pid}" >/dev/null 2>&1; then
      kill "${pid}" 2>/dev/null || true
      sleep 2
    fi
    sudo rm -f "${ui_pid_file}"
  fi
  pkill -f 'python3.12 -m claw_trade.web.app' 2>/dev/null || true
}

log "stopping claw-trade UI"
stop_ui
if ! watchdog_timer_load_state="$(sudo systemctl show claw-trade-watchdog.timer --property=LoadState --value)"; then
  fail "failed to inspect claw-trade-watchdog.timer LoadState"
fi
case "${watchdog_timer_load_state}" in
  not-found) ;;
  loaded)
  sudo systemctl disable --now claw-trade-watchdog.timer >/dev/null 2>&1 \
    || fail "failed to stop claw-trade-watchdog.timer"
  watchdog_timer_active_state="$(sudo systemctl show claw-trade-watchdog.timer --property=ActiveState --value 2>/dev/null)" \
    || fail "failed to confirm claw-trade-watchdog.timer state"
  [[ "${watchdog_timer_active_state}" == "inactive" ]] \
    || fail "claw-trade-watchdog.timer is not inactive: ${watchdog_timer_active_state:-unknown}"
    ;;
  *) fail "unexpected claw-trade-watchdog.timer LoadState: ${watchdog_timer_load_state:-unknown}" ;;
esac
if ! watchdog_load_state="$(sudo systemctl show claw-trade-watchdog.service --property=LoadState --value)"; then
  fail "failed to inspect claw-trade-watchdog.service LoadState"
fi
case "${watchdog_load_state}" in
  not-found) ;;
  loaded)
  sudo systemctl stop claw-trade-watchdog.service >/dev/null 2>&1 \
    || fail "failed to stop claw-trade-watchdog.service"
  watchdog_active_state="$(sudo systemctl show claw-trade-watchdog.service --property=ActiveState --value 2>/dev/null)" \
    || fail "failed to confirm claw-trade-watchdog.service state"
  [[ "${watchdog_active_state}" == "inactive" ]] \
    || fail "claw-trade-watchdog.service is not inactive: ${watchdog_active_state:-unknown}"
    ;;
  *) fail "unexpected claw-trade-watchdog.service LoadState: ${watchdog_load_state:-unknown}" ;;
esac

sudo python3 - "${install_root}" "${purge_shared}" "${ui_pid_file}" <<'PY'
import errno
import fcntl
import grp
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

root = Path(sys.argv[1])
purge_shared = sys.argv[2] == "1"
ui_pid_file = Path(sys.argv[3])
group_gid = grp.getgrnam("clawtrade").gr_gid
parent_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
lock_fds = []
try:
    parent = os.fstat(parent_fd)
    if parent.st_uid != 0 or parent.st_mode & 0o022:
        raise SystemExit(f"unsafe lock parent ownership or mode: {root}")
    for name in ("host-operations.lock", "report-active.lock", "data-work-active.lock"):
        try:
            fd = os.open(name, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW, dir_fd=parent_fd)
        except OSError as exc:
            if name == "data-work-active.lock" and exc.errno == errno.ENOENT:
                fd = os.open(
                    name,
                    os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NONBLOCK | os.O_CLOEXEC | os.O_NOFOLLOW,
                    0o660,
                    dir_fd=parent_fd,
                )
                os.fchown(fd, 0, group_gid)
                os.fchmod(fd, 0o660)
            else:
                raise SystemExit(f"cannot open uninstall lock {root / name}: {exc}")
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_gid != group_gid or stat.S_IMODE(info.st_mode) != 0o660:
            os.close(fd)
            raise SystemExit(f"unsafe uninstall lock inode: {root / name}")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            if exc.errno in {errno.EACCES, errno.EAGAIN}:
                raise SystemExit(f"uninstall blocked by active lock: {root / name}")
            raise
        lock_fds.append(fd)

    for link in (root / "current", root / "rescue-current"):
        if link.exists() or link.is_symlink():
            link.unlink()
    if (root / "releases").is_dir():
        shutil.rmtree(root / "releases")
    if purge_shared and (root / "shared").is_dir():
        shutil.rmtree(root / "shared")
    for path in (
        Path("/tmp/claw-trade-ui.log"),
        ui_pid_file,
        Path("/etc/sudoers.d/claw-trade-update"),
        Path("/usr/local/lib/claw-trade/claw-trade-apply-update"),
        Path("/usr/local/lib/claw-trade/claw-trade-watchdog"),
        Path("/etc/systemd/system/claw-trade-apply-update.service"),
        Path("/etc/systemd/system/claw-trade-watchdog.service"),
        Path("/etc/systemd/system/claw-trade-watchdog.timer"),
    ):
        path.unlink(missing_ok=True)
    retry_dir = Path("/run/claw-trade-watchdog")
    if retry_dir.is_dir() and not retry_dir.is_symlink():
        shutil.rmtree(retry_dir)
    for name in ("host-operations.lock", "report-active.lock", "data-work-active.lock"):
        os.unlink(name, dir_fd=parent_fd)
    subprocess.run(["/bin/systemctl", "daemon-reload"], check=True)
finally:
    for fd in reversed(lock_fds):
        os.close(fd)
    os.close(parent_fd)
PY

cat <<EOF
[OK] claw-trade uninstalled
Removed:
  ${install_root}/current
  ${install_root}/rescue-current
  ${install_root}/releases
  /usr/local/lib/claw-trade/claw-trade-apply-update
  /usr/local/lib/claw-trade/claw-trade-watchdog
Shared data removed: ${purge_shared}
EOF
