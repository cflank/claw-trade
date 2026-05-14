## message 1: user

You are a researcher tasked with analyzing fundamental information over the past week about a company. Please write a comprehensive report of the company's fundamental information such as financial documents, company profile, basic company financials, and company financial history to gain a full view of the company's fundamental information to inform traders.

The company is Apple Inc.. The instrument to analyze is `AAPL`. Use this exact ticker in every tool call, report, and recommendation, preserving any exchange suffix such as `.TO`, `.L`, `.HK`, or `.T`.

For your reference, the current date is 2026-05-14.

Use the available tools: `get_fundamentals` for comprehensive company analysis, `get_balance_sheet`, `get_cashflow`, and `get_income_statement` for specific financial statements.

Before writing the fundamental report, complete the full evidence collection sequence: first call `get_fundamentals`, then call each statement tool with both quarterly and annual history: `get_balance_sheet` with `freq="quarterly"` and `freq="annual"`, `get_cashflow` with `freq="quarterly"` and `freq="annual"`, and `get_income_statement` with `freq="quarterly"` and `freq="annual"`. Use the quarterly statements for recent operating momentum and the annual statements for multi-year history, matching the original TradingAgents fundamental evidence pattern. If one statement call fails or returns no usable data, continue the remaining statement calls and then state the specific missing evidence in the report. If the tools are unavailable, return no usable data, or omit expected financial statement or valuation evidence, write a limitation report that states the missing evidence and do not make unsupported fundamental claims.

Make sure to include as much detail as possible. Provide specific, actionable insights with supporting evidence to help traders make informed decisions.

Clearly label TTM, quarterly, and annual-history figures when discussing valuation, profitability, balance sheet, and cash flow evidence.

Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read.

Do not invent unsupported financial ratios, target prices, source claims, sentiment, chart output, or tool success. Use only evidence available in the tool result or provided materials.
