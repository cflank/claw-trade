# claw-trade UI 实施任务清单

本文是 `docs/UI详细设计.md` 的实现入口清单。它不是新设计源，不新增产品或架构决策；所有任务必须回溯到 `AGENTS.md`、`docs/UI需求分析.md`、`docs/UI详细设计.md`、`docs/report_workflow_env_and_rounds_design.md`。

## 0. 执行红线

- 只实现普通用户投研工作台，不实现 OpenClaw 控制台。
- 普通聊天只透传 OpenClaw 普通聊天/session；不得静默创建 report workflow。
- 报告、定时报告、价格提醒必须先生成确认卡，用户显式确认后才创建。
- 报告追问只走 OpenClaw 普通聊天/session；不得触发 report workflow，不改写已保存报告。
- 运行中 workflow 不能被用户插话修改；新需求只能生成“完成后重新生成”的草稿/确认。
- PDF 只能格式化已保存 Markdown；不得重写、总结、补写报告。
- 报告完成摘要只能来自已保存报告、PM 最终结论和报告元数据；不得新增简报 worker。
- 失败任务不得进入左侧历史，也不得长期保留在右侧。
- 首版 claw-trade 后端可受控读写 `.env.local` 作为配置存储；前端、普通 UI API、workflow controller 不得直接读写 `.env.local`；UI 设置模型必须冻结生成 `RunRequest`，controller 只读 `RunRequest`。
- 普通 UI/API 必须返回 `*ForUser` DTO，不得直接序列化内部 store/model。
- 禁止 mock/stub/fake/fallback、占位骨架、伪成功、空实现作为通过标准。

普通 UI/API 响应边界：

- 使用明确的 `*ForUser` DTO 和禁止字段检查，避免把内部 store/model 直接返回给前端。
- 不做自然语言禁词扫描；报告正文、聊天回复和错误提示不得因为正常词汇命中而失败。

## 1. Implementation Coverage Matrix

| 来源章节 | 覆盖对象 | Phase | 任务编号 | 交付物 | 验证 | blocker/待确认状态 | 内部概念泄露红线 |
|---|---|---:|---|---|---|---|---|
| §0 设计依据与材料范围 | 材料边界、已确认事实、未知点、成功标准 | 1-6 | P1-00, P5-02, P6-03, AR-01 | 实施前检查清单、blocker 表、adversarial review | 文档 lint；任务评审逐项勾选 | 前端框架、UI 后端形态、`.env.local` 存储、PDF 模板、摘要规则、微信映射、LLM 配置入口、运行中不取消均已决策；微信官方插件存在，版本/Channel ID/官方安装步骤已确认；本机/项目运行态仍未按官方路径完成 clean 验收，真实扫码、在线状态、文件发送和入站消息仍需运行时验证；dev/fixed 测试 runtime 不加载外部插件只能作为测试证据缺口，不代表生产配置策略 | 不把运行时未探测能力写成已成功 |
| §1 设计摘要 | 首版目标与明确不做项 | 1-6 | P1-01, P2-02, P4-05, NT-01 | 首版范围测试、负向任务清单 | `uv run pytest tests/unit/ui/test_no_first_version_regression.py` | 无 | 不引入移动端、并行报告、小时级报告、简报 worker、失败任务管理页 |
| §2 架构边界 | claw-trade/UI/OpenClaw/数据源/PDF 职责 | 1-6 | P1-02, P2-03, P5-04, P6-01 | boundary adapter、bridge/service 接口 | 架构边界 contract 测试 | 普通微信内部映射为 `openclaw-weixin`；LLM 走 OpenClaw config/models；微信消息回调只接 OpenClaw，不自实现协议；不得因当前 CLI 不通而把微信协议搬进 claw-trade | 不显示 OpenClaw、OpenViking、MongoDB、worker id、provider attempt |
| §3 总体架构图 | Browser/backend/OpenClaw/data 模块关系 | 1-6 | P1-03, P2-01, P3-01, P5-01 | 模块目录、依赖图、接口分层 | import boundary 测试 | 已决策：参考/copy `claw-invest` 兼容的 web server / route / contract 形态，service contract 保持传输无关 | 前端只调 claw-trade 产品 API，不直连内部运行时 |
| §4 UI 信息架构 | 三栏、左侧历史、中间上下文、右侧详情 | 1,3 | P1-04, P3-02, P3-05 | 三栏组件、上下文 store、报告阅读器、右侧状态 | 组件测试/E2E 截图 | 已决策：参考/copy `claw-invest/web/research-ui` 兼容的 React/Vite/TypeScript 三栏 shell | 左侧不显示失败/运行/取消；右侧不长期保留失败 |
| §5 视觉设计规范 | token、字体、间距、组件视觉规则 | 1,3,5 | P1-05, P3-03, P5-05 | 设计 token、核心组件样式、报告正文样式 | `pnpm test`, `pnpm build` 或等价组件检查 | 已决策：参考 `claw-invest` 浅色投研终端风格和 TradingAgents-CN PDF 样式 | 状态栏不显示 worker id；数据源卡不显示内部路径 |
| §6 核心状态机 | ChatContext/ReportTask/ScheduledReport/PriceAlert/Channel/DataSource/PDF 状态 | 1-6 | P1-06, P2-04, P4-01, P4-02, P5-03, P6-02 | 状态机枚举、迁移函数、状态机测试 | `uv run pytest tests/unit/ui/test_*_state_machine.py` | 已决策：排队任务可取消，运行中 workflow 首版不取消 | 不伪造取消成功，不绕过完整报告队列 |
| §7 数据模型设计 | 内部模型与 `*ForUser` DTO | 1-6 | P1-07, P3-04, P4-03, P4-04, P5-06, P6-04 | DTO module、mapper、序列化红线测试 | `uv run pytest tests/contracts/test_ui_user_dto_redaction.py` | `ScheduledReportForUser`、`PriceAlertForUser` 已同步到详细设计；实现必须补 mapper | API 不返回 runId、dedupeKey、credentialRef、providerChannelId、artifact/hash/receipt/path |
| §8 API / 服务契约 | 普通 UI API、错误码、幂等、权限 | 1-6 | P1-08, P2-05, P3-06, P4-06, P5-07, P6-05 | API contract、OpenAPI/RPC schema、错误映射 | API contract 测试 | 已决策：claw-trade 提供本地产品 API；业务层仍写成传输无关 service contract | 所有普通 API 输出必须是 `*ForUser`，含 scheduled/price alert API |
| §9 模块级设计 | AppShell、Chat、Intent、Queue、Workflow、Repository、Settings 等模块 | 1-6 | P1-02, P1-03, P1-04, P1-08, P1-09, P2-01, P2-03, P2-05, P3-01, P3-04, P3-06, P4-02, P4-05, P5-03, P5-04, P5-05, P6-01, P6-06 | 模块实现和测试 | import/API 单测 | 前端目录与后端形态已决策：参考/copy `claw-invest` 兼容部分 | 禁止空骨架作为完成 |
| §10 函数级设计和伪码 | 关键函数行为、输入输出、副作用、错误 | 1-6 | P1-06, P1-08, P1-09, P1-10, P2-01, P2-02, P2-03, P2-05, P2-06, P2-07, P2-08, P3-01, P3-02, P3-04, P3-05, P3-06, P4-02, P4-03, P4-05, P5-03, P5-04, P5-05, P5-06, P6-01, P6-02, P6-03, P6-04 | 函数实现、伪码对应测试 | 每个函数至少一个正向和一个红线测试 | OpenClaw 微信/PDF 发送需运行时 probe；LLM 用 config/models | 不把伪码里的内部字段透给 UI |
| §11 错误处理和用户文案 | 内部错误映射、显示位置、只进日志错误 | 1-6 | P1-10, P1-07, P2-07, P3-03, P5-03, P5-04, P5-06, P6-02 | `ErrorMessageTranslator`、产品错误码表、字段边界测试 | `uv run pytest tests/unit/ui/test_error_message_translator.py` | 错误类别需随实际 bridge 扩展 | 禁止直接返回内部异常对象或内部模型字段 |
| §12 安全和隐私 | 密钥掩码、replace-only、`.env.local` 边界、保存 owner | 5 | P1-07, P5-04, P5-05, P5-07, P6-01, P6-02 | secret replace-only 流程、env allowlist 写入边界测试 | `uv run pytest tests/unit/ui/test_settings_security.py` | 已决策：首版后端受控读写 `.env.local`；前端/controller 禁止直接读写 | 不返回真实 key、credentialRef、本地路径、端口 |
| §13 测试计划 | 单元、组件、状态机、API、队列、数据源、PDF、Channel、E2E | 1-6 | T-01..T-09 | 测试目录与测试用例 | 对应 `uv run pytest` / `pnpm test` | live/provider 不作为首轮循环 | 禁止用 mock/stub/fake/fallback 证明真实能力 |
| §14 实施切分 | Phase 1-6 | 1-6 | P1-00..P6-06 | 可执行 phase 清单 | 每 phase 完成标准 | 未确认能力不得进入“已完成” | 不用占位骨架冒充 phase 完成 |
| §15 未决问题 | blocker、推荐决策、保守实现 | 1-6 | B-01..B-10 | blocker register、stop/ask gate | blocker 状态评审 | B-01/B-04/B-05/B-06/B-07/B-08/B-09/B-10 已关闭；B-02/B-03 是运行时能力探测/工程接入检查点 | blocker 不得伪装成可实现项 |

