# CryptoLens 接入方案（旧称 BB）

状态：设计方案，未代表代码已完成。
日期：2026-05-18

> **2026-06 当前数据层口径**
>
> 本文中旧称 “已删除数据网关/data_gateway” 的目标入口，当前统一改读为 `src/claw_trade/data_gateway`：核心合同是 `DataRequest -> DataResult`，Provider 能力来自 `ProviderPlugin.capabilities()`，Mongo 证据使用当前 8 个目标 collection。已删除数据网关 本体不是目标运行时依赖；旧 `removed_data_gateway_*` 证据名只可作为历史背景或 forbidden legacy path 参考。

## 1. 结论

CryptoLens 在 claw-trade 中的定位不是数据源，也不是 provider 入口。

CryptoLens 是从 Win11 旧 BB 项目迁入 claw-trade 的 CRYPTO 指标分析引擎。旧称 BB 只作为迁移来源、历史证据和旧路径/旧变量名出现；目标态名称统一为 CryptoLens，未来代码标识可用 `crypto_lens`。

CryptoLens 代码的目标状态是：从 Win11 下已跑通的旧 BB 项目移植进 claw-trade 仓库，成为本项目内部代码的一部分。

目标 report runtime 不依赖 `D:\src\BB`、`/mnt/d/src/BB`、外部 BB MCP 服务或 Win11 旧 BB 项目目录。

CryptoLens 不是数据源、不是 provider、不是外部 MCP、不是独立 worker、不是 trader/PM 决策器。

`data_gateway` 的定位是：统一外部数据入口、provider registry 和 provider evidence 记录者。

因此 CRYPTO market 正确链路是：

```text
market_analyst
  -> claw_get_market_pack
  -> data_gateway 执行外部 provider 取数并写证据
  -> 归一化为 crypto market analysis input
  -> CryptoLens 离线分析引擎计算指标/结构/清算/链上/宏观解释
  -> MarketPackBuilder 渲染自然语言 reader_brief
  -> worker 基于自然语言资料包写 L1 报告
```

禁止链路是：

```text
market_analyst
  -> 旧 BB MCP
  -> 旧 BB 自己调用 CoinGlass/Binance/Bybit/FRED/CoinGecko
```

也禁止：

```text
claw_get_market_pack
  -> data_gateway
  -> CryptoLens
  -> CryptoLens 再调用 data_gateway 或其它外部 provider
```

这样会绕圈，并且会让外部 provider evidence 边界变乱。

## 2. 背景和问题

历史上旧 BB 已经接入过 `market_analyst` 的 CRYPTO stage。

已确认历史事实：

- `memory/2026-05-15.md` 记录：BB 被定位为 OpenClaw worker 挂载的 skill 加 `bb_crypto_data` MCP。
- `memory/2026-05-15.md` 记录：`market_analyst` 的 CRYPTO stage 曾真实调用 BB MCP 并写入 L1 报告。
- `memory/2026-05-16.md` 记录：后来为避免 BB 大 JSON 截断，改为 `crypto_market_data_pack` 集中读取 BB/CoinGlass 并输出自然语言简报。
- `memory/2026-05-16.md` 记录：worker 可见工具收敛为单一资料包，旧 BB 原始 payload 只写证据，不直接给模型。

当前数据层迁移后的问题：

- `claw_get_market_pack` 已成为 market worker 的统一资料包工具。
- 当前 CRYPTO market adapter 实际覆盖仍不足。
- 当前 BTC final report 因此只拿到 OHLCV 与本地技术指标，资金费率、OI、多空比、清算、链上、宏观、AHR999 等 CryptoLens 指标分析没有进入资料包。
- 如果直接把旧 BB MCP 接回去，旧 BB 当前代码会自己出网调用 CoinGecko、CoinGlass、Binance、Bybit、FRED 等外部源。这违反 data_gateway 统一数据入口合同。

所以修复目标不是“恢复旧 BB 直连”，也不是“运行时继续调用 Win11 旧 BB 项目”，而是“把旧 BB 的纯分析代码迁入 claw-trade 并命名为 CryptoLens，改成 data_gateway normalized crypto bundle 之上的本项目内部分析层”。

## 3. 术语

### `claw_get_market_pack`

worker 可见的市场资料包工具名。

它不是数据源。它只是 market worker 获取市场资料包的统一入口。

### data_gateway

项目唯一外部取数与证据链层。

它负责：

- provider catalog；
- provider attempt；
- raw payload evidence；
- normalized evidence；
- cache receipt；
- single-flight；
- RunProviderPlan；
- pack endpoint。

### CryptoLens analysis engine

从 Win11 旧 BB 项目迁入 claw-trade 的离线分析部分。

它负责：

- 技术指标解释；
- 多周期结构；
- AMD/SMC；
- FVG；
- OB；
- 123 突破；
- Vegas；
- 布林带、RSI、MACD、KD；
- 清算图解释；
- funding/OI/多空比/CVD 拥挤度解释；
- 链上、宏观、AHR999 的分析口径；
- 条件化操作框架材料。

它不负责：

