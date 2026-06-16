## message 1: user

您是一位专业的加密市场新闻与事件分析师，请输出面向投资读者的中文新闻事件分析报告。

分析对象：Bitcoin（BTC），所属市场 CRYPTO，当前日期：2026-06-16，分析区间：2025-06-16 至 2026-06-16。
请始终使用精确代码 `BTC`，不要把它改写成股票代码、股票简称或其它资产。

写作边界（必须遵守）：
- 最终报告正文必须直接从报告标题或正文第一句开始，不要先写过程说明。
- 不要输出“我将调用工具”“数据已获取完成”“我已经获取了数据”“现在撰写报告”“根据获得的数据我可以……”这类过程话。
- 不要编造新闻、公告、监管事件、机构资金流、交易所动作、安全事件、治理提案、代币解锁、黑客攻击、合作或市场反应。
- 如果新闻时效、来源覆盖、官方公告验证或跨语言覆盖存在限制，请在“数据限制与风险提示”部分客观说明，不要把限制说明写在开场。

资料使用要求：
- 如果本 turn 可见新闻数据工具，先调用 `claw_request_data` 请求 `公司新闻`，`purpose` 填 `news_report`；随后请求 `宏观` 和 `事件日历`，`purpose` 均填 `news_report`。再基于返回的官方公告、交易所公告、监管来源、项目或行业事件材料、搜索发现、事件预期和数据质量说明写报告；若官方公告、交易所公告、监管来源、宏观来源或事件线索为空，只能写对应缺口，不能补写。只有数据结果真实返回的来源名称才能写入报告；资料缺失时不要自行列举具体来源名。
- 如果新闻数据结果不可见、未调用成功或返回空结果，你的 L1 报告必须明确写出“新闻数据结果未可用 / 未调用成功 / 缺少哪些新闻资料”，不得写真实新闻、真实公告、真实监管事件或真实市场反应结论；只能写资料缺口、影响范围和后续取数需求。
- 搜索摘要和媒体聚合标题只能作为发现线索，不是已确认新闻事实；即使线索提到机构资金、监管、链上活动或其它市场主题，也不能写成已验证事实。只有官方、交易所、监管或已读取原文的可靠媒体内容可作为事实来源。
- 如果同一数据结果中同时存在少量官方来源和大量搜索线索，只能点名官方来源已验证的具体事件；搜索线索里的机构名、法案名、产品名、政策名和标题实体不得进入正文，只能概括为“监管、机构资金、宏观或行业搜索线索待核验”。
- 不要使用列举搜索线索内容的句式，不要在其后列具体关键词、机构、法案、产品、地名、政策名或事件名。数据限制里只写“搜索线索未核验，不能作为事实源”。
- 当某类新闻资料缺少可靠来源时，不要用括号、举例或“如……”列出具体交易所、监管机构、产品、项目、平台或机构名称；只写“交易所公告、监管文件、机构资金、宏观事件或安全事件缺少已验证来源”。
- 新闻缺口段只能使用类别名，不得写任何具体交易所名、监管机构名、资管机构名、协议升级名、提案编号、行业媒体名或产品名；“后续数据需求”也只能列类别，不列具体来源清单。
- 事件预期来源只能表达“市场预期线索”，不能写成新闻事实、事件已经发生或市场共识。
- 市场级情绪指标不是新闻源；新闻报告中不得把它写成新闻事实或社交舆情。
- 报告是给中文投资读者看的。若上游材料含工具名、英文状态词或机器字段，必须按语境解释为“新闻数据结果”“资料就绪度”“资料缺口”“来源尝试记录”“部分覆盖”“不足”“就绪”“监管原始来源”等自然中文，不得照抄成机器日志。
- 工具名和审计字段只用于你理解资料来源，不得写入报告正文；统一写成“新闻数据结果”“未形成可引用的数据集、原始来源或来源尝试记录”“未覆盖完整分析区间”“资料可用性”“资料缺口”“来源尝试记录”等中文表达。
- 报告正文不要使用反引号包裹字段，也不要输出带下划线的英文机器字段。缺少可靠新闻来源时，所有缺口只能写成“官方公告、监管文件、机构资金、交易所公告、安全事件、宏观事件、链上事件缺少已验证来源”，不得列出具体机构产品缩写、发行周期词、机构名称、监管节点、协议升级名或链上叙事名称。
- 输出前做读者版清理：任何工具名、审计计数、机器状态码、带下划线字段、未由可靠来源支持的具体事件、机构、协议或叙事名称，都必须改写成自然中文缺口说明。
- 如果数据结果只返回媒体/搜索线索而未返回官方、交易所、监管或已读取原文的可靠媒体正文，关键新闻表只能写“媒体线索提示存在待核验主题”，方向写“待验证”，影响周期写“无法判断”，证据强度写“低”，不得把标题内容改写成事实、原因、趋势或机构需求判断。
- 缺少可靠新闻来源时，不要展开任何具体利好或利空示例；只能写“若可靠来源恢复并验证方向性事件，需要重新评估”。
- 缺新闻不能被解释成“没有负面消息”，也不能被解释成“存在潜在利好”。缺新闻只表示新闻维度无法判断。
- 只有新闻数据结果返回官方、交易所、监管或已读取原文的可靠媒体正文，才允许写具体事件、旧例、项目事故、监管案例或行业类比；没有可靠来源时，不得用模型记忆补新闻背景。
- 没有可靠来源或明示统计或回测时，只能写“该方向缺少可靠来源或统计回测证据”，不得把新闻背景写成历史规律。
- 没有可靠来源或明示统计或回测时，不得写成确定新闻结论。
- 没有可靠来源时，也不得用模型记忆列举知名事故、监管处罚、机构产品、协议升级或价格旧例；只能写“缺少可引用样本，不能作为当前证据”。
- 新闻数据结果缺失、覆盖不足或只有待核验线索时，仍然输出本角色的新闻报告：先说明资料状态，再区分“已验证来源”和“待核验线索”，最后写清哪些事件问题无法判断。不要把搜索线索改写成事实，也不要自动停止完整新闻分析。

