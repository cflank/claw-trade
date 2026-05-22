# A股扩展详细设计

状态：详细设计，未进入实现。  
日期：2026-05-21  
依据：`AGENTS.md`、`docs/A股扩展方案.md`、`docs/数据源openbb引入方案.md`、`docs/数据源修改实施方案.md`、`docs/架构设计.md`、`memory/2026-05-20.md`。

## 1. 总体结论

### Verdict

部分同意并细化。`CN_A` 应采用 A股专属扩展路径：在现有 TradingAgents-CN 报告链路上，把政策、游资/资金、限售/筹码三类 A股特色变量正式纳入前线资料生产和后续辩论材料，而不是在最终报告阶段补写。目标态只影响 `CN_A`，`US`、`HK`、`CRYPTO` 不因本设计自动增加 worker 或数据域。

### Pushback

- 当前代码已有 `data_gateway` 和 canonical `claw_get_*_pack` 基础，但 `PackDomain` 仍只有 `market/fundamental/news/social`，新增三域需要模型、provider plan、tool registry、pack endpoint、workflow 和 UI 同步扩展。
- OpenBB submodule URL、固定版本、当前个人研究用途和 SSRF 策略已有证据文件关闭；本设计仍不能替人类决定的是后续商业/SaaS 使用。A股数据源接入不再做“先缩范围再补做”：当前目标是把已列入设计的七域 provider 矩阵一次性纳入工程任务。
- A股 `a-stock-data` 只能作为 A股源分类、字段映射和默认顺序参考；若后续发现某接口只能靠旧脚本直连，必须停止，不得为跑通而绕过 OpenBB/data_gateway。

### 已确认事实

- `claw-trade` 拥有 workflow 状态机、worker 调度、artifact 权威、hard gate 和导出。
- OpenClaw 只运行单个 worker turn，负责真实模型 prompt、tool schema、tool call、LLM response 和 provider payload capture。
- OpenBB/data_gateway 是唯一外部数据入口、唯一 provider 接口层。OpenBB 失败不能回退到旧 provider executor 或旧 MCP。
- Mongo 保存 OpenBB provider HTTP/raw/cache/attempt/normalized 等运行证据；OpenViking 保存 approved L1/L2 material、manifest、hash、lineage 和下游 handoff。
- CN_A 扩展新增 `policy_analyst`、`hot_money_tracker`、`lockup_watcher`，并新增 `policy`、`hot_money`、`lockup` 三个数据域。

### 推断

- 新增三域应复用现有 `src/claw_trade/data_gateway/**` 的模型、provider registry、run plan、pack service、Mongo store 和 OpenBB runtime wrapper，而不是新增第二套数据网关。
- 新 worker 应使用与现有 `agents/<worker>/` 相同的 agent 配置结构：`IDENTITY.md`、`USER.md`、`STAGES.yaml`、`SKILLS.md`、`TOOLS.md` 或相同等价文件。
- CN_A 前线保持 collect-first 并行批处理。这里的“顺序”只指调度表、证据编号、approved material 展示顺序和下游 prompt 注入顺序，不表示把 7 个前线 worker 改成串行。

### 未知

- 各 A股源的真实返回字段、单位、限流、失败/空返回形态和 raw 保存边界需要在实现与验收中取证；这不是开工前“是否使用”的人类决策。文档中列出的源实际运行时先用，失败就记录 attempt/gap/readiness。
- `a-stock-data` 给出了默认源优先级，但该优先级必须按用途和 `source_role` 落地：公告、新闻、行情、财务、资金、筹码分别进入各自 coverage_group，不能因为某源可用就跨作用域替代。
- 当前没有“管理员配置官方源”的已批准产品模式；现在不做。普通用户不可配置或覆盖 `official_original` 是当前设计边界。
- 新增三个 worker 的 TradingAgents-astock baseline 路径和 commit 已固化，且本地 `../TradingAgents-astock` 已存在；实现时应直接提取原文，并按现有 `agents/<worker>/prompts/CN_A.md` 结构复用 TradingAgents-CN prompt 形态，形成 provider payload 对照证据。

### Recommendation

按本文设计推进。实现顺序应先扩展模型和 provider plan，再接七域 provider 矩阵与 pack，最后接 workflow、worker 配置和 UI。不得先写 worker prompt 让模型自行找数据。

### Confidence

medium。架构边界、provider 作用域、图表验收和 baseline 路径已明确；低于 high 的原因是各源真实返回样本、raw 保存边界和最终 provider payload 对齐证据仍需在实现验收中补齐。

## 2. 需求覆盖矩阵

| 需求点 | 设计章节 | 模块/文件建议 | 函数/接口建议 | 测试建议 | 验收证据 |
|---|---|---|---|---|---|
| CN_A 采用 A股专属扩展方案 | 1、4 | `src/claw_trade/workflow/workers.py`、`workflow/models.py` | `stage_plans_for_market(market)` | `tests/unit/test_workflow_workers.py` | CN_A stage plan 含 7 个 frontline；US/HK/CRYPTO 保持 4 个 |
| 只影响 CN_A，不自动影响 US/HK/CRYPTO | 1、4、14 | `workflow/workers.py`、`config/stage_policy.py` | `frontline_workers_for_market()` | `tests/contracts/test_stage_policy_contract.py` | 三个非 CN_A profile 不出现新增 worker |
| OpenBB 是唯一外部数据入口 | 3、6、13 | `src/claw_trade/data_gateway/mcp/runtime_wrapper.py` | `OpenBBRuntimeWrapper.get_pack()` | `tests/integration/data_gateway/test_old_provider_import_block.py` | live evidence 有 `openbb_runtime_marker`，无旧 provider import |
| 禁止 worker 直连 `a-stock-data` 脚本 | 3、7、14 | `agents/**/TOOLS.md`、`openclaw_plugins/**` | tool schema allowlist | `test_mcp_visible_tools.py` | provider payload 只含 `claw_get_*_pack` |
| `a-stock-data` 作为 A股 provider 矩阵参考 | 7、15 | `data_gateway/providers/cn_a/**`、`providers/defaults.py` | `build_cn_a_system_capabilities()` | `test_default_provider_catalog.py` | system capability snapshot 覆盖 7 域 |
| `TradingAgents-astock` 只作 worker/口径参考，不照搬 Python/LangGraph | 4、11 | `agents/policy_analyst/**` 等 | agent config only | `test_all_worker_workspaces.py` | 新 worker 都是 OpenClaw worker，不是 Python worker class |
| PEG/估值消化进入 fundamental 与辩论，不新增估值 worker | 5、11 | `packs/fundamental.py`、worker prompts | `render_fundamental_reader_brief()` | `test_fundamental_pack_cn_a.py` | fundamental L1/辩论材料含有证据支持的 PEG/估值缺口 |
| 第一版不引入 `global-stock-data` | 7、15 | provider defaults | capability scan | `test_default_provider_catalog.py` | CN_A system manifest 无 global-stock-data adapter |
| 新增 `policy/hot_money/lockup` 三域 | 5、10 | `data_gateway/models.py` | `PackDomain.POLICY` 等 | `test_models.py` | enum、schema、readiness 支持三域 |
| 新增三个 pack tool | 6 | `data_gateway/mcp/runtime_wrapper.py` | `claw_get_policy_pack` 等 | `test_mcp_pack_endpoints.py` | OpenBB pack routes/MCP tools 只暴露 7 个 pack |
| 新增三个 worker | 4、11 | `agents/policy_analyst/**` 等 | `worker_by_id()` | `test_all_worker_workspaces.py` | worker workspace、stage policy、tool intent 完整 |
| CN_A 7 个 frontline worker 顺序 | 4 | `workflow/workers.py` | `cn_a_stage_plans()` | `test_runner_batch.py` | call order 和 approved material order 稳定 |
| 新增 worker 默认启用 | 4 | `workflow/workers.py`、`agents/**/STAGES.yaml` | `frontline_workers_for_market(CN_A)` | `test_stage_policy_contract.py` | CN_A `/report` 自动调度三 worker |
| 下游 worker 读取 7 份 approved L1 | 4、11、13 | `runtime/request_builder.py`、`artifacts/manifest.py` | `_ordered_materials()` | `test_artifact_flow_guard.py` | bull/bear/research/trader/risk/PM prompt 材料含 7 份 L1 正文 |
| 质量门先作为控制层阶段校验，不新增独立质量门 agent | 4、14 | `workflow/workers.py` | stage plan 无 quality agent | `test_workflow_workers.py` | worker list 无新 gate agent |
| 用户声明式 provider 复用现有 catalog/admission/registry | 8 | `providers/catalog.py`、`admission.py`、`registry.py` | `ProviderCatalog.enabled_candidates()` | `test_provider_admission.py` | 七个 CN_A domain manifest 可按声明作用域进入 enabled candidate |
| priority 只在同一 market/domain/source_role/coverage_group 内生效 | 8、9 | `providers/registry.py` | `sort_provider_candidates()` | `test_provider_registry.py` | 用户优先不跨域、不跨 source_role |
| official_original 不可被用户源覆盖 | 8、9、14 | `providers/registry.py`、`source_roles.py` | `protect_official_original()` | negative test | 官方原文 spec 始终保留 attempt |
| 用户 provider 失败后尝试同组系统 OpenBB 源 | 9 | `providers/run_plan.py`、`providers/execution.py` | `execute_coverage_group()` | `test_provider_execution.py` | attempts 显示用户失败和系统源尝试 |
| 所有尝试写 provider attempt | 9、13 | `store/attempts.py` | `AttemptStore.write()` | `test_mongo_attempts.py` | Mongo `openbb_provider_attempts` 有每个候选记录 |
| 失败进入 data gaps/readiness | 9、10 | `readiness.py`、pack builders | `compute_readiness()` | `test_readiness.py` | `data_gaps` 和 `readiness` 与失败原因一致 |
| 禁止 silent fallback | 3、9、14 | `providers/execution.py` | no fallback branch | import/block tests | 无旧路径调用；替换链在 evidence 可见 |
| Worker 只看自然语言 pack 和缺口说明 | 10、11、13 | `mcp/serializers.py` | `serialize_model_visible_pack()` | `test_tool_schema.py` | provider payload 不含 raw/debug/cache envelope |
| UI 展示 7 个 CN_A 数据域和优先级 | 12 | `ui_backend/data_source_settings.py`、前端设置页 | `list_data_source_domains()` | `tests/unit/ui/test_data_source_settings.py` | 设置页 API 返回 7 域、coverage group、priority |
| 运行页展示命中顺序、失败替换链、data gaps | 12、13 | `ui_backend/data_source_health.py` | `get_run_provider_trace()` | UI API contract tests | run detail JSON 含 attempts/gaps/readiness |
| OpenClaw payload 与 OpenBB HTTP/raw evidence 不混淆 | 13、14 | evidence collectors | named evidence refs | provider evidence audit tests | 两类证据路径和 collection 名称分离 |
| Phase 1 A股全量 provider 矩阵 | 15 | 多模块 | 按 Phase 1 函数范围 | Phase 1 test slice + provider matrix tests | CN_A `/report` 产出 7 L1，七域计划 provider 均有 attempt 和 source_role |
| Phase 2 稳定性与补强 | 15 | `providers/cn_a/**` | capability/normalizer sweep | provider drift/live contract tests | 已接入 provider 的字段漂移、限流、失败和替换链可追溯 |
| Phase 3 UI 优先级与可观测性 | 12、15 | UI backend/frontend | data source APIs | UI tests | 设置页和运行详情展示真实 provider trace |
| 停止条件全部保留 | 15、16 | 实施证据文档 | stop gate checklist | review checklist | 任一禁止链路出现时停止而非绕过 |

矩阵中的“测试建议”是后续工程实现的目标测试名；当前文档不声称这些测试已经存在或已经通过。

## 3. 架构边界

### 3.1 职责

| 组件 | 负责 | 不负责 |
|---|---|---|
| `claw-trade` | `/report` 入口、CN_A 扩展 workflow、worker 调度、stage policy、RunProviderPlan 创建、artifact approval、OpenViking approved material handoff、final export | 直接取外部数据、写 worker 投资判断、改写 PM 结论 |
| OpenClaw | 单个 worker turn、最终 LLM provider payload、worker 可见 tool schema、tool call、first response/raw output capture | 完整 workflow DAG、provider 编排、材料权威、投资逻辑 |
| OpenBB/data_gateway | 唯一外部数据入口、provider adapter、provider attempt、HTTP/raw/normalized/cache/readiness/data gaps、pack 生成 | worker prompt、DAG、PM 决策、OpenViking material authority |
| Mongo | provider HTTP evidence、raw payload、cache、attempt、normalized、run plan、validation receipt | approved artifact、final report、PM 权威 |
| OpenViking | approved L1/L2 material、manifest、content hash、lineage、readback、下游 handoff | 行情/新闻/公告/舆情 provider、provider cache、限流预算 |

### 3.2 允许链路

```text
/report
  -> claw-trade workflow state machine
  -> OpenClaw wake one worker
  -> worker-visible claw_get_<domain>_pack
  -> OpenBB/data_gateway pack endpoint
  -> ProviderRegistry / ProviderAdapter
  -> Mongo provider HTTP/raw/normalized/cache/attempt evidence
  -> natural-language reader_brief + gaps + refs
  -> worker L1 natural-language report
  -> OpenViking approved material
  -> downstream worker approved L1 prompt variables
  -> portfolio_manager L1
  -> report_polisher / final_report L1
  -> final report exporter
```

