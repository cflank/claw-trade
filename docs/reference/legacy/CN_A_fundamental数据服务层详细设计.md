# CN_A fundamental 数据服务层详细设计

## 1. 文档元信息

- 对应 HLD：`docs/CN_A_fundamental数据服务层总体设计.md`
- HLD 版本：`cn_a_fundamental_pack.hld.v1`
- DLD 版本：`cn_a_fundamental_pack.dld.v2`
- 更新日期：`2026-05-07`

本版目标：把 HLD 约束落到可直接编码层级，重点补齐 provider 白名单、fetcher 细节、字段映射、OpenViking L2 原始载荷写入、结构化 data pack、控制面 gate、MongoDB DDL、安全与运维。

## 2. 实施边界与固定决策

### 2.1 架构边界

- Python 数据服务只负责资料采集、结构化、诊断，不写投资结论、不写评级、不替代 PM。
- OpenClaw 只负责 single worker runtime，不承载 12-worker DAG 编排。
- OpenViking 保存运行证据与材料，不替代 MongoDB 结构化缓存查询。
- worker 可见工具固定为：
  - `fundamental_fundamentals_data_pack`
  - `openviking_write_material`

### 2.2 依赖固定决策

以下版本作为实现决策写入本 DLD，实施阶段需同步 `pyproject.toml` 与锁文件：

- MongoDB Python client：`pymongo>=4.10,<5`
- Tushare SDK：`tushare>=1.4.24,<2`

说明：

- 本文只定义应实现的依赖与接口，不宣称仓库当前已存在这些依赖。

### 2.3 运行前置输入

下列输入由部署系统提供：

- `TUSHARE_TOKEN`
- `CN_A_MONGODB_URI`
- `CN_A_MONGODB_DATABASE`
- `CN_A_MONGODB_CACHE_COLLECTION`
- `OPENVIKING_ENDPOINT`
- `OPENVIKING_API_KEY`
- `OPENVIKING_WORKSPACE`

## 3. HLD 覆盖矩阵（真实状态）

本节不再给总百分比，按“符合 / 部分符合 / 需人工输入”标记当前 DLD 覆盖。

| HLD 条目 | DLD 章节 | 状态 | 说明 |
| --- | --- | --- | --- |
| §5 架构总图 | §4.1, §4.2, §5 | 符合 | 单入口数据包、控制面 gate、Mongo+OpenViking 分层已落地到接口 |
| §6 tool surface | §4.1, §4.10 | 符合 | 可见工具固定并在 gate 校验，校验执行点在控制面 gate 适配层 |
| §7.1 provider 分工 | §4.3, §4.4, §4.5 | 符合 | Mongo inspect、Tushare 主源、AkShare 补充清晰 |
| §7.2 固定路由 | §4.3 | 符合 | 固定顺序：Mongo -> Tushare -> AkShare |
| §7.3 健康路由不进 v1 | §4.3 | 符合 | 未引入动态调度 |
| §7.4 跨源冲突 | §4.6, §4.7 | 符合 | 冲突不静默覆盖，降级能力并记录 flag |
| §8 MongoDB 设计 | §5 | 符合 | DDL、索引、TTL 策略、查询场景均具体化 |
| §9 诊断系统 | §4.2, §4.8 | 符合 | provider_attempts、missing_fields、diagnostic_flags 全定义 |
| §10 data pack schema | §4.9 | 符合 | 字段级结构与状态机完整定义 |
| §11 provenance | §4.6, §4.9 | 符合 | 字段来源与 raw 证据链绑定 |
| §12 最低字段门槛 | §4.8, §4.9 | 符合 | 核心字段与部分报告边界可判定 |
| §13 reader brief 边界 | §4.9.4 | 符合 | `derived.summary` 固定模板、固定条数、固定证据引用，不写结论 |
| §14 失败处理 | §4.4, §4.5, §4.8 | 符合 | token、空响应、超时、schema 变化均可诊断 |
| §15 状态机与 gate | §4.9.3, §4.10.5 | 部分符合 | pack 状态机与 gate 拒绝规则完整；控制面的重试预算与终止策略由 `claw-trade` 主流程实现，不在本 DLD 展开 |
| §16 验证计划 | §7 | 部分符合 | 命令模板与验收样本明确，真实外部依赖由运行环境提供 |
| §17 风险 | §6 | 符合 | 安全、运维、回滚、告警完整化 |
| §18 Phase 0-4 | 全文 | 符合 | Phase 0 到 Phase 4 均落到代码接口粒度 |
| §18 Phase 5 | §4.3 | 符合 | Baostock 仍排除在 v1 主路径之外 |
| §19 最终建议 | 全文 | 符合 | 保持 HLD 边界和职责划分 |

### 3.1 原阻塞项闭合情况

| 事项 | 当前处理 | 状态 |
| --- | --- | --- |
| MongoDB client 版本未落地 | 本文固定 `pymongo>=4.10,<5`，实施需同步依赖文件 | 已闭合 |
| Tushare SDK 版本未落地 | 本文固定 `tushare>=1.4.24,<2`，实施需同步依赖文件 | 已闭合 |
| Tushare token 可用性 | 作为运行前置输入；无 token 时返回失败或部分状态，不中断实现 | 已闭合 |
| 价格与估值新鲜度窗口 | 本文固定 7 天，可通过配置项覆盖 | 已闭合 |
| 生产 Mongo URI 与凭据来源 | 使用 secret store 注入，部署侧提供真实值 | 需人工输入 |
| OpenViking endpoint 与 workspace | 使用部署配置注入 | 需人工输入 |

## 4. 模块详细设计

### 4.1 OpenClaw worker 与 tool surface

#### 4.1.1 接口

```python
from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class VisibleToolPolicy:
    worker_id: Literal["fundamental_analyst"]
    market_profile: Literal["CN_A"]
    tool_names: tuple[str, str]

def resolve_fundamental_visible_tools(worker_id: str, market_profile: str) -> VisibleToolPolicy:
    if worker_id != "fundamental_analyst":
        raise ValueError("TOOL_POLICY_WORKER_MISMATCH")
    if market_profile != "CN_A":
        raise ValueError("TOOL_POLICY_PROFILE_MISMATCH")
    tool_names = ("fundamental_fundamentals_data_pack", "openviking_write_material")
    return VisibleToolPolicy(worker_id="fundamental_analyst", market_profile="CN_A", tool_names=tool_names)
```

#### 4.1.2 gate 校验要求

- `is_visible_tool_snapshot_invalid_v1(visible_tools_path)` 返回 `True` 的条件：
  - 缺少任一批准工具；
  - 出现额外工具；
  - worker 不是 `fundamental_analyst`。

