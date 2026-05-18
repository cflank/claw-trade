## message 1: user

您是一位专业的港股市场社交情绪分析师，请输出面向投资读者的中文情绪分析报告。

可用工具：`social_social_sentiment_pack`。
如果消息历史中没有港股社交情绪工具结果，请先调用该工具获取真实材料；如果已有结果，不要重复调用，直接写报告。

标的参数：
- ticker: `00700.HK`
- market: `HK`（必须使用这个精确值，不要写成“港股”或 `CN_A`）
- company_name: `Tencent Holdings`
- start_date: `2026-02-01`
- end_date: `2026-05-17`

写作边界（必须遵守）：
- 最终回答第一行必须是 Markdown 标题，且必须以 `# ` 开头。
- 最终报告正文必须直接从报告标题或正文第一句开始，不要先写过程说明。
- 不要输出“我将调用工具”“数据已获取完成”“我已经获取了数据”“现在撰写报告”“根据获得的数据我可以……”这类过程话。
- 如果社交数据覆盖不足、平台受限、样本时效不足或港股讨论量过低，请在“数据限制与风险提示”部分客观说明，不要把限制说明写在开场。

分析对象：Tencent Holdings（00700.HK），当前日期：2026-05-17。
股票代码必须严格使用 `00700.HK` 原样，不得省略或改写 `.HK` 后缀。

报告至少覆盖以下内容：
1. 港股社交讨论热度与情绪方向（乐观/中性/悲观）。
2. 情绪变化的主要驱动因素，包括业绩、公告、政策、行业事件、南向资金叙事、市场流动性或关键观点传播。
3. 本地投资者、内地投资者和机构观点差异；如果材料不能区分群体，明确说明不能区分。
4. 情绪变化对 1-5 个交易日市场反应的潜在影响与风险。
5. 社交情绪与新闻、基本面、技术面的关系：是强化、背离，还是仅作为观察项。
6. 数据限制与风险提示（仅在此处说明缺口与不确定性）。

要求：
- 全文使用中文。
- 基于已提供的真实材料，不得编造情绪数据、舆情来源、平台热度、南向资金观点或价格影响结论。
- 在证据不足时明确说明不确定性，不要伪造“确定性情绪结论”。
- 在报告末尾附上 Markdown 表格总结关键发现。

## message 2: assistant

### tool_calls
- call 1: `claw_get_social_pack`
  args: `{"ticker":"00700.HK","market":"HK","company_name":"Tencent Holdings","start_date":"2026-02-01","end_date":"2026-05-17"}`

## message 3: tool

资料包工具已返回，但资料就绪状态为 insufficient；这只证明工具调用完成，不证明资料覆盖完成。
请只按下方摘要写已取得事实和缺口，不要补写未提供的数据。
## 社交资料包（HK）
可用性：资料不足
说明：本资料包只呈现来源事实与缺口，不包含投资判断。
原始社交样本：0 条。
聚合情绪指标：0 条。
Alternative.me 市场级情绪：0 条，不代表单标的社交共识。
Polymarket 事件预期：0 条，不代表新闻事实或社交共识。
搜索发现：20 条，仅作线索。
来源尝试：google_news/search_discovery：远端获取成功；eastmoney_hk_guba/social_metrics：远端请求失败；xueqiu_hk/posts：远端请求失败。
资料缺口：social provider is not configured for live fetch: eastmoney_hk_guba/social_metrics；social provider is not configured for live fetch: xueqiu_hk/posts。
