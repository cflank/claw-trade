from __future__ import annotations

import fcntl
import importlib.machinery
import importlib.util
import json
import os
import subprocess
from pathlib import Path
from types import ModuleType

import pytest


ROOT = Path(__file__).resolve().parents[3]
HELPER = ROOT / "packaging" / "production" / "root-helper" / "claw-trade-watchdog"


@pytest.fixture
def module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    loader = importlib.machinery.SourceFileLoader("claw_trade_watchdog", str(HELPER))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    assert spec is not None
    loaded = importlib.util.module_from_spec(spec)
    loader.exec_module(loaded)

    install = tmp_path / "opt" / "claw-trade"
    runtime = tmp_path / "run" / "claw-trade-watchdog"
    install.mkdir(parents=True, mode=0o755)
    runtime.mkdir(parents=True, mode=0o700)
    os.chmod(install, 0o755)
    os.chmod(runtime, 0o700)
    for name in ("host-operations.lock", "report-active.lock"):
        path = install / name
        path.touch()
        os.chmod(path, 0o660)

    loaded.HOST_OPERATION_LOCK_PATH = install / "host-operations.lock"
    loaded.REPORT_ACTIVE_LOCK_PATH = install / "report-active.lock"
    loaded.RETRY_STATE_PATH = runtime / "retry.json"
    loaded.ROOT_UID = os.geteuid()
    monkeypatch.setattr(loaded, "_lock_identity", lambda: (os.geteuid(), os.getegid()))
    return loaded


def _ready(module: ModuleType, monkeypatch: pytest.MonkeyPatch, *, now: float = 1_000.0, pid: int = 101) -> list[float]:
    clock = [now]
    monkeypatch.setattr(module, "control_identity", lambda: (pid, 0.0))
    monkeypatch.setattr(module, "suppressed", lambda: None)
    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    return clock


def _state(module: ModuleType) -> dict[str, object] | None:
    path = module.RETRY_STATE_PATH
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _unhealthy_run(module: ModuleType, monkeypatch: pytest.MonkeyPatch, actions: list[tuple[str, str]]) -> int:
    monkeypatch.setattr(module, "endpoints_healthy", lambda: False)
    monkeypatch.setattr(module, "run_systemctl", lambda action, service: actions.append((action, service)) or True)
    return module.run()


def test_composite_health_requires_both_fixed_json_contracts(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str] = []

    def fetch(url: str) -> dict[str, object]:
        seen.append(url)
        if url == module.OPENVIKING_HEALTH_URL:
            return {"healthy": True, "status": "ok"}
        return {"ok": True, "status": "live"}

    monkeypatch.setattr(module, "fetch_health_json", fetch)
    assert module.endpoints_healthy() is True
    assert seen == ["http://127.0.0.1:1933/health", "http://127.0.0.1:18789/health"]


@pytest.mark.parametrize(
    ("openviking", "openclaw"),
    [
        ({"healthy": False, "status": "ok"}, {"ok": True, "status": "live"}),
        ({"healthy": True, "status": "starting"}, {"ok": True, "status": "live"}),
        ({"healthy": True, "status": "ok"}, {"ok": False, "status": "live"}),
        ({"healthy": True, "status": "ok"}, {"ok": True, "status": "starting"}),
    ],
)
def test_http_200_is_not_enough(
    module: ModuleType,
    monkeypatch: pytest.MonkeyPatch,
    openviking: dict[str, object],
    openclaw: dict[str, object],
) -> None:
    monkeypatch.setattr(
        module,
        "fetch_health_json",
        lambda url: openviking if url == module.OPENVIKING_HEALTH_URL else openclaw,
    )
    assert module.endpoints_healthy() is False