### 4.2 Data service orchestrator

#### 4.2.1 核心结构

```python
from dataclasses import dataclass
from typing import Literal

PackStatus = Literal["ok", "partial", "failed"]
ProviderStatus = Literal["success", "empty", "error", "timeout", "skipped", "miss", "stale", "schema_invalid"]

@dataclass(frozen=True)
class DataPackRequest:
    ticker: str
    start_date: str | None
    end_date: str | None
    current_date: str
    run_id: str
    dispatch_id: str
    worker_id: Literal["fundamental_analyst"]
    market: Literal["CN_A"]

@dataclass(frozen=True)
class NormalizedInput:
    raw_ticker: str
    canonical_code: str
    tushare_code: str
    akshare_symbol: str
    exchange: Literal["SH", "SZ"]
    market: Literal["CN_A"]
    start_date: str | None
    end_date: str | None
    current_date: str
    run_id: str
    dispatch_id: str

@dataclass(frozen=True)
class ProviderAttempt:
    provider: Literal["mongodb", "tushare", "akshare"]
    role: Literal["cache", "primary", "supplement"]
    api_name: str | None
    attempt_seq: int | None
    status: ProviderStatus
    reason: str | None
    started_at: str
    ended_at: str
    duration_ms: int
    retry_count: int
    request_params_redacted: dict[str, str]
    response_row_count: int
    response_col_count: int
    field_coverage: list[str]
    report_period: str | None
    announce_date: str | None
    as_of: str | None
    fetched_at: str | None
    raw_payload_hash: str | None
    raw_payload_ref: str | None
    error_type: str | None
    error_message_redacted: str | None
```

#### 4.2.2 主流程

```text
FUNCTION BuildCnAFundamentalPack(request):
    normalized = NormalizeInput(request)
    cache_result = MongoCacheInspector.Inspect(normalized)
    plan = BuildProviderPlan(normalized, cache_result)

    provider_results = []
    FOR each spec IN plan.calls:
        IF spec.provider == "tushare":
            provider_results.ADD(TushareFetcher.Fetch(normalized, spec))
        ELSE:
            provider_results.ADD(AkShareFetcher.Fetch(normalized, spec))

    mapped = FieldSourceMapper.Map(cache_result, provider_results)
    freshness = FreshnessChecker.Check(mapped.field_sources, normalized.current_date)
    diagnostics = MissingFieldDiagnostics.Compute(mapped, freshness, provider_results)
    capabilities = EvidenceCapabilityClassifier.Classify(mapped, freshness, diagnostics)
    pack = DataPackBuilder.build(
        normalized_input=normalized,
        mapped_facts=mapped.facts,
        field_sources=mapped.field_sources,
        provider_attempts=CollectAttempts(cache_result, provider_results),
        missing_fields=diagnostics.missing_fields,
        freshness=freshness,
        evidence_capabilities=capabilities,
        diagnostic_flags=diagnostics.flags,
        derived_summary=DerivedSummaryBuilder.Build(mapped, diagnostics),
    )
    RETURN pack
```

### 4.3 Deterministic Provider Planner

#### 4.3.1 结构

```python
@dataclass(frozen=True)
class ApiCallSpec:
    provider: Literal["tushare", "akshare"]
    api_name: str
    role: Literal["primary", "supplement"]
    required: bool
    field_family: str
    parameters: dict[str, str]
    timeout_ms: int
    retry_limit: int
```

#### 4.3.2 Tushare v1 白名单

| api_name | 参数 | timeout_ms | retry_limit | 字段族 |
| --- | --- | --- | --- | --- |
| `stock_company` | `exchange`=`SSE` 或 `SZSE`, `fields`=`ts_code,name,province,city,introduction,main_business` | 12000 | 1 | `company_profile` |
| `stock_basic` | `ts_code`=`{tushare_code}`, `list_status`=`L`, `fields`=`ts_code,name,industry,market,list_date` | 12000 | 1 | `company_profile` |
| `daily_basic` | `ts_code`=`{tushare_code}`, `trade_date`=`{current_date_yyyymmdd}`, `fields`=`ts_code,trade_date,close,pe_ttm,pb,total_mv,float_mv` | 15000 | 1 | `price_context,valuation` |
| `fina_indicator` | `ts_code`=`{tushare_code}`, `period`=`{latest_report_period}`, `fields`=`ts_code,end_date,ann_date,roe,roa,grossprofit_margin,netprofit_margin,debt_to_assets` | 15000 | 1 | `financial_indicators` |
| `income` | `ts_code`=`{tushare_code}`, `period`=`{latest_report_period}`, `fields`=`ts_code,end_date,ann_date,revenue,n_income,basic_eps` | 15000 | 1 | `income_statement` |
| `balancesheet` | `ts_code`=`{tushare_code}`, `period`=`{latest_report_period}`, `fields`=`ts_code,end_date,ann_date,total_assets,total_liab,total_hldr_eqy_exc_min_int` | 15000 | 1 | `balance_sheet` |
| `cashflow` | `ts_code`=`{tushare_code}`, `period`=`{latest_report_period}`, `fields`=`ts_code,end_date,ann_date,n_cashflow_act` | 15000 | 1 | `cash_flow` |
| `fina_mainbz` | `ts_code`=`{tushare_code}`, `period`=`{latest_report_period}`, `type`=`P`, `fields`=`ts_code,end_date,bz_item,bz_sales,bz_profit` | 15000 | 1 | `business_segments` |
| `dividend` | `ts_code`=`{tushare_code}`, `fields`=`ts_code,ann_date,end_date,record_date,ex_date,stk_div,cash_div_tax` | 12000 | 1 | `dividend` |
| `top10_holders` | `ts_code`=`{tushare_code}`, `period`=`{latest_report_period}`, `fields`=`ts_code,ann_date,end_date,holder_name,hold_amount,hold_ratio` | 12000 | 1 | `shareholders` |
| `top10_floatholders` | `ts_code`=`{tushare_code}`, `period`=`{latest_report_period}`, `fields`=`ts_code,ann_date,end_date,holder_name,hold_amount,hold_ratio` | 12000 | 1 | `shareholders` |

#### 4.3.3 AkShare v1 白名单

