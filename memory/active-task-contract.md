# Active Task Contract: /select B+C Columnar Storage

Date: 2026-06-05

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

- Add DuckDB and Parquet runtime dependencies.
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

- `pyproject.toml` and lockfile include DuckDB/Parquet dependencies.
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
| Mongo only metadata/status | TBD | TBD | pending |
| DuckDB/Parquet primary selection data | TBD | TBD | pending |
| Incremental missing-data backfill | TBD | TBD | pending |
| File-level hash detects deletion/corruption | TBD | TBD | pending |
| Calculation avoids full-market all-window memory load | TBD | TBD | pending |
| Chrome `/select` passes | TBD | TBD | pending |

