---
name: hk-fundamental-data
version: 0.1.0
description: HK fundamental data pack skill for fundamental_analyst; exposes only the claw-trade fundamental pack tool.
tool: claw_get_fundamental_pack
tool_name: claw_get_fundamental_pack
entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js
schema_version: claw_data_fundamental_pack.v1
---

# hk-fundamental-data

This skill provides only the canonical `claw_get_fundamental_pack` claw-trade data layer fundamental pack capability for HK reports.

It does not write the final report, investment decision, rating, target price, or provider logic.
