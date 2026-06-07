# /report Workflow 参数化与多轮辩论控制设计

## 结论

当前问题不能按“把硬编码的 1 改成参数”处理。`max_debate_rounds` 和
`max_risk_discuss_rounds` 会改变 workflow 控制、worker 调度、artifact 身份、
prompt 上游材料组织、最终报告证据归档。

推荐方案是：

- `.env.local` 作为当前 CLI/dev 环境的统一默认配置入口。
- CLI 只继续承载每次报告不同的业务输入，例如 ticker、company、日期区间。
- workflow 参数先由 `.env.local` 解析成 typed settings，再写进 `RunRequest`。
- controller 只读 `RunRequest`，不直接读环境变量。
- 未来 UI 使用同一套 settings schema 生成 `RunRequest`，但生产环境不应直接编辑
  `.env.local` 文件。

## 当前已确认事实

### 一轮硬编码位置

`src/claw_trade/workflow/controller.py` 当前固定投资辩论顺序：

```text
bull_researcher -> bear_researcher -> research_manager
```

当前固定风险辩论顺序：

```text
risk_challenger -> risk_guardian -> risk_moderator -> portfolio_manager
```

判断方式是“某 worker 在某 stage 是否已有 approved material”。这天然只能表达
“每个 worker 说一次”，不能表达“同一个 worker 第 N 轮再次发言”。

### Manifest 当前不允许同 worker 多次写入

`src/claw_trade/artifacts/manifest.py` 当前 approved material 唯一键是：

```text
(run_id, stage, worker_id)
```

所以第二轮 `bull_researcher` 或第二轮 `risk_challenger` 写入时，会被当作重复
worker 拦住。

### Prompt 材料当前会折叠多轮历史

`src/claw_trade/workflow/runner.py` 当前用：

```text
(worker_id, stage) -> text
```

组织上游报告正文。这个结构只能保存每个 worker 的最后一份或唯一一份材料，无法
保留多轮辩论的顺序历史。

### RunRequest 当前回读只覆盖旧字段

`src/claw_trade/workflow/store.py::_run_request_from_dict` 当前只从 `request.json`
回读 ticker、profile、日期、stop point、target worker 等旧字段。新增 workflow
轮数字段后，如果这里漏改，fresh run 创建时可能是 `2/2`，但下一次 `load_state`
就会回落到 dataclass 默认值，导致同一 run 的调度语义漂移。

### Exporter 当前按 worker 去重

`src/claw_trade/reports/exporter.py` 当前导出链路使用 `material_by_worker` 和
`_report_text_by_worker` 这类结构。多轮后，同一个 worker 会有多份 approved
material；如果 exporter 继续按 worker_id 建 dict，最终报告会天然丢掉前面轮次。

### 原版语义

TradingAgents / TradingAgents-CN 的语义是：

```text
investment_debate 总发言数 = 2 * max_debate_rounds
发言顺序 = Bull, Bear, Bull, Bear, ...

risk_debate 总发言数 = 3 * max_risk_discuss_rounds
发言顺序 = Aggressive, Conservative, Neutral, Aggressive, ...
```

默认值是 1，但参数可以增加。

## 设计目标

1. 保持 claw-trade 拥有 workflow 调度权，LLM 不决定下一步叫谁。
2. 保持 OpenClaw 只负责单 agent turn，不把 12-worker DAG 迁到 OpenClaw。
3. 支持投资辩论和风险辩论多轮。
4. 每一轮 worker 输出都有独立 call、独立 approved material、独立证据。
5. 下游 worker 看到完整已批准历史，不由 Python 摘要、改写或压缩业务内容。
6. CLI 当前可用，未来 UI 设置模块可以复用同一套配置 schema。
7. 默认行为保持当前一轮，避免破坏已有 `/report` 路径。

## 非目标

- 不允许用 fallback prompt 或默认 profile 掩盖配置缺失。
- 不允许让 Python 改写 PM 结论、研究经理判断或辩论内容。
- 不允许把 worker 是否启用做成 env 开关；12 个主 worker 仍是报告链路硬要求。
- 不允许用“只保留最后一轮”简化多轮证据。
- 不把未来生产 UI 设计成直接写 `.env.local` 文件。

## 方案取舍

### 方案 A：只加 CLI 参数

做法：给 `run_control.py` 加 `--max-debate-rounds` 和
`--max-risk-discuss-rounds`。

