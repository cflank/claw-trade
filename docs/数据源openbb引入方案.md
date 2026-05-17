# 数据源 OpenBB 引入方案

## 1. 结论

建议引入 OpenBB 这种统一数据接入架构，但不把 OpenBB 原生全量工具直接暴露给分析师。

目标形态是：

```text
claw-data-mcp 作为唯一数据源 MCP
  ├─ OpenBB 原生 provider：FMP / Polygon / FRED / SEC / yfinance / Benzinga / Biztoc / Tiingo / Deribit ...
  ├─ 项目自有 provider：Tushare / AkShare / EastMoney / BB-CoinGlass / CoinGecko / DefiLlama / LunarCrush ...
  ├─ 统一调度：key、限流、缓存、重试、失败记录、来源角色、缺口、冲突、就绪度
  └─ 对外稳定接口：市场资料包 / 基本面资料包 / 新闻资料包 / 舆情资料包
```

一句话：底层统一成 OpenBB 风格的数据框架，对外仍然输出 claw-trade 需要的领域资料包。

## 2. 背景问题

当前数据层已经能跑，但结构比较分散：

- A股和港股数据在 `frontline_data_pack` 的 Tushare、AkShare、EastMoney、新闻和舆情 provider 中。
- CRYPTO 市场结构在 BB / CoinGlass 和本地 `crypto_market_data_pack` 中。
- CRYPTO 基本面、新闻、舆情分别散在 CoinGecko、DefiLlama、LunarCrush、Alternative.me、Polymarket、搜索源和社交平台源中。
- provider key、限流、缓存、失败记录已经有部分实现，但没有形成跨市场统一模型。
- OpenClaw worker 当前通过各自资料包拿材料，这个边界是对的，但资料包下面的 provider 编排层还可以更统一。

这会带来几个长期问题：

- 新增 provider 成本高，每个资料包都可能重复写 key、限流、缓存、错误处理。
- 同一 provider 可能被多个 worker 或多个市场重复调用，容易踩限流。
- provider 成功、失败、缺字段、权限不足的记录口径不完全一致。
- 美股 provider 体系较弱，缺 FMP、Polygon、Benzinga、Biztoc、Tiingo 等成熟来源。
- 数据来源角色容易混：搜索发现、新闻事实、社交舆情、盘口预期、官方原文必须分开。

## 3. 设计目标

### 3.1 必须做到

1. 建立唯一数据源 MCP：所有外部数据访问最终都经过 `claw-data-mcp`。
2. 复用 OpenBB 的 provider 思路：把 OpenBB 已有 provider 和项目自有 provider 纳入同一套 adapter 规范。
3. 保留现有 worker 边界：分析师只拿市场、基本面、新闻、舆情资料包，不直接面对几十个底层接口。
4. 统一记录 provider attempt：每次调用必须有来源、接口、请求时间、状态、错误原因、返回规模。
5. 统一输出资料质量：每个资料包必须给出资料就绪度、资料缺口、冲突和来源清单。
6. 统一限流和缓存：同一轮报告内集中取数，多个 worker 共享 approved material，不重复申请。
7. 保持真实性边界：搜索只能做发现线索，盘口只能做事件预期，Alternative.me 只能做市场级情绪，OpenViking 只存 approved material。

### 3.2 不做

1. 不把 OpenBB 全量 MCP 工具直接挂给 worker。
2. 不让 worker 自己选择底层 provider。
3. 不让 Python 或数据层替 worker 写投资判断。
4. 不用 OpenBB 替代 Tushare、HKEXnews、BB/CoinGlass、官方公告原文等关键来源。
5. 不把搜索摘要、新闻聚合、社交热度或盘口数据写成事实源。

## 4. 总体架构

```text
OpenClaw worker
  |
  | 只看到领域资料包工具
  v
frontline pack tool
  |
  | 只负责调用数据 MCP、整理 worker 可读自然语言材料
  v
claw-data-mcp
  |
  | 统一数据接入、限流、缓存、审计、来源角色判断
  v
Provider Adapter Layer
  ├─ OpenBB adapters
  ├─ claw-trade custom adapters
  └─ official-source adapters
```

