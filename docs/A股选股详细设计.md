# A股选股详细设计

状态：详细设计草案，已有首版实现，本文按当前实现口径同步。
日期：2026-05-24  
唯一主设计来源：`docs/A股选股总体设计.md`。  
参考边界：`AGENTS.md`、`docs/A股扩展方案.md`、`docs/A股扩展详细设计.md`、`docs/数据层详细设计.md`、`docs/数据层实施任务清单.md`。旧 `docs/数据源openbb引入方案.md` 只作历史背景。

说明：

- 本文只把 `docs/A股选股总体设计.md` 已确认的 `/select` 语义落到可编码合同。
- 原 `/select` completed 后 selection worker 追问设计已撤回；通用 worker 聊天见 `docs/worker聊天详细设计.md`，不属于本文 `/select` 可编码合同。
- 本文所有新增文件、类、函数、枚举名均为建议名，需实现时按当前代码风格确认；除非明确标注“现有”。
- 2026-05-28 人类已确认：`myhhub/stock` 与 `Sequoia-X` 审计到的本地可复现策略全部纳入 `/select` 优先实现清单；v1 跨策略排序权重按本文 §5.0.2 落地。
- 未经总体设计确认的 UI 展示形态、CLI 权限模型、非 CN_A 市场策略不在本文中被写成决定。
- 第一版只面向 `CN_A` A股日频收盘后选股。其它市场只能复用模式，未批准时 fail closed。

## 0. 覆盖审计与不确定性检查

### 0.1 Coverage Matrix

| 总体设计要求 | 详细设计章节 | 覆盖方式 | 是否需要人类确认 |
|---|---|---|---|
| `/select` 是独立 workflow，不能偷用 `report_command` | 1、2、3、4、9 | 新增 `select_command` 入口、`SelectRequest`、selection workflow run；普通 chat 不静默进入 | 否 |
| `/select` 不实时拉全市场数据 | 1、4、6、9 | no-run/no_candidate/stale/warehouse 证据不足时只触发后台 selection data refresh/job，不直接调 provider、不直接读表、不同步跑全市场；integrity 失败仍 fail closed | 否 |
| 收盘后确定性批处理生成 top 20 candidate pack | 2、4、5、6 | 后台 data run 状态机、函数级 job、candidate pack contract；策略全集和 v1 跨策略排序权重已按 2026-05-28 口径确认 | 否；provider adapter 与字段覆盖实测仍见 §0.2 |
| 全市场取数和计算属于 `claw-trade` 确定性层 | 1、2、5、11 | `src/claw_trade/selection/**` 后台任务复用 data_gateway evidence | 否 |
| OpenClaw 只执行 single worker turn，不拥有 selection workflow | 1、2、8、9 | selection controller 串行调度 4 次 OpenClaw wake；OpenClaw 只收单 turn command | 否 |
| 不把 selection 业务写入 `third_party/openclaw` | 1、2、7、14 | 所有业务落在 `src/claw_trade/selection/**`、`agents/selection_*`、插件 tool wrapper | 否 |
| 第一版 worker 固定 4 个 | 3、4、8 | `selection_strategist`、`selection_skeptic`、`selection_manager`、`selection_portfolio_manager` 固定调度 | 否 |
| LLM 不决定下一位 worker | 4、8、9 | `SelectionWorkflowState` 固定状态转换；controller 决定 dispatch | 否 |
| strategist/skeptic 第一版可见 candidate pack tool | 7、8、13 | stage policy 和 provider payload 验收严格等于 `claw_get_selection_candidate_pack` | 否 |
| manager/PM 第一版无工具 | 7、8、13 | `allowed_tools=()`，provider payload 不得有 tool schema | 否 |
| `claw_get_selection_candidate_pack` 只读 approved pack | 6、7、9 | runtime context 锁定 run；只读 OpenViking approved material；不带业务参数 | 否 |
| selection tool 不调 data_gateway/provider、不查 raw、不重新打分 | 6、7、13 | tool backend 明确禁止 provider/raw/Mongo raw/score/sort/topN 变更 | 否 |
| candidate pack 未批准、过期或 hash/readback 不一致不得进入 prompt | 4、6、7、9、12 | approval/readback/hash/manifest/lineage gate；未批准或过期不启动 worker，可触发后台 refresh；hash/readback/lineage 损坏 fail closed | 否 |
| Python 确定性层不能写自然语言入选理由或投资判断 | 1、2、5、6、12 | candidate pack 只含事实表、字段说明、数据质量、来源摘要；理由由 worker L1 产出 | 否 |
| `/select` 最终结论只能是进入 `/report`、观察、放弃 | 3、8、9、10、13 | `SelectionDecision` 枚举和 reader-facing artifact contract | 否 |
| `/select` 只做三分类候选分流；表达类措辞不作为 runtime 失败条件 | 1、6、8、9、13 | 约束三分类机械解析与候选池边界，不约束 `/report` PM 表达 | 否 |
| `/select` 完成后不自动触发 `/report` | 4、9、10、13 | `waiting_report_confirmation` 后必须等用户确认 | 否 |
| 用户确认后只对确认 ticker 启动现有 `/report` workflow | 10、14 | `ReportHandoffRequest` 复用 `ReportTaskQueue.enqueue_report_task` / existing `/report` request factory | 否 |
| 用户确认必须幂等 | 3、4、10、13 | 请求幂等键 `select_workflow_run_id + ticker + confirmation_id` + report handoff 去重键 `select_workflow_run_id + ticker` | 否 |
| no completed run/stale run/warehouse 证据不足不能现场拉数 | 4、7、9、13 | `SelectUnavailableCode` 触发后台 refresh 返回；tool error code 与 hash/readback/lineage integrity 失败仍 fail closed | 否 |
| 第一版只面向 CN_A A股日频收盘后选股 | 1、3、4、11 | `SelectRequest.market/profile` 只接受 `CN_A`；其它市场 fail closed | 否 |
| HK/US/CRYPTO 不 fallback 到 CN_A prompt 或策略 | 1、3、4、15 | 非 CN_A 直接 `market_strategy_unapproved`；不复用 A股策略 | 非 CN_A 策略需未来确认 |
| provider payload 是工具可见性和 prompt 边界最终验收证据 | 7、8、13 | live provider payload 必验工具 schema、model-visible materials、runtime marker | 否 |
| candidate pack 摘要可给 manager/PM，但不得含 raw/debug/protocol/ref/hash 文本 | 3、6、8、13 | `CandidatePackSummary` 单独模型；manifest/hash/lineage 仅审计可见 | 否 |
| 数据源复用 data_gateway、Mongo、OpenViking | 2、5、11、12 | selection batch plan 复用 provider attempt/cache/normalized/gap/readiness 语义 | 具体 provider plugin/adapter 实测需确认 |
| 东财不作为默认稳定依赖，不允许隐藏 fallback provider | 11、13、15 | provider 来源从 approved registry/batch plan 取；失败写 attempt/gap，不切旧路径 | 具体 provider matrix 需实测 |
| myhhub/stock 与 Sequoia-X 策略全部纳入第一版优先实现 | 5、6、11、13 | 每条策略以 source + variant 形式进入 approved strategy config；缺字段必须补数据合同，不得静默裁剪 | 否 |
| v1 透明排序权重已确认 | 5、6、13 | 基础分 100 + 风险/数据缺口扣分；权重必须进入 strategy config 与 candidate pack 审计 | 否 |
| selection provider batch plan 不伪装成单 ticker `RunProviderPlan` | 3、5、11 | 新增 batch-scope `SelectionRunPlan` / `SelectionProviderBatchPlan` 语义 | 是否扩展现有 `RunProviderPlan` 需实现时确认 |
| 同一 market+trade_date+profile 只能一个 active data job | 4、5 | lease 状态机、`acquire_selection_job_lease(...)` | 具体 lease 存储实现需确认 |
| completed run 不原地覆盖，rerun 新 run id | 4、5 | `supersedes_run_id` lineage；immutable completed artifacts | 否 |
| backfill 不改变 latest pointer，除非 freshness 规则认为它是最新可用交易日 | 4、5、15 | store API 区分 backfill/rerun/latest pointer update | 节假日/交易日历来源需确认 |
| TTL 第一版按交易日口径 | 4、6、9 | latest terminal run freshness 校验；非交易日可用最近交易日 | 交易日历实现来源需确认 |
| Artifact approval 负责候选包与 worker L1 入下游 | 2、6、8、12 | candidate pack approval、worker artifact approval、manifest/hash/readback | 否 |
| hard-gate-failed artifact 不进入下游 | 4、8、9、12 | workflow 在 approval failed 后停止，不写 downstream refs | 否 |
| OpenViking 保存 approved material、manifest、hash、lineage；Mongo 保存 provider raw/normalized/attempt/cache | 11、12 | 明确 raw/normalized/approved material 分层 | 否 |
| 测试不能用 prompt 禁止词扫描证明 `/select` 输出语义 | 13 | 测 reader-facing artifact 语义，不测 prompt 中禁止词完全不出现 | 否 |
| Guard source 必须引用总体设计 §2.2/§3.8.5/§11.1 | 13 | 测试设计显式写 Guard source 和非 report guard 边界 | 否 |
| 实施顺序按最小闭环 | 14 | models/state/store -> contract -> batch -> tool -> worker -> dispatch -> command -> handoff -> payload -> integration | 否 |

### 0.2 不确定性检查

这些事项影响最终运行行为，但不阻塞本文把接口和 fail-closed 行为写清楚。实现时如果缺少已批准配置或策略，相关生产路径必须返回明确不可用状态，不得自行套用建议值。

| 不确定项 | 对编码的影响 | 本文处理 | 需要人类确认 |
|---|---|---|---|
| 事件类 provider 覆盖 | 影响定向增发事件策略资料质量 | 策略必须保留；若真实事件源不可用，candidate pack 记录本轮事件资料缺口，不得伪造命中 | 否 |
| 参考策略以外的新策略 | 影响策略集合扩展 | 不在第一版自动加入；需新增来源审计和人类批准 | 是 |
| `select backfill/rerun` CLI 命令名和权限 | 影响运维入口和权限模型 | 本文给建议接口；不冻结命令名和权限 | 是 |
| UI 展示形态 | 影响普通消息、结果卡片、确认卡片 | 本文只定义状态语义和 DTO；不冻结视觉形态 | 是 |
| 新 enum/class/file 名 | 影响代码命名 | 全部标注建议名，需实现时按代码风格确认 | 是 |
| 非 CN_A 市场策略 | 影响 HK/US/CRYPTO `/select` | 第一版 fail closed；未来独立设计 | 是 |
| 交易日历来源 | 影响 TTL、backfill latest pointer | 本文只定义口径；实现需选 approved calendar source | 是 |
| provider adapter 具体矩阵 | 影响字段覆盖和吞吐 | 本文复用 data_gateway 语义；实测后固化 | 是 |

阻塞判断：未发现总体设计内部矛盾。上表未确认项会阻塞生产启用相应行为，但不阻塞编码接口设计，因为本文要求缺少已批准配置时 fail closed。

## 1. 设计边界

### 1.1 覆盖范围

本文覆盖 `docs/A股选股总体设计.md` 中以下章节的可实现要求：

- §1 总体结论与第一版新增/扩展合同。
- §2 产品目标与非目标。
- §3 模块划分、数据流、`/select` 完整流程、用户可见结果。
- §4 确定性执行层。
- §5 OpenClaw Selection Workers 与 tool matrix。
- §6 Worker Prompt 边界。
- §7 `/select` System Prompt 与 OpenClaw 处理。
- §8 Artifact 设计。
- §9 数据源与接口策略。
- §10 调度策略、latest terminal run、TTL、用户确认。
- §11 测试与验收。
- §12 停止条件。
- §13 第一版实施切片。

### 1.2 不覆盖或仅定义 fail-closed 的事项

- 具体 cron、节假日数据源、交易日历 provider。
- CLI 命令最终名称、权限、审计主体。
- UI 视觉呈现。
- HK/US/CRYPTO selection 策略、prompt、provider 矩阵。
- 新 provider adapter 的真实字段覆盖、限流、许可和失败率。
- 超出 `myhhub/stock` 与 `Sequoia-X` 审计范围的新策略。

这些事项必须进入第 15 章“需要人类确认”，不得在实现时默认采用本文之外的猜测。

### 1.3 禁止事项

- 不修改 `third_party/openclaw`；如未来发现必须修改，只能按 AGENTS 的通用 runtime seam 规则另行审批。
- 不把 `/select` workflow、A股策略、candidate pack 生成、用户确认或 report handoff 写入 OpenClaw。
- 不让 `/select` 现场拉全市场数据。
- 不让 selection worker 调 data_gateway/provider、Mongo raw、OpenViking deep read/write 或任何未批准工具。
- 不把 raw/debug/provider envelope、Mongo/OpenViking 协议、refs/hash/manifest/lineage 文本放入模型可见 prompt。
- 不让 Python 写自然语言入选理由、投资判断、最终结论、目标价、止损价、交易建议。
- 不隐藏 fallback provider，不用旧 MCP 或旧 provider executor 兜底。
- 不自动触发 `/report`。

## 2. 现有代码落点

下表中的新增文件均为建议名，需实现时按代码风格确认。

