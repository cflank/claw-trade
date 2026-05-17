# Indicators Reference

Rule version: `bb-indicators-skill-v1`.

Indicators provide context, confirmation, or risk filters. They do not replace structure, readiness, invalidation, or stop logic.

## Required Inputs

Prefer structured values from:

```text
data.technical.timeframes[*].indicators
data.technical.timeframes[*].vegas
data.technical.timeframes[*].patterns.fvg
data.technical.timeframes[*].sample_status
data.technical.rule_versions
data.technical.limitations
```

If an indicator is missing or sample history is insufficient, say what cannot be concluded.

## Vegas Channel

Parameters:

- blue band: EMA 144 and EMA 169
- purple band: EMA 576 and EMA 676

Interpretation:

- price above blue band: bullish structure has dynamic support context
- price below blue band: bearish structure has dynamic resistance context
- price inside blue band: trend context is mixed
- distance to blue/purple bands helps judge chase risk
- purple band crossing below blue band is a major bearish warning

Limits:

- Vegas is not a standalone entry.
- Weekly Vegas needs enough weekly candles; otherwise mark the sample limitation.

## MACD

Use:

- above zero line: bullish momentum context
- below zero line: bearish momentum context
- golden cross: momentum improvement
- death cross: momentum deterioration
- histogram expansion: impulse strengthening
- histogram contraction: impulse weakening
- top divergence: price higher high with weaker MACD
- bottom divergence: price lower low with stronger MACD

Limits:

- MACD divergence needs price structure confirmation.
- Do not use a single cross against higher timeframe structure as a complete plan.

## RSI

Use:

- overbought: upside chase risk, not automatic short
- oversold: downside chase risk, not automatic long
- top divergence: price higher high with weaker RSI
- bottom divergence: price lower low with stronger RSI
- persistent overbought/oversold can be trend strength, not reversal by itself

## KD

Use:

- overbought/oversold as short-term momentum context
- flattening at extremes as possible exhaustion
- divergence as a warning, not a trigger alone

Limits:

- KD is noisy on low timeframes.
- Confirm with structure and readiness before using it in a plan.

## FVG

Meaning:

- fast displacement can leave an inefficient price area
- open FVG can become a retest, magnet, or target area
- nearest above/below FVG is a candidate zone, not a guaranteed destination

Required output:

- direction
- timeframe
- nearest above/below
- open count or candidate count if available
- whether current price is approaching, entering, or leaving the zone

Limits:

- FVG must come from structured technical output for high-confidence use.
- User/browser observation of FVG stays low confidence.

## OB

Meaning:

- order-block-style zone is the opposing candle or area before displacement
- it is a support/resistance candidate

Limits:

- OB does not create a trade alone.
- It must be combined with structure, trigger, invalidation, and risk.

## Output Template

```text
Indicator context:
- timeframe:
- Vegas:
- MACD:
- RSI/KD:
- FVG/OB:
- agreement:
- conflict:
- missing data:
- how it affects the plan:
```
