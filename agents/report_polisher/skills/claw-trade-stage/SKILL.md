---
name: claw-trade-stage
description: Stage contract for `report_polisher` in the claw-trade report path.
---

# claw-trade stage contract

Follow `STAGES.yaml` for the current worker, profile, and stage.

- Use only the approved report text supplied in this turn.
- Write the canonical output named `reader_final_report`.
- Preserve the portfolio manager's final investment conclusion, recommendation direction, execution conditions, and risk conditions.
- Improve readability, structure, language quality, and report presentation only.
- Do not add unsupported investment claims.
- Do not call tools through Python or assume missing evidence has already been fetched.
- CRYPTO is approved. Preserve crypto-specific evidence boundaries: do not invent market
  structure, tokenomics, news, social sentiment, on-chain data, or chart conclusions.
