# A股扩展任务清单

状态：任务拆解稿，未进入实现。
日期：2026-05-21

本清单依据当前已批准文档拆解，只定义真实可交付任务，不允许 mock、fake、stub、fallback、骨架实现、占位实现、假成功、假 provider、假 artifact、假 provider payload 或假数据层 evidence。

## Verdict

可拆解。

未发现需要停止拆解的文档冲突。需要注意：`docs/数据源removed_data_gateway引入方案.md` 的旧任务名仍出现 “skeleton” 字样，但同一文档和后续 A股设计已经明确禁止空壳/占位实现；本清单不沿用“搭骨架”交付语义，只拆真实可验收任务。

## Source Coverage Map

说明：旧 `docs/数据源removed_data_gateway引入方案.md` 和 `docs/数据源修改实施方案.md` 只作为历史背景或负面路径参考；当前实施合同以 `docs/数据层详细设计.md`、`docs/数据层实施任务清单.md` 和 active A股设计为准。

| 文档 | 章节/范围 | 是否覆盖 | 对应任务编号 | 备注 |
|---|---|---:|---|---|
| `AGENTS.md` | 1 项目目标 | 是 | P0-01, P1-08, P1-09, P1-14 | 报告感、真实性、图表证据进入验收 |
| `AGENTS.md` | 2/2.1 判断与中文解释 | 是 | P0-01, P1-15 | 任务证据与边界评审要求 |
| `AGENTS.md` | 3/4 架构问题回答与 pushback | 是 | P0-01, P1-15 | stop condition 与边界检查 |
| `AGENTS.md` | 5 编码纪律 | 是 | 全部任务 | 小范围、可验证、禁止 speculative |
| `AGENTS.md` | 3 架构边界 | 是 | P1-01, P1-04, P1-05, P1-08 | claw-trade/OpenClaw/data_gateway/OpenViking 分工 |
| `AGENTS.md` | 4 OpenClaw agent 边界 | 是 | P1-08, P1-09, P1-10 | 新 worker 必须 OpenClaw turn |
| `AGENTS.md` | 5 workflow 控制面 | 是 | P1-08, P1-10 | CN_A 7-frontline + downstream |
| `AGENTS.md` | 6/6.1 prompt | 是 | P1-09, P1-10 | baseline/provider payload/L1 对齐 |
| `AGENTS.md` | 6.2/6.3 guard freeze | 是 | P0-01, P1-15 | Phase 1 不新增/收紧 runtime guard |
| `AGENTS.md` | 7 tool policy | 是 | P1-05, P1-10 | stage-scoped canonical pack tools |
| `AGENTS.md` | 8 artifact authority | 是 | P1-11, P1-12 | approved L1/L2 与 lineage |
| `AGENTS.md` | 9 PM owner | 是 | P1-12, P1-15 | exporter 不改 PM |
| `AGENTS.md` | 10 truthfulness gates | 是 | P1-06, P1-07, P1-13 | 不造假，不假图 |
| `AGENTS.md` | 11 provider payload | 是 | P1-10, P1-14 | 真实 payload 证明 |
| `AGENTS.md` | 12/12.1 测试与 live preflight | 是 | P1-14, P3-02 | fixed runtime + live/fresh |
| `AGENTS.md` | 13 collect-first | 是 | P1-14, P3-02 | 批量收集失败 |
| `AGENTS.md` | 14 repo sweep | 是 | P1-13 | 旧路径全仓扫 |
| `AGENTS.md` | 15 conformance | 是 | P1-15 | 不过度声称 |
| `AGENTS.md` | 16 sub-agent | 是 | P1-15 | 派发模板要求 |
| `AGENTS.md` | 17 stop-and-ask | 是 | 全部任务 | 每任务 stop conditions |
| `AGENTS.md` | 18 memory | 是 | 全部任务 | 实施任务完成需写 memory |
| `AGENTS.md` | 19 非迁移旧规则 | 是 | P1-13 | 不恢复旧 direct LLM/旧启动口径 |
| `docs/A股扩展方案.md` | 1 结论 | 是 | P1-06 至 P1-10 | CN_A A股专属扩展 |
| `docs/A股扩展方案.md` | 2 项目来源定位 | 是 | P1-06, P1-09 | `a-stock-data` / TradingAgents-astock / astock-peg 边界 |
| `docs/A股扩展方案.md` | 3 架构硬边界 | 是 | P1-01, P1-04, P1-05 | data_gateway 统一数据层入口 |
| `docs/A股扩展方案.md` | 4 CN_A workflow | 是 | P1-08 | 7 frontline + downstream |
| `docs/A股扩展方案.md` | 5 新增三域与 pack | 是 | P1-07 | policy/hot_money/lockup |
| `docs/A股扩展方案.md` | 6 七个 A股资料域 | 是 | P1-06, P1-07 | Phase 1 七域全量 |
| `docs/A股扩展方案.md` | 7 provider 优先级 | 是 | P1-03, P1-06, P1-07 | Tushare 用户源、official_original 保护 |
| `docs/A股扩展方案.md` | 8 用户声明式 provider | 是 | P1-03, P3-01 | catalog/admission/registry/UI |
| `docs/A股扩展方案.md` | 9 失败与缺口 | 是 | P1-02, P1-04, P1-06, P1-07 | attempts/gaps/readiness |
| `docs/A股扩展方案.md` | 10 worker 可见材料 | 是 | P1-05, P1-10, P1-12 | 自然语言 pack/L1 |
| `docs/A股扩展方案.md` | 11 UI | 是 | P3-01 | 设置页与运行详情 |
| `docs/A股扩展方案.md` | 12 实施阶段建议 | 是 | 全部 Phase | Phase 1 全量，Phase 2 补强，Phase 3 UI |
| `docs/A股扩展方案.md` | 13 停止条件 | 是 | 全部任务 | 原样进入 stop conditions |
| `docs/A股扩展方案.md` | 14 成功标准 | 是 | P1-14, P1-15 | 全链路验收 |
| `docs/A股扩展详细设计.md` | 1 总体结论 | 是 | 全部 Phase | 当前主设计 |
| `docs/A股扩展详细设计.md` | 2 需求覆盖矩阵 | 是 | 全部任务 | 逐项转为任务 |
| `docs/A股扩展详细设计.md` | 3 架构边界 | 是 | P1-01, P1-04, P1-05, P1-15 | 允许/禁止链路 |
| `docs/A股扩展详细设计.md` | 4 workflow | 是 | P1-08 | CN_A-only 7 frontline |
| `docs/A股扩展详细设计.md` | 5 新增数据域设计 | 是 | P1-01, P1-07 | 三域模型和 normalized schema |
| `docs/A股扩展详细设计.md` | 6 新增 data_gateway pack 设计 | 是 | P1-05, P1-07 | 三新增 pack tool |
| `docs/A股扩展详细设计.md` | 7 Provider adapter 设计 | 是 | P1-03, P1-06, P1-07 | 七域矩阵、7.2A 默认源 |
| `docs/A股扩展详细设计.md` | 8 用户声明式 provider 设计 | 是 | P1-03, P3-01 | 七域声明式 provider |
| `docs/A股扩展详细设计.md` | 9 Provider 优先级与失败策略 | 是 | P1-03, P1-04, P1-06, P1-07 | official_original attempt/readiness |
| `docs/A股扩展详细设计.md` | 10 Normalized schema 设计 | 是 | P1-07, P1-14 | chart refs/root cause |
| `docs/A股扩展详细设计.md` | 11 Worker prompt/material 边界设计 | 是 | P1-09, P1-10, P1-12 | 三新增 worker baseline |
| `docs/A股扩展详细设计.md` | 12 UI 设计 | 是 | P3-01 | 设置页/运行页 |
| `docs/A股扩展详细设计.md` | 13 数据流和证据链 | 是 | P1-02, P1-11, P1-12, P1-14 | 四类证据不混淆 |
| `docs/A股扩展详细设计.md` | 14 测试与验收设计 | 是 | P1-14, P1-15 | 单元/集成/live/negative |
| `docs/A股扩展详细设计.md` | 15 分阶段实施计划 | 是 | 全部 Phase | Phase 1 不漏到 Phase 2 |
| `docs/A股扩展详细设计.md` | 16 人类拍板清单 | 是 | Questions | 当前无阻断新增问题 |
| `docs/A股扩展详细设计.md` | 17 总体停止条件 | 是 | 全部任务 | 原样进入任务 stop conditions |
| `docs/数据层详细设计.md` | 1-4 目标/模块/数据流/接口 | 是 | P1-01, P1-04, P1-05, P1-12 | `DataRequest -> DataResult`、6 层数据层、provider registry/Mongo evidence |
| `docs/数据层详细设计.md` | 5-11 模型/provider/执行/入库/Mongo | 是 | P1-01 至 P1-07, P1-14 | `ProviderPlugin.capabilities()`、attempt/raw/normalized/cache、8 个 Mongo collection |
| `docs/数据层实施任务清单.md` | 1-7 当前状态/边界/cutover/stop | 是 | P0-01, P1-02, P1-03, P1-13 | 已删除数据网关 本体不是目标运行时依赖；旧 `removed_data_gateway_*` 只作 forbidden legacy path |
| `docs/数据源removed_data_gateway引入方案.md` | superseded 历史口径 | 否 | Historical only | 只作背景；不得作为当前实施合同或验收来源 |
| `docs/数据层实施任务清单.md` | 1 设计来源与硬边界 | 是 | P0-01 | 当前数据层边界 |
| `docs/数据源修改实施方案.md` | superseded 历史口径 | 否 | Historical only | 只作背景；不得作为当前实施合同或验收来源 |
| `docs/数据源修改实施方案.md` | 10 Test Matrix | 是 | P1-14 | unit/integration/live |
| `docs/数据源修改实施方案.md` | 11 Stop Conditions | 是 | 全部任务 | 原样纳入 |
| `docs/数据源修改实施方案.md` | 12 Rollback And Deletion Plan | 是 | P1-13, P2-01 | Git/VCS 回滚，不 silent fallback |
| `docs/数据源修改实施方案.md` | 13 Final Acceptance Criteria | 是 | P1-14, P1-15 | 总验收 |
| `docs/数据源修改实施方案.md` | 14 Residual Risks | 是 | P2-01, P3-02 | 残余风险进入补强和验收 |
| `docs/架构设计.md` | 1-4 总体/边界/workflow/数据流 | 是 | P1-04, P1-08, P1-12 | 控制面与单 worker turn |
| `docs/架构设计.md` | 5 worker/prompt 架构 | 是 | P1-09, P1-10 | prompt 不在 Python |
| `docs/架构设计.md` | 6 tool/skill 架构 | 是 | P1-05, P1-10 | stage tool schema |
| `docs/架构设计.md` | 7 provider 架构 | 是 | P1-03, P1-06, P1-07 | data_gateway/provider registry |
| `docs/架构设计.md` | 8 artifact 架构 | 是 | P1-11, P1-12 | approved material 权威 |
| `docs/架构设计.md` | 9 图表与图片架构 | 是 | P1-06, P1-07, P1-14 | 图表/root cause |
| `docs/架构设计.md` | 10 OpenClaw runtime seam | 是 | P1-10, P1-14 | payload/tool capture |
| `docs/架构设计.md` | 11 本地 runtime 架构 | 是 | P1-14, P3-02 | fixed runtime |
| `docs/架构设计.md` | 12-16 目录/配置/证据/Git/历史 | 是 | P0-01, P1-02, P1-13, P1-15 | 证据与历史边界 |
| `memory/2026-05-20.md` | A股/已删除数据网关 文档形成与修订 | 是 | P0-01, P1-06 至 P1-10 | A股扩展来源与修订依据 |
| `memory/2026-05-21.md` | 复审/拍板/修订 | 是 | 全部任务 | 七域全量、Tushare、official_original、astock-peg、prompt baseline |