只有当新闻数据结果返回官方、交易所、监管或已读取原文的可靠媒体正文时，报告才至少覆盖以下内容：
1. 关键新闻与事件梳理：覆盖数据结果真实返回的官方公告、交易所公告、监管来源、项目事件、行业事件和资金相关事件。
2. 宏观与行业背景：说明数据结果真实返回的宏观、资金、交易所政策或行业事件对该资产的影响。

缺少可靠新闻来源时，不要列举具体协议升级、供给事件、扩容生态、机构产品、宏观政策、链上活动、交易所、监管机构或资管机构名称；只能写成“相关事件类型缺少已验证新闻来源”。
3. 来源可靠性与时效性：区分官方公告、监管文件、交易所公告、链上数据、媒体报道、KOL 传闻和社区转述。未经可靠来源确认的内容不得当成事实。
4. 对价格与市场情绪的短中期影响：区分利好、利空、中性和噪音；说明影响周期是日内、1-5 天、数周还是中长期。
5. 对项目基本面与投资逻辑的潜在影响：说明事件改变的是需求、供给、监管风险、安全假设、生态扩张、流动性还是单纯叙事。
6. 数据限制与风险提示：仅在此处说明缺口、未验证来源、覆盖不足、时间窗口限制和潜在误读。

要求：
- 全文使用中文。
- 聚焦新闻和事件驱动分析，不用技术指标替代新闻结论。
- 只有可靠新闻事件存在时，才对每条关键事件说明方向、影响周期、证据强度和需要跟踪的后续确认信号。
- 只有可靠新闻事件存在时，才在报告末尾附上 Markdown 表格；可靠来源缺失时不要输出表格。

## message 2: assistant

### tool_calls
- call 1: `claw_request_data`
  args: `{"item":"公司新闻","purpose":"news_report","instrument":"BTC","market":"CRYPTO","time_range":{"start":"2025-06-16","end":"2026-06-16"}}`
- call 2: `claw_request_data`
  args: `{"item":"宏观","purpose":"news_report","instrument":"BTC","market":"CRYPTO","time_range":{"start":"2025-06-16","end":"2026-06-16"}}`
- call 3: `claw_request_data`
  args: `{"item":"事件日历","purpose":"news_report","instrument":"BTC","market":"CRYPTO","time_range":{"start":"2025-06-16","end":"2026-06-16"}}`

## message 3: tool

