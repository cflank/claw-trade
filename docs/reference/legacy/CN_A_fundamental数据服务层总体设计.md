# CN_A fundamental 数据服务层总体设计

> 本文档用于设计 `claw-trade` 的 CN_A 基本面资料包服务层。它是设计方案，不是实现记录。
>
> 设计目标：让 `fundamental_analyst` 拿到像 TradingAgents-CN 基本面资料层那样可写研报的底稿，同时保留 `claw-trade` 的真实工具、证据链、OpenClaw worker 和 OpenViking artifact 边界。

## 1. Verdict

推荐采用 **skill-first / MCP-later，但只能作为有条件可行方案**。

当前设计不能直接进入实现。必须先收紧 5 个边界：

1. 数据服务层不能拥有 hard gate 权威，只能产出诊断事实；最终拦截仍归 `claw-trade` 控制面。
2. 旧字段名 `analysis_permissions` 不再进入实现 schema；改成客观的 `evidence_capabilities`，只描述哪些证据可用、哪些声明不能被证据支持。
3. `derived.summary` 必须是模板化事实摘要，不得是自然语言研判。
4. `ok / quality.status / diagnostic_flags` 必须形成明确状态机，避免 `ok=true` 但实际假成功。
5. 第一版只做 Tushare + AkShare + MongoDB；Baostock 不进入 v1 主路径。

第一版不直接接 FinanceMCP MCP 服务，也不把 Tushare、AkShare、Baostock 的原子接口暴露给 worker。正式 worker 仍只看到：

- `fundamental_fundamentals_data_pack`
- `openviking_write_material`

原因：

- OpenClaw 当前前线 worker 已经按 stage policy 收敛到资料包入口。
- MCP 会增加部署、鉴权、工具权限、运行日志和跨进程调试复杂度。
- fundamental worker 的问题主要是资料包质量，不是工具数量不足。
- 稳定的数据服务层以后可以再封装成 MCP；第一版先把 schema、诊断、字段溯源和控制面 hard gate 接口做正确。

关键修正：

- 这不是 `pack-only` 的盲封装，而是 **pack 内部必须带诊断系统和明确状态机**。
- Python 不写报告，不给投资建议，不给目标价，不改 PM rating。
- MongoDB 是结构化缓存，不是流程权威；OpenViking artifact 才是运行证据和材料流转权威。

## 2. Problem

当前 CN_A fundamental worker 缺少研报底稿感，根因不是 prompt，而是资料包不像基本面资料层。

已观察到的问题：

- `fundamental_fundamentals_data_pack` 曾因 AkShare 空响应/解析失败导致财务和估值字段缺失。
- worker 在资料包失败后仍可能受报告模板压力补写公开常识或历史印象。
- 现有资料包更像少量接口结果加价格上下文，不足以支撑 TradingAgents-CN 风格的基本面报告。

需要解决的是：

- 给 worker 一份结构化、可追踪、可读、可判缺口的基本面资料包。
- 让报告中的 PE/PB/ROE、营收、净利润、现金流、主营、分红、股东等字段都有来源。
- provider 失败时返回诊断证据，而不是假成功。

### 2.1 Facts, Assumptions, Unknowns

已确认事实：

- `claw-trade` 必须保留 workflow、artifact approval 和 hard gate 权威。
- worker 必须通过 OpenClaw 真实唤醒，不能退回 Python 直接写报告。
- 600519 既有证据显示：资料包失败后，报告仍可能生成并出现无工具支撑表达。
- 用户已批准使用 MongoDB 作为结构化缓存，并允许使用 Tushare。

设计推断：

- 单入口资料包比暴露一组原子接口更符合 worker 分工。
- Tushare 更适合做 CN_A 基本面主源，AkShare 更适合做补充。
- 若 prompt 强制完整章节和买入/持有/卖出/观望评级，缺数据时更容易诱发补写。

仍未知：

- 当前 runtime 是否已经完全按 fundamental worker 自身 skill 路径加载工具。
- 新资料包 schema 是否已有真实成功输出样本。
- guard 是否能覆盖行业地位、商业模式质量、估值倾向、目标价、评级等隐性推断。
- 是否已有同标的、同日期 TradingAgents-CN 成功 fundamental 对照四件套。