| 模块 | 文件路径 | 职责 | 输入 | 输出 | 不允许做的事 | 依赖 | 失败行为 |
|---|---|---|---|---|---|---|---|
| selection request model | `src/claw_trade/selection/models.py`（新增） | 定义 `SelectRequest`、run、pack、dispatch、decision、confirmation DTO | `/select` 命令解析结果、latest run refs | typed DTO | 不复用 ticker-centric `RunRequest` 承担 selection 语义 | `src/claw_trade/data_gateway/models.py` 的 `Market` | 无效市场/profile/date 返回 typed error |
| workflow entry point | `src/claw_trade/workflow/models.py`（现有，扩展建议）或 `src/claw_trade/selection/models.py` | 增加 `select_command` 入口语义 | chat command | entry point value | 不让 ordinary chat 静默进入 selection | `ChatController` | 非 `/select` 继续普通 chat |
| select command controller | `src/claw_trade/selection/controller.py`（新增） | 检查可用 completed+approved pack；可用才调度 4 worker、产出结果；不可用且属于 no-run/no_candidate/stale/candidate_pack_not_approved/warehouse 证据不足时触发后台 refresh | `SelectRequest` | `SelectionWorkflowRun` / reader result / refresh 状态 | 不直接调 provider、不直接读表、不同步跑全市场、不打分、不写理由 | selection store、scheduler/refresh gateway、OpenViking、OpenClaw client | 返回“补数已启动/已有补数在跑/补数通道未配置”或明确 failed 状态 |
| selection run store | `src/claw_trade/selection/store.py`（新增） | 保存后台 data run 与用户 workflow run；latest pointer；confirmation；UI 启动恢复 | run DTO、artifact refs、data job terminal evidence | persisted run state | 不覆盖 completed/no_candidate run；不把 failed run 当 latest；不允许仅内存字典启动 `/select` | filesystem/Mongo 可选，OpenViking refs | 写入/恢复失败则 stop，不启动 worker |
| background selection data job | `src/claw_trade/selection/data_job.py`（新增） | 执行收盘后全市场 batch，也承接 `/select` 触发的后台 refresh | `SelectionRunPlan` | `SelectionDataRun` | 不在用户 `/select` 请求内同步执行；不由 `/select` 直接调 provider 或读表 | data_gateway、store、lease | 失败写 attempts/gaps/status |
| scheduler / lease | `src/claw_trade/selection/scheduler.py`（新增） | scheduled/backfill/rerun 触发与互斥 | market、trade_date、trigger_source | lease/data run id | 不并发写同一 active run | store/clock/calendar | `already_running` 或 lease takeover with lineage |
| feature builder | `src/claw_trade/selection/features.py`（新增） | 从 normalized refs 计算可复算特征 | normalized refs、universe | `feature_snapshot` | 不写投资判断 | data_gateway store | 缺关键字段写 gap 或 blocked |
| selection engine | `src/claw_trade/selection/engine.py`（新增） | 硬过滤、策略命中、评分排序到 top 20 | feature snapshot、approved config | scores/hits/top20 | 不使用未批准权重/阈值；不写自然语言理由 | strategy config store | 缺 approved config 则 `strategy_config_unapproved` |
| candidate pack builder | `src/claw_trade/selection/candidate_pack.py`（新增） | 生成 worker 可读事实表和审计 manifest | top20、features、hits、quality | `candidate_pack.md/json`、manifest | 不写“值得买/看好”等理由 | engine outputs、source refs | pack 字段缺失则 approval failed |
| artifact approval | `src/claw_trade/selection/artifacts.py`（新增）或扩展 `src/claw_trade/artifacts/**` | 校验 candidate pack 与 selection worker L1，写 OpenViking approved material | artifact body、manifest、hash | `CandidatePackRef`、`SelectionWorkerArtifact` | 不把 failed artifact 放入下游 | OpenViking client、hash/readback | failed 状态，不继续 |
| candidate pack tool backend | `src/claw_trade/selection/tools.py`（新增） | 实现 `claw_get_selection_candidate_pack` 后端 | runtime context | model-visible pack body | 不调 provider/raw/score | selection store、OpenViking | 返回明确 tool error code |
| OpenClaw tool wrapper | `openclaw_plugins/claw-trade-frontline-tools/index.js`（现有，扩展建议）或 `openclaw_plugins/claw-trade-selection-tools/index.js`（新增建议） | 注册 provider-visible tool；从 `ctx.singleWorkerCommand` 取 context | OpenClaw tool call | tool result text | 不接受业务参数；不扩 top20 | selection tool backend subprocess/API | context missing/mismatch 硬失败 |
| worker stage policy | `agents/selection_strategist/STAGES.yaml` 等（新增） | 定义 profile、stage、tool intent、openviking_access | worker profile | allowed tool intent | prompt 不能授权工具 | `tool_names.py` | profile missing fail closed |
| worker prompts | `agents/selection_*/prompts/CN_A.md`（新增） | selection worker 角色与输出结构 | runtime vars、approved L1 | worker L1 | 不 fallback 到 US/HK/CRYPTO | agent config | missing prompt fail closed |
| shared skill | `agents/selection_*/skills/selection-candidate-review/SKILL.md` 或共享目录（新增建议） | 共同评审方法 | candidate materials | method guidance | 不替代 worker prompt 权威 | OpenClaw workspace loader | workspace validation failed |
| OpenClaw worker dispatch | `src/claw_trade/selection/dispatch.py`（新增） | 构造 selection single-worker command、运行 OpenClaw、保存 evidence | `SelectionWorkerDispatch` | provider payload、raw LLM output、result path | 不让 Python 调工具代 worker | `src/claw_trade/runtime/openclaw_client.py` | runtime failed -> workflow failed |
| provider payload evidence | `src/claw_trade/selection/evidence.py`（新增建议） | 读取/验证 provider payload、visible tools、materials boundary | OpenClaw evidence paths | validation result | 不用静态 render 替代 payload | `EvidenceReader`、guards | violation hard fail |
| user confirmation | `src/claw_trade/selection/confirmation.py`（新增） | 记录确认、幂等、防重复 report | decision、ticker、request id | `SelectionConfirmation` | 不自动 report；不允许非入选 ticker | selection store、report queue | duplicate returns existing result |
| report handoff | `src/claw_trade/selection/report_handoff.py`（新增） | 构造现有 `/report` task input | confirmed ticker | report task/run | 不把 `/select` 结论注入 `/report` PM | `build_report_run_request`、`ReportTaskQueue` | report enqueue 失败按 report 队列错误处理 |
| tests | `tests/unit/selection/**`、`tests/contracts/test_selection_*.py`、`tests/integration/selection/**`（新增） | 覆盖模型、状态机、tool、payload、failure、handoff | fixtures | pytest evidence | 不用 mock provider payload 代替 live proof | existing fakes/fixtures | failure 输出指向总体设计 requirement |

## 3. 数据模型

本章字段名为建议名，需实现时按代码风格确认。除 `reader_facing_*` 字段外，所有审计字段默认不可进入模型可见 prompt。

### 3.1 `SelectRequest`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `request_id` | `str` | 必填 | UI/API idempotency | 否 | 是 | 全局请求幂等 | 投资结论 |
| `market` | `Literal["CN_A"]` | 必填 | `/select` 参数或默认 | 是 | 是 | 第一版只能 `CN_A` | HK/US/CRYPTO fallback |
| `profile` | `Literal["CN_A"]` | 必填 | market 映射 | 是 | 是 | 必须与 market 匹配 | 其它 prompt profile |
| `trade_date` | `str | None` | 可选 | 用户参数 | 是 | 是 | ISO date；为空表示 latest terminal | 自然语言日期猜测 |
| `user_id` | `str | None` | 可选 | UI/session | 否 | 是 | 只用于审计/权限 | prompt 材料 |
| `created_at` | `str` | 必填 | controller clock | 否 | 是 | ISO timestamp | - |
| `entry_point` | `Literal["select_command"]` | 必填 | command parser | 否 | 是 | 不得等于 `report_command` | report 语义 |
| `system_context_policy` | `Literal["single_worker_minimal"]` | 必填 | command parser/default | 否 | 是 | 必须固定为 `single_worker_minimal`（总体设计 §7.1） | `report_command` 默认策略或其它策略 |

### 3.2 `SelectionRunPlan`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `selection_run_id` | `str` | 必填 | store 生成 | 否 | 是 | immutable run id | ticker-centric report id |
| `market` | `Literal["CN_A"]` | 必填 | scheduler/request | 否 | 是 | 第一版 CN_A | 非 CN_A strategy |
| `profile` | `Literal["CN_A"]` | 必填 | scheduler/request | 否 | 是 | 与 market 匹配 | fallback profile |
| `trade_date` | `str` | 必填 | trading calendar | 可出现在 pack meta | 是 | 已收盘交易日 | 盘中日期 |
| `lookback_trading_days` | `int` | 必填 | approved config | 否 | 是 | 总体设计默认建议 260，但值需批准 | 硬编码未批准值 |
| `universe_scope` | `str` | 必填 | approved config | 可在 pack meta | 是 | 全 A 或批准股票池 | 手工补股票 |
| `provider_batch_plan_ref` | `str` | 必填 | data gateway plan store | 否 | 是 | batch-scope，不伪装 ticker | raw payload |
| `approved_strategy_config_ref` | `str` | 必填 | strategy config approval | 否 | 是 | 缺失则 fail closed | 未批准权重/阈值 |
| `trigger_source` | `Literal["scheduled","manual_backfill","manual_rerun"]` | 必填 | scheduler/CLI | 否 | 是 | 记录来源 | 用户 prompt |
| `supersedes_run_id` | `str | None` | 可选 | rerun | 否 | 是 | rerun 才可填 | 覆盖旧 run |

### 3.3 `SelectionDataRun`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `selection_run_id` | `str` | 必填 | run plan | 否 | 是 | 与 plan 一致 | - |
| `status` | `SelectionDataRunStatus` | 必填 | state machine | 否 | 是 | 见 §4.1 | 假成功 |
| `lease_id` | `str | None` | 可选 | lease store | 否 | 是 | running 时必填 | prompt 材料 |
| `universe_snapshot_ref` | `str | None` | 可选 | universe builder | 否 | 是 | completed 前可空 | 全量正文进 prompt |
| `normalized_refs` | `tuple[str, ...]` | 可选 | data_gateway | 否 | 是 | Mongo/evidence refs | raw JSON 正文 |
| `feature_snapshot_ref` | `str | None` | 可选 | feature builder | 否 | 是 | completed 前可空 | 投资理由 |
| `candidate_pack_ref` | `CandidatePackRef | None` | 可选 | approval | 否 | 是 | completed 必填且 approved；no_candidate 必须为空 | 未批准 artifact |
| `data_gaps` | `tuple[DataGapRef, ...]` | 可选 | data_gateway | 否 | 是 | 缺口可追溯 | 编造缺口 |
| `started_at` / `completed_at` / `failed_at` | `str | None` | 可选 | store clock | 否 | 是 | 状态匹配 | - |
| `failure_code` / `failure_reason` | `str | None` | 可选 | failing function | 否 | 是 | failed 必填 | 模糊失败 |

### 3.4 `SelectionWorkflowRun`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `select_workflow_run_id` | `str` | 必填 | store | 否 | 是 | 用户 `/select` run id | data run id 混用 |
| `selection_run_id` | `str` | 必填 | latest terminal lookup | 是（context） | 是 | 绑定 completed data run；no_candidate 不进入 worker prompt | latest 模糊指针 |
| `status` | `SelectionWorkflowStatus` | 必填 | state machine | 否 | 是 | 见 §4.2 | 隐藏 fallback |
| `request` | `SelectRequest` | 必填 | controller | 否 | 是 | immutable | report request |
| `candidate_pack_ref` | `CandidatePackRef` | 必填 | validation | 否 | 是 | approved/hash/readback valid | raw refs |
| `dispatches` | `tuple[SelectionWorkerDispatch, ...]` | 可选 | dispatch controller | 否 | 是 | 顺序固定 | LLM 调度决定 |
| `worker_artifacts` | `tuple[SelectionWorkerArtifact, ...]` | 可选 | approval | 部分 L1 可见给下游 | 是 | 仅 approved 进入下游 | failed L1 |
| `decision` | `SelectionDecision | None` | 可选 | PM artifact parser/approval | 是（reader） | 是 | completed 后必填 | 买卖建议 |
| `created_at` / `updated_at` | `str` | 必填 | store | 否 | 是 | ISO timestamp | - |

### 3.5 `CandidatePackRef`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `selection_run_id` | `str` | 必填 | data run | 工具结果可含 meta | 是 | 与 run 匹配 | - |
| `material_id` | `str` | 必填 | OpenViking approval | 否 | 是 | approved material id | prompt 正文 |
| `l1_uri` | `str` | 必填 | OpenViking | 否 | 是 | readback target | 模型可见 URI |
| `content_sha256` | `str` | 必填 | approval | 否 | 是 | readback 匹配 | 模型可见 hash |
| `manifest_ref` | `str` | 必填 | approval | 否 | 是 | manifest 可读 | provider envelope |
| `approved_at` | `str` | 必填 | approval | 否 | 是 | ISO timestamp | - |
| `expires_at` | `str` | 必填 | TTL policy | 可转为自然语言 freshness | 是 | stale 后不可用 | silently stale |
| `pack_summary_ref` | `str` | 必填 | pack builder | 否 | 是 | manager/PM 只读 summary body | raw refs in prompt |

### 3.6 `CandidatePackManifest`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `schema_version` | `str` | 必填 | pack builder | 否 | 是 | semver/date version | - |
| `selection_run_id` | `str` | 必填 | pack builder | 否 | 是 | run 绑定 | - |
| `market` / `profile` / `trade_date` | `str` | 必填 | run plan | 否 | 是 | CN_A only | fallback market |
| `candidate_count` | `int` | 必填 | pack builder | 可见摘要可含 | 是 | approved pack 为 1..20；20 是评审上限，不是必须凑满；0 只可进入 no-candidate 状态，不生成 fake approved pack | top20 以外明细 |
| `source_lineage_refs` | `tuple[str, ...]` | 必填 | data_gateway/feature builder | 否 | 是 | provider plan、attempt、normalized、feature refs | raw payload 正文 |
| `pack_body_sha256` | `str` | 必填 | approval | 否 | 是 | readback hash | prompt 正文 |
| `strategy_config_ref` | `str` | 必填 | engine | 否 | 是 | approved only | 未批准参数 |
| `readback_status` | `Literal["verified"]` | 必填 | approval | 否 | 是 | 未 verified 不可用 | fake success |

### 3.7 `CandidatePackSummary`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `summary_md` | `str` | 必填 | pack builder | 是 | 是 | top20 排序事实表、字段说明、数据质量、读者化来源名称/日期 | refs/hash/manifest/raw/debug/protocol |
| `candidates` | `tuple[CandidateFactRow, ...]` | 必填 | pack builder | 是 | 是 | 每行只含事实字段 | 自然语言入选理由 |
| `data_quality_summary` | `str` | 必填 | pack builder | 是 | 是 | 缺口与时效说明 | provider attempt JSON |
| `source_summary` | `str` | 必填 | pack builder | 是 | 是 | 读者化来源摘要 | source secret/headers |

