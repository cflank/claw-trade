# Worker Chat Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build worker chat so users can talk to one explicitly selected OpenClaw worker in the main workspace and report reader, with `@` only opening a bounded worker picker.

**Architecture:** Add a dedicated worker chat path beside the existing report workflow: deterministic worker catalog, strict request DTOs, OpenClaw agent-chat seam, worker chat store, and UI selectors. The backend never infers worker identity from message text; it only trusts structured `workerId` from the UI or API request.

**Tech Stack:** Python/FastAPI backend, OpenClaw gateway RPC, React/TypeScript research UI, Vitest, pytest.

---

## Source Of Truth

- Design: `docs/worker聊天详细设计.md`
- Task list: `docs/worker聊天实施任务清单.md`
- Current legacy path to replace/migrate: `src/claw_trade/ui_backend/report_qa.py`, `src/claw_trade/web/routes_ui.py`, `web/research-ui/src/api/client.ts`, report reader components/routes.

## Current Workspace Warning

Before implementation, inspect `git status --short`. This workspace currently has unrelated or draft changes outside the two worker chat docs. Do not revert, stash, or include unrelated files. If old unapproved worker-chat draft files exist, read them as reference only, then either replace them deliberately in the task that owns that file or leave them untouched until that task.

## File Structure

Create or modify these backend files:

- Create: `src/claw_trade/ui_backend/worker_chat_catalog.py`
  - Seven allowed workers, display names, default worker, picker search keywords.
  - No message-body mention parser.
- Create: `src/claw_trade/ui_backend/worker_chat_models.py`
  - `WorkerChatMode`, request/result dataclasses, redacted user DTOs.
- Create: `src/claw_trade/ui_backend/worker_chat_openclaw.py`
  - OpenClaw worker chat client, seam discovery, session key construction, request/response parsing.
- Create: `src/claw_trade/ui_backend/worker_chat_store.py`
  - Idempotent turn storage and payload fingerprint checks.
- Create: `src/claw_trade/ui_backend/report_worker_chat_context.py`
  - Completed report validation and approved reader-visible material selection.
- Create or modify: `src/claw_trade/ui_backend/worker_chat_controller.py`
  - Unified controller for `generic_worker_chat` and `report_worker_chat`.
- Create: `agents/_shared/chat/REPORT.md` or `agents/<worker>/chat/REPORT.md`
  - Report worker chat prompt authority. Choose the shared path if the current agent config loader can address it cleanly; otherwise create per-worker files for the seven allowed workers.
- Modify: `src/claw_trade/web/openclaw_gateway.py`
  - Add the minimum gateway calls needed by `worker_chat_openclaw.py`.
- Modify: `src/claw_trade/web/state.py`
  - Wire worker chat services.
- Modify: `src/claw_trade/web/routes_ui.py`
  - Add worker list and worker chat endpoints; migrate/avoid old `report_qa` semantics.

Create or modify these frontend files:

- Modify: `web/research-ui/src/api/contracts.ts`
  - Worker menu, request, reply types.
- Modify: `web/research-ui/src/api/client.ts`
  - `listWorkerChatWorkers`, `sendWorkerChat`.
- Modify: `web/research-ui/src/api/workspace.ts`
  - Re-export worker chat APIs and types.
- Create: `web/research-ui/src/components/WorkerSelector.tsx`
  - Shared worker picker and `@` list interaction.
- Modify: `web/research-ui/src/components/Composer.tsx`
  - Optional worker selector integration for main chat.
- Modify: `web/research-ui/src/components/ReportReaderPanel.tsx` and/or `web/research-ui/src/routes/HomePage.tsx`, `web/research-ui/src/routes/ReportDetailPage.tsx`
  - Replace report "追问" behavior with report worker chat.
- Modify: `web/research-ui/src/styles.css`
  - Picker and chat layout styles.

Tests to add or modify:

