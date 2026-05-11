# CN_A frontline provider 矩阵资料层落地方案

版本：v0.1  
日期：2026-05-08  
适用范围：`market_analyst`、`fundamental_analyst`、`news_analyst`、`social_analyst` 的 CN_A `/report` 前线资料获取链路

## 1. 结论

采用 **一 worker 一个资料包工具 + 资料包内部 provider 矩阵 + MongoDB 结构化缓存 + 自然语言材料 handoff** 的方案。

2026-05-10 人工裁决：正式 workflow 里不允许用 JSON 作为 worker 之间的信息传递格式。worker 之间只通过自然语言材料或自然语言 report 交接，口径对齐 TradingAgents-CN/原版。JSON 只能作为工具协议、provider 内部处理、MongoDB/cache、evidence/log 存在，不进入 worker 可见正文，不写入 OpenViking L1 主材料，也不作为 downstream handoff。

worker 仍只看到当前 stage 的少量工具：

```text
market_analyst:
  - market_market_data_pack
  - openviking_write_material

fundamental_analyst:
  - fundamental_fundamentals_data_pack
  - openviking_write_material

news_analyst:
  - news_news_data_pack
  - openviking_write_material

social_analyst:
  - social_social_sentiment_pack
  - openviking_write_material
```

底层 Tushare、AkShare、东方财富、Sina、Tencent、Baostock、Bocha、Tavily、Jina、NewsNow、alphaear 等能力只作为资料包内部 provider 或 adapter，不直接暴露给 worker；这些 provider 的结构化返回不得作为 worker 材料正文。

2026-05-09 人工裁决：CN_A 所有可由 Tushare 覆盖的数据域，默认以 Tushare 为第一主源；免费源作为备源、补源或交叉校验源。Tushare 初始化统一使用 `src/claw_trade/providers/tushare_client.py`，token 只来自环境变量或函数参数；默认走 Tushare SDK 标准地址，只有显式配置 `TUSHARE_HTTP_URL` 或 `CN_A_TUSHARE_HTTP_URL` 时才设置私有 HTTP URL。

## 2. 背景与问题

最新 CN_A frontline 测试已经证明：

- OpenClaw worker 唤醒、stage scoped tools、`openviking_write_material` 写入链路已基本成立。
- `*_llm_back.md` 已按正确口径抓取为模型调用 `openviking_write_material(content=...)` 时传入的报告正文。
- 4 个 frontline worker 都能提交报告。
- 真正拖累报告质量的是资料包证据链：
  - market 当前成功，但走的是独立的 alphaear 路径；
  - fundamental 因 Tushare 权限/配额和 AkShare 超时导致核心字段缺失；
  - news 出现 `invalid tool runtime context`，属于运行时/工具上下文问题；
  - social 受东方财富/AkShare 端点超时、结构变化、匹配失败和 evidence 写入失败影响。

当前最大架构偏差是 `market_analyst`：

```text
market_market_data_pack
  -> alphaear-techlab
  -> alphaear-stock
  -> local SQLite + AkShare + EastMoney direct + Sina + Tencent + yfinance
```

这条链路可以取到行情并生成图表，但它没有统一的：

- provider attempts
- MongoDB cache refs
- OpenViking L2 raw payload refs
- field-level sources
- quality 状态机
- failed/partial 明确判定

因此 market 应优先收束到与 fundamental/news/social 一致的资料包合同。

## 3. 设计原则

### 3.1 worker 保持纯粹

worker 是分析者，不是数据源编排器。

worker 可以调用本领域资料包工具，但不直接选择 AkShare、东方财富、Bocha、Tavily 或其它 provider。

worker 输出自然语言报告，并通过 `openviking_write_material` 提交正式报告。Python 不写报告，不给评级，不给目标价，不改 PM 决策。

### 3.2 provider fallback 允许，但必须显式可审计

允许资料包内部按矩阵尝试多个 provider。

禁止：

- 某个 provider 失败后静默换源，并在最终报告里表现为“数据完整”。
- cache 命中但 schema 过期，仍当作当前事实。
- OpenViking L2 写入失败，仍声称有可审计 raw evidence。
- 将 provider partial/failed 包装成 success。

