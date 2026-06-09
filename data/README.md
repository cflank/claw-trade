# System Data

## A Share Data Layer

System recovery package:

```text
data/a-share-cn-required-300td-20260608.tar
```

Checksum:

```text
data/a-share-cn-required-300td-20260608.tar.sha256
```

Current package content:

- 300 trading days, from 2025-03-03 to 2026-05-27
- `daily_bar`: 1,626,559 rows
- `valuation_metric`: 1,626,559 rows
- 2 Parquet files
- minimal Mongo metadata JSONL
- company identity is embedded in `daily_bar` rows: `company_name` and `identity_source_ref`
- `daily_bar` company-name missing rows: 0
- 8 symbols without real company identity were skipped during rebuild

Current factory seed restore layout:

```text
.runtime/factory-seeds/a-share-cn-required-300td-20260608/normalized
```

Restore command:

```bash
uv run python scripts/selection/restore_a_share_factory_seed.py \
  --package data/a-share-cn-required-300td-20260608.tar \
  --checksum data/a-share-cn-required-300td-20260608.tar.sha256 \
  --mongo-uri mongodb://127.0.0.1:27017 \
  --mongo-database claw_trade_a_share_factory_seed \
  --replace-existing \
  --confirm-replace-existing
```

Runtime read config after restore. Use a runtime write overlay so live provider output does not pollute the factory seed files:

```bash
export DATA_GATEWAY_SEED_MONGODB_URI=mongodb://127.0.0.1:27017
export DATA_GATEWAY_SEED_MONGODB_DATABASE=claw_trade_a_share_factory_seed
export DATA_GATEWAY_COLUMNAR_ROOT=.runtime/dev-services/data-gateway/normalized
```

Merge runtime daily increments back into the 300-trading-day seed:

```bash
uv run python scripts/selection/merge_a_share_factory_seed_incremental.py \
  --seed-columnar-root .runtime/factory-seeds/a-share-cn-required-300td-20260608/normalized \
  --incremental-columnar-root .runtime/dev-services/data-gateway/normalized \
  --mongo-uri mongodb://127.0.0.1:27017 \
  --mongo-database claw_trade_a_share_factory_seed \
  --trading-days 300 \
  --confirm-merge
```

The merge command:

- reads existing seed Parquet plus runtime overlay Parquet;
- keeps only the latest 300 trading days;
- rewrites the seed `daily_bar` and `valuation_metric` Parquet files;
- replaces seed Mongo metadata with `dataset_manifests=2`, `provider_attempts=1`, `raw_payloads=1`, `normalized_datasets=0`;
- fails by default if `daily_bar` and `valuation_metric` would retain different date windows.

Mongo stores only metadata:

- `dataset_manifests`: 2
- `provider_attempts`: 1
- `raw_payloads`: 1
- `normalized_datasets`: 0

What works now:

- Read CN_A `daily_bar` from Parquet through the data gateway.
- Read CN_A `valuation_metric` from Parquet through the data gateway.
- Use the package to restore the 300-trading-day product seed.
- Merge runtime daily increments into the 300-trading-day product seed.
- Read restored Parquet from the current columnar root even when older manifests contain paths from a previous workspace.
- Rebuild the package with real company identity fields when `--local-root` or approved online identity sources are provided.
- Rebuild in strict mode with `--require-company-identity`, so rows without real company identity do not enter the factory seed.
- Keep `/select` top-20 candidate packs as daily runtime outputs, not as fixed factory seed data.

What is not a product input:

- The 13G vendor zip package.
- The 236 precomputed technical indicator columns.

Current gaps:

- No checked-in daily incremental job for this package yet.
- No checked-in scheduler binding that refreshes this package every trading day.
