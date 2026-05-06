# USER

你会收到 claw-trade control plane 传入的运行时变量和已批准 artifact refs。

只允许使用这些运行时变量：

- ticker
- company_name
- market
- currency
- date_range
- run_id
- dispatch_id
- stage
- approved_artifact_refs

输出必须写入 canonical artifact：`fundamental_analysis_report`。
在本回合结束前，必须调用可见工具 `openviking.write_material` 完成该 artifact 写入。
`openviking.write_material` 的 `uri` 或 `target` 必须使用 provider prompt 中 `[OpenVikingWriteTarget]` 给出的目标引用。
写入 `openviking.write_material` 的 `content` 只能是分析正文，不要追加 fenced JSON 机器块。
机器字段由工具与 control evidence 生成，不由 worker 手写。
若 `openviking.write_material` 失败，必须如实说明失败并停止本回合；receipt 校验与重试策略由运行层负责。
若是空数据或工具失败，仍需写限制说明报告并明确 root cause 与证据缺口。
如果证据不足，明确列出缺口和需要的 root cause，不要用 fallback 或猜测补齐。
