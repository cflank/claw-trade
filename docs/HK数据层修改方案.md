# HK 数据层修改方案（已实施）

## 结论

修复前，默认 `/report` 路径里只有 HK 的 `social` 数据域存在同类缺口。

当前 `main` 已修复 HK `social` 数据层路径：HK 默认 prefetch 会生成 `social_signal/event` 请求，provider selector 能选到 `hk_google_news/social_signal_news_heat`，live HK `/report` 已不再因为 `social` domain 缺失失败。

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

当前代码中的默认请求数量是：

| 市场 | market | fundamental | news | social |
| --- | ---: | ---: | ---: | ---: |
| CN_A | 6 | 4 | 3 | 6 |
| US | 2 | 4 | 4 | 1 |
| HK | 1 | 5 | 3 | 1 |
| CRYPTO | 14 | 4 | 2 | 1 |

修复前 HK 报告失败的直接原因是：HK `social_analyst` 会调用 `claw_get_social_pack`，但 HK `social` 没有任何数据请求，`report-prefetch.json` 不包含 `social` domain，工具调用时失败为 `manifest does not contain domain: social`。

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
- `agents/market_analyst/skills/hk-market-data/SKILL.md`
- `agents/fundamental_analyst/skills/hk-fundamental-data/SKILL.md`
- `agents/news_analyst/skills/hk-news-data/SKILL.md`
- `agents/social_analyst/skills/hk-social-data/SKILL.md`
- `agents/market_analyst/skills/manifest.yaml`
- `agents/fundamental_analyst/skills/manifest.yaml`
- `agents/news_analyst/skills/manifest.yaml`
- `agents/social_analyst/skills/manifest.yaml`

已把 HK profile 的工具 intent 从 CN_A intent 改为 HK 专属 intent：

| worker | 修复前 HK intent | 当前 HK intent |
| --- | --- | --- |
| `market_analyst` | `cn_a_market_data` | `hk_market_data` |
| `fundamental_analyst` | `cn_a_fundamentals_data` | `hk_fundamentals_data` |
| `news_analyst` | `cn_a_news_data` | `hk_news_data` |
| `social_analyst` | `cn_a_social_sentiment` | `hk_social_sentiment` |

注意：这是 stage/profile 配置边界，不改变模型可见工具名。模型仍只看到 `claw_get_market_pack`、`claw_get_fundamental_pack`、`claw_get_news_pack`、`claw_get_social_pack`。

同时已补齐 HK skill mount：

| worker | 新增或启用 skill | 暴露工具 |
| --- | --- | --- |
| `market_analyst` | `hk-market-data` | `claw_get_market_pack` |
| `fundamental_analyst` | `hk-fundamental-data` | `claw_get_fundamental_pack` |
| `news_analyst` | `hk-news-data` | `claw_get_news_pack` |
| `social_analyst` | `hk-social-data` | `claw_get_social_pack` |

这些 HK skill 是很薄的 wrapper，只声明对应统一资料包工具和 HK 使用边界；不写报告正文，不做投资判断，不调用 provider，不放 CN_A 平台说明。

如果 `hk-fundamental-data` 目录已存在但没有 `SKILL.md`，补 `SKILL.md`，并把它加入 `agents/fundamental_analyst/STAGES.yaml` 的 `skills.mounted` 和 `agents/fundamental_analyst/skills/manifest.yaml`。

### 2. 增加 HK intent 到统一工具的映射

修改文件：

- `src/claw_trade/config/tool_names.py`

已新增映射：

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

已把 HK social 从空请求：

```python
"social": (),
```

改为当前实现：

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

当前实现：扩展现有 `HKGoogleNewsDiscoveryPlugin`，增加一个 `social_signal` endpoint。

新增 capability：

```python
endpoint_capability(
    endpoint_id="social_signal_news_heat",
    market="HK",
    data_type="social_signal",
    source_role="discovery",
    granularity=("event",),
    fields=("source", "timestamp", "title", "url", "symbol_id"),
    priority_rank=50,
    can_be_formal_fact_source=False,
)
```

fetch 逻辑：

