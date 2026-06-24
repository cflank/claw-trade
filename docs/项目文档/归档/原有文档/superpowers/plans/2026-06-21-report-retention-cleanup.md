# Report Retention Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a bounded report cleanup mechanism: default 7-day retention, Settings -> General retention control with 7/14/30-day choices, startup/daily automatic cleanup, and hard deletion for existing report delete/clear actions.

**Architecture:** Keep authority in the UI backend/report-management layer. Add one cleanup settings service, one report cleanup service, and one small scheduler wired through existing FastAPI lifespan. Existing report repository remains the in-memory saved-report index; filesystem/runtime deletion belongs to the cleanup service.

**Tech Stack:** Python FastAPI backend under `src/claw_trade`, pytest, React/Vite UI under `web/research-ui`, Vitest, real Chrome acceptance through Playwright MCP.

---

## Implementation Assumptions

- `1 个月` is stored and enforced as `30` days.
- Automatic retention only targets report run directories named `run-*` under the configured `runs` root. It does not clean `/select` workflow resources in this change.
- Report age uses report completion time: `runs/<run_id>/state.json` `updated_at`, then `created_at`, then directory mtime.
- A run whose persisted workflow status is not terminal is protected, even after a backend restart when the in-memory queue is empty. Terminal statuses are only `completed`, `failed`, and `cancelled`.
- Existing tombstones in `runs/.ui-deleted-reports.json` stay useful for old hidden reports until their run is hard-deleted. After a hard delete succeeds for a run, remove that run id from the tombstone index.
- The cleaner may delete per-run OpenViking workflow files but must not mutate OpenViking vector/LevelDB files directly.
- The cleaner may delete OpenClaw session files only when the file content proves it is tied to exactly that run id and not a shared/mixed-run file.

## Success Criteria

- Fresh settings return 7-day retention; only 7, 14, and 30 are accepted.
- Settings -> General shows `报告保留时间` with `7 天`, `14 天`, `1 个月`, persists changes, and reset returns it to 7 days.
- Single delete and bulk clear hard-delete targeted report run artifacts and return partial-failure details instead of claiming full success on failure.
- Startup cleanup runs once, then daily, without blocking app startup.
- Active/queued/in-flight report runs are skipped using backend-owned state, not UI display payloads.
- WeChat login state, OpenClaw install/cache directories, config, device identity, and shared runtime files are not deleted.
- Focused backend/frontend tests pass, and real Chrome verifies the Settings control and new hard-delete confirmation copy.

## Task 1: Backend Cleanup Settings

Add persistence and API for the retention option.

- [ ] Add `UI_REPORT_CLEANUP_SETTINGS_COLLECTION = "ui_report_cleanup_settings"` in `src/claw_trade/data_gateway/settings_store.py`.
- [ ] Add a small Mongo store class in `settings_store.py` for one record keyed by `_id = "report_cleanup_settings"`, with `read()`, `write(payload)`, and `clear()`.
- [ ] Add `src/claw_trade/ui_backend/report_cleanup_settings.py`:
  - `ALLOWED_RETENTION_DAYS = (7, 14, 30)`
  - default payload `{ "reportRetentionDays": 7 }`
  - validation that rejects any other value
  - JSON fallback at `runs/.ui-report-cleanup-settings.json` when Mongo is not configured
  - `load_settings()`, `save_settings(days)`, `reset_to_defaults()`
- [ ] Wire the service into `UiHttpServices` and `build_ui_http_services()` in `src/claw_trade/web/state.py`.
- [ ] Add request/response models and routes in `src/claw_trade/web/routes_ui.py`:
  - `GET /api/ui/get-report-cleanup-settings`
  - `POST /api/ui/save-report-cleanup-settings`
- [ ] Update `reset-settings-to-defaults` to call cleanup settings reset and include `reportCleanup` in the response.
- [ ] Add tests:
  - `tests/unit/ui/test_report_cleanup_settings.py`
  - route coverage in `tests/e2e/ui/test_research_ui_server.py`
- [ ] Run:
  - `uv run pytest tests/unit/ui/test_report_cleanup_settings.py tests/e2e/ui/test_research_ui_server.py`

## Task 2: Report Cleanup Service

Implement the hard-delete engine without UI or scheduler changes yet.

