# Select Crypto Market Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 `/select` 流程里增加 `CRYPTO` 市场入口：`/select` 和 `/select 1` 仍为 A股，`/select 2` 和 `/select CRYPTO` 走加密市场，读取已完成的加密 Parquet 列式历史仓库，生成 approved top 20 candidate cache，并复用现有 selection worker 流程。

**Architecture:** `claw-trade` 继续拥有命令解析、数据作业、候选缓存、workflow dispatch 和 report handoff；OpenClaw 只运行已有 selection worker turn。CRYPTO 历史行情只能通过 data gateway seed overlay 只读读取 Parquet 列式仓库，Mongo 只作为 seed catalog/evidence/attempt/manifest 索引，不作为历史 normalized rows 存储。

**Tech Stack:** Python, pytest, `SelectionController`, `SelectionDataJob`, data gateway seed overlay, `NormalizedColumnarWarehouse`, candidate cache, existing web selection state.

---

## Material Assumptions

- 设计文档是 [docs/加密市场选币设计.md](/home/frank/src/claw-trade/docs/加密市场选币设计.md)。
- 加密历史 normalized rows 已在 Parquet 列式仓库，默认 seed catalog 库为 `claw_trade_crypto_history_usdt_20260608`。
- 第一版只支持 spot USDT 日线，不新增下载器、导入器、futures、1h 因子或外部付费数据源。
- `/select 2 refresh` 是显式刷新；现有自动刷新仍只服务 `CN_A`，除非后续另行批准。
- Python 只生成确定性特征和候选缓存，不写投资理由、最终推荐或 worker 结论。
- 本计划不修改 `third_party/openclaw`，因此不需要 OpenClaw build/restart/provider payload 证明。

## Success Criteria

- `/select`、`/select 1`、`/select CN_A`、`/select A`、`/select A股` 解析为 `CN_A`。
- `/select 2`、`/select CRYPTO`、`/select crypto`、`/select 加密` 解析为 `CRYPTO`。
- `/select 1 refresh` 和 `/select 2 refresh` 保留各自市场并设置 `force_refresh=True`。
- `CRYPTO` 数据计划不再返回 `target_design / mongo_missing`。
- CRYPTO 小型日线 fixture 能跑通 `SelectionDataJob`，并产生 approved candidate cache。
- Mongo 只保存 seed catalog/evidence/attempt/manifest；任何 CRYPTO normalized row 读取都来自 Parquet columnar seed overlay。
- CRYPTO fixture 证明 `return_20d`、`return_60d`、`return_120d`、`rps20`、`rps60`、`rps120` 都由日线派生，不靠预填字段。
- CRYPTO candidate cache 和 data-job evidence 使用 `crypto.selection_strategy.v1`，不泄漏 A股策略、A股字段、A股专属中文词或估值字段。
- selection workers 只消费 approved CRYPTO top 20 candidate cache；pending/unapproved cache 不能触发 worker；用户确认前不能自动进入 `/report`。
- seed catalog、Parquet 文件、spot USDT rows、历史覆盖不足的负例返回具体原因，不回退到旧 blanket `mongo_missing`。
- focused pytest 通过后，必须做 UI/聊天入口 smoke；如果不用 MCP-controlled real Chrome，必须先取得人明确批准替代验收。

## Implementation Completion Checklist

Every implementation or verification sub-agent/task must:

- [ ] Read `AGENTS.md`, the design doc, and this plan before editing.
- [ ] Avoid reverting unrelated dirty worktree changes.
- [ ] Append `memory/YYYY-MM-DD.md` after completing assigned work.
- [ ] Report changed files, commands, exit codes, key output, expected outcome, deviation status, real implementation status, mock/stub/fake/fallback status, and assigned-scope coverage.
- [ ] Stop instead of adding new runtime guards, fake data, hidden fallbacks, or Python-authored investment conclusions.

## File Map