def test_healthy_probe_precedes_grace_and_clears_retry_state(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    module.RETRY_STATE_PATH.write_text("not json", encoding="utf-8")
    os.chmod(module.RETRY_STATE_PATH, 0o600)
    monkeypatch.setattr(module, "control_identity", lambda: (101, 900.0))
    monkeypatch.setattr(module, "suppressed", lambda: None)
    monkeypatch.setattr(module.time, "monotonic", lambda: 1_000.0)
    monkeypatch.setattr(module, "endpoints_healthy", lambda: True)
    monkeypatch.setattr(module, "run_systemctl", lambda *_args: pytest.fail("healthy service must not restart"))

    assert module.run() == 0
    assert not module.RETRY_STATE_PATH.exists()


def test_unhealthy_startup_grace_probes_once_then_stops(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    probes = 0
    monkeypatch.setattr(module, "control_identity", lambda: (101, 900.0))
    monkeypatch.setattr(module, "suppressed", lambda: None)
    monkeypatch.setattr(module.time, "monotonic", lambda: 1_000.0)

    def unhealthy() -> bool:
        nonlocal probes
        probes += 1
        return False

    monkeypatch.setattr(module, "endpoints_healthy", unhealthy)
    monkeypatch.setattr(module, "run_systemctl", lambda *_args: pytest.fail("grace must suppress restart"))
    assert module.run() == 0
    assert probes == 1


def test_three_attempt_policy_and_exact_deadlines(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    clock = _ready(module, monkeypatch)
    actions: list[tuple[str, str]] = []

    assert _unhealthy_run(module, monkeypatch, actions) == 0
    first = _state(module)
    assert first == {"attempts": 1, "next_allowed": clock[0] + 300, "exhausted": False}

    clock[0] = float(first["next_allowed"])
    assert _unhealthy_run(module, monkeypatch, actions) == 0
    second = _state(module)
    assert second == {"attempts": 2, "next_allowed": clock[0] + 900, "exhausted": False}

    clock[0] = float(second["next_allowed"])
    assert _unhealthy_run(module, monkeypatch, actions) == 0
    assert _state(module) == {"attempts": 3, "next_allowed": None, "exhausted": True}
    assert actions == [("restart", module.CONTROL_SERVICE)] * 3


def test_cooldown_and_exhausted_only_probe_health(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(module, monkeypatch, now=1_000.0)
    probes = 0
    actions: list[tuple[str, str]] = []
    monkeypatch.setattr(module, "run_systemctl", lambda action, service: actions.append((action, service)) or True)

    def unhealthy() -> bool:
        nonlocal probes
        probes += 1
        return False

    monkeypatch.setattr(module, "endpoints_healthy", unhealthy)
    module.write_retry_state({"attempts": 1, "next_allowed": 1_001.0, "exhausted": False})
    assert module.run() == 0
    module.write_retry_state({"attempts": 3, "next_allowed": None, "exhausted": True})
    assert module.run() == 0
    assert probes == 2
    assert actions == []


def test_cooldown_boundary_is_inclusive(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(module, monkeypatch, now=1_000.0)
    module.write_retry_state({"attempts": 1, "next_allowed": 1_000.0, "exhausted": False})
    actions: list[tuple[str, str]] = []
    assert _unhealthy_run(module, monkeypatch, actions) == 0
    assert actions == [("restart", module.CONTROL_SERVICE)]


def test_healthy_service_clears_exhausted_state(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(module, monkeypatch)
    module.write_retry_state({"attempts": 3, "next_allowed": None, "exhausted": True})
    monkeypatch.setattr(module, "endpoints_healthy", lambda: True)
    assert module.run() == 0
    assert _state(module) is None


@pytest.mark.parametrize(
    "payload",
    [
        "not json",
        json.dumps({"attempts": 0, "next_allowed": 0, "exhausted": False}),
        json.dumps({"attempts": 1, "next_allowed": 0, "exhausted": False, "extra": 1}),
        json.dumps({"attempts": 3, "next_allowed": 0, "exhausted": True}),
        '{"attempts":1,"next_allowed":NaN,"exhausted":false}',
    ],
)
def test_malformed_retry_state_fails_closed(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    _ready(module, monkeypatch)
    module.RETRY_STATE_PATH.write_text(payload, encoding="utf-8")
    os.chmod(module.RETRY_STATE_PATH, 0o600)
    monkeypatch.setattr(module, "endpoints_healthy", lambda: False)
    monkeypatch.setattr(module, "run_systemctl", lambda *_args: pytest.fail("invalid state must not restart"))
    assert module.main() == 1


@pytest.mark.parametrize("unsafe", ["mode", "symlink", "fifo"])
def test_unsafe_retry_inode_fails_closed(
    module: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe: str
) -> None:
    _ready(module, monkeypatch)
    if unsafe == "mode":
        module.RETRY_STATE_PATH.write_text("{}", encoding="utf-8")
        os.chmod(module.RETRY_STATE_PATH, 0o644)
    elif unsafe == "symlink":
        target = tmp_path / "target"
        target.write_text("{}", encoding="utf-8")
        module.RETRY_STATE_PATH.symlink_to(target)
    else:
        os.mkfifo(module.RETRY_STATE_PATH, 0o600)
    monkeypatch.setattr(module, "endpoints_healthy", lambda: False)
    monkeypatch.setattr(module, "run_systemctl", lambda *_args: pytest.fail("unsafe state must not restart"))
    assert module.main() == 1


def test_atomic_state_write_fsyncs_file_and_directory_before_restart(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(module, monkeypatch)
    events: list[str] = []
    real_fsync = module.os.fsync
    real_replace = module.os.replace

    def fsync(fd: int) -> None:
        events.append("dir-fsync" if os.path.isdir(f"/proc/self/fd/{fd}") else "file-fsync")
        real_fsync(fd)

    def replace(*args, **kwargs) -> None:
        events.append("replace")
        real_replace(*args, **kwargs)

    monkeypatch.setattr(module.os, "fsync", fsync)
    monkeypatch.setattr(module.os, "replace", replace)
    monkeypatch.setattr(module, "endpoints_healthy", lambda: False)
    monkeypatch.setattr(module, "run_systemctl", lambda *_args: events.append("restart") or True)
    assert module.run() == 0
    assert events == ["file-fsync", "replace", "dir-fsync", "restart"]


def test_state_write_failure_is_nonzero_and_never_restarts(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(module, monkeypatch)
    monkeypatch.setattr(module, "endpoints_healthy", lambda: False)
    monkeypatch.setattr(module.os, "replace", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk")))
    monkeypatch.setattr(module, "run_systemctl", lambda *_args: pytest.fail("failed state write must not restart"))
    assert module.main() == 1


@pytest.mark.parametrize("lock_name", ["HOST_OPERATION_LOCK_PATH", "REPORT_ACTIVE_LOCK_PATH"])
def test_lock_contention_suppresses_restart(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, lock_name: str
) -> None:
    _ready(module, monkeypatch)
    monkeypatch.setattr(module, "endpoints_healthy", lambda: False)
    monkeypatch.setattr(module, "run_systemctl", lambda *_args: pytest.fail("contended lock must suppress restart"))
    with Path(getattr(module, lock_name)).open("r") as stream:
        fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert module.run() == 0
    assert _state(module) is None


def test_lock_order_is_host_then_report(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(module, monkeypatch)
    order: list[Path] = []
    real_lock = module.exclusive_lock

    def lock(path: Path):
        order.append(path)
        return real_lock(path)

    monkeypatch.setattr(module, "exclusive_lock", lock)
    monkeypatch.setattr(module, "endpoints_healthy", lambda: False)
    monkeypatch.setattr(module, "run_systemctl", lambda *_args: True)
    assert module.run() == 0
    assert order == [module.HOST_OPERATION_LOCK_PATH, module.REPORT_ACTIVE_LOCK_PATH]


@pytest.mark.parametrize("lock_name", ["HOST_OPERATION_LOCK_PATH", "REPORT_ACTIVE_LOCK_PATH"])
def test_lock_contract_rejects_wrong_mode(module: ModuleType, monkeypatch: pytest.MonkeyPatch, lock_name: str) -> None:
    _ready(module, monkeypatch)
    os.chmod(getattr(module, lock_name), 0o600)
    monkeypatch.setattr(module, "endpoints_healthy", lambda: False)
    monkeypatch.setattr(module, "run_systemctl", lambda *_args: pytest.fail("unsafe lock must not restart"))
    assert module.main() == 1


def test_pid_change_during_three_probes_suppresses_restart(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    identities = iter([(101, 0.0)] * 4 + [(202, 0.0)])
    monkeypatch.setattr(module, "control_identity", lambda: next(identities))
    monkeypatch.setattr(module, "suppressed", lambda: None)
    monkeypatch.setattr(module.time, "monotonic", lambda: 1_000.0)
    monkeypatch.setattr(module.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(module, "endpoints_healthy", lambda: False)
    monkeypatch.setattr(module, "run_systemctl", lambda *_args: pytest.fail("new PID must not restart"))
    assert module.run() == 0


@pytest.mark.parametrize("reason", ["rescue is active", "update is active", "maintenance lock exists"])
def test_suppression_appearing_during_probes_stops_restart(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    _ready(module, monkeypatch)
    probes = 0

    def suppression() -> str | None:
        return reason if probes == 2 else None

    def unhealthy() -> bool:
        nonlocal probes
        probes += 1
        return False

    monkeypatch.setattr(module, "suppressed", suppression)
    monkeypatch.setattr(module, "endpoints_healthy", unhealthy)
    monkeypatch.setattr(module, "run_systemctl", lambda *_args: pytest.fail("suppression must stop restart"))
    assert module.run() == 0
    assert probes == 2


def test_final_locked_health_recheck_prevents_restart(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    _ready(module, monkeypatch)
    health = iter([False, False, False, True])
    monkeypatch.setattr(module, "endpoints_healthy", lambda: next(health))
    monkeypatch.setattr(module, "run_systemctl", lambda *_args: pytest.fail("recovered service must not restart"))
    assert module.run() == 0
    assert _state(module) is None


def test_state_is_written_while_both_locks_are_held_before_restart(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    _ready(module, monkeypatch)
    held: list[Path] = []
    events: list[str] = []
    real_lock = module.exclusive_lock
    real_write = module.write_retry_state

    class TrackingLock:
        def __init__(self, path: Path):
            self.path = path
            self.context = real_lock(path)

        def __enter__(self):
            locked = self.context.__enter__()
            if locked:
                held.append(self.path)
            return locked

        def __exit__(self, *args):
            if self.path in held:
                held.remove(self.path)
            return self.context.__exit__(*args)

    def write(state: dict[str, object]) -> None:
        assert held == [module.HOST_OPERATION_LOCK_PATH, module.REPORT_ACTIVE_LOCK_PATH]
        events.append("state")
        real_write(state)

    def restart(*_args) -> bool:
        assert held == [module.HOST_OPERATION_LOCK_PATH, module.REPORT_ACTIVE_LOCK_PATH]
        events.append("restart")
        return True

    monkeypatch.setattr(module, "exclusive_lock", TrackingLock)
    monkeypatch.setattr(module, "write_retry_state", write)
    monkeypatch.setattr(module, "endpoints_healthy", lambda: False)
    monkeypatch.setattr(module, "run_systemctl", restart)
    assert module.run() == 0
    assert events == ["state", "restart"]


def test_helper_has_no_frontend_recovery_or_start_actions() -> None:
    text = HELPER.read_text(encoding="utf-8")
    assert "claw-trade-ui" not in text
    assert "claw-trade-kiosk" not in text
    assert "UI_HEALTH" not in text
    assert 'run_systemctl("start"' not in text
    assert "RECOVERY_ATTEMPTS" not in text


def test_fixed_paths_and_no_runtime_env() -> None:
    text = HELPER.read_text(encoding="utf-8")
    assert 'Path("/opt/claw-trade/host-operations.lock")' in text
    assert 'Path("/opt/claw-trade/report-active.lock")' in text
    assert 'Path("/run/claw-trade-watchdog/retry.json")' in text
    assert "runtime.env" not in text


def test_systemctl_calls_are_bounded(module: ModuleType, monkeypatch: pytest.MonkeyPatch) -> None:
    timeouts: list[int] = []

    def completed(args, **kwargs):
        timeouts.append(kwargs["timeout"])
        stdout = "1\n" if "show" in args else ""
        return subprocess.CompletedProcess(args, 0, stdout=stdout)

    monkeypatch.setattr(module.subprocess, "run", completed)
    module.systemctl_value(module.CONTROL_SERVICE, "MainPID")
    assert module.service_active(module.CONTROL_SERVICE) is True
    assert module.run_systemctl("restart", module.CONTROL_SERVICE) is True
    assert timeouts == [
        module.SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
        module.SYSTEMCTL_QUERY_TIMEOUT_SECONDS,
        module.SYSTEMCTL_ACTION_TIMEOUT_SECONDS,
    ]


def test_worst_case_execution_budget_fits_ten_minutes_with_margin(module: ModuleType) -> None:
    # Initial identity: 3 queries. Nine eligibility checks: suppression (3) + identity (3).
    systemctl_queries = 3 + 9 * 6
    http_requests = (module.PROBE_ATTEMPTS + 1) * 2
    worst_case = (
        systemctl_queries * module.SYSTEMCTL_QUERY_TIMEOUT_SECONDS
        + http_requests * module.HTTP_TIMEOUT_SECONDS
        + (module.PROBE_ATTEMPTS - 1) * module.PROBE_INTERVAL_SECONDS
        + module.SYSTEMCTL_ACTION_TIMEOUT_SECONDS
    )
    assert worst_case == 439
    assert worst_case + 60 < 10 * 60


def test_local_health_opener_disables_proxies(module: ModuleType) -> None:
    proxy_handlers = [handler for handler in module.LOCAL_OPENER.handlers if isinstance(handler, module.ProxyHandler)]
    assert proxy_handlers == []
