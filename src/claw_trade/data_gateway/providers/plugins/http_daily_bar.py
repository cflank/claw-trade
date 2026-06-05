from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse

from claw_trade.data_gateway.execution.managed_http import HttpRequestSpec
from claw_trade.data_gateway.models import FetchResult

from .common import (
    BatchPolicy,
    CredentialPolicy,
    EndpointCapability,
    LicensePolicy,
    ProviderCapabilities,
)


class _HttpDailyBarPlugin:
    plugin_id: str
    version = "1.0.0"
    market: str
    endpoint_id = "daily_bar"
    credential_name: str
    default_endpoint: str
    source_role = "official"
    fields = ("open", "high", "low", "close", "volume")
    _currency: str
    _timezone: str
    _calendar: str

    def __init__(self) -> None:
        license_policy = LicensePolicy(
            raw_storage_mode="metadata_only",
            normalized_storage_allowed=True,
            redistribution_allowed=False,
            retention_days=30,
        )
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                EndpointCapability(
                    endpoint_id=self.endpoint_id,
                    market=self.market,
                    data_type="daily_bar",
                    source_role=self.source_role,
                    granularity=("daily",),
                    fields=self.fields,
                    freshness_supported=("trading_day",),
                    http_visibility="managed_http",
                    batch_policy=BatchPolicy(
                        supports_batch=True,
                        batch_by="symbol",
                        max_symbols_per_call=20,
                        max_days_per_call=1000,
                        mergeable_fields=self.fields,
                    ),
                    priority_rank=5,
                    rate_limit_policy={"window_seconds": 60, "max_calls": None},
                    license_policy=license_policy,
                ),
            ),
            credential_policy=CredentialPolicy(
                credential_required=True,
                credential_names=(self.credential_name,),
                credential_scope="provider_token",
                missing_behavior="credential_missing",
            ),
            license_policy=license_policy,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": None},
            default_priority_rank=20,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        token = _credential_value(ctx, self.credential_name)
        if token is None:
            return FetchResult.from_error(
                task,
                status="credential_missing",
                error=RuntimeError(f"credential_missing:{self.credential_name}"),
            )

        managed_http = getattr(ctx, "managed_http", None)
        if managed_http is None or not callable(getattr(managed_http, "send_capture", None)):
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))

        request_targets = self._request_targets(task, token=token, ctx=ctx)
        if not request_targets:
            return FetchResult.from_error(task, status="error", error=RuntimeError(self._empty_target_error(task)))

        rows: list[dict[str, Any]] = []
        observations: list[Any] = []
        for symbol, request in request_targets:
            capture = managed_http.send_capture(request)
            observations.append(capture.observation)
            if capture.observation.error_code:
                return FetchResult.from_error(
                    task,
                    status="error",
                    error=RuntimeError(capture.observation.error_code),
                    http_observations=tuple(observations),
                )
            if capture.observation.quota_signal or capture.observation.status_code == 429:
                return FetchResult.from_error(
                    task,
                    status="rate_limited",
                    error=RuntimeError(capture.observation.quota_signal or "http_429"),
                    http_observations=tuple(observations),
                )
            if capture.observation.status_code is None or capture.observation.status_code >= 400:
                return FetchResult.from_error(
                    task,
                    status="error",
                    error=RuntimeError(f"http_{capture.observation.status_code}"),
                    http_observations=tuple(observations),
                )
            parsed = self._rows_from_payload(capture.json_payload, task=task, symbol=symbol)
            if parsed is None:
                return FetchResult.from_error(
                    task,
                    status="error",
                    error=RuntimeError("invalid_provider_payload"),
                    http_observations=tuple(observations),
                )
            rows.extend(parsed)

        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(
            task,
            payload={"rows": rows},
            row_count=len(rows),
            http_observations=tuple(observations),
        )

    def _request_targets(self, task: Any, *, token: str, ctx: Any) -> tuple[tuple[str, HttpRequestSpec], ...]:
        symbols = tuple(str(item).strip().upper() for item in getattr(task, "symbol_ids", ()) if str(item).strip())
        return tuple((symbol, self._request_for(task, symbol=symbol, token=token, ctx=ctx)) for symbol in symbols)

    def _empty_target_error(self, task: Any) -> str:
        del task
        return "symbol_required"

    def _request_for(self, task: Any, *, symbol: str, token: str, ctx: Any) -> HttpRequestSpec:
        raise NotImplementedError

    def _rows_from_payload(self, payload: Any, *, task: Any, symbol: str) -> list[dict[str, Any]] | None:
        raise NotImplementedError

    def _base_row(self, *, symbol: str, period: date, open_: float, high: float, low: float, close: float, volume: float) -> dict[str, Any]:
        return {
            "dataset": "daily_bar",
            "market": self.market,
            "symbol_id": symbol,
            "granularity": "daily",
            "period_start": period,
            "period_end": period,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "currency": self._currency,
            "timezone": self._timezone,
            "calendar": self._calendar,
            "provider_lineage": {"provider_id": self.plugin_id, "endpoint_id": self.endpoint_id},
            "source_roles": (self.source_role,),
            "schema_id": "daily_bar.v1",
            "quality_flags": (),
        }


