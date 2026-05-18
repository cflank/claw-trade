## message 1: user

你是一位专业的股票基本面分析师，请输出面向投资读者的中文基本面报告。

可用工具：`claw_get_fundamental_pack`。
如果消息历史中没有基本面工具结果，请先调用该工具获取真实数据；如果已有结果，不要重复调用，直接写报告。

工具调用参数：
- ticker: `600519.SH`
- market: `CN_A`（必须使用这个精确值，不要写成“中国A股”）
- start_date: `2025-05-17`
- end_date: `2026-05-17`

写作边界（必须遵守）：
- 最终报告正文必须直接从报告标题或正文第一句开始，不要先写过程说明。
- 不要输出“我将调用工具”“数据已获取完成”“我已经获取了数据”“现在撰写报告”“根据获得的数据我可以……”这类过程话。
- 如果数据缺失、口径不完整或时效受限，请在“数据限制与风险提示”部分客观说明，不要把限制说明写在开场。

分析对象：贵州茅台（600519.SH），所属市场 CN_A（中国A股），货币单位人民币（RMB）。
股票代码必须严格使用 `600519.SH` 原样，不得省略或改写后缀。

报告至少覆盖以下内容：
1. 公司经营与财务质量分析（收入、利润、现金流、资产负债结构等）。
2. 估值分析（PE、PB、PEG 等）与当前估值位置判断。
3. 合理价格区间或目标价判断（明确事实依据与情景假设的边界）。
4. 投资建议（买入/持有/卖出）及关键风险因素。
5. 数据限制与风险提示（仅在此处说明缺口与不确定性）。

要求：
- 全文使用中文。
- 基于工具返回的真实数据，不得编造公司信息、财务指标、估值指标或结论依据。
- 投资建议必须使用中文“买入/持有/卖出”，不要使用 buy/hold/sell。

## message 2: assistant

### tool_calls
- call 1: `claw_get_fundamental_pack`
  args: `{"ticker":"600519.SH","market":"CN_A","start_date":"2025-05-17","end_date":"2026-05-17"}`

## message 3: tool

资料包工具已返回，但资料就绪状态为 partial；这只证明工具调用完成，不证明资料覆盖完成。
请只按下方摘要写已取得事实和缺口，不要补写未提供的数据。
# 基本面资料包（CN_A / 600519.SH）
资料状态：部分覆盖。
本资料包仅呈现事实材料、资料缺口与来源状态，不提供投资建议或交易结论。

## 核心字段
- currency: CNY
- financial_indicators.roe: 10.5687
- timezone: Asia/Shanghai
- valuation.pb: 6.1619
- valuation.pe: 20.1803

## 官方原文来源
- 当前未获取到官方原文引用。

## 来源状态
- akshare.stock_financial_analysis_indicator：来源返回为空。
- tushare.fina_indicator+income+balancesheet+cashflow+fina_mainbz：远端获取成功。

## 资料缺口
- stock_financial_analysis_indicator：akshare/stock_financial_analysis_indicator returned empty rows

## 口径冲突
- 未发现 provider 字段冲突。
