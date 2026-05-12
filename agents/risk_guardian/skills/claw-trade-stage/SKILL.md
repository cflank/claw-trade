---
name: claw-trade-stage
description: Stage contract for `risk_guardian` in the claw-trade OpenClaw report path.
---

# claw-trade stage contract

Follow `STAGES.yaml` for the current worker/profile/stage.

- Use only approved upstream report material supplied by claw-trade; refs and capabilities are for traceability and deeper evidence reads.
- Write the canonical artifact named `risk_guardian_review`.
- Do not call tools through Python or assume Python already fetched evidence.
- Do not emit unsupported investment claims.
- If the current profile is HK or CRYPTO, fail explicitly until that profile strategy is approved.