## Task List

全局派发规则：除人类明确指定只读评审外，所有实施、测试、验收任务完成后必须追加 `memory/YYYY-MM-DD.md`；该追加是每个任务“允许修改范围”的共同例外，只能记录本任务完成情况、关键决策和后续待办，不得写源码外的无关内容。

### Phase 0：实施前核对门

#### P0-01：A股扩展实施冻结门核验

- 任务编号：P0-01
- 任务名称：A股扩展实施冻结门核验
- 来源文档和章节：`A股扩展详细设计` 15/16；`数据源修改实施方案` 1/4；`数据源removed_data_gateway引入方案` 13.0；`memory/2026-05-21` 10:40/10:56/11:18。
- 目标：核验当前 `data_gateway` 合同、SSRF 策略、七域 provider 作用域、raw/license、baseline 路径、chart readiness、official_original readiness 均按已批准文档冻结。
- 明确不做什么：不写运行代码；不重新缩小 Phase 1；不把 provider 样本缺失改成人类拍板门。
- 允许修改范围：实施时仅 `docs/evidence/**`、`memory/YYYY-MM-DD.md`。
- 禁止修改范围：`src/**`、`agents/**`、`openclaw_plugins/**`、`third_party/**`、runtime guard。
- 实现要求：逐项记录证据路径；若证据缺失，标为“实施前阻断”，不是自行改设计。
- 验收证据：冻结门核对表；数据层合同对齐 evidence；TradingAgents-astock commit `661ccffa812f5182079f604838d4eea3b4abc7ea` 可读。
- 必跑测试/命令：`uv run pytest tests/contracts/test_data_gateway_cutover.py tests/contracts/test_mongo_collection_contract.py`。
- stop conditions：任一证据仍是占位；需要恢复 已删除数据网关 本体或旧 provider executor 才能承载 pack；SSRF 策略放松。
- mock/stub/fake/fallback 检查：不得用“计划写”或空 evidence 文件冒充关闭。
- 依赖任务：无。
- 是否可并行：否，串行前置。

