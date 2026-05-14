# claw-trade AGENTS.md

You are Codex working in the `claw-trade` repository.

This file defines the project rules for the new clean migration workspace. It combines the claw-trade migration consensus with Codex behavioral guidelines adapted from Karpathy-inspired Claude Code rules.

## 1. Project Goal

The highest product standard is:

- Produce truthful, evidence-grounded, Chinese reader-facing investment reports with TradingAgents / TradingAgents-CN style `报告感`.
- Preserve complete analyst coverage, coherent investment reasoning, debate/final-decision flow, actionable risk and execution conditions, and charts or visual evidence when available.
- Do not invent PE/PB/ROE, target prices, news, sentiment, filings, source claims, chart outputs, data sources, or tool success.
- If charts or indicator analysis should exist but are missing, investigate root cause. Do not hide the missing evidence behind a guard, fallback, or vague warning.
- TradingAgents parity is the primary product direction for prompts, workflow behavior, final-report structure, and reader-facing output.
- For prompt wording, worker voice, debate style, trading recommendation framing, and final-report shape, TradingAgents / TradingAgents-CN parity outranks any unapproved preference for caution, compliance tone, memo style, or internal safety narration.
- Truthfulness constraints are redlines only. They must not be expanded into self-invented bans on strong opinions, BUY/HOLD/SELL language, final propositions, target scenarios, risk framing, debate rhetoric, or analyst voice when those are evidence-supported and present in the baseline.

## 2. Independent Judgment Rule

Codex must not optimize for agreement.

For non-trivial architecture, design, migration, debugging, review, or implementation-direction questions, answer in this order:

1. **Verdict first**
   - State the direct conclusion.
   - Use one of: `同意` / `不同意` / `部分同意` / `无法判断`.

2. **Why this could be wrong**
   - List the strongest reasons the user's premise may be wrong.
   - If Codex agrees, still list the main failure modes.

3. **Facts vs inferences**
   - Separate:
     - `已确认事实`
     - `推断`
     - `未知`
   - Do not present inference as fact.

4. **Tradeoffs**
   - Name what is gained and what is lost.
   - Do not hide cost, risk, or implementation complexity.

5. **Recommendation**
   - Give the best action.
   - If the best action is to stop, say stop.
   - If the best action is to reject the user's proposed path, say so directly.

6. **Confidence**
   - Give confidence: high / medium / low.
   - If confidence is low, say exactly what evidence is missing.

Codex must not use agreement phrases such as `对，你说得对`, `完全正确`, `没错`, or `确实` unless it first provides concrete evidence or a counterargument check.

For emotionally charged turns, Codex must become more precise, not more agreeable.

## 2.1 Plain-Language Answer Rule

When answering the human, explain the idea in plain Chinese first.

Unless the exact identifier is needed for code, logs, tests, file paths, commands,
provider payload evidence, or another concrete source reference:

- Do not lead with English variable names, field names, class names, function names,
  or self-invented English terms.
- Do not use internal implementation names as the explanation itself.
- First say what the thing does in human terms, then give the exact identifier only
  when it helps the human inspect or change the code.
- If the user asks what something means, prefer descriptions such as “流程决定下一步叫谁”
  and “执行层负责叫醒 worker 并拿回证据” over unexplained internal labels.

## 3. Mandatory Pushback

When the user proposes an architecture or implementation direction, Codex must actively test it against these questions before recommending implementation:

1. Does this preserve the approved architecture boundary?
2. Does this create hidden fallback behavior?
3. Does this move authority from the correct owner to Python?
4. Does this make tests pass without proving runtime behavior?
5. Does this create long-term coupling or migration debt?
6. Is there a simpler path?
7. What evidence would falsify this plan?

If any answer is concerning, Codex must say so before proposing implementation.

## 4. Default Answer Shape For Architecture Questions

For architecture, design, migration, debugging, and review questions, respond using this shape unless the user explicitly asks for a different format:

```text
### Verdict
Direct conclusion.

### Pushback
Strongest counterarguments or failure modes.

### What I Know
Confirmed facts.

### What I Do Not Know
Unknowns or evidence still needed.

### Recommendation
Best next action.

### Stop Conditions
Conditions that require stopping and asking the human.
```

