from __future__ import annotations

import importlib.util
import json
import sys
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from claw_trade.data_gateway.models import DataGap, DataRequest, DataResult, DataResultStatus, Market
from claw_trade.data_gateway.runtime import build_data_api_from_env

_DatasetSpec = tuple[str, str, tuple[str, ...]]


_CN_A_DOMAIN_DATASETS: dict[str, tuple[_DatasetSpec, ...]] = {
    "market": (
        ("daily_bar", "daily", ("date", "open", "high", "low", "close", "volume", "amount")),
        ("intraday_bar", "intraday", ("timestamp", "open", "high", "low", "close", "volume")),
        ("quote_snapshot", "realtime", ("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id")),
        ("order_book_snapshot", "realtime", ("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id")),
        ("capital_flow", "daily", ("date", "main_net", "symbol_id")),
        ("sector_snapshot", "event", ("sector_name", "main_net", "timestamp")),
    ),
    "fundamental": (
        ("financial_statement", "quarterly", ("period", "revenue", "net_income", "assets", "liabilities", "cash_flow")),
        ("financial_metric", "quarterly", ("roe", "roa", "gross_margin", "debt_ratio", "eps")),
        ("valuation_metric", "daily", ("pe", "pb", "ps", "market_cap")),
        ("valuation_metric", "realtime", ("market_cap", "price", "symbol_id")),
    ),
    "news": (
        ("company_news", "event", ("title", "published_at", "source", "summary", "url")),
        ("macro_news", "event", ("title", "published_at", "region", "summary", "url")),
        ("official_filing", "event", ("title", "published_at", "url", "source", "body_ref")),
    ),
    "social": (
        ("social_signal", "event", ("source", "timestamp", "question", "answer", "symbol_id")),
        ("social_signal", "event", ("source", "timestamp", "keyword", "score", "symbol_id")),
        ("social_signal", "event", ("source", "timestamp", "metrics", "symbol_id")),
        ("social_signal", "event", ("source", "timestamp", "related_symbol", "change_pct", "symbol_id")),
        ("social_signal", "event", ("source", "timestamp", "symbol_id", "name", "topic", "reason", "change_pct", "turnover_rate", "amount", "volume", "large_order_net")),
        ("social_signal", "event", ("source", "timestamp", "symbol_id", "topic", "concept", "industry", "region", "change_pct", "description")),
    ),
    "policy": (
        ("event_calendar", "event", ("event_type", "event_date", "title", "source")),
        ("official_filing", "event", ("title", "published_at", "url", "source", "body_ref")),
    ),
    "hot_money": (("hot_money_event", "event", ("trade_date", "seat", "buy_amount", "sell_amount", "symbol_id")),),
    "lockup": (("lockup_event", "event", ("unlock_date", "shares", "market_value", "holder")),),
}

_US_DOMAIN_DATASETS: dict[str, tuple[_DatasetSpec, ...]] = {
    "market": (
        ("daily_bar", "daily", ("open", "high", "low", "close", "volume")),
        ("quote_snapshot", "realtime", ("price", "change", "change_pct", "volume", "timestamp", "symbol_id")),
    ),
    "fundamental": (
        ("financial_statement", "quarterly", ("period", "revenue", "net_income", "assets", "liabilities", "cash_flow")),
        ("financial_metric", "quarterly", ("roe", "gross_margin", "profit_margin", "eps", "revenue_growth")),
        ("financial_metric", "quarterly", ("roe", "roa", "profit_margin", "eps")),
        ("valuation_metric", "realtime", ("pe", "pb", "ps", "market_cap")),
    ),
    "news": (
        ("company_news", "event", ("title", "published_at", "source", "summary", "url")),
        ("macro_news", "event", ("title", "published_at", "source", "summary", "url", "region")),
        ("official_filing", "event", ("title", "published_at", "source", "url", "symbol_id")),
        ("macro_series", "monthly", ("series_id", "date", "value", "unit", "region")),
    ),
    "social": (("social_signal", "event", ("source", "timestamp", "message", "sentiment", "symbol_id")),),
    "policy": (),
    "hot_money": (),
    "lockup": (),
}

_HK_DOMAIN_DATASETS: dict[str, tuple[_DatasetSpec, ...]] = {
    "market": (("daily_bar", "daily", ("open", "high", "low", "close", "volume")),),
    "fundamental": (
        ("financial_statement", "quarterly", ("period", "revenue", "net_income")),
        ("financial_metric", "quarterly", ("roe", "gross_margin", "eps")),
        ("financial_metric", "quarterly", ("roe", "eps", "gross_profit")),
        ("valuation_metric", "realtime", ("pe", "market_cap")),
        ("valuation_metric", "daily", ("price", "market_cap", "symbol_id")),
    ),
    "news": (
        ("company_news", "event", ("title", "published_at", "source", "summary", "url")),
        ("macro_news", "event", ("title", "published_at", "source", "summary", "url", "region")),
        ("official_filing", "event", ("title", "published_at", "source", "url", "symbol_id")),
    ),
    "social": (),
    "policy": (
        ("event_calendar", "event", ("event_date", "event_type", "title", "source", "url", "symbol_id")),
        ("official_filing", "event", ("title", "published_at", "source", "url", "symbol_id")),
    ),
    "hot_money": (),
    "lockup": (),
}

