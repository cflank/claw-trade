# control 总体迁移方案

> 本文档是总体方案，不是任务清单。
>
> 目标：从 `claw-invest` 提取“流程控制”思想，在 `claw-trade` 中重新实现完整报告流程。代码从干净状态重建，不沿着旧 `src` 继续补丁式修改。

## 1. 总结

结论：`claw-trade` 管完整报告流程，OpenClaw 管单个工作者的一次真实模型执行，OpenViking 管正式材料和上下文。

简单说：

- `claw-trade` 决定“现在该谁做事、做完后能不能往下走、材料能不能给下游看、最后怎么导出报告”。
- OpenClaw 负责“真正叫醒一个工作者，把最终内容发给模型，让模型看到正确工具，执行工具调用，并把真实证据交回来”。
- OpenViking 负责“保存工作者正式材料、让下游按批准引用读取材料、保留 L1 完整报告和 L2 原始证据、沉淀可复用上下文和记忆”。
- Python 不能假装自己是工作者，不能自己写投资分析内容，不能替工作者查数据，不能改写最终组合经理的结论。
- 人已批准修改 `third_party/openclaw`，但只允许补通用执行证据能力，不能把 `claw-trade` 的业务流程塞进 OpenClaw。允许补的通用能力只包括：单次工具收窄、证据目录传入、真实 provider request 捕获、模型可见工具捕获、第一步回应或工具调用捕获、机器可读结果返回。

本方案优先保证四件事：

1. 真实执行：12 个工作者都通过 OpenClaw 执行。
2. 真实证据：能看到最终发给模型的内容、模型实际看到的工具、模型第一步回应和工具调用。
3. 材料可信：工作者正式产物进入 OpenViking，只有 `claw-trade` 批准后的材料才能给下游读取。
4. 权责清楚：流程、批准、硬检查、报告归 `claw-trade`；单个模型回合归 OpenClaw；材料存储、分层上下文、长期记忆归 OpenViking。

## 2. 总体架构

整体可以看成六层。

```text
用户请求
  ↓
claw-trade 流程层
  ↓
claw-trade 执行适配层
  ↓
OpenClaw 单个工作者执行
  ↓
OpenViking 正式材料与上下文
  ↓
claw-trade 材料、检查、报告层
```

### 2.1 `claw-trade` 负责什么

`claw-trade` 是总控。

它负责：

- 接收一次报告请求。
- 建立一次运行记录。
- 判断市场和提示词配置是否允许使用。
- 决定工作者执行顺序。
- 为每个工作者生成一次调用请求。
- 把调用请求交给 OpenClaw。
- 接收 OpenClaw 返回的真实证据。
- 检查证据是否可信。
- 检查工作者产物是否能进入下游。
- 维护已批准材料清单。
- 决定下一步继续、失败、重跑或结束。
- 导出中文读者能看的最终报告。

它不能做：

- 直接调用模型供应商。
- 写工作者的业务提示词。
- 替工作者调用行情、新闻、基本面、社交等工具。
- 替 OpenViking 生成假材料成功。
- 用日志、导出报告或重构文本冒充真实模型请求。
- 放松硬检查。
- 改写最终组合经理的评级和结论。

### 2.2 OpenClaw 负责什么

OpenClaw 是单个工作者执行器。

它负责：

- 读取工作者身份、提示词、技能和工具。
- 根据本次调用要求，只暴露允许的工具。
- 生成最终发给模型的完整请求。
- 把请求发给真实模型供应商。
- 执行模型产生的工具调用。
- 让工作者通过当前阶段允许的工具读写 OpenViking 材料。
- 保存真实请求、模型可见工具、模型第一步回应、工具调用记录。
- 把这些证据位置和执行结果返回给 `claw-trade`。

它不能做：

- 决定 12 个工作者的完整流程。
- 决定下一个工作者是谁。
- 批准或拒绝下游材料。
- 写 `claw-trade` 的报告导出逻辑。
- 生成投资结论替代工作者输出。
- 内置 `claw-trade` 专用业务规则。

### 2.3 OpenViking 负责什么

OpenViking 是正式材料和上下文底座。

它负责：

