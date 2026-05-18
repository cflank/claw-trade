---
name: crypto-trading-analysis
description: Use only for claw-trade CRYPTO profile market analysis. Crypto trading analysis workflow for BTC, ETH, and major altcoins using BB structured market data, AMD/SMC, 123 reversal rules, chart patterns, Vegas/FVG/OB, RSI/MACD/KD, derivatives, liquidation maps, on-chain and macro risk checks. Do not use for US, CN_A, or HK equity profiles.
---

# Crypto Trading Analysis

Use this skill to produce Chinese, evidence-grounded crypto trade analysis. BB is an analysis system only: no order placement, wallet signing, asset transfer, account management, guaranteed returns, or certainty-language trading calls.

## Core Standard

The default output must be a process-first trade analysis, not a checklist.

For every reader-facing market analysis:

- Explain the conclusion in plain Chinese first.
- Preserve the data path: source, timestamp, readiness, warnings, gaps, and conflicts.
- For each material indicator, write the reasoning chain: `数据 -> 推导 -> 交易作用 -> 失效`.
- Do not compress indicators into one-line labels such as "偏多" or "偏空" unless the full reasoning is already shown.
- Treat `指标覆盖` as proof that data was checked, not as the analysis itself.
- Translate tutorial signals into trade logic: support, resistance, trigger, target, invalidation, risk filter, or no-trade filter.
- If a signal is neutral, low confidence, conflicting, or missing, explain why it does not support a trade.

Failure mode to avoid: outputting a neat matrix and a short conclusion while leaving the user unable to audit how OB, POC, TD, FVG, AMD/SMC, 123, Vegas, Bollinger, RSI, MACD, KD, derivatives, liquidation, macro, on-chain, and AHR999 changed the decision.

## First Step

Infer from the user request, or ask only when the answer changes the analysis:

- asset: one uppercase symbol such as `BTC`, `ETH`, or `SOL`
- timeframe: `long-term`, `swing`, `intraday`, or `scalp`
- direction: `long`, `short`, or `neutral`
- account/risk constraint: spot, contract, leverage context, or max loss if supplied
- available user evidence: screenshot, browser page, chart note, price level, or existing plan

Do not use model memory as current market data.

## Data Routing

Call OpenClaw visible tool `claw_get_market_pack` first. This pack centrally reads OpenBB-routed market structure, derivatives, liquidation, on-chain, macro, events, and AHR999 when those sources are configured, then adds local OHLCV-derived indicators and PNG chart assets.

The worker should use the pack's natural-language `reader_brief`, compact data summary, `provider_attempts`, `data_gaps`, `conflicts`, and `readiness`. Do not use raw BB JSON as the report body; raw provider payload is evidence storage only.

The data tool call does not need model-supplied ticker, market, company_name, or date fields; those values are locked by runtime context.

Rules:

- `ticker` must be a single symbol; do not mix exchange names, narratives, or pair suffixes into it.
- Use `direction=long` for long-plan checks, `short` for short-plan checks, and `neutral` for general analysis.
- Use only canonical domains: `market`, `technical`, `derivatives`, `liquidation_map`, `onchain`, `macro`, `events`, `ahr999`, `browser_evidence`.
- Add `browser_evidence` only when structured data is missing and the user permits browser assistance, or when the user explicitly supplies a screenshot/page.
- Fixture profiles such as `no-key`, `partial-key`, and `full-key` are for local verification; never present fixture data as live market evidence.

## BB/CoinGlass Unavailable Path

If `claw_get_market_pack` reports that the CRYPTO market route is unavailable:

1. Check whether the BB MCP is not built, not registered, or Codex has not been restarted.
2. If `mcp/crypto-data-mcp/dist/server.js` is missing, report `BB_MCP_NOT_BUILT` with the build/register commands from the repo docs.
3. If the MCP exists but cannot be called by the pack, report `BB_MCP_UNAVAILABLE` and ask for provider health or install verification.
4. Use direct provider MCPs only when they are explicitly configured and callable; label that route as degraded.
5. If BB MCP, direct provider MCPs, and browser assistance are all unavailable, ask for user-provided chart/context and output a data gap report.

Never hide this route change. State which source path was used and which `data_gaps[]` remain open.

