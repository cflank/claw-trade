---
id: crypto-news-data
name: CRYPTO news data pack
tool: claw_get_news_pack
tool_name: claw_get_news_pack
schema_version: claw_data_news_pack.v1
bound_workers:
  - news_analyst
profiles:
  - CRYPTO
---

该 skill 只负责向 `news_analyst` 暴露 `claw_get_news_pack` claw-trade 数据层新闻资料包能力，不负责生成报告正文、投资判断或新闻结论。

边界：

- 具体来源选择、优先级、凭证、限流和补充源由 claw-trade 数据层负责；本 skill 不声明固定数据源范围。
- 资料包返回的安全事件、融资事件、事件预期和搜索发现，只能按本次资料包明示的来源可靠性使用。
- 搜索摘要不得直接写成新闻事实、官方公告、交易所公告或监管事实。
- 来源缺凭证、失败或为空时，必须保留来源尝试记录与资料缺口。
