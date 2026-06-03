# A股选股实施任务清单

状态：可派发给实现 agent 的实施清单（第一版，CN_A only）  
日期：2026-05-24  
主来源：`docs/A股选股详细设计.md`、`docs/A股选股总体设计.md`

## 0. 口径冻结（本清单内不再作为待确认）

- 第一版只做 A股 `CN_A`。
- 选股算法必须按原项目最新代码等价实现，先审计后落地；不得发明新算法、权重、阈值。
- 默认北京时间 16:00 后台跑；设置里允许用户修改。
- 数据源沿用 data_gateway/provider registry/Mongo/OpenViking 证据链，不走旧直连路径；OpenBB 本体不是目标运行时依赖。
- prompt 按总体/详细设计中的 selection worker 草案落地。
- `/select` 第一版只在聊天输出结果，不做复杂结果页。
- 第一版不做手动补跑/backfill/rerun 的用户入口。
- 禁止任何 `mock/stub/fake/fallback/capture-only/替身 OpenClaw/替身 provider/假 artifact/readback` 作为 runtime/live 验收证据。
- `top20` 是 `/select` worker 评审上限，不是参考项目原生输出，也不是必须凑满 20。实现必须先保存每个来源策略的原始命中列表、命中字段和阈值证据；合并去重后最多取 20 只进入 approved candidate pack。真实候选不足 20 不得补齐，真实候选为 0 不得生成 fake approved pack。

## 1. 任务图

| 任务编号 | 任务名 | 依赖任务 | 是否可并行 |
|---|---|---|---|
| SEL-00 | 原项目算法审计与等价映射 | 无 | 否 |
| SEL-01 | 选择域模型与状态机骨架 | SEL-00 | 否 |
| SEL-02 | selection run store 与 refresh 触发门 | SEL-01 | 否 |
| SEL-03 | 后台确定性 data job（16:00 默认） | SEL-00, SEL-02 | 部分可并行 |
| SEL-04 | candidate pack 与 artifact authority | SEL-03 | 否 |
| SEL-05 | selection tool 与 tool_names 映射 | SEL-04 | 否 |
| SEL-06 | 4 worker agent 配置与 prompt 边界 | SEL-05 | 否 |
| SEL-07 | OpenClaw dispatch（single worker turn） | SEL-06 | 否 |
| SEL-08 | `/select` 控制器与聊天输出 | SEL-02, SEL-07 | 否 |
| SEL-09 | 用户确认与 `/report` handoff 幂等 | SEL-08 | 否 |
| SEL-10 | 测试矩阵（unit/contract/integration） | SEL-09 | 可并行分片 |
| SEL-11 | provider payload focused run + live 验收 | SEL-10 | 否 |
| SEL-12 | UI completed selection run 持久化恢复接线 | SEL-11 | 否 |

## 2. 可派发任务

### SEL-00 原项目算法审计与等价映射（前置硬门）
- 来源行：`memory/2026-05-24.md:17-24,46-50`，`docs/A股选股总体设计.md:5,32,720-763`，`docs/A股选股详细设计.md:583-730`
- 目标：锁定 `myhhub/stock`、`ArvinLovegood/go-stock`、`sngyai/Sequoia-X` 最新可复现代码中的选股算法、参数、排序规则，并映射到 claw-trade 的 `filter/strategy/per_strategy_raw_hits/score/top20` 合同。
- 允许修改范围：`docs/evidence/**`（新增审计证据），`docs/A股选股实施任务清单.md`（如需补充映射表）。
- 禁止修改范围：`src/**`、`tests/**`、`third_party/openclaw/**`。
- 实现要求：输出“仓库+commit/tag+文件+函数+参数+排序稳定性”映射；未取证项标 `BLOCKED`，不得以经验值补全。
- 验收证据：`docs/evidence/a-share-selection-algo-equivalence-*.md`，包含三仓库对照表和 claw-trade 等价映射表。
- 必跑测试/命令：
  - `git ls-remote https://github.com/myhhub/stock.git`
  - `git ls-remote https://github.com/ArvinLovegood/go-stock.git`
  - `git ls-remote https://github.com/sngyai/Sequoia-X.git`
- stop conditions：任一仓库无法定位最新代码、关键算法不可读、映射不闭合。
- mock/stub/fake/fallback 检查：禁止“根据记忆补参数”；每个阈值必须有来源文件与行号。
- 依赖任务：无。
- 是否可并行：否。

