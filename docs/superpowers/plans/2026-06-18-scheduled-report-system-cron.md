# Scheduled Report And System Cron Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild scheduled report cron and system maintenance cron on the current `main` branch, reusing main's existing data download/refresh paths instead of porting old downloader code.

**Architecture:** OpenClaw cron wakes a tiny OpenClaw worker. The worker calls one internal scheduled-work tool. `claw-trade` owns dispatch: scheduled reports enqueue the existing `/report` workflow, selection refresh calls the existing selection refresh service, and data maintenance calls the existing data gateway maintenance pipeline. No Python code pretends to be a worker, and no old crypto bulk downloader is migrated in this slice.

**Tech Stack:** Python 3.12, FastAPI UI backend, existing `OpenClawCronAdapter`, OpenClaw agent workspaces under `agents/`, shared scheduled-work OpenClaw plugin, pytest, existing data gateway `PublicDataRequest` / `DataAPI.request_data`.

---

## Scope

Implement:

- Scheduled report cron provisioning and wake handling.
- `run_scheduled_report_now` through OpenClaw cron, not direct queue bypass.
- System cron provisioning for:
  - `selection_data_refresh`
  - `data_maintenance` for `CN_A:eod`, `HK:eod`, `US:eod`, `CRYPTO:kline-refresh`
- OpenClaw workers:
  - `scheduled_report_runner`
  - `market_data_maintenance_worker`
- Shared scheduled-work tool support for all scheduled work kinds.

Do not implement:

- New crypto bulk archive downloader.
- New runtime guard/hard gate.
- OpenClaw runtime source changes.
- User notification for background data maintenance failures.
- Any report-generation shortcut outside `/report` and the existing queue.

## Success Criteria

- Existing price alert cron tests still pass.
- Scheduled report creation registers an OpenClaw cron job for `scheduled_report_runner`.
- Scheduled report manual run calls `cron.run`; it does not enqueue the report directly.
- Scheduled report cron wake enqueues a report task with `source="scheduled"`.
- System maintenance cron wake dispatches to selection refresh or data maintenance.
- Data maintenance uses `DataAPI.request_data` / `PublicDataRequest`, not the old branch's direct downloader shape.
- Missing maintenance scope fails closed and records the real error.
- Start runtime config includes both new workers.

## Files

Create:

- `agents/scheduled_report_runner/IDENTITY.md`
- `agents/scheduled_report_runner/USER.md`
- `agents/scheduled_report_runner/SKILLS.md`
- `agents/scheduled_report_runner/STAGES.yaml`
- `agents/scheduled_report_runner/skills/manifest.yaml`
- `agents/scheduled_report_runner/skills/scheduled-work-wake/SKILL.md`
- `agents/market_data_maintenance_worker/IDENTITY.md`
- `agents/market_data_maintenance_worker/USER.md`
- `agents/market_data_maintenance_worker/SKILLS.md`
- `agents/market_data_maintenance_worker/STAGES.yaml`
- `agents/market_data_maintenance_worker/skills/manifest.yaml`
- `agents/market_data_maintenance_worker/skills/scheduled-work-wake/SKILL.md`
- `src/claw_trade/data_gateway/maintenance/scheduled_runner.py`
- `src/claw_trade/ui_backend/system_cron_provisioner.py`
- `src/claw_trade/cli/provision_system_cron.py`
- `tests/unit/data_gateway/test_scheduled_data_maintenance_runner.py`
- `tests/unit/ui/test_system_cron_provisioner.py`
- `tests/unit/cli/test_provision_system_cron.py`

Modify:

- `openclaw_plugins/claw-trade-scheduled-work-tools/index.js`
- `tests/contracts/test_scheduled_work_tool_contract.py`
- `src/claw_trade/ui_backend/scheduler_service.py`
- `src/claw_trade/ui_backend/scheduled_work_store.py`
- `src/claw_trade/ui_backend/scheduled_work_runner.py`
- `src/claw_trade/web/routes_ui.py`
- `src/claw_trade/web/state.py`
- `src/claw_trade/data_gateway/maintenance/__init__.py`
- `scripts/start-control-runtime.sh`
- `tests/unit/test_start_control_runtime_script.py`
- `tests/integration/test_openclaw_cron_price_alert_contract.py`
- `tests/unit/ui/test_scheduler_service.py`
- `tests/unit/ui/test_scheduler_queue_integration.py`
- `tests/contracts/test_scheduled_and_alert_api_contracts.py`

