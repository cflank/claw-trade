# Worker 聊天实施任务清单

状态：可派发给后续 subagent 的实施清单草案
日期：2026-05-27
主来源：`docs/worker聊天详细设计.md`、`docs/UI详细设计.md`、`docs/UI实施任务清单.md`、`AGENTS.md`
替代文档：原 `docs/A股选股@worker追问实施任务清单.md` 已撤回，不再作为实现依据。

## 1. 范围与红线

本清单实现 worker 聊天，不实现“追问”功能名，不实现 selection worker 追问。

两种模式：

```text
generic_worker_chat
  主工作台 worker 聊天
  默认 @组合经理
  无报告、无标的、无 workflow 上下文
  使用 OpenClaw 默认 agent chat prompt

report_worker_chat
  报告阅读区 worker 聊天
  默认 @组合经理
  绑定当前 completed report
  使用报告 worker 聊天专用 prompt
  读取 approved worker L1 + final report / PM conclusion + 相关片段
```

第一版只开放：

- `portfolio_manager`
- `research_manager`
- `market_analyst`
- `fundamental_analyst`
- `news_analyst`
- `social_analyst`
- `risk_moderator`

禁止：

- 不让 LLM 自动选择 worker。
- 不开放多空辩手、交易员、风险挑战者、风险守护者、report_polisher、selection worker。
- 普通 worker 聊天不绑定最近报告、最近标的或最近 workflow。
- 普通 worker 聊天不使用 `/report` workflow stage prompt。
- 报告阅读 worker 聊天不触发 `/report` workflow。
- Worker 聊天回复不写入报告正文、PM 结论、worker L1、`/select` 决策或 handoff material。
- 不读取 raw/provider/debug/receipt/hash/manifest 文本给模型。
- 不使用 Python 代答、润色、摘要或修复 worker 回复。

## 2. 任务图

| 任务编号 | 任务名 | 依赖 | 是否可并行 |
|---|---|---|---|
| WCH-00 | 文档和旧入口清理审计 | 无 | 否 |
| WCH-01 | Worker 聊天 catalog 与别名解析 | WCH-00 | 是 |
| WCH-02 | WorkerChat DTO、模式和用户 DTO 红线 | WCH-01 | 是 |
| WCH-06 | OpenClaw worker chat seam discovery 与调用层 | WCH-02 | 否 |
| WCH-03 | 普通 worker 聊天后端路径 | WCH-02, WCH-06 | 否 |
| WCH-04 | 报告 worker 聊天材料 resolver | WCH-02 | 否 |
| WCH-05 | 报告 worker 聊天 prompt renderer | WCH-04, WCH-06 | 否 |
| WCH-07 | 聊天记录、诊断证据和幂等 | WCH-03, WCH-05 | 是 |
| WCH-08 | 主工作台 worker 选择器 | WCH-02, WCH-03 | 是 |
| WCH-09 | 报告阅读区 worker 选择器 | WCH-04, WCH-05 | 是 |
| WCH-10 | 合同、集成和 UI 测试矩阵 | WCH-08, WCH-09 | 可分片 |
| WCH-11 | 真实 Chrome 验收 | WCH-10 | 否 |

## 3. 任务详情

### WCH-00 文档和旧入口清理审计

- 目标：确认旧 `A股选股 @worker 追问` 文档不再作为实现依据，并列出仍引用旧设计的活跃文档、活跃 UI/API/tests 入口。
- 修改范围：实现阶段必须清理活跃 UI/API/tests 中的“追问”/`report_qa` 旧入口和文案；历史 `memory/`、`docs/evidence/` 可保留为历史记录。
- 实施步骤：
  - 搜索 `A股选股@worker追问`、`selection_worker_mention`、`@worker 追问`、`追问`、`report_qa`。
  - 区分历史 `memory/evidence` 和活跃设计文档。
  - 活跃设计文档必须改为指向 `docs/worker聊天详细设计.md` 或说明旧设计撤回。
  - 活跃 UI 文案不得继续把新功能叫“追问”。
  - 活跃 API/test 不能继续以 `report_qa` 承担报告 worker 聊天语义；如保留兼容层，必须显式迁移到 worker chat controller/contract，并证明不会绕过 worker_id 必填。
