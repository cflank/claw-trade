# Worker 聊天详细设计

状态：详细设计草案，已完成本轮需求方向确认，未进入实现。
日期：2026-05-27
替代文档：原 `docs/A股选股@worker追问详细设计.md` 已撤回，不再作为实现依据。
关联设计：`docs/UI详细设计.md`、`docs/UI实施任务清单.md`、`AGENTS.md`。

## 1. 结论

本设计定义的是 **worker 聊天**，不是“追问”，也不是 workflow 插话。

Worker 聊天分两种模式：

```text
普通 worker 聊天
  -> 用户在主工作台聊天区和一个明确 worker 聊天
  -> 默认 worker 是 @组合经理
  -> 不绑定报告、不绑定标的、不绑定 workflow run
  -> 使用 OpenClaw 默认 agent chat prompt
  -> 不使用 claw-trade 为 /report workflow 设置的 stage prompt 或材料路径

报告阅读 worker 聊天
  -> 用户在已完成报告阅读区和一个明确 worker 聊天
  -> 默认 worker 是 @组合经理
  -> 绑定当前 report_id / run_id
  -> 使用报告 worker 聊天专用 prompt
  -> 可见材料来自已保存报告和 approved worker 材料
  -> 不使用 /report workflow stage prompt，不改写报告，不重跑 workflow
```

两种模式都要求请求里有明确 worker。唯一可信来源是 UI worker 选择器产生的结构化 `worker_id`。用户没有选择时，UI 必须显式填入默认 worker：`portfolio_manager`。

## 2. 背景和纠偏

之前的 `A股选股 @worker 追问` 文档把需求错误锚到了 `/select` completed 后的 selection worker 解释。这是错误方向。

新的口径：

- 不叫追问，叫 worker 聊天。
- 不属于 A 股选股设计。
- 不要求 `/select` 上下文。
- 不默认支持 selection worker。
- 普通聊天可以直接和适合聊天的 worker 对话。
- 报告阅读时可以围绕当前已完成报告和 worker 材料聊天。

历史错误文档和任务清单不得继续派发实现。

## 3. Worker 范围

第一版只开放适合聊天的 7 个 worker。

| 用户显示名 | worker_id | 默认开放场景 | 说明 |
|---|---|---|---|
| 组合经理 | `portfolio_manager` | 普通聊天、报告阅读 | 默认 worker，解释最终决策、条件和风险 |
| 研究经理 | `research_manager` | 普通聊天、报告阅读 | 解释研究层综合判断和多空权衡 |
| 市场分析师 | `market_analyst` | 普通聊天、报告阅读 | 解释行情、技术面和市场状态 |
| 基本面分析师 | `fundamental_analyst` | 普通聊天、报告阅读 | 解释财务、估值、公司质量 |
| 新闻分析师 | `news_analyst` | 普通聊天、报告阅读 | 解释公司新闻和宏观新闻影响 |
| 情绪分析师 | `social_analyst` | 普通聊天、报告阅读 | 解释情绪、社媒和市场关注度 |
| 风险经理 | `risk_moderator` | 普通聊天、报告阅读 | 综合解释风险争议和风险条件 |

不在第一版普通产品菜单中开放：

- `bull_researcher`
- `bear_researcher`
- `trader`
- `risk_challenger`
- `risk_guardian`
- `report_polisher`
- selection worker

原因：

- 多空辩手依赖具体辩论上下文，普通聊天中容易硬凹立场。
- 交易员依赖标的、价格、计划和风险条件，普通聊天中容易输出空泛交易建议。
- 风险挑战者和风险守护者是 workflow 内部辩论位，普通用户默认只需要综合风险入口。
- `report_polisher` 是终稿编辑，不是用户聊天对象。
- selection worker 属于 `/select` 资源分配流程，不属于本轮 worker 聊天首版。

## 4. 通用规则

### 4.1 必须明确 worker

后端不得让 LLM 判断该叫谁，也不得从消息正文解析 worker。

唯一明确方式：

```text
UI worker 选择器：
  worker_id = portfolio_manager
```

用户输入 `@` 时，UI 只弹出 7 个可聊天 worker 的选择列表。用户必须从列表选择，选择结果成为结构化 `worker_id`。如果用户未选择，UI 默认选择 `@组合经理`，但发给后端的请求必须已经包含：

```text
worker_id = portfolio_manager
```

这不算自动路由，因为默认值是产品 UI 显式选择结果，不是模型推断。

API 或后端直调缺少 `worker_id` 必须返回 400。后端不得补 `portfolio_manager` 默认值，也不得把用户文本交给 LLM 选择 worker。

用户手动输入 `@xxx` 但没有从列表选择时，前端和后端都不解析、不切换 worker、不报冲突错误。该文本按普通消息正文处理，或由 UI 忽略 `@` 标记；请求里的 `worker_id` 仍来自选择器当前值。

### 4.2 普通用户不显示内部名

普通 UI、微信和用户文案只显示中文名：

```text
组合经理
研究经理
市场分析师
基本面分析师
新闻分析师
情绪分析师
风险经理
```

内部 `worker_id`、英文 alias、run id、artifact id、hash、manifest、provider request id 不进入普通用户 DTO。

### 4.3 不产生正式 artifact

Worker 聊天回复只是聊天记录。

它不得成为：

- PM 最终决策。
- reader final report。
- worker L1 artifact。
- `/select` 决策。
- `/report` handoff material。
- hard-gate-approved 投资材料。

### 4.4 不改写已有报告

Worker 聊天可以解释已保存内容，但不能改写已保存 Markdown、PDF、PM 结论、worker L1 或导出结果。

如果用户要求“把报告改成更乐观”，系统应引导其重新生成报告或另建任务，而不是在 worker 聊天中修改正式报告。

## 5. 模式 A：普通 Worker 聊天

### 5.1 产品语义

普通 worker 聊天发生在主工作台聊天区。

它是“和某个 OpenClaw worker 身份聊天”，不是报告流程。

```text
用户选择：组合经理
用户 -> 你一般怎么判断买入条件？
系统 -> 以 portfolio_manager 的普通聊天身份回答
```

### 5.2 上下文

普通 worker 聊天不绑定：

- ticker
- market
- report_id
- run_id
- workflow stage
- approved L1
- PM decision
- candidate cache
- provider payload evidence

如果用户在普通聊天里问具体报告：

```text
这份报告为什么是 HOLD？
```