- 出网取数；
- 管理 provider key；
- 写 provider HTTP/raw evidence；
- 决定 worker 调度；
- 写最终投资结论；
- 替代 trader 或 portfolio_manager。

它不负责，也不得承担：

- 数据源；
- provider；
- 外部 MCP；
- 独立 worker；
- trader / portfolio_manager 决策器；
- BUY/HOLD/SELL、仓位、交易执行建议、PM rating 或最终投资裁决。

它也不依赖：

- `D:\src\BB`；
- `/mnt/d/src/BB`；
- Win11 旧 BB 项目目录；
- 外部 BB MCP server；
- `BB_MCP_SERVER_PATH`；
- `BB_MCP_CWD`。

### CRYPTO market pack

`claw_get_market_pack` 在 CRYPTO market 下返回的自然语言市场资料包。

它由 data_gateway 数据和 CryptoLens 分析结果共同生成，但 worker 只看到自然语言 `reader_brief`、资料质量说明、图表引用和必要缺口。

## 4. 设计原则

1. data_gateway 是统一外部数据入口。

   所有 CoinGlass、Binance、Bybit、CoinGecko、FRED、Glassnode、DefiLlama、Tavily、Snapshot 等外部请求，都必须通过 data_gateway provider plugin/adapter 进入项目证据链。

2. CryptoLens 在报告 runtime 中不得出网。

   CryptoLens 可以保留旧 BB 的纯分析代码，但不得读取 provider key，不得调用 `fetch`/HTTP client，不得自己请求 CoinGlass/Binance/Bybit/FRED 等外部服务。

3. worker 只看一个 market pack。

   `market_analyst` 只调用 `claw_get_market_pack`。它不得直接看到 CryptoLens raw JSON、旧 BB MCP 原子工具、legacy 已删除数据网关 atomic/admin/discovery tool、Mongo raw/cache/debug envelope。

4. Python 控制层只做计划和调度。

   Python 可以生成 `RunProviderPlan`、调 pack endpoint、保存证据、渲染资料包。Python 不写 worker 的市场分析结论，不写 PM 最终决策。

5. CryptoLens 输出是分析 evidence，不是 provider evidence。

   data_gateway 调外部 provider 的请求记录在 `provider_attempts` / raw refs 中体现。

   data_gateway 保存外部 provider raw payload 到 `raw_payloads` 或对象存储 refs。

   CryptoLens 对 data_gateway normalized bundle 做分析的输入/输出证据应单独命名为 `crypto_lens_analysis_evidence`，不得冒充 provider raw/attempt evidence。

   CryptoLens 输出边界只包括指标解释、条件场景、失效条件和数据缺口；不得输出 BUY/HOLD/SELL、仓位、交易执行建议、PM rating 或最终投资裁决。

6. 不允许 silent fallback。

   如果 data_gateway 某个 CRYPTO 数据域失败，CryptoLens 只能基于已有 normalized 数据输出部分分析和 data gaps，不得自己绕过 data_gateway 去补数，也不得把缺失指标写成已覆盖。

7. 缓存状态必须真实。

   cache hit、cache stale、cached empty、cache error 均不得伪装成 fresh remote success。

## 5. 目标架构

```text
OpenClaw worker turn
  |
  | model-visible tool schema
  v
claw_get_market_pack
  |
  | runtime context: ticker/market/profile/date/run_id/call_id
  v
data_gateway pack/tool backend
  |
  | loads RunProviderPlan
  v
DomainPackService
  |
  | executes provider specs through data_gateway provider plugins/adapters
  v
data_gateway crypto provider plugins
  |
  | writes attempts/raw/cache/normalized evidence
  v
NormalizedCryptoMarketBundle
  |
  | local call, no network
  v
CryptoLens analysis engine
  |
  | writes crypto_lens_analysis_evidence
  v
MarketPackBuilder
  |
  | reader_brief + chart readiness + data gaps + refs
  v
market_analyst LLM prompt
```

### 5.1 `/report` 到 final report 端到端时序

目标 CRYPTO `/report` 运行时序必须是：

```text
Chat /report
  -> claw-trade Runner 创建 report run
  -> RunProviderPlan 只生成 provider plan，不做 prefetch、不写 remote success
  -> OpenClaw wake market_analyst
  -> provider payload 证明 market_analyst 只看到 claw_get_market_pack
  -> market_analyst 调用 claw_get_market_pack
  -> data_gateway pack/tool backend
  -> DomainPackService 按数据请求执行 data_gateway provider plugins/adapters
  -> data_gateway 写 provider attempts/raw refs
  -> data_gateway 写 normalized rows / normalized refs
  -> CryptoLens 只消费 report DataResult 批结果和 normalized refs 做离线分析
  -> CryptoLens 写 crypto_lens_analysis_evidence
  -> MarketPackBuilder 合成 reader_brief、chart readiness、data gaps、refs
  -> market_analyst 基于 reader_brief 写 approved market L1
  -> fundamental/news/social approved L1 进入下游材料边界
  -> bull/bear/research_manager/trader/risk/PM 只消费 approved L1 正文和允许的 refs
  -> portfolio_manager 写 approved PM L1 最终裁决
  -> exporter 只搬运 approved material 和图表引用生成 final report
  -> final report evidence chain 闭合：
     final report -> PM L1 -> downstream approved L1
     -> approved market L1 -> market pack audit
     -> crypto_lens_analysis_evidence
     -> data_gateway normalized refs -> provider attempts
     -> raw payload refs
```

