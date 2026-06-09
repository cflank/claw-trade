# Crypto History Columnar Smoke Evidence - 2026-06-07

Purpose: prove the CRYPTO prepackaged importer now uses the unified data-layer columnar normalized path instead of writing normalized bars into Mongo rows.

Scope:

- Source package: existing local Binance public data package for `SOLUSDT` spot `1d` on 2026-06-01.
- Mongo database: `claw_trade_crypto_history_columnar_smoke2_20260607`.
- Columnar root: `.runtime/crypto-history-columnar-smoke2/normalized`.
- This is a smoke proof for the current code path, not a full re-import of the 2025-06-01 to 2026-06-06 all-active batch.

Command:

```bash
uv run python scripts/crypto/import_crypto_prepackaged_to_mongo.py \
  --spot-root .runtime/crypto-history-full/binance \
  --trade-date 2026-06-07 \
  --import-run-id crypto-columnar-smoke2-20260607 \
  --start-date 2026-06-01 \
  --end-date 2026-06-01 \
  --symbol SOL \
  --market-segment spot \
  --interval 1d \
  --mongo-uri mongodb://127.0.0.1:27017/claw_trade_crypto_history_columnar_smoke2_20260607 \
  --columnar-root .runtime/crypto-history-columnar-smoke2/normalized \
  --output-json .runtime/crypto-history-columnar-smoke2/import-result.json
```

Result:

- Command exit code: 0.
- `normalized_storage`: `parquet`.
- `catalog_storage`: `mongo`.
- Imported package count: 1.
- Imported unique row count: 1.
- Imported dataset ref: `dataset:daily_bar:CRYPTO:spot:SOLUSDT:daily:2026-06-01:2026-06-01`.

Mongo readback:

```text
normalized 0
raw 1
attempts 1
manifests 2
parquet_manifests 1
seed_manifests 1
```

Columnar file:

```text
.runtime/crypto-history-columnar-smoke2/normalized/market=CRYPTO/dataset=daily_bar/granularity=daily/partition-7f7cb12bffc14871.parquet
```

Warehouse readback through the unified interface:

```text
ready ('dataset:daily_bar:CRYPTO:spot:SOLUSDT:daily:2026-06-01:2026-06-01',) 81.28000000 1
```

Interpretation:

- Mongo no longer receives normalized CRYPTO bar rows on this import path.
- Mongo still stores raw metadata, provider attempt, Parquet catalog manifest, and seed import manifest.
- The upward query path remains `Warehouse` / `DatasetRepository`; callers do not need a CRYPTO-specific reader.

Known limit:

- The prior all-active import in `claw_trade_crypto_history_full_20260607` remains a historical Mongo normalized import until rerun or migrated with the current columnar-enabled path.
