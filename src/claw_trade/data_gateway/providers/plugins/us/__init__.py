from __future__ import annotations

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
_SEC_ENDPOINT = "https://data.sec.gov"
_SEC_WWW_ENDPOINT = "https://www.sec.gov"
_FINNHUB_ENDPOINT = "https://finnhub.io/api/v1"
_FRED_ENDPOINT = "https://api.stlouisfed.org"
_STOCKTWITS_ENDPOINT = "https://api.stocktwits.com"
_GOOGLE_NEWS_ENDPOINT = "https://news.google.com"
_HEADERS = {"accept": "application/json", "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}


class USYahooFinancePlugin:
    plugin_id = "us_yahoo_finance"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="chart_daily",
                    market="US",
                    data_type="daily_bar",
                    source_role="built_in_public",
                    granularity=("daily",),
                    fields=("open", "high", "low", "close", "volume"),
                    priority_rank=25,
                ),
                endpoint_capability(
                    endpoint_id="quote_snapshot",
                    market="US",
                    data_type="quote_snapshot",
                    source_role="built_in_public",
                    granularity=("realtime",),
                    fields=("price", "change", "change_pct", "volume", "timestamp", "symbol_id"),
                    priority_rank=10,
                ),
                endpoint_capability(
                    endpoint_id="quote_summary_valuation",
                    market="US",
                    data_type="valuation_metric",
                    source_role="built_in_public",
                    granularity=("realtime",),
                    fields=("pe", "market_cap"),
                    priority_rank=10,
                ),
                endpoint_capability(
                    endpoint_id="quote_summary_financial",
                    market="US",
                    data_type="financial_metric",
                    source_role="built_in_public",
                    granularity=("quarterly", "realtime"),
                    fields=("gross_margin", "eps"),
                    priority_rank=10,
                ),
            ),
            credential_policy=NO_CREDENTIALS,
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": None},
            default_priority_rank=10,
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
        if endpoint_id == "chart_daily":
            return self._fetch_chart(task, ctx=ctx, symbol=symbol)
        if endpoint_id == "quote_snapshot":
            return self._fetch_quote_snapshot(task, ctx=ctx, symbol=symbol)
        if endpoint_id in {"quote_snapshot", "quote_summary_valuation", "quote_summary_financial"}:
            return self._fetch_quote_summary(task, ctx=ctx, symbol=symbol, endpoint_id=endpoint_id)
        return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))

    def _fetch_chart(self, task: Any, *, ctx: Any, symbol: str) -> FetchResult:
        host, prefix = endpoint(ctx, "data_source:yahoo_finance", _YAHOO_ENDPOINT)
        query: dict[str, Any] = {
            "interval": "1d",
            "events": "history",
            "includeAdjustedClose": "true",
        }
        start = parse_datetime(getattr(task, "date_range_start", None))
        end = parse_datetime(getattr(task, "date_range_end", None))
        if start is not None:
            query["period1"] = int(start.timestamp())
        if end is not None:
            query["period2"] = int(end.timestamp())
        if start is None and end is None:
            query["range"] = "1y"
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/v8/finance/chart/{symbol}",
                query=query,
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        rows = _yahoo_chart_rows(payload, symbol=symbol, provider_id=self.plugin_id, endpoint_id="chart_daily", market="US")
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)

    def _fetch_quote_snapshot(self, task: Any, *, ctx: Any, symbol: str) -> FetchResult:
        host, prefix = endpoint(ctx, "data_source:yahoo_finance", _YAHOO_ENDPOINT)
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/v8/finance/chart/{symbol}",
                query={"range": "1d", "interval": "1m"},
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        row = _yahoo_quote_snapshot_from_chart(payload, symbol=symbol, provider_id=self.plugin_id, endpoint_id="quote_snapshot", market="US")
        if not any(key in row for key in getattr(task, "fields", ()) or ()):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": [row]}, row_count=1, http_observations=observations)

    def _fetch_quote_summary(self, task: Any, *, ctx: Any, symbol: str, endpoint_id: str) -> FetchResult:
        host, prefix = endpoint(ctx, "data_source:yahoo_finance", _YAHOO_ENDPOINT)
        types = "trailingPeRatio,trailingMarketCap" if endpoint_id == "quote_summary_valuation" else "quarterlyBasicEPS,quarterlyGrossMargin"
        now = datetime.now(tz=UTC)
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/ws/fundamentals-timeseries/v1/finance/timeseries/{symbol}",
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
            market="US",
            symbol=symbol,
            provider_id=self.plugin_id,
            endpoint_id=endpoint_id,
            source_role="built_in_public",
            period=values.pop("_period", None),
        )
        row.update(values)
        row["currency"] = "USD"
        if not any(key in row for key in getattr(task, "fields", ()) or ()):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": [row]}, row_count=1, http_observations=observations)