class TushareDailyBarPlugin(_HttpDailyBarPlugin):
    plugin_id = "cn_a_primary"
    market = "CN_A"
    universe_endpoint_id = "daily_bar_by_trade_date"
    credential_name = "data_source:tushare"
    default_endpoint = "https://api.tushare.pro"
    source_role = "paid_data"
    fields = ("date", "open", "high", "low", "close", "volume", "amount")
    _currency = "CNY"
    _timezone = "Asia/Shanghai"
    _calendar = "CN_A_SSE_SZSE"

    def __init__(self) -> None:
        super().__init__()
        base = self._capabilities
        universe_capability = EndpointCapability(
            endpoint_id=self.universe_endpoint_id,
            market=self.market,
            data_type="daily_bar",
            source_role=self.source_role,
            granularity=("daily",),
            fields=self.fields,
            freshness_supported=("trading_day",),
            http_visibility="managed_http",
            batch_policy=BatchPolicy(
                supports_batch=True,
                batch_by="date",
                max_days_per_call=1,
                mergeable_fields=self.fields,
            ),
            priority_rank=5,
            rate_limit_policy={"window_seconds": 60, "max_calls": None},
            license_policy=base.license_policy,
        )
        self._capabilities = ProviderCapabilities(
            provider_id=base.provider_id,
            plugin_version=base.plugin_version,
            endpoints=(*base.endpoints, universe_capability),
            credential_policy=base.credential_policy,
            license_policy=base.license_policy,
            default_rate_limit_policy=base.default_rate_limit_policy,
            default_priority_rank=base.default_priority_rank,
        )

    def _request_targets(self, task: Any, *, token: str, ctx: Any) -> tuple[tuple[str, HttpRequestSpec], ...]:
        if str(getattr(task, "endpoint_id", "")) != self.universe_endpoint_id:
            return super()._request_targets(task, token=token, ctx=ctx)
        days = _date_range_values(getattr(task, "date_range_start", None), getattr(task, "date_range_end", None))
        return tuple(
            (
                f"trade_date:{day.strftime('%Y%m%d')}",
                self._trade_date_request_for(task, trade_date=day, token=token, ctx=ctx),
            )
            for day in days
        )

    def _empty_target_error(self, task: Any) -> str:
        if str(getattr(task, "endpoint_id", "")) == self.universe_endpoint_id:
            return "trade_date_required"
        return super()._empty_target_error(task)

    def _request_for(self, task: Any, *, symbol: str, token: str, ctx: Any) -> HttpRequestSpec:
        host, path = _endpoint(ctx, self.credential_name, self.default_endpoint)
        params: dict[str, Any] = {"ts_code": symbol}
        start = _yyyymmdd(getattr(task, "date_range_start", None))
        end = _yyyymmdd(getattr(task, "date_range_end", None))
        if start:
            params["start_date"] = start
        if end:
            params["end_date"] = end
        body = {
            "api_name": "daily",
            "token": token,
            "params": params,
            "fields": "ts_code,trade_date,open,high,low,close,vol,amount",
        }
        return HttpRequestSpec(
            method="POST",
            host=host,
            path=path,
            body=json.dumps(body, ensure_ascii=True, separators=(",", ":")),
            headers={"accept": "application/json", "content-type": "application/json"},
            provider_config_version=getattr(task, "provider_config_version", None),
        )

    def _trade_date_request_for(self, task: Any, *, trade_date: date, token: str, ctx: Any) -> HttpRequestSpec:
        host, path = _endpoint(ctx, self.credential_name, self.default_endpoint)
        body = {
            "api_name": "daily",
            "token": token,
            "params": {"trade_date": trade_date.strftime("%Y%m%d")},
            "fields": "ts_code,trade_date,open,high,low,close,vol,amount",
        }
        return HttpRequestSpec(
            method="POST",
            host=host,
            path=path,
            body=json.dumps(body, ensure_ascii=True, separators=(",", ":")),
            headers={"accept": "application/json", "content-type": "application/json"},
            provider_config_version=getattr(task, "provider_config_version", None),
        )

    def _rows_from_payload(self, payload: Any, *, task: Any, symbol: str) -> list[dict[str, Any]] | None:
        del task
        if not isinstance(payload, Mapping):
            return None
        code = payload.get("code")
        if code not in (0, "0", None):
            return None
        data = payload.get("data")
        if not isinstance(data, Mapping):
            return []
        fields = data.get("fields")
        items = data.get("items")
        if not isinstance(fields, Sequence) or isinstance(fields, (str, bytes, bytearray)):
            return None
        if not isinstance(items, Sequence) or isinstance(items, (str, bytes, bytearray)):
            return None
        rows: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, Sequence) or isinstance(item, (str, bytes, bytearray)):
                continue
            mapped = dict(zip((str(field) for field in fields), item, strict=False))
            period = _parse_yyyymmdd(mapped.get("trade_date"))
            values = _ohlcv(
                mapped.get("open"),
                mapped.get("high"),
                mapped.get("low"),
                mapped.get("close"),
                mapped.get("vol"),
            )
            if period is None or values is None:
                continue
            row = self._base_row(symbol=str(mapped.get("ts_code") or symbol), period=period, **values)
            row["date"] = period
            row["exchange"] = _cn_a_exchange(row["symbol_id"])
            if mapped.get("amount") is not None:
                # Tushare daily.amount is reported in thousand CNY; normalized daily_bar.amount is CNY.
                row["amount"] = _decimal_float(mapped.get("amount")) * 1000.0
                row["amount_unit"] = "CNY"
            rows.append(row)
        return rows