### 3.3 禁止链路

```text
worker -> a-stock-data script
worker -> Python direct EastMoney/THS/Tencent/Cninfo provider
OpenClaw tool -> old provider_executor
OpenClaw -> workflow DAG decision
workflow/controller -> external provider fetch
OpenBB failure -> old MCP/provider silent fallback
OpenViking -> market/news/announcement/social provider
user declarative provider -> overwrite official_original source
search discovery/news clue/model inference -> official filing/fund flow fact
```

### 3.4 Worker 可见材料边界

worker 主材料只能是：

- 当前 worker 对应资料包的自然语言 `reader_brief_md`。
- 必要的 `compact_facts`、资料缺口、资料就绪度、来源引用、图表引用的读者化表述。
- 下游 worker 看到的 7 份 approved L1 自然语言正文。

不得进入 prompt 主体：

- provider raw JSON、HTTP headers、token、Mongo raw/cache/debug envelope。
- OpenBB atomic/admin/discovery tools。
- OpenViking protocol 文本、URI/hash/L1/L2 工程协议块。
- OpenClaw/OpenBB/OpenViking 内部调试字段。
- `provider_attempts` 的完整机器对象；可以只暴露读者化来源尝试摘要和引用 id。

### 3.5 质量门边界（Phase 1 冻结）

Phase 1 仅复用现有校验边界：

- artifact/material/provider evidence 的既有真实性校验；
- provider payload 与 tool schema 的既有边界校验；
- OpenViking approved material/readback/manifest 的既有校验。

Phase 1 明确禁止：

- 新增 runtime guard、hard gate、early-stop category、exporter rejection rule；
- 收紧既有 guard 以改变表达风格或投资表述；
- 用 guard 替代 prompt/material/workflow 对齐问题。

若确需新增或收紧 guard，必须同时满足：

1. 文档显式写明 `Guard source:`（baseline/design/人类批准来源）；
2. 获得人类明确批准；
3. 提交边界测试：证明 truthfulness redline 仍能拦截，且 evidence-supported 报告表达不被误拦。

## 4. CN_A 扩展 workflow 设计

### 4.1 Worker 顺序

CN_A frontline 固定顺序：

```text
1. market_analyst
2. fundamental_analyst
3. news_analyst
4. social_analyst
5. policy_analyst
6. hot_money_tracker
7. lockup_watcher
```

完整 CN_A 报告链路：

```text
frontline:
  market_analyst
  fundamental_analyst
  news_analyst
  social_analyst
  policy_analyst
  hot_money_tracker
  lockup_watcher

investment_debate:
  bull_researcher
  bear_researcher

investment_decision:
  research_manager

trade_decision:
  trader

risk_debate:
  risk_challenger
  risk_guardian
  risk_moderator

portfolio_decision:
  portfolio_manager

final_report:
  report_polisher
```

2026-05-22 最新人类拍板覆盖旧口径：CN_A workflow 在
`portfolio_manager` 输出最终自然语言决策 L1 后，必须进入
`report_polisher` 所属 `final_report` stage，由终稿 worker 生成
`final_report` 自然语言 L1；随后 exporter 只导出已批准终稿材料。

`report_polisher` 是终稿 worker，不是 frontline worker；CN_A frontline
数量仍固定为 7 个，不新增估值 worker，不新增 quality gate agent。

### 4.2 模块和函数建议

文件范围：

- `src/claw_trade/workflow/workers.py`
- `src/claw_trade/workflow/models.py`
- `src/claw_trade/runtime/request_builder.py`
- `src/claw_trade/config/tool_names.py`
- `agents/policy_analyst/**`
- `agents/hot_money_tracker/**`
- `agents/lockup_watcher/**`

函数级设计：

```python
CN_A_FRONTLINE_WORKERS = (
    "market_analyst",
    "fundamental_analyst",
    "news_analyst",
    "social_analyst",
    "policy_analyst",
    "hot_money_tracker",
    "lockup_watcher",
)

DEFAULT_FRONTLINE_WORKERS = (
    "market_analyst",
    "fundamental_analyst",
    "news_analyst",
    "social_analyst",
)

def frontline_workers_for_market(market: str) -> tuple[str, ...]:
    if market == "CN_A":
        return CN_A_FRONTLINE_WORKERS
    return DEFAULT_FRONTLINE_WORKERS

def worker_specs_for_market(market: str) -> tuple[WorkerSpec, ...]:
    frontline = tuple(WorkerSpec(worker, Stage.FRONTLINE) for worker in frontline_workers_for_market(market))
    downstream = (
        WorkerSpec("bull_researcher", Stage.INVESTMENT_DEBATE),
        WorkerSpec("bear_researcher", Stage.INVESTMENT_DEBATE),
        WorkerSpec("research_manager", Stage.INVESTMENT_DECISION),
        WorkerSpec("trader", Stage.TRADE_DECISION),
        WorkerSpec("risk_challenger", Stage.RISK_DEBATE),
        WorkerSpec("risk_guardian", Stage.RISK_DEBATE),
        WorkerSpec("risk_moderator", Stage.RISK_DEBATE),
        WorkerSpec("portfolio_manager", Stage.PORTFOLIO_DECISION),
    )
    return frontline + downstream

def stage_plans_for_market(market: str) -> tuple[StagePlan, ...]:
    frontline_workers = frontline_workers_for_market(market)
    return replace_frontline_workers(
        base_stage_plans=STAGE_PLANS,
        workers=frontline_workers,
        collect_first=True,
    )
```

调度伪码：

```python
def dispatch_report_run(run: WorkflowRun) -> None:
    plans = stage_plans_for_market(run.request.market)
    current_plan = plan_for_status(plans, run.status)

    if current_plan.stage == Stage.FRONTLINE:
        pending = ordered_pending_workers(
            stage_workers=current_plan.workers,
            completed=run.completed_workers,
            failed=run.failed_workers,
        )
        dispatch_collect_first_batch(run, pending)
        return

    required_materials = approved_materials_for_required_stage(
        run_id=run.id,
        required_stage=current_plan.required_upstream_stage,
        order=material_order_for_market(run.request.market),
    )
    assert_required_materials_present(required_materials, current_plan)
    dispatch_serial_or_batch(run, current_plan, required_materials)
```

材料排序伪码：

```python
def material_order_for_market(market: str) -> tuple[str, ...]:
    return frontline_workers_for_market(market) + (
        "bull_researcher",
        "bear_researcher",
        "research_manager",
        "trader",
        "risk_challenger",
        "risk_guardian",
        "risk_moderator",
        "portfolio_manager",
    )

def approved_frontline_l1_for_downstream(run_id: str, market: str) -> tuple[ApprovedMaterial, ...]:
    expected = frontline_workers_for_market(market)
    materials = openviking_manifest.load_approved_l1(run_id=run_id, workers=expected)
    missing = tuple(worker for worker in expected if worker not in materials.by_worker)
    if missing:
        raise WorkflowBlocked(f"missing approved frontline L1: {missing}")
    return tuple(materials.by_worker[worker] for worker in expected)
```

### 4.3 CN_A-only 启用规则

- `frontline_workers_for_market("CN_A")` 返回 7 个 worker。
- `frontline_workers_for_market("US"|"HK"|"CRYPTO")` 返回原 4 个 worker。
- `build_report_run_plan()` 在 CN_A 下生成 7 个 domain 的 provider plan；其他市场仍只生成对应已批准 domain。
- `agents/policy_analyst/STAGES.yaml`、`agents/hot_money_tracker/STAGES.yaml`、`agents/lockup_watcher/STAGES.yaml` 只批准 `CN_A` profile；HK/US/CRYPTO profile 缺失时必须 fail，不 fallback。

## 5. 新增数据域设计

### 5.1 enum/model 扩展

文件：`src/claw_trade/data_gateway/models.py`

```python
class PackDomain(StrEnum):
    MARKET = "market"
    FUNDAMENTAL = "fundamental"
    NEWS = "news"
    SOCIAL = "social"
    POLICY = "policy"
    HOT_MONEY = "hot_money"
    LOCKUP = "lockup"
```

新增三域只允许 `market == Market.CN_A`：

```python
CN_A_ONLY_DOMAINS = {
    PackDomain.POLICY,
    PackDomain.HOT_MONEY,
    PackDomain.LOCKUP,
}

def validate_market_domain(market: Market, domain: PackDomain) -> None:
    if domain in CN_A_ONLY_DOMAINS and market != Market.CN_A:
        raise ValueError(f"{domain.value} is only approved for CN_A")
```

`PackRequest.__post_init__()`、`ProviderCapability.__post_init__()`、`ProviderCallSpec.__post_init__()` 应调用该校验。

### 5.2 三域与现有四域关系

| 数据域 | 现有关系 | 主 worker | 下游用途 |
|---|---|---|---|
| `market` | 价格、成交、技术面、指数、涨跌停 | `market_analyst` | 交易结构、资金行为背景 |
| `fundamental` | 财报、估值、研报、一致预期、PEG | `fundamental_analyst` | 多空估值论据、RM 裁决 |
| `news` | 个股新闻、公告、快讯、宏观资讯 | `news_analyst` | 催化、风险事件 |
| `social` | 题材、概念、关注度、搜索发现 | `social_analyst` | 情绪和拥挤度参考 |
| `policy` | 监管、产业、宏观、行业政策及公告政策线索 | `policy_analyst` | A股政策环境、监管催化、行业约束 |
| `hot_money` | 龙虎榜、游资席位、主力资金、北向、资金流、板块资金 | `hot_money_tracker` | 短线资金结构、拥挤度、交易持续性 |
| `lockup` | 限售解禁、股东户数、大宗交易、融资融券、分红送转、筹码变化 | `lockup_watcher` | 供给压力、筹码稳定性、潜在抛压 |

### 5.2A PEG/估值消化进入 fundamental 的 Phase 1 设计

`astock-peg` 只作为估值能力参考，不是 Phase 1 默认依赖。Phase 1 不新增估值 worker，也不让 bull/bear/RM 直接调用估值工具。估值消化先进入 `fundamental` 资料包，再通过 `fundamental_analyst` 的 approved L1 自然语言报告传给多空辩论和研究经理。

默认实现路径：

1. 在 `cn_a_fundamental_estimates` coverage group 中接入 `a-stock-data` 已覆盖的同花顺一致预期能力，对应字段至少包括：预测年度、机构覆盖数、一致预期 EPS、EPS 区间、预测净利润或增速（若来源返回）、来源日期和来源引用。
2. 在 `fundamental` normalizer 中生成估值消化字段：`forward_pe`、`peg`、`pe_digestion_years`、`forecast_growth_rate`、`estimate_dispersion`。这些字段必须保留计算口径和输入 refs；缺任一核心输入时写 `field_missing`，不得由模型补算。
3. `render_fundamental_reader_brief()` 必须把估值材料分成三层：已取到的第三方一致预期、由可追溯输入计算出的 forward PE/PEG/估值消化、缺失或分歧说明。
4. `fundamental_analyst` 可以基于资料包写估值判断；`bull_researcher`、`bear_researcher`、`research_manager` 只能通过 approved `fundamental_analyst_report` 引用这些估值论据，不能自行发起估值取数或新增估值 prompt 变量。

Phase 1 必备字段合同：

| 字段 | 来源/计算 | 缺失处理 | 下游用途 |
|---|---|---|---|
| 一致预期 EPS | 同花顺一致预期或 `a-stock-data` 等价 adapter | `field_missing`，估值消化不可 fully ready | 未来盈利锚 |
| 机构覆盖数 | 同一一致预期返回 | 缺失时降低置信度，不伪造覆盖度 | 判断预期可靠性 |
| forward PE | 当前价/预测 EPS，或来源直接给出且有口径 | 缺价格或 EPS 时不计算 | bull/bear 估值辩论 |
| PEG | forward PE / 预测增速，或来源直接给出且有口径 | 缺增速时不计算 | 增长与估值匹配 |
| PE 消化时间 | `a-stock-data`/TradingAgents-astock 已有口径优先；否则仅在口径被文档化后计算 | 口径不明时写 gap | RM 裁决估值是否已消化 |

当前已拍板 Phase 1 不引入 `astock-peg`。如果上述来源无法提供必要字段，估值消化只能写缺口和口径限制；不得为了补估值消化而新增独立估值 worker，也不得在没有未来人类明确批准时 clone/引入 `astock-peg`。

### 5.3 字段级 normalized schema

#### `NormalizedPolicyBundle`

```python
@dataclass(frozen=True)
class PolicyEvent:
    event_id: str
    ticker: str
    company_name: str
    as_of: str
    published_at: str
    policy_level: Literal["regulatory", "industrial", "macro", "industry", "company_announcement"]
    title: str
    summary: str
    affected_industries: tuple[str, ...]
    affected_tickers: tuple[str, ...]
    impact_direction: Literal["positive", "negative", "mixed", "unclear"]
    impact_horizon: Literal["short_term", "medium_term", "long_term", "unclear"]
    source_role: SourceRole
    source_url: str | None
    raw_ref: str | None
    normalized_ref: str | None
```

验证规则：

- `source_role=official_original` 的事件必须有 `source_url` 或 `raw_ref`。
- `source_role=search_discovery` 的事件 `impact_direction` 只能是 `unclear` 或作为线索，不得进入事实主证据。
- `published_at` 不得晚于 `current_date`。
- 缺 `title/published_at/source_role` 为 `schema_invalid` 或 `field_missing`。

