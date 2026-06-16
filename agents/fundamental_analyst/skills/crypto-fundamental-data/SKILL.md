---
name: crypto-fundamental-data
version: 0.1.0
description: CRYPTO 基本面数据服务 skill，仅向 fundamental_analyst 提供 claw-trade 数据层按需取数能力。
tool: claw_request_data
tool_name: claw_request_data
schema_version: claw_data_need.v1
---

# crypto-fundamental-data

该 skill 只负责提供 `claw_request_data` claw-trade 数据层基本面材料能力，不负责生成最终报告正文、投资结论、评级或目标价。

使用边界：

- 具体来源选择、优先级、凭证、限流和补充源由 claw-trade 数据层负责；本 skill 不声明固定数据源范围。
- `claw_request_data` 返回什么字段、单位和来源说明，分析师就只能使用什么；未返回的维度必须保留为资料缺口。
- 不把缺失的解锁、治理、收入、链上活跃度、开发者活跃度或公告数据补写成事实。
