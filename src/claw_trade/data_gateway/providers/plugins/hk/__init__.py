from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Sequence
from xml.etree import ElementTree

from claw_trade.data_gateway.execution.managed_http import HttpRequestSpec
from claw_trade.data_gateway.models import FetchResult
from claw_trade.data_gateway.providers.plugins.common import CredentialPolicy, ProviderCapabilities
from claw_trade.data_gateway.providers.plugins.market_http import (
    METADATA_ONLY_LICENSE,
    NO_CREDENTIALS,
    credential_value,
    decimal_float,
    endpoint,
    endpoint_capability,
    first_symbol,
    managed_http,
    non_empty,
    parse_date,
    parse_datetime,
    result_from_capture_error,
    send_json_request,
)

_YAHOO_ENDPOINT = "https://query1.finance.yahoo.com"
_SINA_HK_ENDPOINT = "https://finance.sina.com.cn"
_HKEX_ENDPOINT = "https://www.hkex.com.hk"
_TUSHARE_ENDPOINT = "https://api.tushare.pro"
_GOOGLE_NEWS_ENDPOINT = "https://news.google.com"
_FINNHUB_ENDPOINT = "https://finnhub.io/api/v1"
_EASTMONEY_DATACENTER_ENDPOINT = "https://datacenter.eastmoney.com"
_HEADERS = {"accept": "application/json", "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
_TUSHARE_CREDENTIAL = "data_source:tushare"
_FINNHUB_CREDENTIAL = "data_source:finnhub"


class HKSinaDailyBarPlugin:
    plugin_id = "hk_sina_public"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="stock_hk_daily",
                    market="HK",
                    data_type="daily_bar",
                    source_role="built_in_public",
                    granularity=("daily",),
                    fields=("open", "high", "low", "close", "volume"),
                    priority_rank=25,
                ),
            ),
            credential_policy=NO_CREDENTIALS,
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
            default_priority_rank=25,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        symbol = first_symbol(task)
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        http = managed_http(ctx)
        if http is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))
        host, prefix = endpoint(ctx, "data_source:sina_hk", _SINA_HK_ENDPOINT)
        capture = http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/stock/hkstock/{_hk_sina_symbol(symbol)}/klc2_kl.js",
                headers={"accept": "application/javascript,text/plain,*/*"},
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        observations = (capture.observation,)
        error = result_from_capture_error(task, capture, observations)
        if error is not None:
            return error
        rows = _sina_hk_rows(capture.body_text, symbol=symbol, provider_id=self.plugin_id)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)


class HKYahooFinancePlugin:
    plugin_id = "hk_yahoo_finance"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="chart_daily",
                    market="HK",
                    data_type="daily_bar",
                    source_role="built_in_public",
                    granularity=("daily",),
                    fields=("open", "high", "low", "close", "volume"),
                    priority_rank=30,
                ),
                endpoint_capability(
                    endpoint_id="quote_summary_valuation",
                    market="HK",
                    data_type="valuation_metric",
                    source_role="built_in_public",
                    granularity=("realtime",),
                    fields=("pe", "market_cap"),
                    priority_rank=5,
                ),
                endpoint_capability(
                    endpoint_id="quote_summary_financial",
                    market="HK",
                    data_type="financial_metric",
                    source_role="built_in_public",
                    granularity=("quarterly", "realtime"),
                    fields=("gross_margin", "eps"),
                    priority_rank=5,
                ),
            ),
            credential_policy=NO_CREDENTIALS,
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
            default_priority_rank=5,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        symbol = first_symbol(task)
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        endpoint_id = str(getattr(task, "endpoint_id", ""))
        yahoo_symbol = _hk_yahoo_symbol(symbol)
        if endpoint_id == "chart_daily":
            return self._fetch_chart(task, ctx=ctx, symbol=symbol, yahoo_symbol=yahoo_symbol)
        if endpoint_id in {"quote_summary_valuation", "quote_summary_financial"}:
            return self._fetch_summary(task, ctx=ctx, symbol=symbol, yahoo_symbol=yahoo_symbol, endpoint_id=endpoint_id)
        return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))

    def _fetch_chart(self, task: Any, *, ctx: Any, symbol: str, yahoo_symbol: str) -> FetchResult:
        host, prefix = endpoint(ctx, "data_source:yahoo_finance", _YAHOO_ENDPOINT)
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/v8/finance/chart/{yahoo_symbol}",
                query={"range": "1y", "interval": "1d", "events": "history", "includeAdjustedClose": "true"},
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        rows = _yahoo_chart_rows(payload, symbol=symbol, provider_id=self.plugin_id, endpoint_id="chart_daily")
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)

    def _fetch_summary(self, task: Any, *, ctx: Any, symbol: str, yahoo_symbol: str, endpoint_id: str) -> FetchResult:
        host, prefix = endpoint(ctx, "data_source:yahoo_finance", _YAHOO_ENDPOINT)
        types = "trailingPeRatio,trailingMarketCap" if endpoint_id == "quote_summary_valuation" else "quarterlyBasicEPS,quarterlyGrossMargin"
        now = datetime.now(tz=UTC)
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/ws/fundamentals-timeseries/v1/finance/timeseries/{yahoo_symbol}",
                query={"type": types, "period1": int((now - timedelta(days=365 * 3)).timestamp()), "period2": int(now.timestamp())},
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        values = _yahoo_timeseries_values(payload)
        row = _base_row(
            dataset=str(getattr(task, "data_type", "valuation_metric")),
            symbol=symbol,
            provider_id=self.plugin_id,
            endpoint_id=endpoint_id,
            source_role="built_in_public",
            period=values.pop("_period", None),
        )
        row.update(values)
        if not any(key in row for key in getattr(task, "fields", ()) or ()):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": [row]}, row_count=1, http_observations=observations)