最终形态中，`frontline_data_pack` 可以逐步变薄。它不再直接散落调用各 provider，而是调用 `claw-data-mcp` 的领域接口。

## 5. MCP 对外接口

`claw-data-mcp` 不暴露 provider 原子工具，而暴露领域资料包接口。

### 5.1 面向 worker 的接口

```text
get_market_pack
get_fundamental_pack
get_news_pack
get_social_pack
```

统一入参：

```text
ticker
market
profile
company_name
start_date
end_date
currency
run_id
worker_id
freshness_policy
```

统一出参：

```text
reader_brief              给 worker 的自然语言材料
compact_facts             结构化但不冗长的事实摘要
provider_attempts         来源尝试记录
data_gaps                 资料缺口
conflicts                 数据冲突
readiness                 资料就绪度
source_roles              来源角色
raw_artifact_refs         原始 provider 证据路径
cache_receipts            缓存命中和复用证明
```

注意：`reader_brief` 是 worker 主要阅读材料；结构化字段用于审计和必要引用，不能变成 worker 报告正文。

### 5.2 面向运维/调试的接口

这些接口不默认暴露给 worker：

```text
list_providers
provider_health
cache_status
rate_limit_status
explain_pack_plan
replay_provider_attempt
```

用途是调试、验收和 UI 设置页，不进入普通分析师 turn。

## 6. Provider 分层

### 6.1 OpenBB 原生或优先通过 OpenBB 接入

| provider | 主要用途 | 优先级 |
|---|---|---|
| FMP | 美股财务、估值、公司资料、新闻 | 高 |
| Polygon | 美股行情、分钟线、快照、期权数据 | 高 |
| FRED | 宏观利率、美元流动性、风险窗口 | 高 |
| SEC | 美股公告和监管文件 | 高 |
| yfinance | 美股/港股行情和基础财务补充 | 中 |
| Benzinga | 美股新闻和事件快讯 | 中 |
| Biztoc | 新闻聚合补充 | 中 |
| Tiingo | 美股行情、新闻、基本面补充 | 中 |
| TradingEconomics | 全球宏观、经济日历 | 中 |
| Deribit | BTC/ETH 期权、隐含波动率、期限结构 | 中 |

### 6.2 项目自有 provider，做成 OpenBB 风格 adapter

| provider | 市场 | 主要用途 | 是否保留主源地位 |
|---|---|---|---|
| Tushare | A股/港股 | 行情、财务、公告 | 是 |
| AkShare | A股/港股 | 行情、财务、新闻、热度 | 是，补充/交叉验证 |
| EastMoney | A股/港股 | 行情补位、热度 | 是，补充/交叉验证 |
| Sina / Tencent | A股 | 行情补位 | 是，补充 |
| BaoStock / efinance | A股 | 候选补位 | 保留但降优先级 |
| HKEXnews | 港股 | 官方公告、业绩、停复牌、公司行动 | 是，权威原文 |
| BB / CoinGlass | CRYPTO | 市场结构、清算、OI、资金费率、AHR999 | 是，主源 |
| Binance | CRYPTO | OHLCV、公开行情 | 是，行情补充 |
| CoinGecko | CRYPTO | 币种基础资料、市值、供应量 | 是，基本面补充 |
| DefiLlama | CRYPTO | TVL、费用、收入、安全/融资背景 | 是，DeFi 基本面 |
| LunarCrush | CRYPTO | 聚合社交指标 | 是，社交聚合 |
| Alternative.me | CRYPTO | 市场级恐惧贪婪 | 是，但只限市场级情绪 |
| Polymarket | CRYPTO | 事件预期和盘口概率 | 是，但不当新闻事实或社交共识 |
| X / Reddit / Telegram / Discord | CRYPTO | 原始社交样本 | 是，社交原始样本 |
| GitHub releases | CRYPTO | 项目发布和协议变更 | 是，项目原始源 |
| Bocha / Tavily / Jina / NewsNow / MiniMax | A股/CRYPTO | 搜索发现、网页读取、热点补充 | 是，但只限发现线索 |

## 7. Provider Adapter 规范

每个 provider 必须实现同一组基础能力。