- 验收：
  - 活跃设计文档不再要求 `/select` completed 后 selection worker 追问。
  - 活跃 UI/API/tests 里的“追问”/`report_qa` 旧路径已迁移或删除，不再作为 worker 聊天实现入口。
  - 历史记录保留，不作为实现来源。
- 测试命令：
  - `rg -n "A股选股@worker追问|selection_worker_mention|@worker 追问" docs/A股选股总体设计.md docs/A股选股详细设计.md docs/A股选股实施任务清单.md`
  - `rg -n "追问|report_qa" src web/research-ui/src tests`
  - 若命中内容仅说明旧设计已撤回，可接受；若仍把旧设计列为实现来源或验收项，必须修正文档。

### WCH-01 Worker 聊天 catalog 与别名解析

- 目标：实现 7 个开放 worker 的确定性 catalog。
- 修改范围建议：
  - `src/claw_trade/ui_backend/worker_chat_catalog.py`
  - `src/claw_trade/ui_contracts/**`
  - `web/research-ui/src/api/contracts.ts`
- 实施要求：
  - catalog 标记 UI 默认 worker 是 `portfolio_manager`。
  - 普通用户显示中文名。
  - 支持中文别名输入。
  - 不支持 LLM 自动选择 worker。
  - 不开放未批准 worker。
- 验收：
  - catalog 只有 7 个 worker。
  - `risk_moderator` 用户显示名是“风险经理”。
  - `bull_researcher`、`bear_researcher`、`trader`、`risk_challenger`、`risk_guardian`、`report_polisher`、selection worker 不在普通产品菜单。
- 测试：
  - `uv run pytest tests/unit/ui/test_worker_chat_catalog.py`
  - `uv run pytest tests/contracts/test_worker_chat_user_surface.py`

### WCH-02 WorkerChat DTO、模式和用户 DTO 红线

- 目标：定义 worker 聊天请求、响应和用户 DTO。
- 修改范围建议：
  - `src/claw_trade/ui_contracts/api_contracts.py`
  - `src/claw_trade/ui_backend/worker_chat.py`
  - `web/research-ui/src/api/contracts.ts`
- 实施要求：
  - 模式只允许 `generic_worker_chat`、`report_worker_chat`。
  - `worker_id` 是后端必填字段；API 直调缺失必须失败，后端不得补默认。
  - `generic_worker_chat` 不接受 `report_id` 作为隐式上下文。
  - `report_worker_chat` 必须带 completed report id。
  - 用户 DTO 不含内部 id/path/hash/manifest/provider refs。
- 验收：
  - API 直调缺 `worker_id` 失败；UI 层负责默认补 `portfolio_manager`。
  - 用户响应只包含 worker 中文名、文本和模式等必要字段。
- 测试：
  - `uv run pytest tests/contracts/test_worker_chat_api_contracts.py`
  - `uv run pytest tests/contracts/test_ui_user_dto_redaction.py`

### WCH-03 普通 worker 聊天后端路径

- 目标：实现主工作台普通 worker 聊天，不绑定报告和 workflow。
- 修改范围建议：
  - `src/claw_trade/ui_backend/chat_controller.py`
  - `src/claw_trade/ui_backend/worker_chat_controller.py`
  - `src/claw_trade/web/routes_ui.py`
