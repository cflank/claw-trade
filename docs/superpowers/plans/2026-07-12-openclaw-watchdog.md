# OpenClaw Watchdog Safety Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the production watchdog restart OpenClaw at most three times with approved 5/15-minute spacing, while never interrupting an active report or competing with maintenance, update, rescue, UI, or kiosk ownership.

**Architecture:** Two fixed advisory lock files under root-owned `/opt/claw-trade` coordinate host operations and active reports. The root watchdog becomes a short control-only probe/restart state machine with a root-only retry file preserved across timer invocations; it never restores frontend services. Existing claw-trade workflow, report artifacts, systemd rescue, and OpenClaw runtime ownership remain unchanged.

**Tech Stack:** Python stdlib `fcntl/json/os`, systemd oneshot/timer, existing production shell packaging, pytest.

---

### Task 1: Shared Host Lock Contract

**Files:**
- Create: `src/claw_trade/production/host_locks.py`
- Modify: `src/claw_trade/production/paths.py`
- Modify: `src/claw_trade/production/maintenance_lock.py`
- Test: `tests/unit/production/test_factory_reset.py`
- Test: `tests/unit/production/test_remote_update.py`
- Create: `tests/unit/production/test_host_locks.py`

- [ ] Add canonical paths for `/opt/claw-trade/host-operations.lock` and `/opt/claw-trade/report-active.lock`.
- [ ] Implement one stdlib helper that opens an existing regular non-symlink lock file, validates root owner, `clawtrade` group and `0660`, then takes shared/exclusive blocking or non-blocking `flock` as requested. Do not create or repair lock files at runtime.
- [ ] Make `ProductionMaintenanceLock.hold()` take the exclusive non-blocking host lock before creating `maintenance.lock`, and release it after the semantic lock is removed.
- [ ] Prove factory reset and update download do not enter their mutation scope while another process holds the host lock.
- [ ] Run: `uv run pytest tests/unit/production/test_host_locks.py tests/unit/production/test_factory_reset.py tests/unit/production/test_remote_update.py -q`.

### Task 2: Active Report Lock

**Files:**
- Modify: `src/claw_trade/ui_backend/report_queue.py`
- Modify: `src/claw_trade/web/state.py`
- Modify: `src/claw_trade/ui_backend/workflow_bridge.py`
- Modify: `tests/unit/ui/test_report_queue.py`
- Modify: `tests/unit/ui/test_report_cancel.py`
- Modify: `tests/unit/ui/test_report_failure_visibility.py`

- [ ] Inject the report lock path/opener into `ReportTaskQueue` with the production default; tests use `tmp_path` lock files.
- [ ] Acquire a shared report lock before changing a queued task to `RUNNING`; lock failure leaves it non-running and records an explicit failure.
- [ ] Keep the file descriptor on the internal task until completed report/artifact writing and terminal task state are finished.
- [ ] Make workflow cancellation confirm the actual background thread exited; writing `CANCELLED` alone is insufficient. If the thread remains alive, keep the task running and locked, do not start the next task, and report that cancellation is not yet complete.
- [ ] Release on success, workflow failure, confirmed thread-exit cancellation, workflow creation exception, and every existing terminal helper path. Do not release merely because a status refresh failed.
- [ ] Add a real subprocess test proving SIGKILL releases the kernel lock.
- [ ] Add a contention test proving a watchdog-style exclusive lock blocks task transition to `RUNNING`, with no fallback.
- [ ] Run: `uv run pytest tests/unit/ui/test_report_queue.py tests/unit/ui/test_report_cancel.py tests/unit/ui/test_report_failure_visibility.py -q`.

### Task 3: Bounded Watchdog State Machine

**Files:**
- Modify: `packaging/production/root-helper/claw-trade-watchdog`
- Rewrite focused expectations: `tests/unit/production/test_watchdog_helper.py`

- [ ] Move both root helpers to `/opt/claw-trade/host-operations.lock`; watchdog also opens `/opt/claw-trade/report-active.lock` exclusively and non-blocking inside the host lock.
- [ ] Delete all UI/kiosk constants, probing, snapshots, recovery loops, and `systemctl start` calls. Keep only one bounded `restart claw-trade-control.service` action.
- [ ] Add strict retry state at `/run/claw-trade-watchdog/retry.json`: root-owned `0600`, regular non-symlink file, validated JSON fields, atomic temp write + flush + file `fsync` + replace + directory `fsync`.
- [ ] Probe health before applying startup grace/cooldown so a recovered service clears state automatically.
- [ ] Implement exact policy: attempt 1 writes deadline `now+300`; attempt 2 after that deadline writes `now+900`; attempt 3 writes `exhausted`; exhausted never restarts until health clears state.
- [ ] Write state while both exclusive locks are held and after final PID/suppression checks, before calling restart. State or lock errors return nonzero and never restart.
- [ ] Cover malformed state, unsafe inode, write failure, cooldown boundaries, three attempts, exhausted, healthy reset, report contention, host contention, PID change, rescue/update/maintenance suppression, and absence of frontend actions.
- [ ] Recalculate the hard upper bound using every HTTP, systemctl query/action, and sleep timeout; assert it fits the unit timeout with margin.
- [ ] Run: `uv run pytest tests/unit/production/test_watchdog_helper.py -q`.

