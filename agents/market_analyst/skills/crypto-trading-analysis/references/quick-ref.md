# Crypto Trading Quick Reference

Use this file only as a compact checklist. For detailed rules, read the specific reference file named by `SKILL.md`.

## Priority Stack

```text
market/macro context -> on-chain and derivatives -> multi-timeframe technicals -> entry trigger -> risk check
```

## Data First

- Start with the visible market data pack; it returns compact natural-language market material for the worker.
- Preserve status, data time, sources, confidence, data-quality state, warnings, open data gaps, conflicts, and next actions.
- Do not re-query domains that are already usable unless stale, incomplete, conflicting, or explicitly requested.
- Browser or user-observed chart evidence stays low confidence and cannot close structured gaps.
- Every analysis must include `指标覆盖`: account for Bollinger, Vegas, double-line reversal, AMD, 123, FVG, OB, RSI, MACD, KD, TD 9/13, harmonic, volume profile, liquidation map, CVD, funding, OI/long-short, macro, on-chain, and AHR999 as `已引用`, `可分析`, `样本限制`, `缺失/不可用`, or `不适用`. Liquidation map is BTC-only; for non-BTC assets mark it `不适用`.
- Every full analysis must include `指标推导过程`: for each material signal family, write `数据 -> 推导 -> 交易作用 -> 失效`. A coverage matrix plus one-line conclusions is incomplete.
- If a field exists but is neutral, low confidence, or conflicting, explain why it does not become a trade signal.
- If `data.technical.tutorial_coverage` or an expected V2 field is missing, state the missing field path and lower confidence.

## AMD In One Pass

- A: accumulation range, flat highs/lows, shrinking volume, stops above and below the box.
- M: manipulation through false breakout, false breakdown, wick, volume expansion, and quick reclaim.
- D: distribution or directional expansion after manipulation confirms direction.
- No M confirmation means "observe", not "AMD confirmed".

## 123 Rule In One Pass

1. Break trendline, neckline, or key support/resistance with volume or structural commitment.
2. Retest without making a new invalidating extreme.
3. Enter only after price breaks the prior confirmation structure again.

Do not chase the first breakout candle.

## Rejection Signal

A rejection candidate needs:

- key level interaction
- long wick or failed acceptance
- volume or volatility expansion
- quick close back inside the prior structure

It is invalid if price accepts beyond the swept level.

## Vegas Channel

- Blue band: EMA 144/169.
- Purple band: EMA 576/676.
- Above blue band: bullish structure has support context.
- Below blue band: bearish structure has resistance context.
- Purple crossing below blue is a major bearish warning, but not a standalone entry.

## FVG And OB

- FVG is an inefficient move. In claw-trade CRYPTO reports, record its location and sample limits; do not call it a magnet or target unless upstream evidence provides statistics or execution-mechanism proof.
- OB is the last opposing candle area before displacement.
- FVG/OB are support/resistance candidates only; neither is a complete trade reason alone.

## Liquidation And Derivatives

- Missing `liquidation_map`: do not output liquidation-hunt targets. For non-BTC assets, mark liquidation map as BTC-only and not applicable instead of treating it as a missing data gap.
- Missing derivatives: do not conclude from funding, OI, CVD, or long/short ratio.
- Funding, OI, CVD, and long/short ratio describe crowding and pressure; none decides direction alone.

## Macro And On-Chain

- FOMC, CPI, nonfarm payrolls, and rate/yield shocks can invalidate short-term technical triggers.
- Institutional product and stablecoin flows indicate marginal demand/liquidity, not guaranteed direction.
- AHR999 is long-term valuation context only, not a short-term chase signal.

## Risk Lines

- Big timeframe decides direction; small timeframe supplies trigger.
- Do not short a strong asset by default when weak alternatives fit the short thesis better.
- At `1R`, a plan may take partial profit and move the remainder to breakeven if that was part of the plan.
- Do not widen stop-loss to avoid realizing loss.
- Do not convert a failed short-term trade into long-term belief.
