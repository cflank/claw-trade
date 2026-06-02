from __future__ import annotations

import json
import re
import socket
from email.utils import parsedate_to_datetime
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree

from claw_trade.data_gateway.execution.managed_http import HttpRequestSpec
from claw_trade.data_gateway.models import FetchResult
from claw_trade.data_gateway.providers.plugins.common import (
    BatchPolicy,
    CredentialPolicy,
    EndpointCapability,
    LicensePolicy,
    ProviderCapabilities,
)
from claw_trade.data_gateway.providers.plugins.http_daily_bar import TushareDailyBarPlugin


_CN_A_LICENSE = LicensePolicy(
    raw_storage_mode="metadata_only",
    normalized_storage_allowed=True,
    redistribution_allowed=False,
    retention_days=30,
)

_NO_CREDENTIALS = CredentialPolicy(
    credential_required=False,
    credential_names=(),
    credential_scope=None,
    missing_behavior="credential_missing",
)

_TUSHARE_CREDENTIAL = "data_source:tushare"
_TUSHARE_ENDPOINT = "https://api.tushare.pro"
_CNINFO_ENDPOINT = "https://www.cninfo.com.cn"
_EASTMONEY_DATACENTER_ENDPOINT = "https://datacenter-web.eastmoney.com"
_EASTMONEY_APPDATA_ENDPOINT = "https://emappdata.eastmoney.com"
_EASTMONEY_SEARCH_ENDPOINT = "https://search-api-web.eastmoney.com"
_EASTMONEY_PUSH2_ENDPOINT = "https://push2.eastmoney.com"
_EASTMONEY_PUSH2HIS_ENDPOINT = "https://push2his.eastmoney.com"
_EASTMONEY_NP_WEBLIST_ENDPOINT = "https://np-weblist.eastmoney.com"
_THS_HOT_ENDPOINT = "http://zx.10jqka.com.cn"
_BAIDU_FINANCE_ENDPOINT = "https://finance.pae.baidu.com"
_GOOGLE_NEWS_ENDPOINT = "https://news.google.com"
_BROWSER_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/81.0.4044.138 Safari/537.36"
_COMMON_HEADERS = {"accept": "application/json", "user-agent": _BROWSER_USER_AGENT}
_BAOSTOCK_SOCKET_TIMEOUT_SECONDS = 8.0
_MOOTDX_SOCKET_TIMEOUT_SECONDS = 5.0


class TushareFundamentalPlugin:
    plugin_id = "cn_a_tushare_fundamental"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                _endpoint(
                    endpoint_id="financial_statement",
                    data_type="financial_statement",
                    source_role="paid_data",
                    granularity=("quarterly",),
                    fields=("period", "revenue", "net_income", "assets", "liabilities", "cash_flow"),
                    priority_rank=10,
                ),
                _endpoint(
                    endpoint_id="financial_metric",
                    data_type="financial_metric",
                    source_role="paid_data",
                    granularity=("quarterly",),
                    fields=("roe", "roa", "gross_margin", "debt_ratio", "eps"),
                    priority_rank=10,
                ),
                _endpoint(
                    endpoint_id="valuation_metric",
                    data_type="valuation_metric",
                    source_role="paid_data",
                    granularity=("daily",),
                    fields=("pe", "pb", "ps", "market_cap"),
                    priority_rank=10,
                ),
                _endpoint(
                    endpoint_id="moneyflow",
                    data_type="capital_flow",
                    source_role="paid_data",
                    granularity=("daily",),
                    fields=("date", "main_net", "small_net", "mid_net", "large_net", "super_net", "symbol_id"),
                    priority_rank=8,
                ),
                _endpoint(
                    endpoint_id="moneyflow_ths",
                    data_type="capital_flow",
                    source_role="paid_data",
                    granularity=("daily",),
                    fields=("date", "main_net", "small_net", "mid_net", "large_net", "symbol_id"),
                    priority_rank=9,
                ),
                _endpoint(
                    endpoint_id="moneyflow_ind_dc",
                    data_type="sector_snapshot",
                    source_role="paid_data",
                    granularity=("event",),
                    fields=("sector_code", "sector_name", "main_net", "super_net", "large_net", "mid_net", "small_net", "timestamp"),
                    priority_rank=8,
                ),
                _endpoint(
                    endpoint_id="moneyflow_ind_ths",
                    data_type="sector_snapshot",
                    source_role="paid_data",
                    granularity=("event",),
                    fields=("sector_code", "sector_name", "main_net", "timestamp"),
                    priority_rank=9,
                ),
                _endpoint(
                    endpoint_id="moneyflow_cnt_ths",
                    data_type="sector_snapshot",
                    source_role="paid_data",
                    granularity=("event",),
                    fields=("sector_code", "sector_name", "main_net", "timestamp"),
                    priority_rank=10,
                ),
                _endpoint(
                    endpoint_id="anns_d",
                    data_type="official_filing",
                    source_role="paid_data",
                    granularity=("event",),
                    fields=("title", "published_at", "url", "source", "body_ref", "symbol_id"),
                    priority_rank=12,
                ),
                _endpoint(
                    endpoint_id="investor_interaction",
                    data_type="social_signal",
                    source_role="sentiment",
                    granularity=("event",),
                    fields=("source", "timestamp", "question", "answer", "symbol_id"),
                    priority_rank=20,
                    can_be_formal_fact_source=False,
                ),
            ),
            credential_policy=CredentialPolicy(
                credential_required=True,
                credential_names=(_TUSHARE_CREDENTIAL,),
                credential_scope="provider_token",
                missing_behavior="credential_missing",
            ),
            license_policy=_CN_A_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": None},
            default_priority_rank=10,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        token = _credential_value(ctx, _TUSHARE_CREDENTIAL)
        if token is None:
            return FetchResult.from_error(
                task,
                status="credential_missing",
                error=RuntimeError(f"credential_missing:{_TUSHARE_CREDENTIAL}"),
            )
        managed_http = _managed_http(ctx)
        if managed_http is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))

        symbol = _first_symbol(task)
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))

        endpoint_id = str(getattr(task, "endpoint_id", ""))
        if endpoint_id == "financial_statement":
            return self._fetch_statement(task, managed_http=managed_http, token=token, symbol=symbol, ctx=ctx)
        if endpoint_id == "financial_metric":
            return self._fetch_metric(task, managed_http=managed_http, token=token, symbol=symbol, ctx=ctx)
        if endpoint_id == "valuation_metric":
            return self._fetch_valuation(task, managed_http=managed_http, token=token, symbol=symbol, ctx=ctx)
        if endpoint_id == "moneyflow":
            return self._fetch_moneyflow(task, managed_http=managed_http, token=token, symbol=symbol, ctx=ctx)
        if endpoint_id == "moneyflow_ths":
            return self._fetch_moneyflow_ths(task, managed_http=managed_http, token=token, symbol=symbol, ctx=ctx)
        if endpoint_id == "moneyflow_ind_dc":
            return self._fetch_sector_moneyflow_dc(task, managed_http=managed_http, token=token, symbol=symbol, ctx=ctx)
        if endpoint_id == "moneyflow_ind_ths":
            return self._fetch_sector_moneyflow_ths(task, managed_http=managed_http, token=token, symbol=symbol, ctx=ctx)
        if endpoint_id == "moneyflow_cnt_ths":
            return self._fetch_concept_moneyflow_ths(task, managed_http=managed_http, token=token, symbol=symbol, ctx=ctx)
        if endpoint_id == "anns_d":
            return self._fetch_official_filing(task, managed_http=managed_http, token=token, symbol=symbol, ctx=ctx)
        if endpoint_id == "investor_interaction":
            return self._fetch_investor_interaction(task, managed_http=managed_http, token=token, symbol=symbol, ctx=ctx)
        return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))

    def _fetch_statement(self, task: Any, *, managed_http: Any, token: str, symbol: str, ctx: Any) -> FetchResult:
        observations: list[Any] = []
        merged: dict[str, dict[str, Any]] = {}
        specs = (
            ("income", "ts_code,end_date,revenue,n_income_attr_p,n_income,net_profit"),
            ("balancesheet", "ts_code,end_date,total_assets,total_liab"),
            ("cashflow", "ts_code,end_date,n_cashflow_act,net_cash_flows_oper_act"),
        )
        for api_name, fields in specs:
            result = _tushare_items(
                task,
                managed_http=managed_http,
                ctx=ctx,
                token=token,
                symbol=symbol,
                api_name=api_name,
                fields=fields,
                observations=observations,
            )
            if result.error is not None:
                if not merged:
                    return FetchResult.from_error(task, status="error", error=result.error, http_observations=tuple(observations))
                continue
            for row in result.rows:
                period = _period(row.get("end_date"))
                if period is None:
                    continue
                target = merged.setdefault(period, _base_row(task, symbol=symbol, dataset="financial_statement", period=period))
                if api_name == "income":
                    _set_decimal(target, "revenue", row.get("revenue"))
                    _set_decimal(target, "net_income", _pick(row, "n_income_attr_p", "n_income", "net_profit"))
                elif api_name == "balancesheet":
                    _set_decimal(target, "assets", row.get("total_assets"))
                    _set_decimal(target, "liabilities", row.get("total_liab"))
                elif api_name == "cashflow":
                    _set_decimal(target, "cash_flow", _pick(row, "n_cashflow_act", "net_cash_flows_oper_act"))
        rows = tuple(merged.values())
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": list(rows)}, row_count=len(rows), http_observations=tuple(observations))

    def _fetch_metric(self, task: Any, *, managed_http: Any, token: str, symbol: str, ctx: Any) -> FetchResult:
        observations: list[Any] = []
        result = _tushare_items(
            task,
            managed_http=managed_http,
            ctx=ctx,
            token=token,
            symbol=symbol,
            api_name="fina_indicator",
            fields="ts_code,end_date,roe,roa,grossprofit_margin,debt_to_assets,eps",
            observations=observations,
        )
        if result.error is not None:
            return FetchResult.from_error(task, status="error", error=result.error, http_observations=tuple(observations))
        rows: list[dict[str, Any]] = []
        for item in result.rows:
            period = _period(item.get("end_date"))
            if period is None:
                continue
            row = _base_row(task, symbol=symbol, dataset="financial_metric", period=period)
            _set_decimal(row, "roe", item.get("roe"))
            _set_decimal(row, "roa", item.get("roa"))
            _set_decimal(row, "gross_margin", item.get("grossprofit_margin"))
            _set_decimal(row, "debt_ratio", item.get("debt_to_assets"))
            _set_decimal(row, "eps", item.get("eps"))
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=tuple(observations))

    def _fetch_investor_interaction(self, task: Any, *, managed_http: Any, token: str, symbol: str, ctx: Any) -> FetchResult:
        observations: list[Any] = []
        api_name = _tushare_irm_api(symbol)
        result = _tushare_items(
            task,
            managed_http=managed_http,
            ctx=ctx,
            token=token,
            symbol=symbol,
            api_name=api_name,
            fields="ts_code,name,trade_date,q,a,pub_time,industry",
            observations=observations,
        )
        if result.error is not None:
            return FetchResult.from_error(task, status="error", error=result.error, http_observations=tuple(observations))
        rows: list[dict[str, Any]] = []
        for item in result.rows:
            timestamp = _parse_datetime(_pick(item, "pub_time", "trade_date"))
            period = timestamp.date() if timestamp else _parse_yyyymmdd(item.get("trade_date"))
            question = _text(_pick(item, "q", "question"))
            answer = _text(_pick(item, "a", "answer"))
            if not question and not answer:
                continue
            row = _base_event_row(task, symbol=symbol, dataset="social_signal", period=period)
            row.update(
                {
                    "source": api_name,
                    "timestamp": timestamp,
                    "question": question,
                    "answer": answer,
                    "company_name": _text(_pick(item, "name")),
                    "industry": _text(_pick(item, "industry")),
                    "symbol_id": symbol,
                    "quality_flags": ("text_sample_without_sentiment_score",),
                }
            )
            row["source_roles"] = ("sentiment",)
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=tuple(observations))

    def _fetch_official_filing(self, task: Any, *, managed_http: Any, token: str, symbol: str, ctx: Any) -> FetchResult:
        observations: list[Any] = []
        result = _tushare_items(
            task,
            managed_http=managed_http,
            ctx=ctx,
            token=token,
            symbol=symbol,
            api_name="anns_d",
            fields="ts_code,ann_date,ann_time,title,url",
            observations=observations,
        )
        if result.error is not None:
            return FetchResult.from_error(task, status="error", error=result.error, http_observations=tuple(observations))
        rows: list[dict[str, Any]] = []
        for item in result.rows:
            timestamp = _parse_datetime(_pick(item, "ann_time", "ann_date"))
            published_date = timestamp.date() if timestamp else _parse_yyyymmdd(item.get("ann_date"))
            title = _text(_pick(item, "title"))
            url = _text(_pick(item, "url"))
            if not title and not url:
                continue
            row = _base_event_row(task, symbol=symbol, dataset="official_filing", period=published_date)
            row.update(
                {
                    "title": title,
                    "published_at": timestamp or published_date,
                    "url": url,
                    "source": "tushare_anns_d",
                    "body_ref": url,
                    "symbol_id": symbol,
                    "quality_flags": ("paid_aggregator_official_filing_url",),
                }
            )
            row["source_roles"] = ("paid_data",)
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=tuple(observations))

    def _fetch_valuation(self, task: Any, *, managed_http: Any, token: str, symbol: str, ctx: Any) -> FetchResult:
        observations: list[Any] = []
        result = _tushare_items(
            task,
            managed_http=managed_http,
            ctx=ctx,
            token=token,
            symbol=symbol,
            api_name="daily_basic",
            fields="ts_code,trade_date,pe,pb,ps,total_mv,circ_mv",
            observations=observations,
        )
        if result.error is not None:
            return FetchResult.from_error(task, status="error", error=result.error, http_observations=tuple(observations))
        rows: list[dict[str, Any]] = []
        for item in result.rows:
            period = _parse_yyyymmdd(item.get("trade_date"))
            if period is None:
                continue
            row = _base_row(task, symbol=symbol, dataset="valuation_metric", period=period.isoformat())
            row["period_start"] = period
            row["period_end"] = period
            _set_decimal(row, "pe", item.get("pe"))
            _set_decimal(row, "pb", item.get("pb"))
            _set_decimal(row, "ps", item.get("ps"))
            _set_decimal(row, "market_cap", item.get("total_mv"))
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=tuple(observations))

    def _fetch_moneyflow(self, task: Any, *, managed_http: Any, token: str, symbol: str, ctx: Any) -> FetchResult:
        observations: list[Any] = []
        result = _tushare_items(
            task,
            managed_http=managed_http,
            ctx=ctx,
            token=token,
            symbol=symbol,
            api_name="moneyflow",
            fields=(
                "ts_code,trade_date,buy_sm_amount,sell_sm_amount,buy_md_amount,sell_md_amount,"
                "buy_lg_amount,sell_lg_amount,buy_elg_amount,sell_elg_amount,net_mf_amount"
            ),
            observations=observations,
        )
        if result.error is not None:
            return FetchResult.from_error(task, status="error", error=result.error, http_observations=tuple(observations))
        rows: list[dict[str, Any]] = []
        for item in result.rows:
            row_date = _parse_yyyymmdd(item.get("trade_date"))
            if row_date is None:
                continue
            row = _base_event_row(task, symbol=symbol, dataset="capital_flow", period=row_date)
            row["granularity"] = "daily"
            row.update(
                {
                    "date": row_date,
                    "main_net": _decimal_or_none(item.get("net_mf_amount")),
                    "small_net": _decimal_delta(item.get("buy_sm_amount"), item.get("sell_sm_amount")),
                    "mid_net": _decimal_delta(item.get("buy_md_amount"), item.get("sell_md_amount")),
                    "large_net": _decimal_delta(item.get("buy_lg_amount"), item.get("sell_lg_amount")),
                    "super_net": _decimal_delta(item.get("buy_elg_amount"), item.get("sell_elg_amount")),
                    "amount_unit": "CNY_10K",
                    "source": "tushare_moneyflow",
                    "symbol_id": symbol,
                }
            )
            row["source_roles"] = ("paid_data",)
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=tuple(observations))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=tuple(observations))

    def _fetch_moneyflow_ths(self, task: Any, *, managed_http: Any, token: str, symbol: str, ctx: Any) -> FetchResult:
        observations: list[Any] = []
        result = _tushare_items(
            task,
            managed_http=managed_http,
            ctx=ctx,
            token=token,
            symbol=symbol,
            api_name="moneyflow_ths",
            fields="ts_code,trade_date,name,net_amount,net_d5_amount,buy_lg_amount,buy_md_amount,buy_sm_amount",
            observations=observations,
        )
        if result.error is not None:
            return FetchResult.from_error(task, status="error", error=result.error, http_observations=tuple(observations))
        rows: list[dict[str, Any]] = []
        for item in result.rows:
            row_date = _parse_yyyymmdd(item.get("trade_date"))
            if row_date is None:
                continue
            row = _base_event_row(task, symbol=symbol, dataset="capital_flow", period=row_date)
            row["granularity"] = "daily"
            row.update(
                {
                    "date": row_date,
                    "main_net": _decimal_or_none(item.get("net_amount")),
                    "large_net": _decimal_or_none(item.get("buy_lg_amount")),
                    "mid_net": _decimal_or_none(item.get("buy_md_amount")),
                    "small_net": _decimal_or_none(item.get("buy_sm_amount")),
                    "net_5d": _decimal_or_none(item.get("net_d5_amount")),
                    "amount_unit": "CNY_10K",
                    "source": "tushare_moneyflow_ths",
                    "symbol_id": symbol,
                }
            )
            row["source_roles"] = ("paid_data",)
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=tuple(observations))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=tuple(observations))

    def _fetch_sector_moneyflow_dc(self, task: Any, *, managed_http: Any, token: str, symbol: str, ctx: Any) -> FetchResult:
        observations: list[Any] = []
        result = _tushare_items(
            task,
            managed_http=managed_http,
            ctx=ctx,
            token=token,
            symbol=symbol,
            api_name="moneyflow_ind_dc",
            fields=(
                "trade_date,content_type,ts_code,name,pct_change,close,net_amount,net_amount_rate,"
                "buy_elg_amount,buy_lg_amount,buy_md_amount,buy_sm_amount,rank"
            ),
            observations=observations,
            include_symbol=False,
            extra_params={"content_type": "行业"},
        )
        return self._sector_moneyflow_result(
            task,
            symbol=symbol,
            source="tushare_moneyflow_ind_dc",
            rows=result.rows,
            observations=observations,
            error=result.error,
            sector_name_key="name",
            unit="CNY",
        )

    def _fetch_sector_moneyflow_ths(self, task: Any, *, managed_http: Any, token: str, symbol: str, ctx: Any) -> FetchResult:
        observations: list[Any] = []
        result = _tushare_items(
            task,
            managed_http=managed_http,
            ctx=ctx,
            token=token,
            symbol=symbol,
            api_name="moneyflow_ind_ths",
            fields="trade_date,ts_code,industry,lead_stock,close,pct_change,company_num,net_buy_amount,net_sell_amount,net_amount",
            observations=observations,
            include_symbol=False,
        )
        return self._sector_moneyflow_result(
            task,
            symbol=symbol,
            source="tushare_moneyflow_ind_ths",
            rows=result.rows,
            observations=observations,
            error=result.error,
            sector_name_key="industry",
            unit="CNY_100M",
        )

    def _fetch_concept_moneyflow_ths(self, task: Any, *, managed_http: Any, token: str, symbol: str, ctx: Any) -> FetchResult:
        observations: list[Any] = []
        result = _tushare_items(
            task,
            managed_http=managed_http,
            ctx=ctx,
            token=token,
            symbol=symbol,
            api_name="moneyflow_cnt_ths",
            fields="trade_date,ts_code,name,lead_stock,close_price,pct_change,industry_index,company_num,net_buy_amount,net_sell_amount,net_amount",
            observations=observations,
            include_symbol=False,
        )
        return self._sector_moneyflow_result(
            task,
            symbol=symbol,
            source="tushare_moneyflow_cnt_ths",
            rows=result.rows,
            observations=observations,
            error=result.error,
            sector_name_key="name",
            unit="CNY_100M",
        )

    def _sector_moneyflow_result(
        self,
        task: Any,
        *,
        symbol: str,
        source: str,
        rows: tuple[Mapping[str, Any], ...],
        observations: list[Any],
        error: Exception | None,
        sector_name_key: str,
        unit: str,
    ) -> FetchResult:
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=tuple(observations))
        normalized: list[dict[str, Any]] = []
        for item in rows:
            row_date = _parse_yyyymmdd(item.get("trade_date"))
            timestamp = _datetime_at_utc_start(row_date) or datetime.now(tz=UTC)
            row = _base_event_row(task, symbol=symbol, dataset="sector_snapshot", period=row_date or timestamp.date())
            row.update(
                {
                    "sector_code": _text(item.get("ts_code")),
                    "sector_name": _text(_pick(item, sector_name_key, "name", "industry")),
                    "main_net": _decimal_or_none(item.get("net_amount")),
                    "main_pct": _decimal_or_none(_pick(item, "net_amount_rate")),
                    "super_net": _decimal_or_none(item.get("buy_elg_amount")),
                    "large_net": _decimal_or_none(item.get("buy_lg_amount")),
                    "mid_net": _decimal_or_none(item.get("buy_md_amount")),
                    "small_net": _decimal_or_none(item.get("buy_sm_amount")),
                    "timestamp": timestamp,
                    "source": source,
                    "amount_unit": unit,
                    "rank": _decimal_or_none(item.get("rank")),
                    "content_type": _text(item.get("content_type")),
                }
            )
            row["source_roles"] = ("paid_data",)
            normalized.append(row)
        if not normalized:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=tuple(observations))
        return FetchResult.from_success(task, payload={"rows": normalized}, row_count=len(normalized), http_observations=tuple(observations))