## 3. Non-Goals

本方案明确不做以下事情：

- 不让 Python 生成最终基本面报告正文。
- 不让 Python 给买入、持有、卖出、观望建议。
- 不让 Python 给目标价、合理估值区间或 PM rating。
- 不把 FinanceMCP 的全部工具直接暴露给 worker。
- 不把 TradingAgents-CN 的生成式 fallback 照搬过来。
- 不用 mock、stub、fake 或 fallback success 绕过缺数据。
- 不让 OpenClaw 接管 12-worker workflow。

## 4. What To Borrow

### 4.1 From TradingAgents-CN

可借鉴：

- 统一基本面工具思路：worker 不直接调用一堆原子接口，而是调用统一基本面入口。
- A 股基本面资料结构：当前价格信息、公司信息、财务指标、估值指标、盈利能力、偿债能力、现金流、主营业务和数据说明。
- 数据源管理思想：缓存优先，多 provider 尝试，记录来源。
- 报告材料表达：给 LLM 的资料应像研报底稿，而不是数据库 dump。

不能照搬：

- 不能 fallback 到“生成基本分析”后当作成功。
- 不能让 Python 生成投资建议或基本面评分作为 worker 事实依据。
- 不能用字符串中没有 `❌` 这种弱条件判断成功。

### 4.2 From FinanceMCP

FinanceMCP 地址：`https://github.com/guangxiangdebizi/FinanceMCP`

可借鉴：

- `company_performance` 对 Tushare 财务接口的字段组织：
  - `fina_indicator`
  - `income`
  - `balancesheet`
  - `cashflow`
  - `fina_mainbz`
  - `dividend`
  - `stock_company`
  - `top10_holders`
  - `top10_floatholders`
- `stock_data` 对行情窗口和指标预取的日期处理思路。
- `hot_news_7x24` 对 token 缺失、provider 错误、调用日志的显式返回方式。
- `tushare-interfaces.md` 对高优先级接口的归类。

不能照搬：

- 不把 FinanceMCP 全工具注册给 `fundamental_analyst`。
- 不让 LLM 用 `data_type` 自己决定查哪张表。
- 不复制 FinanceMCP 的格式化 Markdown 作为证据层。
- 不把 MCP 当第一版正式依赖。

## 5. Proposed Architecture

总体架构：

```text
OpenClaw fundamental worker
  -> fundamental_fundamentals_data_pack
  -> CN_A fundamental data service
  -> input normalizer
  -> MongoDB cache inspector
  -> deterministic provider planner
  -> Tushare / AkShare fetchers
  -> field-level source mapper
  -> freshness checker
  -> missing-field diagnostic system
  -> evidence capability classifier
  -> structured data pack
  -> worker reads pack and writes report to OpenViking
```

边界：

- `claw-trade` 控制 workflow、stage policy、artifact approval 和 hard gate。
- OpenClaw 执行一个 worker 的真实模型回合。
- 数据服务层只给资料和诊断，不给最终投资判断。
- OpenViking 保存 worker report、资料包 evidence、provider attempts、hash 和 receipt。

关键边界：

- 数据服务层不得决定“报告是否通过”。它只能返回 `diagnostic_flags` 和字段证据。
- `claw-trade` 控制面读取这些诊断，执行真正 hard gate、artifact approval 和 rerun 决策。
- OpenViking 里的资料包只有被 `claw-trade` 控制面接受后，才是 approved material。

## 6. Tool Surface

正式 worker 可见工具保持最小：

```text
fundamental_fundamentals_data_pack
openviking_write_material
```

底层 provider 工具不可见：

```text
tushare.*
akshare.*
baostock.*
FinanceMCP.company_performance
FinanceMCP.stock_data
```

原因：

- worker 的职责是分析资料，不是编排数据接口。
- 原子工具太多会让 tool list 膨胀，增加误调用和漏调用风险。
- provider 失败诊断应该由资料包内部完成，不交给 LLM 猜。

后续如果需要补救能力，优先在 pack 内部增强诊断，不默认给 worker 开诊断工具。只有当 pack 内部诊断无法覆盖时，再单独设计“失败时受控诊断工具”，并需重新评审 stage policy。