### 3.8 `SelectionWorkerDispatch`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `dispatch_id` | `str` | 必填 | dispatch controller | 否 | 是 | 单 worker turn id | - |
| `select_workflow_run_id` | `str` | 必填 | workflow run | 是（runtime context） | 是 | 绑定 selection workflow | report run id 混用 |
| `worker_id` | `SelectionWorkerId` | 必填 | fixed sequence | 是 | 是 | 4 个固定 worker | LLM 选择结果 |
| `stage` | `SelectionStage` | 必填 | fixed sequence | 是 | 是 | worker dispatch 仅允许 `selection_review`/`selection_decision`/`selection_portfolio_decision`；handoff 需记录 `selection_report_handoff` 等价标识 | report `portfolio_decision` 语义 |
| `allowed_tools` | `tuple[str, ...]` | 必填 | STAGES.yaml + registry | provider payload 可见 | 是 | 必须等于 worker matrix | 临时 prompt 授权工具 |
| `prompt_runtime_vars` | `dict[str, str]` | 必填 | controller | 是 | 是 | market/profile/trade_date/run ids | raw/debug/protocol |
| `model_visible_materials` | `tuple[str, ...]` | 必填 | approved L1 / summary | 是 | 是 | 只含 approved natural-language material | manifest/hash/lineage |
| `evidence_dir` | `Path` | 必填 | store | 否 | 是 | run/call scoped | - |
| `provider_payload_ref` | `str | None` | 可选 | OpenClaw result | 否 | 是 | success 必填 | reconstructed prompt |

### 3.9 `SelectionWorkerArtifact`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `artifact_id` | `str` | 必填 | approval | 否 | 是 | unique | - |
| `worker_id` / `stage` | `str` | 必填 | dispatch | 下游可见 | 是 | 与 dispatch 匹配 | - |
| `l1_text` | `str` | 必填 | OpenViking readback | 下游可见 | 是 | approved only | Python rewrite |
| `material_id` / `l1_uri` / `l1_sha256` | `str` | 必填 | OpenViking | 否 | 是 | readback verified | prompt-visible protocol |
| `approval_status` | `Literal["approved"]` | 必填 | approval | 否 | 是 | failed 不入模型 | warning-only bypass |
| `provider_payload_ref` | `str` | 必填 | OpenClaw | 否 | 是 | final prompt/tool proof | static render |

### 3.10 `SelectionDecision`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `select_workflow_run_id` | `str` | 必填 | PM artifact | 否 | 是 | 绑定本次 `/select` | - |
| `enter_report` | `tuple[DecisionTicker, ...]` | 必填 | PM L1 解析/approval | reader 可见 | 是 | 0-3 只，且来自 candidate pack | 买入/持有/卖出 |
| `watch` | `tuple[DecisionTicker, ...]` | 必填 | PM L1 | reader 可见 | 是 | 来自 candidate pack | 交易建议 |
| `reject` | `tuple[DecisionTicker, ...]` | 必填 | PM L1 | reader 可见 | 是 | 来自 candidate pack | 目标价/止损价 |
| `report_questions` | `Mapping[str, tuple[str, ...]]` | 可选 | PM L1 | reader 可见 | 是 | 只写 `/report` 需验证问题 | 最终结论 |
| `source_summary` | `Mapping[str, str]` | 可选 | PM L1/candidate summary | reader 可见 | 是 | 读者化来源摘要 | raw refs/hash |
| `approved_material_id` | `str` | 必填 | approval | 否 | 是 | PM decision L1 material | prompt protocol |

### 3.11 `SelectionConfirmation`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `confirmation_id` | `str` | 必填 | confirmation controller | 否 | 是 | unique | - |
| `idempotency_key` | `str` | 必填 | controller | 否 | 是 | `select_workflow_run_id:ticker:confirmation_id` | report conclusion |
| `report_handoff_dedupe_key` | `str` | 必填 | controller | 否 | 是 | `select_workflow_run_id:ticker`；同 workflow 同 ticker 全局唯一，防止不同 `confirmation_id` 重复创建 report run | 绕过去重重复启动 report |
| `select_workflow_run_id` | `str` | 必填 | UI action | 否 | 是 | completed run only | stale run |
| `ticker` | `str` | 必填 | user click | 否 | 是 | 必须在 `enter_report` | 非入选 ticker |
| `status` | `SelectionConfirmationStatus` | 必填 | state machine | 否 | 是 | 仅持久化 `pending`/`accepted`/`report_handoff_started`/`report_handoff_failed`；`duplicate`/`expired`/`cancelled` 只作为返回码 | fake report success |
| `report_task_id` / `report_run_id` | `str | None` | 可选 | report queue/workflow | 否 | 是 | handoff started 后可填 | `/select` PM 交易结论 |

### 3.12 `ReportHandoffRequest`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `confirmation_id` | `str` | 必填 | confirmation | 否 | 是 | idempotent | - |
| `ticker` | `str` | 必填 | confirmation | 否 | 是 | confirmed ticker only | unconfirmed ticker |
| `company_name` | `str` | 必填 | candidate pack/identity resolver | 否 | 是 | 不猜公司名；缺则 resolver | 选股理由 |
| `market` / `profile` | `Literal["CN_A"]` | 必填 | decision | 否 | 是 | existing `/report` CN_A | CN_A fallback for other market |
| `current_date` | `str` | 必填 | confirmation/report factory | 否 | 是 | ISO date | stale label fake today |
| `selection_context_ref` | `str` | 必填 | selection decision | 否 | 是 | 仅审计追溯 | 注入 `/report` PM 结论 |
| `selection_stage_marker` | `Literal["selection_report_handoff"]` | 必填 | confirmation controller | 否 | 是 | 作为总体设计要求的 `selection_report_handoff` 等价阶段标识；用于 run/evidence 对齐，不代表新增 worker stage | 混淆成 report workflow stage |
| `report_request` | `RunRequest` | 必填 | `build_report_run_request` | 否 | 是 | entry_point=`report_command` | `select_command` |

### 3.13 `CandidateFactRow`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `rank` | `int` | 必填 | scoring output | 是 | 是 | 1..20 且唯一 | 超出 top20 或重复 rank |
| `ticker` | `str` | 必填 | universe/identity map | 是 | 是 | 规范化证券代码 | 非候选池 ticker |
| `company_name` | `str` | 必填 | identity resolver | 是 | 是 | 只允许事实名称 | Python 生成观点 |
| `industry` | `str | None` | 可选 | normalized fundamental | 是 | 是 | 缺失需配套质量说明 | 臆测行业 |
| `feature_values` | `Mapping[str, float | int | str | None]` | 必填 | feature snapshot | 是 | 是 | 仅事实值与单位 | 投资判断句子 |
| `strategy_hits` | `tuple[str, ...]` | 必填 | strategy engine | 是 | 是 | 只记录命中标识 | “建议买入”式解释 |
| `risk_flags` | `tuple[str, ...]` | 必填 | deterministic rules | 是 | 是 | 可追溯到输入字段 | 主观风险推断 |
| `data_quality` | `str` | 必填 | quality summary | 是 | 是 | 只描述缺口/时效 | 编造成功率 |
| `source_summary` | `str` | 必填 | approved summary builder | 是 | 是 | 读者化来源摘要 | refs/hash/manifest/raw/debug/protocol |

### 3.14 `DecisionTicker`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `ticker` | `str` | 必填 | PM 明确输出 | 是 | 是 | 必须属于 allowed ticker set | Python 推断补全 |
| `company_name` | `str` | 必填 | PM 明确输出/候选池确定性映射 | 是 | 是 | 与 ticker 一致；只能从 candidate pack 映射补齐，不允许语义补全 | 臆测公司名 |
| `rationale_excerpt` | `str` | 必填 | PM 明确输出 | 是 | 是 | 仅保留 PM 原文中的研究资源分配理由摘录 | Python 改写为投资建议 |

### 3.15 `DataGapRef`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `gap_id` | `str` | 必填 | data_gateway/readiness | 否 | 是 | 全局唯一 | - |
| `domain` | `str` | 必填 | pack domain | 否 | 是 | 属于已批准 selection 域 | 未批准域 |
| `gap_code` | `str` | 必填 | provider attempt/readiness | 否 | 是 | 例如 `field_missing`/`rate_limited`/`schema_invalid` | 模糊“未知异常” |
| `severity` | `Literal["warn","blocker"]` | 必填 | readiness policy | 否 | 是 | blocker 必须阻断下游 | 降级 blocker 为提示 |
| `attempt_refs` | `tuple[str, ...]` | 必填 | attempt store | 否 | 是 | 至少一个 attempt 或明确 admission skipped receipt | 无证据缺口 |
| `reader_message` | `str` | 必填 | quality renderer | 是 | 是 | 读者可理解缺口说明 | provider raw/debug 对象 |

### 3.16 `SelectionProviderBatchPlan`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `plan_id` | `str` | 必填 | planner/store | 否 | 是 | 唯一 id | - |
| `scope` | `Literal["selection_batch"]` | 必填 | planner | 否 | 是 | 固定为 `selection_batch`，不得伪装单 ticker plan | `/report` ticker scope |
| `market` / `profile` / `trade_date` | `str` | 必填 | run plan | 否 | 是 | 与 `SelectionRunPlan` 一致 | fallback market/profile |
| `lookback_trading_days` | `int` | 必填 | approved strategy config | 否 | 是 | approved-only | 写死未批准窗口 |
| `universe_scope` | `str` | 必填 | approved strategy config | 否 | 是 | approved-only | 人工注入临时股票 |
| `coverage_groups` | `tuple[str, ...]` | 必填 | planner | 否 | 是 | 明确域覆盖与顺序 | 省略关键组导致不可追溯 |
| `provider_candidates` | `tuple[str, ...]` | 必填 | provider registry | 否 | 是 | 每个候选需能落 attempt | 隐藏 fallback provider |
| `ttl_policy_ref` | `str` | 必填 | approved policy | 否 | 是 | 与 freshness 规则绑定 | 随机 TTL |
| `lineage_root_ref` | `str` | 必填 | planner | 否 | 是 | 用于 pack->plan 追溯 | 无 lineage |

### 3.17 `ApprovedSelectionStrategyConfig`

| 字段名 | 类型 | 必填/可选 | 来源 | 模型可见 | 审计可见 | 约束 | 不能包含什么 |
|---|---|---|---|---:|---:|---|---|
| `config_ref` | `str` | 必填 | strategy config store | 否 | 是 | 必须来自 approved config store | 未批准草稿 |
| `market` / `profile` | `Literal["CN_A"]` | 必填 | config metadata | 否 | 是 | 第一版固定 CN_A | 非 CN_A fallback |
| `effective_trade_date` | `str` | 必填 | config metadata | 否 | 是 | 与运行日期可比较 | 模糊生效日期 |
| `filter_set_ref` | `str` | 必填 | approved config | 否 | 是 | 引用 SEL-00 来源阈值；同类不同来源保留变体 | 硬编码无来源阈值 |
| `strategy_set_ref` | `str` | 必填 | approved config | 否 | 是 | 必须覆盖 §5.0.1 的优先实现策略集合 | 未批准策略默认启用 |
| `scoring_weight_ref` | `str` | 必填 | approved config | 否 | 是 | 必须覆盖 §5.0.2 的 v1 透明排序权重 | 默认权重猜测 |
| `status` | `Literal["approved"]` | 必填 | approval workflow | 否 | 是 | 缺失或非 approved 必须 fail closed | 自动降级为可用 |

## 4. 状态机

### 4.1 后台 candidate pool run 状态机

状态枚举建议名：`SelectionDataRunStatus`。

```text
planned
lease_pending
running
fetching_data
normalizing_inputs
building_features
filtering_and_scoring
building_candidate_pack
approving_candidate_pack
completed
no_candidate
failed
```

| 当前状态 | 触发事件 | 下一状态 | 失败状态 | 允许重试 | 允许继续 |
|---|---|---|---|---:|---:|
| `planned` | scheduler/backfill/rerun 创建 plan | `lease_pending` | `failed` | 是，新 run id | 否 |
| `lease_pending` | lease acquired | `running` | `failed` / `already_running` | 是，lease 过期后 takeover | 否 |
| `running` | run plan validated | `fetching_data` | `failed` | 是，新 run id | 否 |
| `fetching_data` | provider batch finished | `normalizing_inputs` | `failed` | 是，新 run id | 否 |
| `normalizing_inputs` | normalized refs valid | `building_features` | `failed` | 是，新 run id | 否 |
| `building_features` | feature snapshot written | `filtering_and_scoring` | `failed` | 是，新 run id | 否 |
| `filtering_and_scoring` | approved strategy config used | `building_candidate_pack`；若真实候选为 0 则 `no_candidate` | `failed` | 是，新 run id | 否 |
| `building_candidate_pack` | pack body/manifest written | `approving_candidate_pack` | `failed` | 是，新 run id | 否 |
| `approving_candidate_pack` | readback/hash/manifest verified | `completed` | `failed` | 是，新 run id | 否 |
| `completed` | none | terminal | terminal | rerun 新 run | 是，可被 latest 读取 |
| `no_candidate` | none | terminal | terminal | rerun 新 run | 否；`/select` 只提示本轮无候选，不启动 worker |
| `failed` | manual rerun | new `planned` | terminal | 是，新 run id | 否 |

伪码：

```python
def run_selection_data_job(plan):
    lease = acquire_selection_job_lease(plan.market, plan.trade_date, plan.profile)
    if lease.status == "already_running":
        return mark_selection_run_failed(plan.run_id, "already_running")
    try:
        mark_status(plan.run_id, "fetching_data")
        raw_refs = fetch_selection_market_data(plan)
        mark_status(plan.run_id, "normalizing_inputs")
        normalized = normalize_selection_inputs(plan, raw_refs)
        mark_status(plan.run_id, "building_features")
        features = build_feature_snapshot(plan, normalized)
        mark_status(plan.run_id, "filtering_and_scoring")
        filtered = run_hard_filters(plan, features)
        scored = score_candidates(plan, filtered)
        if scored.candidate_count == 0:
            return mark_selection_run_no_candidate(plan.run_id, scored.data_gaps)
        mark_status(plan.run_id, "building_candidate_pack")
        pack = build_candidate_pack(plan, scored)
        mark_status(plan.run_id, "approving_candidate_pack")
        approved_ref = approve_candidate_pack(plan, pack)
        return mark_selection_run_completed(plan.run_id, approved_ref)
    except SelectionError as exc:
        return mark_selection_run_failed(plan.run_id, exc.code, exc.reason)
    finally:
        release_lease(lease)
```

