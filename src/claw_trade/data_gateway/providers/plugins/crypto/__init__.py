from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any, Mapping, Sequence
from urllib.parse import urlparse
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
from claw_trade.data_gateway.providers.plugins.market_http import (
    METADATA_ONLY_LICENSE,
    NO_CREDENTIALS,
    credential_value,
    endpoint,
    endpoint_capability,
    first_symbol,
    managed_http,
    non_empty,
    parse_datetime,
    result_from_capture_error,
    send_json_request,
)
from claw_trade.instruments.resolver import resolve_crypto_provider_symbols


class BinanceSpotDailyBarPlugin:
    plugin_id = "crypto_primary"
    version = "1.0.0"
    _host = "data-api.binance.vision"

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
                    endpoint_id="spot_daily_bar",
                    market="CRYPTO",
                    data_type="daily_bar",
                    source_role="official",
                    granularity=("daily",),
                    fields=("open", "high", "low", "close", "volume", "amount"),
                    freshness_supported=("trading_day",),
                    http_visibility="managed_http",
                    batch_policy=BatchPolicy(
                        supports_batch=True,
                        batch_by="symbol",
                        max_symbols_per_call=20,
                        max_days_per_call=1000,
                        mergeable_fields=("open", "high", "low", "close", "volume", "amount"),
                    ),
                    priority_rank=20,
                    rate_limit_policy={"window_seconds": 60, "max_calls": 60},
                    license_policy=license_policy,
                ),
            ),
            credential_policy=CredentialPolicy(
                credential_required=False,
                credential_names=(),
                credential_scope=None,
                missing_behavior="credential_missing",
            ),
            license_policy=license_policy,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 60},
            default_priority_rank=20,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        managed_http = getattr(ctx, "managed_http", None)
        if managed_http is None or not callable(getattr(managed_http, "send_capture", None)):
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))

        symbols = tuple(str(item).strip().upper().replace("/", "") for item in getattr(task, "symbol_ids", ()) if str(item).strip())
        if not symbols:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))

        rows: list[dict[str, Any]] = []
        observations: list[Any] = []
        for symbol in symbols:
            base_asset, quote_asset = _split_symbol(symbol)
            if not base_asset or not quote_asset:
                return FetchResult.from_error(task, status="error", error=RuntimeError(f"unsupported_crypto_symbol:{symbol}"))

            capture = managed_http.send_capture(_request_for(task, symbol=symbol, ctx=ctx))
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
            payload = capture.json_payload
            if not isinstance(payload, list):
                return FetchResult.from_error(
                    task,
                    status="error",
                    error=RuntimeError("invalid_binance_klines_payload"),
                    http_observations=tuple(observations),
                )
            for item in payload:
                row = _kline_to_row(item, symbol=symbol, base_asset=base_asset, quote_asset=quote_asset)
                if row is not None:
                    rows.append(row)

        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(
            task,
            payload={"rows": rows},
            row_count=len(rows),
            http_observations=tuple(observations),
        )


class BinanceSpotMarketPlugin:
    plugin_id = "crypto_binance_spot_market"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="ticker_24hr",
                    market="CRYPTO",
                    data_type="quote_snapshot",
                    source_role="official",
                    granularity=("realtime",),
                    fields=("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id"),
                    priority_rank=10,
                ),
                endpoint_capability(
                    endpoint_id="order_book_depth",
                    market="CRYPTO",
                    data_type="order_book_snapshot",
                    source_role="official",
                    granularity=("realtime",),
                    fields=("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id"),
                    priority_rank=10,
                ),
                endpoint_capability(
                    endpoint_id="spot_intraday_bar",
                    market="CRYPTO",
                    data_type="intraday_bar",
                    source_role="official",
                    granularity=("1h", "1m"),
                    fields=("open", "high", "low", "close", "volume", "amount"),
                    priority_rank=15,
                ),
            ),
            credential_policy=NO_CREDENTIALS,
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 60},
            default_priority_rank=10,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        symbol = _crypto_provider_symbol(first_symbol(task))
        if symbol is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("symbol_required"))
        endpoint_id = str(getattr(task, "endpoint_id", ""))
        if endpoint_id == "ticker_24hr":
            return self._fetch_ticker(task, ctx=ctx, symbol=symbol)
        if endpoint_id == "order_book_depth":
            return self._fetch_order_book(task, ctx=ctx, symbol=symbol)
        if endpoint_id == "spot_intraday_bar":
            return self._fetch_intraday(task, ctx=ctx, symbol=symbol)
        return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))

    def _fetch_ticker(self, task: Any, *, ctx: Any, symbol: str) -> FetchResult:
        base_asset, quote_asset = _split_symbol(symbol)
        if not base_asset or not quote_asset:
            return FetchResult.from_error(task, status="error", error=RuntimeError(f"unsupported_crypto_symbol:{symbol}"))
        host, prefix = _endpoint_from_settings(ctx, default_host=BinanceSpotDailyBarPlugin._host)
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/api/v3/ticker/24hr",
                query={"symbol": symbol},
                headers={"accept": "application/json"},
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        if not isinstance(payload, Mapping):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        row = _crypto_base_row(
            dataset="quote_snapshot",
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            provider_id=self.plugin_id,
            endpoint_id="ticker_24hr",
            source_role="official",
            granularity="realtime",
        )
        row.update(
            {
                "price": _decimal_float(payload.get("lastPrice")),
                "change": _decimal_float(payload.get("priceChange")),
                "change_pct": _decimal_float(payload.get("priceChangePercent")),
                "volume": _decimal_float(payload.get("volume")),
                "amount": _decimal_float(payload.get("quoteVolume")),
                "timestamp": parse_datetime(payload.get("closeTime")),
            }
        )
        return FetchResult.from_success(task, payload={"rows": [row]}, row_count=1, http_observations=observations)

    def _fetch_order_book(self, task: Any, *, ctx: Any, symbol: str) -> FetchResult:
        base_asset, quote_asset = _split_symbol(symbol)
        if not base_asset or not quote_asset:
            return FetchResult.from_error(task, status="error", error=RuntimeError(f"unsupported_crypto_symbol:{symbol}"))
        host, prefix = _endpoint_from_settings(ctx, default_host=BinanceSpotDailyBarPlugin._host)
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/api/v3/depth",
                query={"symbol": symbol, "limit": 5},
                headers={"accept": "application/json"},
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        if not isinstance(payload, Mapping):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        bid = _first_level(payload.get("bids"))
        ask = _first_level(payload.get("asks"))
        if bid is None or ask is None:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        row = _crypto_base_row(
            dataset="order_book_snapshot",
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            provider_id=self.plugin_id,
            endpoint_id="order_book_depth",
            source_role="official",
            granularity="realtime",
        )
        row.update(
            {
                "bid_price": bid[0],
                "bid_size": bid[1],
                "ask_price": ask[0],
                "ask_size": ask[1],
                "timestamp": datetime.now(tz=UTC),
            }
        )
        return FetchResult.from_success(task, payload={"rows": [row]}, row_count=1, http_observations=observations)

    def _fetch_intraday(self, task: Any, *, ctx: Any, symbol: str) -> FetchResult:
        base_asset, quote_asset = _split_symbol(symbol)
        if not base_asset or not quote_asset:
            return FetchResult.from_error(task, status="error", error=RuntimeError(f"unsupported_crypto_symbol:{symbol}"))
        host, prefix = _endpoint_from_settings(ctx, default_host=BinanceSpotDailyBarPlugin._host)
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/api/v3/klines",
                query={"symbol": symbol, "interval": "1h", "limit": 1000},
                headers={"accept": "application/json"},
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        if not isinstance(payload, list):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        rows = [
            row
            for item in payload
            if (
                row := _kline_to_row(
                    item,
                    symbol=symbol,
                    base_asset=base_asset,
                    quote_asset=quote_asset,
                    granularity="1h",
                )
            )
            is not None
        ]
        for row in rows:
            row["dataset"] = "intraday_bar"
            row["granularity"] = "1h"
            row["provider_lineage"] = {"provider_id": self.plugin_id, "endpoint_id": "spot_intraday_bar"}
            row["schema_id"] = "intraday_bar.v1"
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)


class CoinGeckoCryptoPlugin:
    plugin_id = "crypto_coingecko_market"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="coins_markets",
                    market="CRYPTO",
                    data_type="valuation_metric",
                    source_role="built_in_public",
                    granularity=("realtime",),
                    fields=(
                        "price",
                        "price_unit",
                        "market_cap",
                        "market_cap_unit",
                        "fdv",
                        "fdv_unit",
                        "circulating_supply",
                        "total_supply",
                        "supply_unit",
                        "volume",
                        "volume_unit",
                    ),
                    priority_rank=20,
                ),
            ),
            credential_policy=NO_CREDENTIALS,
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": None},
            default_priority_rank=20,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        symbol = first_symbol(task)
        symbols = resolve_crypto_provider_symbols(symbol or "")
        coin_id = symbols.coingecko_coin_id or (symbol or "").lower()
        base_asset = symbols.crypto_base_symbol or (symbol or "").upper()
        quote_asset = symbols.crypto_quote_symbol or "USDT"
        host, prefix = endpoint(ctx, "data_source:coingecko", "https://api.coingecko.com/api/v3")
        token = credential_value(ctx, "data_source:coingecko_pro") or credential_value(ctx, "data_source:coingecko")
        headers = {"accept": "application/json"}
        if token:
            headers["x-cg-pro-api-key" if "pro-api" in host else "x-cg-demo-api-key"] = token
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/coins/markets",
                query={"vs_currency": "usd", "ids": coin_id, "per_page": 1, "page": 1},
                headers=headers,
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, bytearray)) or not payload:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        item = payload[0]
        if not isinstance(item, Mapping):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        row = _crypto_base_row(
            dataset="valuation_metric",
            symbol=symbol or symbols.crypto_provider_symbol or base_asset,
            base_asset=base_asset,
            quote_asset=quote_asset,
            provider_id=self.plugin_id,
            endpoint_id="coins_markets",
            source_role="built_in_public",
            granularity="realtime",
        )
        row.update(
            {
                "price": _decimal_float(item.get("current_price")),
                "price_unit": "USD",
                "market_cap": _decimal_float(item.get("market_cap")),
                "market_cap_unit": "USD",
                "fdv": _decimal_float(item.get("fully_diluted_valuation")),
                "fdv_unit": "USD",
                "circulating_supply": _decimal_float(item.get("circulating_supply")),
                "total_supply": _decimal_float(item.get("total_supply")),
                "supply_unit": base_asset,
                "volume": _decimal_float(item.get("total_volume")),
                "volume_unit": "USD",
                "timestamp": parse_datetime(item.get("last_updated")),
            }
        )
        return FetchResult.from_success(task, payload={"rows": [row]}, row_count=1, http_observations=observations)