问题：

- 不利于未来 UI 设置复用。
- 容易形成 CLI 一套、UI 一套、测试一套。
- 没有解决 artifact 多轮身份和 prompt 历史问题。

结论：不推荐。

### 方案 B：controller 直接读环境变量

做法：controller 调度时直接读 `os.environ`。

问题：

- workflow 状态不可复现。`request.json` 里看不到当时到底跑了几轮。
- 同一个 run 恢复时，如果环境变量变了，调度结果会变。
- 测试难写，UI 也难对接。

结论：明确不推荐。

### 方案 C：env.local -> typed settings -> RunRequest

做法：

1. 启动脚本加载 `.env.local`。
2. CLI 从环境变量解析 report workflow 默认设置。
3. CLI 把解析后的设置写进 `RunRequest`。
4. controller 只读 `RunRequest`。
5. 未来 UI 也生成同样的 `RunRequest`。

优点：

- 当前 CLI 和未来 UI 使用同一套参数模型。
- 每个 run 的 workflow 参数进入 `request.json`，可审计、可复现。
- controller 不依赖外部可变环境。
- 默认仍是一轮，不破坏现有行为。

结论：推荐。

## 配置模型设计

新增一个 typed settings 层，建议文件：

```text
src/claw_trade/config/report_workflow_settings.py
```

职责：

- 从环境变量读取 workflow 默认设置。
- 做类型转换和范围校验。
- 给 CLI 和未来 UI/schema 共用字段定义。
- 不负责调度，不负责构造 prompt，不负责读取 OpenViking。

建议核心结构：

```text
ReportWorkflowSettings
  max_debate_rounds
  max_risk_discuss_rounds
  max_rounds_hard_limit
  frontline_execution_mode
  run_dir
  default_profile
  default_market
  default_currency
  default_currency_symbol
```

### env.local 参数

推荐新增这些 env key：

```dotenv
# report workflow defaults
CLAW_TRADE_REPORT_MAX_DEBATE_ROUNDS=1
CLAW_TRADE_REPORT_MAX_RISK_DISCUSS_ROUNDS=1
CLAW_TRADE_REPORT_MAX_ROUNDS_HARD_LIMIT=3
CLAW_TRADE_REPORT_FRONTLINE_EXECUTION_MODE=serial
CLAW_TRADE_REPORT_RUN_DIR=runs

# optional CLI defaults, not product authority
CLAW_TRADE_REPORT_DEFAULT_PROFILE=CN_A
CLAW_TRADE_REPORT_DEFAULT_MARKET=CN_A
CLAW_TRADE_REPORT_DEFAULT_CURRENCY=CNY
CLAW_TRADE_REPORT_DEFAULT_CURRENCY_SYMBOL=¥
```

说明：

- `CLAW_TRADE_REPORT_MAX_DEBATE_ROUNDS`：投资辩论轮数。默认 1。
- `CLAW_TRADE_REPORT_MAX_RISK_DISCUSS_ROUNDS`：风险辩论轮数。默认 1。
- `CLAW_TRADE_REPORT_MAX_ROUNDS_HARD_LIMIT`：本地安全上限，默认 3；env 只能收紧到 1/2/3，不能提高到 4 或 5。
- `CLAW_TRADE_REPORT_FRONTLINE_EXECUTION_MODE`：允许 `serial` 或 `parallel`；默认 `serial`，避免一次性拉起多个 OpenClaw worker/gateway 进程导致内存放大。`parallel` 只能作为人工显式提速选项。
- `CLAW_TRADE_REPORT_RUN_DIR`：默认运行目录。
- `CLAW_TRADE_REPORT_DEFAULT_*`：只是 CLI 默认值；ticker、company、日期仍建议由每次请求传入。

### 不建议放进 env.local 的内容

这些是每次报告的业务输入，不应变成全局环境配置：

```text
ticker
company_name
current_date
start_date
end_date
```

原因很简单：未来 UI 里这些是用户本次表单输入，不是系统设置。

## RunRequest 扩展

`src/claw_trade/workflow/models.py` 的 `RunRequest` 建议新增：

```text
max_debate_rounds: int = 1
max_risk_discuss_rounds: int = 1
frontline_execution_mode: str = "serial"
```

其中：

