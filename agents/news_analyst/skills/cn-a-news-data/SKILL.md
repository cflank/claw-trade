---
name: cn-a-news-data
version: 0.1.0
description: CN_A 新闻资料包数据服务 skill，仅向 news_analyst 提供取数资料包能力。
tool: claw_get_news_pack
tool_name: claw_get_news_pack
entrypoint: openclaw_plugins/claw-trade-frontline-tools/index.js
schema_version: openbb_news_pack.v1
---

# cn-a-news-data

该 skill 只负责提供 `claw_get_news_pack` OpenBB 新闻资料包能力，不负责生成最终报告正文。
