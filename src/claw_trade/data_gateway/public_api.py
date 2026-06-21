from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from claw_trade.data_gateway.models import DataGap, DataResult, DataResultStatus, GapReason, Market


class PublicRequestPriority(str, Enum):
    REQUIRED = "required"
    NORMAL = "normal"
    OPTIONAL = "optional"
    EXPENSIVE = "expensive"


FORBIDDEN_PUBLIC_PARAM_KEYS = frozenset(
    {
        "provider",
        "path",
        "api_name",
        "url",
        "header",
        "headers",
        "header_name",
        "token",
        "api_key",
        "secret",
        "worker",
        "worker_id",
        "domain",
        "report_section",
        "allowed_worker",
        "allowed_domain",
        "allowed_report_section",
        "provider_scope",
        "fields",
        "data_type",
        "params",
        "api_id",
    }
)


class PublicDataRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    request_id: str
    item: str
    market: Market
    instrument: str
    time_range_start: date | datetime | None = None
    time_range_end: date | datetime | None = None
    granularity: str | None = None
    priority: PublicRequestPriority = PublicRequestPriority.NORMAL
    requested_by_worker: str
    purpose: str
    freshness_policy: str = "trading_day"
    deadline_at: datetime
    consumer: str = "report"

    @property
    def api_id(self) -> str:
        return public_api_id_for_item(item=self.item, market=self.market)

    @model_validator(mode="after")
    def validate_request(self) -> "PublicDataRequest":
        for field_name in ("request_id", "item", "instrument", "requested_by_worker", "purpose", "consumer"):
            _require_non_empty(field_name, str(getattr(self, field_name)))
        _ = self.api_id
        if self.deadline_at.tzinfo is None or self.deadline_at.utcoffset() is None:
            raise ValueError("deadline_at 必须有 timezone")
        if self.time_range_start is not None and self.time_range_end is not None:
            start = self.time_range_start.date() if isinstance(self.time_range_start, datetime) else self.time_range_start
            end = self.time_range_end.date() if isinstance(self.time_range_end, datetime) else self.time_range_end
            if start > end:
                raise ValueError("time_range_start 不能晚于 time_range_end")
        forbidden = forbidden_public_payload_keys(self.model_dump(mode="python"))
        if forbidden:
            raise ValueError("provider_execution_detail_rejected")
        return self


class PublicApiContract(BaseModel):
    api_id: str
    market: Market
    params_schema: dict[str, Any] = Field(default_factory=dict)
    output_contract: dict[str, Any]
    granularities: tuple[str, ...]
    asset_classes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def validate_contract(self) -> "PublicApiContract":
        _require_non_empty("api_id", self.api_id)
        dataset = str(self.output_contract.get("dataset") or "").strip()
        if not dataset:
            raise ValueError("output_contract.dataset 不能为空")
        if not self.granularities:
            raise ValueError("granularities 不能为空")
        return self


class ApiImplementation(BaseModel):
    api_id: str
    provider_id: str
    catalog_endpoint_id: str
    priority_rank: int
    source_role: str
    param_map: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_implementation(self) -> "ApiImplementation":
        for field_name in ("api_id", "provider_id", "catalog_endpoint_id", "source_role"):
            _require_non_empty(field_name, str(getattr(self, field_name)))
        if self.priority_rank < 0:
            raise ValueError("priority_rank 必须 >= 0")
        return self


class PublicRequestValidation(BaseModel):
    ok: bool
    request: PublicDataRequest | None = None
    reason: str | None = None
    result: DataResult | None = None