- `src/claw_trade/selection/controller.py`：扩展 `/select` 命令解析。
- `src/claw_trade/selection/models.py`：只在确有缺口时补充 CRYPTO 状态或 evidence 字段；优先复用现有 enum。
- `src/claw_trade/selection/strategy_config.py`：新增 `crypto.selection_strategy.v1` 配置和通用 loader。
- `src/claw_trade/selection/features.py`：让 CRYPTO 从 OHLCV 日线派生收益、RPS、流动性、波动和回撤字段；避免 A股策略信号泄漏。
- `src/claw_trade/selection/data_job.py`：按 market 写入 strategy/weight evidence，移除 CRYPTO blanket unsupported path。
- `src/claw_trade/selection/store.py`：停止把所有 CRYPTO completed cache 判为历史缺失。
- `src/claw_trade/selection/scheduler.py`：允许显式 CRYPTO refresh 调度。
- `src/claw_trade/selection/refresh.py`：让 CRYPTO refresh 不依赖 A股交易日解析。
- `src/claw_trade/selection/confirmation.py`、`src/claw_trade/selection/report_handoff.py`：确认后 handoff 使用候选所属市场，不硬编码 `CN_A`。
- `src/claw_trade/data_gateway/selection_api.py`：按 market 分发 CN_A 和 CRYPTO selection batch。
- `src/claw_trade/data_gateway/_crypto_selection_batch.py`：新增最小 CRYPTO seed overlay 读取与特征行构建。
- `src/claw_trade/web/state.py`：注入通用策略 loader / config-ref resolver / data gateway facade。
- `tests/contracts/test_selection_command_contract.py`：命令合同。
- `tests/integration/selection/test_select_command_chat_flow.py`：聊天入口 request evidence 和无缓存响应。
- `tests/integration/selection/test_data_job_pipeline.py`：数据计划、数据作业、候选缓存和负例。
- `tests/unit/selection/test_data_need_refresh_strategy_and_trade_date.py`：策略配置和刷新 trade-date 解析。
- `tests/unit/selection/test_candidate_cache_contract.py`：CRYPTO cache 反泄漏断言。

## Tasks

### 1. Command Contract

Write tests first:

- [ ] Add parser/contract tests for:
  - `/select` -> `CN_A`
  - `/select 1` -> `CN_A`
  - `/select CN_A`、`/select A`、`/select A股` -> `CN_A`
  - `/select 2` -> `CRYPTO`
  - `/select CRYPTO`、`/select crypto`、`/select 加密` -> `CRYPTO`
  - `/select refresh`、`/select 1 refresh`、`/select 2 refresh`、`/select CRYPTO refresh`
  - bare `select 1` / `select 2` does not start selection
- [ ] Implement a small token parser in `SelectionController._parse_select_request`.
- [ ] Keep default `/select` as `CN_A`.
- [ ] Reject unsupported market tokens with the existing invalid-command path; do not silently fall back.

Verify:

```bash
uv run pytest tests/contracts/test_selection_command_contract.py -q
uv run pytest tests/integration/selection/test_select_command_chat_flow.py -q
```

Expected result: command contract passes; existing `/select` behavior remains unchanged.

Commit suggestion:

```bash
git add src/claw_trade/selection/controller.py tests/contracts/test_selection_command_contract.py tests/integration/selection/test_select_command_chat_flow.py
git commit -m "feat: parse crypto select commands"
```

### 2. Strategy Config And Derived Features

Write tests first:

- [ ] Add tests proving `CRYPTO` resolves to `crypto.selection_strategy.v1`.
- [ ] Add a tiny OHLCV fixture proving 20/60/120-day returns are computed from rows.
- [ ] Add RPS tests for 20/60/120 windows across at least 3 symbols.
- [ ] Add tests proving CRYPTO features do not require A股-only signals such as limit-up/down, private placement, Sequoia, or MyHub fields.

Implement:

- [ ] Add `CRYPTO_SELECTION_STRATEGY_CONFIG_REF`, `CRYPTO_SELECTION_V1_STRATEGY_CONFIG_VERSION`, and a matching weight/version constant.
- [ ] Add a generic `load_selection_strategy_config_ref(market, profile)` and `load_selection_strategy(config_ref)` while preserving existing CN_A functions for compatibility.
- [ ] Add CRYPTO-specific strategy weights using only approved CRYPTO fields: returns, RPS, quote volume/liquidity, volatility/range, drawdown/data-gap penalty.
- [ ] Extend feature derivation to attach `rps20` as well as existing RPS windows.
- [ ] Branch CRYPTO feature snapshot construction away from A股-only strategy signals.