- `tests/unit/ui/test_worker_chat_catalog.py`
- `tests/contracts/test_worker_chat_api_contracts.py`
- `tests/unit/ui/test_openclaw_worker_chat_client.py`
- `tests/contracts/test_openclaw_worker_chat_seam_missing_stops.py`
- `tests/contracts/test_openclaw_worker_chat_contract.py`
- `tests/unit/ui/test_generic_worker_chat.py`
- `tests/unit/ui/test_report_worker_chat_context.py`
- `tests/contracts/test_report_worker_chat_material_boundary.py`
- `tests/contracts/test_report_worker_chat_prompt_boundary.py`
- `tests/unit/ui/test_worker_chat_store.py`
- `tests/unit/ui/test_worker_chat_protocol_block_detection.py`
- `tests/unit/ui/test_report_worker_chat_related_snippets.py`
- `tests/unit/ui/test_worker_chat_tool_policy_request.py`
- `tests/unit/ui/test_report_worker_chat_pm_conclusion_gaps.py`
- `web/research-ui/src/__tests__/worker-chat.test.tsx` or focused tests in existing route test files.

## Task 0: Baseline And WIP Audit

**Files:**
- Read only: `git status --short`
- Read only: existing worker chat draft files if present

- [ ] **Step 1: Capture current dirty state**

Run: `git status --short`

Expected: List current modified/untracked files. Do not revert anything.

- [ ] **Step 2: Identify files owned by this plan**

Run:

```bash
git status --short | rg 'worker聊天|worker_chat|routes_ui.py|openclaw_gateway.py|state.py|HomePage.tsx|ReportDetailPage.tsx|Composer.tsx|ReportReaderPanel.tsx|contracts.ts|client.ts|workspace.ts|styles.css'
```

Expected: A scoped list of files that future tasks may touch.

- [ ] **Step 3: Read existing draft code if present**

Run:

```bash
sed -n '1,240p' src/claw_trade/ui_backend/worker_chat.py
sed -n '1,180p' src/claw_trade/ui_backend/worker_chat_catalog.py
```

Expected: Either files do not exist, or they are understood as draft input. Do not assume they satisfy the docs.

- [ ] **Step 4: Commit nothing**

Expected: No commit in this audit task.

## Task 1: Worker Catalog Contract

**Files:**
- Create/Modify: `src/claw_trade/ui_backend/worker_chat_catalog.py`
- Test: `tests/unit/ui/test_worker_chat_catalog.py`
- Test: `tests/contracts/test_worker_chat_user_surface.py`

- [ ] **Step 1: Write failing catalog tests**

Add tests covering:

```python
def test_worker_chat_catalog_only_exposes_approved_workers():
    menu = list_worker_chat_menu()
    assert [item["workerId"] for item in menu] == [
        "portfolio_manager",
        "research_manager",
        "market_analyst",
        "fundamental_analyst",
        "news_analyst",
        "social_analyst",
        "risk_moderator",
    ]


def test_default_worker_is_portfolio_manager():
    assert default_worker_id() == "portfolio_manager"


def test_catalog_does_not_parse_message_mentions():
    assert not hasattr(worker_chat_catalog, "parse_text_worker_mention")
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/unit/ui/test_worker_chat_catalog.py -q`

Expected: FAIL because catalog module/functions are missing or incomplete.

- [ ] **Step 3: Implement minimal catalog**

Implement:

```python
ALLOWED_WORKER_CHAT_CATALOG = (
    WorkerChatCatalogEntry("portfolio_manager", "组合经理", ("组合经理", "PM"), default=True),
    WorkerChatCatalogEntry("research_manager", "研究经理", ("研究经理",)),
    WorkerChatCatalogEntry("market_analyst", "市场分析师", ("市场分析师", "市场")),
    WorkerChatCatalogEntry("fundamental_analyst", "基本面分析师", ("基本面分析师", "基本面")),
    WorkerChatCatalogEntry("news_analyst", "新闻分析师", ("新闻分析师", "新闻")),
    WorkerChatCatalogEntry("social_analyst", "情绪分析师", ("情绪分析师", "情绪")),
    WorkerChatCatalogEntry("risk_moderator", "风险经理", ("风险经理", "风险")),
)
```

Expose `list_worker_chat_menu()`, `default_worker_id()`, `require_allowed_worker(worker_id)`.

- [ ] **Step 4: Run catalog tests**