class DefiLlamaCryptoPlugin:
    plugin_id = "crypto_defillama_defi"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="protocol",
                    market="CRYPTO",
                    data_type="defi_metric",
                    source_role="built_in_public",
                    granularity=("daily", "realtime"),
                    fields=("tvl", "chains", "category", "symbol_id"),
                    priority_rank=20,
                ),
            ),
            credential_policy=NO_CREDENTIALS,
            license_policy=METADATA_ONLY_LICENSE,
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 30},
            default_priority_rank=20,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        symbol = first_symbol(task)
        symbols = resolve_crypto_provider_symbols(symbol or "")
        slug = symbols.defillama_protocol_slug
        if not slug:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("defillama_slug_missing"))
        base_asset = symbols.crypto_base_symbol or (symbol or "").upper()
        quote_asset = symbols.crypto_quote_symbol or "USDT"
        host, prefix = endpoint(ctx, "data_source:defillama", "https://api.llama.fi")
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/protocol/{slug}",
                headers={"accept": "application/json"},
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        if not isinstance(payload, Mapping):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        row = _crypto_base_row(
            dataset="defi_metric",
            symbol=symbol or base_asset,
            base_asset=base_asset,
            quote_asset=quote_asset,
            provider_id=self.plugin_id,
            endpoint_id="protocol",
            source_role="built_in_public",
            granularity="realtime",
        )
        row.update(
            {
                "tvl": _defillama_tvl(payload),
                "chains": tuple(payload.get("chains") or ()) if isinstance(payload.get("chains"), Sequence) and not isinstance(payload.get("chains"), (str, bytes, bytearray)) else (),
                "category": non_empty(payload.get("category")),
            }
        )
        return FetchResult.from_success(task, payload={"rows": [row]}, row_count=1, http_observations=observations)