- `max_debate_rounds` 只影响 investment debate 的 turn 数。
- `max_risk_discuss_rounds` 只影响 risk debate 的 turn 数。
- `frontline_execution_mode` 控制前线四个 worker 的调度方式。默认 `serial`，逐个叫醒 worker；`parallel` 会并发叫醒前线 worker，速度更快但会明显增加内存。

controller 不读 `.env.local`，只读 `state.request`。

### 持久化回读一致性

新增 `RunRequest` 字段时，必须同步修改：

```text
src/claw_trade/workflow/store.py::_run_request_from_dict
```

验收要求：

```text
create_run 写入 request.json 后，load_state 再读出来，max_debate_rounds、
max_risk_discuss_rounds、frontline_execution_mode 必须保持原值。
```

否则 controller 会在同一个 run 内看到不同配置，直接破坏 workflow 可复现性。

## 多轮调度设计

### 投资辩论

计算方式：

```text
total_turns = 2 * request.max_debate_rounds
completed_turns = investment_debate 已批准 material 的 turn 数
```

下一位 worker：

```text
worker = ["bull_researcher", "bear_researcher"][completed_turns % 2]
```

完成条件：

```text
completed_turns >= total_turns
```

### 风险辩论

计算方式：

```text
total_turns = 3 * request.max_risk_discuss_rounds
completed_turns = risk_debate 已批准 material 的 turn 数
```

下一位 worker：

```text
worker = [
  "risk_challenger",
  "risk_guardian",
  "risk_moderator",
][completed_turns % 3]
```

完成条件：

```text
completed_turns >= total_turns
```

### 并发边界

frontline 默认串行，避免同时拉起多个 OpenClaw gateway 进程造成内存放大。`parallel`
只作为人工显式提速选项。

debate/risk debate 不并行，因为后一个发言必须看到前一个发言，第二轮必须看到第一轮
完整历史。

## Worker turn 身份设计

当前 `StageBatch` 只有 `worker_ids`，表达不了“同一个 worker 的第二轮”。推荐引入：

```text
WorkerDispatch
  worker_id
  stage
  turn_index
  round_index
  role_turn_index
```

含义：

- `turn_index`：当前 stage 内第几个发言，从 0 开始。
- `round_index`：第几轮，从 1 开始。
- `role_turn_index`：这个 worker 自己第几次发言，从 1 开始。

示例：

```text
investment_debate:
  turn 0, round 1, bull role_turn 1
  turn 1, round 1, bear role_turn 1
  turn 2, round 2, bull role_turn 2
  turn 3, round 2, bear role_turn 2

risk_debate:
  turn 0, round 1, challenger role_turn 1
  turn 1, round 1, guardian role_turn 1
  turn 2, round 1, moderator role_turn 1
  turn 3, round 2, challenger role_turn 2
```

`WorkerCall`、`WorkerResult`、`FailureRecord`、`ApprovedMaterial`、`MaterialReadRef`
都应带上 turn 身份。这样证据、失败、prompt、最终报告都能追到具体轮次。

## Manifest 设计

当前唯一键：

```text
(run_id, stage, worker_id)
```

应改为：

```text
(run_id, stage, worker_id, turn_index)
```

同时保留 call 唯一：

```text
(run_id, call_id)
```

新增查询能力：

```text
materials_for_stage(run_id, stage) -> 按 turn_index 排序
materials_for_worker(run_id, stage, worker_id) -> 按 turn_index 排序
latest_material_for_worker(run_id, stage, worker_id)
debate_history_for_worker_call(run_id, stage, next_turn_index)
```

关键点：

- 下游读取 investment debate 时拿完整有序列表，不按 worker 去重。
- 下游读取 risk debate 时拿完整有序列表，不按 worker 去重。
- 非 debate 阶段仍可使用唯一 worker 语义，但内部最好统一成 turn 0。
- `for_worker_call` 和 `capabilities_for_worker_call` 必须返回稳定顺序：先固定上游
  stage 顺序，再按 `turn_index` 升序。`runner` 当前会逐项比较
  `call.upstream_materials` 与 manifest 返回值，顺序不稳定会造成“材料正确但
  guard 失败”。
- `validate_openviking_runtime_reads` 依赖同一 capability 集；新增 turn 身份后，
  capability 仍必须精确绑定 material_id、L1 URI、hash 和 turn 对应的 call。

## Prompt 材料设计