但当前没有绑定报告，系统应要求用户打开或选择一份报告，不得自动猜最近报告。

### 5.3 Prompt 规则

普通 worker 聊天必须走 OpenClaw 默认 agent chat prompt / 默认 agent session 机制。

不得走：

- `/report` stage prompt。
- `single_worker_minimal` workflow command。
- claw-trade 为 workflow 渲染的 final provider prompt。
- `[ApprovedMaterials]` 注入。
- report worker handoff prompt。
- selection worker prompt。

如果 OpenClaw 默认 agent chat 缺少某个 worker 的可用 prompt，应停下修 OpenClaw/agent 默认聊天配置，而不是复用 workflow stage prompt 顶上。

### 5.4 工具和证据

普通 worker 聊天的工具暴露遵循 OpenClaw 默认 agent chat 配置，不继承 claw-trade workflow stage policy。

第一版必须至少有一次 `generic_worker_chat` focused runtime proof。证据必须来自真实 OpenClaw/provider payload，而不是静态 render、日志或 Python 重建文本。

证据至少捕获：

- worker id。
- OpenClaw chat session id，例如 `agent:{worker_id}:...` 或等价 OpenClaw agent chat session key。
- provider final messages。
- model-visible tool schema。
- OpenClaw runtime marker / request id（可用时）。

断言：

- worker 身份是请求字段中的 worker，不是 Python 代答。
- provider prompt 使用 OpenClaw 默认 agent chat prompt。
- provider prompt 不含 `/report` workflow stage prompt。
- provider prompt 不含 `[ApprovedMaterials]`。
- provider prompt 不含 workflow runner / `single_worker_minimal` / report handoff 材料。

后端可以为诊断保存 OpenClaw chat request / response 元数据。保存的诊断信息不得进入普通用户 DTO。

### 5.5 失败语义

| 场景 | 用户结果 | 行为 |
|---|---|---|
| worker 缺失 | “这个角色暂不可用。” | 不 fallback 到其他 worker |
| 未传 worker_id | UI 默认补 `portfolio_manager`；API 直调则 400 | 不让 LLM 选择 |
| 用户问具体报告但无报告上下文 | “请先打开一份报告后再问。” | 不猜最近报告 |
| OpenClaw chat 失败 | “worker 聊天暂不可用。” | 不用 Python 代答 |

## 6. 模式 B：报告阅读 Worker 聊天

### 6.1 产品语义

报告阅读 worker 聊天发生在已完成报告阅读区。

它绑定当前报告，回答用户对这份报告和对应 worker 判断的疑问。

```text
用户打开某份报告
默认 worker = @组合经理
用户问：为什么最后不是买入？
系统基于该报告的最终报告、PM 结论和相关 approved worker 材料回答
```

### 6.2 上下文

报告阅读 worker 聊天必须绑定：

- `report_id` 或等价 saved report id。
- 对应 `run_id`。
- 已保存 final report Markdown。
- 已保存 PM 结论或 PM worker material。

如果这些材料不存在，不能伪造。

### 6.3 材料分层

结构化 `worker_id` 指定的 worker 的材料优先级最高。

```text
第 1 层：当前选择的 worker 的 primary material
  - 非 PM worker：该 worker 自己的 approved L1（必需）
  - PM：优先 PM approved L1；若缺失可用 approved PM conclusion 作为 PM primary material
第 2 层：最终报告正文和 PM 最终结论
第 3 层：和问题相关的其他 approved worker L1 片段
禁止层：raw/provider/debug/receipt/hash/manifest/Mongo/OpenViking 协议文本
```

材料 resolver 只能从已批准、读者可见的报告材料入口读取：

- approved final report Markdown / reader-visible body。
- PM material 或 PM 最终结论。
- 当前选择的 worker 的 approved L1 reader-visible body。
- 其它 approved L1 的 reader-visible body 中与问题相关的片段。

禁止通过路径扫描 `raw/`、`provider/`、`debug/`、`evidence/`、receipt、hash、manifest 或 OpenViking/Mongo 协议目录拼材料。内部 refs 只能用于定位和审计，不能作为模型可见正文。

示例：

```text
用户选择：市场分析师
用户问：为什么技术面没让结论变成买入？
```

默认材料：

- `@组合经理`：PM L1（优先）或 approved PM conclusion（兜底 primary）+ 最终报告 + 相关片段。
- 非 PM worker（例：`market_analyst`）：该 worker L1（必需）+ 最终报告 + PM 结论 + 相关片段。

### 6.4 Prompt 规则

报告阅读 worker 聊天使用专用 prompt，不是 workflow stage prompt。

专用 prompt 的权威属于 agent 配置，例如 `agents/<worker>/chat/REPORT.md` 或等价共享 agent chat 配置。Python 只能选择 approved 自然语言材料并注入运行变量，不能在 `src` 中手写 worker 业务 prompt，也不能把 workflow stage prompt 改造成聊天 prompt。

专用 prompt 只做三件事：

```text
你正在以指定 worker 身份回答用户对已保存报告的聊天问题。
只能基于提供的已保存报告材料和 approved worker 材料回答。
不得声称重新运行 workflow、重新调用工具、改写报告或生成新的正式投资结论。
```

不得把以下内容放入模型可见 prompt：

- `RuntimeTarget`
- `ReportSubmission`
- OpenViking target block
- URI/hash/receipt/L1/L2/manifest 协议文本
- provider attempts/raw payload
- tool protocol audit prose
- workflow stage checklist

### 6.5 工具规则

报告阅读 worker 聊天第一版不暴露 model-visible 工具。

材料选择由 claw-trade 后端基于已保存 approved materials 完成：

- 可以读取 final report。
- 可以读取 worker appendix / approved L1。
- 可以做确定性片段选择。
- 可以在可用时使用已建索引做检索，但检索失败不得伪造命中。

模型只看到最终选出的自然语言材料。

### 6.6 历史报告材料缺失

旧报告可能没有保存完整 worker L1。

规则：

- 当前选择的 worker L1 缺失时，不得假装该 worker 有原始判断。
- 可以提示“这份报告没有保存该角色的独立材料”。
- 如最终报告和 PM 结论存在，用户可在选择器中切换到“组合经理”，或基于最终报告提问。
- 不自动用最终报告冒充当前选择的 worker 的 L1。

### 6.7 失败语义