class AlphaVantageDailyBarPlugin(_HttpDailyBarPlugin):
    plugin_id = "us_primary"
    market = "US"
    credential_name = "data_source:alpha_vantage"
    default_endpoint = "https://www.alphavantage.co"
    source_role = "paid_data"
    _currency = "USD"
    _timezone = "America/New_York"
    _calendar = "US_NYSE_NASDAQ"

    def _request_for(self, task: Any, *, symbol: str, token: str, ctx: Any) -> HttpRequestSpec:
        host, path = _endpoint(ctx, self.credential_name, self.default_endpoint)
        query = {
            "function": "TIME_SERIES_DAILY_ADJUSTED",
            "symbol": symbol,
            "outputsize": _alpha_vantage_outputsize(task),
            "apikey": token,
        }
        return HttpRequestSpec(
            method="GET",
            host=host,
            path=f"{path}/query",
            query=query,
            headers={"accept": "application/json"},
            provider_config_version=getattr(task, "provider_config_version", None),
        )

    def _rows_from_payload(self, payload: Any, *, task: Any, symbol: str) -> list[dict[str, Any]] | None:
        if not isinstance(payload, Mapping):
            return None
        if _provider_payload_has_error(payload):
            return None
        series = payload.get("Time Series (Daily)")
        if not isinstance(series, Mapping):
            return []
        start = _date_value(getattr(task, "date_range_start", None))
        end = _date_value(getattr(task, "date_range_end", None))
        rows: list[dict[str, Any]] = []
        for day_text, item in series.items():
            period = _date_value(day_text)
            if period is None or (start and period < start) or (end and period > end):
                continue
            if not isinstance(item, Mapping):
                continue
            values = _ohlcv(
                item.get("1. open"),
                item.get("2. high"),
                item.get("3. low"),
                item.get("4. close"),
                item.get("6. volume") or item.get("5. volume"),
            )
            if values is None:
                continue
            rows.append(self._base_row(symbol=symbol, period=period, **values))
        return rows