- [ ] Add `src/claw_trade/ui_backend/report_cleanup.py`.
- [ ] Define compact result objects:
  - `ReportCleanupRunResult`
  - `ReportCleanupResult`
  - include `deletedRunIds`, `skippedRunIds`, `failedRunIds`, `deletedBytesApprox`, `warnings`, and `userMessage`.
- [ ] Service constructor inputs:
  - `run_root`
  - `openviking_workflow_root`
  - `openclaw_state_root`
  - `repository`
  - `protected_run_ids_provider`
  - `in_flight_report_ids_provider`
  - optional `now_provider` for tests
- [ ] Resolve runtime roots from env in `build_ui_http_services()`:
  - `OPENVIKING_DATA_DIR` or `.runtime/dev-services/openviking/data`
  - `OPENCLAW_STATE_DIR` or `.runtime/dev-services/openclaw-state`
  - workflow root is `<openviking_data_dir>/viking/default/resources/workflow`
- [ ] Implement safe run id/path checks:
  - accept only non-empty local ids beginning with `run-`
  - resolve paths and require they remain below their root
  - never delete a root directory itself
- [ ] Add a backend-owned protected-run provider to `ReportTaskQueue`:
  - add `protected_run_ids_for_cleanup() -> set[str]`
  - it reads internal `ReportTask.run_id`, not `to_report_task_for_user()` output
  - it includes tasks whose status is `queued`, `running`, `saving_report`, or `pdf_exporting`
  - queued tasks without `run_id` do not map to a run directory yet, so they cannot be deleted by run-id cleanup
  - it returns a thread-safe snapshot because the cleanup scheduler reads it from a background thread
- [ ] Implement protected-run detection in the cleaner from `protected_run_ids_provider()`.
- [ ] Implement persisted workflow status protection in the cleaner:
  - read `runs/<run_id>/state.json` before deleting a run
  - skip if `status` is missing or not one of `completed`, `failed`, `cancelled`
  - this protects old interrupted runs after backend restart, when queue memory is empty
- [ ] Add backend-owned in-flight send tracking:
  - create a tiny tracker, for example `ReportFileSendTracker`, with `track(report_id)` context manager and `active_report_ids()`
  - pass it into `ReportNotificationService`
  - wrap `request_full_report_file()` so UI forward and WeChat “发送完整报告” both mark the report as sending until the send attempt returns
  - pass `tracker.active_report_ids` into the cleaner as `in_flight_report_ids_provider`
- [ ] Implement in-flight protection in the cleaner by skipping any run id in `in_flight_report_ids_provider()`.
- [ ] Add an in-process deletion lock so overlapping manual and automatic cleanup runs serialize.
- [ ] Implement `cleanup_expired_reports(retention_days)`:
  - scan `run_root/run-*`
  - compute age from `state.updated_at`, then `state.created_at`, then directory mtime
  - delete only runs older than cutoff and not protected
- [ ] Implement `delete_report_runs(run_ids)`:
  - no retention cutoff
  - still reject invalid ids and skip protected runs
- [ ] Implement deletion scope per run:
  - delete `runs/<run_id>/`
  - delete OpenViking workflow directory `<workflow_root>/<run_id>/`
  - delete OpenClaw session files under `<openclaw_state_root>/agents/*/sessions/` only if the text contains this exact run id and contains no other `run-*` id
  - clean delivery queue entries/files under `<openclaw_state_root>` only when each removed record/file is uniquely tied to this run; otherwise add a warning and skip
  - remove the run from repository memory and tombstone index after filesystem deletion succeeds
- [ ] Extend `ReportRepository` minimally:
  - add a method to remove in-memory report/pdf state without adding a tombstone
  - add a method to discard a tombstone id and persist the index
  - keep existing restore behavior for older tombstoned runs
- [ ] Add focused tests in `tests/unit/ui/test_report_cleanup_service.py`:
  - expired inactive run deleted
  - recent run kept
  - running run with internal `ReportTask.run_id` skipped even though UI snapshot would not expose `reportId`
  - queued task without `run_id` does not crash cleanup
  - old persisted non-terminal run is skipped after simulated restart with empty protected-run provider
  - in-flight send report skipped
  - explicit delete ignores retention cutoff but respects protected status
  - `runs/<run_id>` and OpenViking workflow dir deleted
  - OpenClaw session file with only that run id deleted
  - OpenClaw mixed-run session file skipped
  - shared delivery queue record removal or warning behavior
  - path traversal/non-run ids rejected
  - tombstone removed after hard delete
