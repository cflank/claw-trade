from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Mapping, Sequence

from claw_trade.data_gateway.models import DataGap, DataResult, DataResultStatus, GapReason, GapSeverity, Market


CRYPTO_LENS_DOMAIN_KEYS = (
    "market",
    "ohlcv",
    "derivatives",
    "liquidation_map",
    "onchain",
    "macro",
    "events",
    "ahr999",
)

_DOMAIN_STATUS_DATA_TYPES: Mapping[str, tuple[str, ...]] = {
    "market": ("quote_snapshot",),
    "ohlcv": ("daily_bar", "intraday_bar"),
    "derivatives": ("crypto_derivative_metric",),
    "liquidation_map": ("crypto_derivative_metric",),
    "onchain": ("crypto_onchain_metric",),
    "macro": ("macro_series", "macro_news"),
    "events": ("event_calendar", "company_news"),
    "ahr999": ("crypto_onchain_metric",),
}


class CryptoLensDomainStatus(StrEnum):
    READY = "ready"
    PARTIAL = "partial"
    MISSING = "missing"
    ERROR = "error"
    NOT_APPLICABLE = "not_applicable"


@dataclass(frozen=True)
class CryptoLensDomains:
    market: Mapping[str, Any] | None = None
    ohlcv: Mapping[str, Any] | None = None
    derivatives: Mapping[str, Any] | None = None
    liquidation_map: Mapping[str, Any] | None = None
    onchain: Mapping[str, Any] | None = None
    macro: Mapping[str, Any] | None = None
    events: Mapping[str, Any] | None = None
    ahr999: Mapping[str, Any] | None = None

    def to_mapping(self) -> dict[str, Any]:
        return {
            "market": self.market,
            "ohlcv": self.ohlcv,
            "derivatives": self.derivatives,
            "liquidation_map": self.liquidation_map,
            "onchain": self.onchain,
            "macro": self.macro,
            "events": self.events,
            "ahr999": self.ahr999,
        }


@dataclass(frozen=True)
class CryptoLensInput:
    run_id: str
    call_id: str
    ticker: str
    market: Market
    quote: str
    as_of: str
    start_date: str
    end_date: str
    domains: CryptoLensDomains
    domain_status: Mapping[str, CryptoLensDomainStatus]
    data_gaps: tuple[DataGap, ...]
    conflicts: tuple[Mapping[str, Any], ...]
    attempt_refs: tuple[str, ...]
    normalized_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.market != Market.CRYPTO:
            raise ValueError("CryptoLensInput.market must be CRYPTO")
        if set(self.domain_status) != set(CRYPTO_LENS_DOMAIN_KEYS):
            raise ValueError("CryptoLensInput.domain_status must include all crypto domains")

    @classmethod
    def from_data_results(
        cls,
        results: Sequence[DataResult],
        *,
        ticker: str,
        quote: str,
        run_id: str,
        call_id: str,
        as_of: str,
        start_date: str = "",
        end_date: str = "",
    ) -> "CryptoLensInput":
        normalized_ticker = ticker.strip().upper()
        domains = _domains_from_results(results)
        domain_status = _domain_status_from_results(results, domains=domains, ticker=normalized_ticker)
        return cls(
            run_id=run_id,
            call_id=call_id,
            ticker=normalized_ticker,
            market=Market.CRYPTO,
            quote=quote.strip().upper() or "USDT",
            as_of=as_of,
            start_date=start_date,
            end_date=end_date,
            domains=domains,
            domain_status=domain_status,
            data_gaps=tuple(_all_gaps(results)),
            conflicts=(),
            attempt_refs=tuple(dict.fromkeys(ref for result in results for ref in result.attempt_refs)),
            normalized_refs=tuple(dict.fromkeys(ref for result in results for ref in result.dataset_refs)),
        )

    def gap_ids_for(self, domain_key: str) -> tuple[str, ...]:
        data_types = _DOMAIN_STATUS_DATA_TYPES.get(domain_key, ())
        return tuple(
            gap.gap_id
            for gap in self.data_gaps
            if gap.data_type in data_types or _gap_matches_domain_text(gap, domain_key)
        )

    def as_dict(self) -> Mapping[str, Any]:
        return {
            "run_id": self.run_id,
            "call_id": self.call_id,
            "ticker": self.ticker,
            "market": self.market.value,
            "quote": self.quote,
            "as_of": self.as_of,
            "start_date": self.start_date,
            "end_date": self.end_date,
            "domains": self.domains.to_mapping(),
            "domain_status": {key: status.value for key, status in self.domain_status.items()},
            "data_gaps": self.data_gaps,
            "conflicts": self.conflicts,
            "attempt_refs": self.attempt_refs,
            "normalized_refs": self.normalized_refs,
        }


