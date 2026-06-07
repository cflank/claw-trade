# Unified Data Layer Implementation Plan

Date: 2026-06-05

## Human Decision

Approved boundary:

- `/report`, `/select`, UI, workers, tools, and upper Python orchestration must not directly read market/fundamental/news/social data from Mongo, Parquet, DuckDB, provider SDKs, provider HTTP, or local data files.
- All data requests must go through the unified data layer interface.
- Mongo remains allowed for configuration, state, catalog, manifest, hash, audit, rate-limit, cache index, and task records.
- Parquet/DuckDB becomes the primary store/query engine for normalized market/fundamental/news/social data.
- OpenViking stores worker/report materials. It is not the market-data warehouse.
- Existing Mongo normalized row data does not need to be retained as a backup after successful migration.
- `/report` business flow, worker order, prompts, report shape, and PM authority must not change. Its data source may change behind `DataAPI/DataService`.
- The allowed `/report` change is only this: the same semantic data request enters `DataAPI/DataService`. `/report` must not choose storage, choose provider, run backfill policy, construct warehouse/provider internals, or change worker-visible material.

Plain Chinese:

`/report` 和 `/select` 表面流程不变；它们仍然“要数据”。但从哪里读、缺了怎么补、校验谁、用 Mongo 还是 Parquet，全部由数据层决定。`/report` 只说“我要这些数据”，不能自己决定“去 Mongo 读、去 Parquet 读、去 provider 拉”。

## Target Architecture

```text
/report
/select
UI
workers/tools
upper Python orchestration
        |
        v
Unified DataAPI / DataService
        |
        +-- Mongo catalog/state/manifest/hash/settings/audit
        |
        +-- DuckDB query engine
        |       |
        |       v
        |   Parquet normalized warehouse
        |
        +-- provider fetch/ingest/backfill
```

Rules:

1. Upper layers may ask for data only by market, symbol/universe, dataset type, fields, and date range.
2. Upper layers must not know whether the answer came from Mongo, Parquet, cache, or provider.
3. Dataset references must be storage-neutral. Business code must not depend on `mongo://normalized_datasets/...`.
4. File integrity checks are data-layer work.
5. OpenViking material reads are allowed only for approved worker/report/candidate-pack material, not for raw market data.
6. Only data-layer code may read Mongo, Parquet, DuckDB, provider SDKs, provider HTTP, local data files, dataset manifests, file hashes, and provider payload records.

## Target Storage Map

All business consumers in this table are served through `DataAPI/DataService`. The table does not grant direct read permission to `/report`, `/select`, UI, workers, tools, or upper Python orchestration.

