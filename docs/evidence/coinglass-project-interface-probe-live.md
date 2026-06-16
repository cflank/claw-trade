# Coinglass Project Interface Probe

- generated_at: `2026-06-16T01:24:22.166822+00:00`
- symbol: `BTC/USDT`
- catalog_only: `False`

| api | endpoint | status | rows | missing declared fields | error |
| --- | --- | --- | ---: | --- | --- |
| crypto.daily_bar | coinglass.futures_price_history | success | 32 |  |  |
| crypto.daily_bar | coinglass.spot_price_history | success | 32 |  |  |
| crypto.intraday_bar | coinglass.futures_price_history | success | 768 |  |  |
| crypto.intraday_bar | coinglass.spot_price_history | success | 768 |  |  |
| crypto.realtime_quote | coinglass.spot_coins_markets | success | 1 |  |  |
| crypto.order_book | not_implemented | not_implemented |  |  |  |
| crypto.valuation_metric | coinglass.spot_coins_markets | success | 1 |  |  |
| crypto.valuation_metric | coinglass.coin_market_data_history | success | 5817 |  |  |
| crypto.funding_rate | coinglass.futures_funding_rate | success | 768 |  |  |
| crypto.funding_rate | coinglass.raw_futures_funding_rate_history | success | 768 |  |  |
| crypto.funding_rate | coinglass.raw_futures_funding_rate_vol_weight_history | success | 768 |  |  |
| crypto.open_interest | coinglass.futures_open_interest_aggregated_history | success | 768 |  |  |
| crypto.open_interest | coinglass.raw_futures_open_interest_aggregated_coin_margin_history | success | 768 |  |  |
| crypto.open_interest | coinglass.raw_futures_open_interest_aggregated_stablecoin_history | success | 768 |  |  |
| crypto.open_interest | coinglass.raw_futures_open_interest_history | success | 768 |  |  |
| crypto.long_short_ratio | coinglass.futures_long_short_ratio | success | 768 |  |  |
| crypto.long_short_ratio | coinglass.raw_futures_top_long_short_account_ratio_history | success | 768 |  |  |
| crypto.long_short_ratio | coinglass.raw_futures_top_long_short_position_ratio_history | success | 768 |  |  |
| crypto.long_short_ratio | coinglass.raw_hyperliquid_global_long_short_account_ratio_history | success | 768 |  |  |
| crypto.liquidation | coinglass.futures_liquidation | success | 768 |  |  |
| crypto.liquidation | coinglass.raw_futures_liquidation_history | success | 768 |  |  |
| crypto.liquidation_heatmap | coinglass.futures_liquidation_heatmap | success | 37984 |  |  |
| crypto.cvd | coinglass.spot_cvd_history | success | 768 |  |  |
| crypto.cvd | coinglass.futures_cvd_history | success | 768 |  |  |
| crypto.cvd | coinglass.raw_futures_cvd_history | success | 768 |  |  |
| crypto.cvd | coinglass.raw_spot_cvd_history | success | 768 |  |  |
| crypto.taker_buy_sell | coinglass.futures_taker_buy_sell | success | 768 |  |  |
| crypto.exchange_netflow | coinglass.spot_coin_netflow | success | 1 |  |  |
| crypto.exchange_netflow | coinglass.futures_coin_netflow | success | 1 |  |  |
| crypto.exchange_netflow | coinglass.raw_futures_netflow_list | success | 100 |  |  |
| crypto.exchange_netflow | coinglass.raw_spot_netflow_list | success | 100 |  |  |
| crypto.options_open_interest | coinglass.options_open_interest | success | 2169 |  |  |
| crypto.options_volume | coinglass.options_volume | success | 2169 |  |  |
| crypto.etf_flow | coinglass.etf_bitcoin_flow_history | success | 6923 |  |  |
| crypto.etf_flow | coinglass.etf_ethereum_flow_history | success | 4447 |  |  |
| crypto.etf_flow | coinglass.raw_etf_hype_flow_history | success | 39 |  |  |
| crypto.etf_flow | coinglass.raw_etf_solana_flow_history | success | 78 |  |  |
| crypto.etf_flow | coinglass.raw_etf_xrp_flow_history | success | 730 |  |  |
| crypto.etf_flow | coinglass.raw_hk_etf_bitcoin_flow_history | success | 1539 |  |  |
| crypto.borrow_interest_rate | coinglass.raw_borrow_interest_rate_history | success | 768 |  |  |
| crypto.exchange_balance | coinglass.onchain_exchange_balance | success | 672 |  |  |
| crypto.whale_transfer | coinglass.onchain_whale_transfer | success | 1000 |  |  |
| crypto.ahr999 | coinglass.bitcoin_ahr999 | success | 5614 |  |  |
| crypto.onchain_metric | coinglass.onchain_exchange_balance | success | 672 |  |  |
| crypto.onchain_metric | coinglass.onchain_whale_transfer | success | 1000 |  |  |
| crypto.onchain_metric | coinglass.raw_index_bitcoin_active_addresses | success | 5781 |  |  |
| crypto.onchain_metric | coinglass.raw_index_bitcoin_lth_sopr | success | 5814 |  |  |
| crypto.onchain_metric | coinglass.raw_index_bitcoin_net_unrealized_profit_loss | success | 6372 |  |  |
| crypto.onchain_metric | coinglass.raw_index_bitcoin_sth_sopr | success | 5816 |  |  |
| crypto.onchain_metric | coinglass.raw_index_stablecoin_marketcap_history | success | 4124 |  |  |
| crypto.defi_metric | not_implemented | not_implemented |  |  |  |
| crypto.company_profile | not_implemented | not_implemented |  |  |  |
| crypto.company_news | coinglass.raw_article_list | success | 20 |  |  |
| crypto.macro_series | not_implemented | not_implemented |  |  |  |
| crypto.macro_news | coinglass.raw_article_list | success | 20 |  |  |
| crypto.macro_news | coinglass.raw_calendar_economic_data | success | 732 |  |  |
| crypto.event_calendar | coinglass.raw_calendar_economic_data | success | 732 |  |  |
| crypto.social_signal | coinglass.raw_index_fear_greed_history | success | 3036 |  |  |
