# Data layer full-chain probe: CRYPTO

- run_id: `btc-data-layer-after-fixes-20260611`
- scratch_database: `claw_trade_btc_data_layer_after_fixes_20260611`
- request_count: `19`
- collection_counts: `{"dataset_manifests": 0, "maintenance_jobs": 0, "normalized_datasets": 1909, "provider_attempts": 26, "provider_rate_limits": 8, "provider_result_cache": 21, "raw_payloads": 21, "single_flight_calls": 26}`

## Provider calls

| provider | endpoint | data_type | called | result | not called reason |
|---|---|---|---:|---|---|
| `crypto_primary` | `spot_daily_bar` | `daily_bar` | yes | `success` | `` |
| `crypto_binance_spot_market` | `ticker_24hr` | `quote_snapshot` | yes | `success` | `` |
| `crypto_binance_spot_market` | `order_book_depth` | `order_book_snapshot` | yes | `success` | `` |
| `crypto_binance_spot_market` | `spot_intraday_bar` | `intraday_bar` | yes | `success` | `` |
| `crypto_coingecko_market` | `coins_markets` | `valuation_metric` | yes | `success` | `` |
| `crypto_defillama_defi` | `protocol` | `defi_metric` | yes | `success` | `` |
| `crypto_coinglass_derivatives` | `spot_coins_markets` | `valuation_metric` | yes | `success` | `` |
| `crypto_coinglass_derivatives` | `coin_market_data_history` | `valuation_metric` | no | `` | `request_fields_or_granularity_do_not_match_capability` |
| `crypto_coinglass_derivatives` | `futures_open_interest` | `crypto_derivative_metric` | yes | `success` | `` |
| `crypto_coinglass_derivatives` | `futures_pairs_markets` | `crypto_derivative_metric` | yes | `error` | `` |
| `crypto_coinglass_derivatives` | `futures_open_interest_aggregated_history` | `crypto_derivative_metric` | no | `` | `request_fields_or_granularity_do_not_match_capability` |
| `crypto_coinglass_derivatives` | `futures_funding_rate` | `crypto_derivative_metric` | yes | `success` | `` |
| `crypto_coinglass_derivatives` | `futures_long_short_ratio` | `crypto_derivative_metric` | yes | `rate_limited` | `` |
| `crypto_coinglass_derivatives` | `futures_taker_buy_sell` | `crypto_derivative_metric` | yes | `success` | `` |
| `crypto_coinglass_derivatives` | `futures_liquidation` | `crypto_derivative_metric` | yes | `success` | `` |
| `crypto_coinglass_derivatives` | `futures_liquidation_heatmap` | `crypto_derivative_metric` | no | `` | `request_fields_or_granularity_do_not_match_capability` |
| `crypto_coinglass_derivatives` | `options_open_interest` | `crypto_derivative_metric` | no | `` | `request_fields_or_granularity_do_not_match_capability` |
| `crypto_coinglass_derivatives` | `spot_cvd_history` | `crypto_derivative_metric` | yes | `success` | `` |
| `crypto_coinglass_derivatives` | `futures_cvd_history` | `crypto_derivative_metric` | yes | `success` | `` |
| `crypto_coinglass_derivatives` | `etf_flow_history` | `crypto_derivative_metric` | no | `` | `request_fields_or_granularity_do_not_match_capability` |
| `crypto_coinglass_derivatives` | `futures_price_history` | `daily_bar` | yes | `success` | `` |
| `crypto_coinglass_derivatives` | `spot_price_history` | `daily_bar` | yes | `success` | `` |
| `crypto_coinglass_derivatives` | `futures_order_book` | `order_book_snapshot` | no | `` | `request_fields_or_granularity_do_not_match_capability` |
| `crypto_coinglass_derivatives` | `spot_order_book` | `order_book_snapshot` | no | `` | `request_fields_or_granularity_do_not_match_capability` |
| `crypto_coinglass_derivatives` | `onchain_exchange_balance` | `crypto_onchain_metric` | yes | `success` | `` |
| `crypto_coinglass_derivatives` | `onchain_whale_transfer` | `crypto_onchain_metric` | yes | `rate_limited` | `` |
| `crypto_coinglass_derivatives` | `spot_coin_netflow` | `crypto_onchain_metric` | yes | `rate_limited` | `` |
| `crypto_coinglass_derivatives` | `futures_coin_netflow` | `crypto_derivative_metric` | yes | `rate_limited` | `` |
| `crypto_coinglass_derivatives` | `bitcoin_ahr999` | `crypto_onchain_metric` | yes | `success` | `` |
| `crypto_lunarcrush_social` | `topic` | `social_signal` | no | `` | `matching_request_exists_but_not_selected` |
| `crypto_glassnode_onchain` | `deep_onchain_metrics` | `crypto_onchain_metric` | no | `` | `matching_request_exists_but_not_selected` |
| `crypto_token_terminal_fundamentals` | `protocol_revenue` | `defi_metric` | no | `` | `request_fields_or_granularity_do_not_match_capability` |
| `crypto_alternative_me_sentiment` | `fear_greed` | `social_signal` | yes | `success` | `` |
| `crypto_project_official_news` | `github_releases` | `company_news` | yes | `success` | `` |
| `crypto_google_news` | `company_news` | `company_news` | yes | `success` | `` |
| `crypto_google_news` | `macro_news` | `macro_news` | yes | `success` | `` |

