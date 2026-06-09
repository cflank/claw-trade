# CRYPTO 数据源总体方案

日期：2026-05-16

## 1. 结论

CRYPTO 数据源不应依赖单一免费项目，也不应把一堆原子 MCP 直接暴露给模型。

目标架构是：

- `src/claw_trade/data_gateway` 统一调用 CoinGlass、Binance、Bybit、Deribit、CoinGecko、FRED、Glassnode、DefiLlama 等 approved provider，并写入 provider evidence。
- CryptoLens 是加密指标分析引擎，只消费 data_gateway normalized crypto bundle，不作为数据源或 provider。
- DefiLlama / CoinGecko 做项目资料和基本面补充。
- 商业搜索与官方公告源做新闻事件层。
- LunarCrush / Polymarket 做舆情和事件预期层。
- OpenViking 继续做 approved material、L1/L2 报告和证据存储，不当作行情、新闻、舆情或链上数据源。
- OpenClaw worker 只看到按 worker 分域封装后的资料包工具，不直接看到一堆底层 provider 原子接口。

最新人类决策：正式 12 个 CRYPTO TradingAgents worker 和 `report_polisher` 全部打开。当前统一入口是 `claw_get_market_pack`、`claw_get_fundamental_pack`、`claw_get_news_pack`、`claw_get_social_pack`。`market_analyst` 只通过 `claw_get_market_pack` 获取自然语言市场资料包；CRYPTO 市场资料包由 data_gateway 取数、归一化，再交给 CryptoLens 做离线指标分析。新闻/舆情资料包若缺 key、失败、返回空或覆盖不足，worker 必须在自己的 L1 报告中直接说明缺哪些资料。`report_polisher` 只能整理已批准上游报告，不能补写新闻、舆情、基本面事实或投资结论。Python 不补写新闻、舆情或基本面事实，也不静默 fallback。

新闻与舆情资料包的详细实现边界见 [CRYPTO 新闻与舆情资料包详细设计](crypto_news_social_data_pack_design.md)。

CryptoLens 迁入口径和 data_gateway 边界见 [CryptoLens 接入方案](CryptoLens接入方案.md)。

## 2. 设计原则

1. 优先使用已经付费并跑通的数据源，但数据源入口只能是 data_gateway。
   - CoinGlass 等外部 provider 必须经 data_gateway provider plugin/adapter 进入证据链。
   - CryptoLens 只分析 data_gateway normalized bundle，不读取 key、不出网、不写 provider evidence。
   - 不能用 CoinGecko、DefiLlama 或免费爬虫替代 CoinGlass 的衍生品结构数据。

2. 免费源只能做补充。
   - 免费源可以提高覆盖面，但不能被当成交易级稳定主源。
   - 免费源失败、限流、字段缺失或口径冲突，必须进入资料包的 `provider_attempts`、`data_gaps` 或 `conflicts`。

3. 不做静默 fallback。
   - 可以有主源、备源和补充源，但报告必须知道用了什么、失败了什么、缺了什么。
   - 资料包可以标记 `ready`、`partial`、`insufficient`，但这不是新增 runtime gate，只是给 worker 的数据质量材料。

4. 不让 Python 代替 worker 分析。
   - Python 负责调度、工具封装、provider 调用和证据保存。
   - 加密币分析结论仍由 OpenClaw worker 根据可见工具和上游报告生成。

5. 不把 OpenViking 当数据源。
   - OpenViking 是 approved material 存储和传递层。
   - 行情、衍生品、基本面、新闻和舆情必须来自对应数据工具。

## 3. 分层数据源