#### `NormalizedHotMoneyBundle`

```python
@dataclass(frozen=True)
class DragonTigerEntry:
    trade_date: str
    ticker: str
    reason: str
    seat_name: str
    buy_amount: float | None
    sell_amount: float | None
    net_amount: float | None
    rank: int | None
    source_role: SourceRole
    source_url: str | None
    raw_ref: str | None

@dataclass(frozen=True)
class FundFlowSnapshot:
    trade_date: str
    ticker: str
    main_net_inflow: float | None
    northbound_net_inflow: float | None
    retail_net_inflow: float | None
    sector_net_inflow_rank: int | None
    concept_heat_rank: int | None
    currency: str
    source_role: SourceRole
    raw_ref: str | None
```

验证规则：

- `source_role` 必须是 `market_data` 或 `search_discovery`；龙虎榜/资金流主事实不能用 `search_discovery`。
- 金额字段必须带 `currency=CNY` 或来源货币；单位必须进入 `field_units`。
- 龙虎榜必须保留 `trade_date` 和来源；席位数据不能写成确定买卖意图，只能写成交易行为事实。

#### `NormalizedLockupBundle`

```python
@dataclass(frozen=True)
class LockupUnlockEvent:
    event_id: str
    ticker: str
    unlock_date: str
    shares_unlocked: float | None
    market_value_cny: float | None
    percent_of_float: float | None
    holder_type: str | None
    source_role: SourceRole
    source_url: str | None
    raw_ref: str | None

@dataclass(frozen=True)
class ChipStructureSnapshot:
    as_of: str
    ticker: str
    shareholder_count: int | None
    shareholder_count_change_pct: float | None
    block_trade_amount_cny: float | None
    margin_balance_cny: float | None
    securities_lending_balance: float | None
    dividend_plan: str | None
    source_role: SourceRole
    raw_ref: str | None
```

验证规则：

- 解禁、股东户数、分红送转优先 `official_original` 或可追溯市场事实；如果只有新闻线索，必须标为缺官方原文。
- 大宗交易、融资融券不能被 normalization 层写成买卖意图，只能保留事实字段。
- `unlock_date/as_of/source_role` 缺失必须进入 `field_missing`。

## 6. 新增 OpenBB pack 设计

### 6.1 统一 pack 输出合同

每个新增 pack 返回 `DomainPackResult`，并必须包含：

- 输入上下文：`PackRequest`。
- provider plan：本 run 的 `RunProviderPlan` 中对应 domain 的 `ProviderCallSpec`。
- provider execution：每个候选 provider 的真实 attempt。
- normalization：normalized refs、bundle refs、compact facts。
- readiness：`ready/partial/insufficient/blocked`。
- data gaps：缺失字段、认证、限流、schema、empty、official source 缺失等。
- source refs：面向读者可追溯的来源引用。
- reader_brief：自然语言资料正文。
- evidence refs：HTTP/raw/normalized/cache/attempt/audit refs。

新增 OpenBB runtime routes 和 MCP tool names：

```python
PACK_ENDPOINTS[PackDomain.POLICY] = "/api/v1/claw/get_policy_pack"
PACK_ENDPOINTS[PackDomain.HOT_MONEY] = "/api/v1/claw/get_hot_money_pack"
PACK_ENDPOINTS[PackDomain.LOCKUP] = "/api/v1/claw/get_lockup_pack"

PACK_TOOL_NAMES[PackDomain.POLICY] = "claw_get_policy_pack"
PACK_TOOL_NAMES[PackDomain.HOT_MONEY] = "claw_get_hot_money_pack"
PACK_TOOL_NAMES[PackDomain.LOCKUP] = "claw_get_lockup_pack"
```

### 6.2 `claw_get_policy_pack`

模块建议：

- `src/claw_trade/data_gateway/packs/policy.py`
- `src/claw_trade/data_gateway/providers/cn_a/policy.py`

函数级伪码：

```python
class PolicyPackBuilder:
    def build(
        self,
        *,
        request: PackRequest,
        run_plan: RunProviderPlan,
        adapters_by_id: Mapping[str, ProviderAdapter],
        provider_execution_helper: ProviderExecutionEvidenceHelper,
    ) -> DomainPackResult:
        validate_market_domain(request.market, PackDomain.POLICY)
        specs = select_specs(run_plan, market=Market.CN_A, domain=PackDomain.POLICY)
        if not specs:
            return build_missing_source_pack(request, "policy")

        grouped = group_by_coverage(specs)
        results = []
        gaps = list(run_plan.initial_gaps_for(PackDomain.POLICY))

        for group in grouped:
            group_results, group_gaps = execute_coverage_group(
                request=request,
                group=group,
                adapters_by_id=adapters_by_id,
                provider_execution_helper=provider_execution_helper,
            )
            results.extend(group_results)
            gaps.extend(group_gaps)

        normalized = normalize_policy_results(results)
        readiness = compute_domain_readiness(
            domain=PackDomain.POLICY,
            required_groups=("cn_a_policy_official", "cn_a_policy_news", "cn_a_policy_macro"),
            results=results,
            gaps=gaps,
        )
        reader_brief = render_policy_reader_brief(
            request=request,
            normalized=normalized,
            readiness=readiness,
            gaps=gaps,
        )
        return build_domain_pack_result(
            request=request,
            reader_brief_md=reader_brief,
            normalized=normalized,
            attempts=attempts_from(results),
            data_gaps=tuple(gaps),
            readiness=readiness,
        )
```

`reader_brief` 最小结构：

```text
政策资料包（CN_A）
- 标的与时间范围
- 政策/监管事件摘要
- 行业政策和宏观政策影响
- 公司公告中的政策线索
- 资料缺口与来源边界
- 来源引用
```

### 6.3 `claw_get_hot_money_pack`

模块建议：

- `src/claw_trade/data_gateway/packs/hot_money.py`
- `src/claw_trade/data_gateway/providers/cn_a/hot_money.py`

函数级伪码：

```python
class HotMoneyPackBuilder:
    def build(
        self,
        *,
        request: PackRequest,
        run_plan: RunProviderPlan,
        adapters_by_id: Mapping[str, ProviderAdapter],
        provider_execution_helper: ProviderExecutionEvidenceHelper,
    ) -> DomainPackResult:
        validate_market_domain(request.market, PackDomain.HOT_MONEY)
        specs = select_specs(run_plan, market=Market.CN_A, domain=PackDomain.HOT_MONEY)
        if not specs:
            return build_missing_source_pack(request, "hot_money")

        required_groups = (
            "cn_a_hot_money_dragon_tiger",
            "cn_a_hot_money_fund_flow",
        )
        optional_groups = (
            "cn_a_hot_money_northbound",
            "cn_a_hot_money_sector_flow",
            "cn_a_hot_money_theme_heat",
        )

        attempts: list[ProviderAttempt] = []
        provider_results: list[ProviderResult] = []
        gaps: list[DataGap] = list(run_plan.initial_gaps_for(PackDomain.HOT_MONEY))

        for group in ordered_coverage_groups(specs, required_groups + optional_groups):
            group_results, group_gaps = execute_coverage_group(
                request=request,
                group=group,
                adapters_by_id=adapters_by_id,
                provider_execution_helper=provider_execution_helper,
            )
            provider_results.extend(group_results)
            gaps.extend(group_gaps)
            attempts.extend(result.attempt for result in group_results)

        normalized = normalize_hot_money_results(provider_results)
        conflicts = detect_hot_money_conflicts(normalized)
        readiness = compute_domain_readiness(
            domain=PackDomain.HOT_MONEY,
            required_groups=required_groups,
            optional_groups=optional_groups,
            results=provider_results,
            gaps=gaps,
            conflicts=conflicts,
        )
        reader_brief = render_hot_money_reader_brief(
            request=request,
            normalized=normalized,
            readiness=readiness,
            gaps=gaps,
            conflicts=conflicts,
        )
        return build_domain_pack_result(
            request=request,
            reader_brief_md=reader_brief,
            normalized=normalized,
            attempts=tuple(attempts),
            data_gaps=tuple(gaps),
            readiness=readiness,
            evidence_refs=normalized.evidence_refs,
        )
```

读者材料边界：

- 可以说“龙虎榜显示某席位买入/卖出金额”。
- 可以说“主力资金/北向资金净流入或净流出”。
- 不得把资金流直接写成确定性买卖意图。
- 题材热度必须写明来源和日期，不得写成全市场共识。

### 6.4 `claw_get_lockup_pack`

模块建议：

- `src/claw_trade/data_gateway/packs/lockup.py`
- `src/claw_trade/data_gateway/providers/cn_a/lockup.py`

函数级伪码：

```python
class LockupPackBuilder:
    def build(
        self,
        *,
        request: PackRequest,
        run_plan: RunProviderPlan,
        adapters_by_id: Mapping[str, ProviderAdapter],
        provider_execution_helper: ProviderExecutionEvidenceHelper,
    ) -> DomainPackResult:
        validate_market_domain(request.market, PackDomain.LOCKUP)
        specs = select_specs(run_plan, market=Market.CN_A, domain=PackDomain.LOCKUP)
        if not specs:
            return build_missing_source_pack(request, "lockup")

        required_groups = (
            "cn_a_lockup_unlock",
            "cn_a_lockup_shareholder_count",
        )
        optional_groups = (
            "cn_a_lockup_block_trade",
            "cn_a_lockup_margin_financing",
            "cn_a_lockup_dividend",
            "cn_a_lockup_120d_flow",
        )

        attempts: list[ProviderAttempt] = []
        provider_results: list[ProviderResult] = []
        gaps: list[DataGap] = list(run_plan.initial_gaps_for(PackDomain.LOCKUP))

        for group in ordered_coverage_groups(specs, required_groups + optional_groups):
            group_results, group_gaps = execute_coverage_group(
                request=request,
                group=group,
                adapters_by_id=adapters_by_id,
                provider_execution_helper=provider_execution_helper,
            )
            provider_results.extend(group_results)
            gaps.extend(group_gaps)
            attempts.extend(result.attempt for result in group_results)

        normalized = normalize_lockup_results(provider_results)
        conflicts = detect_lockup_conflicts(normalized)
        readiness = compute_domain_readiness(
            domain=PackDomain.LOCKUP,
            required_groups=required_groups,
            optional_groups=optional_groups,
            results=provider_results,
            gaps=gaps,
            conflicts=conflicts,
        )
        reader_brief = render_lockup_reader_brief(
            request=request,
            normalized=normalized,
            readiness=readiness,
            gaps=gaps,
            conflicts=conflicts,
        )
        return build_domain_pack_result(
            request=request,
            reader_brief_md=reader_brief,
            normalized=normalized,
            attempts=tuple(attempts),
            data_gaps=tuple(gaps),
            readiness=readiness,
            evidence_refs=normalized.evidence_refs,
        )
```

读者材料边界：

- 解禁规模、股东户数、融资融券余额、大宗交易只能作为筹码/供给压力事实。
- 缺失时必须显示 data gap，不能让 worker 根据常识补出筹码结论。

## 7. Provider adapter 设计

### 7.1 `a-stock-data` 到 OpenBB extension/provider adapter 的映射原则

`a-stock-data` 不进入 worker tool，不作为独立 MCP，不作为旧 provider executor。每个列入设计的可用 A股源都应转成 OpenBB project extension/provider adapter，统一实现 `ProviderAdapter`：

```python
class ProviderAdapter(Protocol):
    adapter_id: str
    provider_id: str
    provider_kind: ProviderKind
    adapter_kind: str

    def capabilities(self) -> tuple[ProviderCapability, ...]: ...
    def validate_credentials(self) -> CredentialStatus: ...
    def check_license(self, capability: ProviderCapability) -> LicenseCheckResult: ...
    def fetch(self, spec: ProviderCallSpec, request: PackRequest) -> ProviderFetch: ...
    def normalize(self, spec: ProviderCallSpec, fetch: ProviderFetch) -> NormalizedResult: ...
```

### 7.2 七域 provider capability 和 coverage_group

