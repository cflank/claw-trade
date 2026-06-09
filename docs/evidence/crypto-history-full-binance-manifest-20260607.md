# CRYPTO Binance All-Active-Symbol Manifest Evidence

Date: 2026-06-07

Current status: superseded for current warehouse operation. The all-quote/mixed package and import numbers recorded here have been replaced by the USDT-only effective warehouse in `docs/evidence/crypto-history-usdt-only-cleanup-20260608.md`.

Scope:

- Binance current active spot symbols from exchangeInfo.
- Binance current active USD-M perpetual symbols from exchangeInfo.
- Intervals: `1d`, `1h`.
- Total generated/downloaded scope: 2025-06-01 to 2026-06-06.
- Monthly subset: 2025-06-01 to 2026-05-31.
- Daily subset: 2026-06-01 to 2026-06-06.

What this proves:

- The downloader can discover Binance current active symbols with `--all-symbols`.
- The downloader generated all-active-symbol monthly URL manifests for completed months and daily URL manifests for the current incomplete month.
- The manifests include symbol metadata needed by the import CLI `--symbol-manifest` path.
- The full all-active-symbol package batch was downloaded where Binance exposed ZIP/CHECKSUM files, checksum-verified, cleaned, normalized, and imported through the unified columnar normalized path.
- Current CRYPTO importer code writes normalized rows to Parquet with Mongo catalog/evidence; see `docs/evidence/crypto-history-full-columnar-import-20260608.md`.

What this does not prove:

- It does not include delisted trading pairs, COIN-M futures, `1m`, trades, aggTrades, tick, order book, chain data, news, social, or paid depth data.
- Live `/report BTC` and `/report SOL` proof is recorded separately in `docs/evidence/live-crypto-btc-sol-20260608/summary.md`.
- It does not make Binance a global all-exchange crypto market source.

Manifest counts:

| Manifest | Symbols | URLs | Status | Checksum status |
|---|---:|---:|---|---|
| `.runtime/crypto-history-full/manifests/spot-1d-2025-06_2026-05.json` | 1365 | 16380 | processed | verified where present |
| `.runtime/crypto-history-full/manifests/spot-1h-2025-06_2026-05.json` | 1365 | 16380 | processed | verified where present |
| `.runtime/crypto-history-full/manifests/usdm-1d-2025-06_2026-05.json` | 571 | 6852 | processed | verified where present |
| `.runtime/crypto-history-full/manifests/usdm-1h-2025-06_2026-05.json` | 571 | 6852 | processed | verified where present |
| `.runtime/crypto-history-full/manifests/spot-1d-2026-06-daily.json` | 1365 | 8190 | processed | verified where present |
| `.runtime/crypto-history-full/manifests/spot-1h-2026-06-daily.json` | 1365 | 8190 | processed | verified where present |
| `.runtime/crypto-history-full/manifests/usdm-1d-2026-06-daily.json` | 571 | 3426 | processed | verified where present |
| `.runtime/crypto-history-full/manifests/usdm-1h-2026-06-daily.json` | 571 | 3426 | processed | verified where present |

Total planned package URLs: 69696.

Current-month coverage note:

- The downloader now generates daily package URLs for non-complete date ranges.
- The daily manifests cover 2026-06-01 through 2026-06-06. 2026-06-07 is intentionally excluded because same-day daily packages are not expected to exist yet.

Download and checksum result:

| Metric | Count |
|---|---:|
| planned URLs | 69696 |
| processed URLs | 69696 |
| checksum verified ZIPs | 64162 |
| Binance missing ZIP/CHECKSUM packages | 5534 |
| checksum failures | 0 |
| local files, ZIP + CHECKSUM | 128324 |
| local data directory size | `.runtime/crypto-history-full/binance`, 1.1G |

Checksum import gate:

- Download stage verifies ZIPs against Binance `.CHECKSUM` files.
- Import CLI also re-verifies same-name `.CHECKSUM` before adding a ZIP to an import job.
- Checksum-gated audit result: `.runtime/crypto-history-full/import-audit-checksum-gated.json`, `job_count=3872`; all jobs' `package_count` sum to 64162.

Final catalog/columnar import result:

| Metric | Count |
|---|---:|
| database | `claw_trade_crypto_history_full_20260607` |
| import jobs | 3872 |
| raw payload metadata rows | 64162 |
| provider attempts | 3872 |
| dataset manifests | 3872 |
| Mongo `normalized_datasets` rows | 0 |
| active Parquet unique rows | 15633535 |
| parsed CSV rows | 15633546 |
| duplicate Kline rows collapsed by `dataset_ref` | 11 |

Columnar import result:

| Metric | Count |
|---|---:|
| columnar import run id | `crypto-full-columnar-final-20260608` |
| columnar root | `.runtime/crypto-history-full/normalized-columnar-final` |
| active Parquet manifests | 3872 |
| physical Parquet files | 3872 |
| active Parquet unique rows | 15633535 |
| parsed CSV rows | 15633546 |
| Mongo `normalized_datasets` rows for final columnar database state | 0 |

Columnar evidence:

- `docs/evidence/crypto-history-full-columnar-import-20260608.md`
- `.runtime/crypto-history-full/import-result-columnar-final-20260608.json`

Provider attempt proof:

- `provider=local_crypto_prepackaged`
- `endpoint=binance_public_data_import`
- `status=local_seed_imported`
- `remote_attempted=false`
- `remote_success=false`

Coverage proof:

- BTCUSDT, ETHUSDT, and SOLUSDT have spot and USD-M `1d` coverage from 2025-06-01 through 2026-06-06 with `date_range_missing=0`.
- BTCUSDT, ETHUSDT, and SOLUSDT have spot and USD-M `1h` coverage from 2025-06-01 00:00 UTC through 2026-06-06 23:00 UTC with `hour_range_missing=0`.
- Warehouse API checks for BTCUSDT and SOLUSDT `daily_bar` returned `ready`, no gaps, and local seed attempt refs.
