# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

**Read `AGENTS.md` first.** It is the authoritative project rulebook (written for Codex, applies equally here): answer shape for architecture questions (Verdict / Pushback / facts vs inferences), the architecture boundary, prompt-alignment hard rules, and guard freeze rules. Answer the human in plain Chinese first; use identifiers only when needed.

## What this is

claw-trade is a multi-agent investment research system producing evidence-grounded Chinese reports in TradingAgents / TradingAgents-CN style. Markets: CN_A, US, HK, CRYPTO. Products: `/report` (full research workflow, triggered only by that chat command) and `/select` (stock selection). Never fabricate data, metrics, news, charts, or tool results; missing evidence must fail loudly, not be hidden behind fallbacks.

## Architecture (big picture)

- **claw-trade (Python, `src/claw_trade/`) owns the workflow state machine**: dispatch order, artifact authority, hard gates, report export. See `workflow/` (controller, runner, store, workers). The LLM never picks the next worker.
- **OpenClaw (`third_party/openclaw/`, Node/TS) runs exactly one agent turn per wake**: final provider prompt, tool schema exposure, session, tool calls. No claw-trade business logic may live there; only generic single-agent seams (tool narrowing, provider-payload capture, run evidence).
- **Workers are OpenClaw agents, not Python functions.** Their prompts, skills, and stage policy live in `agents/<worker>/`. Python only substitutes runtime variables (ticker, market, currency, dates) — it must not write worker business prompts.
- Report sequence: frontline (market, fundamental, news, social) → bull/bear debate → research_manager → trader → risk debate (challenger, guardian, moderator) → portfolio_manager. Other dirs under `agents/` serve selection, UI chat, scheduled/maintenance jobs.
- Stages pass outputs through artifacts (`artifacts/`, OpenViking-backed); `guards/` validates evidence/tool-call/claim integrity; `reports/` exports.
- `data_gateway/` is the market data layer (akshare, tushare, baostock, mootdx, yfinance, DuckDB warehouse, MongoDB) with a factory seed pack in `data/` (see `data/README.md`).
- `web/` + `ui_backend/` + `ui_contracts/` serve the React/Vite Research UI in `web/research-ui/` (WeChat channel bridge lives in `ui_backend/`).
- `production/`, `licensing/` (Virbox license gate), `packaging/production/`: install, signed auto-update, systemd. Version single source: `pyproject.toml` `[project].version`.

## Commands

```bash
uv run pytest                                  # all Python tests
uv run pytest tests/unit/test_workflow_controller.py::test_name   # single test
pnpm --dir web/research-ui test                # UI tests (vitest)
pnpm --dir web/research-ui build               # required after frontend changes
scripts/start-control-runtime.sh               # local control runtime (loads .env.local)
scripts/start-control-runtime.sh -- <cmd...>   # run a test/live command inside the runtime env
scripts/start-research-ui.sh                   # Research UI
```

## OpenClaw runtime change verification (mandatory)

Live runs load the built `third_party/openclaw/dist/**`, not `src`. After any runtime-affecting edit under `third_party/openclaw/src/**`: focused tests → `pnpm build` in `third_party/openclaw` → restart `scripts/start-control-runtime.sh` → fresh live run → inspect the captured provider payload. Skipping any step means live behavior is **NOT VERIFIED** — say so explicitly; stale `dist` evidence is not proof.