受控补救原则：

- worker 不直接调用 Tushare、AkShare、Baostock 或 FinanceMCP 原子接口。
- 资料包失败后的重试由 `claw-trade` 控制面决定，不由 LLM 自己决定。
- 补救调用仍必须回到同一个资料包入口，或者由后续单独批准的诊断入口执行。
- 每次补救都必须生成新的 `provider_attempts`、输入参数、时间戳和 content hash。

## 7. Provider Strategy

### 7.1 Provider 分工

MongoDB：

- 结构化缓存。
- 保存财务表、估值快照、行情快照、字段来源和抓取时间。
- 只有通过缓存体检时才可命中。

Tushare：

- CN_A 基本面主源。
- 负责财务指标、三张报表、主营业务、分红、股东/股本、公司基础信息、日度估值指标。

AkShare：

- 补充源。
- 用于公司信息、行情快照、估值快照、部分财务摘要和 Tushare 缺口补充。

Baostock：

- 低优先级补充源。
- 不进入 v1 主路径。
- 只有在 Tushare + AkShare + MongoDB 的状态机和验证样本稳定后，才作为 v2 明确字段补充源重新评审。
- 即使进入 v2，也只用于它真实覆盖且字段口径明确的基础数据，不承担完整基本面主源。

OpenViking：

- 长期证据记忆。
- 保存 approved data pack、raw payload refs、provider attempts、worker report、hash、receipt。
- 不替代 MongoDB 做结构化查询缓存。

### 7.2 v1 路由策略

不采用简单硬编码成功链，也不采用未定义算法的“健康度路由”：

```text
MongoDB -> Tushare -> AkShare -> Baostock
```

v1 采用确定性 provider plan：

```text
1. MongoDB cache inspector
   - 若字段、报告期、公告日、抓取时间、来源、schema_version 合格，则 cache hit。
   - 若不合格，记录 cache_miss/cache_stale/cache_schema_invalid。
   - cache hit 只代表可复用字段，不直接推出 ok=true。

2. Tushare primary fetch
   - 已批准使用 Tushare，因此第一版作为主取数来源；实际 token、积分、限流和接口可用性必须由运行诊断证明。
   - token 缺失、额度不足、超时、空返回、接口错误都必须记录。

3. AkShare supplement
   - 补齐公司信息、行情快照、估值快照或 Tushare 未覆盖字段。
   - 不允许覆盖 Tushare 已成功且口径明确的核心财务字段，除非记录冲突。

4. Baostock
   - v1 不调用。
   - v2 若启用，必须先定义字段白名单、口径映射、失败样本和冲突处理。
```

后续若引入 provider health router，必须先写清：

- 观测窗口。
- provider 分数计算。
- 熔断阈值和恢复条件。
- 每个接口的 timeout、retry budget、退避策略。
- 哪些失败允许 fallback，哪些失败必须直接标记为 core_missing。

没有这些规则前，不允许把“健康度路由”作为实现依据。

跨源冲突处理：

- 同一字段多源值不一致时，不静默选一个。
- 保留所有候选值、来源、日期、口径。
- 若冲突影响 PE/PB/ROE、净利润、净资产、现金流等核心结论，`evidence_capabilities` 必须降级，并在 `diagnostic_flags` 中记录 `cross_provider_conflict`。

## 8. MongoDB Cache Design

MongoDB 不是让人手工判断可靠性，可靠性由服务层自己体检。

每条缓存记录至少需要：

```json
{
  "ticker": "600519.SH",
  "market": "CN_A",
  "provider": "tushare",
  "api_name": "fina_indicator",
  "report_period": "20251231",
  "announce_date": "20260402",
  "fetched_at": "2026-05-06T21:36:00-04:00",
  "schema_version": "cn_a_fundamental_pack.v1",
  "payload_hash": "sha256:...",
  "raw_payload_ref": "viking://resources/workflow/...",
  "unit": "CNY",
  "scale": "yuan",
  "metric_definition_version": "tushare.daily_basic.2026-05",
  "fields": {}
}
```

缓存体检规则：