| Data | Target storage | Business consumers served via DataAPI | Notes |
| --- | --- | --- | --- |
| Data-source settings | Mongo | Settings UI, credential resolver, provider selector | Configuration, not market data. |
| Data-source connection probes | Data-layer health/probe facade; Mongo stores only probe state if needed | Settings UI | UI asks for a probe result. It must not import provider SDKs or call provider HTTP endpoints directly. |
| API tokens/secrets | Mongo secret store | Provider credential resolver | Existing secret path can stay. |
| Report task state | Mongo or existing task store behind UI service | UI, scheduler, report queue | State only. |
| Selection task state | Mongo or unified task store | UI, `/select`, scheduler | Current local JSON should migrate or be wrapped behind state interface. |
| Dataset catalog | Mongo | DataService, Warehouse | Storage-neutral dataset refs, partition refs, coverage ranges. |
| Parquet partition manifest | Mongo | DataService, Warehouse | Paths, row count, fields, min/max date, SHA-256. |
| File hashes | Mongo | Integrity checker, startup check, incremental check | File-level hash, not row-level CRC. |
| Provider attempts | Mongo | DataService, audit, report evidence | Keep as metadata/audit. |
| Raw payload metadata | Mongo | Audit/debug | Raw body policy remains provider/license dependent. |
| Provider result cache index | Mongo | DataService | Cache key and metadata only. |
| Rate limits | Mongo | Provider execution layer | State only. |
| Single-flight locks | Mongo | DataService | Prevent duplicate fetch. |
| Maintenance jobs | Mongo | Data maintenance scheduler | Jobs and status only. |
| Daily bars | Parquet/DuckDB | `/report`, `/select` | Normalized rows. |
| Intraday bars | Parquet/DuckDB | `/report` | Normalized rows. |
| Quote snapshots | Parquet/DuckDB | `/report`, UI | Normalized rows. |
| Financial statements | Parquet/DuckDB | `/report` fundamental analyst | Normalized rows. |
| Financial metrics | Parquet/DuckDB | `/report` fundamental analyst | Normalized rows. |
| Valuation metrics | Parquet/DuckDB | `/report` fundamental analyst | Normalized rows. |
| Company news | Parquet/DuckDB | `/report` news analyst | Normalized rows; long bodies may be material refs if needed. |
| Macro news | Parquet/DuckDB | `/report` news analyst | Normalized rows. |
| Social signals | Parquet/DuckDB | `/report` social analyst | Normalized rows. |
| Official filings / regulatory announcements | Parquet/DuckDB metadata plus approved source-material refs | `/report` fundamental/news/policy analysis | DataAPI owns retrieval, source refs, and licensing policy. Workers see approved material or summaries only. |
| Source documents and filing bodies | OpenViking approved material or data-layer source-material refs, depending on license | `/report` workers through approved material | Raw provider payloads, normalized rows, manifests, hashes, and protocol envelopes are not worker-visible material. |
| Earnings transcripts | Parquet/DuckDB metadata plus approved source-material refs | `/report` fundamental/news analysis | Full text is exposed only after approval/materialization policy. |
| Corporate actions | Parquet/DuckDB | `/report`, `/select` | Normalized rows. |
| Analyst estimates / target-price source refs | Parquet/DuckDB metadata plus approved source refs | `/report` fundamental analyst, PM when supported | Unsupported target-price claims still hard fail. |
| Capital flow | Parquet/DuckDB | `/report`, `/select` | Normalized rows. |
| Sector data | Parquet/DuckDB | `/report`, `/select` | Normalized rows. |
| Event calendar | Parquet/DuckDB | `/report` | Normalized rows. |
| Selection features | Parquet/DuckDB | `/select` | Can be derived from normalized warehouse; selection-specific feature partitions allowed under data layer. |
| Candidate-pack material | OpenViking or approved material path | Selection workers, UI | Material, not market-data warehouse. |
| Worker reports | OpenViking | Downstream workers, exporter | Approved L1 material. |
| Final report Markdown/PDF/charts | OpenViking plus report export storage | UI, export, notification | Material/output. |
| Run evidence | `runs/**` | Debug/audit | Local evidence; not normal market-data access. |

## New Data Reference Policy

Old business-facing refs to remove:

```text
mongo://normalized_datasets/...
normalized://mongo/normalized_datasets/...
attempt://mongo/provider_attempts/...
```

Target business-facing refs:

```text
dataset://normalized/{market}/{dataset}/{granularity}/{stable_id}
dataset-partition://normalized/{market}/{dataset}/{granularity}/{partition_id}
attempt://data-provider/{attempt_id}
catalog://dataset/{dataset_id}
```

Storage details such as Mongo collection names, Parquet paths, and file hashes stay in the Mongo catalog/manifest and are resolved only by data-layer code.

## Module Changes

### 1. Data Layer Factory

Add a single factory under `src/claw_trade/data_gateway/`, for example:

```text
src/claw_trade/data_gateway/runtime.py
```

Responsibilities:

- Open Mongo settings/catalog connection.
- Build `DatasetRepository` or its replacement.
- Build provider registry.
- Build credential resolver.
- Build `DataService`.
- Return `DataAPI`.

Required outcome:

- `reports/data_pack_bridge.py` no longer opens Mongo directly.
- Public selection callers use `data_gateway/selection_api.py`; the private `data_gateway/_selection_batch.py` implementation no longer owns separate service construction.
- UI/report/select callers receive or call the same data-layer factory.
- Report/select/UI code must not construct `DatasetRepository`, `Warehouse`, `ProviderRegistry`, Mongo clients, Mongo settings stores, provider SDK clients, or provider HTTP clients.

### 2. Columnar Warehouse Moves Into Data Layer

Previous file:

```text
src/claw_trade/selection/columnar_warehouse.py
```

Phase 2 start status:

```text
src/claw_trade/data_gateway/warehouse/columnar.py
```

Implemented path:

```text
src/claw_trade/data_gateway/warehouse/selection_columnar.py
```

