# claw-trade 对齐 TradingAgents-CN 改造参考

本文记录 2026-05-06 market_analyst 对比与修正后的结论，用作后续改造其他 worker 的参考。

## 核心原则

claw-trade 对齐 TradingAgents-CN，不是照搬 CN 的运行方式，而是分三层对齐：

1. 报告表达学 CN。
2. 工具资料改成 CN 那种“可以直接写报告的摘要”。
3. 正式流程保留 claw-trade 的 OpenClaw / OpenViking / evidence 架构。

一句话：

**CN 负责报告感，claw-trade 负责真实、可追踪、可校验。**

## 为什么不能完全照搬 CN

TradingAgents-CN 的 market 流程更像普通 agent 回复：

- worker 调工具。
- 工具返回整理好的中文技术摘要。
- LLM 直接返回报告文本。

claw-trade 是正式控制流程：

- claw-trade 控制 workflow。
- OpenClaw 唤醒单个 worker。
- worker 必须通过真实工具拿数据。
- worker 必须把正式材料写入 OpenViking。
- control plane 必须留下 provider request、visible tools、tool calls、raw output、receipt、guard result。

所以 claw-trade 的业务 prompt 应该像 CN，但不能删掉必要流程约束。

必须保留：

- 精确 ticker，例如 `600519.SH`，不能被改成 `600519`。
- 当前 worker 可见工具。
- 首次工具调用规则。
- OpenViking 写入目标。
- 禁止编造行情、指标、图表、目标价、止损价、工具成功。
- 数据缺失时必须写清楚缺口。

## 后续每个 worker 的对齐流程

每个 worker 都按同一套流程做，不要凭感觉改 prompt。

### 1. 同标的跑两边

选择同一个标的、同一个市场、同一个日期。

分别跑：

- TradingAgents-CN 原版 worker。
- claw-trade 当前 worker。

必须收集三类证据：

- final user prompt。
- LLM back。
- report。

没有这三类证据，不判断“像不像 CN”。

### 2. 对比 final user prompt

重点看：

- 是否是中文报告任务，而不是工程 checklist。
- 是否有正式报告标题。
- 是否无 emoji。
- 是否章节完整。
- 是否要求具体指标和推理。
- 是否把大量运行时细节塞进业务任务。
- 是否把过多原始数据塞进 LLM 上下文。

claw-trade prompt 应该接近 CN：

- 正式标题。
- 四大章节。
- 具体数值。
- 中文读者可读。
- 报告不少于合理长度。

但要比 CN 更严格：

- 不强制模型编目标价。
- 不强制模型编止损价。
- 不允许用“看起来合理”的阈值代替工具证据。

### 3. 对比 LLM back

TradingAgents-CN 通常是：

- `thinking + text`。
- 报告直接在 text block。

claw-trade 通常是：

- `thinking + tool_use`。
- 报告在 `openviking_write_material` 的 `content` 参数里。

这是正常差异，不是问题。

真正要检查的是：

- LLM 是否真的调用了 worker 应该用的工具。
- 是否真的调用了 OpenViking 写入工具。
- 是否有 provider request。
- 是否有 visible tools。
- 是否有 tool calls。
- 是否有 raw output。
- 是否有 OpenViking receipt。
- receipt 是否 readback 校验通过。

后续还需要补强：

- 导出 provider response usage。
- 导出 provider request id / response id。
- 生成一个人能直接看的 `llm_report_text.md`。

### 4. 对比 report

报告必须看读者体验，不只看测试是否过。

检查项：

- 标题是否正式。
- 是否无 emoji。
- 是否像中文研报，而不是工具日志。
- 是否有完整章节。
- 是否引用具体数据。
- 是否有短期趋势。
- 是否有中期趋势。
- 是否有量价关系。
- 是否有明确技术面评级。
- 是否有风险提示。
- 是否有后续观察条件。
- 是否把工程流程、工具名、receipt、artifact 机器字段写进读者正文。

claw-trade 可以比 CN 更保守，但不能比 CN 更干、更短、更像摘要。

## 工具资料包改造原则