- 保存每个工作者写出的正式 Markdown 材料。
- 使用稳定 URI 表达材料位置。
- 保存材料内容指纹、大小、写入回执等证据。
- 承载 L1 完整报告和 L2 原始证据。
- 让下游工作者按批准引用读取上游材料。
- 支持长期上下文和记忆检索。

它不能做：

- 决定下一个工作者是谁。
- 决定材料是否被批准。
- 决定 hard gate 是否通过。
- 改写工作者报告。
- 生成组合经理结论。
- 充当 workflow 状态总线。

本项目采用 OpenViking，是为了避免后续材料接口重写。正式材料接口从第一天就按 OpenViking 形态设计：

```text
viking://resources/workflow/<run_id>/<stage>/<worker>/<call_id>/<artifact>.md
```

材料分层是规范约束，不是读取偏好：

- L1 是每个工作者的完整正式 Markdown 报告，也是下游默认读取和写作依据。即使工作者结论是“无法判断”或“证据不足”，L1 也必须完整写明结论、证据边界、已尝试证据、缺失项和影响，不能只写一句短结论。
- L2 是支撑 L1 的原始或近原始证据入口，包括工具原始响应、行情行、新闻原文和元数据、API 响应、图表资产、指标输出、引用文件和长材料。L2 可以是附件或索引，但必须能回源、能校验。
- 每个完整运行的工作者都必须有 L1 和 L2 索引。没有产生 L2 条目时，索引必须记录原因；只要 L1 使用了具体数字、图表、新闻、情绪、估值、来源或工具成功声明，就必须有对应 L2 证据。
- compact 或检索摘要只能帮助定位材料，不能作为正式写入内容、hard gate 依据或下游写作主路径。
- 工作者原始正式输出写入 OpenViking 前不得被 Python、OpenClaw 适配层或 OpenViking 包装层截断、压缩成摘要，或改写成 compact 材料。
- OpenViking URI 形状保持不变；正式材料 SHA/size 统一以 OpenViking `content/download` 原始字节记录。`content/read` 只用于 worker 文本读取和展示；receipt/manifest/capability/PM/export 的推进阻断不再由 SHA mismatch 单独触发。

但 `claw-trade` 仍然是批准权威。OpenViking 里存在某份材料，不代表这份材料已获批准，也不代表它能进入下游。

## 3. 完整报告流程

完整流程固定，不由模型决定。

```text
前线分析
  1. 市场分析
  2. 基本面分析
  3. 新闻分析
  4. 社交情绪分析

投资辩论
  5. 看多研究
  6. 看空研究

投资判断
  7. 研究经理

交易计划
  8. 交易员

风险辩论
  9. 激进风险观点
  10. 保守风险观点
  11. 中性风险观点

最终判断
  12. 组合经理
```

流程规则：

- 前线四个分析工作者可以并行执行，但必须全部通过材料检查，才能进入投资辩论。
- 投资辩论只能通过 OpenViking 读取已批准的前线材料。
- 研究经理只能通过 OpenViking 读取已批准的投资辩论材料。
- 交易员只能通过 OpenViking 读取已批准的研究经理材料。
- 三个风险工作者只能通过 OpenViking 读取已批准的交易计划。
- 组合经理只能通过 OpenViking 读取已批准的风险辩论材料。
- 任何未通过硬检查的材料，都不能进入共享状态、下游提示词、最终报告。

## 4. 一次运行怎么走

### 4.1 创建运行

用户发起报告请求后，`claw-trade` 先做这些事：

1. 记录标的、公司名、市场、货币、日期范围。
2. 判断市场配置是否允许。
3. 判断对应提示词是否存在并已批准。
4. 创建运行目录。
5. 创建 OpenViking 运行命名空间。
6. 创建运行状态。
7. 进入第一个阶段。

如果市场是 HK 或 CRYPTO，但提示词策略没有明确批准，直接失败，不自动套用 US 或 CN_A。

### 4.2 决定下一步

`claw-trade` 的流程层只做一件事：根据当前状态和已有结果，决定下一步。

它的判断类似这样：

```text
如果运行刚创建：
  叫醒前线工作者

如果前线还没全部完成：
  等待或继续收集前线结果

如果前线全部通过：
  叫醒看多和看空研究

如果投资辩论通过：
  叫醒研究经理

如果研究经理通过：
  叫醒交易员

如果交易计划通过：
  叫醒三个风险工作者

如果风险材料通过：
  叫醒组合经理

如果组合经理通过：
  导出最终报告
```