每次 provider 尝试必须进入 `provider_attempts`。

`provider_attempts`、raw payload、normalized pack 等结构化记录只属于内部证据/排障层，不属于 workflow handoff。worker 能看到和下游能继承的主材料必须是自然语言正文。

### 3.3 MongoDB 与 OpenViking 权威不同

MongoDB 负责结构化缓存和去重：

- provider 原始结果缓存
- normalized rows
- freshness 检查
- schema/version 检查
- query fingerprint 去重

OpenViking 负责运行证据和材料流转：

- worker L1 正式报告
- provider raw payload L2
- pack L2
- chart refs
- attempts refs
- content hash
- receipt / read-back verification

MongoDB 中存在记录，不代表材料已批准；OpenViking 中存在材料，也不代表可以进入下游。下游读取仍以 `claw-trade` approved manifest 为准。

### 3.4 AlphaEar 只拆零件复用

`RKiding/Awesome-finance-skills` 是可安装金融 skill 集合，不是 claw-trade 的正式资料层。

可复用：

- `alphaear-stock` 中的免费行情 provider 思路。
- `alphaear-techlab` 的指标和图表计算能力。
- `alphaear-news` 的多源新闻/热点源清单思想。
- `alphaear-search` 的 Jina/DDG/Baidu 搜索思想。

不可直接接入为权威：

- `alphaear-reporter`：会冲突报告链路和 PM owner。
- `alphaear-predictor`：会引入难以 hard gate 的预测结论。
- worker 自由挂多个 alphaear skill：会回到“单 worker 自己拿工具并直接回答”的模式。

## 4. 总体架构

```text
UI / CLI / test 默认 /report
  |
  v
claw-trade workflow controller
  |
  | dispatch one worker
  v
OpenClaw single worker turn
  |
  | visible tools are narrowed by stage/profile
  v
worker calls one domain data pack tool
  |
  v
domain data pack service inside worker skill
  |
  +--> normalize input
  +--> inspect MongoDB cache
  +--> build provider plan
  +--> execute provider attempts
  +--> write raw payloads to OpenViking L2
  +--> upsert structured cache to MongoDB
  +--> normalize fields / rows / signals
  +--> compute quality + diagnostics
  +--> build reader_brief
  |
  v
worker writes reader-facing Markdown report
  |
  v
openviking_write_material
  |
  v
claw-trade validates receipt / evidence / gates
```

## 5. 统一资料包外壳

四个 frontline 资料包都应返回同类外壳。领域内容放在 `domain_data` 内。

```json
{
  "ok": true,
  "schema_version": "cn_a_frontline_pack.v1",
  "domain": "market",
  "run_id": "run-...",
  "stage": "frontline",
  "worker_id": "market_analyst",
  "call_id": "run-...-frontline-market_analyst-...",
  "tool_name": "market_market_data_pack",
  "input": {
    "ticker": "600519.SH",
    "market": "CN_A",
    "company_name": "贵州茅台",
    "start_date": "2026-03-07",
    "end_date": "2026-05-07"
  },
  "quality": {
    "status": "complete",
    "coverage_score": 0.92,
    "freshness_status": "fresh",
    "warnings": []
  },
  "provider_attempts": [],
  "field_sources": {},
  "raw_payload_refs": [],
  "mongo_cache_refs": [],
  "openviking_l2_refs": [],
  "diagnostic_flags": [],
  "reader_brief": "事实型中文资料摘要，不给投资结论。",
  "domain_data": {}
}
```

### 5.1 `quality.status` 规则

```text
complete:
  核心字段足以支撑该 worker 的正常报告章节。

partial:
  有可用证据，但缺少关键字段、来源过窄、时间不够新、图表缺失或部分 provider 失败。

failed:
  核心 provider 全失败、无 accepted target signal、OpenViking L2 写入关键证据失败，或 schema/context 错误导致资料不可审计。
```

`ok=true` 不等于完整。`ok=true` 只能表示资料包返回了可解析结果；是否完整看 `quality.status`。

### 5.2 `provider_attempts` 最小字段