```text
ProviderAdapter
  name
  supported_markets
  supported_domains
  required_credentials
  rate_limit_policy
  cache_policy
  source_role_policy
  fetch(request) -> ProviderResult
  normalize(raw) -> NormalizedResult
  assess_quality(result) -> QualityAssessment
```

`ProviderResult` 必须包含：

```text
provider
endpoint
request_id
requested_at
latency_ms
status
error_code
error_message
raw_ref
row_count
freshness
source_role
license_note
```

`source_role` 是硬边界，不是备注：

| source_role | 含义 |
|---|---|
| official_original | 官方原文、监管原文、交易所公告原文 |
| market_data | 行情、成交、K线、盘口 |
| derivative_market_data | OI、资金费率、清算、期权 |
| fundamental_data | 财务、TVL、收入、供应量、估值 |
| social_original_sample | 社交平台原帖或频道消息 |
| social_aggregate_metric | 聚合社交指标 |
| search_discovery | 搜索发现线索 |
| event_expectation | 事件预期、预测市场盘口 |
| macro_data | 宏观经济与利率数据 |

资料包生成报告时必须按 `source_role` 决定能不能引用为事实。

## 8. 资料包编排规则

### 8.1 市场资料包

目标：行情、技术指标、市场结构、衍生品、清算、图表资产。

优先级：

1. CRYPTO：BB / CoinGlass 为市场结构主源。
2. 美股：Polygon 或 FMP/yfinance 提供行情，必要时加入 Cboe/Tradier/Deribit 等衍生品源。
3. A股：Tushare 主源，AkShare/EastMoney/Sina/Tencent 补充。
4. 港股：Tushare HK 主源，AkShare HK/EastMoney/yfinance 补充。

市场资料包可以生成图表，但图表必须来自真实数据，不允许占位图。

### 8.2 基本面资料包

目标：公司/项目资料、估值、财务、供应量、TVL、收入、费用、现金流。

优先级：

1. A股：Tushare 财务为主，AkShare/EastMoney 补充。
2. 港股：Tushare HK、HKEXnews、AkShare HK、yfinance/FMP 交叉。
3. 美股：FMP、SEC、Polygon/yfinance 补充。
4. CRYPTO：CoinGecko、DefiLlama、项目官网、白皮书、治理论坛。

基本面资料包不得把缺字段补成估算事实。

### 8.3 新闻资料包

目标：公司新闻、官方公告、监管事件、交易所公告、安全事件、宏观事件。

优先级：

1. 官方原文：公司公告、交易所公告、监管源、GitHub releases。
2. 专业新闻：Benzinga、Biztoc、FMP news 等。
3. 搜索发现：Bocha、Tavily、Jina、NewsNow、MiniMax。
4. 事件背景：DefiLlama hacks/raises、Polymarket 事件预期。

搜索结果只能提示“哪里可能有材料”，不能直接当事实。

### 8.4 舆情资料包

目标：真实社交平台样本、聚合社交指标、社区分歧、KOL 叙事、噪音风险。

优先级：

1. 原始平台：X、Reddit、Telegram、Discord。
2. 聚合指标：LunarCrush。
3. 市场级情绪：Alternative.me。
4. 事件预期：Polymarket。
5. 搜索发现：Bocha、Tavily、Jina。

Alternative.me 不能写成单币种舆情；Polymarket 不能写成社交共识。

## 9. 缓存、限流和共享

统一 MCP 必须解决重复取数问题。

### 9.1 单轮报告共享

同一轮报告中：

- 对同一 ticker、market、domain、date range、provider plan 的请求只执行一次。
- 后续 worker 使用同一份 approved L1 或同一份 data pack receipt。
- 缓存命中必须标注，不得伪装成 fresh provider success。

### 9.2 跨轮缓存

按数据类型设置 TTL：

| 数据类型 | 建议 TTL |
|---|---|
| 实时行情 / CRYPTO 衍生品 | 1 到 5 分钟 |
| K线 / 技术指标 | 5 到 15 分钟 |
| 新闻搜索 | 15 到 30 分钟 |
| 官方公告 / GitHub releases | 30 到 120 分钟 |
| 财务 / 基本面 | 6 到 24 小时 |
| 宏观数据 | 1 到 24 小时 |
| 社交原帖样本 | 1 到 5 分钟 |