| 场景 | 用户结果 | 行为 |
|---|---|---|
| 报告不存在 | “没有找到这份报告。” | 不猜最近报告 |
| 报告未完成 | “报告完成后才能和 worker 聊。” | 不读 running 材料 |
| 当前选择的 worker 材料缺失 | “这份报告没有保存该角色的独立材料。” | 不伪造 L1 |
| 材料过长 | “材料过长，暂时无法回答。” | 不自动摘要压缩兜底 |
| OpenClaw chat 失败 | “worker 聊天暂不可用。” | 不用 Python 代答 |

## 7. 后端模型建议

字段名是建议名，具体实现可按代码风格调整。

```text
WorkerChatMode:
  generic_worker_chat
  report_worker_chat

WorkerChatRequest:
  request_id
  mode
  worker_id
  text
  report_id?       # only report_worker_chat
  conversation_id
  client_source    # ui / wechat / api

WorkerChatTurn:
  turn_id
  request_id
  mode
  worker_id
  user_text
  report_id?
  run_id?
  material_refs_internal
  openclaw_session_id
  provider_request_ref_internal?
  reply_text
  status
  failure_code?
```

用户 DTO：

```text
WorkerChatReplyForUser:
  kind = worker_chat_reply
  workerDisplayName
  text
  mode
```

用户 DTO 不包含：

- worker internal path
- report local path
- material refs
- provider request id
- hash
- manifest
- receipt
- run directory

### 7.1 建议文件落点

本节文件名是可编码建议名；实现时可按现有代码风格微调，但职责边界不能漂移。

```text
src/claw_trade/ui_backend/worker_chat_catalog.py
  -> 7 个 worker 的确定性 catalog、选择器内中文别名搜索、菜单 DTO。

src/claw_trade/ui_backend/worker_chat_models.py
  -> WorkerChatRequest / WorkerChatTurn / WorkerChatReplyForUser 等后端 DTO。

src/claw_trade/ui_backend/worker_chat_controller.py
  -> 统一入口；只做校验、分派、保存和用户 DTO 渲染。

src/claw_trade/ui_backend/worker_chat_openclaw.py
  -> OpenClaw agent chat seam client；负责 session key、chat send、runtime proof refs。

src/claw_trade/ui_backend/report_worker_chat_context.py
  -> completed report 校验、approved reader-visible 材料 resolver、片段选择。

src/claw_trade/ui_backend/worker_chat_store.py
  -> 聊天 turn 幂等保存；不得写 report artifact 或 handoff material。

agents/<worker>/chat/REPORT.md 或共享 agent chat 配置
  -> 报告阅读 worker 聊天专用 prompt 权威来源。

web/research-ui/src/api/contracts.ts
  -> WorkerChatRequest / WorkerChatReplyForUser 前端合同。

web/research-ui/src/components/WorkerSelector.tsx
  -> 7 worker 选择器；默认显示组合经理。
```

明确不建议继续扩展旧 `report_qa` 作为正式入口。若保留兼容层，只能薄转发到 worker chat controller，并且必须补 `worker_id` 必填校验；不能让旧路径绕过 worker 身份。

### 7.2 后端 DTO 伪码

```python
WorkerChatMode = Literal["generic_worker_chat", "report_worker_chat"]

AllowedWorkerId = Literal[
    "portfolio_manager",
    "research_manager",
    "market_analyst",
    "fundamental_analyst",
    "news_analyst",
    "social_analyst",
    "risk_moderator",
]

@dataclass(frozen=True)
class WorkerChatRequest:
    request_id: str
    mode: WorkerChatMode
    worker_id: str
    text: str
    conversation_id: str
    client_source: Literal["ui", "wechat", "api"]
    report_id: str | None = None


@dataclass(frozen=True)
class WorkerChatInternalContext:
    mode: WorkerChatMode
    worker_id: AllowedWorkerId
    request_id: str
    conversation_id: str
    report_id: str | None
    run_id: str | None
    materials: tuple["WorkerChatMaterial", ...]
    openclaw_session_key: str
    prompt_profile: Literal["openclaw_default_agent_chat", "report_worker_chat"]


@dataclass(frozen=True)
class WorkerChatMaterial:
    material_kind: Literal[
        "final_report",
        "pm_conclusion",
        "worker_l1",
        "related_worker_l1_snippet",
    ]
    worker_id: str | None
    text: str
    internal_ref: str
    reader_visible: bool


@dataclass(frozen=True)
class WorkerChatTurn:
    turn_id: str
    request_id: str
    mode: WorkerChatMode
    worker_id: AllowedWorkerId
    user_text: str
    reply_text: str
    report_id: str | None
    run_id: str | None
    status: Literal["succeeded", "failed"]
    failure_code: str | None
    request_fingerprint: str
    openclaw_session_key: str | None
    provider_request_ref_internal: str | None
    material_refs_internal: tuple[str, ...]


@dataclass(frozen=True)
class WorkerChatReplyForUser:
    kind: Literal["worker_chat_reply"]
    mode: WorkerChatMode
    workerDisplayName: str
    text: str
```

字段硬规则：

- `worker_id` 后端必填；缺失返回 400。
- `generic_worker_chat.report_id` 必须为空；出现则返回 400，避免普通聊天偷绑报告。
- `report_worker_chat.report_id` 必填，并且必须指向 completed report。
- `WorkerChatReplyForUser` 不返回内部 id、ref、路径、hash、manifest、provider request id。

## 8. 控制器建议

建议新增统一控制器：

```text
WorkerChatController.handle(request)
```

控制流：

```text
handle(request):
  if not request.worker_id:
      return api_error(400, "worker_id is required")

  worker = resolve_chat_worker(request.worker_id)
  if worker not allowed:
      return user_error("这个角色暂不可用")

  if request.mode == generic_worker_chat:
      maybe_reject_report_question_without_report(request.text)
      context = build_generic_worker_chat_context(request, worker)
      result = openclaw.default_agent_chat(context, request.text)
      return save_and_render(result)

  if request.mode == report_worker_chat:
      report = load_completed_report(request.report_id)
      materials = build_report_worker_chat_materials(report, worker, request.text)
      context = build_report_worker_chat_context(request, worker, report, materials)
      result = openclaw.report_worker_chat(context, request.text)
      return save_and_render(result)
```

禁止：

