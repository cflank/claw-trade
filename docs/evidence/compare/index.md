# CN vs claw evidence comparison index

## Scope

- 标的：`600519 / 贵州茅台 / CN_A`。
- CN 来源：`docs/evidence/cn`，TradingAgents-CN direct run，`deepseek/deepseek-chat`，真实 provider，非 capture-only。
- claw 来源：`docs/evidence/claw`，claw-trade `/report` full12 run，run id `run-20260512-110633-4e1cc0ab`，真实 OpenClaw/OpenViking runtime。
- 本目录只包含 Markdown 对比报告。

## Overall Findings

- prompt 层：两边都是真实 provider 边界证据，但运行框架不同，CN 是原生 LangGraph 节点，claw 是 OpenClaw worker 加项目控制面。
- LLM back/report 层：claw 多数更长、更完整，原因是资料包证据、approved L1 和最终报告边界；CN 个别节点尤其 `news_analyst` 本轮没有形成完整中文报告。
- 最终报告层：CN 是最终裁决原文，claw 是客户可读汇编报告，因此篇幅和结构差异是架构性差异，不等同于单个 PM worker 偏离。

## Worker Reports

| worker | final prompt 相似度 | llm back 相似度 | report 相似度 | 主要结论 |
|---|---:|---:|---:|---|
| [`market_analyst`](market_analyst.md) | 0.740 | 0.028 | 0.028 | 明显偏离：claw 篇幅显著更长，通常来自资料包证据、approved L1 注入或 exporter 汇编。 |
| [`fundamental_analyst`](fundamental_analyst.md) | 0.080 | 0.099 | 0.099 | 明显偏离：claw 篇幅显著更长，通常来自资料包证据、approved L1 注入或 exporter 汇编。 |
| [`news_analyst`](news_analyst.md) | 0.062 | 0.003 | 0.003 | 不具备完整报告可比性：CN 侧本层只有工具调用前置语，claw 侧是完整报告。 |
| [`social_analyst`](social_analyst.md) | 0.056 | 0.053 | 0.053 | 明显偏离：两侧内容低相似，主要应看输入材料、工具链和控制流差异。 |
| [`bull_researcher`](bull_researcher.md) | 0.114 | 0.069 | 0.069 | 明显偏离：两侧内容低相似，主要应看输入材料、工具链和控制流差异。 |
| [`bear_researcher`](bear_researcher.md) | 0.095 | 0.039 | 0.039 | 明显偏离：两侧内容低相似，主要应看输入材料、工具链和控制流差异。 |
| [`research_manager`](research_manager.md) | 0.085 | 0.076 | 0.076 | 明显偏离：两侧内容低相似，主要应看输入材料、工具链和控制流差异。 |
| [`trader`](trader.md) | 0.401 | 0.074 | 0.074 | 明显偏离：两侧内容低相似，主要应看输入材料、工具链和控制流差异。 |
| [`risk_challenger`](risk_challenger.md) | 0.105 | 0.095 | 0.094 | 明显偏离：两侧内容低相似，主要应看输入材料、工具链和控制流差异。 |
| [`risk_guardian`](risk_guardian.md) | 0.101 | 0.063 | 0.063 | 明显偏离：两侧内容低相似，主要应看输入材料、工具链和控制流差异。 |
| [`risk_moderator`](risk_moderator.md) | 0.090 | 0.100 | 0.100 | 明显偏离：两侧内容低相似，主要应看输入材料、工具链和控制流差异。 |
| [`portfolio_manager`](portfolio_manager.md) | 0.159 | 0.081 | 0.081 | 明显偏离：两侧内容低相似，主要应看输入材料、工具链和控制流差异。 |

## Final Report

- [最终报告对比](final_report_comparison.md)