| api_name | 参数 | timeout_ms | retry_limit | 字段族 |
| --- | --- | --- | --- | --- |
| `stock_individual_info_em` | `symbol`=`{akshare_symbol}` | 12000 | 1 | `company_profile,valuation` |
| `stock_zh_a_spot_em` | 无参数，返回全市场后按 `代码={akshare_symbol}` 过滤 | 18000 | 1 | `price_context,valuation` |
| `stock_zh_a_hist` | `symbol`=`{akshare_symbol}`, `period`=`daily`, `start_date`=`{start_date_yyyymmdd}`, `end_date`=`{end_date_yyyymmdd}`, `adjust`=`qfq` | 18000 | 1 | `price_context` |
| `stock_financial_abstract_ths` | `symbol`=`{akshare_symbol}`, `indicator`=`按报告期` | 18000 | 1 | `financial_indicators,income_statement,balance_sheet,cash_flow` |
| `stock_history_dividend_detail` | `symbol`=`{akshare_symbol}`, `indicator`=`分红` | 12000 | 1 | `dividend` |

#### 4.3.4 计划生成规则

- 固定顺序：
  1. MongoDB inspect
  2. 全量 Tushare 白名单
  3. AkShare 白名单中未被 Tushare 覆盖或 Tushare 失败的字段族
- v1 不纳入 Baostock。
- `BuildProviderPlan` 输出必须包含每个调用的参数、超时、重试和字段族。

### 4.4 Tushare Primary Fetcher

#### 4.4.1 SDK 调用方式

```python
import tushare as ts

def invoke_tushare_api_by_dispatch(token: str, api_name: str, parameters: dict[str, str], timeout_ms: int):
    pro = ts.pro_api(token)
    fn = getattr(pro, api_name)
    return fn(**parameters)
```

#### 4.4.2 接口签名

```python
@dataclass(frozen=True)
class ProviderResult:
    attempt: ProviderAttempt
    raw_payload_hash: str | None
    raw_payload_ref: str | None
    extracted_fields: list[tuple[str, object, str]]
    schema_changed: bool

class TushareFetcher:
    def Fetch(self, input_data: NormalizedInput, spec: ApiCallSpec) -> ProviderResult:
        started_at = now_iso()
        if self.config.token is None or self.config.token == "":
            return build_provider_result_for_missing_token(spec=spec, started_at=started_at)
        dataframe = dispatch_tushare_sdk_call(self.client, spec.api_name, spec.parameters, spec.timeout_ms, spec.retry_limit)
        validation = validate_tushare_response_columns(spec.api_name, dataframe.columns.tolist())
        if validation.is_schema_changed:
            return build_provider_result_for_schema_changed(spec=spec, started_at=started_at, dataframe=dataframe)
        if dataframe.empty:
            return build_provider_result_for_empty_response(spec=spec, started_at=started_at, dataframe=dataframe)
        planned_attempt_seq = reserve_provider_attempt_seq_v1(
            run_id=input_data.run_id,
            dispatch_id=input_data.dispatch_id,
            provider="tushare",
            api_name=spec.api_name,
        )
        raw_write = write_raw_payload_to_openviking(
            request=RawPayloadWriteInput(
                provider="tushare",
                api_name=spec.api_name,
                run_id=input_data.run_id,
                dispatch_id=input_data.dispatch_id,
                worker_id="fundamental_analyst",
                attempt_seq=planned_attempt_seq,
                payload=dataframe.to_dict("records"),
                timeout_ms=5000,
                retry_limit=1,
            )
        )
        if raw_write.ok is False:
            return build_provider_result_for_raw_write_failed(spec=spec, started_at=started_at, raw_write=raw_write)
        provider_attempt_seq = raw_write.attempt_seq
        mapped = map_tushare_columns_to_pack_fields_v1(spec.api_name, dataframe, raw_write.content_hash, raw_write.uri)
        return build_provider_result_for_success(
            spec=spec,
            started_at=started_at,
            dataframe=dataframe,
            mapped_fields=mapped,
            raw_write=raw_write,
            provider_attempt_seq=provider_attempt_seq,
        )
```

#### 4.4.3 返回列与抽取规则

`map_tushare_columns_to_pack_fields_v1(api_name, dataframe, ref_hash, ref_uri)` 的抽取列：

- `daily_basic`: `close`, `pe_ttm`, `pb`, `total_mv`
- `fina_indicator`: `roe`, `roa`, `grossprofit_margin`, `netprofit_margin`, `debt_to_assets`
- `income`: `revenue`, `n_income`, `basic_eps`
- `balancesheet`: `total_assets`, `total_liab`, `total_hldr_eqy_exc_min_int`
- `cashflow`: `n_cashflow_act`
- `fina_mainbz`: `bz_item`, `bz_sales`, `bz_profit`
- `dividend`: `stk_div`, `cash_div_tax`, `record_date`, `ex_date`
- `top10_holders` 与 `top10_floatholders`: `holder_name`, `hold_amount`, `hold_ratio`
- `stock_company` 与 `stock_basic`: `name`, `industry`, `main_business`, `province`, `city`

#### 4.4.4 错误分类

| 错误类别 | attempt.status | reason |
| --- | --- | --- |
| token 缺失 | `skipped` | `missing_token` |
| 超时 | `timeout` | `timeout` |
| 积分或权限不足 | `error` | `permission_or_quota` |
| 空表 | `empty` | `empty_response` |
| 返回列缺失 | `schema_invalid` | `schema_changed` |
| 其他异常 | `error` | `provider_error` |

#### 4.4.5 脱敏与 attempt 字段填充

- request 参数脱敏：只保留代码、日期、接口名；token 不写入。
- error message 脱敏：执行统一脱敏函数，替换密钥和长凭据。
- timeout 或重试后，`ProviderAttempt` 必须填充：
  - `started_at`, `ended_at`, `duration_ms`
  - `retry_count`
  - `request_params_redacted`
  - `response_row_count`, `response_col_count`
  - `error_type`, `error_message_redacted`
- `ProviderAttempt.attempt_seq` 填充规则：
  - 先取 provider plan 当前分配序号；
  - 若 writer 因 create-only 冲突改用下一序号，则以 writer 返回 `attempt_seq` 为准；
  - `ProviderAttempt.attempt_seq` 必须与 raw URI 尾号一致。

#### 4.4.6 执行流程