class AkShareSocialNewsPlugin:
    plugin_id = "cn_a_akshare_social_news"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                _endpoint(
                    endpoint_id="stock_hot_rank_latest_em",
                    data_type="social_signal",
                    source_role="sentiment",
                    granularity=("event",),
                    fields=("source", "timestamp", "metrics", "symbol_id"),
                    priority_rank=30,
                    can_be_formal_fact_source=False,
                ),
                _endpoint(
                    endpoint_id="stock_hot_keyword_em",
                    data_type="social_signal",
                    source_role="sentiment",
                    granularity=("event",),
                    fields=("source", "timestamp", "keyword", "score", "symbol_id"),
                    priority_rank=30,
                    can_be_formal_fact_source=False,
                ),
                _endpoint(
                    endpoint_id="stock_hot_rank_relate_em",
                    data_type="social_signal",
                    source_role="sentiment",
                    granularity=("event",),
                    fields=("source", "timestamp", "related_symbol", "change_pct", "symbol_id"),
                    priority_rank=30,
                    can_be_formal_fact_source=False,
                ),
                _endpoint(
                    endpoint_id="stock_news_em",
                    data_type="company_news",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "summary", "url"),
                    priority_rank=20,
                ),
                _endpoint(
                    endpoint_id="stock_zh_a_hist",
                    data_type="daily_bar",
                    source_role="built_in_public",
                    granularity=("daily",),
                    fields=("date", "open", "high", "low", "close", "volume", "amount", "adjustment"),
                    priority_rank=24,
                ),
                _endpoint(
                    endpoint_id="stock_zh_a_spot_em",
                    data_type="quote_snapshot",
                    source_role="built_in_public",
                    granularity=("realtime",),
                    fields=("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id"),
                    priority_rank=18,
                    batch_by="symbol",
                    max_symbols_per_call=100,
                    mergeable_fields=("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id"),
                ),
                _endpoint(
                    endpoint_id="stock_individual_info_em",
                    data_type="valuation_metric",
                    source_role="built_in_public",
                    granularity=("realtime",),
                    fields=("market_cap", "price", "symbol_id"),
                    priority_rank=24,
                ),
                _endpoint(
                    endpoint_id="stock_individual_fund_flow",
                    data_type="capital_flow",
                    source_role="built_in_public",
                    granularity=("daily",),
                    fields=("date", "main_net", "super_net", "large_net", "mid_net", "small_net", "symbol_id"),
                    priority_rank=11,
                    http_visibility="sdk_internal_unknown",
                ),
                _endpoint(
                    endpoint_id="stock_sector_fund_flow_rank",
                    data_type="sector_snapshot",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("sector_name", "main_net", "super_net", "large_net", "mid_net", "small_net", "timestamp"),
                    priority_rank=11,
                    http_visibility="sdk_internal_unknown",
                ),
            ),
            credential_policy=_NO_CREDENTIALS,
            license_policy=_CN_A_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 30},
            default_priority_rank=30,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        managed_http = _managed_http(ctx)
        if managed_http is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))
        symbol = _first_symbol(task)
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        endpoint_id = str(getattr(task, "endpoint_id", ""))
        if endpoint_id == "stock_hot_rank_latest_em":
            return self._fetch_latest(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "stock_hot_keyword_em":
            return self._fetch_keywords(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "stock_hot_rank_relate_em":
            return self._fetch_related(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "stock_news_em":
            return self._fetch_news(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "stock_zh_a_hist":
            return EastMoneyCNMarketDataPlugin()._fetch_daily_bar(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "stock_zh_a_spot_em":
            return EastMoneyCNMarketDataPlugin()._fetch_spot_quote_batch(task, managed_http=managed_http)
        if endpoint_id == "stock_individual_info_em":
            return EastMoneyCNMarketDataPlugin()._fetch_stock_info(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "stock_individual_fund_flow":
            return self._fetch_individual_fund_flow(task, symbol=symbol)
        if endpoint_id == "stock_sector_fund_flow_rank":
            return self._fetch_sector_fund_flow_rank(task, symbol=symbol)
        return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))

    def _fetch_latest(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        capture = managed_http.send_capture(
            _eastmoney_app_post(
                task,
                path="/stockrank/getCurrentLatest",
                payload={
                    "appId": "appId01",
                    "globalId": "786e4c21-70dc-435a-93bb-38",
                    "marketType": "",
                    "srcSecurityCode": _akshare_em_symbol(symbol),
                },
            )
        )
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        data = capture.json_payload.get("data") if isinstance(capture.json_payload, Mapping) else None
        if not isinstance(data, Mapping):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        timestamp = _parse_datetime(_pick(data, "calcTime", "time", "date"))
        row = _social_row(task, symbol=symbol, endpoint="stock_hot_rank_latest_em", timestamp=timestamp)
        row["metrics"] = {str(key): value for key, value in data.items() if key not in {"srcSecurityCode"}}
        return FetchResult.from_success(task, payload={"rows": [row]}, row_count=1, http_observations=(capture.observation,))

    def _fetch_keywords(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        capture = managed_http.send_capture(
            _eastmoney_app_post(
                task,
                path="/stockrank/getHotStockRankList",
                payload={
                    "appId": "appId01",
                    "globalId": "786e4c21-70dc-435a-93bb-38",
                    "srcSecurityCode": _akshare_em_symbol(symbol),
                },
            )
        )
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows: list[dict[str, Any]] = []
        for item in _eastmoney_data_list(capture.json_payload):
            values = _ordered_values_without_flag(item)
            timestamp = _parse_datetime(_pos(values, 0))
            row = _social_row(task, symbol=symbol, endpoint="stock_hot_keyword_em", timestamp=timestamp)
            row.update(
                {
                    "keyword": _text(_pick(item, "概念名称", "keyword", "name")) or _text(_pos(values, 2)),
                    "keyword_code": _text(_pick(item, "概念代码", "code")) or _text(_pos(values, 3)),
                    "score": _decimal_or_none(_pick(item, "热度", "heat", "score")) or _decimal_or_none(_pos(values, 4)),
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))

    def _fetch_related(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        capture = managed_http.send_capture(
            _eastmoney_app_post(
                task,
                path="/stockrank/getFollowStockRankList",
                payload={
                    "appId": "appId01",
                    "globalId": "786e4c21-70dc-435a-93bb-38",
                    "srcSecurityCode": _akshare_em_symbol(symbol),
                },
            )
        )
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows: list[dict[str, Any]] = []
        for item in _eastmoney_data_list(capture.json_payload):
            values = _ordered_values_without_flag(item)
            timestamp = _parse_datetime(_pos(values, 0))
            row = _social_row(task, symbol=symbol, endpoint="stock_hot_rank_relate_em", timestamp=timestamp)
            row.update(
                {
                    "related_symbol": _text(_pick(item, "相关股票代码", "related_symbol")) or _text(_pos(values, 4)),
                    "change_pct": _decimal_or_none(_pick(item, "涨跌幅", "change_pct")) or _decimal_or_none(str(_pos(values, 5)).strip("%")),
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))

    def _fetch_news(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        code = _stock_code(symbol)
        callback = "jQuery_claw_trade_news"
        inner_param = {
            "uid": "",
            "keyword": code,
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": 10,
                    "preTag": "<em>",
                    "postTag": "</em>",
                }
            },
        }
        capture = managed_http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=_EASTMONEY_SEARCH_ENDPOINT,
                path="/search/jsonp",
                query={"cb": callback, "param": json.dumps(inner_param, ensure_ascii=False, separators=(",", ":"))},
                headers={"accept": "*/*", "referer": f"https://so.eastmoney.com/news/s?keyword={code}", "user-agent": _COMMON_HEADERS["user-agent"]},
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows = _eastmoney_news_rows(task, symbol=symbol, payload=_jsonp_payload(capture.body_text or ""))
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))

    def _fetch_individual_fund_flow(self, task: Any, *, symbol: str) -> FetchResult:
        try:
            import akshare as ak

            source = ak.stock_individual_fund_flow(stock=_stock_code(symbol), market=_akshare_market(symbol))
        except Exception as exc:  # noqa: BLE001
            return FetchResult.from_error(task, status="error", error=exc)
        rows: list[dict[str, Any]] = []
        for item in _records_from_tabular(source):
            row_date = _parse_date(_pick(item, "日期"))
            if row_date is None:
                continue
            row = _base_event_row(task, symbol=symbol, dataset="capital_flow", period=row_date)
            row["granularity"] = "daily"
            row.update(
                {
                    "date": row_date,
                    "close": _decimal_or_none(_pick(item, "收盘价")),
                    "change_pct": _decimal_or_none(_pick(item, "涨跌幅")),
                    "main_net": _decimal_or_none(_pick(item, "主力净流入-净额")),
                    "main_pct": _decimal_or_none(_pick(item, "主力净流入-净占比")),
                    "super_net": _decimal_or_none(_pick(item, "超大单净流入-净额")),
                    "large_net": _decimal_or_none(_pick(item, "大单净流入-净额")),
                    "mid_net": _decimal_or_none(_pick(item, "中单净流入-净额")),
                    "small_net": _decimal_or_none(_pick(item, "小单净流入-净额")),
                    "amount_unit": "CNY",
                    "source": "akshare_stock_individual_fund_flow",
                    "symbol_id": symbol,
                }
            )
            row["source_roles"] = ("built_in_public",)
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))

    def _fetch_sector_fund_flow_rank(self, task: Any, *, symbol: str) -> FetchResult:
        try:
            import akshare as ak

            source = ak.stock_sector_fund_flow_rank(indicator="今日", sector_type="行业资金流")
        except Exception as exc:  # noqa: BLE001
            return FetchResult.from_error(task, status="error", error=exc)
        now = datetime.now(tz=UTC)
        rows: list[dict[str, Any]] = []
        for item in _records_from_tabular(source):
            sector_name = _text(_pick(item, "名称"))
            row = _base_event_row(task, symbol=symbol, dataset="sector_snapshot", period=now.date())
            row.update(
                {
                    "sector_name": sector_name,
                    "main_net": _decimal_or_none(_pick(item, "主力净流入-净额", "今日主力净流入-净额")),
                    "main_pct": _decimal_or_none(_pick(item, "主力净流入-净占比", "今日主力净流入-净占比")),
                    "super_net": _decimal_or_none(_pick(item, "超大单净流入-净额", "今日超大单净流入-净额")),
                    "large_net": _decimal_or_none(_pick(item, "大单净流入-净额", "今日大单净流入-净额")),
                    "mid_net": _decimal_or_none(_pick(item, "中单净流入-净额", "今日中单净流入-净额")),
                    "small_net": _decimal_or_none(_pick(item, "小单净流入-净额", "今日小单净流入-净额")),
                    "timestamp": now,
                    "source": "akshare_stock_sector_fund_flow_rank",
                    "amount_unit": "CNY",
                    "rank": _decimal_or_none(_pick(item, "序号")),
                    "leader": _text(_pick(item, "主力净流入最大股", "今日主力净流入最大股")),
                }
            )
            row["source_roles"] = ("built_in_public",)
            row["quality_flags"] = ("sector_code_not_provided_by_source",)
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))