### 9.3 限流策略

限流按 provider 统一管理：

```text
provider
credential_id
market
domain
endpoint
window
remaining_budget
backoff_until
```

触发限流时，资料包应返回“来源暂不可用 / 覆盖不足”，不能静默跳过。

## 10. 配置和密钥管理

短期仍使用 `.env.local`。

中期引入统一配置：

```text
config/data_providers.yaml
```

建议结构：

```yaml
providers:
  fmp:
    enabled: true
    credential: FMP_API_KEY
    markets: [US, HK]
    domains: [market, fundamental, news]
  tushare:
    enabled: true
    credential: TUSHARE_TOKEN
    markets: [CN_A, HK]
    domains: [market, fundamental, news]
  bb_coinglass:
    enabled: true
    credential: COINGLASS_API_KEY
    markets: [CRYPTO]
    domains: [market]
```

密钥只进入本地环境或未来 UI secret store，不写进报告、prompt、artifact 正文。

## 11. 与现有架构的关系

### 11.1 OpenClaw

OpenClaw 仍然只负责单 worker turn。

它不拥有 12-worker DAG，也不拥有 provider 调度。

### 11.2 Python 控制层

Python 仍然只负责调度 worker、传递 approved material、导出报告。

Python 可以调用数据 MCP 来生成资料包，但不能写投资判断。

### 11.3 OpenViking

OpenViking 仍然只保存 approved L1/L2 material。

它不是行情、新闻、公告或舆情数据源。

### 11.4 Worker

worker 只消费自然语言资料包和上游 approved L1 报告。

worker 不应该直接看到 provider 原始 JSON、缓存对象、key 状态、调试字段或全量 OpenBB 工具。

## 12. 迁移路径

### Phase 0：设计冻结和对齐

产出：

- 本设计文档。
- provider 清单和优先级。
- 不新增 runtime guard。
- 不改 worker 权责。

验收：

- 文档明确唯一数据源 MCP 的边界。
- 文档明确哪些 provider 通过 OpenBB，哪些做自定义 adapter。

### Phase 1：MCP 骨架

实现：

- 新建 `claw-data-mcp` 服务。
- 实现 provider registry。
- 实现统一 `ProviderAttempt`、`DataGap`、`Conflict`、`Readiness` 模型。
- 实现 `get_market_pack` / `get_fundamental_pack` / `get_news_pack` / `get_social_pack` 空壳和合同测试。

验收：

- 工具 schema 固定。
- 错误、缺 key、限流、空结果都有结构化返回。
- 不触发真实 provider 时不能伪成功。

### Phase 2：迁移现有 A股/港股 provider

实现：

- Tushare adapter。
- AkShare adapter。
- EastMoney adapter。
- A股/港股 market/fundamental/news/social pack 改为调用 MCP。

验收：

- A股样本和港股样本各跑一轮。
- provider attempt 与当前资料包输出一致或更完整。
- 港股 HKEXnews 未闭合的部分仍明确写缺口。

### Phase 3：迁移 CRYPTO provider

实现：

- BB / CoinGlass adapter。
- Binance adapter。
- CoinGecko adapter。
- DefiLlama adapter。
- LunarCrush / Alternative.me / Polymarket adapter。
- X / Reddit / Telegram / Discord adapter。
- CRYPTO 四个资料包改为调用 MCP。

验收：

- BTC fresh run。
- market worker 仍拿到完整指标分析材料和图表资产。
- 新闻、舆情缺口仍显式写出。
- 搜索、Polymarket、Alternative.me 边界测试通过。

### Phase 4：引入 OpenBB provider

优先顺序：

1. FMP。
2. Polygon。
3. FRED / SEC。
4. Benzinga / Biztoc。
5. TradingEconomics。
6. Deribit。

验收：

- 美股报告质量提升：财务、估值、新闻、宏观更完整。
- 同一 provider key 的限流、缓存和错误记录统一。
- OpenBB provider 不绕过项目 source role 和 data gap 规则。

### Phase 5：收敛旧路径

实现：

- 旧 `frontline_data_pack` 中直接 provider 调用逐步删除或改成 thin client。
- 保留兼容层一段时间。
- 新旧输出对比稳定后，把 MCP 设为唯一数据源入口。

