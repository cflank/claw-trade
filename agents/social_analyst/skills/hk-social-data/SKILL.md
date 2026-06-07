---
name: hk-social-data
version: 0.1.0
description: HK social data pack skill for social_analyst; exposes only the claw-trade social pack tool.
tool: claw_get_social_pack
tool_name: claw_get_social_pack
entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js
schema_version: claw_data_social_pack.v1
---

# hk-social-data

This skill provides only the canonical `claw_get_social_pack` claw-trade data layer social pack capability for HK reports.

It does not write the final report, investment decision, rating, target price, social sentiment conclusion, or provider logic.
