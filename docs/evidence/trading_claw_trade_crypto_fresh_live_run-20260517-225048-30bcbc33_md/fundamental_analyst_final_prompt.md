## message 1: user

你是一位专业的加密资产基本面分析师，请输出面向投资读者的中文加密资产基本面报告。

当前分析标的是 Bitcoin（BTC），所属市场 CRYPTO，计价货币为 $。
当前日期：2026-05-17。资料包分析区间：2026-02-01 至 2026-05-17。
请始终使用精确代码 `BTC` 指代该加密资产，不要把它改写成股票代码、交易所股票简称或其它市场标的。

写作边界（必须遵守）：
- 最终报告正文必须直接从报告标题或正文第一句开始，不要先写过程说明。
- 不要输出“我将调用工具”“数据已获取完成”“我已经获取了数据”“现在撰写报告”“根据获得的数据我可以……”这类过程话。
- 不要把加密资产当成股票，不要使用 PE/PB/ROE、EPS、股本、财报利润表或传统公司估值口径。
- 不要编造协议收入、TVL、FDV、流通市值、供应量、解锁、质押率、活跃地址、链上流入流出、开发者活动、治理提案或项目公告。
- 如果代币经济、链上、生态、收入、解锁或治理数据缺失，请在“数据限制与风险提示”部分客观说明，不要把限制说明写在开场。

资料使用要求：
- 如果本 turn 可见 `crypto_fundamental_data_pack`，先调用该资料包，再基于返回的基本面材料、来源、资料缺口、冲突和资料就绪度写报告。
- `crypto_fundamental_data_pack` 调用参数必须使用：ticker=`BTC`，market=`CRYPTO`，company_name=`Bitcoin`，start_date=`2026-02-01`，end_date=`2026-05-17`。不要自行改成旧日期或其它市场区间。
- 如果 `crypto_fundamental_data_pack` 不可见、未调用成功、返回空结果或返回部分覆盖 / 不足，你的 L1 报告必须明确写出“资料包未可用 / 未调用成功 / 覆盖不足”，并逐项说明缺少哪些资料、这些缺口如何影响基本面判断。不得用模型常识、历史印象或上游未提供的证据补写缺失事实。
- `crypto_fundamental_data_pack` 当前覆盖 CoinGecko 币种基础资料和 DefiLlama DeFi 协议经营指标。不要把它返回的部分覆盖或不足补写成完整基本面。
- CoinGecko 资料只能作为币种元数据、市值、FDV、供应量和价格快照；DefiLlama 资料只能作为 DeFi TVL、fees/revenue 等协议经营指标。两者都不是项目公告、新闻事实、社交舆情或链上全量行为来源。
- 报告是给中文投资读者看的，不要把内部工具名、英文状态词或机器字段写进正文。不要输出 `crypto_fundamental_data_pack`、`readiness`、`data_gaps`、`provider_attempts`、`partial`、`insufficient`、`ready`、`worker` 这类词；要写成“基本面资料包”“资料就绪度”“资料缺口”“来源尝试记录”“部分覆盖”“不足”“就绪”“分析师”。

报告至少覆盖以下内容：
1. 项目定位与需求真实性：说明该资产或协议解决什么问题，需求来自支付、结算、智能合约、DeFi、L2、AI、RWA、meme、基础设施或其它场景中的哪一种，需求是否真实、可持续。
2. 代币经济分析：供应总量、流通量、通胀/销毁、释放节奏、解锁压力、质押/锁仓、激励机制、治理权和代币价值捕获方式。没有可靠数据时必须说明缺口。
3. 链上与生态基本面：活跃地址、交易笔数、链上费用、TVL、协议收入、开发者活跃度、生态应用、交易所流入流出和大户行为。区分真实使用、投机交易和短期空投/挖矿驱动。
4. 竞争格局：同赛道主要资产、替代风险、网络效应、流动性护城河、开发者和用户迁移成本。
5. 基本面估值框架：可使用 FDV/TVL、流通市值/TVL、协议收入倍数、费用捕获、市值/链上活跃度、NVT、MVRV、AHR999 或其它加密资产适用指标；不得使用股票 PE/PB/ROE。
6. 合理价格区间或情景目标：给出保守、基准、乐观三种情景的价格区间或估值区间，并明确事实依据、关键假设和失效条件。不要把缺数据的单点价格写成确定结论。
7. 投资建议：从基本面视角给出买入/持有/卖出，并说明该建议需要与市场技术、新闻、情绪和风险分析进一步合并。
8. 数据限制与风险提示：集中说明缺口、时效、来源覆盖、链上口径差异、交易所数据偏差和项目披露不足。

要求：
- 全文使用中文。
- 基于已提供的真实材料撰写，不得编造项目、链上、估值、供应、解锁、治理或收入证据。
- 投资建议必须使用中文“买入/持有/卖出”，不要使用 buy/hold/sell。
- 可以表达明确判断；证据不足时说明缺口和对结论置信度的影响，不要写成默认保守或默认中性。

## message 2: assistant

### tool_calls
- call 1: `claw_get_fundamental_pack`
  args: `{"ticker":"BTC","market":"CRYPTO","company_name":"Bitcoin","start_date":"2026-02-01","end_date":"2026-05-17","current_date":"2026-05-17","currency":"$"}`

## message 3: tool

资料包工具已返回，但资料就绪状态为 partial；这只证明工具调用完成，不证明资料覆盖完成。
请只按下方摘要写已取得事实和缺口，不要补写未提供的数据。
# 基本面资料包（CRYPTO / BTC）
资料状态：部分覆盖。
本资料包仅呈现事实材料、资料缺口与来源状态，不提供投资建议或交易结论。

## 核心字段
- currency: USD
- funding.last_round: 2009-01-03
- security.score: 72.22
- timezone: UTC
- valuation.market_cap_usd: 1.56157e+12

## 官方原文来源
- 当前未获取到官方原文引用。

## 来源状态
- coingecko.coin_profile+market_cap+supply：远端获取成功。
- defillama.tvl+fees+revenue+security+funding：字段缺失。

## 资料缺口
- tvl+fees+revenue+security+funding：defillama/tvl+fees+revenue+security+funding missing required fundamental fields
- supply.circulating：supply.circulating missing from normalized fundamental fields
- supply.total：supply.total missing from normalized fundamental fields
- defi.tvl_usd：defi.tvl_usd missing from normalized fundamental fields
- defi.fees_24h_usd：defi.fees_24h_usd missing from normalized fundamental fields
- defi.revenue_24h_usd：defi.revenue_24h_usd missing from normalized fundamental fields

## 口径冲突
- 未发现 provider 字段冲突。
