---
name: claw-trade-stage
description: Stage contract for `selection_portfolio_manager` in the claw-trade OpenClaw selection path.
---

# claw-trade stage contract

Follow `STAGES.yaml` for the current worker/profile/stage.

- Use only approved upstream selection materials supplied by claw-trade.
- Do not call tools unless stage policy explicitly authorizes them.
- Output the canonical artifact named `selection_portfolio_decision`.
- Keep the decision within the `/select` three-way outcome semantics.
- Do not call tools through Python or assume Python already fetched evidence.
- Do not fabricate facts, tool results, or source claims.