```json
{
  "provider": "akshare",
  "endpoint": "stock_zh_a_hist",
  "role": "p0_price_history",
  "status": "success",
  "started_at": "2026-05-08T07:00:00Z",
  "finished_at": "2026-05-08T07:00:01Z",
  "elapsed_ms": 1024,
  "timeout_ms": 10000,
  "query_fingerprint": "sha256:...",
  "raw_count": 43,
  "accepted_count": 43,
  "payload_hash": "sha256:...",
  "raw_payload_ref": "viking://resources/workflow/.../provider_raw/akshare/stock_zh_a_hist/1.json",
  "error_code": null,
  "error_message_redacted": null
}
```

失败也必须有 attempt：

```json
{
  "provider": "eastmoney_direct",
  "endpoint": "push2his_kline",
  "status": "timeout",
  "elapsed_ms": 10001,
  "timeout_ms": 10000,
  "raw_count": 0,
  "accepted_count": 0,
  "error_code": "PROVIDER_TIMEOUT",
  "error_message_redacted": "request timed out"
}
```

## 6. Market 落地方案

### 6.1 目标

把 `market_market_data_pack` 从当前 alphaear 独立路径收束到 CN_A provider 矩阵。

worker 可见工具名保持不变，避免 prompt 和 stage policy 大改。

### 6.2 推荐目录

```text
agents/market_analyst/skills/cn-a-market-data/
  SKILL.md
  scripts/
    market_data_pack.py
    profile.py
    config.py
    provider_specs.py
    providers.py
    cache.py
    evidence.py
    normalizer.py
    quality.py
    reader_brief.py
    techlab_adapter.py
  tests/
    test_market_data_pack.py
    test_provider_specs.py
    test_providers.py
    test_cache.py
    test_evidence.py
    test_quality.py
    test_techlab_adapter.py
```

### 6.3 Provider 矩阵

| 优先级 | provider | endpoint / 能力 | 角色 | 备注 |
|---|---|---|---|---|
| P0 | MongoDB | fresh normalized OHLCV cache | 缓存主路径 | 命中需校验 schema、日期范围、payload hash |
| P0 | Tushare | `pro_bar` / 日线相关接口 | A 股日线第一主源 | 统一走 `tushare_client.py` 初始化 |
| P1 | AkShare | `stock_zh_a_hist` | A 股日线备源 | 免费源，可能受接口变动影响 |
| P1 | EastMoney direct | `push2his.eastmoney.com/api/qt/stock/kline/get` | 日线备用源 | 当前 alphaear 已有实现，可迁移为 provider adapter |
| P1 | Sina via AkShare | `stock_zh_a_daily` | 补充源 | 需要统一字段和复权口径 |
| P1 | Tencent via AkShare | `stock_zh_a_hist_tx` | 补充源 | 需要统一字段和复权口径 |
| P2 | Baostock | 日线历史行情 | 候选补充源 | 需先验证稳定性和字段口径 |
| P2 | efinance | 行情历史 | 候选补充源 | 需先验证维护状态 |

### 6.4 AlphaEar 拆分方式

当前：

```text
alphaear-techlab
  -> market_data_provider.load_price_frame
  -> alphaear-stock.StockTools.get_stock_price
  -> SQLite + providers
```

目标：

```text
cn-a-market-data.market_data_pack
  -> provider matrix returns normalized OHLCV
  -> techlab_adapter calls alphaear-techlab indicator/chart functions
  -> pack includes indicators + chart refs + provider evidence
```

`alphaear-techlab` 保留：

- `indicator_engine.py`
- `chart_engine.py`

`alphaear-techlab` 不再拥有取数权。

`alphaear-stock` 处理策略：

1. 第一阶段保留，不删除。
2. 将可用的 EastMoney direct/Sina/Tencent adapter 逻辑迁移或薄封装到 `cn-a-market-data/scripts/providers.py`。
3. 新 market pack 通过测试和 live gate 后，再删除 market 对 `alphaear-stock` 的依赖。

### 6.5 Market pack `domain_data`