### 4.2 用户 `/select` workflow 状态机

状态枚举建议名：`SelectionWorkflowStatus`。

```text
received
resolving_request
loading_completed_selection_run
validating_candidate_pack
select_run_created
strategist_running
strategist_approved
skeptic_running
skeptic_approved
manager_running
manager_approved
portfolio_manager_running
completed
waiting_report_confirmation
report_handoff_started
```

说明：`report_handoff_started` 对应总体设计要求的 `selection_report_handoff` 阶段语义。第一版不新增 worker stage，而是在 handoff 记录和 evidence 中固定写入 `selection_stage_marker="selection_report_handoff"` 作为等价标识。

失败/不可用状态：

```text
market_strategy_unapproved
no_completed_selection_run
no_candidate_selection_run
stale_selection_run
selection_data_refresh_started
selection_data_refresh_running
selection_data_refresh_unconfigured
candidate_pack_not_approved
candidate_pack_integrity_failed
candidate_pack_lineage_incomplete
tool_schema_violation
worker_runtime_failed
artifact_approval_failed
selection_result_invalid
failed
```

| 当前状态 | 触发事件 | 下一状态 | 失败状态 | 允许重试 | 允许继续 |
|---|---|---|---|---:|---:|
| `received` | command parsed | `resolving_request` | `market_strategy_unapproved` | 是 | 否 |
| `resolving_request` | CN_A request valid | `loading_completed_selection_run` | `market_strategy_unapproved` | 是 | 否 |
| `loading_completed_selection_run` | latest found | `validating_candidate_pack` | `no_completed_selection_run` / `no_candidate_selection_run` / `stale_selection_run`，并尝试触发后台 refresh 返回 started/running/unconfigured | 是 | 否 |
| `validating_candidate_pack` | pack verified | `select_run_created` | pack failure states | 是，after new data run | 否 |
| `select_run_created` | strategist command built | `strategist_running` | `tool_schema_violation` | 是 | 否 |
| `strategist_running` | L1 approved | `strategist_approved` | worker/artifact failure | 仅 rerun workflow | 否 |
| `strategist_approved` | skeptic command built | `skeptic_running` | `tool_schema_violation` | 仅 rerun workflow | 否 |
| `skeptic_running` | L1 approved | `skeptic_approved` | worker/artifact failure | 仅 rerun workflow | 否 |
| `skeptic_approved` | manager command built | `manager_running` | `tool_schema_violation` | 仅 rerun workflow | 否 |
| `manager_running` | L1 approved | `manager_approved` | worker/artifact failure | 仅 rerun workflow | 否 |
| `manager_approved` | PM command built | `portfolio_manager_running` | `tool_schema_violation` | 仅 rerun workflow | 否 |
| `portfolio_manager_running` | PM decision approved | `completed` | `selection_result_invalid` | 仅 rerun workflow | 否 |
| `completed` | reader result rendered | `waiting_report_confirmation` | `selection_result_invalid` | 否 | 是，等确认 |
| `waiting_report_confirmation` | user confirms ticker | `report_handoff_started` | confirmation failure | 是，幂等 | 是 |

伪码见 §9。

### 4.3 candidate pack approval 状态机

状态枚举建议名：`CandidatePackApprovalStatus`。

```text
draft
submitted
schema_validating
lineage_validating
writing_openviking
readback_verifying
approved
rejected
integrity_failed
stale
```

| 当前状态 | 触发事件 | 下一状态 | 失败状态 | 允许重试 | 允许继续 |
|---|---|---|---|---:|---:|
| `draft` | pack builder submits | `submitted` | `rejected` | 是，重建 pack | 否 |
| `submitted` | schema check | `schema_validating` | `rejected` | 是，重建 pack | 否 |
| `schema_validating` | fields valid | `lineage_validating` | `rejected` | 是 | 否 |
| `lineage_validating` | provider/feature lineage complete | `writing_openviking` | `integrity_failed` | 是 | 否 |
| `writing_openviking` | write receipt | `readback_verifying` | `integrity_failed` | 是 | 否 |
| `readback_verifying` | sha/size/body match | `approved` | `integrity_failed` | 是 | 否 |
| `approved` | TTL expires | `stale` | - | 否 | 不给 `/select` latest 使用 |

伪码：

```python
def approve_candidate_pack(pack):
    validate_candidate_pack_schema(pack.model_visible_body)
    validate_no_forbidden_prompt_material(pack.model_visible_body)
    validate_lineage(pack.manifest.source_lineage_refs)
    receipt = openviking.write_material(pack.model_visible_body)
    body = openviking.readback(receipt)
    if sha256(body) != pack.manifest.pack_body_sha256:
        return integrity_failed("candidate_pack_hash_mismatch")
    return CandidatePackRef(..., approved_at=now(), expires_at=ttl_for_trade_date(...))
```

### 4.4 用户确认/report handoff 状态机

状态枚举建议名：`SelectionConfirmationStatus`。

```text
pending
accepted
report_handoff_started
report_handoff_failed
```

| 当前状态 | 触发事件 | 下一状态 | 失败状态 | 允许重试 | 允许继续 |
|---|---|---|---|---:|---:|
| `pending` | user confirms allowed ticker | `accepted` | invalid ticker / stale / integrity mismatch | 是，幂等 | 是 |
| `accepted` | enqueue `/report` | `report_handoff_started` | `report_handoff_failed` | 是，同 key 不重复 | 是 |
| `pending` | same idempotency key seen | `pending`（不改持久状态） | - | 返回原结果 | 是 |
| `report_handoff_failed` | same confirmation retries | `accepted` | `report_handoff_failed` | 是 | 否 |

`duplicate`、`expired`、`cancelled` 在第一版只作为非持久返回码/错误码（API 返回），不作为持久 workflow/stage 状态。

伪码：

```python
def confirm_selection_ticker(request):
    existing = store.lookup_confirmation(request.idempotency_key)
    if existing:
        return existing.as_duplicate_or_existing_result(code="duplicate_confirmation")

    workflow = store.load_workflow_run(request.select_workflow_run_id)
    if workflow.status not in {"waiting_report_confirmation", "completed"}:
        raise SelectionConfirmationError("selection_not_confirmable")

    decision = store.load_approved_selection_decision(request.select_workflow_run_id)
    if decision is None:
        raise SelectionConfirmationError("selection_decision_missing_or_unapproved")

    # 再校验：确认时必须重新验证 pack freshness + readback/hash/manifest/lineage 一致性
    recheck = validate_candidate_pack_for_confirmation(workflow.candidate_pack_ref, now=clock.now())
    if not recheck.ok:
        raise SelectionConfirmationError(recheck.code)  # stale/hash/readback/manifest/lineage

    enter_report_tickers = {item.ticker for item in decision.enter_report}
    if request.ticker not in enter_report_tickers:
        raise SelectionConfirmationError("ticker_not_allowed")

    dedupe_key = f"{request.select_workflow_run_id}:{request.ticker}"
    existing_handoff = store.lookup_report_handoff_by_dedupe_key(dedupe_key)
    if existing_handoff:
        return existing_handoff.as_existing_result(code="duplicate_handoff")

    confirmation = store.create_confirmation(...)
    handoff = build_report_handoff_request(confirmation, decision)
    result = start_existing_report_workflow(handoff)
    return store.mark_report_handoff_started(confirmation, result)
```

## 5. 后台数据任务详细设计

所有函数名为建议名，需实现时按代码风格确认。实际策略参数必须来自已批准 `approved_strategy_config_ref`；缺失时 fail closed。

### 5.0 已批准策略与排序合同

#### 5.0.1 第一版优先实现策略

2026-05-28 人类确认：`myhhub/stock` 与 `Sequoia-X` 审计到的本地可复现策略全部纳入第一版优先实现。实现时不得因为当前字段或 provider 未接好而裁剪策略；缺字段必须补 data_gateway 字段合同和 provider plugin/adapter。运行时不得伪造命中，真实字段不可得时应在 candidate pack 中记录该策略本轮资料缺口。

| source | variant_id | 策略名 | 输入字段类型 | 第一版处理 |
|---|---|---|---|---|
| `myhhub/stock` | `myhhub_volume_rise` | 放量上涨 | 日线、成交额、量比、涨跌幅、开收盘 | 优先实现 |
| `myhhub/stock` | `myhhub_ma30_keep_increasing` | 30 日均线持续上行 | 30 日均线窗口 | 优先实现 |
| `myhhub/stock` | `myhhub_parking_apron` | 涨停后平台整理 | 涨停识别、后续横盘窗口 | 优先实现 |
| `myhhub/stock` | `myhhub_backtrace_ma250` | 年线回踩 | 250 日均线、回踩窗口、量比 | 优先实现 |
| `myhhub/stock` | `myhhub_breakthrough_platform` | 平台突破 | 60 日均线、历史偏离区间 | 优先实现 |
| `myhhub/stock` | `myhhub_low_backtrace_increase` | 低位回升 | 60 日收益、回撤控制 | 优先实现 |
| `myhhub/stock` | `myhhub_turtle_60_close` | 60 日海龟突破 | 60 日最高收盘 | 优先实现 |
| `myhhub/stock` | `myhhub_high_tight_flag` | 高窄旗形 | 涨停识别、涨幅倍数、连续涨停 | 优先实现 |
| `myhhub/stock` | `myhhub_climax_limitdown` | 放量跌停/情绪极值 | 跌停识别、成交额、量比 | 优先实现 |
| `myhhub/stock` | `myhhub_low_atr` | 低 ATR / 低波动 | ATR、250 日均线、振幅 | 优先实现 |
| `Sequoia-X` | `sequoia_ma_volume` | 均线放量 | MA5/MA20、20 日均量 | 优先实现 |
| `Sequoia-X` | `sequoia_turtle_20_high` | 20 日海龟突破 | 20 日高点、成交额、阳线、涨幅 | 优先实现 |
| `Sequoia-X` | `sequoia_high_tight_flag` | 高窄旗形 | 40 日动量、10 日收敛、量能收缩 | 优先实现 |
| `Sequoia-X` | `sequoia_limit_up_shakeout` | 涨停洗盘 | 昨日涨停、今日收阴、放量、低点约束 | 优先实现 |
| `Sequoia-X` | `sequoia_uptrend_limit_down` | 上升趋势跌停 | MA20/MA60、跌停识别、量能 | 优先实现 |
| `Sequoia-X` | `sequoia_rps_breakout` | RPS 强势突破 | RPS120、阶段高点 | 优先实现 |
| `Sequoia-X` | `sequoia_private_placement` | 定向增发事件 | 公告/事件日期、7 日窗口 | 优先实现，事件源不可用时记录资料缺口 |

策略取舍规则：

- 重名或相似策略保留为不同 `variant_id`，不强行合并。例如海龟突破保留 20 日版与 60 日版，高窄旗形保留两个来源版本。
- 自然语言黑盒选股不进入本地策略集合；只可作为用户显式配置的外部 provider 信号，且不得反推出本地阈值或权重。
- 策略的具体阈值必须回指 `docs/evidence/a-share-selection-algo-equivalence-2026-05-26.md` 中的来源文件/函数/行号。

#### 5.0.2 v1 透明排序权重

参考项目提供策略条件和局部排序键，但没有统一跨策略权重。2026-05-28 人类确认：第一版使用透明合成排序，并将下列权重写入 approved strategy config。

排序边界：

- `myhhub/stock` 与 `Sequoia-X` 的原始语义是 per-strategy 命中列表，不是统一 20 只总榜。
- `claw-trade` 必须先保存每个策略的 raw hits、命中字段、阈值证据和来源口径，再做合并去重与 v1 合成评分。
- `stable_top_n(scores, n=20)` 表示最多取 20 只给 selection workers 评审；真实候选不足 20 时不得补齐，真实候选为 0 时不得生成 fake approved candidate pack。
- PM 最终进入 `/report` 的数量仍是 0..3；这和 candidate pack 的最多 20 只不是同一层。

基础分满分 100：

| 项 | 权重 | 说明 |
|---|---:|---|
| 策略命中覆盖 | 30 | 命中策略数量、来源多样性、是否跨趋势/形态/量能/事件策略 |
| 策略内强度 | 25 | 关键阈值超过幅度，例如 RPS、突破幅度、放量倍数、均线结构 |
| RPS/趋势强度 | 20 | RPS60/RPS120、均线排列、20/60/120 日收益与趋势连续性 |
| 流动性/可交易性 | 15 | 成交额、换手率、量能连续性、非一字板可交易性 |
| 行业/主题相对强弱 | 5 | 行业强弱、候选行业集中度、主题共振 |
| 证据完整度 | 5 | 字段覆盖、来源一致性、窗口完整性 |

扣分项：

| 项 | 最高扣分 | 说明 |
|---|---:|---|
| 风险扣分 | 20 | ST/退市/停牌/上市过短/涨幅过热/距离均线过远/异常成交不可持续 |
| 数据缺口扣分 | 15 | 策略字段缺失、事件资料缺失、provider 只返回部分覆盖 |

实现要求：

- `score_candidates(...)` 不得再使用“当天涨幅 + 成交额”作为完整选股公式。
- 每只候选必须保留分项得分、命中策略、扣分项和排序 tie-break 字段。
- candidate pack 模型可见正文可以展示分项事实和读者化字段说明，但不得写 Python 生成的自然语言入选观点。
- manifest/evidence 必须记录 strategy config 版本、权重版本和每个策略变体的来源。

### 5.1 `schedule_selection_job(...)`

- signature: `schedule_selection_job(*, market: Market, trade_date: str | None, trigger_source: str, profile: str = "CN_A") -> SelectionRunPlan`
- 输入：market/profile、trade_date、trigger source。
- 输出：planned `SelectionRunPlan`。
- side effects：写 run plan、latest/backfill/rerun lineage。
- failure modes：非 CN_A -> `market_strategy_unapproved`；无交易日历 -> `trading_calendar_unavailable`；已有 active job -> return/raise `already_running`。

```python
def schedule_selection_job(...):
    require_cn_a(market, profile)
    resolved_trade_date = resolve_closed_trade_date(trade_date)
    assert_trigger_source(trigger_source)
    if store.has_active_job(market, profile, resolved_trade_date):
        raise SelectionJobError("already_running")
    config_ref = strategy_config_store.current_approved_ref(market, profile)
    if not config_ref:
        raise SelectionJobError("strategy_config_unapproved")
    plan = build_selection_run_plan(..., approved_strategy_config_ref=config_ref)
    store.write_selection_run_plan(plan)
    return plan
```

