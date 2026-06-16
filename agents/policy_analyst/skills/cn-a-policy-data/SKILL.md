---
name: cn-a-policy-data
version: 0.1.0
description: CN_A 政策数据服务 skill，仅向 policy_analyst 提供统一数据需求工具能力。
tool: claw_request_data
tool_name: claw_request_data
entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js
schema_version: claw_data_need.v1
---

# cn-a-policy-data

该 skill 只负责提供 `claw_request_data` claw-trade 数据层政策数据需求能力，不负责生成最终报告正文、投资结论或评级。