## 2. DTO 与普通 UI API 总要求

### 2.1 必补 DTO 缺口

`docs/UI详细设计.md` 已定义 `ScheduledReport` 和 `PriceAlert` 内部模型，但普通 API 不能直接返回它们。实现前必须新增：

```ts
interface ScheduledReportForUser {
  scheduledReportId: string;
  instrumentCode: string;
  instrumentName?: string | null;
  market: MarketProfile;
  frequency: "daily" | "weekly";
  timeOfDay: string;
  weekday?: number | null;
  notification: { channel: "wechat_clawbot" | "in_app"; enabled: boolean };
  state: "draft" | "active" | "due" | "enqueued" | "paused" | "deleted";
  nextRunAt?: string | null;
}

interface PriceAlertForUser {
  priceAlertId: string;
  instrumentCode: string;
  instrumentName?: string | null;
  market: MarketProfile;
  condition: {
    type: "price_threshold" | "percent_change";
    operator: "above" | "below" | "up_by" | "down_by";
    value: number;
    window?: "24h" | "intraday" | null;
  };
  notification: { channel: "wechat_clawbot" | "in_app"; enabled: boolean };
  state: "draft" | "active" | "checking" | "triggered" | "error" | "paused" | "closed" | "deleted";
  lastCheckedAt?: string | null;
  triggeredAt?: string | null;
  lastErrorMessage?: string | null;
}
```

必须新增 mapper：

```text
toScheduledReportForUser(schedule)
toPriceAlertForUser(alert)
```

### 2.2 普通 UI API 输出 DTO 要求

| API | 必须返回 | 禁止直接返回 |
|---|---|---|
| `sendChatMessage` | `ChatContextForUser`, `ChatMessageForUser[]`, `ReportQueueSnapshotForUser?` | `ChatContext.lockedWorkflowRunId`, raw OpenClaw response |
| `createIntentDraft` | `IntentDraftForUser`, `ConfirmationCard` | `sourceMessageId`, `workflowSettings`, `dedupeKey` |
| `confirmIntentDraft` | `ReportTaskForUser?`, `ScheduledReportForUser?`, `PriceAlertForUser?`, `ChatMessageForUser[]?` | `ScheduledReport`, `PriceAlert`, `runId`, `dedupeKey` |
| `enqueueReportTask` | `ReportTaskForUser`, `ReportQueueSnapshotForUser` | `ReportTask.runId`, `profile`, `priority`, `dedupeKey` |
| `cancelReportTask` | `ReportTaskForUser`, `ReportQueueSnapshotForUser` | workflow cancel internals |
| `getReportQueueSnapshot` | `ReportQueueSnapshotForUser` | failed terminal tasks, internal snapshot time if not needed |
| `listSavedReports` | `{ items: SavedReportForUser[], nextCursor? }` | failed/running/cancelled tasks |
| `getReportDetail` | `ReportDetailForUser` | `ReportArtifact`, path, hash, receipt |
| `askReportQuestion` | `{ text: string }` | report workflow run info |
| `createScheduledReport` | `ScheduledReportForUser` | `ScheduledReport.lastRunTaskId` |
| `pauseScheduledReport` | `ScheduledReportForUser` | internal schedule record |
| `resumeScheduledReport` | `ScheduledReportForUser` | internal schedule record |
| `deleteScheduledReport` | `{ deleted: true, scheduledReportId }` | internal schedule record |
| `runScheduledReportNow` | `{ task: ReportTaskForUser, queueSnapshot: ReportQueueSnapshotForUser }` | run request internals |
| `createPriceAlert` | `PriceAlertForUser` | `PriceAlert` |
| `pausePriceAlert` | `PriceAlertForUser` | internal alert record |
| `resumePriceAlert` | `PriceAlertForUser` | internal alert record |
| `deletePriceAlert` | `{ deleted: true, priceAlertId }` | internal alert record |
| `runPriceAlertNow` | `{ alert: PriceAlertForUser, triggered: boolean, message? }` | quote payload internals |
| `getReportChartEvidence` | `{ reportId, items: ChartEvidenceForUser[], summary }` | chart artifact/path/hash |
| `listDataSources` | `{ supportedTypes, instances: DataSourceInstanceForUser[] }` | `credentialRef`, raw payload |
| `testDataSource` | `{ state, healthEvent: DataSourceHealthEventForUser, canEnable }` | receipt, manifest, raw payload |
| `saveDataSourceInstance` | `DataSourceInstanceForUser` | secret ref, true key |
| `getChannelStatus` | `ChannelStatusForUser` | `providerChannelId` |
| `saveChannelConfigViaOpenClaw` | `{ status: ChannelStatusForUser, restartRequired?: boolean }` | OpenClaw config path/hash |
| `loadLlmSettings` | `LlmConfigDraft` with masked key | true key, OpenClaw raw config |
| `saveLlmConfigViaOpenClaw` | user-facing saved status | OpenClaw raw patch result |
| `testLlmViaOpenClaw` | `{ ok, userMessage, checkedAt }` | provider attempt details |
| `sendReportFileViaChannel` | `{ sent: boolean, messageId?, userMessage }` | local file path, Channel ID |
| `exportReportPdf` | `PdfExportForUser` | PDF artifact id/path/hash |

### 2.3 模型归属补充

这些模型不是新增产品能力，但必须在实现任务中有明确归属和测试，否则容易被漏掉：

| 模型/枚举 | 归属任务 | 必测要求 |
|---|---|---|
| 通用枚举：`MarketProfile`, `ChatContextKind`, `IntentKind`, `ReportTaskStatus`, `UserVisibleSeverity` | P1-03, P1-07 | API schema 和 DTO mapper 使用同一枚举；HK/CRYPTO 未批准时不 fallback |
| `ReportWorkflowSettingsSnapshot` | P2-03, P5-07 | 确认时冻结设置；写入或映射到 `RunRequest`；恢复/重试不重新读 UI store |
| `ReportAssetForUser` | P3-03, P6-01 | 只暴露文件可用性、状态和用户提示；不暴露 artifact id/path/hash |
| `PdfExportRecord` | P6-01 | 仅后端内部保存；普通 UI 只接收 `PdfExportForUser` |
| `ReportCompletionSummary` | P3-04, P6-04 | 只来自已保存报告、PM 结论和元数据；不调用 LLM/简报 worker |
| `ChartEvidenceForUser` | P3-05 | 仅返回图表标题、状态、用户文案和时间；不返回内部证据路径 |

### 2.4 产品错误码映射

所有普通 UI 错误响应必须使用产品错误码。内部类别只进日志。

实现必须覆盖 `docs/UI详细设计.md` §8.1 的全量公共错误码，包括伪码中使用的
`REPORT_CONTEXT_TOO_LONG`。如果后续新增普通 UI 错误，必须先把它
登记为产品错误码，再写 API handler；不得把内部异常类别、OpenClaw/Channel 原始错误码
或 provider 错误码直接返回给普通用户。

基础产品错误码集合：