Verify:

```bash
uv run pytest tests/unit/selection/test_data_need_refresh_strategy_and_trade_date.py -q
```

Expected result: CN_A strategy tests still pass; CRYPTO strategy and derived feature tests pass without prefilled strategy signals.

Commit suggestion:

```bash
git add src/claw_trade/selection/strategy_config.py src/claw_trade/selection/features.py tests/unit/selection/test_data_need_refresh_strategy_and_trade_date.py
git commit -m "feat: add crypto selection strategy config"
```

### 3. CRYPTO Data Plan And Data-Job Evidence

Write tests first:

- [ ] Replace old assertions that CRYPTO is `target_design / mongo_missing`.
- [ ] Add tests proving CRYPTO plan is supported and calls the provider/data gateway facade.
- [ ] Add tests proving data-job evidence records `crypto.selection_strategy.v1` for CRYPTO and keeps `cn_a.selection_strategy.v1` for CN_A.

Implement:

- [ ] Update `build_selection_data_plan` so CRYPTO is supported and has concrete seed/columnar failure gaps only when data gateway evidence says so.
- [ ] Make data-job evidence choose strategy config/version by `plan.market`.
- [ ] Make candidate cache metadata/evidence choose strategy and weight versions by `plan.market`.
- [ ] Preserve CN_A code path and existing status values.

Verify:

```bash
uv run pytest tests/integration/selection/test_data_job_pipeline.py -q
```

Expected result: CRYPTO no longer fails before provider fetch with `mongo_missing`; evidence uses the CRYPTO strategy id.

Commit suggestion:

```bash
git add src/claw_trade/selection/data_job.py src/claw_trade/selection/store.py tests/integration/selection/test_data_job_pipeline.py
git commit -m "feat: support crypto select data plan"
```

### 4. Read CRYPTO Seed History From Columnar Warehouse

Write tests first:

- [ ] Build a fixture seed catalog plus Parquet columnar root in a temp directory.
- [ ] Test spot USDT daily rows are read through the data gateway path, not through `SelectionColumnarWarehouse`.
- [ ] Test fixture wiring does not set `DATA_GATEWAY_COLUMNAR_ROOT` to `data/crypto-history-full/normalized-columnar-usdt-only`; use a temp runtime root plus seed overlay-equivalent fixture.
- [ ] Test missing seed catalog returns a seed catalog error.
- [ ] Test missing/damaged Parquet returns a columnar/Parquet error.
- [ ] Test no spot USDT rows returns a concrete no-rows error.
- [ ] Test all symbols with insufficient history return a concrete coverage error.

Implement:

- [ ] Create `src/claw_trade/data_gateway/_crypto_selection_batch.py`.
- [ ] Read seed catalog through existing data gateway repository/overlay mechanisms.
- [ ] Read daily OHLCV rows from `NormalizedColumnarWarehouse` / equivalent project API.
- [ ] Restrict universe to spot USDT daily rows.
- [ ] Build normalized input rows with ticker/base symbol, display name, close, volume, quote volume, source lineage, and history arrays.
- [ ] Return a `SelectionDataNeedAudit` shape compatible with `SelectionDataJob`.
- [ ] Do not write factory seed Mongo catalog or seed Parquet files.
- [ ] Do not make runtime success depend on pointing runtime `DATA_GATEWAY_COLUMNAR_ROOT` at the factory seed path.

Verify:

```bash
uv run pytest tests/integration/selection/test_data_job_pipeline.py -q
uv run pytest tests/contracts/test_crypto_prepackaged_data_contract.py -q
```

Expected result: CRYPTO fixture produces approved candidate cache; negative cases name seed/catalog/Parquet/row/coverage causes.

Commit suggestion:

```bash
git add src/claw_trade/data_gateway/_crypto_selection_batch.py src/claw_trade/data_gateway/selection_api.py tests/integration/selection/test_data_job_pipeline.py
git commit -m "feat: read crypto select history from columnar seed"
```

### 5. Refresh, Scheduler, And Web Wiring

Write tests first:

- [ ] Add tests for `/select 2 refresh` scheduling a CRYPTO data job.
- [ ] Add tests proving CRYPTO refresh does not call the A股 closed-trade-date resolver.
- [ ] Add tests proving `web/state.py` injects the generic strategy loader/resolver.

Implement:

- [ ] Allow `SelectionMarket.CRYPTO` and `SelectionProfile.CRYPTO` in explicit scheduler paths.
- [ ] Keep automatic refresh CN_A-only.
- [ ] Resolve CRYPTO refresh date as explicit date if provided, otherwise use the current UTC date or existing project market-date helper if one already exists.
- [ ] Wire `SelectionDataRefreshService` and `SelectionDataJob` to generic strategy loaders in `web/state.py`.

Verify:

```bash
uv run pytest tests/unit/selection/test_data_need_refresh_strategy_and_trade_date.py -q
uv run pytest tests/integration/selection/test_select_command_chat_flow.py -q
```

Expected result: explicit CRYPTO refresh can schedule; CN_A automatic refresh is unchanged.

Commit suggestion:

```bash
git add src/claw_trade/selection/scheduler.py src/claw_trade/selection/refresh.py src/claw_trade/web/state.py tests/unit/selection/test_data_need_refresh_strategy_and_trade_date.py tests/integration/selection/test_select_command_chat_flow.py
git commit -m "feat: wire crypto select refresh"
```

### 6. Candidate Cache, Confirmation, And Report Handoff

Write tests first:

- [ ] Add CRYPTO candidate cache contract tests for market/profile/source lineage.
- [ ] Add approval gate tests proving pending/unapproved CRYPTO cache does not trigger selection workers.
- [ ] Add worker-dispatch tests proving approved CRYPTO top 20 is the material consumed by the existing selection worker flow.
- [ ] Add command-flow tests proving CRYPTO selection does not auto-run `/report` before user confirmation.
- [ ] Add anti-leak tests for body, summary, manifest, and data-job evidence:
  - forbidden strategy ids: `cn_a.selection_strategy.v1`, `cn_a.selection_weights.v1`
  - forbidden A股 fields/labels: `myhhub/stock`, `Sequoia-X`, `limit_up`, `limit_down`, `private_placement`, `涨停`, `跌停`, `定增`
  - forbidden valuation standalone keys/tokens/labels: `pe`, `pb`, `roe`, `PE`, `PB`, `ROE`, `市盈率`, `市净率`, `净资产收益率`
  - use structured key/token/label matching, not substring scan; normal fields such as `open`, `type`, and `scope` must remain allowed.
- [ ] Add confirmation/handoff tests proving selected CRYPTO candidate is handed to report flow as CRYPTO, not hardcoded CN_A.

Implement:

- [ ] Update candidate cache metadata and evidence to carry CRYPTO market/profile consistently.
- [ ] Update `store._validate_warehouse_evidence_for_select` so completed CRYPTO cache is validated by real evidence, not blanket history-missing status.
- [ ] Ensure worker dispatch reads only approved CRYPTO top 20 cache material.
- [ ] Update confirmation identity resolution to use candidate/request market.
- [ ] Update report handoff to preserve CRYPTO market when user confirms a CRYPTO candidate.

Verify:

```bash
uv run pytest tests/unit/selection/test_candidate_cache_contract.py -q
uv run pytest tests/integration/selection/test_select_command_chat_flow.py -q
```

Expected result: CRYPTO cache is market-clean and confirmation does not convert it to CN_A.

Commit suggestion:

```bash
git add src/claw_trade/selection/store.py src/claw_trade/selection/confirmation.py src/claw_trade/selection/report_handoff.py tests/unit/selection/test_candidate_cache_contract.py tests/integration/selection/test_select_command_chat_flow.py
git commit -m "feat: preserve crypto select handoff"
```

