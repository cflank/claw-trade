# 123 Breakout Reference

Rule version: `bb-rule-123-skill-v1`.

The 123 rule is a right-side confirmation method. It is designed to avoid chasing the first breakout and to force the plan to define invalidation.

## Required Structured Inputs

Prefer:

```text
data.technical.timeframes[*].patterns.rule_123
data.technical.timeframes[*].sample_status
data.technical.timeframe_roles.requested
data.technical.timeframe_roles.auxiliary
data.technical.limitations
```

If structured `rule_123` is missing, state the data gap. User charts may be described as `user_observed`, not as structured confirmation.

## Bullish 123

Step 1: Break

- price breaks a downtrend line, neckline, or key resistance
- close or structural acceptance is stronger than a wick-only probe
- volume or volatility supports the break when available

Step 2: Retest

- price retests the broken area or a nearby higher-low zone
- retest does not make a new invalidating low
- retest should not erase the whole breakout impulse

Step 3: Confirmation

- price breaks the prior short-term high after retest
- entry is allowed only after this confirmation, not on the first breakout candle

Stop logic:

- below the retest low, or below the structural invalidation point if tighter

## Bearish 123

Step 1: Break

- price breaks an uptrend line, neckline, or key support
- close or structural acceptance is stronger than a wick-only probe
- volume or volatility supports the break when available

Step 2: Retest

- price retests the broken support or a nearby lower-high zone
- retest does not make a new invalidating high
- retest should not fully reclaim the broken structure

Step 3: Confirmation

- price breaks the prior short-term low after retest
- entry is allowed only after this confirmation, not on the first breakdown candle

Stop logic:

- above the retest high, or above the structural invalidation point if tighter

## State Mapping

Use the analyzer state when present:

| state | output meaning |
|---|---|
| `none` | no valid 123 structure |
| `forming_step_1` | break observed; retest not confirmed |
| `forming_step_2` | retest candidate exists; final confirmation missing |
| `confirmed` | Step 3 complete |
| `invalidated` | structure failed |

## Output Template

```text
123 state:
- direction:
- timeframe:
- Step 1 evidence:
- Step 2 evidence:
- Step 3 trigger:
- invalidation:
- stop logic:
- target logic:
- missing data:
```

## Hard Limits

- Do not chase the first breakout candle.
- Do not treat a single wick as confirmation.
- Do not output a 123 trade plan if the retest level, invalidation point, and stop logic are absent.
- If current price or retest level is conflicted, use a trigger zone and lower confidence.
