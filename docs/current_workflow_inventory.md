# 当前工作流清点

状态：待人工确认的草案。

本文档是 `迁移设计.md` 和旧仓库
`/home/frank/src/claw-invest/实施计划.md` 要求的 T0 清点结果。它记录旧
`claw-invest` 工作流当前实际做了什么，哪些内容值得作为证据保留，哪些内容需要
在 `claw-trade` 中重写，哪些内容不应迁移。

本文档不是实现计划，也不批准开始 scaffold。任何工作流 scaffold 或运行时实现开始
前，都需要先经过人工确认。

## 总体结论

旧仓库已经有一套有用的控制平面雏形：

- Python 负责工作流状态、dispatch 记录、worker 推进、artifact 接受和最终导出。
- 前四个 frontline worker 已经通过 OpenClaw wake request 路径执行。
- 后八个 worker 在报告路径中不是 OpenClaw-woken agent；它们走的是 `direct_llm`
  和 Python materialization。
- 即使 `agents/` 下已经存在 agent 文件，prompt 生成、stage policy、tool exposure
  和 prompt profile 选择仍然主要由 Python 持有。
- 旧证据文档和 provider payload capture 对后续对齐有价值，但旧 direct-LLM 执行
  路径是迁移债。

新的 `claw-trade` 方向应该保留已验证证据和有用的控制平面概念，围绕 OpenClaw
single-turn agent 重写 12 个 worker 的运行时边界，并将 direct-LLM /
Python-materialized 报告路径作为生产行为丢弃。

## 必答清点项

