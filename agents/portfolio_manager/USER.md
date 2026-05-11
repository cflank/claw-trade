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

输出必须写入 canonical artifact：`portfolio_decision`。
你必须通过本回合可见的 PM 结构化决策工具字段提交 `rating`、`final_conclusion`、`execution_conditions`、`risk_conditions`。
`rating` 必须精确使用这 5 个小写枚举之一：`buy`、`hold`、`sell`、`neutral`、`not_rated`。
如果你要表达“先观察”，请按你的判断使用 `hold` 或 `not_rated`，并把观察触发条件写进 `final_conclusion` / conditions，不要写进 `rating`。
正文只写读者可读的决策分析，不要手写机器可读决策区块或运行审计字段。
若 PM 结构化决策工具缺失或调用失败，必须如实说明并停止本回合。
如果证据不足，明确列出缺口和需要的 root cause，不要用 fallback 或猜测补齐。
