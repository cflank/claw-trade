---
name: claw-trade-stage
description: Stage contract for `risk_challenger` in the claw-trade OpenClaw report path.
---

# claw-trade stage contract

Follow `STAGES.yaml` for the current worker/profile/stage.

- Read only approved artifact refs supplied by claw-trade.
- Write the canonical artifact named `risk_challenge`.
- Do not call tools through Python or assume Python already fetched evidence.
- Do not emit unsupported investment claims.
- If the current profile is HK or CRYPTO, fail explicitly until that profile strategy is approved.