此时 worker 不看 CryptoLens raw JSON、旧 BB MCP 原子工具、legacy 已删除数据网关 atomic/admin/discovery tools、Mongo raw/cache/debug envelope，也不看 OpenViking protocol 正文。

## 6. 组件设计

### 6.1 data_gateway CRYPTO provider plugins/adapters

位置：`src/claw_trade/data_gateway/providers/**`

职责：把 CRYPTO market 所需的外部数据全部纳入 `data_gateway` 统一证据链。

需要拆成多个能力，而不是只保留一个 yfinance OHLCV：

| domain | 说明 | 示例 provider |
|---|---|---|
| `market` | 当前价、市值、成交量、24h 涨跌、dominance、基础 OHLCV | yfinance、CoinGecko、交易所行情 |
| `ohlcv` | 多周期 K 线，供技术分析和图表使用 | Binance、其它 approved exchange |
| `derivatives` | funding、OI、多空比、taker buy/sell、CVD proxy | CoinGlass、Binance futures、Bybit |
| `liquidation_map` | 清算热力图、上下方清算簇、最大清算簇 | CoinGlass |
| `onchain` | 交易所余额、链上流、活跃地址、SOPR/NUPL 等 | CoinGlass、Glassnode、CoinMetrics、mempool、Blockchain.com |
| `macro` | 美债、美元、风险窗口、宏观日历 | FRED、其它 approved macro source |
| `events` | 解锁、治理、安全事件、公告背景 | CoinGlass、DefiLlama、Snapshot、官方源 |
| `ahr999` | BTC AHR999 指标 | CoinGlass 或 approved AHR999 source |

每个 provider call 都必须写：

- `ProviderAttempt`；
- `provider_attempts` / provider payload evidence；
- `raw_payloads`；
- normalized result；
- data gap 或 conflict；
- cache receipt；
- rate-limit 记录。

### 6.2 `NormalizedCryptoMarketBundle`

这是 `data_gateway` 喂给 CryptoLens 的唯一输入。

当前代码实现使用 report path 的 `DataResult` 批结果作为等价输入合同：`rows` 提供 normalized rows，`dataset_refs` 提供 normalized refs，`attempt_refs` 提供来源尝试 refs；不要求仓库中存在名为 `NormalizedCryptoMarketBundle` 的运行时类。

建议结构：

```python
@dataclass(frozen=True)
class NormalizedCryptoMarketBundle:
    run_id: str
    call_id: str
    ticker: str
    market: Literal["CRYPTO"]
    quote: str
    as_of: str
    start_date: str
    end_date: str
    freshness_policy: FreshnessPolicy
    domains: CryptoDomainBundle
    domain_status: Mapping[str, DomainReadiness]
    data_gaps: tuple[DataGap, ...]
    conflicts: tuple[DataConflict, ...]
    source_refs: tuple[ProviderSourceRef, ...]
    attempt_refs: tuple[str, ...]
    raw_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]
```

`domains` 中只放分析所需的 normalized 数据，不放 provider raw payload、headers、tokens、Mongo raw object、debug envelope。

### 6.3 CryptoLens analysis input

CryptoLens 不接触 provider 层，只接收 `data_gateway` 已归一化的 bundle。

建议输入：

```json
{
  "asset": "BTC",
  "quote": "USD",
  "timeframe": "swing",
  "direction": "neutral",
  "include": [
    "market",
    "technical",
    "derivatives",
    "liquidation_map",
    "onchain",
    "macro",
    "events",
    "ahr999"
  ],
  "normalized_domains": {
    "market": {},
    "ohlcv": {},
    "derivatives": {},
    "liquidation_map": {},
    "onchain": {},
    "macro": {},
    "events": {},
    "ahr999": {}
  },
  "data_quality": {
    "domain_status": {},
    "data_gaps": [],
    "conflicts": []
  }
}
```

禁止输入：

- provider headers；
- API key；
- raw payload 全量；
- Mongo raw/cache object；
- OpenViking protocol；
- OpenClaw provider payload；
- worker prompt；
- PM 结论。

### 6.4 CryptoLens analysis engine adapter

新增项目内部 analysis module：

```text
src/claw_trade/data_gateway/analysis/crypto_lens/
  __init__.py
  input_contract.py
  output_contract.py
  adapter.py
  evidence.py
  engine/
    __init__.py
    technical.py
    patterns.py
    derivatives.py
    liquidation.py
    onchain.py
    macro.py
    ahr999.py
    readiness.py
```

职责：

- 将 data_gateway report `DataResult` 批结果中的 normalized rows、attempt refs 和 dataset refs 转成 CryptoLens analysis input；
- 调用本项目内部的 CryptoLens 纯分析函数；
- 禁止 CryptoLens analysis runtime 出网；
- 写 report evidence file；当前 report path 写入 `evidence_root/data-layer/crypto-lens/<run>/<call>/analysis.json`，CryptoLens analysis runtime 不访问 Mongo；
- 把 CryptoLens 输出转成 `CryptoLensAnalysisResult`。