- 后端为缺失 `worker_id` 补默认 worker。
- `generic_worker_chat` 调 report repository 猜上下文。
- `report_worker_chat` 走 `/report` workflow runner。
- 任一模式用 Python 改写 worker 回复。
- 任一模式失败后 fallback 到普通助手回答。

### 8.1 总入口函数伪码

```python
class WorkerChatController:
    def handle(self, payload: Mapping[str, object]) -> WorkerChatReplyForUser:
        request = parse_worker_chat_request(payload)
        validation = validate_worker_chat_request(request)
        if validation.failed:
            raise UiProductError(validation.code, validation.user_message)

        fingerprint = compute_request_fingerprint(request)
        cached = store.get_by_request_id(request.request_id)
        if cached is not None:
            if cached.request_fingerprint != fingerprint:
                raise UiProductError("WORKER_CHAT_IDEMPOTENCY_CONFLICT", "请求编号冲突，请刷新后重试。")
            return render_worker_chat_reply(cached)

        worker = catalog.require_allowed_worker(request.worker_id)

        if request.mode == "generic_worker_chat":
            maybe_reject_report_question_without_report(request.text)
            context = build_generic_worker_chat_context(request, worker)
            openclaw_result = openclaw_chat.send_generic_worker_chat(context, request.text)

        elif request.mode == "report_worker_chat":
            report = report_context.load_completed_report(request.report_id)
            materials = report_context.build_report_worker_chat_materials(
                report=report,
                worker_id=worker.worker_id,
                user_question=request.text,
            )
            context = build_report_worker_chat_context(request, worker, report, materials)
            openclaw_result = openclaw_chat.send_report_worker_chat(context, request.text)

        else:
            raise UiProductError("WORKER_CHAT_MODE_UNSUPPORTED", "worker 聊天模式不可用。")

        turn = materialize_worker_chat_turn(
            request=request,
            context=context,
            request_fingerprint=fingerprint,
            openclaw_result=openclaw_result,
        )
        stored_turn = store.save_turn_if_absent(turn)
        if stored_turn.status != "succeeded":
            raise UiProductError("WORKER_CHAT_UNAVAILABLE", "worker 聊天暂不可用。")
        return render_worker_chat_reply(stored_turn)
```

请求 fingerprint 只用于幂等冲突判断，必须由用户请求的稳定语义字段生成，不能包含 provider ref、材料 ref、时间戳或运行中诊断字段：

```python
def compute_request_fingerprint(request: WorkerChatRequest) -> str:
    return stable_sha256_json(
        {
            "mode": request.mode,
            "worker_id": request.worker_id,
            "text": request.text,
            "conversation_id": request.conversation_id,
            "client_source": request.client_source,
            "report_id": request.report_id,
        }
    )
```

`handle()` 不允许调用：

- `/report` workflow runner。
- `agent.runSingleWorker` workflow command。
- report artifact writer。
- PM decision writer。
- worker L1 artifact writer。
- `/select` decision writer。
- Python direct LLM 或普通助手 fallback。

### 8.2 请求解析和校验函数

```python
def parse_worker_chat_request(payload: Mapping[str, object]) -> WorkerChatRequest:
    return WorkerChatRequest(
        request_id=require_non_empty_str(payload, "requestId"),
        mode=require_mode(payload, "mode"),
        worker_id=require_non_empty_str(payload, "workerId"),
        text=require_non_empty_str(payload, "text"),
        conversation_id=require_non_empty_str(payload, "conversationId"),
        client_source=require_client_source(payload, "clientSource"),
        report_id=optional_non_empty_str(payload, "reportId"),
    )


def validate_worker_chat_request(request: WorkerChatRequest) -> ValidationResult:
    if not request.worker_id:
        return failed("WORKER_ID_REQUIRED", "请选择聊天对象。", http_status=400)

    if not catalog.is_allowed_worker(request.worker_id):
        return failed("WORKER_NOT_ALLOWED", "这个角色暂不可用。", http_status=400)

    if request.mode == "generic_worker_chat" and request.report_id:
        return failed("GENERIC_CHAT_REPORT_CONTEXT_FORBIDDEN", "普通 worker 聊天不能绑定报告。", http_status=400)

    if request.mode == "report_worker_chat" and not request.report_id:
        return failed("REPORT_ID_REQUIRED", "请先打开一份已完成报告。", http_status=400)

    return passed()
```

请求解析不得调用正文 worker 解析函数。用户手动输入 `@xxx` 但没有从选择列表选择时，`text` 原样进入普通消息正文；后端只信任 `workerId` 字段，不能返回冲突错误。

不得把自然语言文本交给 LLM 或选择器搜索逻辑判断 worker。

### 8.3 Worker catalog 函数

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


def list_worker_chat_menu() -> tuple[WorkerChatMenuItem, ...]:
    return tuple(
        WorkerChatMenuItem(display_name=item.display_name, worker_id=item.worker_id)
        for item in ALLOWED_WORKER_CHAT_CATALOG
    )


def require_allowed_worker(worker_id: str) -> WorkerChatCatalogEntry:
    entry = CATALOG_BY_ID.get(worker_id)
    if entry is None:
        raise UiProductError("WORKER_NOT_ALLOWED", "这个角色暂不可用。")
    return entry
```

测试必须断言以下 worker 不在 menu，也不能通过选择器搜索出现：

```text
bull_researcher
bear_researcher
trader
risk_challenger
risk_guardian
report_polisher
selection_strategist
selection_skeptic
selection_manager
selection_portfolio_manager
```

### 8.4 OpenClaw agent chat seam 函数

OpenClaw agent chat seam 的实现可以复用现有 gateway call，但必须证明 worker 身份来自 session/API，而不是写进 prompt。

```python
@dataclass(frozen=True)
class OpenClawAgentChatRequest:
    worker_id: str
    session_key: str
    user_message: str
    prompt_profile: str
    prompt_variables: Mapping[str, object]
    tool_policy: Literal["openclaw_default", "report_chat_no_tools"]
    visible_tools: tuple[str, ...] | None
    idempotency_key: str
    capture_provider_payload: bool


@dataclass(frozen=True)
class OpenClawAgentChatResponse:
    status: Literal["succeeded", "failed"]
    worker_id: str
    session_key: str
    request_id: str | None
    provider_request_ref: str | None
    provider_payload_ref: str | None
    prompt_profile: str | None
    visible_tools: tuple[str, ...] | None
    reply_text: str | None
    failure_code: str | None
    error_message: str | None