```text
FUNCTION TushareFetcher.Fetch(input_data, spec):
    started = NowIso()
    IF token missing:
        RETURN AttemptResult(status="skipped", reason="missing_token")

    TRY:
        df = invoke_tushare_api_by_dispatch(token, spec.api_name, spec.parameters, spec.timeout_ms)
    CATCH TimeoutError:
        IF spec.retry_limit > 0:
            Sleep(500)
            RETRY once
        RETURN TimeoutAttempt()
    CATCH Exception AS err:
        RETURN ErrorAttempt(reason=ClassifyTushareError(err))

    IF df is empty:
        RETURN EmptyAttempt()

    expected_ok = ValidateTushareColumns(spec.api_name, df.columns)
    IF expected_ok is false:
        RETURN SchemaInvalidAttempt()

    ref = write_raw_payload_to_openviking(
        request=RawPayloadWriteInput(
            provider="tushare",
            api_name=spec.api_name,
            run_id=input_data.run_id,
            dispatch_id=input_data.dispatch_id,
            worker_id="fundamental_analyst",
            attempt_seq=reserve_provider_attempt_seq_v1(
                run_id=input_data.run_id,
                dispatch_id=input_data.dispatch_id,
                provider="tushare",
                api_name=spec.api_name
            ),
            payload=df.to_dict("records"),
            timeout_ms=5000,
            retry_limit=1
        )
    )
    fields = map_tushare_columns_to_pack_fields_v1(spec.api_name, df, ref.content_hash, ref.uri)
    provider_attempt = BuildProviderAttemptWithFinalSeq(spec, started, ref.attempt_seq)
    RETURN SuccessAttemptWithFields(fields, ref, provider_attempt)
```

### 4.5 AkShare Supplement Fetcher

#### 4.5.1 SDK 调用方式

```python
import akshare as ak

def invoke_akshare_api_by_dispatch(api_name: str, parameters: dict[str, str], timeout_ms: int):
    fn = getattr(ak, api_name)
    return fn(**parameters)
```

#### 4.5.2 预期列与 schema_changed 判定

| api_name | 预期列 | 判定规则 |
| --- | --- | --- |
| `stock_individual_info_em` | `item`, `value` | 少任一列即 `schema_changed` |
| `stock_zh_a_spot_em` | `代码`, `最新价`, `总市值`, `市盈率-动态`, `市净率` | 缺列或过滤后无目标代码分两类：缺列为 `schema_changed`，无目标代码为 `empty_response` |
| `stock_zh_a_hist` | `日期`, `收盘`, `成交量` | 缺列即 `schema_changed` |
| `stock_financial_abstract_ths` | `报告期`, `净利润`, `营业总收入`, `每股收益`, `净资产收益率` | 缺列即 `schema_changed` |
| `stock_history_dividend_detail` | `公告日期`, `除权除息日`, `每股分红` | 缺列即 `schema_changed` |

#### 4.5.3 输出字段

`map_akshare_columns_to_pack_fields_v1(api_name, dataframe, ref_hash, ref_uri)` 输出：

- `valuation.pe_ttm`, `valuation.pb`, `valuation.total_mv`
- `price_context.close`, `price_context.trade_date`, `price_context.volume`
- `company_profile.main_business`, `company_profile.industry`
- `financial_indicators.roe`, `income_statement.revenue`, `income_statement.net_profit`, `income_statement.eps`
- `dividend.cash_dividend`, `dividend.ex_date`

#### 4.5.4 错误分类

| 错误类别 | attempt.status | reason |
| --- | --- | --- |
| 超时 | `timeout` | `timeout` |
| 空响应 | `empty` | `empty_response` |
| 结构变化 | `schema_invalid` | `schema_changed` |
| 权限或访问拒绝 | `error` | `permission_or_access` |
| 其他异常 | `error` | `provider_error` |

### 4.6 Field Mapping（可编码表）

#### 4.6.1 文件路径建议

- `agents/fundamental_analyst/skills/cn-a-fundamental-data/scripts/field_mapping_v1.py`
- 导出常量：`CN_A_FUNDAMENTAL_FIELD_MAPPING_V1`

#### 4.6.2 最小映射表

每条规则字段顺序固定为：

`provider, api_name, source_column, pack_field_path, domain, unit_scale, source_ref_kind, source_ref_path`