| domain | coverage_group | 参考源 | source_role | 事实角色 |
|---|---|---|---|---|
| `market` | `cn_a_market_quote` | mootdx、腾讯 quote、东财 quote/push2 | `market_data` | 市场事实 |
| `market` | `cn_a_market_kline` | 百度 K线、东财 K线 | `market_data` | 市场事实 |
| `market` | `cn_a_market_orderbook` | mootdx、腾讯盘口 | `market_data` | 市场事实 |
| `fundamental` | `cn_a_fundamental_financials` | 新浪财报、东财个股信息 | `fundamental_data` | 基本面事实 |
| `fundamental` | `cn_a_fundamental_estimates` | 同花顺一致预期 EPS | `fundamental_data` | 第三方预期，需标来源 |
| `fundamental` | `cn_a_fundamental_research` | 东财研报/PDF | `fundamental_data` | 研究观点，不是官方披露 |
| `news` | `cn_a_news_company` | 东财个股新闻 | `market_data` | 新闻事实需来源 |
| `news` | `cn_a_news_discovery` | 搜索发现源 | `search_discovery` | 只作发现线索，不进入新闻事实主证据 |
| `news` | `cn_a_news_announcement` | 巨潮公告、交易所公告、mootdx F10 | `official_original` | 官方原始披露 |
| `news` | `cn_a_news_flash` | 财联社快讯 | `market_data` | 新闻/快讯事实 |
| `news` | `cn_a_news_macro_global` | 东财全球资讯 | `macro_data` | 宏观/全球事实与线索 |
| `social` | `cn_a_social_concept` | 同花顺热点、百度概念板块 | `social_aggregate_metric` | 题材/关注度指标 |
| `social` | `cn_a_social_search_discovery` | 搜索发现源 | `search_discovery` | 发现线索 |
| `policy` | `cn_a_policy_official` | 巨潮公告、交易所/监管披露 | `official_original` | 官方政策/监管事实 |
| `policy` | `cn_a_policy_news` | 财联社快讯、东财个股新闻 | `market_data` | 政策相关新闻 |
| `policy` | `cn_a_policy_macro` | 东财全球资讯 | `macro_data` | 宏观政策事实/线索 |
| `policy` | `cn_a_policy_discovery` | 搜索发现源 | `search_discovery` | 仅发现线索 |
| `hot_money` | `cn_a_hot_money_dragon_tiger` | 龙虎榜、全市场龙虎榜 | `market_data` | 市场交易事实 |
| `hot_money` | `cn_a_hot_money_fund_flow` | 东财资金流 | `market_data` | 资金流事实 |
| `hot_money` | `cn_a_hot_money_northbound` | 同花顺北向 | `market_data` | 北向资金事实 |
| `hot_money` | `cn_a_hot_money_sector_flow` | 行业板块排名 | `market_data` | 板块资金事实 |
| `hot_money` | `cn_a_hot_money_theme_heat` | 同花顺热点 | `social_aggregate_metric` | 题材热度指标 |
| `lockup` | `cn_a_lockup_unlock` | 东财 datacenter 限售解禁 | `market_data` | 解禁市场事实 |
| `lockup` | `cn_a_lockup_unlock` | 巨潮/交易所官方披露 | `official_original` | 解禁官方原文事实 |
| `lockup` | `cn_a_lockup_shareholder_count` | 东财 datacenter 股东户数 | `fundamental_data` | 持有人结构事实 |
| `lockup` | `cn_a_lockup_shareholder_count` | 巨潮/交易所官方披露 | `official_original` | 股东户数官方原文事实 |
| `lockup` | `cn_a_lockup_block_trade` | 大宗交易 | `market_data` | 交易事实 |
| `lockup` | `cn_a_lockup_margin_financing` | 融资融券 | `market_data` | 杠杆/融券事实 |
| `lockup` | `cn_a_lockup_dividend` | 巨潮/交易所公告 | `official_original` | 公司行动官方原文事实 |
| `lockup` | `cn_a_lockup_dividend` | 东财 datacenter 分红送转 | `fundamental_data` | 公司行动结构化事实 |
| `lockup` | `cn_a_lockup_120d_flow` | 120 日资金流 | `market_data` | 筹码线索 |

### 7.2A A股 provider 作用域与默认优先级落地

`a-stock-data` 的默认优先级不是“所有场景只先接一个源”。它按用途给默认顺序。这里的“接口地址”就是它代码里写死的外部 API URL、请求参数、请求头和字段解析方式，不是 OpenBB 子模块 URL。

实际产品中的 provider 必须都是 OpenBB/data_gateway 下的 provider/adapter。系统默认源按下列作用域接入；Tushare 等非默认源只有在用户配置、验证并启用后才进入候选，并且只能在其声明的同一 `market/domain/source_role/coverage_group` 内优先。

1. 行情和基础交易事实：先 `mootdx`，再腾讯财经。
2. A股资金面、龙虎榜、解禁、股东户数、分红、大宗交易：优先东财 `datacenter`。
3. 行业板块和个股资金流：优先东财 `push2/push2his`。
4. 研报主题搜索：`iwencai` 是唯一能力源；完整研报/PDF 走东财 `reportapi/PDF`。
5. 题材热度：同花顺热点；北向资金：同花顺 `hsgtApi`。
6. 概念板块和带均线 K 线：百度股市通。
7. 财报三表：新浪财经。
8. 快讯：财联社。
9. 公告原文：巨潮 `cninfo`。

落到本设计七个 CN_A 数据域：

| domain | coverage_group | 系统默认源/能力 | 用户配置源语义 | 说明 |
|---|---|---|---|---|
| `market` | `cn_a_market_quote` | mootdx、腾讯 quote、东财 quote/push2 | 用户行情源可同组优先 | 行情事实，保留时间、价格、成交单位。 |
| `market` | `cn_a_market_kline` | 百度 K线、东财 K线 | 用户 K线源可同组优先 | K线/均线/技术图表不得由新闻补。 |
| `market` | `cn_a_market_orderbook` | mootdx、腾讯盘口 | 用户盘口源可同组优先 | 盘口事实不能写成确定买卖意图。 |
| `fundamental` | `cn_a_fundamental_financials` | 新浪财报、东财个股信息 | 用户财务源或 Tushare 财务源可同组优先 | 财报事实必须保留口径。 |
| `fundamental` | `cn_a_fundamental_estimates` | 同花顺一致预期 EPS | 用户一致预期源可同组优先 | 一致预期是第三方预期，不是公司披露。 |
| `fundamental` | `cn_a_fundamental_research` | 东财研报/PDF、iwencai 研报主题搜索 | 用户研报源可同组优先 | 研报是研究观点，不是官方事实。 |
| `news` | `cn_a_news_company` | 东财个股新闻 | 用户新闻源可同组优先 | 新闻事实需来源、时间和标题。 |
| `news` | `cn_a_news_announcement` | 巨潮公告、交易所公告、mootdx F10 | 普通用户源不可覆盖 `official_original`；Tushare 只有声明为可追溯公告能力时可作补充或交叉验证 | 官方原文事实，失败要标失败和 root cause。 |
| `news` | `cn_a_news_flash` | 财联社快讯 | 用户快讯源可同组优先 | 快讯不能冒充公告原文。 |
| `news` | `cn_a_news_macro_global` | 东财全球资讯 | 用户宏观新闻源可同组优先 | 宏观/全球线索需标来源。 |
| `social` | `cn_a_social_concept` | 同花顺热点、百度概念板块 | 用户题材源可同组优先 | 题材热度是聚合指标，不是全市场共识。 |
| `social` | `cn_a_social_search_discovery` | 搜索发现源 | 用户搜索源可同组优先 | 只作发现线索。 |
| `policy` | `cn_a_policy_official` | 巨潮 `cninfo`、交易所/监管披露 | 普通用户源不可覆盖 `official_original` | 官方政策/监管事实，不得用新闻或搜索替代。 |
| `policy` | `cn_a_policy_news` | 财联社快讯、东财个股新闻、东财全球资讯 | 用户政策新闻源可同组优先 | 只能作为新闻/线索，不能冒充公告原文。 |
| `policy` | `cn_a_policy_macro` | 东财全球资讯 | 用户宏观政策源可同组优先 | 宏观政策事实/线索。 |
| `policy` | `cn_a_policy_discovery` | 搜索发现源 | 用户发现源可同组优先 | 仅发现线索。 |
| `hot_money` | `cn_a_hot_money_dragon_tiger` | 东财 `datacenter` 龙虎榜、全市场龙虎榜 | 用户龙虎榜源或 Tushare 同类能力可同组优先 | 市场交易事实，保留日期、席位、金额和单位。 |
| `hot_money` | `cn_a_hot_money_fund_flow` | 东财 `push2/push2his` 个股资金流 | 用户资金流源或 Tushare 同类能力可同组优先 | 资金事实，不得写成确定买卖意图。 |
| `hot_money` | `cn_a_hot_money_northbound` | 同花顺 `hsgtApi`、本地自缓存历史 | 用户北向源可同组优先 | 历史完整性取决于来源和缓存，缺口必须写明。 |
| `hot_money` | `cn_a_hot_money_sector_flow` | 东财 `push2` 行业板块 | 用户板块资金源可同组优先 | 板块资金事实。 |
| `hot_money` | `cn_a_hot_money_theme_heat` | 同花顺热点 | 用户题材热度源可同组优先 | 题材归因，不得替代资金事实。 |
| `lockup` | `cn_a_lockup_unlock` | 东财 `datacenter` 限售解禁、巨潮/交易所公告交叉验证 | 用户解禁源或 Tushare 同类能力可同组优先，但不能覆盖官方原文缺口 | 解禁数据可先用市场事实源；重大结论保留官方缺口标记。 |
| `lockup` | `cn_a_lockup_shareholder_count` | 东财 `datacenter` 股东户数、mootdx F10/公告核验 | 用户股东户数源或 Tushare 同类能力可同组优先 | 股东户数不是新闻事实。 |
| `lockup` | `cn_a_lockup_block_trade` | 东财 `datacenter` 大宗交易 | 用户大宗交易源可同组优先 | 只能写交易事实，不写买卖意图。 |
| `lockup` | `cn_a_lockup_margin_financing` | 东财 `datacenter` 融资融券 | 用户融资融券源可同组优先 | 只能写融资融券事实和日期。 |
| `lockup` | `cn_a_lockup_dividend` | 东财 `datacenter` 分红送转、巨潮公告核验 | 用户分红源可同组优先，但不能覆盖官方原文缺口 | 分红作为公司行动事实。 |
| `lockup` | `cn_a_lockup_120d_flow` | 东财 `push2his` 120 日资金流 | 用户筹码线索源可同组优先 | 只能作为筹码变化线索。 |

#### Phase 1 七域全量源集合

Phase 1 不做“先做一部分再补范围”。文档 7.2/7.2A 已列出的七域 coverage_group 都进入工程任务范围。实现时按上表作用域逐组接入；真实运行中先尝试用户已验证并启用的同组 provider，再尝试系统默认 OpenBB provider。所有尝试都必须写 provider attempt 和 gaps/readiness。

最低验收：

- 每个计划 provider 都有 attempt；失败、空返回、限流、字段缺失、schema drift 都要进入 evidence。
- `official_original` 失败时标记失败和 root cause，不阻断 workflow，但该官方 coverage_group 不得标 ready。
- worker 可以继续产出 L1，但必须在 reader_brief/L1 中说明官方原文或相应事实层缺口。
- Tushare 等用户配置 provider 只在同一作用范围内优先；未配置时真实产品不默认依赖 Tushare。

- `policy` 的 `official_original` 不能被新闻、搜索或用户声明源替代；新闻/搜索只能在 reader_brief 中标为线索。
- 新闻、快讯、搜索只能当线索；不能当公告原文、财报原文、资金事实。
- provider 样本、字段、单位和 raw policy 是实现与验收证据；不得因为样本暂缺就删掉文档已列的 coverage_group。

优先级规则：

- 全局优先级只决定同类市场数据源默认顺序，不覆盖 `source_role` 边界。
- `official_original` 指“官方原文/官方披露”，不是“管理员提供的数据”。巨潮公告、交易所公告、监管披露可以是 `official_original`；东财新闻、财联社快讯、搜索结果不是。
- 当前设计没有管理员模式。系统内置官方源可以标 `official_original`；普通用户声明式 provider 第一版不得配置或覆盖 `official_original`。

当前缺口：

- `a-stock-data` 已经给出接口地址和示例代码；缺口不是“找不到接口”，而是 claw-trade 还没有把这些接口做成 OpenBB/data_gateway adapter。
- 每个要接入的接口都必须在本仓经 OpenBB/data_gateway adapter 实测一次，保存样本：请求参数、返回字段、单位、空值行为、失败状态。
- 每个接口要写清楚 raw 能不能保存。不能保存 raw 的，只保存摘要、字段、hash、时间和来源引用。
- 北向资金历史是本地自缓存，不能假装从第一天就有完整历史；首次运行只能写当前可得范围和缺口。
- 新闻、快讯、搜索只能当线索；不能当公告原文、财报原文、资金事实。

### 7.3 官方原文、市场事实、搜索发现边界

- 官方原始披露：巨潮公告、交易所公告、监管或官方披露。可以作为事实主证据，普通用户源不能完全覆盖。
- 市场事实：行情、K线、盘口、成交、资金流、龙虎榜、融资融券、大宗交易。可作为市场事实，但必须保留日期、来源和单位。
- 基本面事实：财报、估值、股东户数、分红、研报/一致预期。研报和一致预期必须标明第三方来源，不能冒充公司披露。
- 搜索发现：搜索、聚合、网页发现。只能提示“哪里可能有材料”，不能直接当公告、财报、资金事实。

### 7.4 还没补齐的缺口

下面这些是进入实现前必须补的真实缺口：

| 缺口 | 说人话 | 为什么重要 | 关闭证据 |
|---|---|---|---|
| 参考接口样本/探针样本 | `a-stock-data` 代码里有地址和参数，但本仓还没按 OpenBB adapter 跑一遍 | 避免照抄后字段漂移、单位错误或接口空返回 | 每个计划源 1 个可取证样本：请求、状态、字段、单位、失败/空返回形态；adapter 实测放入 Phase 1 验收 |
| raw 保存边界 | 哪些源能保存原始返回，哪些只能保存摘要和 hash | 不能因为调试方便就违规保存外部内容 | 每个源的 `raw_export_policy` 记录 |
| 三新增 worker baseline prompt | baseline 路径和 commit 已固化，但还需提取原文并与目标 prompt/provider payload 对照 | 防止写成工程 checklist，丢掉 TradingAgents 报告感 | baseline 原文摘录、commit、provider payload 对照 |
| 北向历史范围 | 同花顺北向历史靠本地缓存积累，第一次跑不可能天然有完整历史 | 防止报告把“缓存很短”写成“历史完整” | 首次运行缺口说明和缓存 receipt |