- 实施要求：
  - 必须复用 WCH-06 交付的 OpenClaw seam discovery/client 结果；WCH-03 不再独立发明 chat seam 或字段合同。
  - worker chat session 必须绑定 worker 身份，例如 session key 使用 `agent:{worker_id}:...` 或等价 OpenClaw API 明确传入 agent id / worker id；不能只把 worker 名写进用户 prompt。
  - API 缺 `worker_id` 必须 400；后端不得补默认 `portfolio_manager`。UI 层发送前显式填入默认值。
  - 文本点名与结构化 `worker_id` 冲突时，后端确定性处理或返回冲突错误；不得交给 LLM。
  - `maybe_reject_report_question_without_report` 必须明确接入 controller 的 generic 分支，并在 OpenClaw 调用前执行。
  - 使用 OpenClaw 默认 agent chat / default session prompt。
  - 不构造 `RunRequest`。
  - 不调用 report workflow runner。
  - 不读取 report repository 猜上下文。
  - 不使用 `/report` workflow stage prompt。
  - 如果现有 OpenClaw seam 不存在，或只能复用 workflow stage prompt 才能跑，必须停问；不得在 Python 里临时代答。
- 验收：
  - 普通 worker 聊天可以和默认 `portfolio_manager` 对话。
  - 具体报告问题在无报告上下文时返回可读提示，不猜最近报告。
  - provider payload proof 复用 WCH-06 已建立的字段合同与取证方法，不得在 WCH-06 之前要求独立 proof。
- 测试：
  - `uv run pytest tests/unit/ui/test_generic_worker_chat.py`
  - `uv run pytest tests/contracts/test_worker_chat_prompt_boundary.py`

### WCH-04 报告 worker 聊天材料 resolver

- 目标：为报告阅读 worker 聊天选择已保存材料。
- 修改范围建议：
  - `src/claw_trade/ui_backend/report_worker_chat_context.py`
  - `src/claw_trade/ui_backend/report_repository.py`
- 实施要求：
  - 必须绑定 completed report。
  - 非 PM worker：默认读取被点名 worker approved L1（必需）。
  - `portfolio_manager`：优先 PM approved L1；若缺失可用 approved PM conclusion 作为 PM primary material。
  - 最终报告是硬要求；PM 结论默认读取，有则加入，缺失时记录 internal gap。仅当 `portfolio_manager` 同时缺 PM L1 和 approved PM conclusion 时才阻断。
  - 相关其他 worker 材料只选片段；必须通过 approved reader-visible index 检索，不得扫目录。
  - related snippet 需要明确排序（相关性/时序/worker 优先级）、去重、预算上限和检索失败语义（检索失败可降级为无片段，但不得伪造命中）。
  - 只能从 approved final report、PM material、被点名 worker approved L1、其它 approved L1 reader-visible body 读取。
  - 禁止扫描 raw/provider/debug/evidence 目录或 receipt/hash/manifest 协议文本拼模型材料。
  - 不读取 raw/provider/debug/receipt/hash/manifest 协议文本给模型。
  - 非 PM worker 的被点名 L1 缺失时，不伪造；不能用 final report 冒充该 worker L1。
- 验收：
  - `@市场分析师` 默认包含 market L1 + final report，并在 approved PM conclusion 存在时包含 PM conclusion；PM conclusion 缺失时不阻断但记录 internal gap。
  - `@组合经理` 默认包含 PM L1（优先）或 approved PM conclusion（兜底 primary）+ final report；PM L1 和 PM conclusion 都缺失时失败。
  - 旧报告缺 worker L1 时返回可读错误或限制提示。
- 测试：
  - `uv run pytest tests/unit/ui/test_report_worker_chat_context.py`
  - `uv run pytest tests/contracts/test_report_worker_chat_material_boundary.py`
  - 必须覆盖场景：`test_report_worker_chat_pm_primary_fails_only_when_pm_l1_and_pm_conclusion_missing`
  - 必须覆盖场景：`test_report_worker_chat_non_pm_allows_missing_pm_conclusion_with_internal_gap`

### WCH-05 报告 worker 聊天 prompt renderer

- 目标：新增报告 worker 聊天专用 prompt，不能复用 workflow stage prompt。
- 修改范围建议：
  - 必须新增或复用 agent config，例如 `agents/<worker>/chat/REPORT.md` 或等价共享 agent chat 配置。
  - Python 侧只允许新增材料选择和变量注入逻辑；不得在 `src` 中手写 worker 业务 prompt。