`selection/data_job.py` and `selection/store.py` now call
`data_gateway.selection_integrity.validate_selection_columnar_manifest_ref(...)` instead of
importing the columnar warehouse implementation.

Responsibilities:

- Write normalized Parquet partitions.
- Write selection feature Parquet partitions.
- Read Parquet through DuckDB.
- Validate manifest SHA-256 and partition file SHA-256.
- Expose data-layer methods, not selection-owned storage methods.

Selection code should call methods like:

```text
data_api.get_selection_feature_rows(plan)
data_api.validate_dataset_ref(ref)
data_api.ensure_selection_data_ready(plan)
```

It should not import `duckdb` or `SelectionColumnarWarehouse`.

### 3. Warehouse Reads Switch From Mongo Rows To Parquet

Old normal read path before this migration:

```text
Warehouse -> DatasetRepository -> Mongo normalized_datasets rows
```

Target read path:

```text
Warehouse -> Mongo catalog/manifest -> DuckDB read_parquet(...) -> DataResult rows
```

Mongo keeps:

- dataset refs;
- partition refs;
- path;
- row count;
- field set;
- date range;
- SHA-256;
- provider attempt refs.

Mongo no longer keeps normalized rows as the normal read path.

### 4. Ingest Writes Parquet

Current ingest writes normalized rows into Mongo.

Target ingest:

1. Provider fetch returns raw observations.
2. Normalizer produces normalized row dicts.
3. Columnar writer writes Parquet partitions.
4. Catalog writer records dataset/partition manifest in Mongo.
5. Attempt log records provider attempt refs in Mongo.

For small metadata fields, Mongo can keep summaries, but not full normalized market/news/fundamental rows as the normal data store.

### 5. `/report` Data Pack Bridge

Phase 1 start status:

`src/claw_trade/reports/data_pack_bridge.py` now calls `data_gateway.runtime.build_data_api_from_env()` for DataAPI construction.

Target:

- Keep report request construction.
- Keep report domain behavior.
- Keep direct Mongo/warehouse/provider construction out of the report bridge.
- Call injected or factory-built `DataAPI`.
- Do not change worker prompts, worker order, or report output.

Allowed:

```text
report bridge -> DataAPI.get_data_batch(...)
```

Forbidden:

```text
report bridge -> MongoClient(...)
report bridge -> DatasetRepository.from_database(...)
report bridge -> Warehouse(...)
report bridge -> ProviderRegistry(...)
report bridge -> MongoSettingsStore(...)
```

### 6. `/select` Data Path

Previous issue:

`selection` code validated Parquet manifests directly, and the selection data fetch path exposed too much implementation detail.

Phase 1 start status:

`src/claw_trade/data_gateway/selection_api.py` is the public selection data-layer API. Its private implementation in `src/claw_trade/data_gateway/_selection_batch.py` calls `data_gateway.runtime.build_data_gateway_runtime_from_env()` for DataAPI/repository construction. It no longer opens Mongo or constructs DataService/provider internals directly.

Target:

- `/select` asks data layer to ensure current trade-date data is ready.
- Data layer checks catalog/manifest/hash.
- Data layer backfills only missing dates/partitions.
- Data layer exposes selection feature rows or feature dataset refs.
- `/select` receives feature rows/candidate-pack input through data-layer DTOs.

Forbidden:

```text
selection/store.py -> SelectionColumnarWarehouse
selection/data_job.py -> SelectionColumnarWarehouse
selection/candidate_pack.py -> mongo://normalized_datasets
```

### 7. UI Report Context

Previous issue:

`ui_backend/report_context.py` opens Mongo and summarizes warehouse collections.

Phase 1 start status:

- Added `data_gateway/report_evidence.py`.
- `ui_backend/report_context.py` now calls `data_gateway.report_evidence.summarize_data_refs(...)`.
- UI report context no longer imports `pymongo`, constructs `MongoClient`, or names warehouse collections directly.

Target:

- For approved worker/report material: read via OpenViking/material interfaces.
- For data evidence summaries: call a data-layer evidence summary API.

Forbidden:

```text
ui_backend/report_context.py -> MongoClient(...)
ui_backend/report_context.py -> db["normalized_datasets"].find_one(...)
```

### 8. UI Data-Source Runtime Checks

Phase 1 implemented status:

