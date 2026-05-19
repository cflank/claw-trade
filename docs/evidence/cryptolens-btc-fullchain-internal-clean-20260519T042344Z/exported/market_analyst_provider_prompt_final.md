## message 1: user

请输出你的前线加密币市场与技术分析报告。

你是一位专业的加密货币市场技术分析师，重点分析价格结构、技术指标、衍生品拥挤度、清算压力、链上与宏观风险对当前交易窗口的影响。

可用工具：`claw_get_market_pack`。

请先调用 `claw_get_market_pack`。这个资料包内部通过 OpenBB/data_gateway 获取市场结构、衍生品、清算、链上、宏观、AHR999、公开交易所 OHLCV 和图表资产，再把归一化数据交给 CryptoLens 离线分析引擎生成指标解释。

重要边界：worker 不直接读取 CryptoLens raw JSON、OpenBB raw payload、旧外部加密 MCP 原子工具或任何 provider 原始大 JSON；你只基于资料包返回的自然语言 `reader_brief`、紧凑数据摘要和数据质量说明写自己的市场分析报告。原始返回内容只作为证据文件保存，不作为报告主体。

调用 `claw_get_market_pack` 时不需要填写 ticker、market、日期、币种或项目名；这些运行参数已由系统上下文锁定。

写作边界（必须遵守）：
- 最终报告正文必须直接从报告标题或正文第一句开始，不要先写过程说明。
- 不要输出“我将调用工具”“数据已获取完成”“现在撰写报告”这类过程话。
- 不要把加密币当成股票，不要使用 PE/PB/ROE、财报、股本或传统公司估值口径。
- 不要编造价格、指标、清算图、资金费率、链上、宏观、事件、来源或工具成功。
- 资料包返回的资料就绪度、警告、资料缺口、冲突是数据质量材料；请在报告里解释其影响，但不要把它写成系统拦截或工程规则。
- 资料就绪度只能说明资料覆盖和通道质量，不能替代具体指标读数；如果资金费率、OI、多空比、CVD 或清算点位没有具体数值，只能写“具体读数缺失”，不得推断其正常、过热或极端。
- 如果资料包只列出价格历史和本地技术指标成功，而没有 OpenBB provider attempts、资金费率、OI、多空比、清算、链上、宏观或 AHR999 的成功来源，这些指标必须写为未覆盖，不能用常识或市场印象补齐。
- 资金费率、OI、清算金额等单位必须沿用资料包原文；资料包只给原始值但没有确认单位时，不要自行换算成百分比、年化或美元金额。
- 如果 OpenBB/data_gateway 的 CRYPTO provider 路径不可用、接口密钥缺失、限流、字段缺失或样本不足，请写出可用观察、缺口、影响和下一步取数建议，不要补写不存在的数据。
- 如果 `claw_get_market_pack` 没有生成图表文件，只能说明图表资产缺失，不要编造图片路径或图表结论。
- 工具结果是 JSON 时，不要把 JSON 原文作为报告主体；要转成读者可读的自然语言分析。
- 最终市场报告是给中文读者看的，不要把内部字段名写进正文。必须把类似 `nearest_above`、`nearest_below`、`value_area_low`、`cumulative_delta`、`latest_delta`、`readiness`、`data_gaps`、`provider_attempts`、`overall_score`、`confidence=medium` 这类字段或键值串改写成中文标签，例如“上方最近清算簇”“下方最近清算簇”“价值区低点”“主动买卖量累计差值”“资料就绪度”“资料缺口”“来源尝试记录”“整体评分”“置信度为中等”。

现在请基于 `claw_get_market_pack` 返回的真实材料，生成详细的 CRYPTO 市场分析报告。

**分析对象：**
- 币种：BTC
- 名称：Bitcoin
- 所属市场：CRYPTO
- 计价货币：USDT（USDT）
- 分析日期：2026-05-19
- 数据区间：2025-05-19 至 2026-05-19

**输出格式要求（必须严格遵守）：**

## 加密币基本信息

- **币种**：BTC
- **名称**：Bitcoin
- **当前价格**：[从资料包中获取] USDT
- **24h 涨跌幅**：[从资料包中获取]
- **24h 成交量**：[从资料包中获取]
- **数据时间**：[从 `as_of` / sources 中获取]
- **数据质量**：[用中文概括资料就绪度、警告、资料缺口、冲突]

## 市场结构结论

[先用 plain Chinese 给出当前市场结构判断：趋势、震荡、转折、过热、挤压、缺口或观望。说明这个结论为什么可能错。]

## 指标覆盖

请逐项说明以下指标是 `已引用`、`有数据但未构成信号`、`缺失/不可用` 或 `不适用`：

- 布林带
- 维加斯通道
- 双线反转
- AMD/SMC
- 123 突破
- FVG
- OB 订单块
- RSI
- MACD
- KD
- TD 9/13
- 谐波形态
- 交易密集带/成交量分布
- 清算地图
- CVD/主动买卖量
- 资金费率
- OI/多空比
- 宏观
- 链上
- AHR999

## 指标推导过程

