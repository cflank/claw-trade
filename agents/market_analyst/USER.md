# USER

你会收到 claw-trade control plane 传入的运行时变量和已批准 artifact refs。

只允许使用这些运行时变量：

- ticker
- company_name
- market
- currency
- currency_symbol
- date_range
- current_date
- start_date
- end_date
- run_id
- dispatch_id
- stage
- approved_artifact_refs

本回合直接输出 `market_analysis_report` 的读者正文，不要包含工具日志、JSON、URI 或机器字段。
即使行情数据为空、工具失败或指标缺失，也必须写入一份限制说明型 `market_analysis_report`，明确 root cause 与证据缺口。
如果证据不足，明确列出缺口和需要的 root cause，不要用 fallback 或猜测补齐。
