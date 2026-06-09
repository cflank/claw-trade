# CRYPTO USDT-only Cleanup And Warehouse Evidence

Date: 2026-06-08

Purpose: record the current effective CRYPTO Phase 1 state after the requirement changed to USDT-only pairs. This evidence supersedes the earlier all-quote/mixed import evidence for current warehouse operation.

## Scope

- Keep only Binance spot / USD-M perpetual USDT Kline packages.
- Remove old mixed catalog state and old mixed result files.
- Keep normalized rows in the unified columnar warehouse.
- Keep Mongo only for catalog, raw metadata, provider attempts, and evidence.

## Current Storage

| Item | Current value |
|---|---:|
| Raw ZIP packages | 32452 |
| Raw checksum files | 32452 |
| Raw unique USDT symbols | 596 |
| Raw non-USDT ZIP packages | 0 |
| Raw ZIP bytes | 358514812 |
| Raw ZIP size | 341.91 MiB |
| Raw ZIP + checksum directory size | 571M |
| Mongo catalog database | `claw_trade_crypto_history_usdt_20260608` |
| Mongo `normalized_datasets` rows | 0 |
| Mongo raw payload rows | 32452 |
| Mongo provider attempts | 2079 |
| Mongo dataset manifests, all types | 4001 |
| Mongo logical data size after runtime cleanup | 244338852 bytes |
| Mongo allocated total size after runtime cleanup and compact | 86773760 bytes |
| Mongo normalized row storage | 0 rows; normalized bars are in Parquet |
| Mongo runtime cache / rate-limit / single-flight rows | 0 |
| Active Parquet manifests | 1921 |
| Active Parquet rows | 8001515 |
| Active Parquet files | 1921 |
| Active Parquet size | 662.10 MiB |
| Physical Parquet files in USDT-only root | 2126 |
| Physical Parquet size in USDT-only root | 735.61 MiB |
| Superseded Parquet files, not read by active warehouse | 158 |
| Orphan Parquet files, not read by active warehouse | 47 |

Current effective paths:

```text
data/crypto-history-full/binance/data
data/crypto-history-full/normalized-columnar-usdt-only
mongodb://127.0.0.1:27017/claw_trade_crypto_history_usdt_20260608
```

Deleted old mixed result files:

```text
.runtime/crypto-history-full/import-result-columnar-final-20260608.json
.runtime/crypto-history-full/import-result-columnar-20260607.json
.runtime/crypto-history-full/import-result.json
```

The old mixed Mongo catalog database is not present in the current Mongo database list.

## Package Scan

Command:

```bash
python3 - <<'PY'
from pathlib import Path
from collections import Counter
import json, re
root=Path('data/crypto-history-full/binance/data')
zip_files=[p for p in root.rglob('*.zip') if p.is_file()]
checksum_files=[p for p in root.rglob('*.CHECKSUM') if p.is_file()]
def symbol_of(p):
    parts=p.parts
    i=parts.index('klines')
    return parts[i+1]
usdt=[p for p in zip_files if symbol_of(p).endswith('USDT')]
non_usdt=[p for p in zip_files if not symbol_of(p).endswith('USDT')]
print(len(zip_files), len(checksum_files), len(usdt), len(non_usdt))
PY
```

Key result:

```text
zip_count=32452
checksum_count=32452
usdt_zip_count=32452
non_usdt_zip_count=0
```

## Mongo Scan

Command:

```bash
uv run python - <<'PY'
from pymongo import MongoClient
client=MongoClient('mongodb://127.0.0.1:27017',serverSelectionTimeoutMS=5000)
print([d for d in client.list_database_names() if 'crypto' in d.lower()])
db=client['claw_trade_crypto_history_usdt_20260608']
for name in ('raw_payloads','provider_attempts','dataset_manifests','normalized_datasets'):
    print(name, db[name].estimated_document_count())
PY
```

Key result:

```text
crypto_databases=['claw_trade_crypto_history_usdt_20260608']
raw_payloads=32452
provider_attempts=2079
dataset_manifests=4001
normalized_datasets=0
```

After lineage compaction, accidental live-runtime cleanup, and Mongo `compact`:

```text
dbStats.dataSize=244338852
dbStats.storageSize=83898368
dbStats.indexSize=2875392
dbStats.totalSize=86773760
```

Root cause of the earlier Mongo bloat:

- seed import manifests stored full row-level `dataset_refs`;
- `output_refs.dataset_refs` duplicated the same full list;
- provider attempts also stored full row-level `dataset_refs`;
- Parquet manifests stored full row-level `dataset_refs`;
- `provider_attempts.dataset_refs` had a large multi-key index.

Current mechanism:

- large lineage lists are stored as count + 20-row sample + SHA256 + dataset-ref prefix;
- provider attempt lookup uses the compact prefix when the full row-level ref is not stored inline;
- Mongo keeps catalog/evidence metadata only; normalized bars remain in Parquet;
- the old `provider_attempts.dataset_refs` multi-key index was replaced by `provider_attempts.dataset_ref_prefixes`.
- live/runtime reads this seed catalog through `DATA_GATEWAY_SEED_MONGODB_DATABASE`; runtime provider evidence, cache, rate-limit, and single-flight state must write to a separate runtime Mongo database.
- `scripts/start-control-runtime.sh` blocks using `claw_trade_crypto_history_*` as `DATA_GATEWAY_MONGODB_DATABASE` unless maintenance explicitly sets `CLAW_TRADE_ALLOW_FACTORY_MONGO_WRITE=1`.

The live proof initially wrote runtime records into this seed DB. Cleanup removed:

```text
raw_payloads=37
provider_attempts=40
provider_rate_limits=15
single_flight_calls=40
provider_result_cache=38
```

Post-cleanup seed counts:

```text
raw_payloads=32452
provider_attempts=2079
provider_rate_limits=0
single_flight_calls=0
provider_result_cache=0
dataset_manifests=4001
normalized_datasets=0
```

## Active Warehouse Scan

Command:

```bash
uv run python - <<'PY'
from pathlib import Path
from pymongo import MongoClient
import duckdb
client=MongoClient('mongodb://127.0.0.1:27017',serverSelectionTimeoutMS=5000)
db=client['claw_trade_crypto_history_usdt_20260608']
paths=[d['path'] for d in db.dataset_manifests.find({'storage':'parquet','status':'active'},{'path':1,'_id':0}) if d.get('path')]
quote="json_extract_string(row_json, '$.quote_asset')"
with duckdb.connect(':memory:') as con:
    print(con.execute(f"""
        SELECT COUNT(*), COUNT(DISTINCT symbol_id),
               SUM(CASE WHEN {quote} != 'USDT' OR symbol_id NOT LIKE '%USDT' THEN 1 ELSE 0 END)
        FROM read_parquet(?)
    """, [paths]).fetchone())
PY
```

Key result:

```text
active_rows=8001515
active_unique_symbols=596
active_non_usdt_rows=0
```

## Warehouse Readback Smoke

Command:

```bash
uv run python - <<'PY'
from datetime import datetime, UTC
from pymongo import MongoClient
from claw_trade.data_gateway.warehouse import DatasetRepository
from claw_trade.data_gateway.warehouse.normalized_columnar import NormalizedColumnarWarehouse
client=MongoClient('mongodb://127.0.0.1:27017',serverSelectionTimeoutMS=5000)
db=client['claw_trade_crypto_history_usdt_20260608']
repo=DatasetRepository.from_database(
    db,
    normalized_columnar=NormalizedColumnarWarehouse('data/crypto-history-full/normalized-columnar-usdt-only'),
    allow_normalized_mongo_read=False,
)
for sym in ['BTCUSDT','ETHUSDT','SOLUSDT']:
    for dataset, start, end in [
        ('daily_bar', datetime(2025,6,1,tzinfo=UTC), datetime(2026,6,6,tzinfo=UTC)),
        ('intraday_bar', datetime(2025,6,1,tzinfo=UTC), datetime(2026,6,6,23,59,59,tzinfo=UTC)),
    ]:
        rows=repo.query_normalized(dataset=dataset, market='CRYPTO', symbol_id=sym, universe_ref='binance_spot_all_symbols', date_range_start=start, date_range_end=end, include_row=False)
        starts=[r.period_start for r in rows]
        ends=[r.period_end for r in rows]
        print(sym, dataset, len(rows), min(starts), max(ends))
PY
```

Key result:

```text
BTCUSDT daily_bar rows 371 min_start 2025-06-01 max_end 2026-06-06
BTCUSDT intraday_bar rows 8904 min_start 2025-06-01 max_end 2026-06-06
ETHUSDT daily_bar rows 371 min_start 2025-06-01 max_end 2026-06-06
ETHUSDT intraday_bar rows 8904 min_start 2025-06-01 max_end 2026-06-06
SOLUSDT daily_bar rows 371 min_start 2025-06-01 max_end 2026-06-06
SOLUSDT intraday_bar rows 8904 min_start 2025-06-01 max_end 2026-06-06
```

## Notes

- Three non-ASCII USDT symbols are present in the current Binance package set and active Parquet rows. They came from Binance public data URLs and verified checksums, so they are not treated as non-USDT contamination.
- The previous live `/report BTC` and `/report SOL` proof remains historical evidence for the earlier all-quote root. The current effective project-local USDT-only proof is the seed overlay live run in `docs/evidence/live-crypto-seed-overlay-20260608/summary.md`.
- Superseded and orphan Parquet files are not read by active warehouse manifests. They remain as a storage cleanup opportunity, not as active data.