Run: `uv run pytest tests/unit/ui/test_worker_chat_catalog.py tests/contracts/test_worker_chat_user_surface.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Use the repo-approved committer if present:

```bash
scripts/committer "Worker chat: add allowed worker catalog" src/claw_trade/ui_backend/worker_chat_catalog.py tests/unit/ui/test_worker_chat_catalog.py tests/contracts/test_worker_chat_user_surface.py
```

If `scripts/committer` is unavailable in this repo, use scoped `git add`/`git commit` for only these files.

## Task 2: Request/Response Models And API Contract

**Files:**
- Create: `src/claw_trade/ui_backend/worker_chat_models.py`
- Modify: `src/claw_trade/web/routes_ui.py`
- Modify: `web/research-ui/src/api/contracts.ts`
- Test: `tests/contracts/test_worker_chat_api_contracts.py`

- [ ] **Step 1: Write failing API contract tests**

Cover:

```python
def test_worker_chat_requires_worker_id(api_client):
    response = api_client.post("/api/ui/send-worker-chat", json={
        "requestId": "req-1",
        "mode": "generic_worker_chat",
        "text": "hello",
        "conversationId": "main",
    })
    assert response.status_code == 400


def test_generic_worker_chat_rejects_report_id(api_client):
    response = api_client.post("/api/ui/send-worker-chat", json={
        "requestId": "req-2",
        "mode": "generic_worker_chat",
        "workerId": "portfolio_manager",
        "text": "hello",
        "conversationId": "main",
        "reportId": "r1",
    })
    assert response.status_code == 400


def test_legacy_report_qa_cannot_act_as_worker_chat(api_client, saved_report):
    response = api_client.post("/api/ui/ask-report-question", json={
        "requestId": "req-legacy",
        "reportId": saved_report.id,
        "text": "以市场分析师身份回答",
    })
    assert response.status_code in {200, 400, 404, 409}
    assert response.json().get("kind") != "worker_chat_reply"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/contracts/test_worker_chat_api_contracts.py -q`

Expected: FAIL because endpoint/model is missing.

- [ ] **Step 3: Add Python DTOs**

Implement dataclasses:

- `WorkerChatRequest`
- `WorkerChatReplyForUser`
- `WorkerChatMode`
- `WorkerChatValidationError` if useful

Rules:

- `worker_id` is required.
- No `mentioned_worker_id`.
- No body-text worker parsing.
- User DTO does not expose internal ids/paths/provider refs.

- [ ] **Step 4: Add route skeleton**

In `routes_ui.py`, add:

- `GET /list-worker-chat-workers`
- `POST /send-worker-chat`

For this task, route can call a fake/stub controller only if tests do not require OpenClaw yet. Do not call old `report_qa` as worker chat.

Also add a regression test proving `/ask-report-question` is not a worker chat bypass. It may remain as explicitly legacy non-worker report QA, but it must not return `worker_chat_reply`, accept `workerId`, or satisfy worker chat identity semantics.

- [ ] **Step 5: Add frontend contracts**

Add TypeScript types:

- `WorkerChatMode`
- `WorkerChatWorkerForUser`
- `ListWorkerChatWorkersOutput`
- `SendWorkerChatInput`
- `WorkerChatReplyForUser`

- [ ] **Step 6: Run contract tests**

Run: `uv run pytest tests/contracts/test_worker_chat_api_contracts.py -q`

Expected: PASS for validation-level behavior.

- [ ] **Step 7: Commit**

Commit only files from this task.

## Task 3: OpenClaw Worker Chat Seam

**Files:**
- Create: `src/claw_trade/ui_backend/worker_chat_openclaw.py`
- Modify: `src/claw_trade/web/openclaw_gateway.py`
- Test: `tests/unit/ui/test_openclaw_worker_chat_client.py`
- Test: `tests/contracts/test_openclaw_worker_chat_contract.py`
- Test: `tests/contracts/test_openclaw_worker_chat_seam_missing_stops.py`

- [ ] **Step 1: Write failing seam tests**

Cover:

```python
def test_generic_worker_chat_uses_agent_scoped_session_key():
    request = OpenClawAgentChatRequest(
        worker_id="market_analyst",
        session_key="agent:market_analyst:generic:main",
        user_message="hello",
        prompt_profile="openclaw_default_agent_chat",
        prompt_variables={},
        tool_policy="openclaw_default",
        visible_tools=None,
        idempotency_key="req-1",
        capture_provider_payload=True,
    )
    result = client.send_agent_chat(request)
    assert transport.calls["sessions.create"]["agentId"] == "market_analyst"
    assert transport.calls["chat.send"]["sessionKey"].startswith("agent:market_analyst:")
