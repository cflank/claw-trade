---
name: alphaear-stock
description: Search A-Share/HK/US finance stock tickers and retrieve finance stock price history. Use when user asks about finance stock codes, recent price changes, or specific company finance stock info.
---

# AlphaEar Stock Skill

Primary evidence source for structured ticker, price, and fundamentals evidence.
Provides real market data lookup.
Invocation boundary: `alphaear-stock` is not a callable tool name; use only the runtime-listed tool names for stock lookup.

- Capabilities: ticker search, price history, fundamentals.
- Use returned price rows, company fields, and error fields as evidence.
- If data is unavailable, state the limitation plainly instead of inventing values.