### SEL-01 选择域模型与状态机骨架
- 来源行：`docs/A股选股详细设计.md:146-340,363-463`，`docs/A股选股总体设计.md:49-57,571-607`，`src/claw_trade/data_gateway/models.py:12-27`
- 目标：按详细设计 §3 全量模型合同落地 `SelectRequest/SelectionRunPlan/SelectionDataRun/SelectionWorkflowRun/SelectionWorkerDispatch/SelectionDecision/SelectionConfirmation/SelectionProviderBatchPlan` 等 DTO 及双状态机（data run + workflow run）；后续任务只填实现细节，不把这些模型当可选项。
- 允许修改范围：`src/claw_trade/selection/models.py`（新增）、`src/claw_trade/workflow/models.py`（必要最小扩展）。
- 禁止修改范围：`third_party/openclaw/**`、`report workflow` 既有业务逻辑。
- 实现要求：`entry_point=select_command`；第一版只允许 `CN_A`；失败状态显式枚举；data run 必须有 `no_candidate` 终态，不得把 0 候选写成 failed 或 fake approved pack；不得隐式 fallback。
- 验收证据：模型字段合同与状态转换表对应关系文档（可放 `docs/evidence/`）。
- 必跑测试/命令：`uv run pytest tests/unit/selection/test_models.py tests/unit/selection/test_state_machine.py`
- stop conditions：`select_command` 与 `report_command` 语义混用；状态机遗漏失败态。
- mock/stub/fake/fallback 检查：禁止“默认转 report path”。
- 依赖任务：SEL-00。
- 是否可并行：否。

### SEL-02 selection run store 与 refresh 触发门
- 来源行：`docs/A股选股详细设计.md:40,49-51,127-130,197-205,1515-1519`，`docs/A股选股总体设计.md:168-171,1538-1546`
- 目标：实现 run store、latest terminal 选择、stale 校验和 refresh 触发入口；确保只有 completed + approved pack 可启动 workers，不可用时由 `/select` 触发后台补数状态返回。
- 允许修改范围：`src/claw_trade/selection/store.py`（新增）、`src/claw_trade/selection/controller.py`（查询与 refresh enqueue 部分）、必要最小 `src/claw_trade/selection/scheduler.py` enqueue 接口。
- 禁止修改范围：后台任务执行实现、OpenClaw 调度代码。
- 实现要求：no completed/no_candidate/stale/warehouse 证据不足时触发后台 selection data refresh/job，并返回“补数已启动/已有补数在跑/补数通道未配置”；latest no_candidate 不得回退旧 completed run；不得现场拉数、直接调 provider、直接读表或同步跑全市场；not approved/hash/readback/lineage mismatch 仍 fail closed。
- 验收证据：store 状态迁移记录与不可用返回码对照。
- 必跑测试/命令：`uv run pytest tests/integration/selection/test_latest_completed_gate.py`
- stop conditions：`/select` 路径触发 provider fetch、直接读 warehouse 表、同步执行全市场 data job，或用补数隐藏 hash/readback/lineage 损坏。
- mock/stub/fake/fallback 检查：provider/table spy 必须证明未调用；refresh gateway 只能创建/复用后台 job。
- 依赖任务：SEL-01。
- 是否可并行：否。

### SEL-03 后台确定性 data job（16:00 默认）
- 来源行：`docs/A股选股详细设计.md:365-423,581-749`，`docs/A股选股总体设计.md:140-171,454-455,1474-1483`，`memory/2026-05-24.md:46-50`
- 目标：实现收盘后批处理链路（取数->归一化->特征->过滤->per-strategy raw hits->合并去重->评分->最多 top20），默认北京时间 16:00，设置可改。
- 允许修改范围：`src/claw_trade/selection/scheduler.py`、`data_job.py`、`features.py`、`engine.py`、`settings` 相关最小配置入口。
- 禁止修改范围：聊天入口、worker prompt、OpenClaw 源码。
- 实现要求：算法参数来自 SEL-00 审计映射；不得 invent 权重/阈值；每个 `myhhub/stock` / `Sequoia-X` 策略必须先产出原始命中集合、命中字段、阈值证据和来源口径；top20 只是在合并评分后的评审上限，不得为了凑满 20 放宽过滤或伪造候选；不得提供用户手动 backfill/rerun 入口；执行时间以本清单 0 节冻结口径“默认北京时间 16:00”为准，旧设计中的 16:30/17:00 仅作历史背景。
- 验收证据：单 run 完整 evidence（provider attempts、normalized refs、feature snapshot、per-strategy raw hits、score/top20）。
- 必跑测试/命令：
  - `uv run pytest tests/integration/selection/test_data_job_pipeline.py`
  - `uv run pytest tests/integration/selection/test_scheduler_default_time.py`
