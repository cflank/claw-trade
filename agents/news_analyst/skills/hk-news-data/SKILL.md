---
name: hk-news-data
version: 0.1.0
description: HK news data pack skill for news_analyst; exposes only the claw-trade news pack tool.
tool: claw_get_news_pack
tool_name: claw_get_news_pack
entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js
schema_version: claw_data_news_pack.v1
---

# hk-news-data

This skill provides only the canonical `claw_get_news_pack` claw-trade data layer news pack capability for HK reports.

It does not write the final report, investment decision, rating, target price, or provider logic.