### Phase 1：A股七域全量 provider 矩阵与 workflow 闭环

#### P1-01：数据模型、状态、三新增 domain 合同

- 任务编号：P1-01
- 任务名称：数据模型、状态、三新增 domain 合同
- 来源文档和章节：`数据源removed_data_gateway引入方案` 5、13.2、13.3；`A股扩展详细设计` 5、10。
- 目标：落地七域 `PackDomain`、CN_A-only 校验、ProviderAttempt/Result/Readiness/DataGap/CacheReceipt/ChartAsset/DomainPackResult 等合同。
- 明确不做什么：不写 provider fetch；不写 pack 业务；不新增 guard。
- 允许修改范围：`src/claw_trade/data_gateway/models.py`、`errors.py`、`providers/base.py`、`settings.py`、模型测试。
- 禁止修改范围：workflow、worker prompt、OpenClaw/OpenViking runtime、旧路径删除。
- 实现要求：`policy/hot_money/lockup` 仅 CN_A 合法；`cache_hit/cache_stale/cached_empty/cache_error` 不可转 fresh success；`DataGap.reason` 不直接复用 `ProviderStatus`。
- 验收证据：字段对照表；pytest 输出；CN_A-only negative 样本。
- 必跑测试/命令：`uv run pytest tests/unit/data_gateway/test_models.py tests/unit/data_gateway/test_source_roles.py tests/unit/data_gateway/test_provider_adapter_contract.py`。
- stop conditions：需要删核心字段；无法表达 `LICENSE_BLOCKED`、`SHARED_RESULT`、official_original attempt 语义。
- mock/stub/fake/fallback 检查：只允许状态 fixture，不允许假 provider 成功证明数据能力。
- 依赖任务：P0-01。
- 是否可并行：可与 P1-02/P1-03 准备并行，模型字段变更需串行协调。

#### P1-02：Mongo evidence store 与 persistent single-flight

- 任务编号：P1-02
- 任务名称：Mongo evidence store 与 persistent single-flight
- 来源文档和章节：`数据源removed_data_gateway引入方案` 13.4、13.5；`数据源修改实施方案` T3。
- 目标：实现 provider manifests、validation receipts、attempts、raw payloads、cache、normalized、rate limits、run plans、single-flight stores。
- 明确不做什么：不让 Mongo 成为 worker 材料源；不以 legacy 已删除数据网关 cache 替代 Mongo evidence。
- 允许修改范围：`src/claw_trade/data_gateway/store/**`、cache/attempt/run-plan integration tests。
- 禁止修改范围：pack reader brief、provider adapter、worker prompt、exporter。
- 实现要求：raw/normalized/cache/attempt 写失败必须显式失败；single-flight 用 Mongo lease，多进程有效。
- 验收证据：Mongo sample docs；index list；owner/consumer attempt；cache receipt。
- 必跑测试/命令：`uv run pytest tests/unit/data_gateway/test_cache_state_machine.py tests/integration/data_gateway/test_mongo_attempts.py tests/integration/data_gateway/test_run_provider_plan_snapshot.py`。
- stop conditions：只能用进程内锁；consumer 写独立 remote_success；evidence 写失败仍出成功资料。
- mock/stub/fake/fallback 检查：不能用内存 dict 或假 Mongo 通过 integration。
- 依赖任务：P1-01。
- 是否可并行：可与 P1-03 并行。

#### P1-03：Provider catalog、声明式准入与 priority 边界

- 任务编号：P1-03
- 任务名称：Provider catalog、声明式准入与 priority 边界
- 来源文档和章节：`A股扩展方案` 7/8；`A股扩展详细设计` 8/9；`数据源removed_data_gateway引入方案` 8.5、13.6.1、13.7、13.16。
- 目标：实现 system/user provider catalog、admission 状态机、SSRF/license/raw policy、user-preferred 同四元组排序。
- 明确不做什么：不新增第二套 provider 系统；不允许普通用户代码型 provider；不允许用户源声明/覆盖 `official_original`。
- 允许修改范围：`src/claw_trade/data_gateway/providers/{catalog,admission,declarative,registry,license_policy,secrets}.py` 和相关测试。
- 禁止修改范围：worker prompt、stage policy、pack business logic、旧路径删除。
- 实现要求：priority 仅在同 `market/domain/source_role/coverage_group`；enabled candidate 才进 run plan；active run 固定 `provider_config_version`。
- 验收证据：validation/quarantine receipts；enabled candidates；排序 trace。
- 必跑测试/命令：`uv run pytest tests/unit/data_gateway/test_provider_admission.py tests/unit/data_gateway/test_provider_admission_security.py tests/unit/data_gateway/test_provider_registry.py tests/integration/data_gateway/test_declarative_provider_admission.py`。
- stop conditions：未验证 provider 进 run plan；SSRF 绕过；用户源跨 source_role/coverage_group；Tushare 默认启用。
- mock/stub/fake/fallback 检查：准入 health/sample 必须真实或明确失败，不 mock 成 validated。
- 依赖任务：P1-01，P0-01。
- 是否可并行：可与 P1-02 并行。

