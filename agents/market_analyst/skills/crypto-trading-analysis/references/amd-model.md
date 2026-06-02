# AMD / SMC Reference

Rule version: `bb-amd-skill-v1`.

AMD is a market-structure model for reading accumulation, manipulation, and distribution. It is not a prediction engine and cannot be confirmed without structured technical evidence.

## Core Ideas

- Do not guess tops or bottoms.
- Look for asymmetric confirmation instead of early certainty.
- Follow the higher timeframe first, then use the lower timeframe for trigger.
- Treat "smart money" language as a structure shorthand, not as proof of actual institutional intent.

## Required Structured Inputs

Use these fields when present:

```text
data.technical.timeframe_roles.requested
data.technical.timeframe_roles.auxiliary
data.technical.timeframes[*].patterns.amd
data.technical.timeframes[*].sample_status
data.technical.limitations
data-quality state for technical analysis
```

Screenshots, browser pages, or user chart descriptions can support low-confidence observation only.

## A Zone: Accumulation

A zone is a range candidate, not a trade signal by itself.

Required traits:

- rectangular consolidation or repeated range acceptance
- flat or nearly flat highs and lows
- contracting volume or volatility relative to the prior impulse
- visible liquidity above and below the range
- no accepted breakout from the range yet

Output language:

- "A-zone candidate" is allowed.
- "AMD confirmed" is not allowed at this stage.

Invalidation:

- clean breakout with acceptance and no reclaim
- range loses both boundary and internal structure
- insufficient OHLCV sample

## M Zone: Manipulation

M zone is the liquidity sweep or false breakout phase.

Required traits:

- false breakout above the range or false breakdown below the range
- wick, displacement, or failed acceptance outside the range
- volume or volatility expansion
- quick reclaim of the key level or range boundary
- sweep direction and reclaim direction are consistent with the later thesis

Long thesis example:

- price sweeps below range low
- quickly reclaims the range
- follow-through breaks a short-term lower-high structure

Short thesis example:

- price sweeps above range high
- quickly falls back into the range
- follow-through breaks a short-term higher-low structure

Invalidation:

- price accepts outside the swept boundary
- reclaim does not occur
- follow-through contradicts the thesis

## D Zone: Distribution Or Directional Expansion

D zone is the actual directional expansion after manipulation confirms.

Target logic:

- first measured target: `2R` to `2.5R`
- extreme trend target: `4R` to `4.5R`
- alternative target: next strong resistance/support or structured FVG area

These are measurement conventions, not promised outcomes.

Execution rules:

- entry requires M-zone confirmation plus a trigger from the requested timeframe
- stop logic should sit beyond the manipulation extreme or the structural invalidation point
- target should be expressed as a zone when price/source conflicts exist
- if `conflicts[]` affects price, do not output a single precise target

## Output Rules

Use this shape when AMD is relevant:

```text
AMD state:
- timeframe:
- phase:
- direction:
- structured evidence:
- missing evidence:
- trigger:
- invalidation:
- stop logic:
- target logic:
- confidence limit:
```

Hard limits:

- Do not call AMD confirmed from a sideways range alone.
- Do not use tutorial prices as current support, resistance, stop, or target.
- Do not use Coinglass or browser visual evidence as the AMD source of truth.
- Do not close non-technical gaps with technical pattern evidence.

## Teaching Example Boundary

Any retained tutorial price must be labeled:

```text
The following prices are teaching examples only and do not represent current market levels.
```