模型不能决定这张流程图。

### 4.3 生成一次工作者调用

每次要叫醒一个工作者时，`claw-trade` 生成一份调用请求。

这份请求只包含运行事实和控制要求，不包含 Python 写出来的业务分析。

必须包含：

- 本次运行编号。
- 本次调用编号。
- 要叫醒哪个工作者。
- 当前阶段。
- 当前市场和提示词配置。
- 标的、公司名、货币、日期范围。
- 本次允许的工具。
- 上游已批准材料的 OpenViking 引用。
- 本次正式写入目标 URI。
- 本次读取策略：默认读 L1，缺关键事实时按需读 L2。
- 证据保存目录。
- 是否只验证第一步回应。
- 说明 worker 只写读者正文；claim/identity/receipt 等机器字段由工具与 control evidence 生成。

不允许包含：

- Python 写出的市场分析正文。
- Python 写出的投资建议。
- 未批准上游全文。
- 未批准 OpenViking URI。
- fallback 提示词。
- 临时补出来的工具列表。

### 4.4 交给 OpenClaw

执行适配层把调用请求交给 OpenClaw。

这一层只做翻译，不做业务判断：

```text
claw-trade 调用请求
  ↓
OpenClaw 单个工作者命令
  ↓
OpenClaw 真实模型回合
  ↓
工作者通过允许工具读写 OpenViking
  ↓
OpenClaw 返回证据
```

执行适配层不能直接调用模型供应商，也不能自己模拟 OpenClaw 返回成功。

### 4.5 接收 OpenClaw 证据

OpenClaw 每次执行完，必须返回机器可读结果。

结果至少包含：

- OpenClaw 运行标识。
- 供应商请求标识；如果供应商没有返回，则明确为空。
- 最终发给模型的请求原文路径。
- 模型实际看到的工具列表。
- 第一条模型文本或第一条工具调用路径。
- 工具调用记录路径；如果没有工具调用，则明确为空。
- 工作者最终原始输出路径；如果只做首步验证，则可以为空。
- OpenViking 写入回执路径；如果未完整运行，则可以为空。
- 执行状态。
- 失败原因；如果成功，则为空。

`claw-trade` 只接受 OpenClaw 返回的真实证据。

不能接受：

- 日志摘要。
- 渲染后的报告。
- 导出后的报告。
- Python 重构出来的提示词。
- Python 从允许工具列表反推出来的“模型可见工具”。
- Python 自己编造的 OpenViking 写入回执。
- 没有原文路径的摘要。

## 5. `claw-trade`、OpenClaw 和 OpenViking 的接口

接口要简单、稳定、可测试。

### 5.1 `claw-trade` 交给 OpenClaw 的内容

| 内容 | 人话解释 | 归属 |
| --- | --- | --- |
| 工作者身份 | 这次叫醒谁 | `claw-trade` 决定 |
| 当前阶段 | 这次属于哪一段流程 | `claw-trade` 决定 |
| 市场配置 | 用 US、CN_A、HK、CRYPTO 哪套配置 | `claw-trade` 校验 |
| 运行事实 | 标的、公司名、货币、日期 | 用户请求和运行记录 |
| 允许工具 | 这次模型能看到哪些工具 | `claw-trade` 按阶段策略生成，OpenClaw 实际执行 |
| 上游材料引用 | 下游能读取哪些已批准 OpenViking 材料 | `claw-trade` 材料层生成 |
| 正式写入目标 | 本工作者应该写到哪个 OpenViking URI | `claw-trade` 生成 |
| 读取策略 | 默认读 L1，必要时补读 L2 | `claw-trade` 生成，OpenViking 执行 |
| 证据目录 | 真实请求和回应写到哪里 | `claw-trade` 提供 |
| 首步停止 | 是否只拿第一步回应做验证 | `claw-trade` 决定 |

### 5.2 OpenClaw 返回给 `claw-trade` 的内容