class CoinglassCryptoPlugin:
    plugin_id = "crypto_coinglass_derivatives"
    version = "1.0.0"
    credential_name = "data_source:coinglass"
    _endpoint_specs: tuple[dict[str, Any], ...] = (
        {
            "endpoint_id": "futures_open_interest",
            "data_type": "crypto_derivative_metric",
            "source_role": "paid_data",
            "granularity": ("realtime",),
            "fields": ("open_interest", "open_interest_unit", "timestamp", "symbol_id"),
            "priority_rank": 10,
            "path": "/api/futures/open-interest/exchange-list",
            "symbol_mode": "asset",
            "metric_keys": (
                "open_interest_usd",
                "sumOpenInterestValue",
                "openInterestUsd",
                "openInterestValue",
                "open_interest_value",
                "value_usd",
                "open_interest",
                "open_interest_quantity",
                "openInterest",
                "sumOpenInterest",
                "oi",
                "close",
                "value",
            ),
            "metric_field": "open_interest",
            "metric_unit": "USD",
        },
        {
            "endpoint_id": "futures_funding_rate",
            "data_type": "crypto_derivative_metric",
            "source_role": "paid_data",
            "granularity": ("1h", "daily"),
            "fields": ("funding_rate", "funding_rate_unit", "timestamp", "symbol_id"),
            "priority_rank": 10,
            "path": "/api/futures/funding-rate/oi-weight-history",
            "symbol_mode": "asset",
            "requires_interval": True,
            "metric_keys": ("funding_rate", "fundingRate", "rate", "close", "value"),
            "metric_field": "funding_rate",
            "metric_unit": "percent",
        },
        {
            "endpoint_id": "futures_long_short_ratio",
            "data_type": "crypto_derivative_metric",
            "source_role": "paid_data",
            "granularity": ("1h", "daily"),
            "fields": ("long_short_ratio", "timestamp", "symbol_id"),
            "priority_rank": 10,
            "path": "/api/futures/global-long-short-account-ratio/history",
            "symbol_mode": "contract",
            "requires_exchange": True,
            "requires_interval": True,
            "metric_keys": (
                "long_short_ratio",
                "longShortRatio",
                "global_account_long_short_ratio",
                "longAccount",
                "longShort",
                "ratio",
                "close",
                "value",
            ),
            "metric_field": "long_short_ratio",
        },
        {
            "endpoint_id": "futures_taker_buy_sell",
            "data_type": "crypto_derivative_metric",
            "source_role": "paid_data",
            "granularity": ("1h", "daily"),
            "fields": ("taker_buy_volume", "taker_sell_volume", "taker_volume_unit", "taker_buy_sell_ratio", "timestamp", "symbol_id"),
            "priority_rank": 12,
            "path": "/api/futures/aggregated-taker-buy-sell-volume/history",
            "symbol_mode": "asset",
            "requires_exchange_list": True,
            "requires_interval": True,
            "metric_kind": "taker",
        },
        {
            "endpoint_id": "futures_liquidation",
            "data_type": "crypto_derivative_metric",
            "source_role": "paid_data",
            "granularity": ("1h", "daily"),
            "fields": ("long_liquidation", "short_liquidation", "liquidation_value", "liquidation_value_unit", "timestamp", "symbol_id"),
            "priority_rank": 12,
            "path": "/api/futures/liquidation/aggregated-history",
            "symbol_mode": "asset",
            "requires_exchange_list": True,
            "requires_interval": True,
            "metric_kind": "liquidation",
        },
        {
            "endpoint_id": "futures_liquidation_heatmap",
            "data_type": "crypto_derivative_metric",
            "source_role": "paid_data",
            "granularity": ("1h",),
            "fields": ("liquidation_price", "liquidation_price_unit", "liquidation_size", "liquidation_size_unit", "side", "timestamp", "symbol_id"),
            "priority_rank": 11,
            "path": "/api/futures/liquidation/heatmap/model1",
            "symbol_mode": "contract",
            "requires_exchange": True,
            "requires_range": True,
            "metric_kind": "liquidation_heatmap",
        },
        {
            "endpoint_id": "options_open_interest",
            "data_type": "crypto_derivative_metric",
            "source_role": "paid_data",
            "granularity": ("1h",),
            "fields": ("options_open_interest", "options_volume", "timestamp", "symbol_id"),
            "priority_rank": 11,
            "path": "/api/option/exchange-oi-history",
            "symbol_mode": "asset",
            "requires_range": True,
            "metric_kind": "options",
        },
        {
            "endpoint_id": "spot_cvd_history",
            "data_type": "crypto_derivative_metric",
            "source_role": "paid_data",
            "granularity": ("1h",),
            "fields": ("cvd", "taker_buy_volume", "taker_sell_volume", "taker_volume_unit", "timestamp", "symbol_id"),
            "priority_rank": 11,
            "path": "/api/spot/cvd/history",
            "symbol_mode": "contract",
            "requires_exchange": True,
            "requires_interval": True,
            "requires_unit": True,
            "metric_kind": "cvd",
        },
        {
            "endpoint_id": "etf_flow_history",
            "data_type": "crypto_derivative_metric",
            "source_role": "paid_data",
            "granularity": ("daily",),
            "fields": ("etf_flow_usd", "price", "timestamp", "symbol_id"),
            "priority_rank": 11,
            "path_by_asset": {"BTC": "/api/etf/bitcoin/flow-history", "ETH": "/api/etf/ethereum/flow-history", "XRP": "/api/etf/xrp/flow-history"},
            "symbol_mode": "asset",
            "metric_kind": "etf_flow",
        },
        {
            "endpoint_id": "futures_price_history",
            "data_type": "daily_bar",
            "source_role": "paid_data",
            "granularity": ("daily",),
            "fields": ("open", "high", "low", "close", "volume", "amount"),
            "priority_rank": 35,
            "path": "/api/futures/price/history",
            "symbol_mode": "contract",
            "requires_exchange": True,
            "requires_interval": True,
        },
        {
            "endpoint_id": "spot_price_history",
            "data_type": "daily_bar",
            "source_role": "paid_data",
            "granularity": ("daily",),
            "fields": ("open", "high", "low", "close", "volume", "amount"),
            "priority_rank": 35,
            "path": "/api/spot/price/history",
            "symbol_mode": "contract",
            "requires_exchange": True,
            "requires_interval": True,
        },
        {
            "endpoint_id": "futures_order_book",
            "data_type": "order_book_snapshot",
            "source_role": "paid_data",
            "granularity": ("1h",),
            "fields": ("bid_price", "bid_size", "ask_price", "ask_size", "timestamp"),
            "priority_rank": 20,
            "path": "/api/futures/orderbook/history",
            "symbol_mode": "contract",
            "requires_exchange": True,
            "requires_interval": True,
        },
        {
            "endpoint_id": "spot_order_book",
            "data_type": "order_book_snapshot",
            "source_role": "paid_data",
            "granularity": ("1h",),
            "fields": ("bid_price", "bid_size", "ask_price", "ask_size", "timestamp"),
            "priority_rank": 20,
            "path": "/api/spot/orderbook/history",
            "symbol_mode": "contract",
            "requires_exchange": True,
            "requires_interval": True,
        },
        {
            "endpoint_id": "onchain_exchange_balance",
            "data_type": "crypto_onchain_metric",
            "source_role": "paid_data",
            "granularity": ("daily",),
            "fields": ("timestamp", "metric", "value", "value_unit", "chain"),
            "priority_rank": 12,
            "path": "/api/exchange/balance/chart",
            "symbol_mode": "asset",
            "metric": "exchange_balance",
            "metric_keys": ("balance", "value", "amount"),
        },
        {
            "endpoint_id": "onchain_whale_transfer",
            "data_type": "crypto_onchain_metric",
            "source_role": "paid_data",
            "granularity": ("event",),
            "fields": ("timestamp", "metric", "value", "value_unit", "chain"),
            "priority_rank": 14,
            "path": "/api/chain/v2/whale-transfer",
            "symbol_mode": "asset",
            "metric": "whale_transfer",
            "metric_keys": ("amount_usd", "amountUsd", "asset_quantity", "value", "amount"),
        },
        {
            "endpoint_id": "spot_coin_netflow",
            "data_type": "crypto_onchain_metric",
            "source_role": "paid_data",
            "granularity": ("realtime",),
            "fields": ("timestamp", "metric", "value", "value_unit", "chain"),
            "priority_rank": 16,
            "path": "/api/spot/coin/netflow",
            "symbol_mode": "asset",
            "requires_exchange_list": True,
            "metric": "spot_coin_netflow",
            "metric_keys": (
                "net_inflow",
                "netInflow",
                "netFlow",
                "net_flow_usd_1h",
                "net_flow_usd_24h",
                "net_flow_usd_7d",
                "value",
            ),
        },
        {
            "endpoint_id": "futures_coin_netflow",
            "data_type": "crypto_derivative_metric",
            "source_role": "paid_data",
            "granularity": ("realtime",),
            "fields": ("net_inflow", "net_inflow_unit", "timestamp", "symbol_id"),
            "priority_rank": 16,
            "path": "/api/futures/coin/netflow",
            "symbol_mode": "asset",
            "requires_exchange_list": True,
            "metric_keys": (
                "net_inflow",
                "netInflow",
                "netFlow",
                "net_flow_usd_1h",
                "net_flow_usd_24h",
                "net_flow_usd_7d",
                "value",
            ),
            "metric_field": "net_inflow",
            "metric_unit": "USD",
        },
        {
            "endpoint_id": "bitcoin_ahr999",
            "data_type": "crypto_onchain_metric",
            "source_role": "paid_data",
            "granularity": ("daily",),
            "fields": ("timestamp", "metric", "value", "value_unit", "chain"),
            "priority_rank": 18,
            "path": "/api/index/ahr999",
            "symbol_mode": "btc_only",
            "metric": "ahr999",
            "metric_unit": "dimensionless",
            "metric_keys": ("ahr999", "ahr999Index", "ahr999_value", "index", "current_value", "value"),
        },
    )

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=tuple(
                endpoint_capability(
                    endpoint_id=str(spec["endpoint_id"]),
                    market="CRYPTO",
                    data_type=str(spec["data_type"]),
                    source_role=str(spec["source_role"]),
                    granularity=tuple(spec["granularity"]),
                    fields=tuple(spec["fields"]),
                    priority_rank=int(spec["priority_rank"]),
                )
                for spec in self._endpoint_specs
            ),
            credential_policy=CredentialPolicy(
                credential_required=True,
                credential_names=(self.credential_name,),
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
        token = credential_value(ctx, self.credential_name)
        if token is None:
            return FetchResult.from_error(task, status="credential_missing", error=RuntimeError(f"credential_missing:{self.credential_name}"))
        symbol = first_symbol(task)
        symbols = resolve_crypto_provider_symbols(symbol or "")
        endpoint_id = str(getattr(task, "endpoint_id", "futures_open_interest"))
        spec = self._spec(endpoint_id)
        if spec is None:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"unsupported_endpoint:{endpoint_id}"))
        asset = symbols.coinglass_asset_symbol or symbols.crypto_base_symbol
        contract = symbols.coinglass_contract_symbol or symbols.crypto_provider_symbol
        if not asset:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("coinglass_asset_missing"))
        if spec.get("symbol_mode") == "btc_only" and asset.upper() != "BTC":
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("coinglass_btc_only_indicator"))
        if spec.get("symbol_mode") == "contract" and not contract:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("coinglass_contract_missing"))
        path = _coinglass_path(spec, asset=asset)
        if path is None:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError(f"coinglass_endpoint_not_supported_for_asset:{endpoint_id}:{asset}"))
        host, prefix = self._endpoint(ctx)
        header_name = _coinglass_header_name(ctx, self.credential_name)
        rows_list: list[dict[str, Any]] = []
        observations_list: list[Any] = []
        for query in _coinglass_queries(task, spec=spec, asset=asset, contract=contract):
            payload, observations, error = send_json_request(
                task,
                ctx,
                HttpRequestSpec(
                    method="GET",
                    host=host,
                    path=f"{prefix}{path}",
                    query=query,
                    headers={"accept": "application/json", header_name: token},
                    provider_config_version=getattr(task, "provider_config_version", None),
                ),
            )
            observations_list.extend(observations)
            if error is not None:
                return error
            rows_list.extend(
                _coinglass_rows_from_payload(
                    payload,
                    task,
                    spec=spec,
                    symbol=symbol or contract or asset,
                    asset=asset,
                    quote_asset=symbols.crypto_quote_symbol or "USDT",
                )
            )
        rows = _dedupe_coinglass_rows(rows_list)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=tuple(observations_list))
        return FetchResult.from_success(task, payload={"rows": list(rows)}, row_count=len(rows), http_observations=tuple(observations_list))

    @classmethod
    def _spec(cls, endpoint_id: str) -> dict[str, Any] | None:
        for spec in cls._endpoint_specs:
            if spec["endpoint_id"] == endpoint_id:
                return spec
        return None

    def _endpoint(self, ctx: Any) -> tuple[str, str]:
        host, prefix = endpoint(ctx, self.credential_name, "https://open-api-v4.coinglass.com")
        if _is_keystore_coinglass_proxy(host, prefix) and not prefix.endswith("/v4") and "/v4/" not in prefix:
            prefix = f"{prefix}/v4"
        return host, prefix


def _coinglass_header_name(ctx: Any, credential_name: str) -> str:
    resolver = getattr(ctx, "credential_resolver", None)
    getter = getattr(resolver, "get_header_name", None)
    configured = non_empty(getter(credential_name)) if callable(getter) else None
    return configured or "CG-API-KEY"


def _is_keystore_coinglass_proxy(host: str, prefix: str) -> bool:
    target = f"{host}{prefix}".lower()
    return "proxy.keystore.com.cn" in target and "/proxy/coinglass" in target


def _coinglass_path(spec: Mapping[str, Any], *, asset: str) -> str | None:
    path_by_asset = spec.get("path_by_asset")
    if isinstance(path_by_asset, Mapping):
        return non_empty(path_by_asset.get(asset.upper()))
    return non_empty(spec.get("path"))


def _coinglass_query(task: Any, *, spec: Mapping[str, Any], asset: str, contract: str | None) -> dict[str, Any]:
    params = getattr(task, "params", {}) if isinstance(getattr(task, "params", {}), Mapping) else {}
    query: dict[str, Any] = {}
    symbol_mode = str(spec.get("symbol_mode", "asset"))
    if symbol_mode == "contract":
        query["symbol"] = contract or asset
    elif symbol_mode == "asset":
        query["symbol"] = asset
    if spec.get("requires_exchange"):
        query["exchange"] = non_empty(params.get("exchange")) or "Binance"
    if spec.get("requires_exchange_list"):
        query["exchange_list"] = non_empty(params.get("exchange_list")) or "Binance,OKX,Bybit"
    if spec.get("requires_interval"):
        query["interval"] = _coinglass_interval(task)
    if spec.get("requires_range"):
        query["range"] = non_empty(params.get("range")) or _coinglass_range(task)
    if spec.get("requires_unit"):
        query["unit"] = non_empty(params.get("unit")) or "usd"
    if spec.get("metric_kind") == "options":
        query["unit"] = non_empty(params.get("unit")) or ("USD" if asset.upper() in {"BTC", "ETH"} else "USD")
    start_ms = _start_millis(getattr(task, "date_range_start", None))
    end_ms = _end_millis(getattr(task, "date_range_end", None))
    if start_ms is not None:
        query["start_time"] = start_ms
    if end_ms is not None:
        query["end_time"] = end_ms
    if spec.get("requires_interval"):
        query["limit"] = non_empty(params.get("limit")) or _coinglass_limit(task)
    return query