```json
{
  "price_history": {
    "ticker": "600519.SH",
    "adjust": "qfq",
    "row_count": 43,
    "date_range": {
      "start_date": "2026-03-07",
      "end_date": "2026-05-07"
    },
    "recent_rows": []
  },
  "technical_indicators": {
    "ma": {},
    "macd": {},
    "rsi": {},
    "boll": {},
    "kdj": {},
    "atr": {}
  },
  "chart_refs": [
    {
      "kind": "market_structure",
      "path": "tool-evidence/.../600519.SH_market_structure.png",
      "openviking_ref": "viking://resources/workflow/.../charts/market_structure.png",
      "sha256": "..."
    }
  ],
  "support_resistance": {},
  "volume_profile": {}
}
```

### 6.6 Market 成功标准

600519.SH live gate 至少满足：

- `provider_attempts` 包含 cache/provider 尝试。
- 至少一个 P0/P1 行情源成功。
- raw payload 写入 OpenViking L2。
- MongoDB cache upsert 成功或明确记录 cache unavailable。
- `alphaear-techlab` 指标和图表成功；若失败，报告必须写明缺图原因。
- worker report 不出现 JSON 工具日志、URI 噪音或 provider 内部字段堆砌。

## 7. News 落地方案

### 7.1 先修当前 blocker

`news_news_data_pack` 当前出现 `invalid tool runtime context`。这是代码/协议问题，不是数据源不稳定问题。

优先级最高：

```text
openclaw plugin runNewsPack
  -> news_data_pack.py main/stdin contract
  -> run_news_data_pack(tool_input, runtime_context)
  -> context worker_id/tool_name/stage/run_id/call_id/evidence_root
```

修复目标：

- OpenClaw 工具调用能传入完整 runtime context。
- news pack 不再因 context 失败而直接 failed。
- context mismatch 测试覆盖 worker_id、tool_name、stage、market。

### 7.2 Provider 矩阵

| 优先级 | provider | 角色 | 说明 |
|---|---|---|---|
| P0 | AkShare `stock_news_em` | 个股新闻 | 现有设计保留 |
| P0 | AkShare `stock_info_global_cls` | 财联社快讯 | 现有设计保留 |
| P0/P1 | Bocha Web Search | 中文搜索增强 | 适合作为更稳定中文新闻搜索主力候选 |
| P1 | Tavily | 新闻/网页搜索增强 | 适合 LLM 结构化搜索结果 |
| P1 | Jina Search/Reader | 搜索与正文提取 | 可借鉴 alphaear-search |
| P1 | NewsNow / alphaear-news source list | 热点聚合 | 财联社、华尔街见闻、雪球等，多源观察 |
| P2 | MiniMax structured search | 候选 | 需验证 API 与授权 |
| TODO | Tushare `anns_d` | 公告增强 | 非主力，权限明确后再接 |

### 7.3 新闻匹配硬规则

- 公司新闻必须有股票代码、公司全称、已批准简称或公告主体硬命中。
- 行业词命中只能进入行业/宏观背景，不得冒充公司新闻。
- 每条 accepted news 必须保留匹配片段、来源、时间、链接。
- Search API 返回摘要不能直接等于事实，应保留原始链接和 provider payload。

## 8. Social 落地方案

### 8.1 当前问题

social 当前失败来自：

- 东方财富/AkShare 热度端点超时或结构异常。
- 有 raw rows 但 target accepted signal 为 0。
- pack evidence 写入失败。

### 8.2 Provider 矩阵

| 优先级 | provider | 角色 | 说明 |
|---|---|---|---|
| P0 | EastMoney/AkShare hot rank latest | 目标热度 | 现有主源，需修匹配和结构变化 |
| P0 | EastMoney/AkShare hot keyword | 关键词热度 | 现有主源 |
| P0 | EastMoney/AkShare related hot rank | 相关热门标的 | 现有主源 |
| P1 | EastMoney full hot rank board | 全市场榜单校验 | 目标过滤 |
| P1 | Bocha/Jina/Tavily search | 舆情线索增强 | 搜索雪球/股吧/微博公开页时必须可审计 |
| P1 | alphaear-news source list | 热点辅助 | 微博、知乎、百度热搜等只能作背景，不能冒充目标情绪 |
| P2 | 雪球/股吧专门 provider | 候选 | 需确认登录态、反爬和授权风险 |

