from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any, Mapping

from claw_trade.data_gateway.models import DataRequest, DataResult, DataResultStatus, Market
from claw_trade.data_gateway.runtime import build_data_api_from_env


class DataLayerAgentToolError(RuntimeError):
    pass


def search_ticker(query: str, *, limit: int = 5) -> list[dict[str, str]]:
    clean = _clean_ticker(query)
    if not clean:
        return []
    if clean.isdigit() and len(clean) in {5, 6}:
        return [{"code": clean, "name": ""}][:limit]
    return []


def load_price_rows(*, ticker: str, start_date: str | None = None, end_date: str | None = None) -> list[dict[str, Any]]:
    market, symbol = _market_symbol(ticker)
    start, end = _date_range(start_date=start_date, end_date=end_date)
    result = _fetch_one(
        _request(
            request_id=f"agent:market:daily_bar:{symbol}:{start}:{end}",
            market=market,
            symbol=symbol,
            data_type="daily_bar",
            granularity="daily",
            fields=("open", "high", "low", "close", "volume", "amount"),
            start=start,
            end=end,
            consumer_id="alphaear-stock",
        )
    )
    rows = [_normalize_price_row(row) for row in result.rows]
    rows = [row for row in rows if row]
    if not rows:
        raise DataLayerAgentToolError(f"data_gateway_missing: no price rows for {symbol}")
    return rows


def load_price_frame(*, ticker: str, start_date: str, end_date: str):
    import pandas as pd

    return pd.DataFrame(load_price_rows(ticker=ticker, start_date=start_date, end_date=end_date))


def load_fundamentals(ticker: str) -> dict[str, Any]:
    market, symbol = _market_symbol(ticker)
    if market == Market.CRYPTO:
        raise DataLayerAgentToolError("data_gateway_unsupported: stock skill does not load crypto fundamentals")
    requests = [
        _request(
            request_id=f"agent:fundamental:{data_type}:{symbol}",
            market=market,
            symbol=symbol,
            data_type=data_type,
            granularity=granularity,
            fields=fields,
            consumer_id="alphaear-stock",
        )
        for data_type, granularity, fields in (
            ("valuation_metric", "realtime", ("pe", "pb", "market_cap", "currency")),
            ("financial_metric", "quarterly", ("roe", "gross_margin", "debt_ratio", "eps")),
            ("financial_statement", "quarterly", ("revenue", "net_profit", "operating_cashflow")),
        )
    ]
    results = _fetch_many(requests)
    rows = [dict(row) for result in results for row in result.rows]
    if not rows:
        raise DataLayerAgentToolError(_gap_message("data_gateway_missing: no fundamentals rows", results))
    merged: dict[str, Any] = {"code": symbol, "rows": rows, "source": "data_gateway"}
    for row in rows:
        for key, value in row.items():
            if value is not None and key not in merged:
                merged[key] = value
    return merged


def load_cn_a_news_pack(*, ticker: str, start_date: str | None, end_date: str | None) -> dict[str, Any]:
    market, symbol = _market_symbol(ticker)
    start, end = _date_range(start_date=start_date, end_date=end_date)
    results = _fetch_many(
        [
            _request(
                request_id=f"agent:news:company_news:{symbol}",
                market=market,
                symbol=symbol,
                data_type="company_news",
                granularity="event",
                fields=("title", "published_at", "source", "summary", "url"),
                start=start,
                end=end,
                consumer_id="alphaear-stock",
            ),
            _request(
                request_id=f"agent:news:macro_news:{symbol}",
                market=market,
                symbol=symbol,
                data_type="macro_news",
                granularity="event",
                fields=("title", "published_at", "region", "summary", "url"),
                start=start,
                end=end,
                consumer_id="alphaear-stock",
            ),
        ]
    )
    company_news = [dict(row) for result in results if ":company_news:" in result.request_id for row in result.rows]
    macro_news = [dict(row) for result in results if ":macro_news:" in result.request_id for row in result.rows]
    warnings = _warnings(results)
    status = "ok" if company_news and macro_news else "partial" if company_news or macro_news else "failed"
    return {
        "ok": bool(company_news or macro_news),
        "ticker": symbol,
        "start_date": start_date,
        "end_date": end_date,
        "data": {"company_news": company_news, "macro_news": macro_news, "limitations": warnings},
        "quality": {
            "status": status,
            "is_partial": status == "partial",
            "warnings": warnings,
            "source_used": "data_gateway",
        },
    }


def load_cn_a_social_pack(*, ticker: str) -> dict[str, Any]:
    market, symbol = _market_symbol(ticker)
    result = _fetch_one(
        _request(
            request_id=f"agent:social:social_signal:{symbol}",
            market=market,
            symbol=symbol,
            data_type="social_signal",
            granularity="event",
            fields=("source", "timestamp", "score", "sentiment", "symbol_id"),
            consumer_id="alphaear-stock",
        ),
        allow_partial=True,
    )
    rows = [dict(row) for row in result.rows]
    warnings = _warnings((result,))
    return {
        "ok": bool(rows),
        "ticker": symbol,
        "data": {
            "heat_snapshot": {},
            "hot_keywords": [row for row in rows if row.get("keyword")],
            "related_hot_stocks": [row for row in rows if row.get("related_symbol")],
            "top_rank_match": rows,
            "limitations": warnings,
        },
        "quality": {
            "status": "partial" if rows else "failed",
            "is_partial": True,
            "warnings": warnings,
            "source_used": "data_gateway",
        },
    }