#### P1-04：RunProviderPlan 与 no-prefetch workflow 接入

- 任务编号：P1-04
- 任务名称：RunProviderPlan 与 no-prefetch workflow 接入
- 来源文档和章节：`数据源removed_data_gateway引入方案` 13.8；`A股扩展详细设计` 13.1。
- 目标：`/report` run 初始化生成七域 RunProviderPlan，冻结 config version，`remote_prefetch_allowed=false`。
- 明确不做什么：Python 控制层不 fetch provider，不为普通 chat 建 plan。
- 允许修改范围：`src/claw_trade/data_gateway/providers/run_plan.py`、`store/run_plans.py`、workflow run 初始化接入点、run plan tests。
- 禁止修改范围：ProviderAdapter.fetch、pack builder、OpenClaw DAG、普通 chat 入口行为。
- 实现要求：CN_A plan 含 7 域；非 CN_A 只含已批准域；缺 plan/config mismatch 显式失败。
- 验收证据：run plan document；fetch spy 未调用；config version snapshot。
- 必跑测试/命令：`uv run pytest tests/unit/data_gateway/test_run_provider_plan.py tests/integration/data_gateway/test_run_provider_plan_snapshot.py tests/integration/data_gateway/test_run_plan_no_prefetch.py`。
- stop conditions：必须预取 provider 才能跑；active run 需要热加载 UI 配置。
- mock/stub/fake/fallback 检查：不得用 fake run plan 或临时重建 plan 继续。
- 依赖任务：P1-01, P1-02, P1-03。
- 是否可并行：否，packs 前置。

#### P1-05：data_gateway pack tool / 7-pack 接线

- 任务编号：P1-05
- 任务名称：data_gateway pack tool / 7-pack 接线
- 来源文档和章节：`数据源removed_data_gateway引入方案` 4.8、13.1.1、13.12、13.12.1；`A股扩展详细设计` 6。
- 目标：通过当前 `data_gateway` 和 stage-scoped tool schema 暴露七个 canonical pack tool。
- 明确不做什么：不暴露 provider atomic/admin/discovery/debug/cache tools；不 import 旧 provider executor fallback；不恢复 已删除数据网关 runtime。
- 允许修改范围：`src/claw_trade/data_gateway/**` 的 pack/tool 接线、OpenClaw plugin wrapper、tool schema tests。
- 禁止修改范围：workflow DAG、PM/exporter 结论、worker business prompt、OpenViking provider 行为。
- 实现要求：工具返回 worker-visible 自然语言 `reader_brief_md`；audit payload 写 data_gateway/provider evidence；provider payload 证明模型只看 canonical pack tool。
- 验收证据：data_gateway evidence refs；7 endpoint/tool audit；无旧 executor import 证据。
- 必跑测试/命令：`uv run pytest tests/contracts/test_frontline_tool_protocol.py tests/contracts/test_tool_registry_contract.py tests/unit/reports/test_data_pack_bridge.py`。
- stop conditions：必须恢复 已删除数据网关 runtime 或旧 executor；只能本地 Python 直连 provider；tool 需要绕过 `DataAPI`/provider registry。
- mock/stub/fake/fallback 检查：endpoint 不能返回占位 reader brief 或假 success。
- 依赖任务：P1-01, P1-04。
- 是否可并行：tool registry / pack bridge 审计可早做，接入需等 P1-01。

#### P1-06：四个既有 A股域 provider 矩阵接入

- 任务编号：P1-06
- 任务名称：四个既有 A股域 provider 矩阵接入
- 来源文档和章节：`A股扩展方案` 6/12；`A股扩展详细设计` 7.2/7.2A；`数据源removed_data_gateway引入方案` 13.16。
- 目标：把 `market/fundamental/news/social` 的 A股 provider coverage_group 全量落到 `data_gateway` provider plugins/adapters。
- 明确不做什么：不把 Tushare 作为默认源；不把 astock-peg 引入；不让新闻/搜索替代 official_original、财报或资金事实。
- 允许修改范围：`src/claw_trade/data_gateway/providers/cn_a/**`、`packs/{market,fundamental,news,social}.py`、normalizers、fixtures/tests。
- 禁止修改范围：workflow 调度、worker prompt、report exporter 投资结论、runtime guards。
- 实现要求：覆盖 market `cn_a_market_quote/cn_a_market_kline/cn_a_market_orderbook`；fundamental `cn_a_fundamental_financials/cn_a_fundamental_estimates/cn_a_fundamental_research`，含 PEG 字段；news `cn_a_news_company/cn_a_news_announcement/cn_a_news_flash/cn_a_news_macro_global/cn_a_news_discovery`；social `cn_a_social_concept/cn_a_social_search_discovery`。
- 验收证据：每个计划 provider attempt；请求/状态/字段/单位/source refs/raw policy；reader brief gaps/readiness；图表或 root cause。
- 必跑测试/命令：`uv run pytest tests/integration/data_gateway/test_market_pack_cn_a.py tests/integration/data_gateway/test_fundamental_pack_cn_a.py tests/integration/data_gateway/test_news_pack_cn_a.py tests/integration/data_gateway/test_social_pack_cn_a.py tests/unit/data_gateway/test_source_roles.py tests/unit/data_gateway/test_readiness.py`。
- stop conditions：某源只能绕过 `data_gateway`；PE/PB/ROE/PEG 被补造；official_original 无 attempt。
- mock/stub/fake/fallback 检查：不能用假 provider response；fixture 必须来自真实 adapter 样本或失败样本。
- 依赖任务：P1-01 至 P1-05。
- 是否可并行：四域可并行，shared model/registry 不可冲突。

#### P1-07：三新增 A股域 pack 与 provider 矩阵接入

