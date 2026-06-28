# Auto Update Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add production automatic update checks, with optional automatic install, then rebuild and retest a signed production package.

**Architecture:** A systemd timer runs a small production CLI as `clawtrade`. The CLI reuses `RemoteUpdateService`, so manifest signature checks, archive verification, apply service handoff, rollback, and updater state stay in one implementation.

**Tech Stack:** Python stdlib, existing `requests` dependency, existing production systemd packaging.

---

### Task 1: Add the auto-update CLI

**Files:**
- Create: `src/claw_trade/production/auto_update.py`
- Create: `packaging/production/bin/claw-trade-auto-update`
- Test: `tests/unit/production/test_auto_update.py`

- [ ] Add a tiny CLI that reads `CLAW_TRADE_UPDATE_BASE_URL`, current release version, and `CLAW_TRADE_AUTO_UPDATE_INSTALL`.
- [ ] If no update source is configured, exit successfully without touching state.
- [ ] Always check the signed manifest when configured.
- [ ] Install only when `CLAW_TRADE_AUTO_UPDATE_INSTALL=1`.
- [ ] Unit-test check-only and install-enabled behavior.

### Task 2: Add the systemd timer

**Files:**
- Create: `packaging/production/systemd/claw-trade-auto-update.service`
- Create: `packaging/production/systemd/claw-trade-auto-update.timer`
- Modify: `scripts/production/install_production_package.sh`
- Modify: `packaging/production/bin/claw-trade-preflight`
- Modify: `tests/unit/production/test_production_paths_contract.py`

- [ ] Add a oneshot service that runs `/opt/claw-trade/current/bin/claw-trade-auto-update`.
- [ ] Add a timer with boot delay, periodic interval, randomized delay, and persistent catch-up.
- [ ] Install `.timer` files with `.service` files.
- [ ] Enable and start the timer during production install.
- [ ] Extend production path contract tests.

### Task 3: Update docs and verify

**Files:**
- Modify: `packaging/production/README_FACTORY_TEST.md`

- [ ] Document: automatic check exists; automatic install requires `CLAW_TRADE_AUTO_UPDATE_INSTALL=1`.
- [ ] Run focused production tests.
- [ ] Build a new `0.1.2` package.
- [ ] Upload package, manifest, and signatures to Qiniu.
- [ ] Retest on Ubuntu target: timer/manual check sees `0.1.2`, install succeeds, services healthy, rescue inactive.