class USSECProviderPlugin:
    plugin_id = "us_sec_official"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="submissions",
                    market="US",
                    data_type="official_filing",
                    source_role="official",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "url", "symbol_id"),
                    priority_rank=0,
                    supports_batch=False,
                    batch_by="none",
                ),
                endpoint_capability(
                    endpoint_id="companyfacts",
                    market="US",
                    data_type="financial_statement",
                    source_role="official",
                    granularity=("quarterly", "annual"),
                    fields=("period", "revenue", "net_income", "assets", "liabilities", "cash_flow"),
                    priority_rank=0,
                    supports_batch=False,
                    batch_by="none",
                ),
            ),
            credential_policy=NO_CREDENTIALS,
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": None},
            default_priority_rank=0,
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
        observations: list[Any] = []
        cik_result = self._resolve_cik(task, ctx=ctx, http=http, observations=observations, symbol=symbol)
        if isinstance(cik_result, FetchResult):
            return cik_result
        endpoint_id = str(getattr(task, "endpoint_id", ""))
        if endpoint_id == "submissions":
            return self._fetch_submissions(task, ctx=ctx, http=http, observations=observations, symbol=symbol, cik=cik_result)
        if endpoint_id == "companyfacts":
            return self._fetch_companyfacts(task, ctx=ctx, http=http, observations=observations, symbol=symbol, cik=cik_result)
        return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))

    def _resolve_cik(self, task: Any, *, ctx: Any, http: Any, observations: list[Any], symbol: str) -> str | FetchResult:
        host, prefix = endpoint(ctx, "data_source:sec", _SEC_WWW_ENDPOINT)
        capture = http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/files/company_tickers.json",
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        observations.append(capture.observation)
        error = result_from_capture_error(task, capture, tuple(observations))
        if error is not None:
            return error
        payload = capture.json_payload
        if not isinstance(payload, Mapping):
            return FetchResult.from_error(task, status="error", error=RuntimeError("invalid_sec_ticker_payload"), http_observations=tuple(observations))
        for item in payload.values():
            if isinstance(item, Mapping) and str(item.get("ticker", "")).upper() == symbol:
                cik = str(item.get("cik_str", "")).strip()
                if cik:
                    return cik.zfill(10)
        return FetchResult.from_empty(task, error=RuntimeError("ticker_not_found"))

    def _fetch_submissions(
        self,
        task: Any,
        *,
        ctx: Any,
        http: Any,
        observations: list[Any],
        symbol: str,
        cik: str,
    ) -> FetchResult:
        host, prefix = endpoint(ctx, "data_source:sec", _SEC_ENDPOINT)
        capture = http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/submissions/CIK{cik}.json",
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        observations.append(capture.observation)
        error = result_from_capture_error(task, capture, tuple(observations))
        if error is not None:
            return error
        rows = _sec_submission_rows(capture.json_payload, symbol=symbol, provider_id=self.plugin_id)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=tuple(observations))

    def _fetch_companyfacts(
        self,
        task: Any,
        *,
        ctx: Any,
        http: Any,
        observations: list[Any],
        symbol: str,
        cik: str,
    ) -> FetchResult:
        host, prefix = endpoint(ctx, "data_source:sec", _SEC_ENDPOINT)
        capture = http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/api/xbrl/companyfacts/CIK{cik}.json",
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            )
        )
        observations.append(capture.observation)
        error = result_from_capture_error(task, capture, tuple(observations))
        if error is not None:
            return error
        rows = _sec_companyfacts_rows(capture.json_payload, symbol=symbol, provider_id=self.plugin_id)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=tuple(observations))