```text
INVALID_INPUT
CONFIRMATION_REQUIRED
DRAFT_EXPIRED
QUEUE_FULL
DUPLICATE_TASK
TASK_NOT_FOUND
TASK_NOT_CANCELLABLE
REPORT_NOT_FOUND
REPORT_NOT_READY
REPORT_CONTEXT_TOO_LONG
NOTIFICATION_UNAVAILABLE
FILE_SEND_UNSUPPORTED
ASSISTANT_UNAVAILABLE
DATASOURCE_TEST_FAILED
PDF_EXPORT_FAILED
PROFILE_STRATEGY_UNAPPROVED
SCHEDULE_NOT_FOUND
ALERT_NOT_FOUND
UNAUTHORIZED
CONFLICT
```

| 内部类别/来源 | 产品错误码 | 用户文案方向 |
|---|---|---|
| OpenClaw 普通聊天不可用、OpenClaw config/model API 不可用 | `ASSISTANT_UNAVAILABLE` | 助手服务暂不可用，请稍后重试 |
| OpenClaw Channel 状态/发送失败 | `NOTIFICATION_UNAVAILABLE` | 微信通知暂不可用，已在设备界面显示 |
| Channel 文件发送能力缺失 | `FILE_SEND_UNSUPPORTED` | 完整报告文件暂不可发送，请在设备界面查看 |
| 报告追问上下文过长 | `REPORT_CONTEXT_TOO_LONG` | 当前报告过长，暂时无法追问 |
| workflow 失败 | `ASSISTANT_UNAVAILABLE` 或已登记产品业务码 | 这次报告没有生成成功，请检查后重新生成 |
| 队列满 | `QUEUE_FULL` | 报告队列已满，请稍后再试 |
| 同标的同配置重复 | `DUPLICATE_TASK` | 已有同标的报告在队列中，已复用 |
| 数据源密钥/网络/限流/格式错误 | `DATASOURCE_TEST_FAILED` 或 `INVALID_INPUT` | 到设置页更新密钥、接口或代理 |
| PDF 导出失败 | `PDF_EXPORT_FAILED` | PDF 暂不可用，完整报告仍可在设备界面查看 |
| HK/CRYPTO prompt/profile 策略未批准 | `PROFILE_STRATEGY_UNAPPROVED` | 当前市场策略尚未批准 |

验证：序列化每个普通 API 成功和失败响应后，检查禁止字段；不得做自然语言禁词扫描。

## 3. Phase 1：布局、状态模型、普通聊天透传

### P1-00 实施前源文档校验

- 目标：确认实现者读取 `AGENTS.md`、`docs/UI需求分析.md`、`docs/UI详细设计.md`、`docs/report_workflow_env_and_rounds_design.md` 和本清单。
- 输入/输出：输入为文档；输出为 implementation note 中的源文档版本/commit 记录。
- 涉及模型/API/函数：无。
- 文件范围：任务证据文档或 PR 描述。
- 验收标准：PR/task evidence 明确列出源文档；未读不得开工。
- 验证命令：`rg -n "UI实施任务清单|UI详细设计|RunRequest" docs memory`
- 禁止事项：不得凭记忆实现。

### P1-01 首版范围闸门

- 目标：把首版不做项写成负向测试，防止后续“顺手加上”。
- 输入/输出：输入为 §1 首版不解决；输出为 `test_no_first_version_regression.py`。
- 涉及模型/API/函数：无。
- 文件范围：`tests/unit/ui/test_no_first_version_regression.py`。
- 验收标准：测试覆盖移动端布局、并行完整报告、小时级完整报告、简报 worker、失败任务管理页、任意未知 HTTP/JSON 数据源、claw-trade 微信协议实现。
- 验证命令：`uv run pytest tests/unit/ui/test_no_first_version_regression.py`
- 禁止事项：不得用 feature flag 让首版不做项悄悄可用。

### P1-02 架构边界适配层

- 目标：定义 claw-trade 产品 API 与 OpenClaw gateway 的边界，不让前端直连内部运行时。
- 输入/输出：输入为用户消息和 UI 请求；输出为产品 DTO。
- 涉及模型/API/函数：`OpenClawGatewayClient.chatSend`, `sendChatMessage`, `loadLlmSettings`。
- 文件范围：`src/claw_trade/ui_backend/openclaw_client.py` 或等价适配层。
- 验收标准：普通聊天只调用 OpenClaw 普通聊天；不创建 report task；错误映射为 `ASSISTANT_UNAVAILABLE`。
- 验证命令：`uv run pytest tests/unit/ui/test_chat_controller.py tests/contracts/test_ui_boundary.py`
- 禁止事项：不得把 OpenClaw 原始错误、端口、gateway 名称返回给 UI。

### P1-03 模块目录与传输无关 API contract

- 目标：参考/copy `claw-invest` 兼容的 web server / route / contract 组织方式，同时稳定传输无关 service contract。
- 输入/输出：输入为 §8 API 表；输出为传输无关接口和 DTO schema。
- 涉及模型/API/函数：全部 `*ForUser` DTO。
- 文件范围：`src/claw_trade/ui_contracts/**`, `src/claw_trade/ui_backend/**`；可参考/copy `claw-invest/src/claw_invest/web/**` 的通用非业务结构。
- 验收标准：service 层不依赖 HTTP/IPC/WebSocket 细节；不得复制旧 report workflow、direct LLM、alphaear reporter 或旧启动脚本。
- 验证命令：`uv run pytest tests/contracts/test_ui_api_contracts.py`
- 禁止事项：不得把 `claw-invest` 旧业务边界带回 claw-trade。

### P1-04 三栏 AppShell 与上下文可见性

- 目标：实现左侧历史、中间上下文、右侧状态的固定三栏工作台。
- 输入/输出：输入为 `ChatContextForUser`, `ReportQueueSnapshotForUser`, `SavedReportForUser[]`；输出为 UI 视图。
- 涉及模型/API/函数：`switchChatContext`, `listSavedReports`, `getReportQueueSnapshot`。
- 文件范围：前端 `AppShell/Layout/ReportHistoryRail/ChatMainPanel/RightRail`；参考/copy `claw-invest/web/research-ui` 兼容的 React/Vite/TypeScript shell。
- 验收标准：上下文状态可见；左侧只显示保存成功报告；右侧不显示内部服务状态。
- 验证命令：`pnpm test && pnpm build` 或等价前端测试命令。
- 禁止事项：不得做营销首页、移动端首版、科技大屏。

### P1-05 设计 token 与关键组件样式

- 目标：落地 §5 的颜色、字体、间距、圆角和报告正文样式。
- 输入/输出：输入为 design token；输出为 CSS/theme。
- 涉及模型/API/函数：无。
- 文件范围：前端 theme/style 文件；参考/copy `claw-invest/web/research-ui` 兼容样式，PDF 样式参考 TradingAgents-CN 中文横排导出。
- 验收标准：报告正文使用中文衬线；UI 控件不按视口缩放字体；任务进度条稳定。
- 验证命令：`pnpm test && pnpm build`；截图 QA 在前端目录落地后补。
- 禁止事项：不得加入深色驾驶舱、渐变装饰光斑、嵌套卡片。

### P1-06 ChatContext 状态机

- 目标：实现 `normal_chat`、`intent_confirming`、`task_following`、`report_reading` 迁移。
- 输入/输出：输入为用户动作/消息；输出为 `ChatContextForUser`。
- 涉及模型/API/函数：`switchChatContext`, `handleUserMessage`。
- 文件范围：`src/claw_trade/ui_backend/chat_context.py`, `tests/unit/ui/test_chat_context_state_machine.py`。
- 验收标准：报告追问不触发 workflow；运行中任务上下文锁不返回给 UI。
- 验证命令：`uv run pytest tests/unit/ui/test_chat_context_state_machine.py`
- 禁止事项：不得让 LLM 猜 UI 当前上下文。

### P1-07 基础 DTO 映射与字段边界

