# HK 数据层修改方案

## 结论

当前默认 `/report` 路径里，只有 HK 的 `social` 数据域存在同类缺口。

默认 frontline worker 只包括：

- `market_analyst`
- `fundamental_analyst`
- `news_analyst`
- `social_analyst`

默认 prefetch 数据域只包括：

- `market`
- `fundamental`
- `news`
- `social`

其中 HK 目前是：

| 市场 | market | fundamental | news | social |
| --- | ---: | ---: | ---: | ---: |
| CN_A | 6 | 4 | 3 | 6 |
| US | 2 | 4 | 4 | 1 |
| HK | 1 | 5 | 3 | 0 |
| CRYPTO | 14 | 4 | 2 | 1 |

HK 报告失败的直接原因是：HK `social_analyst` 会调用 `claw_get_social_pack`，但 HK `social` 没有任何数据请求，`report-prefetch.json` 不包含 `social` domain，工具调用时失败为 `manifest does not contain domain: social`。

## 明确不做

US/HK/CRYPTO 不补 `policy`、`hot_money`、`lockup`。

原因：这三个是 A 股专用 worker / 数据域，不属于 US/HK/CRYPTO 默认 `/report` frontline 路径。它们为空不是本次问题，不应被当成缺口。

本次也不做以下事情：

- 不给 US/HK/CRYPTO 增加 A 股专用 worker。
- 不把 CN_A 的雪球、股吧、热榜 provider 借给 HK。
- 不新增模型可见的 `hk_*` 工具名。
- 不伪造 HK 社交平台热度、情绪分数、讨论量、X/雪球/富途/股吧结论。
- 不改 OpenClaw 源码。

## 修改范围

### 1. 修正 HK stage intent 边界

修改文件：

- `agents/market_analyst/STAGES.yaml`
- `agents/fundamental_analyst/STAGES.yaml`
- `agents/news_analyst/STAGES.yaml`
- `agents/social_analyst/STAGES.yaml`

把 HK profile 的工具 intent 从 CN_A intent 改为 HK 专属 intent：

| worker | 当前 HK intent | 修改为 |
| --- | --- | --- |
| `market_analyst` | `cn_a_market_data` | `hk_market_data` |
| `fundamental_analyst` | `cn_a_fundamentals_data` | `hk_fundamentals_data` |
| `news_analyst` | `cn_a_news_data` | `hk_news_data` |
| `social_analyst` | `cn_a_social_sentiment` | `hk_social_sentiment` |

注意：这是 stage/profile 配置边界，不改变模型可见工具名。模型仍只看到 `claw_get_market_pack`、`claw_get_fundamental_pack`、`claw_get_news_pack`、`claw_get_social_pack`。

### 2. 增加 HK intent 到统一工具的映射

修改文件：

- `src/claw_trade/config/tool_names.py`

新增映射：

```python
"hk_market_data": ("claw_get_market_pack",),
"hk_fundamentals_data": ("claw_get_fundamental_pack",),
"hk_news_data": ("claw_get_news_pack",),
"hk_social_sentiment": ("claw_get_social_pack",),
```

同时更新 contract tests，旧的“HK 复用 CN_A intent”断言应删除或改为 HK intent 断言。

### 3. 补 HK social 数据请求

修改文件：

- `src/claw_trade/reports/data_pack_bridge.py`

把 HK social 从空请求：

```python
"social": (),
```

改为：

```python
"social": (
    ("social_signal", "event", ("source", "timestamp", "title", "url", "symbol_id")),
),
```

字段选择理由：

- `source`、`timestamp`、`title`、`url`、`symbol_id` 能表达“可审计的舆情/新闻热度线索”。
- 第一版不要求 `score`、`sentiment`、`mentions`，因为 HK 目前没有稳定完整社交情绪源，不能伪造情绪分数或讨论量。

### 4. 补 HK social provider capability

修改文件：

- `src/claw_trade/data_gateway/providers/plugins/hk/__init__.py`