- [ ] Update existing repository/restore tests affected by tombstone semantics:
  - `tests/unit/ui/test_report_repository.py`
  - `tests/unit/ui/test_report_history_restore.py`
- [ ] Run:
  - `uv run pytest tests/unit/ui/test_report_cleanup_service.py tests/unit/ui/test_report_repository.py tests/unit/ui/test_report_history_restore.py`

## Task 3: Wire Manual Delete/Clear To Cleaner

Make existing delete endpoint perform hard deletion.

- [ ] Update `UiHttpServices` to include `report_cleanup_service`.
- [ ] Update `POST /api/ui/delete-saved-report` in `routes_ui.py` to call `services.report_cleanup_service.delete_report_runs([payload.reportId])`.
- [ ] Return the structured cleanup result while preserving `deleted: true` only when that report id was deleted.
- [ ] Use this exact delete response shape:
  - `deleted: boolean`
  - `reportId: string`
  - `userMessage: string`
  - `cleanup: { deletedRunIds: string[]; skippedRunIds: string[]; failedRunIds: string[]; deletedBytesApprox: number; warnings: string[] }`
- [ ] If the selected report is skipped or failed, return HTTP success with `deleted: false` and the cleanup payload, unless the request itself is invalid.
- [ ] Keep frontend bulk clear as the existing frontend loop; each call uses the same cleaner.
- [ ] Update API contract tests if the delete output shape changes:
  - `src/claw_trade/ui_contracts/api_contracts.py`
  - `tests/contracts/test_ui_api_contracts.py`
- [ ] Add route tests in `tests/e2e/ui/test_research_ui_server.py` proving delete calls the cleaner and returns partial details.
- [ ] Add a unit test proving `ReportNotificationService.request_full_report_file()` marks the report id as in-flight while sending and clears it afterwards:
  - update `tests/unit/ui/test_report_file_send.py` or `tests/unit/ui/test_report_notification_service.py`
- [ ] Run:
  - `uv run pytest tests/e2e/ui/test_research_ui_server.py tests/contracts/test_ui_api_contracts.py tests/unit/ui/test_report_file_send.py tests/unit/ui/test_report_notification_service.py`

## Task 4: Startup/Daily Cleanup Scheduler

Run automatic cleanup without blocking the UI.

- [ ] Add `ReportCleanupScheduler` in `src/claw_trade/ui_backend/report_cleanup.py` or a tiny sibling module.
- [ ] Scheduler behavior:
  - starts a daemon thread
  - runs cleanup once shortly after start
  - then sleeps 24 hours between runs
  - reads retention days from cleanup settings each time
  - catches/logs exceptions and keeps the UI alive
  - has `start()` and `stop()` methods for FastAPI lifespan
- [ ] Wire scheduler into `UiHttpServices`.
- [ ] In `build_research_ui_app()` lifespan, start/stop cleanup scheduler only when `owns_services` is true.
- [ ] Keep tests deterministic by not auto-starting scheduler for injected service fakes.
- [ ] Add focused scheduler tests:
  - start calls cleanup once
  - stop exits cleanly
  - cleanup exception does not crash scheduler
- [ ] Run:
  - `uv run pytest tests/unit/ui/test_report_cleanup_service.py tests/e2e/ui/test_research_ui_server.py`

## Task 5: Frontend API Contracts And Settings UI

Expose retention under Settings -> General.

- [ ] Add TypeScript contracts in `web/research-ui/src/api/contracts.ts`:
  - `ReportCleanupSettingsForUser`
  - `GetReportCleanupSettingsOutput`
  - `SaveReportCleanupSettingsInput`
  - `SaveReportCleanupSettingsOutput`
  - update `DeleteSavedReportOutput` to the exact shape from Task 3, with `deleted: boolean`, `cleanup`, and `userMessage`
  - cover both `deleted: true` and `deleted: false` in TypeScript-facing tests
  - add `reportCleanup` to `ResetSettingsToDefaultsOutput`