- 没有 `report_period`：不可用于财报字段。
- 没有 `announce_date`：不可证明披露时点，降级为可疑字段。
- 没有 `fetched_at`：不可判断抓取新鲜度。
- 没有 `provider/api_name`：不可作为证据字段。
- schema 版本不匹配：不可直接命中。
- `unit` 或 `scale` 缺失：不可进入数值事实字段。
- `payload_hash` 或 `raw_payload_ref` 缺失：不可作为可追溯证据字段。
- 同一 ticker/report_period/api_name 多版本并存时，必须按 `announce_date/fetched_at/schema_version` 选取，并保留被替换版本引用。
- 财报字段过期：可读但不可支撑“当前基本面”结论。
- 价格、PE/PB、市值类字段必须比财报字段更严格，新鲜度窗口更短。

缓存命中不是 `ok=true`。只有核心字段门槛达标，才允许 `ok=true`。

MongoDB 写入要求：

- 写入必须幂等，同一 `provider/api_name/ticker/report_period/announce_date/payload_hash` 不重复制造新事实。
- 不同 provider 的同名字段不得静默覆盖。
- 单位换算必须写入字段来源，不能只在展示层转换。
- TTL 只适用于价格、估值快照等短周期字段；财报原始 payload 应长期保留引用。

## 9. Diagnostic System

诊断系统内置在 `fundamental_fundamentals_data_pack` 内部。

它回答工程事实：

- 哪个 provider 被尝试了。
- 哪个 provider 没尝试，为什么。
- 哪个接口成功、失败、超时、空响应。
- 哪些核心字段拿到了。
- 哪些核心字段缺失。
- 缺失原因是什么。
- MongoDB 命中是否可信。
- 数据是否过期。
- 哪些证据族可用于报告，哪些声明缺证据支撑。

它不做：

- 不写投资建议。
- 不写买入、持有、卖出、观望。
- 不给目标价。
- 不评价公司好坏。
- 不替 worker 写基本面结论。

`provider_attempts` 必填。缺失即工具结果无效。

诊断系统输出的是事实，不是授权。字段命名使用 `evidence_capabilities`，不得使用 `analysis_permissions`。

`evidence_capabilities` 只回答：

- 公司信息是否有证据。
- 财务字段是否足够支撑静态描述。
- 多期字段是否足够支撑趋势描述。
- 估值字段是否足够支撑估值快照描述。
- 哪些声明类型必须禁止，例如目标价、高估/低估、投资评级。

它不回答：

- 是否买入、持有、卖出。
- 公司是不是优秀。
- 估值是否有吸引力。
- PM rating 是否应调整。

示例：

```json
{
  "provider_attempts": [
    {
      "provider": "mongodb",
      "role": "cache",
      "status": "miss",
      "reason": "no_record_for_ticker",
      "started_at": "2026-05-06T21:36:00-04:00",
      "ended_at": "2026-05-06T21:36:00-04:00",
      "field_coverage": []
    },
    {
      "provider": "tushare",
      "api_name": "fina_indicator",
      "status": "success",
      "reason": null,
      "field_coverage": ["roe", "roa", "gross_margin", "netprofit_margin"],
      "report_period": "20251231",
      "announce_date": "20260402",
      "fetched_at": "2026-05-06T21:36:03-04:00"
    }
  ]
}
```

## 10. Data Pack Schema

建议第一版 schema：