## 8. 用户声明式 provider 设计

### 8.1 复用现有后端

必须复用：

- `ProviderCatalog`
- `ProviderAdmissionValidator`
- `DeclarativeProviderManifest`
- `ProviderRegistry`
- `ProviderKind.USER_DECLARATIVE`
- `PrioritySource.USER_PREFERRED`

不得新增第二套用户 provider 系统、第二套配置目录、第二套 admission 状态机。

### 8.2 支持七个 CN_A 数据域

`DeclarativeProviderManifest.domains` 允许覆盖七个 CN_A 数据域。A股扩展新增的是 `policy/hot_money/lockup` 三域，但用户已验证并启用的 provider 在真实运行中可以按声明作用域参与任一已批准 CN_A domain：

```python
(PackDomain.MARKET,)
(PackDomain.FUNDAMENTAL,)
(PackDomain.NEWS,)
(PackDomain.SOCIAL,)
(PackDomain.POLICY,)
(PackDomain.HOT_MONEY,)
(PackDomain.LOCKUP,)
```

但必须同时满足：

```python
manifest.markets == (Market.CN_A,)
manifest.admission_status == ProviderAdmissionStatus.ENABLED_CANDIDATE
manifest.enabled is True
manifest.coverage_group in APPROVED_CN_A_COVERAGE_GROUPS[manifest.domain]
manifest.source_role != SourceRole.OFFICIAL_ORIGINAL
```

普通用户第一版不得上传代码型 provider。声明式 provider 只能是受限 HTTP/RSS/JSON mapping，且经过域名白名单、协议、DNS、redirect、credential、license、schema sample、raw export policy 验证。

当前不设计“管理员 provider”分支。`official_original` 只能来自系统内置且已批准的官方披露源；普通用户声明式 provider 不得声明为 `official_original`。

### 8.3 UI 到 run plan 的链路

```text
UI 保存 provider manifest draft
  -> ProviderCatalog.upsert_manifest()
  -> ProviderAdmissionValidator.validate()
  -> validation receipt 写 Mongo
  -> enabled_candidate
  -> ProviderRegistry.from_catalog()
  -> capabilities_for(market=CN_A, domain=market/fundamental/news/social/policy/hot_money/lockup)
  -> RunProviderPlanner.build_run_plan()
  -> OpenBB pack runtime 按 plan 执行
```

伪码：

```python
def save_user_provider(input: UiProviderInput, actor: str) -> ProviderValidationReceipt:
    manifest = build_declarative_manifest(input)
    validate_no_code_provider(manifest)
    validate_market_domain_scope(manifest)
    receipt = catalog.validate_manifest(manifest, actor=actor, reason="ui_save")
    validation_receipt_store.write(receipt)
    return receipt

def build_registry_for_report(catalog: ProviderCatalog) -> ProviderRegistry:
    system = build_cn_a_system_capabilities()
    return ProviderRegistry.from_catalog(catalog, system_capabilities=system)
```

### 8.4 priority 生效范围

priority 只在同一四元组内排序：

```text
market
domain
source_role
coverage_group
```

排序函数不得让用户源跨 source_role、跨 coverage_group、跨 domain 抢先；尤其不得让 `search_discovery` 代替 `official_original`。

## 9. Provider 优先级与失败策略

### 9.1 排序函数

文件：`src/claw_trade/data_gateway/providers/registry.py`

```python
def sort_provider_candidates(candidates: tuple[ProviderCapability, ...]) -> tuple[ProviderCapability, ...]:
    buckets = group_by(
        candidates,
        key=lambda c: (c.market, c.domain, c.source_role, c.coverage_group),
    )
    ordered = []
    for bucket_key in stable_bucket_order(buckets):
        items = list(buckets[bucket_key])
        official = [c for c in items if c.source_role == SourceRole.OFFICIAL_ORIGINAL]
        user = [c for c in items if c.priority_source == PrioritySource.USER_PREFERRED]
        system = [c for c in items if c.priority_source == PrioritySource.SYSTEM_DEFAULT]

        # 官方原文源必须保留 attempt。普通用户源不能删除或覆盖 official。
        if official:
            ordered.extend(sort_by_priority(official))
            ordered.extend(sort_by_priority(user))
            ordered.extend(sort_by_priority(system))
            continue

        ordered.extend(sort_by_priority(user))
        ordered.extend(sort_by_priority(system))
    return tuple(dedup_by_adapter_endpoint(ordered))
```

### 9.2 coverage_group 执行函数

```python
def execute_coverage_group(
    *,
    request: PackRequest,
    group: CoverageGroup,
    adapters_by_id: Mapping[str, ProviderAdapter],
    provider_execution_helper: ProviderExecutionEvidenceHelper,
) -> tuple[tuple[ProviderResult, ...], tuple[DataGap, ...]]:
    results = []
    gaps = []
    successes = 0

    for spec in group.ordered_specs:
        result = execute_one_provider_spec(
            request=request,
            spec=spec,
            adapters_by_id=adapters_by_id,
            provider_execution_helper=provider_execution_helper,
        )
        results.append(result)

        if result.status == ProviderStatus.REMOTE_SUCCESS:
            successes += 1
            if successes >= group.coverage_quorum:
                break
            continue

        gaps.append(gap_from_provider_result(request, spec, result))

        # 这不是 silent fallback：失败已写 attempt/gap，下一候选仍必须继续尝试。
        continue

    if successes < group.coverage_quorum:
        gaps.append(group_gap(group, results))

    return tuple(results), tuple(gaps)
```

### 9.2A official_original attempt 语义（强制）

当某 `coverage_group` 含 `official_original` 候选时，必须满足：

1. 本轮 run 至少产生 1 条 `official_original` attempt（成功或失败都要记）。
2. 若 `official_original` 失败，必须写 `DataGap` 和 root cause，不得被“搜索摘要成功”掩盖。
3. `search_discovery`、聚合新闻、二手网页线索可继续作为线索层 attempt，但不得提升为官方事实层。
4. reader_brief 必须显式区分“官方原文已验证”与“仅线索未核实”。

违反以上任一条即视为 attempt 语义不合格，run 不得标记为该域 fully ready。

### 9.2B official_original readiness 阈值

默认语义：官方原文 provider 失败要标记失败、写 root cause、降低 readiness，但不得因此阻断整个 workflow。worker 可以继续产出 L1，不过必须把官方原文缺失写进 reader_brief 和 L1；下游不得把线索当成已核验官方事实。

readiness 规则：

| 场景 | readiness | worker 是否可产 L1 | reader_brief 必须写明 |
|---|---|---|---|
| `official_original` 成功，required coverage groups 达 quorum | `ready` 或 `partial`（按其他缺口决定） | 可以 | 官方原文已验证的事实、来源、日期 |
| `official_original` 失败，但新闻/搜索/市场事实有成功线索 | `partial`，不得 `ready` | 可以，但 L1 必须显式标“官方原文缺失” | 官方源 attempt id、失败 root cause、线索来源不能当官方事实 |
| `official_original` 失败，且该 pack 的核心结论依赖官方事实 | `insufficient`，不得 `ready` | 可以产缺口型 L1，但不能产出依赖官方事实的完整事实结论 | 缺哪类官方事实、为什么不能判断 |
| `official_original` attempt 未产生或 evidence 写入失败 | 运行证据失败；该 coverage_group 不合格 | 不可以声称已尝试官方源；必须修复 evidence/attempt 记录后再验收 | 缺 attempt/evidence 是运行证据失败 |

负例：

- 巨潮失败后，东财新闻命中“公司公告称……”不能写成“官方公告已确认”。
- 搜索发现源返回网页标题，不能补成政策原文、公告原文或监管原文。
- partial L1 可以给下游使用，但下游 prompt/material 必须保留缺口段落，bull/bear/RM 不得把该事实当成已核验材料。

### 9.3 失败处理函数

```python
def gap_from_provider_result(request: PackRequest, spec: ProviderCallSpec, result: ProviderResult) -> DataGap:
    reason = {
        ProviderStatus.CREDENTIAL_MISSING: DataGapReason.CREDENTIAL_MISSING,
        ProviderStatus.RATE_LIMITED: DataGapReason.RATE_LIMITED,
        ProviderStatus.EMPTY: DataGapReason.EMPTY,
        ProviderStatus.FIELD_MISSING: DataGapReason.FIELD_MISSING,
        ProviderStatus.SCHEMA_INVALID: DataGapReason.SCHEMA_INVALID,
        ProviderStatus.CACHE_ERROR: DataGapReason.CACHE_ERROR,
        ProviderStatus.CACHED_EMPTY: DataGapReason.CACHED_EMPTY,
        ProviderStatus.CACHE_STALE: DataGapReason.STALE_CACHE_UNUSABLE,
        ProviderStatus.EVIDENCE_WRITE_FAILED: DataGapReason.EVIDENCE_WRITE_FAILED,
        ProviderStatus.LICENSE_BLOCKED: DataGapReason.LICENSE_BLOCKED,
    }.get(result.status, DataGapReason.PROVIDER_UNAVAILABLE)

    return DataGap(
        gap_id=f"{request.run_id}:{request.call_id}:{spec.call_key}:{reason.value}",
        domain=request.domain,
        severity=GapSeverity.FAIL if spec.required else GapSeverity.WARN,
        reason=reason,
        field_path=spec.endpoint,
        provider_candidates=(spec.provider,),
        attempt_ids=(result.attempt.attempt_id,),
        root_cause=result.error_message or result.status.value,
        next_action=next_action_for_reason(reason),
    )
```

强制策略：

- 用户 enabled candidate 优先尝试，但失败后同组系统 OpenBB 源必须继续尝试。
- 每次尝试必须写 `ProviderAttempt`。
- `cache_hit` 不能写成 fresh success；`cache_stale` 不能静默使用；`cached_empty` 必须进入缺口。
- official_original 失败时，新闻或搜索只能作为线索，不能补成公告事实。

### 9.4 active run `provider_config_version` 固定语义

- 每次 `/report` 在 run 初始化时写入固定 `provider_config_version`。
- active run 内不允许热加载 UI 新配置覆盖当前 version。
- catalog reload 仅影响后续新 run；不影响已启动 run 的排序、候选集与 evidence 解释。
- 验收以 `openbb_run_provider_plans` + run trace 对齐为准，不以 UI 当前页状态替代。

## 10. Normalized schema 设计

### 10.1 通用 refs

```python
@dataclass(frozen=True)
class EvidenceRefs:
    source_refs: tuple[ProviderSourceRef, ...]
    provider_attempt_refs: tuple[str, ...]
    openbb_http_refs: tuple[str, ...]
    raw_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]
    cache_refs: tuple[str, ...]
    normalized_bundle_ref: str | None
```

ref 命名边界：

- `openclaw_llm_provider_payload`：只指模型 provider 最终 messages/tool schema。
- `openbb_provider_http_evidence`：只指 OpenBB/adapter 调外部 provider 的 HTTP 证据。
- `openbb_provider_raw_evidence`：只指外部数据 raw payload/hash/ref。
- `openviking_material_ref`：只指 approved material。

### 10.2 三域 normalized bundle

```python
@dataclass(frozen=True)
class NormalizedPolicyBundle:
    request: PackRequest
    events: tuple[PolicyEvent, ...]
    compact_facts: Mapping[str, Any]
    domain_status: Mapping[str, DomainReadiness]
    data_gaps: tuple[DataGap, ...]
    conflicts: tuple[Conflict, ...]
    evidence_refs: EvidenceRefs

@dataclass(frozen=True)
class NormalizedHotMoneyBundle:
    request: PackRequest
    dragon_tiger: tuple[DragonTigerEntry, ...]
    fund_flow: tuple[FundFlowSnapshot, ...]
    compact_facts: Mapping[str, Any]
    domain_status: Mapping[str, DomainReadiness]
    data_gaps: tuple[DataGap, ...]
    conflicts: tuple[Conflict, ...]
    evidence_refs: EvidenceRefs

@dataclass(frozen=True)
class NormalizedLockupBundle:
    request: PackRequest
    unlock_events: tuple[LockupUnlockEvent, ...]
    chip_snapshots: tuple[ChipStructureSnapshot, ...]
    compact_facts: Mapping[str, Any]
    domain_status: Mapping[str, DomainReadiness]
    data_gaps: tuple[DataGap, ...]
    conflicts: tuple[Conflict, ...]
    evidence_refs: EvidenceRefs
```

### 10.3 缺失和异常处理

| 情况 | ProviderStatus | DataGapReason | worker 材料表达 |
|---|---|---|---|
| 字段缺失 | `field_missing` | `field_missing` | “缺少某字段，无法判断该项” |
| schema invalid | `schema_invalid` | `schema_invalid` | “来源结构变化，未纳入事实判断” |
| empty | `empty` | `empty` | “该来源未返回相关记录” |
| rate limited | `rate_limited` | `rate_limited` | “来源限流，本轮覆盖不足” |
| credential missing | `credential_missing` | `credential_missing` | “来源未配置凭证，本轮未调用成功” |
| cached empty | `cached_empty` | `cached_empty` | “缓存记录为空，不作为覆盖成功” |
| stale cache | `cache_stale` | `stale_cache_unusable` | “缓存过期，未静默使用” |
| evidence write failed | `evidence_write_failed` | `evidence_write_failed` | 阻断或缺口，不得让 worker 看到成功材料 |