def _domains_from_results(results: Sequence[DataResult]) -> CryptoLensDomains:
    ohlcv_rows = _rows_for(results, "daily_bar") or _rows_for(results, "intraday_bar")
    candles = _ohlcv_candles(ohlcv_rows)
    quote_rows = _rows_for(results, "quote_snapshot")
    derivative_rows = _rows_for(results, "crypto_derivative_metric")
    onchain_rows = _rows_for(results, "crypto_onchain_metric")
    event_rows = _rows_for(results, "event_calendar") + _rows_for(results, "company_news")
    macro_rows = _rows_for(results, "macro_series") + _rows_for(results, "macro_news")

    market_payload = _market_payload(quote_rows=quote_rows, candles=candles)
    return CryptoLensDomains(
        market=market_payload,
        ohlcv={"rows": len(candles), "candles": candles} if candles else None,
        derivatives=_derivatives_payload(derivative_rows),
        liquidation_map=_liquidation_payload(derivative_rows, current_price=_to_float((market_payload or {}).get("price"))),
        onchain=_onchain_payload(onchain_rows),
        macro=_macro_payload(macro_rows),
        events=_events_payload(event_rows),
        ahr999=_ahr999_payload(onchain_rows),
    )


def _domain_status_from_results(
    results: Sequence[DataResult],
    *,
    domains: CryptoLensDomains,
    ticker: str,
) -> dict[str, CryptoLensDomainStatus]:
    status: dict[str, CryptoLensDomainStatus] = {}
    domain_payloads = domains.to_mapping()
    for domain in CRYPTO_LENS_DOMAIN_KEYS:
        if domain == "ahr999" and not ticker.startswith("BTC"):
            status[domain] = CryptoLensDomainStatus.NOT_APPLICABLE
            continue
        if domain_payloads.get(domain):
            status[domain] = _status_from_data_type_results(results, _DOMAIN_STATUS_DATA_TYPES.get(domain, ()))
        else:
            status[domain] = _missing_or_error_status(results, _DOMAIN_STATUS_DATA_TYPES.get(domain, ()))
    return status


def _status_from_data_type_results(results: Sequence[DataResult], data_types: tuple[str, ...]) -> CryptoLensDomainStatus:
    statuses = [result.status for result in results if _data_type_from_request_id(result.request_id) in data_types]
    if any(status == DataResultStatus.ERROR for status in statuses):
        return CryptoLensDomainStatus.PARTIAL if any(status == DataResultStatus.READY for status in statuses) else CryptoLensDomainStatus.ERROR
    if any(status == DataResultStatus.PARTIAL for status in statuses):
        return CryptoLensDomainStatus.PARTIAL
    return CryptoLensDomainStatus.READY


def _missing_or_error_status(results: Sequence[DataResult], data_types: tuple[str, ...]) -> CryptoLensDomainStatus:
    statuses = [result.status for result in results if _data_type_from_request_id(result.request_id) in data_types]
    if any(status == DataResultStatus.ERROR for status in statuses):
        return CryptoLensDomainStatus.ERROR
    return CryptoLensDomainStatus.MISSING