- Added `data_gateway/source_probe.py`.
- `ui_backend/data_source_runtime_checks.py` is now a thin compatibility wrapper.
- `web/state.py` imports `build_data_source_health_tester()` and `build_price_alert_quote_provider()` from `data_gateway.source_probe`.
- UI code no longer imports provider SDKs or calls provider HTTP endpoints for data-source connection probes.

Target:

- UI asks a data-layer probe facade to test a configured source.
- Data layer owns provider SDK/HTTP calls, rate-limit handling, credential resolution, and audit/probe evidence.
- UI receives only a probe result DTO.

Forbidden:

```text
ui_backend/data_source_runtime_checks.py -> importlib.import_module("tushare")
ui_backend/data_source_runtime_checks.py -> importlib.import_module("akshare")
ui_backend/data_source_runtime_checks.py -> urllib.request.urlopen(...)
```

Allowed separate case:

`ui_backend/llm_settings_bridge.py` may keep local runtime health probes such as OpenViking/OpenClaw localhost `/health` checks. Those are not market/fundamental/news/social data or provider data-source probes.

### 9. UI Settings Store Boundary

Phase 1 implemented status:

- Added `data_gateway/settings_store.py`.
- `ui_backend/mongo_settings_store.py` is now a thin compatibility wrapper.
- `data_gateway/runtime.py` uses `build_data_source_settings_stores(...)`.
- `web/state.py` no longer imports `ui_backend.mongo_settings_store` or constructs `MongoDataSourceStore` / `MongoSecretStore` directly.

Target:

- Web/UI composition may assemble UI services.
- Mongo collection names, settings-store classes, and secret-store construction live behind data-layer/settings-store facades.
- Provider credential resolution receives stores from the data layer.

### 10. Worker Skill Data Access

Phase 1 implemented status:

- Added `data_gateway/agent_tools.py`.
- `alphaear-stock/scripts/stock_tools.py` delegates price and fundamentals to `data_gateway.agent_tools`.
- `alphaear-stock/scripts/database_manager.py` was removed; `stock_entrypoint.py` no longer accepts `--db-path`.
- `alphaear-stock/scripts/stock_entrypoint.py` delegates CN-A news/social packs to `data_gateway.agent_tools`.
- `alphaear-techlab/scripts/market_data_provider.py` loads price frames through `data_gateway.agent_tools` and no longer dynamically loads the old stock skill runtime.
- `alphaear-techlab/scripts/techlab_entrypoint.py` calls `load_market_price_frame(...)`, not the old legacy `load_price_frame(...)` path.

Target:

- Worker tools may ask the data layer for market/fundamental/news/social data.
- Worker tools must not import provider SDKs, call provider HTTP, open local market-data files, or maintain their own SQLite market-data cache.

### 11. OpenViking Boundary

OpenViking remains responsible for:

- approved natural-language worker outputs;
- approved upstream/downstream report material;
- approved selection candidate-pack material when promoted into workflow;
- final report material;
- approved chart/image assets used by reports.

OpenViking does not become:

- the market data warehouse;
- the Parquet catalog;
- the provider cache;
- the normalized row store.
- a worker-visible store for raw market data, normalized rows, provider attempts, raw payloads, dataset manifests, URI/hash/protocol envelopes, or debug payloads.

Downstream pure-LLM workers receive approved L1 report bodies through `claw-trade` prompt variables when the baseline expects full upstream reports. They must not be instructed to read data manifests, storage refs, provider payloads, or OpenViking protocol/debug material as the main worker-facing material.

## Migration Phases

### Phase 0: Ratchet Boundary Guard

Goal: prevent new violations while existing violations are being removed.

Reason:

The current codebase already has known direct-access paths. A contract test that immediately fails on every existing hit would block the migration before it can start. The correct first test is a ratchet: record the current violations, reject new ones, and require the old list to shrink phase by phase.

Current status:

- `tests/contracts/test_data_access_boundary.py` exists.
- `tests/contracts/fixtures/data_access_boundary_known_violations.json` is now empty for guarded normal business paths.
- This proves upper-layer direct provider/storage/local-file access is blocked by contract tests.
- This does not prove Phase 4/5 Parquet normalized warehouse cutover is complete.

Changes:

- Add `tests/contracts/test_data_access_boundary.py`.
- Add a checked-in violation manifest that records current known violations.
- Each manifest item must include path/pattern, category, reason, and planned removal phase.
- New forbidden imports/access outside the manifest fail immediately.
- Existing violation count must not increase.
- When a phase removes a violation, delete that manifest item.
- Phase 6 requires the manifest to be empty for normal business paths.
- Allowlist only data-layer internals and material/evidence paths.
- Add project doc reference to `AGENTS.md` or a project rules doc if approved.