class HKFinnhubDataPlugin:
    plugin_id = "hk_finnhub_data"
    version = "1.0.0"
    credential_name = _FINNHUB_CREDENTIAL

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="quote",
                    market="HK",
                    data_type="quote_snapshot",
                    source_role="paid_data",
                    granularity=("realtime",),
                    fields=("price", "change", "change_pct", "timestamp", "symbol_id"),
                    priority_rank=8,
                ),
                endpoint_capability(
                    endpoint_id="stock_candle_daily",
                    market="HK",
                    data_type="daily_bar",
                    source_role="paid_data",
                    granularity=("daily",),
                    fields=("date", "open", "high", "low", "close", "volume"),
                    priority_rank=8,
                ),
                endpoint_capability(
                    endpoint_id="stock_metric_valuation",
                    market="HK",
                    data_type="valuation_metric",
                    source_role="paid_data",
                    granularity=("realtime",),
                    fields=("pe", "pb", "ps", "market_cap"),
                    priority_rank=8,
                ),
                endpoint_capability(
                    endpoint_id="stock_metric_financial",
                    market="HK",
                    data_type="financial_metric",
                    source_role="paid_data",
                    granularity=("quarterly", "realtime"),
                    fields=("roe", "roa", "profit_margin", "eps"),
                    priority_rank=8,
                ),
                endpoint_capability(
                    endpoint_id="company_news",
                    market="HK",
                    data_type="company_news",
                    source_role="paid_data",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "summary", "url", "symbol_id"),
                    priority_rank=8,
                ),
            ),
            credential_policy=CredentialPolicy(
                credential_required=True,
                credential_names=(self.credential_name,),
                credential_scope="provider_token",
                missing_behavior="credential_missing",
            ),
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": None},
            default_priority_rank=8,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        token = credential_value(ctx, self.credential_name)
        if token is None:
            return FetchResult.from_error(task, status="credential_missing", error=RuntimeError(f"credential_missing:{self.credential_name}"))
        symbol = first_symbol(task)
        endpoint_id = str(getattr(task, "endpoint_id", ""))
        if endpoint_id in {"quote", "stock_candle_daily", "stock_metric_valuation", "stock_metric_financial", "company_news"} and symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        finnhub_symbol = _hk_finnhub_symbol(str(symbol))
        if endpoint_id == "quote":
            return self._fetch_quote(task, ctx=ctx, token=token, symbol=str(symbol), finnhub_symbol=finnhub_symbol)
        if endpoint_id == "stock_candle_daily":
            return self._fetch_candles(task, ctx=ctx, token=token, symbol=str(symbol), finnhub_symbol=finnhub_symbol)
        if endpoint_id in {"stock_metric_valuation", "stock_metric_financial"}:
            return self._fetch_metric(task, ctx=ctx, token=token, symbol=str(symbol), finnhub_symbol=finnhub_symbol, endpoint_id=endpoint_id)
        if endpoint_id == "company_news":
            return self._fetch_company_news(task, ctx=ctx, token=token, symbol=str(symbol), finnhub_symbol=finnhub_symbol)
        return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))

    def _fetch_quote(self, task: Any, *, ctx: Any, token: str, symbol: str, finnhub_symbol: str) -> FetchResult:
        payload, observations, error = self._finnhub_request(task, ctx=ctx, token=token, path="/quote", query={"symbol": finnhub_symbol})
        if error is not None:
            return error
        if not isinstance(payload, Mapping) or _finnhub_payload_has_error(payload):
            return FetchResult.from_error(task, status="error", error=RuntimeError("invalid_provider_payload"), http_observations=observations)
        price = decimal_float(payload.get("c"))
        if price is None:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        row = _base_row(dataset="quote_snapshot", symbol=symbol, provider_id=self.plugin_id, endpoint_id="quote", source_role="paid_data")
        row.update(
            {
                "price": price,
                "change": decimal_float(payload.get("d")),
                "change_pct": decimal_float(payload.get("dp")),
                "open": decimal_float(payload.get("o")),
                "high": decimal_float(payload.get("h")),
                "low": decimal_float(payload.get("l")),
                "previous_close": decimal_float(payload.get("pc")),
                "timestamp": parse_datetime(payload.get("t")),
            }
        )
        return FetchResult.from_success(task, payload={"rows": [{key: value for key, value in row.items() if value is not None}]}, row_count=1, http_observations=observations)

    def _fetch_candles(self, task: Any, *, ctx: Any, token: str, symbol: str, finnhub_symbol: str) -> FetchResult:
        start, end = _date_window(task, default_days=365)
        payload, observations, error = self._finnhub_request(
            task,
            ctx=ctx,
            token=token,
            path="/stock/candle",
            query={
                "symbol": finnhub_symbol,
                "resolution": "D",
                "from": _unix_day_start(start),
                "to": _unix_day_start(end) + 86_399,
            },
        )
        if error is not None:
            return error
        rows = _finnhub_candle_rows(payload, symbol=symbol, provider_id=self.plugin_id)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)

    def _fetch_metric(self, task: Any, *, ctx: Any, token: str, symbol: str, finnhub_symbol: str, endpoint_id: str) -> FetchResult:
        payload, observations, error = self._finnhub_request(task, ctx=ctx, token=token, path="/stock/metric", query={"symbol": finnhub_symbol, "metric": "all"})
        if error is not None:
            return error
        metric = payload.get("metric") if isinstance(payload, Mapping) else None
        if not isinstance(metric, Mapping):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        dataset = "valuation_metric" if endpoint_id == "stock_metric_valuation" else "financial_metric"
        row = _base_row(dataset=dataset, symbol=symbol, provider_id=self.plugin_id, endpoint_id=endpoint_id, source_role="paid_data")
        if endpoint_id == "stock_metric_valuation":
            row.update(
                {
                    "pe": _first_metric(metric, "peBasicExclExtraTTM", "peTTM", "peNormalizedAnnual"),
                    "pb": _first_metric(metric, "pbAnnual", "pbQuarterly"),
                    "ps": _first_metric(metric, "psAnnual", "psTTM"),
                    "market_cap": _first_metric(metric, "marketCapitalization"),
                }
            )
        else:
            row.update(
                {
                    "roe": _first_metric(metric, "roeTTM", "roeRfy", "roeAnnual"),
                    "roa": _first_metric(metric, "roaTTM", "roaRfy", "roaAnnual"),
                    "profit_margin": _first_metric(metric, "netProfitMarginTTM", "netMarginTTM"),
                    "eps": _first_metric(metric, "epsInclExtraItemsTTM", "epsBasicExclExtraItemsTTM", "epsAnnual"),
                }
            )
        row = {key: value for key, value in row.items() if value is not None}
        if not any(key in row for key in getattr(task, "fields", ()) or ()):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        return FetchResult.from_success(task, payload={"rows": [row]}, row_count=1, http_observations=observations)

    def _fetch_company_news(self, task: Any, *, ctx: Any, token: str, symbol: str, finnhub_symbol: str) -> FetchResult:
        start, end = _date_window(task, default_days=30)
        payload, observations, error = self._finnhub_request(
            task,
            ctx=ctx,
            token=token,
            path="/company-news",
            query={"symbol": finnhub_symbol, "from": start.isoformat(), "to": end.isoformat()},
        )
        if error is not None:
            return error
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, bytearray)):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        rows: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            title = non_empty(item.get("headline"))
            url = non_empty(item.get("url"))
            if title is None or url is None:
                continue
            published = parse_datetime(item.get("datetime"))
            period = published.date() if published else end
            row = _base_row(dataset="company_news", symbol=symbol, provider_id=self.plugin_id, endpoint_id="company_news", source_role="paid_data", period=period)
            row.update(
                {
                    "title": title,
                    "published_at": published,
                    "source": non_empty(item.get("source")) or "Finnhub",
                    "summary": non_empty(item.get("summary")),
                    "url": url,
                }
            )
            rows.append({key: value for key, value in row.items() if value is not None})
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)

    def _finnhub_request(
        self,
        task: Any,
        *,
        ctx: Any,
        token: str,
        path: str,
        query: Mapping[str, Any],
    ) -> tuple[Any, tuple[Any, ...], FetchResult | None]:
        host, prefix = endpoint(ctx, self.credential_name, _FINNHUB_ENDPOINT)
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}{path}",
                query={**dict(query), "token": token},
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return payload, observations, error
        if isinstance(payload, Mapping) and _finnhub_payload_has_error(payload):
            return payload, observations, FetchResult.from_error(task, status="error", error=RuntimeError("provider_error:finnhub"), http_observations=observations)
        return payload, observations, None