def _rows_for(results: Sequence[DataResult], dataset: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for result in results:
        if _data_type_from_request_id(result.request_id) == dataset:
            for row in result.rows:
                normalized = dict(row)
                if result.attempt_refs:
                    normalized["_attempt_refs"] = tuple(str(ref) for ref in result.attempt_refs)
                rows.append(normalized)
    return rows


def _data_type_from_request_id(request_id: str) -> str:
    parts = request_id.split(":")
    return parts[4] if len(parts) >= 5 else ""


def _all_gaps(results: Sequence[DataResult]) -> list[DataGap]:
    return [gap for result in results for gap in result.gaps]


def _gap_matches_domain_text(gap: DataGap, domain_key: str) -> bool:
    text = f"{gap.request_id} {gap.human_readable}".lower()
    return domain_key.lower() in text


def _ohlcv_candles(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    candles: list[dict[str, Any]] = []
    for row in rows:
        close = _to_float(row.get("close"))
        if close is None:
            continue
        candles.append(
            {
                "date": row.get("date") or row.get("period_start") or row.get("timestamp"),
                "time": row.get("timestamp") or row.get("date") or row.get("period_start"),
                "open": _to_float(row.get("open")),
                "high": _to_float(row.get("high")),
                "low": _to_float(row.get("low")),
                "close": close,
                "volume": _to_float(row.get("volume")),
            }
        )
    return sorted(candles, key=lambda item: str(item.get("time") or item.get("date") or ""))


def _market_payload(*, quote_rows: Sequence[Mapping[str, Any]], candles: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    latest_quote = _latest_row(quote_rows)
    latest_candle = _latest_row(candles)
    price = _to_float((latest_quote or {}).get("price")) or _to_float((latest_candle or {}).get("close"))
    if price is None:
        return None
    return {
        "price": price,
        "change_pct": _to_float((latest_quote or {}).get("change_pct")),
        "volume_24h": _to_float((latest_quote or {}).get("volume")) or _to_float((latest_candle or {}).get("volume")),
    }


def _derivatives_payload(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    if not rows:
        return None
    latest_values: dict[str, Any] = {}
    for row in rows:
        for source, target in (
            ("open_interest", "oi"),
            ("funding_rate", "funding"),
            ("long_short_ratio", "long_short_ratio"),
            ("net_inflow", "net_inflow"),
            ("taker_buy_sell_ratio", "taker_buy_sell_ratio"),
            ("cvd", "cvd"),
        ):
            value = _to_float(row.get(source))
            if value is not None:
                latest_values[target] = value
                unit = row.get(f"{source}_unit") or _inferred_derivative_unit(source, row)
                if unit is not None:
                    latest_values[f"{target}_unit"] = str(unit)
                source_field = row.get(f"{source}_source_field")
                if source_field is not None:
                    latest_values[f"{target}_source_field"] = str(source_field)
        buy = _to_float(row.get("taker_buy_volume"))
        sell = _to_float(row.get("taker_sell_volume"))
        if buy is not None or sell is not None:
            latest_values["taker_buy_volume"] = buy
            latest_values["taker_sell_volume"] = sell
            taker_unit = row.get("taker_volume_unit")
            if taker_unit is not None:
                latest_values["taker_volume_unit"] = str(taker_unit)
    return latest_values or None


def _inferred_derivative_unit(source: str, row: Mapping[str, Any]) -> str | None:
    if source == "funding_rate":
        return "percent"
    if source == "open_interest":
        return "USD"
    if source == "net_inflow" and _row_attempts_include(row, "futures_coin_netflow"):
        return "USD"
    return None


def _row_attempts_include(row: Mapping[str, Any], endpoint_id: str) -> bool:
    return any(endpoint_id in str(ref) for ref in row.get("_attempt_refs") or ())


def _liquidation_payload(rows: Sequence[Mapping[str, Any]], *, current_price: float | None = None) -> Mapping[str, Any] | None:
    heatmap_rows = [
        row for row in rows
        if _to_float(row.get("liquidation_price")) is not None and _to_float(row.get("liquidation_size")) is not None
    ]
    valid_heatmap_rows = [
        row for row in heatmap_rows
        if _valid_liquidation_price(_to_float(row.get("liquidation_price")), current_price=current_price)
    ]
    if not valid_heatmap_rows:
        return None
    return {
        "heatmap_sample_count": len(heatmap_rows),
        "heatmap_points": tuple(_liquidation_heatmap_point(row) for row in valid_heatmap_rows[:20]),
        "invalid_heatmap_sample_count": len(heatmap_rows) - len(valid_heatmap_rows),
    }


def _valid_liquidation_price(price: float | None, *, current_price: float | None) -> bool:
    if price is None or price <= 0:
        return False
    if current_price is None or current_price <= 0:
        return True
    return current_price * 0.05 <= price <= current_price * 20.0


def _liquidation_heatmap_point(row: Mapping[str, Any]) -> Mapping[str, Any]:
    return {
        "price": _to_float(row.get("liquidation_price")),
        "size": _to_float(row.get("liquidation_size")),
        "side": row.get("side"),
        "price_unit": row.get("liquidation_price_unit") or _quote_from_symbol(row.get("symbol_id")) or "USDT",
        "size_unit": row.get("liquidation_size_unit") or "USD",
    }


def _quote_from_symbol(value: object) -> str | None:
    symbol = str(value or "").strip().upper()
    for quote in ("USDT", "USDC", "BUSD", "USD"):
        if symbol.endswith(quote) and len(symbol) > len(quote):
            return quote
    return None


def _onchain_payload(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    payload: dict[str, Any] = {}
    whale_count = 0
    for row in rows:
        metric = str(row.get("metric") or "").strip().lower()
        value = _to_float(row.get("value"))
        if value is None:
            continue
        if metric == "exchange_balance":
            payload["exchange_balance"] = value
        elif metric == "spot_coin_netflow":
            payload["exchange_netflow"] = value
            if row.get("value_unit") is not None:
                payload["exchange_netflow_unit"] = str(row.get("value_unit"))
            if row.get("value_source_field") is not None:
                payload["exchange_netflow_source_field"] = str(row.get("value_source_field"))
        elif metric == "whale_transfer":
            whale_count += 1
            payload["whale_transfer_value"] = value
        elif metric in {"active_addresses", "mvrv", "sth_sopr", "lth_sopr", "nupl", "stablecoin_exchange_netflow", "stablecoin_market_cap"}:
            payload[metric] = value
    if whale_count:
        payload["whale_large_tx_count"] = whale_count
        payload["whale_large_tx_count_unit"] = "count"
    return payload or None


def _ahr999_payload(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    for row in reversed(rows):
        if str(row.get("metric") or "").strip().lower() == "ahr999":
            value = _to_float(row.get("value"))
            if value is not None:
                value_unit = str(row.get("value_unit") or "").strip() or "dimensionless"
                return {"value": value, "value_unit": value_unit, "fitted_price": _to_float(row.get("fitted_price"))}
    return None


def _macro_payload(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    if not rows:
        return None
    payload: dict[str, Any] = {"sample_count": len(rows)}
    for row in rows:
        series_id = str(row.get("series_id") or row.get("title") or "").upper()
        value = _to_float(row.get("value"))
        if "DXY" in series_id and value is not None:
            payload["dxy"] = value
        if "DGS10" in series_id and value is not None:
            payload["us10y"] = value
    return payload


def _events_payload(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    if not rows:
        return None
    return {"sample_count": len(rows), "latest": dict(_latest_row(rows) or {})}


def _latest_row(rows: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    return rows[-1] if rows else None


def _to_float(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def synthetic_gap(
    *,
    domain: str,
    message: str,
    as_of: str,
) -> DataGap:
    return DataGap.by_reason(
        GapReason.FIELD_MISSING,
        request_id=f"crypto_lens:{domain}",
        market=Market.CRYPTO,
        data_type=domain,
        granularity="analysis",
        severity=GapSeverity.WARN,
        required_fields=(domain,),
        message=message,
        as_of=None,
    )