- 目标：实现通用 DTO mapper 和普通响应禁止字段测试。
- 输入/输出：输入为内部模型；输出为 `*ForUser`。
- 涉及模型/API/函数：`toChatMessageForUser`, `toChatContextForUser`, `toIntentDraftForUser`, `toReportTaskForUser`, `toReportQueueSnapshotForUser`。
- 文件范围：`src/claw_trade/ui_contracts/user_dto.py`, `tests/contracts/test_ui_user_dto_redaction.py`。
- 验收标准：响应 JSON 不含明确禁止字段；报告正文和聊天正文不做词表扫描。
- 验证命令：`uv run pytest tests/contracts/test_ui_user_dto_redaction.py`
- 禁止事项：不得先返回内部对象后“前端不展示”。

### P1-08 普通聊天与意图确认 API

- 目标：实现 `sendChatMessage`, `createIntentDraft`, `confirmIntentDraft` 的首段行为。
- 输入/输出：输入为用户文本；输出为普通聊天回复或确认卡。
- 涉及模型/API/函数：`handleUserMessage`, `classifyUserIntent`, `buildConfirmationCard`。
- 文件范围：`src/claw_trade/ui_backend/chat_controller.py`, `intent_recognizer.py`, API route/handler。
- 验收标准：普通聊天透传 OpenClaw；报告/定时/提醒只生成确认卡，不自动执行。
- 验证命令：`uv run pytest tests/unit/ui/test_chat_controller.py tests/unit/ui/test_intent_recognizer.py`
- 禁止事项：不得用关键词命中后直接入队。

### P1-09 意图识别首版规则

- 目标：覆盖普通报告、每天/每周定时报告、价格阈值/涨跌幅提醒。
- 输入/输出：输入自然语言；输出 `IntentDraftForUser` + confirmation card。
- 涉及模型/API/函数：`classifyUserIntent`, `parseSchedule`, `parsePriceCondition`。
- 文件范围：`src/claw_trade/ui_backend/intent_recognizer.py`。
- 验收标准：小时级报告拒绝；缺标的低置信度或可读提示；不调用 worker。
- 验证命令：`uv run pytest tests/unit/ui/test_intent_recognizer.py`
- 禁止事项：不得把普通聊天误判成已确认 report workflow。

### P1-10 公共错误翻译器

- 目标：建立内部错误到产品错误码/用户文案的唯一入口。
- 输入/输出：输入为内部异常；输出为 `UserFacingFailure`。
- 涉及模型/API/函数：`translateInternalErrorForUser`, `publicCodeFor`, `stripInternalTerms`。
- 文件范围：`src/claw_trade/ui_backend/error_translator.py`。
- 验收标准：OpenClaw/Channel/数据源/PDF 错误映射到产品错误码；不得把内部异常原文直接返回。
- 验证命令：`uv run pytest tests/unit/ui/test_error_message_translator.py`
- 禁止事项：不得把内部错误原文直接返回。

## 4. Phase 2：报告队列和 workflow 接入

### P2-01 ReportTask 状态机与串行队列

- 目标：实现完整报告串行队列，默认上限 10。
- 输入/输出：输入 `ReportTaskInput`；输出 `ReportTaskForUser`, `ReportQueueSnapshotForUser`。
- 涉及模型/API/函数：`enqueueReportTask`, `startNextReportTaskIfIdle`, `getReportQueueSnapshotForUser`。
- 文件范围：`src/claw_trade/ui_backend/report_queue.py`。
- 验收标准：同时最多一个 `running`；第 11 个 queued 失败；手动优先但不打断 running。
- 验证命令：`uv run pytest tests/unit/ui/test_report_queue.py`
- 禁止事项：不得并行完整报告，不得伪造 workflow 成功。

### P2-02 确认后创建报告任务

- 目标：`confirmIntentDraft` 对 report draft 创建完整报告任务。
- 输入/输出：输入已确认 draft；输出 `ReportTaskForUser`。
- 涉及模型/API/函数：`confirmIntentDraft`, `buildReportTaskInput`, `assertApprovedProfileStrategy`。
- 文件范围：`src/claw_trade/ui_backend/confirmation_controller.py`。
- 验收标准：HK/CRYPTO 未批准时 `PROFILE_STRATEGY_UNAPPROVED`，不 fallback；重复确认幂等。
- 验证命令：`uv run pytest tests/unit/ui/test_confirmation_controller.py`
- 禁止事项：不得跳过确认，不得自动 fallback 到 US/CN_A。

### P2-03 UI 设置模型生成 RunRequest

- 目标：对齐 `report_workflow_env_and_rounds_design`：UI/product 设置生成 `RunRequest`，controller 只读 `RunRequest`。
- 输入/输出：输入用户本次表单 + `ReportWorkflowSettingsSnapshot`；输出 `RunRequest`。
- 涉及模型/API/函数：`ReportWorkflowBridge.buildRunRequest`, `ReportWorkflowSettings`。
- 文件范围：`src/claw_trade/ui_backend/workflow_bridge.py`, `src/claw_trade/config/report_workflow_settings.py` 只在已有设计允许范围内接入。
- 验收标准：`max_debate_rounds`、`max_risk_discuss_rounds`、`frontline_execution_mode`、`default_profile`、`default_market`、`default_currency`、`default_currency_symbol` 全部从确认时冻结的设置快照进入或映射到 `RunRequest`；`max_rounds_hard_limit` 校验在 settings/env 写入层执行；首版后端可受控读写 `.env.local`，但 controller 不读 env。
- 验证命令：`uv run pytest tests/unit/ui/test_workflow_bridge.py tests/unit/test_report_workflow_settings.py`
- 禁止事项：不得让 workflow controller 直接读 UI 设置 store 或 `.env.local`；不得让前端读写 `.env.local`。

### P2-04 Workflow 启动入口与普通聊天隔离

- 目标：只有确认后的报告任务可以创建 workflow，入口标记为 `report_command`。
- 输入/输出：输入 queued task；输出 workflow run 记录。
- 涉及模型/API/函数：`startNextReportTaskIfIdle`, `ReportWorkflowBridge.createWorkflowRun`。
- 文件范围：`src/claw_trade/ui_backend/workflow_bridge.py`, `report_queue.py`。
- 验收标准：普通聊天路径没有 workflow run；报告任务 run request 含 `entry_point=report_command`。
- 验证命令：`uv run pytest tests/unit/ui/test_workflow_bridge.py tests/contracts/test_ui_chat_report_boundary.py`
- 禁止事项：不得从普通 chat message 直接调 workflow runner。

### P2-05 进度映射与中文角色名

- 目标：把 workflow 阶段和 worker id 映射成用户可读阶段/角色。
- 输入/输出：输入 workflow state；输出 `ReportProgressUiState`。
- 涉及模型/API/函数：`mapWorkflowProgressToUiState`。
- 文件范围：`src/claw_trade/ui_backend/progress_mapper.py`。
- 验收标准：UI 显示中文阶段、中文角色、当前操作、已完成/等待角色；响应不含 worker id。
- 验证命令：`uv run pytest tests/unit/ui/test_progress_mapper.py`
- 禁止事项：不得显示 `market_analyst`、provider attempt、runtime marker。

### P2-06 运行中修改拦截

- 目标：运行中用户插话不能修改 workflow 输入或 worker prompt。
- 输入/输出：输入 task_following 上下文消息；输出可读提示 + 完成后重做确认草稿。
- 涉及模型/API/函数：`handleUserMessage`, `looksLikeTaskMutation`, `createRegenerateDraftAfterCompletion`。
- 文件范围：`chat_controller.py`, `intent_recognizer.py`。
- 验收标准：原任务 `RunRequest` 不变；新需求只成为新草稿。
- 验证命令：`uv run pytest tests/unit/ui/test_running_task_mutation_block.py`
- 禁止事项：不得把用户插话拼进运行中 worker prompt。

### P2-07 失败任务处理

- 目标：workflow/保存失败只生成中间聊天可读提醒，不进入历史，不长期留右侧。
- 输入/输出：输入内部失败；输出 `ReportTaskForUser` failure + chat message。
- 涉及模型/API/函数：`handleReportFailed`, `translateInternalErrorForUser`。
- 文件范围：`report_queue.py`, `right_rail_store.py` 或等价实现。
- 验收标准：左侧历史无失败项；右侧 terminal failed 被移除；失败响应只含用户 DTO 字段。
- 验证命令：`uv run pytest tests/unit/ui/test_report_failure_visibility.py`
- 禁止事项：不得为方便调试把失败 run 路径显示给用户。