- [ ] Add client functions in `web/research-ui/src/api/client.ts` and re-export from `workspace.ts`:
  - `getReportCleanupSettings()`
  - `saveReportCleanupSettings(input)`
- [ ] Update `SettingsPage.tsx`:
  - add cleanup settings state
  - load cleanup settings as the fourth initial request and update the pending count
  - add save handler
  - reset handler sets retention back to returned default
- [ ] Update `SettingsSections.tsx` props and General tab:
  - add a `报告保留时间` select
  - options `7 天`, `14 天`, `1 个月`
  - save button or immediate save using the existing settings action style; prefer explicit save for consistency with other settings sections
  - show success/error feedback without internal runtime wording
- [ ] Add or update CSS only if existing settings form classes do not fit; do not create a new visual pattern.
- [ ] Update frontend tests:
  - `web/research-ui/src/__tests__/settings-page.test.tsx`
  - `web/research-ui/src/__tests__/settings-css-sections.test.tsx`
  - any settings enhanced-source mocks that need the new endpoint
- [ ] Run:
  - `pnpm --dir web/research-ui test -- settings-page.test.tsx settings-css-sections.test.tsx settings-enhanced-sources.test.tsx`

## Task 6: Frontend Delete/Clear Copy And Partial Handling

Make the existing History Rail actions tell the truth.

- [ ] Update single-delete confirmation in `web/research-ui/src/routes/HomePage.tsx`:
  - say it will delete report files, charts, runtime evidence, worker output, and related local cache
  - remove the old “底层运行证据会保留” copy
- [ ] Update bulk clear confirmation:
  - keep current target semantics: search active means searched reports, no search means all listed reports
  - say every targeted report run will be hard-deleted
- [ ] Update delete handling:
  - remove items only when the API says that report was deleted
  - for bulk clear, report partial failures if any selected report is skipped/failed
- [ ] Update `HistoryRail.tsx` delete title if needed so it no longer says only “从历史中移除”.
- [ ] Update frontend tests:
  - `web/research-ui/src/__tests__/home-page.test.tsx`
  - assertions for single-delete confirmation copy
  - assertions for searched clear and all clear confirmation copy
  - single delete does not remove the report when API returns `deleted: false`
  - bulk clear keeps skipped/failed reports visible and shows a partial-failure message
- [ ] Run:
  - `pnpm --dir web/research-ui test -- home-page.test.tsx`

## Task 7: Focused Regression And Real Chrome Acceptance

Verify the implementation end to end.

- [ ] Run backend focused suite:
  - `uv run pytest tests/unit/ui/test_report_cleanup_settings.py tests/unit/ui/test_report_cleanup_service.py tests/unit/ui/test_report_repository.py tests/unit/ui/test_report_history_restore.py tests/e2e/ui/test_research_ui_server.py tests/contracts/test_ui_api_contracts.py`
- [ ] Run frontend focused suite:
  - `pnpm --dir web/research-ui test -- settings-page.test.tsx settings-css-sections.test.tsx settings-enhanced-sources.test.tsx home-page.test.tsx`
- [ ] Run UI build if frontend tests pass:
  - `pnpm --dir web/research-ui build`
- [ ] Start the UI backend using the project’s normal local UI command already used in this repo, or the existing dev-server command if that is the current local path.
- [ ] Use real Chrome through Playwright MCP to verify:
  - Settings -> General shows `报告保留时间`
  - default selected value is `7 天`
  - changing to `14 天` persists after page reload
  - reset returns value to `7 天`
  - deleting a report shows the new hard-delete confirmation copy
  - clearing searched reports and all reports show the correct target/hard-delete copy
- [ ] Stop any server started for verification before final reporting.

## Stop Conditions

- Stop and ask if implementation would require deleting OpenViking vector/LevelDB files directly.
- Stop and ask if cleanup must delete shared OpenClaw state whose ownership cannot be tied to one `run-*` id.
- Stop and ask if active/sending report tracking cannot be implemented with current local state and would require changing report-send semantics.
- Stop and ask if real Chrome verification is blocked by missing Playwright MCP access after code and tests pass.

## Expected Commits

- Commit 1: backend settings and cleanup service with tests.
- Commit 2: delete endpoint and scheduler wiring with tests.
- Commit 3: frontend settings/delete UI with tests.
- Commit 4: verification/docs adjustments if needed.
