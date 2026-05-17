# CRYPTO 新闻与舆情资料包详细设计

日期：2026-05-16

## 1. 结论

目标不是“全网都抓”，而是把设计内的 provider 代码路径全部落地，并且让没有 credential/config 的部分显式成为缺口。当前共识是：

- `crypto_news_data_pack` 已实现官方公告/RSS/页面、GitHub releases、交易所公告配置源、监管 feed、DefiLlama 安全/融资背景、Polymarket 事件预期和商业搜索发现。
- `crypto_social_sentiment_pack` 已实现 Alternative.me、LunarCrush、X、Reddit、Telegram、Discord、Polymarket 和商业搜索发现。
- Brave Search、Bocha、NewsAPI、SerpAPI、Tavily 作为可选搜索 provider：用户填了 key 就调用；没填就不调用，但必须写入 provider attempt / data gap，不能静默消失。
- Alternative.me 和 Polymarket 可以零 key 接入，但它们不是社交平台：Alternative.me 是市场级情绪指标，Polymarket 是事件预期盘口。
- Scrapling 暂不接入；当前资料包没有 Scrapling provider、工具依赖或 stage 依赖。未来如需接入，只能作为指定公开页面提取工具另行评审，不能当新闻源或舆情源。
- 不新增 runtime gate / guard；资料包只输出数据质量材料，stage 是否打开仍由人类批准和现有 stage policy 控制。

最新人类决策是：正式 12 个 CRYPTO TradingAgents worker 和 `report_polisher` 全部打开。`news_analyst` 和 `social_analyst` 必须通过资料包调用拿材料。若资料包不可见、未调用成功、provider 缺 key、provider 失败或返回 `partial` / `insufficient`，worker 的 L1 报告必须直接说明资料缺口和后续取数需求，不能补写真实新闻事实、真实社交舆情结论或完整覆盖声明。`report_polisher` 只整理已批准上游报告，不补写缺失新闻、舆情或投资判断。

## 2. 架构边界

必须保持以下边界：

- claw-trade 控制 12-worker workflow。
- OpenClaw 只运行单 worker turn。
- worker 通过 OpenClaw 可见工具调用资料包。
- Python 只负责 provider 调用、结构化整理、证据写入和缺口标注。
- Python 不写新闻判断、不写舆情判断、不替 worker 下投资结论。
- OpenViking 只存 approved L1/L2 材料和证据，不当新闻、舆情、行情或链上数据源。
- 通用搜索 API、Polymarket、Alternative.me 都不直接暴露给 worker；Scrapling 当前不接入；worker 只看到封装后的资料包工具。

## 3. 工具范围

### 3.1 `crypto_news_data_pack`

面向 worker：`news_analyst`

目标：给新闻分析员提供可追溯的新闻事件材料。

已实现覆盖：

- 项目官方公告、博客、RSS 或公开页面，来自 `CRYPTO_NEWS_OFFICIAL_SOURCES_JSON`。
- GitHub releases，来自 `CRYPTO_NEWS_GITHUB_REPOS_JSON`。
- 交易所公告源，来自 `CRYPTO_NEWS_EXCHANGE_SOURCES_JSON`。
- SEC / CFTC / Fed 或其他监管 feed，来自 `CRYPTO_NEWS_REGULATORY_SOURCES_JSON` 或默认监管源开关。
- DefiLlama Pro hacks / raises，用于安全和融资事件背景。
- Polymarket 公共搜索，用于事件预期盘口。
- Brave / Bocha / NewsAPI / SerpAPI / Tavily 搜索发现。

不覆盖：

- 社交平台情绪分析。
- Telegram / Discord 私域群聊。
- X 全量舆情。
- Reddit API 舆情。
- 付费新闻全文。
- 用搜索摘要直接证明新闻事实。

### 3.2 `crypto_social_sentiment_pack`

面向 worker：`social_analyst`

目标：给社交/情绪分析员提供市场情绪、事件预期和公开讨论线索。

已实现覆盖：