class HKTushareProviderPlugin:
    plugin_id = "hk_tushare"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="hk_daily",
                    market="HK",
                    data_type="daily_bar",
                    source_role="paid_data",
                    granularity=("daily",),
                    fields=("date", "open", "high", "low", "close", "volume", "amount"),
                    priority_rank=10,
                ),
                endpoint_capability(
                    endpoint_id="hk_daily_adj",
                    market="HK",
                    data_type="daily_bar",
                    source_role="paid_data",
                    granularity=("daily",),
                    fields=("date", "open", "high", "low", "close", "volume", "amount", "adjustment"),
                    priority_rank=10,
                ),
                endpoint_capability(
                    endpoint_id="hk_daily_adj_valuation",
                    market="HK",
                    data_type="valuation_metric",
                    source_role="paid_data",
                    granularity=("daily",),
                    fields=("price", "market_cap", "symbol_id"),
                    priority_rank=10,
                ),
                endpoint_capability(
                    endpoint_id="hk_fina_indicator",
                    market="HK",
                    data_type="financial_metric",
                    source_role="paid_data",
                    granularity=("quarterly",),
                    fields=("roe", "eps", "gross_profit"),
                    priority_rank=10,
                ),
            ),
            credential_policy=CredentialPolicy(
                credential_required=True,
                credential_names=(_TUSHARE_CREDENTIAL,),
                credential_scope="provider_token",
                missing_behavior="credential_missing",
            ),
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": None},
            default_priority_rank=10,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        token = credential_value(ctx, _TUSHARE_CREDENTIAL)
        if token is None:
            return FetchResult.from_error(task, status="credential_missing", error=RuntimeError(f"credential_missing:{_TUSHARE_CREDENTIAL}"))
        symbol = first_symbol(task)
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        endpoint_id = str(getattr(task, "endpoint_id", ""))
        if endpoint_id in {"hk_daily", "hk_daily_adj", "hk_daily_adj_valuation"}:
            return self._fetch_daily(task, ctx=ctx, token=token, symbol=symbol, endpoint_id=endpoint_id)
        if endpoint_id != "hk_fina_indicator":
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))
        host, path = endpoint(ctx, _TUSHARE_CREDENTIAL, _TUSHARE_ENDPOINT)
        body = {
            "api_name": "hk_fina_indicator",
            "token": token,
            "params": {"ts_code": symbol},
            "fields": "ts_code,end_date,roe_avg,basic_eps,gross_profit",
        }
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="POST",
                host=host,
                path=path,
                body=json.dumps(body, ensure_ascii=True, separators=(",", ":")),
                headers={"accept": "application/json", "content-type": "application/json"},
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        rows = _tushare_hk_metric_rows(payload, symbol=symbol, provider_id=self.plugin_id)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)

    def _fetch_daily(self, task: Any, *, ctx: Any, token: str, symbol: str, endpoint_id: str) -> FetchResult:
        api_name = "hk_daily_adj" if endpoint_id in {"hk_daily_adj", "hk_daily_adj_valuation"} else "hk_daily"
        host, path = endpoint(ctx, _TUSHARE_CREDENTIAL, _TUSHARE_ENDPOINT)
        params: dict[str, Any] = {"ts_code": symbol}
        start_date = _tushare_yyyymmdd(getattr(task, "date_range_start", None))
        end_date = _tushare_yyyymmdd(getattr(task, "date_range_end", None))
        if start_date:
            params["start_date"] = start_date
        if end_date:
            params["end_date"] = end_date
        if api_name == "hk_daily_adj":
            fields = "ts_code,trade_date,close,open,high,low,pre_close,pct_change,vol,amount,adj_factor,total_mv,free_mv"
        else:
            fields = "ts_code,trade_date,open,high,low,close,pre_close,change,pct_chg,vol,amount"
        body = {
            "api_name": api_name,
            "token": token,
            "params": params,
            "fields": fields,
        }
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="POST",
                host=host,
                path=path,
                body=json.dumps(body, ensure_ascii=True, separators=(",", ":")),
                headers={"accept": "application/json", "content-type": "application/json"},
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        rows = _tushare_hk_daily_rows(payload, symbol=symbol, provider_id=self.plugin_id, endpoint_id=endpoint_id)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)