LLM 需要的是“可写报告的资料”，不是数据库 dump。

工具应该返回：

- 当前价格。
- 涨跌幅。
- 成交量。
- 数据区间。
- 数据条数。
- 最近 5 到 20 行行情样本。
- MA / MACD / RSI / BOLL 等核心指标。
- 该市场常用指标，例如 A 股可考虑 MA60、RSI6/12/24、KDJ、ATR。
- 支撑位 / 压力位。
- 图表路径。
- 数据来源或后端。
- warning / 缺失原因。

工具不应该直接把长周期全量行情都塞进 prompt。

完整原始数据可以留在 evidence 或 artifact 中，但给 LLM 的上下文应该是：

- 摘要。
- 关键指标。
- 最近样本。
- 图表引用。
- 可追踪证据路径。

## 日期窗口原则

不要默认一年。

market 单 worker 对齐 CN 时，优先使用短窗口，例如 40 到 90 天，除非任务明确需要一年趋势。

原因：

- CN market 工具本次实际返回约 39 条。
- 上一轮 claw-trade 用一年窗口拿到 243 行，导致 prompt 过胖。
- market 技术分析的常规报告更关注短中期结构，不需要每次把一年全量 K 线给 LLM。

如果需要一年：

- 工具内部可以用完整窗口计算指标。
- LLM 上下文只给摘要和最近样本。
- 不能把一年原始行情逐行塞给 LLM。

## market_analyst 本轮已验证的结论

本轮修正后的 claw-trade market run：

- run_id: `run-20260506-114251-c7c9a100`
- 日期窗口：`2026-03-07` 至 `2026-05-06`
- `price_row_count=39`
- `recent_price_row_count=20`
- OpenViking receipt verified。
- hard gate passed。
- 报告正文约 `9001` bytes。

改造效果：

- CN_A prompt 已改成接近 TradingAgents-CN 的正式中文技术分析报告结构。
- market 资料包不再把一年 243 行完整行情塞进 prompt。
- report 从上一轮短摘要变成完整报告。
- OpenClaw / OpenViking 正式证据链仍保留。

仍需继续收紧：

- 报告中不应生成工具未明确支持的阈值，例如 `1300-1320`、`6000万股以上`。
- A 股指标口径需要继续向 CN 靠，例如补齐 MA60、RSI6/12/24 或明确不提供原因。
- LLM back 导出还需要更友好，最好同时保存 raw tool call 和可读报告正文。

## 后续 worker 改造顺序建议

对每个 worker 重复以下步骤：

1. 跑 TradingAgents-CN 对应 worker，保存 final prompt / LLM back / report。
2. 跑 claw-trade 对应 worker，保存 final prompt / LLM back / report。
3. 写三方面对比。
4. 把 claw-trade prompt 改成 CN 报告结构。
5. 把工具结果改成报告友好摘要。
6. 重跑 claw-trade。
7. 对比本轮 claw 与 CN。
8. 收紧 unsupported claim。
9. 确认 provider request、visible tools、tool calls、raw output、receipt、guard result 都存在。
10. 通过后再处理下一个 worker。

## 禁止事项

后续对齐 CN 时，不允许：

- 用 Python 替 worker 写报告。
- 用 direct LLM 绕过 OpenClaw。
- 用 mock / stub / fake / fallback 成功路径。
- 用日志、renderer output、export report 冒充 provider request。
- 放弃 OpenViking receipt。
- 放弃 provider request capture。
- 放弃 visible tools / tool calls 证据。
- 为了像 CN 而允许模型编造目标价、止损价、新闻、指标或图表。

## 验收标准

一个 worker 可以认为“阶段性对齐 CN”，至少需要满足：

- final user prompt 像 CN 的业务报告任务，而不是工程说明。
- LLM back 保留真实原始返回。
- report 有 CN 风格报告感。
- 工具结果支持报告中的主要事实。
- 没有 unsupported 关键投资结论。
- 没有 Python 代写 worker 正文。
- OpenClaw 唤醒真实 worker。
- OpenViking 写入真实 material。
- receipt 校验通过。
- hard gate passed。
- evidence 文件能直接复核。