class AStockSignalSocialPlugin:
    plugin_id = "cn_a_astock_signal_social"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                _endpoint(
                    endpoint_id="ths_hot_reason",
                    data_type="social_signal",
                    source_role="sentiment",
                    granularity=("event",),
                    fields=(
                        "source",
                        "timestamp",
                        "symbol_id",
                        "name",
                        "topic",
                        "reason",
                        "change_pct",
                        "turnover_rate",
                        "amount",
                        "volume",
                        "large_order_net",
                    ),
                    priority_rank=25,
                    can_be_formal_fact_source=False,
                ),
                _endpoint(
                    endpoint_id="baidu_concept_blocks",
                    data_type="social_signal",
                    source_role="sentiment",
                    granularity=("event",),
                    fields=("source", "timestamp", "symbol_id", "topic", "concept", "industry", "region", "change_pct", "description"),
                    priority_rank=26,
                    can_be_formal_fact_source=False,
                ),
            ),
            credential_policy=_NO_CREDENTIALS,
            license_policy=_CN_A_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
            default_priority_rank=25,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        managed_http = _managed_http(ctx)
        if managed_http is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))
        symbol = _first_symbol(task)
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        endpoint_id = str(getattr(task, "endpoint_id", ""))
        if endpoint_id == "ths_hot_reason":
            return self._fetch_ths_hot_reason(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "baidu_concept_blocks":
            return self._fetch_baidu_concept_blocks(task, managed_http=managed_http, symbol=symbol)
        return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))

    def _fetch_ths_hot_reason(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        query_date = _date_text(getattr(task, "date_range_end", None)) or _date_text(datetime.now(tz=UTC))
        capture = managed_http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=_THS_HOT_ENDPOINT,
                path=f"/event/api/getharden/date/{query_date}/orderby/date/orderway/desc/charset/GBK/",
                headers={
                    "accept": "application/json,text/plain,*/*",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
                },
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        payload = capture.json_payload if isinstance(capture.json_payload, Mapping) else {}
        data = payload.get("data")
        if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=(capture.observation,))

        code = _stock_code(symbol)
        timestamp = _parse_datetime(query_date)
        rows: list[dict[str, Any]] = []
        for rank, item in enumerate(data, start=1):
            if not isinstance(item, Mapping) or _stock_code(str(_pick(item, "code", "股票代码") or "")) != code:
                continue
            row = _social_row(task, symbol=symbol, endpoint="ths_hot_reason", timestamp=timestamp)
            reason = _text(_pick(item, "reason", "题材归因"))
            row.update(
                {
                    "name": _text(_pick(item, "name", "股票简称")),
                    "topic": reason,
                    "reason": reason,
                    "rank": rank,
                    "change_pct": _decimal_or_none(_pick(item, "zhangfu", "涨幅")),
                    "turnover_rate": _decimal_or_none(_pick(item, "huanshou", "换手")),
                    "amount": _decimal_or_none(_pick(item, "chengjiaoe", "成交额")),
                    "volume": _decimal_or_none(_pick(item, "chengjiaoliang", "成交量")),
                    "large_order_net": _decimal_or_none(_pick(item, "ddejingliang", "大单净量")),
                    "close": _decimal_or_none(_pick(item, "close", "收盘价")),
                    "quality_flags": ("astock_signal_without_sentiment_label", "topic_reason_signal"),
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=(capture.observation,))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))

    def _fetch_baidu_concept_blocks(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        capture = managed_http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=_BAIDU_FINANCE_ENDPOINT,
                path="/api/getrelatedblock",
                query={"code": _stock_code(symbol), "market": "ab", "typeCode": "all", "finClientType": "pc"},
                headers={
                    "accept": "application/vnd.finance-web.v1+json",
                    "host": "finance.pae.baidu.com",
                    "origin": "https://gushitong.baidu.com",
                    "referer": "https://gushitong.baidu.com/",
                    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
                },
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        payload = capture.json_payload if isinstance(capture.json_payload, Mapping) else {}
        result_code = str(payload.get("ResultCode", payload.get("resultCode", "0"))).strip()
        if result_code != "0":
            message = _text(payload.get("ResultMessage") or payload.get("message")) or f"provider_code:{result_code}"
            return FetchResult.from_error(task, status="error", error=RuntimeError(message), http_observations=(capture.observation,))
        blocks = _baidu_block_groups(payload)
        now = datetime.now(tz=UTC)
        rows: list[dict[str, Any]] = []
        for block in blocks:
            block_type = _text(_pick(block, "type", "name", "title")) or ""
            items = _pick(block, "list", "items", "data")
            if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
                continue
            field_name = _baidu_block_field(block_type)
            for item in items:
                if not isinstance(item, Mapping):
                    continue
                topic = _text(_pick(item, "name", "blockName", "concept"))
                if topic is None:
                    continue
                row = _social_row(task, symbol=symbol, endpoint="baidu_concept_blocks", timestamp=now)
                row.update(
                    {
                        "topic": topic,
                        "block_type": block_type,
                        field_name: topic,
                        "change_pct": _decimal_or_none(_pick(item, "increase", "change_pct", "涨跌幅")),
                        "description": _text(_pick(item, "desc", "description")),
                        "quality_flags": ("astock_signal_without_sentiment_label", "related_block_signal"),
                    }
                )
                rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=(capture.observation,))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))


class EastMoneyCNEventsPlugin:
    plugin_id = "cn_a_eastmoney_events"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                _endpoint(
                    endpoint_id="hot_money_event",
                    data_type="hot_money_event",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("trade_date", "seat", "buy_amount", "sell_amount", "symbol_id"),
                    priority_rank=10,
                ),
                _endpoint(
                    endpoint_id="lockup_event",
                    data_type="lockup_event",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("unlock_date", "shares", "market_value", "holder"),
                    priority_rank=10,
                ),
            ),
            credential_policy=_NO_CREDENTIALS,
            license_policy=_CN_A_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 30},
            default_priority_rank=10,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        managed_http = _managed_http(ctx)
        if managed_http is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))
        symbol = _first_symbol(task)
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        endpoint_id = str(getattr(task, "endpoint_id", ""))
        if endpoint_id == "hot_money_event":
            return self._fetch_hot_money(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "lockup_event":
            return self._fetch_lockup(task, managed_http=managed_http, symbol=symbol)
        return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))

    def _fetch_hot_money(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        request = _eastmoney_datacenter_request(
            task,
            report_name="RPT_DAILYBILLBOARD_DETAILSNEW",
            filter_expr=f'(SECURITY_CODE="{_stock_code(symbol)}")',
            sort_columns="TRADE_DATE,SECURITY_CODE",
            sort_types="-1,1",
        )
        capture = managed_http.send_capture(request)
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows: list[dict[str, Any]] = []
        for item in _eastmoney_items(capture.json_payload):
            if not isinstance(item, Mapping):
                continue
            trade_date = _parse_date(_pick(item, "TRADE_DATE", "trade_date"))
            row = _base_event_row(task, symbol=symbol, dataset="hot_money_event", period=trade_date)
            row.update(
                {
                    "trade_date": trade_date,
                    "seat": _pick(item, "OPERATEDEPT_NAME", "EXPLAIN", "SECURITY_NAME_ABBR"),
                    "buy_amount": _decimal_or_none(_pick(item, "BILLBOARD_BUY_AMT", "SUM_BUY_AMT", "BUY_AMT", "BUY_AMOUNT", "NET_BUY_AMT")),
                    "sell_amount": _decimal_or_none(_pick(item, "BILLBOARD_SELL_AMT", "SUM_SELL_AMT", "SELL_AMT", "SELL_AMOUNT", "NET_SELL_AMT")),
                    "symbol_id": symbol,
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))

    def _fetch_lockup(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        request = _eastmoney_datacenter_request(
            task,
            report_name="RPT_LIFT_STAGE",
            filter_expr=f'(SECURITY_CODE="{_stock_code(symbol)}")',
            sort_columns="FREE_DATE",
            sort_types="-1",
        )
        capture = managed_http.send_capture(request)
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows: list[dict[str, Any]] = []
        for item in _eastmoney_items(capture.json_payload):
            if not isinstance(item, Mapping):
                continue
            unlock_date = _parse_date(_pick(item, "FREE_DATE", "LIFT_DATE", "unlock_date"))
            row = _base_event_row(task, symbol=symbol, dataset="lockup_event", period=unlock_date)
            row.update(
                {
                    "unlock_date": unlock_date,
                    "shares": _decimal_or_none(_pick(item, "FREE_SHARES", "LIFT_NUM", "shares")),
                    "market_value": _decimal_or_none(_pick(item, "LIFT_MARKET_CAP", "ALIFT_MARKET_CAP", "market_value")),
                    "holder": _pick(item, "LIMITED_HOLDER_NAME", "HOLDER_NAME", "SHAREHOLDER_NAME"),
                    "holder_count": _decimal_or_none(_pick(item, "BATCH_HOLDER_NUM", "holder_count")),
                    "share_type": _pick(item, "FREE_SHARES_TYPE"),
                }
            )
            if row["holder"] is None:
                row["quality_flags"] = ("holder_detail_missing",)
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))