### 8.3 Social 质量规则

- 没有 accepted target signals：`failed`。
- 只有平台热度，无正文舆情：最多 `partial`。
- 只有泛市场热榜，没有目标命中：`failed` 或背景材料，不支撑目标情绪结论。
- 不得生成 KOL 观点、散户机构分歧、具体用户观点，除非 provider 返回原文证据。

## 9. Fundamental 落地方案

### 9.1 Tushare 主源

当前 Tushare token 与私有 HTTP URL（显式配置时）已通过最小探针验证，Tushare 改为 CN_A 基本面第一主源。

动作：

- `CN_A_FUNDAMENTAL_DISABLE_TUSHARE=false` 作为推荐默认。
- `TUSHARE_TOKEN` 由环境变量注入；`TUSHARE_HTTP_URL`/`CN_A_TUSHARE_HTTP_URL` 仅在需要私有代理时显式注入。初始化统一走 `src/claw_trade/providers/tushare_client.py`。
- PE/PB/市值优先使用 `daily_basic`；财务、现金流、股东、公告等优先使用 Tushare 对应接口；免费源只做备源、补源和交叉校验。
- 若出现 `token invalid`，先检查运行环境实际生效的 `TUSHARE_TOKEN`，再确认当前环境是否需要显式配置私有代理 URL。

### 9.2 Provider 矩阵

| 优先级 | provider | 角色 | 说明 |
|---|---|---|---|
| P0 | MongoDB fresh cache | 基本面缓存 | 来自历史成功 provider payload |
| P0 | Tushare Pro | 基本面第一主源 | `stock_basic/daily_basic/fina_indicator/income/balancesheet/cashflow/fina_mainbz/dividend/top10_*` |
| P1 | AkShare company info | 公司基本信息备源 | `stock_individual_info_em` 等 |
| P1 | AkShare financial abstract | 财务摘要备源 | 需字段映射和单位校验 |
| P1 | EastMoney direct / AkShare realtime | 估值/市值补充 | 需校验 PE/PB 字段口径 |
| P1 | Baostock | 财务/行情候选 | 需验证字段覆盖 |
| P2 | efinance | 候选补充 | 需验证 |

### 9.3 基本面硬规则

- PE/PB/ROE/营收/净利润/现金流缺失时，不得让 worker 编造。
- 字段冲突时保留冲突诊断，不自动覆盖。
- 估值和目标价不由资料包生成。
- 资料包可以给事实型 `reader_brief`，不能给买入/卖出/目标价。

## 10. 配置与环境变量

建议新增统一命名，避免每个 worker 自造一套。

```text
CN_A_MONGODB_URI
CN_A_PROVIDER_CACHE_REQUIRED=false
CN_A_PROVIDER_DEFAULT_TIMEOUT_MS=10000
CN_A_PROVIDER_TOTAL_TIMEOUT_MS=30000
CN_A_PROVIDER_MAX_CONCURRENCY=3

CN_A_MARKET_ENABLE_AKSHARE=true
CN_A_MARKET_ENABLE_EASTMONEY_DIRECT=true
CN_A_MARKET_ENABLE_SINA=true
CN_A_MARKET_ENABLE_TENCENT=true
CN_A_MARKET_ENABLE_BAOSTOCK=true
CN_A_MARKET_ENABLE_EFINANCE=true
CN_A_MARKET_ENABLE_TUSHARE=true
TUSHARE_TOKEN
# Optional: only set when using private Tushare proxy.
# TUSHARE_HTTP_URL=https://your-private-tushare-proxy.example
# CN_A_TUSHARE_HTTP_URL=https://your-private-tushare-proxy.example

CN_A_NEWS_ENABLE_BOCHA=true
CN_A_NEWS_BOCHA_API_KEY
CN_A_NEWS_ENABLE_TAVILY=true
CN_A_NEWS_TAVILY_API_KEY
CN_A_NEWS_ENABLE_JINA=true
JINA_API_KEY

CN_A_SOCIAL_ENABLE_SEARCH_ENRICHMENT=true

CN_A_FUNDAMENTAL_ENABLE_TUSHARE=true
CN_A_FUNDAMENTAL_DISABLE_TUSHARE=false
```