- 任务编号：P1-07
- 任务名称：三新增 A股域 pack 与 provider 矩阵接入
- 来源文档和章节：`A股扩展方案` 5/6/12；`A股扩展详细设计` 5.3、6.2-6.4、7.2/7.2A、10.4。
- 目标：实现 `policy/hot_money/lockup` 三域 normalized schema、pack builder、provider adapters、chart readiness。
- 明确不做什么：不把新闻/搜索线索写成官方政策或公告事实；不把资金流写成确定买卖意图；不把解禁缺失补写筹码结论。
- 允许修改范围：`packs/{policy,hot_money,lockup}.py`、`providers/cn_a/{policy,hot_money,lockup}.py`、charts/readiness/tests。
- 禁止修改范围：OpenClaw DAG、worker prompt、PM/exporter 结论、runtime guard。
- 实现要求：policy 覆盖 `cn_a_policy_official/cn_a_policy_news/cn_a_policy_macro/cn_a_policy_discovery`；hot_money 覆盖 `cn_a_hot_money_dragon_tiger/cn_a_hot_money_fund_flow/cn_a_hot_money_northbound/cn_a_hot_money_sector_flow/cn_a_hot_money_theme_heat`；lockup 覆盖 `cn_a_lockup_unlock/cn_a_lockup_shareholder_count/cn_a_lockup_block_trade/cn_a_lockup_margin_financing/cn_a_lockup_dividend/cn_a_lockup_120d_flow`。
- 验收证据：attempt/http/raw/normalized/cache refs；官方失败 root cause；chart refs 或 non-ready root cause；L1 缺口说明样本。
- 必跑测试/命令：`uv run pytest tests/integration/data_gateway/test_policy_pack_cn_a.py tests/integration/data_gateway/test_hot_money_pack_cn_a.py tests/integration/data_gateway/test_lockup_pack_cn_a.py tests/unit/data_gateway/test_policy_normalizer.py tests/unit/data_gateway/test_hot_money_normalizer.py tests/unit/data_gateway/test_lockup_normalizer.py`。
- stop conditions：任一新增 pack 需绕过 `data_gateway`；图表缺失靠假图/静默；官方原文失败被 ready。
- mock/stub/fake/fallback 检查：不得先返回空壳 pack；不得用“暂无数据”替代 provider attempt。
- 依赖任务：P1-01 至 P1-05。
- 是否可并行：三域可并行，chart/readiness 合同需统一。

#### P1-08：CN_A workflow 7-frontline 接入

- 任务编号：P1-08
- 任务名称：CN_A workflow 7-frontline 接入
- 来源文档和章节：`A股扩展方案` 4；`A股扩展详细设计` 4；`架构设计` 3。
- 目标：CN_A 自动调度 7 个 frontline worker，US/HK/CRYPTO 保持 4 frontline。
- 明确不做什么：不把 `report_polisher` 计入 frontline 或改成 P1-08 的前线扩容范围；不新增 quality gate agent；不让 LLM 决定下一 worker。
- 允许修改范围：`src/claw_trade/workflow/workers.py`、`workflow/models.py`、stage plan tests。
- 禁止修改范围：OpenClaw DAG ownership、provider fetch、worker prompt 内容。
- 实现要求：frontline collect-first；下游顺序保持 bull -> bear -> RM -> trader -> risk 三人 -> PM；按 2026-05-22 人类拍板，CN_A 在 PM 后还必须进入 `report_polisher/final_report`，再由 exporter 输出读者报告。
- 验收证据：CN_A stage plan snapshot；非 CN_A negative snapshot；真实 run call order。
- 必跑测试/命令：`uv run pytest tests/unit/test_workflow_controller.py tests/unit/workflow/test_workflow_workers.py tests/contracts/test_stage_policy_contract.py`。
- stop conditions：调度权移入 OpenClaw/LLM；新增 worker 不是 OpenClaw turn。
- mock/stub/fake/fallback 检查：不允许 Python direct_llm worker 替代。
- 依赖任务：P1-05 至 P1-07 可调用。
- 是否可并行：可与 P1-09 准备并行，接入需串行。

#### P1-09：三新增 worker 配置与 prompt baseline 迁移

- 任务编号：P1-09
- 任务名称：三新增 worker 配置与 prompt baseline 迁移
- 来源文档和章节：`A股扩展详细设计` 11.1A；`AGENTS.md` 6/6.1；`memory/2026-05-21` 08:30/10:40。
- 目标：创建 `policy_analyst`、`hot_money_tracker`、`lockup_watcher` agent 配置，基于 TradingAgents-astock 固定 commit 与现有 CN_A prompt 结构。
- 明确不做什么：不猜 prompt；不照搬 LangGraph/Python 结构；不写成工程 checklist；不加入“禁止强观点”等风格 gate。
- 允许修改范围：`agents/policy_analyst/**`、`agents/hot_money_tracker/**`、`agents/lockup_watcher/**`、prompt alignment tests、baseline evidence docs。
- 禁止修改范围：Python 控制层 business prompt、runtime guard、OpenClaw business DAG。
- 实现要求：每个 worker 仅 CN_A approved；visible tool 分别严格一个 canonical pack tool；prompt front matter 不进 provider payload。
- 验收证据：baseline 原文路径+commit；目标 prompt；同 run provider payload；worker L1；baseline vs 新实现对比。
- 必跑测试/命令：`uv run pytest tests/contracts/test_prompt_alignment_cn_a_workers.py tests/contracts/test_worker_prompt_alignment_policy.py tests/unit/test_all_worker_workspaces.py`。
- stop conditions：baseline 文件不可读；payload 出现工程协议块；输出变成 memo/checklist 风格需人类批准。
- mock/stub/fake/fallback 检查：不得用静态 render 或 reconstructed prompt 当 payload 证据。
- 依赖任务：P1-05, P1-07。
- 是否可并行：三个 worker 可并行，prompt 合同统一审查。

#### P1-10：OpenClaw visible tool schema 与 provider payload 边界