---

### Task 1: Extend The Shared Scheduled-Work Tool Contract

**Files:**

- Modify: `openclaw_plugins/claw-trade-scheduled-work-tools/index.js`
- Modify: `tests/contracts/test_scheduled_work_tool_contract.py`
- Create/modify agent files listed above
- Modify: `scripts/start-control-runtime.sh`
- Modify: `tests/unit/test_start_control_runtime_script.py`

- [x] **Step 1: Write the contract test**

Update `tests/contracts/test_scheduled_work_tool_contract.py` so it asserts:

```python
def test_scheduled_work_tool_accepts_all_scheduled_work_kinds_without_internal_endpoint_or_token() -> None:
    source = (PLUGIN_DIR / "index.js").read_text(encoding="utf-8")
    schema_start = source.index("const TOOL_INPUT_SCHEMA")
    schema_end = source.index("const TOOL_ERROR_CODES")
    schema_text = source[schema_start:schema_end]

    for kind in ("price_alert_scan", "scheduled_report", "selection_data_refresh", "data_maintenance"):
        assert kind in schema_text
    for field in ("bucketKey", "scheduledReportId", "market", "jobKind", "cronRunId", "requestId", "reason"):
        assert field in schema_text
    assert "token" not in schema_text.lower()
    assert "url" not in schema_text.lower()
```

Also update the manifest/profile test to include the two new agent directories and ensure they expose only `claw-trade-scheduled-work-wake`.

- [x] **Step 2: Run the failing test**

Run:

```bash
uv run pytest tests/contracts/test_scheduled_work_tool_contract.py -q
```

Expected: FAIL because the tool schema only allows `price_alert_scan` and the new agents do not exist.

- [x] **Step 3: Update the OpenClaw tool schema and validation**

In `openclaw_plugins/claw-trade-scheduled-work-tools/index.js`:

- Keep the tool name `claw-trade-scheduled-work-wake`.
- Keep the internal token/base URL environment variables.
- Expand `kind` enum to:
  - `price_alert_scan`
  - `scheduled_report`
  - `selection_data_refresh`
  - `data_maintenance`
- Allow only these input keys:
  - `kind`
  - `cronRunId`
  - `requestId`
  - `bucketKey`
  - `scheduledReportId`
  - `market`
  - `jobKind`
  - `reason`
  - `maintenanceJobId`
- Validation:
  - `price_alert_scan` requires `bucketKey` and `cronRunId`.
  - `scheduled_report` requires `scheduledReportId`; `cronRunId` may be `"auto"`.
  - `selection_data_refresh` accepts optional `reason`; default to `scheduled_data_refresh`.
  - `data_maintenance` requires `market` and `jobKind`; `cronRunId` may be `"auto"`.
- Generate a kind-specific `cronRunId` when `"auto"` is provided.

Do not add model-facing investment prose. Tool text should say the internal wake completed or failed.

- [x] **Step 4: Add the two minimal worker profiles**

Add `scheduled_report_runner` and `market_data_maintenance_worker` with the same shape as `price_alert_scan_worker`:

- runtime: OpenClaw single-agent turn
- mounted tool: `claw-trade-scheduled-work-wake`
- no OpenViking access
- no report writing, no market judgment, no notification writing

`scheduled_report_runner/USER.md` should tell the worker to call the tool with the supplied cron payload and not generate a report.
`market_data_maintenance_worker/USER.md` should tell the worker to call the tool with the supplied cron payload and not choose providers, fabricate maintenance success, or notify users.

- [x] **Step 5: Register workers in runtime setup**

Update the worker list in `scripts/start-control-runtime.sh` to include:

```text
scheduled_report_runner
market_data_maintenance_worker
```

Update `tests/unit/test_start_control_runtime_script.py` expected workers accordingly.

- [x] **Step 6: Run contract tests**

Run:

```bash
uv run pytest tests/contracts/test_scheduled_work_tool_contract.py tests/unit/test_start_control_runtime_script.py -q
```

Expected: PASS.

---

### Task 2: Persist Scheduled Reports And Provision OpenClaw Cron

**Files:**

