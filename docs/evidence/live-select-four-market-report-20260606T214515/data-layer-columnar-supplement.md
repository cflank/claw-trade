# Data Layer Columnar Supplement

Local run window: 2026-06-06 evening America/Toronto. Runtime run IDs use UTC 2026-06-07 timestamps.

## Verdict

Previous data-layer reporting was incomplete. It described Mongo/provider attempts but omitted the columnar warehouse layer.

Plain meaning:

- Mongo is the evidence ledger and manifest index.
- Report normalized data entities are stored in Parquet under the normalized columnar warehouse.
- `/select` uses a separate selection columnar warehouse for full-market snapshots and feature rows.
- OpenViking materializes approved report/pack artifacts after the data layer has produced refs and packs.
- OpenClaw provider payloads prove worker prompts/tool schemas, not data warehouse storage by themselves.

## Store Roles

| Store / runtime layer | Role in this validation | Evidence |
| --- | --- | --- |
| Mongo `provider_attempts` / raw refs | Provider attempt ledger, raw evidence refs, rate/cache/singleflight state | `provider-attempt-window.json` files |
| Mongo `dataset_manifests` | Manifest index for normalized columnar Parquet partitions | queried with `storage=parquet,status=active` |
| Normalized columnar warehouse | Report-path normalized dataset storage | `.runtime/dev-services/data-gateway/normalized` |
| Selection columnar warehouse | `/select` full-market daily bars and deterministic feature rows | `.runtime/selection/columnar` |
| OpenViking | Approved material storage/export layer | `openviking/*`, `.ovpack`, approved manifests |
| OpenClaw | Worker runtime, provider prompt/tool schema capture | `provider-request.json`, `visible-tools.json`, `tool-calls.json` |

## Code Path Evidence

The report data API is built with a normalized columnar warehouse:

- `src/claw_trade/data_gateway/runtime.py:44-49` constructs `DatasetRepository.from_database(..., normalized_columnar=NormalizedColumnarWarehouse.from_env())`.
- `src/claw_trade/data_gateway/warehouse/normalized_columnar.py:41-51` defaults the columnar root to `.runtime/dev-services/data-gateway/normalized` when `DATA_GATEWAY_COLUMNAR_ROOT` is unset.
- `src/claw_trade/data_gateway/warehouse/repository.py:609-617` writes normalized records to columnar Parquet when `_normalized_columnar` exists, then writes the manifest index.
- `src/claw_trade/data_gateway/warehouse/repository.py:668-704` looks up active Parquet manifests through Mongo `dataset_manifests`.
- `src/claw_trade/data_gateway/warehouse/repository.py:800-828` reads normalized rows from columnar first and returns empty instead of silently falling back to old Mongo rows when fallback is disabled.

The `/select` data job has its own selection columnar path:

- `src/claw_trade/data_gateway/_selection_batch.py:239-258` loads a valid selection columnar manifest before fetching provider data.
- `src/claw_trade/data_gateway/_selection_batch.py:689-709` returns `warehouse_check_ref`, `columnar_manifest_ref`, and manifest sha from the selected columnar manifest.

## `/select` Columnar Evidence

Run:

- Selection data run: `sel-live-20260606T214515-v2`
- Workflow: `select-20260607T014714-select-live-20260606T214515-v2`
- Data run evidence: `runs/selection/2026-06-05/sel-live-20260606T214515-v2.json`
- Store record: `runs/selection/store/data-runs/sel-live-20260606T214515-v2.json`

Confirmed columnar fields:

- `warehouse_check_ref`: `warehouse-check://selection-columnar/CN_A/CN_A/2026-06-05`
- `columnar_manifest_ref`: `columnar://selection/market_data/market=CN_A/profile=CN_A/trade_date=2026-06-05/manifest.json`
- `columnar_manifest_sha256`: `a3e74a5e604ac82f724fa2b9cbef072db13563b6178c8dbec119434dbe1d8cc0`

Physical selection columnar directory:

- `.runtime/selection/columnar/market_data/market=CN_A/profile=CN_A/trade_date=2026-06-05`
- Size: `113M`
- Daily partitions: `38`
- Daily rows: `1,854,035`
- Feature partitions: `6`
- Feature rows: `5,373`
- Coverage status in manifest: `verified`
- Coverage gap code: `selection_batch_rows_dropped`
- Provider attempt in manifest: `attempt:cn_a_primary:daily_bar_by_trade_date:b3649efb3396`

Correct `/select` data flow:

```text
provider attempt / raw evidence
-> normalized refs and feature calculation
-> selection columnar Parquet manifest
-> candidate pack material
-> OpenViking approved material
-> OpenClaw selection workers
-> select-reader-report.md
```