- 对 `social_signal_news_heat` 使用 Google News RSS 查询。
- 查询词先用保守表达，避免暗示真实社交平台覆盖：`"{symbol} Hong Kong stock investor discussion"`；实现时保留 query 到 provider attempt，便于复盘噪音和空结果。
- 复用现有 RSS 解析逻辑，但生成 `social_signal` row。
- `published_at` 映射为 `timestamp`。
- row 必须包含 `dataset="social_signal"`、`market="HK"`、`symbol_id`、`provider_lineage`、`source_roles=("discovery",)`。
- `quality_flags` 应包含 `not_formal_fact_source`，避免后续把搜索线索升级为正式事实源。

语义边界：

- 这不是完整 HK 社交平台情绪，也不是 sentiment provider。
- 这是“有限舆情/新闻热度线索”。
- 如果来源返回空，数据层应返回可审计 empty/missing，而不是让 prefetch manifest 缺 `social` domain。
- 报告里只能说“公开新闻/搜索线索显示的讨论线索有限或存在若干相关报道”，不能说“社交情绪升温/转弱/一致乐观”等没有社交平台证据的结论。

### 5. 修正 HK social model-visible 文案

已只改数据包可见文案，未改 worker prompt。

修改位置：

- `src/claw_trade/reports/data_pack_bridge.py`

实现路径：

- 在 `_model_visible_text()` 中增加一个窄分支：`market == Market.HK and domain == "social"`。
- 当有 `social_signal` row 时，说明这些 row 是 `Google News/公开搜索发现线索`，不是正式事实源，也不是完整社交情绪样本；如果状态是 `partial`，具体缺口继续由资料包的“数据缺口”段呈现。
- 当 `ready_results` 为空时，追加明确说明：`HK 社交资料包未取得可用公开讨论/热度线索；只能把社交证据视为缺口，不能补写情绪方向、讨论量或平台观点。`
- 不修改 `agents/social_analyst/prompts/HK.md`，避免把数据缺口策略写进 prompt 风格层。

目标：

- `social` 包有数据时，worker 能看到来源、时间、标题、链接。
- `social` 包为空时，worker 看到“HK 社交/讨论热度来源返回空或未配置”的明确缺口。
- 不允许输出“已获得完整社交情绪”“讨论热度显著升温”这类无证据结论。

## 已知风险

- Google News RSS 对港股小票可能返回空、噪音或非目标公司内容。这不是 blocker；实现必须保留 provider attempt、query、HTTP 观察和 empty/missing gap。
- `social_signal_news_heat` 只是让 HK social domain 有可审计路径，不能替代雪球、富途、X、Reddit 等真实社交平台。
- provider selector 能否路由到该 endpoint 需要测试证明，不能只靠 capability 注册推断。

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
- 模型可见工具仍是统一工具，不出现 `hk_market_data`、`hk_fundamental_data`、`hk_fundamentals_data`、`hk_news_data`、`hk_social_sentiment`。
- HK `social` 会生成 `social_signal` request。
- provider registry 有 HK `social_signal` capability。
- HK 默认 frontline 四域 `market/fundamental/news/social` 都进入 prefetch manifest。
- 测试不检查 US/HK/CRYPTO 的 `policy/hot_money/lockup`，因为它们是 A 股专用域。

### 具体测试修改清单

`tests/contracts/test_tool_registry_contract.py`

- 在 `test_load_tool_registry_defaults_to_canonical_data_pack_intents` 增加：
  - `hk_market_data -> claw_get_market_pack`
  - `hk_fundamentals_data -> claw_get_fundamental_pack`
  - `hk_news_data -> claw_get_news_pack`
  - `hk_social_sentiment -> claw_get_social_pack`
- 在 `test_legacy_rollback_flag_no_longer_changes_tool_registry` 增加同样 HK intent 断言。
- 把 `test_hk_frontline_stage_policy_declares_approved_pack_tools` 的期望从 `cn_a_*` 改成 `hk_*`。
- 同一个测试还要断言 HK skill 已进入 `STAGES.yaml` 的 `skills.mounted`，对应 `SKILL.md` 文件存在，并进入对应 worker 的 `skills/manifest.yaml`。
- 修改 `test_hk_frontline_reuses_existing_pack_tool_contracts_without_hk_specific_visible_tools`：仍断言 OpenClaw plugin manifest 不注册 `hk_*` 可见工具，但不能再断言 `registry.intent_to_tools` 不包含 `hk_*` intent。

