# Prompt 对齐执行手册

## 目标

后续 worker prompt 要先像 TradingAgents-CN 或原版 TradingAgents，再承接 claw-trade 的证据边界。

本手册用于防止两类回归：

- AI 为了“安全”自作主张新增报告口吻类 guard，压扁报告感。
- AI 把 URI、hash、receipt、L1/L2、manifest、JSON claim、运行目标、提交协议等机器字段写进 worker prompt。

## 核心原则

1. CN_A prompt 以 TradingAgents-CN 为基线。
2. US prompt 以原版 TradingAgents 为基线。
3. guard 只守真实性红线，不负责塑造报告文风。
4. worker 可见材料必须优先是已批准的完整自然语言 report、debate 或 decision；artifact ref / capability 只作为追踪和必要深读入口。
5. 结构化 payload、provider attempts、cache、OpenViking 审计、manifest、receipt、hash 只属于运行层和审计层。
6. CN/原版是纯 prompt 推理的后续 worker，模型可见工具必须为空；OpenViking 读写由运行层和证据层处理，不能进入模型任务。
7. prompt 文件头部的 profile/frontmatter 是配置，不是 prompt 正文；真实 provider prompt 必须从角色正文直接开始。
8. 对 prompt、角色声线、辩论风格、交易建议表达和报告结构而言，原版对齐优先级高于未经批准的审慎、合规、memo 化或“更安全”的偏好。
9. 真实性红线只能防编造，不能变成禁止强观点、禁止 BUY/HOLD/SELL、禁止 final proposition、禁止目标情景、禁止风险判断或禁止辩论语气的风格系统。
10. US 和 CN_A 的模型可见材料边界不同，不能用一个公共边界互相覆盖。US 必须对齐英文原版 TradingAgents；CN_A 必须对齐 TradingAgents-CN。

## Profile 材料边界

### US / original TradingAgents

- 后续 worker 的模型可见材料按英文原版 TradingAgents 处理。
- `research_manager` 的 provider-visible prompt 围绕 bull/bear debate history 做裁决；不要把四份前台报告作为独立综合材料再次塞进它的 prompt。
- 四份前台报告仍可作为后台审计材料、manifest refs、memory 检索材料或证据链存在，但这些不等于模型可见 prompt 变量。

### CN_A / TradingAgents-CN

- CN_A 保持 TradingAgents-CN 的材料边界。
- `research_manager` 可以直接看到综合分析报告：市场、情绪、新闻、基本面，再结合辩论历史做中文投资决策。
- 不得为了修 US 的 transcript 体积，把 CN_A 已对齐的综合报告输入砍掉。

## 每个 Worker 的准备流程

### 1. 先抓基线

每个 worker prompt 迁移前，必须先准备：

- `baseline_source`：TradingAgents-CN 或原版 TradingAgents 的源 prompt 或真实 final prompt。
- `baseline_output`：对应 worker 的真实输出；如果上游项目没有完整报告，记录真实停止状态。
- `claw_provider_final_prompt`：claw-trade fresh run 中真实 provider payload 捕获的 final prompt。
- `claw_llm_back_or_report`：同一 run 的 LLM back、worker report 或 raw output。

不得用静态渲染、手写摘要、导出报告或日志重构文本冒充 provider final prompt。

### 2. 最小替换

保留基线中的：

- 角色身份。
- 报告章节。
- 推理任务。
- 多空、风险、决策语气。
- 读者面对的研究报告感。
- 原版允许的强观点表达、直接反驳、final proposition、交易建议和辩论室语气。

只允许替换：

- ticker、company name、market、currency、date range 等运行变量。
- 当前 profile 已批准的工具或材料读取方式。
- claw-trade 必需的“不编造、缺证据说明”底线；该底线只能约束事实真实性，不能改变角色口吻或压低观点强度。
- PM 最终裁决权边界。

### 3. 禁止项

不得写入 worker profile prompt：

