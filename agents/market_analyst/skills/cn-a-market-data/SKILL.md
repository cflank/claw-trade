---
name: cn-a-market-data
version: 0.1.0
description: CN_A 行情资料包数据服务 skill，仅向 market_analyst 提供取数资料包能力。
tool: claw_get_market_pack
tool_name: claw_get_market_pack
entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js
schema_version: claw_data_market_pack.v1
---

# cn-a-market-data

该 skill 只负责提供 `claw_get_market_pack` claw-trade 数据层行情资料包能力，不负责生成最终报告正文、投资结论、评级或目标价。