| 数据层 | 主源 | 补充源 | 主要 worker | 说明 |
|---|---|---|---|---|
| 市场结构 | data_gateway provider plugins/adapters：CoinGlass / Binance / Bybit / Deribit 等 approved source | CoinGecko OHLC、交易所流动性 | `market_analyst` | 资金费率、OI、清算、多空比、期权、ETF、链上周期指标；CryptoLens 只做分析层 |
| 项目资料 / 基本面 | DefiLlama + CoinGecko | Dune、The Graph、项目官网、白皮书、治理论坛 | `fundamental_analyst` | TVL、fees、revenue、stablecoins、bridges、hacks、unlocks、metadata、FDV、流通量 |
| 新闻事件 | 商业搜索 API + 官方公告源 | CoinGlass ETF / 链上事件、Polymarket 事件盘口 | `news_analyst` | 项目公告、协议升级、交易所公告、监管、ETF、安全事件 |
| 舆情 / 情绪 | LunarCrush | X / Reddit / Telegram / Discord 搜索、Polymarket | `social_analyst` | 社交热度、KOL 叙事、社区分歧、机器人噪音、事件预期 |
| 深度链上 | Dune / The Graph | Arkham、Nansen 后续评估 | `fundamental_analyst` | 钱包、协议活动、资金流、链上行为解释；风险 worker 只通过上游 approved L1 报告间接消费 |
| 预测模型 | Kronos | 自研回测结果 | `market_analyst` 后续辅助 | 不是数据源，只能作为 OHLCV 预测辅助；`trader` 直接可见需另行评审 |

## 4. 数据源逐项判断

### 4.1 data_gateway CRYPTO market providers + CryptoLens

定位：data_gateway 是 CRYPTO 市场结构的外部取数入口；CryptoLens 是指标分析引擎，不是数据源。

适合：

- 持仓量 OI。
- 资金费率。
- 清算。
- 多空比。
- 主动买卖量。
- 期权。
- ETF。
- 部分链上和宏观指标。

不适合：

- 完整新闻事实源。
- 社交舆情源。
- 项目白皮书、治理论坛、协议收入解释的唯一来源。

接入建议：

- `market_analyst` 只看到 `claw_get_market_pack`。
- `claw_get_market_pack` 内部通过 data_gateway 执行 CoinGlass、Binance、Bybit、FRED 等 approved provider plugin/adapter，并写 data_gateway attempt/raw/normalized evidence。
- CryptoLens 只读取 data_gateway normalized crypto bundle 做离线分析，输出指标解释、条件场景、失效条件和数据缺口。
- 禁止任何 worker 直接调用旧 BB MCP、CryptoLens raw tool 或 legacy 已删除数据网关 atomic provider tool。下游 worker 默认读取前线已批准报告。

### 4.2 CoinGecko

定位：币种基础资料和通用市场数据补充，不替代 CoinGlass。

适合：

- coin metadata。
- 市值、FDV、流通量、总供应量。
- 现货价格、成交量、历史 OHLC。
- 交易所、交易对、流动性分布。
- 币种分类和生态标签。

不适合：

- 衍生品拥挤度。
- 清算和杠杆结构。
- 社交舆情。
- 新闻事实主源。

接入建议：

- 放入 `claw_get_fundamental_pack`。
- 对于没有 DeFi 经营数据的资产，CoinGecko 只能提供基础资料补充；资料包必须同时标明 DeFi/链上经营数据覆盖不足，不能把 CoinGecko 基础资料写成完整基本面。

### 4.3 DefiLlama

定位：DeFi / 链上经营数据主源，不是所有加密资产的万能基本面源。

适合：

- TVL。
- fees / revenue。
- stablecoins。
- bridges。
- yields。
- hacks。
- treasuries。
- token unlocks。
- ETF / DAT 数据。

不适合：

- 纯 meme 币。
- CEX 平台币完整基本面。
- 主要业务在线下或链下的项目。
- BTC 这类核心叙事不是 DeFi 协议收入的资产。

接入建议：

- 放入 `claw_get_fundamental_pack`。
- 对 DeFi 协议、公链生态、L2、DEX、借贷、稳定币和桥类项目优先使用。
- 对非 DeFi 资产，资料包必须明确说明 DefiLlama 覆盖不足。

### 4.4 legacy 已删除数据网关

定位：历史评估过的统一数据接入平台。已删除数据网关 本体已删除，不再作为当前目标运行时、唯一外部数据入口或 provider 接口层。

