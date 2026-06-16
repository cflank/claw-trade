---
name: provider-interface-verification
description: Verify a data provider against project-required data interfaces. Use when checking whether Tushare, Coinglass, Finnhub, FRED, Glassnode, CoinGecko Pro, AkShare, mootdx, Yahoo, Binance, or any other provider covers the data the project needs; when diagnosing missing data; when validating provider API coverage before full-chain report tests; or when preventing fake coverage, narrow capability tables, bad parser mappings, parameter mistakes, or hidden whitelist behavior.
---

# Provider Interface Verification

## Core Rule

Verify from the project need outward:

`project-required data item -> official/provider interface -> planned call -> live response -> normalized project fields`.

Do not start from a provider's broad marketing capability. Do not run full-chain reports until the provider interface layer is green.

## Workflow

1. List project-required interfaces for the target market.
   - Use the project's public data contracts, worker/tool needs, and report evidence requirements.
   - Record the exact project item, dataset, granularity, and required fields.
   - Do not invent needs because a provider happens to expose an endpoint.

2. Build a provider coverage table.
   - For each project item, find matching provider endpoints from official docs or an official/project-captured API catalog.
   - Mark each row as `implemented` only when an endpoint is bound, plannable, and has a parser/output contract.
   - Mark `not_implemented` only after checking the local catalog and official/current public docs.
   - If a provider has a related endpoint but it does not satisfy the project contract, record the mismatch plainly.

3. Run focused live probes for every implemented endpoint.
   - Call each covered provider endpoint directly through the data layer.
   - Do not use full-chain reports as the first validation loop.
   - Check status, row count, HTTP/provider error, planner output, normalized fields, and declared field coverage.
   - Treat `success` with missing declared fields as failure, e.g. `schema_mismatch`.

4. Diagnose failures by root cause.
   - Parameter error: fix planner parameter mapping.
   - Provider business error: record permission, rate limit, bad request, or unsupported parameter exactly.
   - Empty result: inspect request, symbol/date/granularity, and raw payload before blaming provider capability.
   - Parser gap: add aliases or a specific parser for the real payload shape.
   - False capability: delete the output binding or field declaration.
   - Multi-purpose endpoint mismatch: select output contract by the requested project API, not by the first catalog output.
   - Cross-asset pollution: filter returned multi-symbol rows to the requested instrument.

5. Repeat until the provider table has only accepted states.
   - Accepted live states: `success`, `not_implemented`.
   - Rejected final states: `error`, `empty`, `plan_failed`, `schema_mismatch`, missing declared fields.

6. Only then run broader workflow tests when requested.
   - Interface success proves provider calls and parsers work.
   - It does not prove workers used the data or that final reports are complete.
   - Full-chain tests must separately inspect worker data usage and final report missing-data text.

## Evidence Requirements

Always produce or update two artifacts when feasible:

- Catalog coverage: project item -> provider endpoint -> implemented/not_implemented -> declared fields.
- Live probe: project item -> provider endpoint -> status -> rows -> missing declared fields -> error.

Final summary must state:

- Total project-required interfaces checked.
- Implemented endpoint count.
- Live success count.
- Explicit `not_implemented` count and why.
- Any remaining defect or unverified area.
- Exact evidence paths and focused test commands.

## Hard Bans

- Do not claim coverage because an endpoint name looks close.
- Do not keep false fields in the catalog to satisfy an upper layer.
- Do not hide provider errors behind fallback success.
- Do not call a related endpoint implemented if it cannot produce the project-required contract.
- Do not treat one live success as proof that every endpoint in that provider works.
- Do not run repeated full-chain tests while provider-interface failures are still unresolved.