- 实施要求：
  - agent config 中的专用聊天 prompt 说明当前是已保存报告上的 worker 聊天。
  - agent config 中的专用聊天 prompt 要求只基于提供的已保存材料回答。
  - agent config 中的专用聊天 prompt 禁止声称重新调用工具、重新跑 workflow、改写报告或生成正式投资结论。
  - Python 只返回结构化 `prompt_variables`，不得返回完整业务 prompt 字符串。
  - `send_report_worker_chat` 必须传 `prompt_profile + prompt_variables + user_message` 给 OpenClaw 渲染，不得由 Python 拼接完整聊天 prompt。
  - agent config 和 provider final prompt 不含 RuntimeTarget、ReportSubmission、URI/hash/receipt/L1/L2/manifest 协议文本。
  - 如果实现发现必须改 `third_party/openclaw` 才能支持 agent chat prompt 配置，先停问；获批后必须按 AGENTS 顺序 `src` edit -> focused tests -> `pnpm build` -> restart `scripts/start-control-runtime.sh` -> focused live proof -> inspect fresh provider payload。
- 验收：
  - 渲染 prompt 不含 workflow stage checklist。
  - 渲染 prompt 不含 provider/debug 协议字段。
  - provider final prompt 证明专用聊天 prompt 来自 agent config，不是 Python 拼接的 workflow stage prompt。
- 测试：
  - `uv run pytest tests/contracts/test_report_worker_chat_prompt_boundary.py`

### WCH-06 OpenClaw worker chat seam discovery 与调用层

- 目标：统一调用 OpenClaw worker chat，但不进入 claw-trade workflow。
- 修改范围建议：
  - `src/claw_trade/web/openclaw_gateway.py`
  - `src/claw_trade/ui_backend/worker_chat_controller.py`
- 实施要求：
  - 先做 seam discovery：确认可用的是 OpenClaw agent chat seam，而不是 workflow stage command。
  - 必须定义并落地结构化请求/响应合同。请求字段至少包含：`worker_id`、`session_key`、`user_message`、`prompt_profile`、`prompt_variables`、`tool_policy/visible_tools`、`idempotency_key`、`capture_provider_payload`。
  - `generic_worker_chat` 的 `visible_tools` 字段不得用空集合表示“无工具”；应传 `None` 或等价 sentinel 表示采用 OpenClaw 默认 agent chat 工具配置。`report_worker_chat` 第一版才传空工具集合。
  - 响应字段至少包含：`status`、`worker_id`、`session_key`、`request_id`（可用时）、`provider_request_ref/provider_payload_ref`（可用时）、`prompt_profile`、`visible_tools`、`reply_text/failure_code`。
  - `generic_worker_chat` 调默认 agent chat。
  - `report_worker_chat` 调 worker chat session，并传入报告聊天专用上下文。
  - 两种模式都必须用 OpenClaw agent chat seam 绑定 worker 身份和 session，例如 `agent:{worker_id}:generic:{conversation_id}`、`agent:{worker_id}:report:{report_id}:{conversation_id}` 或等价 OpenClaw API。
  - worker 身份权威来自结构化字段或 session 绑定，不得只把 worker 身份写进 prompt 文本。
  - 若 gateway 不支持上述字段合同，触发 stop condition，不得继续后端路径开发。
  - OpenClaw 调用结果必须带可诊断的 worker id/session id/provider request ref；缺失时不能把 Python 包装结果当作成功证据。
  - 禁止使用 `agent.runSingleWorker` 或任何 workflow stage command 伪装 worker chat；没有可用 OpenClaw agent chat seam 时必须触发 stop condition。
  - 如果现有 seam 只能复用 workflow stage prompt，必须停问。
  - 如果获批修改 `third_party/openclaw`，必须按 AGENTS 的 OpenClaw runtime seam 验证顺序执行：edit `src` -> focused source/unit tests -> `pnpm build` -> restart `scripts/start-control-runtime.sh` -> fresh focused live run -> inspect real provider payload。
  - 失败不 fallback 到 Python 代答。