- Alternative.me Crypto Fear & Greed，作为市场级情绪指标。
- LunarCrush 聚合社交指标，需 `LUNARCRUSH_API_KEY`。
- X recent search，需 `X_BEARER_TOKEN` 或 `TWITTER_BEARER_TOKEN`。
- Reddit OAuth search，需 `REDDIT_BEARER_TOKEN`。
- Telegram Bot API `getUpdates`，需 `TELEGRAM_BOT_TOKEN`，只覆盖 bot 可访问消息。
- Discord channel messages，需 `DISCORD_BOT_TOKEN` 与 `CRYPTO_SOCIAL_DISCORD_CHANNEL_IDS_JSON`，只覆盖 bot 可访问频道。
- Polymarket 公共 market / event 数据，作为事件预期和概率定价。
- Brave / Bocha / SerpAPI / Tavily 搜索到的公开讨论页面线索，例如 Reddit 网页、论坛、项目社区公开页面、媒体评论页。

不覆盖：

- 私域、登录态、绕反爬、账号抓取。

报告必须把能力名称写清楚：只有真实 provider payload 返回的平台才算覆盖；搜索发现、Alternative.me 和 Polymarket 不能冒充完整社交舆情。

## 4. 数据源分层

### 4.1 不需要 key 的事实源

这些优先接入，因为它们更像事实源：

| 来源 | 放入资料包 | 作用 | 注意事项 |
|---|---|---|---|
| 项目官网公告 / Blog / RSS | news | 项目升级、合作、路线图、安全公告 | 每个项目 URL 需要配置或搜索发现 |
| GitHub releases / tags | news | 客户端升级、协议版本发布 | 只适合开源项目 |
| 交易所公告公开页 | news | 上币、下架、维护、合约调整 | 页面结构可能变化 |
| SEC RSS / 新闻页 | news | 监管、ETF、执法新闻 | 只覆盖 SEC 范围 |
| CFTC RSS / 新闻页 | news | 衍生品、合约、执法新闻 | 只覆盖 CFTC 范围 |
| Fed RSS | news | 宏观利率、流动性、政策事件 | 不是加密专门新闻 |
| DefiLlama hacks / raises | news | 黑客、安全、融资事件背景 | 不是新闻媒体 |

### 4.2 需要 key 但可选的搜索发现源

这些源不直接证明事实，只用于发现链接、补充覆盖和多语言搜索。

| 环境变量 | provider | 放入资料包 | 用途 | 缺 key 行为 |
|---|---|---|---|---|
| `BRAVE_SEARCH_API_KEY` | Brave Search | news/social | 英文网页和新闻搜索 | 记录 `credential_missing`，不调用 |
| `BOCHA_API_KEY` | Bocha | news/social | 中文/多语言搜索 | 记录 `credential_missing`，不调用 |
| `NEWSAPI_API_KEY` | NewsAPI | news | 新闻检索 | 记录 `credential_missing`，不调用 |
| `SERPAPI_API_KEY` | SerpAPI | news/social | Google / 新闻 / 网页搜索 | 记录 `credential_missing`，不调用 |
| `TAVILY_API_KEY` | Tavily | news/social | AI search / 网页搜索 | 记录 `credential_missing`，不调用 |
| `EXA_API_KEY` | Exa | news/social | 网页搜索 | 记录 `credential_missing`，不调用 |

注意：

- NewsAPI 免费 Developer 档只适合开发测试，有 24 小时延迟和请求限制，不能当生产实时新闻主源。
- SerpAPI 免费档通常只有有限月度额度。
- Brave Search 当前有免费额度或免费信用，但通常仍需 key 和账号。
- Bocha 额度以平台实际账户为准。

### 4.3 不需要 key 的情绪 / 事件预期源

| 来源 | 放入资料包 | 作用 | 不能写成 |
|---|---|---|---|
| Alternative.me Fear & Greed | social | 市场级恐惧/贪婪情绪 | 社交舆情、社区共识 |
| Polymarket public API | news/social | 事件预期、盘口概率 | 新闻事实、社交共识 |

Polymarket 只能表达“市场如何给事件定价”。如果某个事件盘口显示概率上升，报告只能写“事件预期升温”，不能写“事件已经发生”。

### 4.4 仍不做的方式

| 来源 | 原因 |
|---|---|
| 无授权 Reddit/X/Telegram/Discord 抓取 | 不绕过平台权限 |
| Telegram / Discord 全网舆情 | Bot API 只能读 bot 可访问更新或频道 |
| Scrapling | 暂不接入；不作为当前新闻或舆情资料包依赖 |