class EastMoneyCNMarketDataPlugin:
    plugin_id = "cn_a_eastmoney_market_data"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                _endpoint(
                    endpoint_id="stock_fund_flow_daily",
                    data_type="capital_flow",
                    source_role="built_in_public",
                    granularity=("daily",),
                    fields=("date", "main_net", "small_net", "mid_net", "large_net", "super_net", "symbol_id"),
                    priority_rank=12,
                ),
                _endpoint(
                    endpoint_id="sector_fund_flow_rank",
                    data_type="sector_snapshot",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("sector_code", "sector_name", "main_net", "super_net", "large_net", "mid_net", "small_net", "timestamp"),
                    priority_rank=12,
                ),
                _endpoint(
                    endpoint_id="shareholder_count",
                    data_type="corporate_action",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("event_type", "event_date", "title", "source", "holder_count", "symbol_id"),
                    priority_rank=18,
                ),
                _endpoint(
                    endpoint_id="block_trade",
                    data_type="capital_flow",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("trade_date", "buyer", "seller", "price", "volume", "amount", "symbol_id"),
                    priority_rank=18,
                ),
                _endpoint(
                    endpoint_id="margin_trading_detail",
                    data_type="capital_flow",
                    source_role="built_in_public",
                    granularity=("daily",),
                    fields=("date", "financing_balance", "margin_balance", "security_lending_volume", "symbol_id"),
                    priority_rank=22,
                ),
                _endpoint(
                    endpoint_id="dividend_event",
                    data_type="corporate_action",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("event_type", "event_date", "title", "source", "dividend", "symbol_id"),
                    priority_rank=20,
                ),
                _endpoint(
                    endpoint_id="spot_quote_batch",
                    data_type="quote_snapshot",
                    source_role="built_in_public",
                    granularity=("realtime",),
                    fields=("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id"),
                    priority_rank=12,
                    batch_by="symbol",
                    max_symbols_per_call=100,
                    mergeable_fields=("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id"),
                ),
                _endpoint(
                    endpoint_id="daily_bar",
                    data_type="daily_bar",
                    source_role="built_in_public",
                    granularity=("daily",),
                    fields=("date", "open", "high", "low", "close", "volume", "amount", "adjustment"),
                    priority_rank=22,
                ),
                _endpoint(
                    endpoint_id="stock_info",
                    data_type="valuation_metric",
                    source_role="built_in_public",
                    granularity=("realtime",),
                    fields=("market_cap", "price", "symbol_id"),
                    priority_rank=24,
                ),
                _endpoint(
                    endpoint_id="global_news_7x24",
                    data_type="macro_news",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("title", "published_at", "region", "summary", "url"),
                    priority_rank=18,
                ),
            ),
            credential_policy=_NO_CREDENTIALS,
            license_policy=_CN_A_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 30},
            default_priority_rank=20,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        managed_http = _managed_http(ctx)
        if managed_http is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))
        symbol = _first_symbol(task)
        endpoint_id = str(getattr(task, "endpoint_id", ""))
        if endpoint_id == "sector_fund_flow_rank":
            return self._fetch_sector_fund_flow_rank(task, managed_http=managed_http)
        if endpoint_id == "global_news_7x24":
            return self._fetch_global_news(task, managed_http=managed_http)
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        if endpoint_id == "stock_fund_flow_daily":
            return self._fetch_stock_fund_flow_daily(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "shareholder_count":
            return self._fetch_shareholder_count(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "block_trade":
            return self._fetch_block_trade(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "margin_trading_detail":
            return self._fetch_margin_detail(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "dividend_event":
            return self._fetch_dividend_event(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "spot_quote_batch":
            return self._fetch_spot_quote_batch(task, managed_http=managed_http)
        if endpoint_id == "daily_bar":
            return self._fetch_daily_bar(task, managed_http=managed_http, symbol=symbol)
        if endpoint_id == "stock_info":
            return self._fetch_stock_info(task, managed_http=managed_http, symbol=symbol)
        return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))

    def _fetch_stock_fund_flow_daily(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        capture = managed_http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=_EASTMONEY_PUSH2HIS_ENDPOINT,
                path="/api/qt/stock/fflow/daykline/get",
                query={
                    "lmt": "0",
                    "klt": "101",
                    "secid": _eastmoney_secid(symbol),
                    "fields1": "f1,f2,f3,f7",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
                    "ut": "b2884a393a59ad64002292a3e90d46a5",
                    "_": _eastmoney_timestamp(),
                },
                headers=_COMMON_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows: list[dict[str, Any]] = []
        data = capture.json_payload.get("data") if isinstance(capture.json_payload, Mapping) else None
        klines = data.get("klines") if isinstance(data, Mapping) else None
        if not isinstance(klines, Sequence) or isinstance(klines, (str, bytes, bytearray)):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        for line in klines:
            values = str(line).split(",")
            row_date = _parse_date(_pos(values, 0))
            if row_date is None:
                continue
            row = _base_event_row(task, symbol=symbol, dataset="capital_flow", period=row_date)
            row["granularity"] = "daily"
            row.update(
                {
                    "date": row_date,
                    "main_net": _decimal_or_none(_pos(values, 1)),
                    "small_net": _decimal_or_none(_pos(values, 2)),
                    "mid_net": _decimal_or_none(_pos(values, 3)),
                    "large_net": _decimal_or_none(_pos(values, 4)),
                    "super_net": _decimal_or_none(_pos(values, 5)),
                    "main_pct": _decimal_or_none(_pos(values, 6)),
                    "close": _decimal_or_none(_pos(values, 11)),
                    "change_pct": _decimal_or_none(_pos(values, 12)),
                    "symbol_id": symbol,
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))

    def _fetch_stock_info(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        capture = managed_http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=_EASTMONEY_PUSH2_ENDPOINT,
                path="/api/qt/stock/get",
                query={
                    "fltt": 2,
                    "invt": 2,
                    "secid": _eastmoney_secid(symbol),
                    "fields": "f43,f57,f58,f84,f85,f116,f117,f127,f189",
                    "_": _eastmoney_timestamp(),
                },
                headers=_COMMON_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        data = capture.json_payload.get("data") if isinstance(capture.json_payload, Mapping) else None
        if not isinstance(data, Mapping):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        now = datetime.now(tz=UTC)
        row = _base_row(task, symbol=symbol, dataset="valuation_metric", period=now.date().isoformat())
        row["granularity"] = "realtime"
        row["source_roles"] = ("built_in_public",)
        row["period_start"] = now.date()
        row["period_end"] = now.date()
        _set_decimal(row, "price", _pick(data, "f43"))
        _set_decimal(row, "market_cap", _pick(data, "f116"))
        _set_decimal(row, "float_market_cap", _pick(data, "f117"))
        _set_decimal(row, "total_shares", _pick(data, "f84"))
        _set_decimal(row, "float_shares", _pick(data, "f85"))
        row["industry"] = _text(_pick(data, "f127"))
        row["listed_date"] = _parse_yyyymmdd(_pick(data, "f189"))
        row["timestamp"] = now
        row["symbol_id"] = symbol
        return FetchResult.from_success(task, payload={"rows": [row]}, row_count=1, http_observations=(capture.observation,))

    def _fetch_global_news(self, task: Any, *, managed_http: Any) -> FetchResult:
        capture = managed_http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=_EASTMONEY_NP_WEBLIST_ENDPOINT,
                path="/comm/web/getFastNewsList",
                query={"client": "web", "biz": "web_724", "fastColumn": "102", "sortEnd": "", "pageSize": 50},
                headers={**_COMMON_HEADERS, "referer": "https://kuaixun.eastmoney.com/"},
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows: list[dict[str, Any]] = []
        payload = capture.json_payload
        data = payload.get("data") if isinstance(payload, Mapping) else None
        news_items = data.get("fastNewsList") if isinstance(data, Mapping) else None
        if not isinstance(news_items, Sequence) or isinstance(news_items, (str, bytes, bytearray)):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        for item in news_items:
            if not isinstance(item, Mapping):
                continue
            published_at = _parse_datetime(_pick(item, "showTime", "ctime", "time"))
            row = _base_event_row(task, symbol=_first_symbol(task) or "CN_A", dataset="macro_news", period=published_at.date() if published_at else None)
            row.update(
                {
                    "title": _text(_pick(item, "title")),
                    "published_at": published_at,
                    "region": "CN",
                    "summary": _text(_pick(item, "summary", "digest")),
                    "url": _text(_pick(item, "url", "newsUrl")),
                    "source": "eastmoney_7x24",
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))

    def _fetch_sector_fund_flow_rank(self, task: Any, *, managed_http: Any) -> FetchResult:
        capture = managed_http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=_EASTMONEY_PUSH2_ENDPOINT,
                path="/api/qt/clist/get",
                query={
                    "pn": 1,
                    "pz": 100,
                    "po": 1,
                    "np": 1,
                    "ut": "b2884a393a59ad64002292a3e90d46a5",
                    "fltt": 2,
                    "invt": 2,
                    "fid0": "f62",
                    "fs": "m:90 t:2",
                    "stat": 1,
                    "fields": "f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124",
                    "rt": "52975239",
                    "_": _eastmoney_timestamp(),
                },
                headers=_COMMON_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows: list[dict[str, Any]] = []
        now = datetime.now(tz=UTC)
        for item in _eastmoney_diff_items(capture.json_payload):
            row = _base_event_row(task, symbol=_first_symbol(task) or "CN_A", dataset="sector_snapshot", period=now.date())
            row.update(
                {
                    "sector_code": _text(_pick(item, "f12")),
                    "sector_name": _text(_pick(item, "f14")),
                    "price": _decimal_or_none(_pick(item, "f2")),
                    "change_pct": _decimal_or_none(_pick(item, "f3")),
                    "main_net": _decimal_or_none(_pick(item, "f62")),
                    "main_pct": _decimal_or_none(_pick(item, "f184")),
                    "super_net": _decimal_or_none(_pick(item, "f66")),
                    "large_net": _decimal_or_none(_pick(item, "f72")),
                    "mid_net": _decimal_or_none(_pick(item, "f78")),
                    "small_net": _decimal_or_none(_pick(item, "f84")),
                    "timestamp": now,
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))

    def _fetch_shareholder_count(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        request = _eastmoney_datacenter_request(
            task,
            report_name="RPT_HOLDERNUM_DET",
            filter_expr=f'(SECURITY_CODE="{_stock_code(symbol)}")',
            sort_columns="END_DATE",
            sort_types="-1",
        )
        capture = managed_http.send_capture(request)
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows: list[dict[str, Any]] = []
        for item in _eastmoney_items(capture.json_payload):
            event_date = _parse_date(_pick(item, "END_DATE"))
            row = _base_event_row(task, symbol=symbol, dataset="corporate_action", period=event_date)
            row.update(
                {
                    "event_type": "shareholder_count",
                    "event_date": event_date,
                    "title": "股东户数",
                    "source": "eastmoney",
                    "holder_count": _decimal_or_none(_pick(item, "HOLDER_NUM")),
                    "previous_holder_count": _decimal_or_none(_pick(item, "PRE_HOLDER_NUM")),
                    "holder_count_change": _decimal_or_none(_pick(item, "HOLDER_NUM_CHANGE")),
                    "holder_count_change_pct": _decimal_or_none(_pick(item, "HOLDER_NUM_RATIO")),
                    "symbol_id": symbol,
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))

    def _fetch_block_trade(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        request = _eastmoney_datacenter_request(
            task,
            report_name="RPT_DATA_BLOCKTRADE",
            filter_expr=f'(SECURITY_CODE="{_stock_code(symbol)}")',
            sort_columns="TRADE_DATE",
            sort_types="-1",
        )
        capture = managed_http.send_capture(request)
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows: list[dict[str, Any]] = []
        for item in _eastmoney_items(capture.json_payload):
            trade_date = _parse_date(_pick(item, "TRADE_DATE"))
            row = _base_event_row(task, symbol=symbol, dataset="capital_flow", period=trade_date)
            row.update(
                {
                    "trade_date": trade_date,
                    "buyer": _text(_pick(item, "BUYER_NAME", "BUYER")),
                    "seller": _text(_pick(item, "SELLER_NAME", "SELLER")),
                    "price": _decimal_or_none(_pick(item, "DEAL_PRICE")),
                    "volume": _decimal_or_none(_pick(item, "DEAL_VOLUME", "TRADE_VOLUME")),
                    "amount": _decimal_or_none(_pick(item, "DEAL_AMT", "TRADE_AMOUNT")),
                    "symbol_id": symbol,
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))

    def _fetch_margin_detail(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        request = _eastmoney_datacenter_request(
            task,
            report_name="RPTA_WEB_RZRQ_GGMX",
            filter_expr=f'(scode="{_stock_code(symbol)}")',
            sort_columns="DATE",
            sort_types="-1",
        )
        capture = managed_http.send_capture(request)
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows: list[dict[str, Any]] = []
        for item in _eastmoney_items(capture.json_payload):
            row_date = _parse_date(_pick(item, "DATE"))
            row = _base_event_row(task, symbol=symbol, dataset="capital_flow", period=row_date)
            row["granularity"] = "daily"
            row.update(
                {
                    "date": row_date,
                    "financing_balance": _decimal_or_none(_pick(item, "RZYE")),
                    "margin_balance": _decimal_or_none(_pick(item, "RZRQYE")),
                    "security_lending_volume": _decimal_or_none(_pick(item, "RQYL")),
                    "security_lending_balance": _decimal_or_none(_pick(item, "RQYE")),
                    "financing_buy_amount": _decimal_or_none(_pick(item, "RZMRE")),
                    "financing_repayment_amount": _decimal_or_none(_pick(item, "RZCHE")),
                    "symbol_id": symbol,
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))

    def _fetch_dividend_event(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        request = _eastmoney_datacenter_request(
            task,
            report_name="RPT_SHAREBONUS_DET",
            filter_expr=f'(SECURITY_CODE="{_stock_code(symbol)}")',
            sort_columns="EX_DIVIDEND_DATE",
            sort_types="-1",
        )
        capture = managed_http.send_capture(request)
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows: list[dict[str, Any]] = []
        for item in _eastmoney_items(capture.json_payload):
            event_date = _parse_date(_pick(item, "EX_DIVIDEND_DATE", "NOTICE_DATE"))
            row = _base_event_row(task, symbol=symbol, dataset="corporate_action", period=event_date)
            row.update(
                {
                    "event_type": "dividend",
                    "event_date": event_date,
                    "title": "分红配股",
                    "source": "eastmoney",
                    "dividend": _decimal_or_none(_pick(item, "PRETAX_BONUS_RMB", "BONUS_AMOUNT")),
                    "bonus_ratio": _decimal_or_none(_pick(item, "BONUS_RATIO")),
                    "transfer_ratio": _decimal_or_none(_pick(item, "TRANSFER_RATIO", "IT_RATIO", "BONUS_IT_RATIO")),
                    "symbol_id": symbol,
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))

    def _fetch_spot_quote_batch(self, task: Any, *, managed_http: Any) -> FetchResult:
        symbols = tuple(str(symbol).strip().upper() for symbol in getattr(task, "symbol_ids", ()) or () if str(symbol).strip())
        if not symbols:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        rows: list[dict[str, Any]] = []
        observations: list[Any] = []
        first_error: RuntimeError | None = None
        for symbol in symbols:
            capture = managed_http.send_capture(
                HttpRequestSpec(
                    method="GET",
                    host=_EASTMONEY_PUSH2_ENDPOINT,
                    path="/api/qt/stock/get",
                    query={
                        "fltt": 2,
                        "invt": 2,
                        "secid": _eastmoney_secid(symbol),
                        "fields": "f43,f57,f58,f169,f170,f46,f44,f47,f48,f60,f45,f86",
                        "_": _eastmoney_timestamp(),
                    },
                    headers=_COMMON_HEADERS,
                    provider_config_version=getattr(task, "provider_config_version", None),
                )
            )
            observations.append(capture.observation)
            error = _http_error(capture)
            if error is not None:
                if first_error is None:
                    first_error = error
                continue
            item = capture.json_payload.get("data") if isinstance(capture.json_payload, Mapping) else None
            if not isinstance(item, Mapping):
                continue
            timestamp = _parse_datetime(_pick(item, "f86")) or datetime.now(tz=UTC)
            row = _base_event_row(task, symbol=symbol, dataset="quote_snapshot", period=timestamp.date())
            row["granularity"] = "realtime"
            row.update(
                {
                    "price": _decimal_or_none(_pick(item, "f43")),
                    "change_pct": _decimal_or_none(_pick(item, "f170")),
                    "change": _decimal_or_none(_pick(item, "f169")),
                    "volume": _decimal_or_none(_pick(item, "f47")),
                    "amount": _decimal_or_none(_pick(item, "f48")),
                    "high": _decimal_or_none(_pick(item, "f44")),
                    "low": _decimal_or_none(_pick(item, "f45")),
                    "open": _decimal_or_none(_pick(item, "f46")),
                    "previous_close": _decimal_or_none(_pick(item, "f60")),
                    "timestamp": timestamp,
                    "symbol_id": symbol,
                }
            )
            rows.append(row)
        if not rows:
            if first_error is not None:
                return FetchResult.from_error(task, status="error", error=first_error, http_observations=tuple(observations))
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=tuple(observations))

    def _fetch_daily_bar(self, task: Any, *, managed_http: Any, symbol: str) -> FetchResult:
        capture = managed_http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=_EASTMONEY_PUSH2HIS_ENDPOINT,
                path="/api/qt/stock/kline/get",
                query={
                    "secid": _eastmoney_secid(symbol),
                    "klt": 101,
                    "fqt": 1,
                    "beg": _yyyymmdd(getattr(task, "date_range_start", None)) or "0",
                    "end": _yyyymmdd(getattr(task, "date_range_end", None)) or "20500101",
                    "fields1": "f1,f2,f3,f4,f5,f6",
                    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f116",
                    "ut": "7eea3edcaed734bea9cbfc24409ed989",
                    "_": _eastmoney_timestamp(),
                },
                headers=_COMMON_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        data = capture.json_payload.get("data") if isinstance(capture.json_payload, Mapping) else None
        klines = data.get("klines") if isinstance(data, Mapping) else None
        if not isinstance(klines, Sequence) or isinstance(klines, (str, bytes, bytearray)):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        rows: list[dict[str, Any]] = []
        for line in klines:
            values = str(line).split(",")
            row_date = _parse_date(_pos(values, 0))
            if row_date is None:
                continue
            row = _base_row(task, symbol=symbol, dataset="daily_bar", period=row_date.isoformat())
            row["source_roles"] = ("built_in_public",)
            row["date"] = row_date
            _set_decimal(row, "open", _pos(values, 1))
            _set_decimal(row, "close", _pos(values, 2))
            _set_decimal(row, "high", _pos(values, 3))
            _set_decimal(row, "low", _pos(values, 4))
            _set_decimal(row, "volume", _pos(values, 5))
            _set_decimal(row, "amount", _pos(values, 6))
            row["adjustment"] = "qfq"
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))


class BaostockCNProviderPlugin:
    plugin_id = "cn_a_baostock_market"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                _endpoint(
                    endpoint_id="daily_bar",
                    data_type="daily_bar",
                    source_role="built_in_public",
                    granularity=("daily",),
                    fields=("date", "open", "high", "low", "close", "volume", "amount", "adjustment"),
                    priority_rank=25,
                    http_visibility="no_http",
                ),
                _endpoint(
                    endpoint_id="intraday_bar",
                    data_type="intraday_bar",
                    source_role="built_in_public",
                    granularity=("intraday",),
                    fields=("timestamp", "open", "high", "low", "close", "volume"),
                    priority_rank=32,
                    http_visibility="no_http",
                ),
                _endpoint(
                    endpoint_id="valuation_metric",
                    data_type="valuation_metric",
                    source_role="built_in_public",
                    granularity=("daily",),
                    fields=("pe", "pb", "ps"),
                    priority_rank=28,
                    http_visibility="no_http",
                ),
                _endpoint(
                    endpoint_id="adjust_factor",
                    data_type="corporate_action",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("event_type", "event_date", "title", "source", "adjust_factor", "symbol_id"),
                    priority_rank=24,
                    http_visibility="no_http",
                ),
                _endpoint(
                    endpoint_id="dividend",
                    data_type="corporate_action",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("event_type", "event_date", "title", "source", "dividend", "symbol_id"),
                    priority_rank=26,
                    http_visibility="no_http",
                ),
                _endpoint(
                    endpoint_id="financial_statement",
                    data_type="financial_statement",
                    source_role="built_in_public",
                    granularity=("quarterly",),
                    fields=("period", "revenue", "net_income", "assets", "liabilities", "cash_flow"),
                    priority_rank=30,
                    http_visibility="no_http",
                ),
                _endpoint(
                    endpoint_id="financial_metric",
                    data_type="financial_metric",
                    source_role="built_in_public",
                    granularity=("quarterly",),
                    fields=("roe", "roa", "gross_margin", "debt_ratio", "eps"),
                    priority_rank=30,
                    http_visibility="no_http",
                ),
                _endpoint(
                    endpoint_id="trade_calendar",
                    data_type="event_calendar",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("event_type", "event_date", "title", "source"),
                    priority_rank=32,
                    http_visibility="no_http",
                ),
                _endpoint(
                    endpoint_id="stock_industry",
                    data_type="sector_snapshot",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("sector_name", "timestamp", "symbol_id"),
                    priority_rank=34,
                    http_visibility="no_http",
                ),
            ),
            credential_policy=_NO_CREDENTIALS,
            license_policy=_CN_A_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
            default_priority_rank=30,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        del ctx
        symbol = _first_symbol(task)
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        previous_timeout = socket.getdefaulttimeout()
        socket.setdefaulttimeout(_BAOSTOCK_SOCKET_TIMEOUT_SECONDS)
        try:
            try:
                import baostock as bs
            except ImportError as exc:
                return FetchResult.from_error(task, status="error", error=RuntimeError(f"baostock_not_installed:{exc}"))

            login = bs.login()
            if str(getattr(login, "error_code", "0")) != "0":
                return FetchResult.from_error(task, status="error", error=RuntimeError(str(getattr(login, "error_msg", "baostock_login_failed"))))
            try:
                endpoint_id = str(getattr(task, "endpoint_id", ""))
                if endpoint_id == "daily_bar":
                    return self._fetch_daily_bar(task, bs=bs, symbol=symbol, frequency="d")
                if endpoint_id == "intraday_bar":
                    return self._fetch_daily_bar(task, bs=bs, symbol=symbol, frequency="5")
                if endpoint_id == "valuation_metric":
                    return self._fetch_valuation(task, bs=bs, symbol=symbol)
                if endpoint_id == "adjust_factor":
                    return self._fetch_adjust_factor(task, bs=bs, symbol=symbol)
                if endpoint_id == "dividend":
                    return self._fetch_dividend(task, bs=bs, symbol=symbol)
                if endpoint_id == "financial_statement":
                    return self._fetch_financial_statement(task, bs=bs, symbol=symbol)
                if endpoint_id == "financial_metric":
                    return self._fetch_financial_metric(task, bs=bs, symbol=symbol)
                if endpoint_id == "trade_calendar":
                    return self._fetch_trade_calendar(task, bs=bs, symbol=symbol)
                if endpoint_id == "stock_industry":
                    return self._fetch_stock_industry(task, bs=bs, symbol=symbol)
                return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))
            finally:
                bs.logout()
        finally:
            socket.setdefaulttimeout(previous_timeout)

    def _fetch_daily_bar(self, task: Any, *, bs: Any, symbol: str, frequency: str) -> FetchResult:
        fields = (
            "date,code,open,high,low,close,volume,amount,adjustflag,turn,pctChg,peTTM,pbMRQ,psTTM,pcfNcfTTM,isST"
            if frequency == "d"
            else "date,time,code,open,high,low,close,volume,amount,adjustflag"
        )
        rs = bs.query_history_k_data_plus(
            _baostock_symbol(symbol),
            fields,
            start_date=_date_text(getattr(task, "date_range_start", None)) or "2015-01-01",
            end_date=_date_text(getattr(task, "date_range_end", None)) or _date_text(datetime.now(tz=UTC)),
            frequency=frequency,
            adjustflag="2",
        )
        error = _baostock_error(rs)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error)
        rows: list[dict[str, Any]] = []
        dataset = "intraday_bar" if frequency != "d" else "daily_bar"
        for item in _baostock_result_rows(rs):
            if dataset == "intraday_bar":
                timestamp = _parse_datetime(_pick(item, "time", "date"))
                if timestamp is None:
                    continue
                row = _base_event_row(task, symbol=symbol, dataset=dataset, period=timestamp.date())
                row["granularity"] = "intraday"
                row["timestamp"] = timestamp
            else:
                row_date = _parse_date(_pick(item, "date"))
                if row_date is None:
                    continue
                row = _base_row(task, symbol=symbol, dataset=dataset, period=row_date.isoformat())
                row["date"] = row_date
                row["adjustment"] = "qfq"
            row["source_roles"] = ("built_in_public",)
            _set_decimal(row, "open", item.get("open"))
            _set_decimal(row, "high", item.get("high"))
            _set_decimal(row, "low", item.get("low"))
            _set_decimal(row, "close", item.get("close"))
            _set_decimal(row, "volume", item.get("volume"))
            _set_decimal(row, "amount", item.get("amount"))
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))

    def _fetch_valuation(self, task: Any, *, bs: Any, symbol: str) -> FetchResult:
        rs = bs.query_history_k_data_plus(
            _baostock_symbol(symbol),
            "date,code,peTTM,pbMRQ,psTTM,pcfNcfTTM",
            start_date=_date_text(getattr(task, "date_range_start", None)) or "2015-01-01",
            end_date=_date_text(getattr(task, "date_range_end", None)) or _date_text(datetime.now(tz=UTC)),
            frequency="d",
            adjustflag="3",
        )
        error = _baostock_error(rs)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error)
        rows: list[dict[str, Any]] = []
        for item in _baostock_result_rows(rs):
            row_date = _parse_date(item.get("date"))
            if row_date is None:
                continue
            row = _base_row(task, symbol=symbol, dataset="valuation_metric", period=row_date.isoformat())
            row["source_roles"] = ("built_in_public",)
            _set_decimal(row, "pe", item.get("peTTM"))
            _set_decimal(row, "pb", item.get("pbMRQ"))
            _set_decimal(row, "ps", item.get("psTTM"))
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))

    def _fetch_adjust_factor(self, task: Any, *, bs: Any, symbol: str) -> FetchResult:
        rs = bs.query_adjust_factor(
            code=_baostock_symbol(symbol),
            start_date=_date_text(getattr(task, "date_range_start", None)) or "2015-01-01",
            end_date=_date_text(getattr(task, "date_range_end", None)) or _date_text(datetime.now(tz=UTC)),
        )
        error = _baostock_error(rs)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error)
        rows: list[dict[str, Any]] = []
        for item in _baostock_result_rows(rs):
            event_date = _parse_date(_pick(item, "dividOperateDate", "date"))
            row = _base_event_row(task, symbol=symbol, dataset="corporate_action", period=event_date)
            row.update(
                {
                    "event_type": "adjust_factor",
                    "event_date": event_date,
                    "title": "复权因子",
                    "source": "baostock",
                    "adjust_factor": _decimal_or_none(_pick(item, "backAdjustFactor", "foreAdjustFactor")),
                    "symbol_id": symbol,
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))

    def _fetch_dividend(self, task: Any, *, bs: Any, symbol: str) -> FetchResult:
        year, _quarter = _year_quarter(getattr(task, "date_range_end", None))
        rs = bs.query_dividend_data(code=_baostock_symbol(symbol), year=year, yearType="report")
        error = _baostock_error(rs)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error)
        rows: list[dict[str, Any]] = []
        for item in _baostock_result_rows(rs):
            event_date = _parse_date(_pick(item, "dividOperateDate", "dividRegistDate", "statDate"))
            row = _base_event_row(task, symbol=symbol, dataset="corporate_action", period=event_date)
            row.update(
                {
                    "event_type": "dividend",
                    "event_date": event_date,
                    "title": "分红送配",
                    "source": "baostock",
                    "dividend": _decimal_or_none(_pick(item, "dividCashPsBeforeTax", "dividCashPsAfterTax")),
                    "bonus_ratio": _decimal_or_none(_pick(item, "dividStocksPs")),
                    "transfer_ratio": _decimal_or_none(_pick(item, "dividReserveToStockPs")),
                    "symbol_id": symbol,
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))

    def _fetch_financial_statement(self, task: Any, *, bs: Any, symbol: str) -> FetchResult:
        year, quarter = _year_quarter(getattr(task, "date_range_end", None))
        calls = (
            ("profit", bs.query_profit_data(code=_baostock_symbol(symbol), year=year, quarter=quarter)),
            ("balance", bs.query_balance_data(code=_baostock_symbol(symbol), year=year, quarter=quarter)),
            ("cash_flow", bs.query_cash_flow_data(code=_baostock_symbol(symbol), year=year, quarter=quarter)),
        )
        merged: dict[str, dict[str, Any]] = {}
        for kind, rs in calls:
            error = _baostock_error(rs)
            if error is not None and not merged:
                return FetchResult.from_error(task, status="error", error=error)
            if error is not None:
                continue
            for item in _baostock_result_rows(rs):
                period = _period(_pick(item, "statDate", "pubDate")) or f"{year}Q{quarter}"
                row = merged.setdefault(period, _base_row(task, symbol=symbol, dataset="financial_statement", period=period))
                row["source_roles"] = ("built_in_public",)
                if kind == "profit":
                    _set_decimal(row, "revenue", _pick(item, "MBRevenue", "totalShare", "revenue"))
                    _set_decimal(row, "net_income", _pick(item, "NPParentCompanyOwners", "netProfit", "net_income"))
                elif kind == "balance":
                    _set_decimal(row, "assets", _pick(item, "totalAssets", "asset"))
                    _set_decimal(row, "liabilities", _pick(item, "totalLiability", "liability"))
                else:
                    _set_decimal(row, "cash_flow", _pick(item, "CAToAsset", "NCFOperateA", "cash_flow"))
        rows = tuple(merged.values())
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": list(rows)}, row_count=len(rows))

    def _fetch_trade_calendar(self, task: Any, *, bs: Any, symbol: str) -> FetchResult:
        rs = bs.query_trade_dates(
            start_date=_date_text(getattr(task, "date_range_start", None)) or "2015-01-01",
            end_date=_date_text(getattr(task, "date_range_end", None)) or _date_text(datetime.now(tz=UTC)),
        )
        error = _baostock_error(rs)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error)
        rows: list[dict[str, Any]] = []
        for item in _baostock_result_rows(rs):
            event_date = _parse_date(_pick(item, "calendar_date", "date"))
            if event_date is None:
                continue
            row = _base_event_row(task, symbol=symbol, dataset="event_calendar", period=event_date)
            row.update(
                {
                    "event_type": "trade_date" if str(_pick(item, "is_trading_day")) == "1" else "non_trade_date",
                    "event_date": event_date,
                    "title": "交易日" if str(_pick(item, "is_trading_day")) == "1" else "非交易日",
                    "source": "baostock",
                    "symbol_id": symbol,
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))

    def _fetch_stock_industry(self, task: Any, *, bs: Any, symbol: str) -> FetchResult:
        rs = bs.query_stock_industry(code=_baostock_symbol(symbol))
        error = _baostock_error(rs)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error)
        now = datetime.now(tz=UTC)
        rows: list[dict[str, Any]] = []
        for item in _baostock_result_rows(rs):
            row = _base_event_row(task, symbol=symbol, dataset="sector_snapshot", period=now.date())
            row.update(
                {
                    "sector_code": _text(_pick(item, "industryClassification")),
                    "sector_name": _text(_pick(item, "industry", "industryClassification")),
                    "timestamp": now,
                    "source": "baostock",
                    "symbol_id": symbol,
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))

    def _fetch_financial_metric(self, task: Any, *, bs: Any, symbol: str) -> FetchResult:
        year, quarter = _year_quarter(getattr(task, "date_range_end", None))
        calls = (
            bs.query_profit_data(code=_baostock_symbol(symbol), year=year, quarter=quarter),
            bs.query_dupont_data(code=_baostock_symbol(symbol), year=year, quarter=quarter),
            bs.query_operation_data(code=_baostock_symbol(symbol), year=year, quarter=quarter),
        )
        merged: dict[str, dict[str, Any]] = {}
        for rs in calls:
            error = _baostock_error(rs)
            if error is not None and not merged:
                return FetchResult.from_error(task, status="error", error=error)
            if error is not None:
                continue
            for item in _baostock_result_rows(rs):
                period = _period(_pick(item, "statDate", "pubDate")) or f"{year}Q{quarter}"
                row = merged.setdefault(period, _base_row(task, symbol=symbol, dataset="financial_metric", period=period))
                row["source_roles"] = ("built_in_public",)
                _set_decimal(row, "roe", _pick(item, "roeAvg", "ROE"))
                _set_decimal(row, "roa", _pick(item, "ROA", "roa"))
                _set_decimal(row, "gross_margin", _pick(item, "grossProfitMargin", "GPToAsset"))
                _set_decimal(row, "debt_ratio", _pick(item, "liabilityToAsset", "DebtToAsset"))
                _set_decimal(row, "eps", _pick(item, "epsTTM", "eps"))
        rows = tuple(merged.values())
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": list(rows)}, row_count=len(rows))