| 内容 | 人话解释 | 必须性 |
| --- | --- | --- |
| 运行标识 | OpenClaw 内部这次执行的编号 | 必须 |
| 请求标识 | 模型供应商本次请求编号；没有就明确为空 | 尽量有 |
| 请求原文路径 | 最终发给模型的完整请求 | 必须 |
| 模型可见工具 | 模型真实看到的工具列表 | 必须 |
| 第一条回应路径 | 模型第一条文本或第一条工具调用 | 必须 |
| 工具调用记录 | 模型调用了哪些工具，以及工具结果入口 | 有调用时必须 |
| 原始输出路径 | 工作者最终原始输出 | 完整运行时必须 |
| OpenViking 回执 | 正式材料写入位置、大小、内容指纹 | 完整运行时必须 |
| 执行状态 | 成功、失败、取消、超时 | 必须 |
| 失败原因 | 失败时说明为什么 | 失败时必须 |

### 5.3 `claw-trade` 和 OpenViking 的接口

`claw-trade` 不把材料保存在自己发明的正式路径里。它使用 OpenViking-shaped 材料合同。

`claw-trade` 交给 OpenViking 的正式材料目标：

```text
viking://resources/workflow/<run_id>/<stage>/<worker>/<call_id>/<artifact>.md
viking://resources/workflow/<run_id>/<stage>/<worker>/<call_id>/evidence/<evidence_id>
```

材料定位规则：

- `claw-trade` 在派发前生成确定的 L1 写入 URI 和 L2 证据前缀。
- 调用编号必须出现在 URI 或 approved manifest 的唯一键里，重跑不能覆盖旧材料。
- 下游只能通过 approved manifest 里的目标名、URI、内容指纹和调用编号定位材料。
- 禁止通过列目录、按时间排序、猜“最新材料”或 worker 自己拼 URI 来定位材料。

每份正式材料包至少要有：

- 运行编号。
- 阶段。
- 工作者。
- 调用编号。
- 材料目标名。
- L1 OpenViking URI。
- L1 内容哈希。
- L1 大小。
- L2 索引 URI 或索引内容。
- 写入回执。
- L1 完整报告。
- L2 原始证据或长材料入口。

写入回执至少要包含：

- OpenViking URI。
- 运行编号、阶段、工作者、调用编号和材料目标名。
- 内容 SHA256 或等价强内容指纹。
- 内容大小。
- 写入时间。
- OpenViking 返回的回执编号；如果没有则明确为空。

回执不是批准。`claw-trade` 必须在批准前重新读取或 stat 对应 URI，校验实际内容指纹、大小、运行编号、阶段、工作者和调用编号都与预期一致。只要出现回执存在但内容不可读、指纹不一致、大小不一致、URI 不一致或身份字段不一致，就视为正式写入失败，不能进入 approved manifest，也不能给下游读取。

`claw-trade` 维护 approved manifest。它记录哪些 OpenViking 材料已经通过 hard gate，哪些材料可以给下游读。approved manifest 每条记录至少包含目标名、L1 URI、L1 指纹、L2 索引、工作者、阶段、调用编号、批准时间和 hard gate 结果。

读取规则：

- 下游默认读取已批准材料的 L1。
- 只有当 L1 无法支撑具体数字、图表、原文证据、冲突裁决或 hard gate 必填字段时，才补读 L2。
- worker 读取时优先表达“选哪份 material、读 L1 还是 L2”，`capability_id/uri` 由 runtime manifest 映射并校验。
- 禁止把 compact read 当成写作主路径。
- 禁止 worker 自己拼接未批准 URI。
- 禁止扫描 OpenViking 目录猜“最新材料”。
- 禁止裸 URI/latest/list/compact read 成功路径。

OpenViking 不能替 `claw-trade` 做批准。OpenViking 里存在某份材料，只说明它被写入，不说明它能进入下游。

### 5.4 OpenClaw 需要补的能力

人已批准修改 `third_party/openclaw`。修改范围只限这几件事：

1. 让单次工作者命令接收“本次允许工具”。
2. 让单次工作者命令接收“证据保存目录”。
3. 在请求发给模型前保存真实请求原文。
4. 从真实请求原文中保存模型可见工具。
5. 保存模型第一条文本或第一条工具调用。
6. 在命令结果里返回这些证据路径和标识。

不能借这个机会把下面内容写进 OpenClaw：

- 12 个工作者的完整流程。
- 阶段推进规则。
- 材料批准规则。
- 投资报告导出规则。
- Python 业务提示词。
- 组合经理结论改写。