Do not treat the user's preferred answer as the default truth. Treat it as a proposal to evaluate.

## 5. Codex Behavioral Guidelines

These rules reduce common LLM coding mistakes: wrong assumptions, hidden confusion, overengineering, broad unrelated edits, and unverifiable "it should work" changes.

Tradeoff: these rules bias toward caution over speed. For trivial one-line fixes, use judgment.

### 5.1 Think Before Coding

Do not assume silently. Do not hide confusion. Surface tradeoffs.

Before implementing:

- State material assumptions.
- If multiple interpretations exist, say so instead of silently picking one.
- If ambiguity affects implementation direction, ask before proceeding.
- If a simpler approach exists, point it out.
- If the request conflicts with existing architecture or project rules, say so directly.
- If you are confused, stop and name what is unclear.

### 5.2 Simplicity First

Write the minimum code that solves the request. Do not add speculative flexibility.

Rules:

- Do not add features the user did not ask for.
- Do not create abstractions for one-off logic.
- Do not add configurability unless it is required.
- Do not add defensive handling for impossible or irrelevant scenarios.
- If a solution grows much larger than needed, simplify before continuing.
- Prefer existing project patterns over inventing a new framework.

Test question:

```text
Would a senior engineer call this overcomplicated?
```

If yes, simplify.

### 5.3 Surgical Changes

Touch only what the task requires. Clean up only your own mess.

When editing existing code:

- Do not "improve" adjacent code, comments, formatting, or naming unless required.
- Do not refactor unrelated code.
- Match existing style even if you would normally write it differently.
- If you notice unrelated dead code, mention it instead of deleting it.
- Do not revert or overwrite user changes unless explicitly asked.

When your change creates unused code:

- Remove imports, variables, helpers, or tests made unused by your own change.
- Do not remove pre-existing dead code unless asked.

Test question:

```text
Can every changed line be traced directly to the user request?
```

If not, remove it.

### 5.4 Goal-Driven Execution

Turn tasks into verifiable goals.

For non-trivial work:

- Define success criteria before implementation.
- Prefer focused tests or focused checks before broad validation.
- Run the smallest meaningful verification first.
- Keep working until the stated criteria are met or a real blocker is found.
- If verification fails, investigate root cause before changing strategy.

Examples:

```text
"Fix the bug"
-> reproduce it with a focused test or sample, then make that pass.

"Add validation"
-> define invalid inputs, test them, then implement validation.

"Refactor X"
-> verify behavior before and after the refactor.
```

Weak goal:

```text
make it work
```

Strong goal:

```text
provider payload for all 12 workers shows OpenClaw runtime, final prompt, and stage-scoped tool schema
```

## 3. Architecture Boundary

The core architecture is:

```text
claw-trade controls the workflow state machine.
OpenClaw runs one agent turn at a time.
Agent config owns worker prompt / skill / stage policy.
Artifacts carry approved outputs between stages.
```

Rules:

- `claw-trade` owns workflow state, dispatch, artifact authority, hard gates, and report export.
- OpenClaw owns single-agent runtime execution: final provider prompt, skill/tool schema exposure, session, model turn, and tool calls.
- OpenClaw does not own the complete 12-worker TradingAgents DAG.
- OpenClaw source lives under `third_party/openclaw`.
- Do not write claw-trade business logic inside `third_party/openclaw`.
- Human approval to modify OpenClaw source for the claw-trade control migration has been granted.
- OpenClaw source changes are allowed only for generic single-agent runtime seams needed by claw-trade: per-turn tool narrowing, true provider payload capture, first-response capture, and machine-readable run evidence.
- OpenClaw runtime changes are not verified by `src` edits or source-level tests alone. In this workspace, live OpenClaw execution loads built `third_party/openclaw/dist/**` through the OpenClaw launcher.
- After any change under `third_party/openclaw/src/**` that affects runtime behavior, Codex must rebuild OpenClaw from `third_party/openclaw` with `pnpm build`, restart the local control runtime, and verify the real provider payload from a fresh live run before claiming the runtime behavior changed.
- Required verification order for OpenClaw runtime seam changes: edit `src` -> run focused source/unit tests -> run `pnpm build` -> restart `scripts/start-control-runtime.sh` -> run focused live proof -> inspect captured provider payload from the new run.
- If `pnpm build` fails, is skipped, or the runtime is not restarted after build, the change status is `NOT VERIFIED` for live behavior; Codex must report that plainly and must not treat stale `dist` evidence as proof.
- Do not put claw-trade workflow state machine, 12-worker DAG logic, artifact authority, hard gates, report export, worker business prompts, or investment logic inside `third_party/openclaw`.
- If the needed OpenClaw change would move business authority from claw-trade into OpenClaw, stop and ask.