class MootdxCNProviderPlugin:
    plugin_id = "cn_a_mootdx_market"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                _endpoint(
                    endpoint_id="quote_snapshot",
                    data_type="quote_snapshot",
                    source_role="built_in_public",
                    granularity=("realtime",),
                    fields=("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id"),
                    priority_rank=15,
                    http_visibility="no_http",
                    batch_by="symbol",
                    max_symbols_per_call=80,
                    mergeable_fields=("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id"),
                ),
                _endpoint(
                    endpoint_id="order_book_snapshot",
                    data_type="order_book_snapshot",
                    source_role="built_in_public",
                    granularity=("realtime",),
                    fields=("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id"),
                    priority_rank=15,
                    http_visibility="no_http",
                    batch_by="symbol",
                    max_symbols_per_call=80,
                    mergeable_fields=("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id"),
                ),
                _endpoint(
                    endpoint_id="intraday_bar",
                    data_type="intraday_bar",
                    source_role="built_in_public",
                    granularity=("intraday",),
                    fields=("timestamp", "open", "high", "low", "close", "volume"),
                    priority_rank=28,
                    http_visibility="no_http",
                ),
                _endpoint(
                    endpoint_id="daily_bar",
                    data_type="daily_bar",
                    source_role="built_in_public",
                    granularity=("daily",),
                    fields=("date", "open", "high", "low", "close", "volume", "amount", "adjustment"),
                    priority_rank=32,
                    http_visibility="no_http",
                ),
                _endpoint(
                    endpoint_id="corporate_action",
                    data_type="corporate_action",
                    source_role="built_in_public",
                    granularity=("event",),
                    fields=("event_type", "event_date", "title", "source", "adjustment", "symbol_id"),
                    priority_rank=36,
                    http_visibility="no_http",
                ),
                _endpoint(
                    endpoint_id="finance_snapshot",
                    data_type="financial_metric",
                    source_role="built_in_public",
                    granularity=("quarterly",),
                    fields=("roe", "eps", "symbol_id"),
                    priority_rank=34,
                    http_visibility="no_http",
                ),
            ),
            credential_policy=_NO_CREDENTIALS,
            license_policy=_CN_A_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
            default_priority_rank=25,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        del ctx
        symbols = tuple(str(symbol).strip().upper() for symbol in getattr(task, "symbol_ids", ()) or () if str(symbol).strip())
        if not symbols:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        try:
            from mootdx.quotes import Quotes
        except ImportError as exc:
            return FetchResult.from_error(task, status="error", error=RuntimeError(f"mootdx_not_installed:{exc}"))
        client = Quotes.factory(market="std", quiet=True, timeout=_MOOTDX_SOCKET_TIMEOUT_SECONDS)
        try:
            endpoint_id = str(getattr(task, "endpoint_id", ""))
            if endpoint_id in {"quote_snapshot", "order_book_snapshot"}:
                return self._fetch_quotes(task, client=client, symbols=symbols, order_book=endpoint_id == "order_book_snapshot")
            if endpoint_id == "intraday_bar":
                return self._fetch_intraday(task, client=client, symbol=symbols[0])
            if endpoint_id == "daily_bar":
                return self._fetch_daily_bar(task, client=client, symbol=symbols[0])
            if endpoint_id == "corporate_action":
                return self._fetch_corporate_action(task, client=client, symbol=symbols[0])
            if endpoint_id == "finance_snapshot":
                return self._fetch_finance_snapshot(task, client=client, symbol=symbols[0])
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))
        finally:
            closer = getattr(client, "close", None)
            if callable(closer):
                closer()

    def _fetch_quotes(self, task: Any, *, client: Any, symbols: tuple[str, ...], order_book: bool) -> FetchResult:
        try:
            rows_source = _records_from_tabular(client.quotes(symbol=[_stock_code(symbol) for symbol in symbols]))
        except Exception as exc:  # noqa: BLE001
            return FetchResult.from_error(task, status="error", error=exc)
        symbols_by_code = {_stock_code(symbol): symbol for symbol in symbols}
        rows: list[dict[str, Any]] = []
        now = datetime.now(tz=UTC)
        for item in rows_source:
            code = _text(_pick(item, "code", "symbol"))
            symbol = symbols_by_code.get(_stock_code(code or ""))
            if symbol is None:
                continue
            dataset = "order_book_snapshot" if order_book else "quote_snapshot"
            row = _base_event_row(task, symbol=symbol, dataset=dataset, period=now.date())
            row["granularity"] = "realtime"
            row["timestamp"] = now
            if order_book:
                row.update(
                    {
                        "bid_price": _decimal_or_none(_pick(item, "bid1", "bid_price1", "buy1")),
                        "bid_size": _decimal_or_none(_pick(item, "bid_vol1", "bid_volume1", "buy_vol1")),
                        "ask_price": _decimal_or_none(_pick(item, "ask1", "ask_price1", "sell1")),
                        "ask_size": _decimal_or_none(_pick(item, "ask_vol1", "ask_volume1", "sell_vol1")),
                        "symbol_id": symbol,
                    }
                )
            else:
                row.update(
                    {
                        "price": _decimal_or_none(_pick(item, "price", "last_close", "close")),
                        "change": _decimal_or_none(_pick(item, "change", "涨跌")),
                        "change_pct": _decimal_or_none(_pick(item, "change_percent", "涨跌幅")),
                        "volume": _decimal_or_none(_pick(item, "vol", "volume")),
                        "amount": _decimal_or_none(_pick(item, "amount")),
                        "symbol_id": symbol,
                    }
                )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))

    def _fetch_intraday(self, task: Any, *, client: Any, symbol: str) -> FetchResult:
        try:
            source = client.minute(symbol=_stock_code(symbol))
        except Exception as exc:  # noqa: BLE001
            return FetchResult.from_error(task, status="error", error=exc)
        rows: list[dict[str, Any]] = []
        for item in _records_from_tabular(source):
            timestamp = _parse_datetime(_pick(item, "datetime", "time"))
            if timestamp is None:
                continue
            row = _base_event_row(task, symbol=symbol, dataset="intraday_bar", period=timestamp.date())
            row["granularity"] = "intraday"
            row["timestamp"] = timestamp
            _set_decimal(row, "open", _pick(item, "open", "price"))
            _set_decimal(row, "high", _pick(item, "high", "price"))
            _set_decimal(row, "low", _pick(item, "low", "price"))
            _set_decimal(row, "close", _pick(item, "close", "price"))
            _set_decimal(row, "volume", _pick(item, "vol", "volume"))
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))

    def _fetch_daily_bar(self, task: Any, *, client: Any, symbol: str) -> FetchResult:
        try:
            source = client.bars(symbol=_stock_code(symbol), frequency=9, offset=800)
        except Exception as exc:  # noqa: BLE001
            return FetchResult.from_error(task, status="error", error=exc)
        rows: list[dict[str, Any]] = []
        for item in _records_from_tabular(source):
            row_date = _parse_date(_pick(item, "datetime", "date"))
            if row_date is None:
                continue
            row = _base_row(task, symbol=symbol, dataset="daily_bar", period=row_date.isoformat())
            row["source_roles"] = ("built_in_public",)
            row["date"] = row_date
            row["adjustment"] = "none"
            _set_decimal(row, "open", item.get("open"))
            _set_decimal(row, "high", item.get("high"))
            _set_decimal(row, "low", item.get("low"))
            _set_decimal(row, "close", item.get("close"))
            _set_decimal(row, "volume", _pick(item, "vol", "volume"))
            _set_decimal(row, "amount", item.get("amount"))
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))

    def _fetch_corporate_action(self, task: Any, *, client: Any, symbol: str) -> FetchResult:
        try:
            source = client.xdxr(symbol=_stock_code(symbol))
        except Exception as exc:  # noqa: BLE001
            return FetchResult.from_error(task, status="error", error=exc)
        rows: list[dict[str, Any]] = []
        for item in _records_from_tabular(source):
            event_date = _parse_date(_pick(item, "date", "datetime"))
            row = _base_event_row(task, symbol=symbol, dataset="corporate_action", period=event_date)
            row.update(
                {
                    "event_type": "xdxr",
                    "event_date": event_date,
                    "title": "除权除息",
                    "source": "mootdx",
                    "adjustment": dict(item),
                    "symbol_id": symbol,
                }
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))

    def _fetch_finance_snapshot(self, task: Any, *, client: Any, symbol: str) -> FetchResult:
        try:
            source = client.finance(symbol=_stock_code(symbol))
        except Exception as exc:  # noqa: BLE001
            return FetchResult.from_error(task, status="error", error=exc)
        now = datetime.now(tz=UTC)
        records = _records_from_tabular(source)
        if not records and isinstance(source, Mapping):
            records = (source,)
        rows: list[dict[str, Any]] = []
        for item in records:
            row = _base_row(task, symbol=symbol, dataset="financial_metric", period=now.date().isoformat())
            row["source_roles"] = ("built_in_public",)
            row["period_start"] = now.date()
            row["period_end"] = now.date()
            _set_decimal(row, "roe", _pick(item, "jingzichanshouyilv", "roe"))
            _set_decimal(row, "eps", _pick(item, "meigushouyi", "eps"))
            _set_decimal(row, "revenue", _pick(item, "zhuyingshouru", "income"))
            _set_decimal(row, "net_income", _pick(item, "jinglirun", "profit"))
            row["symbol_id"] = symbol
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows))