HKTushareFundamentalPlugin = HKTushareProviderPlugin


class HKAkShareFundamentalPlugin:
    plugin_id = "hk_akshare_fundamental"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="stock_financial_hk_analysis_indicator_em",
                    market="HK",
                    data_type="financial_metric",
                    source_role="built_in_public",
                    granularity=("quarterly",),
                    fields=("roe", "gross_margin", "eps"),
                    priority_rank=20,
                ),
                endpoint_capability(
                    endpoint_id="stock_financial_hk_report_em_income",
                    market="HK",
                    data_type="financial_statement",
                    source_role="built_in_public",
                    granularity=("quarterly", "annual"),
                    fields=("period", "revenue", "net_income"),
                    priority_rank=20,
                ),
            ),
            credential_policy=NO_CREDENTIALS,
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
            default_priority_rank=20,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        symbol = first_symbol(task)
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        endpoint_id = str(getattr(task, "endpoint_id", ""))
        if endpoint_id == "stock_financial_hk_analysis_indicator_em":
            return self._fetch_analysis_indicator(task, ctx=ctx, symbol=symbol)
        if endpoint_id == "stock_financial_hk_report_em_income":
            return self._fetch_income_statement(task, ctx=ctx, symbol=symbol)
        return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))

    def _fetch_analysis_indicator(self, task: Any, *, ctx: Any, symbol: str) -> FetchResult:
        code = _hk_plain_code(symbol)
        host, prefix = endpoint(ctx, "data_source:eastmoney_hk", _EASTMONEY_DATACENTER_ENDPOINT)
        query = {
            "reportName": "RPT_HKF10_FN_MAININDICATOR",
            "columns": "HKF10_FN_MAININDICATOR",
            "quoteColumns": "",
            "pageNumber": "1",
            "pageSize": "9",
            "sortTypes": "-1",
            "sortColumns": "STD_REPORT_DATE",
            "source": "F10",
            "client": "PC",
            "v": "01975982096513973",
            "filter": f'(SECUCODE="{code}.HK")',
        }
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/securities/api/data/v1/get",
                query=query,
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        rows = _eastmoney_hk_metric_rows(payload, symbol=symbol, provider_id=self.plugin_id)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)

    def _fetch_income_statement(self, task: Any, *, ctx: Any, symbol: str) -> FetchResult:
        code = _hk_plain_code(symbol)
        host, prefix = endpoint(ctx, "data_source:eastmoney_hk", _EASTMONEY_DATACENTER_ENDPOINT)
        report_list_payload, report_list_observations, report_list_error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/securities/api/data/v1/get",
                query={
                    "reportName": "RPT_CUSTOM_HKSK_APPFN_CASHFLOW_SUMMARY",
                    "columns": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,START_DATE,REPORT_DATE,FISCAL_YEAR,CURRENCY,ACCOUNT_STANDARD,REPORT_TYPE",
                    "quoteColumns": "",
                    "filter": f'(SECUCODE="{code}.HK")',
                    "source": "F10",
                    "client": "PC",
                    "v": "02092616586970355",
                },
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if report_list_error is not None:
            return report_list_error
        report_dates = _eastmoney_hk_report_dates(
            report_list_payload,
            annual_only=str(getattr(task, "granularity", "")).lower() == "annual",
        )
        if not report_dates:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=report_list_observations)

        quoted_dates = "'" + "','".join(report_dates[:9]) + "'"
        income_payload, income_observations, income_error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/securities/api/data/v1/get",
                query={
                    "reportName": "RPT_HKF10_FN_INCOME_PC",
                    "columns": "SECUCODE,SECURITY_CODE,SECURITY_NAME_ABBR,ORG_CODE,REPORT_DATE,DATE_TYPE_CODE,FISCAL_YEAR,START_DATE,STD_ITEM_CODE,STD_ITEM_NAME,AMOUNT",
                    "quoteColumns": "",
                    "filter": f'(SECUCODE="{code}.HK")(REPORT_DATE in ({quoted_dates}))',
                    "pageNumber": "1",
                    "pageSize": "",
                    "sortTypes": "-1,1",
                    "sortColumns": "REPORT_DATE,STD_ITEM_CODE",
                    "source": "F10",
                    "client": "PC",
                    "v": "01975982096513973",
                },
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        observations = (*report_list_observations, *income_observations)
        if income_error is not None:
            return income_error
        rows = _eastmoney_hk_income_rows(income_payload, symbol=symbol, provider_id=self.plugin_id)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)


class HKEXNewsProviderPlugin:
    plugin_id = "hk_hkexnews_official"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="regulatory_announcements",
                    market="HK",
                    data_type="official_filing",
                    source_role="official",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "url", "symbol_id"),
                    priority_rank=0,
                ),
                endpoint_capability(
                    endpoint_id="regulatory_calendar",
                    market="HK",
                    data_type="event_calendar",
                    source_role="official",
                    granularity=("event",),
                    fields=("event_date", "event_type", "title", "source", "url", "symbol_id"),
                    priority_rank=0,
                ),
            ),
            credential_policy=NO_CREDENTIALS,
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
            default_priority_rank=0,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        symbol = first_symbol(task) or "HK"
        http = managed_http(ctx)
        if http is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))
        host, prefix = endpoint(ctx, "data_source:hkexnews", _HKEX_ENDPOINT)
        capture = http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/Services/RSS-Feeds/regulatory-announcements",
                query={"sc_lang": "en"},
                headers={"accept": "application/rss+xml,application/xml,text/xml"},
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        observations = (capture.observation,)
        error = result_from_capture_error(task, capture, observations)
        if error is not None:
            return error
        rows = _rss_rows(
            capture.body_text,
            dataset=str(getattr(task, "data_type", "official_filing")),
            symbol=symbol,
            provider_id=self.plugin_id,
            endpoint_id=str(getattr(task, "endpoint_id", "regulatory_announcements")),
            source_role="official",
            source="HKEXnews",
            require_symbol_match=symbol != "HK",
        )
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)