class OpenClawWorkerChatClient:
    def assert_agent_chat_seam_available(self) -> None:
        methods = gateway.list_methods()
        if not supports_agent_chat(methods):
            raise StopForHuman("OpenClaw 缺少默认 agent chat seam，不能复用 /report stage prompt 顶上。")
        if not gateway.supports_chat_contract_fields(
            required_fields=(
                "worker_id",
                "session_key",
                "user_message",
                "prompt_profile",
                "prompt_variables",
                "tool_policy",
                "visible_tools",
                "idempotency_key",
                "capture_provider_payload",
            )
        ):
            raise StopForHuman("OpenClaw gateway 不支持 worker chat 结构化合同字段，不能继续实现。")

    def build_session_key(self, *, mode: WorkerChatMode, worker_id: str, report_id: str | None, conversation_id: str) -> str:
        if mode == "generic_worker_chat":
            return f"agent:{worker_id}:generic:{safe_key(conversation_id)}"
        if mode == "report_worker_chat":
            return f"agent:{worker_id}:report:{safe_key(report_id)}:{safe_key(conversation_id)}"
        raise AssertionError("unsupported worker chat mode")

    def send_generic_worker_chat(self, context: WorkerChatInternalContext, text: str) -> OpenClawAgentChatResponse:
        request = OpenClawAgentChatRequest(
            worker_id=context.worker_id,
            session_key=context.openclaw_session_key,
            user_message=text,
            prompt_profile="openclaw_default_agent_chat",
            prompt_variables={},
            tool_policy="openclaw_default",
            visible_tools=None,
            idempotency_key=context.request_id,
            capture_provider_payload=True,
        )
        return self._send_agent_chat(request)

    def send_report_worker_chat(self, context: WorkerChatInternalContext, text: str) -> OpenClawAgentChatResponse:
        variables = build_report_worker_chat_prompt_variables(context.materials, text)
        request = OpenClawAgentChatRequest(
            worker_id=context.worker_id,
            session_key=context.openclaw_session_key,
            user_message=text,
            prompt_profile="report_worker_chat",
            prompt_variables=variables,
            tool_policy="report_chat_no_tools",
            visible_tools=tuple(),
            idempotency_key=context.request_id,
            capture_provider_payload=True,
        )
        return self._send_agent_chat(request)

    def _send_agent_chat(self, request: OpenClawAgentChatRequest) -> OpenClawAgentChatResponse:
        try:
            result = gateway.chat_send(
                worker_id=request.worker_id,
                session_key=request.session_key,
                user_message=request.user_message,
                prompt_profile=request.prompt_profile,
                prompt_variables=request.prompt_variables,
                tool_policy=request.tool_policy,
                visible_tools=request.visible_tools,
                idempotency_key=request.idempotency_key,
                capture_provider_payload=request.capture_provider_payload,
            )
            return parse_openclaw_chat_result(result)
        except OpenClawGatewayError as exc:
            return OpenClawAgentChatResponse(
                status="failed",
                worker_id=request.worker_id,
                session_key=request.session_key,
                request_id=None,
                provider_request_ref=None,
                provider_payload_ref=None,
                prompt_profile=request.prompt_profile,
                visible_tools=None,
                reply_text=None,
                failure_code=exc.code or "OPENCLAW_CHAT_FAILED",
                error_message=str(exc),
            )
```

`aliases` 只用于 UI 选择列表内搜索，例如用户打开选择器后输入“市场”能筛到“市场分析师”。它不用于解析消息正文，也不允许把手打 `@市场` 转换成 `worker_id`。

Stop condition：

```python
if only_available_path == "agent.runSingleWorker":
    stop("不能用 workflow stage command 伪装 worker 聊天；先确认或补 OpenClaw 通用 agent chat seam。")
```

如果必须修改 `third_party/openclaw`，只允许做通用 agent chat seam，且必须按 AGENTS 的 OpenClaw runtime seam 验证顺序执行。

### 8.5 普通 worker 聊天上下文函数

```python
def build_generic_worker_chat_context(
    request: WorkerChatRequest,
    worker: WorkerChatCatalogEntry,
) -> WorkerChatInternalContext:
    assert request.mode == "generic_worker_chat"
    if request.report_id is not None:
        raise UiProductError("GENERIC_CHAT_REPORT_CONTEXT_FORBIDDEN", "普通 worker 聊天不能绑定报告。")

    session_key = openclaw_chat.build_session_key(
        mode=request.mode,
        worker_id=worker.worker_id,
        report_id=None,
        conversation_id=request.conversation_id,
    )

    return WorkerChatInternalContext(
        mode="generic_worker_chat",
        worker_id=worker.worker_id,
        request_id=request.request_id,
        conversation_id=request.conversation_id,
        report_id=None,
        run_id=None,
        materials=(),
        openclaw_session_key=session_key,
        prompt_profile="openclaw_default_agent_chat",
    )
```

普通聊天如果用户询问“这份报告”“上次报告”“最近那个票”，后端只可返回可读错误：

```python
def maybe_reject_report_question_without_report(text: str) -> None:
    if looks_like_specific_report_question(text):
        raise UiProductError("REPORT_CONTEXT_REQUIRED", "请先打开一份报告后再问。")
```

不能猜最近报告，不能读取 report repository。

### 8.6 报告阅读材料 resolver 函数

```python
def load_completed_report(report_id: str) -> CompletedReport:
    report = report_repository.get_report(report_id)
    if report is None:
        raise UiProductError("REPORT_NOT_FOUND", "没有找到这份报告。")
    if report.status != "completed":
        raise UiProductError("REPORT_NOT_COMPLETED", "报告完成后才能和 worker 聊。")
    return report