密钥规则：

- 不在日志、report、tool result 中打印 token/API key。
- 错误信息必须脱敏。
- `.env.local` 可以用于本地开发，但 evidence 文档不得复制具体 secret 值。

## 11. MongoDB 设计

### 11.1 通用 collection

```text
cn_a_provider_cache
cn_a_provider_attempts
cn_a_normalized_market_prices
cn_a_normalized_news_items
cn_a_normalized_social_signals
cn_a_normalized_fundamental_fields
```

### 11.2 通用 cache key

```json
{
  "market": "CN_A",
  "domain": "market",
  "ticker": "600519.SH",
  "provider": "akshare",
  "endpoint": "stock_zh_a_hist",
  "query_fingerprint": "sha256:...",
  "schema_version": "cn_a_market_data_pack.v1"
}
```

### 11.3 索引建议

```text
{ market: 1, domain: 1, ticker: 1, provider: 1, endpoint: 1, query_fingerprint: 1, schema_version: 1 } unique
{ market: 1, domain: 1, ticker: 1, fetched_at: -1 }
{ expires_at: 1 } TTL
{ payload_hash: 1 }
```

TTL 初值：

- market OHLCV：交易日内 1 天，历史窗口可更长。
- news：6 小时。
- social：1 小时。
- fundamental：7 天到 90 天，按字段族区分。

## 12. OpenViking L2 设计

统一路径：

```text
viking://resources/workflow/{run_id}/frontline/{worker_id}/{call_id}/evidence/
  provider_raw/{provider}/{endpoint}/{attempt_seq}.json
  provider_attempts.json
  normalized_pack.json
  charts/{chart_kind}.png
```

每个 raw payload 写入后必须记录：

- URI
- content hash
- size bytes
- provider
- endpoint
- attempt seq
- query fingerprint
- write receipt / read-back verification result

如果 L2 写入失败：

- provider attempt 仍记录 status 和错误。
- `quality.status` 至少降为 `partial`，如果核心证据无法审计则 `failed`。
- report 必须说明证据链不完整。

## 13. 工具注册与 OpenClaw 边界

不修改 OpenClaw 源码。

只需要调整 `openclaw_plugins/claw-trade-frontline-tools/index.js` 的工具执行路由：

```text
market_market_data_pack
  -> agents/market_analyst/skills/cn-a-market-data/scripts/market_data_pack.py

fundamental_fundamentals_data_pack
  -> existing cn-a-fundamental-data, after Tushare policy adjustment

news_news_data_pack
  -> existing cn-a-news-data, after runtime context fix

social_social_sentiment_pack
  -> existing cn-a-social-data, after provider/evidence fixes
```

OpenClaw 只负责：

- 单 worker turn。
- 最终 provider payload capture。
- visible tools capture。
- tool execution。

OpenClaw 不负责：

- provider 策略。
- MongoDB cache。
- OpenViking L2 证据语义。
- workflow 顺序。
- report hard gate。

## 14. 实施阶段

### M0：文档与任务冻结

交付：

- 本方案。
- `CN_A_market数据服务层设计方案.md`。
- `CN_A_market数据服务层开发任务清单.md`。
- Tushare TODO 记录。

验收：

- 人确认 market 第一优先级。
- 明确不改 OpenClaw。
- 明确不直接接 alphaear-reporter/predictor。

### M1：market pack 最小闭环

交付：

- `cn-a-market-data` skill。
- AkShare + EastMoney direct provider。
- normalized OHLCV。
- OpenViking L2 raw write。
- MongoDB cache inspect/upsert。
- techlab adapter。
- market pack tests。

验收：

- focused pytest green。
- `market_market_data_pack` 工具返回包含 provider attempts 和 L2 refs。
- 600519.SH market worker report 可生成且不含机器噪音。

### M2：market provider 扩展与旧依赖剥离

交付：

- Sina/Tencent provider。
- Baostock/efinance 验证结果。
- 移除 market 对 `alphaear-stock` 取数路径的运行依赖。
- `alphaear-techlab` 只作为计算层。