### 5.2 `acquire_selection_job_lease(...)`

- signature: `acquire_selection_job_lease(*, market: Market, trade_date: str, profile: str, owner_id: str, ttl_seconds: int) -> SelectionJobLease`
- 输入：market/date/profile/owner/ttl。
- 输出：lease object。
- side effects：atomic store upsert/update。
- failure modes：already running、stale lease takeover write failed。

```python
def acquire_selection_job_lease(...):
    lease = store.get_active_lease(market, trade_date, profile)
    if lease and not lease.expired:
        return SelectionJobLease(status="already_running", ...)
    return store.acquire_or_takeover_lease(..., lineage_note="lease_takeover" if lease else None)
```

### 5.3 `build_selection_run_plan(...)`

- signature: `build_selection_run_plan(*, market: Market, profile: str, trade_date: str, trigger_source: str, approved_strategy_config_ref: str, supersedes_run_id: str | None = None) -> SelectionRunPlan`
- 输入：market/profile/date/config refs。
- 输出：selection batch plan。
- side effects：无或写 store 由 caller 负责。
- failure modes：missing approved config、non-CN_A、unapproved trigger source。

```python
def build_selection_run_plan(...):
    require_cn_a(market, profile)
    strategy_config = strategy_config_store.load_approved(approved_strategy_config_ref)
    if strategy_config is None:
        raise SelectionJobError("strategy_config_unapproved")
    provider_batch_plan_ref = data_gateway_planner.build_selection_batch_plan(...)
    return SelectionRunPlan(..., provider_batch_plan_ref=provider_batch_plan_ref)
```

### 5.4 `fetch_selection_market_data(...)`

- signature: `fetch_selection_market_data(plan: SelectionRunPlan) -> SelectionMarketDataRefs`
- 输入：selection batch plan。
- 输出：universe, market/fundamental/snapshot normalized refs and gaps。
- side effects：调用 data_gateway approved provider plugins/adapters；写 Mongo attempts/raw/normalized/cache/gaps。
- failure modes：provider plan missing、evidence write failed、credential/rate/license gaps、data authenticity failure。

```python
def fetch_selection_market_data(plan):
    batch_plan = selection_provider_plan_store.load(plan.provider_batch_plan_ref)
    assert batch_plan.scope == "selection_batch"
    refs = data_gateway.fetch_batch(batch_plan)
    if refs.evidence_write_failed:
        raise SelectionJobError("provider_evidence_failed")
    return refs
```

### 5.5 `normalize_selection_inputs(...)`

- signature: `normalize_selection_inputs(plan: SelectionRunPlan, refs: SelectionMarketDataRefs) -> SelectionNormalizedInputs`
- 输入：provider refs。
- 输出：normalized inputs for feature builder。
- side effects：写 normalized index/quality refs。
- failure modes：schema invalid、field missing、unit/currency ambiguity。

```python
def normalize_selection_inputs(plan, refs):
    normalized = normalizer.load_and_validate(refs.normalized_refs)
    quality = build_data_quality_report(normalized, refs.data_gaps)
    if quality.core_fields_insufficient:
        raise SelectionJobError("selection_inputs_insufficient")
    return SelectionNormalizedInputs(..., data_quality_report=quality)
```

### 5.6 `build_feature_snapshot(...)`

- signature: `build_feature_snapshot(plan: SelectionRunPlan, inputs: SelectionNormalizedInputs) -> FeatureSnapshotRef`
- 输入：normalized OHLCV/snapshot/fundamental/industry refs。
- 输出：feature snapshot ref。
- side effects：写 `feature_snapshot` evidence。
- failure modes：missing OHLCV window、unit mismatch、insufficient rows。

```python
def build_feature_snapshot(plan, inputs):
    rows = load_normalized_ohlcv(inputs)
    features = compute_features(rows, config=load_approved_strategy_config(plan.approved_strategy_config_ref))
    validate_features_are_numeric_or_gap(features)
    return feature_store.write(plan.selection_run_id, features)
```

### 5.7 `run_hard_filters(...)`

- signature: `run_hard_filters(plan: SelectionRunPlan, feature_ref: FeatureSnapshotRef) -> FilteredUniverseRef`
- 输入：feature snapshot、approved strategy config。
- 输出：filter decisions and remaining universe ref。
- side effects：写 filter audit。
- failure modes：approved config missing、threshold missing、core field missing。

```python
def run_hard_filters(plan, feature_ref):
    config = require_approved_strategy_config(plan.approved_strategy_config_ref)
    if not config.hard_filters_approved:
        raise SelectionJobError("strategy_config_unapproved")
    features = feature_store.read(feature_ref)
    decisions = apply_configured_hard_filters(features, config.hard_filters)
    assert_no_python_natural_language_reasons(decisions)
    return filter_store.write(plan.selection_run_id, decisions)
```

### 5.8 `score_candidates(...)`

- signature: `score_candidates(plan: SelectionRunPlan, filtered_ref: FilteredUniverseRef) -> CandidateScoresRef`
- 输入：filtered universe、approved strategy config。
- 输出：candidate scores、strategy hits、最多 20 只候选。
- side effects：写 score audit。
- failure modes：weights missing/unapproved、strategy set missing/unapproved、strategy source mapping missing、no valid candidate can be produced due insufficient data/evidence.

```python
def score_candidates(plan, filtered_ref):
    config = require_approved_strategy_config(plan.approved_strategy_config_ref)
    if not config.scoring_approved:
        raise SelectionJobError("strategy_config_unapproved")
    filtered = filter_store.read(filtered_ref)
    hits = run_configured_strategy_hits(filtered, config.strategy_set)
    scores = compute_configured_scores(filtered, hits, config.transparent_v1_weights)
    top = stable_top_n(scores, n=20)
    return score_store.write(plan.selection_run_id, hits=hits, scores=scores, top=top)
```

### 5.9 `build_candidate_pack(...)`

- signature: `build_candidate_pack(plan: SelectionRunPlan, scores_ref: CandidateScoresRef) -> CandidatePackDraft`
- 输入：最多 20 只候选、features、hits、data quality、source summaries。
- 输出：draft pack body + manifest。
- side effects：写 local draft artifact。
- failure modes：top > 20、forbidden language detected in Python-written body、missing lineage。

```python
def build_candidate_pack(plan, scores_ref):
    data = score_store.read(scores_ref)
    if len(data.top_candidates) > 20:
        raise SelectionJobError("candidate_pack_top_limit_exceeded")
    body = render_fact_table_only(data)
    validate_no_python_opinion_language(body)
    manifest = build_candidate_pack_manifest(plan, data)
    return CandidatePackDraft(body_md=body, body_json=data.model_visible_json, manifest=manifest)
```

### 5.10 `approve_candidate_pack(...)`

- signature: `approve_candidate_pack(plan: SelectionRunPlan, draft: CandidatePackDraft) -> CandidatePackRef`
- 输入：draft pack。
- 输出：approved candidate pack ref。
- side effects：write OpenViking approved material; write manifest/hash/readback refs。
- failure modes：readback mismatch、manifest incomplete、lineage missing。

```python
def approve_candidate_pack(plan, draft):
    validate_candidate_pack_contract(draft)
    receipt = openviking.write_material(target=selection_pack_target(plan), content=draft.body_md)
    readback = openviking.read_for_receipt_verification(receipt)
    if sha256(readback) != sha256(draft.body_md):
        raise SelectionJobError("candidate_pack_integrity_failed")
    return store.write_candidate_pack_ref(plan.selection_run_id, receipt, draft.manifest)
```

### 5.11 `mark_selection_run_completed(...)`

- signature: `mark_selection_run_completed(selection_run_id: str, candidate_pack_ref: CandidatePackRef) -> SelectionDataRun`
- 输入：run id + approved pack ref。
- 输出：completed data run。
- side effects：status update, latest pointer update if freshness rules allow。
- failure modes：candidate pack not approved, immutable run overwrite attempt。

```python
def mark_selection_run_completed(run_id, candidate_pack_ref):
    assert candidate_pack_ref.approved_at
    run = store.load_data_run(run_id)
    if run.status in TERMINAL_STATUSES:
        raise SelectionJobError("immutable_run_update_forbidden")
    completed = store.transition_to_completed(run_id, candidate_pack_ref)
    maybe_update_latest_terminal_pointer(completed)
    return completed
```

### 5.12 `mark_selection_run_failed(...)`

- signature: `mark_selection_run_failed(selection_run_id: str, failure_code: str, failure_reason: str, evidence_refs: tuple[str, ...] = ()) -> SelectionDataRun`
- 输入：run id, code, reason, evidence refs。
- 输出：failed data run。
- side effects：status update; release lease; no latest pointer update。
- failure modes：immutable terminal update attempt。

```python
def mark_selection_run_failed(run_id, code, reason, evidence_refs=()):
    run = store.load_data_run(run_id)
    if run.status in TERMINAL_STATUSES:
        raise SelectionJobError("immutable_run_update_forbidden")
    return store.transition_to_failed(run_id, code, reason, evidence_refs)
```

## 6. Candidate Pack Contract

### 6.1 Python 可以写的字段

Python 确定性层只可以写可复算事实和读者化来源摘要：

- `rank`、`ticker`、`company_name`、`industry`。
- 总分、分项得分、排序 tie-break 字段、策略配置版本、权重版本。
- 价格、成交额、换手率、收益率、均线、RPS、波动、回撤、数据窗口。
- 策略命中 id、策略来源项目、策略变体、命中字段、命中布尔值或数值、实际指标值。
- v1 透明排序分项：策略命中覆盖、策略内强度、RPS/趋势强度、流动性/可交易性、行业/主题相对强弱、证据完整度、风险扣分、数据缺口扣分。
- 风险 flag，例如 ST/停牌/退市风险/上市时间不足/数据缺口。
- data quality、readiness、field_missing、schema_invalid、rate_limited 等缺口说明。
- 读者化来源名称、日期、资料域。
- 审计可见 provider plan / attempt / normalized / feature / manifest / hash / lineage refs。

### 6.2 Python 绝对不能写的字段

- 自然语言入选理由。
- “看好、值得买、强烈推荐、配置、建仓、卖出、止损、目标价”等投资判断。
- worker 的最终结论、组合建议、交易动作。
- 任何买入/持有/卖出或等价交易建议。
- provider raw JSON、debug envelope、HTTP headers、token、Mongo collection raw object。
- OpenViking URI/hash/manifest/lineage/receipt 正文进入模型可见材料。

### 6.3 worker 可见正文结构

`candidate_pack.md` 的模型可见正文建议结构：

```text
# A股候选事实包

## 本轮范围
- 市场：CN_A
- 交易日：YYYY-MM-DD
- 候选数量：N（最多 20）
- 数据时效：...
- 策略配置版本：strategy_config_version
- 权重版本：weight_version

## 候选事实表
| 排名 | 股票代码 | 股票名称 | 行业 | 总分 | 分项得分 | 策略来源 | 策略变体 | 命中字段 | 实际指标值 | 风险扣分 | 数据缺口扣分 | 排序 tie-break 字段 | 数据质量 | 读者化来源摘要 |

## 策略命中明细
按来源项目和策略变体列出命中原因、阈值、实际数值、窗口日期。

## 排序与扣分说明
只解释 v1 透明权重、权重版本、策略配置版本、风险扣分、数据缺口扣分和 tie-break 事实，不写入选观点。

## 字段说明
只解释字段口径、单位、日期窗口。

## 数据质量与缺口
说明缺哪些字段、哪些来源失败、哪些指标不可用。
```

### 6.4 审计可见 manifest/hash/lineage 结构

审计 manifest 存在于 OpenViking/evidence，不进入模型可见正文：

```json
{
  "schema_version": "selection_candidate_pack.v1",
  "selection_run_id": "...",
  "market": "CN_A",
  "trade_date": "YYYY-MM-DD",
  "candidate_count": 20,
  "strategy_config_ref": "...",
  "strategy_config_version": "cn_a.selection_strategy.v1",
  "weight_version": "cn_a.selection_weights.v1",
  "provider_batch_plan_ref": "...",
  "feature_snapshot_ref": "...",
  "candidate_scores_ref": "...",
  "stable_top20_rule": {
    "primary": "total_score_desc",
    "tie_break": ["data_gap_penalty_asc", "risk_penalty_asc", "liquidity_score_desc", "ticker_asc"]
  },
  "source_lineage_refs": ["..."],
  "pack_body_sha256": "...",
  "pack_json_sha256": "...",
  "readback_status": "verified"
}
```

`strategy_config_version` 与 `weight_version` 必须进入 manifest/evidence，并能追溯到本轮 `approved_strategy_config_ref`。worker 可见正文只展示版本号和可读字段说明；OpenViking URI/hash/manifest/ref 机器字段仍只保留在审计证据中。

### 6.5 readback 校验

- OpenViking write 后必须 stat/readback。
- readback body sha256 必须等于 manifest `pack_body_sha256`。
- readback size 必须等于 receipt size。
- readback 失败或 hash mismatch 时状态为 `candidate_pack_integrity_failed`，不得启动 worker。

### 6.6 TTL/freshness 规则

- 第一版 CN_A 只接受最近一个已收盘交易日的 completed run。
- 非交易日可继续使用最近交易日 completed run。
- 跨过下一个交易日收盘数据窗口后，旧 run stale。
- 用户显式指定历史 `trade_date` 时可读取该日期 completed run，但 reader-facing 输出必须标明历史日期。
- stale 不触发现场拉数；`/select` 只尝试触发后台 refresh/job，并返回“补数已启动/已有补数在跑/补数通道未配置”。

### 6.7 approved-only 读取规则

`claw_get_selection_candidate_pack` 只读取 approved pack；`/select` controller 只有在进入 worker 前才读取并绑定：

- status=`completed` 的 data run。
- approved candidate pack。
- readback/hash/manifest/lineage 全部通过的 pack。
- market/profile 与 request 匹配的 pack。

其它状态不得进入 worker。no-run/no_candidate/stale/warehouse 证据不足由 `/select` 触发后台 refresh/job；hash/readback/manifest/lineage 损坏仍 fail closed。