数据结果：CRYPTO BTC/USDT 的公司新闻，状态：可用。
已返回的数据摘要：
- 项目新闻：2 行。
  - 最新读数：事件 Bitcoin rises after Bank of Japan hikes interest rates to a 31-year high，时间 2026-06-16 04:30:41，来源 CoinDesk，粒度 event，样本 2026-06-15 至 2026-06-16
  - 字段覆盖 as of, event id, 事件类型, fresh until, 结束日期, 开始日期, 发布时间, 来源, source type, 摘要, 标的代码, 时间戳。
  - 时间覆盖 2026-06-15 至 2026-06-16，下列摘要优先展示最新记录。
  - 时间 2026-06-16，媒体/搜索线索已返回，来源 CoinDesk，标题 Bitcoin rises after Bank of Japan hikes interest rates to a 31-year high，摘要 The Bank of Japan raised its key interest rate by 25 basis points to 1%, the highest level since 1995.
  - 时间 2026-06-15，媒体/搜索线索已返回，来源 THE BLOCK，标题 'Let's just put it in an ETF' is the worst outcome for bitcoin, says Trezor exec，摘要 <div class="fallback-content"> 
 <p>Investors have poured more than $53 billion into spot bitcoin exchange-traded funds since launching in the U.S. in early 202
本次数据结果已经可用于正文；不要重复请求同一数据需求。

## message 4: tool

数据结果：CRYPTO BTC/USDT 的宏观，状态：可用。
已返回的数据摘要：
- macro series：11 行。
  - 最新读数：宏观序列 DGS10 4.39 FRED，时间 2026-05-01，来源 BINANCE，粒度 monthly，样本 2025-07-01 至 2026-05-01
  - 字段覆盖 as of, 日期, fresh until, 结束日期, 开始日期, 地区, 序列, 标的代码, 来源/单位, 数值。
  - 时间覆盖 2025-07-01 至 2026-05-01，下列摘要优先展示最新记录。
  - 时间 2026-05-01，序列 DGS10，地区 US，来源/单位 FRED，数值 4.39 FRED
  - 时间 2026-04-01，序列 DGS10，地区 US，来源/单位 FRED，数值 4.33 FRED
  - 时间 2026-03-01，序列 CPIAUCSL，地区 US，来源/单位 FRED，数值 330.293 FRED
  - 时间 2026-02-01，序列 CPIAUCSL，地区 US，来源/单位 FRED，数值 327.46 FRED
  - 时间 2026-01-01，序列 DGS10，地区 US，来源/单位 FRED，数值 . FRED
- macro series：260 行。
  - 最新读数：宏观序列 DGS10 4.48 FRED，时间 2026-06-12，来源 BINANCE，粒度 monthly，样本 2025-06-16 至 2026-06-12
  - 字段覆盖 as of, 日期, fresh until, 结束日期, 开始日期, 地区, 序列, 标的代码, 来源/单位, 数值。
  - 时间覆盖 2025-06-16 至 2026-06-12，下列摘要优先展示最新记录。
  - 时间 2026-06-12，序列 DGS10，地区 US，来源/单位 FRED，数值 4.48 FRED
  - 时间 2026-06-11，序列 DGS10，地区 US，来源/单位 FRED，数值 4.45 FRED
  - 时间 2026-06-10，序列 DGS10，地区 US，来源/单位 FRED，数值 4.55 FRED
  - 时间 2026-06-09，序列 DGS10，地区 US，来源/单位 FRED，数值 4.53 FRED
  - 时间 2026-06-08，序列 DGS10，地区 US，来源/单位 FRED，数值 4.56 FRED
- macro series：52 行。
  - 最新读数：宏观序列 DGS10 4.55 FRED，时间 2026-06-10，来源 BINANCE，粒度 monthly，样本 2025-06-18 至 2026-06-10
  - 字段覆盖 as of, 日期, fresh until, 结束日期, 开始日期, 地区, 序列, 标的代码, 来源/单位, 数值。
  - 时间覆盖 2025-06-18 至 2026-06-10，下列摘要优先展示最新记录。
  - 时间 2026-06-10，序列 DGS10，地区 US，来源/单位 FRED，数值 4.55 FRED
  - 时间 2026-06-03，序列 DGS10，地区 US，来源/单位 FRED，数值 4.49 FRED
  - 时间 2026-05-27，序列 DGS10，地区 US，来源/单位 FRED，数值 4.48 FRED
  - 时间 2026-05-20，序列 DGS10，地区 US，来源/单位 FRED，数值 4.57 FRED
  - 时间 2026-05-13，序列 DGS10，地区 US，来源/单位 FRED，数值 4.46 FRED
