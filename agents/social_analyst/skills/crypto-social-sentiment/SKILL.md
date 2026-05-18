---
id: crypto-social-sentiment
name: CRYPTO social sentiment data pack
tool: claw_get_social_pack
tool_name: claw_get_social_pack
schema_version: openbb_social_pack.v1
bound_workers:
  - social_analyst
profiles:
  - CRYPTO
---

该 skill 只负责向 `social_analyst` 暴露 `claw_get_social_pack` OpenBB 社交资料包能力，不负责生成报告正文、投资判断或舆情结论。

边界：

- Alternative.me 只用于市场级 Fear & Greed 指标。
- Polymarket 只用于事件预期盘口。
- Brave Search / Bocha / SerpAPI / Tavily / Exa 只用于公开讨论线索发现。
- X、Reddit、Telegram、Discord 或 LunarCrush 只有在对应 credential/config 存在且 provider 返回可匹配 payload 时才算覆盖。
- provider 缺 key、失败或为空时必须写入 `provider_attempts` 与 `data_gaps`。