验收：

- `rg alphaear-stock` 显示 market 运行路径不再依赖其取数。
- 旧 news/social/fundamental 残留命令不再被 market 引用。

### M3：news 修复与 provider 增强

交付：

- 修复 `invalid tool runtime context`。
- Bocha/Tavily/Jina/NewsNow provider 候选按配置接入。
- provider attempts + L2 raw evidence。

验收：

- 600519.SH news pack 不再因 runtime context failed。
- 至少一个公司新闻源或搜索源成功，或明确 failed 且原因可审计。

### M4：social provider 与 evidence 修复

交付：

- 修复 target matching。
- 修复 evidence write failed。
- 对 EastMoney/AkShare 结构变化加 schema guard。
- 可选搜索增强源只做 evidence，不生成观点。

验收：

- 有 target accepted signal 时 report 可写出热度/关键词证据。
- 无 target signal 时 failed/partial 清晰，不生成虚构舆情。

### M5：fundamental 以 Tushare 为主路径

交付：

- 默认启用 Tushare。
- Tushare daily_basic/fina_indicator/income/balancesheet/cashflow 等主源矩阵。
- AkShare/EastMoney/Baostock/efinance 作为备源、补源和交叉校验。
- 字段缺口 hard gate 继续保留。

验收：

- 没有 Tushare 权限时不再表现为主源失败拖垮整包。
- 缺 PE/PB/ROE/核心财务字段时 report 明确缺口，不给 unsupported target/rating。

### M6：frontline 统一验收

交付：

- 600519.SH `/report` fresh run。
- 4 worker final prompt、LLM back、report、tool calls、visible tools、OpenViking L2 evidence。
- 与 TradingAgents-CN 对照报告。

验收：

- 4 worker 均调用各自资料包工具。
- 4 worker 均写入 OpenViking L1。
- 所有 pack 均有 provider attempts。
- LLM back 与 report 正文一致。
- 报告有 TradingAgents-CN 报告感，同时不编造缺失证据。

## 15. 测试计划

### 15.1 Unit tests

Market：

- ticker normalization：`600519` / `600519.SH` / `SH600519`。
- provider plan：enabled/disabled providers。
- provider timeout：记录 failed attempt。
- provider empty：记录 empty attempt。
- field normalization：OHLCV 字段、日期、复权口径。
- quality：complete/partial/failed。
- Mongo cache hit/stale/schema invalid。
- OpenViking L2 write success/failure。
- techlab adapter：输入 normalized OHLCV，输出 indicators/chart refs。

News：

- runtime context complete/missing/mismatch。
- company hard match。
- industry macro bucket。
- search provider result normalization。
- provider all failed。

Social：

- target accepted signal exact code/company alias。
- no accepted signal -> failed。
- hot board raw rows but target absent。
- evidence write failed -> failed/partial。

Fundamental：

- Tushare disabled。
- AkShare field mapping。
- missing PE/PB/ROE blocks unsupported claim support。
- provider conflict diagnostics。

### 15.2 Contract tests

- skill manifest 只导出本 worker 资料包工具。
- stage policy 只暴露资料包工具 + OpenViking write。
- provider 原子接口不出现在 visible tools。
- `openviking_write_material` schema 对普通 worker 只暴露 `content`。

### 15.3 Integration tests

- 资料包 CLI stdin/stdout contract。
- MongoDB local integration。
- OpenViking L2 write/read-back。
- OpenClaw plugin tool execution。

### 15.4 Live gate

默认 `/report`，CN_A，600519.SH：

```text
run -> frontline_ready stop point
capture:
  final_prompt
  llm_back
  report
  assistant_return_after_submit
  visible_tools
  tool_calls
  openviking_receipts
  provider_attempts
  l2 refs
```

### 15.5 Eval / report quality

对比 TradingAgents-CN：

- 是否有传统报告结构。
- 是否有证据驱动。
- 是否没有工具日志/JSON/URI 噪音。
- 是否明确缺口但不过度防御。
- 是否没有 unsupported PE/PB/ROE/目标价/新闻/情绪。