验收：

- A股、港股、美股、CRYPTO 四市场 fresh run。
- 每个前线 worker 的 final prompt、tool schema、LLM back、report 都可追溯。
- provider raw、provider attempts、data gaps、conflicts、readiness 都完整。

## 13. 测试矩阵

### 13.1 单元测试

- provider adapter 缺 key。
- provider adapter 限流。
- provider adapter 空返回。
- provider adapter schema 变化。
- source role 判断。
- cache hit 不伪装成 fresh success。

### 13.2 合同测试

- worker 可见工具只能是领域资料包。
- `get_market_pack` 等 MCP 接口 schema 稳定。
- 搜索发现不能当新闻事实。
- Polymarket 不能当新闻事实或社交共识。
- Alternative.me 不能当单币种社交舆情。
- OpenViking 不能当数据源。

### 13.3 集成测试

- A股样本：600519。
- 港股样本：00700.HK 和一个非腾讯样本。
- 美股样本：AAPL 或 MSFT。
- CRYPTO 样本：BTC。

每个样本检查：

- final prompt。
- 模型可见工具 schema。
- tool call 真实发生。
- provider attempts。
- worker L1 报告。
- final report。

### 13.4 回归验收

每次迁移 provider 后，必须对比：

- 迁移前后资料包信息量。
- 迁移前后资料缺口。
- 迁移前后报告结论是否因为数据缺失而退化。
- 是否出现伪成功、静默跳过、内部字段泄漏。

## 14. 主要风险

| 风险 | 影响 | 控制方式 |
|---|---|---|
| OpenBB provider 覆盖不稳定 | 数据缺口增加 | 每个 provider 独立 attempt，不把 OpenBB 当万能源 |
| 全量 MCP 工具暴露 | worker 乱调工具 | 只暴露领域资料包 |
| 迁移期间双路径不一致 | 报告结果漂移 | 新旧输出对比和分阶段切换 |
| 自定义 adapter 太多 | 维护成本上升 | 先迁移主源，低优先级 provider 后置 |
| 限流仍被打爆 | 报告失败或缺口扩大 | 单轮共享、TTL、provider budget |
| 新闻/舆情边界混乱 | 伪事实风险 | source role 强制进入资料包生成逻辑 |

## 15. 成功标准

第一阶段成功标准：

- 所有数据调用都能通过统一 MCP 记录 provider attempt。
- 同一轮报告不会重复请求同一 provider 数据。
- worker 仍只看到领域资料包。
- 缺 key、限流、失败、空结果全部显式上报。
- A股、港股、美股、CRYPTO 均能跑通至少一个 fresh 样本。

最终成功标准：

- `claw-data-mcp` 成为唯一数据源入口。
- OpenBB 原生 provider 和项目自有 provider 使用同一 adapter 合同。
- 资料包输出稳定包含自然语言材料、来源尝试、资料缺口、冲突、就绪度。
- 最终报告质量不低于迁移前，且 provider 证据更完整。

## 16. 推荐实施顺序

推荐先做：

1. `claw-data-mcp` 骨架和统一模型。
2. A股/港股 Tushare、AkShare、EastMoney adapter。
3. CRYPTO BB/CoinGlass、CoinGecko、DefiLlama adapter。
4. FMP provider。
5. Polygon provider。

暂缓：

1. 全量 OpenBB MCP 暴露。
2. 低频宏观源大规模接入。
3. FINRA、Cboe、Tradier 等美股深度源。
4. 未闭合合同的网页抓取型 provider。

## 17. 参考

- 项目 CRYPTO 数据源方案：`docs/crypto_data_source_plan.md`
- 项目总体架构：`docs/架构设计.md`
- 港股详细设计：`docs/港股详细设计.md`
- 当前 provider 规格：`openclaw_plugins/claw-trade-frontline-tools/python/frontline_data_pack/provider_specs.py`
- OpenBB provider 文档：https://docs.openbb.co/platform/usage/extensions/overview
- OpenBB API key 文档：https://docs.openbb.co/platform/settings/user_settings/api_keys
- OpenBB MCP 文档：https://docs.openbb.co/odp/python/extensions/interface/openbb-mcp
