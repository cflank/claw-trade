# Data Access Boundary Audit

Date: 2026-06-05

## Boundary

Human-approved rule:

- `/report`, `/select`, UI, workers, tools, and upper Python orchestration must not directly read market/fundamental/news/social data from Mongo, Parquet, DuckDB, provider SDKs, provider HTTP, or local data files.
- They must request data through the unified data layer interface.
- Mongo, Parquet, DuckDB, provider SDKs, provider HTTP, dataset manifests, file hashes, and backfill logic are data-layer internals.
- OpenViking materials are not market-data storage. Worker outputs, approved reports, candidate-pack materials, PDFs, charts, and evidence files may be read through approved material/report interfaces.

In plain terms: callers say what data they need; the data layer decides where it lives, whether it is missing, how to backfill it, and how to verify it.

## Current Classification

### A. Must Move Behind Data Layer Interface

These paths currently touch data storage details from outside the unified data layer, or hard-code storage-specific refs in upper code.

| Path | Current behavior | Why it violates the new boundary | Required direction |
| --- | --- | --- | --- |
| None in the current ratchet scope. | `tests/contracts/fixtures/data_access_boundary_known_violations.json` is empty. | Any new upper-layer provider/storage/local-file read is now a new violation. | Keep all market/fundamental/news/social access behind data-layer facades. |

### A.1 Moved Behind Data Layer In Phase 1 Start

| Path | Current behavior | Evidence |
| --- | --- | --- |
| `src/claw_trade/reports/data_pack_bridge.py` | Calls `data_gateway.runtime.build_data_api_from_env()` and no longer opens Mongo or constructs warehouse/provider internals directly. | Covered by `tests/contracts/test_data_access_boundary.py`; old report-bridge fixture entries were removed. |
| `src/claw_trade/data_gateway/selection_api.py` / `src/claw_trade/data_gateway/_selection_batch.py` | Public callers use `selection_api`; the private implementation calls `data_gateway.runtime.build_data_gateway_runtime_from_env()` and no longer opens Mongo or constructs DataService/provider internals directly. | Covered by focused selection tests after this phase; storage-specific refs are blocked by `tests/contracts/test_data_access_boundary.py`. |
| `src/claw_trade/data_gateway/warehouse/selection_columnar.py` | Owns selection columnar DuckDB/Parquet reads, writes, manifest and SHA-256 checks. | Moved from `src/claw_trade/selection/columnar_warehouse.py`; `selection/data_job.py` and `selection/store.py` now call the data-layer `selection_integrity` validation facade. |
| Selection dataset/attempt lineage refs | Normal selection code now uses `dataset://normalized/...` and `attempt://data-provider/...` through `data_gateway.refs`. | `candidate_pack`, `data_job`, `store`, and private selection batch implementation old Mongo-shaped ref fixture entries were removed after focused tests passed. |
| `src/claw_trade/selection/provider_batch.py` | CLI/date helper module now imports stable `data_gateway.selection_api` instead of the private selection batch implementation. | Old fixture entry removed after contract tests passed. |
| `src/claw_trade/ui_backend/report_context.py` | Calls `data_gateway.report_evidence.summarize_data_refs(...)` for data evidence summaries. | UI no longer opens Mongo or names warehouse collections directly. |
| `src/claw_trade/data_gateway/source_probe.py` | Owns provider SDK/HTTP connection probes for settings-page tests. | `ui_backend/data_source_runtime_checks.py` is now a thin compatibility wrapper that imports only data-layer probe facades. |
| `src/claw_trade/data_gateway/settings_store.py` | Owns UI settings/secret Mongo store classes and `build_data_source_settings_stores(...)`. | `web/state.py` no longer imports `ui_backend.mongo_settings_store` or constructs `MongoDataSourceStore`/`MongoSecretStore` directly. |
| `src/claw_trade/data_gateway/agent_tools.py` | Provides worker skill facades for ticker search, price rows, fundamentals, news pack, and social pack. | `alphaear-stock` and `alphaear-techlab` scripts no longer import provider SDKs, provider HTTP clients, SQLite, or dynamically load legacy stock data tools. |

### A.2 Closed Migration / Deletion Gate

| Path | Current behavior | Required direction |
| --- | --- | --- |
| Normalized warehouse migration/cutover | `data_gateway/warehouse/normalized_columnar.py` supports manifest-driven Parquet writes/reads, whole-file SHA-256 verification, tamper/delete integrity failure reporting, active/superseded manifest status, DuckDB aggregate metadata checks for universe coverage, and caller field projection from `WarehouseCheck.fields` down to DuckDB JSON extraction. Configured columnar reads do not silently fall back to old Mongo normalized rows. Database-backed repositories do not read `normalized_datasets` rows by default through the normal query/iter/count path. Direct old-row helper names without maintenance scope were removed; destructive old-row discard is isolated in `data_gateway/maintenance/normalized_rows.py` and requires explicit confirmation. Existing Mongo `normalized_datasets` rows were intentionally discarded after the deletion gate: dry-run matched 2,063,918 rows, confirmed discard deleted 2,063,918 rows, and post-check dry-run matched 0 rows. Existing on-disk selection columnar cache manifests may still contain old Mongo-shaped refs; they are not rewritten in place because their manifest SHA-256 is integrity evidence. The data-layer load/read boundary normalizes those refs before exposing lineage to business callers. | Post-deletion real Chrome `/select` and `/report` acceptance passed on 2026-06-06: `/select` workflow `select-20260606T181132-req-1780769492459-2d47262743d9e`; `/report` run `run-20260606-181625-d24f24ba`. |

### B. Allowed Data-Layer Internals

These paths are allowed to touch storage/provider internals because they are data-layer implementation code.