The contract must check at least:

- upper layers do not import `pymongo`, `duckdb`, provider SDKs, provider HTTP clients, or local data-file readers for market data;
- `reports/data_pack_bridge.py` does not construct `DatasetRepository`, `Warehouse`, `ProviderRegistry`, Mongo clients, Mongo settings stores, provider SDK clients, or provider HTTP clients;
- `selection/**` does not import data-layer internals directly except the approved DataAPI facade;
- worker tool scripts under `agents/*/skills/*/scripts/**/*.py` do not import provider/raw/warehouse/local data-file internals, including indirect dynamic loading of legacy data tools;
- UI route/composition code under `src/claw_trade/web/**` does not construct storage/provider internals;
- OpenClaw plugin entry files under `openclaw_plugins/**` do not add storage/provider/direct data refs;
- UI data-source runtime checks do not import provider SDKs or call provider HTTP directly;
- business refs do not generate `mongo://normalized_datasets`, `normalized://mongo/normalized_datasets`, or `attempt://mongo/provider_attempts`;
- OpenViking/material reads do not expose raw market-data refs, provider attempts, raw payloads, dataset manifests, hashes, or protocol envelopes as worker-visible material.

Verification:

```text
uv run pytest --import-mode=importlib tests/contracts/test_data_access_boundary.py -q
```

### Phase 1: DataAPI Factory

Goal: one construction path for report/select data access.

Changes:

- Add `data_gateway/runtime.py`.
- Move Mongo/DataService construction from report bridge into data layer.
- Update report bridge to call factory or injected DataAPI.
- Add `data_gateway/source_probe.py` for settings-page provider probes.
- Add `data_gateway/settings_store.py` for data-source settings/secret store construction.
- Add `data_gateway/agent_tools.py` for worker skill market/fundamental/news/social data access.
- Keep behavior unchanged.

Phase 1 implemented status:

- `reports/data_pack_bridge.py` calls `data_gateway.runtime.build_data_api_from_env()`.
- `data_gateway/selection_api.py` exposes the selection data-layer API; private `data_gateway/_selection_batch.py` calls `data_gateway.runtime.build_data_gateway_runtime_from_env()`.
- `ui_backend/report_context.py` calls `data_gateway.report_evidence.summarize_data_refs(...)`.
- `ui_backend/data_source_runtime_checks.py` is a thin wrapper over `data_gateway.source_probe`.
- `web/state.py` gets data-source settings stores through `data_gateway.settings_store`.
- `alphaear-stock` and `alphaear-techlab` worker skill scripts get data through `data_gateway.agent_tools`.

Verification:

- Existing `/report` data-pack tests pass.
- Contract test proves report bridge no longer imports or constructs storage/provider internals.
- Contract test proves guarded upper-layer direct data-access violations remain at zero.
- Real Chrome `/report` starts from the UI command, creates the report task card, and data prefetch evidence goes through DataAPI.
- Ordinary chat does not trigger report prefetch.

### Phase 2: Move Selection Columnar Storage Into Data Layer

Goal: `/select` no longer owns Parquet/DuckDB internals.

Changes:

- Move/wrap `SelectionColumnarWarehouse` into `data_gateway/warehouse`.
- Replace selection imports with data-layer interface calls.
- Keep current Parquet layout initially to avoid unnecessary churn.

Phase 2 start status:

- `SelectionColumnarWarehouse` implementation moved to `data_gateway/warehouse/selection_columnar.py`.
- `selection/data_job.py` and `selection/store.py` call the data-layer selection integrity validation facade.
- `selection/**` no longer imports `duckdb`, `read_parquet`, or the columnar warehouse implementation.
- `selection/provider_batch.py` now imports stable `data_gateway.selection_api` instead of the private `data_gateway._selection_batch` implementation.

Verification:

- `/select` unit/integration tests pass.
- Contract test proves `selection/**` no longer imports `duckdb`.
- Real Chrome `/select` completes.
- Memory evidence shows selection data checks do not load all-market/all-history rows into Python.

### Phase 3: Storage-Neutral Refs

Goal: remove Mongo collection names from business refs.

Changes:

