# Approved Provider Scope Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent approved data sources from being silently narrowed by code, report request sets, provider capability declarations, or agent interpretation.

**Architecture:** Add a code-readable approved provider scope matrix and contract tests that connect approved source modules to provider capabilities and report data requests. The contract distinguishes implemented report scope from explicit implementation gaps, so a missing attempt cannot be described as “source has no data.”

**Tech Stack:** Python, pytest, existing `ProviderRegistry`, existing report data pack request specs.

---

### Task 1: Add Approved Provider Scope Matrix

**Files:**
- Create: `src/claw_trade/data_gateway/approved_provider_scope.py`
- Test: `tests/contracts/test_approved_provider_scope.py`

- [ ] **Step 1: Write failing tests**

Create tests that assert:
- every implemented approved scope has matching provider capability;
- every report-required implemented scope is reachable from `data_pack_bridge` report requests;
- every approved but not-yet-implemented scope has explicit `implementation_status="not_implemented"` and a reason.

- [ ] **Step 2: Implement matrix**

Add immutable `ApprovedProviderScope` records with market, provider id, endpoint id, data type, granularity, fields, domain, report_required, implementation_status, and reason.

- [ ] **Step 3: Run focused test**

Run:

```bash
uv run pytest tests/contracts/test_approved_provider_scope.py -q
```

Expected: pass.

### Task 2: Wire Scope Into Existing Provider Contract Tests

**Files:**
- Modify: `tests/contracts/test_provider_capabilities.py`
- Modify: `tests/unit/data_gateway/test_provider_selector.py`

- [ ] **Step 1: Add coverage checks**

Ensure approved implemented scope cannot disappear from provider capabilities or selector candidates.

- [ ] **Step 2: Run focused tests**

Run:

```bash
uv run pytest tests/contracts/test_approved_provider_scope.py tests/contracts/test_provider_capabilities.py tests/unit/data_gateway/test_provider_selector.py -q
```

Expected: pass.

### Task 3: Add Human-Readable Audit Output Helper

**Files:**
- Modify: `src/claw_trade/data_gateway/approved_provider_scope.py`
- Create: `scripts/validation/audit_approved_provider_scope.py`

- [ ] **Step 1: Add audit summary helper**

Emit implemented, not implemented, capability missing, request missing, and selector missing categories.

- [ ] **Step 2: Run audit command**

Run:

```bash
uv run python scripts/validation/audit_approved_provider_scope.py
```

Expected: JSON summary with no missing implemented scope.

### Task 4: Record Evidence

**Files:**
- Modify: `memory/2026-06-09.md`

- [ ] **Step 1: Append concise evidence**

Record changed files, tests, and remaining explicitly declared non-implemented source modules.

- [ ] **Step 2: Final verification**

Run:

```bash
git diff --check
uv run pytest tests/contracts/test_approved_provider_scope.py tests/contracts/test_provider_capabilities.py tests/unit/data_gateway/test_provider_selector.py tests/unit/reports/test_data_pack_bridge.py -q
```

Expected: pass.