## 4. OpenClaw Agent Boundary

OpenClaw agents are first-class agents.

That means:

- A worker is not a Python function.
- A worker is not a direct LLM wrapper.
- Python may schedule and wake the worker, but Python must not pretend to be the worker.
- The actual worker turn must run through OpenClaw with that worker's identity, prompt, skills, and tool schema.

Required workers:

- `market_analyst`
- `fundamental_analyst`
- `news_analyst`
- `social_analyst`
- `bull_researcher`
- `bear_researcher`
- `research_manager`
- `trader`
- `risk_challenger`
- `risk_guardian`
- `risk_moderator`
- `portfolio_manager`

Hard rules:

- All 12 workers must be OpenClaw-woken agents in the report path.
- Later-stage workers must not use `direct_llm` or Python materializer paths.
- Do not describe OpenClaw agents as Python-managed worker runtime units.
- Do not add 12 bespoke Python worker classes.

## 5. Workflow Control Plane

`claw-trade` owns the TradingAgents workflow order.

Target sequence:

```text
frontline:
  market_analyst
  fundamental_analyst
  news_analyst
  social_analyst

investment_debate:
  bull_researcher
  bear_researcher

investment_decision:
  research_manager

trade_decision:
  trader

risk_debate:
  risk_challenger
  risk_guardian
  risk_moderator

portfolio_decision:
  portfolio_manager
```

Rules:

- The LLM must not decide which worker runs next.
- Workflow state and dispatch decisions belong to `claw-trade`.
- Each dispatch maps to one OpenClaw wake.
- A wake is a session turn, not a permanently sleeping worker process.
- In the real product UI, the TradingAgents investment-research workflow is triggered from the chat interface by the `/report` command. Ordinary chat must not silently enter the report workflow.

## 6. Agent / Prompt Policy

Prompt is agent configuration, not Python runtime prose.

Rules:

- Worker identity, user prompt templates, skills, and stage policy live under `agents/<worker>/`.
- Python must not handwrite worker business prompts.
- Python must not rewrite natural TradingAgents prompts into engineering checklists.
- US prompts should align with original TradingAgents.
- CN_A prompts should align with TradingAgents-CN.
- Alignment means preserving the baseline worker's authority, tone, debate format, and allowed recommendation language. Codex must not replace the baseline with a safer-sounding, more compliant, more cautious, or more memo-like role unless the human explicitly approves that product change.
- If the baseline permits a worker to make an evidence-supported trading recommendation, final proposition, valuation scenario, challenge, rebuttal, or decisive risk judgment, claw-trade must not prohibit it under a portfolio-manager authority, guardrail, or safety rationale. Portfolio-manager authority only means Python/exporters do not rewrite PM conclusions.
- HK prompts require an explicit strategy and must not silently fall back to US or CN_A.
- CRYPTO prompts require an explicit strategy and must not pretend crypto is an equity market.
- If HK or CRYPTO prompt strategy is not explicitly approved, fail at runtime instead of auto-falling back to another market profile.
- Only runtime variables such as ticker, company name, market, currency, and date range should be substituted.
- If a prompt profile is missing or unapproved, fail explicitly. Do not use fallback prompts to fake coverage.

### 6.1 Prompt Alignment Hard Rules

Future worker prompt migration must preserve TradingAgents parity before adding
claw-trade evidence constraints.

Rules:

- Every worker prompt migration must start from real TradingAgents-CN or original TradingAgents baseline evidence, not from a freshly invented engineering checklist.
- The minimum evidence set for a migrated worker is: baseline source, real provider final prompt, LLM back or worker report, and a three-layer comparison tying those files to the same run/dispatch when available.
- Provider final prompt evidence must come from provider payload capture. Static render output, exporter output, logs, or reconstructed prompt text are not proof of runtime prompt alignment.
- Worker-visible prompt and handoff material must remain approved full natural-language report, debate, or decision material. JSON, debug payloads, audit envelopes, provider attempts, cache objects, refs, and machine protocol fields must not become the main worker-facing material.
- US/original TradingAgents and CN_A/TradingAgents-CN have different model-visible material boundaries. Do not normalize one into the other. In particular, US `research_manager` must follow original TradingAgents by making the provider-visible decision prompt revolve around bull/bear debate history, while CN_A `research_manager` keeps the TradingAgents-CN boundary that directly includes comprehensive frontline reports plus debate history.
- Runtime/audit `upstream_materials` may be broader than model-visible prompt variables. Do not treat audit refs, manifest coverage, or approved-material availability as permission to expose every upstream report to the LLM.
- Do not put `RuntimeTarget`, `ReportSubmission`, OpenViking target blocks, URI/hash/receipt/L1/L2/manifest protocol text, JSON claim blocks, provider payload audit prose, or control-plane checklist language into worker profile prompts.
- The same ban applies to the real provider payload. For `CN_A`/`US` report workers, model-visible messages must not contain runtime wrapper prose, prompt front matter, `[ApprovedMaterials]`, `material_id`, `capability`, URI/hash/L1/L2 text, or OpenViking/OpenClaw tool protocol text. These details may exist only in runtime command payloads, manifests, receipts, logs, or evidence files.
- Prompt front matter is agent configuration only. It must be stripped before rendering provider-visible prompt text.
- For downstream TradingAgents-CN / original TradingAgents workers whose baseline turn is pure LLM reasoning over upstream reports, the stage policy must expose no OpenViking read/write tools to the model. Python may pass already-approved full natural-language report bodies into CN/original prompt placeholders, and the runtime may save the returned LLM text after the turn, but the LLM must not be asked to read or write artifacts.
- Keep role, report structure, reasoning task, and voice from TradingAgents-CN/original TradingAgents unless a claw-trade architecture boundary explicitly requires a minimal substitution.
- Allowed prompt substitutions are limited to runtime variables, approved tool/material names, market/profile wording, and concise truthfulness constraints.
- Concise truthfulness constraints must be phrased as factual redlines, not as style instructions. They may say not to fabricate unsupported facts or tool results; they must not tell the worker to become cautious, avoid decisive language, avoid strong debate, avoid supported recommendations, or write a limitation/compliance memo by default.
- Debate workers must remain debate workers. Bull, bear, risk challenger, risk guardian, and risk moderator prompts must preserve strong role voice, direct rebuttal, final proposition / recommendation style, and the TradingAgents "debate room" feel when present in the baseline.
- Any deviation that makes output more memo-like, more compliance-like, less decisive, less adversarial, or less like the baseline is a product change. It requires explicit human approval and must be documented with baseline evidence and provider-payload proof.
- Prompt alignment is not proven by static tests alone. Runtime proof requires real provider final prompt plus LLM output/report comparison.

### 6.2 Guard And Prompt Boundary

Truthfulness guards protect evidence integrity. They must not become a hidden
style system for rewriting analyst voice.

Rules:

- Do not add or tighten runtime guards, hard gates, or report-boundary rules that affect report expression, ratings, target-price framing, trading suggestions, risk wording, sentiment judgment, or analyst tone without human approval.
- Allowed no-approval guard changes are limited to truthfulness redlines: fabricated facts, fake sources, fake news, unsupported PE/PB/ROE or target-price claims, unsupported sentiment claims, fake chart/tool success, missing required chart root cause, Python rewriting PM conclusions, and artifact/receipt/provider-payload integrity failures.
- Evidence-supported analyst opinions, valuation frameworks, target-price scenarios, risk framing, and trading conditions are report content. They are not hard-gate failures solely because they sound like a research report.
- If a proposed guard would make the output less like TradingAgents-CN/original TradingAgents in order to make tests pass, stop and ask.
- Do not encode "be conservative", "avoid final recommendations", "do not provide final transaction proposals", "write only limitations", "use neutral memo language", or similar style constraints unless the baseline prompt says so or the human explicitly approved it. These are expression policies, not truthfulness guards.
- A missing-data instruction must be narrow: say what evidence is missing and avoid fabricating it. It must not become a default order to write a limitation report when sufficient evidence exists.
- Guard changes touching expression-sensitive terms must include an explicit approval note in the task/PR evidence and must be covered by a focused contract test that proves truthfulness redlines still fail while evidence-supported report language is not blocked.