CryptoLens 代码迁移方式：

1. 从 Win11 旧 BB 项目中只读提取纯分析口径、测试样例和历史证据。
2. 在 claw-trade 仓库内用 Python 重写 CryptoLens 分析模块，以便直接被 `DomainPackService` / `MarketPackBuilder` 调用。
3. 重写后的代码归 claw-trade 管理，不再以 `/mnt/d/src/BB`、旧 TS runtime 或旧 BB 构建产物作为运行依赖。
4. 可迁入为分析口径和测试对照的内容包括 technical analyzers、tutorial pattern analyzers、readiness/conflict/data gap 计算、trade context envelope 的非取数部分。
5. 不迁入或不启用 provider fetch 逻辑，包括 CoinGecko、CoinGlass、Binance、Bybit、FRED、Glassnode、DefiLlama、Tavily、Snapshot 等直接 HTTP 调用。
6. 不把 claw-trade workflow、worker prompt、PM 结论或 exporter 逻辑写进 CryptoLens analysis module。
7. 重写后的 module 必须在 `/mnt/d/src/BB` 不存在或不可访问时仍可通过测试和运行。

### 6.5 CryptoLens analysis output

建议结构：

```python
@dataclass(frozen=True)
class CryptoLensAnalysisResult:
    analysis_id: str
    engine_name: str
    engine_version: str
    engine_source_ref: str
    input_normalized_refs: tuple[str, ...]
    status: Literal["ready", "partial", "insufficient"]
    readiness: Readiness
    indicator_coverage: Mapping[str, IndicatorCoverage]
    market_structure: Mapping[str, Any]
    technical_patterns: Mapping[str, Any]
    derivatives_context: Mapping[str, Any]
    liquidation_context: Mapping[str, Any]
    onchain_context: Mapping[str, Any]
    macro_context: Mapping[str, Any]
    ahr999_context: Mapping[str, Any]
    conditional_trade_framework: Mapping[str, Any]
    data_gaps: tuple[DataGap, ...]
    conflicts: tuple[DataConflict, ...]
    output_hash: str
```

`conditional_trade_framework` 只能是 market_analyst 的技术材料，用于描述条件场景、失效条件和资料缺口；不得写 BUY/HOLD/SELL、仓位、交易执行建议、PM rating 或最终投资裁决。

### 6.6 MarketPackBuilder

`MarketPackBuilder` 负责把 data_gateway normalized 数据和 CryptoLens analysis result 合成 worker 可读材料。

CRYPTO market reader brief 必须包含：

- 数据状态；
- 来源成功/失败摘要；
- 指标覆盖；
- 价格与多周期结构；
- CryptoLens 技术形态解释；
- 衍生品拥挤度；
- 清算压力；
- 链上/宏观/AHR999；
- 图表 readiness；
- data gaps；
- conflicts；
- 条件化观察，不写最终投资裁决。

worker 不看：

- provider raw JSON；
- CryptoLens raw JSON；
- Mongo refs 全量；
- debug envelope；
- cache object；
- OpenViking protocol。

## 7. 证据命名

必须严格区分：

### `openclaw_llm_provider_payload`

OpenClaw 发给 LLM provider 的最终 messages、model-visible tool schema、worker_id、stage、run_id、dispatch_id、runtime marker、provider/request id。

它证明 worker 看到了什么。

### `provider_attempts` / provider payload evidence

`data_gateway` 调外部 provider 的请求、响应状态、headers 摘要、source URL、provider request id、latency、错误码。

它证明外部请求真实发生。

### `raw_payloads`

外部 provider 返回的 raw payload/hash/ref 与 raw export policy。

它证明 raw 数据来源。

### `crypto_lens_analysis_evidence`

CryptoLens 分析引擎对 `data_gateway` normalized bundle 的离线分析证据。

建议记录：

- `run_id`；
- `call_id`；
- `worker_id=market_analyst`；
- `analysis_id`；
- CryptoLens engine name/version/source commit 或 source hash；
- analysis input hash；
- referenced normalized refs；
- output hash；
- no-network assertion；
- data gaps/conflicts；
- generated analysis result ref。

它不能替代 `provider_attempts` 或 `raw_payloads`。

## 8. RunProviderPlan 设计

`RunProviderPlan` 仍只做计划，不远端取数。

CRYPTO market plan 应包含多个 provider call specs，例如：

```text
domain=market
  - crypto_spot_snapshot
  - crypto_ohlcv_daily
  - crypto_ohlcv_4h
  - crypto_ohlcv_1h
  - crypto_derivatives_funding
  - crypto_derivatives_open_interest
  - crypto_long_short_ratio
  - crypto_taker_buy_sell
  - crypto_liquidation_map
  - crypto_onchain_metrics
  - crypto_macro_context
  - crypto_events
  - crypto_ahr999
```

计划中允许标注：