- 任务编号：P1-10
- 任务名称：OpenClaw visible tool schema 与 provider payload 边界
- 来源文档和章节：`数据源removed_data_gateway引入方案` 13.17；`A股扩展详细设计` 11.1/14.4；`AGENTS.md` 11。
- 目标：frontline worker 只见对应 canonical pack tool，downstream worker 不见数据工具，并用真实 `openclaw_llm_provider_payload` 验收。
- 明确不做什么：不把 data_gateway raw/attempt evidence 当 LLM payload；不让 OpenViking write/read 成为 model-visible 工具。
- 允许修改范围：`src/claw_trade/config/tool_names.py`、OpenClaw plugin wrapper、tool schema tests、payload scan tests。
- 禁止修改范围：OpenClaw 业务 DAG、worker prompt 风格、report exporter。
- 实现要求：禁止旧 alias、US atomics、provider admin/discovery/raw/debug/cache、OpenViking 工具进入 payload。
- 验收证据：每 worker visible tools；provider payload scan；data_gateway audit refs。
- 必跑测试/命令：`uv run pytest tests/unit/data_gateway/test_tool_schema.py tests/integration/data_gateway/test_mcp_visible_tools.py tests/contracts/test_provider_payload_visible_tools.py`。
- stop conditions：worker 必须看 atomic provider tool；downstream 看数据工具；payload capture 缺失。
- mock/stub/fake/fallback 检查：payload 必须来自 fresh/live call，不用日志/静态输出替代。
- 依赖任务：P1-05, P1-08, P1-09。
- 是否可并行：可与 P1-11/P1-12 准备并行。

#### P1-11：OpenViking material plane 与 evidence relations

- 任务编号：P1-11
- 任务名称：OpenViking material plane 与 evidence relations
- 来源文档和章节：`数据源removed_data_gateway引入方案` 13.13/13.14；`架构设计` 8/14。
- 目标：建立 approved L1/L2 material、tree/grep/glob、relations、ovpack、context index、runtime health wrapper。
- 明确不做什么：OpenViking 不做 provider；OpenViking search 不当 fresh 数据；engineering memory 不进 worker prompt。
- 允许修改范围：`src/claw_trade/data_gateway/openviking/**`、`src/claw_trade/artifacts/openviking_client.py` 通用 wrapper、OpenViking tests。
- 禁止修改范围：provider fetch、worker prompt、OpenClaw tool schema、PM/exporter 结论。
- 实现要求：final report -> PM L1 -> worker L1 -> L2 -> pack audit -> attempt/raw/normalized/cache refs 可追踪；semantic unavailable 必须显式 blocked/unavailable。
- 验收证据：tree/grep/glob output；relations dump；ovpack receipt/import verification；runtime health snapshot。
- 必跑测试/命令：`uv run pytest tests/unit/data_gateway/test_openviking_relations.py tests/unit/data_gateway/test_openviking_context_index.py tests/unit/data_gateway/test_openviking_runtime_health.py tests/integration/data_gateway/test_openviking_lineage.py tests/integration/data_gateway/test_openviking_evidence_bundle.py`。
- stop conditions：worker 看见 OpenViking protocol；semantic queue disabled 却写 ok；ovpack 依赖 `docs/evidence` 才能复现。
- mock/stub/fake/fallback 检查：不能用自写 probe 冒充 OpenViking recovery/observer 能力。
- 依赖任务：P1-01, P1-02。
- 是否可并行：可与 packs 并行；P1-14 前置。

#### P1-12：Downstream approved L1 material 与 exporter 边界

- 任务编号：P1-12
- 任务名称：Downstream approved L1 material 与 exporter 边界
- 来源文档和章节：`A股扩展详细设计` 11.2、13；`AGENTS.md` 8/9；`架构设计` 8。
- 目标：下游 CN_A workers 接收 7 份 approved frontline L1 原文；`portfolio_manager` 后必须进入 `report_polisher/final_report` 生成终稿 L1；exporter 只导出 approved final_report/material，不补事实、不改 PM。
- 明确不做什么：不摘要/压缩/改写 L1；不让 downstream 读 provider raw/cache；不新增 PM sidecar。
- 允许修改范围：runtime request builder、artifact manifest flow、exporter boundary tests。
- 禁止修改范围：PM natural-language conclusion、runtime guard、worker prompt 风格策略。
- 实现要求：CN_A downstream prompt 变量包含 7 份 L1 正文；`report_polisher` 是终稿 worker，不是 frontline；raw/debug/cache envelope 不进入 prompt。
- 验收证据：downstream provider payload；PM 后 `report_polisher/final_report` provider payload/L1；approved manifest/readback/hash；exporter read trace。
- 必跑测试/命令：`uv run pytest tests/contracts/test_prompt_material_boundary.py tests/integration/data_gateway/test_exporter_material_boundary.py tests/contracts/test_final_report_evidence_chain.py`。
- stop conditions：Python 改写 PM 结论；exporter 读 Mongo raw 补事实；downstream 看数据工具。
- mock/stub/fake/fallback 检查：不得用 fake approved material 或假 OpenViking receipt。
- 依赖任务：P1-08, P1-10, P1-11。
- 是否可并行：可与 P1-13 准备并行。

#### P1-13：旧 provider 路径盘点、负面证明与按 pack 删除边界

- 任务编号：P1-13
- 任务名称：旧 provider 路径盘点、负面证明与按 pack 删除边界
- 来源文档和章节：`数据源removed_data_gateway引入方案` 4.10、8、13.18；`数据源修改实施方案` T13/T14。
- 目标：全仓盘点旧 MCP/provider/direct client/US atomics，建立 import-block 负面证明；已验收 pack 才按 pack 删除/隔离旧路径。
- 明确不做什么：不保留旧 provider runtime fallback；不一次性大爆炸删除未验收路径；compare 结果不进 worker 正文。
- 允许修改范围：迁移看板、import-block tests、已验收 pack 旧路径删除 diff。
- 禁止修改范围：未验收 pack 旧路径、新 pack internals、workflow、worker prompt、runtime guards。
- 实现要求：data_gateway 路径下旧 provider_executor/direct modules 被 monkeypatch/import-block 后，pack 仍经当前数据层成功或显式失败。
- 验收证据：`rg` hit list；import-block 输出；删除/隔离 diff；Git/VCS rollback doc。
- 必跑测试/命令：`rg -n "frontline_data_pack\\.provider_executor|provider_executor|get_stock_data|get_indicators|get_fundamentals|get_balance_sheet|get_cashflow|get_income_statement" src openclaw_plugins agents`；`uv run pytest tests/integration/data_gateway/test_old_provider_import_block.py tests/integration/data_gateway/test_mcp_visible_tools.py`。
- stop conditions：旧路径仍 silent fallback；删除需要跨 pack 大改；回滚吞掉 data_gateway 失败原因。
- mock/stub/fake/fallback 检查：不得以旧路径成功证明 data_gateway 成功。
- 依赖任务：P1-06/P1-07/P1-10 对应 pack 完成。
- 是否可并行：盘点可早做；删除按 pack 串行。