## 5. 输入输出合同

### 5.1 共同输入

两个资料包都接受同类输入：

```json
{
  "ticker": "BTC",
  "asset_name": "Bitcoin",
  "market": "CRYPTO",
  "lookback_days": 7,
  "topics": ["ETF", "regulation", "upgrade"],
  "official_urls": [],
  "exchange_watchlist": ["binance", "coinbase", "okx", "kraken"]
}
```

说明：

- `ticker` 和 `market` 必填。
- `asset_name` 可选，但有助于构造搜索查询。
- 官方、GitHub、交易所、监管和社交平台源通过环境配置提供；未配置时记录 `config_blocked`，不能用搜索发现替代。
- `lookback_days` 默认 7 天。

### 5.2 `crypto_news_data_pack` 输出

```json
{
  "schema_version": "crypto_news_data_pack.v1",
  "tool_name": "crypto_news_data_pack",
  "asset": "BTC",
  "market": "CRYPTO",
  "as_of": "2026-05-16T12:00:00Z",
  "data": {
    "official_announcements": [],
    "github_releases": [],
    "exchange_announcements": [],
    "regulatory_sources": [],
    "defillama_event_background": {
      "security_incidents": [],
      "funding_events": []
    },
    "polymarket_event_expectations": [],
    "commercial_search_discoveries": []
  },
  "sources": [],
  "provider_attempts": [],
  "data_gaps": [],
  "conflicts": [],
  "readiness": {
    "status": "partial",
    "reason": "official/regulatory sources available; social sources not part of this pack"
  },
  "reader_brief": ""
}
```

关键字段：

- `official_announcements`：官网、博客、RSS 或指定公开页面。
- `github_releases`：GitHub release API 返回的项目发布材料。
- `exchange_announcements`：交易所上币、下架、维护、合约调整。
- `regulatory_sources`：SEC / CFTC / Fed 或其他配置的监管源。
- `defillama_event_background.security_incidents` / `funding_events`：DefiLlama hacks / raises 背景。
- `commercial_search_discoveries`：搜索 API 发现的候选链接，不能直接当事实。
- `polymarket_event_expectations`：Polymarket 盘口，必须标注为预期。

### 5.3 `crypto_social_sentiment_pack` 输出

```json
{
  "schema_version": "crypto_social_sentiment_pack.v1",
  "tool_name": "crypto_social_sentiment_pack",
  "asset": "BTC",
  "market": "CRYPTO",
  "as_of": "2026-05-16T12:00:00Z",
  "data": {
    "market_level_sentiment": [],
    "polymarket_event_expectations": [],
    "public_discussion_discoveries": [],
    "social_platform_metrics": {
      "lunarcrush": []
    },
    "social_platform_posts": {
      "x": [],
      "reddit": [],
      "telegram": [],
      "discord": []
    },
    "true_social_platform_coverage": {
      "x": "missing_credentials_or_config",
      "reddit": "missing_credentials_or_config",
      "telegram": "missing_credentials_or_config",
      "discord": "missing_credentials_or_config",
      "lunarcrush": "missing_credentials_or_config"
    }
  },
  "sources": [],
  "provider_attempts": [],
  "data_gaps": [],
  "conflicts": [],
  "readiness": {
    "status": "partial",
    "reason": "market sentiment and event expectations available; no direct social API coverage"
  },
  "reader_brief": ""
}
```

关键字段：

- `market_level_sentiment`：Alternative.me Fear & Greed。
- `polymarket_event_expectations`：Polymarket 事件概率。
- `public_discussion_discoveries`：搜索 API 找到的公开讨论页面。
- `social_platform_metrics.lunarcrush`：LunarCrush 聚合社交指标。
- `social_platform_posts`：X、Reddit、Telegram、Discord 的真实 provider payload 样本。
- `true_social_platform_coverage`：逐平台说明是否真实覆盖、配置但空/失败，或缺 credential/config。

## 6. Provider attempt 规则

每个计划中的 provider 都必须产生 attempt 记录。

推荐状态：