def _coinglass_queries(task: Any, *, spec: Mapping[str, Any], asset: str, contract: str | None) -> tuple[dict[str, Any], ...]:
    query = _coinglass_query(task, spec=spec, asset=asset, contract=contract)
    if not _coinglass_should_chunk(task, spec=spec):
        return (query,)
    start = _as_date(getattr(task, "date_range_start", None))
    end = _as_date(getattr(task, "date_range_end", None))
    if start is None or end is None or start > end:
        return (query,)
    queries: list[dict[str, Any]] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=179), end)
        chunk_query = dict(query)
        chunk_query["start_time"] = _start_millis(chunk_start)
        chunk_query["end_time"] = _end_millis(chunk_end)
        queries.append(chunk_query)
        chunk_start = chunk_end + timedelta(days=1)
    return tuple(queries)


def _coinglass_should_chunk(task: Any, *, spec: Mapping[str, Any]) -> bool:
    if not spec.get("requires_interval"):
        return False
    endpoint_id = str(spec.get("endpoint_id") or "")
    if endpoint_id not in {
        "futures_funding_rate",
        "futures_long_short_ratio",
        "futures_taker_buy_sell",
        "futures_liquidation",
    }:
        return False
    params = getattr(task, "params", {}) if isinstance(getattr(task, "params", {}), Mapping) else {}
    if non_empty(params.get("limit")) or non_empty(params.get("start_time")) or non_empty(params.get("end_time")):
        return False
    start = _as_date(getattr(task, "date_range_start", None))
    end = _as_date(getattr(task, "date_range_end", None))
    return start is not None and end is not None and (end - start).days + 1 > 180


def _coinglass_limit(task: Any) -> str:
    del task
    return "100"


def _coinglass_range(task: Any) -> str:
    granularity = str(getattr(task, "granularity", "") or "").lower()
    if granularity == "daily":
        return "all"
    start = _as_date(getattr(task, "date_range_start", None))
    end = _as_date(getattr(task, "date_range_end", None))
    if start is not None and end is not None:
        span_days = max((end - start).days + 1, 1)
        if span_days <= 1:
            return "1d"
        if span_days <= 3:
            return "3d"
        if span_days <= 7:
            return "7d"
        if span_days <= 30:
            return "30d"
        if span_days <= 90:
            return "90d"
        if span_days <= 180:
            return "180d"
        if span_days <= 366:
            return "1y"
        return "all"
    return "3d"


def _coinglass_interval(task: Any) -> str:
    params = getattr(task, "params", {}) if isinstance(getattr(task, "params", {}), Mapping) else {}
    configured = non_empty(params.get("interval"))
    if configured:
        return configured
    granularity = str(getattr(task, "granularity", "") or "").lower()
    if granularity in {"1m", "5m", "15m", "30m", "1h", "4h", "12h", "1d"}:
        return "1d" if granularity == "daily" else granularity
    if granularity in {"daily", "trading_day"}:
        return "1d"
    return "1h"


def _coinglass_rows_from_payload(
    payload: Any,
    task: Any,
    *,
    spec: Mapping[str, Any],
    symbol: str,
    asset: str,
    quote_asset: str,
) -> tuple[dict[str, Any], ...]:
    dataset = str(spec["data_type"])
    metric_kind = str(spec.get("metric_kind") or "")
    if dataset == "crypto_derivative_metric" and metric_kind == "liquidation_heatmap":
        return _coinglass_heatmap_metric_rows(payload, task, spec=spec, symbol=symbol, asset=asset, quote_asset=quote_asset)
    if dataset == "crypto_derivative_metric" and metric_kind in {"options", "cvd", "etf_flow"}:
        return _coinglass_named_metric_rows(payload, task, spec=spec, symbol=symbol, asset=asset, quote_asset=quote_asset)
    if dataset == "order_book_snapshot":
        rows: list[dict[str, Any]] = []
        for levels in _coinglass_order_book_rows_from_payload(payload):
            row = _crypto_base_row(
                dataset=dataset,
                symbol=symbol,
                base_asset=asset,
                quote_asset=quote_asset,
                provider_id=CoinglassCryptoPlugin.plugin_id,
                endpoint_id=str(spec["endpoint_id"]),
                source_role=str(spec["source_role"]),
                granularity=str(getattr(task, "granularity", None) or tuple(spec["granularity"])[0]),
            )
            row.update({key: value for key, value in levels.items() if key != "timestamp"})
            timestamp = levels.get("timestamp")
            if not isinstance(timestamp, datetime):
                timestamp = datetime.now(tz=UTC)
            row["timestamp"] = timestamp
            _apply_crypto_period(row, timestamp)
            rows.append(row)
        if rows:
            return tuple(rows)
    else:
        data = _provider_data(payload)
        if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray, Mapping)):
            rows: list[dict[str, Any]] = []
            for item in data:
                if not isinstance(item, Mapping):
                    continue
                row = _coinglass_row_from_payload(
                    {"data": item},
                    task,
                    spec=spec,
                    symbol=symbol,
                    asset=asset,
                    quote_asset=quote_asset,
                )
                if row is not None:
                    rows.append(row)
            if rows:
                return tuple(rows)
    row = _coinglass_row_from_payload(payload, task, spec=spec, symbol=symbol, asset=asset, quote_asset=quote_asset)
    return (row,) if row is not None else ()


def _dedupe_coinglass_rows(rows: Sequence[Mapping[str, Any]]) -> tuple[dict[str, Any], ...]:
    deduped: list[dict[str, Any]] = []
    seen: set[tuple[tuple[str, str], ...]] = set()
    for row in rows:
        key = tuple(sorted((str(field), repr(value)) for field, value in row.items()))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(dict(row))
    return tuple(deduped)


def _coinglass_row_from_payload(
    payload: Any,
    task: Any,
    *,
    spec: Mapping[str, Any],
    symbol: str,
    asset: str,
    quote_asset: str,
) -> dict[str, Any] | None:
    dataset = str(spec["data_type"])
    endpoint_id = str(spec["endpoint_id"])
    row = _crypto_base_row(
        dataset=dataset,
        symbol=symbol,
        base_asset=asset,
        quote_asset=quote_asset,
        provider_id=CoinglassCryptoPlugin.plugin_id,
        endpoint_id=endpoint_id,
        source_role=str(spec["source_role"]),
        granularity=str(getattr(task, "granularity", None) or tuple(spec["granularity"])[0]),
    )
    item = _first_mapping(_provider_data(payload))
    if dataset in {"daily_bar", "intraday_bar"}:
        ohlc = _coinglass_ohlc_values(item)
        if ohlc is None:
            return None
        row.update(ohlc)
        timestamp = _coinglass_timestamp_or_none(item)
        if timestamp is not None:
            row["timestamp"] = timestamp
            _apply_crypto_period(row, timestamp)
        return row
    if dataset == "order_book_snapshot":
        levels = _coinglass_order_book_values_from_payload(payload)
        if levels is None:
            return None
        row.update({key: value for key, value in levels.items() if key != "timestamp"})
        row["timestamp"] = levels.get("timestamp") or _coinglass_timestamp(item)
        _apply_crypto_period(row, row["timestamp"])
        return row
    if dataset == "crypto_onchain_metric":
        value, source_field = _coinglass_onchain_value_with_key(payload, item, spec=spec)
        if value is None:
            return None
        timestamp = _coinglass_timestamp(item)
        row.update({"timestamp": timestamp, "metric": str(spec.get("metric", endpoint_id)), "value": value, "chain": _coinglass_chain(item)})
        if source_field:
            row["value_source_field"] = source_field
        value_unit = non_empty(spec.get("metric_unit")) or _coinglass_onchain_value_unit(
            str(spec.get("metric", endpoint_id)),
            source_field,
            base_asset=asset,
        )
        if value_unit == "base_asset":
            value_unit = asset.upper()
        if value_unit:
            row["value_unit"] = value_unit
        _apply_crypto_period(row, timestamp)
        return row
    if dataset == "crypto_derivative_metric":
        if spec.get("metric_kind") == "options":
            _set_optional_metric(row, "options_open_interest", item, ("options_open_interest", "open_interest", "openInterest", "oi", "sumOpenInterest", "value"))
            _set_optional_metric(row, "options_volume", item, ("options_volume", "volume", "vol", "sumVolume"))
        elif spec.get("metric_kind") == "cvd":
            _set_optional_metric(row, "cvd", item, ("cvd", "cumulativeVolumeDelta", "cumulative_volume_delta", "delta", "value", "close"))
            _set_optional_metric(row, "taker_buy_volume", item, ("taker_buy_volume", "takerBuyVolume", "buy_volume", "buyVolume", "buy"))
            _set_optional_metric(row, "taker_sell_volume", item, ("taker_sell_volume", "takerSellVolume", "sell_volume", "sellVolume", "sell"))
            if any(row.get(field) is not None for field in ("cvd", "taker_buy_volume", "taker_sell_volume")):
                row["taker_volume_unit"] = "USD"
        elif spec.get("metric_kind") == "etf_flow":
            _set_optional_metric(row, "etf_flow_usd", item, ("etf_flow_usd", "changeUsd", "change_usd", "netFlow", "net_flow", "flow", "value"))
            _set_optional_metric(row, "price", item, ("price", "closePrice", "close_price", "close"))
        elif spec.get("metric_kind") == "taker":
            _set_optional_metric(
                row,
                "taker_buy_volume",
                item,
                ("taker_buy_volume", "takerBuyVolume", "buy_volume", "buyVolume", "buy", "aggregated_buy_volume_usd"),
            )
            _set_optional_metric(
                row,
                "taker_sell_volume",
                item,
                ("taker_sell_volume", "takerSellVolume", "sell_volume", "sellVolume", "sell", "aggregated_sell_volume_usd"),
            )
            _set_optional_metric(row, "taker_buy_sell_ratio", item, ("taker_buy_sell_ratio", "buySellRatio", "ratio", "value"))
            buy = row.get("taker_buy_volume")
            sell = row.get("taker_sell_volume")
            if row.get("taker_buy_sell_ratio") is None and buy is not None and sell not in {None, 0}:
                row["taker_buy_sell_ratio"] = buy / sell
            if any(row.get(field) is not None for field in ("taker_buy_volume", "taker_sell_volume")):
                row["taker_volume_unit"] = "USD"
        elif spec.get("metric_kind") == "liquidation":
            _set_optional_metric(
                row,
                "long_liquidation",
                item,
                ("long_liquidation", "longLiquidation", "longLiquidationUsd", "longVolUsd", "aggregated_long_liquidation_usd"),
            )
            _set_optional_metric(
                row,
                "short_liquidation",
                item,
                ("short_liquidation", "shortLiquidation", "shortLiquidationUsd", "shortVolUsd", "aggregated_short_liquidation_usd"),
            )
            _set_optional_metric(row, "liquidation_value", item, ("liquidation_value", "liquidation", "liquidationUsd", "value"))
            long_value = row.get("long_liquidation")
            short_value = row.get("short_liquidation")
            if row.get("liquidation_value") is None and long_value is not None and short_value is not None:
                row["liquidation_value"] = long_value + short_value
            if any(row.get(field) is not None for field in ("long_liquidation", "short_liquidation", "liquidation_value")):
                row["liquidation_value_unit"] = "USD"
        else:
            field = str(spec.get("metric_field", "value"))
            value, source_field = _first_numeric_metric_with_key(_provider_data(payload), tuple(spec.get("metric_keys", (field, "value"))))
            if value is not None:
                row[field] = value
                if source_field:
                    row[f"{field}_source_field"] = source_field
                unit = _coinglass_metric_unit(field, source_field)
                if source_field is None:
                    unit = non_empty(spec.get("metric_unit")) or unit
                if unit:
                    row[f"{field}_unit"] = unit
        row["timestamp"] = _coinglass_timestamp(item)
        _apply_crypto_period(row, row["timestamp"])
        if not any(row.get(field) is not None for field in tuple(spec["fields"]) if field not in {"timestamp", "symbol_id"}):
            return None
        return row
    return None