适合：

- 统一接商业授权数据源。
- 把多 provider 通过同一接口管理。
- 与 MCP 生态整合。

风险：

- 层太厚，容易把过多工具暴露给 worker。
- 已删除数据网关 本身开源不等于底层数据都免费。
- 许多高质量 provider 仍然需要单独 key 或订阅。

当前口径：

- CRYPTO provider 主干落在 `src/claw_trade/data_gateway`。
- 不恢复 已删除数据网关 runtime/submodule，也不把 legacy 已删除数据网关 全量工具、discovery/admin tool 或 atomic provider tool 暴露给 worker。

### 4.5 商业搜索 API

候选：

- Tavily。
- SerpAPI。
- Brave Search API。
- Bocha。
- NewsAPI。
- MiniMax 搜索。

定位：新闻事件和公开网页检索主力。

适合：

- 官方公告检索。
- 交易所公告检索。
- 监管文件和 ETF 新闻。
- 安全事件、黑客攻击、合作公告。
- 多语言公开页面覆盖。

风险：

- 搜索结果不是事实，需要来源可靠性分级。
- 高频调用通常收费。
- 对社交平台原帖覆盖不稳定。

接入建议：

- 放入 `claw_get_news_pack`。
- 允许用户按需填写 `BRAVE_SEARCH_API_KEY`、`BOCHA_API_KEY`、`NEWSAPI_API_KEY`、`SERPAPI_API_KEY`、`TAVILY_API_KEY`、`EXA_API_KEY`；未填写的 provider 不发起请求，但必须写入 `provider_attempts` / `data_gaps`，不能从资料包里静默消失。
- 每条新闻必须记录来源、时间、标题、URL、摘要、证据强度和是否官方确认。
- 官方公告、交易所公告、监管源、商业搜索结果和社区/KOL 传闻必须分层展示；商业搜索只能做发现入口，不能把搜索摘要直接当成事实。

### 4.6 LunarCrush

定位：加密社交舆情主源候选。

适合：

- 社交热度。
- 情绪指标。
- KOL / community 相关指标。
- 加密资产社交传播趋势。

风险：

- 具体原帖和语义细节可能需要额外数据。
- 商业 API / MCP 成本和额度需要确认。
- 社交热度容易受机器人、空投、交易所活动影响。

接入建议：

- 放入 `claw_get_social_pack`。
- 社交指标必须和价格、成交量、OI、清算等市场结构交叉验证。
- LunarCrush 适合作为聚合指标源；X / Reddit / Telegram / Discord 适合作为原帖与社区样本源；Polymarket 只能表达事件预期或盘口概率，不能写成社交共识或新闻事实。

### 4.7 Polymarket

定位：事件预期和预测盘口源，不是新闻事实源。

适合：

- 监管、ETF、选举、宏观、项目事件的隐含概率。
- 市场对事件结果的概率定价。
- 事件驱动情绪辅助。

不适合：

- 证明新闻事实。
- 替代官方公告。
- 普通币种社交热度。

接入建议：

- 放入 `claw_get_news_pack` 或 `claw_get_social_pack` 的辅助域。
- 报告中应写成“事件盘口预期”，不要写成已发生事实。

### 4.8 daily_stock_analysis / Awesome-finance-skills

定位：参考项目，不是稳定生产主源。

可借鉴：

- 多 provider 配置。
- 免费源 + 商业源组合。
- 新闻搜索和社交情绪技能设计。
- Polymarket 事件数据使用方式。

不建议：

- 直接把它们当作 claw-trade 的加密数据底座。
- 直接复用其免费爬虫口径作为稳定事实源。

### 4.9 Kronos

定位：K 线预测模型，不是数据源。

适合：

- 在有稳定 OHLCV 的前提下做趋势预测辅助。
- 后续放入 `market_analyst` 的辅助材料；`trader` 只能通过 approved L1 报告间接使用，若要直接可见需另行评审。

不适合：

- 新闻、舆情、基本面资料采集。
- 作为无回测证明的交易建议主依据。