def _fetch_one(request: DataRequest, *, allow_partial: bool = False) -> DataResult:
    result = build_data_api_from_env().get_data(request)
    if result.status == DataResultStatus.READY or (allow_partial and result.status == DataResultStatus.PARTIAL):
        return result
    if result.rows and result.status == DataResultStatus.PARTIAL:
        return result
    raise DataLayerAgentToolError(_gap_message(f"data_gateway_{result.status.value}: {request.data_type}", (result,)))


def _fetch_many(requests: list[DataRequest]) -> list[DataResult]:
    results = build_data_api_from_env().get_data_batch(tuple(requests))
    usable = [result for result in results if result.status in {DataResultStatus.READY, DataResultStatus.PARTIAL} and result.rows]
    if usable:
        return results
    raise DataLayerAgentToolError(_gap_message("data_gateway_missing: no usable rows", results))


def _request(
    *,
    request_id: str,
    market: Market,
    symbol: str,
    data_type: str,
    granularity: str,
    fields: tuple[str, ...],
    consumer_id: str,
    start: date | None = None,
    end: date | None = None,
) -> DataRequest:
    return DataRequest(
        request_id=request_id,
        market=market,
        symbol_id=symbol,
        exchange=_exchange(market=market, symbol=symbol),
        currency=_currency(market),
        timezone=_timezone(market),
        calendar=_calendar(market),
        data_type=data_type,
        granularity=granularity,
        fields=fields,
        date_range_start=start,
        date_range_end=end,
        freshness_policy="trading_day",
        consumer="report",
        consumer_id=consumer_id,
        as_of=datetime.now(tz=UTC),
    )


def _market_symbol(ticker: str) -> tuple[Market, str]:
    clean = _clean_ticker(ticker)
    if not clean:
        raise DataLayerAgentToolError("ticker is required")
    if clean.isdigit() and len(clean) == 6:
        suffix = "SH" if clean.startswith(("5", "6", "9")) else "SZ"
        return Market.CN_A, f"{clean}.{suffix}"
    if clean.isdigit() and len(clean) == 5:
        return Market.HK, f"{clean}.HK"
    return Market.US, clean.upper().removesuffix(".US")


def _clean_ticker(ticker: str) -> str:
    value = str(ticker or "").strip().upper()
    if value.endswith((".SZ", ".SH", ".HK", ".US")):
        return value.rsplit(".", 1)[0]
    digits = "".join(char for char in value if char.isdigit())
    if digits and len(digits) in {5, 6}:
        return digits
    return value


def _date_range(*, start_date: str | None, end_date: str | None) -> tuple[date, date]:
    end = _parse_date(end_date) or datetime.now(tz=UTC).date()
    start = _parse_date(start_date) or (end - timedelta(days=90))
    return start, end


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    digits = "".join(char for char in text if char.isdigit())
    if len(digits) >= 8:
        return date(int(digits[:4]), int(digits[4:6]), int(digits[6:8]))
    return date.fromisoformat(text[:10])


def _normalize_price_row(row: Mapping[str, Any]) -> dict[str, Any]:
    date_value = row.get("date") or row.get("period_start") or row.get("trade_date")
    if not date_value:
        return {}
    close = _number(row.get("close"))
    previous_close = _number(row.get("pre_close") or row.get("previous_close"))
    change_pct = _number(row.get("change_pct") or row.get("pct_chg"))
    if change_pct is None and close is not None and previous_close:
        change_pct = (close - previous_close) / previous_close * 100
    return {
        "date": str(date_value)[:10],
        "open": _number(row.get("open")),
        "high": _number(row.get("high")),
        "low": _number(row.get("low")),
        "close": close,
        "volume": _number(row.get("volume") or row.get("vol")),
        "change_pct": change_pct or 0.0,
    }


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _warnings(results: tuple[DataResult, ...] | list[DataResult]) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for result in results:
        for gap in result.gaps:
            warnings.append(
                {
                    "type": str(getattr(gap.reason, "value", gap.reason)),
                    "provider": ",".join(gap.provider_ids_tried),
                    "message": gap.human_readable,
                }
            )
    return warnings


def _gap_message(prefix: str, results: tuple[DataResult, ...] | list[DataResult]) -> str:
    details = [gap.human_readable for result in results for gap in result.gaps]
    if details:
        return f"{prefix}: {'; '.join(details[:3])}"
    return prefix


def _exchange(*, market: Market, symbol: str) -> str | None:
    if market == Market.CN_A:
        return symbol.rsplit(".", 1)[-1]
    if market == Market.HK:
        return "HKEX"
    if market == Market.US:
        return "US"
    return None


def _currency(market: Market) -> str:
    return {Market.CN_A: "CNY", Market.HK: "HKD", Market.US: "USD", Market.CRYPTO: "USD"}[market]


def _timezone(market: Market) -> str:
    return {Market.CN_A: "Asia/Shanghai", Market.HK: "Asia/Hong_Kong", Market.US: "America/New_York", Market.CRYPTO: "UTC"}[market]


def _calendar(market: Market) -> str:
    return {Market.CN_A: "SSE", Market.HK: "HKEX", Market.US: "NYSE", Market.CRYPTO: "CRYPTO_24_7"}[market]
