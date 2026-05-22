# A股扩展方案

状态：需求与设计草案，已完成人类方向确认，未进入实现。
日期：2026-05-20

## 1. 结论

CN_A 采用 A股专属扩展方案。

本方案不是简单补几个数据接口，而是把 A股特色变量纳入正式报告工作流：

- 扩展 CN_A frontline worker。
- 新增 A股特色 OpenBB 资料包。
- 将 `a-stock-data` 的免费数据源分类和默认顺序转成 OpenBB provider 矩阵。
- 复用现有用户声明式 provider/catalog/admission/registry 能力。
- 保持 OpenBB 作为唯一外部数据入口和唯一 provider 接口层。

适用范围只包括 CN_A。US、HK、CRYPTO 不因本方案自动扩展 worker。

## 2. 项目来源定位

### `a-stock-data`

定位：A股免费数据源分类、接口经验、字段映射和默认优先级参考。

它可以提供：

- 行情、盘口、K线、估值快照。
- 财报、研报、一致预期、PEG/估值消化线索。
- 新闻、公告、财联社快讯、全球/宏观资讯。
- 热点题材、概念板块、北向资金、资金流、龙虎榜。
- 限售解禁、股东户数、大宗交易、融资融券、分红送转。

它不能作为：

- worker 直连脚本。
- 独立 MCP。
- 绕过 OpenBB 的 Python provider 执行器。
- OpenBB 失败后的旧路径 silent fallback。

### `TradingAgents-astock`

定位：CN_A 扩展 worker、A股报告维度和 A股分析口径参考。

采用它的方向：

- 政策分析。
- 游资/资金追踪。
- 限售/筹码观察。
- A股特色变量进入牛熊辩论、交易决策、风险辩论和组合经理结论。

不采用它的方向：

- 不照搬 Python/LangGraph 运行结构。
- 不让 Python 伪装成 worker。
- 不让新增 worker 直接调用外部 provider。

### `astock-peg`

定位：估值能力参考。

PEG、估值消化和增长匹配逻辑进入：

- `fundamental_analyst`
- `bull_researcher` / `bear_researcher`
- `research_manager`

第一版不新增独立估值 worker，不引入独立估值 UI，也不引入 `astock-peg` 工程依赖。估值消化先通过 fundamental 资料包中的一致预期、forward PE、PEG 和可追溯口径表达；字段不足时写缺口。

### `global-stock-data`

第一版不引入。当前范围只针对 A股。

## 3. 架构硬边界

### 数据入口

所有外部数据源必须进入 OpenBB/data_gateway：

```text
外部数据源
  -> OpenBB project extension / provider adapter
  -> OpenBB provider attempt / http / raw / normalized / cache evidence
  -> OpenBB domain pack
  -> OpenClaw worker
  -> OpenViking approved L1/L2 material
```

禁止链路：

```text
worker -> a-stock-data 脚本
worker -> Python 直连东财/同花顺/腾讯/巨潮
OpenClaw tool -> 旧 provider executor
用户配置源 -> claw-trade 控制层直接调用
OpenBB 失败 -> 旧数据路径 silent fallback
```

### 运行职责

- claw-trade 控制工作流状态机、worker 调度、artifact 权威、hard gate 和报告导出。
- OpenClaw 只运行单个 worker turn，负责真实 provider prompt、tool schema、tool call 和 LLM response。
- OpenBB/data_gateway 是唯一外部数据入口、唯一 provider 接口层和 provider evidence 记录者。
- Mongo 保存 provider raw/cache/attempt/normalized 等运行证据。
- OpenViking 保存 approved L1/L2 material、manifest、hash、lineage 和下游 handoff。

Python 控制层不得预取 provider 主流程，不得写 worker 投资判断，不得改写 PM 结论。

## 4. CN_A 扩展工作流

CN_A 第一版新增 3 个 A股特色 worker：

```text
policy_analyst
hot_money_tracker
lockup_watcher
```

CN_A 工作流目标顺序：

```text
frontline:
  market_analyst
  fundamental_analyst
  news_analyst
  social_analyst
  policy_analyst
  hot_money_tracker
  lockup_watcher

investment_debate:
  bull_researcher
  bear_researcher

investment_decision:
  research_manager

trade_decision:
  trader

risk_debate:
  risk_challenger
  risk_guardian
  risk_moderator

portfolio_decision:
  portfolio_manager
```

规则：

- 这 3 个新增 worker 出厂默认启用。
- 只对 CN_A 启用扩展路径。
- 新增 worker 必须通过 OpenClaw 唤醒，不能是 Python 函数或 direct LLM。
- 下游 worker 接收 7 份 frontline 已批准自然语言 L1 报告。
- 质量门先作为 claw-trade 控制层阶段校验，不新增独立质量门 agent。