## No Duplicate Ready-Domain Queries

Read the full envelope first. Do not re-query ready domains in the same asset/time window unless one condition is true:

- the domain is missing from `data`
- `readiness.domains[domain].state` is not `ready`
- `as_of` is older than the domain freshness window
- related fields are present in `conflicts[]`
- the user explicitly asks for a narrower recheck

Freshness guide:

| domain | freshness |
|---|---:|
| `market` | 15-60 seconds |
| `technical` | 60-300 seconds, adjusted by timeframe |
| `derivatives` | 60-180 seconds |
| `liquidation_map` | 60-300 seconds |
| `events` | ETF 1-6 hours; token unlocks 6-24 hours; news 5-30 minutes |
| `macro` | 6-24 hours; recheck around major releases |
| `onchain` | 1-6 hours unless provider says otherwise |
| `ahr999` | 1-24 hours; never a short-term entry trigger |

## Conflict Rules

Do not silently merge conflicting provider values.

| field type | material conflict threshold |
|---|---:|
| spot price | 1% |
| market cap | 5% |
| 24h volume | 10% |
| OI/funding/long-short | 10% |
| liquidation cluster level | 2% |
| macro latest value | no numeric merge; report by source |
| event/unlock date | 1 calendar day |

If conflict is below threshold, a primary value may be used while recording alternatives in `conflicts[]`. If conflict exceeds threshold, do not output a single precise entry, stop, or target price; use ranges or mark the field unresolved and lower confidence.

## Technical Pattern Boundary

For AMD/FVG/123/rejection conclusions, inspect:

```text
data.technical.timeframe_roles.requested
data.technical.timeframe_roles.auxiliary
data.technical.timeframes[*].patterns
data.technical.timeframes[*].sample_status
data.technical.limitations
```

Hard rules:

- AMD/FVG/123/rejection structured conclusions must come from `data.technical.timeframes[*].patterns`.
- Coinglass, screenshots, browser pages, and user chart descriptions cannot become structured pattern truth.
- Browser observations must be labeled `browser_observed`; user chart descriptions must be labeled `user_observed`.
- Observed evidence cannot close `technical`, `macro`, `onchain`, `liquidation_map`, or `derivatives` gaps.
- If pattern samples are insufficient, output limited observations and next data actions, not a high-confidence pattern call.

## Process-First Indicator Analysis

For every full reader-facing market analysis, after the data-status section and before scenarios, include a section named `指标推导过程`.

For each subsection below, write four explicit lines:

```text
数据:
推导:
交易作用:
失效:
```

Required subsections:

- `OB 订单块`
- `POC / 成交量分布`
- `TD Sequential`
- `谐波形态`
- `FVG`
- `AMD/SMC 与 123`
- `Vegas / 布林带 / RSI / MACD / KD`
- `清算地图 / CVD / 资金费率 / OI / 多空比`
- `宏观 / 链上 / AHR999 / 事件`

Depth requirements:

- `数据` must cite actual values, levels, counts, states, or missing field paths from the envelope.
- `推导` must explain why the data is bullish, bearish, neutral, conflicting, or not actionable.
- `交易作用` must map the signal to support, resistance, trigger, target, invalidation, risk filter, or no-trade filter.
- `失效` must give a price level, structure break, sample limitation, provider limitation, or missing-domain reason.
- If the signal is low confidence, explain the confidence cap instead of omitting it.
- If timeframes conflict, name the conflicting timeframes and do not treat the lower timeframe as standalone confirmation.

This section is the primary analysis body. The later scenario section must reuse these derived triggers and invalidation levels rather than inventing new ones.

## Mandatory Tutorial Coverage Matrix

For every reader-facing market analysis, include a short `指标覆盖` section before the scenario analysis. This is mandatory even when the conclusion is obvious.

Inspect these structured paths first:

```text
data.technical.tutorial_coverage
data.technical.tutorial_signal_summary
data.technical.timeframes[*].indicators
data.technical.timeframes[*].vegas
data.technical.timeframes[*].patterns
data.derivatives
data.liquidation_map
data.macro
data.onchain
data.events
data.ahr999
readiness.domains
data_gaps[]
```

The coverage section must account for each item below with one of `已引用`, `有数据但未构成信号`, `缺失/不可用`, or `不适用`:

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