## 6. 模块划分

因为 `src` 已删除，下面是重建时的建议目录。

### 6.1 流程模块

建议目录：

```text
src/claw_trade/workflow/
```

职责：

- 定义运行状态。
- 定义固定阶段。
- 定义 12 个工作者顺序。
- 决定下一步叫醒谁。
- 判断什么时候完成、失败、等待。

建议文件：

| 文件 | 负责什么 |
| --- | --- |
| `state.py` | 一次运行有哪些状态和阶段。 |
| `workers.py` | 12 个工作者名单、顺序、所属阶段。 |
| `calls.py` | 一次工作者调用需要携带什么。 |
| `controller.py` | 根据当前状态和结果决定下一步。 |
| `store.py` | 保存运行状态、调用状态、失败原因和证据位置。 |

禁止：

- 不调用模型。
- 不读写业务提示词。
- 不替工作者调用工具。
- 不批准材料。

### 6.2 配置模块

建议目录：

```text
src/claw_trade/config/
```

职责：

- 读取工作者配置。
- 判断市场配置是否批准。
- 读取阶段工具策略。
- 读取 OpenViking 连接和命名空间配置。
- 检查 OpenClaw 工作区是否挂载正确。

建议文件：

| 文件 | 负责什么 |
| --- | --- |
| `profiles.py` | 市场配置是否批准，缺失时怎么失败。 |
| `stage_policy.py` | 当前阶段允许哪些工具。 |
| `workspace.py` | 检查 `agents/<工作者>/` 文件是否齐全。 |
| `tool_names.py` | 把阶段策略里的工具意图对应到 OpenClaw 实际工具名。 |
| `openviking.py` | 检查 OpenViking endpoint、workspace、运行命名空间和工具挂载。 |

禁止：

- 不做 fallback。
- 不把 HK 或 CRYPTO 偷偷换成 US 或 CN_A。
- 不写业务提示词。
- 不创建临时工具白名单冒充正式策略。

### 6.3 执行适配模块

建议目录：

```text
src/claw_trade/runtime/
```

职责：

- 把一次工作者调用转换成 OpenClaw 命令。
- 调用 OpenClaw。
- 接收 OpenClaw 结果。
- 判断 OpenClaw 结果是否包含必要证据。

建议文件：

| 文件 | 负责什么 |
| --- | --- |
| `openclaw_client.py` | 只负责调用 OpenClaw，并解析返回结果。 |
| `request_builder.py` | 把工作者调用翻译成 OpenClaw 命令参数。 |
| `run_loop.py` | 执行“决定下一步、调用 OpenClaw、回收结果、推进状态”的循环。 |
| `evidence_reader.py` | 读取 OpenClaw 返回的证据文件。 |

禁止：

- 不直接调用模型供应商。
- 不伪造 OpenClaw 成功。
- 不从输入的允许工具反推模型可见工具。
- 不改写工作者输出。

### 6.4 材料模块

建议目录：

```text
src/claw_trade/artifacts/
```

职责：

- 为每个工作者生成确定的 OpenViking L1 正式写入 URI 和 L2 证据前缀。
- 读取 OpenViking 写入回执。
- 校验 OpenViking 实际内容和回执里的指纹、大小、身份字段一致。
- 维护 approved manifest。
- 为通过检查的材料生成下游引用。
- 记录材料的来源、路径、内容指纹。
- 决定材料能不能给下游读取。
- 从工具审计记录和 control evidence 生成结构化 claim ledger；L1 正文不靠 worker 手拼 fenced JSON claim block。
- 对 portfolio_manager，接收其旧结构化字段提交的 `旧评级与条件字段`，并补齐 run/call/worker/stage/material_id/l1_sha 等机器字段。

建议文件：

| 文件 | 负责什么 |
| --- | --- |
| `store.py` | 保存材料元数据、回执和批准状态，不保存正式材料正文作为权威。 |
| `refs.py` | 生成 OpenViking-shaped 可追踪材料引用。 |
| `approval.py` | 判断材料是否通过，能否进入下游。 |
| `manifest.py` | 维护 approved manifest，防止下游读未批准材料。 |
| `openviking_client.py` | 只封装 OpenViking read/write/stat/receipt 操作。 |