def _alpha_vantage_outputsize(task: Any) -> str:
    start = _date_value(getattr(task, "date_range_start", None))
    end = _date_value(getattr(task, "date_range_end", None)) or datetime.now(tz=UTC).date()
    if start is not None and (end - start).days > 100:
        return "full"
    return "compact"


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


def _endpoint(ctx: Any, name: str, default: str) -> tuple[str, str]:
    resolver = getattr(ctx, "credential_resolver", None)
    getter = getattr(resolver, "get_endpoint_url", None)
    endpoint = _non_empty(getter(name)) if callable(getter) else None
    parsed = urlparse(endpoint or default)
    if not parsed.scheme or not parsed.netloc:
        return str(endpoint or default).rstrip("/"), ""
    return f"{parsed.scheme}://{parsed.netloc}", parsed.path.rstrip("/")


def _provider_payload_has_error(payload: Mapping[str, Any]) -> bool:
    for key in ("error", "errors", "error_message", "Error Message", "Note", "Information"):
        value = payload.get(key)
        if _non_empty(value):
            return True
    for key in ("message", "detail"):
        message = _non_empty(payload.get(key))
        if message and message.lower() not in {"ok", "success", "success."}:
            return True
    status = payload.get("status")
    if isinstance(status, str) and status.lower() not in {"", "ok", "success"}:
        return True
    code = payload.get("code")
    if code not in (None, 0, "0"):
        return True
    return False


def _ohlcv(open_: Any, high: Any, low: Any, close: Any, volume: Any) -> dict[str, float] | None:
    try:
        return {
            "open_": _decimal_float(open_),
            "high": _decimal_float(high),
            "low": _decimal_float(low),
            "close": _decimal_float(close),
            "volume": _decimal_float(volume),
        }
    except (TypeError, ValueError, InvalidOperation):
        return None


def _decimal_float(value: Any) -> float:
    return float(Decimal(str(value)))


def _yyyymmdd(value: Any) -> str | None:
    day = _date_value(value)
    return day.strftime("%Y%m%d") if day else None


def _date_range_values(start_value: Any, end_value: Any) -> tuple[date, ...]:
    start = _date_value(start_value)
    end = _date_value(end_value) or start
    if start is None or end is None or start > end:
        return ()
    days: list[date] = []
    current = start
    while current <= end:
        days.append(current)
        current += timedelta(days=1)
    return tuple(days)


def _parse_yyyymmdd(value: Any) -> date | None:
    text = _non_empty(value)
    if text is None or len(text) < 8:
        return None
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def _date_value(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    text = _non_empty(value)
    if text is None:
        return None
    if text.isdigit():
        number = int(text)
        if number > 10_000_000_000:
            return datetime.fromtimestamp(number / 1000, tz=UTC).date()
    return date.fromisoformat(text[:10])


def _cn_a_exchange(symbol: str) -> str | None:
    if symbol.endswith(".SH"):
        return "XSHG"
    if symbol.endswith(".SZ"):
        return "XSHE"
    if symbol.endswith(".BJ"):
        return "BJSE"
    return None


def _non_empty(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


__all__ = [
    "TushareDailyBarPlugin",
    "AlphaVantageDailyBarPlugin",
]