- Introduce storage-neutral dataset refs.
- Update candidate-pack lineage and selection store validation.
- Keep backward parser only in migration tool, not normal business path.

Phase 3 start status:

- Added `data_gateway/refs.py`.
- Normal selection lineage uses `dataset://normalized/...` and `attempt://data-provider/...`.
- `candidate_pack`, `data_job`, `store`, and the private selection data-layer implementation no longer emit or accept old Mongo-shaped refs in the normal path. Legacy refs that enter the data-layer bridge are normalized before feature rows and columnar feature partitions are written.
- Backward normalization for old ref inputs exists only in `data_gateway.refs`, so old values can be converted during migration without staying in business code.
- Existing on-disk selection columnar cache manifests may still contain old Mongo-shaped refs from earlier runs. They are not rewritten in place because the manifest SHA-256 is part of the integrity evidence. The data-layer load/read boundary normalizes those refs before exposing them to selection evidence, candidate-pack lineage, or business callers; new manifest writes use storage-neutral refs.

Verification:

- Tests fail if new candidate packs contain `mongo://normalized_datasets`.
- Existing migrated refs validate through catalog API.

### Phase 4: Parquet Normalized Warehouse Capability

Goal: add the Parquet normalized warehouse without cutting over `/report` before existing data is migrated and verified.

Changes:

- Add catalog records for normalized Parquet partitions.
- Update ingest to write normalized rows to Parquet.
- Add Warehouse query/check/recheck support for catalog + DuckDB.
- Keep any temporary dual-read behavior strictly inside the data layer.
- Do not let `/report`, `/select`, UI, worker tools, or upper Python choose old/new storage.
- Mongo keeps catalog/manifest/hash/attempts.
- The normal `/report` read path must not be declared accepted until Phase 5 migration verification and real Chrome `/report` acceptance pass.

Phase 4 partial implementation status:

- Added `data_gateway/warehouse/normalized_columnar.py`.
- `DatasetRepository` can be constructed with `NormalizedColumnarWarehouse`.
- When configured with a columnar warehouse, `upsert_normalized_documents(...)` writes normalized records to Parquet and records a `dataset_manifests` entry with file SHA-256.
- `query_normalized(...)`, `iter_normalized(...)`, `count_normalized(...)`, and universe coverage aggregation use Parquet/DuckDB when a columnar warehouse is configured.
- Columnar reads are manifest-driven: the data layer selects active `dataset_manifests` entries first, verifies the referenced Parquet file exists, verifies whole-file SHA-256, then reads only the requested columns.
- If a Parquet file is missing, tampered, or unreadable, Warehouse reports `DATA_INTEGRITY_FAILED` instead of silently treating it as ordinary missing data.
- Re-ingesting the same `dataset_ref` writes a new partition and marks old active manifests as `superseded`; normal reads only use active manifests.
- Full-universe coverage checks use DuckDB aggregate metadata instead of materializing all rows in Python.
- Warehouse row reads pass the caller's requested field list through `WarehouseCheck.fields` -> `DatasetRepository.query_normalized(fields=...)` -> `NormalizedColumnarWarehouse`, so DuckDB extracts only those row fields from Parquet JSON payloads instead of loading the whole row object when a projection exists.
- Configured columnar reads do not silently fall back to old Mongo normalized rows. Missing Parquet data must surface as missing data so the data layer can backfill or repair explicitly.
- `data_gateway/runtime.py` constructs `DatasetRepository.from_database(..., normalized_columnar=NormalizedColumnarWarehouse.from_env())`.
- Database-backed repositories do not read `normalized_datasets` rows by default through the normal query/iter/count path. Direct helper names without maintenance scope were removed; old Mongo normalized row discard is isolated in `data_gateway/maintenance/normalized_rows.py` and requires explicit confirmation.
- Selection feature reads from a cached columnar manifest use an explicit strategy-derived field projection, a bounded row limit, and `fetchmany(...)` batches. They do not use `fetch_df()` or row-level `SELECT *` materialization for feature rows. The cold path that streams per-symbol history from the data layer also has an explicit feature-row cap and emits a blocker gap if that cap is exceeded.
- Phase 5/6 deletion gate has been executed for the local dev runtime: dry-run matched 2,063,918 old Mongo normalized rows, confirmed discard deleted 2,063,918 rows, and post-check dry-run matched 0 rows. Post-deletion real Chrome `/select` and `/report` acceptance also passed.