当前 `material_texts` 是：

```text
dict[(worker_id, stage), text]
```

应改为 ordered material list：

```text
PromptMaterialText
  worker_id
  stage
  turn_index
  round_index
  role_turn_index
  text
```

然后构造：

```text
investment_debate_history = 所有已批准 investment debate turn 的完整正文，按 turn_index 拼接
risk_debate_history = 所有已批准 risk debate turn 的完整正文，按 turn_index 拼接
latest_bull_argument = bull_researcher 最近一次完整正文
latest_bear_argument = bear_researcher 最近一次完整正文
latest_risky_argument = risk_challenger 最近一次完整正文
latest_safe_argument = risk_guardian 最近一次完整正文
latest_neutral_argument = risk_moderator 最近一次完整正文
current_response = 当前 worker 需要回应的上一位发言人完整正文
```

这里不允许 Python 摘要。Python 只做有序拼接和变量填充。

Prompt 材料边界必须按 profile 的原版基线确定。审计完整性可以保留在 manifest/evidence 中，但不能把更多上游材料塞进模型可见 prompt 来替代原版 turn。若原版 worker 只看 debate history，claw-trade 也只能把 debate history 放入该 worker 的 prompt 变量；后台 refs/capabilities 不能成为默认模型可见正文。

## 下游输入规则

### bull_researcher

- 第 1 轮：frontline 四份报告。
- 第 2 轮及以后：frontline 四份报告 + 此前全部 investment debate 历史。
- `current_response` 指向上一位 bear 的完整正文；第 1 轮为空。

### bear_researcher

- 读取 frontline 四份报告 + 此前全部 investment debate 历史。
- `current_response` 指向上一位 bull 的完整正文。

### research_manager

- 按 profile baseline 读取材料。
- CN_A 若基线需要 frontline 四份报告，则读取 frontline 四份报告 + 全部 investment debate 历史。
- US 原版 TradingAgents research manager 只裁决 bull/bear debate 时，只读取全部 investment debate 历史，不额外注入 frontline 四份报告。
- 不只读最后一轮；也不把审计材料完整性当成扩大模型可见材料范围的理由。

### risk_challenger / risk_guardian / risk_moderator

- 均读取 trader decision。
- 第 2 个风险发言开始读取此前全部 risk debate 历史。
- `current_*_response` 使用各角色最近一次发言。

### portfolio_manager

- 读取 research_manager 输出。
- 读取 trader 输出。
- 读取全部 risk debate 历史。
- PM 仍然是最终投资决策 owner。

### report_polisher

- 读取 PM 输出。
- 读取所有 supporting worker report，包括全部 debate/risk debate turn。
- 只负责报告表达和结构，不改 PM 结论。

## Exporter 多轮保真设计

最终导出不能继续按 `worker_id` 折叠材料。

必须改造：

```text
src/claw_trade/reports/exporter.py
```

重点：

- `load_report_materials` 不再构造单值 `material_by_worker` 作为最终 ordered
  materials 来源。
- `_ordered_materials` 应按报告链路顺序排序：frontline 固定顺序，investment
  debate 按 `turn_index`，research/trader，risk debate 按 `turn_index`，PM，
  report_polisher。
- `_report_text_by_worker` 不能再返回 `dict[str, str]` 用于 debate/risk debate；
  应改成“按 worker 和 turn 保序的集合”，或提供 `report_texts_for_worker`。
- PM 和 report_polisher 仍是单材料语义，必须显式校验只有一份有效 material。
- fallback 渲染模式必须输出全部 debate/risk debate turn，不能只输出最后一轮。
- export claim id 需要纳入 turn 身份，避免同 worker 多轮 claim id 冲突。

## Evidence 和文件命名

多轮后，汇总导出文件不能继续只用 worker 名做文件名。建议所有轮次化汇总
evidence 文件使用：

```text
{stage}_t{turn_index:02d}_{worker_id}_final_prompt.md
{stage}_t{turn_index:02d}_{worker_id}_llm_back.md
{stage}_t{turn_index:02d}_{worker_id}_report.md
```

示例：

```text
investment_debate_t00_bull_researcher_report.md
investment_debate_t01_bear_researcher_report.md
investment_debate_t02_bull_researcher_report.md
investment_debate_t03_bear_researcher_report.md
```

兼容策略：