### 7. Final Acceptance

Run focused regression:

```bash
uv run pytest tests/contracts/test_selection_command_contract.py tests/unit/selection/test_data_need_refresh_strategy_and_trade_date.py tests/unit/selection/test_candidate_cache_contract.py tests/integration/selection/test_data_job_pipeline.py tests/integration/selection/test_select_command_chat_flow.py tests/contracts/test_crypto_prepackaged_data_contract.py -q
```

Expected result: all focused tests pass.

After focused tests pass, run a UI/chat-entry smoke with MCP-controlled real Chrome unless the human explicitly approves a different final acceptance method.

If the smoke requires fresh runtime/provider execution, first follow the live runtime preflight gate:

- [ ] Read fixed runtime guidance in `memory/`.
- [ ] Print the runtime profile before running the smoke:
  - start with `scripts/start-control-runtime.sh`
  - repo `uv` environment
  - OpenViking on `1933`
  - OpenClaw gateway on `18789`
  - no invest sidecar
  - OpenViking source config from `~/.openviking/ov.conf`
  - generated runtime OpenViking config at `.runtime/dev-services/openviking/ov.conf`
  - runtime config/data/cache under `.runtime/dev-services`
- [ ] Check existing runtime first and reuse it if `runtime.env`, `1933/health` or `1933/healthz`, and `18789/health` all pass.
- [ ] If restart is required, print and pass the full preflight table:
  - `CLAW_TRADE_OPENVIKING_MCP_MODULE` and `CLAW_TRADE_OPENVIKING_MCP_CWD` are unset.
  - `CLAW_TRADE_OPENVIKING_SERVER_BIN` and `CLAW_TRADE_OPENVIKING_SERVER_CWD` are unset unless external server mode was explicitly approved.
  - `OPENVIKING_CONFIG_FILE` points to `.runtime/dev-services/openviking/ov.conf`.
  - `OPENVIKING_DATA_DIR` points to `.runtime/dev-services/openviking/data`.
  - `CLAW_TRADE_OPENVIKING_MCP_STARTED=0` after `runtime.env` is generated.
  - OpenViking `1933` health and OpenClaw gateway `18789` health both pass.
- [ ] If a clean runtime command is needed, use fixed script command mode, not manual service startup:

```bash
scripts/start-control-runtime.sh -- <existing-or-new-focused-select-crypto-smoke-command>
```

UI smoke acceptance:

- [ ] Send `/select 2` through the real chat UI.
- [ ] Verify request evidence records `SelectionMarket.CRYPTO`.
- [ ] If no approved CRYPTO cache exists, verify the UI returns a concrete data/cache unavailable state, not a fake completed selection.
- [ ] If an approved CRYPTO cache exists or is generated by the smoke, verify workers consume approved top 20 material.
- [ ] Verify the UI does not auto-run `/report` before user confirmation.
- [ ] Confirm one CRYPTO candidate and verify report handoff preserves CRYPTO market.

Expected result: runtime reads seed overlay, produces or reports a concrete CRYPTO cache/data status, and the real UI command flow preserves CRYPTO through confirmation. Do not invent a fake success path.

Final commit suggestion:

```bash
git status --short
git log --oneline --max-count=5
```

Expected result: only planned files changed in this branch; unrelated pre-existing dirty files are not reverted.

## Stop Conditions

- 实现需要 Python 编写 CRYPTO 投资理由或最终推荐。
- 实现需要把 A股阈值、A股专属字段或 A股交易规则套到 CRYPTO。
- `/select` 期间需要写 factory seed Mongo catalog 或 factory seed Parquet。
- 需要新增 runtime guard、hard gate、output validator 或表达限制。
- 需要修改 `third_party/openclaw`。
- 发现历史数据只能从 Mongo normalized rows 读取，而不是从 Parquet 列式仓库读取。
- 实现需要把 runtime `DATA_GATEWAY_COLUMNAR_ROOT` 指向 `data/crypto-history-full/normalized-columnar-usdt-only` 才能成功。
- 候选缓存无法保留 CRYPTO market/profile 到确认和 report handoff。