class USFinnhubDataPlugin:
    plugin_id = "us_finnhub_data"
    version = "1.0.0"
    credential_name = "data_source:finnhub"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="quote",
                    market="US",
                    data_type="quote_snapshot",
                    source_role="paid_data",
                    granularity=("realtime",),
                    fields=("price", "change", "change_pct", "timestamp", "symbol_id"),
                    priority_rank=14,
                ),
                endpoint_capability(
                    endpoint_id="stock_metric_valuation",
                    market="US",
                    data_type="valuation_metric",
                    source_role="paid_data",
                    granularity=("realtime",),
                    fields=("pe", "pb", "ps", "market_cap"),
                    priority_rank=16,
                ),
                endpoint_capability(
                    endpoint_id="stock_metric_financial",
                    market="US",
                    data_type="financial_metric",
                    source_role="paid_data",
                    granularity=("quarterly", "realtime"),
                    fields=("roe", "roa", "profit_margin", "eps"),
                    priority_rank=16,
                ),
                endpoint_capability(
                    endpoint_id="company_news",
                    market="US",
                    data_type="company_news",
                    source_role="paid_data",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "summary", "url", "symbol_id"),
                    priority_rank=12,
                ),
                endpoint_capability(
                    endpoint_id="stock_filings",
                    market="US",
                    data_type="official_filing",
                    source_role="paid_data",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "url", "body_ref", "symbol_id"),
                    priority_rank=12,
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
            default_priority_rank=16,
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
        if endpoint_id in {"quote", "stock_metric_valuation", "stock_metric_financial", "company_news", "stock_filings"} and symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        if endpoint_id == "quote":
            return self._fetch_quote(task, ctx=ctx, token=token, symbol=str(symbol))
        if endpoint_id in {"stock_metric_valuation", "stock_metric_financial"}:
            return self._fetch_metric(task, ctx=ctx, token=token, symbol=str(symbol), endpoint_id=endpoint_id)
        if endpoint_id == "company_news":
            return self._fetch_company_news(task, ctx=ctx, token=token, symbol=str(symbol))
        if endpoint_id == "stock_filings":
            return self._fetch_filings(task, ctx=ctx, token=token, symbol=str(symbol))
        return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))

    def _fetch_quote(self, task: Any, *, ctx: Any, token: str, symbol: str) -> FetchResult:
        payload, observations, error = self._finnhub_request(task, ctx=ctx, token=token, path="/quote", query={"symbol": symbol})
        if error is not None:
            return error
        if not isinstance(payload, Mapping) or _finnhub_payload_has_error(payload):
            return FetchResult.from_error(task, status="error", error=RuntimeError("invalid_provider_payload"), http_observations=observations)
        price = decimal_float(payload.get("c"))
        if price is None:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        row = _base_row(
            dataset="quote_snapshot",
            market="US",
            symbol=symbol,
            provider_id=self.plugin_id,
            endpoint_id="quote",
            source_role="paid_data",
        )
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

    def _fetch_metric(self, task: Any, *, ctx: Any, token: str, symbol: str, endpoint_id: str) -> FetchResult:
        payload, observations, error = self._finnhub_request(task, ctx=ctx, token=token, path="/stock/metric", query={"symbol": symbol, "metric": "all"})
        if error is not None:
            return error
        metric = payload.get("metric") if isinstance(payload, Mapping) else None
        if not isinstance(metric, Mapping):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        dataset = "valuation_metric" if endpoint_id == "stock_metric_valuation" else "financial_metric"
        row = _base_row(
            dataset=dataset,
            market="US",
            symbol=symbol,
            provider_id=self.plugin_id,
            endpoint_id=endpoint_id,
            source_role="paid_data",
        )
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

    def _fetch_company_news(self, task: Any, *, ctx: Any, token: str, symbol: str) -> FetchResult:
        start, end = _date_window(task, default_days=30)
        payload, observations, error = self._finnhub_request(
            task,
            ctx=ctx,
            token=token,
            path="/company-news",
            query={"symbol": symbol, "from": start.isoformat(), "to": end.isoformat()},
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
            row = _event_row(
                dataset="company_news",
                market="US",
                symbol=symbol,
                provider_id=self.plugin_id,
                endpoint_id="company_news",
                source_role="paid_data",
                period=period,
                title=title,
                published_at=published,
                source=non_empty(item.get("source")) or "Finnhub",
                summary=non_empty(item.get("summary")),
                url=url,
            )
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)

    def _fetch_filings(self, task: Any, *, ctx: Any, token: str, symbol: str) -> FetchResult:
        start, end = _date_window(task, default_days=365)
        payload, observations, error = self._finnhub_request(
            task,
            ctx=ctx,
            token=token,
            path="/stock/filings",
            query={"symbol": symbol, "from": start.isoformat(), "to": end.isoformat()},
        )
        if error is not None:
            return error
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, bytearray)):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        rows: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            filed = parse_datetime(item.get("acceptedDate")) or parse_datetime(item.get("filedDate")) or parse_date(item.get("filedDate"))
            period = filed.date() if isinstance(filed, datetime) else filed
            form = non_empty(item.get("form")) or "filing"
            url = non_empty(item.get("reportUrl") or item.get("filingUrl"))
            if period is None or url is None:
                continue
            row = _event_row(
                dataset="official_filing",
                market="US",
                symbol=symbol,
                provider_id=self.plugin_id,
                endpoint_id="stock_filings",
                source_role="paid_data",
                period=period,
                title=f"{form} filing",
                published_at=filed,
                source="Finnhub SEC filings",
                url=url,
                body_ref=url,
            )
            row["quality_flags"] = ("paid_aggregator_official_filing_url",)
            rows.append(row)
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