- 运行时原始证据仍保存在每个 call 独立目录下，不靠汇总文件名区分。
- 一轮模式下可以继续额外导出旧汇总文件名，方便现有对比证据。
- 多轮模式必须使用带 turn 的汇总文件名，禁止覆盖。

## CLI 设计

当前 CLI 仍保留这些每次报告输入：

```text
--ticker
--company-name
--market
--profile
--currency
--currency-symbol
--current-date
--start-date
--end-date
```

workflow 设置默认从 `.env.local` 来，不建议先暴露同名 CLI override。原因：

- 用户已经明确希望设置集中到 `.env.local`。
- 未来 UI 设置模块更容易对齐同一套配置。
- 避免 CLI flag、env、UI 三处优先级混乱。

如果未来确实需要临时覆盖，应明确优先级：

```text
本次 UI/CLI 显式输入 > 环境变量默认值 > 代码默认值
```

并且最终值必须写入 `request.json`。

## UI 未来设计

未来 UI 不应该直接编辑生产环境 `.env.local`，而应该有一个设置模型：

```text
ReportWorkflowSettings
  max_debate_rounds
  max_risk_discuss_rounds
  frontline_execution_mode
  default_profile
  default_market
  default_currency
```

UI 保存设置后，发起 `/report` 时生成：

```text
RunRequest = 用户本次输入 + ReportWorkflowSettings 当前值
```

这样 CLI/dev 和 UI/product 使用同一套字段，但存储介质可以不同：

```text
dev/CLI: .env.local
product/UI: DB 或配置服务
runtime/controller: RunRequest
```

## 与启动脚本的关系

`scripts/start-control-runtime.sh` 已经加载 `.env.local`。本设计要求：

1. 所有 report workflow 默认值都放进 `.env.local`。
2. 启动脚本只负责把 env 注入进程。
3. Python 配置模块负责解析、校验、报错。
4. controller 不直接读 env。

这样 MongoDB、OpenViking、OpenClaw、数据工具、report workflow 设置都从同一个本地配置文件进入。

## 测试设计

### 单元测试

新增或更新：

```text
tests/unit/test_report_workflow_settings.py
tests/unit/test_workflow_controller.py
tests/unit/test_manifest.py
tests/unit/test_prompt_materials.py
tests/unit/test_workflow_store.py
tests/unit/test_request_builder.py
tests/unit/test_control_runner.py
tests/contracts/test_worker_call_contract.py
tests/contracts/test_runner_batch.py
tests/contracts/test_artifact_flow_guard.py
tests/contracts/test_openviking_runtime_read_guard.py
tests/contracts/test_final_report_exporter.py
```

必须覆盖：

- env 默认缺失时 rounds 为 1。
- rounds 非整数、0、负数、超过 hard limit 时阻断。
- `max_debate_rounds=2` 时调度顺序是 bull、bear、bull、bear。
- `max_risk_discuss_rounds=2` 时调度顺序是 challenger、guardian、moderator、challenger、guardian、moderator。
- 同一 worker 不同 `turn_index` 可以进入 manifest。
- 同一 worker 同一 `turn_index` 仍然被判重复。
- research_manager prompt 收到全部 investment debate 历史。
- portfolio_manager prompt 收到全部 risk debate 历史。
- report_polisher supporting reports 不丢多轮材料。
- `WorkflowStore.create_run -> load_state` 后 rounds 和 execution mode 不丢。
- request_builder 给第二轮同 worker 生成不同 call/material target。
- artifact flow guard 对同一组 ordered refs/caps 通过，对乱序 refs/caps 失败。
- exporter fallback 渲染和 report_polisher 输入都保留全部轮次。
- export claim id 在多轮同 worker 下不冲突。

### focused live 验证

实现后需要跑一次：

```text
CLAW_TRADE_REPORT_MAX_DEBATE_ROUNDS=2
CLAW_TRADE_REPORT_MAX_RISK_DISCUSS_ROUNDS=2
scripts/start-control-runtime.sh -- uv run python -m claw_trade.cli.run_control ...
```

验收标准：

- investment debate 有 4 个 worker turn。
- risk debate 有 6 个 worker turn。
- 每个 turn 都有独立 call 目录。
- approved manifest 中不存在覆盖。
- downstream provider final prompt 包含完整历史。
- final report 能体现多轮争论，不只引用最后一轮。
- OpenViking/Mongo/OpenClaw 无启动失败、写入冲突、隐藏重试导致的证据缺口。

