# CRYPTO Binance Full Columnar Import Evidence

Date: 2026-06-08

Current status: superseded for current warehouse operation. The all-quote/mixed import recorded here has been replaced by the USDT-only effective warehouse in `docs/evidence/crypto-history-usdt-only-cleanup-20260608.md`.

Purpose: prove the Binance current-active-symbol Phase 1 historical package batch has a final unified columnar normalized import, not only earlier Mongo-backed proof. Mongo is now used only as catalog/evidence storage for this batch.

Scope:

- Binance current active spot symbols from exchangeInfo.
- Binance current active USD-M perpetual symbols from exchangeInfo.
- Intervals: `1d`, `1h`.
- Date range: 2025-06-01 through 2026-06-06.
- Source packages: previously downloaded local ZIP + `.CHECKSUM` files under `.runtime/crypto-history-full/binance`.
- Columnar root: `.runtime/crypto-history-full/normalized-columnar-final`.
- Mongo catalog database: `claw_trade_crypto_history_full_20260607`.
- Import run id: `crypto-full-columnar-final-20260608`.

Command:

```bash
uv run python scripts/crypto/import_crypto_prepackaged_to_mongo.py \
  --spot-root .runtime/crypto-history-full/binance \
  --futures-root .runtime/crypto-history-full/binance \
  --trade-date 2026-06-07 \
  --import-run-id crypto-full-columnar-final-20260608 \
  --start-date 2025-06-01 \
  --end-date 2026-06-06 \
  --symbol-manifest .runtime/crypto-history-full/manifests/spot-1d-2025-06_2026-05.json \
  --symbol-manifest .runtime/crypto-history-full/manifests/spot-1h-2025-06_2026-05.json \
  --symbol-manifest .runtime/crypto-history-full/manifests/spot-1d-2026-06-daily.json \
  --symbol-manifest .runtime/crypto-history-full/manifests/spot-1h-2026-06-daily.json \
  --symbol-manifest .runtime/crypto-history-full/manifests/usdm-1d-2025-06_2026-05.json \
  --symbol-manifest .runtime/crypto-history-full/manifests/usdm-1h-2025-06_2026-05.json \
  --symbol-manifest .runtime/crypto-history-full/manifests/usdm-1d-2026-06-daily.json \
  --symbol-manifest .runtime/crypto-history-full/manifests/usdm-1h-2026-06-daily.json \
  --market-segment spot \
  --market-segment usdm_futures \
  --interval 1d \
  --interval 1h \
  --mongo-uri mongodb://127.0.0.1:27017/claw_trade_crypto_history_full_20260607 \
  --columnar-root .runtime/crypto-history-full/normalized-columnar-final \
  --output-json .runtime/crypto-history-full/import-result-columnar-final-20260608.json
```

Result:

| Metric | Count |
|---|---:|
| provider attempts for this run | 3872 |
| raw payload metadata rows for this run | 64162 |
| seed import manifests for this run | 3872 |
| active Parquet manifests | 3872 |
| physical Parquet files in final columnar root | 3872 |
| Mongo `normalized_datasets` rows | 0 |
| active Parquet unique rows | 15633535 |
| parsed CSV rows | 15633546 |
| duplicate Kline rows collapsed by `dataset_ref` | 11 |

Verification command:

```bash
uv run python -c 'from pymongo import MongoClient
client=MongoClient("mongodb://127.0.0.1:27017", serverSelectionTimeoutMS=5000)
db=client["claw_trade_crypto_history_full_20260607"]
run="crypto-full-columnar-final-20260608"
print("attempts", db.provider_attempts.count_documents({"source_metadata.import_run_id":run}))
print("raw_payloads", db.raw_payloads.count_documents({"payload_summary.import_run_id":run}))
print("seed_manifests", db.dataset_manifests.count_documents({"job_id":run}))
print("active_parquet", db.dataset_manifests.count_documents({"storage":"parquet","status":"active"}))
print("mongo_normalized", db.normalized_datasets.count_documents({}))
print("active_agg", list(db.dataset_manifests.aggregate([{"$match":{"storage":"parquet","status":"active"}},{"$group":{"_id":None,"row_count":{"$sum":"$row_count"},"manifest_count":{"$sum":1}}}])))
print("seed_agg", list(db.dataset_manifests.aggregate([{"$match":{"job_id":run}},{"$group":{"_id":None,"row_count":{"$sum":"$audit.row_count"},"parsed_row_count":{"$sum":"$audit.parsed_row_count"},"file_count":{"$sum":"$audit.file_count"},"manifest_count":{"$sum":1}}}])))'
```

Key output:

```text
attempts 3872
raw_payloads 64162
seed_manifests 3872
active_parquet 3872
mongo_normalized 0
active_agg [{'_id': None, 'row_count': 15633535, 'manifest_count': 3872}]
seed_agg [{'_id': None, 'row_count': 15633535, 'parsed_row_count': 15633546, 'file_count': 64162, 'manifest_count': 3872}]
```

Warehouse raw readback proof:

```text
status ready
row_count 2226
dataset_refs 2226
gaps []
verify:BTCUSDT:daily records 742 actual 2025-06-01 2026-06-06 missing 0 missing_ranges ()
verify:ETHUSDT:daily records 742 actual 2025-06-01 2026-06-06 missing 0 missing_ranges ()
verify:SOLUSDT:daily records 742 actual 2025-06-01 2026-06-06 missing 0 missing_ranges ()
```

Note: the raw warehouse readback above is an unfiltered repository check over both spot and USD-M daily rows. It proves local coverage, but it is not the report-visible spot row count.

Report-visible spot proof:

```text
BTC /report daily rows 371 symbol BTCUSDT universe binance_spot_all_symbols gaps 0
BTC /report hourly rows 8904 symbol BTCUSDT universe binance_spot_all_symbols gaps 0
SOL /report daily rows 371 symbol SOLUSDT universe binance_spot_all_symbols gaps 0
SOL /report hourly rows 8904 symbol SOLUSDT universe binance_spot_all_symbols gaps 0
```

What this proves:

- The full Phase 1 Binance current-active-symbol batch has a verified columnar normalized import.
- Mongo is catalog/evidence only for this run: raw metadata, provider attempts, seed manifests, and Parquet manifest index.
- The upward data access remains the unified `Warehouse` / `DatasetRepository` path; no CRYPTO-specific reader or collection is required.
- BTCUSDT, ETHUSDT, and SOLUSDT `daily_bar` can be read from the columnar warehouse with Mongo fallback disabled and `date_range_missing_count=0`.
- The live BTC/SOL report path filters market bars to spot rows before charting and worker-visible market material.

Related live `/report` proof:

- `docs/evidence/live-crypto-btc-sol-20260608/summary.md`
- `/report BTC` run `run-20260608-154751-fd320952`
- `/report SOL` run `run-20260608-160147-f6bbe938`
- Both live reports read spot Kline rows from `.runtime/crypto-history-full/normalized-columnar-final` through the unified data layer.

What this does not prove:

- It does not cover delisted trading pairs, COIN-M futures, `1m`, trades, aggTrades, tick, order book, chain data, news, social, or paid depth data.
- It does not make Binance a global all-exchange crypto market source.