class USFREDMacroPlugin:
    plugin_id = "us_fred_macro"
    version = "1.0.0"
    credential_name = "data_source:fred"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="series_observations",
                    market="US",
                    data_type="macro_series",
                    source_role="official",
                    granularity=("daily", "monthly", "quarterly"),
                    fields=("series_id", "date", "value", "unit", "region"),
                    priority_rank=5,
                    supports_batch=False,
                    batch_by="none",
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
            default_priority_rank=5,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        token = credential_value(ctx, self.credential_name)
        if token is None:
            return FetchResult.from_error(task, status="credential_missing", error=RuntimeError(f"credential_missing:{self.credential_name}"))
        params = getattr(task, "params", {}) if isinstance(getattr(task, "params", {}), Mapping) else {}
        series_id = non_empty(params.get("series_id")) or first_symbol(task)
        if series_id is None:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("fred_series_id_required"))
        host, prefix = endpoint(ctx, self.credential_name, _FRED_ENDPOINT)
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/fred/series/observations",
                query={"series_id": series_id, "api_key": token, "file_type": "json", "sort_order": "desc", "limit": "100"},
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        observations_payload = payload.get("observations") if isinstance(payload, Mapping) else None
        if not isinstance(observations_payload, Sequence) or isinstance(observations_payload, (str, bytes, bytearray)):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        rows: list[dict[str, Any]] = []
        for item in observations_payload:
            if not isinstance(item, Mapping):
                continue
            value = decimal_float(item.get("value"))
            period = parse_date(item.get("date"))
            if value is None or period is None:
                continue
            rows.append(
                {
                    "dataset": "macro_series",
                    "market": "US",
                    "symbol_id": series_id,
                    "series_id": series_id,
                    "date": period,
                    "period_start": period,
                    "period_end": period,
                    "value": value,
                    "unit": "index",
                    "region": "US",
                    "exchange": "FRED",
                    "currency": "USD",
                    "timezone": "America/New_York",
                    "calendar": "US_FED",
                    "provider_lineage": {"provider_id": self.plugin_id, "endpoint_id": "series_observations"},
                    "source_roles": ("official",),
                    "schema_id": "macro_series.v1",
                    "quality_flags": (),
                }
            )
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)