`tests/contracts/test_stage_tool_policy_contract.py`

- 增加或修改 HK frontline policy 断言：HK 四个 worker 的 `tool_intents` 是 `hk_*`，`resolve_tools()` 结果仍是统一 `claw_get_*_pack`。

`tests/contracts/test_worker_prompt_alignment_policy.py`

- 保留 HK prompt 不出现 `hk_*` token 的断言，因为 `hk_*` 只是 stage intent，不是 prompt 或模型可见工具。

`tests/contracts/test_provider_capabilities.py`

- 在 multi-market provider capability 断言中增加 `("HK", "social_signal")`。
- 断言 provider 是 `hk_google_news`，endpoint 是 `social_signal_news_heat`，`source_role == "discovery"`，`can_be_formal_fact_source is False`。
- 增加 fetch fixture：`HKGoogleNewsDiscoveryPlugin.fetch()` 在 `endpoint_id="social_signal_news_heat"` 时能把 RSS item 转成 `dataset="social_signal"`、`source_roles=("discovery",)`、`quality_flags` 包含 `not_formal_fact_source` 的 row。

`tests/unit/data_gateway/test_provider_selector.py`

- 在 `test_selector_with_migrated_us_hk_crypto_matrices_picks_domain_providers` 加 HK `social_signal` case，证明 provider selector 能选到 `hk_google_news/social_signal_news_heat`。

`tests/unit/reports/test_data_pack_bridge.py`

- 把现有 `assert hk_social == ()` 改成断言 HK social 生成 1 个 `social_signal` request。
- 增加 HK prefetch test：`run_report_data_prefetch()` 对 HK request 写出的 `domains` 包含 `social`。
- 增加 HK social empty 文案 test：调用 `_model_visible_text(market=Market.HK, domain="social", status="missing", results=[missing result])`，断言输出包含“HK 社交资料包”“社交证据视为缺口”，且不包含“完整社交情绪”等成功暗示。

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

已验证结果：

- live run id：`run-20260607-052437-e10fd015`
- export result：`passed`
- 证据目录：`docs/evidence/trading_claw_trade_hk_fresh_live_run-20260607-052437-e10fd015_md/`
- `report-prefetch.json` domains：`market,fundamental,news,social`
- HK social request：`run-20260607-052437-e10fd015:report-prefetch:social:1:social_signal`
- HK social status：`partial`
- HK social rows：`20`
- provider attempt：`attempt:hk_google_news:social_signal_news_heat:b901ebc3678e`
- columnar path：`.runtime/dev-services/data-gateway/normalized/market=HK/dataset=social_signal/granularity=event/partition-a9a259b7cb8a42b0.parquet`
- DuckDB readback：`source_roles_json=["discovery"]`，`quality_flags=["not_formal_fact_source"]`，provider lineage `hk_google_news/social_signal_news_heat`
- `social_analyst` 可见工具只包含 `claw_get_social_pack`
- `social_analyst` tool call `claw_get_social_pack` 为 `success`
- run/evidence 中未再出现 `manifest does not contain domain: social`

剩余问题：

- 数据层已返回 HK `social_signal` 行，且列式 Parquet 可读；但 live 报告文本仍把社交资料覆盖期概括为“2026 年 1 月至 5 月”，而 worker 可见的 20 行中实际包含 2026-06-01、2026-06-02、2026-06-03、2026-06-05 的 Google News 线索。这个问题属于报告解读/摘要口径，不是 HK social 数据层缺失。

## 成功标准

本次修改完成后，HK `/report` 不应因为 social domain 没有进入 manifest 而失败。

如果 HK social 来源没有返回可用数据，系统应继续生成报告，并把社交证据不足写成明确数据缺口；不能跳过 worker，不能伪造社交数据，不能借 CN_A 数据源。