| 问题 | 旧仓库当前发现 | 迁移分类 |
| --- | --- | --- |
| 1. 当前状态机是什么？ | `workflow/records.py` 定义了从 `activating` 到 `completed` 的状态；`workflow/protocol.py` 推进 frontline、investment debate、investment decision、trade decision、risk debate 和最终 risk decision/PM。证据：`/home/frank/src/claw-invest/src/claw_invest/workflow/records.py:20`，`/home/frank/src/claw-invest/src/claw_invest/workflow/protocol.py:1601`，`:1622`，`:1651`，`:1665`。 | 重写。保留有序控制平面思路，但状态名称和 gate 要对齐 `claw-trade` 设计，尤其是 `portfolio_decision` 所有权。 |
| 2. dispatch 如何发布？ | `DispatchRecord` 存储 run、stage、worker、round/cycle、retry、worker stage manifest 和 brief refs。`_publish_dispatch` 存储 brief、固定 stage manifest、创建记录并发出 dispatch event。证据：`/home/frank/src/claw-invest/src/claw_invest/workflow/dispatches.py:22`，`:68`，`:89`，`/home/frank/src/claw-invest/src/claw_invest/workflow/protocol.py:1249`。 | 重写。保留 dispatch-as-authority 概念，但报告路径中的每个 dispatch 都必须映射到一次 OpenClaw wake。 |
| 3. OpenClaw 如何被唤醒？ | `runtime/dispatcher.py` 只允许 `frontline` 走 OpenClaw，并阻止 approved direct-LLM pair 进入 wake。`runtime/orchestrator.py` 通过 `gateway.wake_worker` 批量提交 wake request。`runtime/live_openclaw_runtime.py` 写入 current-stage settings 并调用 OpenClaw agent bridge。证据：`/home/frank/src/claw-invest/src/claw_invest/runtime/dispatcher.py:78`，`:86`，`:521`，`:562`，`/home/frank/src/claw-invest/src/claw_invest/runtime/orchestrator.py:682`，`:1897`，`/home/frank/src/claw-invest/src/claw_invest/runtime/live_openclaw_runtime.py:1025`，`/home/frank/src/claw-invest/scripts/openclaw_agent_bridge.mjs:63`。 | 重写。保留 gateway/wake 概念，但移除 frontline-only 边界，并证明 12 个 worker 全部由 OpenClaw 唤醒。 |
| 4. prompt 在哪里生成？ | Python 构造 enriched brief、extra system prompt、market profile、worker prompt view 和 later-stage direct prompt。证据：`/home/frank/src/claw-invest/src/claw_invest/runtime/openviking_artifacts.py:388`，`:1024`，`:1084`，`/home/frank/src/claw-invest/src/claw_invest/runtime/prompt_policy.py:123`，`:629`，`:748`，`/home/frank/src/claw-invest/src/claw_invest/runtime/tradingagents_prompt_templates.py:66`，`/home/frank/src/claw-invest/src/claw_invest/runtime/later_stage_prompt_materials.py:1066`，`:1153`。 | 重写。worker identity、user prompt template、skills 和 stage policy 必须迁到 `agents/<worker>/`；Python 只能替换运行时变量并传递已批准 artifact refs。 |
| 5. stage/tool policy 在哪里？ | `worker_stages.py` 解析 `STAGES.yaml`，其中包含 direct-LLM completion mode。`registry.py` 保存 worker/phase/tool policy 和 legacy fallback skill 行为。`dispatcher.py` 有硬编码 frontline report allowlist experiment。后八个 `agents/*/STAGES.yaml` 使用 `direct_llm_python_materialized`。证据：`/home/frank/src/claw-invest/src/claw_invest/runtime/worker_stages.py:16`，`:143`，`:184`，`/home/frank/src/claw-invest/src/claw_invest/agents/registry.py:98`，`:210`，`:258`，`/home/frank/src/claw-invest/src/claw_invest/runtime/dispatcher.py:56`。 | 重写/丢弃。保留 stage-scoped exposure 需求；direct completion mode、legacy fallback 和硬编码实验 allowlist 不应成为最终架构。 |
| 6. artifact 如何流转？ | Artifact 使用确定性 `viking://workflow/{run_id}/...` URI 和 accepted pointer。Protocol 提交 staged output、记录 evidence，并在 terminal worker event 后推进。下游 projection 使用结构化 report view。证据：`/home/frank/src/claw-invest/src/claw_invest/runtime/openviking_artifacts.py:202`，`:233`，`:299`，`:1223`，`/home/frank/src/claw-invest/src/claw_invest/workflow/protocol.py:1088`，`:1169`，`:1706`，`:1980`，`/home/frank/src/claw-invest/src/claw_invest/workflow/reports.py:750`。 | 重写。保留 artifact authority、traceability、validation 和 downstream projection 概念；除非重新批准，否则重命名或移除 OpenViking 特定假设。 |
| 7. direct LLM 在哪里使用？ | `APPROVED_DIRECT_STAGE_WORKERS` 覆盖全部后八个 worker。Dispatcher 为这些 pair 选择 `direct_llm`；orchestrator 调用 `run_direct_dispatch`；`direct_llm.py` 发送无工具 provider request；`direct_later_stage_materializer.py` 将 direct response 转为 artifact。CLI 和 web server 注入 direct client。证据：`/home/frank/src/claw-invest/src/claw_invest/runtime/later_stage_prompt_materials.py:18`，`/home/frank/src/claw-invest/src/claw_invest/runtime/dispatcher.py:78`，`/home/frank/src/claw-invest/src/claw_invest/runtime/orchestrator.py:1020`，`/home/frank/src/claw-invest/src/claw_invest/runtime/direct_llm.py:1`，`/home/frank/src/claw-invest/src/claw_invest/runtime/direct_later_stage_materializer.py:31`，`/home/frank/src/claw-invest/src/claw_invest/app/cli.py:19`，`/home/frank/src/claw-invest/src/claw_invest/web/server.py:15`。 | 作为生产报告路径丢弃。除非作为负面证据或测试证明其不存在，否则不要迁移此路径。 |
| 8. 应该保留什么？ | 证据文档、provider payload capture、worker list/phase/output registry facts、dispatch/artifact authority 概念、OpenClaw bridge/wake 证据和 prompt comparison material。 | 作为证据或设计输入保留，不作为可直接搬运的运行时代码。 |
| 9. 应该重写什么？ | State records、dispatch publication、OpenClaw gateway integration、prompt profiles、stage/tool policy、artifact validation、hard gates、provider payload capture 和 report export。 | 在 `claw-trade` 中按批准边界重写：Python 控制 workflow，OpenClaw 拥有 single-agent turn。 |
| 10. 应该丢弃什么？ | 后八个 `direct_llm`、Python direct materializer、Python-authored worker business prompts、fallback prompt/tool behavior、legacy fallback skills、hardcoded experiment allowlists、mandatory `alphaear-reporter`、旧 startup scripts 和未批准的 OpenViking assumptions。 | 丢弃，除非人工明确重新批准非常窄的 evidence-only 使用。 |

## 保留

| 项目 | 保留原因 | 边界说明 |
| --- | --- | --- |
| 旧证据文档 `/home/frank/src/claw-invest/docs/2026-05-02-*` | 它们比较了原始 TradingAgents、TradingAgents-CN、旧 runtime prompts、provider payload 和 worker outputs。 | 仅作为证据。它们不能证明新的 `claw-trade` runtime 行为。 |
| Provider payload capture 文件 `/home/frank/src/claw-invest/.runtime/prompt-capture/` | 它们展示旧 run 中实际 provider request messages，可指导 parity check。 | 新 capture 必须证明 `claw-trade` run 的最终 provider messages 和可见 tool schema。 |
| Worker ID 与 phase/output mapping | 旧 registry 枚举了必需的 12 个 worker 和大致 phase。 | 重新干净创建；不要导入 legacy fallback policy。 |
| Dispatch record 概念 | Dispatch 是 run/stage/worker authority 和 retry accounting 的合适单位。 | 新报告路径中的每个 dispatch 都应映射到一次 OpenClaw wake。 |
| Artifact pointer 与 accepted-output 概念 | 旧仓库区分 staged output、accepted output、evidence 和 downstream projection。 | 保留 authority model；如果 OpenViking 命名造成误导，应移除或重命名。 |
| OpenClaw bridge/wake 机制 | 旧 bridge 证明 Python dispatch 到 OpenClaw agent turn 存在可行路径。 | 泛化到全部 12 个 worker，不保留 direct-LLM exception path。 |