class HKGoogleNewsDiscoveryPlugin:
    plugin_id = "hk_google_news"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="company_news",
                    market="HK",
                    data_type="company_news",
                    source_role="discovery",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "summary", "url"),
                    priority_rank=50,
                    can_be_formal_fact_source=False,
                ),
                endpoint_capability(
                    endpoint_id="macro_news",
                    market="HK",
                    data_type="macro_news",
                    source_role="discovery",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "summary", "url", "region"),
                    priority_rank=50,
                    can_be_formal_fact_source=False,
                ),
                endpoint_capability(
                    endpoint_id="social_signal_news_heat",
                    market="HK",
                    data_type="social_signal",
                    source_role="discovery",
                    granularity=("event",),
                    fields=("source", "timestamp", "title", "url", "symbol_id"),
                    priority_rank=50,
                    can_be_formal_fact_source=False,
                ),
            ),
            credential_policy=NO_CREDENTIALS,
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
            default_priority_rank=50,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        symbol = first_symbol(task) or "HK"
        endpoint_id = str(getattr(task, "endpoint_id", ""))
        if endpoint_id == "company_news":
            query = f"{symbol} Hong Kong stock news"
        elif endpoint_id == "social_signal_news_heat":
            query = f"{symbol} Hong Kong stock investor discussion"
        else:
            query = "Hong Kong market economy news"
        http = managed_http(ctx)
        if http is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))
        host, prefix = endpoint(ctx, "data_source:google_news", _GOOGLE_NEWS_ENDPOINT)
        capture = http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/rss/search",
                query={"q": query, "hl": "en-HK", "gl": "HK", "ceid": "HK:en"},
                headers={"accept": "application/rss+xml,application/xml,text/xml"},
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        observations = (capture.observation,)
        error = result_from_capture_error(task, capture, observations)
        if error is not None:
            return error
        rows = _rss_rows(
            capture.body_text,
            dataset=str(getattr(task, "data_type", "company_news")),
            symbol=symbol,
            provider_id=self.plugin_id,
            endpoint_id=endpoint_id or "company_news",
            source_role="discovery",
            source="Google News",
            region="HK",
        )
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)


def build_hk_provider_plugin() -> HKFinnhubDataPlugin:
    return HKFinnhubDataPlugin()


def build_hk_provider_plugins() -> tuple[object, ...]:
    return (
        HKEXNewsProviderPlugin(),
        HKFinnhubDataPlugin(),
        HKYahooFinancePlugin(),
        HKTushareProviderPlugin(),
        HKAkShareFundamentalPlugin(),
        HKSinaDailyBarPlugin(),
        HKGoogleNewsDiscoveryPlugin(),
    )


def _hk_sina_symbol(symbol: str) -> str:
    token = symbol.upper().replace(".HK", "")
    return token.zfill(5)


def _hk_yahoo_symbol(symbol: str) -> str:
    code = symbol.upper().replace(".HK", "").zfill(5)
    yahoo_code = code.lstrip("0")
    if len(yahoo_code) < 4:
        yahoo_code = code[-4:]
    return f"{yahoo_code}.HK"


def _hk_finnhub_symbol(symbol: str) -> str:
    token = symbol.upper()
    return token if token.endswith(".HK") else f"{token}.HK"


def _hk_plain_code(symbol: str) -> str:
    token = symbol.upper().replace(".HK", "")
    digits = "".join(ch for ch in token if ch.isdigit())
    return (digits or token).zfill(5)


def _finnhub_candle_rows(payload: Any, *, symbol: str, provider_id: str) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    status = non_empty(payload.get("s"))
    if status and status.lower() != "ok":
        return []
    timestamps = payload.get("t")
    if not isinstance(timestamps, Sequence) or isinstance(timestamps, (str, bytes, bytearray)):
        return []
    rows: list[dict[str, Any]] = []
    for index, stamp in enumerate(timestamps):
        try:
            period = datetime.fromtimestamp(int(stamp), tz=UTC).date()
        except (TypeError, ValueError):
            period = None
        values = {
            "open": decimal_float(_sequence_value(payload.get("o"), index)),
            "high": decimal_float(_sequence_value(payload.get("h"), index)),
            "low": decimal_float(_sequence_value(payload.get("l"), index)),
            "close": decimal_float(_sequence_value(payload.get("c"), index)),
            "volume": decimal_float(_sequence_value(payload.get("v"), index)),
        }
        if period is None or any(value is None for value in values.values()):
            continue
        row = _base_row(dataset="daily_bar", symbol=symbol, provider_id=provider_id, endpoint_id="stock_candle_daily", source_role="paid_data", period=period)
        row.update({"date": period, "granularity": "daily", **values})
        rows.append({key: value for key, value in row.items() if value is not None})
    return rows


