---
name: alphaear-techlab
description: Compute technical indicators and render professional market charts for the current ticker.
tool: market_market_data_pack
tool_name: market_market_data_pack
entrypoint: scripts/techlab_entrypoint.py
entrypoint_command: market-pack
schema_version: market_data_pack.v1
---

# AlphaEar TechLab

Secondary technical layer once market data is available.
Provides technical indicators and market charts.
Invocation boundary: `alphaear-techlab` is not a callable tool name; use only the runtime-listed tool names for technical analysis.

- Formal market-data package entry: `market_market_data_pack` returns price rows, indicators, and chart paths from one skill call.
- technical indicators: BOLL, ATR, KDJ, MACD, RSI, moving averages, support, resistance.
- market charts: price structure and indicator panels.
- Use returned indicator values, chart paths, and error fields as evidence.
- If chart or indicator generation is unavailable, state the limitation plainly instead of inventing values.
