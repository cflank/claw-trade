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

- 市场级情绪来源只用于市场级情绪指标。
- 预测市场或事件预期来源只用于事件预期盘口。
- Brave Search / Bocha / SerpAPI / Tavily / Exa 只用于公开讨论线索发现。
- 具体社交平台或聚合社交来源只有在对应 credential/config 存在且 provider 返回可匹配 payload 时才算覆盖。
- provider 缺 key、失败或为空时必须写入 `provider_attempts` 与 `data_gaps`。