```

Also cover stop behavior when required OpenClaw structure cannot be proven.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/unit/ui/test_openclaw_worker_chat_client.py tests/contracts/test_openclaw_worker_chat_contract.py tests/contracts/test_openclaw_worker_chat_seam_missing_stops.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement seam client**

Implement:

- `OpenClawAgentChatRequest`
- `OpenClawAgentChatResponse`
- `OpenClawWorkerChatClient`
- `build_session_key(mode, worker_id, report_id, conversation_id)`

Rules:

- `generic_worker_chat`: `visible_tools=None`
- `report_worker_chat`: `visible_tools=()`
- Worker identity must be in structured request/session, not only prompt text.
- Do not use `agent.runSingleWorker`.

- [ ] **Step 4: Extend gateway RPC wrapper minimally**

In `openclaw_gateway.py`, add support for `sessions.create` `agentId` if missing and add a worker-chat send method if current `chat.send` wrapper cannot carry required fields.

Stop condition: if current OpenClaw gateway cannot accept/prove required worker/session/prompt/tool/idempotency/payload fields, stop implementation and report the missing seam. Do not fake it in Python.

- [ ] **Step 5: Run seam tests**

Run: `uv run pytest tests/unit/ui/test_openclaw_worker_chat_client.py tests/contracts/test_openclaw_worker_chat_contract.py tests/contracts/test_openclaw_worker_chat_seam_missing_stops.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Commit only seam files/tests.

## Task 4: Worker Chat Store And Idempotency

**Files:**
- Create: `src/claw_trade/ui_backend/worker_chat_store.py`
- Test: `tests/unit/ui/test_worker_chat_store.py`
- Test: `tests/unit/ui/test_worker_chat_idempotency.py`

- [ ] **Step 1: Write failing store tests**

Cover:

```python
def test_same_request_and_same_fingerprint_replays_existing_turn():
    saved = store.save_turn_if_absent(turn)
    replay = store.save_turn_if_absent(turn)
    assert replay == saved


def test_same_request_different_fingerprint_conflicts():
    store.save_turn_if_absent(turn)
    with pytest.raises(UiProductError) as exc:
        store.save_turn_if_absent(replace(turn, request_fingerprint="different"))
    assert exc.value.code == "WORKER_CHAT_IDEMPOTENCY_CONFLICT"
```

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/unit/ui/test_worker_chat_store.py tests/unit/ui/test_worker_chat_idempotency.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement in-memory first**

Implement a focused store matching existing UI service patterns. Persist later only if current project patterns require it for UI history.

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/ui/test_worker_chat_store.py tests/unit/ui/test_worker_chat_idempotency.py -q`

Expected: PASS.

- [ ] **Step 5: Commit**

Commit only store files/tests.

## Task 5: Report Worker Chat Prompt Authority

**Files:**
- Create: `agents/_shared/chat/REPORT.md` or `agents/<worker>/chat/REPORT.md`
- Create/Modify: `src/claw_trade/ui_backend/worker_chat_openclaw.py`
- Test: `tests/contracts/test_report_worker_chat_prompt_boundary.py`

- [ ] **Step 1: Write failing prompt boundary tests**

Cover:

```python
def test_report_worker_chat_prompt_profile_is_agent_configured():
    request = build_report_worker_chat_request(worker_id="market_analyst")
    response = client.send_report_worker_chat(request)
    payload = response.captured_provider_payload
    assert payload.prompt_profile == "report_worker_chat"
    assert "RuntimeTarget" not in payload.model_visible_text
    assert "[ApprovedMaterials]" not in payload.model_visible_text
    assert "single_worker_minimal" not in payload.model_visible_text
```

- [ ] **Step 2: Run test to verify failure**

Run: `uv run pytest tests/contracts/test_report_worker_chat_prompt_boundary.py -q`

Expected: FAIL because report chat prompt authority is not configured or not wired.

- [ ] **Step 3: Add report chat prompt config**

Add the report worker chat prompt in agent config, not Python source. It must express only these constraints:

- answer as the selected worker;
- use only supplied saved report and approved worker materials;
- do not rerun workflow, call tools, rewrite reports, or create formal conclusions.

- [ ] **Step 4: Wire prompt profile to OpenClaw seam**

Ensure `send_report_worker_chat()` passes `prompt_profile="report_worker_chat"`, `prompt_variables`, and `user_message` to OpenClaw for rendering. Python may select materials but must not assemble the final worker business prompt.

- [ ] **Step 5: Run prompt boundary test**

Run: `uv run pytest tests/contracts/test_report_worker_chat_prompt_boundary.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Commit only prompt config, seam wiring, and prompt boundary tests.

