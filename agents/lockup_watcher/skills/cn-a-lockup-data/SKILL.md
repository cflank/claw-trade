---
name: cn-a-lockup-data
version: 0.1.0
description: CN_A 解禁与筹码资料包数据服务 skill，仅向 lockup_watcher 提供取数资料包能力。
tool: claw_get_lockup_pack
tool_name: claw_get_lockup_pack
entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js
schema_version: claw_data_lockup_pack.v1
---

# cn-a-lockup-data

该 skill 只负责提供 `claw_get_lockup_pack` claw-trade 数据层解禁与筹码资料包能力，不负责生成最终报告正文、投资结论或评级。