def build_report_worker_chat_materials(
    *,
    report: CompletedReport,
    worker_id: str,
    user_question: str,
) -> tuple[WorkerChatMaterial, ...]:
    materials: list[WorkerChatMaterial] = []

    if worker_id == "portfolio_manager":
        pm_l1 = load_approved_worker_l1_reader_body(report.run_id, "portfolio_manager")
        if pm_l1 is not None:
            materials.append(with_primary_role(pm_l1, primary_role="pm_l1"))
        else:
            pm_primary = load_pm_conclusion_reader_body(report.run_id)
            if pm_primary is None:
                raise UiProductError("PM_PRIMARY_MISSING", "这份报告没有可用的组合经理主材料。")
            materials.append(with_primary_role(pm_primary, primary_role="pm_conclusion"))
    else:
        worker_l1 = load_approved_worker_l1_reader_body(report.run_id, worker_id)
        if worker_l1 is None:
            raise UiProductError("WORKER_L1_MISSING", "这份报告没有保存该角色的独立材料。")
        materials.append(worker_l1)

    final_report = load_final_report_reader_body(report.report_id)
    if final_report is None:
        raise UiProductError("FINAL_REPORT_MISSING", "报告材料不完整，暂时无法聊天。")
    add_unique_material(materials, final_report)

    pm_conclusion = load_pm_conclusion_reader_body(report.run_id)
    if pm_conclusion is not None:
        add_unique_material(materials, pm_conclusion)
    else:
        record_optional_material_gap(report.report_id, "pm_conclusion_missing")

    related = select_related_worker_l1_snippets(
        report=report,
        requested_worker_id=worker_id,
        question=user_question,
        max_snippets=3,
    )
    materials.extend(related)

    checked = tuple(assert_model_visible_material_safe(item) for item in materials)
    ensure_material_budget(checked, question=user_question)
    return checked
```

PM 结论缺失策略：

- `portfolio_manager` 聊天：PM L1 和 approved PM conclusion 至少要有一个作为 PM primary；两者都缺失才返回 `PM_PRIMARY_MISSING`。
- 非 PM worker 聊天：结构化 `worker_id` 指定 worker 的 L1 和 final report 是硬要求；approved PM conclusion 默认加入，缺失时记录 internal gap，但不阻断，也不得用 final report 冒充 PM conclusion。
- 用户 DTO 不暴露 `pm_conclusion_missing` 这类 internal gap；它只用于诊断和验收。

`select_related_worker_l1_snippets()` 必须走 approved reader-visible index，不可扫目录：

```python
def select_related_worker_l1_snippets(
    *,
    report: CompletedReport,
    requested_worker_id: str,
    question: str,
    max_snippets: int,
) -> tuple[WorkerChatMaterial, ...]:
    query = build_reader_visible_query(report_id=report.report_id, run_id=report.run_id, question=question)
    hits = approved_reader_visible_index.search(query=query, limit=20)
    if hits.error is not None:
        log_info("WORKER_CHAT_SNIPPET_RETRIEVAL_FAILED", report_id=report.report_id, reason=hits.error.code)
        return tuple()

    ranked = sort_hits(hits.items, key=("relevance_desc", "recency_desc", "worker_priority"))
    deduped = dedupe_by_material_ref_and_text(ranked)
    filtered = [
        hit
        for hit in deduped
        if hit.worker_id != requested_worker_id and hit.reader_visible and hit.status == "approved"
    ]
    selected = filtered[:max_snippets]
    return tuple(to_related_snippet_material(hit) for hit in selected)
```

报告阅读上下文构造：

```python
def build_report_worker_chat_context(
    request: WorkerChatRequest,
    worker: WorkerChatCatalogEntry,
    report: CompletedReport,
    materials: tuple[WorkerChatMaterial, ...],
) -> WorkerChatInternalContext:
    assert request.mode == "report_worker_chat"
    if not request.report_id:
        raise UiProductError("REPORT_ID_REQUIRED", "请先打开一份已完成报告。")
    if report.status != "completed":
        raise UiProductError("REPORT_NOT_COMPLETED", "报告完成后才能和 worker 聊。")

    session_key = openclaw_chat.build_session_key(
        mode=request.mode,
        worker_id=worker.worker_id,
        report_id=report.report_id,
        conversation_id=request.conversation_id,
    )

    return WorkerChatInternalContext(
        mode="report_worker_chat",
        worker_id=worker.worker_id,
        request_id=request.request_id,
        conversation_id=request.conversation_id,
        report_id=report.report_id,
        run_id=report.run_id,
        materials=materials,
        openclaw_session_key=session_key,
        prompt_profile="report_worker_chat",
    )
```

读取函数只允许通过 approved reader-visible 入口：

```python
def load_approved_worker_l1_reader_body(run_id: str, worker_id: str) -> WorkerChatMaterial | None:
    record = approved_material_index.find_worker_l1(run_id=run_id, worker_id=worker_id)
    if record is None or record.status != "approved":
        return None
    body = approved_material_reader.read_reader_visible_body(record.material_ref)
    return WorkerChatMaterial("worker_l1", worker_id, body, record.internal_ref, True)
```

禁止实现：

```python
Path(run_dir).glob("**/raw*")
Path(run_dir).glob("**/provider*")
Path(run_dir).glob("**/debug*")
read_json("provider-payload.json")
read_manifest_text_into_prompt(...)
```

材料安全检查：

```python
def assert_model_visible_material_safe(material: WorkerChatMaterial) -> WorkerChatMaterial:
    if not material.reader_visible:
        raise BoundaryError("non_reader_visible_material")
    if looks_like_machine_protocol_block(material.text):
        raise BoundaryError("protocol_text_leaked_to_worker_chat")
    return material


def looks_like_machine_protocol_block(text: str) -> bool:
    if has_protocol_header_block(text, headers=("[ApprovedMaterials]", "[ProviderPayload]", "[RuntimeTarget]")):
        return True
    if contains_fenced_machine_json(
        text,
        required_any_keys=(
            "RuntimeTarget",
            "ReportSubmission",
            "material_id",
            "content_sha256",
            "receipt",
            "manifest",
            "provider_request_id",
        ),
    ):
        return True
    if contains_machine_kv_lines(
        text,
        keys=(
            "uri:",
            "hash:",
            "receipt:",
            "manifest:",
            "content_sha256:",
            "provider_attempt:",
            "provider_payload_ref:",
        ),
    ):
        return True
    if contains_protocol_uri(text, prefixes=("openviking://", "mongodb://")):
        return True
    return False
```

### 8.7 报告聊天 prompt 输入函数

Python 只拼装运行变量和 approved 自然语言材料；prompt 模板从 agent config 读取。

```python
def build_report_worker_chat_prompt_variables(
    materials: tuple[WorkerChatMaterial, ...],
    user_question: str,
) -> Mapping[str, object]:
    safe_materials = [assert_model_visible_material_safe(item) for item in materials]
    return {
        "saved_report_materials": format_material_blocks(safe_materials),
        "user_question": user_question,
    }