class CNInfoEventsPlugin:
    plugin_id = "cn_a_cninfo_events"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                _endpoint(
                    endpoint_id="event_calendar",
                    data_type="event_calendar",
                    source_role="official",
                    granularity=("event",),
                    fields=("event_type", "event_date", "title", "source"),
                    priority_rank=5,
                ),
                _endpoint(
                    endpoint_id="official_filing",
                    data_type="official_filing",
                    source_role="official",
                    granularity=("event",),
                    fields=("title", "published_at", "url", "source", "body_ref"),
                    priority_rank=5,
                ),
            ),
            credential_policy=_NO_CREDENTIALS,
            license_policy=_CN_A_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
            default_priority_rank=5,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        managed_http = _managed_http(ctx)
        if managed_http is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))
        symbol = _first_symbol(task)
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        capture = managed_http.send_capture(_cninfo_request(task, symbol=symbol))
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        endpoint_id = str(getattr(task, "endpoint_id", ""))
        rows = _cninfo_rows(task, symbol=symbol, payload=capture.json_payload, dataset=endpoint_id)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))


class GoogleNewsDiscoveryPlugin:
    plugin_id = "cn_a_google_news"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                _endpoint(
                    endpoint_id="company_news",
                    data_type="company_news",
                    source_role="discovery",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "summary", "url"),
                    priority_rank=50,
                    can_be_formal_fact_source=False,
                ),
                _endpoint(
                    endpoint_id="macro_news",
                    data_type="macro_news",
                    source_role="discovery",
                    granularity=("event",),
                    fields=("title", "published_at", "region", "summary", "url"),
                    priority_rank=50,
                    can_be_formal_fact_source=False,
                ),
            ),
            credential_policy=_NO_CREDENTIALS,
            license_policy=_CN_A_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
            default_priority_rank=50,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        managed_http = _managed_http(ctx)
        if managed_http is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))
        query = _google_query(task)
        capture = managed_http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=_endpoint_host(ctx, "data_source:google_news", _GOOGLE_NEWS_ENDPOINT),
                path=_endpoint_path(ctx, "data_source:google_news", _GOOGLE_NEWS_ENDPOINT, "/rss/search"),
                query={"q": query, "hl": "zh-CN", "gl": "CN", "ceid": "CN:zh-Hans"},
                headers={"accept": "application/rss+xml"},
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        error = _http_error(capture)
        if error is not None:
            return FetchResult.from_error(task, status="error", error=error, http_observations=(capture.observation,))
        rows = _google_news_rows(task, xml_text=capture.body_text or "")
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=(capture.observation,))


