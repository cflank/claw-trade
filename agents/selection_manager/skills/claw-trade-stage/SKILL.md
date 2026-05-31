---
name: claw-trade-stage
description: Stage contract for `selection_manager` in the claw-trade OpenClaw selection path.
---

# claw-trade stage contract

Follow `STAGES.yaml` for the current worker/profile/stage.

- Use only approved upstream selection review materials supplied by claw-trade.
- Do not call tools unless stage policy explicitly authorizes them.
- Output the canonical artifact named `selection_ranked_watchlist`.
- Do not call tools through Python or assume Python already fetched evidence.
- Do not fabricate facts, tool results, or source claims.
