# USER

你会收到 claw-trade control plane 传入的运行时变量和已批准上游报告正文；必要时也会收到可追踪材料引用用于深读。

只允许使用这些运行时变量：

- ticker
- company_name
- market
- currency
- date_range
- run_id
- dispatch_id
- stage
- approved_upstream_reports

本回合直接输出 `social_sentiment_report` 的读者正文，不要包含工具日志、JSON、URI 或机器字段。
若是空数据或工具失败，仍需写限制说明报告并明确 root cause 与证据缺口。
如果证据不足，明确列出缺口和需要的 root cause，不要用 fallback 或猜测补齐。