### P2-08 排队任务取消

- 目标：排队任务可立即取消；运行中 workflow 首版不取消。
- 输入/输出：输入 `cancelReportTask`; 输出 `ReportTaskForUser`。
- 涉及模型/API/函数：`cancelReportTask`。
- 文件范围：`report_queue.py`。
- 验收标准：queued 任务转 `cancelled`；running 任务没有取消入口，或 API 返回 `TASK_NOT_CANCELLABLE` 且状态不变。
- 验证命令：`uv run pytest tests/unit/ui/test_report_cancel.py`
- 禁止事项：不得发 running abort，不得显示“已请求取消”，不得伪造 running -> cancelled 成功。

## 5. Phase 3：报告仓库、阅读、完成摘要

### P3-01 报告仓库与左侧历史

- 目标：保存成功报告进入正式历史；失败/运行/取消不进入。
- 输入/输出：输入 workflow 成功 Markdown；输出 `SavedReportForUser`。
- 涉及模型/API/函数：`handleReportSucceeded`, `listSavedReports`, `redactReportForUser`。
- 文件范围：`src/claw_trade/ui_backend/report_repository.py`。
- 验收标准：Markdown 已保存且可打开；左侧只列成功报告。
- 验证命令：`uv run pytest tests/unit/ui/test_report_repository.py`
- 禁止事项：不得把 PDF 当报告真相源。

### P3-02 报告阅读器与报告追问

- 目标：打开完整报告，底部保留追问输入框。
- 输入/输出：输入 `reportId` 与追问文本；输出 `ReportDetailForUser` 与 OpenClaw 普通聊天回复。
- 涉及模型/API/函数：`getReportDetail`, `askReportQuestion`。
- 文件范围：`report_repository.py`, `report_qa.py`, 前端 `ReportReaderPanel`。
- 验收标准：追问只基于已保存 Markdown；不改报告正文；不触发 workflow。
- 验证命令：`uv run pytest tests/unit/ui/test_report_question.py`
- 禁止事项：不得摘要压缩报告正文来绕过上下文预算；不够则返回 `REPORT_CONTEXT_TOO_LONG`。

### P3-03 报告详情 DTO 与资产状态

- 目标：报告详情只返回普通用户可见状态。
- 输入/输出：输入内部 report/artifact/pdf/chart/data-source 记录；输出 `ReportDetailForUser`。
- 涉及模型/API/函数：`getReportDetail`, `toPdfExportForUser`, `getReportChartEvidence`。
- 文件范围：`report_repository.py`, `chart_evidence.py`, DTO mapper。
- 验收标准：不含 artifact、path、hash、receipt、本地路径。
- 验证命令：`uv run pytest tests/contracts/test_report_detail_user_dto.py`
- 禁止事项：不得通过前端隐藏内部字段。

### P3-04 完成摘要提取规则

- 目标：摘要只从已保存报告、PM 结论和元数据确定性摘取。
- 输入/输出：输入 Markdown + PM final conclusion ref；输出 `ReportCompletionSummary`。
- 涉及模型/API/函数：`buildCompletionSummaryFromSavedReport`。
- 文件范围：`summary_builder.py`。
- 验收标准：缺字段时显示“完整理由请查看报告正文/主要风险请查看报告正文”；不补写新事实。
- 验证命令：`uv run pytest tests/unit/ui/test_completion_summary.py`
- 禁止事项：不得新增简报 worker，不得在报告未生成前编摘要。

### P3-05 图表证据状态

- 目标：右侧报告详情显示图表状态，来源于真实证据聚合。
- 输入/输出：输入 `ChartAsset` / `chart_assets`、相关 `DataGap`、`reports/export-result.json`、已保存 Markdown 图片引用；输出 `ChartEvidenceForUser[]`。
- 涉及模型/API/函数：`getReportChartEvidence`。
- 文件范围：`chart_evidence.py`。
- 验收标准：ready/missing/failed 有用户可读原因；无静态占位图；不扫本地路径给 UI；不返回内部路径。
- 验证命令：`uv run pytest tests/unit/ui/test_report_chart_evidence.py`
- 禁止事项：不得写死“图表正常”。

### P3-06 报告成功通知入口

- 目标：中间聊天显示完成卡，微信只发送摘要，不主动推全文。
- 输入/输出：输入 `reportId`; 输出完成卡和通知结果。
- 涉及模型/API/函数：`notifyReportCompletion`, `renderCompletionSummaryText`。
- 文件范围：`report_notification_service.py`。
- 验收标准：完整报告需用户点击查看；Channel 不可用时落 UI 聊天。
- 验证命令：`uv run pytest tests/unit/ui/test_report_notification_service.py`
- 禁止事项：不得主动推送完整报告全文。

## 6. Phase 4：定时任务和价格提醒

### P4-01 `ScheduledReportForUser` 与状态机

- 目标：补齐定时报告用户 DTO 和状态迁移。
- 输入/输出：输入 `ScheduledReport`; 输出 `ScheduledReportForUser`。
- 涉及模型/API/函数：`toScheduledReportForUser`, `createScheduledReport`, `pauseScheduledReport`, `resumeScheduledReport`, `deleteScheduledReport`。
- 文件范围：`scheduler_service.py`, `ui_contracts/user_dto.py`。
- 验收标准：普通 API 不返回 `lastRunTaskId`、内部 id；状态按 §6.3。
- 验证命令：`uv run pytest tests/unit/ui/test_scheduler_service.py tests/contracts/test_ui_user_dto_redaction.py`
- 禁止事项：不得返回内部 `ScheduledReport`。

### P4-02 创建/暂停/恢复/删除定时报告

- 目标：实现每天/每周完整报告计划。
- 输入/输出：输入确认后的 scheduled draft；输出 `ScheduledReportForUser`。
- 涉及模型/API/函数：`createScheduledReport`, `pauseScheduledReport`, `resumeScheduledReport`, `deleteScheduledReport`。
- 文件范围：`scheduler_service.py`。
- 验收标准：小时级完整报告拒绝；暂停不触发；删除软删除。
- 验证命令：`uv run pytest tests/unit/ui/test_scheduler_service.py`
- 禁止事项：不得支持每小时完整报告，不得生成简报。

### P4-03 定时报告到点入队

- 目标：到点后进入完整报告串行队列。
- 输入/输出：输入 due schedule；输出 `ReportTaskForUser`, `ReportQueueSnapshotForUser`。
- 涉及模型/API/函数：`tickScheduledReports`, `runScheduledReportNow`, `enqueueReportTask`。
- 文件范围：`scheduler_service.py`, `report_queue.py`。
- 验收标准：不并行；队列满可读提示；恢复后不追补历史窗口。
- 验证命令：`uv run pytest tests/unit/ui/test_scheduler_queue_integration.py`
- 禁止事项：不得绕过完整报告队列。

### P4-04 `PriceAlertForUser` 与状态机

- 目标：补齐价格提醒用户 DTO 和状态迁移。
- 输入/输出：输入 `PriceAlert`; 输出 `PriceAlertForUser`。
- 涉及模型/API/函数：`toPriceAlertForUser`, `createPriceAlert`, `pausePriceAlert`, `resumePriceAlert`, `deletePriceAlert`, `evaluatePriceAlert`。
- 文件范围：`price_alert_service.py`, `ui_contracts/user_dto.py`。
- 验收标准：普通 API 不返回内部 alert 记录；状态按 §6.4。
- 验证命令：`uv run pytest tests/unit/ui/test_price_alert_service.py tests/contracts/test_ui_user_dto_redaction.py`
- 禁止事项：不得返回内部 `PriceAlert`。

### P4-05 价格提醒创建与触发

- 目标：支持价格阈值、百分比涨跌提醒；触发后默认关闭。
- 输入/输出：输入确认后的 price alert draft；输出 `PriceAlertForUser`。
- 涉及模型/API/函数：`createPriceAlert`, `evaluatePriceAlert`, `runPriceAlertNow`。
- 文件范围：`price_alert_service.py`。
- 验收标准：只发提醒，不生成报告，不调用 LLM 解释。
- 验证命令：`uv run pytest tests/unit/ui/test_price_alert_service.py`
- 禁止事项：不得支持技术指标触发、AI 解释、自动生成报告。