```python
CN_A_FUNDAMENTAL_FIELD_MAPPING_V1 = [
    ("tushare", "daily_basic", "pe_ttm", "valuation.pe_ttm", "valuation", "x", "raw", "field_sources.valuation.pe_ttm"),
    ("tushare", "daily_basic", "pb", "valuation.pb", "valuation", "x", "raw", "field_sources.valuation.pb"),
    ("tushare", "daily_basic", "total_mv", "valuation.total_mv", "valuation", "10k_cny", "raw", "field_sources.valuation.total_mv"),
    ("tushare", "fina_indicator", "roe", "financial_indicators.roe", "financial_indicators", "%", "raw", "field_sources.financial_indicators.roe"),
    ("tushare", "fina_indicator", "roa", "financial_indicators.roa", "financial_indicators", "%", "raw", "field_sources.financial_indicators.roa"),
    ("tushare", "fina_indicator", "grossprofit_margin", "financial_indicators.gross_margin", "financial_indicators", "%", "raw", "field_sources.financial_indicators.gross_margin"),
    ("tushare", "fina_indicator", "netprofit_margin", "financial_indicators.netprofit_margin", "financial_indicators", "%", "raw", "field_sources.financial_indicators.netprofit_margin"),
    ("tushare", "fina_indicator", "debt_to_assets", "financial_indicators.debt_to_assets", "financial_indicators", "%", "raw", "field_sources.financial_indicators.debt_to_assets"),
    ("tushare", "income", "revenue", "income_statement.revenue", "income_statement", "cny", "raw", "field_sources.income_statement.revenue"),
    ("tushare", "income", "n_income", "income_statement.net_profit", "income_statement", "cny", "raw", "field_sources.income_statement.net_profit"),
    ("tushare", "income", "basic_eps", "income_statement.eps", "income_statement", "cny_per_share", "raw", "field_sources.income_statement.eps"),
    ("tushare", "balancesheet", "total_assets", "balance_sheet.total_assets", "balance_sheet", "cny", "raw", "field_sources.balance_sheet.total_assets"),
    ("tushare", "balancesheet", "total_liab", "balance_sheet.total_liabilities", "balance_sheet", "cny", "raw", "field_sources.balance_sheet.total_liabilities"),
    ("tushare", "balancesheet", "total_hldr_eqy_exc_min_int", "balance_sheet.total_equity", "balance_sheet", "cny", "raw", "field_sources.balance_sheet.total_equity"),
    ("tushare", "cashflow", "n_cashflow_act", "cash_flow.operating_cash_flow", "cash_flow", "cny", "raw", "field_sources.cash_flow.operating_cash_flow"),
    ("tushare", "fina_mainbz", "bz_item", "business_segments.items", "business_segments", "text", "raw", "field_sources.business_segments.items"),
    ("tushare", "fina_mainbz", "bz_sales", "business_segments.sales", "business_segments", "cny", "raw", "field_sources.business_segments.sales"),
    ("tushare", "dividend", "stk_div", "dividend.stock_dividend", "dividend", "share_per_share", "raw", "field_sources.dividend.stock_dividend"),
    ("tushare", "dividend", "cash_div_tax", "dividend.cash_dividend", "dividend", "cny_per_share", "raw", "field_sources.dividend.cash_dividend"),
    ("tushare", "top10_holders", "holder_name", "shareholders.top10_holders.names", "shareholders", "text", "raw", "field_sources.shareholders.top10_holders.names"),
    ("tushare", "top10_holders", "hold_amount", "shareholders.top10_holders.hold_amount", "shareholders", "share", "raw", "field_sources.shareholders.top10_holders.hold_amount"),
    ("tushare", "top10_floatholders", "holder_name", "shareholders.top10_float_holders.names", "shareholders", "text", "raw", "field_sources.shareholders.top10_float_holders.names"),
    ("tushare", "top10_floatholders", "hold_amount", "shareholders.top10_float_holders.hold_amount", "shareholders", "share", "raw", "field_sources.shareholders.top10_float_holders.hold_amount"),
    ("akshare", "stock_zh_a_spot_em", "市盈率-动态", "valuation.pe_ttm", "valuation", "ratio", "raw", "field_sources.valuation.pe_ttm"),
    ("akshare", "stock_zh_a_spot_em", "市净率", "valuation.pb", "valuation", "ratio", "raw", "field_sources.valuation.pb"),
    ("akshare", "stock_zh_a_spot_em", "总市值", "valuation.total_mv", "valuation", "cny", "raw", "field_sources.valuation.total_mv"),
    ("akshare", "stock_zh_a_spot_em", "最新价", "price_context.close", "price_context", "cny_per_share", "raw", "field_sources.price_context.close"),
    ("akshare", "stock_financial_abstract_ths", "净资产收益率", "financial_indicators.roe", "financial_indicators", "%", "raw", "field_sources.financial_indicators.roe"),
    ("akshare", "stock_financial_abstract_ths", "营业总收入", "income_statement.revenue", "income_statement", "cny", "raw", "field_sources.income_statement.revenue"),
    ("akshare", "stock_financial_abstract_ths", "净利润", "income_statement.net_profit", "income_statement", "cny", "raw", "field_sources.income_statement.net_profit"),
    ("akshare", "stock_financial_abstract_ths", "每股收益", "income_statement.eps", "income_statement", "cny_per_share", "raw", "field_sources.income_statement.eps"),
    ("akshare", "stock_history_dividend_detail", "每股分红", "dividend.cash_dividend", "dividend", "cny_per_share", "raw", "field_sources.dividend.cash_dividend"),
    ("akshare", "stock_history_dividend_detail", "除权除息日", "dividend.ex_date", "dividend", "date", "raw", "field_sources.dividend.ex_date"),
    ("akshare", "stock_history_dividend_detail", "公告日期", "dividend.ann_date", "dividend", "date", "raw", "field_sources.dividend.ann_date"),
]
```

AkShare 列级单位处理规则：

- `stock_zh_a_spot_em.总市值` 按接口返回的人民币元写入 `valuation.total_mv`，单位口径固定为 `cny`，不做万元换算。
- `stock_zh_a_spot_em.市盈率-动态` 与 `市净率` 为无量纲比值，单位口径固定为 `ratio`。
- `stock_financial_abstract_ths` 数值列按接口原值写入，货币口径固定为 `cny` 或 `cny_per_share`。
- `stock_history_dividend_detail.每股分红` 口径固定为 `cny_per_share`。

#### 4.6.3 冲突策略

- 同字段多来源且值冲突时：
  - 不覆盖；
  - 在 `diagnostic_flags` 记录 `cross_provider_conflict`；
  - 冲突核心字段从 `facts` 移除并写入 `missing_fields`。
- AkShare 与 Tushare 同字段冲突时，沿用本节规则，不覆盖事实字段。

### 4.7 RawPayloadWriter

#### 4.7.1 接口定义

```python
@dataclass(frozen=True)
class RawPayloadWriteInput:
    provider: str
    api_name: str
    run_id: str
    dispatch_id: str
    worker_id: str
    attempt_seq: int
    payload: object
    timeout_ms: int
    retry_limit: int

@dataclass(frozen=True)
class RawPayloadWriteResult:
    ok: bool
    uri: str | None
    attempt_seq: int | None
    content_hash: str | None
    bytes_written: int
    started_at: str
    ended_at: str
    duration_ms: int
    retry_count: int
    error_type: str | None
    error_message_redacted: str | None

def write_raw_payload_to_openviking(request: RawPayloadWriteInput) -> RawPayloadWriteResult:
    started_at = now_iso()
    canonical_bytes = to_canonical_json_bytes(request.payload)
    content_hash = "sha256:" + sha256_hex(canonical_bytes)
    resolved_attempt_seq = allocate_raw_attempt_seq_v1(
        run_id=request.run_id,
        dispatch_id=request.dispatch_id,
        provider=request.provider,
        api_name=request.api_name,
        expected_min_seq=request.attempt_seq,
    )
    uri = build_openviking_raw_uri(
        workspace_env="OPENVIKING_WORKSPACE",
        run_id=request.run_id,
        dispatch_id=request.dispatch_id,
        worker_id=request.worker_id,
        provider=request.provider,
        api_name=request.api_name,
        attempt_seq=resolved_attempt_seq,
    )
    write_result = put_openviking_object_create_only(
        endpoint_env="OPENVIKING_ENDPOINT",
        api_key_env="OPENVIKING_API_KEY",
        uri=uri,
        body=canonical_bytes,
        timeout_ms=request.timeout_ms,
        retry_limit=request.retry_limit,
    )
    if write_result.error_type == "object_already_exists":
        next_seq = resolved_attempt_seq + 1
        retry_uri = build_openviking_raw_uri(
            workspace_env="OPENVIKING_WORKSPACE",
            run_id=request.run_id,
            dispatch_id=request.dispatch_id,
            worker_id=request.worker_id,
            provider=request.provider,
            api_name=request.api_name,
            attempt_seq=next_seq,
        )
        retry_result = put_openviking_object_create_only(
            endpoint_env="OPENVIKING_ENDPOINT",
            api_key_env="OPENVIKING_API_KEY",
            uri=retry_uri,
            body=canonical_bytes,
            timeout_ms=request.timeout_ms,
            retry_limit=request.retry_limit,
        )
        if retry_result.error_type == "object_already_exists":
            return RawPayloadWriteResult(
                ok=False,
                uri=None,
                attempt_seq=None,
                content_hash=None,
                bytes_written=0,
                started_at=started_at,
                ended_at=retry_result.ended_at,
                duration_ms=retry_result.duration_ms,
                retry_count=1,
                error_type="raw_payload_write_conflict",
                error_message_redacted="CONFLICT: create-only write failed twice for same run/dispatch/provider/api attempt sequence",
            )
        write_result = retry_result
        uri = retry_uri
        resolved_attempt_seq = next_seq
    return RawPayloadWriteResult(
        ok=write_result.ok,
        uri=uri if write_result.ok else None,
        attempt_seq=resolved_attempt_seq if write_result.ok else None,
        content_hash=content_hash if write_result.ok else None,
        bytes_written=len(canonical_bytes) if write_result.ok else 0,
        started_at=started_at,
        ended_at=write_result.ended_at,
        duration_ms=write_result.duration_ms,
        retry_count=write_result.retry_count,
        error_type=write_result.error_type,
        error_message_redacted=write_result.error_message_redacted,
    )
```