禁止：

- 不修改工作者结论。
- 不把失败材料塞给下游。
- 不把材料导出格式当成原始证据。
- 不扫描 OpenViking 目录猜最新材料。
- 不用本地文件冒充 OpenViking 正式材料。
- 不把 compact read 或 compact write 当成默认写作材料。
- 不在工作者正式输出写入 OpenViking 前做截断、压缩或摘要改写。
- 不让 Python 改写 PM 结论；旧版曾设计过额外机器裁决文件，现已删除，正式路径只搬运 PM 自然语言 L1。

### 6.5 硬检查模块

建议目录：

```text
src/claw_trade/guards/
```

职责：

- 检查真实请求是否存在。
- 检查模型可见工具是否符合阶段策略。
- 检查是否出现无证据投资断言。
- 检查材料是否越权进入下游。
- 检查组合经理结论是否被 Python 改写。

建议文件：

| 文件 | 负责什么 |
| --- | --- |
| `provider_request.py` | 检查真实模型请求证据。 |
| `visible_tools.py` | 检查模型实际看到的工具。 |
| `claims.py` | 检查估值、目标价、新闻、情绪、图表、工具成功等断言是否有证据。 |
| `artifact_flow.py` | 检查未批准材料没有进入下游。 |
| `removed_structured_pm_guard_module` | 检查最终评级和结论只来自组合经理。 |

禁止：

- 不把硬失败降级成警告。
- 不用 fallback 绕过检查。
- 不因为测试方便接受假证据。

### 6.6 报告模块

建议目录：

```text
src/claw_trade/reports/
```

职责：

- 把已批准材料整理成中文读者报告。
- 保留来源路径和证据引用。
- 输出最终报告文件。

建议文件：

| 文件 | 负责什么 |
| --- | --- |
| `exporter.py` | 从已批准材料导出最终报告。 |
| `sections.py` | 报告章节结构。 |
| `evidence_links.py` | 报告中的证据引用。 |

禁止：

- 不新增投资结论。
- 不改写组合经理评级。
- 不把缺失证据写成已经存在。

### 6.7 OpenClaw 通用接缝

建议修改范围：

```text
third_party/openclaw/
```

只补通用能力，不写 `claw-trade` 业务逻辑。

建议涉及：

| 位置 | 目的 |
| --- | --- |
| 单次命令入口 | 接收允许工具、证据目录、首步停止设置。 |
| 命令参数类型 | 保存这些新参数。 |
| 执行入口 | 把参数传给真实模型运行层。 |
| 模型请求捕获点 | 在请求发出前保存原文。 |
| 模型回应捕获点 | 保存第一条文本或工具调用。 |
| 结果类型 | 返回证据路径和运行标识。 |
| 命令输出 | 用机器可读格式交给 `claw-trade`。 |

## 7. 证据目录

每次运行都应该有独立目录。

建议结构：

```text
runs/
  一次运行编号/
    request.json
    state.json
    openviking/
      accepted-manifest.json
      write-receipts.json
      l1-index.json
      l2-index.json
    calls/
      一次调用编号/
        openclaw-result.json
        provider-request.json
        visible-tools.json
        first-response.json
        tool-calls.json
        raw-output.md
    reports/
      final-report.md
```

说明：

- OpenViking 是正式材料正文的权威位置。
- `openviking/accepted-manifest.json` 是 `claw-trade` 批准清单的本地审计副本。
- `openviking/write-receipts.json` 保存每次正式写入的 URI、大小、内容指纹和回执编号；当前口径是 `adapter verified receipt`（真实 write+stat/read-back 校验后生成，非 OpenViking 原生 receipt），其中 SHA/size 统一按 `content/download` 原始字节计算。
- `openviking/l1-index.json` 保存每个工作者 L1 正式报告的目标名、URI、调用编号、指纹和批准状态。
- `openviking/l2-index.json` 保存每个工作者 L2 证据入口，包括原始响应、行情行、新闻原文、图表资产、指标输出、引用文件或长材料的 URI、指纹和来源说明。
- L1/L2 的权威正文存放在 OpenViking；本地 `openviking/` 目录只保存索引、回执和批准审计副本。
- 完整运行的每个工作者都必须有 L1 记录和 L2 索引记录。L2 条目为空时，索引必须说明原因。
- `provider-request.json` 必须来自 OpenClaw 在请求发出前保存的真实内容。
- `visible-tools.json` 必须来自同一份真实请求。
- `first-response.json` 必须来自模型第一步真实回应。
- `raw-output.md` 是工作者原始输出，不是导出报告。
- `final-report.md` 是最终读者报告，不能冒充模型请求证据。