每个小节必须使用 Markdown 表格，不要写成整段散文。表头固定为：

| 指标 | 数据 | 推导 | 交易作用 | 失效条件 |

规则：
- 每个关键指标单独一行；不要把 Vegas、布林带、RSI、MACD、KD 合成一个长段落。
- 如果某项缺失，在“数据”列写“缺失/不可用”，在“推导”和“交易作用”列写清影响，不要空着。
- 每个单元格只写 1-2 句，避免长段落堆在一个格子里。
- 表格后可以补 1 段小结，但小结不能替代表格。

### OB 订单块

### POC / 成交量分布

### TD Sequential

### 谐波形态

### FVG

### AMD/SMC 与 123

### Vegas / 布林带 / RSI / MACD / KD

### 清算地图 / CVD / 资金费率 / OI / 多空比

### 宏观 / 链上 / AHR999 / 事件

## 多空场景

### 偏多场景

[列出触发条件、确认指标、目标逻辑、失效条件。]

### 偏空场景

[列出触发条件、确认指标、目标逻辑、失效条件。]

### 观望场景

[说明什么情况下不应追多或追空，哪些数据缺口会压低结论置信度。]

## 关键价格与风险

- **支撑区间**：[具体价格或区间；无可靠数据则说明缺口]
- **压力区间**：[具体价格或区间；无可靠数据则说明缺口]
- **触发位**：[突破/跌破/回踩确认位]
- **失效位**：[结构失效或风险控制位置]
- **主要风险**：[资金费率、OI、清算、宏观、链上、事件或数据缺口]

## 数据限制与下一步

[客观说明资料包返回的警告、资料缺口、冲突、样本限制、来源限流或接口密钥缺失，以及这些限制如何影响结论。]

**重要提醒：**
- 报告必须基于 `claw_get_market_pack` 返回的真实数据；OpenBB/provider 原始证据和 CryptoLens raw analysis 只进入证据链，不进入报告主体。
- 报告长度不少于 900 字。
- 使用中文撰写。
- 可以给出技术面偏多、偏空或中性判断，但不要把前线市场分析写成最终投资裁决。
- 不要使用“最终交易建议”前缀；最终决策需要后续 worker 和 portfolio manager 综合判断。

## message 2: assistant

### tool_calls
- call 1: `claw_get_market_pack`
  args: `{}`

## message 3: tool

资料包工具已返回，但资料就绪状态为 partial；这只证明工具调用完成，不证明资料覆盖完成。
请只按下方摘要写已取得事实和缺口，不要补写未提供的数据。
## 数据资料包：BTC / CRYPTO / market

### 资料就绪度
- 当前状态：部分覆盖
- 根因：存在非阻断缺口，建议结合 attempts 与 data gaps 一起解读。
- 覆盖概览：完成 7 项检查，仍有 0 项缺口。
- 加密市场覆盖边界：本次资料就绪度只代表已列明来源的价格历史、OpenBB 归一化资料、CryptoLens 指标分析和图表资产；如果来源列表没有资金费率、OI、多空比、清算、链上、宏观或 AHR999 的成功记录，这些指标均视为未覆盖，不得写成已验证事实。

### 核心事实材料
- OHLCV 行数：705（交易日升序）
- 最新收盘：76,667.81
- 均线：MA5=78,469.11，MA10=79,663.17，MA20=79,323.30
- 成交量：最新=39,203,663,872.00，VOL_MA5=33,582,928,208.20，VOL_MA20=33,279,044,596.65
- MACD：macd=609.9201，signal=1,285.86，hist=-675.9393
- RSI(14)：44.0936
- Bollinger：mid=79,323.30，upper=82,914.77，lower=75,731.82
- 币种/时区：USDT / UTC

### 图表和指标
- 价格与均线结构：就绪
- 指标面板：就绪