```json
{
  "schema_version": "cn_a_fundamental_pack.v1",
  "ok": true,
  "profile": {
    "ticker": "600519.SH",
    "canonical_code": "600519.SH",
    "company_name": "贵州茅台",
    "market": "CN_A",
    "currency": "CNY",
    "currency_symbol": "¥"
  },
  "query": {
    "start_date": "2026-04-06",
    "end_date": "2026-05-06",
    "current_date": "2026-05-06"
  },
  "facts": {
    "company_profile": {},
    "price_context": {},
    "valuation": {},
    "financial_indicators": {},
    "income_statement": [],
    "balance_sheet": [],
    "cash_flow": [],
    "business_segments": [],
    "dividend": [],
    "shareholders": {}
  },
  "field_sources": {},
  "provider_attempts": [],
  "missing_fields": [],
  "freshness": {},
  "evidence_capabilities": {
    "company_profile": {
      "status": "available",
      "supported_fields": ["company_name", "industry", "main_business"]
    },
    "financial_snapshot": {
      "status": "available",
      "supported_fields": ["revenue", "net_profit", "roe", "debt_to_assets"]
    },
    "financial_trend": {
      "status": "partial",
      "supported_periods": ["20231231", "20241231", "20251231"],
      "blocked_claims": []
    },
    "valuation_snapshot": {
      "status": "available",
      "supported_fields": ["pe_ttm", "pb", "total_mv"]
    },
    "valuation_judgment": {
      "status": "blocked",
      "reason": "data_service_never_decides_overvalued_or_undervalued"
    },
    "target_price": {
      "status": "blocked",
      "reason": "data_service_never_supports_target_price"
    },
    "rating": {
      "status": "blocked",
      "reason": "data_service_never_supports_buy_hold_sell_rating"
    }
  },
  "diagnostic_flags": [],
  "derived": {
    "summary": [],
    "summary_is_evidence": false
  },
  "quality": {
    "status": "ok",
    "is_partial": false,
    "warnings": []
  },
  "evidence": {
    "raw_payload_refs": [],
    "content_hash": "sha256:..."
  }
}
```

`facts` 是事实层。worker 可以引用，但必须看字段来源。

`evidence_capabilities` 是机器约束字段，不是 Python 授权 worker 写报告。它只描述证据能力，最终是否允许成稿、是否触发 rerun、是否 hard fail，由 `claw-trade` 控制面决定。

`diagnostic_flags` 是资料包层诊断标签，不是最终 gate 结果。

`derived.summary` 是派生摘要，不是证据字段。它只能列事实和缺口，必须是数组形式的模板化短句，不能包含建议、评级、目标价、因果推断或公司好坏判断。

## 11. Field-Level Provenance

每个关键字段必须有来源。

示例：

```json
{
  "field_sources": {
    "valuation.pe_ttm": {
      "value": 23.4,
      "provider": "tushare",
      "api_name": "daily_basic",
      "as_of": "2026-05-06",
      "fetched_at": "2026-05-06T21:36:03-04:00",
      "stale_flag": false,
      "payload_hash": "sha256:..."
    },
    "financial_indicators.roe": {
      "value": 31.2,
      "provider": "tushare",
      "api_name": "fina_indicator",
      "report_period": "20251231",
      "announce_date": "20260402",
      "fetched_at": "2026-05-06T21:36:03-04:00",
      "stale_flag": false,
      "payload_hash": "sha256:..."
    }
  }
}
```

没有来源的字段不能进入 `facts`，只能进入 `missing_fields` 或 `warnings`。

## 12. Minimum Data Threshold

最低门槛参考 TradingAgents-CN 和原版 TradingAgents 的基本面资料要求。

### 12.1 完整基本面报告最低字段

要允许 worker 写完整基本面分析，至少需要：

- 公司基本信息：名称、代码、市场、行业或主营。
- 价格上下文：最近收盘价、日期、成交量或市值上下文。
- 估值：PE 或 PE_TTM、PB、市值，至少两个可用。
- 财务指标：ROE、ROA、毛利率、净利率、资产负债率，至少三个可用。
- 利润表：营业收入、净利润、EPS 或可替代盈利字段。
- 资产负债表：总资产、总负债、股东权益。
- 现金流：经营现金流，最好有自由现金流或现金及等价物。
- 主营业务构成：产品、地区或行业构成，至少一个维度。
- 分红：最近分红或明确无数据。
- 股东/股本：前十大股东、流通股东、股本结构或明确无数据。

要允许 worker 写趋势分析，还需要：

- 至少两个可比报告期，才能写“上升/下降/改善/恶化”。
- 至少两个同口径年度报告期，才能写年度趋势。
- 至少去年同期或连续季度口径，才能写同比或环比。
- 同一指标跨 provider 口径不一致时，只能写“口径冲突”，不能写趋势。

要允许 worker 写“报告感”而不是静态拼表，资料包至少应提供：

