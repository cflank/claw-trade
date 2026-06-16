# Coinglass Project Interface Probe

- generated_at: `2026-06-16T00:53:55.002100+00:00`
- symbol: `BTC/USDT`
- catalog_only: `True`

| api | endpoint | status | rows | missing declared fields | error |
| --- | --- | --- | ---: | --- | --- |
| crypto.daily_bar | coinglass.futures_price_history | implemented |  |  |  |
| crypto.daily_bar | coinglass.spot_price_history | implemented |  |  |  |
| crypto.intraday_bar | coinglass.futures_price_history | implemented |  |  |  |
| crypto.intraday_bar | coinglass.spot_price_history | implemented |  |  |  |
| crypto.realtime_quote | coinglass.spot_coins_markets | implemented |  |  |  |
| crypto.order_book | not_implemented | not_implemented |  |  |  |
| crypto.valuation_metric | coinglass.spot_coins_markets | implemented |  |  |  |
| crypto.valuation_metric | coinglass.coin_market_data_history | implemented |  |  |  |
| crypto.funding_rate | coinglass.futures_funding_rate | implemented |  |  |  |
| crypto.funding_rate | coinglass.raw_futures_funding_rate_history | implemented |  |  |  |
| crypto.funding_rate | coinglass.raw_futures_funding_rate_vol_weight_history | implemented |  |  |  |
| crypto.open_interest | coinglass.futures_open_interest_aggregated_history | implemented |  |  |  |
| crypto.open_interest | coinglass.raw_futures_open_interest_aggregated_coin_margin_history | implemented |  |  |  |
| crypto.open_interest | coinglass.raw_futures_open_interest_aggregated_stablecoin_history | implemented |  |  |  |
| crypto.open_interest | coinglass.raw_futures_open_interest_history | implemented |  |  |  |
| crypto.long_short_ratio | coinglass.futures_long_short_ratio | implemented |  |  |  |
| crypto.long_short_ratio | coinglass.raw_futures_top_long_short_account_ratio_history | implemented |  |  |  |
| crypto.long_short_ratio | coinglass.raw_futures_top_long_short_position_ratio_history | implemented |  |  |  |
| crypto.long_short_ratio | coinglass.raw_hyperliquid_global_long_short_account_ratio_history | implemented |  |  |  |
| crypto.liquidation | coinglass.futures_liquidation | implemented |  |  |  |
| crypto.liquidation | coinglass.raw_futures_liquidation_history | implemented |  |  |  |
| crypto.liquidation_heatmap | coinglass.futures_liquidation_heatmap | implemented |  |  |  |
| crypto.cvd | coinglass.spot_cvd_history | implemented |  |  |  |
| crypto.cvd | coinglass.futures_cvd_history | implemented |  |  |  |
| crypto.cvd | coinglass.raw_futures_cvd_history | implemented |  |  |  |
| crypto.cvd | coinglass.raw_spot_cvd_history | implemented |  |  |  |
| crypto.taker_buy_sell | coinglass.futures_taker_buy_sell | implemented |  |  |  |
| crypto.exchange_netflow | coinglass.spot_coin_netflow | implemented |  |  |  |
| crypto.exchange_netflow | coinglass.futures_coin_netflow | implemented |  |  |  |
| crypto.exchange_netflow | coinglass.raw_futures_netflow_list | implemented |  |  |  |
| crypto.exchange_netflow | coinglass.raw_spot_netflow_list | implemented |  |  |  |
| crypto.options_open_interest | coinglass.options_open_interest | implemented |  |  |  |
| crypto.options_volume | coinglass.options_volume | implemented |  |  |  |
| crypto.etf_flow | coinglass.etf_bitcoin_flow_history | implemented |  |  |  |
| crypto.etf_flow | coinglass.etf_ethereum_flow_history | implemented |  |  |  |
| crypto.etf_flow | coinglass.raw_etf_hype_flow_history | implemented |  |  |  |
| crypto.etf_flow | coinglass.raw_etf_solana_flow_history | implemented |  |  |  |
| crypto.etf_flow | coinglass.raw_etf_xrp_flow_history | implemented |  |  |  |
| crypto.etf_flow | coinglass.raw_hk_etf_bitcoin_flow_history | implemented |  |  |  |
| crypto.borrow_interest_rate | coinglass.raw_borrow_interest_rate_history | implemented |  |  |  |
| crypto.exchange_balance | coinglass.onchain_exchange_balance | implemented |  |  |  |
| crypto.whale_transfer | coinglass.onchain_whale_transfer | implemented |  |  |  |
| crypto.ahr999 | coinglass.bitcoin_ahr999 | implemented |  |  |  |
| crypto.onchain_metric | coinglass.onchain_exchange_balance | implemented |  |  |  |
| crypto.onchain_metric | coinglass.onchain_whale_transfer | implemented |  |  |  |
| crypto.onchain_metric | coinglass.raw_index_bitcoin_active_addresses | implemented |  |  |  |
| crypto.onchain_metric | coinglass.raw_index_bitcoin_lth_sopr | implemented |  |  |  |
| crypto.onchain_metric | coinglass.raw_index_bitcoin_net_unrealized_profit_loss | implemented |  |  |  |
| crypto.onchain_metric | coinglass.raw_index_bitcoin_sth_sopr | implemented |  |  |  |
| crypto.onchain_metric | coinglass.raw_index_stablecoin_marketcap_history | implemented |  |  |  |
| crypto.defi_metric | not_implemented | not_implemented |  |  |  |
| crypto.company_profile | not_implemented | not_implemented |  |  |  |
| crypto.company_news | coinglass.raw_article_list | implemented |  |  |  |
| crypto.macro_series | not_implemented | not_implemented |  |  |  |
| crypto.macro_news | coinglass.raw_article_list | implemented |  |  |  |
| crypto.macro_news | coinglass.raw_calendar_economic_data | implemented |  |  |  |
| crypto.event_calendar | coinglass.raw_calendar_economic_data | implemented |  |  |  |
| crypto.social_signal | coinglass.raw_index_fear_greed_history | implemented |  |  |  |