## 8. 最小重建顺序

为了避免再次改乱，重建顺序必须从边界最清楚的部分开始。

### 第一步：只建流程骨架

目标：

- 能创建一次运行。
- 能按固定顺序决定下一步叫醒谁。
- 能表达等待、失败、完成。
- 不调用 OpenClaw。
- 不写任何业务提示词。

验证：

- 12 个工作者顺序正确。
- 前线四个没全通过时不能进入下游。
- 模型不能决定下一步。

### 第二步：先定 OpenViking 材料合同

目标：

- 每个工作者都有确定的 L1 正式写入 URI 和 L2 证据前缀。
- 每个阶段都有明确的上游 approved manifest。
- L1/L2 的语义固定：L1 是下游默认消费的完整报告，L2 是原始证据、图表、行情行、新闻原文和长材料。
- 下游默认读 L1，必要时补读 L2。
- 不先做本地 artifact store 再迁 OpenViking。

验证：

- 材料 URI 形状稳定。
- 写入回执经过 URI、大小、内容指纹和调用身份校验，且 SHA/size 明确使用 OpenViking `content/download` 原始字节口径。
- 未批准材料不能进入 approved manifest。
- 禁止目录扫描猜最新材料。
- 禁止 compact read 或 compact write 成为默认写作材料。

### 第三步：补 OpenClaw 通用接缝

目标：

- OpenClaw 能按本次调用收窄工具。
- OpenClaw 能让工作者通过当前允许工具读写 OpenViking。
- OpenClaw 能保存真实模型请求。
- OpenClaw 能保存模型可见工具。
- OpenClaw 能保存第一步回应。
- OpenClaw 能返回机器可读证据。

验证：

- 缺真实请求时失败。
- 缺模型可见工具时失败。
- 日志或导出报告不能冒充真实请求。

### 第四步：接上 `claw-trade` 到 OpenClaw

目标：

- `claw-trade` 能叫醒一个真实工作者。
- 能拿回 OpenClaw 证据。
- 能拒绝不完整证据。
- 能拿回 OpenViking 写入回执。

验证：

- 不直接调用模型供应商。
- 不从允许工具反推模型可见工具。
- 不伪造 OpenClaw 成功。
- 不伪造 OpenViking 写入成功。

### 第五步：接配置和工具策略

目标：

- 12 个工作者配置齐全。
- US 和 CN_A 按批准配置运行。
- HK 和 CRYPTO 未批准时明确失败。
- 当前阶段只暴露当前应有工具。
- OpenViking 读写工具按 worker/stage 收窄。

验证：

- 没有 fallback。
- 没有临时工具白名单。
- 工具策略和模型可见工具能逐项对比。
- OpenViking 工具暴露面和 approved manifest 能逐项对比。

### 第六步：接材料流转

目标：

- 每个工作者完整 L1 输出都写入 OpenViking，且写入前不被 compact。
- 每个工作者都有 L2 索引；使用具体事实时能回源到 L2 条目。
- 通过检查后才进入 approved manifest。
- 下游只能读取 approved manifest 中的材料。
- 关键数字、图表和新闻原文能回源到 L2 或工具证据。

验证：

- 失败材料不能进入下游。
- 材料引用包含运行、阶段、工作者、调用编号、L1 URI、L2 索引和内容指纹。
- L2 存在但未被消费的情况要被测试覆盖。

### 第七步：跑完整 12 个工作者

目标：

- 12 个工作者全部通过 OpenClaw。
- 每个工作者都有真实请求和工具证据。
- 每个工作者都有 OpenViking 正式材料或明确失败原因。
- 组合经理输出成为最终投资结论来源。

验证：

- 后八个没有直接模型路径。
- Python 没有写业务提示词。
- Python 没有替工作者调工具。
- Python 没有改写组合经理结论。

### 第八步：导出最终报告

目标：