### 6.8 raw/debug/provider envelope 禁止进入 prompt

模型可见 prompt 和 tool result 不得包含：

- raw/debug/provider/cache/attempt JSON。
- Mongo collection 名、OpenViking URI、hash、manifest、lineage、receipt。
- OpenClaw/OpenViking/legacy OpenBB runtime wrapper prose。
- provider secrets、headers、tokens。

## 7. `claw_get_selection_candidate_pack` 工具详细设计

### 7.1 tool intent

- intent 名：`selection_candidate_pack`（建议名）。
- canonical provider-visible tool name：`claw_get_selection_candidate_pack`。
- 作用：让 `selection_strategist` 和 `selection_skeptic` 在当前 OpenClaw turn 内读取本轮 approved top20 candidate pack。

### 7.2 `STAGES.yaml` 配置

`agents/selection_strategist/STAGES.yaml`（新增建议）：

```yaml
worker: selection_strategist
runtime: openclaw
stage: selection_review
profiles:
  CN_A:
    approved: true
    prompt: prompts/CN_A.md
    tools:
      - selection_candidate_pack
    openviking_access: none
tool_policy:
  owner: stage_profile
  python_calls_tools: false
```

`selection_skeptic` 同样使用 `selection_candidate_pack`。  
`selection_manager` 与 `selection_portfolio_manager`：

```yaml
tools: []
openviking_access: none
```

### 7.3 `tool_names.py` 映射

在现有 `src/claw_trade/config/tool_names.py` 中新增映射（建议）：

```python
"selection_candidate_pack": ("claw_get_selection_candidate_pack",)
```

### 7.4 `allowed_tools` 传入规则

| worker | `allowed_tools` |
|---|---|
| `selection_strategist` | `("claw_get_selection_candidate_pack",)` |
| `selection_skeptic` | `("claw_get_selection_candidate_pack",)` |
| `selection_manager` | `()` |
| `selection_portfolio_manager` | `()` |

任何其它工具出现在 OpenClaw provider payload 中均为 `tool_schema_violation`。

### 7.5 runtime context

工具不接受业务参数。它只从 `ctx.singleWorkerCommand` 读取：

- `run_id` / `select_workflow_run_id`。
- `selection_run_id`。
- `candidate_pack_material_id` 或受控 ref。
- `worker_id`。
- `stage`。
- `system_context_policy`（固定 `single_worker_minimal`）。
- `profile` / `market` / `trade_date`。
- `evidence_dir`。

缺任一必要上下文返回明确错误，不补默认值。

### 7.6 approved-only 读取流程

```python
def claw_get_selection_candidate_pack(ctx):
    command = require_single_worker_command(ctx)
    require_worker_in({"selection_strategist", "selection_skeptic"})
    require_stage("selection_review")
    run = selection_store.load_workflow_run(command.select_workflow_run_id)
    pack_ref = run.candidate_pack_ref
    validate_pack_ref_matches_context(pack_ref, command)
    validate_pack_not_stale(pack_ref)
    body = openviking.readback(pack_ref.l1_uri)
    validate_sha256(body, pack_ref.content_sha256)
    validate_no_forbidden_prompt_material(body)
    return body
```

### 7.7 错误码

| 错误码 | 含义 | 行为 |
|---|---|---|
| `selection_runtime_context_missing` | OpenClaw context 缺失 | tool hard fail |
| `selection_worker_not_allowed` | 非 strategist/skeptic 调用 | tool hard fail |
| `selection_run_missing` | workflow/data run 不存在 | tool hard fail，不拉数 |
| `candidate_pack_not_approved` | pack 未 approved | tool hard fail |
| `candidate_pack_stale` | pack 过期 | tool hard fail |
| `candidate_pack_integrity_failed` | readback/hash/manifest/lineage 不一致 | tool hard fail |
| `candidate_pack_lineage_incomplete` | provider/feature lineage 缺失 | tool hard fail |
| `candidate_pack_forbidden_material` | body 含 raw/debug/protocol | tool hard fail |

### 7.8 明确禁止

- 不调用 data_gateway/provider。
- 不查 raw。
- 不读 Mongo raw/debug。
- 不重新打分。
- 不扩大 top 20。
- 不提供 fallback provider。
- 不生成自然语言入选理由。

### 7.9 provider payload 验收方式

验收只看真实 OpenClaw provider payload：

```text
selection_strategist tools == ["claw_get_selection_candidate_pack"]
selection_skeptic tools == ["claw_get_selection_candidate_pack"]
selection_manager tools == []
selection_portfolio_manager tools == []
all dispatches system_context_policy == "single_worker_minimal"
```

同时检查 model-visible messages：

- strategist/skeptic 首轮 prompt 不预注入完整 candidate pack；候选材料来自工具调用结果。
- manager/PM prompt 只含上游 approved L1 和受控 `candidate_pack_summary`。
- 不含 raw/debug/provider envelope、Mongo/OpenViking 协议、refs/hash/manifest/lineage 文本。

## 8. Worker Dispatch 详细设计

### 8.1 `selection_strategist`

- worker id：`selection_strategist`
- stage：`selection_review`
- allowed_tools：`("claw_get_selection_candidate_pack",)`
- prompt variables：`market`、`profile`、`trade_date`、`selection_run_id`、`select_workflow_run_id`
- model-visible materials：运行上下文；工具调用后的 candidate pack
- forbidden materials：上游 L1、raw/debug/provider envelope、refs/hash/manifest/lineage
- output artifact：`selection_strategy_review.md`
- approval gate：L1/readback/hash/provider payload/tool calls 全部通过；输出不得新增 candidate pack 之外股票
- downstream handoff：approved L1 给 skeptic、manager、PM
- provider payload 必验项：工具严格等于 candidate pack tool；首轮 prompt 未包含完整 pack 正文

### 8.2 `selection_skeptic`

- worker id：`selection_skeptic`
- stage：`selection_review`
- allowed_tools：`("claw_get_selection_candidate_pack",)`
- prompt variables：`market`、`profile`、`trade_date`、`selection_run_id`、`select_workflow_run_id`
- model-visible materials：candidate pack 工具结果、approved strategist L1
- forbidden materials：raw/debug/provider envelope、未批准 strategist output、manifest/hash/lineage
- output artifact：`selection_skeptic_review.md`
- approval gate：L1/readback/hash/provider payload/tool calls 通过；不得把缺失数据当负面事实
- downstream handoff：approved L1 给 manager、PM
- provider payload 必验项：工具严格等于 candidate pack tool；上游材料是 approved strategist L1 正文

### 8.3 `selection_manager`

- worker id：`selection_manager`
- stage：`selection_decision`
- allowed_tools：`()`
- prompt variables：`market`、`profile`、`trade_date`、`select_workflow_run_id`
- model-visible materials：approved strategy review、approved skeptic review、受控 `candidate_pack_summary`
- forbidden materials：工具 schema、raw/debug/provider envelope、Mongo/OpenViking 协议、refs/hash/manifest/lineage
- output artifact：`selection_ranked_watchlist.md`
- approval gate：不得新增候选；结论只可表达优先进入组合评审/观察/放弃
- downstream handoff：approved ranked watchlist 给 PM
- provider payload 必验项：`tools=[]`；prompt 不含协议/refs/hash

### 8.4 `selection_portfolio_manager`

- worker id：`selection_portfolio_manager`
- stage：`selection_portfolio_decision`
- allowed_tools：`()`
- prompt variables：`market`、`profile`、`trade_date`、`select_workflow_run_id`
- model-visible materials：approved ranked watchlist、strategy review、skeptic review、受控 `candidate_pack_summary`
- forbidden materials：工具 schema、raw/debug/provider envelope、refs/hash/manifest/lineage
- output artifact：`selection_portfolio_decision.md`
- approval gate：最终结论只能是进入 `/report`、观察、放弃；进入 `/report` 0-3 只且来自 candidate pack
- downstream handoff：reader-facing `/select` result 和用户确认 allowed ticker set
- provider payload 必验项：`tools=[]`；prompt 不含 raw/debug/protocol；输出 artifact 可机械解析为三分类且 ticker 边界有效

## 9. `/select` 命令流程伪码

```python
def handle_select_command(raw_text, request_id, user_id):
    request = parse_select_command(raw_text, request_id=request_id, user_id=user_id)
    if request.market != "CN_A" or request.profile != "CN_A":
        return unavailable("market_strategy_unapproved")
    if request.system_context_policy != "single_worker_minimal":
        return unavailable("selection_system_context_policy_invalid")

    latest = selection_store.load_latest_terminal_run(
        market=request.market,
        profile=request.profile,
        trade_date=request.trade_date,
    )
    if latest is None:
        return trigger_selection_data_refresh_or_unavailable(request, reason="no_completed_selection_run")
    if latest.status == "no_candidate":
        return trigger_selection_data_refresh_or_unavailable(request, reason="no_candidate_selection_run")

    if is_stale(latest, now=clock.now()):
        return trigger_selection_data_refresh_or_unavailable(request, reason="stale_selection_run")

    pack_ref = latest.candidate_pack_ref
    if pack_ref is None:
        return trigger_selection_data_refresh_or_unavailable(request, reason="candidate_pack_not_approved")

    pack_check = validate_candidate_pack_for_select(pack_ref, request)
    if pack_check.code == "candidate_pack_not_approved":
        return trigger_selection_data_refresh_or_unavailable(request, reason="candidate_pack_not_approved")
    if pack_check.code == "candidate_pack_integrity_failed":
        return unavailable("candidate_pack_integrity_failed")
    if pack_check.code == "candidate_pack_lineage_incomplete":
        return unavailable("candidate_pack_lineage_incomplete")
    if not pack_check.ok:
        return unavailable(pack_check.code)

    workflow = selection_store.create_workflow_run(
        request=request,
        selection_run_id=latest.selection_run_id,
        candidate_pack_ref=pack_ref,
        status="select_run_created",
    )

    strategist = build_selection_dispatch(
        workflow=workflow,
        worker_id="selection_strategist",
        stage="selection_review",
        allowed_tools=("claw_get_selection_candidate_pack",),
        model_visible_materials=(),
        system_context_policy="single_worker_minimal",
    )
    assert_allowed_tools_from_stage_policy(strategist)
    strategist_result = run_openclaw_single_worker(strategist)
    if not strategist_result.ok:
        return fail_workflow(workflow, "worker_runtime_failed", strategist_result.reason)
    validate_provider_payload(
        strategist_result,
        expected_tools=("claw_get_selection_candidate_pack",),
        expected_system_context_policy="single_worker_minimal",
    )
    strategy_l1 = approve_selection_worker_l1(strategist, strategist_result)
    if not strategy_l1.ok:
        return fail_workflow(workflow, "artifact_approval_failed", strategy_l1.reason)

    skeptic = build_selection_dispatch(
        workflow=workflow,
        worker_id="selection_skeptic",
        stage="selection_review",
        allowed_tools=("claw_get_selection_candidate_pack",),
        model_visible_materials=(strategy_l1.approved_text,),
        system_context_policy="single_worker_minimal",
    )
    assert_allowed_tools_from_stage_policy(skeptic)
    skeptic_result = run_openclaw_single_worker(skeptic)
    if not skeptic_result.ok:
        return fail_workflow(workflow, "worker_runtime_failed", skeptic_result.reason)
    validate_provider_payload(
        skeptic_result,
        expected_tools=("claw_get_selection_candidate_pack",),
        expected_system_context_policy="single_worker_minimal",
    )
    skeptic_l1 = approve_selection_worker_l1(skeptic, skeptic_result)
    if not skeptic_l1.ok:
        return fail_workflow(workflow, "artifact_approval_failed", skeptic_l1.reason)

    summary = load_model_visible_candidate_pack_summary(pack_ref)
    manager = build_selection_dispatch(
        workflow=workflow,
        worker_id="selection_manager",
        stage="selection_decision",
        allowed_tools=(),
        model_visible_materials=(strategy_l1.approved_text, skeptic_l1.approved_text, summary.summary_md),
        system_context_policy="single_worker_minimal",
    )
    assert_allowed_tools_from_stage_policy(manager)
    manager_result = run_openclaw_single_worker(manager)
    if not manager_result.ok:
        return fail_workflow(workflow, "worker_runtime_failed", manager_result.reason)
    validate_provider_payload(
        manager_result,
        expected_tools=(),
        expected_system_context_policy="single_worker_minimal",
        forbidden_material_patterns=PROTOCOL_PATTERNS,
    )
    manager_l1 = approve_selection_worker_l1(manager, manager_result)
    if not manager_l1.ok:
        return fail_workflow(workflow, "artifact_approval_failed", manager_l1.reason)

    pm = build_selection_dispatch(
        workflow=workflow,
        worker_id="selection_portfolio_manager",
        stage="selection_portfolio_decision",
        allowed_tools=(),
        model_visible_materials=(
            manager_l1.approved_text,
            strategy_l1.approved_text,
            skeptic_l1.approved_text,
            summary.summary_md,
        ),
        system_context_policy="single_worker_minimal",
    )
    assert_allowed_tools_from_stage_policy(pm)
    pm_result = run_openclaw_single_worker(pm)
    if not pm_result.ok:
        return fail_workflow(workflow, "worker_runtime_failed", pm_result.reason)
    validate_provider_payload(
        pm_result,
        expected_tools=(),
        expected_system_context_policy="single_worker_minimal",
        forbidden_material_patterns=PROTOCOL_PATTERNS,
    )
    pm_l1 = approve_selection_worker_l1(pm, pm_result)
    if not pm_l1.ok:
        return fail_workflow(workflow, "artifact_approval_failed", pm_l1.reason)

    decision = parse_and_validate_selection_decision(
        pm_l1.approved_text,
        allowed_tickers=summary.ticker_set,
        parser_mode="mechanical_only",
        forbid_python_inference=True,
        reject_ambiguous=True,
    )
    if not decision.ok:
        return fail_workflow(workflow, "selection_result_invalid", decision.reason)

    selection_store.mark_workflow_completed(workflow.id, decision.value)
    reader_result = render_selection_reader_result(decision.value)
    selection_store.mark_waiting_report_confirmation(workflow.id)
    return reader_result
```

### 9.1 `parse_and_validate_selection_decision(...)` 边界