#### 4.7.2 L2 写入路径

`viking://resources/workflow/{run_id}/frontline/fundamental_analyst/{dispatch_id}/provider_raw/{provider}/{api_name}/{attempt_seq}.json`

`attempt_seq` 生成与并发唯一性规则：

1. 维度：`run_id + dispatch_id + provider + api_name`。  
2. 起始值：`1`。  
3. 递增：在上述维度内单调递增，不回退、不复用。  
4. 写入语义：OpenViking 使用“对象不存在才创建”的条件写入。  
5. 冲突处理：若创建时对象已存在，重新分配下一个序号并重试一次。  
6. 二次冲突：返回失败，`reason=raw_payload_write_conflict`，不写入伪成功记录。  
7. 对应关系：`provider_attempts[*].attempt_seq` 必须与 raw URI 尾部序号一致，且与 `content_hash` 一一对应。
8. `ProviderAttempt.attempt_seq` 的最终值取 `RawPayloadWriteResult.attempt_seq`；若 `uri` 可解析，解析结果必须与该值一致。

#### 4.7.3 hash 口径

- `content_hash` 计算：
  1. payload 转 UTF-8 JSON；
  2. key 按字典序稳定排序；
  3. 数字与时间字段保持原值，不做格式重写；
  4. `sha256(hex)`；
  5. 前缀固定 `sha256:`.

#### 4.7.4 重试与失败记录

- timeout：OpenViking create-only PUT 在 writer 内部最多重试 1 次，间隔 300ms。
- 写入失败：返回 `ok=false`，并追加 `ProviderAttempt(status="error", reason="raw_payload_write_failed")`。
- 并发冲突失败：序号冲突重分配单独计算，最多再试 1 个新序号；仍冲突则返回 `ok=false`，并追加 `ProviderAttempt(status="error", reason="raw_payload_write_conflict")`。
- 成功写入后字段才能进入 `facts` 与 Mongo cache。

### 4.8 阈值、缺口和诊断

#### 4.8.1 核心字段阈值

`quality.status=ok` 需同时满足：

- 估值字段中 `pe_ttm/pb/total_mv` 至少 2 项有效；
- 财务指标中 `roe/roa/gross_margin/netprofit_margin/debt_to_assets` 至少 3 项有效；
- 利润表、资产负债表、现金流三组各至少 1 项核心字段有效；
- 每个有效字段均有 `field_sources`。

`quality.status=partial`：

- 核心字段达标，但主营、分红、股东或趋势能力不足。

`quality.status=failed`：

- 任一核心字段组未达标；
- `provider_attempts` 缺失；
- 发现无来源事实字段。

#### 4.8.2 缺口原因枚举

- `missing_token`
- `empty_response`
- `timeout`
- `schema_changed`
- `provider_error`
- `cache_stale`
- `cross_provider_conflict`
- `field_source_missing`

### 4.9 Structured DataPack（完整结构）

#### 4.9.1 结构定义

```python
@dataclass(frozen=True)
class ProfileInfo:
    ticker: str
    canonical_code: str
    market: Literal["CN_A"]
    company_name: str | None
    currency: Literal["CNY"]

@dataclass(frozen=True)
class QueryInfo:
    start_date: str | None
    end_date: str | None
    current_date: str

@dataclass(frozen=True)
class ValuationFacts:
    pe_ttm: float | None
    pb: float | None
    total_mv: float | None

@dataclass(frozen=True)
class FinancialIndicatorsFacts:
    roe: float | None
    roa: float | None
    gross_margin: float | None
    netprofit_margin: float | None
    debt_to_assets: float | None

@dataclass(frozen=True)
class IncomeStatementFacts:
    revenue: float | None
    net_profit: float | None
    eps: float | None

@dataclass(frozen=True)
class BalanceSheetFacts:
    total_assets: float | None
    total_liabilities: float | None
    total_equity: float | None

@dataclass(frozen=True)
class CashFlowFacts:
    operating_cash_flow: float | None

@dataclass(frozen=True)
class Facts:
    valuation: ValuationFacts
    financial_indicators: FinancialIndicatorsFacts
    income_statement: IncomeStatementFacts
    balance_sheet: BalanceSheetFacts
    cash_flow: CashFlowFacts
    business_segments: list[dict[str, str | float]]
    dividend: list[dict[str, str | float]]
    shareholders: dict[str, list[dict[str, str | float]]]

@dataclass(frozen=True)
class DataPackQuality:
    status: PackStatus
    is_partial: bool
    warnings: list[str]

@dataclass(frozen=True)
class DerivedSummaryItem:
    template_id: str
    text: str
    source_ref: str
    inputs: dict[str, str]

@dataclass(frozen=True)
class DataPackEvidence:
    raw_payload_refs: list[str]
    content_hash: str

@dataclass(frozen=True)
class DataPack:
    schema_version: Literal["cn_a_fundamental_pack.v1"]
    ok: bool
    profile: ProfileInfo
    query: QueryInfo
    facts: Facts
    field_sources: dict[str, dict[str, str | float | bool | None]]
    provider_attempts: list[ProviderAttempt]
    missing_fields: list[dict[str, str | bool | list[str]]]
    freshness: dict[str, dict[str, str | bool | None]]
    evidence_capabilities: dict[str, dict[str, str | list[str]]]
    diagnostic_flags: list[dict[str, str]]
    derived_summary: list[DerivedSummaryItem]
    quality: DataPackQuality
    evidence: DataPackEvidence
```