## 5. 新增 CN_A 数据域与资料包

新增 3 个 CN_A 专属数据域：

```text
policy
hot_money
lockup
```

新增 3 个 worker-visible OpenBB 资料包：

```text
policy_analyst    -> claw_get_policy_pack
hot_money_tracker -> claw_get_hot_money_pack
lockup_watcher    -> claw_get_lockup_pack
```

这些资料包在对应 worker turn 内懒加载，不做控制层预取。

### `policy`

用途：政策、监管和行业政策对标的及行业的影响判断。

覆盖内容：

- 监管政策。
- 产业政策。
- 宏观政策。
- 行业政策。
- 政策相关新闻。
- 公告中的政策影响线索。

事实边界：

- 官方公告、监管原文、交易所披露属于 `official_original`。
- 宏观/政策统计属于 `macro_data`。
- 搜索或新闻聚合只能作为发现线索，不能冒充官方事实。

### `hot_money`

用途：短线资金、游资、主力和交易拥挤度判断。

覆盖内容：

- 龙虎榜。
- 游资席位。
- 主力资金。
- 北向资金。
- 个股资金流。
- 板块资金。
- 题材热度和概念活跃度。

事实边界：

- 行情、成交、资金流属于市场事实。
- 龙虎榜和席位数据必须保留来源和日期。
- 热点题材不能直接写成全市场共识。

### `lockup`

用途：筹码结构、供给压力和潜在抛压判断。

覆盖内容：

- 限售解禁。
- 股东户数。
- 大宗交易。
- 融资融券。
- 分红送转。
- 筹码变化。

事实边界：

- 解禁、股东户数、分红等必须保留公告或数据源口径。
- 大宗交易和融资融券不能被写成确定性买卖意图。
- 缺失时必须作为 data gap，而不是补写结论。

## 6. 七个 A股资料域

CN_A 第一版资料能力按 7 个域管理：

```text
market
fundamental
news
social
policy
hot_money
lockup
```

### `market`

内容：

- 行情。
- K线。
- 盘口。
- 逐笔。
- 技术指标。
- 指数/ETF。
- 涨跌停。

`a-stock-data` 参考源：

- mootdx 行情和 F10。
- 腾讯财经 quote。
- 百度 K线。
- 东财 quote/push2。

### `fundamental`

内容：

- 财报三表。
- 财务指标。
- 主营构成。
- PE/PB/PEG。
- 一致预期。
- 研报。

`a-stock-data` 参考源：

- 新浪财报。
- 东财个股信息。
- 东财研报/PDF。
- 同花顺一致预期 EPS。
- `astock-peg` 的历史 PEG/估值口径可作对照参考（仅参考口径，不作为 Phase 1 provider/source/task）。

### `news`

内容：

- 个股新闻。
- 公司公告。
- 财联社快讯。
- 全球/宏观资讯。

`a-stock-data` 参考源：

- 东财个股新闻。
- 财联社快讯。
- 东财全球资讯。
- 巨潮公告。
- mootdx 最新提示/F10。

### `social`

内容：

- 热点题材。
- 概念板块。
- 市场关注度。
- 社区/搜索发现。

`a-stock-data` 参考源：

- 同花顺热点。
- 百度概念板块。
- 搜索发现类源。

### `policy`

内容：

- 政策新闻。
- 监管动态。
- 行业政策。
- 宏观政策。
- 公告中的政策影响线索。

参考源：

- 财联社快讯。
- 东财全球资讯。
- 东财个股新闻。
- 巨潮公告。
- 搜索发现源作为线索。

### `hot_money`

内容：

- 龙虎榜。
- 北向资金。
- 主力资金。
- 个股资金流。
- 板块资金。
- 题材资金。

`a-stock-data` 参考源：

- 同花顺北向。
- 东财资金流。
- 龙虎榜。
- 全市场龙虎榜。
- 行业板块排名。
- 同花顺热点。

### `lockup`

内容：

- 限售解禁。
- 股东户数。
- 大宗交易。
- 融资融券。
- 分红送转。
- 120 日个股资金流中的筹码线索。

`a-stock-data` 参考源：

- 东财 datacenter。
- 限售解禁。
- 股东户数。
- 大宗交易。
- 融资融券。
- 分红送转。
- 个股资金流 120 日。

## 7. Provider 优先级

产品出厂默认不要求用户配置付费 provider。Tushare 等非默认源可以作为用户配置 provider 参与运行；真实产品中未配置时不默认依赖 Tushare。

出厂默认：

```text
使用 a-stock-data 已整理的 A股源分类、字段经验和默认顺序作为 CN_A system provider 矩阵参考；所有实现都必须落到 OpenBB/data_gateway provider/adapter 下。
```