def _coinglass_named_metric_rows(
    payload: Any,
    task: Any,
    *,
    spec: Mapping[str, Any],
    symbol: str,
    asset: str,
    quote_asset: str,
) -> tuple[dict[str, Any], ...]:
    data = _provider_data(payload)
    items = data if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray, Mapping)) else (data,)
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        row = _coinglass_row_from_payload({"data": item}, task, spec=spec, symbol=symbol, asset=asset, quote_asset=quote_asset)
        if row is not None:
            rows.append(row)
    if rows:
        return tuple(rows)
    row = _coinglass_row_from_payload(payload, task, spec=spec, symbol=symbol, asset=asset, quote_asset=quote_asset)
    return (row,) if row is not None else ()


def _coinglass_heatmap_metric_rows(
    payload: Any,
    task: Any,
    *,
    spec: Mapping[str, Any],
    symbol: str,
    asset: str,
    quote_asset: str,
) -> tuple[dict[str, Any], ...]:
    points = _coinglass_heatmap_points(_provider_data(payload))
    rows: list[dict[str, Any]] = []
    for point in points:
        price = _first_numeric_metric(point, ("liquidation_price", "liquidationPrice", "price", "p", "y"))
        size = _first_numeric_metric(point, ("liquidation_size", "liquidationSize", "size", "amount", "value", "v", "liq", "liquidation"))
        if price is None or size is None:
            continue
        timestamp = _coinglass_timestamp_or_none(point) or datetime.now(tz=UTC)
        row = _crypto_base_row(
            dataset="crypto_derivative_metric",
            symbol=symbol,
            base_asset=asset,
            quote_asset=quote_asset,
            provider_id=CoinglassCryptoPlugin.plugin_id,
            endpoint_id=str(spec["endpoint_id"]),
            source_role=str(spec["source_role"]),
            granularity=str(getattr(task, "granularity", None) or tuple(spec["granularity"])[0]),
        )
        row.update(
            {
                "liquidation_price": price,
                "liquidation_size": size,
                "liquidation_price_unit": quote_asset,
                "liquidation_size_unit": "USD",
                "side": non_empty(point.get("side") or point.get("direction")) or "unknown",
                "timestamp": timestamp,
            }
        )
        _apply_crypto_period(row, timestamp)
        rows.append(row)
    return tuple(rows)


def _coinglass_heatmap_points(value: Any) -> tuple[dict[str, Any], ...]:
    points: list[dict[str, Any]] = []
    _collect_coinglass_heatmap_points(value, points)
    return tuple(points)


def _collect_coinglass_heatmap_points(value: Any, points: list[dict[str, Any]]) -> None:
    if isinstance(value, Mapping):
        price = _first_numeric_metric(value, ("liquidation_price", "liquidationPrice", "price", "p", "y"))
        size = _first_numeric_metric(value, ("liquidation_size", "liquidationSize", "size", "amount", "value", "v", "liq", "liquidation"))
        if price is not None and size is not None:
            points.append(dict(value))
            return
        for child in value.values():
            _collect_coinglass_heatmap_points(child, points)
        return
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) >= 3:
            first = _decimal_float(value[0])
            second = _decimal_float(value[1])
            third = _decimal_float(value[2])
            first_timestamp = _safe_parse_plausible_datetime(value[0])
            if first_timestamp is not None and second is not None and third is not None:
                points.append({"liquidation_price": second, "liquidation_size": third, "timestamp": first_timestamp})
                return
            if first is not None and second is not None:
                point: dict[str, Any] = {"liquidation_price": first, "liquidation_size": second}
                parsed = _safe_parse_plausible_datetime(value[2])
                if parsed is not None:
                    point["timestamp"] = parsed
                points.append(point)
                return
        if len(value) >= 2:
            first = _decimal_float(value[0])
            second = _decimal_float(value[1])
            if first is not None and second is not None and _safe_parse_datetime(value[0]) is None:
                points.append({"liquidation_price": first, "liquidation_size": second})
                return
        for child in value:
            _collect_coinglass_heatmap_points(child, points)


def _safe_parse_datetime(value: Any) -> datetime | None:
    try:
        return parse_datetime(value)
    except (TypeError, ValueError):
        return None


def _safe_parse_plausible_datetime(value: Any) -> datetime | None:
    parsed = _safe_parse_datetime(value)
    if parsed is None:
        return None
    if parsed < datetime(2010, 1, 1, tzinfo=UTC):
        return None
    if parsed > datetime.now(tz=UTC) + timedelta(days=730):
        return None
    return parsed


def _first_mapping(value: Any) -> Mapping[str, Any]:
    data = _provider_data(value)
    if isinstance(data, Mapping):
        return data
    if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray)):
        for item in data:
            found = _first_mapping(item)
            if found:
                return found
    return {}


def _coinglass_ohlc_values(item: Mapping[str, Any]) -> dict[str, Any] | None:
    open_price = _first_numeric_metric(item, ("open", "o"))
    high = _first_numeric_metric(item, ("high", "h"))
    low = _first_numeric_metric(item, ("low", "l"))
    close = _first_numeric_metric(item, ("close", "c", "price", "value"))
    volume = _first_numeric_metric(item, ("volume", "vol", "v"))
    if None in {open_price, high, low, close}:
        return None
    return {"open": open_price, "high": high, "low": low, "close": close, "volume": volume, "amount": _first_numeric_metric(item, ("amount", "turnover", "quoteVolume"))}


def _coinglass_order_book_values(item: Mapping[str, Any]) -> dict[str, Any] | None:
    bid = _first_level(item.get("bids") or item.get("bid") or item.get("buy"))
    ask = _first_level(item.get("asks") or item.get("ask") or item.get("sell"))
    if bid is None and ask is None:
        bid_price = _first_numeric_metric(item, ("bid_price", "bidPrice", "bestBidPrice"))
        bid_size = _first_numeric_metric(item, ("bid_size", "bidSize", "bestBidSize"))
        ask_price = _first_numeric_metric(item, ("ask_price", "askPrice", "bestAskPrice"))
        ask_size = _first_numeric_metric(item, ("ask_size", "askSize", "bestAskSize"))
    else:
        bid_price, bid_size = bid or (None, None)
        ask_price, ask_size = ask or (None, None)
    if bid_price is None and ask_price is None:
        return None
    return {"bid_price": bid_price, "bid_size": bid_size, "ask_price": ask_price, "ask_size": ask_size}


def _coinglass_order_book_values_from_payload(payload: Any) -> dict[str, Any] | None:
    rows = _coinglass_order_book_rows_from_payload(payload)
    return rows[0] if rows else None


def _coinglass_order_book_rows_from_payload(payload: Any) -> tuple[dict[str, Any], ...]:
    data = _provider_data(payload)
    if isinstance(data, Mapping):
        row = _coinglass_order_book_values(data)
        if row is None:
            return ()
        row["timestamp"] = _coinglass_timestamp(data)
        return (row,)
    if not isinstance(data, Sequence) or isinstance(data, (str, bytes, bytearray)):
        return ()
    rows: list[dict[str, Any]] = []
    for item in data:
        if isinstance(item, Mapping):
            levels = _coinglass_order_book_values(item)
            if levels is not None:
                levels["timestamp"] = _coinglass_timestamp(item)
                rows.append(levels)
        if isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)) and len(item) >= 3:
            bid = _first_level(item[1])
            ask = _first_level(item[2])
            if bid is None and ask is None:
                continue
            bid_price, bid_size = bid or (None, None)
            ask_price, ask_size = ask or (None, None)
            row: dict[str, Any] = {"bid_price": bid_price, "bid_size": bid_size, "ask_price": ask_price, "ask_size": ask_size}
            timestamp = parse_datetime(item[0])
            if timestamp is not None:
                row["timestamp"] = timestamp
            rows.append(row)
    return tuple(rows)