## 5. 建议暴露给 worker 的四个资料包

### 5.1 `claw_get_market_pack`

第一阶段 CRYPTO 市场分析只有一个 worker 可见工具：

- `claw_get_market_pack`。它是 market worker 的统一资料包入口，不是数据源、不是 CryptoLens、不是 legacy 已删除数据网关 本体。
- CRYPTO 下的内部链路是：`claw_get_market_pack -> data_gateway 取数 -> normalized crypto bundle -> CryptoLens analysis engine -> reader_brief`。
- worker 只看到资料包的自然语言 `reader_brief`、紧凑数据摘要、`provider_attempts`、`data_gaps`、`conflicts` 和 `readiness`；provider raw payload、CryptoLens raw result、Mongo raw/cache object 都不得成为 worker 主材料。

注册边界：

- `bb_crypto_data__build_trade_context` 不暴露给 `market_analyst` 的 CRYPTO stage，避免 worker 直接接收旧 BB 原始大 JSON。
- 旧 `crypto_market_data_pack` 只作为历史实现/迁移 alias 讨论；目标态不得作为 data_gateway 失败后的 fallback。
- `claw_get_market_pack` 不能把缺失的 OI、资金费率、清算、多空比、主动买卖、期权、ETF 或链上周期指标补写成已覆盖；缺失必须进入 `data_gaps`。
- CRYPTO market stage 是否真实可用，必须看 fresh provider payload 中的 visible tool schema 和真实 tool call 证据，不能只看静态合同。

目标覆盖：

- 价格结构。
- 技术指标。
- OI。
- 资金费率。
- 清算。
- 多空比。
- 主动买卖。
- ETF。
- 链上周期指标。
- 图表资产：目标态由 `claw_get_market_pack` 的 data_gateway market pack 生成真实 OHLCV 衍生技术图表，供最终报告复制图片时使用；旧 `crypto_market_data_pack` 只作为历史实现说明。

### 5.2 `claw_get_fundamental_pack`

当前实现状态：

- 目标 worker-visible 工具名是 `claw_get_fundamental_pack`；旧 `crypto_fundamental_data_pack` 只作为历史实现路径和迁移对象。
- 当前实现接入 CoinGecko 与 DefiLlama：CoinGecko 负责币种基础资料、市值、FDV、供应量和价格快照；DefiLlama 负责 DeFi 协议 TVL 与 fees/revenue。
- CoinGecko 必须配置 `COINGECKO_DEMO_API_KEY` 或 `COINGECKO_PRO_API_KEY`；缺 key 时资料包记录 credential missing，不走 public no-key 静默 fallback。DefiLlama 免费 API 可无 key，`DEFILLAMA_API_KEY` 只用于 Pro 路径。
- `fundamental_analyst` 的 CRYPTO stage 仍不应仅凭静态工具注册打开；打开前还需要 fresh provider payload、真实 tool call、provider attempts / data gaps / conflicts、approved L1 报告证据。

底层候选：

- DefiLlama。
- CoinGecko。
- Dune / The Graph 后续评估。
- 项目官网、白皮书、治理论坛。

目标覆盖：

- 项目定位。
- token metadata。
- 市值 / FDV / 流通量 / 供应量。
- TVL。
- fees / revenue。
- stablecoin / bridge / yield。
- hacks / unlocks / treasuries。
- 生态和竞争格局。

### 5.3 `claw_get_news_pack`

底层候选：

- Tavily / Brave / SerpAPI / Bocha。
- 项目官方公告。
- 交易所公告。
- 监管机构 / ETF 公开页面。
- Polymarket 事件盘口辅助。

目标覆盖：

- 项目公告。
- 协议升级。
- 治理提案。
- 代币解锁。
- 交易所上币 / 下架。
- 监管事件。
- ETF / 机构资金新闻。
- 安全事件和黑客攻击。

### 5.4 `claw_get_social_pack`

底层候选：

- LunarCrush。
- X / Reddit / Telegram / Discord 搜索。
- Polymarket。
- 商业搜索 API。