- 多期财务指标序列。
- 估值快照与日期。
- 主营或业务构成证据。
- 分红或股东信息中至少一类补充材料。
- 明确缺口说明。

### 12.2 部分报告允许范围

如果只有价格和公司信息：

- `ok=false`
- `quality.status=failed`
- `evidence_capabilities.financial_snapshot.status=blocked`
- `evidence_capabilities.valuation_snapshot.status=blocked`
- worker 只能写数据缺口和影响，不能补写历史财务常识。

如果缺主营、分红、股东，但三表和估值完整：

- `ok=true`
- `quality.status=partial`
- 对缺失章节必须写缺口。

如果估值字段缺失：

- 禁止判断高估、低估、合理。
- 禁止给目标价。

如果核心财务字段缺失：

- 不允许输出“买入/持有/卖出/观望”的基本面评级。
- prompt 应允许 worker 明确写“证据不足，无法给出基本面评级”。

## 13. Reader Brief Boundary

原 `reader_brief` 改为 `derived.summary`。

Python 可以生成中文资料摘要，但只能做“字段翻译和缺口整理”，不能做研判。

实现边界：

- 使用固定模板生成，不调用 LLM。
- 每条摘要必须对应一个 `field_sources`、`missing_fields` 或 `provider_attempts` 记录。
- 每条摘要最多表达一个事实或一个缺口。
- 最多 8 条，避免把摘要写成报告正文。
- 不生成章节标题、不写投资结论、不写公司评价。

允许写：

- “已取得 2025 年年报期的营收、净利润、ROE、资产负债率。”
- “PE_TTM 来自 Tushare daily_basic，日期为 2026-05-06。”
- “主营业务构成未取得，provider 返回空。”

禁止写：

- “公司基本面优秀。”
- “估值具备吸引力。”
- “建议持有/买入/卖出。”
- “股价被低估。”
- “目标价为某区间。”
- “护城河深厚、长期价值突出”这类无字段证据的判断。

不合格摘要示例：

- “公司盈利能力强，因此估值合理。”
- “作为行业龙头，公司长期配置价值突出。”
- “虽然缺少最新 PE，但从历史看仍可持有。”

## 14. Failure Handling

失败不等于停止所有信息返回，但必须停止假成功。

### 14.1 Tushare token 缺失

返回：

```json
{
  "provider": "tushare",
  "status": "skipped",
  "reason": "missing_token"
}
```

继续尝试 AkShare，但最终质量必须标明 Tushare 未执行。

### 14.2 Tushare 额度或接口失败

记录接口、错误码、错误信息、是否重试、重试次数。

不能静默用 AkShare 覆盖后 `ok=true`，除非核心字段门槛仍然达标且 field_sources 明确来自 AkShare。

### 14.3 AkShare 空响应

记录：

- provider
- api_name
- exception type
- raw response 是否为空
- 字段覆盖率为 0

不能把空响应包装为 `fundamentals` 成功。

### 14.4 MongoDB 过期或 schema 不合格

返回 cache diagnostic：

```json
{
  "provider": "mongodb",
  "status": "stale",
  "reason": "missing_announce_date"
}
```

该数据可保留为 evidence，但不能作为当前事实字段。

## 15. State Machine and Control-Plane Gates

资料包层不执行最终 hard gate。资料包层只返回状态和诊断，`claw-trade` 控制面执行真正 hard gate。

状态机：

```text
ok=false, quality.status=failed
  -> 核心资料不足或 provider 结果不可用。
  -> 控制面应只允许 worker 写数据缺口，不得接受完整基本面报告。

ok=true, quality.status=partial
  -> 核心门槛达标，但存在非核心缺口或趋势能力不足。
  -> 控制面可接受有限分析，缺失章节必须显式说明缺口。

ok=true, quality.status=ok
  -> 核心门槛达标，字段来源、口径、新鲜度均通过。
  -> 控制面可接受完整基本面分析，但仍不得接受目标价或 PM rating。
```

不允许出现：

- `ok=true` 但核心字段门槛未达标。
- `quality.status=ok` 但 `missing_fields` 包含核心字段。
- provider 最终失败但 `facts` 填入无来源字段。
- MongoDB cache stale 却作为当前事实字段。