Verification:

- Report data-pack tests pass for market/fundamental/news/social.
- Tests prove Warehouse can answer the same semantic data request from Parquet partitions.
- Tests prove requested row fields are projected from Parquet reads and unrequested row fields are not returned to callers.
- Tests prove `/select` cold-path feature materialization stops at the explicit row limit and reports a blocker gap instead of continuing to accumulate rows.
- Tests prove upper layers still call DataAPI and do not know whether Mongo or Parquet answered.
- Memory check shows no full-market Mongo row load and no full-market Parquet load into Python.
- 2026-06-06 local Chrome evidence exists for the current default runtime: `/select` completed to `waiting_report_confirmation` at `runs/selection/workflows/select-20260606T070324-req-1780729404989-6eaa26f63b4db8/selection-workflow-evidence.json`, and `/report 688146.SH` completed with export guard status `passed`. The `/select` run reused the valid columnar manifest for trade date `2026-06-05`, did not start data backfill, and the right-side selection refresh snapshot stayed at completed data-ready status for `sel-run-evidence-fix-4e3250081314`. This proves the current UI flow can run through the data-layer path in this workspace; it does not close the Phase 5 migration/deletion gate.

### Phase 5: Migrate Or Discard Existing Mongo Data And Cut Over Normal Reads

Goal: move existing `normalized_datasets` rows to Parquet/catalog, verify equivalence, then switch the normal data-layer read path to Parquet.

Changes:

- Add migration command under data layer.
- Read Mongo normalized rows in bounded batches.
- Write Parquet partitions.
- Write catalog/manifest/hash.
- Verify source/target by market, dataset, granularity, and partition.
- After verification, switch normal Warehouse reads to Parquet/DuckDB.
- Temporary dual-read remains data-layer-only, explicit, logged, and closed before Phase 6.

No backup requirement:

- Per human decision, old Mongo normalized row data does not need to be retained as a backup after successful migration.
- Deletion happens only after migration verification passes.
- 2026-06-06 local deletion gate used intentional discard rather than data preservation: `scripts/selection/discard_mongo_normalized_rows.py --dry-run` matched 2,063,918 rows; `--confirm-discard-normalized-rows` deleted 2,063,918 rows; post-check dry-run matched 0 rows.

Verification:

- Migration summary includes source count, target count, partition count, date range, field set, and hash status per market/dataset/granularity/partition.
- Sample semantic queries return equivalent results before and after cutover.
- Old business ref generation fails in normal code.
- Real Chrome `/report` passes after cutover: command input, task card, data-prefetch manifest, frontline data pack using that manifest, and no fallback when manifest is missing.
- Real Chrome `/select` passes after cutover.

### Phase 6: Delete Old Normal Read Path

Goal: old Mongo normalized row path cannot be used by normal product flows.

Changes:

- Remove or disable normal `normalized_datasets` row reads.
- Keep migration/repair-only access if needed under explicit data-layer maintenance commands.
- Remove `mongo://normalized_datasets` as a normal accepted business ref.
- Any migration/repair-only command must be isolated, explicit, and not callable from `/report`, `/select`, UI normal flow, workers, tools, or scheduler auto-runs.

Verification:

- Contract tests fail on old ref generation.
- `/report` and `/select` live acceptance pass in real Chrome.
- Direct Mongo row reads from upper layers are absent.
- Data-layer normal Warehouse tests prove Mongo row read is disabled.

Current partial implementation status:

- Database-backed `DatasetRepository.from_database(...)` disables normal Mongo row reads unless `allow_normalized_mongo_read=True` is passed explicitly.
- Direct old-row helper names without maintenance scope were removed. Remaining old-row read/delete methods are named `*_for_maintenance`, and contract tests forbid production calls outside `data_gateway/maintenance/normalized_rows.py`.
- The default runtime constructs the repository with `NormalizedColumnarWarehouse.from_env()`.
- Real Chrome `/select` and `/report 688017.SH` passed in the local default runtime after the deletion gate on 2026-06-06. Latest evidence: `/select` workflow `runs/selection/workflows/select-20260606T181132-req-1780769492459-2d47262743d9e/selection-workflow-evidence.json`; `/report` run `runs/run-20260606-181625-d24f24ba`.
- Explicit Mongo row discard is isolated under `data_gateway/maintenance/normalized_rows.py` and requires explicit confirmation. The local dev runtime old `normalized_datasets` rows have been intentionally discarded and post-checked at zero.

