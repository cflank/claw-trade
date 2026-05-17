# Risk And Position Sizing Reference

Rule version: `bb-risk-position-skill-v1`.

Risk rules turn a market view into a bounded plan. They do not provide financial advice or account management.

## Big Direction, Small Trigger

Rules:

- Higher timeframe decides directional bias.
- Lower timeframe supplies entry trigger.
- In a higher-timeframe uptrend, do not chase lower-timeframe shorts unless the plan is explicitly countertrend and risk is tight.
- In a higher-timeframe downtrend, do not chase lower-timeframe longs unless the plan is explicitly countertrend and risk is tight.
- If higher and lower timeframes conflict, prefer observation or wait for alignment.

## Soft Target Shorting

Use this when the user asks for weak altcoin short candidates.

Candidate traits:

- market benchmark is pulling back
- asset is weaker than BTC/ETH
- trendline or key support is broken
- bounce is weak or rejected at resistance
- upcoming unlock, weak narrative, exploit, governance risk, or event overhang exists
- derivatives show crowding or fragility when available

Hard limits:

- Do not default to shorting strong BTC, ETH, SOL, or a relative leader when weaker candidates are the stated objective.
- If candidate batch data is unavailable, output screening criteria and data gaps, not a definitive ranked list.

## Spot Scaling

Golden-pocket style scaling may use:

- `0.382`
- `0.5`
- `0.618`

Rules:

- Use only with higher timeframe structure and a planned capital allocation.
- It is a spot or low-leverage planning concept, not permission to average down endlessly.
- If structure breaks, stop the plan instead of adding blindly.

## Contract Discipline

Rules:

- Define invalidation before entry.
- Stop-loss must sit at a structural invalidation point, not a pain threshold.
- At `1R`, a plan may take partial profit and move the remaining position to breakeven if that discipline was part of the plan.
- Do not widen stop-loss after entry to avoid loss.
- Do not convert a failed short-term contract trade into long-term belief.

## Leverage

Rules:

- Do not recommend a specific leverage multiple unless the user asks for a risk-budget example.
- Express sizing through risk budget when possible, such as "risk X% if stop is hit".
- Warn that leverage amplifies losses and liquidation risk.

## Low Liquidity Windows

Watch:

- weekends
- holidays
- thin Asian/US handoff periods when volume is low
- major macro release windows

Rules:

- Lower exposure or stand aside when liquidity is thin and the plan needs precise execution.
- High leverage should be reduced or avoided in these windows.

## Precheck Template

```text
Plan:
- asset:
- direction:
- timeframe:
- entry trigger:
- invalidation:
- stop logic:
- target logic:
- R multiple:
- higher timeframe support:
- lower timeframe trigger:
- macro/event risk:
- derivatives/liquidation risk:
- data gaps:
- executable: yes/no/conditional
```

## Prohibited Output

- Do not tell the user they must open a position.
- Do not promise a return.
- Do not provide account management, exchange execution, wallet signing, swap, transfer, or order logic.