- 把已批准材料整理成中文报告。
- 报告有证据引用。
- 报告不新增 unsupported 结论。

验证：

- 最终评级来自组合经理。
- 缺失图表或指标时说明真实原因。
- 不把缺失证据写成存在。

## 9. 停止条件

遇到下面情况必须停下来问人：

- 为了跑通，必须把 `claw-trade` 流程写进 OpenClaw。
- OpenClaw 补完通用接缝后仍不能保存真实模型请求。
- OpenClaw 补完通用接缝后仍不能给出模型可见工具。
- OpenClaw 返回成功，但 `provider_request_path` 缺失、不可读，或不能证明是本次真实发给供应商的请求。
- OpenClaw 返回成功，但 `visible_tools_path` 缺失、不可读，或不能证明来自同一份真实 provider request。
- provider request 或 visible tools 只能从日志、渲染文本、导出报告或 Python 重构内容得到。
- OpenViking 不能稳定提供正式写入、读取和回执。
- OpenViking 写入成功但 `claw-trade` 无法校验内容指纹。
- OpenViking 材料接口只能依赖目录扫描猜最新材料。
- OpenViking 读取策略只能提供 compact read，无法让下游按需读完整 L1 或 L2。
- 工具收窄只能靠临时硬编码列表。
- 需要 fallback 提示词、fallback 工具、fallback 市场配置。
- 需要用本地文件路径冒充 OpenViking 正式材料。
- HK 或 CRYPTO 提示词策略需要人决定。
- 需要 Python 直接调用模型供应商。
- 需要 Python 替工作者查数据或调工具。
- 需要 Python 写工作者业务提示词。
- 需要 Python 改写组合经理结论。
- 需要让 OpenViking 决定流程推进、重试、材料批准或最终报告结论。
- 同一类硬检查失败两次后仍找不到根因。

## 10. 最终验收标准

完整迁移完成必须同时满足：

- 12 个工作者全部通过 OpenClaw 执行。
- 每个工作者都有真实模型请求原文。
- 每个工作者都有模型可见工具列表。
- 每个完整运行的工作者都有 OpenViking L1 URI、L2 索引和写入回执。
- 每个进入 approved manifest 的 OpenViking 材料都通过 URI、大小、内容指纹和调用身份校验。
- receipt/manifest/capability/PM/export 的 SHA/size 口径统一为 OpenViking `content/download` 原始字节，并保留为审计字段；`content/read` 展示差异（例如末尾换行）和 canonical download bytes mismatch 都不能单独判定失败。
- 流程阻断条件保持在 URI/身份不一致、capability 越权、材料不存在或不可读、内容为空/size<=0、OpenViking 后端错误、PM final authority 被改写、unsupported claim。
- 下游只读取 approved manifest 中的材料。
- 下游默认读取 L1，关键事实缺口能回源 L2 或工具证据。
- 没有 compact-first 写作主路径。
- 没有 compact write 冒充工作者正式材料。
- 没有扫描 OpenViking 目录猜“最新材料”。
- 没有直接模型报告路径。
- 没有 Python 业务提示词。
- 没有 Python 替工作者调工具。
- 没有 fallback 冒充成功。
- 没有假 provider 请求。
- 没有假 OpenViking 回执。
- 没有导出报告冒充模型请求。
- 组合经理评级和结论没有被 Python 改写。
- 最终报告只使用已批准材料。
- HK 和 CRYPTO 在策略未批准时明确失败，不偷偷套用别的市场。

## 11. 给后续实现者的提醒

不要从脚本开始写。

先写最小流程，再定 OpenViking 材料合同，再补 OpenClaw 真实证据接缝，再接单个工作者，最后扩到 12 个工作者。

每一步都必须能回答：

- 这一步是谁负责？
- 证据和材料从哪里来？
- 有没有真实模型请求？
- 模型实际看见了哪些工具？
- 正式材料是否写入 OpenViking？
- 正式材料写入前有没有被截断、压缩或摘要改写？
- 下游读的是 approved manifest 里的 L1，还是在偷偷吃 compact 摘要？
- 关键数字、图表和新闻证据能不能回源 L2 或工具证据？
- Python 有没有越权？
- 下游有没有读到未批准材料？
- 组合经理结论有没有被改写？

如果这些问题答不上来，不算完成。