### P4-06 定时/提醒 API contract

- 目标：所有 scheduled/price alert API 返回用户 DTO。
- 输入/输出：API 请求；用户 DTO 响应。
- 涉及模型/API/函数：`createScheduledReport`, `pauseScheduledReport`, `resumeScheduledReport`, `deleteScheduledReport`, `runScheduledReportNow`, `createPriceAlert`, `pausePriceAlert`, `resumePriceAlert`, `deletePriceAlert`, `runPriceAlertNow`。
- 文件范围：API route/handler, DTO mapper。
- 验收标准：响应只含用户 DTO 字段；幂等 `requestId` 生效。
- 验证命令：`uv run pytest tests/contracts/test_scheduled_and_alert_api_contracts.py`
- 禁止事项：不得遗漏 scheduled/price alert 的 `*ForUser` 映射。

## 7. Phase 5：设置页、数据源、LLM、Channel

### P5-01 设置页分区

- 目标：实现微信通知、模型、数据源三个设置分区。
- 输入/输出：输入 settings DTO；输出设置页 UI。
- 涉及模型/API/函数：`getChannelStatus`, `loadLlmSettings`, `listDataSources`。
- 文件范围：前端 settings 页面。
- 验收标准：不显示 MongoDB、OpenViking、OpenClaw Gateway、本地端口、缓存目录、runtime path。
- 验证命令：`pnpm test && pnpm build` 或等价检查。
- 禁止事项：普通用户设置页不得变成诊断页。

### P5-02 blocker register 入库

- 目标：把人类决策、已查清能力和运行时探测点分开；真正未确认的能力不得伪装成可实现项。
- 输入/输出：输入 §15 和本清单 §10；输出 blocker register。
- 涉及模型/API/函数：无。
- 文件范围：任务证据或 `docs/UI实施任务清单.md` 维护。
- 验收标准：每个 blocker 有 owner、影响、保守行为、停止条件；已查清项不得继续要求人类拍板，运行时 probe 不得写成已成功。
- 验证命令：`rg -n "B-0[1-9]|B-10" docs/UI实施任务清单.md`
- 禁止事项：不得写死未知 OpenClaw API。

### P5-03 Channel 状态与连接边界

- 目标：UI 只显示微信 ClawBot 产品概念，实际 Channel 管理归 OpenClaw。
- 输入/输出：OpenClaw Channel 状态；`ChannelStatusForUser`。
- 涉及模型/API/函数：`getChannelStatus`, `saveChannelConfigViaOpenClaw`, `toChannelStatusForUser`。
- 文件范围：`channel_bridge.py`, settings UI。
- 验收标准：内部把 `wechat_clawbot` 映射到 `openclaw-weixin`；设置页接入流程使用 OpenClaw 插件安装/启用/Gateway 重启/扫码登录能力；当前仅确认 `@tencent-weixin/openclaw-weixin@2.4.3` 存在、可安装并可被 `plugins inspect` 识别，不把本机 CLI 静态能力输出当成接通证据；每次状态/发送前复核真实安装、启用、登录、媒体能力和发送结果；不返回 `providerChannelId`、插件包名、配置路径；连接失败映射为 `NOTIFICATION_UNAVAILABLE`。
- 验证命令：`uv run pytest tests/unit/ui/test_channel_bridge.py`
- 禁止事项：claw-trade 不保存 Channel 凭据，不实现微信协议，不接入 Wechaty/逆向/WeCom。

### P5-04 LLM 设置桥接

- 目标：模型配置入口由 UI 提供，保存/测试由 OpenClaw 执行。
- 输入/输出：`LlmConfigDraft`; saved/test result。
- 涉及模型/API/函数：`loadLlmSettings`, `saveLlmConfigViaOpenClaw`, `testLlmViaOpenClaw`。
- 文件范围：`llm_settings_bridge.py`, settings UI。
- 验收标准：真实密钥不回传、不由 claw-trade 持久化；通过隔离桥接使用 OpenClaw `config.schema.lookup`、`config.get`、`config.patch`、`models.list/status/authStatus` 或等价 gateway 方法；不得自造 `llm.save/test`。
- 验证命令：`uv run pytest tests/unit/ui/test_llm_settings_bridge.py`
- 禁止事项：不得把 OpenClaw config 原始 schema/path/hash 暴露给普通用户。

### P5-05 数据源设置与 secret 边界

- 目标：只支持已内置数据源类型；测试通过才启用；密钥 replace-only；首版由后端 allowlist 写入 `.env.local`。
- 输入/输出：`DataSourceInstanceInput`; `DataSourceInstanceForUser`。
- 涉及模型/API/函数：`listDataSources`, `testDataSourceInstance`, `saveDataSourceInstance`, `toDataSourceInstanceForUser`。
- 文件范围：`data_source_settings.py`, settings UI。
- 验收标准：未知 `custom_http` 拒绝；未测试不能启用；响应无真实 key/credentialRef；env writer 只写 allowlist key、原子写入或备份、不向 UI 返回 `.env.local` 路径。
- 验证命令：`uv run pytest tests/unit/ui/test_data_source_settings.py`
- 禁止事项：不得做任意未知 HTTP/JSON 数据源接入或字段映射平台；不得让浏览器直接写 `.env.local`。

### P5-06 已配置但本次失效数据源提醒

- 目标：右侧只提醒 configured + enabled + usedInRun + failed 的来源。
- 输入/输出：运行证据 + 数据源实例；`DataSourceHealthEventForUser[]`。
- 涉及模型/API/函数：`collectConfiguredFailedDataSources`, `toDataSourceHealthEventForUser`。
- 文件范围：`data_source_health.py`, report detail/right rail。
- 验收标准：未配置、未启用、未使用、不需要 key 的来源不提醒。
- 验证命令：`uv run pytest tests/unit/ui/test_configured_failed_data_sources.py`
- 禁止事项：不得为凑提醒显示所有缺失数据源。

### P5-07 `.env.local` 首版配置存储边界

- 目标：按人类决策实现首版后端受控读写 `.env.local`，创建报告时仍冻结设置并写入 `RunRequest`。
- 输入/输出：UI settings; `ReportWorkflowSettings` snapshot。
- 涉及模型/API/函数：`SettingsService.currentReportWorkflowSettings`, `ReportWorkflowBridge.buildRunRequest`。
- 文件范围：`settings_service.py`, `workflow_bridge.py`。
- 验收标准：dev/CLI 可读 `.env.local`；后端配置写入层可按 allowlist 原子写入或备份 `.env.local`；前端不读写 `.env.local`；普通 API 不返回路径/真实值；controller 不直接读 env；UI 设置保存后生成包含 rounds、frontline mode、default profile/market/currency/currency symbol 的快照；rounds 超过 hard limit 时确认失败且不创建任务。
- 验证命令：`uv run pytest tests/unit/ui/test_report_workflow_settings_ui_boundary.py`
- 禁止事项：不得在普通 UI 暴露 `.env.local` 路径或真实值；不得让 controller 用运行时 env 覆盖已冻结 `RunRequest`。

## 8. Phase 6：PDF、文件发送、端到端验收

### P6-01 PDF 导出服务

- 目标：把已保存 Markdown 格式化为 PDF。
- 输入/输出：已保存 Markdown；`PdfExportForUser`。
- 涉及模型/API/函数：`exportSavedMarkdownToPdf`, `exportReportPdf`, `toPdfExportForUser`。
- 文件范围：`pdf_export_service.py`。
- 验收标准：Markdown hash 不变；PDF 失败不影响报告保存；响应无 artifact/path/hash。
- 验证命令：`uv run pytest tests/unit/ui/test_pdf_export_service.py`
- 禁止事项：不得重写、总结、补写 Markdown。

### P6-02 完整报告文件发送