def validate_public_request(raw: PublicDataRequest | Mapping[str, Any]) -> PublicRequestValidation:
    if isinstance(raw, PublicDataRequest):
        return PublicRequestValidation(ok=True, request=raw)
    request_id = str(raw.get("request_id") or raw.get("requestId") or raw.get("need_id") or "unknown")
    market = _extract_market(raw)
    forbidden = forbidden_public_payload_keys(raw)
    if forbidden:
        return _invalid_validation(
            reason="provider_execution_detail_rejected",
            request_id=request_id,
            market=market,
            message="公开数据请求不能包含数据层执行细节",
        )
    try:
        request = PublicDataRequest.model_validate(raw)
    except ValidationError as exc:
        return _invalid_validation(
            reason="invalid_public_request",
            request_id=request_id,
            market=market,
            message=f"invalid_public_request: {exc.errors()[0]['msg']}",
        )
    if request.api_id not in PUBLIC_API_CONTRACTS:
        return _invalid_validation(
            reason="unknown_business_data_item",
            request_id=request.request_id,
            market=request.market,
            message=f"unknown_business_data_item: {request.item}",
        )
    return PublicRequestValidation(ok=True, request=request)


def forbidden_public_payload_keys(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for key, child in value.items():
            key_text = str(key)
            if key_text in FORBIDDEN_PUBLIC_PARAM_KEYS or key_text.startswith(("allowed_", "only_for_")):
                found.add(key_text)
            found.update(forbidden_public_payload_keys(child))
    elif isinstance(value, (list, tuple)):
        for child in value:
            found.update(forbidden_public_payload_keys(child))
    return found


def public_api_ids_for_output(
    *,
    market: str | Market,
    data_type: str,
    public_api_ids: Sequence[str] = (),
) -> tuple[str, ...]:
    prefix = _market_prefix(market)
    values: list[str] = []
    derived = _derived_public_api_id(prefix=prefix, data_type=data_type)
    if derived:
        values.append(derived)
    for raw in public_api_ids:
        text = _canonical_public_api_suffix(str(raw or "").strip().lower().replace("-", "_"))
        if not text:
            continue
        candidate = text if "." in text else f"{prefix}.{text}"
        if candidate in PUBLIC_API_CONTRACTS:
            values.append(candidate)
    return tuple(dict.fromkeys(values))


def public_output_contract_for_api(api_id: str) -> dict[str, Any]:
    contract = PUBLIC_API_CONTRACTS.get(api_id)
    return dict(contract.output_contract) if contract is not None else {}


def public_result_satisfies_contract(request: PublicDataRequest, result: DataResult) -> bool:
    if result.status != DataResultStatus.READY:
        return False
    contract = PUBLIC_API_CONTRACTS.get(request.api_id)
    if contract is None:
        return False
    if not result.rows:
        return bool(result.dataset_refs)
    return any(_row_satisfies_output_contract(row, contract.output_contract) for row in result.rows if isinstance(row, Mapping))


def public_api_contracts() -> tuple[PublicApiContract, ...]:
    return tuple(PUBLIC_API_CONTRACTS.values())


def public_api_id_for_item(*, item: str, market: str | Market) -> str:
    market_value = market if isinstance(market, Market) else _extract_market({"market": market})
    if market_value == Market.CRYPTO and _is_plain_macro_item(item) and "crypto.macro_series" in PUBLIC_API_CONTRACTS:
        return "crypto.macro_series"
    suffix = _business_item_suffix(item)
    if not suffix:
        raise ValueError(f"未知业务数据项: {item}")
    api_id = f"{_market_prefix(market_value)}.{suffix}"
    if api_id not in PUBLIC_API_CONTRACTS:
        raise ValueError(f"当前市场不支持该业务数据项: {item}")
    return api_id


def _invalid_validation(*, reason: str, request_id: str, market: Market, message: str) -> PublicRequestValidation:
    as_of = datetime.now(tz=UTC)
    gap = DataGap.invalid_request(request_id=request_id, market=market, message=message, as_of=as_of)
    return PublicRequestValidation(
        ok=False,
        reason=reason,
        result=DataResult(request_id=request_id, status=DataResultStatus.ERROR, gaps=(gap,), as_of=as_of),
    )


def _contract(api_id: str, *, market: Market, dataset: str, granularity: str, required_fields: tuple[str, ...]) -> PublicApiContract:
    event_contract = None
    if granularity in {"event", "realtime"} and dataset in {"company_news", "macro_news", "event_calendar", "official_filing", "social_signal"}:
        event_contract = {
            "event_time_field": "published_at",
            "title_field": "title",
            "source_field": "source",
            "url_field": "url",
        }
    standard_contract = _standard_output_contract_shape(api_id=api_id, dataset=dataset, required_fields=required_fields)
    return PublicApiContract(
        api_id=api_id,
        market=market,
        granularities=(granularity,),
        output_contract={
            "dataset": dataset,
            "granularity": granularity,
            "required_fields": required_fields,
            "standard_output_contract_id": standard_contract["standard_output_contract_id"],
            "satisfaction_contract_id": standard_contract["satisfaction_contract_id"],
            "all_of": standard_contract["all_of"],
            "any_of_groups": standard_contract["any_of_groups"],
            "identity_fields": ("symbol_id", "market"),
            "event_contract": event_contract,
            "parser_required": True,
        },
    )


def _standard_output_contract_shape(*, api_id: str, dataset: str, required_fields: tuple[str, ...]) -> dict[str, Any]:
    suffix = api_id.rsplit(".", 1)[-1]
    all_of = tuple(required_fields)
    any_of_groups: tuple[tuple[str, ...], ...] = ()
    if suffix == "realtime_quote":
        all_of = tuple(field for field in required_fields if field != "price")
        any_of_groups = (("price", "last_price", "close"),)
    elif suffix == "order_book":
        all_of = tuple(field for field in required_fields if field not in {"bid_price", "ask_price"})
        any_of_groups = (("bid_price", "bid_price_1", "bids"), ("ask_price", "ask_price_1", "asks"))
    elif suffix == "liquidation":
        all_of = tuple(field for field in required_fields if field != "liquidation_value")
        any_of_groups = (("liquidation_value", "long_liquidation", "short_liquidation", "liquidation_quantity"),)
    elif suffix == "liquidation_heatmap":
        all_of = tuple(field for field in required_fields if field not in {"liquidation_price", "liquidation_value"})
        any_of_groups = (("liquidation_price", "price", "price_range"), ("liquidation_value", "liquidation_size"))
    elif suffix == "exchange_netflow":
        all_of = tuple(field for field in required_fields if field != "net_inflow")
        any_of_groups = (("net_inflow", "netflow", "inflow"),)
    elif suffix == "exchange_balance":
        all_of = tuple(field for field in required_fields if field != "exchange_balance")
        any_of_groups = (("exchange_balance", "balance", "value"),)
    elif suffix == "ahr999":
        all_of = tuple(field for field in required_fields if field not in {"ahr999", "value"})
        any_of_groups = (("ahr999", "value", "metric_value"),)
    elif suffix == "onchain_metric":
        all_of = tuple(field for field in required_fields if field != "value")
        any_of_groups = (("ahr999", "value", "metric_value"),)
    elif dataset in {"company_news", "macro_news", "event_calendar", "official_filing", "social_signal"}:
        all_of = tuple(field for field in required_fields if field not in {"published_at", "event_date", "timestamp"})
        any_of_groups = (("published_at", "event_date", "timestamp"),)
    return {
        "standard_output_contract_id": f"{dataset}.v1",
        "satisfaction_contract_id": f"{api_id}.satisfaction.v1",
        "all_of": all_of,
        "any_of_groups": any_of_groups,
    }


def _row_satisfies_output_contract(row: Mapping[str, Any], contract: Mapping[str, Any]) -> bool:
    present = {str(key) for key, value in row.items() if value is not None and str(value).strip() != ""}
    all_of = tuple(str(field) for field in tuple(contract.get("all_of") or contract.get("required_fields") or ()))
    if any(field not in present for field in all_of):
        return False
    any_of_groups = tuple(contract.get("any_of_groups") or ())
    for group in any_of_groups:
        values = tuple(str(field) for field in tuple(group or ()))
        if values and not any(field in present for field in values):
            return False
    return True


def _build_public_api_contracts() -> dict[str, PublicApiContract]:
    rows = [
        ("cn_a.daily_bar", Market.CN_A, "daily_bar", "daily", ("date", "open", "high", "low", "close", "volume")),
        ("cn_a.intraday_bar", Market.CN_A, "intraday_bar", "intraday", ("timestamp", "open", "high", "low", "close", "volume")),
        ("cn_a.realtime_quote", Market.CN_A, "quote_snapshot", "realtime", ("price", "timestamp", "symbol_id")),
        ("cn_a.order_book", Market.CN_A, "order_book_snapshot", "realtime", ("bid_price", "ask_price", "timestamp", "symbol_id")),
        ("cn_a.financial_statement", Market.CN_A, "financial_statement", "quarterly", ("period", "revenue", "net_income")),
        ("cn_a.financial_metric", Market.CN_A, "financial_metric", "quarterly", ("period", "roe", "eps")),
        ("cn_a.valuation_metric", Market.CN_A, "valuation_metric", "daily", ("date", "pe", "pb")),
        ("cn_a.capital_flow", Market.CN_A, "capital_flow", "daily", ("date", "symbol_id")),
        ("cn_a.northbound_flow", Market.CN_A, "northbound_flow", "realtime", ("timestamp", "northbound_net", "hgt_net", "sgt_net")),
        ("cn_a.margin_trading", Market.CN_A, "margin_trading", "daily", ("date", "financing_balance", "margin_balance", "symbol_id")),
        ("cn_a.company_news", Market.CN_A, "company_news", "event", ("title", "published_at", "source")),
        ("cn_a.macro_news", Market.CN_A, "macro_news", "event", ("title", "published_at", "source")),
        ("cn_a.official_filing", Market.CN_A, "official_filing", "event", ("title", "published_at", "source")),
        ("cn_a.social_signal", Market.CN_A, "social_signal", "event", ("source", "timestamp")),
        ("cn_a.event_calendar", Market.CN_A, "event_calendar", "event", ("event_date", "event_type", "title")),
        ("cn_a.hot_money_event", Market.CN_A, "hot_money_event", "event", ("trade_date", "symbol_id")),
        ("cn_a.lockup_event", Market.CN_A, "lockup_event", "event", ("unlock_date", "holder")),
        ("cn_a.sector_snapshot", Market.CN_A, "sector_snapshot", "event", ("sector_name", "timestamp")),
        ("cn_a.sector_flow", Market.CN_A, "sector_snapshot", "event", ("sector_name", "timestamp", "main_net")),
        ("cn_a.corporate_action", Market.CN_A, "corporate_action", "event", ("event_type", "event_date")),
        ("crypto.daily_bar", Market.CRYPTO, "daily_bar", "daily", ("date", "open", "high", "low", "close", "volume")),
        ("crypto.intraday_bar", Market.CRYPTO, "intraday_bar", "intraday", ("timestamp", "open", "high", "low", "close", "volume")),
        ("crypto.realtime_quote", Market.CRYPTO, "quote_snapshot", "realtime", ("price", "timestamp", "symbol_id")),
        ("crypto.order_book", Market.CRYPTO, "order_book_snapshot", "realtime", ("bid_price", "ask_price", "timestamp")),
        ("crypto.valuation_metric", Market.CRYPTO, "valuation_metric", "realtime", ("price", "market_cap")),
        ("crypto.funding_rate", Market.CRYPTO, "crypto_derivative_metric", "realtime", ("funding_rate", "timestamp")),
        ("crypto.open_interest", Market.CRYPTO, "crypto_derivative_metric", "hourly", ("open_interest", "timestamp")),
        ("crypto.long_short_ratio", Market.CRYPTO, "crypto_derivative_metric", "realtime", ("long_short_ratio", "timestamp")),
        ("crypto.liquidation", Market.CRYPTO, "crypto_derivative_metric", "realtime", ("liquidation_value", "timestamp")),
        ("crypto.liquidation_heatmap", Market.CRYPTO, "crypto_derivative_metric", "realtime", ("liquidation_price", "liquidation_value")),
        ("crypto.cvd", Market.CRYPTO, "crypto_derivative_metric", "realtime", ("cvd", "timestamp")),
        ("crypto.taker_buy_sell", Market.CRYPTO, "crypto_derivative_metric", "realtime", ("taker_buy_volume", "taker_sell_volume")),
        ("crypto.exchange_netflow", Market.CRYPTO, "crypto_derivative_metric", "realtime", ("net_inflow", "timestamp")),
        ("crypto.options_open_interest", Market.CRYPTO, "crypto_derivative_metric", "realtime", ("options_open_interest", "timestamp")),
        ("crypto.options_volume", Market.CRYPTO, "crypto_derivative_metric", "realtime", ("options_volume", "timestamp")),
        ("crypto.etf_flow", Market.CRYPTO, "crypto_derivative_metric", "realtime", ("etf_flow_usd", "timestamp")),
        ("crypto.borrow_interest_rate", Market.CRYPTO, "crypto_derivative_metric", "realtime", ("borrow_interest_rate", "timestamp")),
        ("crypto.exchange_balance", Market.CRYPTO, "crypto_onchain_metric", "realtime", ("exchange_balance", "timestamp")),
        ("crypto.whale_transfer", Market.CRYPTO, "crypto_onchain_metric", "event", ("whale_transfer", "timestamp")),
        ("crypto.ahr999", Market.CRYPTO, "crypto_onchain_metric", "daily", ("ahr999", "value", "timestamp")),
        ("crypto.onchain_metric", Market.CRYPTO, "crypto_onchain_metric", "daily", ("metric", "value", "timestamp")),
        ("crypto.defi_metric", Market.CRYPTO, "defi_metric", "realtime", ("tvl", "symbol_id")),
        ("crypto.company_profile", Market.CRYPTO, "company_profile", "event", ("name", "symbol")),
        ("crypto.company_news", Market.CRYPTO, "company_news", "event", ("title", "published_at", "source")),
        ("crypto.macro_series", Market.CRYPTO, "macro_series", "monthly", ("series_id", "date", "value")),
        ("crypto.macro_news", Market.CRYPTO, "macro_news", "event", ("title", "published_at", "source")),
        ("crypto.event_calendar", Market.CRYPTO, "event_calendar", "event", ("event_date", "event_type", "title")),
        ("crypto.social_signal", Market.CRYPTO, "social_signal", "event", ("source", "timestamp")),
        ("us.daily_bar", Market.US, "daily_bar", "daily", ("date", "open", "high", "low", "close", "volume")),
        ("us.intraday_bar", Market.US, "intraday_bar", "intraday", ("timestamp", "open", "high", "low", "close", "volume")),
        ("us.realtime_quote", Market.US, "quote_snapshot", "realtime", ("price", "timestamp", "symbol_id")),
        ("us.company_news", Market.US, "company_news", "event", ("title", "published_at", "source")),
        ("us.macro_news", Market.US, "macro_news", "event", ("title", "published_at", "source")),
        ("us.macro_series", Market.US, "macro_series", "monthly", ("series_id", "date", "value")),
        ("us.valuation_metric", Market.US, "valuation_metric", "realtime", ("pe", "market_cap")),
        ("us.financial_metric", Market.US, "financial_metric", "quarterly", ("period", "eps")),
        ("us.financial_statement", Market.US, "financial_statement", "quarterly", ("period", "revenue", "net_income")),
        ("us.official_filing", Market.US, "official_filing", "event", ("title", "published_at", "source")),
        ("us.event_calendar", Market.US, "event_calendar", "event", ("event_date", "event_type", "title")),
        ("us.corporate_action", Market.US, "corporate_action", "event", ("event_type", "event_date")),
        ("us.company_profile", Market.US, "company_profile", "event", ("name", "industry")),
        ("us.social_signal", Market.US, "social_signal", "event", ("source", "timestamp")),
        ("hk.daily_bar", Market.HK, "daily_bar", "daily", ("date", "open", "high", "low", "close", "volume")),
        ("hk.intraday_bar", Market.HK, "intraday_bar", "intraday", ("timestamp", "open", "high", "low", "close", "volume")),
        ("hk.realtime_quote", Market.HK, "quote_snapshot", "realtime", ("price", "timestamp", "symbol_id")),
        ("hk.company_news", Market.HK, "company_news", "event", ("title", "published_at", "source")),
        ("hk.macro_news", Market.HK, "macro_news", "event", ("title", "published_at", "source")),
        ("hk.valuation_metric", Market.HK, "valuation_metric", "realtime", ("pe", "market_cap")),
        ("hk.financial_metric", Market.HK, "financial_metric", "quarterly", ("period", "eps")),
        ("hk.financial_statement", Market.HK, "financial_statement", "quarterly", ("period", "revenue", "net_income")),
        ("hk.official_filing", Market.HK, "official_filing", "event", ("title", "published_at", "source")),
        ("hk.event_calendar", Market.HK, "event_calendar", "event", ("event_date", "event_type", "title")),
        ("hk.social_signal", Market.HK, "social_signal", "event", ("source", "timestamp")),
    ]
    return {api_id: _contract(api_id, market=market, dataset=dataset, granularity=granularity, required_fields=fields) for api_id, market, dataset, granularity, fields in rows}


def _derived_public_api_id(*, prefix: str, data_type: str) -> str | None:
    suffix = {
        "quote_snapshot": "realtime_quote",
        "order_book_snapshot": "order_book",
    }.get(str(data_type or "").strip().lower(), str(data_type or "").strip().lower())
    if not suffix:
        return None
    candidate = f"{prefix}.{suffix}"
    return candidate if candidate in PUBLIC_API_CONTRACTS else None


def _canonical_public_api_suffix(value: str) -> str:
    return {
        "quote_snapshot": "realtime_quote",
        "order_book_snapshot": "order_book",
        "auction_order_book_snapshot": "order_book",
        "money_flow": "capital_flow",
        "liquidation_map": "liquidation_heatmap",
        "active_buy_sell": "taker_buy_sell",
        "crypto_onchain_metric": "onchain_metric",
        "exchange_balance": "exchange_balance",
        "whale_transfer": "whale_transfer",
        "options_open_interest": "options_open_interest",
        "options_volume": "options_volume",
        "etf_flow": "etf_flow",
        "borrow_interest_rate": "borrow_interest_rate",
    }.get(value, value)


def _business_item_suffix(value: str) -> str | None:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return {
        "k线": "daily_bar",
        "日线": "daily_bar",
        "kline": "daily_bar",
        "k_line": "daily_bar",
        "daily": "daily_bar",
        "daily_bar": "daily_bar",
        "分钟线": "intraday_bar",
        "分时": "intraday_bar",
        "intraday": "intraday_bar",
        "intraday_bar": "intraday_bar",
        "实时价": "realtime_quote",
        "行情快照": "realtime_quote",
        "quote": "realtime_quote",
        "realtime_quote": "realtime_quote",
        "盘口": "order_book",
        "order_book": "order_book",
        "财报": "financial_statement",
        "financial_statement": "financial_statement",
        "财务指标": "financial_metric",
        "financial_metric": "financial_metric",
        "估值": "valuation_metric",
        "valuation_metric": "valuation_metric",
        "资金流": "capital_flow",
        "capital_flow": "capital_flow",
        "北向": "northbound_flow",
        "北向资金": "northbound_flow",
        "沪深股通": "northbound_flow",
        "northbound": "northbound_flow",
        "northbound_flow": "northbound_flow",
        "融资融券": "margin_trading",
        "两融": "margin_trading",
        "杠杆资金": "margin_trading",
        "margin_trading": "margin_trading",
        "板块资金": "sector_flow",
        "行业资金": "sector_flow",
        "板块资金轮动": "sector_flow",
        "行业/板块资金轮动": "sector_flow",
        "sector_flow": "sector_flow",
        "公司新闻": "company_news",
        "新闻": "company_news",
        "company_news": "company_news",
        "宏观": "macro_news",
        "宏观新闻": "macro_news",
        "macro_news": "macro_news",
        "公告": "official_filing",
        "公司公告": "official_filing",
        "官方公告": "official_filing",
        "公告原文": "official_filing",
        "公司公告原文": "official_filing",
        "官方公告原文": "official_filing",
        "official_filing": "official_filing",
        "社交情绪": "social_signal",
        "市场情绪": "social_signal",
        "市场情绪指标": "social_signal",
        "情绪指标": "social_signal",
        "恐惧贪婪": "social_signal",
        "恐惧与贪婪": "social_signal",
        "恐惧与贪婪指数": "social_signal",
        "fear_greed": "social_signal",
        "fear_and_greed": "social_signal",
        "fear_and_greed_index": "social_signal",
        "social_signal": "social_signal",
        "事件日历": "event_calendar",
        "event_calendar": "event_calendar",
        "游资": "hot_money_event",
        "hot_money_event": "hot_money_event",
        "解禁": "lockup_event",
        "lockup_event": "lockup_event",
        "板块": "sector_snapshot",
        "sector_snapshot": "sector_snapshot",
        "公司行动": "corporate_action",
        "corporate_action": "corporate_action",
        "资金费率": "funding_rate",
        "funding": "funding_rate",
        "funding_rate": "funding_rate",
        "oi": "open_interest",
        "未平仓量": "open_interest",
        "open_interest": "open_interest",
        "多空比": "long_short_ratio",
        "long_short_ratio": "long_short_ratio",
        "清算": "liquidation",
        "liquidation": "liquidation",
        "清算地图": "liquidation_heatmap",
        "清算热力图": "liquidation_heatmap",
        "liquidation_heatmap": "liquidation_heatmap",
        "cvd": "cvd",
        "主动买卖量差": "cvd",
        "taker_buy_sell": "taker_buy_sell",
        "交易所净流量": "exchange_netflow",
        "exchange_netflow": "exchange_netflow",
        "期权持仓": "options_open_interest",
        "options_open_interest": "options_open_interest",
        "期权成交": "options_volume",
        "options_volume": "options_volume",
        "etf资金流": "etf_flow",
        "机构产品资金流": "etf_flow",
        "etf_flow": "etf_flow",
        "借贷利率": "borrow_interest_rate",
        "borrow_interest_rate": "borrow_interest_rate",
        "交易所余额": "exchange_balance",
        "exchange_balance": "exchange_balance",
        "大额转账": "whale_transfer",
        "whale_transfer": "whale_transfer",
        "ahr999": "ahr999",
        "链上": "onchain_metric",
        "onchain": "onchain_metric",
        "onchain_metric": "onchain_metric",
        "defi": "defi_metric",
        "defi_metric": "defi_metric",
        "项目资料": "company_profile",
        "公司资料": "company_profile",
        "company_profile": "company_profile",
        "宏观序列": "macro_series",
        "macro_series": "macro_series",
    }.get(text)


def _is_plain_macro_item(value: str) -> bool:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return text in {"宏观", "宏观数据", "macro", "macro_data"}


def _market_prefix(market: str | Market) -> str:
    value = market.value if isinstance(market, Market) else str(market or "")
    return value.strip().lower()


def _extract_market(payload: Mapping[str, Any]) -> Market:
    raw_market = payload.get("market")
    if isinstance(raw_market, Market):
        return raw_market
    if isinstance(raw_market, str):
        try:
            return Market(raw_market.strip().upper())
        except ValueError:
            return Market.CN_A
    return Market.CN_A


def _require_non_empty(field_name: str, value: str) -> None:
    if not value or not value.strip():
        raise ValueError(f"{field_name} 不能为空")


PUBLIC_API_CONTRACTS = _build_public_api_contracts()


__all__ = [
    "ApiImplementation",
    "FORBIDDEN_PUBLIC_PARAM_KEYS",
    "PUBLIC_API_CONTRACTS",
    "PublicApiContract",
    "PublicDataRequest",
    "PublicRequestPriority",
    "PublicRequestValidation",
    "forbidden_public_payload_keys",
    "public_api_contracts",
    "public_api_ids_for_output",
    "public_api_id_for_item",
    "public_output_contract_for_api",
    "public_result_satisfies_contract",
    "validate_public_request",
]