- Modify: `src/claw_trade/ui_backend/scheduled_work_store.py`
- Modify: `src/claw_trade/ui_backend/scheduler_service.py`
- Modify: `src/claw_trade/web/state.py`
- Modify: `tests/unit/ui/test_scheduler_service.py`
- Modify: `tests/unit/ui/test_scheduler_queue_integration.py`
- Modify: `tests/contracts/test_scheduled_and_alert_api_contracts.py`

- [x] **Step 1: Write scheduled report cron tests**

Add/update tests so they prove:

```python
def test_create_scheduled_report_registers_openclaw_cron_job() -> None:
    # SchedulerService(... cron_adapter=OpenClawCronAdapter(fake_gateway), store=InMemoryScheduledWorkStore())
    # create daily AAPL report at 09:30
    # assert fake_gateway.calls[0]["method"] == "cron.add"
    # assert params["agentId"] == "scheduled_report_runner"
    # assert params["schedule"] == {"kind": "cron", "expr": "30 9 * * *", "tz": "UTC", "staggerMs": 0}
    # assert params["payload"]["kind"] == "agentTurn"
    # assert '"kind":"scheduled_report"' in params["payload"]["message"]
    # assert '"scheduledReportId":"schedule-1"' in params["payload"]["message"]
    # assert params["payload"]["toolsAllow"] == ["claw-trade-scheduled-work-wake"]
```

Add a weekly schedule assertion with Python weekday `0` mapping to cron weekday `1`.

- [x] **Step 2: Write persistence tests**

Add tests for `JsonScheduledWorkStore`:

```python
def test_scheduled_reports_persist_across_store_instances(tmp_path: Path) -> None:
    store = JsonScheduledWorkStore(tmp_path / "scheduled.json")
    store.save_scheduled_report(report)

    second = JsonScheduledWorkStore(tmp_path / "scheduled.json")

    assert second.get_scheduled_report(report.id) == report
```

Ensure old JSON payloads without `scheduled_reports` still load.

- [x] **Step 3: Run the failing tests**

Run:

```bash
uv run pytest tests/unit/ui/test_scheduler_service.py tests/unit/ui/test_scheduler_queue_integration.py tests/contracts/test_scheduled_and_alert_api_contracts.py -q
```

Expected: FAIL until store and scheduler are updated.

- [x] **Step 4: Extend `scheduled_work_store.py` minimally**

Add a `ScheduledReport` dataclass to `scheduled_work_store.py` with existing scheduler fields plus:

```python
openclaw_cron_job_id: str | None
last_cron_run_id: str | None
sync_error_message: str | None
```

Add protocol/store methods:

```python
def save_scheduled_report(self, report: ScheduledReport) -> None: ...
def get_scheduled_report(self, report_id: str) -> ScheduledReport | None: ...
def list_scheduled_reports(self, *, states: set[str] | None = None) -> list[ScheduledReport]: ...
```

Update JSON payload shape to include `scheduled_reports`, but default missing keys so existing `.ui-scheduled-work.json` files do not break.

- [x] **Step 5: Wire `SchedulerService` to the store and cron adapter**

Update `SchedulerService.__init__`:

```python
store: ScheduledWorkStore | None = None
cron_adapter: OpenClawCronAdapter | None = None
user_id_provider: Callable[[], str] | None = None
allow_local_tick_for_tests: bool = False
```

Use `store or InMemoryScheduledWorkStore()` instead of `_items`.

On `create_scheduled_report`:

- Save the report.
- If `cron_adapter` exists, call `add_job(...)`.
- Store `openclaw_cron_job_id`.
- If provisioning fails, save state `sync_failed` and `sync_error_message`, then raise `UiServiceError("CRON_PROVISION_FAILED", ...)`.

Cron payload message should be exactly boring:

```text
Call `claw-trade-scheduled-work-wake` exactly once with this JSON payload and no other tool calls:
{"kind":"scheduled_report","scheduledReportId":"schedule-1","cronRunId":"auto"}
Do not generate reports, analyze markets, or rewrite the payload.
```

On pause/resume/delete:

- If an OpenClaw cron job exists, call `cron_adapter.update_job(..., {"enabled": False/True})` or `remove_job`.
- Keep existing DTO shape unchanged.

- [x] **Step 6: Keep local tick test-only**

Change `tick_scheduled_reports` so:

- It raises `UiServiceError("INVALID_INPUT", "本地 tick 仅允许测试。")` unless `allow_local_tick_for_tests=True`.
- Existing tests that intentionally cover local ticking pass that flag.

- [x] **Step 7: Wire `web/state.py`**

Construct:

```python
scheduler_service = SchedulerService(
    enqueue_report_task=...,
    queue_snapshot_provider=...,
    store=scheduled_work_store,
    cron_adapter=cron_adapter,
)
```

Create `scheduled_work_store` and `cron_adapter` before `SchedulerService`.

- [x] **Step 8: Run focused tests**

Run:

```bash
uv run pytest tests/unit/ui/test_scheduler_service.py tests/unit/ui/test_scheduler_queue_integration.py tests/contracts/test_scheduled_and_alert_api_contracts.py -q
```

Expected: PASS.

---

### Task 3: Dispatch Scheduled Report Wake Through The Existing Report Queue

**Files:**

- Modify: `src/claw_trade/ui_backend/scheduler_service.py`
- Modify: `src/claw_trade/ui_backend/scheduled_work_runner.py`
- Modify: `src/claw_trade/web/routes_ui.py`
- Modify: `tests/integration/test_openclaw_cron_price_alert_contract.py`
- Create or modify: `tests/unit/ui/test_scheduled_work_runner.py`

- [x] **Step 1: Write dispatch tests**

Add tests proving:

```python
def test_scheduled_report_wake_dispatches_to_scheduler_queue() -> None:
    # create schedule
    # runner.handle_wake({"kind": "scheduled_report", "scheduledReportId": id, "cronRunId": "cron-run-1", "requestId": "cron-request-1"})
    # assert queue received one task
    # assert task["source"] == "scheduled"
    # assert response["status"] == "ok"
```

Keep existing price alert tests unchanged except constructor arguments.

- [x] **Step 2: Run failing dispatch tests**

Run:

```bash
uv run pytest tests/integration/test_openclaw_cron_price_alert_contract.py tests/unit/ui/test_scheduled_work_runner.py -q
```

Expected: FAIL until `ScheduledWorkRunner` dispatches multiple kinds.

- [x] **Step 3: Add `handle_scheduled_report_cron_wake`**

In `SchedulerService` add:

```python
def handle_scheduled_report_cron_wake(
    self,
    *,
    request_id: str,
    scheduled_report_id: str,
    cron_run_id: str,
) -> dict[str, ReportTaskForUser | ReportQueueSnapshotForUser]:
```

This method:

- Loads the schedule from the store.
- Rejects deleted/missing schedules.
- Enqueues `_build_task_input(item)` with `source="scheduled"`.
- Updates `last_run_task_id`, `last_cron_run_id`, `next_run_at`, `state`, `updated_at`.
- Returns the same `task` and `queueSnapshot` shape the UI already understands.

- [x] **Step 4: Generalize `ScheduledWorkRunner`**

Update constructor:

```python
def __init__(
    self,
    *,
    price_alert_scan_service: Any | None = None,
    scheduler_service: Any | None = None,
    selection_data_refresh_runner: Any | None = None,
    data_maintenance_runner: Any | None = None,
) -> None:
```

`handle_wake` dispatches:

- `price_alert_scan` -> existing scan path.
- `scheduled_report` -> `scheduler_service.handle_scheduled_report_cron_wake(...)`.
- `selection_data_refresh` -> later task runner.
- `data_maintenance` -> later task runner.

Unknown kind raises `ScheduledWorkRunnerError("INVALID_INPUT", ...)`.

- [x] **Step 5: Let the route forward generic fields**

Update `ScheduledWorkCronWakeRequest` in `routes_ui.py` to include optional:

```python
requestId: str | None = None
scheduledReportId: str | None = None
market: str | None = None
jobKind: str | None = None
reason: str | None = None
maintenanceJobId: str | None = None
```

Forward all non-`None` fields to `handle_wake`.

- [x] **Step 6: Run focused tests**

Run:

```bash
uv run pytest tests/integration/test_openclaw_cron_price_alert_contract.py tests/unit/ui/test_scheduled_work_runner.py -q
```

Expected: PASS.

---

### Task 4: Add System Maintenance Runners And Cron Provisioner

**Files:**

