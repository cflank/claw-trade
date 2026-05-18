---
name: claw-trade-stage
description: Stage contract for `market_analyst` in the claw-trade OpenClaw report path.
---

# claw-trade stage contract

Follow `STAGES.yaml` for the current worker/profile/stage.

- Use only approved upstream report material supplied by claw-trade; refs and capabilities are for traceability and deeper evidence reads.
- Output the final reader-facing report body for `market_analysis_report`.
- Even when market data is empty or tool calls fail, write a limitation-only `market_analysis_report` with root cause and missing evidence.
- The report body must not include tool logs, JSON, URIs, or machine-readable fields.
- Do not call tools through Python or assume Python already fetched evidence.
- Do not emit unsupported investment claims.
- If the current profile is CRYPTO, use the approved CRYPTO prompt plus `crypto-trading-analysis` and the `claw_get_market_pack` OpenBB material. Do not fallback to US, CN_A, or HK.