class USStocktwitsSocialPlugin:
    plugin_id = "us_stocktwits_social"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="symbol_stream",
                    market="US",
                    data_type="social_signal",
                    source_role="sentiment",
                    granularity=("event",),
                    fields=("source", "timestamp", "message", "sentiment", "symbol_id"),
                    priority_rank=10,
                    can_be_formal_fact_source=False,
                ),
            ),
            credential_policy=NO_CREDENTIALS,
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": None},
            default_priority_rank=10,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        symbol = first_symbol(task)
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        host, prefix = endpoint(ctx, "data_source:stocktwits", _STOCKTWITS_ENDPOINT)
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/api/2/streams/symbol/{symbol}.json",
                headers=_HEADERS,
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        messages = payload.get("messages") if isinstance(payload, Mapping) else None
        if not isinstance(messages, Sequence) or isinstance(messages, (str, bytes, bytearray)):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        rows: list[dict[str, Any]] = []
        for item in messages:
            if not isinstance(item, Mapping):
                continue
            timestamp = parse_datetime(item.get("created_at"))
            body = non_empty(item.get("body"))
            if timestamp is None or body is None:
                continue
            sentiment = item.get("entities", {}).get("sentiment") if isinstance(item.get("entities"), Mapping) else None
            rows.append(
                _event_row(
                    dataset="social_signal",
                    market="US",
                    symbol=symbol,
                    provider_id=self.plugin_id,
                    endpoint_id="symbol_stream",
                    source_role="sentiment",
                    period=timestamp,
                    source="stocktwits",
                    timestamp=timestamp,
                    message=body,
                    sentiment=_sentiment_label(sentiment),
                    can_score=False,
                )
            )
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)


class USGoogleNewsDiscoveryPlugin:
    plugin_id = "us_google_news"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="company_news",
                    market="US",
                    data_type="company_news",
                    source_role="discovery",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "summary", "url"),
                    priority_rank=50,
                    can_be_formal_fact_source=False,
                ),
                endpoint_capability(
                    endpoint_id="macro_news",
                    market="US",
                    data_type="macro_news",
                    source_role="discovery",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "summary", "url", "region"),
                    priority_rank=50,
                    can_be_formal_fact_source=False,
                ),
            ),
            credential_policy=NO_CREDENTIALS,
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": None},
            default_priority_rank=50,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        symbol = first_symbol(task) or "US"
        query = f"{symbol} stock news" if str(getattr(task, "endpoint_id", "")) == "company_news" else "United States economy stock market news"
        host, prefix = endpoint(ctx, "data_source:google_news", _GOOGLE_NEWS_ENDPOINT)
        http = managed_http(ctx)
        if http is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))
        capture = http.send_capture(
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/rss/search",
                query={"q": query, "hl": "en-US", "gl": "US", "ceid": "US:en"},
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
            market="US",
            symbol=symbol,
            provider_id=self.plugin_id,
            endpoint_id=str(getattr(task, "endpoint_id", "company_news")),
            source_role="discovery",
            region="US",
        )
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)


def build_us_provider_plugin() -> USYahooFinancePlugin:
    return USYahooFinancePlugin()