- macro series：10 行。
  - 最新读数：宏观序列 DGS10 4.33 FRED，时间 2026-04-01，来源 BINANCE，粒度 monthly，样本 2025-07-01 至 2026-04-01
  - 字段覆盖 as of, 日期, fresh until, 结束日期, 开始日期, 地区, 序列, 标的代码, 来源/单位, 数值。
  - 时间覆盖 2025-07-01 至 2026-04-01，下列摘要优先展示最新记录。
  - 时间 2026-04-01，序列 DGS10，地区 US，来源/单位 FRED，数值 4.33 FRED
  - 时间 2026-03-01，序列 CPIAUCSL，地区 US，来源/单位 FRED，数值 330.293 FRED
  - 时间 2026-02-01，序列 CPIAUCSL，地区 US，来源/单位 FRED，数值 327.46 FRED
  - 时间 2026-01-01，序列 DGS10，地区 US，来源/单位 FRED，数值 . FRED
  - 时间 2025-12-01，序列 DGS10，地区 US，来源/单位 FRED，数值 4.09 FRED
本次数据结果已经可用于正文；不要重复请求同一数据需求。

## message 5: tool

数据结果：CRYPTO BTC/USDT 的事件日历，状态：可用。
已返回的数据摘要：
- 事件日历：26 行。
  - 最新读数：事件 Merchandise Exports (YoY) (Customs)(May)，时间 2026-06-16 23:50:00，来源 Japan，粒度 event，样本 2026-05-17 至 2026-06-16
  - 历史/样本统计：事件来源分布：Japan 15 条、US 5 条、new Zealand 3 条、UK 2 条、Australia 1 条。事件类型分布：宏观经济日历 26 条。宏观经济日历、项目/协议事件和搜索线索需分开解读。
  - 字段覆盖 as of, 事件日期, event id, 事件类型, fresh until, 结束日期, 开始日期, 来源, source type, 标的代码, 时间戳, 标题。
  - 时间覆盖 2026-05-17 至 2026-06-16，下列摘要优先展示最新记录。
  - 时间 2026-06-16，事件材料已返回，来源 Japan，事件 Merchandise Exports (YoY) (Customs)(May)
  - 时间 2026-06-15，事件材料已返回，来源 new Zealand，事件 Food Price Index (MoM)(May)
  - 时间 2026-06-14，事件材料已返回，来源 new Zealand，事件 Service industry performance index(May)
  - 时间 2026-06-12，事件材料已返回，来源 US，事件 Total number of wells drilled in the week (rigs)(to 0612)
  - 时间 2026-06-11，事件材料已返回，来源 new Zealand，事件 manufacturing index(May)
- 事件日历：10 行。
  - 最新读数：事件 Bitcoin Core 31.0，时间 2026-04-20 13:21:17，来源 GitHub:bitcoin/bitcoin，粒度 event，样本 2025-06-26 至 2026-04-20
  - 历史/样本统计：事件来源分布：GitHub:bitcoin/bitcoin 10 条。事件类型分布：项目/协议事件 10 条。宏观经济日历、项目/协议事件和搜索线索需分开解读。
  - 字段覆盖 as of, fresh until, 结束日期, 开始日期, 发布时间, 来源, 摘要, 标的代码, 标题, 链接。
  - 时间覆盖 2025-06-26 至 2026-04-20，下列摘要优先展示最新记录。
  - 时间 2026-04-20，事件材料已返回，来源 GitHub:bitcoin/bitcoin，事件 Bitcoin Core 31.0，摘要 Bitcoin Core version 31.0 is now available from:

https://bitcoincore.org/bin/bitcoin-core-31.0/

For the release notes please see the git repository:

ht
  - 时间 2026-04-06，事件材料已返回，来源 GitHub:bitcoin/bitcoin，事件 Bitcoin Core 28.4，摘要 Bitcoin Core version 28.4 is now available from:

https://bitcoincore.org/bin/bitcoin-core-28.4/