## 重写

| 领域 | 重写目标 |
| --- | --- |
| Workflow state machine | 建立干净的 `claw-trade` 状态和推进逻辑，覆盖 `frontline`、`investment_debate`、`investment_decision`、`trade_decision`、`risk_debate` 和 `portfolio_decision`。 |
| Dispatch publisher | 由 `claw-trade` 拥有 dispatch records、brief refs、idempotency、retries 和 stage metadata；每个 dispatch 对应一次 OpenClaw wake。 |
| Prompt ownership | Agent prompt 和 worker identity 放在 `agents/<worker>/`；Python 只替换运行时变量并提供已批准 artifact refs/summaries。 |
| Market profiles | 明确支持 US 和 CN_A；HK 与 CRYPTO 必须在策略获批后才运行。禁止 silent fallback。 |
| Stage/tool exposure | Stage policy 缩窄当前 turn 中 OpenClaw 可见的 skills/tools。Python hardcoded allowlist 只能作为临时实验，不能成为最终行为。 |
| Artifact validation | Canonical artifact validation、hash/URI traceability、hard gates 和 accepted-output promotion。 |
| Provider payload capture | 捕获最终 provider request messages、visible tool schema、worker/stage/run/dispatch/runtime metadata，以及可用时的 provider/request id。 |
| Report export | 只导出已批准 artifact，不重写 PM rating 或 final investment decision。 |

## 丢弃

| 项目 | 原因 |
| --- | --- |
| `direct_llm` report path | 它绕过 OpenClaw agent identity、skills 和 tool schema。 |
| `direct_later_stage_materializer.py` 作为生产路径 | 它允许 Python 从 direct provider text 中 materialize 后续阶段 worker artifact。 |
| 后八个 `direct_llm_python_materialized` stage completion | 它违反 12 个 worker 全部为 OpenClaw-woken agent 的要求。 |
| Python-authored worker business prompts | Prompt policy 属于 agent configuration，不属于 Python runtime prose。 |
| Silent prompt/profile/tool fallback | 它会伪造覆盖面，并隐藏 HK/CRYPTO/tool strategy 缺失。 |
| Hardcoded report experiment allowlists | 它不能证明最终 stage/profile policy ownership。 |
| Mandatory `alphaear-reporter` workflow dependency | `AGENTS.md` 明确不迁移。 |
| 旧 startup scripts 和 OpenViking-only assumptions | 除非重新批准，否则它们不属于新 clean migration 默认范围。 |

## 证据文档

后续 parity work 可参考这些旧仓库证据：

- `/home/frank/src/claw-invest/docs/2026-05-02-claw-all12-final-provider-prompts-full-evidence.md`
- `/home/frank/src/claw-invest/docs/2026-05-02-all12-runtime-prompts-full-evidence.md`
- `/home/frank/src/claw-invest/docs/2026-05-02-all12-three-version-final-prompt-comparison.md`
- `/home/frank/src/claw-invest/docs/2026-05-02-later8-runtime-prompt-alignment-audit.md`
- `/home/frank/src/claw-invest/docs/2026-05-02-frontline4-us-cn-final-prompt-response-experiment.md`
- `/home/frank/src/claw-invest/docs/2026-05-02-later8-runtime-prompts-full-evidence.md`

重要限制：部分证据文件是 runtime-rendered prompt comparison，不是 live provider
payload capture。只要 prompt conformance 是判断依据，就应优先使用 raw provider
payload evidence。

## 人工确认门

开始 scaffold 或实现前，人工需要确认：

- 旧 later-eight direct-LLM 路径是迁移债，不是可接受桥接方案。
- 新 control plane 应在 `claw-trade` 中重建，不应从 `claw-invest` 整体复制。
- Prompt/business policy 应放在 `agents/<worker>/`，Python 仅处理运行时变量和已批准
  artifact refs。
- HK 和 CRYPTO prompt strategy 在明确批准前仍视为未批准。
- 声称 prompt/runtime parity 前，必须有 provider payload capture。

## 停止条件

如果下一步任务需要做以下任一事项，先停止并询问：

- 修改 `third_party/openclaw`。
- 保留 later-stage `direct_llm` 作为报告路径。
- 让 Python 重写 PM rating 或 final investment decision。
- 添加 fallback prompt/profile/tool behavior。
- 放松 truthfulness、fabrication、source、chart 或 artifact hard gates。
- 未经明确批准就选择 HK 或 CRYPTO prompt strategy。