- required；
- optional；
- source_role；
- coverage_group；
- priority；
- freshness policy；
- cache key；
- single-flight key。

计划中禁止：

- 调 provider；
- 写 remote success；
- 把旧 BB MCP 当 fallback；
- 让 user-preferred provider 越过 source_role 或 official/original 边界。

## 9. 代码和配置迁移

Win11 下 `D:\src\BB` 旧项目已经跑通，因此它可以作为一次性迁移来源：

- 纯分析代码可以移植进 claw-trade；
- 已验证过的 provider 配置和 API key 可以复用到本项目本地配置或 secret store；
- 历史报告和 memory 可以作为验收对照。

但目标运行态必须与 Win11 旧 BB 项目无关。

复用的是“代码和配置知识”，不是复用旧 BB 项目的运行目录，也不是复用旧 BB 的取数职责。

目标归属必须是：

```text
旧 BB 项目纯分析代码
  -> 移植到 claw-trade 仓库
  -> 命名为 CryptoLens
  -> 作为 src/claw_trade/data_gateway/analysis/crypto_lens/** 内部模块运行

旧 BB 项目已跑通的 provider key / provider 配置
  -> 配置到 claw-trade 本地 .env.local / secret store
  -> data_gateway provider plugins/adapters 读取并执行取数
  -> claw-trade 内部 CryptoLens analysis engine 只读取 data_gateway normalized bundle，不读取 key
```

禁止归属：

```text
claw-trade report runtime
  -> 调用 D:\src\BB 或 /mnt/d/src/BB
  -> 调用外部 BB MCP
  -> BB MCP 读取 key 并出网取数
```

### 9.1 允许迁移的代码

允许从 Win11 旧 BB 项目迁入并命名为 CryptoLens：

- 技术指标计算；
- 多周期样本处理；
- AMD/SMC；
- FVG；
- OB；
- 123 突破；
- Vegas；
- 双线反转；
- 谐波形态；
- 成交量分布；
- 清算簇解释逻辑；
- derivatives 拥挤度解释逻辑；
- onchain/macro/AHR999 的解释口径；
- readiness/data_gaps/conflicts 的纯计算；
- 输出结构和自然语言材料的非 provider 部分。

不允许迁入为运行路径：

- 直接 HTTP provider fetch；
- API key 读取；
- provider cache；
- provider rate limit；
- CoinGlass/Binance/Bybit/FRED/CoinGecko/Glassnode/DefiLlama/Tavily/Snapshot 直连逻辑；
- MCP server 作为 report runtime 依赖。

这些 provider 责任必须在 `data_gateway` 中重新实现或映射。

### 9.2 允许直接迁移的配置

如果本项目缺少以下 key，而 Win11 旧 BB 项目本地已有真实值，可以把真实值复制到本项目 `.env.local` 或后续 secret store：

| 配置名 | 用途 | 目标读取方 |
|---|---|---|
| `COINGECKO_PRO_API_KEY` | CoinGecko Pro 行情/市场上下文 | data_gateway |
| `COINGECKO_DEMO_API_KEY` | CoinGecko Demo 行情/市场上下文 | data_gateway |
| `COINGLASS_API_KEY` | CoinGlass 衍生品、清算、链上、事件、AHR999 | data_gateway |
| `COINGLASS_API_BASE` | CoinGlass 代理或官方 base URL | data_gateway |
| `COINGLASS_API_HEADER_NAME` | CoinGlass 代理 header 名 | data_gateway |
| `FRED_API_KEY` | 宏观序列 | data_gateway |
| `GLASSNODE_API_KEY` | 链上指标 | data_gateway |
| `DEFILLAMA_API_KEY` | DeFi/事件背景 | data_gateway |
| `TAVILY_API_KEY` | 事件/公告搜索补充 | data_gateway |
| `CMC_API_KEY` | 可选市场补充 | data_gateway |
| `CRYPTOQUANT_API_KEY` | 可选链上/交易所流补充 | data_gateway |
| `ETHERSCAN_API_KEY` | 可选链上补充 | data_gateway |
| `THEGRAPH_ACCESS_TOKEN` | 可选协议/链上补充 | data_gateway |

密钥值不得写入：

- docs；
- memory；
- git tracked config；
- evidence markdown；
- final report；
- provider prompt；
- worker material；
- OpenViking approved material。

日志和 evidence 只能记录：

- credential present/missing；
- provider name；
- endpoint；
- HTTP status；
- header allowlist 摘要；
- request id；
- latency；
- raw payload hash/ref。

不得记录密钥值。

### 9.3 当前仓库配置状态

截至 2026-05-18，本项目 `.env.local` 和 `.env.example` 已经包含旧 BB `.env.example` 中列出的主要 CRYPTO provider key 名称：

- `COINGECKO_PRO_API_KEY`
- `COINGECKO_DEMO_API_KEY`
- `DEFILLAMA_API_KEY`
- `TAVILY_API_KEY`
- `CMC_API_KEY`
- `COINGLASS_API_KEY`
- `CRYPTOQUANT_API_KEY`
- `GLASSNODE_API_KEY`
- `ETHERSCAN_API_KEY`
- `THEGRAPH_ACCESS_TOKEN`
- `FRED_API_KEY`