def _coinglass_timestamp(item: Mapping[str, Any]) -> datetime:
    parsed = _coinglass_timestamp_or_none(item)
    return parsed or datetime.now(tz=UTC)


def _coinglass_timestamp_or_none(item: Mapping[str, Any]) -> datetime | None:
    for key in ("timestamp", "time", "t", "date", "block_timestamp", "date_string"):
        try:
            value = item.get(key)
            if value is not None:
                parsed = parse_datetime(value)
                if parsed is not None:
                    return parsed
        except (TypeError, ValueError):
            continue
    return None


def _apply_crypto_period(row: dict[str, Any], timestamp: datetime) -> None:
    day = timestamp.astimezone(UTC).date() if timestamp.tzinfo else timestamp.date()
    row["period_start"] = day
    row["period_end"] = day
    row.setdefault("date", day.isoformat())


def _coinglass_chain(item: Mapping[str, Any]) -> str:
    return non_empty(item.get("chain") or item.get("network") or item.get("blockchain_name")) or "MULTI_CHAIN"


def _coinglass_onchain_value(payload: Any, item: Mapping[str, Any], *, spec: Mapping[str, Any]) -> float | None:
    value, _source_field = _coinglass_onchain_value_with_key(payload, item, spec=spec)
    return value


def _coinglass_onchain_value_with_key(payload: Any, item: Mapping[str, Any], *, spec: Mapping[str, Any]) -> tuple[float | None, str | None]:
    endpoint_id = str(spec.get("endpoint_id", ""))
    if endpoint_id == "onchain_exchange_balance":
        data = _provider_data(payload)
        if isinstance(data, Mapping):
            data_map = data.get("data_map")
            if isinstance(data_map, Mapping):
                preferred_series = data_map.get("Binance") or data_map.get("binance")
                value = _last_numeric_from_series(preferred_series)
                if value is not None:
                    return value, "data_map.Binance"
                for series in data_map.values():
                    value = _last_numeric_from_series(series)
                    if value is not None:
                        return value, "data_map"
    return _first_numeric_metric_with_key(_provider_data(payload), tuple(spec.get("metric_keys", ("value", "amount", "balance", "total", "close"))))


def _coinglass_onchain_value_unit(metric: str, source_field: str | None, *, base_asset: str | None = None) -> str | None:
    token = (source_field or "").lower()
    if "usd" in token:
        return "USD"
    if metric == "exchange_balance":
        return str(base_asset or "base_asset").upper()
    if metric == "spot_coin_netflow":
        return "USD"
    if metric == "whale_transfer" and token in {"netflow", "net_flow", "netinflow", "value", "amountusd", "amount_usd"}:
        return "USD"
    return None


def _last_numeric_from_series(value: Any) -> float | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray, Mapping)):
        return _decimal_float(value)
    for item in reversed(value):
        result = _decimal_float(item)
        if result is not None:
            return result
    return None


def _set_optional_metric(row: dict[str, Any], field: str, item: Mapping[str, Any], keys: tuple[str, ...]) -> None:
    value = _first_numeric_metric(item, keys)
    if value is not None:
        row[field] = value


class LunarCrushCryptoSocialPlugin:
    plugin_id = "crypto_lunarcrush_social"
    version = "1.0.0"
    credential_name = "data_source:lunarcrush"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="topic",
                    market="CRYPTO",
                    data_type="social_signal",
                    source_role="paid_data",
                    granularity=("event",),
                    fields=("source", "timestamp", "score", "sentiment", "social_dominance", "num_posts", "interactions", "symbol_id"),
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
        symbol = first_symbol(task) or "BTC"
        symbols = resolve_crypto_provider_symbols(symbol)
        base_asset = symbols.crypto_base_symbol or symbol.upper()
        quote_asset = symbols.crypto_quote_symbol or "USDT"
        topic = symbols.coingecko_coin_id or base_asset.lower()
        host, prefix = endpoint(ctx, self.credential_name, "https://lunarcrush.com/api4")
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/public/topic/{topic}/v1",
                headers={"accept": "application/json", "authorization": f"Bearer {token}"},
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        item = _first_mapping(_provider_data(payload))
        if not item:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        row = _crypto_base_row(
            dataset="social_signal",
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            provider_id=self.plugin_id,
            endpoint_id="topic",
            source_role="paid_data",
            granularity="event",
        )
        timestamp = _safe_parse_datetime(item.get("timestamp") or item.get("time")) or datetime.now(tz=UTC)
        row.update(
            {
                "source": "LunarCrush",
                "timestamp": timestamp,
                "score": _first_numeric_metric(item, ("galaxy_score", "galaxyScore", "alt_rank", "altRank", "score")),
                "sentiment": non_empty(item.get("sentiment") or item.get("sentiment_classification")),
                "social_dominance": _first_numeric_metric(item, ("social_dominance", "socialDominance")),
                "num_posts": _first_numeric_metric(item, ("num_posts", "posts", "post_count")),
                "interactions": _first_numeric_metric(item, ("interactions", "interactions_24h", "social_interactions")),
            }
        )
        _apply_crypto_period(row, timestamp)
        if not any(row.get(field) is not None for field in ("score", "social_dominance", "num_posts", "interactions")):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        return FetchResult.from_success(task, payload={"rows": [row]}, row_count=1, http_observations=observations)


class GlassnodeCryptoOnchainPlugin:
    plugin_id = "crypto_glassnode_onchain"
    version = "1.0.0"
    credential_name = "data_source:glassnode"
    _metrics: tuple[tuple[str, str], ...] = (
        ("active_addresses", "/v1/metrics/addresses/active_count"),
        ("mvrv", "/v1/metrics/indicators/mvrv_account_based"),
        ("sth_sopr", "/v1/metrics/indicators/sopr_less_155"),
        ("lth_sopr", "/v1/metrics/indicators/sopr_more_155"),
        ("nupl", "/v1/metrics/indicators/net_unrealized_profit_loss"),
    )

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="deep_onchain_metrics",
                    market="CRYPTO",
                    data_type="crypto_onchain_metric",
                    source_role="paid_data",
                    granularity=("daily",),
                    fields=("timestamp", "metric", "value", "chain", "source_metric"),
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
        symbol = first_symbol(task) or "BTC"
        symbols = resolve_crypto_provider_symbols(symbol)
        base_asset = symbols.crypto_base_symbol or symbol.upper()
        quote_asset = symbols.crypto_quote_symbol or "USDT"
        host, prefix = endpoint(ctx, self.credential_name, "https://api.glassnode.com")
        rows: list[dict[str, Any]] = []
        observations: list[Any] = []
        for metric, path in self._metrics:
            query = {
                "a": base_asset.upper(),
                "i": "24h",
                "f": "json",
                "api_key": token,
            }
            start = _start_seconds(getattr(task, "date_range_start", None))
            end = _end_seconds(getattr(task, "date_range_end", None))
            if start is not None:
                query["s"] = start
            if end is not None:
                query["u"] = end
            payload, http_observations, error = send_json_request(
                task,
                ctx,
                HttpRequestSpec(
                    method="GET",
                    host=host,
                    path=f"{prefix}{path}",
                    query=query,
                    headers={"accept": "application/json"},
                    provider_config_version=getattr(task, "provider_config_version", None),
                ),
            )
            observations.extend(http_observations)
            if error is not None:
                return error
            rows.extend(
                _glassnode_rows(
                    payload,
                    task,
                    metric=metric,
                    symbol=symbol,
                    base_asset=base_asset,
                    quote_asset=quote_asset,
                    provider_id=self.plugin_id,
                    endpoint_id="deep_onchain_metrics",
                )
            )
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=tuple(observations))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=tuple(observations))


class TokenTerminalCryptoFundamentalsPlugin:
    plugin_id = "crypto_token_terminal_fundamentals"
    version = "1.0.0"
    credential_name = "data_source:token_terminal"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="protocol_revenue",
                    market="CRYPTO",
                    data_type="defi_metric",
                    source_role="paid_data",
                    granularity=("daily",),
                    fields=("protocol_revenue", "fees", "timestamp", "symbol_id"),
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
        symbol = first_symbol(task) or "BTC"
        symbols = resolve_crypto_provider_symbols(symbol)
        base_asset = symbols.crypto_base_symbol or symbol.upper()
        quote_asset = symbols.crypto_quote_symbol or "USDT"
        project_id = symbols.defillama_protocol_slug or symbols.coingecko_coin_id or base_asset.lower()
        host, prefix = endpoint(ctx, self.credential_name, "https://api.tokenterminal.com")
        query: dict[str, Any] = {"project_ids": project_id}
        start = _date_text_for_query(getattr(task, "date_range_start", None))
        end = _date_text_for_query(getattr(task, "date_range_end", None))
        if start is not None:
            query["start"] = start
        if end is not None:
            query["end"] = end
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/v2/metrics/revenue",
                query=query,
                headers={"accept": "application/json", "authorization": f"Bearer {token}"},
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        rows = _token_terminal_revenue_rows(
            payload,
            task,
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            provider_id=self.plugin_id,
            endpoint_id="protocol_revenue",
        )
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"), http_observations=observations)
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)