#### P1-14：CN_A fresh/live 全链路验收

- 任务编号：P1-14
- 任务名称：CN_A fresh/live 全链路验收
- 来源文档和章节：`A股扩展详细设计` 14.4/14.5；`数据源removed_data_gateway引入方案` 9/13.19；`AGENTS.md` 12.1/13。
- 目标：用固定 runtime 跑 CN_A 样本，证明 7 frontline、七域 provider attempts、payload、OpenViking lineage、PM 后 `report_polisher/final_report`、最终读者报告全链路。
- 明确不做什么：不使用 capture-only、mock provider、假 artifact、假 payload；不首错即停，除非命中早停例外。
- 允许修改范围：live evidence docs、collect-first report、memory；不改源码。
- 禁止修改范围：源码、runtime guard、fallback 逻辑、worker prompt。
- 实现要求：manager 先打印 fixed runtime preflight；命令模式启动；分离 `openclaw_llm_provider_payload` 与 data_gateway raw/attempt evidence。
- 验收证据：run_id；preflight 表；7 frontline calls；provider payloads；tool calls；Mongo attempts/raw/normalized/cache；OpenViking manifest/relations；charts/root cause；`report_polisher/final_report` payload 与 L1；exporter 输出的 reader-facing final report。
- 必跑测试/命令：`scripts/start-control-runtime.sh -- uv run python scripts/run_claw_trade_fresh_report.py --market CN_A --ticker 600519`；再跑 payload/evidence scan tests。
- stop conditions：无 preflight；runtime alignment 不可信；同类 gate focused fix 后二次失败；数据真实性/PM authority 边界失败。
- mock/stub/fake/fallback 检查：任何假 provider/artifact/payload 均失败。
- 依赖任务：P1-01 至 P1-13。
- 是否可并行：证据扫描可并行；同一 live runtime 资源冲突时串行。

#### P1-15：集成评审、任务派发模板与 Phase 1 收口

- 任务编号：P1-15
- 任务名称：集成评审、任务派发模板与 Phase 1 收口
- 来源文档和章节：`AGENTS.md` 15/16/18；`数据源修改实施方案` 8/9/13。
- 目标：把 Phase 1 任务按 owner 派发，收敛边界、证据、memory、mock/fallback 检查和残余风险。
- 明确不做什么：不声称“完全覆盖/100%”除非逐项证据齐全；不把失败项挪到 Phase 2。
- 允许修改范围：任务/evidence 文档、memory、review checklist。
- 禁止修改范围：源码行为、runtime guard、provider strategy。
- 实现要求：每个 subagent 读 AGENTS 与设计；报告 changed files、commands、exit code、deviation、mock/fake/fallback status、coverage。
- 验收证据：任务完成矩阵；Source Coverage Map 更新；Boundary Check 更新；Phase 1 evidence bundle。
- 必跑测试/命令：按 P1-01 至 P1-14 汇总；`uv run pytest tests/contracts/test_guard_change_requires_approval.py`。
- stop conditions：任一 Phase 1 coverage_group 无任务/无 evidence；guard allowlist 被未批准修改。
- mock/stub/fake/fallback 检查：汇总每项任务自证，不接受未说明。
- 依赖任务：P1-01 至 P1-14。
- 是否可并行：否，Phase 1 收口任务。

### Phase 2：provider 稳定性与补强

#### P2-01：已接入 provider 稳定性、字段漂移、限流与交叉验证补强

- 任务编号：P2-01
- 任务名称：已接入 provider 稳定性、字段漂移、限流与交叉验证补强
- 来源文档和章节：`A股扩展方案` 12 Phase 2；`A股扩展详细设计` 15.3；`数据源removed_data_gateway引入方案` 11 Phase 2。
- 目标：只对 Phase 1 已接入七域矩阵做 schema drift、field_missing、rate limit、failure replacement chain、conflict/cross-check 补强。
- 明确不做什么：不补 Phase 1 漏做范围；不新增 coverage_group；不把搜索发现升级为事实；不引入 astock-peg。
- 允许修改范围：`src/claw_trade/data_gateway/providers/cn_a/**`、`packs/**`、`readiness.py`、normalizer/live contract tests。
- 禁止修改范围：workflow worker 数量、OpenClaw/OpenViking 边界、runtime guards、PM/exporter 结论。
- 实现要求：每个 provider 的失败/空/限流/schema drift 有真实 attempt 和 root cause；官方原文失败仍不得 ready。
- 验收证据：provider live contract；schema drift samples；failure chain trace；conflict records。
- 必跑测试/命令：`uv run pytest tests/unit/data_gateway/test_readiness.py tests/unit/data_gateway/test_source_roles.py tests/integration/data_gateway/test_*_pack_cn_a.py`。
- stop conditions：发现 Phase 1 范围遗漏；某源只能直连旧脚本；license/raw policy 不可接受。
- mock/stub/fake/fallback 检查：不得用 mock drift 样本替代真实 provider sample，除非该测试只验证状态机且明确非 live proof。
- 依赖任务：P1-15。
- 是否可并行：可按 domain/provider 并行。

### Phase 3：UI 优先级与可观测性

#### P3-01：七域数据源设置页与运行 trace UI