Rules:

- If a field exists but is neutral or low confidence, say `有数据但未构成信号` instead of omitting it.
- If `tutorial_coverage` is missing from the envelope, say `coverage matrix missing from MCP output` and lower output confidence one level.
- If an expected V2 field is missing from `technical.indicators` or `technical.patterns`, do not infer it from prose or tutorial text.
- Do not repeat ready-domain tool calls only to fill prose. Use the first envelope and report the missing field path.
- Coverage is not enough by itself. The `指标推导过程` section must explain how covered tutorial fields affect the trade decision.

## Tutorial Signal Expansion Source

Use `data.technical.tutorial_signal_summary` first when present. Fall back to `data.technical.timeframes[*].indicators`, `data.technical.timeframes[*].vegas`, and `data.technical.timeframes[*].patterns` only when the summary field is missing or incomplete.

Hard rules:

- Do not compress OB, POC, TD Sequential, harmonic, or FVG into one-line coverage notes.
- If harmonic has zero candidates, explicitly say `当前没有有效谐波形态`; do not force a Gartley/Bat/Butterfly/Crab label.
- If OB has both bullish and bearish zones near price, describe the overlap and explain why it is a decision zone rather than a clean trend signal.
- If POC or value area is available, identify POC, value-area high, value-area low, and how price location changes the scenario.
- If TD signals conflict across timeframes, state the conflict and do not treat the lower-timeframe signal as standalone confirmation.
- If an item is `有数据但未构成信号`, still explain why it does not affect the plan.
- A full analysis that only lists the coverage matrix but does not expand these tutorial signals into `数据 -> 推导 -> 交易作用 -> 失效` is incomplete.

## Readiness Usage

This is a worker-facing data-quality rule, not a claw-trade runtime guard or hard stop.

Treat `readiness` as the structured data-quality boundary.

- `ready`: full analysis is allowed if no material conflicts block the conclusion.
- `partial`: give scenarios and conditions, with explicit gaps and confidence limits.
- `limited` or `not_reliable`: do not output a full trade plan; output only available observations, missing domains, invalid conclusions, and next actions.
- `status=error`: output failure reason, data gaps, and next actions only.

## When To Read References

- Short checklist: `references/quick-ref.md`
- AMD/SMC: `references/amd-model.md`
- 123 reversal: `references/123-breakout.md`
- W/M/top/bottom/channel/wedge patterns: `references/chart-patterns.md`
- Vegas/MACD/RSI/KD/FVG/OB: `references/indicators.md`
- Liquidation/CVD/funding/OI: `references/liquidity-and-derivatives.md`
- Macro/on-chain/ETF/AHR999: `references/macro-and-onchain.md`
- Position sizing/risk: `references/risk-and-position-sizing.md`
- Output templates: `references/output-templates.md`
- Data boundaries: `references/source-boundaries.md`

Read only the reference files needed for the current task.

## Output Requirements

Every reader-facing analysis should include:

- data time, sources, status, confidence, and readiness
- a short verdict and why it could be wrong
- mandatory `指标覆盖` matrix for the tutorial checklist
- mandatory `指标推导过程` with `数据 -> 推导 -> 交易作用 -> 失效` for each required signal family
- warnings, data gaps, conflicts, and next actions when present
- main conclusion and what invalidates it
- bullish scenario, bearish scenario, and stand-aside scenario
- entry trigger or observation trigger, not a direct buy/sell instruction
- stop-loss logic or structural invalidation logic
- target logic, if data supports it
- reverse-case explanation
- risk notes

For "can I long/short" questions, answer one of:

- can consider only if specific trigger conditions occur
- not recommended now, with evidence
- data is insufficient, with missing domains and next actions

Do not answer with certainty-language or compulsory trading calls.

## Limit Conditions

Stop or limit the answer if:

- the user asks for a guaranteed or compulsory buy/sell instruction
- the user asks to use screenshots or browser observation to close structured data gaps
- samples are insufficient but the user asks for high-confidence AMD/FVG/123/rejection conclusions
- provider failure, rate limit, missing key, or field drift makes structured input unreliable
- rule semantics are ambiguous enough to change analyzer thresholds or state definitions