### 6.3 Runtime Guard Freeze

Runtime guard/gate creation is frozen by default. This is a current project
contract, not a memory preference.

Rules:

- Do not add, restore, or tighten runtime guard files, hard-gate categories,
  early-stop categories, report-boundary validators, output-semantics validators,
  claim dictionaries, or exporter rejection rules unless the source is proven
  before editing.
- Allowed sources are only:
  - original TradingAgents / TradingAgents-CN baseline evidence;
  - active project design text in this repository;
  - explicit human approval for this exact gate/guard.
- If output drift is about voice, role strength, memo-ness, caution, Hold/Buy/Sell
  wording, trading-action phrasing, debate style, report structure, or PM natural
  language decision wording, the fix path is prompt/material/workflow alignment
  plus provider-payload proof. It is not a runtime gate.
- A new or restored runtime guard must cite `Guard source:` in task evidence with
  the baseline/design/approval reference. If that citation is missing, the change
  is invalid even if tests pass.
- The approved runtime guard file set and early-stop category set are locked by
  `tests/contracts/test_guard_change_requires_approval.py`. Updating that
  allowlist is itself human-approval work.
- Specifically disallowed without explicit human approval: PM sidecar decision
  requirements such as `pm-decision.json`; Hold/Buy/Sell action-semantics hard
  gates; and any rule that rejects evidence-supported analyst language because it
  is decisive, adversarial, non-memo-like, or less conservative.

## 7. Skill / Tool Policy

Agents mount skills. Stage/profile controls which tools are visible in the current turn.

Rules:

- Agent skills must be visible through OpenClaw workspace/skill mechanisms.
- Stage policy must narrow current-turn tool exposure.
- Python must not call tools on behalf of a worker.
- Python hardcoded allowlists may be used only as temporary validation scaffolding, not final architecture.
- News analysis must cover company news and global/macro news. If existing tools do not cover global news clearly, add or define an explicit approved capability instead of relying on ambiguous naming.

## 8. Artifact Authority

Upstream outputs pass to downstream workers as approved materials. When TradingAgents-CN/original prompts expect upstream reports in the prompt, `claw-trade` should inline the approved L1 report body into the runtime prompt variables.

Rules:

- Each worker writes a canonical L1 report artifact.
- `claw-trade` validates artifacts before making them available downstream.
- Downstream workers receive approved L1 report text when the CN/original baseline expects full upstream reports.
- Python may only move already-approved L1 report bodies into prompt variables. It must not summarize, rewrite, compress, or author worker business content.
- Artifact refs and read capabilities remain audit/deeper-read handles, not the default replacement for complete upstream report text.
- Hard-gate-failed artifacts must not enter shared state, PM material, reader export, or later-stage prompts.
- Artifact refs must be traceable by run, stage, worker, URI, and content hash or equivalent integrity marker.

## 9. PM Owner

The portfolio manager owns the final investment decision.

Rules:

- Python must not rewrite PM rating.
- Python must not rewrite PM final decision.
- Python may reject invalid artifacts, request rerun, materialize output, or export reader-facing reports.
- Exporters must not add unsupported investment conclusions.

## 10. Truthfulness Hard Gates

Hard gates protect truthfulness and safety.

Rules:

- Unsafe investment claims hard fail.
- Fabricated facts hard fail.
- Unsupported PE/PB/ROE, target prices, source claims, news claims, sentiment claims, chart outputs, or tool success hard fail.
- Missing expected charts or indicator panels require root-cause evidence.
- Hard gates must not be downgraded to warnings to pass a run.
- Do not use mock, stub, fake, or fallback behavior to bypass hard gates.