- 专项用例：必须覆盖真实候选 `1..19` 可 completed、真实候选 `0` 进入 `no_candidate` 且无 approved pack。
- stop conditions：配置缺失导致算法无法等价映射；出现未批准参数；某策略无法生成 raw hits 证据却被写入命中；候选不足 20 时试图补齐。
- mock/stub/fake/fallback 检查：失败必须落 data gaps，不得走旧 provider 直连。
- 依赖任务：SEL-00, SEL-02。
- 是否可并行：部分可并行（`features` 与 `scheduler` 可拆分）。

### SEL-04 candidate pack 与 artifact authority
- 来源行：`docs/A股选股详细设计.md:207-241,484-523,732-765,1425-1467`，`docs/A股选股总体设计.md:208-223,281-302,618-619`
- 目标：生成并批准 candidate pack；保证 pack 只含事实与机器可复算结果；candidate pack 数量为 1..20，20 是上限不是必须数量。
- 允许修改范围：`src/claw_trade/selection/candidate_pack.py`、`src/claw_trade/selection/artifacts.py`、必要的 `src/claw_trade/artifacts/**`。
- 禁止修改范围：worker prompt、selection decision 解析逻辑。
- 实现要求：Python 不写自然语言入选理由/投资判断/目标价/止损价/交易建议；pack 必须包含每只候选的策略来源、策略变体、原始命中证据和评分排序字段；真实候选不足 20 时按实际数量生成，0 只候选进入 no-candidate 状态而不是 fake approved pack；approval 必须 readback/hash/manifest/lineage 全通过。
- 验收证据：approved candidate pack + manifest + readback/hash 校验日志。
- 必跑测试/命令：`uv run pytest tests/unit/selection/test_candidate_pack_contract.py tests/integration/selection/test_candidate_pack_approval.py`
- 专项用例：必须覆盖 candidate pack 拒绝 `0` 和 `>20`，并证明 `0` 候选只由 data job 的 `no_candidate` 终态承接。
- stop conditions：pack 含主观判断语言；readback/hash 不一致。
- mock/stub/fake/fallback 检查：禁止 fake approved/readback。
- 依赖任务：SEL-03。
- 是否可并行：否。

### SEL-05 selection tool 与 tool_names 映射
- 来源行：`docs/A股选股详细设计.md:135-137,888-929,1499-1505`，`docs/A股选股总体设计.md:240-257,845-850,859-870`，`src/claw_trade/config/tool_names.py:28-52`
- 目标：落地 `claw_get_selection_candidate_pack`，并把 intent 映射到唯一 canonical tool 名。
- 允许修改范围：`src/claw_trade/selection/tools.py`（新增）、`src/claw_trade/config/tool_names.py`、selection plugin wrapper。
- 禁止修改范围：report pack endpoint 业务实现、旧 report tool contracts。
- 实现要求：tool 无业务参数；只读 runtime context 对应 approved pack；不调 data_gateway/provider/Mongo raw，不重排或扩大 candidate pack。
- 验收证据：tool schema + runtime call evidence（同一 hash pack 被 strategist/skeptic 读取）。
- 必跑测试/命令：`uv run pytest tests/contracts/test_selection_tool_contract.py tests/contracts/test_selection_tool_import_block.py`
- stop conditions：tool 接受 ticker/date/topN 等业务参数；tool 路径触发外部取数。
- mock/stub/fake/fallback 检查：禁止 provider fallback 或 fake tool result。
- 依赖任务：SEL-04。
- 是否可并行：否。