## 16. 验收清单

```text
[ ] OpenClaw 源码无业务逻辑新增
[ ] worker 可见工具保持 stage scoped
[ ] provider 原子接口不暴露给 worker
[ ] market 不再由 alphaear-stock 决定取数
[ ] alphaear-techlab 只作为指标/图表计算层
[ ] MongoDB cache 有 freshness/schema/hash 校验
[ ] OpenViking L2 raw payload refs 可 read-back
[ ] provider_attempts 覆盖 success/empty/timeout/error
[ ] failed/partial 不被包装成 success
[ ] Tushare 不作为默认主力源
[ ] report 不含工具日志、JSON、URI 噪音
[ ] LLM back 是 write_material(content) 原文
[ ] 600519.SH frontline 4 worker fresh run 通过
```

## 17. 风险与缓解

| 风险 | 影响 | 缓解 |
|---|---|---|
| provider 全失败 | 报告变成证据缺口报告 | failed/partial 明确写出，不编造 |
| 免费源接口变动 | 数据不稳定 | 多 provider attempts + schema guard |
| MongoDB 不可用 | 缓存失效、重复请求 | 记录 cache_unavailable，允许远端尝试 |
| OpenViking L2 写失败 | 证据不可审计 | 降级 quality，关键证据失败则 failed |
| worker 看到太多工具 | 行为漂移 | 只暴露资料包工具 |
| alphaear 直接接管报告 | PM owner 冲突 | 禁止接 alphaear-reporter |
| Tushare 权限不足或配额耗尽 | 基本面字段缺口 | 显式失败并启用备源；不得默认关闭或伪装成功 |
| 搜索 API 返回摘要不可靠 | 新闻/舆情误判 | 保留原始链接、匹配片段、provider payload |

## 18. NOT in scope

- 不做 UI。
- 不做 HK/CRYPTO。
- 不做完整 MCP 化。
- 不做数据源商业采购决策。
- 不接 alphaear-reporter。
- 不接 alphaear-predictor。
- 不让 Python 生成报告。
- 不改变 12-worker workflow 顺序。
- 不把 OpenViking 当 MongoDB 查询库。
- 不把 MongoDB 当 artifact approval 权威。

## 19. TODO

以下只记录未来方向，不进入当前主线：

1. Tushare Pro 作为增强源重新评估。
   - 条件：预算、权限、接口白名单、调用频率、字段覆盖和 token 管理明确。

2. Baostock/efinance 稳定性评估。
   - 条件：完成 600519、000001、300750、指数样本的字段覆盖和延迟测试。

3. Bocha/Tavily/MiniMax 商业搜索评估。
   - 条件：API key、费用、中文财经覆盖、新闻时间字段、来源链接质量明确。

4. provider matrix 抽公共库。
   - 条件：market/news/social/fundamental 至少两个领域完成后，再抽公共代码，避免过早抽象。

5. alphaear legacy 清理。
   - 条件：market 不再依赖 `alphaear-stock` 取数，news/social/fundamental 无残留调用。

## 20. 参考

- `docs/CN_A_fundamental数据服务层总体设计.md`
- `docs/CN_A_fundamental数据服务层详细设计.md`
- `docs/CN_A_news数据服务层设计方案.md`
- `docs/CN_A_news数据服务层详细设计.md`
- `docs/CN_A_social数据服务层设计方案.md`
- `docs/CN_A_social数据服务层详细设计.md`
- `docs/evidence/frontline_prompt_feedback/integration_cn_a_frontline_600519_20260508/post_report_voice_comparison_report.md`
- Awesome Finance Skills: `https://github.com/RKiding/Awesome-finance-skills`
- AlphaEar Stock skill: `https://raw.githubusercontent.com/RKiding/Awesome-finance-skills/main/skills/alphaear-stock/SKILL.md`
- AlphaEar News skill: `https://raw.githubusercontent.com/RKiding/Awesome-finance-skills/main/skills/alphaear-news/SKILL.md`
- AlphaEar Search skill: `https://raw.githubusercontent.com/RKiding/Awesome-finance-skills/main/skills/alphaear-search/SKILL.md`