目标覆盖：

- 社交热度。
- 情绪方向。
- KOL 叙事。
- 社区分歧。
- 机器人 / 空投噪音。
- 短期情绪对价格和成交量的影响。

## 6. 统一资料包返回结构

建议所有 CRYPTO 资料包返回同一类 envelope：

```json
{
  "asset": "BTC",
  "market": "CRYPTO",
  "as_of": "2026-05-15T00:00:00Z",
  "data": {},
  "sources": [],
  "provider_attempts": [],
  "data_gaps": [],
  "conflicts": [],
  "readiness": {
    "status": "ready",
    "reason": "关键数据域已覆盖"
  }
}
```

字段含义：

- `data`：worker 直接可用的结构化资料。
- `sources`：来源、URL、时间、provider、可信度。
- `provider_attempts`：每个底层 provider 的请求状态、失败原因、限流、认证缺失。
- `data_gaps`：缺失的数据域。
- `conflicts`：不同 provider 之间的口径冲突。
- `readiness`：资料包对 worker 的数据质量说明，不是新增 runtime gate。

## 7. Stage 打开策略

### 7.1 已打开

- `market_analyst` / CRYPTO：只挂 `claw_get_market_pack`。data_gateway 负责所有外部 provider 取数，CryptoLens 负责对 normalized bundle 做离线指标分析。
- `fundamental_analyst` / CRYPTO：只挂 `claw_get_fundamental_pack`，当前目标覆盖 CoinGecko 与 DefiLlama 边界。
- `news_analyst` / CRYPTO：只挂 `claw_get_news_pack`；该包覆盖官方公告/RSS/页面、GitHub releases、交易所公告配置源、监管 feed、DefiLlama 安全/融资背景、Polymarket 事件预期和商业搜索发现。
- `social_analyst` / CRYPTO：只挂 `claw_get_social_pack`；该包覆盖 Alternative.me、LunarCrush、X、Reddit、Telegram、Discord、Polymarket 和公开讨论搜索发现。只有真实 provider payload 返回的平台才算覆盖。
- 下游 8 个正式 worker：`bull_researcher`、`bear_researcher`、`research_manager`、`trader`、`risk_challenger`、`risk_guardian`、`risk_moderator`、`portfolio_manager` 均打开，但不挂外部数据工具，只读取上游 approved L1 报告并对缺口做条件化推理。

### 7.2 下游纯推理 worker 边界

这些 worker 主要消费上游已批准 L1 报告，不需要新外部数据工具：

- `bull_researcher`
- `bear_researcher`
- `research_manager`
- `trader`
- `risk_challenger`
- `risk_guardian`
- `risk_moderator`
- `portfolio_manager`

运行时需要确认：

- CRYPTO prompt 已存在并通过 prompt 合同。
- `market_research_report`、`fundamentals_report`、`news_report`、`sentiment_report` 都来自真实 worker 的 approved L1 自然语言报告。若新闻/舆情资料包返回 `partial`、`insufficient`、缺 key 或调用失败，下游必须把该缺口作为材料边界，不能用空字符串、占位材料、模型常识或 Python 摘要代替。
- 不新增 fallback prompt。
- 不新增 runtime gate。
- provider payload 证明这些 worker 没看到不该看的工具。
- 如果某个前线资料包返回 `partial` 或 `insufficient`，下游可以基于已批准报告做条件化推理；但不能把缺失的基本面、新闻或舆情补写成事实。

### 7.3 覆盖不足的前线资料包

这些 worker 已打开，且已挂真实资料包，但当前覆盖边界如下：

- `news_analyst`：必须调用 `claw_get_news_pack`；如果只得到搜索发现、Polymarket 或 DefiLlama 背景，必须写明缺少官方公告、GitHub、交易所公告和监管原始源。
- `social_analyst`：必须调用 `claw_get_social_pack`；如果只得到 Alternative.me、Polymarket 或搜索发现，必须写明缺少真实社交平台原始舆情覆盖。

原因：