def _sina_hk_rows(body: str | None, *, symbol: str, provider_id: str) -> list[dict[str, Any]]:
    if not body or "=" not in body:
        return []
    try:
        from akshare.stock.stock_hk_sina import hk_js_decode  # type: ignore
        from py_mini_racer import MiniRacer  # type: ignore
    except Exception:
        return []
    encoded = body.split("=", 1)[1].split(";", 1)[0].replace('"', "")
    try:
        js_code = MiniRacer()
        js_code.eval(hk_js_decode)
        decoded = js_code.call("d", encoded)
    except Exception:
        return []
    if not isinstance(decoded, Sequence) or isinstance(decoded, (str, bytes, bytearray)):
        return []
    rows: list[dict[str, Any]] = []
    for item in decoded:
        if not isinstance(item, Mapping):
            continue
        period = parse_date(item.get("date"))
        open_ = decimal_float(item.get("open"))
        high = decimal_float(item.get("high"))
        low = decimal_float(item.get("low"))
        close = decimal_float(item.get("close"))
        volume = decimal_float(item.get("volume"))
        if None in {period, open_, high, low, close, volume}:
            continue
        rows.append(
            {
                "dataset": "daily_bar",
                "market": "HK",
                "symbol_id": symbol,
                "granularity": "daily",
                "period_start": period,
                "period_end": period,
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": volume,
                "exchange": "XHKG",
                "currency": "HKD",
                "timezone": "Asia/Hong_Kong",
                "calendar": "HK_XHKG",
                "provider_lineage": {"provider_id": provider_id, "endpoint_id": "stock_hk_daily"},
                "source_roles": ("built_in_public",),
                "schema_id": "daily_bar.v1",
                "quality_flags": (),
            }
        )
    return rows


def _quote_summary_result(payload: Any) -> Mapping[str, Any] | None:
    if not isinstance(payload, Mapping):
        return None
    summary = payload.get("quoteSummary")
    if not isinstance(summary, Mapping):
        return None
    result = summary.get("result")
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes, bytearray)) or not result:
        return None
    item = result[0]
    return item if isinstance(item, Mapping) else None


def _raw(summary: Mapping[str, Any], *path: str) -> Any:
    value: Any = summary
    for key in path:
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    if isinstance(value, Mapping) and "raw" in value:
        return value.get("raw")
    return value


def _yahoo_chart_rows(payload: Any, *, symbol: str, provider_id: str, endpoint_id: str) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    result = payload.get("chart", {}).get("result") if isinstance(payload.get("chart"), Mapping) else None
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes, bytearray)) or not result:
        return []
    record = result[0]
    if not isinstance(record, Mapping):
        return []
    timestamps = record.get("timestamp")
    quote = record.get("indicators", {}).get("quote", [{}]) if isinstance(record.get("indicators"), Mapping) else [{}]
    item = quote[0] if isinstance(quote, Sequence) and quote and isinstance(quote[0], Mapping) else {}
    rows: list[dict[str, Any]] = []
    if not isinstance(timestamps, Sequence) or isinstance(timestamps, (str, bytes, bytearray)):
        return []
    for index, stamp in enumerate(timestamps):
        values = {key: _sequence_value(item.get(key), index) for key in ("open", "high", "low", "close", "volume")}
        if any(value is None for value in values.values()):
            continue
        period = datetime.fromtimestamp(int(stamp), tz=UTC).date()
        row = _base_row(dataset="daily_bar", symbol=symbol, provider_id=provider_id, endpoint_id=endpoint_id, source_role="built_in_public", period=period)
        row.update({"granularity": "daily", **values})
        rows.append(row)
    return rows


def _yahoo_summary_row(summary: Mapping[str, Any], *, symbol: str, dataset: str, provider_id: str, endpoint_id: str) -> dict[str, Any]:
    row = _base_row(dataset=dataset, symbol=symbol, provider_id=provider_id, endpoint_id=endpoint_id, source_role="built_in_public")
    row.update(
        {
            "pe": decimal_float(_raw(summary, "summaryDetail", "trailingPE") or _raw(summary, "defaultKeyStatistics", "trailingPE")),
            "pb": decimal_float(_raw(summary, "defaultKeyStatistics", "priceToBook")),
            "ps": decimal_float(_raw(summary, "summaryDetail", "priceToSalesTrailing12Months")),
            "market_cap": decimal_float(_raw(summary, "summaryDetail", "marketCap") or _raw(summary, "price", "marketCap")),
            "roe": decimal_float(_raw(summary, "financialData", "returnOnEquity")),
            "gross_margin": decimal_float(_raw(summary, "financialData", "grossMargins")),
            "profit_margin": decimal_float(_raw(summary, "financialData", "profitMargins")),
            "eps": decimal_float(_raw(summary, "defaultKeyStatistics", "trailingEps")),
            "revenue_growth": decimal_float(_raw(summary, "financialData", "revenueGrowth")),
        }
    )
    return {key: value for key, value in row.items() if value is not None}


def _yahoo_timeseries_values(payload: Any) -> dict[str, Any]:
    result = payload.get("timeseries", {}).get("result") if isinstance(payload, Mapping) and isinstance(payload.get("timeseries"), Mapping) else None
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes, bytearray)):
        return {}
    values: dict[str, Any] = {}
    periods: list[Any] = []
    field_map = {
        "trailingPeRatio": "pe",
        "trailingMarketCap": "market_cap",
        "quarterlyBasicEPS": "eps",
        "quarterlyGrossMargin": "gross_margin",
    }
    for block in result:
        if not isinstance(block, Mapping):
            continue
        for source_key, target_key in field_map.items():
            series = block.get(source_key)
            if not isinstance(series, Sequence) or isinstance(series, (str, bytes, bytearray)) or not series:
                continue
            latest = series[-1]
            if not isinstance(latest, Mapping):
                continue
            reported = latest.get("reportedValue")
            raw = reported.get("raw") if isinstance(reported, Mapping) else None
            value = decimal_float(raw)
            if value is not None:
                values[target_key] = value
            period = parse_date(latest.get("asOfDate"))
            if period is not None:
                periods.append(period)
    if periods:
        values["_period"] = max(periods)
    return values


def _tushare_hk_metric_rows(payload: Any, *, symbol: str, provider_id: str) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping) or payload.get("code") not in (0, "0", None):
        return []
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return []
    fields = data.get("fields")
    items = data.get("items")
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes, bytearray)):
        return []
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return []
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
            continue
        mapped = dict(zip((str(field) for field in fields), item, strict=False))
        period = parse_date(mapped.get("end_date"))
        if period is None:
            continue
        row = _base_row(dataset="financial_metric", symbol=symbol, provider_id=provider_id, endpoint_id="hk_fina_indicator", source_role="paid_data", period=period)
        row.update(
            {
                "roe": decimal_float(mapped.get("roe_avg")),
                "eps": decimal_float(mapped.get("basic_eps")),
                "gross_profit": decimal_float(mapped.get("gross_profit")),
            }
        )
        rows.append({key: value for key, value in row.items() if value is not None})
    return rows