### SEL-06 4 worker agent 配置与 prompt 边界
- 来源行：`docs/A股选股详细设计.md:27-31,109-118,947-1079,1099-1324`，`docs/A股选股总体设计.md:95,261-277,611-619`
- 目标：新增四个 selection worker 配置与 CN_A prompt 草案落地。
- 允许修改范围：`agents/selection_strategist/**`、`selection_skeptic/**`、`selection_manager/**`、`selection_portfolio_manager/**`。
- 禁止修改范围：`agents` 下非 selection worker、`third_party/openclaw/**`。
- 实现要求：strategist/skeptic 仅 candidate pack tool；manager/PM 无工具；worker 可见材料仅 approved L1 + approved summary，不含 raw/debug/provider envelope 与 refs/hash/manifest/lineage 文本。
- 验收证据：四个 worker 的 STAGES/profile/tool matrix 与 prompt 文件。
- 必跑测试/命令：`uv run pytest tests/contracts/test_selection_stage_policy.py tests/contracts/test_selection_prompt_material_boundary.py`
- stop conditions：非 CN_A prompt fallback；manager/PM 暴露工具。
- mock/stub/fake/fallback 检查：禁止“临时 prompt 授权工具”。
- 依赖任务：SEL-05。
- 是否可并行：否。

### SEL-07 OpenClaw dispatch（single worker turn）
- 来源行：`docs/A股选股详细设计.md:25,140-142,242-255,1020-1036,1338-1357,1526-1536`，`docs/A股选股总体设计.md:55,304-306,436`
- 目标：用固定顺序调度 4 次 OpenClaw single worker turn，并落 provider payload 证据。
- 允许修改范围：`src/claw_trade/selection/dispatch.py`、`src/claw_trade/selection/evidence.py`、selection controller 中 dispatch 组装最小代码。
- 禁止修改范围：`third_party/openclaw/**`（除非另行审批）。
- 实现要求：OpenClaw 只执行单 turn，不拥有 selection workflow；LLM 不决定下一 worker；每个 dispatch 必须有 payload/tool schema/material 边界验证。
- 验收证据：4 次 dispatch 的 provider payload 与 visible-tools 证据文件。
- 必跑测试/命令：`uv run pytest tests/contracts/test_selection_dispatch_order.py tests/contracts/test_selection_provider_payload.py`
- stop conditions：dispatch 顺序被模型输出影响；payload 缺失或无法对应 run/dispatch。
- mock/stub/fake/fallback 检查：禁止 fake OpenClaw success 作为 runtime/live 验收。
- 依赖任务：SEL-06。
- 是否可并行：否。

### SEL-08 `/select` 控制器与聊天输出
- 来源行：`docs/A股选股详细设计.md:425-483,1088-1243,1515-1524,1559-1561`，`docs/A股选股总体设计.md:499-607,620-647`
- 目标：接入 `/select` 命令流程，输出聊天文本结果（进入 `/report` / 观察 / 放弃）。
- 允许修改范围：`src/claw_trade/ui_backend/chat_controller.py` 或 command router、`src/claw_trade/selection/controller.py`。
- 禁止修改范围：复杂结果页、新 UI 路由、`/report` 业务逻辑。
- 实现要求：`/select` 不现场拉全市场数据、不直接调 provider、不直接读表、不同步跑全市场；先检查可用 completed + approved candidate pack，只有可用 pack 才启动 workers；无可用 pack/no_candidate/stale/warehouse 证据不足时触发后台 selection data refresh/job 并返回“补数已启动/已有补数在跑/补数通道未配置”；结果只在聊天输出。
- no-candidate 要求：若 latest terminal data run 是 `no_candidate`，不启动 selection workers，不回退旧 completed run，不生成 fake candidate pack；只触发后台 refresh/job 并返回补数状态。
- 验收证据：一次完整 `/select` 聊天回合日志与 selection workflow run evidence。
- 必跑测试/命令：`uv run pytest tests/integration/selection/test_select_command_chat_flow.py`
- stop conditions：`/select` 同步跑全市场、直接调 provider/读表、用 fake completed pack 代替补数；输出超出三分类语义。
- mock/stub/fake/fallback 检查：禁止 capture-only 文案通过（必须有 workflow/evidence 对应）。
- 依赖任务：SEL-02, SEL-07。
- 是否可并行：否。