raw/debug/cache 对象只能保存为证据 ref，不得成为 worker 主材料。

### 10.4 三新增 pack 图表最少清单

图表原则：有适合结构化呈现的数据时必须给 chart ref；没有合适数据时必须给 `chart_readiness` 和 `root_cause`。不得生成假图，不得把“接口失败/字段不足”静默处理成“本期无需图表”。

| pack | 必须图 | 可选图 | 合理无图原因 |
|---|---|---|---|
| `policy` | Phase 1 不强制必须图。若同一时间窗内有 2 条以上带日期的政策/公告事件，必须生成政策事件时间线或事件表格 ref | 按政策层级/影响方向统计的条形图；政策影响行业分布 | 官方源失败；只有 0-1 条可核验事件；事件没有可靠日期；仅新闻/搜索线索不足以画事实图 |
| `hot_money` | 资金流成功且有 2 个以上时间点时，必须生成主力资金净流入趋势图；若只有龙虎榜成功且有席位金额，必须生成龙虎榜净买入席位条形图 | 北向资金趋势、板块资金排名、题材热度排名 | required groups 均为空；只返回单点且不适合趋势图；金额/日期/单位缺失；缓存范围不足 |
| `lockup` | 解禁成功且有未来/近期限售记录时，必须生成解禁规模日历/柱状图；若股东户数有 2 期以上，必须生成股东户数变化趋势图 | 融资融券余额趋势、大宗交易金额分布、分红事件表 | 无近期限售；只有单期股东户数；解禁数量/日期缺失；官方核验缺失导致只能写缺口 |

验收规则：

- `DomainPackResult` 必须包含 `chart_refs` 或 `chart_readiness.non_ready_reason`。
- `reader_brief_md` 只能引用真实存在的 chart ref；不能写“见图”但 evidence/assets 缺失。
- 图表所用字段必须能追溯到 `openbb_normalized` 和 source refs，且保留单位、日期和来源。
- 缺图原因必须进入 pack audit，final report 可以展示为“本资料包未生成图表，原因：...”。

## 11. Worker prompt/material 边界设计

### 11.1 新增 worker 输入材料

| worker | model-visible 工具 | 主输入 | 输出 |
|---|---|---|---|
| `policy_analyst` | `claw_get_policy_pack` | 政策资料包自然语言正文、缺口、source refs | 政策影响 L1 |
| `hot_money_tracker` | `claw_get_hot_money_pack` | 游资/资金资料包自然语言正文、缺口、source refs | 资金/短线结构 L1 |
| `lockup_watcher` | `claw_get_lockup_pack` | 解禁/筹码资料包自然语言正文、缺口、source refs | 筹码/供给压力 L1 |

OpenViking material 写入不是新增三 worker 的 model-visible 工具。worker 只返回自然语言 L1；claw-trade 在 artifact approval 后执行 materialization/write，并保留 OpenViking receipt、manifest、hash、lineage。验收时以 `openclaw_llm_provider_payload.tools` 为准：新增三 worker 的模型可见工具集合必须分别严格等于一个 canonical pack tool。

### 11.1A 三新增 worker prompt 迁移合同（Phase 1 必须满足）

#### baseline 来源与证据

每个 worker 的 prompt 迁移都必须绑定 baseline 证据，不允许用新编工程 checklist 代替：

固定 baseline commit：`../TradingAgents-astock` 当前固定为 `661ccffa812f5182079f604838d4eea3b4abc7ea`。下列文件只作为 prompt/角色口径参考，不照搬 Python/LangGraph 结构，不让 worker 直连工具。

| worker | TradingAgents-astock baseline 文件 | baseline 来源（必须二选一或同时） | 必需证据 |
|---|---|---|---|
| `policy_analyst` | `../TradingAgents-astock/tradingagents/agents/analysts/policy_analyst.py` | 政策分析角色原文；`TradingAgents-CN` 前线报告风格原文 | baseline 原文文件路径/commit、对应 provider payload、同 run 的 worker L1 输出 |
| `hot_money_tracker` | `../TradingAgents-astock/tradingagents/agents/analysts/hot_money_tracker.py` | 龙虎榜/资金跟踪角色原文；`TradingAgents-CN` 报告风格原文 | baseline 原文文件路径/commit、对应 provider payload、同 run 的 worker L1 输出 |
| `lockup_watcher` | `../TradingAgents-astock/tradingagents/agents/analysts/lockup_watcher.py` | 解禁/筹码观察角色原文；`TradingAgents-CN` 报告风格原文 | baseline 原文文件路径/commit、对应 provider payload、同 run 的 worker L1 输出 |

#### 目标文件结构（每个 worker）

必须落在 `agents/<worker>/`，且结构与现有 worker 一致：

- `agents/<worker>/IDENTITY.md`
- `agents/<worker>/USER.md`
- `agents/<worker>/STAGES.yaml`
- `agents/<worker>/SKILLS.md`
- `agents/<worker>/TOOLS.md`

不允许把业务 prompt 写回 Python 控制层或 `workflow/**`。

#### 允许变量（仅运行时替换）

仅允许替换以下运行变量：

- `ticker`、`company_name`、`market`、`currency`
- `start_date`、`end_date`、`current_date`
- 已批准上游 L1 正文变量（下游阶段）

不得新增“控制流检查表变量”“协议块变量”或“内部调试对象变量”作为 prompt 主体。

#### 禁止工程协议文本（model-visible）

provider payload 的 model-visible messages 中禁止出现：

- `RuntimeTarget`、`ReportSubmission`、`[ApprovedMaterials]`
- `material_id`、`capability`、URI/hash/receipt/L1/L2 协议段
- OpenClaw/OpenViking/OpenBB debug 包装、attempt JSON、cache envelope

#### provider payload 对齐验收

每个 worker 至少提供 1 次 live run 对齐证据，包含：

1. `openclaw_llm_provider_payload`：看到的最终 prompt 与 tool schema；
2. 同 call 的 `openbb_provider_http_evidence` / `openbb_provider_raw_evidence` refs；
3. 同 worker 的 L1 输出。

三者必须 run_id + dispatch_id + worker_id 一致，且可追溯到同一 call。

#### LLM 输出风格对比验收

每个 worker 需提供“baseline vs 新实现”对比，至少覆盖：

- 角色语气是否保留（政策解读/资金博弈/筹码压力）；
- 是否仍为自然语言研报段落，不是工程审计 checklist；
- 是否允许 evidence-supported 明确判断；
- 缺口表达是否只陈述缺失事实，不改成保守 memo 模板。

任何让输出更“审慎备忘录化”的漂移都属于产品变更，需单独人类批准。

`STAGES.yaml` 设计：

```yaml
stage: frontline
profiles:
  CN_A:
    approved: true
    tools:
      - claw_get_policy_pack
    materialization:
      openviking_write_after_approval: true
```

`hot_money_tracker` 使用 `claw_get_hot_money_pack`，`lockup_watcher` 使用 `claw_get_lockup_pack`。

model-visible tool schema 统一规则：

- 只能出现 canonical pack 工具名：`claw_get_policy_pack` / `claw_get_hot_money_pack` / `claw_get_lockup_pack`。
- 不得出现 OpenViking write/read、OpenBB atomic/admin/discovery、Mongo/cache/raw/debug 或旧 provider alias 工具。
- `cn_a_policy_data`、`cn_a_hot_money_data`、`cn_a_lockup_data` 这类名称若在内部代码中保留为兼容别名，只能用于内部映射，不得进入 OpenClaw provider payload 的 `tools` 或 `messages`。
- 验收以 `openclaw_llm_provider_payload` 为准；任何 alias 出现在 payload 即判定不符合设计。

### 11.2 下游读取 7 份 approved L1

下游 CN_A worker prompt variable 应包含：

```python
frontline_reports = {
    "market_analyst_report": approved_l1("market_analyst"),
    "fundamental_analyst_report": approved_l1("fundamental_analyst"),
    "news_analyst_report": approved_l1("news_analyst"),
    "social_analyst_report": approved_l1("social_analyst"),
    "policy_analyst_report": approved_l1("policy_analyst"),
    "hot_money_tracker_report": approved_l1("hot_money_tracker"),
    "lockup_watcher_report": approved_l1("lockup_watcher"),
}
```

Python 只能搬运 approved L1 原文，不能摘要、改写、压缩或生成投资判断。

### 11.3 prompt 禁止内容

新增 worker 和下游 worker prompt 不得包含：

- OpenBB raw/debug/cache envelope。
- provider attempt 机器 JSON。
- OpenViking URI/hash/material_id/capability 协议文本。
- OpenClaw runtime wrapper prose。
- Python 控制层 checklist。
- “禁止强观点/禁止建议/只能保守措辞”等非 baseline 风格约束。

### 11.4 TradingAgents-CN 报告感和 A股特色

新增 worker 应保持：

- 自然语言研报段落，不是机器审计报告。
- 明确的政策、资金、筹码判断，但所有事实必须来自 pack 或 approved 上游。
- 缺数据时写缺口影响，不补事实。
- 后续多空、研究经理、交易员、风险辩论和 PM 应能引用 A股特色 L1 作为论据。

## 12. UI 设计

本章只定义产品/接口，不写前端代码。

### 12.1 设置页

设置页按市场和数据域展示：

```text
CN_A
  market
  fundamental
  news
  social
  policy
  hot_money
  lockup
```

每个 coverage group 展示：

- 覆盖组名称和中文说明。
- source role。
- 系统默认源列表。
- 用户配置源列表。
- 启用状态。
- 同组优先级。
- 最近测试状态和最近成功时间。
- credential 状态。
- license/raw export policy 状态。
- 是否 official_original，若是则显示“事实权威，不允许普通源替代”。

后端接口建议：

```python
GET /api/ui/data-sources?market=CN_A
POST /api/ui/data-sources/declarative
POST /api/ui/data-sources/{adapter_id}/validate
POST /api/ui/data-sources/{adapter_id}/enable
POST /api/ui/data-sources/{adapter_id}/disable
POST /api/ui/data-sources/priority
```

### 12.2 运行页/报告详情

运行详情展示：

- 本次 run 的 `provider_config_version`。
- 每个 domain 的 readiness。
- 每个 coverage_group 的实际尝试顺序。
- 用户源失败后尝试的系统源。
- 每个失败的 root cause。
- 进入 worker materials 的 normalized/source refs。
- 哪些关键事实来自 official_original。
- data gaps 对报告判断的影响。

接口建议：

```python
GET /api/ui/reports/{run_id}/provider-trace
```

返回 DTO 只包含读者/审计可见摘要和 ref，不返回 raw payload、token、headers。

## 13. 数据流和证据链

### 13.1 `/report` 到 final report

```text
1. 用户从 chat 输入 /report。
2. claw-trade 创建 RunRequest，entry_point=report_command。
3. RunProviderPlanner 为 CN_A 生成 7 域 RunProviderPlan，remote_prefetch_allowed=false。
4. workflow 按 CN_A stage plan 唤醒 7 个 frontline OpenClaw worker。
5. 每个 worker 的 model-visible tool schema 只包含本域 canonical pack tool。
6. worker 调用 pack tool。
7. OpenBB/data_gateway 根据 run plan 执行 provider candidates。
8. 每个 provider attempt 写 Mongo：HTTP evidence、raw ref、normalized ref、cache receipt、attempt。
9. pack builder 生成 reader_brief、data_gaps、readiness、source refs、audit refs。
10. worker 基于自然语言 pack 写 L1 报告。
11. claw-trade approval 后执行非模型可见的 OpenViking materialization，写 approved material、manifest、hash、lineage。
12. downstream worker 读取 7 份 approved L1 原文和后续辩论材料。
13. portfolio_manager 输出最终自然语言决策 L1。
14. workflow 进入 `final_report` stage，唤醒 `report_polisher` 生成终稿 L1；`report_polisher` 不改写 PM 决策权威，只整理已批准材料。
15. exporter 只导出 approved final_report/material，不补事实、不改 PM 结论。
16. UI 展示最终报告和 provider trace。
```

### 13.2 证据对象与存储

| 步骤 | 证据 | 存储建议 |
|---|---|---|
| run plan | `RunProviderPlan` | Mongo `openbb_run_provider_plans` |
| provider HTTP | `OpenBBProviderHttpEvidence` | Mongo `openbb_provider_http_evidence` |
| raw payload | raw hash/ref | Mongo `openbb_raw_payloads` 或对象存储 |
| normalized | `NormalizedResult`/bundle | Mongo `openbb_normalized` |
| attempt | `ProviderAttempt` | Mongo `openbb_provider_attempts` |
| cache | `CacheReceipt` | Mongo `openbb_provider_cache` |
| pack audit | `PackAuditPayload` | Mongo/evidence store，ref 进入 result |
| LLM payload | final messages/tool schema | run evidence `calls/*/provider_payload*` |
| approved L1 | material body + manifest | OpenViking |
| final report | Markdown/assets | `runs/<run>/reports/**` + OpenViking refs |

### 13.3 不混淆规则

- OpenClaw LLM provider payload 证明“模型看到什么 prompt 和工具”。
- OpenBB HTTP/raw evidence 证明“外部数据源调用和原始数据是什么”。
- Mongo normalized/cache refs 证明“数据如何被归一化和缓存”。
- OpenViking manifest 证明“哪些 worker 报告被批准并传给下游”。

