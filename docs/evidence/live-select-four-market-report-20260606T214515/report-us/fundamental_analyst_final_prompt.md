## message 1: user

You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders.

The company is Apple Inc.. The instrument to analyze is `AAPL`. Use this exact ticker in the report and recommendation, preserving any exchange suffix such as `.TO`, `.L`, `.HK`, or `.T`.

For your reference, the current date is 2026-06-06.

Use the available tool: `claw_get_fundamental_pack` for comprehensive company analysis, valuation metrics, and financial statement evidence.

Before writing the fundamental report, call `claw_get_fundamental_pack` and use its returned quarterly/annual financial statement coverage, valuation metrics, source notes, and data gaps. If the tool is unavailable, returns no usable data, or omits expected financial statement or valuation evidence, write a limitation report that states the missing evidence and do not make unsupported fundamental claims.

Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions.

Start directly with the fundamental report heading or first analytical section. Do not include process preambles such as "Excellent", "Let me compile", "I have the data", "Now I will", "Thank you", or similar chat/progress narration.

Clearly label TTM, quarterly, and annual-history figures when discussing valuation, profitability, balance sheet, and cash flow evidence.

Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read.

Do not invent unsupported financial ratios, target prices, source claims, sentiment, chart output, or tool success. Use only evidence available in the tool result or provided materials.

## message 2: assistant

### tool_calls
- call 1: `claw_get_fundamental_pack`
  args: `{}`

## message 3: tool

AAPL 的 US 基本面资料包结果：部分可用。
这不是报告结论，只是资料包返回的数据、证据状态和缺口事实；缺失内容不能补写。
数据缺口：
- 财务报表：必需字段缺失，缺少 收入
- 财务报表：未覆盖完整分析区间
- 财务报表：本地数据校验失败，说明：资料包返回字段与请求不一致
- 财务指标：仓库没有可用记录，缺少 净资产收益率、毛利率、profit margin、每股收益、revenue growth
- 财务指标：仓库没有可用记录，缺少 净资产收益率、资产收益率、profit margin、每股收益
- 财务指标：未覆盖完整分析区间
- 估值指标：仓库没有可用记录，缺少 市盈率、市净率、市销率、市值
- 估值指标：未覆盖完整分析区间
审计状态：已形成 2 个标准化数据引用、2 个原始或元数据引用、2 个来源尝试记录。
没有可用数据集时，只能写数据不可用和影响范围，不得用模型常识补出真实行情、基本面、新闻或舆情事实。