### Task 4: Apply Helper And Host Installation

**Files:**
- Modify: `packaging/production/root-helper/claw-trade-apply-update`
- Modify: `packaging/production/systemd/claw-trade-watchdog.service`
- Modify: `packaging/production/systemd/claw-trade-apply-update.service`
- Modify: `packaging/production/bin/claw-trade-preflight`
- Modify: `scripts/production/install_production_package.sh`
- Modify: `scripts/production/install_factory_test_ubuntu.sh`
- Modify: `scripts/production/uninstall_factory_test_ubuntu.sh`
- Modify: `tests/unit/production/test_apply_update_helper.py`
- Modify: `tests/unit/production/test_production_paths_contract.py`

- [ ] Full installers create missing lock files once as `root:clawtrade 0660`, then verify parent and inode security without replacing existing files.
- [ ] Apply helper validates and reuses both lock inodes, blocks on the host lock, and never silently creates or replaces them during update.
- [ ] Package preflight checks packaged helper/unit/install contracts; add an explicit post-install host verification function for actual lock ownership and modes.
- [ ] Configure watchdog `RuntimeDirectory=claw-trade-watchdog`, mode `0700`, preserve `yes`, and a timeout above the proven helper bound. Set apply timeout to watchdog bound plus the existing update budget.
- [ ] The first fixed full/factory installer must run `disable --now claw-trade-watchdog.timer` before replacing host files and verify it remains disabled/inactive afterward; removing `enable --now` alone is insufficient for an already-enabled target.
- [ ] Later online updates snapshot the existing watchdog timer enabled/active states before copying units and restore only those states afterward. They must not unconditionally enable or disable a previously accepted installation.
- [ ] Uninstall stops timer and running service, confirms inactive, opens and non-blocking exclusively locks both fixed lock files, and keeps both FDs locked until retry directory/helper/units/lock inodes are deleted. Contention aborts uninstall without fallback.
- [ ] Run: `uv run pytest tests/unit/production/test_apply_update_helper.py tests/unit/production/test_production_paths_contract.py -q`.
- [ ] Run shell/Python syntax checks, `git diff --check`, and `systemd-analyze verify` against an equivalent temporary production layout.

### Task 5: Integrated Regression

**Files:**
- Modify only tests found necessary by the scoped regression.

- [ ] Run all focused Task 1-4 tests together.
- [ ] Run scoped production/UI regression for report queue, factory reset, remote update, packaging contracts, and watchdog.
- [ ] Dispatch independent specification and code-quality reviewers; fix every blocking finding and rerun focused tests.
- [ ] Build a production package with `scripts/production/build_production_package.sh`; require package preflight and archive audit success.

### Task 6: Target Acceptance With Timer Disabled

**Files:**
- No repository edits expected; preserve logs as task evidence.

- [ ] Before any live `/report`, follow the repository live-runtime preflight gate and print the required runtime table. Do not reuse the earlier no-report injection as report safety proof.
- [ ] Install the full package while watchdog timer remains disabled; verify lock owner/group/mode, retry directory contract, raw health JSON, and unit parsing.
- [ ] Use the real UI flow to start a report, hold it in a real worker turn, then inject the approved OpenClaw pause. Manually run the watchdog service and prove it does not restart while the shared report lock is held; verify run/artifact state is unchanged.
- [ ] Finish/cancel the report to a terminal state, inject again, and verify attempt 1 restarts exactly once.
- [ ] Keep the fault persistent through the 5-minute and 15-minute deadlines; verify exactly three total restarts and then `exhausted` with no fourth restart.
- [ ] Repair OpenClaw manually; verify healthy probing clears retry state automatically.
- [ ] Prove retry state survives at least three real inactive/active oneshot service rounds. With separate explicit approval for target reboot, reboot once and prove `/run` retry state is gone, attempt count is initial, and the five-minute startup grace applies.
- [ ] Verify rescue is never stopped, UI/kiosk are never started by watchdog, old PIDs disappear, and each endpoint has one listener.
- [ ] Only after all evidence passes, explicitly enable `claw-trade-watchdog.timer`.
