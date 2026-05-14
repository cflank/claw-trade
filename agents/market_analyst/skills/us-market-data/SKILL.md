---
name: us-market-data
version: 0.1.0
description: Original TradingAgents-style US yfinance market tools for market_analyst.
tools:
  - get_stock_data
  - get_indicators
entrypoint: openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/us_data_pack.py
schema_version: original_tradingagents_us_market_tools.v1
---

# us-market-data

This skill provides only the original TradingAgents-style `get_stock_data` and `get_indicators` yfinance tool capabilities. It does not write the final report, investment decision, rating, or target price.
