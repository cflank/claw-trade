---
id: crypto-social-sentiment
name: CRYPTO social sentiment data pack
tool: claw_get_social_pack
tool_name: claw_get_social_pack
schema_version: claw_data_social_pack.v1
bound_workers:
  - social_analyst
profiles:
  - CRYPTO
---

该 skill 只负责向 `social_analyst` 暴露 `claw_get_social_pack` claw-trade 数据层社交资料包能力，不负责生成报告正文、投资判断或舆情结论。

边界：

- 具体来源选择、优先级、凭证、限流和补充源由 claw-trade 数据层负责；本 skill 不声明固定数据源范围。
- 市场级情绪、事件预期和公开讨论线索只能按本次资料包明示的来源可靠性使用。
- 具体社交平台或聚合社交来源只有在资料包返回可匹配材料时才算覆盖。
- 来源缺凭证、失败或为空时，必须保留来源尝试记录与资料缺口。