- Create: `src/claw_trade/data_gateway/maintenance/scheduled_runner.py`
- Modify: `src/claw_trade/data_gateway/maintenance/__init__.py`
- Create: `src/claw_trade/ui_backend/system_cron_provisioner.py`
- Create: `src/claw_trade/cli/provision_system_cron.py`
- Modify: `src/claw_trade/web/state.py`
- Create: `tests/unit/data_gateway/test_scheduled_data_maintenance_runner.py`
- Create: `tests/unit/ui/test_system_cron_provisioner.py`
- Create: `tests/unit/cli/test_provision_system_cron.py`
- Modify: `tests/unit/ui/test_scheduled_work_runner.py`

- [x] **Step 1: Write data maintenance runner tests**

Tests should use a fake `DataAPI` with `request_data`, not the old branch's `get_data_batch`.

Minimum tests:

```python
def test_cn_a_eod_runs_existing_daily_incremental_maintenance_job() -> None:
    # fake planner returns two gaps
    # fake api.request_data receives two PublicDataRequest objects
    # runner returns status succeeded and saved job requested_by == "openclaw_cron"

def test_crypto_kline_refresh_uses_data_layer_daily_bar_scope_without_bulk_downloader() -> None:
    # dataset repository has one CRYPTO daily_bar record through existing insert_normalized
    # runner plans the next missing day and calls DataAPI.request_data
    # assert no packageRefresh key or packageRefresh is None

def test_crypto_kline_refresh_fails_closed_when_scope_is_empty() -> None:
    # empty repository
    # runner raises ValueError with maintenance scope is empty
    # job is saved failed with the same error
```

- [x] **Step 2: Implement `ScheduledDataMaintenanceRunner`**

Use main's current interfaces:

- `run_daily_incremental`
- `MaintenanceJob`
- `MaintenanceJobRepository`
- `DataAPI.request_data`
- `PublicDataRequest`

Supported plans:

- `CN_A:eod` -> `daily_incremental`, `daily_bar`
- `HK:eod` -> `daily_incremental`, `daily_bar`
- `US:eod` -> `daily_incremental`, `daily_bar`
- `CRYPTO:kline-refresh` -> `daily_incremental`, `daily_bar`

Implement a small `DailyBarMaintenanceGap` with `to_public_data_request(...)`.

Implement repository-backed planning by scanning existing normalized metadata through `DatasetRepository.iter_normalized(..., include_row=False)` and grouping by `(symbol_id, universe_ref)` to find the latest `period_end`.

Add a comment:

```python
# ponytail: O(n) metadata scan is fine for the first cron bridge; replace with an aggregate query if maintenance scope grows.
```

If no scope rows are found, raise `ValueError("<MARKET> daily_bar maintenance scope is empty in data layer")` and save a failed job.

Do not import or call old `crypto_binance_refresh`.

- [x] **Step 3: Write provisioner tests**

`SystemCronProvisioner` should call `OpenClawCronAdapter.add_job` with:

- `selection-data-refresh:CN_A:daily`
  - agent: `market_data_maintenance_worker`
  - payload JSON kind: `selection_data_refresh`
- `data-maintenance:CN_A:eod`
- `data-maintenance:HK:eod`
- `data-maintenance:US:eod`
- `data-maintenance:CRYPTO:kline-refresh`
  - agent: `market_data_maintenance_worker`
  - payload JSON kind: `data_maintenance`

All payloads use only `claw-trade-scheduled-work-wake`.

- [x] **Step 4: Implement `SystemCronProvisioner`**

Keep it thin:

```python
@dataclass(frozen=True)
class SystemCronJobRef:
    key: str
    openclaw_cron_job_id: str

class SystemCronProvisioner:
    def ensure_selection_data_refresh(self) -> SystemCronJobRef: ...
    def ensure_data_maintenance(self, *, market: str, job_kind: str, schedule: Mapping[str, Any]) -> SystemCronJobRef: ...
```

Convenience methods for CN_A/HK/US/CRYPTO may call `ensure_data_maintenance`.

- [x] **Step 5: Write CLI tests**

Test:

- `--selection-data-refresh` provisions one job.
- `--cn-a-eod-cron-expr`, `--hk-eod-cron-expr`, `--us-eod-cron-expr`, `--crypto-kline-refresh-cron-expr` provision expected jobs.
- No flags exits with "No system cron job selected."

