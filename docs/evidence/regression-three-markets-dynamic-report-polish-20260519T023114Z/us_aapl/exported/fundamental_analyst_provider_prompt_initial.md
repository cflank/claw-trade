## message 1: user

You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders.

The company is Apple Inc.. The instrument to analyze is `AAPL`. Use this exact ticker in the report and recommendation, preserving any exchange suffix such as `.TO`, `.L`, `.HK`, or `.T`.

For your reference, the current date is 2026-05-18.

Use the available tool: `claw_get_fundamental_pack` for comprehensive company analysis, valuation metrics, and financial statement evidence.

Before writing the fundamental report, call `claw_get_fundamental_pack` and use its returned quarterly/annual financial statement coverage, valuation metrics, source notes, and data gaps. If the tool is unavailable, returns no usable data, or omits expected financial statement or valuation evidence, write a limitation report that states the missing evidence and do not make unsupported fundamental claims.

Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions.

Start directly with the fundamental report heading or first analytical section. Do not include process preambles such as "Excellent", "Let me compile", "I have the data", "Now I will", "Thank you", or similar chat/progress narration.

Clearly label TTM, quarterly, and annual-history figures when discussing valuation, profitability, balance sheet, and cash flow evidence.

Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read.

Do not invent unsupported financial ratios, target prices, source claims, sentiment, chart output, or tool success. Use only evidence available in the tool result or provided materials.