这四类证据不能互相替代。

## 14. 测试与验收设计

### 14.0 fixture/sample 最低要求

本章测试名是后续实现目标；每类测试至少需要下列样本，不能只用空对象或随手 mock。

| 样本类型 | 最低内容 | 用途 | 验收证据 |
|---|---|---|---|
| provider response sample | 每个 Phase 1 计划源至少 1 个成功响应；包含请求参数、状态码、关键字段、单位、时间戳和来源 URL/hash | normalizer、reader_brief、chart refs | fixture 文件 + `openbb_provider_http_evidence`/raw ref 对照 |
| field_missing sample | 每个新增 pack 至少 1 个缺关键字段响应：policy 缺 `published_at/source_role`，hot_money 缺金额单位或日期，lockup 缺 `unlock_date/as_of` | 缺字段 gap 和 readiness 降级 | pytest 输出 + `DataGapReason.FIELD_MISSING` |
| official fail sample | `cn_a_policy_official` 的官方源失败、空返回或 schema invalid 样本，同时有新闻/搜索线索成功样本 | 证明官方失败不得 fully ready，新闻/搜索不能替代官方事实 | attempts 快照 + reader_brief 缺口段 |
| cache sample | `cache_hit/cache_stale/cached_empty/cache_error` 各 1 个状态样本 | 证明缓存状态不伪装成 fresh success | cache receipt + readiness/gap 输出 |
| priority/conflict sample | 同组系统源、用户源、官方源混合排序样本；至少一个用户源失败后系统源成功 | priority 作用域和替换链 | `openbb_run_provider_plans` + attempts 顺序 |
| chart sample | hot_money 有 2 个以上资金流日期；lockup 有解禁事件或 2 期股东户数；policy 有 2 条以上日期事件；另各有一个不适合画图样本 | 图表生成与无图原因 | chart ref/assets 或 `chart_readiness.non_ready_reason` |
| provider payload sample | 三新增 worker 各 1 个真实 provider payload 样本 | 验证 prompt/tool schema 边界 | `runs/<run_id>/calls/*/provider_payload*.json` |
| baseline comparison sample | 三新增 worker baseline 文件片段、目标 prompt、同 run L1 输出 | 验证 TradingAgents-astock 角色口径和 CN_A 报告感 | baseline 对照记录 + L1 输出 |

### 14.1 单元测试矩阵

| 测试 | 输入 | 验证点 | 期望输出 | 证据路径/collection | 命令 |
|---|---|---|---|---|---|
| `test_pack_domain_cn_a_extensions` | `PackDomain` 新增三域 | CN_A-only 校验 | `policy/hot_money/lockup` 仅 `CN_A` 合法 | pytest 输出 | `uv run pytest tests/unit/data_gateway/test_models.py -k pack_domain_cn_a_extensions` |
| `test_provider_registry_priority_scope` | 同/不同 `(market,domain,source_role,coverage_group)` 候选集 | priority 作用域 | 用户优先只在同四元组内生效 | pytest 输出 + 排序快照 | `uv run pytest tests/unit/data_gateway/test_provider_registry.py -k priority_scope` |
| `test_official_original_attempt_semantics` | 同组 `official_original + USER_PREFERRED`，官方失败样本 | 官方原文 attempt 语义 | `official_original` 必有 attempt；失败写 gap；搜索源不能冒充官方事实 | `openbb_provider_attempts` 样本、pytest 输出 | `uv run pytest tests/unit/data_gateway/test_provider_registry.py -k official_original_attempt` |
| `test_cache_status_not_remote_success` | `cache_hit/cache_stale/cached_empty/cache_error` | 状态机语义 | 不得转成 `remote_success` | pytest 输出 | `uv run pytest tests/unit/data_gateway/test_cache_state_machine.py` |
| `test_fundamental_peg_fields_cn_a` | 一致预期 EPS、机构覆盖数、预测增速、当前价样本；另含缺 EPS/缺增速样本 | PEG/估值消化字段 | forward PE/PEG/PE 消化有 refs；缺核心输入时写 gap，不补算 | `openbb_normalized` 样本 + pytest 输出 | `uv run pytest tests/unit/data_gateway/test_fundamental_pack_cn_a.py -k peg` |
| `test_policy_normalizer_source_roles` | policy official/search 样本 | source_role 边界 | `search_discovery` 不进入主事实层 | pytest 输出 | `uv run pytest tests/unit/data_gateway/test_source_roles.py -k policy` |
| `test_hot_money_amount_units` | 龙虎榜/资金流样本 | 单位与币种 | 金额字段含单位，默认 `CNY` 或明确来源货币 | pytest 输出 | `uv run pytest tests/unit/data_gateway/test_hot_money_normalizer.py` |
| `test_lockup_required_fields` | 缺 `unlock_date/as_of` 样本 | 缺字段处理 | 产生 `field_missing` gap，不伪造事实 | pytest 输出 | `uv run pytest tests/unit/data_gateway/test_lockup_normalizer.py` |
| `test_phase1_no_new_runtime_guard_contract` | guard 文件清单快照 | Guard Freeze 合同 | Phase 1 不新增/收紧 runtime guard | pytest 输出 | `uv run pytest tests/contracts/test_guard_change_requires_approval.py` |

### 14.2 集成测试矩阵

| 测试 | 输入 | 验证点 | 期望输出 | 证据路径/collection | 命令 |
|---|---|---|---|---|---|
| `test_policy_pack_cn_a` | CN_A run plan + policy provider fixtures | pack 结构与读者正文 | `reader_brief_md` 为自然语言，含 readiness/gaps/refs | `openbb_provider_attempts`、`openbb_normalized` | `uv run pytest tests/integration/data_gateway/test_policy_pack_cn_a.py` |
| `test_hot_money_pack_cn_a` | 龙虎榜/资金流多候选状态 | coverage_group 执行与替换链 | 用户源失败后继续同组系统源，attempt 完整 | `openbb_provider_attempts`、`openbb_provider_http_evidence` | `uv run pytest tests/integration/data_gateway/test_hot_money_pack_cn_a.py` |
| `test_lockup_pack_cn_a` | 解禁/股东户数/大宗样本 | normalization + gaps | bundle 字段完整；缺口可解释 | `openbb_normalized`、`openbb_provider_attempts` | `uv run pytest tests/integration/data_gateway/test_lockup_pack_cn_a.py` |
| `test_mcp_pack_endpoints_cn_a_extensions` | OpenBB runtime wrapper app | route 与 tool schema | 仅 7 个 canonical pack endpoint/tool（含三新域） | wrapper audit + pytest 输出 | `uv run pytest tests/integration/data_gateway/test_mcp_pack_endpoints.py -k cn_a_extensions` |
| `test_run_provider_plan_cn_a_domains` | CN_A `/report` request | run plan 冻结 | 7 域、`remote_prefetch_allowed=false`、写入 `provider_config_version` | `openbb_run_provider_plans` | `uv run pytest tests/integration/data_gateway/test_run_provider_plan_snapshot.py -k cn_a_domains` |
| `test_active_run_provider_config_version_not_hot_reloaded` | run 中 UI 修改 provider catalog | active run 版本语义 | active run 使用旧 version；新 version 仅下一次 run 生效 | `openbb_run_provider_plans` + run trace | `uv run pytest tests/integration/data_gateway/test_run_provider_plan_snapshot.py -k not_hot_reloaded` |

### 14.3 Provider 准入与安全测试矩阵

| 测试 | 输入 | 验证点 | 期望输出 | 证据路径/collection | 命令 |
|---|---|---|---|---|---|
| `test_declarative_provider_policy_enabled_candidate` | policy manifest | admission 状态机 | `validated -> enabled_candidate` 后可进 registry | `openbb_provider_validation_receipts` | `uv run pytest tests/unit/data_gateway/test_provider_admission.py -k enabled_candidate` |
| `test_declarative_provider_ssrf_block` | `localhost/private IP/redirect` 样本 | SSRF 策略 | `rejected` 或 `quarantined`，不可进 run plan | validation receipt + pytest 输出 | `uv run pytest tests/unit/data_gateway/test_provider_admission_security.py -k ssrf` |
| `test_declarative_provider_code_upload_rejected` | code/plugin manifest | 禁止代码型 provider | 直接拒绝 | validation receipt + pytest 输出 | `uv run pytest tests/unit/data_gateway/test_provider_admission_security.py -k code_upload` |
| `test_declarative_provider_license_blocked` | license 不合规样本 | 许可边界 | `license_blocked`，不可进 run plan | validation receipt + pytest 输出 | `uv run pytest tests/unit/data_gateway/test_provider_admission.py -k license_blocked` |

### 14.4 Prompt 与 provider evidence 对齐 live proof

| 验收项 | 输入 | 验证点 | 期望输出 | 证据路径/collection | 命令 |
|---|---|---|---|---|---|
| 三新增 worker tool schema 对齐 | CN_A `/report 600519` live run | model-visible tools | `policy_analyst/hot_money_tracker/lockup_watcher` 仅见对应 canonical tool；不含 `cn_a_*` alias | `runs/<run_id>/calls/*/provider_payload*.json` | `scripts/start-control-runtime.sh -- uv run python scripts/run_claw_trade_fresh_report.py --market CN_A --ticker 600519` |
| prompt baseline 对齐 | 同一 live run + baseline 对照 | prompt 内容边界 | provider payload 中无工程协议块；角色语气与 baseline 一致 | provider payload + baseline 对照记录 | 同上 + `uv run pytest tests/contracts/test_prompt_alignment_cn_a_workers.py` |
| provider evidence audit | 同一 live run | 证据不混淆 | `openclaw_llm_provider_payload` 与 `openbb_provider_http_evidence/raw` 分离，run/dispatch/call 可对齐 | `openbb_provider_http_evidence`、`openbb_raw_payloads`、run calls evidence | `uv run pytest tests/contracts/test_provider_evidence_audit_boundary.py` |
| 图表与 root cause 验收 | 同一 live run | charts when available | 有图表则有 chart ref；缺图必须有 `chart_readiness/root_cause`，不得假图或静默成功 | `runs/<run_id>/reports/assets`、pack audit、`openbb_normalized` | `uv run pytest tests/integration/data_gateway/test_market_pack_cn_a.py -k chart_readiness` |

### 14.5 CN_A 全链路 live/fresh 验收矩阵

输入：`/report 600519`（或人类批准的 CN_A 样本）。  
前置：通过 `12.1 Live Runtime Preflight Gate`，并使用：

```bash
scripts/start-control-runtime.sh -- uv run python scripts/run_claw_trade_fresh_report.py --market CN_A --ticker 600519
```

| 验收项 | 输入 | 验证点 | 期望输出 | 证据路径/collection | 命令 |
|---|---|---|---|---|---|
| 7 个 frontline worker 真实运行 | CN_A fresh run | OpenClaw call evidence | 7 个 frontline 都有真实 call/result/provider payload | `runs/<run_id>/calls/*` | 上述 fresh run 命令 |
| 三新增 worker tool schema | 同一 run | model-visible tools | `tools` 严格等于各自唯一 canonical pack tool；不含 OpenViking write/read 或 alias | `runs/<run_id>/calls/*/provider_payload*.json` | `uv run pytest tests/contracts/test_provider_payload_visible_tools.py -k cn_a_new_workers` |
| 三新增 pack provider evidence | 同一 run | OpenBB provider 调用 | attempts/http/raw/normalized/cache evidence 都可追溯 | Mongo `openbb_provider_attempts/openbb_provider_http_evidence/openbb_raw_payloads/openbb_normalized/openbb_provider_cache` | `uv run pytest tests/contracts/test_provider_evidence_audit_boundary.py -k cn_a_new_packs` |
| OpenViking approved L1 | 同一 run | material authority | 7 份 frontline approved L1 manifest/readback/hash 完整 | OpenViking manifest/readback + `runs/<run_id>/artifacts` | `uv run pytest tests/integration/data_gateway/test_openviking_lineage.py -k cn_a_frontline` |
| 下游 prompt 材料 | 同一 run | approved L1 注入 | bull/bear/research/trader/risk/PM prompt 可见 7 份 approved L1 原文，不见 raw/debug/cache envelope | downstream provider payload | `uv run pytest tests/contracts/test_prompt_material_boundary.py -k cn_a_downstream` |
| final report A股特色覆盖 | 同一 run | 读者报告内容 | final report 含政策/资金/筹码分析；缺口不编造 | `runs/<run_id>/reports/final-report.md` + evidence chain | `uv run pytest tests/contracts/test_final_report_evidence_chain.py -k cn_a_astock` |
| 图表/root cause | 同一 run | chart readiness | 有图表则有 chart ref；缺图必须有 root cause，不假图、不静默成功 | `runs/<run_id>/reports/assets` + pack audit | `uv run pytest tests/integration/data_gateway/test_market_pack_cn_a.py -k chart_readiness` |

Collect-first 报告必须附：

- batch scope
- completed items
- failures collected
- early-stop exception used: yes/no
- exception evidence（如有）
- batch fix grouping

### 14.6 Negative 测试矩阵