用户配置后：

```text
用户已验证、已启用的 provider 在其声明的同一作用范围内优先。
```

优先级只在同一范围内生效：

```text
market
domain
source_role
coverage_group
```

例如：

- 用户配置行情源，只优先影响行情/K线/盘口覆盖组。
- 用户配置新闻源，只优先影响新闻发现或新闻事实覆盖组。
- 用户配置 policy 源，只优先影响 CN_A policy 域下声明支持的覆盖组。
- 用户配置 hot_money 源，只优先影响资金/龙虎榜/北向等覆盖组。
- 用户配置 lockup 源，只优先影响解禁/筹码/融资融券等覆盖组。

官方原始披露源不可被普通源完全覆盖：

- 巨潮公告。
- 交易所公告。
- 监管或官方披露。

用户源可以补充、交叉验证或在同组内优先；但不能让系统完全不查官方原始披露，也不能跨作用域替代事实来源。

## 8. 用户声明式 provider

现有声明式 provider 后端基础必须复用，不新增第二套系统。

复用范围：

- ProviderCatalog。
- ProviderAdmissionValidator。
- DeclarativeProviderManifest。
- ProviderRegistry。
- USER_DECLARATIVE。
- USER_PREFERRED。

本次 A股扩展要补齐：

- `policy` / `hot_money` / `lockup` 三个新 domain。
- 新 domain 的 coverage_group。
- 新 domain 在 UI 数据源配置中的可见入口。
- 用户 provider 在七个 CN_A domain 下的 health/admission/priority/run plan 证据链。

用户声明式 provider 第一版允许按声明作用域参与七个 CN_A domain；A股扩展新增并必须补齐的是后三个：

```text
market
fundamental
news
social
policy
hot_money
lockup
```

manifest 示例语义：

```text
market=CN_A
domain=hot_money
source_role=market_data
coverage_group=cn_a_hot_money_dragon_tiger
priority_source=USER_PREFERRED
```

声明式 provider 仍必须满足：

- 域名白名单。
- 协议限制。
- DNS 和 redirect 后目标复验。
- 禁止 localhost/private IP/link-local/metadata service。
- credential 检查。
- license/raw export policy。
- schema sample / response mapping。
- admission status 必须进入 enabled candidate 才可参与运行。

普通用户不得上传代码型 provider 进入 live report。

## 9. 失败与缺口策略

默认策略：

```text
1. 先尝试用户已配置、已验证、已启用的 provider。
2. 用户 provider 失败后，尝试同一 coverage_group 下的系统 OpenBB 默认源。
3. 同组默认源也失败时，记录 data gap。
4. worker 报告只能说明缺口，不能补写事实。
```

这不是 silent fallback，因为每次尝试都必须进入证据链：

- 尝试了哪个 provider。
- 成功/失败。
- 失败原因。
- 是否命中缓存。
- 最终哪个 provider 的 normalized 数据进入资料包。
- 哪些字段缺失。
- readiness 是否 ready/partial/insufficient。

官方原始披露失败时：

- 不能用普通新闻冒充公告。
- 可以用新闻或搜索结果提示“可能存在公告事件”。
- 不能把线索写成官方公告事实。
- 必须标记官方源失败和 root cause；该官方覆盖组不得 ready，但 workflow 继续，worker 报告必须明示官方原文缺口。

缓存状态要求：

- `cache_hit` 不能写成 fresh remote success。
- `cache_stale` 不能静默使用。
- `cached_empty` 必须进入缺口。
- `credential_missing`、`rate_limited`、`schema_invalid`、`field_missing` 必须进入 attempts/gaps。

## 10. Worker 可见材料边界

新增 worker 只看自然语言资料包和必要缺口说明。

worker 不应直接看到：

- provider raw JSON。
- HTTP headers。
- provider token。
- Mongo raw/cache/debug envelope。
- OpenBB atomic/admin/discovery tools。
- OpenViking protocol 文本。
- OpenClaw/OpenViking/OpenBB 内部工程协议。

资料包应返回：

- `reader_brief`：自然语言资料正文。
- `compact_facts`：必要事实摘要。
- `data_gaps`：缺失项。
- `readiness`：资料状态。
- `source_refs`：证据引用。
- `chart_refs`：如该域需要图表。
- provider attempt refs。

下游 worker 读取的是已批准 L1 报告正文，不直接拼读 provider raw/cache。

## 11. UI 需求

UI 不是本方案唯一目标，但需要支持 A股扩展后的配置体验。

设置页需要按市场、数据域、覆盖组展示 provider：

```text
CN_A
  market
  fundamental
  news
  social
  policy
  hot_money
  lockup
```