_DOMAIN_DATASETS = _CN_A_DOMAIN_DATASETS
_REPORT_PREFETCH_DOMAINS = ("market", "fundamental", "news", "social")
_US_DEFAULT_FRED_SERIES = ("FEDFUNDS", "CPIAUCSL", "UNRATE", "DGS10")

_CRYPTO_DOMAIN_DATASETS: dict[str, tuple[_DatasetSpec, ...]] = {
    "market": (
        ("daily_bar", "daily", ("open", "high", "low", "close", "volume", "amount")),
        ("intraday_bar", "1h", ("open", "high", "low", "close", "volume", "amount")),
        ("quote_snapshot", "realtime", ("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id")),
        ("order_book_snapshot", "realtime", ("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id")),
        ("crypto_derivative_metric", "realtime", ("open_interest", "timestamp", "symbol_id")),
        ("crypto_derivative_metric", "1h", ("funding_rate", "timestamp", "symbol_id")),
        ("crypto_derivative_metric", "1h", ("long_short_ratio", "timestamp", "symbol_id")),
        ("crypto_derivative_metric", "1h", ("taker_buy_volume", "taker_sell_volume", "taker_buy_sell_ratio", "timestamp", "symbol_id")),
        ("crypto_derivative_metric", "1h", ("long_liquidation", "short_liquidation", "liquidation_value", "timestamp", "symbol_id")),
        ("crypto_derivative_metric", "realtime", ("net_inflow", "timestamp", "symbol_id")),
        ("order_book_snapshot", "1h", ("bid_price", "bid_size", "ask_price", "ask_size", "timestamp")),
        ("crypto_onchain_metric", "daily", ("timestamp", "metric", "value", "chain")),
        ("crypto_onchain_metric", "event", ("timestamp", "metric", "value", "chain")),
        ("crypto_onchain_metric", "realtime", ("timestamp", "metric", "value", "chain")),
    ),
    "fundamental": (
        ("valuation_metric", "realtime", ("price", "market_cap", "fdv", "circulating_supply", "total_supply", "volume")),
        ("defi_metric", "realtime", ("tvl", "chains", "category", "symbol_id")),
        ("crypto_onchain_metric", "daily", ("timestamp", "metric", "value", "chain")),
        ("crypto_onchain_metric", "realtime", ("timestamp", "metric", "value", "chain")),
    ),
    "news": (
        ("company_news", "event", ("title", "published_at", "source", "summary", "url")),
        ("macro_news", "event", ("title", "published_at", "source", "summary", "url", "region")),
    ),
    "social": (("social_signal", "event", ("source", "timestamp", "score", "sentiment", "symbol_id")),),
}

_ROW_FIELD_EXCLUDE = {
    "dataset",
    "market",
    "granularity",
    "exchange",
    "currency",
    "timezone",
    "calendar",
    "base_asset",
    "quote_asset",
    "provider_lineage",
    "source_raw_refs",
    "quality_flags",
    "schema_id",
    "field_set",
    "source_roles",
}

_MARKET_DEFAULTS: dict[Market, dict[str, str | None]] = {
    Market.CN_A: {
        "exchange": "SSE",
        "currency": "CNY",
        "timezone": "Asia/Shanghai",
        "calendar": "CN_A_SSE_SZSE",
    },
    Market.US: {
        "exchange": "NASDAQ",
        "currency": "USD",
        "timezone": "America/New_York",
        "calendar": "US_NYSE_NASDAQ",
    },
    Market.HK: {
        "exchange": "XHKG",
        "currency": "HKD",
        "timezone": "Asia/Hong_Kong",
        "calendar": "HK_XHKG",
    },
    Market.CRYPTO: {
        "exchange": "BINANCE",
        "currency": "USDT",
        "timezone": "UTC",
        "calendar": "CRYPTO_24_7",
    },
}


@dataclass(frozen=True)
class _SymbolParts:
    symbol_id: str
    exchange: str | None
    currency: str
    base_asset: str | None = None
    quote_asset: str | None = None


class ReportDataPrefetcher:
    def prefetch_report(self, state: Any) -> dict[str, Any]:
        return run_report_data_prefetch(
            getattr(state, "request"),
            run_id=str(getattr(state, "run_id")),
            evidence_root=Path(getattr(state, "run_dir")),
        )