The earlier summary line saying warehouse behavior could not be fully proven is wrong for `/select`. `/select` has explicit columnar manifest and hash proof.

## Four-Market `/report` Columnar Evidence

The four report runs produced `report-prefetch.json` files. I matched every prefetch `dataset_ref` against active Mongo `dataset_manifests` with `storage=parquet,status=active`.

| Market | Run ID | Prefetch refs | Active columnar manifests | Matched manifests | Matched refs | Conclusion |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| CN_A | `run-20260607-015125-f929a61d` | 1009 | 414 | 14 | 1009 | all prefetch dataset refs backed by active Parquet manifests |
| US | `run-20260607-020702-488c55ce` | 582 | 15 | 14 | 582 | all prefetch dataset refs backed by active Parquet manifests |
| HK | `run-20260607-021545-08c995e7` | 553 | 8 | 5 | 553 | all prefetch dataset refs backed by active Parquet manifests |
| CRYPTO | `run-20260607-021913-3ca93bc2` | 244 | 18 | 15 | 244 | all prefetch dataset refs backed by active Parquet manifests |

Matched refs by market and dataset:

| Market | Matched dataset refs by dataset |
| --- | --- |
| CN_A | `daily_bar=486`, `intraday_bar=243`, `capital_flow=243`, `company_news=6`, `macro_news=11`, `official_filing=7`, `financial_statement=4`, `financial_metric=4`, `quote_snapshot=1`, `valuation_metric=1`, `social_signal=3` |
| US | `daily_bar=251`, `company_news=54`, `macro_news=50`, `macro_series=127`, `official_filing=96`, `quote_snapshot=1`, `financial_metric=1`, `valuation_metric=1`, `social_signal=1` |
| HK | `daily_bar=492`, `company_news=22`, `macro_news=38`, `valuation_metric=1` |
| CRYPTO | `daily_bar=185`, `intraday_bar=42`, `crypto_derivative_metric=2`, `crypto_onchain_metric=7`, `company_news=2`, `quote_snapshot=1`, `order_book_snapshot=1`, `valuation_metric=1`, `defi_metric=1`, `macro_news=1`, `social_signal=1` |

Physical normalized columnar root:

- `.runtime/dev-services/data-gateway/normalized`
- Size: `5.2M`
- Observed per-market Parquet partitions exist for `CN_A`, `US`, `HK`, and `CRYPTO`.

Correct `/report` data flow:

```text
report prefetch request
-> QueryPlanner decides requested datasets
-> Warehouse checks Mongo dataset_manifests for active Parquet manifests
-> NormalizedColumnarWarehouse reads/writes Parquet rows
-> Execution gate/cache/rate/singleflight decides whether providers are called
-> Provider fetch records provider_attempt/raw refs in Mongo
-> Ingest writes normalized Parquet partitions and updates Mongo manifest index
-> report data pack material
-> OpenViking approved material
-> OpenClaw worker pack tool calls
-> final report export
```

This is not a Mongo-only flow. Mongo is the index and audit trail; normalized report records are backed by Parquet manifests.

## Remaining Evidence Limit

The current report evidence still does not emit one single per-request timeline that says:

```text
request_id -> query planner -> warehouse manifest hit/miss -> exact Parquet manifest -> provider selector -> provider attempt
```

So the corrected statement is:

- Proven: report runtime is wired to normalized columnar; all prefetch dataset refs from the four runs are backed by active Parquet manifests; physical Parquet files exist.
- Not fully proven by current exported evidence: an ordered per-request hit/miss timeline for every warehouse lookup before provider fetch.

This is an evidence-reporting gap, not proof that the system is Mongo-only.

## HK Failure Reframed

HK did not fail because columnar data was absent. HK had all `553` prefetch dataset refs backed by active Parquet manifests for the domains it prefetched.

HK failed because the prefetch material only covered:

```text
market, fundamental, news
```

but the workflow still woke `social_analyst`, which called:

```text
claw_get_social_pack
```

The HK manifest had no `social` domain material, so the social pack tool failed at frontline. This is a domain coverage mismatch between report prefetch and the worker/tool policy.

## Required Follow-Up

1. Future four-market validation must run or add a data-layer full-chain probe that records per-request `warehouse manifest hit/miss`, `columnar manifest_ref`, `provider selector`, `provider attempt`, and final material refs.
2. HK profile must either prefetch approved social material or not expose/wake `social_analyst` with a social-pack dependency for HK.
3. `summary.md` must keep Mongo and columnar separate: Mongo is ledger/index; Parquet columnar is normalized data storage.