def _glassnode_rows(
    payload: Any,
    task: Any,
    *,
    metric: str,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    provider_id: str,
    endpoint_id: str,
) -> list[dict[str, Any]]:
    data = _provider_data(payload)
    items = data if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray, Mapping)) else (data,)
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        value = _first_numeric_metric(item, ("v", "value"))
        if value is None:
            continue
        timestamp = _safe_parse_datetime(item.get("t")) or datetime.now(tz=UTC)
        row = _crypto_base_row(
            dataset="crypto_onchain_metric",
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            source_role="paid_data",
            granularity=str(getattr(task, "granularity", None) or "daily"),
        )
        row.update({"timestamp": timestamp, "metric": metric, "source_metric": metric, "value": value, "chain": base_asset.upper()})
        _apply_crypto_period(row, timestamp)
        rows.append(row)
    return rows


def _token_terminal_revenue_rows(
    payload: Any,
    task: Any,
    *,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    provider_id: str,
    endpoint_id: str,
) -> list[dict[str, Any]]:
    data = _provider_data(payload)
    items = data if isinstance(data, Sequence) and not isinstance(data, (str, bytes, bytearray, Mapping)) else (data,)
    rows: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        revenue = _first_numeric_metric(item, ("protocol_revenue", "revenue", "value"))
        fees = _first_numeric_metric(item, ("fees", "total_fees"))
        if revenue is None and fees is None:
            continue
        timestamp = _safe_parse_datetime(item.get("timestamp") or item.get("date")) or datetime.now(tz=UTC)
        row = _crypto_base_row(
            dataset="defi_metric",
            symbol=symbol,
            base_asset=base_asset,
            quote_asset=quote_asset,
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            source_role="paid_data",
            granularity=str(getattr(task, "granularity", None) or "daily"),
        )
        row.update({"timestamp": timestamp, "protocol_revenue": revenue, "fees": fees})
        _apply_crypto_period(row, timestamp)
        rows.append(row)
    return rows


def _date_text_for_query(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    return non_empty(value)


class AlternativeMeCryptoSentimentPlugin:
    plugin_id = "crypto_alternative_me_sentiment"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="fear_greed",
                    market="CRYPTO",
                    data_type="social_signal",
                    source_role="sentiment",
                    granularity=("event",),
                    fields=("source", "timestamp", "score", "sentiment", "symbol_id"),
                    priority_rank=20,
                    can_be_formal_fact_source=False,
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
        symbol = first_symbol(task) or "CRYPTO"
        symbols = resolve_crypto_provider_symbols(symbol)
        host, prefix = endpoint(ctx, "data_source:alternative_me", "https://api.alternative.me")
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/fng/",
                query={"limit": 1, "format": "json"},
                headers={"accept": "application/json"},
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        data = payload.get("data") if isinstance(payload, Mapping) else None
        item = data[0] if isinstance(data, Sequence) and data and isinstance(data[0], Mapping) else None
        if item is None:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        timestamp = parse_datetime(item.get("timestamp"))
        row = _crypto_base_row(
            dataset="social_signal",
            symbol=symbol,
            base_asset=symbols.crypto_base_symbol or symbol,
            quote_asset=symbols.crypto_quote_symbol or "USDT",
            provider_id=self.plugin_id,
            endpoint_id="fear_greed",
            source_role="sentiment",
            granularity="event",
        )
        row.update(
            {
                "source": "Alternative.me Fear & Greed",
                "timestamp": timestamp,
                "score": _decimal_float(item.get("value")),
                "sentiment": non_empty(item.get("value_classification")),
                "quality_flags": ("market_level_sentiment_not_platform_social",),
            }
        )
        return FetchResult.from_success(task, payload={"rows": [row]}, row_count=1, http_observations=observations)


class CryptoProjectNewsPlugin:
    plugin_id = "crypto_project_official_news"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="github_releases",
                    market="CRYPTO",
                    data_type="company_news",
                    source_role="official",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "summary", "url"),
                    priority_rank=0,
                    supports_batch=False,
                    batch_by="none",
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
        symbol = first_symbol(task) or ""
        symbols = resolve_crypto_provider_symbols(symbol)
        repo = symbols.official_repo
        if not repo:
            return FetchResult.from_error(task, status="not_applicable", error=RuntimeError("official_repo_missing"))
        host, prefix = endpoint(ctx, "data_source:github", "https://api.github.com")
        payload, observations, error = send_json_request(
            task,
            ctx,
            HttpRequestSpec(
                method="GET",
                host=host,
                path=f"{prefix}/repos/{repo}/releases",
                headers={"accept": "application/vnd.github+json", "user-agent": "claw-trade-data-gateway/1.0"},
                provider_config_version=getattr(task, "provider_config_version", None),
            ),
        )
        if error is not None:
            return error
        if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes, bytearray)):
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        rows: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, Mapping):
                continue
            title = non_empty(item.get("name") or item.get("tag_name"))
            url = non_empty(item.get("html_url"))
            published = parse_datetime(item.get("published_at"))
            if title is None or url is None:
                continue
            row = _crypto_base_row(
                dataset="company_news",
                symbol=symbol,
                base_asset=symbols.crypto_base_symbol or symbol,
                quote_asset=symbols.crypto_quote_symbol or "USDT",
                provider_id=self.plugin_id,
                endpoint_id="github_releases",
                source_role="official",
                granularity="event",
            )
            row.update({"title": title, "published_at": published, "source": f"GitHub:{repo}", "summary": non_empty(item.get("body")), "url": url})
            rows.append(row)
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)