```

`send_report_worker_chat()` 必须把 `prompt_profile=report_worker_chat + prompt_variables + user_message` 一起传给 OpenClaw；由 OpenClaw/agent config 渲染最终聊天 prompt。Python 不得返回完整业务 prompt 字符串，也不得在 `src` 里拼 workflow stage 风格大段业务 prompt。

### 8.8 OpenClaw 结果落 turn 函数

```python
def materialize_worker_chat_turn(
    *,
    request: WorkerChatRequest,
    context: WorkerChatInternalContext,
    request_fingerprint: str,
    openclaw_result: OpenClawAgentChatResponse,
) -> WorkerChatTurn:
    if openclaw_result.status != "succeeded":
        return WorkerChatTurn(
            turn_id=new_turn_id(),
            request_id=request.request_id,
            mode=request.mode,
            worker_id=context.worker_id,
            user_text=request.text,
            reply_text="",
            report_id=context.report_id,
            run_id=context.run_id,
            status="failed",
            failure_code=openclaw_result.failure_code or "WORKER_CHAT_UNAVAILABLE",
            request_fingerprint=request_fingerprint,
            openclaw_session_key=context.openclaw_session_key,
            provider_request_ref_internal=openclaw_result.provider_request_ref,
            material_refs_internal=tuple(item.internal_ref for item in context.materials),
        )

    reply_text = extract_openclaw_reply_text(openclaw_result)
    if not reply_text:
        raise UiProductError("WORKER_CHAT_EMPTY_REPLY", "worker 聊天暂不可用。")

    return WorkerChatTurn(
        turn_id=new_turn_id(),
        request_id=request.request_id,
        mode=request.mode,
        worker_id=context.worker_id,
        user_text=request.text,
        reply_text=reply_text,
        report_id=context.report_id,
        run_id=context.run_id,
        status="succeeded",
        failure_code=None,
        request_fingerprint=request_fingerprint,
        openclaw_session_key=context.openclaw_session_key,
        provider_request_ref_internal=openclaw_result.provider_request_ref,
        material_refs_internal=tuple(item.internal_ref for item in context.materials),
    )
```

`extract_openclaw_reply_text()` 只能读取 OpenClaw 返回的 worker 回复；不得让 Python 重新摘要、补写或润色空回复。

失败 turn 策略：保存 `status=failed` 的 turn（含失败码与 provider ref）用于审计与重放去重，但失败 turn 不写入任何正式 artifact，不进入 report handoff，不更新 PM 结论，不更新 worker L1。

### 8.9 聊天记录与幂等函数

```python
class WorkerChatStore:
    def get_by_request_id(self, request_id: str) -> WorkerChatTurn | None:
        ...

    def save_turn_if_absent(self, turn: WorkerChatTurn) -> WorkerChatTurn:
        existing = self.get_by_request_id(turn.request_id)
        if existing is not None:
            if existing.request_fingerprint != turn.request_fingerprint:
                raise UiProductError("WORKER_CHAT_IDEMPOTENCY_CONFLICT", "请求编号冲突，请刷新后重试。")
            return existing
        self._write_worker_chat_record(turn)
        return turn
```

`_write_worker_chat_record()` 只能写 worker chat store，不允许写：

```text
reports/<report_id>/final.md
reports/<report_id>/pm-decision.*
runs/<run_id>/materials/worker_l1.*
selection decision store
report handoff materials
OpenViking approved material
```

材料 ref 和 provider request ref 可保存为 internal diagnostics，但用户 DTO 不暴露。

### 8.10 用户 DTO 渲染函数

```python
def render_worker_chat_reply(turn: WorkerChatTurn) -> WorkerChatReplyForUser:
    worker = catalog.require_allowed_worker(turn.worker_id)
    return WorkerChatReplyForUser(
        kind="worker_chat_reply",
        mode=turn.mode,
        workerDisplayName=worker.display_name,
        text=turn.reply_text,
    )
```

DTO redaction 测试必须失败于这些字段：

```text
worker_id
run_id
report local path
material_refs_internal
provider_request_ref_internal
content_sha256
manifest
receipt
openclaw_session_key
```

### 8.11 前端请求构造伪码

主工作台：

```ts
const DEFAULT_WORKER_ID = "portfolio_manager";

function buildGenericWorkerChatRequest(inputText: string, selectedWorkerId?: WorkerId): WorkerChatRequest {
  const workerId = selectedWorkerId ?? DEFAULT_WORKER_ID;
  return {
    requestId: crypto.randomUUID(),
    mode: "generic_worker_chat",
    workerId,
    text: inputText,
    conversationId: currentConversationId,
    clientSource: "ui",
  };
}
```

报告阅读区：

```ts
function buildReportWorkerChatRequest(inputText: string, reportId: string, selectedWorkerId?: WorkerId): WorkerChatRequest {
  const workerId = selectedWorkerId ?? DEFAULT_WORKER_ID;
  return {
    requestId: crypto.randomUUID(),
    mode: "report_worker_chat",
    workerId,
    reportId,
    text: inputText,
    conversationId: `report:${reportId}`,
    clientSource: "ui",
  };
}
```

前端默认值必须体现在发送出去的 `workerId` 字段里。不能发送空 worker 让后端补默认。

### 8.12 Runtime proof 收集函数

```python
def assert_worker_chat_runtime_proof(
    *,
    mode: WorkerChatMode,
    requested_worker_id: str,
    openclaw_result: OpenClawAgentChatResponse,
) -> None:
    payload = provider_payload_reader.read(openclaw_result.provider_request_ref)

    assert payload.worker_id == requested_worker_id
    assert payload.session_key.startswith(f"agent:{requested_worker_id}:")
    assert payload.messages

    text = "\n".join(message.content_text for message in payload.messages)
    assert "/report workflow" not in text
    assert "[ApprovedMaterials]" not in text
    assert "single_worker_minimal" not in text
    assert not contains_runtime_wrapper_block(text)
    assert not contains_provider_payload_or_attempt_block(text)
    assert not contains_hash_receipt_manifest_block(text)

    if mode == "generic_worker_chat":
        assert payload.prompt_profile == "openclaw_default_agent_chat"
        assert payload.visible_tools == openclaw_agent_chat_default_tools(requested_worker_id)
    if mode == "report_worker_chat":
        assert payload.prompt_profile == "report_worker_chat"
        assert payload.visible_tools == ()
