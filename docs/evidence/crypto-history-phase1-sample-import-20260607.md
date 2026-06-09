# CRYPTO Phase 1 Sample Import Evidence

Date: 2026-06-07

Scope:

- Binance Public Data monthly Kline sample, 2025-01.
- Symbols: BTCUSDT, ETHUSDT, SOLUSDT.
- Market segments: spot, USD-M futures.
- Intervals: 1d, 1h.
- Database: `claw_trade_crypto_phase1_20260607_r3`.

Negative status:

- This evidence proves only the BTCUSDT/ETHUSDT/SOLUSDT sample download, checksum verification, normalization, and Mongo import.
- It does not prove Binance all-active-symbol package download, checksum verification, or full Mongo import.

Commands:

```bash
scripts/start-local-mongodb.sh
uv run python scripts/crypto/download_binance_public_data.py --output-root .runtime/crypto-history-phase1/binance-sample --market-segment spot --interval 1d --start-date 2025-01-01 --end-date 2025-01-31 --symbol BTCUSDT --symbol ETHUSDT --symbol SOLUSDT --checksum-required --output-json .runtime/crypto-history-phase1/manifests/spot-1d.json
uv run python scripts/crypto/download_binance_public_data.py --output-root .runtime/crypto-history-phase1/binance-sample --market-segment spot --interval 1h --start-date 2025-01-01 --end-date 2025-01-31 --symbol BTCUSDT --symbol ETHUSDT --symbol SOLUSDT --checksum-required --output-json .runtime/crypto-history-phase1/manifests/spot-1h.json
uv run python scripts/crypto/download_binance_public_data.py --output-root .runtime/crypto-history-phase1/binance-sample --market-segment usdm_futures --interval 1d --start-date 2025-01-01 --end-date 2025-01-31 --symbol BTCUSDT --symbol ETHUSDT --symbol SOLUSDT --checksum-required --output-json .runtime/crypto-history-phase1/manifests/usdm-1d.json
uv run python scripts/crypto/download_binance_public_data.py --output-root .runtime/crypto-history-phase1/binance-sample --market-segment usdm_futures --interval 1h --start-date 2025-01-01 --end-date 2025-01-31 --symbol BTCUSDT --symbol ETHUSDT --symbol SOLUSDT --checksum-required --output-json .runtime/crypto-history-phase1/manifests/usdm-1h.json
uv run python scripts/crypto/import_crypto_prepackaged_to_mongo.py --spot-root .runtime/crypto-history-phase1/binance-sample/data/spot --futures-root .runtime/crypto-history-phase1/binance-sample/data/futures/um --trade-date 2026-06-07 --import-run-id crypto-phase1-sample-20260607-r3 --start-date 2025-01-01 --end-date 2025-01-31 --mongo-uri mongodb://127.0.0.1:27017 --mongo-database claw_trade_crypto_phase1_20260607_r3 --output-json .runtime/crypto-history-phase1/import-result-r3.json
```

Current CLI rerun note:

- The command above records the command used during the sample proof.
- After the CLI scope hardening, an equivalent rerun must add `--phase1-sample`; otherwise the import CLI fails closed instead of silently falling back to BTC/ETH/SOL.

```bash
uv run python scripts/crypto/import_crypto_prepackaged_to_mongo.py --spot-root .runtime/crypto-history-phase1/binance-sample/data/spot --futures-root .runtime/crypto-history-phase1/binance-sample/data/futures/um --trade-date 2026-06-07 --import-run-id crypto-phase1-sample-20260607-rerun --start-date 2025-01-01 --end-date 2025-01-31 --phase1-sample --mongo-uri mongodb://127.0.0.1:27017 --mongo-database claw_trade_crypto_phase1_20260607_rerun --output-json .runtime/crypto-history-phase1/import-result-rerun.json
```

Download result:

- `spot-1d.json`: 3 items, all `downloaded`, checksum `verified`, 6489 bytes.
- `spot-1h.json`: 3 items, all `downloaded`, checksum `verified`, 126609 bytes.
- `usdm-1d.json`: 3 items, all `downloaded`, checksum `verified`, 6276 bytes.
- `usdm-1h.json`: 3 items, all `downloaded`, checksum `verified`, 112415 bytes.

Mongo result:

- `normalized_datasets`: 4650.
- `raw_payloads`: 12.
- `provider_attempts`: 12.
- `dataset_manifests`: 12.
- attempt provider: `local_crypto_prepackaged`.
- attempt endpoint: `binance_public_data_import`.
- attempt status: `local_seed_imported`.
- manifest job type: `seed_import`.
- manifest source summary: `local:crypto_prepackaged:binance_public_data`.

Coverage result:

- Each BTCUSDT, ETHUSDT, SOLUSDT spot `daily_bar`: 31 rows, 2025-01-01 to 2025-01-31.
- Each BTCUSDT, ETHUSDT, SOLUSDT spot `intraday_bar` 1h: 744 rows, 2025-01-01 00:00:00 to 2025-01-31 23:59:59.
- Each BTCUSDT, ETHUSDT, SOLUSDT USD-M `daily_bar`: 31 rows, 2025-01-01 to 2025-01-31.
- Each BTCUSDT, ETHUSDT, SOLUSDT USD-M `intraday_bar` 1h: 744 rows, 2025-01-01 00:00:00 to 2025-01-31 23:59:59.

Evidence files:

- `.runtime/crypto-history-phase1/import-result-r3.json`
- `.runtime/crypto-history-phase1/mongo-coverage-r3.json`
- `.runtime/crypto-history-phase1/manifests/spot-1d.json`
- `.runtime/crypto-history-phase1/manifests/spot-1h.json`
- `.runtime/crypto-history-phase1/manifests/usdm-1d.json`
- `.runtime/crypto-history-phase1/manifests/usdm-1h.json`

Defects found and fixed during this proof:

- Real USD-M futures CSV contains a header row. Parser now supports optional Kline headers with alias mapping while keeping 12-column validation.
- spot and USD-M daily rows initially shared the same dataset ref. CRYPTO seed rows now include `source_market_segment` in `dataset_ref` and write the proper Binance universe ref.
- Mongo-backed intraday coverage initially failed because repository date-range criteria only used date strings. Normalized query criteria now include a datetime branch for Mongo datetime fields.