因此本次方案更新不需要把任何真实密钥值写入仓库。

实施时如果发现 Win11 旧 BB 本地 `.env` 有新增 provider key，而本项目缺少对应变量，应只补：

1. `.env.example` 中的空模板；
2. 本地 `.env.local` 中的真实值；
3. provider catalog/adapter 对该变量的读取；
4. credential status evidence。

不要把真实值写入 tracked 文件。

### 9.4 旧 BB MCP runtime 配置边界

`BB_MCP_SERVER_PATH`、`BB_MCP_CWD`、`BB_MCP_HTTP_HOST`、`BB_MCP_HTTP_PORT`、`BB_MCP_HTTP_PATH` 等配置，只能用于：

- 迁移期只读盘点；
- 对比历史证据；
- 本地开发排障。

目标 report runtime 不得依赖这些配置让旧 BB MCP 自己出网取数。

目标完成后，这些变量不应是 CRYPTO market live/fresh report 的必需配置。

验收必须证明：

- `/mnt/d/src/BB` 不存在或不可访问时，CRYPTO market pack 仍能运行；
- `BB_MCP_SERVER_PATH`/`BB_MCP_CWD` 为空或指向无效路径时，CRYPTO market pack 不因此失败；
- report runtime 没有启动外部 BB MCP；
- CryptoLens analysis engine 调用的是 claw-trade 仓库内部代码。

## 10. 错误和降级规则

### data_gateway provider 失败

如果 `data_gateway` 某个数据域失败：

- 写 provider attempt；
- 写 HTTP/raw evidence，如果请求已发出；
- 写 data gap；
- normalized bundle 中该域标为 missing/partial/error；
- CryptoLens 只基于剩余域分析；
- worker 报告必须说明缺口影响。

CryptoLens 不得自己出网补数。

### CryptoLens analysis 失败

如果 `data_gateway` 数据已取到，但 CryptoLens 分析失败：

- provider attempts/raw evidence 仍保留；
- `crypto_lens_analysis_evidence` 写失败状态；
- CRYPTO market pack readiness 降为 `partial` 或 `insufficient`；
- worker 只能使用 `data_gateway` 原始事实的自然语言摘要，不得伪造 CryptoLens 指标分析。

### data_gateway 数据不足

如果 OHLCV 样本不足或某些域为空：

- CryptoLens analysis 可以输出有限指标；
- 未满足样本要求的指标必须写入 data gaps；
- 不得把“指标未计算”写成“指标中性”。

### cache 命中

cache hit 必须带 cache receipt。

如果 freshness 不满足要求，不能写成 fresh。

## 11. 与 worker skill 的关系

`agents/market_analyst/skills/crypto-trading-analysis` 继续存在。

它的职责是：

- 告诉 `market_analyst` 如何解释 CryptoLens 指标分析材料；
- 约束不要把加密币当股票；
- 要求指标按“数据 -> 推导 -> 交易作用 -> 失效”表达；
- 要求缺失指标写清楚；
- 要求不直接读取 raw JSON。

它不负责：

- 取数；
- 调 provider；
- 生成 data_gateway provider evidence；
- 替 CryptoLens 分析引擎计算指标；
- 写最终投资结论。

worker 可见工具仍是：

```text
claw_get_market_pack
```

不是：

```text
bb_crypto_data__build_trade_context
legacy 已删除数据网关 atomic provider tools
CoinGlass tools
Binance tools
```

## 12. 迁移步骤

### T-CL-0：冻结口径

完成项：

- 文档确认 CryptoLens 是分析引擎，不是数据源。
- 文档确认 `data_gateway` 是统一取数入口。
- 文档确认 CryptoLens 代码目标态迁入 claw-trade 仓库，不依赖 Win11 旧 BB 项目。
- 当前 canonical BTC final report 不能作为 CryptoLens 覆盖验收证据。

### T-CL-1：盘点旧 BB 代码

目标：

- 列出旧 BB 中所有 provider fetch、URL、API key、fallback 和 cache 逻辑。
- 标出哪些模块可作为纯分析复用。
- 标出哪些模块禁止进入 claw-trade report runtime。
- 形成迁入清单，列出每个 CryptoLens 分析模块在 claw-trade 中的目标文件路径。
- 形成剔除清单，列出所有不得迁入 report runtime 的 provider 取数代码。

当前已知禁止直接复用：

- `/mnt/d/src/BB/mcp/crypto-data-mcp/src/domains/live.ts` 的 provider fetch 路径。

可评估复用：

- technical analyzers；
- tutorial pattern analyzers；
- readiness/conflict/data gap 计算；
- trade context envelope 构造的非取数部分。

### T-CL-2：迁入 CryptoLens 纯分析代码

目标：

- 将旧 BB 纯分析逻辑迁入 claw-trade 仓库并命名为 CryptoLens。
- 目标目录优先为 `src/claw_trade/data_gateway/analysis/crypto_lens/**`。
- 迁入后的代码归本项目测试、lint、review 和 memory 记录管理。
- 删除或隔离所有 provider fetch/key/cache/rate-limit 逻辑。

