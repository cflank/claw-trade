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
4. worker 可见材料必须是自然语言 report、debate、decision、approved summary 或 approved artifact ref。
5. 结构化 payload、provider attempts、cache、OpenViking 审计、manifest、receipt、hash 只属于运行层和审计层。

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

只允许替换：

- ticker、company name、market、currency、date range 等运行变量。
- 当前 profile 已批准的工具或材料读取方式。
- claw-trade 必需的“不编造、缺证据说明”底线。
- PM owner 边界。

### 3. 禁止项

不得写入 worker profile prompt：

- `RuntimeTarget`、`ReportSubmission`、`OpenVikingWriteTarget`。
- URI、viking URI、hash、receipt、L1/L2、manifest、material id 等机器协议字段。
- JSON claim block、PM decision JSON block、provider payload 审计说明。
- provider attempts、Mongo cache、OpenViking raw payload、tool-call log。
- 为了让测试通过而新增的工程 checklist。
- 未经人类批准的报告口吻类 guard 规则。

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
- 改 workflow 权责
- 改 PM owner
- 把机器协议字段写进 prompt
- 用静态测试代替真实 provider final prompt

本任务只允许：
- 基于 CN/原版 baseline 改 prompt
- 替换运行变量和批准工具名
- 保留不编造底线
- 产出 final prompt / LLM back / report 三层对比证据
```

## Stop Conditions

遇到以下情况必须停止并询问人类：

- 找不到 CN/原版基线，却准备继续写 prompt。
- 准备新增报告表达类 guard。
- 准备把 URI、hash、receipt、L1/L2、manifest、JSON claim 等机器字段放入 prompt。
- 三层证据不能绑定真实 provider payload。
- 某 worker 的原版权限与 PM owner 边界冲突。