- 任务编号：P3-01
- 任务名称：七域数据源设置页与运行 trace UI
- 来源文档和章节：`A股扩展方案` 11/12 Phase 3；`A股扩展详细设计` 12；`数据源removed_data_gateway引入方案` 8.5。
- 目标：UI 展示 CN_A 七域、coverage_group、source_role、system/user provider、priority、health/admission/license/raw、official_original 标识，并在报告详情展示 provider trace。
- 明确不做什么：不新增第二套 provider 后端；不允许用户上传代码 provider；不展示 raw/token/headers。
- 允许修改范围：`src/claw_trade/ui_backend/**`、`src/claw_trade/ui_contracts/**`、前端设置页/报告详情、UI tests。
- 禁止修改范围：worker tool schema、OpenClaw prompt、provider direct code upload、data_gateway bypass。
- 实现要求：active run 固定 config version；UI 修改只影响下一 run；用户源同组 priority 管理。
- 验收证据：设置页 API/截图；provider trace DTO；redaction tests；priority reorder trace。
- 必跑测试/命令：`uv run pytest tests/contracts/test_ui_api_contracts.py tests/unit/ui tests/integration/data_gateway/test_declarative_provider_admission.py`。
- stop conditions：UI 保存后绕过 admission 进 run；raw/token/header 泄漏；普通用户可覆盖 official_original。
- mock/stub/fake/fallback 检查：UI 状态必须来自 catalog/admission/run trace，不用前端假状态。
- 依赖任务：P1-03, P1-14。
- 是否可并行：可与 P2-01 并行。

#### P3-02：四市场 live/fresh 回归与最终证据收敛

- 任务编号：P3-02
- 任务名称：四市场 live/fresh 回归与最终证据收敛
- 来源文档和章节：`数据源修改实施方案` T16/T17；`数据源removed_data_gateway引入方案` 9/13.19。
- 目标：CN_A `600519`、HK `00700.HK` + 非腾讯、US `AAPL/MSFT`、CRYPTO `BTC` 四市场 collect-first 验收与最终证据收敛。
- 明确不做什么：不改源码；不降低验收；不把 HK/US/CRYPTO 缺口塞进 A股任务补做。
- 允许修改范围：`docs/evidence/**`、collect-first report、memory、最终验收文档。
- 禁止修改范围：源码、runtime guard、fallback 逻辑、worker prompt、exporter。
- 实现要求：fixed runtime preflight；分离 LLM payload 与 data_gateway raw/attempt evidence；OpenViking tree/grep/glob/relations/ovpack；chart readiness。
- 验收证据：四市场 run evidence；final acceptance checklist；rollback 文档；residual risks。
- 必跑测试/命令：`scripts/start-control-runtime.sh -- uv run python scripts/run_claw_trade_fresh_report.py ...` 按市场执行；配套 evidence scan。
- stop conditions：无 preflight；使用 mock/stub/fake/capture-only；runtime state 不可信；同类 gate 二次失败。
- mock/stub/fake/fallback 检查：任何假 payload、假 data_gateway evidence、假 artifact 直接失败。
- 依赖任务：P1-15, P2-01, P3-01。
- 是否可并行：市场证据采集可并行，但 manager preflight 必须先做。

## Phase 1 Completeness Check

| domain | coverage_group 覆盖 | data_gateway adapter | pack endpoint | worker/tool schema | evidence tests | 是否完整 |
|---|---|---|---|---|---|---|
| market | `cn_a_market_quote/kline/orderbook` | P1-06 | `claw_get_market_pack` P1-05 | `market_analyst` only P1-10 | P1-06/P1-14 | 是 |
| fundamental | `cn_a_fundamental_financials/estimates/research`，含 PEG 字段 | P1-06 | `claw_get_fundamental_pack` P1-05 | `fundamental_analyst` only P1-10 | P1-06/P1-14 | 是 |
| news | `company/announcement/flash/macro_global/discovery` | P1-06 | `claw_get_news_pack` P1-05 | `news_analyst` only P1-10 | P1-06/P1-14 | 是 |
| social | `concept/search_discovery` | P1-06 | `claw_get_social_pack` P1-05 | `social_analyst` only P1-10 | P1-06/P1-14 | 是 |
| policy | `official/news/macro/discovery` | P1-07 | `claw_get_policy_pack` P1-05 | `policy_analyst` only P1-09/P1-10 | P1-07/P1-14 | 是 |
| hot_money | `dragon_tiger/fund_flow/northbound/sector_flow/theme_heat` | P1-07 | `claw_get_hot_money_pack` P1-05 | `hot_money_tracker` only P1-09/P1-10 | P1-07/P1-14 | 是 |
| lockup | `unlock/shareholder_count/block_trade/margin_financing/dividend/120d_flow` | P1-07 | `claw_get_lockup_pack` P1-05 | `lockup_watcher` only P1-09/P1-10 | P1-07/P1-14 | 是 |

## Boundary Check

| 边界 | 是否违反 | 涉及任务 | 说明 |
|---|---:|---|---|
| data_gateway 是统一 provider 入口 | 否 | P1-04 至 P1-07 | provider fetch 只在 data_gateway pack/tool backend 内 |
| OpenClaw 只运行单 worker turn | 否 | P1-08 至 P1-10、P1-12 | 不让 OpenClaw 承担 DAG/provider 编排；`report_polisher` 也必须是单 worker turn |
| claw-trade 控制 workflow DAG | 否 | P1-08 | stage plan 在 claw-trade |
| OpenViking 不做 provider | 否 | P1-11 | 只做 material/lineage/health |
| Python 控制层不预取 provider | 否 | P1-04 | run plan no-fetch |
| worker 不直连 provider | 否 | P1-05 至 P1-10 | worker 只见 canonical pack tool |
| downstream worker 不看 provider 原子工具 | 否 | P1-10/P1-12 | downstream visible tools 为空 |
| CN_A PM 后进入终稿 worker | 否 | P1-12/P1-14 | `portfolio_manager` 后必须唤醒 `report_polisher/final_report`，exporter 再输出读者报告 |
| 用户 provider 不覆盖 official_original | 否 | P1-03/P1-06/P1-07 | 普通用户不可声明 official_original |
| 无 mock/fake/stub/fallback | 否 | 全部任务 | 每任务有检查项 |
| 无 Phase 1 范围遗漏到 Phase 2 | 否 | P1-06/P1-07/P2-01 | Phase 2 只补强已接入 provider |

## Questions For Human

无。

## Final Recommendation

可以进入实现任务派发。建议先派发 P0-01 做冻结门证据核验；P0-01 通过后，按 P1-01/P1-02/P1-03 并行启动基础合同，再进入 P1-04/P1-05 和七域 pack 实现。