class CryptoGoogleNewsDiscoveryPlugin:
    plugin_id = "crypto_google_news"
    version = "1.0.0"

    def __init__(self) -> None:
        self._capabilities = ProviderCapabilities(
            provider_id=self.plugin_id,
            plugin_version=self.version,
            endpoints=(
                endpoint_capability(
                    endpoint_id="company_news",
                    market="CRYPTO",
                    data_type="company_news",
                    source_role="discovery",
                    granularity=("event",),
                    fields=("title", "published_at", "source", "summary", "url"),
                    priority_rank=50,
                    can_be_formal_fact_source=False,
                ),
                endpoint_capability(
                    endpoint_id="macro_news",
                    market="CRYPTO",
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
            default_rate_limit_policy={"window_seconds": 60, "max_calls": 20},
            default_priority_rank=50,
        )

    def capabilities(self) -> ProviderCapabilities:
        return self._capabilities

    def build_fetch_tasks(self, batch: Any) -> tuple[Any, ...]:
        return (batch,)

    def fetch(self, task: Any, ctx: Any) -> FetchResult:
        symbol = first_symbol(task) or "CRYPTO"
        query = f"{symbol} crypto project news" if str(getattr(task, "endpoint_id", "")) == "company_news" else "crypto market macro news"
        http = managed_http(ctx)
        if http is None:
            return FetchResult.from_error(task, status="error", error=RuntimeError("managed_http_required"))
        host, prefix = endpoint(ctx, "data_source:google_news", "https://news.google.com")
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
        rows = _crypto_rss_rows(
            capture.body_text,
            dataset=str(getattr(task, "data_type", "company_news")),
            symbol=symbol,
            provider_id=self.plugin_id,
            endpoint_id=str(getattr(task, "endpoint_id", "company_news")),
            source_role="discovery",
            source="Google News",
        )
        if not rows:
            return FetchResult.from_empty(task, error=RuntimeError("empty_result"))
        return FetchResult.from_success(task, payload={"rows": rows}, row_count=len(rows), http_observations=observations)


def build_crypto_provider_plugin() -> BinanceSpotDailyBarPlugin:
    return BinanceSpotDailyBarPlugin()


def build_crypto_provider_plugins() -> tuple[object, ...]:
    return (
        BinanceSpotDailyBarPlugin(),
        BinanceSpotMarketPlugin(),
        CoinGeckoCryptoPlugin(),
        DefiLlamaCryptoPlugin(),
        CoinglassCryptoPlugin(),
        LunarCrushCryptoSocialPlugin(),
        GlassnodeCryptoOnchainPlugin(),
        TokenTerminalCryptoFundamentalsPlugin(),
        AlternativeMeCryptoSentimentPlugin(),
        CryptoProjectNewsPlugin(),
        CryptoGoogleNewsDiscoveryPlugin(),
    )


def _request_for(task: Any, *, symbol: str, ctx: Any) -> HttpRequestSpec:
    query: dict[str, Any] = {
        "symbol": symbol,
        "interval": "1d",
        "limit": 1000,
    }
    start_ms = _start_millis(getattr(task, "date_range_start", None))
    end_ms = _end_millis(getattr(task, "date_range_end", None))
    if start_ms is not None:
        query["startTime"] = start_ms
    if end_ms is not None:
        query["endTime"] = end_ms
    host, path_prefix = _endpoint_from_settings(ctx, default_host=BinanceSpotDailyBarPlugin._host)
    return HttpRequestSpec(
        method="GET",
        host=host,
        path=f"{path_prefix}/api/v3/klines",
        query=query,
        headers={"accept": "application/json"},
        provider_config_version=getattr(task, "provider_config_version", None),
    )


def _endpoint_from_settings(ctx: Any, *, default_host: str) -> tuple[str, str]:
    resolver = getattr(ctx, "credential_resolver", None)
    getter = getattr(resolver, "get_endpoint_url", None)
    endpoint = getter("data_source:binance") if callable(getter) else None
    if not endpoint:
        return default_host, ""
    parsed = urlparse(str(endpoint).strip())
    if not parsed.scheme or not parsed.netloc:
        return str(endpoint).strip().rstrip("/"), ""
    return f"{parsed.scheme}://{parsed.netloc}", parsed.path.rstrip("/")


def _kline_to_row(
    item: Any,
    *,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    granularity: str = "daily",
) -> dict[str, Any] | None:
    if not isinstance(item, list) or len(item) < 8:
        return None
    try:
        open_time = int(item[0])
        close_time = int(item[6]) if len(item) > 6 else open_time
    except (TypeError, ValueError):
        return None
    open_price = _decimal_float(item[1])
    high = _decimal_float(item[2])
    low = _decimal_float(item[3])
    close = _decimal_float(item[4])
    volume = _decimal_float(item[5])
    amount = _decimal_float(item[7])
    if None in {open_price, high, low, close, volume, amount}:
        return None
    normalized_open = datetime.fromtimestamp(open_time / 1000, tz=UTC)
    normalized_close = datetime.fromtimestamp(close_time / 1000, tz=UTC)
    is_daily = granularity == "daily"
    period_start = normalized_open.date() if is_daily else normalized_open
    period_end = normalized_close.date() if is_daily else normalized_close
    row = {
        "dataset": "daily_bar" if is_daily else "intraday_bar",
        "market": "CRYPTO",
        "symbol_id": symbol,
        "granularity": granularity,
        "period_start": period_start,
        "period_end": period_end,
        "open_time": normalized_open,
        "close_time": normalized_close,
        "open": open_price,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
        "amount": amount,
        "exchange": "BINANCE",
        "currency": quote_asset,
        "timezone": "UTC",
        "calendar": "CRYPTO_24_7",
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "universe_ref": "binance_spot_all_symbols",
        "source_market_segment": "spot",
        "provider_lineage": {"provider_id": "crypto_primary", "endpoint_id": "spot_daily_bar"},
        "source_roles": ("official",),
        "schema_id": "daily_bar.v1",
        "quality_flags": (),
    }
    if is_daily:
        row["date"] = period_start
    return row


def _decimal_float(value: Any) -> float | None:
    try:
        return float(Decimal(str(value)))
    except (TypeError, ValueError, InvalidOperation):
        return None


def _start_millis(value: Any) -> int | None:
    day = _as_date(value)
    if day is None:
        return None
    return int(datetime.combine(day, time.min, tzinfo=UTC).timestamp() * 1000)


def _end_millis(value: Any) -> int | None:
    day = _as_date(value)
    if day is None:
        return None
    return int(datetime.combine(day, time.max, tzinfo=UTC).timestamp() * 1000)


def _start_seconds(value: Any) -> int | None:
    day = _as_date(value)
    if day is None:
        return None
    return int(datetime.combine(day, time.min, tzinfo=UTC).timestamp())


def _end_seconds(value: Any) -> int | None:
    day = _as_date(value)
    if day is None:
        return None
    return int(datetime.combine(day, time.max, tzinfo=UTC).timestamp())


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.astimezone(UTC).date() if value.tzinfo else value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        return date.fromisoformat(value.strip()[:10])
    return None


def _split_symbol(symbol: str) -> tuple[str | None, str | None]:
    if "/" in symbol:
        base, quote = symbol.split("/", 1)
        return base or None, quote or None
    for quote in ("FDUSD", "USDT", "USDC", "BUSD", "TUSD", "USD", "BTC", "ETH", "BNB"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return symbol[: -len(quote)], quote
    return None, None


def _crypto_provider_symbol(symbol: str | None) -> str | None:
    if symbol is None:
        return None
    token = str(symbol).strip().upper().replace("/", "")
    if not token:
        return None
    symbols = resolve_crypto_provider_symbols(token)
    return symbols.crypto_provider_symbol or token


def _first_level(value: Any) -> tuple[float, float] | None:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or not value:
        return None
    first = value[0]
    if not isinstance(first, Sequence) or isinstance(first, (str, bytes, bytearray)) or len(first) < 2:
        return None
    price = _decimal_float(first[0])
    size = _decimal_float(first[1])
    if price is None or size is None:
        return None
    return price, size


def _crypto_base_row(
    *,
    dataset: str,
    symbol: str,
    base_asset: str,
    quote_asset: str,
    provider_id: str,
    endpoint_id: str,
    source_role: str,
    granularity: str,
) -> dict[str, Any]:
    now = datetime.now(tz=UTC)
    return {
        "dataset": dataset,
        "market": "CRYPTO",
        "symbol_id": symbol,
        "granularity": granularity,
        "exchange": _crypto_exchange_label(provider_id),
        "currency": quote_asset,
        "timezone": "UTC",
        "calendar": "CRYPTO_24_7",
        "base_asset": base_asset,
        "quote_asset": quote_asset,
        "provider_lineage": {"provider_id": provider_id, "endpoint_id": endpoint_id},
        "source_roles": (source_role,),
        "schema_id": f"{dataset}.v1",
        "quality_flags": (),
    }


def _defillama_tvl(payload: Mapping[str, Any]) -> float | None:
    direct = _decimal_float(payload.get("tvl"))
    if direct is not None:
        return direct
    current_chain_tvls = payload.get("currentChainTvls")
    if isinstance(current_chain_tvls, Mapping):
        values = [_decimal_float(value) for value in current_chain_tvls.values()]
        numeric_values = [value for value in values if value is not None]
        if numeric_values:
            return sum(numeric_values)
    series_value = _last_numeric_from_series(payload.get("tvl"))
    if series_value is not None:
        return series_value
    chain_tvls = payload.get("chainTvls")
    if isinstance(chain_tvls, Mapping):
        values: list[float] = []
        for chain_payload in chain_tvls.values():
            if isinstance(chain_payload, Mapping):
                value = _last_numeric_from_series(chain_payload.get("tvl"))
                if value is not None:
                    values.append(value)
        if values:
            return sum(values)
    return None


def _provider_data(payload: Any) -> Any:
    if not isinstance(payload, Mapping):
        return payload
    for key in ("data", "result", "items", "list"):
        if key in payload:
            return payload[key]
    return payload


def _crypto_exchange_label(provider_id: str) -> str:
    token = provider_id.upper()
    if "BINANCE" in token:
        return "BINANCE"
    if "COINGECKO" in token:
        return "COINGECKO"
    if "DEFILLAMA" in token:
        return "DEFILLAMA"
    if "COINGLASS" in token:
        return "COINGLASS"
    if "LUNARCRUSH" in token:
        return "LUNARCRUSH"
    if "GLASSNODE" in token:
        return "GLASSNODE"
    if "TOKEN_TERMINAL" in token:
        return "TOKEN_TERMINAL"
    if "ALTERNATIVE" in token:
        return "ALTERNATIVE_ME"
    if "PROJECT" in token:
        return "GITHUB"
    if "GOOGLE" in token:
        return "GOOGLE_NEWS"
    return token


def _first_numeric_metric(value: Any, keys: tuple[str, ...]) -> float | None:
    result, _source_field = _first_numeric_metric_with_key(value, keys)
    return result


def _first_numeric_metric_with_key(value: Any, keys: tuple[str, ...]) -> tuple[float | None, str | None]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, Mapping)):
        for item in value:
            result, source_field = _first_numeric_metric_with_key(item, keys)
            if result is not None:
                return result, source_field
        return None, None
    if not isinstance(value, Mapping):
        return _decimal_float(value), None
    for key in keys:
        result = _decimal_float(value.get(key))
        if result is not None:
            return result, key
    return None, None


def _coinglass_metric_unit(field: str, source_field: str | None) -> str | None:
    token = (source_field or "").lower()
    if field == "open_interest":
        if "usd" in token or "value" in token or token in {"value", "close"}:
            return "USD"
        if "quantity" in token:
            return "base_asset_quantity"
        return "contracts"
    if field == "funding_rate":
        return "percent"
    if field == "net_inflow":
        if "usd" in token or token in {"value", "netflow", "net_flow", "netinflow", "net_inflow"}:
            return "USD"
    return None


def _crypto_rss_rows(
    body: str | None,
    *,
    dataset: str,
    symbol: str,
    provider_id: str,
    endpoint_id: str,
    source_role: str,
    source: str,
) -> list[dict[str, Any]]:
    if not body:
        return []
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError:
        return []
    symbols = resolve_crypto_provider_symbols(symbol)
    rows: list[dict[str, Any]] = []
    for item in root.findall(".//item"):
        title = non_empty(item.findtext("title"))
        url = non_empty(item.findtext("link"))
        if title is None or url is None:
            continue
        published = _parse_rss_datetime(item.findtext("pubDate"))
        row = _crypto_base_row(
            dataset=dataset,
            symbol=symbol,
            base_asset=symbols.crypto_base_symbol or symbol,
            quote_asset=symbols.crypto_quote_symbol or "USDT",
            provider_id=provider_id,
            endpoint_id=endpoint_id,
            source_role=source_role,
            granularity="event",
        )
        row.update(
            {
                "title": title,
                "published_at": published,
                "source": source,
                "summary": non_empty(item.findtext("description")),
                "url": url,
                "region": "GLOBAL",
            }
        )
        if source_role == "discovery":
            row["quality_flags"] = ("not_formal_fact_source",)
        rows.append(row)
    return rows


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
    "AlternativeMeCryptoSentimentPlugin",
    "BinanceSpotDailyBarPlugin",
    "BinanceSpotMarketPlugin",
    "CoinGeckoCryptoPlugin",
    "CoinglassCryptoPlugin",
    "CryptoGoogleNewsDiscoveryPlugin",
    "CryptoProjectNewsPlugin",
    "DefiLlamaCryptoPlugin",
    "GlassnodeCryptoOnchainPlugin",
    "LunarCrushCryptoSocialPlugin",
    "TokenTerminalCryptoFundamentalsPlugin",
    "build_crypto_provider_plugin",
    "build_crypto_provider_plugins",
]