### SEL-09 用户确认与 `/report` handoff 幂等
- 来源行：`docs/A股选股详细设计.md:279-303,525-579,1296-1337,1562-1570`，`docs/A股选股总体设计.md:57,640-644`
- 目标：用户确认后才启动 `/report`；同 workflow+ticker 全局去重。
- 允许修改范围：`src/claw_trade/selection/confirmation.py`、`report_handoff.py`、必要最小的 queue 接口调用层。
- 禁止修改范围：`/report` PM 决策逻辑、自动触发 report 的捷径逻辑。
- 实现要求：未确认不触发 report；确认时重验 pack freshness/hash/readback/lineage；不把 `/select` 结论注入 `/report` PM。
- 验收证据：confirmation 去重记录 + report task 创建证据。
- 必跑测试/命令：`uv run pytest tests/integration/selection/test_confirmation_idempotency.py tests/integration/selection/test_report_handoff_gate.py`
- stop conditions：watch/reject ticker 能触发 report；重复点击创建多个 report run。
- mock/stub/fake/fallback 检查：禁止 fake report_task_id。
- 依赖任务：SEL-08。
- 是否可并行：否。

### SEL-10 测试矩阵（unit/contract/integration）
- 来源行：`docs/A股选股详细设计.md:1469-1570`，`docs/A股选股总体设计.md:1568-1595`
- 目标：按详细设计测试矩阵补齐选择域测试，并把 fake 场景降级为纯状态机测试或替换成真实 focused run。
- 允许修改范围：`tests/unit/selection/**`、`tests/contracts/test_selection_*.py`、`tests/integration/selection/**`。
- 禁止修改范围：与 selection 无关的全局测试重构。
- 实现要求：区分三类证明：模型/状态机、合约、runtime/live；不得用单元替代 provider payload 验收。
- 验收证据：测试报告（按层级列出通过项）。
- 必跑测试/命令：
  - `uv run pytest tests/unit/selection`
  - `uv run pytest tests/contracts/test_selection_*`
  - `uv run pytest tests/integration/selection`
- stop conditions：关键 contract 测试未覆盖 payload/tool/material 边界。
- mock/stub/fake/fallback 检查：带 fake 的测试必须标明“仅状态机证明，不可作为 runtime/live 验收”。
- 依赖任务：SEL-09。
- 是否可并行：可并行分片。

### SEL-11 provider payload focused run + live 验收
- 来源行：`docs/A股选股详细设计.md:1526-1536,1571-1585`，`docs/A股选股总体设计.md:100,850,1590-1608`，`AGENTS.md:233-245`
- 目标：完成真实 OpenClaw focused run 与 live 验收；provider payload 作为最终证据。
- 允许修改范围：`docs/evidence/**`（新增验收证据）、必要测试脚本。
- 禁止修改范围：用静态 render/log/exporter 替代 payload；以 capture-only 或 fake pass 结案。
- 实现要求：必须拿到 4 worker payload、tool schema、material boundary、final `/select` reader artifact、confirmation 后 handoff 证据。
- 验收证据：`docs/evidence/a-share-select-live-*.md` + 原始 payload 文件路径清单。
- 必跑测试/命令：
  - `scripts/start-control-runtime.sh -- uv run pytest tests/contracts/test_selection_provider_payload.py`
  - `scripts/start-control-runtime.sh -- uv run pytest tests/integration/selection/test_select_live_acceptance.py`
- stop conditions：preflight 未过、payload 缺失、tools/materials 边界不符。
- mock/stub/fake/fallback 检查：静态 render/log/exporter 只能做辅助，不得作为最终通过证据。
- 依赖任务：SEL-10。
- 是否可并行：否。

### SEL-12 UI completed selection run 持久化恢复接线
- 来源行：`docs/A股选股总体设计.md:1538-1549`，`docs/A股选股详细设计.md:129,1515-1519`，`memory/2026-05-26.md` 最新 SEL-11 live 条目
- 目标：真实 UI runtime 启动时可恢复已完成的 selection data run，`/select` 不再因空内存 store 必然 `no_completed_selection_run`。
- 允许修改范围：`src/claw_trade/selection/**`（store/persistence/data_job runtime glue）、`src/claw_trade/ui_backend/chat_controller.py`、`src/claw_trade/web/state.py`、`tests/unit/selection/**`、`tests/integration/selection/**`、`tests/unit/ui/**`、`tests/contracts/test_selection_*.py`、`docs/evidence/**`、`memory/2026-05-26.md`。
- 禁止修改范围：`third_party/openclaw/**`、`/report` PM 决策逻辑与 exporter/prompt、selection 算法参数/权重/阈值、provider fallback/fake provider/runtime 假 artifact。
- 实现要求：恢复源必须是 data job completed evidence 或同等可审计 persisted record；没有真实 completed run 不得伪造恢复；`/select` 请求内不得触发全市场同步抓取，但必须走后台 refresh/job 返回“补数已启动/已有补数在跑/补数通道未配置”。
- 验收证据：`docs/evidence/sel-12-ui-completed-selection-run.md`（恢复来源、运行路径、测试结果、fail-closed 证明）。
- 必跑测试/命令：
  - `uv run pytest tests/integration/selection/test_store_persistence_restore.py`
  - `uv run pytest tests/integration/selection/test_latest_completed_gate.py`