- 目标：用户请求完整报告时发送已保存 PDF；能力缺失时可读失败。
- 输入/输出：`reportId`, `channelKind`; `{ sent, userMessage }`。
- 涉及模型/API/函数：`requestFullReportFile`, `sendReportFileViaChannel`。
- 文件范围：`report_notification_service.py`, `channel_bridge.py`。
- 验收标准：只发送 ready PDF；先探测 `openclaw-weixin` 已安装、已启用、已登录、文件/媒体能力、PDF 大小限制；失败不暴露本地路径；能力缺失时 `FILE_SEND_UNSUPPORTED`，通知不可用时 `NOTIFICATION_UNAVAILABLE`，不模拟 message id。
- 验证命令：`uv run pytest tests/unit/ui/test_report_file_send.py`
- 禁止事项：不得临时生成未保存内容，不得发送 Markdown 作为微信默认格式。

### P6-03 微信消息通知/回调

- 目标：仅在 OpenClaw 能把入站微信消息通知/回调交给 claw-trade 后接入 `request_full_report` / `open_report_summary`。
- 输入/输出：`OpenClawChannelInboundEvent`; action result。
- 涉及模型/API/函数：`handleOpenClawChannelInboundEvent`, `requestFullReportFile`。
- 文件范围：`channel_inbound_bridge.py`。
- 验收标准：支持 `/report <标的>`、`报告 <标的>`、`确认`、`取消`、`进度`、`发送完整报告` 的产品动作映射；`/report` 和“报告”只生成确认卡；`确认` 才创建 workflow；`取消` 只取消 queued，running 返回“报告正在生成，不能中途取消”；桥接不存在时不注册替代微信 webhook；不实现微信协议；“事件”只作为代码幂等 ID/回调记录，不作为用户文案。
- 验证命令：`uv run pytest tests/unit/ui/test_channel_inbound_bridge.py`
- 禁止事项：不得模拟入站回调证明微信能力。

### P6-04 完成通知与 PDF 状态联动

- 目标：报告保存成功后完成摘要、PDF 状态和通知行为一致。
- 输入/输出：`reportId`; completion card + notification status。
- 涉及模型/API/函数：`handleReportSucceeded`, `buildCompletionSummaryFromSavedReport`, `notifyReportCompletion`, `exportSavedMarkdownToPdf`。
- 文件范围：`report_queue.py`, `summary_builder.py`, `pdf_export_service.py`, `report_notification_service.py`。
- 验收标准：PDF 失败时任务仍 `succeeded`；完成卡提示 PDF 暂不可用；左侧历史仍显示。
- 验证命令：`uv run pytest tests/unit/ui/test_report_success_pdf_notification.py`
- 禁止事项：不得因 PDF 失败删除报告。

### P6-05 普通 UI API 端到端 contract

- 目标：完整跑通普通聊天、手动报告、报告追问、定时报告、价格提醒、数据源失效、PDF、文件发送失败。
- 输入/输出：用户流程；用户 DTO 响应。
- 涉及模型/API/函数：全部普通 UI API。
- 文件范围：`tests/e2e/ui/test_report_user_flows.py` 或等价非 live 测试。
- 验收标准：所有响应只含用户 DTO 字段；无 mock/stub/fake/fallback 作为成功标准。
- 验证命令：`uv run pytest tests/e2e/ui/test_report_user_flows.py`
- 禁止事项：不得运行 live/provider 作为开发循环；live 验证另行按运行时 preflight。

### P6-06 Adversarial review gate

- 目标：实现完后逐项审查 mock/stub/fake/fallback、空实现、内部概念泄露、未知 API、首版不做项、未授权决策。
- 输入/输出：实现 diff；review report。
- 涉及模型/API/函数：无。
- 文件范围：PR/task evidence。
- 验收标准：§11 adversarial review 全部有结论；发现问题必须修复或标 BLOCKED。
- 验证命令：`rg -n "TODO|stub|mock|fake|fallback|pass$|NotImplemented" src tests web`
- 禁止事项：不得用“后续补”关闭红线问题。

## 9. 测试清单

| 编号 | 测试组 | 必测内容 | 建议命令 |
|---|---|---|---|
| T-01 | 单元：聊天/意图 | 普通聊天透传、确认卡、小时级拒绝、运行中修改拦截 | `uv run pytest tests/unit/ui/test_chat_controller.py tests/unit/ui/test_intent_recognizer.py` |
| T-02 | 单元：队列/workflow bridge | 串行、上限 10、手动优先、去重、RunRequest 设置快照、running 不可取消 | `uv run pytest tests/unit/ui/test_report_queue.py tests/unit/ui/test_workflow_bridge.py` |
| T-03 | 单元：报告仓库/摘要/图表 | 成功报告历史、摘要不补写、图表证据状态 | `uv run pytest tests/unit/ui/test_report_repository.py tests/unit/ui/test_completion_summary.py tests/unit/ui/test_report_chart_evidence.py` |
| T-04 | 单元：定时/价格 | ScheduledReportForUser、PriceAlertForUser、状态机、立即执行 | `uv run pytest tests/unit/ui/test_scheduler_service.py tests/unit/ui/test_price_alert_service.py` |
| T-05 | 单元：设置 | 密钥掩码、replace-only、数据源测试、LLM/Channel bridge、`openclaw-weixin` 安装/启用/扫码状态映射 | `uv run pytest tests/unit/ui/test_data_source_settings.py tests/unit/ui/test_llm_settings_bridge.py tests/unit/ui/test_channel_bridge.py` |
| T-06 | 单元：PDF/通知 | PDF 不改 Markdown、通知摘要、文件能力缺失、微信文件发送能力 probe | `uv run pytest tests/unit/ui/test_pdf_export_service.py tests/unit/ui/test_report_notification_service.py` |
| T-07 | contract：DTO 红线 | 所有普通 API 返回 `*ForUser`，禁止字段检查 | `uv run pytest tests/contracts/test_ui_user_dto_redaction.py tests/contracts/test_ui_api_contracts.py` |
| T-08 | 组件/前端 | 三栏、确认卡、右侧任务、报告阅读器、设置页 | `pnpm test && pnpm build` 或选型后的等价命令 |
| T-09 | E2E 非 live | 普通聊天、报告确认、失败不进历史、追问不触发 workflow、定时/提醒/PDF 文件失败、微信 `/report` 确认流和 running 取消拒绝 | `uv run pytest tests/e2e/ui/test_report_user_flows.py` |

测试红线：

- live/provider 不作为本清单验证要求；如后续需要 live，必须另按 `AGENTS.md` live preflight。
- 不允许以 mock/stub/fake/fallback 证明 OpenClaw Channel、PDF 文件发送、微信入站回调、LLM 保存等真实能力。
- 单元测试可以用 fake client 验证“不可用路径”和错误映射，但不能把 fake client 的成功响应当成真实能力已确认。
- 对运行时能力只能测试“不可用时产品降级文案和无泄露”；成功发送必须来自真实 status/capabilities 或另行批准的 live 证据。

## 10. Blocker / 待确认能力清单

本表现在同时记录三类东西：人类已拍板的决策、已经查清的技术事实、仍要在当前运行时探测的能力。只有 B-02/B-03 是实现前必须做的运行时检查点；它们不是让人类拍板，也不能被写成默认成功。

- 停问红线：微信接入默认路径必须先走腾讯官方流程（安装、启用、扫码、Gateway restart）；未按官方流程在同一 profile/config/Gateway 下形成最小失败复现前，不得修改 OpenClaw 源码。只有官方流程仍失败、且已提交最小复现证据并由人类拍板后，才允许进入 OpenClaw 源码修复。