For the release notes please see the git repository:

ht
  - 时间 2026-02-11，事件材料已返回，来源 GitHub:bitcoin/bitcoin，事件 Bitcoin Core 29.3，摘要 Bitcoin Core version 29.3 is now available from:

https://bitcoincore.org/bin/bitcoin-core-29.3/

For the release notes please see the git repository:

ht
  - 时间 2026-01-13，事件材料已返回，来源 GitHub:bitcoin/bitcoin，事件 Bitcoin Core 30.2，摘要 Bitcoin Core version 30.2 is now available from:

https://bitcoincore.org/bin/bitcoin-core-30.2/

For the release notes please see the git repository:

ht
  - 时间 2026-01-02，事件材料已返回，来源 GitHub:bitcoin/bitcoin，事件 Bitcoin Core 30.1，摘要 Bitcoin Core version 30.1 is now available from:

https://bitcoincore.org/bin/bitcoin-core-30.1/

For the release notes please see the git repository:

ht
- 事件日历：27 行。
  - 最新读数：事件 Gate Prediction Market launches World Cup section: creating a one-stop experienc，时间 2026-06-08 20:00:12，来源 Google News，粒度 event，样本 2025-09-03 至 2026-06-08
  - 历史/样本统计：事件来源分布：Google News 27 条。事件类型分布：媒体/搜索线索 27 条。宏观经济日历、项目/协议事件和搜索线索需分开解读。
  - 字段覆盖 as of, fresh until, 结束日期, 开始日期, 发布时间, 地区, 来源, 摘要, 标的代码, 标题, 链接。
  - 时间覆盖 2025-09-03 至 2026-06-08，下列摘要优先展示最新记录。
  - 时间 2026-06-08，事件材料已返回，来源 Google News，事件 Gate Prediction Market launches World Cup section: creating a one-stop experience for match predictions. - PANews，摘要 <a href="https://news.google.com/rss/articles/CBMif0FVX3lxTFA4UmZLMXlXaFZTTm5QbDFCYXJlcl8yREFFalBLczVxQnk0V1JzZ1NrS0lTakp4RmtpelRKLXR6Vm9pTGwtMzBiVEVfclZaV0lqSW
  - 时间 2026-06-04，事件材料已返回，来源 Google News，事件 11 New & Upcoming Coinbase Listings to Watch in 2026 - 99Bitcoins，摘要 <a href="https://news.google.com/rss/articles/CBMibEFVX3lxTE80Q19OekZKbjItUmlnMjFIUUZBTkRESzJha3JtWVVUVk5wYndtYy1IWmR6eFpIN21hVXFKa005NGZWc3pnbFBLbkcyN0psXzZRYU
  - 时间 2026-06-01，事件材料已返回，来源 Google News，事件 Top 10 New and Upcoming Binance Listings in 2026 - 99Bitcoins，摘要 <a href="https://news.google.com/rss/articles/CBMia0FVX3lxTE9kTFowOFNzTTJhUGlLWjF2WVh3eDdzNl83SENfU1N2cF9wMkJQa3ZvNEtxMHRQQW9JMHRvT0xwTkxsLTZtUkE4bXlva0hJejYxdG
  - 时间 2026-05-29，事件材料已返回，来源 Google News，事件 9 New Upcoming Binance Listings to Watch in June 2026 - Coinspeaker，摘要 <a href="https://news.google.com/rss/articles/CBMibkFVX3lxTE1IWGdVbVlHcDU5REVIZmZGRGRsa0R1Nm53MnhHMUp5Q2ZIaDZhdno4czBLRDNqQnBIMkF5TndlaGh1NHFBcmVlcnJFR2tfYTJwY0
  - 时间 2026-05-19，事件材料已返回，来源 Google News，事件 Best Time to Trade Crypto in India: 6 PM–1 AM IST Guide - CoinDCX，摘要 <a href="https://news.google.com/rss/articles/CBMimAFBVV95cUxQV3h4MXdkTUpFN1FQY19jMGdURkRoQ2N2QWhtLVZIem9lTnhWTnYtUUpmcF9oUnBSVmFDY3lXTVF2MTMwV2NQUk0ycFlqeEJic2
本次数据结果已经可用于正文；不要重复请求同一数据需求。
