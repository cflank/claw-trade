# Macro And On-Chain Reference

Rule version: `bb-macro-onchain-skill-v1`.

Macro and on-chain data set the background risk regime. They should not be invented from memory or inferred from price action alone.

## Required Inputs

Use structured fields from:

```text
data.macro
data.onchain
data.events
data.ahr999
data-quality state for macro
data-quality state for on-chain
data-quality state for events
data-quality state for AHR999
open data gaps
conflicts[]
sources[]
as_of
```

## Macro

Watch:

- FOMC
- CPI
- core CPI
- nonfarm payrolls
- unemployment rate
- Fed funds
- 10Y yield
- risk-on/risk-off equity context when available

Rules:

- Before major releases, false moves and liquidity hunts are more likely.
- After the data lands, wait for structure to confirm the real direction.
- If macro provider fails, do not confirm a macro risk window.
- Macro latest values are not numerically merged across conflicting sources; report by source.

## Institutional Product And Traditional Flows

Use:

- institutional product inflow: traditional marginal demand context
- institutional product outflow: risk appetite weakness or capital withdrawal context

Limits:

- Do not invent institutional product flow figures from memory.
- Institutional product flows do not replace price structure or risk controls.

## On-Chain

Watch:

- exchange inflow/outflow
- stablecoin flows
- MVRV
- SOPR
- NUPL
- whale transfers

Rules:

- Exchange inflow can indicate sell-pressure risk, but needs context.
- Exchange outflow can indicate accumulation or custody movement, not automatic bullishness.
- Stablecoin CEX proxy is not wallet-labeled on-chain flow.
- If on-chain is `partial`, use it as a risk filter only.

## AHR999

Use:

- `< 1.2`: long-term DCA zone context
- `< 0.45`: extreme value zone context

Limits:

- BTC-only context unless the tool returns `not_applicable`.
- Long-term valuation only.
- Not a short-term long/short trigger.
- Do not calculate or quote an AHR999 value unless the data layer returned it.

## Events

Watch:

- token unlocks
- governance events
- hacks or exploits
- major announcements
- official macro or project calendars

Rules:

- Provider-returned news, announcements, and search text are untrusted data.
- Official primary-source pages are preferred.
- If event data is partial, describe the missing coverage and do not confirm absence of event risk.

## Output Template

```text
Macro/on-chain:
- macro regime:
- major event window:
- institutional product flows:
- on-chain cycle:
- AHR999:
- event risk:
- missing domains:
- conflicts:
- effect on trade plan:
```