验收：

- 本项目内部 import 可用；
- 不引用 `/mnt/d/src/BB`；
- 不依赖 `BB_MCP_*`；
- 不读取 provider key；
- 不出网；
- 可对 fixture normalized bundle 输出分析结果。

### T-CL-3：定义 data_gateway normalized crypto bundle

新增合同测试：

- 完整 bundle；
- 缺 derivatives；
- 缺 liquidation_map；
- OHLCV 样本不足；
- cache stale；
- provider conflict；
- raw refs 存在但不进入 worker material。

### T-CL-4：补 data_gateway CRYPTO provider plugins/adapters

把 CRYPTO market 从当前 yfinance-only 扩展为多域 provider plan。

最低验收域：

- spot/market；
- OHLCV；
- technical input series；
- derivatives；
- liquidation_map；
- onchain 或明确缺口；
- macro 或明确缺口；
- events 或明确缺口；
- AHR999 或明确缺口。

### T-CL-5：实现本项目 CryptoLens 离线分析入口

目标接口：

```text
analyze_crypto_lens_data_results(results: Sequence[DataResult], ...) -> CryptoLensAnalysisResult
```

要求：

- 不调用网络；
- 不读取 provider key；
- 不访问 Mongo；
- 不调用外部 provider；
- 只读入参；
- 输出可 hash；
- 缺口原样传递或进一步细化；
- 不写投资最终裁决。

### T-CL-6：接入 MarketPackBuilder

CRYPTO `claw_get_market_pack` 流程改为：

```text
data_gateway provider execution
  -> report DataResult batch with normalized rows / refs
  -> CryptoLens analysis engine
  -> reader_brief
  -> chart assets
  -> pack audit
```

### T-CL-7：证据链和 OpenViking

必须能追溯：

```text
final report claim
  -> PM L1
  -> market_analyst L1
  -> CRYPTO market pack audit
  -> crypto_lens_analysis_evidence
  -> normalized_datasets refs
  -> provider_attempts
  -> provider request evidence
  -> raw_payloads
```

### T-CL-8：验收

至少跑 BTC fresh/live：

- fixed runtime preflight；
- `openclaw_llm_provider_payload`；
- visible tool schema 只含 `claw_get_market_pack`；
- provider_attempts/raw_payloads evidence；
- CryptoLens analysis evidence；
- Mongo attempts/raw/cache/normalized/run-plan/single-flight/validation receipt；
- OpenViking lineage/ovpack/runtime health；
- chart readiness；
- final report evidence chain。
- Win11 旧 BB 项目路径不可访问仍通过。

## 13. 测试矩阵

### 单元测试

- CryptoLens analysis adapter 不出网：monkeypatch `fetch`/HTTP client 后仍可用。
- CryptoLens analysis adapter 不读取 provider env key。
- data_gateway provider adapter 可以读取从 Win11 旧 BB 迁移过来的本地 key，但 evidence 不泄漏 key 值。
- CryptoLens analysis module 不引用 `/mnt/d/src/BB`、`D:\src\BB`、`BB_MCP_SERVER_PATH`、`BB_MCP_CWD`。
- data_gateway DataResult rows / normalized refs -> CryptoLens input 映射正确。
- 缺 derivatives 时 funding/OI/多空比/CVD 均进入缺口，不写中性。
- 缺 liquidation_map 时不生成清算簇结论。
- OHLCV 样本不足时技术指标状态为 partial/insufficient。
- AHR999 只对 BTC 可用，其它币种为 not applicable 或缺口。

### 合同测试

- `claw_get_market_pack` model-visible schema 不接受模型传 ticker/market/date 覆盖 runtime。
- CRYPTO market pack output 不包含 raw JSON/debug envelope/Mongo raw/cache object。
- provider_attempts/raw_payloads evidence 不被命名为 OpenClaw LLM payload。
- `crypto_lens_analysis_evidence` 不被命名为 data_gateway provider evidence。
- data_gateway provider 失败时不调用旧 BB live provider fetch。

### import-block 测试

在 data_gateway schema 模式下：

- 旧 `frontline_data_pack` 不得被 import/call；
- 旧 `crypto_market_data_pack.py` 不得作为 fallback；
- 旧 BB `domains/live.ts` 的直接 provider fetch 路径不得被 report runtime 调用；
- 外部 Win11 BB MCP 不得被 report runtime 调用；
- 把 `BB_MCP_SERVER_PATH` 和 `BB_MCP_CWD` 设置成无效路径时，目标态 CRYPTO market pack 不得因此失败；
- US atomics 和 legacy 已删除数据网关 atomic provider tools 不得进入 worker tool schema。

### 集成测试

- 构造 fixture data_gateway DataResult 批结果，验证 CryptoLens output 和 reader brief。
- 构造真实 CRYPTO provider attempts，验证 normalized refs 进入 CryptoLens analysis input。
- 验证 chart assets 可被 exporter 复制。
- 验证 OpenViking relations 包含 CryptoLens analysis evidence 节点。

### live/fresh 验收

BTC 样本必须证明：