def build_us_provider_plugins() -> tuple[object, ...]:
    return (
        USSECProviderPlugin(),
        USFinnhubDataPlugin(),
        USYahooFinancePlugin(),
        USFREDMacroPlugin(),
        USStocktwitsSocialPlugin(),
        USGoogleNewsDiscoveryPlugin(),
    )


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


def _yahoo_chart_rows(payload: Any, *, symbol: str, provider_id: str, endpoint_id: str, market: str) -> list[dict[str, Any]]:
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
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    rows: list[dict[str, Any]] = []
    if not isinstance(timestamps, Sequence) or isinstance(timestamps, (str, bytes, bytearray)):
        return []
    for index, stamp in enumerate(timestamps):
        values = {key: _sequence_value(item.get(key), index) for key in ("open", "high", "low", "close", "volume")}
        if any(value is None for value in values.values()):
            continue
        period = datetime.fromtimestamp(int(stamp), tz=UTC).date()
        rows.append(
            {
                "dataset": "daily_bar",
                "market": market,
                "symbol_id": symbol,
                "granularity": "daily",
                "period_start": period,
                "period_end": period,
                **values,
                "exchange": non_empty(meta.get("exchangeName") or meta.get("exchange")),
                "currency": non_empty(meta.get("currency")) or "USD",
                "timezone": "America/New_York",
                "calendar": "US_NYSE_NASDAQ",
                "provider_lineage": {"provider_id": provider_id, "endpoint_id": endpoint_id},
                "source_roles": ("built_in_public",),
                "schema_id": "daily_bar.v1",
                "quality_flags": (),
            }
        )
    return rows


def _yahoo_quote_summary_row(
    summary: Mapping[str, Any],
    *,
    symbol: str,
    dataset: str,
    provider_id: str,
    endpoint_id: str,
    market: str,
) -> dict[str, Any]:
    row = _base_row(dataset=dataset, market=market, symbol=symbol, provider_id=provider_id, endpoint_id=endpoint_id, source_role="built_in_public")
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
    price = summary.get("price") if isinstance(summary.get("price"), Mapping) else {}
    row["exchange"] = non_empty(price.get("exchangeName") or price.get("exchange"))
    row["currency"] = non_empty(_raw(summary, "price", "currency")) or "USD"
    row["timezone"] = "America/New_York"
    row["calendar"] = "US_NYSE_NASDAQ"
    return {key: value for key, value in row.items() if value is not None}


def _yahoo_quote_snapshot_row(
    summary: Mapping[str, Any],
    *,
    symbol: str,
    provider_id: str,
    endpoint_id: str,
    market: str,
) -> dict[str, Any]:
    row = _base_row(dataset="quote_snapshot", market=market, symbol=symbol, provider_id=provider_id, endpoint_id=endpoint_id, source_role="built_in_public")
    row.update(
        {
            "price": decimal_float(_raw(summary, "price", "regularMarketPrice")),
            "change": decimal_float(_raw(summary, "price", "regularMarketChange")),
            "change_pct": decimal_float(_raw(summary, "price", "regularMarketChangePercent")),
            "volume": decimal_float(_raw(summary, "price", "regularMarketVolume")),
            "timestamp": parse_datetime(_raw(summary, "price", "regularMarketTime")),
            "exchange": non_empty(_raw(summary, "price", "exchangeName") or _raw(summary, "price", "exchange")),
            "currency": non_empty(_raw(summary, "price", "currency")) or "USD",
            "timezone": "America/New_York",
            "calendar": "US_NYSE_NASDAQ",
        }
    )
    return {key: value for key, value in row.items() if value is not None}


