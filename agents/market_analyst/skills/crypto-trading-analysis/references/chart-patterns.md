# Chart Patterns Reference

Rule version: `bb-chart-patterns-skill-v1`.

Chart patterns are structure candidates. They need source evidence, invalidation, and a trigger before becoming a trade plan.

## Evidence Boundary

- AMD/FVG/123/rejection structured conclusions must come from `data.technical.timeframes[*].patterns`.
- Other classical patterns may be discussed from structured technical output or from user/browser observation, but observed evidence must be labeled `user_observed` or `browser_observed`.
- Observed evidence cannot close `technical`, `macro`, `onchain`, `liquidation_map`, or `derivatives` gaps.
- Explain requested and auxiliary timeframes separately.

## Bottom Reversal Patterns

### W Bottom

Required traits:

- two tests of a support zone
- second test does not produce accepted breakdown
- neckline or middle high is clear
- confirmation requires neckline break and preferably retest

Invalidation:

- support zone breaks and accepts below
- neckline break fails and price returns below the second bottom

### Inverse Head And Shoulders

Required traits:

- left shoulder, lower head, right shoulder
- neckline is identifiable
- right shoulder holds above or near left shoulder support
- confirmation requires neckline break and retest when possible

Invalidation:

- right shoulder low breaks with acceptance
- neckline break fails without reclaim

### Rounded Bottom

Required traits:

- long basing process
- volatility contraction before expansion
- gradual higher lows
- volume or momentum improves into the breakout

Invalidation:

- base support breaks
- breakout lacks follow-through and returns into the lower base

## Top Reversal Patterns

### M Top

Required traits:

- two tests of resistance
- second test fails to accept higher
- neckline or middle low is clear
- confirmation requires neckline breakdown

Invalidation:

- resistance breaks and accepts above
- neckline breakdown is quickly reclaimed

### Head And Shoulders Top

Required traits:

- left shoulder, higher head, right shoulder
- neckline is identifiable
- right shoulder fails to reclaim head area
- confirmation requires neckline breakdown

Invalidation:

- right shoulder breaks above head or neckline breakdown fails

### Broadening Top

Required traits:

- expanding swings near the top
- higher highs with weak acceptance
- lower lows or widening volatility
- breakdown from lower boundary confirms risk

Invalidation:

- price accepts above the expansion structure and holds

## Continuation Patterns

### Bull Flag Or Down Channel In Uptrend

Required traits:

- prior impulse up
- corrective downward channel or flag
- declining or controlled volume during correction
- breakout above channel/flag confirms continuation candidate

Invalidation:

- correction breaks the prior impulse base or key higher-low

### Bear Flag Or Up Channel In Downtrend

Required traits:

- prior impulse down
- corrective upward channel or flag
- weak bounce relative to BTC/ETH or the market benchmark
- breakdown below channel/flag confirms continuation candidate

Invalidation:

- bounce reclaims the broken higher timeframe structure

### Wedge

Required traits:

- converging trendlines
- momentum divergence or volume contraction when available
- breakout direction must be confirmed by close/acceptance

Invalidation:

- breakout fails and price re-enters the wedge with acceptance

## Output Template

```text
Pattern:
- source type:
- timeframe:
- evidence:
- trigger:
- invalidation:
- stop logic:
- target logic:
- gaps:
```
