# System Data

## Current Factory Seed

当前默认交付数据包：

```text
data/current-seed-20260626.tar
```

这个包是新机器首次安装时恢复历史数据用的。除非本次交付专门生成了更新的 `data/current-seed-*.tar`，否则就使用这个包。

制作新的 seed：

```bash
uv run python scripts/production/export_current_seed.py \
  --env-file .runtime/dev-services/runtime.env \
  --output data/current-seed-$(date -u +%Y%m%d).tar
```

这个命令不会重新下载数据。它只把当前 Mongo 元数据和当前 Parquet 历史数据打包成新的交付 seed。

它包含两个市场：

```text
CN_A
CRYPTO
```

当前包内容概要：

```text
CN_A:
  capital_flow
  company_news
  corporate_action
  daily_bar
  event_calendar
  financial_metric
  financial_statement
  hot_money_event
  lockup_event
  macro_news
  margin_trading
  northbound_flow
  official_filing
  sector_snapshot
  social_signal
  valuation_metric

CRYPTO:
  company_news
  company_profile
  crypto_derivative_metric
  crypto_onchain_metric
  daily_bar
  event_calendar
  macro_series
  order_book_snapshot
  quote_snapshot
  social_signal
  valuation_metric
```

恢复默认 seed：

```bash
uv run python scripts/selection/restore_a_share_factory_seed.py \
  --package data/current-seed-20260626.tar \
  --checksum data/current-seed-20260626.tar.sha256 \
  --mongo-uri mongodb://127.0.0.1:27017 \
  --mongo-database claw_trade_a_share_factory_seed \
  --replace-existing \
  --confirm-replace-existing
```

运行时默认恢复目录：

```text
.runtime/factory-seeds/current-seed-20260626/normalized
```

注意：

```text
data/a-share-cn-required-300td-20260608.tar 已废弃，不再作为备用包或打包 fallback。
```
