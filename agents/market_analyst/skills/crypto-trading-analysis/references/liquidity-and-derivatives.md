# Liquidity And Derivatives Reference

Rule version: `bb-liquidity-derivatives-skill-v1`.

Liquidation and derivatives data describe current levels, units, crowding readings, and sample limits. They do not prove direction, targets, or execution paths unless upstream evidence provides statistics or execution-mechanism proof.
Liquidation map is BTC-only in the current claw-trade CRYPTO profile. For non-BTC assets, mark liquidation map as not applicable instead of treating it as a data gap.

## Required Inputs

Use structured fields from:

```text
data.derivatives
data.liquidation_map
data-quality state for derivatives
data-quality state for liquidation map
open data gaps
conflicts[]
sources[]
as_of
```

If a domain is not ready, state the impact before using any derivative conclusion.

## Liquidation Map

Use:

- above-price and below-price liquidation clusters are recorded levels, sizes, units, and sample limits
- cluster size alone must not be converted into a target, support/resistance, squeeze path, or reversal story
- if upstream evidence provides statistics or execution-mechanism proof, describe that proof before discussing any trading effect
- without that proof, use liquidation clusters only as monitored reference levels

Hard rule:

- If `liquidation_map` is missing, partial, or lacks statistical/execution-mechanism proof, do not output liquidation-hunt targets.

If liquidation cluster levels conflict by more than 2%, do not output a single precise level.

## CVD

Use:

- sideways price with falling CVD: active selling or short pressure
- sideways price with rising CVD: active buying pressure
- CVD divergence can signal hidden accumulation or distribution

Limits:

- CVD needs exchange/source context.
- CVD does not decide direction alone.

## Funding

Use:

- negative funding can indicate short crowding
- positive funding can indicate long crowding
- extreme funding can be reported as a reading only; squeeze risk needs upstream statistical or execution-mechanism proof

Limits:

- negative funding does not mean price must rise
- positive funding does not mean price must fall
- combine funding with OI, price structure, and liquidation map

## Open Interest

Use:

- price up plus OI up: trend participation or leveraged build-up
- price down plus OI up: short participation or crowded chasing
- violent price move plus OI down: deleveraging
- price range plus OI up: compression before expansion, direction unresolved

Limits:

- OI without price structure is not a plan.
- OI/funding conflicts above 10% across sources must lower confidence.

## Long/Short Ratio

Use:

- crowding reference
- contrarian risk when ratio is extreme and structure is vulnerable

Limits:

- not a standalone direction signal
- source and exchange coverage matter

## Missing Data Behavior

- Missing `derivatives`: do not conclude from funding, OI, CVD, or long/short ratio.
- Missing `liquidation_map`: do not output liquidation cluster targets. For non-BTC assets, write BTC-only/not applicable rather than a missing-data conclusion.
- `partial` derivatives: use as a risk filter only.
- Browser-observed heatmap: label `browser_observed`; it cannot close `liquidation_map` gaps.

## Output Template

```text
Liquidity/derivatives:
- sources and as_of:
- liquidation clusters:
- funding:
- OI:
- CVD:
- long/short:
- crowding read:
- missing domains:
- conflicts:
- effect on long/short plan:
```