- stop conditions：恢复路径需要 fake batch 或测试 fixture 冒充 completed run；或恢复后绕过 stale/hash/readback/manifest/lineage gate。
- mock/stub/fake/fallback 检查：不得在 UI 启动或 `/select` 请求中生成 fake completed run，不得用 capture-only 代替真实 completed evidence。
- 依赖任务：SEL-11。
- 是否可并行：否。

### SEL-13 data_gateway selection batch 接线
- 来源行：`docs/A股选股总体设计.md:1421-1460,1467-1546,1604-1642`，`docs/A股选股详细设计.md:0.2,3.16,5.3-5.5,11.1,11.4,15.2`，`docs/evidence/sel-12-ui-completed-selection-run.md`，`memory/2026-05-26.md` 最新 SEL-12 Chrome 条目
- 目标：为现有 `SelectionDataJob` 接入真实 data_gateway 批量取数适配层，产出可审计 `SelectionProviderBatchResult`；`/select` 只检查可用 completed+approved pack，不在请求中现场拉全市场，不可用时触发后台 refresh/job。
- 允许修改范围：`src/claw_trade/selection/**`（`provider_batch.py`、`scheduler.py`、`data_job.py` 必要 glue）、`src/claw_trade/data_gateway/**` 最小复用/适配、必要最小 `src/claw_trade/web/state.py` runtime wiring、`tests/unit/selection/**`、`tests/integration/selection/**`、`tests/contracts/test_selection_*.py`、`docs/evidence/**`、`memory/2026-05-26.md`。
- 禁止修改范围：`third_party/openclaw/**`、`/report` PM 决策逻辑与 exporter/prompt、selection 算法权重/阈值/排序规则、新增 runtime guard/hard gate、mock/stub/fake/fallback/capture-only 冒充 runtime/live。
- 实现要求：必须走现有 data_gateway/provider registry/Mongo/OpenViking 证据链；无可用数据源或字段不足时 fail closed 并写 data gaps，不得生成 fake completed run；provider attempts/normalized refs/provider batch plan/data gaps 必须可审计。
- 验收证据：`docs/evidence/sel-13-data-gateway-batch-*.md`（或 `*-blocked-*.md`）+ 对应 run/测试证据路径；旧 `sel-13-openbb-*` 文件名只作为历史证据，不代表 OpenBB 是当前运行依赖。
- 必跑测试/命令：
  - `uv run pytest tests/integration/selection/test_data_job_pipeline.py`
  - `uv run pytest tests/integration/selection/test_store_persistence_restore.py`
  - `uv run pytest tests/integration/selection/test_latest_completed_gate.py`
  - SEL-13 新增 focused 测试
- stop conditions：现有 data_gateway 无法提供批量全市场字段；或 provider config/strategy config/交易日历来源缺失导致无法闭环；或需要绕过 data_gateway 才能通过。
- mock/stub/fake/fallback 检查：禁止用测试夹具、手工假 record、capture-only 或隐藏 fallback 伪造 completed run。
- 依赖任务：SEL-12。
- 是否可并行：否。
- 最新执行（2026-06-03，SEL-13-REWORK）：已完成功能接线与回归测试；当前运行目标是 data_gateway/provider registry/Mongo/OpenViking 证据链，不是 OpenBB。真实本地预打包 smoke 已不再出现旧 `symbol_required`，但用户当前包缺公司名/行业身份和定增覆盖，候选池按真实缺口 fail closed。

## 3. 派发规则（给实现 agent）

- 按 `SEL-00 -> SEL-13` 执行；未完成前置任务不得跳步。
- 每个任务提交必须包含：变更文件、测试命令、退出码、证据路径、是否命中 stop conditions。
- 如触发 stop condition，状态标 `BLOCKED`，不得用 fallback/临时参数/假成功继续推进。