每个 provider 至少展示：

- 数据源名称。
- 数据域。
- 覆盖组。
- source role。
- 是否系统默认源。
- 是否用户配置源。
- 是否启用。
- 优先级。
- 最近测试状态。
- 最近成功时间。
- credential 状态。
- license/raw export policy 状态。

优先级配置：

- 用户可以调整同一覆盖组内的顺序。
- 用户 provider 通过验证并启用后应默认排在同组系统默认源前。
- 官方原始披露源显示为事实权威，不允许普通源替代。

运行页或报告详情需要展示：

- 本次实际使用了哪些数据源。
- 哪些用户源失败后切到了系统 OpenBB 默认源。
- 哪些数据域 partial/insufficient。
- 哪些关键事实来自官方原始披露。

## 12. 实施阶段建议

### Phase 1：A股扩展全量闭环

目标：

- 扩展 CN_A workflow。
- 新增 3 个 worker。
- 新增 3 个 domain。
- 新增 3 个 OpenBB pack。
- 七个 A股资料域的 provider 矩阵都进入工程任务范围。
- 用户声明式 provider 可以在七个 CN_A domain 下按声明作用域参与 registry 和 run plan。

七域 provider 范围：

```text
market:
  mootdx/腾讯/东财/百度等行情、盘口、K线能力

fundamental:
  新浪财报、东财个股信息、同花顺一致预期、东财研报/PDF

news:
  东财个股新闻、财联社快讯、东财全球资讯、巨潮/交易所公告

social:
  同花顺热点、百度概念板块、搜索发现源

policy:
  财联社快讯
  东财全球资讯/政策相关新闻
  巨潮公告政策线索

hot_money:
  龙虎榜
  东财资金流
  北向资金
  板块资金
  题材热度

lockup:
  限售解禁
  股东户数
  大宗交易
  融资融券
  分红送转
  120 日资金流筹码线索
```

验收：

- CN_A `/report` 可跑出 7 个 frontline L1。
- 新增 3 个 worker 均为 OpenClaw worker turn。
- 新增 3 个 pack 均通过 OpenBB/data_gateway 取数。
- provider attempt/http/raw/normalized/cache evidence 可追溯。
- 用户声明式 provider 在七个 CN_A domain 下可按声明作用域进入 enabled candidate 和 run plan。
- 下游 bull/bear/research_manager/trader/risk/PM 可读取 7 份 approved L1。
- 每个计划 provider 都有 attempt；失败、空返回、字段缺失、限流和 schema drift 进入 gaps/readiness。

### Phase 2：provider 稳定性与补强

目标：

- 对 Phase 1 已接入 provider 做字段漂移、限流、失败替换链和更多交叉验证补强。
- 按真实运行证据补充 provider live contract。
- 优化 readiness、gap 和冲突检测。

验收：

- provider 失效时记录真实失败原因。
- 不保留旧 provider executor fallback。
- 不把搜索发现写成官方事实。

### Phase 3：UI 优先级与可观测性

目标：

- 设置页支持 7 个 CN_A 数据域。
- 支持覆盖组内 provider 优先级管理。
- 支持用户源测试、启用、禁用和状态展示。
- 报告运行证据展示实际命中顺序和失败替换链。

注意：

- 不是新增第二套声明式 provider 后端。
- 是把现有 catalog/admission/registry 能力产品化到 A股新增域。

## 13. 停止条件

遇到以下情况必须停下重新评审：

- 某个 A股源必须绕过 OpenBB 才能取数。
- 新增 worker 必须直接调用 provider 脚本才能工作。
- 需要新增第二套用户 provider 配置系统。
- 用户 provider 试图覆盖官方原始披露事实源。
- 缺数据时想用新闻、搜索或模型推断冒充公告、财报、资金事实。
- 需要让 Python 控制层写投资判断或 PM 最终结论。
- 需要把 OpenBB atomic/admin/discovery tools 暴露给报告 worker。
- 需要恢复旧 provider executor 或旧 MCP 作为 runtime fallback。

## 14. 成功标准

第一版成功标准：

- CN_A 扩展 workflow 只对 A股生效。
- 7 个 frontline worker 都是真实 OpenClaw turn。
- 新增 3 个 pack 都走 OpenBB/data_gateway。
- 用户声明式 provider 能参与新增三域排序。
- A股七域 provider 矩阵都进入 OpenBB/data_gateway，并且每个计划 provider 都有 attempt。
- 官方原始披露源边界不被用户源覆盖。
- provider attempts、raw、normalized、cache、gaps 和 readiness 可追溯。
- 最终报告体现政策、游资/资金、限售/筹码三类 A股特色分析。
- 缺失数据被明确说明，不编造、不静默替换。
