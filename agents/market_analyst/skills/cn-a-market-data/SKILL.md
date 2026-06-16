---
name: cn-a-market-data
version: 0.1.0
description: CN_A 行情数据服务 skill，仅向 market_analyst 提供按需取数能力。
tool: claw_request_data
tool_name: claw_request_data
entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js
schema_version: claw_data_need.v1
---

# cn-a-market-data

该 skill 只负责提供 `claw_request_data` claw-trade 数据层行情材料能力，不负责生成最终报告正文、投资结论、评级或目标价。