推荐最小实现：扩展现有 `HKGoogleNewsDiscoveryPlugin`，增加一个 `social_signal` endpoint。

新增 capability：

```python
endpoint_capability(
    endpoint_id="social_signal_news_heat",
    market="HK",
    data_type="social_signal",
    source_role="sentiment",
    granularity=("event",),
    fields=("source", "timestamp", "title", "url", "symbol_id"),
    priority_rank=50,
    can_be_formal_fact_source=False,
)
```

fetch 逻辑：

- 对 `social_signal_news_heat` 使用 Google News RSS 查询。
- 查询词可先用：`"{symbol} Hong Kong stock discussion sentiment"`。
- 复用现有 RSS 解析逻辑，但生成 `social_signal` row。
- `published_at` 映射为 `timestamp`。
- row 必须包含 `dataset="social_signal"`、`market="HK"`、`symbol_id`、`provider_lineage`、`source_roles=("sentiment",)`。
- `quality_flags` 应包含 `not_formal_fact_source`，避免后续把搜索线索升级为正式事实源。

语义边界：

- 这不是完整 HK 社交平台情绪。
- 这是“有限舆情/新闻热度线索”。
- 如果来源返回空，数据层应返回可审计 empty/missing，而不是让 prefetch manifest 缺 `social` domain。

### 5. 修正 HK social model-visible 文案

必要时只做很小修改，位置：

- `src/claw_trade/reports/data_pack_bridge.py`

目标：

- `social` 包有数据时，worker 能看到来源、时间、标题、链接。
- `social` 包为空时，worker 看到“HK 社交/讨论热度来源返回空或未配置”的明确缺口。
- 不允许输出“已获得完整社交情绪”“讨论热度显著升温”这类无证据结论。

## 测试计划

### Focused tests

运行：

```bash
uv run pytest \
  tests/contracts/test_tool_registry_contract.py \
  tests/contracts/test_stage_tool_policy_contract.py \
  tests/contracts/test_worker_prompt_alignment_policy.py \
  tests/contracts/test_provider_capabilities.py \
  tests/unit/data_gateway/test_provider_selector.py \
  tests/unit/reports/test_data_pack_bridge.py
```

### 必须覆盖的断言

- HK 四个 frontline profile 使用 HK intent，不再使用 CN_A intent。
- HK intent 映射到统一 `claw_get_*_pack` 工具。
- 模型可见工具仍是统一工具，不出现 `hk_market_data`、`hk_fundamental_data`、`hk_news_data`、`hk_social_sentiment`。
- HK `social` 会生成 `social_signal` request。
- provider registry 有 HK `social_signal` capability。
- HK 默认 frontline 四域 `market/fundamental/news/social` 都进入 prefetch manifest。
- 测试不检查 US/HK/CRYPTO 的 `policy/hot_money/lockup`，因为它们是 A 股专用域。

## Live 验收

按固定 runtime 入口执行 HK fresh report：

```bash
scripts/start-control-runtime.sh -- uv run python scripts/run_claw_trade_fresh_report.py \
  --ticker 00700.HK \
  --company-name 腾讯控股 \
  --market HK \
  --profile HK \
  --currency HKD \
  --currency-symbol 'HK$'
```

验收标准：

- run 完成，生成 final report。
- `data-layer/report-prefetch.json` 中 `domains` 包含 `social`。
- `data_results` 中存在 `social_signal` 结果，状态可以是 `ready`、`partial`、`missing` 或 `error`，但必须有明确来源尝试或缺口证据。
- `social_analyst` 的 `tool-calls.json` 不再出现 `manifest does not contain domain: social`。
- social 报告不编造 HK 社交平台情绪、讨论量、热度排名或情绪分数。
- final report 能如实呈现 HK 社交数据限制。

## 成功标准

本次修改完成后，HK `/report` 不应因为 social domain 没有进入 manifest 而失败。

如果 HK social 来源没有返回可用数据，系统应继续生成报告，并把社交证据不足写成明确数据缺口；不能跳过 worker，不能伪造社交数据，不能借 CN_A 数据源。
