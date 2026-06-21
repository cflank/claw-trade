# Report Retention Cleanup Design

## Summary

Add a report retention cleanup mechanism so local report runs do not grow until
the disk fills. The default retention is 7 days. The Settings page exposes a
single General setting with three choices: 7 days, 14 days, and 1 month.

Existing History Rail actions change from "hide from UI" to hard deletion:

- Deleting one report deletes that report run's local and runtime artifacts.
- Clearing reports deletes every report currently targeted by the clear action.
  If a search is active, this means the searched reports. If no search is active,
  this means all currently listed saved reports.

This design was approved by the user on 2026-06-21.

## Current Facts

- `ReportRepository.delete_saved_report()` currently removes in-memory report
  state, deletes known PDF artifacts, and writes a tombstone to
  `runs/.ui-deleted-reports.json`.
- `restore_completed_workflow_reports()` restores completed runs from
  `runs/<run_id>/state.json` and `runs/<run_id>/reports/final-report.md` unless
  the run id is tombstoned.
- Completed run data also exists outside the UI repository:
  - `runs/<run_id>/`
  - `.runtime/dev-services/openviking/data/viking/default/resources/workflow/<run_id>/`
  - `.runtime/dev-services/openclaw-state/agents/*/sessions/*.jsonl`
  - `.runtime/dev-services/openclaw-state/delivery-queue*`
- OpenClaw identity, devices, WeChat login state, and npm runtime dependencies
  are shared runtime state and must not be deleted by report cleanup.

## Goals

- Keep disk usage bounded without requiring users to manually inspect runtime
  directories.
- Make the Settings page control report retention with only the requested three
  values.
- Make existing single-delete and clear actions truly remove the report run data
  they target.
- Preserve safety: never delete active, queued, or in-flight-send report runs.
- Keep the implementation narrow and local to the UI/backend report management
  surface.

## Non-Goals

- No new general-purpose storage manager.
- No arbitrary retention value input.
- No cleanup of OpenClaw installation dependencies such as `npm/node_modules`.
- No deletion of WeChat login/session state.
- No direct mutation of OpenViking LevelDB/vector store files while OpenViking is
  running.

## User-Facing Design

### Settings

Add a control under `设置 -> 通用`:

- Label: `报告保留时间`
- Options:
  - `7 天`
  - `14 天`
  - `1 个月` (stored as 30 days)
- Default: `7 天`

The setting persists across restarts. Resetting settings returns retention to
7 days.

### History Delete

The existing per-report `删除` action hard-deletes the run behind that report.
The confirmation copy must say that it deletes the report, chart files, runtime
evidence, worker output, and related local cache for that run.

### History Clear

The existing `清空` action keeps its current target selection:

- Search active: clear searched reports.
- No search: clear all listed saved reports.

The backend hard-deletes every targeted run. Partial failures are reported; the
UI must not claim full success if any targeted run fails to delete.

## Backend Design

### Settings Storage

Add a small report cleanup settings record:

```text
reportRetentionDays: 7 | 14 | 30
```

Use the existing UI settings persistence pattern instead of a new config system.
If no saved value exists, return `7`.

### Cleaner Service

Add one backend service responsible for cleanup planning and execution. It takes:

- `run_root`
- OpenViking runtime data root
- OpenClaw state root
- current queue/task state
- retention days

It exposes two operations:

- `cleanup_expired_reports(retention_days)`: hard-delete old inactive runs.
- `delete_report_runs(run_ids)`: hard-delete explicitly selected runs.

The service returns a structured result:

```text
deletedRunIds
skippedRunIds
failedRunIds
deletedBytesApprox
userMessage
```

### Run Eligibility

A run can be hard-deleted only if:

- The run id is non-empty and matches a local run id shape such as `run-*`.
- The resolved path stays under the configured run/runtime root.
- The run is not running, queued, stopping, or currently sending a file.
- The run is either older than the retention cutoff or explicitly selected by
  the user.