| status | 含义 |
|---|---|
| `success` | 调用成功并返回可解析数据 |
| `empty` | 调用成功但没有相关结果 |
| `config_blocked` | 缺 key、缺 URL、缺配置，不发起请求 |
| `auth_failed` | key 存在但认证失败 |
| `rate_limited` | 被限流 |
| `provider_error` | provider 返回错误 |
| `parse_error` | 原始响应无法解析成合同字段 |

缺 key 行为：

- 不发起 HTTP 请求。
- 记录 `provider_attempts`。
- 追加 `data_gaps`。
- 资料包可以是 `partial`，但不能把该 provider 视为成功。

搜索结果行为：

- 搜索结果必须进入 `commercial_search_discoveries` 或 `public_discussion_discoveries`。
- 只有当结果 URL 属于官方、交易所、监管或明确可信来源时，才能升级为事实材料。
- 搜索摘要不能直接写成已确认新闻事实。

## 7. 去重与可信度

新闻材料按可信度分层：

| level | 来源类型 | 报告使用方式 |
|---|---|---|
| `official` | 项目官网、官方博客、GitHub release、交易所公告、监管机构 | 可作为事实依据 |
| `primary_media` | 原始媒体报道、采访、公司声明转载 | 可作为报道依据，需保留来源 |
| `search_result` | Brave / Bocha / NewsAPI / SerpAPI / Tavily 结果 | 只能作为发现线索 |
| `discussion` | 论坛、公开社区页面、评论区 | 只能作为讨论样本 |
| `prediction_market` | Polymarket | 只能作为事件预期 |
| `market_sentiment_index` | Alternative.me | 只能作为市场级情绪 |

去重规则：

- URL 规范化后相同则去重。
- 标题相似且来源相同则去重。
- 同一事件多来源报道要保留最高可信来源，同时保留被合并来源数量。
- 发布时间缺失时保留该条，但降低可信度，并写入 `data_gaps`。

## 8. Evidence 写入

每次资料包调用至少写入：

- `{tool_name}.json`：标准化资料包。
- `provider_attempts.json`：所有 provider attempt。
- `provider_raw/<provider>/<attempt_id>.json`：成功或失败的原始 payload 摘要。

证据要求：

- 不保存 API key。
- 不把完整网页正文塞给 worker。
- 不把 raw debug JSON 作为 worker 主材料。
- `reader_brief` 只能总结事实、来源和缺口，不能写投资判断。

## 9. Worker 与 stage 策略

### 9.1 `news_analyst`

当前 stage 状态：已打开。

当前工具状态：

- 已注册并挂载 `crypto_news_data_pack`。
- 不把搜索 API、Polymarket 或 Alternative.me 直接暴露给 worker；Scrapling 当前不接入。
- worker 必须先调用资料包；若资料包不可见、调用失败、缺 key、返回空或 `insufficient`，必须输出新闻资料缺口报告。

验证条件：

- `crypto_news_data_pack` 工具已注册。
- OpenClaw provider payload 证明 `news_analyst` 当前 turn 只看到 `crypto_news_data_pack`。
- 真实调用至少返回一个成功/空/缺 key provider attempt，不允许无 evidence 成功。
- L1 新闻报告能明确区分官方事实、搜索发现、事件预期和缺口。

### 9.2 `social_analyst`

当前 stage 状态：已打开。

当前工具状态：

- 已注册并挂载 `crypto_social_sentiment_pack`。
- 不把搜索 API、Polymarket 或 Alternative.me 直接暴露给 worker；Scrapling 当前不接入。
- worker 必须先调用资料包；若资料包不可见、调用失败、缺 key、返回空或 `insufficient`，必须输出舆情资料缺口报告。

验证条件：

- `crypto_social_sentiment_pack` 工具已注册。
- OpenClaw provider payload 证明 `social_analyst` 当前 turn 只看到 `crypto_social_sentiment_pack`。
- 真实调用返回 Alternative.me、LunarCrush、X、Reddit、Telegram、Discord、Polymarket 或搜索 provider 的成功/空/缺 key attempt。
- L1 舆情报告必须写清楚哪些平台有真实 payload、哪些平台缺 credential/config 或返回空。

### 9.3 下游 worker

下游 worker 不直接挂新闻/舆情工具。

当前 stage 状态：已打开。