资料包必须返回 `diagnostic_flags`，供控制面 gate 使用：

```json
[
  {
    "code": "core_financials_missing",
    "severity": "fail",
    "evidence_ref": "provider_attempts[2]",
    "message": "Tushare income returned empty and no valid cache hit"
  }
]
```

控制面 hard gate 建议规则：

- `provider_attempts` 缺失：fail。
- 核心字段无来源：fail 或降级。
- `ok=true` 但核心字段门槛未达标：fail。
- provider 最终失败却包装成功：fail。
- 估值字段缺失但 worker 给出高估/低估/合理判断：fail。
- PE/PB/ROE 等字段没有 source：fail。
- MongoDB 命中但无 report_period/announce_date/fetched_at：不能用于事实字段。
- `derived.summary` 出现建议、评级、目标价、因果结论：fail。

注意：这些 gate 不写在数据服务层里直接决定 workflow。数据服务层只提供可判定输入；`claw-trade` 控制面负责拒绝 artifact、请求 rerun 或终止该 worker 输出。

## 16. Verification Plan

第一轮验证不能只用 600519 一个失败样本。

最小可置信集：

1. 600519 失败样本。
   - 证明 AkShare 空响应、Tushare 失败或 Mongo miss 时不会假成功。
2. 一个 CN_A 成功样本。
   - 证明 Tushare 能拿到三表、指标、估值和主营字段。
3. 一个结构性失败样本。
   - 模拟或真实捕获 token 缺失、空 JSON、非 JSON、超时。
4. 一个 guard 样本。
   - 工具失败时，worker 不能补写历史 PE/PB/ROE、毛利率、行业地位或目标价。
5. TradingAgents-CN 同标的对照。
   - 对比 final prompt、tool result、LLM back、report。
   - 必须同层级对比，不能拿一边 tool_use 和另一边成稿报告比较。

每个验证报告必须包含：

- visible tools
- provider request
- tool calls
- data pack raw output
- provider_attempts
- field_sources
- missing_fields
- OpenViking receipt
- guard result
- report

实现时必须补齐可复现验收命令。目标命令形态如下，命令名可以按实际测试文件调整，但断言不能省略：

```text
uv run pytest tests/fundamental/test_cn_a_data_pack_schema.py
uv run pytest tests/fundamental/test_cn_a_data_pack_failure_samples.py
uv run pytest tests/fundamental/test_cn_a_data_pack_state_machine.py
uv run pytest tests/fundamental/test_cn_a_report_claim_guard.py
```

失败样本基线：

| 样本 | 预期状态 | 必须断言 |
| --- | --- | --- |
| Tushare token 缺失 | `ok=false` 或 `partial` | `provider_attempts.reason=missing_token`，不得伪造 Tushare 字段 |
| AkShare 空 JSON | `ok=false` 或字段级缺失 | `field_coverage=[]`，不得包装为 fundamentals 成功 |
| provider 非 JSON | `ok=false` | 记录 exception type，`facts` 不写入无来源字段 |
| provider timeout | `ok=false` 或 `partial` | 记录 timeout、retry count、duration |
| MongoDB 过期 | cache stale | stale 字段不得进入当前事实层 |
| 跨源冲突 | `partial` 或 fail | 记录所有候选值，不静默覆盖 |
| 工具失败后报告补写 | guard fail | 报告中的 PE/PB/ROE、行业地位、目标价、评级必须被拦截 |

报告感验收需要单独评分，不能只看链路成功：

| 维度 | 通过要求 |
| --- | --- |
| 证据密度 | 关键判断旁边能追溯到资料包字段 |
| 非日志化表达 | 正文像研报底稿，不逐条复述接口日志 |
| 缺口解释 | 缺字段时写清 provider、接口、原因、影响范围 |
| 趋势能力 | 趋势句必须有多期可比字段 |
| CN 对齐 | 与 TradingAgents-CN 同标的同日期报告做 final prompt/tool result/LLM back/report 同层对比 |
| 真实性 | 不出现无来源 PE/PB/ROE、目标价、评级、行业地位判断 |

通过线：

- 真实性相关维度必须全部通过。
- 报告感维度至少达到“可读研报底稿”，否则不能声明 CN_A fundamental parity。