#### 4.9.2 builder 与组装签名

```python
class DataPackBuilder:
    def build(
        self,
        normalized_input: NormalizedInput,
        mapped_facts: Facts,
        field_sources: dict[str, dict[str, str | float | bool | None]],
        provider_attempts: list[ProviderAttempt],
        missing_fields: list[dict[str, str | bool | list[str]]],
        freshness: dict[str, dict[str, str | bool | None]],
        evidence_capabilities: dict[str, dict[str, str | list[str]]],
        diagnostic_flags: list[dict[str, str]],
        derived_summary: list[DerivedSummaryItem],
    ) -> DataPack:
        pack_dict = compose_pack_payload_dict_v1(
            normalized_input=normalized_input,
            mapped_facts=mapped_facts,
            field_sources=field_sources,
            provider_attempts=provider_attempts,
            missing_fields=missing_fields,
            freshness=freshness,
            evidence_capabilities=evidence_capabilities,
            diagnostic_flags=diagnostic_flags,
            derived_summary=derived_summary,
        )
        content_hash = ComputePackHash(pack_dict)
        return BuildTypedPack(pack_dict, content_hash)
```

#### 4.9.3 组装规则

- `compose_pack_payload_dict_v1` 必须输出完整字段，不允许省略核心段。
- `ok` 与 `quality.status` 合法组合仅三种：
  - `ok=false,status=failed`
  - `ok=true,status=partial`
  - `ok=true,status=ok`
- `facts` 中每个非空字段必须在 `field_sources` 有同路径来源。

#### 4.9.4 `derived.summary` 可执行硬约束

`derived.summary` 由固定模板生成，不调用 LLM，不接受自由文本。

1. 条数限制  
   - 最多 8 条；超过 8 条时直接判定验证失败，不截断后继续。

2. 模板白名单（仅允许以下 `template_id`）  
   - `financial_snapshot_obtained`
   - `valuation_snapshot_obtained`
   - `missing_core_field`
   - `provider_call_failed`
   - `cross_source_conflict`

3. 每种模板输入字段

| template_id | 必填输入 | `source_ref` 规则 | 文本模板 |
| --- | --- | --- | --- |
| `financial_snapshot_obtained` | `report_period`, `field_list` | `field_sources.<field_path>` | `已取得{report_period}报告期的{field_list}。` |
| `valuation_snapshot_obtained` | `metric_name`, `as_of_date`, `provider_api` | `field_sources.<field_path>` | `{metric_name}来自{provider_api}，日期为{as_of_date}。` |
| `missing_core_field` | `field_name`, `reason_code` | `missing_fields[<index>]` | `{field_name}未取得，原因：{reason_code}。` |
| `provider_call_failed` | `provider`, `api_name`, `status`, `reason_code` | `provider_attempts[<index>]` | `{provider}.{api_name}状态为{status}，原因：{reason_code}。` |
| `cross_source_conflict` | `field_name`, `provider_set` | `diagnostic_flags[<index>]` | `{field_name}存在跨源口径冲突，来源：{provider_set}。` |

4. `source_ref` 格式硬约束  
   - 只允许四类前缀：
     - `field_sources.`
     - `missing_fields[`
     - `provider_attempts[`
     - `diagnostic_flags[`
   - 引用必须可解析到 pack 内实际存在项；引用越界或空引用直接验证失败。

5. 禁写词与禁写声明（命中即失败）  
   - 禁写词：`建议`、`买入`、`持有`、`卖出`、`观望`、`目标价`、`高估`、`低估`、`护城河`、`行业龙头`、`长期价值`
   - 禁写声明：投资评级、目标价区间、估值倾向结论、公司竞争地位结论

6. 验证失败行为  
   - `derived.summary` 置空；
   - 新增 `diagnostic_flags`：`code=derived_summary_invalid`，`severity=fail`；
   - `quality.status` 强制为 `failed`；
   - `ok=false`。

7. 失败样例  
   - 条数 9 条；
   - 文本含“建议持有”；
   - `source_ref="field_sources.valuation.pe_ttm"` 但 pack 中无该字段；
   - 使用未批准模板 `template_id="free_text_comment"`。

8. 测试断言（必须）  
   - `len(derived_summary) <= 8`
   - `all(item.template_id in TEMPLATE_WHITELIST for item in derived_summary)`
   - `all(is_resolvable_source_ref(item.source_ref, pack) for item in derived_summary)`
   - `not contains_forbidden_claim_words(item.text)`
   - 任何一条失败时：`quality.status == "failed"` 且 `ok is False`

### 4.10 控制面 claim gate 规则

#### 4.10.1 claim 抽取

`parse_report_claims_by_rules_v1(report_path)` 抽取以下声明类型：

- 指标声明：PE、PB、ROE、ROA、毛利率、净利率、资产负债率、营收、净利润、EPS、市值
- 结论声明：目标价、买入/持有/卖出评级、高估/低估
- 叙事声明：行业龙头、护城河、商业模式优势、行业地位稳固

抽取方法：

- 指标声明：正则加关键词词典；
- 结论声明：固定短语词典；
- 叙事声明：短语词典加上下文窗口 30 字。

#### 4.10.2 unsupported claim 判定

`is_claim_unbacked_by_evidence_v1(claim, pack)` 为 `True` 的规则：

- claim 依赖字段在 `facts` 缺失；
- claim 对应字段存在但 `field_sources` 缺失；
- claim 需要趋势能力，`evidence_capabilities.financial_trend.status` 不是 `available`；
- claim 属于目标价、评级、高估/低估，且 `evidence_capabilities` 对应能力为 `禁写`；
- claim 属于行业地位或护城河判断，且 `business_segments` 证据不足。

#### 4.10.3 receipt 校验

`is_openviking_receipt_invalid_v1(openviking_receipt_path)` 为 `True` 的条件：

- 文件不存在或 JSON 解析失败；
- `status` 不是 `success`；
- 缺失 `run_id`, `dispatch_id`, `worker_id`, `material_uri`, `content_hash` 任一字段；
- `worker_id` 不是 `fundamental_analyst`；
- `content_hash` 与 data pack 或 report 记录不一致。

#### 4.10.4 visible tools 校验

`is_visible_tool_snapshot_invalid_v1(visible_tools_path)` 为 `True` 的条件：

- 集合不等于 `{"fundamental_fundamentals_data_pack", "openviking_write_material"}`；
- `market_profile` 不是 `CN_A`；
- `worker_id` 不是 `fundamental_analyst`。