- 验收：
  - 可以区分普通 worker chat 与 report worker chat session。
  - OpenClaw 失败返回用户可读错误。
  - 同一 `request_id` 不同 payload fingerprint 返回冲突，不得回放旧 turn。
  - provider payload 证明 worker/session id、final messages、visible tools 与请求模式匹配。
- 测试：
  - `uv run pytest tests/unit/ui/test_openclaw_worker_chat_client.py`
  - `uv run pytest tests/contracts/test_openclaw_worker_chat_seam_missing_stops.py`

### WCH-07 聊天记录、诊断证据和幂等

- 目标：保存 worker 聊天 turn，但不写正式报告 artifact。
- 修改范围建议：
  - `src/claw_trade/ui_backend/worker_chat_store.py`
  - `src/claw_trade/ui_backend/chat_controller.py`
- 实施要求：
  - 保存 request_id、mode、worker_id、user_text、reply_text、status。
  - 保存 request payload fingerprint；同一 request_id 但 fingerprint 不同返回冲突，不得返回旧 turn。
  - report 模式保存 report_id/run_id 内部关联。
  - 可保存 provider request ref 供诊断，但用户 DTO 不暴露。
  - 重复 request_id 且 fingerprint 相同才幂等返回既有结果。
  - OpenClaw 失败 turn 也要保存（`status=failed` + failure_code + provider ref），但不写正式 artifact。
- 验收：
  - worker 聊天记录不进入 report handoff material。
  - 不修改 saved report Markdown。
- 测试：
  - `uv run pytest tests/unit/ui/test_worker_chat_store.py`

### WCH-08 主工作台 worker 选择器

- 目标：主聊天区支持 worker 选择器，默认组合经理。
- 修改范围建议：
  - `web/research-ui/src/components/Composer.tsx`
  - `web/research-ui/src/api/client.ts`
  - `web/research-ui/src/api/contracts.ts`
- 实施要求：
  - 默认显示“组合经理”。
  - 可切换 7 个开放 worker。
  - 发送请求时必须带 `workerId`。
  - 不显示内部 worker id。
- 验收：
  - 主工作台默认 `portfolio_manager`。
  - 切换后请求 workerId 正确。
- 测试：
  - `pnpm --dir web/research-ui test -- worker-chat`

### WCH-09 报告阅读区 worker 选择器

- 目标：报告阅读区支持同一组 worker，默认组合经理，绑定当前 report id。
- 修改范围建议：
  - `web/research-ui/src/components/ReportReaderPanel.tsx` 或现有报告阅读组件
  - `web/research-ui/src/api/client.ts`
  - worker chat controller；若现有 `report_qa` 路径仍活跃，迁移或删除其 worker 聊天语义
- 实施要求：
  - 默认显示“组合经理”。
  - 请求必须带 `reportId` 和 `workerId`。
  - 不再使用“追问”作为功能名。
  - 发送后报告正文不变。
- 验收：
  - 报告阅读区 worker 聊天不触发新 report workflow。
  - 切换 worker 后材料 resolver 使用对应 worker。
- 测试：
  - `pnpm --dir web/research-ui test -- report-worker-chat`
  - `uv run pytest tests/unit/ui/test_report_worker_chat.py`

### WCH-10 合同、集成和 UI 测试矩阵