## Results

| request | status | refs | gaps |
|---|---|---:|---|
| `btc-data-layer-after-fixes-20260611:01:daily_bar` | `partial` | 732 datasets / 3 raw / 3 attempts | `warehouse_missing,warehouse_missing` |
| `btc-data-layer-after-fixes-20260611:02:quote_snapshot` | `partial` | 1 datasets / 1 raw / 1 attempts | `warehouse_missing` |
| `btc-data-layer-after-fixes-20260611:03:order_book_snapshot` | `partial` | 1 datasets / 1 raw / 1 attempts | `warehouse_missing` |
| `btc-data-layer-after-fixes-20260611:04:order_book_snapshot` | `missing` | 0 datasets / 0 raw / 0 attempts | `warehouse_missing` |
| `btc-data-layer-after-fixes-20260611:05:intraday_bar` | `partial` | 1000 datasets / 1 raw / 1 attempts | `warehouse_missing,warehouse_missing` |
| `btc-data-layer-after-fixes-20260611:06:valuation_metric` | `partial` | 2 datasets / 2 raw / 2 attempts | `warehouse_missing` |
| `btc-data-layer-after-fixes-20260611:07:defi_metric` | `partial` | 1 datasets / 1 raw / 1 attempts | `warehouse_missing` |
| `btc-data-layer-after-fixes-20260611:08:crypto_derivative_metric` | `partial` | 1 datasets / 1 raw / 2 attempts | `warehouse_missing,provider_error` |
| `btc-data-layer-after-fixes-20260611:09:crypto_derivative_metric` | `partial` | 8 datasets / 1 raw / 1 attempts | `warehouse_missing` |
| `btc-data-layer-after-fixes-20260611:10:crypto_derivative_metric` | `missing` | 0 datasets / 0 raw / 1 attempts | `warehouse_missing,rate_limited` |
| `btc-data-layer-after-fixes-20260611:11:crypto_derivative_metric` | `partial` | 16 datasets / 3 raw / 3 attempts | `warehouse_missing` |
| `btc-data-layer-after-fixes-20260611:12:crypto_derivative_metric` | `partial` | 8 datasets / 1 raw / 1 attempts | `warehouse_missing` |
| `btc-data-layer-after-fixes-20260611:13:crypto_derivative_metric` | `missing` | 0 datasets / 0 raw / 1 attempts | `warehouse_missing,rate_limited` |
| `btc-data-layer-after-fixes-20260611:14:crypto_onchain_metric` | `partial` | 1 datasets / 2 raw / 2 attempts | `warehouse_missing,date_range_missing` |
| `btc-data-layer-after-fixes-20260611:15:crypto_onchain_metric` | `missing` | 0 datasets / 0 raw / 1 attempts | `warehouse_missing,rate_limited` |
| `btc-data-layer-after-fixes-20260611:16:crypto_onchain_metric` | `missing` | 0 datasets / 0 raw / 1 attempts | `warehouse_missing,rate_limited` |
| `btc-data-layer-after-fixes-20260611:17:social_signal` | `partial` | 1 datasets / 1 raw / 1 attempts | `warehouse_missing` |
| `btc-data-layer-after-fixes-20260611:18:company_news` | `partial` | 71 datasets / 2 raw / 2 attempts | `warehouse_missing` |
| `btc-data-layer-after-fixes-20260611:19:macro_news` | `partial` | 66 datasets / 1 raw / 1 attempts | `warehouse_missing` |

## Flow

`DataAPI -> DataService.plan_batch -> QueryPlanner -> Warehouse.check -> ProviderSelector -> RequestCoalescer -> ProviderBatchPlanner -> ExecutionGate -> FetchEngine -> IngestPipeline(RawStore, Normalizer, NormalizedStore, AttemptLog) -> Warehouse.recheck -> DataResult`