## Task 6: Generic Worker Chat Backend

**Files:**
- Create/Modify: `src/claw_trade/ui_backend/worker_chat_controller.py`
- Modify: `src/claw_trade/web/state.py`
- Modify: `src/claw_trade/web/routes_ui.py`
- Test: `tests/unit/ui/test_generic_worker_chat.py`
- Test: `tests/contracts/test_worker_chat_prompt_boundary.py`

- [ ] **Step 1: Write failing controller tests**

Cover:

- default is not applied by backend;
- API missing worker fails;
- hand-typed `@市场分析师` does not change `worker_id`;
- generic mode never reads report repository;
- generic mode does not call report workflow runner.

- [ ] **Step 2: Run tests to verify failure**

Run: `uv run pytest tests/unit/ui/test_generic_worker_chat.py tests/contracts/test_worker_chat_prompt_boundary.py -q`

Expected: FAIL.

- [ ] **Step 3: Implement controller generic branch**

Flow:

```text
parse request -> validate worker -> reject report_id -> compute fingerprint -> idempotency lookup -> OpenClaw generic chat -> save turn -> render user DTO
```

Do not parse `@` from text.

- [ ] **Step 4: Wire services and route**

Use `UiHttpServices.worker_chat_controller`.

Ensure old normal `send-chat-message` remains unchanged unless explicitly routed by UI in later tasks.

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/ui/test_generic_worker_chat.py tests/contracts/test_worker_chat_prompt_boundary.py tests/contracts/test_worker_chat_api_contracts.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

Commit only generic backend files/tests.

## Task 7: Report Worker Chat Context And Backend

**Files:**
- Create: `src/claw_trade/ui_backend/report_worker_chat_context.py`
- Modify: `src/claw_trade/ui_backend/worker_chat_controller.py`
- Modify: `src/claw_trade/web/routes_ui.py`
- Test: `tests/unit/ui/test_report_worker_chat_context.py`
- Test: `tests/contracts/test_report_worker_chat_material_boundary.py`
- Test: `tests/unit/ui/test_report_worker_chat_pm_conclusion_gaps.py`
- Test: `tests/unit/ui/test_worker_chat_protocol_block_detection.py`
- Test: `tests/unit/ui/test_report_worker_chat_related_snippets.py`
- Test: `tests/unit/ui/test_worker_chat_tool_policy_request.py`

- [ ] **Step 1: Write failing context tests**

Cover:

- completed report required;
- final report required;
- PM worker allows PM conclusion fallback;
- non-PM worker requires that worker L1;
- raw/provider/debug/receipt/hash/manifest protocol text never becomes model-visible.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
uv run pytest tests/unit/ui/test_report_worker_chat_context.py tests/contracts/test_report_worker_chat_material_boundary.py tests/unit/ui/test_report_worker_chat_pm_conclusion_gaps.py tests/unit/ui/test_worker_chat_protocol_block_detection.py tests/unit/ui/test_report_worker_chat_related_snippets.py tests/unit/ui/test_worker_chat_tool_policy_request.py -q
```

Expected: FAIL.

- [ ] **Step 3: Implement material resolver**

Read only approved reader-visible sources:

- saved final report;
- PM L1 or PM conclusion;
- selected worker L1;
- related approved snippets.

Never scan raw/provider/debug/evidence directories for model-visible text.

- [ ] **Step 4: Implement report branch**

Pass `prompt_profile="report_worker_chat"`, prompt variables, selected materials, and user message into OpenClaw seam. Do not hand-build final business prompt in Python.

- [ ] **Step 5: Run report backend tests**

Run the command from Step 2 again.

Expected: PASS.

- [ ] **Step 6: Commit**

Commit only report worker chat backend/context files/tests.

## Task 8: Frontend API And Worker Picker

**Files:**
- Modify: `web/research-ui/src/api/contracts.ts`
- Modify: `web/research-ui/src/api/client.ts`
- Modify: `web/research-ui/src/api/workspace.ts`
- Create: `web/research-ui/src/components/WorkerSelector.tsx`
- Modify: `web/research-ui/src/components/Composer.tsx`
- Test: `web/research-ui/src/__tests__/worker-chat.test.tsx`

- [ ] **Step 1: Write failing UI tests**

Cover:

- default picker is 组合经理;
- typing `@` opens a list with exactly seven workers;
- selecting 市场分析师 changes outgoing `workerId` to `market_analyst`;
- hand-typing `@市场分析师` without choosing does not change `workerId`.

- [ ] **Step 2: Run tests to verify failure**

Run: `pnpm --dir web/research-ui exec vitest run src/__tests__/worker-chat.test.tsx`

Expected: FAIL.

- [ ] **Step 3: Add frontend API functions**

Add:

- `listWorkerChatWorkers()`
- `sendWorkerChat(input)`

- [ ] **Step 4: Implement WorkerSelector**

Rules:

- Button/combobox shows display name only.
- `@` in composer opens selector list.
- List contains only backend menu values.
- Search aliases apply only inside the open list.
- Message text is not parsed into worker identity.

- [ ] **Step 5: Integrate Composer for main worker chat**

Add optional props so existing non-worker uses are not broken:

- `workerChatEnabled`
- `workers`
- `selectedWorkerId`
- `onWorkerChange`

- [ ] **Step 6: Run UI tests**

Run: `pnpm --dir web/research-ui exec vitest run src/__tests__/worker-chat.test.tsx`

Expected: PASS.

- [ ] **Step 7: Commit**

Commit only frontend API/picker files/tests.

## Task 9: Main Workspace Integration

**Files:**
- Modify: `web/research-ui/src/routes/HomePage.tsx`
- Modify: `web/research-ui/src/styles.css`
- Test: existing `web/research-ui/src/__tests__/home-page.test.tsx` plus worker-chat tests

- [ ] **Step 1: Write failing HomePage tests**

Cover:

- main composer sends `generic_worker_chat`;
- default workerId is `portfolio_manager`;
- no `reportId` is included;
- hand-typed `@xxx` does not affect workerId.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pnpm --dir web/research-ui exec vitest run src/__tests__/home-page.test.tsx src/__tests__/worker-chat.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Route main composer to worker chat**

Main workspace worker chat is required by the source docs. Route the main workspace worker-chat composer through `sendWorkerChat({ mode: "generic_worker_chat", workerId, text, conversationId })`. If the existing app still needs a separate general assistant chat, add an explicit visible mode control in a separate follow-up task; do not leave this task ambiguous.

- [ ] **Step 4: Run tests**

Run the command from Step 2 again.

Expected: PASS.

- [ ] **Step 5: Commit**

Commit only HomePage/style/test files.

## Task 10: Report Reader Integration And Legacy Report QA Migration

**Files:**
- Modify: `web/research-ui/src/components/ReportReaderPanel.tsx`
- Modify: `web/research-ui/src/routes/HomePage.tsx`
- Modify: `web/research-ui/src/routes/ReportDetailPage.tsx`
- Modify: `web/research-ui/src/api/client.ts`
- Test: `web/research-ui/src/__tests__/report-detail-page.test.tsx`
- Test: `web/research-ui/src/__tests__/home-page.test.tsx`

- [ ] **Step 1: Write failing report reader tests**

Cover:

- report reader label says worker chat, not 追问;
- request uses `report_worker_chat`;
- request includes current `reportId`;
- default `workerId=portfolio_manager`;
- switching worker changes request workerId;
- report markdown remains unchanged after reply.

- [ ] **Step 2: Run tests to verify failure**

Run:

```bash
pnpm --dir web/research-ui exec vitest run src/__tests__/report-detail-page.test.tsx src/__tests__/home-page.test.tsx
```

Expected: FAIL.

- [ ] **Step 3: Replace report ask path in UI**

Replace `askReportQuestion()` calls with `sendWorkerChat()` in report reader surfaces.

Keep old backend `/ask-report-question` only as non-worker compatibility if needed, but do not use it for worker chat UI.

- [ ] **Step 4: Remove user-facing "追问" copy for worker chat**

Use copy like:

- `worker 聊天`
- `和组合经理聊`
- `当前聊天对象`

- [ ] **Step 5: Run UI tests**

Run command from Step 2.

Expected: PASS.

- [ ] **Step 6: Commit**

Commit only report reader UI files/tests.

## Task 11: Legacy Path And Reverse Checks

**Files:**
- Modify only if necessary: `src/claw_trade/ui_backend/report_qa.py`, `src/claw_trade/web/routes_ui.py`, old tests under `tests/unit/ui/test_report_question.py`
- Test: reverse grep checks from task list

- [ ] **Step 1: Enforce compatibility policy**

Use the compatibility policy chosen in Task 2:

- Keep `/ask-report-question` as legacy non-worker report QA, unused by new UI.
- Or remove/migrate it fully to worker chat and require `workerId`.

Do not leave active UI using `report_qa` for worker chat.

- [ ] **Step 2: Update tests for chosen policy**

If keeping legacy endpoint, tests must prove it is not a worker chat endpoint. If removing, tests must prove old UI path is gone.

- [ ] **Step 3: Run reverse grep**

Run:

```bash
rg -n "追问|report_qa" src web/research-ui/src tests
```

Expected: No active worker chat UI/API path still uses those names. Historical tests for legacy compatibility are allowed only if explicitly labeled non-worker.

- [ ] **Step 4: Run backend and frontend focused tests**

Run:

```bash
uv run pytest tests/unit/ui/test_worker_chat_catalog.py tests/contracts/test_worker_chat_api_contracts.py tests/unit/ui/test_openclaw_worker_chat_client.py tests/unit/ui/test_generic_worker_chat.py tests/unit/ui/test_report_worker_chat_context.py -q
pnpm --dir web/research-ui exec vitest run src/__tests__/worker-chat.test.tsx src/__tests__/home-page.test.tsx src/__tests__/report-detail-page.test.tsx
```

Expected: PASS.

- [ ] **Step 5: Commit**

Commit only legacy cleanup/test files.

## Task 12: Runtime Proof And Full Verification

**Files:**
- No source changes unless proof exposes a bug.
- Evidence paths are internal; do not expose provider payload refs in user DTO.

- [ ] **Step 1: Run static formatting checks**

Run:

```bash
git diff --check
pnpm --dir web/research-ui build
```

Expected: PASS.

- [ ] **Step 2: Run focused backend tests**

Run:

```bash
uv run pytest tests/contracts/test_worker_chat_api_contracts.py tests/contracts/test_worker_chat_prompt_boundary.py tests/contracts/test_openclaw_worker_chat_contract.py tests/contracts/test_openclaw_worker_chat_seam_missing_stops.py tests/contracts/test_report_worker_chat_material_boundary.py tests/contracts/test_report_worker_chat_prompt_boundary.py tests/unit/ui/test_worker_chat_catalog.py tests/unit/ui/test_openclaw_worker_chat_client.py tests/unit/ui/test_generic_worker_chat.py tests/unit/ui/test_report_worker_chat_context.py tests/unit/ui/test_worker_chat_store.py tests/unit/ui/test_worker_chat_idempotency.py tests/unit/ui/test_worker_chat_protocol_block_detection.py tests/unit/ui/test_report_worker_chat_related_snippets.py tests/unit/ui/test_worker_chat_tool_policy_request.py tests/unit/ui/test_report_worker_chat_pm_conclusion_gaps.py -q
```

Expected: PASS. Every named test file is introduced by an earlier task; do not silently skip any file.

- [ ] **Step 3: Run runtime provider proof**

Run one focused `generic_worker_chat` proof and one focused `report_worker_chat` proof.

Each proof must capture:

- mode;
- worker id;
- OpenClaw session key;
- provider final messages;
- visible tools;
- request id/runtime marker;
- user-visible reply.

Expected:

- worker identity matches structured `workerId`;
- no `/report` stage prompt in generic path;
- no `[ApprovedMaterials]` in generic path;
- no raw/provider/debug/receipt/hash/manifest protocol text in model-visible prompt;
- response is not Python fallback.

- [ ] **Step 4: Run Chrome QA**

Verify:

- main workspace default worker is 组合经理;
- `@` opens only seven workers;
- hand-typed `@xxx` does not change worker;
- report reader default worker is 组合经理;
- report worker chat does not create a new `/report` workflow;
- report markdown does not change.

- [ ] **Step 5: Final commit**

Only after all checks pass, commit any remaining verification docs or test adjustments.

## Execution Notes

- Do not use `git stash`.
- Do not switch branches or create worktrees unless explicitly approved.
- Keep commits scoped by task.
- If OpenClaw worker chat structured seam is missing, stop at Task 3 and report exactly which field/API is missing.
- Do not ship a Python prompt-fallback implementation.