- 只允许“机械解析” PM 明确输出结构，不允许 Python 推断、补全或重写 PM 结论。
- 若缺字段（如 `enter_report/watch/reject` 任一缺失）、ticker 不在 allowed set、同一 ticker 重复落入多个桶、语义歧义，必须 `selection_result_invalid`。
- 出现“建议买入/卖出/持有”“投资建议：买入”“最终交易建议：…”“目标价：…”“止损价：…”“仓位：…”“交易计划：…”等表达时，不因词面或模板触发 `/select` runtime 失败。
- `/select` 只做机械边界校验：三分类结构、ticker 在 allowed set、ticker 不重复、歧义行人工复核；本 gate 不做表达类禁词扫描。

失败分支共同规则：

- `no_completed_selection_run`、`no_candidate_selection_run`、`stale_selection_run`、`candidate_pack_not_approved`、warehouse 证据不足不启动任何 worker，只触发后台 refresh/job 并返回 started/running/unconfigured。
- `candidate_pack_integrity_failed`、`candidate_pack_lineage_incomplete` 不启动任何 worker；hash/readback/lineage 损坏仍 fail closed，不用补数隐藏问题。
- tool/payload/material violation 是硬失败，不继续下游 worker。
- worker artifact approval failed 不进入下游 prompt。
- completed 后只等待确认，不自动 report。

## 10. 用户确认与 `/report` handoff

### 10.1 confirmation id 与幂等键

- `confirmation_id`：由 selection confirmation controller 生成或接受 UI 提交的稳定 id。
- `idempotency_key`：`select_workflow_run_id + ":" + ticker + ":" + confirmation_id`。
- `report_handoff_dedupe_key`：`select_workflow_run_id + ":" + ticker`，作为 report handoff 唯一约束。
- 若同 key 已成功，返回同一 `report_task_id` / `report_run_id`，不得再创建。
- 若同 key 曾失败，可按同 key 重试 handoff，但不得绕过 ticker allowed set。
- 即使 `confirmation_id` 不同，同一 `select_workflow_run_id + ticker` 也不得重复创建 report run。

### 10.2 allowed ticker set

allowed set 只来自 approved `selection_portfolio_decision` 的 `enter_report`：

- ticker 必须在 candidate pack top20 中。
- ticker 必须在 PM 进入 `/report` 列表中。
- 一次确认最多 1-3 只，且不能超过 PM 结论。
- 观察/放弃 ticker 不能 handoff。

### 10.3 repeated click behavior

| 场景 | 行为 |
|---|---|
| 同 confirmation id 同 ticker 重复点击 | 返回已有结果 |
| 同 workflow 同 ticker 不同 confirmation id | 命中 `report_handoff_dedupe_key`，返回 existing；不重复创建 |
| 确认非 allowed ticker | `ticker_not_allowed` |
| workflow 非 waiting/completed | `selection_not_confirmable` |
| decision 过期或 pack stale | `selection_decision_stale`，不启动 report |

### 10.3A 确认前再校验门（必须通过）

`confirm_selection_ticker` 在启动 `/report` 前必须重新校验：

- workflow 仍处于可确认状态（`waiting_report_confirmation` 或 `completed`）。
- PM decision 仍是 approved decision material。
- ticker 仍在 allowed set（`enter_report`）。
- candidate pack freshness 仍有效。
- OpenViking readback/hash/manifest/lineage 与 workflow 绑定关系仍一致。

任一失败都必须直接返回错误，不得 enqueue `/report`。

### 10.4 no auto report rule

`/select` workflow 到 `completed` 后只能：

- 保存 reader-facing result。
- 保存 confirmation allowed set。
- 进入 `waiting_report_confirmation`。

不得调用 `ReportTaskQueue.enqueue_report_task(...)`，直到用户确认。

### 10.5 existing `/report` workflow reuse

handoff 构造现有 report 请求：

```python
def build_report_handoff_request(confirmation, decision):
    ticker = confirmation.ticker
    return ReportHandoffRequest(
        confirmation_id=confirmation.confirmation_id,
        ticker=ticker,
        company_name=decision.company_name_for(ticker),
        market="CN_A",
        profile="CN_A",
        current_date=today_or_selection_date(),
        selection_context_ref=decision.approved_material_id,
        selection_stage_marker="selection_report_handoff",
        report_request=build_report_run_request(
            ticker=ticker,
            company_name=...,
            market="CN_A",
            entry_point=WorkflowEntryPoint.REPORT_COMMAND,
            settings=current_report_settings,
        ),
    )
```

handoff 不向 `/report` 注入 `/select` 最终判断、评级、目标价、交易建议。`/report` PM 权威保持不变。

通用 worker 聊天记录不属于 report handoff 材料。它只能作为聊天记录和诊断证据保存，不进入 `/report` 正式材料，也不能改写 approved `selection_portfolio_decision`。

### 10.6 failure behavior

- queue full：返回 report queue 用户错误，不改 selection decision。
- report model not ready：返回 `report_model_not_ready`。
- profile unapproved：理论上 CN_A 已批准；若失败，返回 `profile_strategy_unapproved`。
- enqueue success but run later failed：按现有 `/report` failure 处理，不回写 `/select` 为交易失败。

## 11. Provider/Data Gateway 详细设计

### 11.1 selection batch 如何复用 data_gateway

后台 selection batch 复用 data_gateway 的 provider plugin/adapter、attempt、raw/normalized/cache、readiness/data gap 语义。区别是 scope：

- `/report RunProviderPlan` 是单 ticker、单 report run。
- selection batch plan 是全市场/批量 scope，不能把 5000 只股票伪装成某个 ticker。

建议新增 `SelectionProviderBatchPlan` 或让现有 `RunProviderPlan` 增加明确 `scope="selection_batch"`；具体实现方式需编码时确认，但语义必须满足：

- market/profile/trade_date/lookback_days/universe_scope。
- coverage group：股票列表、日线历史、当日快照、基础估值、行业分类。
- provider candidate 顺序、attempt id、cache key、normalized ref、失败原因、data gap。
- batch freshness/TTL、completed/failed、lineage、rerun/backfill source。
- approved selection strategy config 引用（filter/strategy/weight）必须来自 approved-only 配置；缺失或非 approved 一律 fail closed。

### 11.2 Mongo/evidence store 保存什么

Mongo/evidence store 保存：

- provider attempts。
- raw payload refs/hash 或 metadata-only refs。
- normalized refs。
- cache receipts。
- data gaps/readiness。
- selection batch provider plan。
- feature snapshot refs 和 score audit refs。

Mongo 不保存 worker 主材料权威，不直接给 worker 读 raw/cache/debug。

### 11.3 OpenViking 保存什么

OpenViking 保存：

- approved candidate pack material。
- approved selection worker L1。
- manifest、hash、readback、lineage。
- final `/select` decision approved material。
- evidence relation cards/ref index。

OpenViking 不做外部 provider，不做 cache freshness 判断，不让 worker deep-read raw evidence。

### 11.4 provider batch plan 与 `/report RunProviderPlan` 的关系

关系是“复用 provider 计划和 evidence 语义”，不是“复用单标的请求形状”。

- 可复用 `ProviderAttempt`、`DataGap`、`Readiness`、cache/normalized/raw refs、source_role、coverage_group。
- 不可复用 `ticker` 必填的 report shape 代表全市场。
- 验收必须能从 candidate pack 字段追溯到 selection batch plan、provider attempts、normalized refs、feature snapshot。

### 11.5 provider attempts/cache/data gaps/readiness 记录

- 每个计划 provider 分支都必须有 attempt 或明确 skipped/admission receipt。
- `cache_hit` 不等于 fresh remote success。
- `cached_empty` 进入 data gap。
- `cache_stale` 不静默使用。
- `credential_missing`、`rate_limited`、`schema_invalid`、`field_missing` 必须进入 gaps/readiness。
- provider 失败后同组其它 provider 可继续尝试，但失败必须可见，这不是 hidden fallback。

### 11.6 东财与 fallback 边界

- 东财系接口只能作为经批准 adapter 的候选实测来源。
- 不作为默认稳定依赖。
- 不作为 data_gateway/provider 失败后的隐藏 fallback。
- 如果新增 baostock 或其它源，必须纳入 provider registry/evidence 链，不得绕过 data_gateway。

## 12. Artifact Authority

### 12.1 approved material authority

- candidate pack approved 后才可被 tool 读取。
- worker L1 approved 后才可进入下游 prompt。
- PM selection decision approved 后才可 reader-facing 展示和 confirmation。
- hard-gate-failed artifact 不进入 shared state、downstream prompt、reader export、report handoff。

### 12.2 manifest/hash/lineage

每个 approved artifact 必须有：

- run id、stage、worker/candidate_pack target。
- OpenViking material id / uri。
- content hash、size、readback status。
- lineage refs：provider plan、attempts、normalized refs、feature snapshot、upstream approved L1。
- approval status and timestamp。

### 12.3 raw vs normalized vs approved material

| 类型 | 存储 | 模型可见 | 用途 |
|---|---|---:|---|
| raw provider payload | Mongo/object evidence | 否 | provider 真实性审计 |
| normalized result | Mongo/evidence store | 否 | feature/pack builder 输入 |
| feature snapshot | selection evidence store | 否 | scoring/filter audit |
| candidate pack summary/body | OpenViking approved material | 是，受控 | strategist/skeptic tool result，manager/PM summary |
| worker L1 | OpenViking approved material | 是，下游 | worker reasoning handoff |
| provider payload | run evidence files | 否 | prompt/tool schema 验收 |

### 12.4 L1 artifact approval

selection worker L1 approval 需要：

- OpenClaw result succeeded。
- provider payload exists and matches worker/stage/run/call。
- visible tools match expected matrix。
- OpenViking receipt readback matches L1。
- material claims/run/stage/worker/call match dispatch。
- output ticker set is subset of candidate pack where applicable。

### 12.5 provider payload 保存位置

沿用 OpenClaw result evidence 形态：

```text
runs/<select_workflow_run_id>/calls/<dispatch_id>/provider-requests.jsonl
runs/<select_workflow_run_id>/calls/<dispatch_id>/visible-tools.json
runs/<select_workflow_run_id>/calls/<dispatch_id>/openclaw-result.json
```

具体文件名以 OpenClaw 当前 evidence 输出为准；验收读取真实 result paths，不自行重构。

### 12.6 worker output 保存位置

```text
runs/<select_workflow_run_id>/calls/<dispatch_id>/raw-output.md
runs/<select_workflow_run_id>/calls/<dispatch_id>/openviking-receipt.json
OpenViking: viking://resources/workflow/<select_workflow_run_id>/<selection_stage>/<worker>/<dispatch>/report.md
```

路径中 `selection_stage` 是建议语义；实现时必须避免与 `/report` `Stage` 混淆。

## 13. 测试设计

Guard source: `docs/A股选股总体设计.md §2.2/§3.8.5/§11.1`。  
该 guard 只约束 `/select` 研究资源分配输出，不约束 `/report` PM 投资表达，不是新增 report 投资表达 guard。

### 13.1 Unit tests

| 测试名 | fixture | 执行动作 | 断言 | 证明哪个总体设计要求 |
|---|---|---|---|---|
| `test_select_request_cn_a_only` | CN_A/HK/US/CRYPTO requests | build `SelectRequest` | CN_A ok；其它 `market_strategy_unapproved` | 第一版 CN_A only，非 CN_A fail closed |
| `test_select_request_system_context_policy_single_worker_minimal` | `/select` command parse fixture | parse request | `system_context_policy == single_worker_minimal` | 总体设计 §7.1 |
| `test_selection_latest_terminal_handles_no_candidate` | completed/no_candidate/running/failed runs | `load_latest_terminal_selection_run` | completed 可用；latest no_candidate 返回 unavailable/refresh reason | `/select` 不回退旧 run，后续只触发后台补数 |
| `test_selection_ttl_stales_after_next_close_window` | trade calendar fixture | `is_selection_run_stale` | next close 后 stale | TTL 交易日口径 |
| `test_candidate_pack_schema_rejects_forbidden_python_reason` | pack body with opinion text | `validate_candidate_pack_contract` | rejected | Python 不写自然语言入选理由 |
| `test_candidate_pack_top_limit` | 21 rows | `build_candidate_pack` | fails `top_limit_exceeded` | top 20 |
| `test_selection_engine_requires_approved_strategy_config` | missing config | `score_candidates` | `strategy_config_unapproved` | 缺少已批准策略配置时不得自作主张 |
| `test_selection_tool_registry_mapping` | tool registry | resolve `selection_candidate_pack` | `claw_get_selection_candidate_pack` | tool_names mapping |
| `test_selection_worker_tool_matrix` | STAGES.yaml fixtures | resolve tools | strategist/skeptic one tool；manager/PM empty | worker-visible tool matrix |
| `test_selection_decision_reader_semantics` | PM decision artifact | parse/render reader result | only enter_report/watch/reject enums | `/select` final conclusion only three categories |
| `test_parse_selection_decision_rejects_ambiguous_or_missing_fields` | PM text with missing bucket/重复 ticker/歧义字段 | parse decision | `selection_result_invalid` | 机械解析，不允许 Python 推断补全 |
| `test_parse_selection_decision_allows_trade_advice_templates_when_structure_is_valid` | PM text with 建议买入/投资建议/目标价/止损价/仓位/交易计划模板 | parse decision | parse ok | 表达类措辞不作为 `/select` runtime 失败条件 |
| `test_selection_decision_reader_message_only_contains_three_buckets` | parsed decision | render reader message | only enter_report/watch/reject sections | `/select` reader-facing 机械三分类边界 |

禁止测试：不得要求 prompt 中完全不出现“买入/持有/卖出”等词；prompt 可以用这些词说明边界。只测试 reader-facing `/select` artifact 语义。

### 13.2 Contract tests

