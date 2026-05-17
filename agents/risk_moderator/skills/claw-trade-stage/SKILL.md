---
name: claw-trade-stage
description: Stage contract for `risk_moderator` in the claw-trade OpenClaw report path.
---

# claw-trade stage contract

Follow `STAGES.yaml` for the current worker/profile/stage.

- Use only approved upstream report material supplied by claw-trade; refs and capabilities are for traceability and deeper evidence reads.
- Write the canonical artifact named `risk_moderation`.
- Do not call tools through Python or assume Python already fetched evidence.
- Do not emit unsupported investment claims.
- If the current profile is CRYPTO, use the approved CRYPTO prompt only when STAGES.yaml approves that profile; do not fallback to US, CN_A, or HK.
