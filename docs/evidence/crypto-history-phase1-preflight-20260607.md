# CRH-00 加密历史数据 Phase 1 预检

日期：2026-06-07
分支：`crypto-history-phase1`
计划来源：`docs/加密层历史数据实施计划.md`

## 结论

可以进入 CRH-01/CRH-02。

预检发现一个计划口径需要修正：原计划要求 `dataset://normalized/...`，但当前 `DatasetRepository`、仓库测试和 A股预打包路径沿用 `dataset:<dataset>:<market>:...`。CRYPTO Phase 1 应沿用现有仓库 ref 合同，不私自引入新的 normalized ref scheme。

除此之外，未发现 Phase 1 设计与当前 `data_gateway` 仓库合同冲突。现有数据层已经具备三件可复用能力：

- `GapReason.DATE_RANGE_MISSING` 已存在，值为 `date_range_missing`。
- `WarehouseResult.freshness["coverage_by_request"]` 已记录请求范围、实际覆盖范围、缺失区间和 dataset refs。
- A股预打包 importer 已经给出本地 seed 的 raw metadata、normalized rows、provider attempt、dataset manifest 写入模式。

## 已确认代码事实

### 缺口合同

- 文件：`src/claw_trade/data_gateway/models.py`
- `GapReason.DATE_RANGE_MISSING = "date_range_missing"` 已存在。
- `DataResult` 已有：
  - `dataset_refs`
  - `raw_refs`
  - `attempt_refs`
  - `gaps`
  - `freshness`

### 仓库覆盖口径

- 文件：`src/claw_trade/data_gateway/warehouse/warehouse.py`
- `Warehouse._check()` 在每个请求上生成 `coverage_by_request`。
- `_coverage_summary()` 已输出：
  - `request_id`
  - `data_type`
  - `granularity`
  - `market`
  - `symbol_id`
  - `record_count`
  - `dataset_refs`
  - `actual_start`
  - `actual_end`
  - `expected_start`
  - `expected_end`
  - `missing_ranges`
- 本地完全无记录时，当前通用行为是 `warehouse_missing`。CRH-07 需要在 CRYPTO 本地 seed 路径上保留 `local_warehouse_empty` 语义，不能只留下泛化缺口。

### A股 seed importer 可复用模式

- 文件：`src/claw_trade/data_gateway/a_share_prepackaged_importer.py`
- 可复用形态：
  - normalized row：`dataset`、`market`、`symbol_id`、`granularity`、`period_start`、`period_end`、`field_set`、`provider_lineage`、`schema_id`、`quality_flags`、`row`
  - raw metadata：`storage_mode="metadata_only"`，不保存完整原始包正文
  - provider attempt：本地 seed 使用 `status="local_seed_imported"`，并标记 `remote_attempted=False`、`remote_success=False`
  - dataset manifest：`job_type="seed_import"`，并记录 `dataset_refs`、`raw_refs`、`attempt_refs`、`source_summary`

CRYPTO importer 应复用这些模式，但 provider、endpoint、market、exchange、calendar、timezone、schema_id 必须使用 CRYPTO 设计值。

## Binance 官方口径

来源：

- `https://github.com/binance/binance-public-data`
- `https://raw.githubusercontent.com/binance/binance-public-data/master/python/README.md`
- `https://raw.githubusercontent.com/binance/binance-public-data/master/python/utility.py`

已确认：

- Binance public data 以 `daily` 和 `monthly` 文件提供；daily 通常次日可用，monthly 通常每月第一个周一可用。
- Spot Kline CSV 来自 `/api/v3/klines`，列顺序为 12 列：
  - open time
  - open
  - high
  - low
  - close
  - volume
  - close time
  - quote asset volume
  - number of trades
  - taker buy base asset volume
  - taker buy quote asset volume
  - ignore
- Spot 数据从 2025-01-01 起时间戳可能为微秒；importer 必须识别毫秒/微秒，不能固定假设毫秒。
- USD-M futures Kline CSV 来自 `/fapi/v1/klines`，也是 12 列，字段口径与设计中的 `quote_asset_volume` 兼容。
- COIN-M futures Kline CSV 来自 `/dapi/v1/klines`，列中包含 `base asset volume` 等不同口径；Phase 1 不支持 COIN-M，遇到必须显式 fail closed。
- 下载路径规则：
  - spot：`data/spot/{daily|monthly}/klines/{SYMBOL}/{INTERVAL}/...`
  - USD-M futures：`data/futures/um/{daily|monthly}/klines/{SYMBOL}/{INTERVAL}/...`
- 文件名规则：
  - monthly：`{SYMBOL}-{INTERVAL}-{YYYY}-{MM}.zip`
  - daily：`{SYMBOL}-{INTERVAL}-{YYYY-MM-DD}.zip`
  - checksum：同名追加 `.CHECKSUM`
- checksum 使用 SHA256；官方 README 示例使用 `sha256sum -c` 或 `shasum -a 256 -c` 校验。

## Phase 1 明确不包含

- `1m` 全量。
- trades、aggTrades、tick、order book 全量。
- COIN-M futures。
- 已下架交易对完整历史。
- 付费深度数据源采购或接入。
- OpenClaw runtime、worker prompt、runtime guard 修改。
- 用远端 provider 成功替代本地 seed 仓库验收。

## 需新增文件

- `src/claw_trade/data_gateway/crypto_prepackaged_importer.py`
- `src/claw_trade/data_gateway/crypto_symbols.py`
- `scripts/crypto/download_binance_public_data.py`
- `scripts/crypto/import_crypto_prepackaged_to_mongo.py`
- `tests/unit/data_gateway/test_binance_public_data_downloader.py`
- `tests/unit/data_gateway/test_crypto_prepackaged_importer.py`
- `tests/unit/data_gateway/test_crypto_asset_mapping.py`
- `tests/contracts/test_crypto_prepackaged_data_contract.py`

## 阻断检查

- 设计与当前 `data_gateway` 仓库合同冲突：发现 normalized ref 口径冲突，已按现有仓库合同修正设计和计划。
- Binance Kline 格式与设计不一致：未发现；但 COIN-M 与 spot/USD-M 字段口径不同，保持 Phase 1 禁入。
- checksum 规则无法确认：未发现；官方 README 明确 SHA256 `.CHECKSUM`。
- 旧已删除平台残留：已清理。全仓大小写不敏感搜索无命中。