class CNADefaultProviderPlugin:
    plugin_id = "cn_a_primary"
    version = "1.0.0"

    def __init__(self) -> None:
        self._daily_bar = TushareDailyBarPlugin()

    def capabilities(self) -> ProviderCapabilities:
        return self._daily_bar.capabilities()

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return self._daily_bar.build_fetch_tasks(batch)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        return self._daily_bar.fetch(task, ctx)


def build_cn_a_provider_plugins() -> tuple[object, ...]:
    return (
        CNADefaultProviderPlugin(),
        TushareFundamentalPlugin(),
        AkShareSocialNewsPlugin(),
        AStockSignalSocialPlugin(),
        EastMoneyCNEventsPlugin(),
        EastMoneyCNMarketDataPlugin(),
        BaostockCNProviderPlugin(),
        MootdxCNProviderPlugin(),
        CNInfoEventsPlugin(),
        GoogleNewsDiscoveryPlugin(),
    )


def build_cn_a_provider_plugin() -> CNADefaultProviderPlugin:
    return CNADefaultProviderPlugin()


def _endpoint(
    *,
    endpoint_id: str,
    data_type: str,
    source_role: str,
    granularity: tuple[str, ...],
    fields: tuple[str, ...],
    priority_rank: int,
    can_be_formal_fact_source: bool | None = None,
    http_visibility: str = "managed_http",
    batch_by: str = "none",
    max_symbols_per_call: int | None = None,
    max_days_per_call: int | None = None,
    mergeable_fields: tuple[str, ...] = (),
) -> EndpointCapability:
    supports_batch = batch_by != "none"
    return EndpointCapability(
        endpoint_id=endpoint_id,
        market="CN_A",
        data_type=data_type,
        source_role=source_role,
        granularity=granularity,
        fields=fields,
        freshness_supported=("trading_day", "event_time"),
        http_visibility=http_visibility,
        batch_policy=BatchPolicy(
            supports_batch=supports_batch,
            batch_by=batch_by,
            max_symbols_per_call=max_symbols_per_call if supports_batch else None,
            max_days_per_call=max_days_per_call if supports_batch else None,
            mergeable_fields=mergeable_fields if supports_batch else (),
        ),
        priority_rank=priority_rank,
        rate_limit_policy={"window_seconds": 60, "max_calls": 30},
        license_policy=_CN_A_LICENSE,
        can_be_formal_fact_source=can_be_formal_fact_source,
    )


class _TushareRows:
    def __init__(self, rows: tuple[Mapping[str, Any], ...], error: Exception | None = None) -> None:
        self.rows = rows
        self.error = error


def _tushare_items(
    task: Any,
    *,
    managed_http: Any,
    ctx: Any,
    token: str,
    symbol: str,
    api_name: str,
    fields: str,
    observations: list[Any],
    include_symbol: bool = True,
    extra_params: Mapping[str, Any] | None = None,
) -> _TushareRows:
    params: dict[str, Any] = {"ts_code": symbol} if include_symbol else {}
    start = _yyyymmdd(getattr(task, "date_range_start", None))
    end = _yyyymmdd(getattr(task, "date_range_end", None))
    if start:
        params["start_date"] = start
    if end:
        params["end_date"] = end
    if extra_params:
        params.update({key: value for key, value in extra_params.items() if value not in (None, "")})
    request = HttpRequestSpec(
        method="POST",
        host=_endpoint_host(ctx, _TUSHARE_CREDENTIAL, _TUSHARE_ENDPOINT),
        path=_endpoint_path(ctx, _TUSHARE_CREDENTIAL, _TUSHARE_ENDPOINT, ""),
        body=json.dumps(
            {
                "api_name": api_name,
                "token": token,
                "params": params,
                "fields": fields,
            },
            ensure_ascii=True,
            separators=(",", ":"),
        ),
        headers={"accept": "application/json", "content-type": "application/json"},
        provider_config_version=getattr(task, "provider_config_version", None),
    )
    capture = managed_http.send_capture(request)
    observations.append(capture.observation)
    error = _http_error(capture)
    if error is not None:
        return _TushareRows((), error)
    payload = capture.json_payload
    if not isinstance(payload, Mapping):
        return _TushareRows((), RuntimeError("invalid_provider_payload"))
    code = payload.get("code")
    if code not in (0, "0", None):
        return _TushareRows((), RuntimeError(str(payload.get("msg") or f"provider_code:{code}")))
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return _TushareRows(())
    field_names = data.get("fields")
    items = data.get("items")
    if not isinstance(field_names, Sequence) or isinstance(field_names, (str, bytes, bytearray)):
        return _TushareRows((), RuntimeError("invalid_provider_fields"))
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return _TushareRows(())
    rows: list[Mapping[str, Any]] = []
    for item in items:
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            rows.append(dict(zip((str(field) for field in field_names), item, strict=False)))
    return _TushareRows(tuple(rows))


def _tushare_irm_api(symbol: str) -> str:
    exchange = _cn_a_exchange(symbol)
    if exchange == "SSE":
        return "irm_qa_sh"
    if exchange in {"SZSE", "BSE"}:
        return "irm_qa_sz"
    return "irm_qa_sz"


def _base_row(task: Any, *, symbol: str, dataset: str, period: str) -> dict[str, Any]:
    parsed = _period_date(period)
    return {
        "dataset": dataset,
        "market": "CN_A",
        "symbol_id": symbol,
        "granularity": str(getattr(task, "granularity", "")) or "event",
        "period": period,
        "period_start": parsed,
        "period_end": parsed,
        "exchange": _cn_a_exchange(symbol),
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
        "provider_lineage": {"provider_id": getattr(task, "provider_id", None), "endpoint_id": getattr(task, "endpoint_id", None)},
        "source_roles": ("paid_data",),
        "schema_id": f"{dataset}.v1",
        "quality_flags": (),
    }


def _base_event_row(task: Any, *, symbol: str, dataset: str, period: date | None) -> dict[str, Any]:
    return {
        "dataset": dataset,
        "market": "CN_A",
        "symbol_id": symbol,
        "granularity": "event",
        "period_start": period,
        "period_end": period,
        "exchange": _cn_a_exchange(symbol),
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
        "provider_lineage": {"provider_id": getattr(task, "provider_id", None), "endpoint_id": getattr(task, "endpoint_id", None)},
        "source_roles": ("built_in_public",),
        "schema_id": f"{dataset}.v1",
        "quality_flags": (),
    }


def _eastmoney_datacenter_request(
    task: Any,
    *,
    report_name: str,
    filter_expr: str,
    sort_columns: str | None = None,
    sort_types: str | None = None,
) -> HttpRequestSpec:
    query: dict[str, Any] = {
        "reportName": report_name,
        "columns": "ALL",
        "source": "WEB",
        "client": "WEB",
        "pageNumber": 1,
        "pageSize": 20,
        "filter": filter_expr,
    }
    if sort_columns:
        query["sortColumns"] = sort_columns
    if sort_types:
        query["sortTypes"] = sort_types
    return HttpRequestSpec(
        method="GET",
        host=_EASTMONEY_DATACENTER_ENDPOINT,
        path="/api/data/v1/get",
        query=query,
        headers=_COMMON_HEADERS,
        provider_config_version=getattr(task, "provider_config_version", None),
    )


def _eastmoney_app_post(task: Any, *, path: str, payload: Mapping[str, Any]) -> HttpRequestSpec:
    return HttpRequestSpec(
        method="POST",
        host=_EASTMONEY_APPDATA_ENDPOINT,
        path=path,
        body=json.dumps(dict(payload), ensure_ascii=False, separators=(",", ":")),
        headers={"accept": "application/json", "content-type": "application/json", "user-agent": _COMMON_HEADERS["user-agent"]},
        provider_config_version=getattr(task, "provider_config_version", None),
    )


def _akshare_em_symbol(symbol: str) -> str:
    code = _stock_code(symbol)
    exchange = _cn_a_exchange(symbol)
    if exchange == "SSE":
        return f"SH{code}"
    if exchange == "BSE":
        return f"BJ{code}"
    return f"SZ{code}"