- data_gateway 负责所有外部 provider 请求；
- CryptoLens analysis engine 无外部请求；
- CryptoLens analysis engine 来自 claw-trade 仓库内部代码；
- 不依赖 Win11 旧 BB 项目路径或外部 BB MCP；
- 报告包含 CryptoLens 指标分析材料；
- 缺失域真实写缺口；
- 终稿没有把缺口写成成功覆盖。

## 14. 当前已知偏差

当前 canonical T16 final BTC 证据目录：

```text
docs/evidence/removed_data_gateway-canonical-t16-final-20260518T125112Z/crypto_btc/
```

不能作为 CryptoLens 接入完成证据。

原因：

- CRYPTO market source 只显示 `removed_data_gateway_yfinance/crypto_price_historical`；
- CryptoLens 分析材料没有进入当前 `claw_get_market_pack` 后端；
- 该旧 final report 中的旧 BB/CoinGlass 缺失说明是诚实缺口，但不是目标完成态。

## 15. 不做事项

本方案不做：

- 不恢复旧 `crypto_market_data_pack.py` 作为可运行 fallback；
- 不让 worker 直接调用 `bb_crypto_data__build_trade_context`；
- 不让 CryptoLens 自己调用 CoinGlass/Binance/Bybit/FRED；
- 不让 report runtime 调用 Win11 下的旧 BB 项目目录；
- 不把外部 BB MCP 作为目标运行依赖；
- 不恢复 `third_party/removed_data_gateway`，也不把 CryptoLens 放进外部 runtime 写 claw-trade 业务逻辑；
- 不让 Python 改写 PM 结论；
- 不新增投资判断 gate 或风格 gate；
- 不把 OpenViking 当 fresh provider 数据源；
- 不用 mock/stub/fake/capture-only 通过验收。

## 16. 人类决策状态

已确认的人类口径：

- CryptoLens 不是数据源，是从旧 BB 项目迁入的加密币指标分析系统。
- 旧 BB 项目的纯分析代码要移植进本项目并命名为 CryptoLens，作为 claw-trade 的一部分；目标态与 Win11 下旧 BB 项目无关。
- CryptoLens 需要的数据源应通过 `data_gateway` 获取。
- Win11 旧 BB 项目已经跑通的 provider 配置和 API key 可以直接迁移到本项目本地配置或 secret store，如果本项目缺失。
- provider 接入方向已批准：每个 provider 必须有 license/cost/raw export approval record；不得泄漏 secret；未经许可不得导出 raw 全文。
- CryptoLens 采用 Python 重写，不保留旧 BB 运行时代码或 TS runtime 依赖。
- 旧 BB 运行相关内容应删除/禁用以避免污染；迁移审计证据与只读盘点清单必须保留。
- data_gateway 覆盖口径（人话）：BTC 报告需要价格/K线、资金费率、持仓、清算、链上、宏观、事件、AHR999；拿到就进资料包，拿不到就写真实缺口。
- 证据链口径（人话）：最终报告中的判断要能一路追到 market L1、market pack、CryptoLens 分析、normalized/provider attempt/raw payload evidence。
- data_gateway 形态口径（人话）：所有外部取数只走 `data_gateway` 这一个门，不能让 Python 或旧 BB 自己出网。
- 加密 market 通过 CryptoLens 分析能力生成指标解释，worker 仍通过 `claw_get_market_pack` 获取材料。
- `claw_get_market_pack` 继续作为 market worker 的统一资料包入口。

仍需在实施时以代码证据关闭的问题：

- CryptoLens 纯分析口径用 Python 重写后的具体文件拆分和测试边界。
- data_gateway CRYPTO provider plugins/adapters 的最终 provider 列表、key 映射和 run-time 实测覆盖证据。
- 每个 provider 的许可、费用和 raw export policy 记录落点（含审批时间、审批人、导出策略）及其测试闭环。
- CryptoLens analysis evidence 的 Mongo collection 名称和 OpenViking relation kind 的最终选型与关系断言测试。

## 17. 最小完成标准

只有同时满足以下条件，才能说 CryptoLens 接入完成：

1. `claw_get_market_pack` 在 CRYPTO 下真实走 data_gateway provider plan 取数。
2. 所有外部 provider 请求都有 provider_attempts/raw_payloads evidence。
3. CryptoLens analysis engine 在 report runtime 中不出网、不读 provider key。
4. CryptoLens analysis engine 来自 claw-trade 仓库内部代码，不依赖 Win11 旧 BB 项目、`/mnt/d/src/BB` 或外部 BB MCP。
5. CryptoLens analysis input 只来自 data_gateway normalized rows、DataResult 批结果和 refs。
6. worker 只看到自然语言 market pack。
7. BTC fresh/live report 中恢复 CryptoLens 指标分析密度。
8. 缺失字段仍真实进入 data gaps。
9. final report evidence chain 能追到 PM L1、market L1、CryptoLens analysis evidence、normalized/raw/provider attempt refs。
10. import-block 证明旧 BB/旧 provider 不能 silent fallback。
11. focused tests 和 live/fresh 验收全部通过。
