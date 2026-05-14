---
name: us-fundamental-data
version: 0.1.0
description: Original TradingAgents-style US yfinance fundamental tools for fundamental_analyst.
tools:
  - get_fundamentals
  - get_balance_sheet
  - get_cashflow
  - get_income_statement
entrypoint: openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/us_data_pack.py
schema_version: original_tradingagents_us_fundamental_tools.v1
---

# us-fundamental-data

This skill provides only the original TradingAgents-style `get_fundamentals`, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` yfinance tool capabilities. It does not write the final report, investment decision, rating, or target price.