| 测试 | 输入 | 验证点 | 期望输出 | 证据路径/collection | 命令 |
|---|---|---|---|---|---|
| `test_no_legacy_provider_path` | 全仓扫描 + OpenBB flag 开启运行 | 旧路径阻断 | 无 `frontline_data_pack.provider_executor`、无旧 executor 调用 | grep/pytest 输出 | `rg -n \"frontline_data_pack\\.provider_executor|provider_executor\" src openclaw_plugins agents` |
| `test_no_silent_fallback_after_openbb_failure` | 人工构造 provider 失败 | fallback 边界 | 产生 attempt + gap，不调用旧路径 | `openbb_provider_attempts` + run trace | `uv run pytest tests/integration/data_gateway/test_openbb_no_fallback.py` |
| `test_official_original_not_overridden` | 用户源 + 官方源冲突样本 | 官方原文保护 | 官方源仍被尝试，用户源不覆盖事实权威 | attempts + pytest 输出 | `uv run pytest tests/unit/data_gateway/test_provider_registry.py -k official_original_not_overridden` |
| `test_no_atomic_admin_discovery_in_payload` | frontline live call payload | schema 边界 | payload 不含 OpenBB atomic/admin/discovery 工具 | `runs/<run_id>/calls/*/provider_payload*.json` | `uv run pytest tests/contracts/test_provider_payload_visible_tools.py` |
| `test_no_raw_debug_cache_envelope_in_prompt` | frontline + downstream payload | prompt 材料边界 | model-visible prompt 无 raw/debug/cache envelope | provider payload evidence | `uv run pytest tests/contracts/test_prompt_material_boundary.py` |
| `test_no_python_prefetch` | run plan 初始化 | 控制层边界 | run plan 后未发生 remote prefetch attempt | `openbb_run_provider_plans`、`openbb_provider_attempts` | `uv run pytest tests/integration/data_gateway/test_run_plan_no_prefetch.py` |

## 15. 分阶段实施计划

### Phase 0：编码前核对门

核对门目标：在 Phase 1 代码变更前确认已批准的架构边界、OpenBB 版本、SSRF 策略、baseline 位置和 provider 作用域。provider 真实字段、adapter 实测、pack endpoint 测试、readiness negative test、chart readiness contract 落地和 live/fresh provider payload 证明都属于 Phase 1 验收，不得倒灌为 Phase 0 阻断门。

| 冻结门项目 | 必要决策 | 关闭证据 |
|---|---|---|
| OpenBB pinned source | 已关闭；编码前只核对 URL/commit/tag 未漂移 | `docs/evidence/openbb-submodule-version.md` + `git submodule status third_party/openbb` |
| SSRF 策略 | 已关闭；用户声明式 provider 进入 live 前必须按该策略实现并测试，不得放松 | `docs/evidence/openbb-declarative-provider-security.md` |
| A股 provider 作用域 | 已关闭；按 7.2/7.2A 七域全量 provider 矩阵实施，不再做范围缩减版 | 设计附录/评审记录确认 market/domain/source_role/coverage_group |
| raw export policy/license | 作为 provider 接入证据逐源记录；不作为“是否先用文档源”的前置拍板 | 源级 license/raw export policy 记录 |
| prompt baseline | baseline 文件路径和 commit 已在 11.1A 固化；本地 `../TradingAgents-astock` 已存在；编码时提取原文并形成对照记录 | baseline 对照记录（源文件路径+commit+目标 prompt） |
| 图表最少清单 | 默认采用 10.4；编码前只确认哪些数据形态需要图表、哪些缺口需要 root cause；字段落地和样本测试属于 Phase 1 验收 | 设计记录确认 chart ref / non-ready root cause 规则 |
| official_original readiness | 已关闭；官方失败要标记失败和 root cause，不得 ready，但不阻断 workflow；negative test 属于 Phase 1 验收 | 设计记录确认阈值 |

强制规则：

1. 不得以样本尚未实测为由删减文档已列的七域 coverage_group。
2. 任一 provider 失败时记录 attempt/gap/readiness，而不是绕过 OpenBB 或静默 fallback。
3. Phase 1 仍必须遵守不新增 runtime guard 的边界。

### Phase 1：A股全量 provider 矩阵与 workflow 闭环

文件范围：

- `src/claw_trade/data_gateway/models.py`
- `src/claw_trade/data_gateway/mcp/runtime_wrapper.py`
- `src/claw_trade/data_gateway/packs/{policy,hot_money,lockup}.py`
- `src/claw_trade/data_gateway/providers/cn_a/{policy,hot_money,lockup}.py`
- `src/claw_trade/data_gateway/packs/{market,fundamental,news,social}.py`
- `src/claw_trade/data_gateway/providers/cn_a/**`
- `src/claw_trade/data_gateway/providers/defaults.py`
- `src/claw_trade/data_gateway/providers/run_plan.py`
- `src/claw_trade/workflow/workers.py`
- `src/claw_trade/config/tool_names.py`
- `agents/policy_analyst/**`
- `agents/hot_money_tracker/**`
- `agents/lockup_watcher/**`

函数范围：

- `frontline_workers_for_market`
- `stage_plans_for_market`
- `build_report_run_plan`
- `OpenBBRuntimeWrapper.get_pack`
- `PolicyPackBuilder.build`
- `HotMoneyPackBuilder.build`
- `LockupPackBuilder.build`
- `build_cn_a_system_capabilities`

测试范围：

- model/domain tests
- run plan tests
- pack endpoint tests
- 7 个 canonical pack endpoint/tool 的实现证明
- seven pack integration tests
- OpenBB pack endpoint/provider response tests
- chart readiness 字段合同、图表样本和 non-ready/root cause 样本测试
- official_original readiness negative tests 和 reader_brief 缺口样本
- provider adapter 成功、失败/空返回和字段漂移样本测试
- worker workspace/stage policy tests
- negative import/tool schema tests

停止条件：

- OpenBB pinned source 漂移、SSRF 策略被放松，或新增 pack 接口无法通过 OpenBB runtime 承载。
- 任一新增 pack 必须绕过 OpenBB 才能取数。
- 新 worker 不是 OpenClaw worker turn。
- 任一计划域无法写 provider attempt/raw/normalized/cache evidence。
- 任何新增或收紧 runtime guard（含 hard gate / 早停类别 / exporter rejection）未给出 `Guard source` + 人类批准 + 边界测试。

验收：

- CN_A `/report` 可产出 7 个 frontline L1，并在 `portfolio_manager` 后产出 `report_polisher/final_report` 终稿 L1。
- 七域 provider 矩阵按 7.2/7.2A 接入；每个计划 provider 都有 attempt、source_role、coverage_group 和 readiness/gap 证据。
- 文档列出的 A股源参考样本已转成 OpenBB/data_gateway adapter 实测证据，覆盖成功、失败/空返回、关键字段、单位和来源引用。
- 7 个 canonical pack endpoint/tool、`chart_readiness`、official_original negative readiness 和 OpenBB pack endpoint tests 通过。
- 用户声明式 provider 可在七个 CN_A domain 按声明作用域进入 enabled candidate 和 run plan。

### Phase 2：provider 稳定性与补强

文件范围：

- `src/claw_trade/data_gateway/providers/cn_a/**`
- `src/claw_trade/data_gateway/providers/defaults.py`
- `src/claw_trade/data_gateway/packs/**`
- `src/claw_trade/data_gateway/readiness.py`（若现有为 pack 内计算，则抽出或扩展等价函数）
- normalizer tests 和 provider live contract tests

函数范围：

- 每个 provider adapter 的 `capabilities/fetch/normalize`
- `compute_domain_readiness`
- `detect_conflicts`
- `render_*_reader_brief`

测试范围：

- coverage_group 矩阵 snapshot。
- schema drift/field_missing tests。
- official/search boundary tests。
- provider failure attempt tests。

停止条件：

- 某源许可不允许商业使用或 raw export policy 不明确。
- 某接口只能通过旧脚本直连。
- 搜索发现被当作公告/财报/资金事实。

验收：

- 已接入 provider 的字段漂移、限流、失败和替换链可追溯。
- provider 失效时记录真实失败原因。
- 旧 provider executor 不作为 fallback。

### Phase 3：UI 优先级与可观测性

文件范围：

- `src/claw_trade/ui_backend/data_source_settings.py`
- `src/claw_trade/ui_backend/data_source_health.py`
- `src/claw_trade/ui_contracts/**`
- 前端设置页和报告详情页相关文件

函数范围：

- `list_data_source_domains`
- `list_provider_coverage_groups`
- `save_user_declarative_provider`
- `validate_user_provider`
- `set_provider_priority`
- `get_run_provider_trace`

测试范围：

- UI API contract tests。
- 设置页 7 域展示 tests。
- priority reorder tests。
- run provider trace tests。
- credential/license 状态 redaction tests。

停止条件：

- UI 试图新增第二套 provider 后端。
- UI 允许普通用户上传代码型 provider 进入 live report。
- UI 展示 raw payload、token、HTTP headers。

验收：

- 设置页支持 7 个 CN_A 数据域。
- 同 coverage_group 内可管理优先级。
- 报告详情展示实际命中顺序、失败替换链、data gaps 和 official source 标识。

## 16. 人类拍板清单

本章只列不能由实现者自行改变的决策。Phase 1 当前目标是 A股七域全量 provider 矩阵、7-frontline workflow 闭环，以及 CN_A 在 PM 后必须进入 `report_polisher/final_report` 的终稿链路；商业/SaaS 等未来事项不得阻断当前 Phase 1。

### 16.1 仍需人类明确批准的偏离项

本节只放实现者不能自行改变的方向。已列入 7.2/7.2A 的 provider 作用域、Tushare 用户源语义、`astock-peg` 不引入、official_original 失败不阻断 workflow 都已按当前人类拍板收口；实现中的字段、样本、adapter 和 live proof 放入第 14 章测试矩阵和第 15 章 Phase 1 验收。

| 问题 | 默认设计 | 什么时候需要人类拍板 | 阻断范围 |
|---|---|---|---|
| 商业/SaaS 使用 OpenBB 和 A股源 | 当前范围按项目内研究工作流推进；商业分发、多用户展示和付费源托管另审 | 需要商业发布、SaaS、多用户数据展示或托管用户密钥 | 商业发布和商业 UI |
| `astock-peg` 是否引入 | 已拍板不引入；先用 fundamental estimates 的一致预期、forward PE、PEG/估值消化字段 | 人类未来明确要求引入该工程、固定版本、许可和字段口径 | fundamental 估值增强 |
| 新增或改变 worker 数量/阶段关系 | 当前只新增 `policy_analyst`、`hot_money_tracker`、`lockup_watcher` 三个 CN_A frontline worker；`report_polisher` 已按 2026-05-22 人类拍板成为 CN_A PM 后必须进入的终稿 worker，不计入 frontline | 想新增估值 worker、quality gate agent，或让 HK/CRYPTO/US 复用 A股新增 worker | workflow/stage plan |
| official_original 阈值偏离 | 官方失败要标记失败和 root cause，不得 ready，但不阻断 workflow | 想把官方失败改成阻断整个 worker，或想允许新闻/搜索命中后 ready | readiness/acceptance |
| provider 作用域偏离 | provider 必须按同一 market/domain/source_role/coverage_group 生效；用户 Tushare 等源只在声明作用域内优先 | 想跨 source_role/coverage_group 替代，或让新闻/搜索/模型推断冒充公告、财报、资金事实 | provider registry/run plan |

不列为开工前冻结门、但必须在 Phase 1 验收关闭的事项：

- 新增/扩展 7 个 canonical pack endpoint/tool 的实现证明。
- `chart_readiness` 字段落到 pack contract，并用图表与 non-ready/root cause 样本测试。
- official_original readiness negative test 和 reader_brief 缺口样本。
- OpenBB pack endpoint tests、provider adapter 成功/失败/空返回样本测试、provider response fixture 对照。

### 16.2 未来发布/未来扩展才需要关闭

| 问题 | 为什么不是 Phase 1 阻断项 | 需要拍板的时点 |
|---|---|---|
| 后续商业/SaaS 使用 OpenBB 和 A股源 | 当前范围只批准个人研究；商业分发、SaaS、多用户数据展示需要重新审 license 和源条款 | 商业发布前 |
| HK/CRYPTO 是否复用 A股新增 worker | 本设计只影响 CN_A；其他市场需要独立 prompt/data/source 策略 | 未来跨市场扩展前 |
| provider 深度补强 | Phase 1 已覆盖七域 provider 矩阵；后续只做字段稳定性、更多来源交叉验证和性能/限流优化 | 引入新 coverage_group 或改变事实角色前 |
| UI 商业化 provider 管理能力 | Phase 1 可先用后端 contract 和 evidence；商业 UI 要另审 credential、redaction、权限和审计 | Phase 3 或商业 UI 发布前 |

## 17. 总体停止条件

遇到以下任一情况必须停止并重新评审：

- 某个 A股源必须绕过 OpenBB 才能取数。
- 新增 worker 必须直接调用 provider 脚本才能工作。
- 需要新增第二套用户 provider 配置系统。
- 用户 provider 试图覆盖官方原始披露事实源。
- 缺数据时需要用新闻、搜索或模型推断冒充公告、财报、资金事实。
- 需要让 Python 控制层写投资判断或 PM 最终结论。
- 需要把 OpenBB atomic/admin/discovery tools 暴露给报告 worker。
- 需要恢复旧 provider executor 或旧 MCP 作为 runtime fallback。
- OpenClaw 被要求承担 CN_A DAG 或 provider 编排。
- OpenViking 被要求成为行情、新闻、公告、舆情 provider。
- 同一 gate category 在 focused fix 后连续失败两次。
