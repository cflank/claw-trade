## message 1: user

You are a trading assistant tasked with analyzing financial markets. Your role is to select the **most relevant indicators** for a given market condition or trading strategy from the following list. The goal is to choose up to **8 indicators** that provide complementary insights without redundancy. Categories and each category's indicators are:

Moving Averages:
- close_50_sma: 50 SMA: A medium-term trend indicator. Usage: Identify trend direction and serve as dynamic support/resistance. Tips: It lags price; combine with faster indicators for timely signals.
- close_200_sma: 200 SMA: A long-term trend benchmark. Usage: Confirm overall market trend and identify golden/death cross setups. Tips: It reacts slowly; best for strategic trend confirmation rather than frequent trading entries.
- close_10_ema: 10 EMA: A responsive short-term average. Usage: Capture quick shifts in momentum and potential entry points. Tips: Prone to noise in choppy markets; use alongside longer averages for filtering false signals.

MACD Related:
- macd: MACD: Computes momentum via differences of EMAs. Usage: Look for crossovers and divergence as signals of trend changes. Tips: Confirm with other indicators in low-volatility or sideways markets.
- macds: MACD Signal: An EMA smoothing of the MACD line. Usage: Use crossovers with the MACD line to trigger trades. Tips: Should be part of a broader strategy to avoid false positives.
- macdh: MACD Histogram: Shows the gap between the MACD line and its signal. Usage: Visualize momentum strength and spot divergence early. Tips: Can be volatile; complement with additional filters in fast-moving markets.

Momentum Indicators:
- rsi: RSI: Measures momentum to flag overbought/oversold conditions. Usage: Apply 70/30 thresholds and watch for divergence to signal reversals. Tips: In strong trends, RSI may remain extreme; always cross-check with trend analysis.

Volatility Indicators:
- boll: Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands. Usage: Acts as a dynamic benchmark for price movement. Tips: Combine with the upper and lower bands to effectively spot breakouts or reversals.
- boll_ub: Bollinger Upper Band: Typically 2 standard deviations above the middle line. Usage: Signals potential overbought conditions and breakout zones. Tips: Confirm signals with other tools; prices may ride the band in strong trends.
- boll_lb: Bollinger Lower Band: Typically 2 standard deviations below the middle line. Usage: Indicates potential oversold conditions. Tips: Use additional analysis to avoid false reversal signals.
- atr: ATR: Averages true range to measure volatility. Usage: Set stop-loss levels and adjust position sizes based on current market volatility. Tips: It's a reactive measure, so use it as part of a broader risk management strategy.

Volume-Based Indicators:
- vwma: VWMA: A moving average weighted by volume. Usage: Confirm trends by integrating price action with volume data. Tips: Watch for skewed results from volume spikes; use in combination with other volume analyses.

The instrument to analyze is `AAPL`. Use this exact ticker in the report and recommendation, preserving any exchange suffix such as `.TO`, `.L`, `.HK`, or `.T`.

For your reference, the current date is 2026-06-06. The market data window is 2025-06-06 to 2026-06-06; the data tool receives this from runtime context.

Available tool: `claw_get_market_pack`.

Before writing the market report, call `claw_get_market_pack` to retrieve price data, indicators, and chart readiness. If the tool is unavailable, returns no usable price data, or omits expected technical outputs, write a limitation report that states the missing evidence and do not make unsupported market claims.

Select indicators that provide diverse and complementary information. Avoid redundancy, for example do not select both rsi and stochrsi. Include `vwma` when evaluating volume confirmation because it is the volume-based indicator in the available indicator set. Also briefly explain why the selected indicators are suitable for the given market context.

Write a very detailed and nuanced report of the trends you observe. Provide specific, actionable insights with supporting evidence to help traders make informed decisions. Analyze observed trend direction, momentum, volatility, volume confirmation, support and resistance, and invalidation conditions.

Start directly with the market report heading or first analytical section. Do not include process preambles such as "Excellent", "Let me analyze", "I have the data", "Now I will", "Thank you", or similar chat/progress narration.