| 编号 | blocker/待确认能力 | 影响 | 保守实现 | 停止条件 |
|---|---|---|---|---|
| B-01 | 微信 ClawBot 映射 | 已查清：普通微信使用外部插件 `@tencent-weixin/openclaw-weixin`，Channel ID 是 `openclaw-weixin`；npm 包 `2.4.3` 可安装，插件 inspect 识别 Channel；官方接入流程是安装/启用/扫码/Gateway restart | UI 仍只显示 `wechat_clawbot`；后端内部映射到 `openclaw-weixin`；不能把静态能力探测当成已扫码、已在线或已发送成功 | 不得硬编码 `wechat`/`clawbot`/WeCom；不得把 `openclaw-weixin` 返回给普通 UI；不得接入 Wechaty/逆向/WeCom |
| B-02 | PDF 文件发送运行时能力 | 微信“发送完整报告”取决于当前 OpenClaw 插件是否安装、启用、登录、支持媒体/文件和大小限制；本机/项目运行态尚无扫码登录和真实文件发送证据 | `sendReportFileViaChannel` 先看真实 status、登录态、媒体/文件能力和大小限制；不可用返回 `FILE_SEND_UNSUPPORTED` 或 `NOTIFICATION_UNAVAILABLE` | 不得模拟成功 message id；不得跳过能力探测 |
| B-03 | 微信消息通知/回调接入 | 插件源码有入站长轮询 monitor，但微信内回复“报告”能否直达 claw-trade 取决于 OpenClaw 是否有可接入的入站消息回调/路由扩展点；当前 dev/fixed 测试 runtime 尚不会加载外部微信插件 | 工程接 OpenClaw 的入站消息回调；生产级 UI runtime 需要稳定保留并加载 `openclaw-weixin` 配置；若用 `scripts/start-control-runtime.sh` 做测试证据，先让该测试 runtime 加载插件；支持 `/report`、`报告`、`确认`、`取消`、`进度`、`发送完整报告` 的产品动作映射；没有可接入口时不注册微信 webhook，只保留设备界面按钮/提示 | 不得在 claw-trade 内实现微信协议；不得用假回调证明能力；不得让 `/report` 直接绕过确认 |
| B-04 | LLM 配置桥接 | 已查清：首版用 OpenClaw config/models，不需要等待 `llm.save/test` 专用 API | `LlmSettingsBridge` 隔离 `config.schema.lookup`、`config.get`、`config.patch`、`models.list/status/authStatus` | 不得把 OpenClaw raw config 暴露给 UI；不得让 claw-trade 保存真实密钥 |
| B-05 | UI 后端形态 | 已决策：参考/copy `claw-invest` 兼容的 web server / route / contract 形态 | claw-trade 提供本地产品 API；service contract 保持传输无关；前端只调 claw-trade | 不得复制旧 report workflow、direct LLM、alphaear reporter 或让前端直连 OpenClaw |
| B-06 | 前端框架 | 已决策：参考/copy `claw-invest/web/research-ui` 兼容的 React/Vite/TypeScript | 复用三栏 shell、通用组件、浅色投研终端样式和构建配置 | 不得把旧业务逻辑、旧 API 字段或内部诊断概念带入普通 UI |
| B-07 | 数据源持久化/secret 存储 | 已决策：首版本机单用户形态由后端受控读写 `.env.local` | env writer 必须 allowlist、原子写入或备份、掩码回显、replace-only；后续可替换成配置服务/secret store | 前端写文件、普通 API 暴露路径/真实值、controller 直接读 env、明文密钥进入响应均停止 |
| B-08 | 完成摘要提取规则 | 已决策：确定性从已保存报告、PM 结论和元数据摘取 | 缺字段给查看全文提示；后续可增加结构化 metadata，但内容来源不变 | 不得新增简报 worker、调用 LLM 补写、或编造报告外事实 |
| B-09 | 图表状态来源 | 已查清：后端已有 `ChartAsset` / `chart_assets`、`DataGap`、`reports/export-result.json` 和 Markdown 图片引用 | 只从这些已保存证据聚合 `ChartEvidenceForUser`；缺失显示 missing/原因 | 不得用静态占位或“图表正常”；不得扫本地路径给 UI |
| B-10 | 运行中取消规则 | 已决策：首版不取消 running workflow | 排队可取消；running 不显示取消入口或返回 `TASK_NOT_CANCELLABLE`，状态不变 | 不得发 running abort；不得显示“已请求取消”；不得伪造 running -> cancelled 成功 |

## 11. Adversarial Review 清单

实现者和评审者必须逐项回答“是/否/证据”。任何“是”都不能直接合并，必须修复、降级为 blocker，或取得明确人类批准。

### AR-01 mock/stub/fake/fallback

- 是否用 mock/stub/fake/fallback 证明 OpenClaw、Channel、PDF 文件发送、微信入站回调、LLM 保存、数据源测试成功？
- 是否有隐藏 fallback prompt/tool/provider/report/PDF？
- 是否将无法确认的能力包装成成功？

### AR-02 骨架空实现

- 是否存在只返回空数组、固定成功、`pass`、`NotImplemented` 被 API 当成功返回？
- 是否有 TODO 骨架但测试仍通过？
- 是否只有组件壳，没有真实 API/状态机/DTO 红线？

### AR-03 内部概念泄露

- 普通 UI/API 响应是否直接返回内部模型字段？
- 错误文案是否把内部异常原文、端口、本地路径或调试 payload 直接返回？
- scheduled/price alert API 是否直接返回内部对象？

### AR-04 未知 API 被当成已存在

- 是否把用户概念 `wechat_clawbot` 直接写成 `wechat`、`clawbot` 或 WeCom，而不是内部映射 `openclaw-weixin`？
- 是否跳过 OpenClaw status/capabilities 就宣称 PDF 文件发送成功？
- 是否假设微信入站回调已经接到 claw-trade，而没有真实 OpenClaw 接入口？
- 是否自造或假设 `llm.save/test` 专用 API，而不是使用 OpenClaw config/models？

### AR-05 首版不做项被引入

- 是否新增移动端适配目标？
- 是否允许并行完整报告？
- 是否支持小时级完整报告？
- 是否新增简报 worker？
- 是否添加失败任务管理页？
- 是否允许自定义未知 HTTP/JSON 数据源或字段映射平台？
- 是否在 claw-trade 内实现微信协议？

### AR-06 文档未授权产品/架构决策

- 是否改变 claw-trade/OpenClaw 边界？
- 是否让 claw-trade 运行普通聊天 runtime，而不是透传 OpenClaw 普通聊天/session？
- 是否让 claw-trade 运行单 agent turn，而不是通过 OpenClaw 唤醒单 agent？
- 是否让 UI 后端保存 LLM 或 Channel 真实密钥？
- 是否让前端直接读写 `.env.local`？
- 是否让 controller 直接读取 `.env.local` 或 UI settings store 而不是 `RunRequest`？
- 是否让普通 UI/API 返回 `.env.local` 路径、真实密钥、内部配置 key 或本地目录？
- 是否让 Python 改写 PM 结论、报告正文或 PDF 内容？
- 是否把报告追问改成 report workflow？

## 12. 负向任务和测试

| 编号 | 禁止重新引入项 | 负向测试 |
|---|---|---|
| NT-01 | 移动端首版布局 | 搜索/组件测试确认无 mobile-first 断点作为首版验收条件 |
| NT-02 | 并行完整报告 | 队列测试确认最多一个 running |
| NT-03 | 小时级完整报告 | “每小时给我 BTC 报告”返回只支持每天/每周 |
| NT-04 | 简报 worker | repo 搜索确认未新增 brief/summary worker；摘要测试只读报告/PM |
| NT-05 | 运行中修改 workflow | task_following 修改请求不改变原 `RunRequest` |
| NT-06 | 失败任务历史/右侧长期保留 | 失败任务不出现在 `listSavedReports` 和 right rail active list |
| NT-07 | 任意未知 HTTP/JSON 数据源 | `custom_http` / `customJsonMapping` 返回 `INVALID_INPUT` |
| NT-08 | claw-trade 微信协议实现 | repo 搜索确认无微信 socket/webhook 协议实现 |
| NT-09 | 普通 UI 内部诊断泄露 | 禁止字段和 DTO contract 覆盖所有普通 API 成功/失败响应 |
| NT-10 | PDF 重写/补写报告 | Markdown hash 导出前后不变；PDF 服务不调用摘要/LLM |
| NT-11 | 运行中取消 workflow | running 任务取消返回 `TASK_NOT_CANCELLABLE` 或无入口，原状态和 `RunRequest` 不变 |

## 13. 完成定义

一个 Phase 只有同时满足以下条件才算完成：

- 该 Phase 所有任务有真实实现，不是空骨架。
- 对应 `*ForUser` DTO 和禁止字段检查通过。
- 对应负向测试通过。
- blocker 被明确标为“已确认可实现”或“保守不可用路径已实现”。
- 未运行 live/provider，或如果后续另行批准 live，则有 `AGENTS.md` 要求的 preflight 和真实证据。
- memory 当日追加记录，说明改动、验证、未解决 blocker。