- `RuntimeTarget`、`ReportSubmission`、`OpenVikingWriteTarget`。
- URI、viking URI、hash、receipt、L1/L2、manifest、material id 等机器协议字段。
- JSON claim block、投资裁决 JSON block、provider payload 审计说明。
- provider attempts、Mongo cache、OpenViking raw payload、tool-call log。
- 为了让测试通过而新增的工程 checklist。
- 未经人类批准的报告口吻类 guard 规则。
- 自作主张新增的“更审慎”“更合规”“更像 memo”“避免 final recommendation”“禁止 final transaction proposal”“默认写 limitation report”等表达层限制。

不得出现在真实 provider prompt：

- OpenClaw 运行包装句，例如“你正在执行当前分析师的一轮任务”。
- profile/frontmatter，例如 `profile:`、`profile_status:`、`worker_id:`、`stage:`。
- `[ApprovedMaterials]`、`material_id`、`capability`、`l1_sha256`、`l2_available`、`call_id` 这类材料目录字段。
- `OpenViking`、`OpenClaw`、`openviking_read_with_capability`、`openviking_write_material`、`viking://`。

这些字段只能保留在 command payload、approved manifest、receipt、provider evidence、visible tools evidence、debug logs 或测试断言中。

### 4. Guard 边界

允许无额外审批的 guard 变更仅限真实性红线：

- fabricated facts。
- fake source / fake news / fake sentiment。
- unsupported PE/PB/ROE。
- unsupported target-price claim。
- fake chart output。
- fake tool success。
- Python rewrite of PM rating or final conclusion。
- provider payload、visible tools、tool calls、receipt、artifact authority 的完整性失败。

需要先停下询问人类的 guard 变更：

- 会影响评级、目标价、交易建议、风险措辞、情绪判断、投资观点强弱、报告语气的规则。
- 会把“有依据的分析师观点”当作 hard fail 的规则。
- 会让输出更像工程摘要而不是 TradingAgents-CN / 原版报告的规则。
- 会把 portfolio manager 最终裁决权边界错误扩展成非 PM worker 不能发表强观点、不能给证据支持的交易建议、不能写 final proposition 的规则。
- 会把“缺证据要说明”错误扩展成“有证据也要写成 limitation/compliance memo”的规则。

### 5. 三层验收

每个 worker 的验收报告必须至少比较：

1. final prompt：真实 provider payload 中报告生成回合的 prompt。
2. LLM back：模型真实生成的正文或工具提交内容。
3. report：进入材料链或证据目录的 worker report。

结论必须区分：

- 已确认事实。
- 推断。
- 未知或缺证据。

不得用 happy-path 测试替代三层对比。

## 执行模板

每个后续 worker prompt 任务开头必须写明：

```text
本任务禁止：
- 新增或收紧未经批准的 guard / hard gate
- 新增未经批准的审慎、合规、memo 化、限制强观点或限制交易建议的 prompt 规则
- 改 workflow 权责
- 改 PM 最终裁决权
- 把机器协议字段写进 prompt
- 用静态测试代替真实 provider final prompt

本任务只允许：
- 基于 CN/原版 baseline 改 prompt
- 替换运行变量和批准工具名
- 保留不编造底线，但不得改变 baseline 角色声线、辩论强度、建议表达
- 产出 final prompt / LLM back / report 三层对比证据
```

## Stop Conditions

遇到以下情况必须停止并询问人类：

- 找不到 CN/原版基线，却准备继续写 prompt。
- 准备新增报告表达类 guard。
- 准备把“真实性”写成风格禁令、审慎禁令、memo 化要求或禁止强观点/交易建议的规则。
- 准备把 URI、hash、receipt、L1/L2、manifest、JSON claim 等机器字段放入 prompt。
- 准备让 CN/原版纯 prompt worker 暴露 OpenViking read/write 工具。
- 准备用 artifact ref / capability 代替 CN/原版 prompt 中的完整上游材料正文。
- 三层证据不能绑定真实 provider payload。
- 某 worker 的原版权限与 PM 最终裁决权边界冲突。