```

`contains_*_block()` 检查的是协议块、URI/hash/receipt/manifest 机器字段或 provider/debug envelope，不是对自然语言做粗暴禁词扫描。验收目标是防止机器协议正文泄露给模型。

这类 proof 是首版验收证据，不是普通单元测试替代品。若拿不到真实 provider payload，只能标记 `NOT VERIFIED`，不能宣称聊天路径完成。

## 9. UI 设计要求

### 9.1 主工作台

主工作台聊天区显示 worker 选择器。

默认：

```text
@组合经理
```

用户可以切换 7 个开放角色。

用户输入 `@` 时，只显示这 7 个 worker 的选择列表。只有从列表选择才会改变当前 worker；手动输入 `@xxx` 不改变当前 worker，也不影响发送请求里的 `workerId`。

发送时，前端必须把选择器当前值作为 `worker_id` 发送；后端不从自然语言中猜。

### 9.2 报告阅读区

报告阅读聊天区也显示同一组 worker。

默认：

```text
@组合经理
```

报告阅读区发起请求时必须带当前 `report_id`。

输入 `@` 的交互规则与主工作台一致：只弹 7 个 worker 的选择列表；手动输入 `@xxx` 不解析、不切换 worker。

### 9.3 文案

不要使用“追问”作为功能名。用户可见文案使用：

- worker 聊天
- 和组合经理聊
- 和市场分析师聊
- 当前聊天对象

可以在输入框旁显示：

```text
当前：组合经理
```

## 10. 测试与验收

### 10.1 单元测试

- worker catalog 只包含 7 个开放角色。
- 默认 worker 为 `portfolio_manager`。
- 用户 DTO 不暴露内部 id、路径、hash、manifest、provider id。
- 普通 worker 聊天缺少 worker_id 时，API 直调失败；UI 只能在发送前显式补齐。
- 不允许 LLM 选择 worker。
- `maybe_reject_report_question_without_report` 已接入 generic 分支，并在 OpenClaw 调用前执行。
- 同一 `request_id` 且 payload fingerprint 不一致时返回冲突，不得回放旧 turn。
- OpenClaw 失败 turn 保存为 `failed`，且不写正式 artifact。

### 10.2 合同测试

- `generic_worker_chat` 不包含 report id、run id、workflow stage、approved materials。
- `generic_worker_chat` 不调用 report workflow runner。
- `generic_worker_chat` 不使用 `/report` stage prompt。
- API 直调缺 `worker_id` 返回 400；只有 UI 层可以在发送前显式填入 `portfolio_manager`。
- 手打 `@xxx` 不解析、不改变选择器 workerId、不产生冲突错误；请求里的 `workerId` 仍来自选择器。
- `report_worker_chat` 必须绑定 completed report。
- `report_worker_chat` 必须传 `prompt_profile + prompt_variables + user_message` 到 OpenClaw，不能传 Python 拼出的完整业务 prompt。
- `report_worker_chat` 不调用 `/report` workflow runner。
- `report_worker_chat` prompt 不包含 raw/provider/debug/receipt/hash/manifest 协议文本。
- OpenClaw chat 请求/响应满足结构化字段合同（`worker_id/session_key/user_message/prompt_profile/prompt_variables/tool_policy/visible_tools/idempotency_key/capture_provider_payload`）。
- OpenClaw chat 响应包含 worker/session/request/provider payload 引用；若 gateway 不支持字段合同触发 stop condition。
- 聊天回复不写入 report artifact、PM decision、worker L1 或 `/select` decision。

### 10.3 Runtime Provider Proof

进入实现前或首版验收时，两种模式各至少跑一次 focused runtime proof：

- `generic_worker_chat`：主工作台普通 worker 聊天。
- `report_worker_chat`：已完成报告阅读区 worker 聊天。

每次 proof 必须捕获：

- mode。
- worker id。
- OpenClaw session id / agent chat session key。
- provider final messages。
- visible tools。
- request id / runtime marker（可用时）。
- 用户可见回复。

每次 proof 必须断言：

- worker 是请求字段指定 worker。
- 模型可见 prompt 不含 `/report` workflow stage prompt。
- 模型可见 prompt 不含 `[ApprovedMaterials]`、workflow runner、`single_worker_minimal` 或 report workflow handoff prompt。
- 模型可见 prompt 不含 raw/provider/debug/receipt/hash/manifest/OpenViking/Mongo 协议块或机器字段。
- visible tools 符合对应 OpenClaw agent chat 配置；`report_worker_chat` 第一版无 model-visible tools。
- 回复来自 OpenClaw worker turn，不是 Python 代答、摘要或 fallback。

### 10.4 集成测试

- 主工作台默认“组合经理”，发送普通 worker 聊天成功。
- 主工作台从选择列表切换“市场分析师”后，后端收到 `market_analyst`。
- 报告阅读区默认“组合经理”，请求带当前 `report_id`。
- 报告阅读区切换 worker 后，只读取该报告对应 approved materials。
- 材料缺失时返回可读错误，不伪造 worker L1。

### 10.5 UI 验收

最终 UI 验收必须用真实 Chrome 覆盖：

- 主工作台默认 worker。
- 主工作台切换 worker。
- 报告阅读区默认 worker。
- 报告阅读区发送 worker 聊天。
- 报告正文未改变。
- 没有触发新的 `/report` workflow。

## 11. Stop Conditions

遇到以下情况必须停问：

- 需要开放 7 个以外的 worker。
- 需要让 LLM 自动选择 worker。
- 需要普通 worker 聊天绑定最近报告或最近标的。
- 需要复用 `/report` workflow stage prompt 作为普通聊天 prompt。
- 需要把 worker 聊天回复写入正式报告或 PM 结论。
- 需要让报告阅读 worker 聊天重新调用工具或重新跑 workflow。
- 需要读取 raw/provider/debug/receipt/hash/manifest 文本给模型。
- 需要新增或收紧 runtime guard 来限制 `/report` PM 表达。
- OpenClaw gateway 不支持 worker chat 结构化合同字段。

## 12. 与 A 股选股设计的关系

Worker 聊天不是 A 股选股的一部分。

`/select` 的职责仍是：

```text
生成进入 /report / 观察 / 放弃的研究资源分配结论。
```

如果未来要支持“围绕 `/select` 结果和 selection worker 聊天”，必须另起设计，并先确认它是否仍属于本通用 worker 聊天框架。不得恢复旧 `A股选股 @worker 追问` 文档作为实现依据。
