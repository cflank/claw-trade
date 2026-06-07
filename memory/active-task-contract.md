# Active Task Contract: /select B+C Columnar Storage

Date: 2026-06-05
Last updated: 2026-06-06

## Human-Approved Objective

Implement B+C for `/select`:

- Mongo is no longer the primary storage for selection market data.
- DuckDB/Parquet is the primary columnar storage for selection market data and
  selection feature inputs.
- Mongo keeps task state, run metadata, manifests, and operational records.
- `/select` startup checks data state and file integrity before selecting.
- Missing or corrupted columnar data is detected and backfilled incrementally.
- Calculation reads only the required columns and windows, and releases large
  temporary objects promptly.

## Approved Architecture Direction

Use Mongo for:

- selection run status
- refresh progress
- candidate pack refs
- columnar partition manifests
- file hashes
- provider attempt refs

Use DuckDB/Parquet for:

- A-share daily bars
- selection input rows
- feature rows used by selection strategies
- partition-level row counts and file hashes

## Allowed Changes

- Add DuckDB runtime dependency with Parquet read/write support.
- Add a columnar selection warehouse module.
- Add columnar manifest/state records.
- Add file-level sha256 integrity checks for Parquet files.
- Change `/select` refresh/data job to write and read columnar data.
- Keep Mongo as metadata/status storage.
- Add focused unit/integration tests.
- Run real Chrome `/select` acceptance after implementation.

## Forbidden Changes

- Do not keep Mongo as the primary selection market-data warehouse.
- Do not present a Mongo memory reduction as B+C completion.
- Do not silently fall back to full Mongo reads when columnar data is missing.
- Do not fake columnar files or mark data complete without real file evidence.
- Do not compute by loading all 260 trading days for the whole market into one
  Python object.
- Do not bypass candidate pack integrity checks.

## Stop Conditions

Stop and ask the human if any of these occur:

- DuckDB or Parquet dependencies cannot be installed.
- The current environment blocks writing Parquet files.
- The data gateway cannot provide enough rows to build columnar partitions.
- The implementation would require keeping Mongo as primary market-data storage.
- `/select` can only pass by using a hidden fallback path.
- Real Chrome acceptance cannot prove the columnar path was used.

## Acceptance Evidence

Required before claiming completion:

- `pyproject.toml` and lockfile include DuckDB with Parquet read/write support.
- Code contains a selection columnar warehouse with partition manifest records.
- Tests prove missing Parquet files and hash mismatch are detected.
- Tests prove valid columnar partitions skip unnecessary refresh.
- Tests prove selection scoring reads from columnar rows instead of all Mongo rows.
- Mongo usage is limited to metadata/status for the B+C path.
- Real Chrome `/select <date>` completes through the columnar path.
- Final response includes approved item -> code evidence -> test evidence -> status.

## Final Reconciliation Checklist

Use this table in the final response:

| Approved item | Code evidence | Test / live evidence | Status |
| --- | --- | --- | --- |
| Mongo only metadata/status | `data_gateway/runtime.py` constructs `DatasetRepository` with `NormalizedColumnarWarehouse`; `DatasetRepository.from_database(...)` disables normal `normalized_datasets` query/iter/count reads by default; direct old-row helper names without maintenance scope were removed; destructive old-row discard is isolated in `data_gateway/maintenance/normalized_rows.py` and requires explicit confirmation. | `tests/contracts/test_data_access_boundary.py`, `tests/contracts/test_mongo_collection_contract.py`, and focused selection/report tests passed on 2026-06-06. Local deletion gate: dry-run matched 2,063,918 rows; confirmed discard deleted 2,063,918 rows; post-check dry-run matched 0 rows. Real Chrome was re-run after deletion: `/select` workflow `select-20260606T181132-req-1780769492459-2d47262743d9e`; `/report` run `run-20260606-181625-d24f24ba`. | met for local data-layer code, deletion gate, and post-deletion Chrome acceptance. |
| DuckDB/Parquet primary selection data | `data_gateway/warehouse/selection_columnar.py`, `data_gateway/selection_integrity.py`, `data_gateway/refs.py`, public `data_gateway/selection_api.py`, and private `data_gateway/_selection_batch.py` route selection refs and feature reads through data-layer columnar/catalog interfaces. Existing old-cache manifests are not rewritten in place because their SHA-256 is integrity evidence; load/read boundaries normalize old refs before exposing lineage. | Latest real Chrome `/select` evidence: `runs/selection/workflows/select-20260606T181132-req-1780769492459-2d47262743d9e/selection-workflow-evidence.json` completed with candidate decisions. Focused regression: `tests/unit/selection/test_columnar_warehouse.py`, `tests/integration/selection/test_data_job_pipeline.py`, `tests/integration/selection/test_select_command_chat_flow.py`, and `tests/unit/data_gateway/test_normalized_columnar_warehouse.py`. | met for the current `/select` product path. |
| Incremental missing-data backfill | `selection/data_job.py`, `selection/store.py`, public `data_gateway/selection_api.py`, and private `data_gateway/_selection_batch.py` request readiness/backfill through data-layer APIs instead of upper-layer storage reads. Legacy data-job evidence restore was removed; restore now uses persisted `store/data-runs` records only. | Real Chrome `/select` reached `waiting_report_confirmation` for trade date `2026-06-05`; `/report 688017.SH` data prefetch completed through DataAPI and generated `runs/run-20260606-181625-d24f24ba/reports/final-report.md`. Prefetch logs showed one-symbol provider requests and bounded warehouse rows, not whole-market reload. | met for code path and post-deletion Chrome run. |
| File-level hash detects deletion/corruption | `data_gateway/warehouse/selection_columnar.py` and `data_gateway/warehouse/normalized_columnar.py` verify manifest file existence and whole-file SHA-256 before reading. | `tests/unit/selection/test_columnar_warehouse.py` and `tests/unit/data_gateway/test_normalized_columnar_warehouse.py` cover missing/tampered files and hash mismatch. | met at unit/data-layer level. |
| Calculation avoids full-market all-window memory load | Private `data_gateway/_selection_batch.py` uses strategy-derived field projection and `_SELECTION_FEATURE_ROW_LIMIT`; `data_gateway/warehouse/normalized_columnar.py` pushes requested row fields into DuckDB JSON extraction; repository/warehouse propagate `fields`; `DatasetRepository._normalized_columnar_manifests(...)` queries `dataset_manifests` by storage/status/dataset/market/date instead of full collection scan; report frontline default is `serial`. | Focused tests passed: `test_selection_local_feature_rows_stop_at_explicit_row_limit`, normalized-columnar projection tests, and `test_columnar_manifest_lookup_uses_targeted_query_not_full_scan`. Live memory during latest Chrome `/select` stayed in hundreds of MB per service; latest `/report` had one gateway process and completed without multi-GB project memory spike. | met for tested `/select` and `/report` runs; keep profiling as a regression requirement. |
| Selection PM ticker integrity | `selection/controller.py` rejects candidate-pool-outside tickers; it only canonicalizes an out-of-set ticker when the same PM line explicitly says the stock code should be an allowed candidate ticker and that allowed ticker matches the row company name. | `tests/unit/selection/test_decision_parser.py` covers explicit correction for `603645.SH` -> `002636.SZ` and proves uncorrected out-of-set tickers still fail. Latest Chrome `/select` evidence has no `603645.SH` or `selection_result_invalid` hit. | met for the current parser boundary. |
| Chrome `/select` passes | UI submitted `/select` through real Chrome at `http://172.27.36.34:5175/`; no console errors; refresh snapshot cleared after completion. | `runs/selection/workflows/select-20260606T181132-req-1780769492459-2d47262743d9e/selection-workflow-evidence.json` status `completed`, reason `waiting_report_confirmation`; approved enter-report symbols were `688017.SH`, `600869.SH`, `688146.SH`. | met for post-deletion local runtime acceptance. |
| Chrome `/report` after `/select` passes | UI submitted `/report 688017.SH`, clicked confirmation, and watched task card to completion. | `runs/run-20260606-181625-d24f24ba/state.json` status `completed`; `reports/export-result.json` status `passed`; final report exists at `reports/final-report.md`; Chrome page showed `688017.SH 报告 2026/6/6 14:28:28 完整报告已生成`; Chrome console errors/warnings were 0. | met for post-deletion local runtime acceptance. |