- 目标：补齐跨层测试。
- 必跑：
  - `uv run pytest tests/contracts/test_worker_chat_api_contracts.py`
  - `uv run pytest tests/contracts/test_worker_chat_prompt_boundary.py`
  - `uv run pytest tests/contracts/test_openclaw_worker_chat_contract.py`
  - `uv run pytest tests/contracts/test_openclaw_worker_chat_seam_missing_stops.py`
  - `uv run pytest tests/contracts/test_report_worker_chat_material_boundary.py`
  - `uv run pytest tests/unit/ui/test_generic_worker_chat.py`
  - `uv run pytest tests/unit/ui/test_report_worker_chat_context.py`
  - `uv run pytest tests/unit/ui/test_worker_chat_idempotency.py`
  - `uv run pytest tests/unit/ui/test_worker_chat_protocol_block_detection.py`
  - `uv run pytest tests/unit/ui/test_report_worker_chat_related_snippets.py`
  - `uv run pytest tests/unit/ui/test_worker_chat_tool_policy_request.py`
  - `uv run pytest tests/unit/ui/test_report_worker_chat_pm_conclusion_gaps.py`
  - `uv run pytest tests/e2e/ui/test_worker_chat_user_flows.py`
  - `pnpm --dir web/research-ui test`
- 反向验证：
  - `rg -n "A股选股@worker追问|selection_worker_mention" docs/A股选股总体设计.md docs/A股选股详细设计.md docs/A股选股实施任务清单.md src web/research-ui/src tests`
  - `rg -n "追问|report_qa" src web/research-ui/src tests`
  - 活跃 UI 文案不得继续叫“追问”；活跃 API/tests 不得继续以 `report_qa` 作为 worker 聊天入口；新设计文档中说明旧设计撤回的历史文字不算失败。
- Runtime provider proof：
  - `generic_worker_chat` focused runtime proof 至少一次。
  - `report_worker_chat` focused runtime proof 至少一次。
  - 每次 proof 捕获 worker/session id、provider final messages、visible tools、request/runtime marker。
  - proof 必须覆盖结构化字段合同：`worker_id/session_key/user_message/prompt_profile/prompt_variables/tool_policy/visible_tools/idempotency_key/capture_provider_payload`。
  - proof 必须证明 `generic_worker_chat` 的 visible tools 来自 OpenClaw 默认 agent chat 配置，`report_worker_chat` 的 visible tools 为空集合。
  - proof 必须覆盖 PM 材料规则：PM 优先 PM L1，缺 PM L1 时允许 PM conclusion 兜底；非 PM worker 缺 L1 必须失败。
  - proof 必须覆盖 generic 分支的 `maybe_reject_report_question_without_report` 前置拒绝。
  - 每次 proof 断言无 `/report` stage prompt、无 `[ApprovedMaterials]`、无 workflow runner、无 raw/provider/debug/receipt/hash/manifest 协议文本、不是 Python 代答。

### WCH-11 真实 Chrome 验收

- 目标：用真实 Chrome 验证用户流。
- 验收步骤：
  - 打开主工作台。
  - 确认默认 worker 是组合经理。
  - 发送一条普通 worker 聊天。
  - 切换市场分析师并发送。
  - 打开一份已完成报告。
  - 确认报告阅读区默认 worker 是组合经理。
  - 发送报告 worker 聊天。
  - 确认没有创建新 `/report` workflow。
  - 确认报告正文未改变。
  - 对应后端诊断证据能关联到 WCH-10 的两类 provider payload proof。
- 证据：
  - Chrome 截图。
  - 网络请求字段截图或日志。
  - 后端 workflow queue 未新增 report 的证据。
  - OpenClaw/provider payload proof 路径或 request ref。

## 4. Stop Conditions

必须停问：

- OpenClaw 没有默认 agent chat seam，必须复用 workflow stage prompt 才能跑。
- OpenClaw gateway 不支持 worker chat 结构化字段合同（worker/session/prompt/idempotency/payload capture）。
- 产品要求 LLM 自动选择 worker。
- 产品要求普通 worker 聊天猜最近报告。
- 产品要求开放 7 个以外 worker。
- 产品要求 worker 聊天回复写入正式报告或 PM 结论。
- 报告材料 resolver 只能通过 raw/provider/debug/receipt/hash/manifest 文本才能回答。
- 需要新增或收紧 `/report` PM 表达 guard。