- 新决策要求正式 worker 参与链路；资料包覆盖不足时必须由 worker 自己暴露资料缺口。
- 这避免 Python 补写事实、静默 fallback 或新增 runtime gate / guard。

## 8. Worker 工具挂载建议

| worker | CRYPTO 工具策略 |
|---|---|
| `market_analyst` | 只挂 `claw_get_market_pack`；工具内部经 data_gateway 获取 CRYPTO 市场资料，再调用 CryptoLens 离线分析引擎生成自然语言资料包 |
| `fundamental_analyst` | 只挂 `claw_get_fundamental_pack`；缺 key、失败、partial 或 insufficient 必须写入 L1 缺口 |
| `news_analyst` | 只挂 `claw_get_news_pack`；搜索发现、Polymarket 和 DefiLlama 背景不能被写成新闻事实，缺原始源必须上报 |
| `social_analyst` | 只挂 `claw_get_social_pack`；Alternative.me / Polymarket / 搜索发现不能被写成完整社交舆情，缺原始平台必须上报 |
| `bull_researcher` | 不挂外部数据工具；读四份前线 approved L1 报告，缺口必须条件化处理 |
| `bear_researcher` | 不挂外部数据工具；读四份前线报告和 bull 发言，缺口必须条件化处理 |
| `research_manager` | 不挂外部数据工具；读前线报告和牛熊辩论，不能替缺失前线材料补事实 |
| `trader` | 不挂外部数据工具；读研究经理投资计划，基于上游证据给执行方案 |
| `risk_challenger` | 不挂外部数据工具；读交易员计划和上游报告 |
| `risk_guardian` | 不挂外部数据工具；读交易员计划和激进风险观点 |
| `risk_moderator` | 不挂外部数据工具；读激进和保守风险观点 |
| `portfolio_manager` | 不挂外部数据工具；读全链路已批准材料并做最终裁决 |

## 9. 收费与稳定性判断

| 数据源 | 收费判断 | 稳定性判断 | 备注 |
|---|---|---|---|
| CoinGlass 等 CRYPTO provider（CryptoLens 仅分析） | CoinGlass 已付费；CryptoLens 是项目内部分析代码 | 高，旧 BB 项目已跑通但目标态需迁入本仓库 | CoinGlass 等外部源只经 data_gateway；CryptoLens 不作为数据源 |
| DefiLlama | 免费 API + Pro/API plan | 中高，DeFi 覆盖强 | MCP/API 高额度通常需要计划 |
| CoinGecko | Freemium / Pro | 中高，币种基础覆盖强 | 高频和高级能力需付费 |
| LunarCrush | 商业 / Freemium 倾向 | 待确认 | 社交舆情候选主源 |
| Tavily | Freemium / paid | 中高 | 新闻和网页搜索 |
| SerpAPI | Freemium / paid | 中高 | 新闻和网页搜索 |
| Brave Search API | paid credits | 中高 | 新闻和网页搜索 |
| Bocha | 商业 / 可能有免费额度 | 待确认 | 中文搜索候选 |
| NewsAPI | Developer 免费，生产需付费 | 中 | 新闻检索；免费开发档有延迟和用途限制 |
| Polymarket | 市场数据公开程度高 | 中 | 事件预期，不是新闻事实 |
| Dune | Freemium / paid | 高 | 深度链上，成本和查询维护较高 |
| The Graph | Freemium / paid | 中高 | 链上查询，依赖 subgraph/API 覆盖 |
| legacy 已删除数据网关 | 框架开源，数据源另算 | 不作为当前目标运行时 | 只保留历史评估和 forbidden legacy path 语境 |
| Awesome-finance-skills | 代码免费 | 取决于底层免费源 | 适合参考 skill 设计 |
| daily_stock_analysis | 代码免费 | 取决于底层免费/商业源 | 适合参考配置设计 |
| Kronos | 模型开源 | 取决于输入数据和回测 | 不是数据源 |

## 10. 推荐实施顺序