Automatic retention age is based on report completion time: use
`state.updated_at` from `runs/<run_id>/state.json` first, then `state.created_at`,
then the run directory mtime only if the state timestamps are missing or invalid.
Explicit user deletion does not use the retention cutoff, but it still must pass
the active-run protection checks.

### Deletion Scope

For each eligible run id, delete:

- `runs/<run_id>/`
- `.runtime/dev-services/openviking/data/viking/default/resources/workflow/<run_id>/`
- OpenClaw agent session JSONL files that are uniquely tied to the run id
- delivery queue entries that reference the run id
- tombstone entries for that run id

The `runs/<run_id>/` deletion removes the final Markdown report, generated PDFs,
charts/assets, state, provider payload capture, tool calls, guard results,
receipts, raw worker output, and workflow evidence.

Delivery queue cleanup must not delete a shared queue file wholesale unless the
file is uniquely for that run. If queue storage is shared, remove only the
matching per-run record or skip the queue cleanup with a warning if safe record
rewriting is not supported.

### OpenViking Vector Index

Do not delete `.ldb` vector store files directly while OpenViking is running.
The cleaner should remove the workflow resource directory for the run. If
OpenViking has a safe resource-delete or reindex API available at implementation
time, use that API so the index no longer returns deleted content. If no safe
API exists, keep direct vector-store pruning out of the first implementation and
report this as a known limitation in the cleanup result.

### Scheduled Cleanup

Run automatic cleanup:

- Once after the UI backend starts.
- Then once every 24 hours while the UI backend remains alive.

Automatic cleanup uses the saved retention setting. It must log a compact
summary and must not interrupt normal UI startup if cleanup fails.

### Manual Deletion

The existing delete and clear endpoints should call the same cleaner service.
This prevents UI delete from diverging from automatic retention cleanup.

## Frontend Design

Update `SettingsSections` under the General tab:

- Add the retention select.
- Load the current value with the existing settings load path or a small
  dedicated cleanup-settings endpoint.
- Save the selected value with a dedicated save endpoint or existing settings
  save flow.

Update `HistoryRail`/home behavior:

- Keep existing button placement.
- Update confirmation copy to make hard deletion explicit.
- Update success/error handling for partial clear failures.

## Error Handling

- If a selected run is active, skip it and tell the user it was not deleted
  because it is still running or sending.
- If a filesystem deletion fails, keep going for other selected runs and return
  partial failure.
- If OpenViking index pruning is unavailable, still delete file resources and
  report the index limitation in logs/result.
- Never follow symlinks outside the configured cleanup roots.
- Never delete runtime roots wholesale; delete only per-run paths.

## Testing

Add focused backend tests:

- Default retention is 7 days when no setting exists.
- Only 7, 14, and 30 are accepted.
- Expired inactive runs are deleted.
- Recent runs are kept.
- Running/queued/sending runs are skipped.
- Hard delete removes `runs/<run_id>` and OpenViking workflow resources.
- Hard delete removes matching OpenClaw session and delivery queue files.
- Path traversal and non-run ids are rejected/skipped.
- Clear reports returns partial failure when one run cannot be deleted.

Add frontend tests:

- Settings General tab shows `报告保留时间` with 7/14/1-month options.
- Default UI selection is 7 days.
- Saving the selection calls the expected API.
- Delete confirmation mentions hard deletion of report data and runtime evidence.
- Clear confirmation mentions hard deletion for all targeted reports.

Final UI acceptance should use real Chrome to verify the setting appears under
`设置 -> 通用` and delete/clear show the new hard-delete copy.

## Acceptance Criteria

- A fresh environment defaults to 7-day retention.
- User can choose only 7 days, 14 days, or 1 month in Settings -> General.
- Deleting one report removes that run's report directory and associated runtime
  artifacts covered by this design.
- Clearing reports hard-deletes the targeted report runs.
- Automatic cleanup removes expired inactive runs on startup and daily.
- Active/queued/sending runs are not deleted.
- WeChat login state, OpenClaw identity, device state, config, and npm
  dependencies remain intact.