- [x] **Step 6: Implement CLI**

Add `src/claw_trade/cli/provision_system_cron.py` based on the old worktree shape, but using the current `OpenClawCronAdapter` generic `add_job` API.

Output:

```json
{"results":[{"key":"selection-data-refresh:CN_A:daily","openclawCronJobId":"..."}]}
```

- [x] **Step 7: Wire runtime services**

In `web/state.py`:

- Build `ScheduledWorkRunner` with:
  - `price_alert_scan_service`
  - `scheduler_service`
  - `selection_data_refresh_runner=selection_refresh_service`
  - `data_maintenance_runner_factory` or lazy runner wrapper

Use lazy data gateway construction for data maintenance so UI startup does not require Mongo until the maintenance wake actually runs.

The lazy data maintenance runner should:

- call `build_data_gateway_runtime_from_env()`
- use `runtime.data_api`
- use `CollectionMaintenanceJobRepository(runtime.repository)`
- pass `dataset_repository=runtime.repository`

- [x] **Step 8: Add scheduled-work dispatch tests**

Add tests that `ScheduledWorkRunner`:

- dispatches `selection_data_refresh` to `run_automatic_refresh_once(reason=...)`
- dispatches `data_maintenance` to the runner and preserves failures as `DATA_MAINTENANCE_FAILED`

- [x] **Step 9: Run focused tests**

Run:

```bash
uv run pytest \
  tests/unit/data_gateway/test_scheduled_data_maintenance_runner.py \
  tests/unit/ui/test_system_cron_provisioner.py \
  tests/unit/cli/test_provision_system_cron.py \
  tests/unit/ui/test_scheduled_work_runner.py \
  -q
```

Expected: PASS.

---

### Task 5: Regression And Runtime Evidence

**Files:**

- Modify: `docs/evidence/` only if a live proof is run and needs recording.
- Modify: `memory/2026-06-18.md` after implementation.

- [x] **Step 1: Run scheduled-work regression**

Run:

```bash
uv run pytest \
  tests/contracts/test_scheduled_work_tool_contract.py \
  tests/integration/test_openclaw_cron_price_alert_contract.py \
  tests/unit/ui/test_scheduler_service.py \
  tests/unit/ui/test_scheduler_queue_integration.py \
  tests/unit/ui/test_scheduled_work_runner.py \
  tests/unit/ui/test_system_cron_provisioner.py \
  tests/unit/cli/test_provision_system_cron.py \
  tests/unit/data_gateway/test_scheduled_data_maintenance_runner.py \
  tests/contracts/test_scheduled_and_alert_api_contracts.py \
  tests/unit/test_start_control_runtime_script.py \
  -q
```

Expected: PASS.

- [x] **Step 2: Run broader relevant regression**

Run:

```bash
uv run pytest tests/unit/ui tests/contracts/test_scheduled_and_alert_api_contracts.py tests/integration/test_openclaw_cron_price_alert_contract.py -q
```

Expected: PASS.

- [x] **Step 3: Optional live proof only after preflight**

If live proof is requested, follow `AGENTS.md` live runtime preflight:

- Read fixed runtime guidance in `memory/`.
- Reuse healthy runtime if `1933` and `18789` pass.
- Otherwise use:

```bash
scripts/start-control-runtime.sh -- <focused live proof command>
```

Proof targets:

- OpenClaw cron runs `scheduled_report_runner` and internal route enqueues `/report`.
- OpenClaw cron runs `market_data_maintenance_worker` for `selection_data_refresh`.
- Data maintenance empty scope fails closed with evidence.

Do not claim live completion from unit tests alone.

- [x] **Step 4: Update memory**

Append to `memory/2026-06-18.md`:

- branch name
- changed files
- tests run and exit codes
- whether live proof was run
- explicit note: no old crypto bulk downloader migrated

---

## Stop Conditions

Stop and ask the human before proceeding if:

- Implementing data maintenance would require porting old `crypto_binance_refresh` or adding a new downloader.
- A proposed change moves workflow/report authority into OpenClaw.
- Scheduled report wake would bypass `/report` or direct-enqueue from worker code.
- Missing data maintenance scope is being treated as success.
- Any OpenClaw runtime source under `third_party/openclaw/src/**` must be changed.
- A new guard, hard gate, output validator, or report-expression restriction seems necessary.