1. 保持正式 12 个 CRYPTO worker 与 `report_polisher` 打开；当前能力标记为“CRYPTO 全链路，新闻/舆情覆盖不完整”，不声称完整新闻或完整社交舆情覆盖。
2. 用 fresh provider payload 证明 `market_analyst` 实际只看到 `claw_get_market_pack`，并由该资料包内部真实调用 data_gateway provider plugins/adapters 产出市场结构材料，再由 CryptoLens 生成指标分析材料。
3. 对 `claw_get_fundamental_pack` 做 fresh provider payload、真实 tool call、provider attempts / data gaps / conflicts、approved L1 报告验证。
4. 为 `claw_get_news_pack` 配置项目官方公告 / GitHub releases / 交易所公告 / 监管源，并验证 provider attempts 与 source URL。
5. 为 `claw_get_social_pack` 配置 LunarCrush、X、Reddit、Telegram、Discord 等真实社交源，并验证 provider attempts 与平台覆盖状态。
6. 每接一个资料包，就更新对应 CRYPTO prompt 的“可用工具 / 先调用工具”段落。
7. 每打开或调整一个 worker 的 CRYPTO stage，都要用 provider payload 证明：
   - 模型实际看到的工具集合正确。
   - 工具调用真实发生或纯推理 worker 不暴露工具。
   - 输出报告进入正确 L1 artifact。

## 11. Guard 明确边界

当前没有批准新增 CRYPTO runtime guard。这里说的 guard 不是泛泛而谈，具体指以下 runtime artifact flow guard 缺口：

- 目标模块：`src/claw_trade/guards/artifact_flow.py`。
- 目标函数：`validate_artifact_flow(call, manifest)`。
- 已存在合同测试：`tests/contracts/test_artifact_flow_guard.py`。
- 应检查的具体事项：
  - 下游 worker 缺少必需的上游 approved material 时失败。
  - 下游 worker 带入当前 stage 不允许的上游 worker 材料时失败。
  - frontline worker 带入任何上游 material 或 capability 时失败。
  - 上游 ref 的 `l2_index_uri` 被改到别的 evidence index 时失败。
  - 上游 ref 的 `l2_allowed_prefix` 被改到别的目录前缀时失败。
  - approved refs、capabilities 与 manifest 完全一致时通过。

这类 guard 会改变 runtime 接受/拒绝材料的行为；按项目 `Runtime Guard Freeze` 规则，不能在没有明确批准的情况下为了让测试通过就新增或恢复。当前实施只把缺口写清楚，不偷偷加 guard。

## 12. 暂不做

- 不把 legacy 已删除数据网关 全量 MCP 直接挂给 worker。
- 不把 CoinGecko 当成 CoinGlass 替代品。
- 不把 DefiLlama 当成所有币种的万能基本面源。
- 不把 Polymarket 当新闻事实源。
- 不把 Kronos 当数据源。
- 不新增 CRYPTO runtime gate / guard。
- 不接入 Scrapling；当前没有 Scrapling 工具、MCP、provider 或 stage 依赖。未来如需接入，只能作为“指定公开页面提取工具”另行评审，不能当新闻源或舆情源。
- 不添加静默 fallback。

## 13. 参考资料

- CoinGlass API: https://www.coinglass.com/CryptoApi
- CoinGecko API endpoint overview: https://docs.coingecko.com/reference/endpoint-overview
- DefiLlama MCP: https://defillama.com/mcp
- DefiLlama downloads / data categories: https://defillama.com/downloads
- Dune MCP: https://docs.dune.com/api-reference/agents/mcp/
- LunarCrush GitHub: https://github.com/lunarcrush
- Polymarket API docs: https://polymarket-docs.copilot.markets/api-reference/introduction
- 已删除数据网关 MCP tools: https://docs.removed_data_gateway.co/workspace/analysts/ai-features/mcp-tools
- Awesome-finance-skills: https://github.com/RKiding/Awesome-finance-skills
- daily_stock_analysis: https://github.com/ZhuLinsen/daily_stock_analysis
- Kronos: https://github.com/shiyu-coder/Kronos
