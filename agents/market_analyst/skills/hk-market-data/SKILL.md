---
name: hk-market-data
version: 0.1.0
description: HK market data pack skill for market_analyst; exposes only the claw-trade market pack tool.
tool: claw_get_market_pack
tool_name: claw_get_market_pack
entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js
schema_version: claw_data_market_pack.v1
---

# hk-market-data

This skill provides only the canonical `claw_get_market_pack` claw-trade data layer market pack capability for HK reports.

It does not write the final report, investment decision, rating, target price, or provider logic.