def _yahoo_quote_snapshot_from_chart(
    payload: Any,
    *,
    symbol: str,
    provider_id: str,
    endpoint_id: str,
    market: str,
) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return {}
    result = payload.get("chart", {}).get("result") if isinstance(payload.get("chart"), Mapping) else None
    if not isinstance(result, Sequence) or isinstance(result, (str, bytes, bytearray)) or not result:
        return {}
    record = result[0]
    if not isinstance(record, Mapping):
        return {}
    meta = record.get("meta") if isinstance(record.get("meta"), Mapping) else {}
    row = _base_row(dataset="quote_snapshot", market=market, symbol=symbol, provider_id=provider_id, endpoint_id=endpoint_id, source_role="built_in_public")
    previous_close = decimal_float(meta.get("previousClose") or meta.get("chartPreviousClose"))
    price = decimal_float(meta.get("regularMarketPrice"))
    change = price - previous_close if price is not None and previous_close not in (None, 0) else None
    change_pct = (change / previous_close * 100) if change is not None and previous_close not in (None, 0) else None
    row.update(
        {
            "price": price,
            "change": change,
            "change_pct": change_pct,
            "volume": decimal_float(meta.get("regularMarketVolume")),
            "timestamp": parse_datetime(meta.get("regularMarketTime")),
            "exchange": non_empty(meta.get("exchangeName") or meta.get("exchange")),
            "currency": non_empty(meta.get("currency")) or "USD",
            "timezone": "America/New_York",
            "calendar": "US_NYSE_NASDAQ",
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


def _sec_submission_rows(payload: Any, *, symbol: str, provider_id: str) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    recent = payload.get("filings", {}).get("recent") if isinstance(payload.get("filings"), Mapping) else None
    if not isinstance(recent, Mapping):
        return []
    forms = recent.get("form") or ()
    dates = recent.get("filingDate") or ()
    accessions = recent.get("accessionNumber") or ()
    docs = recent.get("primaryDocument") or ()
    rows: list[dict[str, Any]] = []
    for index, form in enumerate(forms if isinstance(forms, Sequence) and not isinstance(forms, (str, bytes, bytearray)) else ()):
        filing_date = _sequence_value(dates, index)
        period = parse_date(filing_date)
        if period is None:
            continue
        accession = non_empty(_sequence_value(accessions, index))
        document = non_empty(_sequence_value(docs, index))
        cik = non_empty(payload.get("cik")) or ""
        accession_path = accession.replace("-", "") if accession else ""
        url = f"https://www.sec.gov/Archives/edgar/data/{int(cik) if cik.isdigit() else cik}/{accession_path}/{document}" if accession and document and cik else None
        rows.append(
            _event_row(
                dataset="official_filing",
                market="US",
                symbol=symbol,
                provider_id=provider_id,
                endpoint_id="submissions",
                source_role="official",
                period=period,
                source="SEC EDGAR",
                published_at=period,
                title=f"{form} filing",
                url=url,
            )
        )
    return rows


def _sec_companyfacts_rows(payload: Any, *, symbol: str, provider_id: str) -> list[dict[str, Any]]:
    if not isinstance(payload, Mapping):
        return []
    facts = payload.get("facts", {}).get("us-gaap") if isinstance(payload.get("facts"), Mapping) else None
    if not isinstance(facts, Mapping):
        return []
    rows_by_period: dict[str, dict[str, Any]] = {}
    tags = {
        "revenue": ("Revenues", "SalesRevenueNet"),
        "net_income": ("NetIncomeLoss",),
        "assets": ("Assets",),
        "liabilities": ("Liabilities",),
        "cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    }
    for field, candidates in tags.items():
        for tag in candidates:
            tag_payload = facts.get(tag)
            units = tag_payload.get("units", {}) if isinstance(tag_payload, Mapping) else {}
            values = units.get("USD") if isinstance(units, Mapping) else None
            if not isinstance(values, Sequence) or isinstance(values, (str, bytes, bytearray)):
                continue
            for item in reversed(values):
                if not isinstance(item, Mapping):
                    continue
                period = parse_date(item.get("end"))
                value = decimal_float(item.get("val"))
                if period is None or value is None:
                    continue
                key = period.isoformat()
                row = rows_by_period.setdefault(
                    key,
                    _base_row(
                        dataset="financial_statement",
                        market="US",
                        symbol=symbol,
                        provider_id=provider_id,
                        endpoint_id="companyfacts",
                        source_role="official",
                        period=period,
                    ),
                )
                row[field] = value
                row["period"] = key
                break
            if any(field in row for row in rows_by_period.values()):
                break
    return list(rows_by_period.values())


def _rss_rows(
    body: str | None,
    *,
    dataset: str,
    market: str,
    symbol: str,
    provider_id: str,
    endpoint_id: str,
    source_role: str,
    region: str,
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
        if title is None or url is None:
            continue
        published = _parse_rss_datetime(item.findtext("pubDate"))
        period = published.date() if published else datetime.now(tz=UTC).date()
        row = _event_row(
            dataset=dataset,
            market=market,
            symbol=symbol,
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            source_role=source_role,
            period=period,
            source="Google News",
            published_at=published,
            title=title,
            summary=non_empty(item.findtext("description")),
            url=url,
        )
        row["region"] = region
        rows.append(row)
    return rows


def _event_row(
    *,
    dataset: str,
    market: str,
    symbol: str,
    provider_id: str,
    endpoint_id: str,
    source_role: str,
    period: Any,
    **extra: Any,
) -> dict[str, Any]:
    day = period.date() if isinstance(period, datetime) else parse_date(period)
    row = _base_row(dataset=dataset, market=market, symbol=symbol, provider_id=provider_id, endpoint_id=endpoint_id, source_role=source_role, period=day)
    row.update(extra)
    row["source_roles"] = (source_role,)
    if source_role in {"discovery", "sentiment"}:
        row["quality_flags"] = ("not_formal_fact_source",)
    return {key: value for key, value in row.items() if value is not None}


def _base_row(
    *,
    dataset: str,
    market: str,
    symbol: str,
    provider_id: str,
    endpoint_id: str,
    source_role: str,
    period: Any | None = None,
) -> dict[str, Any]:
    day = parse_date(period) if period is not None else datetime.now(tz=UTC).date()
    if dataset in {"financial_statement", "financial_metric"}:
        granularity = "quarterly"
    elif dataset in {"official_filing", "social_signal", "company_news", "macro_news"}:
        granularity = "event"
    elif dataset in {"quote_snapshot", "valuation_metric"}:
        granularity = "realtime"
    else:
        granularity = "daily"
    return {
        "dataset": dataset,
        "market": market,
        "symbol_id": symbol,
        "granularity": granularity,
        "period_start": day,
        "period_end": day,
        "exchange": "NASDAQ" if market == "US" else None,
        "currency": "USD",
        "timezone": "America/New_York",
        "calendar": "US_NYSE_NASDAQ",
        "provider_lineage": {"provider_id": provider_id, "endpoint_id": endpoint_id},
        "source_roles": (source_role,),
        "schema_id": f"{dataset}.v1",
        "quality_flags": (),
    }


def _sequence_value(value: Any, index: int) -> Any:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) and index < len(value):
        return value[index]
    return None


def _sentiment_label(value: Any) -> str | None:
    if not isinstance(value, Mapping):
        return None
    label = value.get("basic") or value.get("label")
    return non_empty(label)


def _parse_rss_datetime(value: Any) -> datetime | None:
    text = non_empty(value)
    if text is None:
        return None
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _finnhub_payload_has_error(payload: Mapping[str, Any]) -> bool:
    return any(non_empty(payload.get(key)) for key in ("error", "Error", "s"))


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


__all__ = [
    "USFinnhubDataPlugin",
    "USFREDMacroPlugin",
    "USGoogleNewsDiscoveryPlugin",
    "USSECProviderPlugin",
    "USStocktwitsSocialPlugin",
    "USYahooFinancePlugin",
    "build_us_provider_plugin",
    "build_us_provider_plugins",
]