## 17. Risks

架构风险：

- 数据服务层如果继续膨胀，会把业务判断从 worker 挪回 Python。
- runtime 目前存在默认路径和旧工具兼容债务，后续需要收敛。
- `evidence_capabilities` 如果写成“允许/禁止分析”的主观授权，会越过数据层边界。
- `diagnostic_flags` 如果直接在数据服务层终止 workflow，会越过 `claw-trade` 控制面。

数据风险：

- MongoDB 旧数据可能新鲜度不足。
- MongoDB 可能混用报告期、公告日、单位或不同 provider 口径。
- Tushare token、积分、限流会影响主路径稳定性。
- AkShare 接口可能空响应或字段变动。
- Baostock 覆盖面有限，因此 v1 不进入主路径。

工具风险：

- 单入口资料包如果诊断不足，会让 worker 无法知道缺口根因。
- 原子工具如果暴露过多，会让 LLM 接管数据编排。

报告风险：

- 模板压力会诱发 worker 补写公开常识。
- `derived.summary` 如果写成分析段落，会污染证据层。
- 当前 CN_A prompt 若继续强制买入/持有/卖出/观望评级，会在缺数据场景诱发 unsupported rating。

验证风险：

- 只跑 600519 失败样本不能证明成功路径。
- 只看最终报告不能证明工具 schema、provider request 和资料包真实进入模型。
- 没有固定失败样本时，后续容易把“链路通了”误认为“真实性达标”。

## 18. Implementation Phases

### Phase 0: Boundary Closure

- 实现 schema 时不得引入 `analysis_permissions`，统一使用 `evidence_capabilities`。
- 明确资料包只产出 `diagnostic_flags`，控制面执行 hard gate。
- 明确 `derived.summary` 模板规则。
- 明确 `ok / quality.status / diagnostic_flags` 状态机。
- 补 CN_A fundamental prompt 的缺数降级规则：核心数据缺失时允许写“证据不足，无法给出基本面评级”。
- 验证 runtime 实际 visible tools 与 worker skill 来源，确认旧工具不会泄漏给 fundamental worker。

### Phase 1: Schema and Diagnostics

- 定义 `cn_a_fundamental_pack.v1`。
- 实现 provider_attempts、field_sources、missing_fields、freshness、evidence_capabilities、diagnostic_flags。
- 实现 MongoDB cache inspector。
- 实现 `derived.summary` 边界检查。

### Phase 2: Tushare Primary Source

- 接入 `stock_company` / `stock_basic`。
- 接入 `daily_basic`。
- 接入 `fina_indicator`。
- 接入 `income`。
- 接入 `balancesheet`。
- 接入 `cashflow`。
- 接入 `fina_mainbz`。
- 接入 `dividend`。
- 接入 `top10_holders` / `top10_floatholders`。

### Phase 3: AkShare Supplements

- AkShare 补公司信息、行情、估值快照、财务摘要。
- 增加跨源冲突记录。

### Phase 4: Guard and Verification

- 加控制面 hard gates。
- 加 report claim 检查。
- 跑最小可置信验证集。
- 与 TradingAgents-CN 同标的成稿对比。

### Phase 5: Baostock v2 Review

- 只有在 v1 通过失败样本、成功样本和 CN 对照后才评审 Baostock。
- 明确 Baostock 字段白名单、口径、失败签名和冲突规则。

## 19. Final Recommendation

总体方案 **有条件可行**。先修边界，再写实现。

最终设计应坚持：

- worker 只看一个基本面资料包入口。
- 诊断系统在资料包内部自动完成。
- MongoDB 做结构化缓存，Viking 做长期证据记忆。
- Tushare 做 CN_A 基本面主源，AkShare 做显式补充，Baostock 留到 v2 评审。
- 每个关键字段都必须有来源、新鲜度和缺口状态。
- 不满足字段门槛时，资料包必须给出可判定诊断，控制面据此阻止完整估值和基本面结论进入 artifact。

实施顺序不能跳过 Phase 0。否则最大风险不是取不到数据，而是系统再次产生“工具失败但报告成功”的假成功路径。