- `market_analyst`、`fundamental_analyst`、`news_analyst`、`social_analyst` 都必须产生 approved L1。
- 如果 news/social L1 是缺口报告，下游必须把缺口作为条件化推理边界，不能补写新闻事实、社交平台观点或社区共识。
- 如果 social 资料包缺少某个平台 payload，下游必须把它当作有限材料，不能补写 Reddit/X/Telegram/Discord 舆情。

## 10. 实施顺序

### 当前阶段：文档、配置和真实资料包

已完成或应保持：

- `.env.local` / `.env.example` 支持：
  - `BRAVE_SEARCH_API_KEY`
  - `BOCHA_API_KEY`
  - `NEWSAPI_API_KEY`
  - `SERPAPI_API_KEY`
  - `TAVILY_API_KEY`
  - `EXA_API_KEY`
  - `CRYPTO_NEWS_OFFICIAL_SOURCES_JSON`
  - `CRYPTO_NEWS_GITHUB_REPOS_JSON`
  - `CRYPTO_NEWS_EXCHANGE_SOURCES_JSON`
  - `CRYPTO_NEWS_REGULATORY_SOURCES_JSON`
  - `LUNARCRUSH_API_KEY`
  - `X_BEARER_TOKEN`
  - `REDDIT_BEARER_TOKEN`
  - `TELEGRAM_BOT_TOKEN`
  - `DISCORD_BOT_TOKEN`
  - `CRYPTO_SOCIAL_DISCORD_CHANNEL_IDS_JSON`
- 正式 12 个 CRYPTO worker 与 `report_polisher` 均打开。
- 已注册 `crypto_news_data_pack` 与 `crypto_social_sentiment_pack`，并挂到 CRYPTO `news_analyst` / `social_analyst`。
- 不声称完整新闻或完整社交舆情覆盖；缺 key、失败、空结果和未覆盖原始源必须进入 `provider_attempts` / `data_gaps`。

### 第二阶段：真实运行验证

验收：

- 缺 key provider 有 attempt。
- 搜索摘要不升级为事实。
- 官方/监管/交易所来源有 source URL。
- 单测覆盖 success / empty / missing key / auth failed / parse error。
- OpenClaw provider payload 证明 worker 实际只看到对应资料包工具。
- fresh tool call evidence 证明 provider attempts 被写入证据目录。

## 11. 不做事项

- 不新增 runtime gate / guard。
- 不把缺 key provider 静默忽略。
- 不把未覆盖的官方公告、GitHub、交易所公告、监管源或真实社交平台伪装成已覆盖。
- 不把 Polymarket 当新闻事实源。
- 不把 Alternative.me 当社交舆情源。
- 不把搜索 API 当事实源。
- 不接入 Scrapling；不把 Scrapling MCP 全量暴露给 worker。
- 不抓登录态 X / Reddit / Telegram / Discord。
- 不让 Python 写新闻结论、舆情结论或投资判断。

## 12. 停止条件

遇到以下情况必须停止并问人类：

- 需要新增或修改 runtime gate / guard。
- 想声称 `news_analyst` 或 `social_analyst` 已有完整新闻/舆情覆盖，或想用 live 结果证明工具可见性但没有 provider payload 和真实 tool call 证据。
- 想把搜索结果直接写成新闻事实。
- 想把 Polymarket 写成已发生新闻。
- 想把 Alternative.me 写成社交平台舆情。
- 想接入 Scrapling，或想用 Scrapling 绕过平台登录、反爬或服务条款。
- provider 连续两次同类认证/限流失败后仍要继续扩大调用。
- 需要保存或打印 API key。

## 13. 参考

- NewsAPI pricing: https://newsapi.org/pricing
- NewsAPI auth: https://newsapi.org/docs/authentication
- Brave Search API: https://brave.com/search/api
- Bocha open platform: https://open.bochaai.com/
- SerpAPI pricing: https://serpapi.com/pricing
- Exa search API: https://docs.exa.ai/reference/search
- SEC RSS: https://www.sec.gov/about/secrss
- CFTC RSS: https://www.cftc.gov/RSS/index.htm
- Fed RSS: https://www.federalreserve.gov/feeds/
- Alternative.me Crypto API: https://alternative.me/crypto/api/
- Polymarket public API docs: https://docs.polymarket.com/developers/misc-endpoints/data-api-holders
- Scrapling: https://github.com/D4Vinci/Scrapling
