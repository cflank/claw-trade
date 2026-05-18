---
name: alphaear-techlab
description: Compute technical indicators and render professional market charts for the current ticker.
tool: claw_get_market_pack
tool_name: claw_get_market_pack
entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js
entrypoint_command: market-pack
schema_version: openbb_market_pack.v1
---

# AlphaEar TechLab

Secondary technical layer once market data is available.
Provides technical indicators and market charts.
Invocation boundary: `alphaear-techlab` is not a callable tool name; use only the runtime-listed tool names for technical analysis.

- Formal market-data package entry: `claw_get_market_pack` returns price rows, indicators, and chart paths from one OpenBB skill call.
- technical indicators: BOLL, ATR, KDJ, MACD, RSI, moving averages, support, resistance.
- market charts: price structure and indicator panels.
- Use returned indicator values, chart paths, and error fields as evidence.
- If chart or indicator generation is unavailable, state the limitation plainly instead of inventing values.