Make sure to append a Markdown table at the end of the report to organize key points in the report, organized and easy to read.

When the technical evidence supports a clear view, include an actionable BUY/HOLD/SELL technical recommendation or `FINAL TRANSACTION PROPOSAL: **BUY/HOLD/SELL**`.

Do not invent price data, indicator values, volume, charts, source claims, tool success, target prices, or unsupported recommendations. Do not use fundamental metrics as substitutes for market technical analysis.

## message 2: assistant

### tool_calls
- call 1: `claw_get_market_pack`
  args: `{}`

## message 3: tool

AAPL 的 US 行情资料包结果：部分可用。
这不是报告结论，只是资料包返回的数据、证据状态和缺口事实；缺失内容不能补写。
已返回的数据摘要：
- 日线行情：251 行，字段覆盖 开盘价, 最高价, 最低价, 收盘价, 成交量, 标的代码, 开始日期, 日期, 结束日期。
  - 时间 2026-06-01，开盘价 309.6300048828125，最高价 310.94000244140625，最低价 305.0199890136719，收盘价 306.30999755859375，成交量 48849900
  - 时间 2026-06-02，开盘价 307.4599914550781，最高价 315.45001220703125，最低价 306.69000244140625，收盘价 315.20001220703125，成交量 44534700
  - 时间 2026-06-03，开盘价 314.17999267578125，最高价 316.94000244140625，最低价 308.8500061035156，收盘价 310.260009765625，成交量 50836700
  - 时间 2026-06-04，开盘价 313.2300109863281，最高价 313.5400085449219，最低价 309.6499938964844，收盘价 311.2300109863281，成交量 44869100
  - 时间 2026-06-05，开盘价 312.8599853515625，最高价 315.1700134277344，最低价 307.1499938964844，收盘价 307.3399963378906，成交量 65246700
图表资产：已生成。
图表文件：
- /home/frank/src/claw-trade/runs/run-20260607-020702-488c55ce/calls/run-20260607-020702-488c55ce-frontline-t00-market_analyst-20260607T020716094752Z-2697c9df/pack-tool-evidence/techlab/charts-local/runs/run-20260607-020702-488c55ce/run-20260607-020702-488c55ce-frontline-t00-market_analyst-20260607T020716094752Z-2697c9df__tool-call_00_5kV4H6gBcFFEIrqVMn4P4651/charts/AAPL_market_structure.png
- /home/frank/src/claw-trade/runs/run-20260607-020702-488c55ce/calls/run-20260607-020702-488c55ce-frontline-t00-market_analyst-20260607T020716094752Z-2697c9df/pack-tool-evidence/techlab/charts-local/runs/run-20260607-020702-488c55ce/run-20260607-020702-488c55ce-frontline-t00-market_analyst-20260607T020716094752Z-2697c9df__tool-call_00_5kV4H6gBcFFEIrqVMn4P4651/charts/AAPL_indicator_panels.png
本地技术摘要：趋势 uptrend，成交量状态 expanding，支撑 305.02、290.0，压力 316.94。
本地指标快照：{"ma":{"ma5":310.068,"ma10":310.291,"ma20":304.2535},"macd":{"macd":8.4645,"signal":9.3952,"hist":-0.9308},"rsi":{"rsi14":60.7228},"boll":{"upper":318.2203,"mid":304.2535,"lower":290.2867},"atr":{"atr14":6.0682},"kdj":{"k":66.7272,"d":74.7349,"j":50.7117}}
数据缺口：
- 日线行情：未覆盖完整分析区间
- quote snapshot：仓库没有可用记录，缺少 价格、涨跌额、涨跌幅、成交量、时间戳、标的代码
- quote snapshot：未覆盖完整分析区间
审计状态：已形成 252 个标准化数据引用、2 个原始或元数据引用、2 个来源尝试记录。
没有可用数据集时，只能写数据不可用和影响范围，不得用模型常识补出真实行情、基本面、新闻或舆情事实。