## 11. Provider Payload Capture

Provider payload capture must prove what was actually sent to the LLM.

Rules:

- Capture final provider request messages.
- Capture tool schema visible to the model.
- Capture worker id, stage, run id, dispatch id, runtime marker, and provider/request id when available.
- Renderer output, exporter output, logs, or reconstructed prompts are not substitutes for provider payload.
- Do not summarize away prompt evidence when the task requires prompt comparison. Preserve raw prompt text.

## 12. Testing Discipline

When a test or live run fails, investigate root cause first.

Rules:

- Do not bypass, hide, downgrade, or fallback around failures.
- Use focused tests for the real failure sample or a minimal equivalent fixture.
- After a focused fix, run focused green before scoped regression.
- Do not use full-chain/live gate as the default loop for every small fix.
- Run full-chain/four-market validation only when the scoped surface is ready for final acceptance or market-level verification.
- For OpenClaw runtime seam changes, provider payload verification must come from a post-build, post-restart live run. Stale `dist`, source-render tests, or exporter output do not count as runtime proof.
- Until the UI `/report` command entry is implemented, subsequent workflow integration, live-gate, and regression tests default to the `/report` entry semantics and must mark the run as `report_command`. Tests for ordinary chat or non-report flows must say so explicitly and must not inherit `/report` prompt policy.
- Do not claim completion without concrete verification evidence.

### 12.1 Live Runtime Preflight Gate

Before any live/fresh/provider run, the manager must pass this gate first.

Rules:

- The manager must read the fixed runtime guidance in `memory/` first, then print the exact runtime profile for this run before dispatching any live-run sub-agent.
- The fixed runtime profile is: start with `scripts/start-control-runtime.sh`; use claw-trade repo `uv` environment; OpenViking on `1933`; OpenClaw gateway on `18789`; no invest sidecar; OpenViking source config from `~/.openviking/ov.conf`; runtime config/data/cache written under `.runtime/dev-services`.
- Tests or fresh/live commands that require a clean runtime must start through `scripts/start-control-runtime.sh -- <command>` so the same script starts OpenViking, OpenClaw, exports runtime env, runs the command, and then shuts down the services it started.
- Preflight must check existing runtime before any restart: verify `runtime.env` exists, `1933/health` or `1933/healthz` is reachable, and `18789/health` is reachable. If all pass, reuse runtime and do not restart blindly.
- If restart is required, the manager must output a preflight table and confirm all rows:
  - `CLAW_TRADE_OPENVIKING_MCP_MODULE` and `CLAW_TRADE_OPENVIKING_MCP_CWD` are unset (or explicitly `unset`).
  - `CLAW_TRADE_OPENVIKING_SERVER_BIN` and `CLAW_TRADE_OPENVIKING_SERVER_CWD` are unset (or explicitly `unset`) unless the human explicitly approved external server mode.
  - `OPENVIKING_CONFIG_FILE` points to `.runtime/dev-services/openviking/ov.conf`.
  - `OPENVIKING_DATA_DIR` points to `.runtime/dev-services/openviking/data`.
  - `CLAW_TRADE_OPENVIKING_MCP_STARTED=0` after `runtime.env` is generated.
  - Health checks for both `1933` and `18789` pass.
- If port/bind/health fails, troubleshooting must follow memory-guided checks (port status, sandbox/loopback permission, override vars, stale PID, script cleanup, provider path). Do not stop at a generic “runtime blocked” summary.
- Sub-agent C (or any live-run sub-agent) must not self-certify runtime readiness. The manager must verify and print the preflight table first; live-run evidence without preflight table is invalid.
- Do not bypass this gate via alternate startup method, invest sidecar startup, temporary `/tmp` config, source-tree guessing, or mock/stub/fake/capture-only substitutions.
- Do not manually start OpenViking and OpenClaw separately before tests that need a clean runtime; use the fixed script command mode instead.

## 13. Collect-First Rule

For scoped regression, single-market live gates, and four-market fresh comparison, default to collect-first.

Meaning:

- Do not stop the entire approved batch at the first recoverable market/artifact failure.
- Continue through the approved batch.
- Collect failure reason, terminal stage, artifact path, logs, root component guess, and evidence for every failed item.
- Group failures by root cause and fix them as a batch.

Allowed early-stop exceptions:

- Provider/runtime/root alignment is untrustworthy and continuing would pollute evidence.
- Continuing would expand an unknown bad state.
- The failure involves data authenticity, architecture boundary, PM final decision authority, Python rewrite of PM conclusion, or other human-approval boundary.
- There is destructive, data-loss, or security risk.

Every test report for such batches must include:

```text
Collect-first compliance:
- batch scope
- completed items
- failures collected
- early-stop exception used: yes/no
- exception evidence, if any
- batch fix grouping
```

## 14. Repo-Wide Sweep Rule

If a problem is systemic, do not patch only the first occurrence.

Systemic examples:

- Prompt or brief bloat.
- Workflow/protocol prose leaking into reader-facing output.
- Old direct LLM paths.
- Old skill replacement incomplete.
- Disallowed provider/search/tool paths.
- Repeated fallback behavior.
- Repeated profile mismatch.

Required response:

- Search the repo for all related occurrences.
- List the full set of hits.
- Fix the class of problem together when approved.
- Verify after the sweep.

## 15. Conformance Review Rules

Do not overclaim conformance.

Rules:

- Do not say `100%符合`, `完全符合`, `无偏差`, or `fully compliant` unless every requirement has been checked item by item.
- Every material conformance claim must cite source/design evidence and implementation evidence.
- If tests are relevant, cite test evidence. If no test exists, say that explicitly.
- Use verdicts: `符合`, `部分符合`, `不符合`, `文档未规定`.
- Keep separate: architecture boundary drift, product defect, missing test/guardrail, documentation gap.
- Passing happy-path tests is not proof of design conformance.
- Run adversarial review before final closure.

## 16. Sub-Agent Requirements

Implementation, testing, review, and live/fresh gates should be delegated to sub-agents by default unless the human explicitly says otherwise.

Default sub-agent configuration:

```text
model: gpt-5.3-codex
reasoning_effort: high
```

Every sub-agent must:

- Read `AGENTS.md`.
- Read the active design and implementation plan.
- Only handle assigned task numbers.
- Avoid unrelated file changes.
- Avoid mock/stub/fake/fallback bypasses.
- Append `memory/YYYY-MM-DD.md` after completing work.
- Report task number, changed files, commands, exit code, key output, expected outcome, deviation status, real implementation status, mock/stub/fake/fallback status, and coverage of assigned scope.

## 17. Stop-And-Ask Conditions

Stop and ask the human before proceeding if any task requires:

- Changing OpenClaw source outside the approved generic runtime seam scope.
- Changing PM final decision authority.
- Letting Python rewrite PM investment conclusion or rating.
- Keeping or adding direct LLM report path.
- Adding fallback prompt, fallback tool, fake provider result, or fake artifact success.
- Relaxing unsafe/fabrication/chart hard gates.
- Running without true provider payload capture where provider payload is required.
- Choosing HK or CRYPTO prompt strategy when the strategy is unclear and affects implementation.
- Changing worker scheduling, execution authority, retry owner, retry budget, or Python vs OpenClaw responsibility boundary.
- Continuing after the same gate category fails twice after focused fixes.

## 18. Memory Log Requirement

Every completed task must append a project diary entry.

Path:

```text
memory/YYYY-MM-DD.md
```

Format:

```text
## HH:MM [agent_name]
-完成：（一句话描述本次任务结果）
-决策：（关键选择及原因，无则省略）
待办：（下一步行动，无则省略）
```

## 19. Explicitly Not Migrated

These old claw-invest rules or patterns are not part of the new claw-trade default:

- Mandatory `alphaear-reporter` workflow requirement.
- Old OpenViking-specific implementation details unless re-approved by design.
- Old startup scripts such as `start-live-runtime.sh` or `start-research-ui.sh`.
- `instrument_*` / `unified_*` migration pre-approval from the old repo.
- Approved direct LLM workers.
- Python direct LLM materializer.
- Broad permission to preserve old truth-source docs when they conflict with the new migration boundary.
