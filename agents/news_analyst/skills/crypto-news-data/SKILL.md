---
id: crypto-news-data
name: CRYPTO news data pack
tool: claw_get_news_pack
tool_name: claw_get_news_pack
schema_version: openbb_news_pack.v1
bound_workers:
  - news_analyst
profiles:
  - CRYPTO
---

该 skill 只负责向 `news_analyst` 暴露 `claw_get_news_pack` OpenBB 新闻资料包能力，不负责生成报告正文、投资判断或新闻结论。

边界：

- DefiLlama 只用于安全事件 / 融资事件背景。
- Polymarket 只用于事件预期盘口。
- Brave Search / Bocha / NewsAPI / SerpAPI / Tavily / Exa 只用于商业搜索发现。
- 搜索摘要不得直接写成新闻事实、官方公告、交易所公告或监管事实。
- provider 缺 key、失败或为空时必须写入 `provider_attempts` 与 `data_gaps`。
