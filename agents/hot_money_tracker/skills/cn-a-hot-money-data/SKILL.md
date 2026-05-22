---
name: cn-a-hot-money-data
version: 0.1.0
description: CN_A 资金资料包数据服务 skill，仅向 hot_money_tracker 提供取数资料包能力。
tool: claw_get_hot_money_pack
tool_name: claw_get_hot_money_pack
entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js
schema_version: openbb_hot_money_pack.v1
---

# cn-a-hot-money-data

该 skill 只负责提供 `claw_get_hot_money_pack` OpenBB 资金资料包能力，不负责生成最终报告正文、投资结论或评级。