### CryptoLens 指标分析
- 数据状态：部分覆盖；分析状态：partial。
- 来源摘要：CryptoLens 仅消费 OpenBB 归一化资料，本次引用 7 个 normalized ref。
- 指标覆盖：价格结构=就绪；技术形态=就绪；衍生品=就绪；清算=就绪；链上=部分覆盖；宏观=就绪；AHR999=就绪
- 价格/多周期结构：就绪；现价 76667.81，OHLCV 样本 705 条。 观察期涨跌约 14.85%。
- 技术形态：就绪；技术形态以 OpenBB OHLCV 本地计算，覆盖维加斯通道、双线反转、FVG、KD、TD。 RSI 中性。 MACD 柱值 -675.9393。 维加斯通道：位置=below_blue_band，主趋势=major_trend_bearish，距蓝带中线=-3.71%。 双线反转：EMA20/EMA50 状态=above_lines，EMA20=78742.27，EMA50=76707.79。 FVG：候选 21 个，开放 1 个，最近下方缺口中线 73515.62。 KD(9,3,3)：K=16.40，D=30.61，J=-12.02。 TD Sequential：方向=bullish_reversal，setup=6，countdown=13，信号=bullish_reversal_countdown_13。 观察期价格上行。 备注：样本长度达到多周期形态粗判阈值
- 衍生品拥挤度：就绪；衍生品上下文基于 funding/OI/多空比/CVD 代理字段生成。 funding=0.005000. OI=27641932.59. 多空比=1.420. CVD代理=-221590818.84.
- 清算压力：就绪；清算上下文基于清算簇聚合结果生成。 最大清算簇价格 78817.66. 规模 165628998.16.
- 链上指标：部分覆盖；链上上下文来自归一化链上字段。 大额转账样本=804. 缺口：run-20260519-042451-bb511eac:run-20260519-042451-bb511eac-frontline-t00-market_analyst-20260519T042451565119Z-b3bd2a5e__tool-call_00_LpAIuNyM4sHdsyKoRNkP4429:crypto_lens:onchain.exchange_netflow、run-20260519-042451-bb511eac:run-20260519-042451-bb511eac-frontline-t00-market_analyst-20260519T042451565119Z-b3bd2a5e__tool-call_00_LpAIuNyM4sHdsyKoRNkP4429:crypto_lens:onchain.active_addresses、run-20260519-042451-bb511eac:run-20260519-042451-bb511eac-frontline-t00-market_analyst-20260519T042451565119Z-b3bd2a5e__tool-call_00_LpAIuNyM4sHdsyKoRNkP4429:crypto_lens:onchain.sth_sopr、run-20260519-042451-bb511eac:run-20260519-042451-bb511eac-frontline-t00-market_analyst-20260519T042451565119Z-b3bd2a5e__tool-call_00_LpAIuNyM4sHdsyKoRNkP4429:crypto_lens:onchain.lth_sopr、run-20260519-042451-bb511eac:run-20260519-042451-bb511eac-frontline-t00-market_analyst-20260519T042451565119Z-b3bd2a5e__tool-call_00_LpAIuNyM4sHdsyKoRNkP4429:crypto_lens:onchain.nupl、run-20260519-042451-bb511eac:run-20260519-042451-bb511eac-frontline-t00-market_analyst-20260519T042451565119Z-b3bd2a5e__tool-call_00_LpAIuNyM4sHdsyKoRNkP4429:crypto_lens:onchain.stablecoin_exchange_netflow
- 宏观约束：就绪；宏观上下文来自归一化宏观字段。 美债10Y=4.59.
- AHR999：就绪；AHR999 上下文来自归一化 AHR999 字段。 指标值=0.4664.
- 图表 readiness：部分覆盖；图表结论须以资料包图表条目为准。
- conflicts：暂无。
- data gaps：onchain.exchange_netflow:CryptoLens 输入缺少 onchain.exchange_netflow，链上资料只能部分覆盖。；onchain.active_addresses:CryptoLens 输入缺少 onchain.active_addresses，链上资料只能部分覆盖。；onchain.sth_sopr:CryptoLens 输入缺少 onchain.sth_sopr，链上资料只能部分覆盖。；onchain.lth_sopr:CryptoLens 输入缺少 onchain.lth_sopr，链上资料只能部分覆盖。；onchain.nupl:CryptoLens 输入缺少 onchain.nupl，链上资料只能部分覆盖。；onchain.stablecoin_exchange_netflow:CryptoLens 输入缺少 onchain.stablecoin_exchange_netflow，链上资料只能部分覆盖。
- 条件化观察：上述结构、技术、衍生品与清算状态只用于 market analyst 形成市场解释；若这些缺口未补齐，相关观察失效：暂无关键失效缺口。

### 来源和缺口
- openbb_yfinance/crypto_price_historical：远端获取成功（资料新鲜度：远端实时获取）
- coinglass/futures_oi_funding：远端获取成功（资料新鲜度：远端实时获取）
- coinglass/liquidation_heatmap：远端获取成功（资料新鲜度：远端实时获取）
- coinglass/onchain_signals：远端获取成功（资料新鲜度：远端实时获取）
- fred/macro_regime：远端获取成功（资料新鲜度：远端实时获取）
- tavily/catalyst_events：远端获取成功（资料新鲜度：远端实时获取）
- coinglass/ahr999_index：远端获取成功（资料新鲜度：远端实时获取）
- 字段缺失：CryptoLens 输入缺少 onchain.exchange_netflow，链上资料只能部分覆盖。
- 字段缺失：CryptoLens 输入缺少 onchain.active_addresses，链上资料只能部分覆盖。
- 字段缺失：CryptoLens 输入缺少 onchain.sth_sopr，链上资料只能部分覆盖。
- 字段缺失：CryptoLens 输入缺少 onchain.lth_sopr，链上资料只能部分覆盖。
- 字段缺失：CryptoLens 输入缺少 onchain.nupl，链上资料只能部分覆盖。
- 字段缺失：CryptoLens 输入缺少 onchain.stablecoin_exchange_netflow，链上资料只能部分覆盖。

### 分析时必须注意
- 缓存命中不等于远端实时成功，需结合来源尝试和资料就绪度解读。
- 缺 key、限流、空返回、字段缺失已写入缺口；禁止把缺口信息改写成成功覆盖。
- 本资料包只提供事实材料与技术指标，不直接生成投资结论。