def _eastmoney_hk_metric_rows(payload: Any, *, symbol: str, provider_id: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _eastmoney_result_rows(payload):
        period = _safe_parse_date(
            _first_mapping_value(item, "STD_REPORT_DATE", "REPORT_DATE", "END_DATE", "FISCAL_YEAR")
        )
        row = _base_row(
            dataset="financial_metric",
            symbol=symbol,
            provider_id=provider_id,
            endpoint_id="stock_financial_hk_analysis_indicator_em",
            source_role="built_in_public",
            period=period,
        )
        row.update(
            {
                "roe": _first_mapping_metric(
                    item,
                    "ROE",
                    "ROE_AVG",
                    "ROE_WEIGHTED",
                    "WEIGHTAVGROE",
                    "AVG_ROE",
                    "JQJZCSYL",
                    "净资产收益率",
                    "淨資產收益率",
                ),
                "gross_margin": _first_mapping_metric(
                    item,
                    "GROSS_MARGIN",
                    "GROSS_PROFIT_MARGIN",
                    "GROSSPROFITMARGIN",
                    "SALESGROSSPROFITRTO",
                    "XSMLL",
                    "销售毛利率",
                    "銷售毛利率",
                ),
                "eps": _first_mapping_metric(
                    item,
                    "BASIC_EPS",
                    "BASIC_EPS_BY",
                    "BASICEPS",
                    "EPS",
                    "JBMGSR",
                    "每股收益",
                    "每股盈利",
                ),
            }
        )
        cleaned = {key: value for key, value in row.items() if value is not None}
        if any(key in cleaned for key in ("roe", "gross_margin", "eps")):
            rows.append(cleaned)
    return rows


def _eastmoney_hk_report_dates(payload: Any, *, annual_only: bool) -> list[str]:
    dates: list[str] = []
    for item in _eastmoney_result_rows(payload):
        report_list = item.get("REPORT_LIST")
        if not isinstance(report_list, Sequence) or isinstance(report_list, (str, bytes, bytearray)):
            continue
        for report in report_list:
            if not isinstance(report, Mapping):
                continue
            report_type = non_empty(report.get("REPORT_TYPE")) or ""
            if annual_only and report_type != "年报":
                continue
            day = _safe_parse_date(report.get("REPORT_DATE"))
            if day is not None:
                dates.append(day.isoformat())
    return dates


def _eastmoney_hk_income_rows(payload: Any, *, symbol: str, provider_id: str) -> list[dict[str, Any]]:
    grouped: dict[str, dict[str, float]] = {}
    for item in _eastmoney_result_rows(payload):
        day = _safe_parse_date(item.get("REPORT_DATE"))
        name = non_empty(item.get("STD_ITEM_NAME")) or non_empty(item.get("ITEM_NAME")) or ""
        value = decimal_float(item.get("AMOUNT"))
        if day is None or value is None:
            continue
        period = day.isoformat()
        values = grouped.setdefault(period, {})
        if _is_revenue_item(name):
            values.setdefault("revenue", value)
        elif _is_net_income_item(name):
            values.setdefault("net_income", value)

    rows: list[dict[str, Any]] = []
    for period in sorted(grouped, reverse=True):
        values = grouped[period]
        row = _base_row(
            dataset="financial_statement",
            symbol=symbol,
            provider_id=provider_id,
            endpoint_id="stock_financial_hk_report_em_income",
            source_role="built_in_public",
            period=period,
        )
        row["period"] = period
        row.update(values)
        if any(key in values for key in ("revenue", "net_income")):
            rows.append(row)
    return rows


def _eastmoney_result_rows(payload: Any) -> list[Mapping[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    result = payload.get("result")
    if not isinstance(result, Mapping):
        return []
    data = result.get("data")
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
        return []
    return [item for item in data if isinstance(item, Mapping)]


def _safe_parse_date(value: Any) -> Any | None:
    try:
        return parse_date(value)
    except (TypeError, ValueError):
        return None


def _first_mapping_value(item: Mapping[str, Any], *keys: str) -> Any:
    lowered = {str(key).lower(): value for key, value in item.items()}
    for key in keys:
        if key in item:
            return item[key]
        lowered_value = lowered.get(key.lower())
        if lowered_value is not None:
            return lowered_value
    return None


def _first_mapping_metric(item: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = decimal_float(_first_mapping_value(item, key))
        if value is not None:
            return value
    return None


def _is_revenue_item(name: str) -> bool:
    upper = name.upper()
    if "REVENUE" in upper:
        return True
    return "营业收入" in name or "營業收入" in name


def _is_net_income_item(name: str) -> bool:
    upper = name.upper()
    if "NET" in upper and "INCOME" in upper:
        return True
    if ("归属" in name or "歸屬" in name or "母公司" in name) and ("净利润" in name or "淨利潤" in name):
        return True
    return name in {"净利润", "淨利潤"}


def _tushare_hk_daily_rows(payload: Any, *, symbol: str, provider_id: str, endpoint_id: str) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping) or payload.get("code") not in (0, "0", None):
        return []
    data = payload.get("data")
    if not isinstance(data, Mapping):
        return []
    fields = data.get("fields")
    items = data.get("items")
    if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes, bytearray)):
        return []
    if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
        return []
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
            continue
        mapped = dict(zip((str(field) for field in fields), item, strict=False))
        period = parse_date(mapped.get("trade_date"))
        if period is None:
            continue
        if endpoint_id == "hk_daily_adj_valuation":
            market_cap = decimal_float(mapped.get("total_mv") or mapped.get("free_mv"))
            price = decimal_float(mapped.get("close"))
            if market_cap is None and price is None:
                continue
            row = _base_row(dataset="valuation_metric", symbol=symbol, provider_id=provider_id, endpoint_id=endpoint_id, source_role="paid_data", period=period)
            row.update({"granularity": "daily", "price": price, "market_cap": market_cap})
        else:
            open_ = decimal_float(mapped.get("open"))
            high = decimal_float(mapped.get("high"))
            low = decimal_float(mapped.get("low"))
            close = decimal_float(mapped.get("close"))
            volume = decimal_float(mapped.get("vol"))
            amount = decimal_float(mapped.get("amount"))
            if None in {open_, high, low, close}:
                continue
            row = _base_row(dataset="daily_bar", symbol=symbol, provider_id=provider_id, endpoint_id=endpoint_id, source_role="paid_data", period=period)
            row.update(
                {
                    "granularity": "daily",
                    "date": period,
                    "open": open_,
                    "high": high,
                    "low": low,
                    "close": close,
                    "volume": volume,
                    "amount": amount,
                    "adjustment": decimal_float(mapped.get("adj_factor")) if endpoint_id == "hk_daily_adj" else None,
                }
            )
        rows.append({key: value for key, value in row.items() if value is not None})
    return rows


def _tushare_yyyymmdd(value: Any) -> str | None:
    parsed = parse_date(value)
    if parsed is None:
        return None
    return parsed.strftime("%Y%m%d")


def _rss_rows(
    body: str | None,
    *,
    dataset: str,
    symbol: str,
    provider_id: str,
    endpoint_id: str,
    source_role: str,
    source: str,
    region: str | None = None,
    require_symbol_match: bool = False,
) -> list[dict[str, Any]]:
    if not body:
        return []
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return []
    rows: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = non_empty(item.findtext("title"))
        url = non_empty(item.findtext("link"))
        summary = non_empty(item.findtext("description"))
        if title is None or url is None:
            continue
        if require_symbol_match and not _rss_symbol_matches(symbol, title=title, summary=summary, url=url):
            continue
        published = _parse_rss_datetime(item.findtext("pubDate"))
        period = published.date() if published else datetime.now(tz=UTC).date()
        row = _base_row(dataset=dataset, symbol=symbol, provider_id=provider_id, endpoint_id=endpoint_id, source_role=source_role, period=period)
        row.update(
            {
                "title": title,
                "published_at": published,
                "source": source,
                "summary": summary,
                "url": url,
            }
        )
        if dataset == "event_calendar":
            row["event_date"] = period
            row["event_type"] = "regulatory_announcement"
        if dataset == "social_signal":
            row["timestamp"] = published or datetime(period.year, period.month, period.day, tzinfo=UTC)
        if region:
            row["region"] = region
        if source_role == "discovery":
            row["quality_flags"] = ("not_formal_fact_source",)
        rows.append({key: value for key, value in row.items() if value is not None})
    return rows


def _rss_symbol_matches(symbol: str, *, title: str, summary: str | None, url: str) -> bool:
    code = symbol.split(".", 1)[0].strip().upper()
    if not code or not code.isdigit():
        return True
    tokens = {code, code.lstrip("0")}
    if len(code) == 5:
        tokens.add(code[-4:])
    haystack = f"{title} {summary or ''} {url}".upper()
    return any(token and token in haystack for token in tokens)


def _base_row(
    *,
    dataset: str,
    symbol: str,
    provider_id: str,
    endpoint_id: str,
    source_role: str,
    period: Any | None = None,
) -> dict[str, Any]:
    day = parse_date(period) if period is not None else datetime.now(tz=UTC).date()
    if dataset in {"financial_statement", "financial_metric"}:
        granularity = "quarterly"
    elif dataset in {"official_filing", "event_calendar", "company_news", "macro_news", "social_signal"}:
        granularity = "event"
    elif dataset == "valuation_metric":
        granularity = "realtime"
    else:
        granularity = "daily"
    return {
        "dataset": dataset,
        "market": "HK",
        "symbol_id": symbol,
        "granularity": granularity,
        "period_start": day,
        "period_end": day,
        "exchange": "XHKG",
        "currency": "HKD",
        "timezone": "Asia/Hong_Kong",
        "calendar": "HK_XHKG",
        "provider_lineage": {"provider_id": provider_id, "endpoint_id": endpoint_id},
        "source_roles": (source_role,),
        "schema_id": f"{dataset}.v1",
        "quality_flags": (),
    }


def _finnhub_payload_has_error(payload: Mapping[str, Any]) -> bool:
    if any(non_empty(payload.get(key)) for key in ("error", "Error")):
        return True
    status = non_empty(payload.get("s"))
    return bool(status and status.lower() not in {"ok", "no_data"})


def _first_metric(metric: Mapping[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = decimal_float(metric.get(key))
        if value is not None:
            return value
    return None


def _date_window(task: Any, *, default_days: int) -> tuple[Any, Any]:
    end = parse_date(getattr(task, "date_range_end", None)) or datetime.now(tz=UTC).date()
    start = parse_date(getattr(task, "date_range_start", None)) or (end - timedelta(days=default_days))
    return start, end


def _unix_day_start(value: Any) -> int:
    day = parse_date(value) or datetime.now(tz=UTC).date()
    return int(datetime(day.year, day.month, day.day, tzinfo=UTC).timestamp())


def _sequence_value(value: Any, index: int) -> Any:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and index < len(value):
        return value[index]
    return None


def _parse_rss_datetime(value: Any) -> datetime | None:
    text = non_empty(value)
    if text is None:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


__all__ = [
    "HKAkShareFundamentalPlugin",
    "HKEXNewsProviderPlugin",
    "HKFinnhubDataPlugin",
    "HKGoogleNewsDiscoveryPlugin",
    "HKSinaDailyBarPlugin",
    "HKTushareFundamentalPlugin",
    "HKTushareProviderPlugin",
    "HKYahooFinancePlugin",
    "build_hk_provider_plugin",
    "build_hk_provider_plugins",
]
