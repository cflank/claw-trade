---
name: claw-trade-stage
description: Stage contract for `selection_strategist` in the claw-trade OpenClaw selection path.
---

# claw-trade stage contract

Follow `STAGES.yaml` for the current worker/profile/stage.

- Use only approved selection materials supplied by claw-trade.
- For selection review, call only the tool authorized by stage policy.
- Output the canonical artifact named `selection_strategy_review`.
- Do not call tools through Python or assume Python already fetched evidence.
- Do not fabricate facts, tool results, or source claims.