def _eastmoney_data_list(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(payload, Mapping):
        return ()
    data = payload.get("data")
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        return tuple(item for item in data if isinstance(item, Mapping))
    return ()


def _ordered_values_without_flag(item: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(value for key, value in item.items() if str(key) != "flag")


def _pos(values: Sequence[Any], index: int) -> Any:
    return values[index] if len(values) > index else None


def _social_row(task: Any, *, symbol: str, endpoint: str, timestamp: datetime | None) -> dict[str, Any]:
    period = timestamp.date() if timestamp else None
    row = _base_event_row(task, symbol=symbol, dataset="social_signal", period=period)
    row.update(
        {
            "source": endpoint,
            "timestamp": timestamp,
            "symbol_id": symbol,
            "source_roles": ("sentiment",),
            "quality_flags": ("aggregate_signal_without_sentiment_label",),
        }
    )
    return row


def _baidu_block_groups(payload: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    result = payload.get("Result")
    if result is None:
        result = payload.get("result") or payload.get("data")
    if isinstance(result, Mapping):
        groups: list[Mapping[str, Any]] = []
        for key, value in result.items():
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                groups.append({"type": str(key), "list": value})
        return tuple(groups)
    if isinstance(result, Sequence) and not isinstance(result, (str, bytes, bytearray)):
        return tuple(item for item in result if isinstance(item, Mapping))
    return ()


def _baidu_block_field(block_type: str) -> str:
    if "行业" in block_type:
        return "industry"
    if "地域" in block_type or "地区" in block_type:
        return "region"
    return "concept"


def _eastmoney_items(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(payload, Mapping):
        return ()
    result = payload.get("result")
    if not isinstance(result, Mapping):
        return ()
    data = result.get("data")
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in data if isinstance(item, Mapping))


def _eastmoney_diff_items(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if not isinstance(payload, Mapping):
        return ()
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return ()
    diff = data.get("diff")
    if not isinstance(diff, Sequence) or isinstance(diff, (str, bytes, bytearray)):
        return ()
    return tuple(item for item in diff if isinstance(item, Mapping))


def _eastmoney_secid(symbol: str) -> str:
    code = _stock_code(symbol)
    exchange = _cn_a_exchange(symbol)
    market = "1" if exchange == "SSE" else "0"
    return f"{market}.{code}"


def _eastmoney_timestamp() -> int:
    return int(datetime.now(tz=UTC).timestamp() * 1000)


def _records_from_tabular(value: Any) -> tuple[Mapping[str, Any], ...]:
    if value is None:
        return ()
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        try:
            records = to_dict("records")
        except TypeError:
            records = to_dict()
        if isinstance(records, Mapping):
            records = records.values()
        if isinstance(records, Sequence) and not isinstance(records, (str, bytes, bytearray)):
            return tuple(item for item in records if isinstance(item, Mapping))
    if isinstance(value, Mapping):
        return (value,)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(item for item in value if isinstance(item, Mapping))
    return ()


def _baostock_symbol(symbol: str) -> str:
    code = _stock_code(symbol)
    exchange = _cn_a_exchange(symbol)
    if exchange == "SSE":
        return f"sh.{code}"
    if exchange == "BSE":
        return f"bj.{code}"
    return f"sz.{code}"


def _akshare_market(symbol: str) -> str:
    exchange = _cn_a_exchange(symbol)
    if exchange == "SSE":
        return "sh"
    if exchange == "BSE":
        return "bj"
    return "sz"


def _baostock_error(result: Any) -> RuntimeError | None:
    error_code = str(getattr(result, "error_code", "0"))
    if error_code and error_code != "0":
        return RuntimeError(str(getattr(result, "error_msg", f"baostock_error:{error_code}")))
    return None


def _baostock_result_rows(result: Any) -> tuple[Mapping[str, Any], ...]:
    fields = tuple(str(field) for field in getattr(result, "fields", ()) or ())
    rows: list[Mapping[str, Any]] = []
    next_row = getattr(result, "next", None)
    get_row_data = getattr(result, "get_row_data", None)
    if callable(next_row) and callable(get_row_data):
        while str(getattr(result, "error_code", "0")) == "0" and next_row():
            values = get_row_data()
            if isinstance(values, Sequence) and not isinstance(values, (str, bytes, bytearray)):
                rows.append(dict(zip(fields, values, strict=False)))
        return tuple(rows)
    getter = getattr(result, "get_data", None)
    if callable(getter):
        return _records_from_tabular(getter())
    return ()


def _year_quarter(value: Any) -> tuple[int, int]:
    parsed = _parse_date(value) or datetime.now(tz=UTC).date()
    quarter = ((parsed.month - 1) // 3) + 1
    return parsed.year, quarter


def _jsonp_payload(text: str) -> Mapping[str, Any]:
    start = text.find("(")
    end = text.rfind(")")
    if start < 0 or end <= start:
        return {}
    try:
        payload = json.loads(text[start + 1 : end])
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, Mapping) else {}


def _eastmoney_news_rows(task: Any, *, symbol: str, payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    result = payload.get("result")
    if not isinstance(result, Mapping):
        return []
    articles = result.get("cmsArticleWebOld")
    if not isinstance(articles, Sequence) or isinstance(articles, (str, bytes, bytearray)):
        return []
    rows: list[dict[str, Any]] = []
    for item in articles:
        if not isinstance(item, Mapping):
            continue
        published_at = _parse_datetime(_pick(item, "date", "发布时间"))
        code = _text(_pick(item, "code"))
        url = _text(_pick(item, "url")) or (f"http://finance.eastmoney.com/a/{code}.html" if code else None)
        row = _base_event_row(task, symbol=symbol, dataset="company_news", period=published_at.date() if published_at else None)
        row.update(
            {
                "title": _strip_html(_text(_pick(item, "title", "新闻标题"))),
                "published_at": published_at,
                "source": _text(_pick(item, "mediaName", "文章来源")) or "eastmoney",
                "summary": _strip_html(_text(_pick(item, "content", "新闻内容"))),
                "url": url,
                "source_roles": ("built_in_public",),
                "schema_id": "company_news.v1",
            }
        )
        rows.append(row)
    return rows


def _cninfo_request(task: Any, *, symbol: str) -> HttpRequestSpec:
    stock = _cninfo_stock_query(symbol)
    start = _date_text(getattr(task, "date_range_start", None))
    end = _date_text(getattr(task, "date_range_end", None))
    body = {
        "pageNum": 1,
        "pageSize": 20,
        "column": stock["column"],
        "tabName": "fulltext",
        "plate": stock["plate"],
        "stock": stock["stock"],
        "searchkey": "",
        "secid": "",
        "category": "",
        "trade": "",
        "seDate": f"{start}~{end}" if start or end else "",
        "sortName": "",
        "sortType": "",
        "isHLtitle": "true",
    }
    return HttpRequestSpec(
        method="POST",
        host=_CNINFO_ENDPOINT,
        path="/new/hisAnnouncement/query",
        body=body,
        headers={**_COMMON_HEADERS, "x-requested-with": "XMLHttpRequest"},
        provider_config_version=getattr(task, "provider_config_version", None),
    )


def _cninfo_rows(task: Any, *, symbol: str, payload: Any, dataset: str) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    announcements = payload.get("announcements")
    if not isinstance(announcements, Sequence) or isinstance(announcements, (str, bytes, bytearray)):
        return []
    rows: list[dict[str, Any]] = []
    for item in announcements:
        if not isinstance(item, Mapping):
            continue
        title = _text(_pick(item, "announcementTitle"))
        published_at = _parse_datetime(_pick(item, "announcementTime"))
        adjunct = _text(_pick(item, "adjunctUrl"))
        url = f"https://static.cninfo.com.cn/{adjunct.lstrip('/')}" if adjunct else None
        if dataset == "event_calendar":
            row = _base_event_row(task, symbol=symbol, dataset="event_calendar", period=published_at.date() if published_at else None)
            row.update(
                {
                    "event_type": _text(_pick(item, "announcementTypeName")) or "announcement",
                    "event_date": published_at.date() if published_at else None,
                    "title": title,
                    "source": "cninfo",
                    "url": url,
                }
            )
        else:
            row = _base_event_row(task, symbol=symbol, dataset="official_filing", period=published_at.date() if published_at else None)
            row.update(
                {
                    "title": title,
                    "published_at": published_at,
                    "url": url,
                    "source": "cninfo",
                    "body_ref": url,
                }
            )
        row["source_roles"] = ("official",)
        rows.append(row)
    return rows


def _google_query(task: Any) -> str:
    endpoint = str(getattr(task, "endpoint_id", ""))
    symbol = _first_symbol(task) or ""
    code = _stock_code(symbol)
    if endpoint == "macro_news":
        return "中国 宏观 政策 金融 市场"
    return " ".join(part for part in (symbol, code, "公司 新闻") if part)


def _google_news_rows(task: Any, *, xml_text: str) -> list[dict[str, Any]]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []
    endpoint = str(getattr(task, "endpoint_id", ""))
    dataset = str(getattr(task, "data_type", endpoint))
    rows: list[dict[str, Any]] = []
    for item in root.findall(".//item")[:20]:
        title = _xml_text(item, "title")
        link = _xml_text(item, "link")
        published_at = _parse_datetime(_xml_text(item, "pubDate"))
        row = {
            "dataset": dataset,
            "market": "CN_A",
            "symbol_id": _first_symbol(task),
            "granularity": "event",
            "period_start": published_at.date() if published_at else None,
            "period_end": published_at.date() if published_at else None,
            "exchange": _cn_a_exchange(_first_symbol(task) or ""),
            "currency": "CNY",
            "timezone": "Asia/Shanghai",
            "calendar": "CN_A_SSE_SZSE",
            "title": title,
            "published_at": published_at,
            "source": "google_news_rss",
            "summary": _xml_text(item, "description"),
            "url": link,
            "provider_lineage": {"provider_id": getattr(task, "provider_id", None), "endpoint_id": endpoint},
            "source_roles": ("discovery",),
            "schema_id": f"{dataset}.v1",
            "quality_flags": ("discovery_not_formal_fact_source",),
        }
        if endpoint == "macro_news":
            row["region"] = "CN"
        rows.append(row)
    return rows


def _http_error(capture: Any) -> RuntimeError | None:
    observation = capture.observation
    if getattr(observation, "error_code", None):
        return RuntimeError(str(observation.error_code))
    status_code = getattr(observation, "status_code", None)
    if status_code is None or int(status_code) >= 400:
        return RuntimeError(f"http_{status_code}")
    payload = getattr(capture, "json_payload", None)
    if isinstance(payload, Mapping) and payload.get("success") is False:
        code = str(payload.get("code") or "").strip()
        message = str(payload.get("message") or "").strip()
        if code == "9201" or message == "返回数据为空":
            return None
        return RuntimeError(f"provider_rejected:{code}:{message}")
    return None


def _managed_http(ctx: Any) -> Any | None:
    managed_http = getattr(ctx, "managed_http", None)
    if managed_http is None or not callable(getattr(managed_http, "send_capture", None)):
        return None
    return managed_http


def _credential_value(ctx: Any, name: str) -> str | None:
    resolver = getattr(ctx, "credential_resolver", None)
    if resolver is not None:
        getter = getattr(resolver, "get_credential", None)
        if callable(getter):
            return _non_empty(getter(name))
    credentials = getattr(ctx, "credentials", None)
    if isinstance(credentials, Mapping):
        return _non_empty(credentials.get(name))
    return None


def _endpoint_host(ctx_or_task: Any, name: str, default: str) -> str:
    endpoint = _configured_endpoint(ctx_or_task, name) or default
    if "://" not in endpoint:
        return endpoint.rstrip("/")
    scheme, rest = endpoint.split("://", 1)
    host, _sep, _path = rest.partition("/")
    return f"{scheme}://{host}"


def _endpoint_path(ctx_or_task: Any, name: str, default: str, suffix: str) -> str:
    endpoint = _configured_endpoint(ctx_or_task, name) or default
    path = ""
    if "://" in endpoint:
        _scheme, rest = endpoint.split("://", 1)
        _host, sep, tail = rest.partition("/")
        path = f"/{tail.rstrip('/')}" if sep else ""
    return f"{path}{suffix}"


def _configured_endpoint(ctx_or_task: Any, name: str) -> str | None:
    resolver = getattr(ctx_or_task, "credential_resolver", None)
    getter = getattr(resolver, "get_endpoint_url", None)
    if callable(getter):
        return _non_empty(getter(name))
    return None


def _first_symbol(task: Any) -> str | None:
    symbols = tuple(getattr(task, "symbol_ids", ()) or ())
    if not symbols:
        return None
    return str(symbols[0]).strip().upper() or None


def _stock_code(symbol: str) -> str:
    token = symbol.strip().upper()
    if "." in token:
        token = token.split(".", 1)[0]
    if token.startswith(("SH", "SZ", "BJ")):
        token = token[2:]
    return token.zfill(6) if token.isdigit() and len(token) < 6 else token


def _cn_a_exchange(symbol: str) -> str | None:
    if symbol.endswith(".SH") or _stock_code(symbol).startswith(("6", "9")):
        return "SSE"
    if symbol.endswith(".SZ") or _stock_code(symbol).startswith(("0", "3")):
        return "SZSE"
    if symbol.endswith(".BJ") or _stock_code(symbol).startswith(("4", "8")):
        return "BSE"
    return None


def _cninfo_stock_query(symbol: str) -> dict[str, str]:
    code = _stock_code(symbol)
    market = symbol.rsplit(".", 1)[1].upper() if "." in symbol else ""
    if market in {"SH", "SS"} or code.startswith(("6", "9")):
        return {"column": "sse", "plate": "sh", "stock": f"{code},gssh0{code}"}
    if market == "SZ" or code.startswith(("0", "3")):
        return {"column": "szse", "plate": "sz", "stock": f"{code},gssz0{code}"}
    if market == "BJ" or code.startswith(("4", "8")):
        return {"column": "bj", "plate": "bj", "stock": code}
    return {"column": "szse", "plate": "", "stock": code}


def _pick(row: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = row.get(key)
        if value not in (None, ""):
            return value
    return None


def _set_decimal(row: dict[str, Any], key: str, value: Any) -> None:
    number = _decimal_or_none(value)
    if number is not None:
        row[key] = number


def _decimal_delta(left: Any, right: Any) -> float | None:
    left_number = _decimal_or_none(left)
    right_number = _decimal_or_none(right)
    if left_number is None or right_number is None:
        return None
    return left_number - right_number


def _decimal_or_none(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip().replace(",", "")
    if not text:
        return None
    try:
        return float(Decimal(text))
    except (InvalidOperation, ValueError):
        return None


def _period(value: Any) -> str | None:
    parsed = _parse_yyyymmdd(value) or _parse_date(value)
    return parsed.isoformat() if parsed else None


def _period_date(value: str) -> date | None:
    return _parse_date(value)


def _parse_yyyymmdd(value: Any) -> date | None:
    text = _non_empty(value)
    if text is None or len(text) < 8 or not text[:8].isdigit():
        return None
    try:
        return date(int(text[:4]), int(text[4:6]), int(text[6:8]))
    except ValueError:
        return None


def _datetime_at_utc_start(value: date | None) -> datetime | None:
    if value is None:
        return None
    return datetime.combine(value, datetime.min.time(), tzinfo=UTC)


def _parse_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = _non_empty(value)
    if text is None:
        return None
    if len(text) >= 8 and text[:8].isdigit():
        return _parse_yyyymmdd(text)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        parsed = _parse_datetime(text)
        return parsed.date() if parsed else None


def _parse_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    text = _non_empty(value)
    if text is None:
        return None
    if text.isdigit():
        if len(text) >= 14 and text[:8].isdigit():
            try:
                return datetime(
                    int(text[:4]),
                    int(text[4:6]),
                    int(text[6:8]),
                    int(text[8:10]),
                    int(text[10:12]),
                    int(text[12:14]),
                    tzinfo=UTC,
                )
            except ValueError:
                pass
        number = int(text)
        if number > 10_000_000_000:
            return datetime.fromtimestamp(number / 1000, tz=UTC)
        if number > 1_000_000_000:
            return datetime.fromtimestamp(number, tz=UTC)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _yyyymmdd(value: Any) -> str | None:
    parsed = _parse_date(value)
    return parsed.strftime("%Y%m%d") if parsed else None


def _date_text(value: Any) -> str:
    parsed = _parse_date(value)
    return parsed.strftime("%Y-%m-%d") if parsed else ""


def _xml_text(parent: ElementTree.Element, tag: str) -> str:
    node = parent.find(tag)
    return node.text.strip() if node is not None and node.text else ""


def _text(value: Any) -> str | None:
    return _non_empty(value)


def _strip_html(value: str | None) -> str | None:
    if value is None:
        return None
    return _non_empty(re.sub(r"<[^>]+>", "", value).replace("\u3000", " ").replace("\r\n", " "))


def _non_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "CNADefaultProviderPlugin",
    "AkShareSocialNewsPlugin",
    "AStockSignalSocialPlugin",
    "BaostockCNProviderPlugin",
    "CNInfoEventsPlugin",
    "EastMoneyCNEventsPlugin",
    "EastMoneyCNMarketDataPlugin",
    "GoogleNewsDiscoveryPlugin",
    "MootdxCNProviderPlugin",
    "TushareFundamentalPlugin",
    "build_cn_a_provider_plugin",
    "build_cn_a_provider_plugins",
]