| Path | Why allowed |
| --- | --- |
| `src/claw_trade/data_gateway/warehouse/repository.py` | Mongo repository adapter for catalog/raw/normalized/attempt/cache collections. |
| `src/claw_trade/data_gateway/warehouse/warehouse.py` | Warehouse read/check layer. Target implementation should switch normalized market/fundamental/news/social rows to Parquet/DuckDB. Mongo keeps catalog, manifest, hashes, metadata, audit, cache index, rate-limit, and task state. |
| `src/claw_trade/data_gateway/execution/managed_http.py` | Managed provider HTTP executor. |
| `src/claw_trade/data_gateway/providers/**` | Provider plugins. They may call provider SDK/HTTP through managed data-layer execution. |
| `src/claw_trade/data_gateway/ingest/**` | Raw/normalized ingest path. Target should write normalized rows to Parquet and Mongo catalog. |
| `src/claw_trade/data_gateway/execution/**` | Cache, rate limit, and single-flight internals. |
| `src/claw_trade/data_gateway/a_share_prepackaged_importer.py` | Data import utility. It should remain data-layer/import code, not report/select caller code. |
| `src/claw_trade/data_gateway/settings_store.py` | Settings/secret storage, not market data. |
| `src/claw_trade/ui_backend/mongo_settings_store.py` | Thin compatibility wrapper for old imports; implementation lives in `data_gateway.settings_store`. |
| `src/claw_trade/runtime/settings_projection.py` | Runtime settings projection, not market data. |

### C. Not Market Data: Material / Evidence Paths

These are not market-data warehouse reads. They are worker/report material, evidence, or UI export paths.

| Path | Current role |
| --- | --- |
| `src/claw_trade/selection/tools.py` | Reads approved selection candidate-pack files for selection workers. This is material access, not market-data access. |
| `src/claw_trade/selection/artifacts.py` | Writes/reads selection candidate-pack artifacts. Target should clarify OpenViking vs local evidence role. |
| `src/claw_trade/selection/controller.py` | Reads selection workflow raw outputs and PM decision artifacts. This is workflow material/evidence. |
| `src/claw_trade/workflow/runner.py` | Reads worker raw outputs and manifests. This is workflow material/evidence. |
| `src/claw_trade/reports/exporter.py` | Reads approved worker/report materials and chart assets for export. |
| `src/claw_trade/ui_backend/report_repository.py` | Stores saved report Markdown/PDF records in memory/local paths. This is report material, not market data. |
| `src/claw_trade/ui_backend/pdf_export_service.py` and `pdf_renderer.py` | Convert saved report Markdown to PDF. |
| `runs/**` | Local run evidence, selection run JSON, workflow evidence, PM decisions, and report outputs. |
| OpenViking runtime data | Approved worker/report materials. It is not the market-data warehouse. |

## First Migration Scope

Do these in order.

1. Add a ratchet data-access boundary contract test.
   - Record current known violations with path, category, reason, and planned removal phase.
   - Fail if `selection`, `reports`, `ui_backend`, `workflow`, or worker/tool modules add new imports of `pymongo`, `duckdb`, provider SDKs, provider HTTP clients, local data-file readers, or direct reads of `normalized_datasets`.
   - Existing violations may only decrease. By the final migration phase, the normal business-path violation list must be empty.
   - Allowlist data-layer internals and approved material/evidence readers only.

2. Move selection columnar storage behind the data layer.
   - `SelectionColumnarWarehouse` now lives under `data_gateway/warehouse/selection_columnar.py`.
   - `selection/data_job.py` and `selection/store.py` now call `data_gateway.selection_integrity.validate_selection_columnar_manifest_ref(...)` instead of importing the warehouse implementation.
   - `/select` should continue converging on data-layer methods such as "get selection feature rows" or "validate dataset ref", not DuckDB/Parquet directly.

3. Create one DataAPI/DataService factory inside the data layer.
   - `reports/data_pack_bridge.py` now calls `data_gateway.runtime.build_data_api_from_env()`.
   - Public selection data callers use `data_gateway/selection_api.py`.
   - `data_gateway/_selection_batch.py` is a private data-layer implementation that calls `data_gateway.runtime.build_data_gateway_runtime_from_env()` instead of constructing DataService/provider internals itself.

4. Replace UI report-context direct Mongo summaries.
   - `ui_backend/report_context.py` now asks `data_gateway.report_evidence` for data evidence summaries.
   - It no longer opens Mongo or names warehouse collections directly.

5. Move UI probes, web settings stores, and worker skill data access behind data-layer facades.
   - `data_gateway/source_probe.py` owns provider connection probes.
   - `data_gateway/settings_store.py` owns data-source settings/secret store construction.
   - `data_gateway/agent_tools.py` owns worker skill data access.
   - Boundary fixture is empty after this step.

6. Migrate normalized datasets from Mongo rows to Parquet/DuckDB.
   - Mongo keeps dataset catalog, manifest, paths, row counts, SHA-256, attempts, rate limits, and task state.
   - Parquet stores normalized market/fundamental/news/social rows.
   - DuckDB reads Parquet for `/report` and `/select`.

7. Delete old Mongo row read path.
   - Remove normal reads from `normalized_datasets`.
   - Keep only migration/repair tools until migration is complete.
   - Remove `mongo://normalized_datasets/...` as a normal business ref.

## Stop Conditions

Stop and ask the human if any migration step requires:

- changing `/report` worker order, prompts, report structure, or PM authority;
- letting `/report` or `/select` read storage internals directly "temporarily";
- keeping Mongo normalized rows as the normal read path after Parquet migration;
- using fake/mocked data to pass `/report` or `/select`;
- changing OpenViking's role from approved material storage into market-data storage.