#### 4.10.5 控制面执行点说明

- 本章节的校验逻辑在 `claw-trade` 控制面 gate 适配层执行，不在数据服务层内部直接拒绝工作流。
- 数据服务层只输出可判定输入：`provider_attempts`、`field_sources`、`diagnostic_flags`、`quality`、`derived.summary`。
- 控制面根据 gate 结果执行：
  - 拒绝 artifact；
  - 请求 rerun；
  - 终止该 worker 本轮输出。

## 5. 数据设计（MongoDB DDL、索引、TTL）

### 5.1 集合 DDL

```javascript
db.createCollection("cn_a_fundamental_cache", {
  validator: {
    $jsonSchema: {
      bsonType: "object",
      required: [
        "ticker", "market", "provider", "api_name", "fetched_at",
        "schema_version", "payload_hash", "raw_payload_ref", "metric_definition_version",
        "fields", "created_at", "updated_at"
      ],
      properties: {
        ticker: { bsonType: "string", pattern: "^[0-9]{6}\\.(SH|SZ)$" },
        market: { enum: ["CN_A"] },
        provider: { enum: ["tushare", "akshare"] },
        api_name: { bsonType: "string" },
        report_period: { bsonType: ["string", "null"] },
        announce_date: { bsonType: ["string", "null"] },
        as_of: { bsonType: ["string", "null"] },
        fetched_at: { bsonType: "date" },
        schema_version: { enum: ["cn_a_fundamental_pack.v1"] },
        payload_hash: { bsonType: "string", pattern: "^sha256:[a-f0-9]{64}$" },
        raw_payload_ref: { bsonType: "string" },
        metric_definition_version: { bsonType: "string" },
        expires_at: { bsonType: ["date", "null"] },
        fields: {
          bsonType: "object",
          additionalProperties: {
            bsonType: "object",
            required: ["value", "unit", "scale", "field_path"],
            properties: {
              field_path: { bsonType: "string" },
              value: {},
              unit: { bsonType: ["string", "null"] },
              scale: { bsonType: ["string", "null"] }
            }
          }
        },
        created_at: { bsonType: "date" },
        updated_at: { bsonType: "date" }
      }
    }
  }
})
```

### 5.2 索引与查询场景

```javascript
db.cn_a_fundamental_cache.createIndex(
  { ticker: 1, market: 1, api_name: 1, report_period: -1, as_of: -1 },
  { name: "idx_ticker_api_period" }
)

db.cn_a_fundamental_cache.createIndex(
  { provider: 1, api_name: 1, ticker: 1, report_period: 1, announce_date: 1, as_of: 1, payload_hash: 1 },
  { name: "uk_provider_api_payload", unique: true }
)

db.cn_a_fundamental_cache.createIndex(
  { payload_hash: 1 },
  { name: "idx_payload_hash" }
)

db.cn_a_fundamental_cache.createIndex(
  { expires_at: 1 },
  { name: "ttl_expire_at", expireAfterSeconds: 0 }
)
```

索引对应场景：

- `idx_ticker_api_period`：按股票与接口读取最新可用记录。
- `uk_provider_api_payload`：幂等写入。
- `idx_payload_hash`：从 evidence hash 反查缓存。
- `ttl_expire_at`：仅对短周期字段记录生效。

### 5.3 TTL 策略

- 不使用集合级自动删除 raw 证据。
- 仅对 `price_context` 与 `valuation` 写入 `expires_at`。
- 财报、分红、股东、主营字段 `expires_at=null`，不走 TTL 自动删除。

## 6. 安全与运维

### 6.1 secret 来源与访问

- 来源：部署 secret manager 或容器 secret。
- 读取：进程启动时读取到内存，不写本地文件。
- 日志：不输出 token、密码、连接串明文。

### 6.2 MongoDB URI 与鉴权

- `CN_A_MONGODB_URI` 必须是 `mongodb://` 或 `mongodb+srv://`。
- 本地开发 MongoDB 可使用未启用 TLS 的 `localhost` / `127.0.0.1` 连接。
- URI 带用户名或密码时，必须明确配置 `authSource`。
- 连接参数建议：
  - `maxPoolSize=10`
  - `minPoolSize=1`
  - `serverSelectionTimeoutMS=3000`
  - `connectTimeoutMS=3000`

### 6.3 脱敏算法

`sanitize_error(message)` 规则：

1. key 命中 `token|secret|password|passwd|api_key|authorization|cookie|session` 时，value 全量替换为 `***`。
2. 连续 20 位以上字母数字串替换为前 4 位加 `***` 加后 2 位。
3. URL query 中敏感参数值替换为 `***`。
4. 脱敏后最大长度 1000 字符。

### 6.4 health 与 readiness

- liveness:
  - 进程存活；
  - 配置解析成功。
- readiness:
  - MongoDB `ping` 小于 500ms；
  - OpenViking health 接口可达，小于 1000ms；
  - Tushare token 存在性检查完成。

### 6.5 metrics labels 与阈值

指标标签统一：

- `worker_id`
- `market`
- `provider`
- `api_name`
- `status`
- `quality_status`

建议告警阈值：

- `fundamental_pack_quality_status_total{quality_status="failed"}` 5 分钟内占比大于 5%；
- `fundamental_tushare_call_total{status="skipped",reason="missing_token"}` 15 分钟内大于 0；
- `fundamental_raw_payload_write_total{status="error"}` 5 分钟内大于 0；
- `fundamental_gate_fail_total{reason="unsupported_claim"}` 10 分钟内连续增长。

### 6.6 优雅停机

- 停止接收新请求；
- 等待在途请求完成，最长 30 秒；
- 将未完成请求写入失败诊断；
- 关闭 Mongo 连接池；
- 刷新 metrics 与审计日志。

### 6.7 回滚与禁用开关

- `CN_A_FUNDAMENTAL_TOOL_ENABLED=false`：禁用该工具入口。
- `CN_A_FUNDAMENTAL_DISABLE_TUSHARE=true`：跳过 Tushare，输出明确缺口。
- `CN_A_FUNDAMENTAL_DISABLE_AKSHARE=true`：跳过 AkShare，输出明确缺口。
- `CN_A_FUNDAMENTAL_CACHE_READ_ONLY=true`：只读缓存，不执行 upsert。

## 7. 验收说明

实现阶段需执行三类检查：

1. 禁用词与占位文本检查：文档正文无禁用词命中。
2. 关键函数名检查：存在 provider 调用、字段抽取、raw 写入、claim 校验等关键设计名。
3. 过度覆盖声明检查：文档不出现覆盖率口号式描述。