def run_frontline_data_pack(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    domain = str(runtime_context.get("pack_domain") or "").strip()
    if domain not in _DOMAIN_DATASETS:
        return _error_payload("data_pack_domain_unsupported", f"unsupported data pack domain: {domain}")

    try:
        market = Market(str(tool_input.get("market") or "").strip().upper())
    except ValueError:
        return _error_payload("invalid_request", f"unsupported market: {tool_input.get('market')}")

    prefetch = _load_report_prefetch_results(runtime_context=runtime_context, market=market, domain=domain)
    if prefetch.error is not None:
        return _error_payload(prefetch.error[0], prefetch.error[1])
    if prefetch.results is not None:
        results = prefetch.results
    else:
        try:
            with redirect_stdout(sys.stderr):
                api = build_data_api_from_env()
                requests = _build_requests(tool_input=tool_input, runtime_context=runtime_context, market=market, domain=domain)
                results = api.get_data_batch(requests)
        except Exception as exc:
            return _error_payload("data_layer_runtime_blocked", str(exc))
    results = _validate_report_data_results(results=results, market=market, domain=domain)

    status = _aggregate_status(results)
    chart_payload = _market_chart_payload(
        tool_input=tool_input,
        runtime_context=runtime_context,
        results=results,
    ) if domain == "market" else {}
    return {
        "ok": True,
        "schema_version": "data_result_pack.v1",
        "tool_name": runtime_context.get("tool_name"),
        "status": status,
        "readiness": {"status": status},
        "model_visible_text": _model_visible_text(
            tool_input=tool_input,
            runtime_context=runtime_context,
            market=market,
            domain=domain,
            status=status,
            results=results,
            chart_payload=chart_payload,
        ),
        "data_results": [item.model_dump(mode="json") for item in results],
        "dataset_refs": tuple(_dedupe(ref for item in results for ref in item.dataset_refs)),
        "raw_refs": tuple(_dedupe(ref for item in results for ref in item.raw_refs)),
        "attempt_refs": tuple(_dedupe(ref for item in results for ref in item.attempt_refs)),
        **chart_payload,
    }


@dataclass(frozen=True)
class _PrefetchLoadResult:
    results: tuple[DataResult, ...] | None = None
    error: tuple[str, str] | None = None


def _load_report_prefetch_results(
    *,
    runtime_context: Mapping[str, Any],
    market: Market,
    domain: str,
) -> _PrefetchLoadResult:
    manifest_path = _report_prefetch_manifest_path(runtime_context)
    prefetch_required = bool(runtime_context.get("report_prefetch_required"))
    if manifest_path is None:
        if prefetch_required:
            return _PrefetchLoadResult(
                error=("report_prefetch_manifest_missing", "report_prefetch_required=true but manifest path is missing")
            )
        return _PrefetchLoadResult()
    if not manifest_path.exists():
        return _PrefetchLoadResult(
            error=("report_prefetch_manifest_missing", f"report prefetch manifest not found: {manifest_path}")
        )
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            return _PrefetchLoadResult(error=("report_prefetch_manifest_invalid", "manifest payload must be an object"))
        if payload.get("schema_version") != "report_data_prefetch.v1":
            return _PrefetchLoadResult(error=("report_prefetch_manifest_invalid", "unsupported manifest schema_version"))
        if payload.get("ok") is not True:
            reason = str(payload.get("reason") or "report prefetch manifest is not ok")
            return _PrefetchLoadResult(error=("report_prefetch_manifest_not_ready", reason))
        runtime_run_id = str(runtime_context.get("run_id") or "").strip()
        manifest_run_id = str(payload.get("run_id") or "").strip()
        if runtime_run_id and manifest_run_id and runtime_run_id != manifest_run_id:
            return _PrefetchLoadResult(
                error=("report_prefetch_manifest_mismatch", f"manifest run_id {manifest_run_id} != runtime run_id {runtime_run_id}")
            )
        if str(payload.get("market") or "").strip().upper() != market.value:
            return _PrefetchLoadResult(error=("report_prefetch_manifest_mismatch", "manifest market does not match tool input"))
        domains = tuple(str(item) for item in payload.get("domains") or ())
        if domain not in domains:
            return _PrefetchLoadResult(error=("report_prefetch_manifest_mismatch", f"manifest does not contain domain: {domain}"))
        raw_results = payload.get("data_results")
        if not isinstance(raw_results, Sequence) or isinstance(raw_results, (str, bytes, bytearray)):
            return _PrefetchLoadResult(error=("report_prefetch_manifest_invalid", "manifest data_results must be a list"))
        results = tuple(
            DataResult.model_validate(item)
            for item in raw_results
            if isinstance(item, Mapping) and _result_domain(str(item.get("request_id") or "")) == domain
        )
    except Exception as exc:
        return _PrefetchLoadResult(error=("report_prefetch_manifest_invalid", str(exc)))
    if not results:
        return _PrefetchLoadResult(error=("report_prefetch_manifest_mismatch", f"manifest has no results for domain: {domain}"))
    return _PrefetchLoadResult(results=_validate_report_data_results(results=results, market=market, domain=domain))


def _report_prefetch_manifest_path(runtime_context: Mapping[str, Any]) -> Path | None:
    value = str(runtime_context.get("report_prefetch_manifest_path") or "").strip()
    if not value:
        return None
    return Path(value).expanduser()


def _result_domain(request_id: str) -> str:
    parts = request_id.split(":")
    if len(parts) < 5:
        return ""
    return parts[2]


def _result_dataset(request_id: str) -> str:
    parts = request_id.split(":")
    if len(parts) < 5:
        return ""
    return parts[4]


def run_report_data_prefetch(request: Any, *, run_id: str, evidence_root: Path) -> dict[str, Any]:
    evidence_path = evidence_root / "data-layer" / "report-prefetch.json"
    try:
        market = Market(str(getattr(request, "market", "") or "").strip().upper())
        tool_input = {
            "ticker": getattr(request, "ticker", ""),
            "market": getattr(request, "market", ""),
            "currency": getattr(request, "currency", ""),
            "current_date": getattr(request, "current_date", ""),
            "start_date": getattr(request, "start_date", ""),
            "end_date": getattr(request, "end_date", ""),
        }
        runtime_base = {
            "run_id": run_id,
            "call_id": "report-prefetch",
            "worker_id": "report_prefetch",
            "evidence_root": str(evidence_root),
        }
        requests: list[DataRequest] = []
        domains: list[str] = []
        for domain in _REPORT_PREFETCH_DOMAINS:
            if not _domain_datasets(domain=domain, market=market):
                continue
            domains.append(domain)
            requests.extend(
                _build_requests(
                    tool_input=tool_input,
                    runtime_context={**runtime_base, "pack_domain": domain},
                    market=market,
                    domain=domain,
                )
            )
        with redirect_stdout(sys.stderr):
            results = build_data_api_from_env().get_data_batch(tuple(requests))
        results = _validate_report_data_results(results=results, market=market, domain="*")
        payload = {
            "ok": True,
            "schema_version": "report_data_prefetch.v1",
            "run_id": run_id,
            "market": market.value,
            "domains": tuple(domains),
            "request_count": len(requests),
            "status": _aggregate_status(results),
            "dataset_refs": tuple(_dedupe(ref for item in results for ref in item.dataset_refs)),
            "raw_refs": tuple(_dedupe(ref for item in results for ref in item.raw_refs)),
            "attempt_refs": tuple(_dedupe(ref for item in results for ref in item.attempt_refs)),
            "data_results": [item.model_dump(mode="json") for item in results],
            "evidence_paths": (str(evidence_path),),
        }
    except Exception as exc:
        payload = {
            "ok": False,
            "schema_version": "report_data_prefetch.v1",
            "run_id": run_id,
            "category": "data_prefetch",
            "reason": str(exc),
            "evidence_paths": (str(evidence_path),),
        }
    _write_json(evidence_path, payload)
    return payload

def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

def _build_requests(
    *,
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
    market: Market,
    domain: str,
) -> tuple[DataRequest, ...]:
    symbol = _normalize_symbol(tool_input=tool_input, market=market)
    as_of = _resolve_as_of(tool_input.get("current_date"))
    start = _parse_date(tool_input.get("start_date"))
    end = _parse_date(tool_input.get("end_date"))
    run_id = str(runtime_context.get("run_id") or "run")
    call_id = str(runtime_context.get("call_id") or "call")
    worker_id = str(runtime_context.get("worker_id") or "report")

    requests: list[DataRequest] = []
    for index, (data_type, granularity, fields) in enumerate(_domain_datasets(domain=domain, market=market), start=1):
        request_symbols = _request_symbol_ids(market=market, data_type=data_type, default_symbol=symbol.symbol_id)
        for symbol_index, request_symbol in enumerate(request_symbols, start=1):
            request_suffix = f":{symbol_index}:{request_symbol}" if len(request_symbols) > 1 else ""
            requests.append(
                DataRequest(
                    request_id=f"{run_id}:{call_id}:{domain}:{index}:{data_type}{request_suffix}",
                    market=market,
                    symbol_id=request_symbol,
                    exchange=_request_exchange(market=market, data_type=data_type, default_exchange=symbol.exchange),
                    currency=symbol.currency,
                    timezone=str(_MARKET_DEFAULTS[market]["timezone"]),
                    calendar=_request_calendar(market=market, data_type=data_type),
                    base_asset=symbol.base_asset,
                    quote_asset=symbol.quote_asset,
                    data_type=data_type,
                    granularity=granularity,
                    fields=fields,
                    date_range_start=start,
                    date_range_end=end,
                    freshness_policy="trading_day",
                    consumer="report",
                    consumer_id=worker_id,
                    as_of=as_of,
                )
            )
    return tuple(requests)


def _request_symbol_ids(*, market: Market, data_type: str, default_symbol: str) -> tuple[str, ...]:
    if market == Market.US and data_type == "macro_series":
        return _US_DEFAULT_FRED_SERIES
    return (default_symbol,)


def _request_exchange(*, market: Market, data_type: str, default_exchange: str | None) -> str | None:
    if market == Market.US and data_type == "macro_series":
        return "FRED"
    return default_exchange


def _request_calendar(*, market: Market, data_type: str) -> str:
    if market == Market.US and data_type == "macro_series":
        return "US_FED"
    return str(_MARKET_DEFAULTS[market]["calendar"])


def _domain_datasets(*, domain: str, market: Market) -> tuple[_DatasetSpec, ...]:
    by_market: dict[Market, dict[str, tuple[_DatasetSpec, ...]]] = {
        Market.CN_A: _CN_A_DOMAIN_DATASETS,
        Market.US: _US_DOMAIN_DATASETS,
        Market.HK: _HK_DOMAIN_DATASETS,
        Market.CRYPTO: _CRYPTO_DOMAIN_DATASETS,
    }
    return by_market[market].get(domain, ())


def _validate_report_data_results(
    *,
    results: Sequence[DataResult],
    market: Market,
    domain: str,
) -> tuple[DataResult, ...]:
    return tuple(_validate_report_data_result(result=result, market=market, domain=domain) for result in results)


def _validate_report_data_result(*, result: DataResult, market: Market, domain: str) -> DataResult:
    expected_dataset = _result_dataset(result.request_id)
    if not expected_dataset:
        if not result.rows and not result.dataset_refs:
            return result
        return _data_integrity_error_result(
            result=result,
            market=market,
            data_type="unknown",
            granularity="unknown",
            required_fields=(),
            message="report_data_result_request_id_mismatch",
        )
    result_domain = _result_domain(result.request_id)
    expected_domain = result_domain if domain == "*" else domain
    specs = _expected_specs_for_result(market=market, domain=expected_domain, dataset=expected_dataset)
    if not specs:
        if not result.rows and not result.dataset_refs:
            return result
        return _data_integrity_error_result(
            result=result,
            market=market,
            data_type=expected_dataset,
            granularity="unknown",
            required_fields=(),
            message="report_data_result_unknown_dataset",
        )
    expected_fields = tuple(dict.fromkeys(field for _dataset, _granularity, fields in specs for field in fields))
    dataset_mismatch = _dataset_refs_mismatch(
        result.dataset_refs,
        expected_dataset=expected_dataset,
        rows_present=bool(result.rows),
    )
    row_mismatch = bool(result.rows) and any(
        not _row_matches_expected_spec(row, specs=specs) for row in result.rows
    )
    if not dataset_mismatch and not row_mismatch:
        return result

    reason = "report_data_result_dataset_mismatch" if dataset_mismatch else "report_data_result_field_mismatch"
    return _data_integrity_error_result(
        result=result,
        market=market,
        data_type=expected_dataset,
        granularity=specs[0][1],
        required_fields=expected_fields,
        message=reason,
    )


def _data_integrity_error_result(
    *,
    result: DataResult,
    market: Market,
    data_type: str,
    granularity: str,
    required_fields: Sequence[str],
    message: str,
) -> DataResult:
    gap = DataGap.by_reason(
        "data_integrity_failed",
        request_id=result.request_id,
        market=market,
        data_type=data_type,
        granularity=granularity,
        required_fields=tuple(required_fields),
        evidence_refs=tuple(result.dataset_refs) + tuple(result.raw_refs) + tuple(result.attempt_refs),
        message=message,
        as_of=result.as_of,
    )
    return result.model_copy(
        update={
            "status": DataResultStatus.ERROR,
            "rows": (),
            "dataset_refs": (),
            "raw_refs": (),
            "attempt_refs": (),
            "gaps": tuple(result.gaps) + (gap,),
        }
    )


def _expected_specs_for_result(*, market: Market, domain: str, dataset: str) -> tuple[_DatasetSpec, ...]:
    return tuple(spec for spec in _domain_datasets(domain=domain, market=market) if spec[0] == dataset)


def _dataset_refs_mismatch(dataset_refs: Sequence[str], *, expected_dataset: str, rows_present: bool = False) -> bool:
    if rows_present and not dataset_refs:
        return True
    for ref in dataset_refs:
        parts = str(ref).split(":")
        if len(parts) < 3 or parts[0] != "dataset" or not parts[1]:
            return True
        if parts[1] != expected_dataset:
            return True
    return False


def _row_matches_expected_spec(row: Any, *, specs: Sequence[_DatasetSpec]) -> bool:
    if not isinstance(row, Mapping):
        return False
    fields = {str(key) for key in row if str(key) not in _ROW_FIELD_EXCLUDE}
    for _dataset, _granularity, expected_fields in specs:
        required = {str(field) for field in expected_fields if str(field).strip()}
        if required and required.issubset(fields):
            return True
    return False


def _normalize_symbol(*, tool_input: Mapping[str, Any], market: Market) -> _SymbolParts:
    ticker = str(tool_input.get("ticker") or "").strip().upper()
    currency = str(tool_input.get("currency") or _MARKET_DEFAULTS[market]["currency"] or "").strip().upper()
    if not ticker:
        raise RuntimeError("ticker is required")

    if market == Market.CN_A:
        if "." not in ticker and len(ticker) == 6 and ticker.isdigit():
            suffix = ".SH" if ticker.startswith("6") else ".SZ"
            ticker = f"{ticker}{suffix}"
        exchange = "SSE" if ticker.endswith(".SH") else "SZSE" if ticker.endswith(".SZ") else "BSE" if ticker.endswith(".BJ") else None
        return _SymbolParts(symbol_id=ticker, exchange=exchange, currency=currency or "CNY")

    if market == Market.HK:
        raw = ticker.removesuffix(".HK")
        if raw.isdigit():
            ticker = f"{raw.zfill(5)}.HK"
        return _SymbolParts(symbol_id=ticker, exchange="XHKG", currency=currency or "HKD")

    if market == Market.US:
        return _SymbolParts(symbol_id=ticker, exchange=_MARKET_DEFAULTS[market]["exchange"], currency=currency or "USD")

    base, quote = _crypto_pair(ticker=ticker, currency=currency or "USDT")
    return _SymbolParts(
        symbol_id=f"{base}{quote}",
        exchange=str(_MARKET_DEFAULTS[market]["exchange"]),
        currency=quote,
        base_asset=base,
        quote_asset=quote,
    )


def _crypto_pair(*, ticker: str, currency: str) -> tuple[str, str]:
    normalized = ticker.replace("-", "/").replace(".", "/")
    if "/" in normalized:
        base, quote = normalized.split("/", 1)
        base = base.strip().upper()
        quote = quote.strip().upper()
        if base and quote:
            return base, quote
    for quote in ("FDUSD", "USDT", "USDC", "BUSD", "TUSD", "USD", "BTC", "ETH", "BNB"):
        if ticker.endswith(quote) and len(ticker) > len(quote):
            return ticker[: -len(quote)], quote
    quote = currency if currency in {"FDUSD", "USDT", "USDC", "BUSD", "TUSD", "USD", "BTC", "ETH", "BNB"} else "USDT"
    return ticker, quote


def _aggregate_status(results: Sequence[DataResult]) -> str:
    statuses = [item.status for item in results]
    if statuses and all(item == DataResultStatus.READY for item in statuses):
        return DataResultStatus.READY.value
    if any(item in {DataResultStatus.READY, DataResultStatus.PARTIAL} for item in statuses):
        return DataResultStatus.PARTIAL.value
    if statuses and all(item == DataResultStatus.ERROR for item in statuses):
        return DataResultStatus.ERROR.value
    return DataResultStatus.MISSING.value


def _model_visible_text(
    *,
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
    market: Market,
    domain: str,
    status: str,
    results: Sequence[DataResult],
    chart_payload: Mapping[str, Any] | None = None,
) -> str:
    ticker = str(tool_input.get("ticker") or "").strip()
    label = {
        "market": "行情",
        "fundamental": "基本面",
        "news": "新闻",
        "social": "舆情",
        "policy": "政策",
        "hot_money": "龙虎榜/资金",
        "lockup": "解禁",
    }.get(domain, domain)
    lines = [
        f"{ticker} 的 {market.value} {label}资料包结果：{_status_zh(status)}。",
        "这不是报告结论，只是资料包返回的数据、证据状态和缺口事实；缺失内容不能补写。",
    ]

    ready_results = [item for item in results if item.rows]
    if ready_results:
        lines.append("已返回的数据摘要：")
        for result in ready_results[:3]:
            lines.append(
                f"- {_dataset_label(_result_dataset(result.request_id) or result.request_id.split(':')[-1], market=market)}："
                f"{len(result.rows)} 行，字段覆盖 {', '.join(_row_fields(result.rows)) or '未声明'}。"
            )
            for row in result.rows[-5:]:
                summary = _row_summary(row, domain=domain)
                if summary:
                    lines.append(f"  - {summary}")

    if domain == "news" and ready_results:
        lines.append(
            "新闻资料包中的媒体/搜索线索只能证明来源返回过相关线索；"
            "其中涉及机构资金、宏观、链上或事件的说法，未经官方、交易所、监管或原始数据交叉验证前，不能升级为报告事实或投资结论。"
        )

    if domain == "market":
        lines.extend(_chart_brief_lines(chart_payload or {}))

    gap_lines = _gap_lines(results, market=market)
    if gap_lines:
        lines.append("数据缺口：")
        lines.extend(f"- {item}" for item in gap_lines[:12])

    attempt_count = sum(len(item.attempt_refs) for item in results)
    dataset_count = sum(len(item.dataset_refs) for item in results)
    raw_count = sum(len(item.raw_refs) for item in results)
    lines.append(
        "审计状态："
        f"已形成 {dataset_count} 个标准化数据引用、"
        f"{raw_count} 个原始或元数据引用、"
        f"{attempt_count} 个来源尝试记录。"
    )
    if not any(item.status == DataResultStatus.READY for item in results):
        lines.append("没有可用数据集时，只能写数据不可用和影响范围，不得用模型常识补出真实行情、基本面、新闻或舆情事实。")
    return "\n".join(lines)


def _chart_brief_lines(chart_payload: Mapping[str, Any]) -> list[str]:
    status = str(chart_payload.get("chart_status") or "missing")
    if status == "ready":
        files = [str(item) for item in chart_payload.get("chart_files", ()) if str(item)]
        lines = ["图表资产：已生成。"]
        if files:
            lines.append("图表文件：")
            lines.extend(f"- {item}" for item in files[:4])
        analysis = chart_payload.get("technical_analysis")
        if isinstance(analysis, Mapping):
            summary = analysis.get("summary")
            indicators = analysis.get("indicators")
            if isinstance(summary, Mapping):
                lines.append(
                    "本地技术摘要："
                    f"趋势 {_display_value(summary.get('trend'))}，"
                    f"成交量状态 {_display_value(summary.get('volume_state'))}，"
                    f"支撑 {_display_value(summary.get('support_levels'))}，"
                    f"压力 {_display_value(summary.get('resistance_levels'))}。"
                )
            if isinstance(indicators, Mapping):
                lines.append(f"本地指标快照：{_compact_json(indicators)}")
        return lines
    reason = _human_error(str(chart_payload.get("chart_error") or "market pack did not generate chart assets"))
    return [f"图表资产：缺失，原因：{reason}。"]


def _market_chart_payload(
    *,
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
    results: Sequence[DataResult],
) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    for result in results:
        if result.request_id.endswith(":daily_bar") and result.rows:
            rows.extend(dict(row) for row in result.rows)
            break
    if not rows:
        return {"chart_status": "missing", "chart_error": "no usable OHLCV rows"}

    try:
        import pandas as pd
    except ModuleNotFoundError:
        return {"chart_status": "missing", "chart_error": "chart_dependency_missing:pandas"}

    frame = pd.DataFrame(rows)
    if "date" not in frame.columns and "period_start" in frame.columns:
        frame["date"] = frame["period_start"]
    required = {"date", "open", "high", "low", "close", "volume"}
    missing = sorted(required - set(frame.columns))
    if missing:
        return {"chart_status": "missing", "chart_error": f"ohlcv_fields_missing:{','.join(missing)}"}

    try:
        indicator_engine = _load_techlab_module("indicator_engine")
        chart_engine = _load_techlab_module("chart_engine")
        ticker = _chart_ticker(tool_input=tool_input, rows=rows)
        bundle = indicator_engine.analyze_market_frame(frame, ticker=ticker)
        chart_dir = _chart_output_dir(runtime_context)
        chart_paths = chart_engine.render_market_charts(bundle.chart_frame, ticker=ticker, output_dir=chart_dir)
    except Exception as exc:
        return {"chart_status": "missing", "chart_error": str(exc)}

    return {
        "chart_status": "ready",
        "chart_files": tuple(str(path) for path in chart_paths),
        "technical_analysis": {
            "summary": bundle.summary,
            "indicators": bundle.indicators,
            "warnings": bundle.warnings,
        },
    }


def _load_techlab_module(module_name: str) -> Any:
    script_path = (
        Path(__file__).resolve().parents[3]
        / "agents"
        / "market_analyst"
        / "skills"
        / "alphaear-techlab"
        / "scripts"
        / f"{module_name}.py"
    )
    spec = importlib.util.spec_from_file_location(f"claw_trade_alphaear_techlab_{module_name}", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"techlab_module_unavailable:{module_name}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _chart_output_dir(runtime_context: Mapping[str, Any]) -> Path:
    evidence_root = Path(str(runtime_context.get("evidence_root") or "")).expanduser()
    if not str(evidence_root):
        raise RuntimeError("chart_evidence_root_missing")
    run_id = _safe_path_token(str(runtime_context.get("run_id") or "run"))
    call_id = _safe_path_token(str(runtime_context.get("call_id") or "call"))
    return evidence_root / "techlab" / "charts-local" / "runs" / run_id / call_id / "charts"


def _chart_ticker(*, tool_input: Mapping[str, Any], rows: Sequence[Mapping[str, Any]]) -> str:
    for row in reversed(rows):
        value = row.get("symbol_id")
        if value:
            return _safe_path_token(str(value))
    return _safe_path_token(str(tool_input.get("ticker") or "instrument"))


def _safe_path_token(value: str) -> str:
    safe = "".join(char if char.isalnum() or char in "._-" else "-" for char in value.strip())
    return safe.strip("-._") or "instrument"


def _compact_json(value: object) -> str:
    import json

    text = json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
    return text if len(text) <= 600 else text[:597].rstrip() + "..."


def _gap_lines(results: Sequence[DataResult], *, market: Market) -> list[str]:
    lines: list[str] = []
    for result in results:
        dataset = _dataset_label(_result_dataset(result.request_id) or result.request_id.split(":")[-1], market=market)
        for gap in result.gaps:
            reason = str(getattr(gap.reason, "value", gap.reason))
            required = "、".join(_field_label(field) for field in gap.required_fields)
            detail = f"{dataset}：{_gap_reason_label(reason)}"
            if required:
                detail = f"{detail}，缺少 {required}"
            human = _human_error(gap.human_readable)
            if human and human != _gap_reason_label(reason):
                detail = f"{detail}，说明：{human}"
            lines.append(detail)
    return list(_dedupe(lines))


def _row_fields(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields and key not in _ROW_FIELD_EXCLUDE:
                fields.append(str(key))
    return tuple(_field_label(field) for field in fields[:12])


def _row_summary(row: Mapping[str, Any], *, domain: str) -> str:
    date_value = row.get("date") or row.get("period_start") or row.get("published_at") or row.get("timestamp")
    parts: list[str] = []
    if date_value:
        parts.append(f"时间 {str(date_value)[:19]}")
    if domain == "news":
        source = row.get("source")
        parts.append("媒体/搜索线索已返回")
        if source is not None:
            parts.append(f"来源 {source}")
        return "，".join(parts[:4])
    for key in (
        "open",
        "high",
        "low",
        "close",
        "price",
        "change_pct",
        "volume",
        "amount",
        "market_cap",
        "fdv",
        "circulating_supply",
        "total_supply",
        "tvl",
        "open_interest",
        "funding_rate",
        "long_short_ratio",
        "long_liquidation",
        "short_liquidation",
        "liquidation_value",
        "net_inflow",
        "metric",
        "value",
        "chain",
        "source",
        "sentiment",
        "score",
    ):
        value = row.get(key)
        if value is not None:
            parts.append(f"{_field_label(key)} {value}")
    return "，".join(parts[:8])


def _dataset_label(value: str, *, market: Market | None = None) -> str:
    if market == Market.CRYPTO:
        crypto_labels = {
            "daily_bar": "日线行情",
            "intraday_bar": "小时行情",
            "quote_snapshot": "实时行情快照",
            "order_book_snapshot": "盘口快照",
            "valuation_metric": "币种市值与供应数据",
            "defi_metric": "DeFi 经营数据",
            "crypto_derivative_metric": "衍生品数据",
            "crypto_onchain_metric": "链上数据",
            "company_news": "项目新闻",
            "macro_news": "宏观新闻",
            "social_signal": "舆情信号",
            "event_calendar": "事件日历",
        }
        if value in crypto_labels:
            return crypto_labels[value]
    return {
        "daily_bar": "日线行情",
        "financial_statement": "财务报表",
        "financial_metric": "财务指标",
        "valuation_metric": "估值指标",
        "company_news": "公司新闻",
        "macro_news": "宏观新闻",
        "social_signal": "舆情信号",
        "event_calendar": "事件日历",
        "hot_money_event": "龙虎榜/资金事件",
        "lockup_event": "解禁事件",
    }.get(value, value.replace("_", " "))


def _field_label(value: str) -> str:
    return {
        "date": "日期",
        "period_start": "开始日期",
        "period_end": "结束日期",
        "published_at": "发布时间",
        "timestamp": "时间戳",
        "open": "开盘价",
        "high": "最高价",
        "low": "最低价",
        "close": "收盘价",
        "volume": "成交量",
        "title": "标题",
        "source": "来源",
        "summary": "摘要",
        "url": "链接",
        "sentiment": "情绪",
        "score": "分数",
        "mentions": "提及量",
        "price": "价格",
        "change": "涨跌额",
        "change_pct": "涨跌幅",
        "amount": "成交额",
        "fdv": "完全稀释估值",
        "circulating_supply": "流通供应量",
        "total_supply": "总供应量",
        "tvl": "总锁仓量",
        "chains": "链",
        "category": "类别",
        "open_interest": "未平仓量",
        "funding_rate": "资金费率",
        "long_short_ratio": "多空比",
        "long_liquidation": "多头清算额",
        "short_liquidation": "空头清算额",
        "liquidation_value": "清算总额",
        "net_inflow": "净流入",
        "metric": "指标",
        "value": "数值",
        "chain": "链",
        "bid_price": "买一价",
        "bid_size": "买一量",
        "ask_price": "卖一价",
        "ask_size": "卖一量",
        "revenue": "收入",
        "net_income": "净利润",
        "assets": "资产",
        "liabilities": "负债",
        "cash_flow": "现金流",
        "roe": "净资产收益率",
        "roa": "资产收益率",
        "gross_margin": "毛利率",
        "debt_ratio": "负债率",
        "eps": "每股收益",
        "pe": "市盈率",
        "pb": "市净率",
        "ps": "市销率",
        "market_cap": "市值",
        "ev_ebitda": "企业价值倍数",
        "event_type": "事件类型",
        "event_date": "事件日期",
        "trade_date": "交易日期",
        "seat": "席位",
        "buy_amount": "买入金额",
        "sell_amount": "卖出金额",
        "symbol_id": "标的代码",
    }.get(value, value.replace("_", " "))


def _gap_reason_label(value: str) -> str:
    return {
        "credential_missing": "接口凭证缺失",
        "rate_limited": "来源限流",
        "provider_error": "来源调用失败",
        "empty_result": "来源返回为空",
        "warehouse_missing": "仓库没有可用记录",
        "warehouse_stale": "仓库记录已过期",
        "field_missing": "必需字段缺失",
        "date_range_missing": "未覆盖完整分析区间",
        "data_integrity_failed": "本地数据校验失败",
        "granularity_mismatch": "数据粒度不匹配",
        "license_blocked": "许可限制",
        "evidence_write_failed": "证据写入失败",
        "cache_hit": "复用已有缓存结果",
        "shared_result": "复用同批共享结果",
        "cached_empty": "缓存记录为空",
        "cooldown_skipped": "冷却期内跳过远端调用",
        "not_applicable": "不适用",
        "invalid_request": "请求无效",
        "sdk_http_unknown": "SDK 内部请求不可审计",
    }.get(value, value.replace("_", " "))


def _human_error(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    replacements = {
        "date_range_missing": "未覆盖完整分析区间",
        "warehouse_missing": "仓库没有可用记录",
        "field_missing": "必需字段缺失",
        "data_integrity_failed": "本地数据校验失败",
        "provider_error": "来源调用失败",
        "empty_result": "来源返回为空",
        "credential_missing": "接口凭证缺失",
        "sdk_http_unknown": "SDK 内部请求不可审计",
        "normalized rows do not expose period_start/period_end": "标准化数据没有提供完整起止日期",
        "chart_dependency_missing:pandas": "图表依赖缺失",
        "market pack did not generate chart assets": "市场资料包没有生成图表资产",
        "no usable OHLCV rows": "没有可用于画图的开高低收量数据",
        "ohlcv_fields_missing": "画图必需字段缺失",
        "warehouse_recheck_missing_for_request": "取数后仓库仍未形成当前请求的可用记录",
        "report_data_result_dataset_mismatch": "资料包返回的数据类型与请求不一致",
        "report_data_result_field_mismatch": "资料包返回字段与请求不一致",
        "report_data_result_request_id_mismatch": "资料包请求标识无法识别",
        "report_data_result_unknown_dataset": "资料包请求的数据类型未登记",
    }
    for source, target in replacements.items():
        text = text.replace(source, target)
    return text.replace("_", " ")


def _display_value(value: object) -> str:
    if value is None:
        return "未提供"
    if isinstance(value, (list, tuple)):
        return "、".join(str(item) for item in value) or "未提供"
    return str(value)


def _status_zh(status: str) -> str:
    return {
        "ready": "可用",
        "partial": "部分可用",
        "missing": "缺失",
        "error": "错误",
    }.get(status, status)


def _error_payload(code: str, message: str) -> dict[str, Any]:
    return {"ok": False, "error": {"code": code, "message": message}}


def _parse_date(value: Any) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    return date.fromisoformat(text[:10])


def _parse_datetime(value: Any) -> datetime | None:
    parsed = _parse_date(value)
    if parsed is None:
        return None
    return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)


def _resolve_as_of(value: Any) -> datetime:
    now = datetime.now(tz=UTC)
    parsed = _parse_date(value)
    if parsed is None:
        return now
    if parsed == now.date():
        return now
    return datetime(parsed.year, parsed.month, parsed.day, 23, 59, 59, tzinfo=UTC)

def _dedupe(values: Sequence[str] | Any) -> tuple[str, ...]:
    output: list[str] = []
    for value in values:
        text = str(value)
        if text and text not in output:
            output.append(text)
    return tuple(output)
