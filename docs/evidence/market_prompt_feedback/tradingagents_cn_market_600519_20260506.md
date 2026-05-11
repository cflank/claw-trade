# TradingAgents-CN Market Analyst 首轮 fresh 捕获（600519）

## 原始文件入口

- final prompt 原文：`docs/evidence/market_prompt_feedback/tradingagents_cn_final_prompt_raw_600519_20260506.md`
- LLM 第一轮原始返回：`docs/evidence/market_prompt_feedback/tradingagents_cn_llm_first_response_raw_600519_20260506.json`
- provider request 原始 JSON：`docs/evidence/market_prompt_feedback/tradingagents_cn_provider_request_600519_20260506.json`

## 运行结论
- 请求标的：`600519.SH`
- 实际执行标的：`600519`
- 映射说明：TradingAgents-CN A股路径本次按 `600519.SH -> 600519` 执行，并在证据中保留映射记录。
- 是否 fresh：是（本次为 2026-05-06 新执行，非复用历史会话）
- 是否只到 Market Analyst 首轮：是（仅触发 `Market Analyst`，拿到第一次模型返回后即停止）

## 实际命令与退出码
1. 首次命令（阻塞）  
   ```bash
   cd /home/frank/src/TradingAgents-CN
   timeout 900s .venv/bin/python - <<'PY'
   # 首版脚本：通过 tradingagents.graph.trading_graph 导入 create_llm_by_provider
   PY
   ```
   - exit code: `1`
   - 关键信息：导入链触发 `protobuf/chromadb` 兼容错误（`Descriptors cannot be created directly`）。

2. 最终成功命令（用于本次证据）  
   ```bash
   cd /home/frank/src/TradingAgents-CN
   timeout 900s .venv/bin/python - <<'PY'
   # 直接使用 ChatAnthropic 构造 LLM，单独调用 create_market_analyst，
   # 在首个 LLM 响应处捕获并停止，写出 provider request 与 first response
   PY
   ```
   - exit code: `0`
   - 关键信息：`HTTP 200`，并成功写出两份证据 JSON。

## 证据路径
- provider request（实际传给模型的 messages）  
  `/home/frank/src/claw-trade/docs/evidence/market_prompt_feedback/tradingagents_cn_provider_request_600519_20260506.json`
- first response（第一次模型返回）  
  `/home/frank/src/claw-trade/docs/evidence/market_prompt_feedback/tradingagents_cn_first_response_600519_20260506.json`

## 阻塞情况
- 是否有阻塞：有（已解除）
- 阻塞点：首次脚本因 `trading_graph` 导入链触发 `protobuf/chromadb` 兼容错误。
- 处理方式：改为不走 `trading_graph` 导入链，直接用 `ChatAnthropic + create_market_analyst` 完成同一捕获目标。