| 测试名 | fixture | 执行动作 | 断言 | 证明哪个总体设计要求 |
|---|---|---|---|---|
| `test_select_command_is_not_report_command` | command parser | parse `/select` and `/report` | entry point distinct | 独立 workflow |
| `test_select_command_uses_single_worker_minimal` | command parser/runtime command builder | inspect command payload | `system_context_policy=single_worker_minimal` | 总体设计 §7.1 |
| `test_selection_stage_policy_profiles_cn_a_only` | `agents/selection_*/STAGES.yaml` | load profiles | CN_A approved；其它 missing/fail | 非 CN_A 不 fallback |
| `test_selection_pack_tool_has_no_business_params` | plugin registration | inspect JSON schema | `properties == {}` | 工具无业务参数 |
| `test_selection_tool_backend_import_block` | monkeypatch provider/raw modules | call tool with approved pack | provider/raw modules not imported | tool 不查 provider/raw |
| `test_selection_manager_pm_payload_has_no_tools` | provider payload fixture | scan tools | tools empty | manager/PM 无工具 |
| `test_selection_payload_rejects_protocol_materials` | provider payload with URI/hash/manifest | scan messages | rejected | prompt material boundary |
| `test_selection_report_handoff_stage_marker_contract` | handoff request fixture | inspect handoff DTO/evidence | `selection_stage_marker=selection_report_handoff` | 总体设计阶段覆盖要求 |
| `test_selection_no_runtime_guard_allowlist_change` | guard freeze test | run existing guard contract | no new guard file/category | 不新增 report runtime guard |

### 13.3 Integration tests

| 测试名 | fixture | 执行动作 | 断言 | 证明哪个总体设计要求 |
|---|---|---|---|---|
| `test_selection_data_job_generates_approved_candidate_pack` | small CN_A normalized fixture + approved config | run background job | completed run + approved pack + lineage | 后台确定性闭环 |
| `test_selection_scheduler_lease_backfill_rerun_and_latest_pointer_rules` | scheduled/backfill/rerun fixtures | run scheduler + store transitions | lease 互斥；backfill 不抢 latest；rerun 新 run id 并有 supersedes lineage | backfill/rerun/lease/latest pointer |
| `test_selection_completed_run_is_immutable` | completed run fixture | try overwrite completed run | `immutable_run_update_forbidden` | immutable completed run |
| `test_select_no_completed_run_triggers_refresh_without_fetch` | empty store + provider fetch spy + refresh gateway spy | `/select` | refresh started/running/unconfigured；provider spy not called | 不现场拉数，只触发后台补数 |
| `test_select_no_candidate_run_triggers_refresh_without_old_completed` | latest no-candidate + older completed + provider fetch spy + refresh gateway spy | `/select` | refresh started/running/unconfigured；provider spy not called | 0 候选不造 pack、不回退旧 run，触发后台补数 |
| `test_select_stale_run_triggers_refresh_without_fetch` | stale completed run + provider spy + refresh gateway spy | `/select` | refresh started/running/unconfigured；no worker；no provider | stale 触发后台补数但不现场拉数 |
| `test_select_non_cn_a_does_not_start_worker_or_fetch_or_prompt_fallback` | HK/US/CRYPTO `/select` request + spies | `/select` | `market_strategy_unapproved`；no worker；no provider fetch；no CN_A prompt fallback | 非 CN_A fail closed |
| `test_select_unapproved_pack_triggers_refresh_before_worker` | completed run without approved pack | `/select` | `candidate_pack_not_approved` + refresh started/running/unconfigured；no OpenClaw call | approved-only；不使用半成品 |
| `test_select_hash_mismatch_stops_before_worker` | pack readback mismatch | `/select` | `candidate_pack_integrity_failed` | hash/readback gate |
| `test_selection_confirmation_stale_or_integrity_mismatch_does_not_start_report` | waiting workflow + stale/hash mismatch pack | confirm | `selection_decision_stale`/integrity error；report queue unchanged | 确认再校验门 |
| `test_selection_full_workflow_dispatch_order` | approved pack + real OpenClaw focused run evidence（或纯状态机 fixture，且标注“非 runtime/live 验收”） | `/select` | 4 dispatches fixed order | LLM 不决定调度 |
| `test_selection_tool_reads_same_pack_for_strategist_and_skeptic` | approved pack | run both worker turns | same pack hash/readback | same approved candidate pack |
| `test_selection_handoff_requires_user_confirmation` | completed `/select` | inspect report queue | no report task before confirm | no auto report |
| `test_selection_confirmation_starts_existing_report` | decision enter_report ticker | confirm | report task entry_point report_command | report handoff reuse |

### 13.4 Provider payload tests

| 测试名 | fixture | 执行动作 | 断言 | 证明哪个总体设计要求 |
|---|---|---|---|---|
| `test_selection_strategist_provider_payload_tools` | fresh OpenClaw run | inspect payload | only `claw_get_selection_candidate_pack` | provider payload final evidence |
| `test_selection_skeptic_provider_payload_tools` | fresh OpenClaw run | inspect payload | only `claw_get_selection_candidate_pack` | tool matrix |
| `test_selection_downstream_provider_payload_no_tools` | manager/PM fresh payload | inspect tools | empty tools | manager/PM no tools |
| `test_selection_provider_payload_system_context_policy` | four worker payloads | inspect request envelope | all dispatches `system_context_policy=single_worker_minimal` | 总体设计 §7.1 |
| `test_selection_payload_candidate_pack_tool_result_boundary` | sequence=2 provider requests | inspect messages | tool result has pack body, no raw/debug/protocol | prompt boundary |
| `test_selection_payload_manager_pm_material_boundary` | manager/PM payload | inspect messages | approved L1 + summary only | downstream material boundary |

### 13.5 Failure path tests

| 测试名 | fixture | 执行动作 | 断言 | 证明哪个总体设计要求 |
|---|---|---|---|---|
| `test_selection_tool_missing_runtime_context_fails` | empty ctx | call tool | `selection_runtime_context_missing` | runtime context required |
| `test_selection_tool_worker_mismatch_fails` | manager ctx | call tool | `selection_worker_not_allowed` | only strategist/skeptic |
| `test_selection_lineage_incomplete_fails` | pack without feature/provider lineage | `/select` | `candidate_pack_lineage_incomplete` | lineage required |
| `test_selection_worker_failure_stops_downstream` | strategist fails | `/select` | skeptic not dispatched | failed L1 not downstream |
| `test_selection_artifact_approval_failed_stops_downstream` | invalid skeptic L1 | `/select` | manager not dispatched | approval boundary |

### 13.6 No-fallback tests

| 测试名 | fixture | 执行动作 | 断言 | 证明哪个总体设计要求 |
|---|---|---|---|---|
| `test_selection_data_job_provider_failure_records_gap_no_hidden_fallback` | first provider fails, second succeeds | run job | both attempts visible; no old path import | no hidden fallback |
| `test_selection_tool_never_calls_openbb` | approved pack + data_gateway/provider spy raising | call tool | tool succeeds from approved pack; data_gateway/provider spy not called | tool only reads approved pack |
| `test_select_unavailable_only_schedules_background_refresh` | no completed run | `/select` | refresh gateway called once；provider/table fetch not called；response is started/running/unconfigured | `/select` 不现场拉数，只触发后台补数 |

### 13.7 No-auto-report tests

| 测试名 | fixture | 执行动作 | 断言 | 证明哪个总体设计要求 |
|---|---|---|---|---|
| `test_select_completed_waits_for_confirmation` | successful selection workflow | complete `/select` | state `waiting_report_confirmation`; queue empty | no auto report |
| `test_select_reader_result_has_confirmation_allowed_set_only` | decision with enter_report/watch/reject | render | only enter_report tickers confirmable | only confirmed ticker starts report |

### 13.8 Confirmation idempotency tests

| 测试名 | fixture | 执行动作 | 断言 | 证明哪个总体设计要求 |
|---|---|---|---|---|
| `test_selection_confirmation_same_key_returns_existing_report_task` | existing confirmation | confirm same key | same task id, no enqueue | idempotency |
| `test_selection_confirmation_different_confirmation_id_same_ticker_dedupes_by_workflow_ticker` | same workflow+ticker, different confirmation_id | confirm twice | one report task/run；返回 existing | report handoff 去重唯一约束 |
| `test_selection_confirmation_rejects_watch_ticker` | watch ticker | confirm | `ticker_not_allowed` | only PM enter_report |
| `test_selection_confirmation_duplicate_click_does_not_duplicate_report_run` | double click | confirm twice | one report task | repeated click behavior |

### 13.9 Live acceptance

Live/fresh provider payload proof must follow AGENTS live runtime preflight. Required evidence:

- fixed runtime preflight table.
- fresh `/select` run id and selection data run id.
- four OpenClaw provider payloads.
- payload 证据中 `select_command` + `system_context_policy=single_worker_minimal`。
- candidate pack approved material readback/hash.
- strategist/skeptic real tool calls.
- manager/PM tools empty.
- final reader-facing `/select` artifact.
- no report task until explicit confirmation.
- confirmation 成功后的 handoff evidence 含 `selection_stage_marker=selection_report_handoff`。

## 14. 实施顺序

### 14.1 models/state/store

- 修改文件：`src/claw_trade/selection/models.py`、`src/claw_trade/selection/store.py`、可选扩展 `src/claw_trade/workflow/models.py`。
- 新增函数：`create_selection_data_run`、`load_latest_terminal_selection_run`、`create_selection_workflow_run`。
- 验收测试：`test_select_request_cn_a_only`、`test_selection_latest_terminal_handles_no_candidate`。
- 不允许顺手做的事：不接 OpenClaw、不写 provider adapter、不改 `/report` workflow。

### 14.2 candidate pack artifact contract

- 修改文件：`src/claw_trade/selection/candidate_pack.py`、`src/claw_trade/selection/artifacts.py`。
- 新增函数：`build_candidate_pack`、`validate_candidate_pack_contract`、`approve_candidate_pack`。
- 验收测试：`test_candidate_pack_schema_rejects_forbidden_python_reason`、`test_candidate_pack_top_limit`。
- 不允许顺手做的事：不写自然语言理由、不加 runtime guard 文件。

### 14.3 background deterministic job

- 修改文件：`src/claw_trade/selection/scheduler.py`、`data_job.py`、`features.py`、`engine.py`。
- 新增函数：§5 全部后台函数。
- 验收测试：`test_selection_data_job_generates_approved_candidate_pack`。
- 不允许顺手做的事：不使用未批准评分权重/阈值；缺配置 fail closed。

### 14.4 tool registry/tool backend

- 修改文件：`src/claw_trade/config/tool_names.py`、`src/claw_trade/selection/tools.py`、OpenClaw plugin wrapper。
- 新增函数：`get_selection_candidate_pack_from_context`、tool wrapper registration。
- 验收测试：`test_selection_tool_registry_mapping`、`test_selection_pack_tool_has_no_business_params`。
- 不允许顺手做的事：不提供 provider fallback、不接受 ticker/date/topN 参数。

### 14.5 worker STAGES/prompt vars

- 修改文件：`agents/selection_strategist/**`、`agents/selection_skeptic/**`、`agents/selection_manager/**`、`agents/selection_portfolio_manager/**`。
- 新增函数：无；新增 agent config。
- 验收测试：`test_selection_worker_tool_matrix`、`test_selection_stage_policy_profiles_cn_a_only`。
- 不允许顺手做的事：不创建 HK/US/CRYPTO prompts，不 fallback 到 CN_A。

### 14.6 OpenClaw dispatch wiring

- 修改文件：`src/claw_trade/selection/dispatch.py`、`src/claw_trade/selection/evidence.py`。
- 新增函数：`build_selection_dispatch`、`run_selection_worker_turn`、`validate_selection_provider_payload`。
- 验收测试：provider payload tests。
- 不允许顺手做的事：不修改 `third_party/openclaw`，不让 Python 调工具。

### 14.7 `/select` command controller

- 修改文件：`src/claw_trade/ui_backend/chat_controller.py` 或 command router、`src/claw_trade/selection/controller.py`。
- 新增函数：`handle_select_command`、`parse_select_command`。
- 验收测试：`test_select_no_completed_run_triggers_refresh_without_fetch`、`test_selection_full_workflow_dispatch_order`。
- 不允许顺手做的事：不做最终 UI 视觉卡片，不直接调 provider/读表/同步跑全市场。

### 14.8 confirmation/report handoff

- 修改文件：`src/claw_trade/selection/confirmation.py`、`src/claw_trade/selection/report_handoff.py`、必要时扩展现有 `ConfirmationController`。
- 新增函数：`confirm_selection_ticker`、`build_report_handoff_request`。
- 验收测试：confirmation idempotency tests、no-auto-report tests。
- 不允许顺手做的事：不把 `/select` 结论注入 `/report` PM。

### 14.9 provider payload evidence tests

- 修改文件：`tests/contracts/test_selection_provider_payload.py`（新增建议）。
- 新增函数：payload scanner helpers。
- 验收测试：§13.4。
- 不允许顺手做的事：不用静态 render 替代真实 provider payload。

### 14.10 integration tests

- 修改文件：`tests/integration/selection/**`。
- 新增函数：selection workflow fixtures。
- 验收测试：§13.3、§13.6、§13.7、§13.8。
- 不允许顺手做的事：不使用 mock/stub/fake/fallback 证明 live/provider 行为。

## 15. 仍需人类确认（仅保留真实未知）

### 15.1 本轮已确认、实现中不得再当“待确认”

1. 第一版只做 `CN_A`。
2. 策略条件和阈值必须回指 SEL-00 审计证据中的来源策略；跨策略排序权重按本文 §5.0.2 已确认的 claw-trade v1 透明排序合同落地，不得用未批准算法、权重或阈值替代。
3. 数据源路径已定为 data_gateway/provider registry/Mongo/OpenViking 证据链。
4. selection worker prompt 按本文草案落地，并以 provider payload 做对齐验收。
5. 默认北京时间 16:00 后台跑；设置中允许用户修改。
6. `/select` 第一版在聊天中输出结果，不做复杂结果页。
7. 第一版不做手动补跑/backfill/rerun 的用户入口。

### 15.2 真实未知项

1. `SelectionStage`、`SelectionDataRunStatus`、`SelectionWorkflowStatus` 等新增 enum/class/file 名称：语义已冻结，命名按代码风格实现时确认。
2. `RunProviderPlan` 扩展 batch scope 还是新增 `SelectionProviderBatchPlan`：需在实现层择一，但不得把全市场 batch 伪装成单 ticker report plan。
3. 交易日历具体来源与半日市/节假日细则：需实现阶段选择批准源；默认执行窗口保持北京时间 16:00。
4. selection confirmation 与现有 report confirmation 共用控制器还是独立 endpoint：幂等语义已冻结，实现形态待确认。
5. live acceptance 样本 ticker、交易日、provider key/许可边界：需要在真实环境验收前锁定。

任何需要越过以上“真实未知项”才能继续的实现任务必须停止并问人。
