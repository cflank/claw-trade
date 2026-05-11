# TradingAgents-CN market rerun execution

- command: DEBUG=false timeout 1200s .venv/bin/python <capture-script>
- exit_code: 0
- project: /home/frank/src/TradingAgents-CN
- ticker: 600519
- company: 贵州茅台
- env_override: DEBUG=false
- llm_calls: 2
- final_prompt_raw: docs/evidence/market_prompt_feedback/tradingagents_cn_market_rerun_600519_20260506_final_prompt_raw.json
- llm_back_raw: docs/evidence/market_prompt_feedback/tradingagents_cn_market_rerun_600519_20260506_llm_back_raw.json
- report_path: docs/evidence/market_prompt_feedback/tradingagents_cn_market_rerun_600519_20260506_report.md
- report_chars: 711
- outcome: LLM ran, but the data tool returned `获取600519股票数据失败: 'tuple' object has no attribute 'split'`, so the report is a failure report rather than a complete technical report.