## Migration Deletion Gate

Old Mongo normalized rows may be deleted only after all checks pass:

1. For every market/dataset/granularity/partition, source count equals target count.
2. Source and target date ranges match.
3. Source and target field sets match or an approved schema migration explains the difference.
4. Target Parquet file SHA-256 is recorded in the manifest.
5. Sample semantic queries are equivalent through DataAPI before and after cutover.
6. Old business ref generation fails in normal code.
7. Normal Mongo row read is disabled.
8. Real Chrome `/report` and `/select` pass after Mongo row read is disabled.
9. Migration/repair-only access is isolated under explicit data-layer maintenance commands.

## Memory And Query Constraints

These rules are part of the implementation contract, not optional tuning:

- Migration reads Mongo in bounded batches and writes bounded Parquet partitions.
- DuckDB queries must push date, symbol/universe, dataset, granularity, and field projection into SQL.
- `/select` feature retrieval must have row/field limits and must not materialize full-market/full-history rows in Python.
- `/report` data packs must request only the date range, symbols, datasets, and fields needed by the worker stage.
- Data-layer methods returning rows must stream or page when the result can be large.
- Avoid `fetch_df()` or equivalent whole-result materialization for all-market/all-history reads unless the result is proven bounded.
- Tests or profiling checks must record peak memory by stage using RSS, `tracemalloc`, or equivalent.
- Acceptance evidence must include peak memory for startup check, incremental backfill, `/select` feature build, and `/report` data prefetch.

## Acceptance Criteria

The migration is complete only when all are true:

| Requirement | Evidence |
| --- | --- |
| Boundary ratchet is in place and only shrinks | Contract test + violation manifest |
| Upper layers do not import `pymongo`, `duckdb`, provider SDKs, or provider HTTP for data access | Contract test |
| `/report` gets data through DataAPI only and does not construct storage/provider internals | Code review + contract tests |
| `/select` gets data through DataAPI only | Code review + contract tests |
| `selection/**` does not own DuckDB/Parquet internals | Contract test |
| `ui_backend/report_context.py` does not read Mongo warehouse collections | Contract test |
| Warehouse reads normalized rows from Parquet/DuckDB | Unit/integration tests |
| Mongo normalized row path is deleted/disabled for normal flows | Contract test |
| Existing Mongo normalized rows migrated or intentionally discarded after verified migration | Migration evidence by market/dataset/granularity/partition |
| OpenViking remains material storage, not market-data storage | Code review |
| Real Chrome `/select` passes | Playwright/Chrome evidence |
| Real Chrome `/report` passes through command input, task card, data prefetch, and report flow | Playwright/Chrome evidence |
| Ordinary chat does not trigger `/report` prefetch | Playwright/Chrome evidence |
| Data unavailable state is shown correctly without fake data or hidden fallback | UI/runtime evidence |
| Memory stays bounded during startup check, backfill, `/select`, and `/report` prefetch | Profiling output with peak memory by stage |

## Stop Conditions

Stop and ask the human if any step requires:

- changing `/report` worker order, prompts, report structure, or PM final authority;
- making workers read Mongo/Parquet/provider data directly;
- using OpenViking as market-data storage;
- keeping Mongo normalized rows as the normal read path after migration;
- fake/stub/mock data to pass UI or report flows;
- destructive deletion before migration verification;
- changing selection strategy scoring behavior while moving storage.

## Current Closure Evidence

Current local closure evidence after the deletion gate:

1. Existing local Mongo `normalized_datasets` rows were intentionally discarded after the deletion gate; post-check dry-run matched 0 rows.
2. Remaining explicit Mongo normalized row access is isolated under maintenance-only commands.
3. Real Chrome `/select` passed after the deletion gate:
   - `runs/selection/workflows/select-20260606T181132-req-1780769492459-2d47262743d9e/selection-workflow-evidence.json`
4. Real Chrome `/report 688017.SH` passed after the deletion gate:
   - `runs/run-20260606-181625-d24f24ba/state.json`
   - `runs/run-20260606-181625-d24f24ba/reports/export-result.json`
5. Separate UI regression coverage for ordinary chat and data-unavailable state remains a future regression item unless that surface is explicitly in the current acceptance scope.