## 实施阶段

### Phase 1：配置入口

- 新增 `ReportWorkflowSettings`。
- CLI 从 env 解析 workflow 默认值。
- `RunRequest` 写入 rounds 和 frontline execution mode。
- `request.json` 可审计最终值。
- `WorkflowStore._run_request_from_dict` 回读新增字段，并用测试锁定 reload 不丢值。

### Phase 2：turn 身份

- 新增 `WorkerDispatch` 或等价结构。
- `StageBatch` 从 `worker_ids` 升级为 dispatch list，或保持兼容字段但内部使用 dispatch。
- `WorkerCall`、`WorkerResult`、`ApprovedMaterial`、`MaterialReadRef` 增加 turn 字段。
- `FailureRecord`、`StageBatchResult`、call/result/stage-batch JSON 回读也要能保留或推导
  turn 身份。
- 旧 run 的 `call.json`、`result.json`、manifest material、stage-batch JSON 缺 turn 字段时，
  反序列化默认 `turn_index=0`、`round_index=1`、`role_turn_index=1`。

### Phase 3：manifest 多轮能力

- 修改 manifest 唯一键。
- 新增按 stage/turn 排序查询。
- 修改 downstream source 选择，不再按 `(worker, stage)` 去重 debate 材料。
- `for_worker_call` / `capabilities_for_worker_call` 必须稳定排序，并与 runtime guard
  比较规则一致。

### Phase 4：controller 调度

- investment debate 改为 count-based deterministic scheduler。
- risk debate 改为 count-based deterministic scheduler。
- 默认 rounds=1 时行为与当前一致。

### Phase 5：prompt 材料和报告导出

- `material_texts` 改为 ordered material list。
- downstream prompt vars 使用完整历史。
- evidence/export 文件命名支持 turn。
- exporter 的 `material_by_worker` / `_report_text_by_worker` 单值结构改为多轮保序结构。
- final report fallback 渲染、claim mapping、PM/report_polisher 读取都必须通过多轮测试。

### Phase 6：验证

- 先跑 focused unit/contract tests。
- 再跑 CN_A `2/2` fresh `/report`。
- 最后检查 provider final prompt、LLM back、approved manifest、final report。

## 风险与反证条件

### token 风险

多轮会显著增加 prompt 长度。由于项目规则禁止 Python 摘要 worker 业务内容，
不能用隐藏摘要解决。如果 `3/3` 超上下文，需要单独设计“由 worker 自己
产出可批准摘要”的显式阶段，不能让 Python 偷偷压缩。

### 证据覆盖风险

如果 evidence 导出仍用旧文件名，多轮会互相覆盖。实现时必须先改 evidence 命名。

### UI 设置误区

`.env.local` 是当前 CLI/dev 的统一入口，不是最终产品 UI 的唯一存储。未来 UI 应复用
settings schema，不应在生产运行时直接改 env 文件。

### 兼容风险

旧 run 的 manifest、`call.json`、`result.json`、`stage-batches/*.json` 都没有 turn 字段。
读取旧证据时必须默认 `turn_index=0`、`round_index=1`、`role_turn_index=1`，否则旧 run
不可读。兼容只用于读取旧证据；新 run 必须写出显式 turn 字段。

## Stop Conditions

遇到以下情况必须停下确认：

- 需要让 Python 摘要或改写多轮辩论内容。
- 需要让 LLM 决定下一位 worker。
- 需要把 worker 启停做成 env 配置。
- 需要放宽 artifact hard gate 或 provider payload capture。
- 多轮 prompt 超上下文，需要引入新的压缩/摘要策略。
- 需要改 OpenClaw 源码且超出单 agent runtime seam。

## 最小验收定义

第一版实现只算完成当且仅当：

```text
默认 .env.local 缺省值仍跑 1/1。
request.json 写入 2/2 后，load_state 回读仍是 2/2。
设置 2/2 后，真实 run 产生 4 个 investment debate turn 和 6 个 risk debate turn。
每个 turn 都有独立 provider final prompt、LLM back、approved L1 report。
research_manager、portfolio_manager、report_polisher 的 provider prompt 可看到完整历史。
exporter 和 final-report.md 没有丢弃前面轮次，也没有被 Python 改写 PM 结论。
```
