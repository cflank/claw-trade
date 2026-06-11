from __future__ import annotations

import importlib.util
import json
import multiprocessing as mp
import os
import sys
import tempfile
from contextlib import redirect_stdout
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from time import monotonic
from typing import Any, Mapping, Sequence

from claw_trade.data_gateway.analysis.crypto_lens import analyze_crypto_lens_data_results
from claw_trade.data_gateway.crypto_symbols import KNOWN_QUOTE_ASSETS, map_asset_to_symbol
from claw_trade.data_gateway.models import DataGap, DataRequest, DataResult, DataResultStatus, Market
from claw_trade.data_gateway.runtime import build_data_api_from_env

_DatasetSpec = tuple[str, str, tuple[str, ...]]


_CN_A_DOMAIN_DATASETS: dict[str, tuple[_DatasetSpec, ...]] = {
    "market": (
        ("daily_bar", "daily", ("date", "open", "high", "low", "close", "volume", "amount", "amount_unit")),
        ("intraday_bar", "intraday", ("timestamp", "open", "high", "low", "close", "volume")),
        ("quote_snapshot", "realtime", ("price", "change", "change_pct", "volume", "amount", "amount_unit", "timestamp", "symbol_id")),
        ("order_book_snapshot", "realtime", ("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id")),
        ("capital_flow", "daily", ("date", "main_net", "amount_unit", "symbol_id")),
        ("sector_snapshot", "event", ("sector_name", "main_net", "amount_unit", "timestamp")),
    ),
    "fundamental": (
        (
            "financial_statement",
            "quarterly",
            (
                "period",
                "revenue",
                "net_income",
                "assets",
                "liabilities",
                "cash_flow",
                "amount_unit",
                "revenue_basis",
                "net_income_basis",
                "cash_flow_basis",
                "assets_basis",
                "liabilities_basis",
            ),
        ),
        ("financial_metric", "quarterly", ("roe", "roa", "gross_margin", "debt_ratio", "eps")),
        ("valuation_metric", "daily", ("pe", "pb", "ps", "market_cap", "market_cap_unit")),
        ("valuation_metric", "realtime", ("market_cap", "market_cap_unit", "price", "symbol_id")),
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
    "social": (("social_signal", "event", ("source", "timestamp", "title", "url", "symbol_id")),),
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
_REPORT_PREFETCH_DOMAIN_TIMEOUT_SECONDS = 0.0

_CRYPTO_DOMAIN_DATASETS: dict[str, tuple[_DatasetSpec, ...]] = {
    "market": (
        ("daily_bar", "daily", ("open", "high", "low", "close", "volume", "volume_unit", "amount", "amount_unit")),
        ("intraday_bar", "1h", ("open", "high", "low", "close", "volume", "volume_unit", "amount", "amount_unit")),
        ("quote_snapshot", "realtime", ("price", "change", "change_pct", "volume", "amount", "timestamp", "symbol_id")),
        ("order_book_snapshot", "realtime", ("bid_price", "bid_size", "ask_price", "ask_size", "timestamp", "symbol_id")),
        ("crypto_derivative_metric", "realtime", ("open_interest", "open_interest_unit", "timestamp", "symbol_id")),
        ("crypto_derivative_metric", "1h", ("funding_rate", "funding_rate_unit", "timestamp", "symbol_id")),
        ("crypto_derivative_metric", "1h", ("long_short_ratio", "timestamp", "symbol_id")),
        ("crypto_derivative_metric", "1h", ("taker_buy_volume", "taker_sell_volume", "taker_volume_unit", "taker_buy_sell_ratio", "timestamp", "symbol_id")),
        (
            "crypto_derivative_metric",
            "1h",
            ("long_liquidation", "short_liquidation", "liquidation_value", "liquidation_value_unit", "timestamp", "symbol_id"),
        ),
        (
            "crypto_derivative_metric",
            "1h",
            ("liquidation_price", "liquidation_price_unit", "liquidation_size", "liquidation_size_unit", "side", "timestamp", "symbol_id"),
        ),
        ("crypto_derivative_metric", "1h", ("options_open_interest", "options_volume", "timestamp", "symbol_id")),
        ("crypto_derivative_metric", "1h", ("cvd", "taker_buy_volume", "taker_sell_volume", "taker_volume_unit", "timestamp", "symbol_id")),
        ("crypto_derivative_metric", "realtime", ("net_inflow", "net_inflow_unit", "timestamp", "symbol_id")),
        ("order_book_snapshot", "1h", ("bids_usd", "bids_quantity", "asks_usd", "asks_quantity", "timestamp", "symbol_id")),
        ("crypto_onchain_metric", "daily", ("timestamp", "metric", "value", "value_unit", "chain")),
        ("crypto_onchain_metric", "event", ("timestamp", "metric", "value", "value_unit", "chain")),
        ("crypto_onchain_metric", "realtime", ("timestamp", "metric", "value", "value_unit", "chain")),
    ),
    "fundamental": (
        (
            "valuation_metric",
            "realtime",
            (
                "price",
                "price_unit",
                "market_cap",
                "market_cap_unit",
                "volume",
                "volume_unit",
            ),
        ),
        (
            "valuation_metric",
            "daily",
            (
                "price",
                "price_unit",
                "market_cap",
                "market_cap_unit",
                "circulating_supply",
                "supply_unit",
                "timestamp",
                "symbol_id",
            ),
        ),
        (
            "valuation_metric",
            "realtime",
            (
                "fdv",
                "fdv_unit",
                "total_supply",
                "supply_unit",
            ),
        ),
        ("defi_metric", "realtime", ("tvl", "chains", "category", "symbol_id")),
        ("crypto_derivative_metric", "daily", ("etf_flow_usd", "price", "timestamp", "symbol_id")),
        ("crypto_onchain_metric", "daily", ("timestamp", "metric", "value", "value_unit", "chain")),
        ("crypto_onchain_metric", "realtime", ("timestamp", "metric", "value", "value_unit", "chain")),
    ),
    "news": (
        ("company_news", "event", ("title", "published_at", "source", "summary", "url")),
        ("macro_news", "event", ("title", "published_at", "source", "summary", "url", "region")),
    ),
    "social": (
        ("social_signal", "event", ("source", "timestamp", "score", "sentiment", "symbol_id")),
        ("social_signal", "event", ("source", "timestamp", "score", "sentiment", "social_dominance", "num_posts", "interactions", "symbol_id")),
    ),
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

_ROW_LEVEL_LIMIT_REASONS = {
    "date_range_missing",
    "empty_result",
    "provider_error",
    "rate_limited",
    "warehouse_missing",
    "warehouse_stale",
    "cached_empty",
    "cooldown_skipped",
    "granularity_mismatch",
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
    results = _filter_crypto_market_bars(results=results, market=market, domain=domain)

    status = _aggregate_status(results)
    chart_payload = _market_chart_payload(
        tool_input=tool_input,
        runtime_context=runtime_context,
        results=results,
    ) if domain == "market" else {}
    crypto_lens_payload = _crypto_lens_payload(
        tool_input=tool_input,
        runtime_context=runtime_context,
        results=results,
    ) if market == Market.CRYPTO and domain == "market" else {}
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
            crypto_lens_payload=crypto_lens_payload,
        ),
        "data_results": [item.model_dump(mode="json") for item in results],
        "dataset_refs": tuple(_dedupe(ref for item in results for ref in item.dataset_refs)),
        "raw_refs": tuple(_dedupe(ref for item in results for ref in item.raw_refs)),
        "attempt_refs": tuple(_dedupe(ref for item in results for ref in item.attempt_refs)),
        **chart_payload,
        **crypto_lens_payload,
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
        domain_requests: list[tuple[str, tuple[DataRequest, ...]]] = []
        domains: list[str] = []
        for domain in _REPORT_PREFETCH_DOMAINS:
            if not _domain_datasets(domain=domain, market=market):
                continue
            domains.append(domain)
            requests = _build_requests(
                tool_input=tool_input,
                runtime_context={**runtime_base, "pack_domain": domain},
                market=market,
                domain=domain,
            )
            domain_requests.append((domain, requests))

        timeout_seconds = _report_prefetch_domain_timeout_seconds()
        outcomes = tuple(
            _fetch_report_prefetch_domain(
                domain=domain,
                requests=requests,
                timeout_seconds=timeout_seconds,
            )
            for domain, requests in domain_requests
        )
        results = tuple(result for outcome in outcomes for result in outcome.results)
        results = _validate_report_data_results(results=results, market=market, domain="*")
        results = _filter_crypto_market_bars(results=results, market=market, domain="*")
        payload = {
            "ok": True,
            "schema_version": "report_data_prefetch.v1",
            "run_id": run_id,
            "market": market.value,
            "domains": tuple(domains),
            "request_count": sum(len(requests) for _domain, requests in domain_requests),
            "status": _aggregate_status(results),
            "domain_statuses": tuple(_domain_status_payload(outcome) for outcome in outcomes),
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


@dataclass(frozen=True)
class _DomainPrefetchOutcome:
    domain: str
    request_count: int
    results: tuple[DataResult, ...]
    elapsed_seconds: float
    timed_out: bool = False
    error: str | None = None


def _fetch_report_prefetch_domain(
    *,
    domain: str,
    requests: Sequence[DataRequest],
    timeout_seconds: float,
) -> _DomainPrefetchOutcome:
    started = monotonic()
    request_tuple = tuple(requests)
    if not request_tuple:
        return _DomainPrefetchOutcome(domain=domain, request_count=0, results=(), elapsed_seconds=0.0)
    if timeout_seconds <= 0:
        try:
            with redirect_stdout(sys.stderr):
                results = tuple(build_data_api_from_env().get_data_batch(request_tuple))
            return _DomainPrefetchOutcome(
                domain=domain,
                request_count=len(request_tuple),
                results=results,
                elapsed_seconds=monotonic() - started,
            )
        except Exception as exc:
            return _DomainPrefetchOutcome(
                domain=domain,
                request_count=len(request_tuple),
                results=_report_prefetch_error_results(
                    requests=request_tuple,
                    message=f"report_prefetch_domain_error:{domain}:{exc}",
                ),
                elapsed_seconds=monotonic() - started,
                error=str(exc),
            )

    ctx = _multiprocessing_context()
    fd, raw_output_path = tempfile.mkstemp(prefix="claw-report-prefetch-domain-", suffix=".json")
    os.close(fd)
    output_path = Path(raw_output_path)
    process = ctx.Process(target=_report_prefetch_domain_worker, args=(request_tuple, str(output_path)))
    process.start()
    process.join(timeout_seconds)
    if process.is_alive():
        process.terminate()
        process.join(2)
        if process.is_alive() and hasattr(process, "kill"):
            process.kill()
            process.join(2)
        partial_results = _read_report_prefetch_worker_results(output_path)
        completed_request_ids = {result.request_id for result in partial_results}
        missing_requests = tuple(request for request in request_tuple if request.request_id not in completed_request_ids)
        output_path.unlink(missing_ok=True)
        return _DomainPrefetchOutcome(
            domain=domain,
            request_count=len(request_tuple),
            results=partial_results
            + _report_prefetch_error_results(
                requests=missing_requests,
                message=f"report_prefetch_domain_timeout:{domain}:{timeout_seconds:g}s",
            ),
            elapsed_seconds=monotonic() - started,
            timed_out=True,
        )
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception:
        payload = {
            "ok": False,
            "reason": f"report_prefetch_domain_worker_exited_without_payload:{process.exitcode}",
        }
    finally:
        output_path.unlink(missing_ok=True)
    if isinstance(payload, Mapping) and payload.get("ok") is True:
        results = tuple(DataResult.model_validate(item) for item in tuple(payload.get("results", ()) or ()))
        return _DomainPrefetchOutcome(
            domain=domain,
            request_count=len(request_tuple),
            results=results,
            elapsed_seconds=monotonic() - started,
        )

    reason = str(payload.get("reason") if isinstance(payload, Mapping) else payload)
    return _DomainPrefetchOutcome(
        domain=domain,
        request_count=len(request_tuple),
        results=_report_prefetch_error_results(
            requests=request_tuple,
            message=f"report_prefetch_domain_error:{domain}:{reason}",
        ),
        elapsed_seconds=monotonic() - started,
        error=reason,
    )


def _report_prefetch_domain_worker(requests: tuple[DataRequest, ...], output_path: str) -> None:
    try:
        with redirect_stdout(sys.stderr):
            api = build_data_api_from_env()
            results: list[DataResult] = []
            _write_report_prefetch_worker_payload(output_path, {"ok": True, "partial": True, "results": []})
            for request in requests:
                try:
                    request_results = tuple(api.get_data_batch((request,)))
                    if request_results:
                        results.extend(request_results)
                    else:
                        results.extend(
                            _report_prefetch_error_results(
                                requests=(request,),
                                message=f"report_prefetch_request_error:{request.request_id}:empty_result",
                            )
                        )
                except Exception as exc:
                    results.extend(
                        _report_prefetch_error_results(
                            requests=(request,),
                            message=f"report_prefetch_request_error:{request.request_id}:{exc}",
                        )
                    )
                _write_report_prefetch_worker_payload(
                    output_path,
                    {"ok": True, "partial": True, "results": [item.model_dump(mode="json") for item in results]},
                )
        payload = {"ok": True, "partial": False, "results": [item.model_dump(mode="json") for item in results]}
    except Exception as exc:
        payload = {"ok": False, "reason": str(exc)}
    _write_report_prefetch_worker_payload(output_path, payload)


def _read_report_prefetch_worker_results(output_path: Path) -> tuple[DataResult, ...]:
    if not output_path.exists():
        return ()
    try:
        payload = json.loads(output_path.read_text(encoding="utf-8"))
    except Exception:
        return ()
    if not isinstance(payload, Mapping) or payload.get("ok") is not True:
        return ()
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return ()
    results: list[DataResult] = []
    for item in raw_results:
        try:
            results.append(DataResult.model_validate(item))
        except Exception:
            continue
    return tuple(results)


def _write_report_prefetch_worker_payload(output_path: str | Path, payload: Mapping[str, Any]) -> None:
    path = Path(output_path)
    tmp_path = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp_path, path)


def _multiprocessing_context() -> Any:
    return mp.get_context("fork" if "fork" in mp.get_all_start_methods() else "spawn")


def _report_prefetch_error_results(*, requests: Sequence[DataRequest], message: str) -> tuple[DataResult, ...]:
    as_of = datetime.now(tz=UTC)
    return tuple(
        DataResult(
            request_id=request.request_id,
            status=DataResultStatus.ERROR,
            gaps=(
                DataGap.by_reason(
                    "provider_error",
                    request_id=request.request_id,
                    market=request.market,
                    data_type=request.data_type,
                    granularity=request.granularity,
                    message=message,
                    symbol_id=request.symbol_id,
                    as_of=as_of,
                ),
            ),
            as_of=as_of,
        )
        for request in requests
    )


def _domain_status_payload(outcome: _DomainPrefetchOutcome) -> dict[str, Any]:
    return {
        "domain": outcome.domain,
        "request_count": outcome.request_count,
        "status": _aggregate_status(outcome.results),
        "timed_out": outcome.timed_out,
        "error": outcome.error,
        "elapsed_seconds": round(outcome.elapsed_seconds, 3),
    }


def _report_prefetch_domain_timeout_seconds() -> float:
    raw = os.environ.get("CLAW_TRADE_REPORT_PREFETCH_DOMAIN_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return _REPORT_PREFETCH_DOMAIN_TIMEOUT_SECONDS
    try:
        return float(raw)
    except ValueError:
        return _REPORT_PREFETCH_DOMAIN_TIMEOUT_SECONDS

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
        if (
            market == Market.CRYPTO
            and data_type == "crypto_derivative_metric"
            and _is_non_btc_crypto_base(symbol.base_asset or "")
            and _is_crypto_liquidation_fields(fields)
        ):
            continue
        request_start, request_end = _request_date_range(
            market=market,
            data_type=data_type,
            granularity=granularity,
            start=start,
            end=end,
        )
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
                    date_range_start=request_start,
                    date_range_end=request_end,
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


def _request_date_range(
    *,
    market: Market,
    data_type: str,
    granularity: str,
    start: date | None,
    end: date | None,
) -> tuple[date | None, date | None]:
    if granularity == "realtime":
        return None, None
    if market != Market.CRYPTO:
        if data_type == "intraday_bar":
            point = end or start
            return point, point
        if data_type in {"sector_snapshot", "social_signal"}:
            return None, None
        return start, end
    if data_type in {"daily_bar", "intraday_bar"}:
        return start, end
    if data_type == "order_book_snapshot" and granularity == "1h":
        return start, end
    if data_type == "crypto_derivative_metric" and granularity in {"1h", "daily"}:
        return start, end
    if data_type == "crypto_onchain_metric" and granularity in {"daily", "event"}:
        return start, end
    if data_type == "defi_metric" and granularity == "daily":
        return start, end
    if data_type in {"company_news", "macro_news"}:
        return start, end
    return None, None


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


def _filter_crypto_market_bars(
    *,
    results: Sequence[DataResult],
    market: Market,
    domain: str,
) -> tuple[DataResult, ...]:
    if market != Market.CRYPTO or domain not in {"*", "market"}:
        return tuple(results)

    filtered: list[DataResult] = []
    for result in results:
        if _result_domain(result.request_id) != "market" or _result_dataset(result.request_id) not in {
            "daily_bar",
            "intraday_bar",
        }:
            filtered.append(result)
            continue

        spot_rows = tuple(row for row in result.rows if _is_crypto_spot_row(row))
        if not result.rows:
            filtered.append(result)
            continue
        if not spot_rows:
            filtered.append(result)
            continue

        spot_refs = tuple(ref for ref in result.dataset_refs if ":CRYPTO:spot:" in str(ref))
        filtered.append(result.model_copy(update={"rows": spot_rows, "dataset_refs": spot_refs or result.dataset_refs}))
    return tuple(filtered)


def _is_crypto_spot_row(row: Any) -> bool:
    if not isinstance(row, Mapping):
        return False
    lineage = row.get("provider_lineage")
    endpoint_id = str(lineage.get("endpoint_id") if isinstance(lineage, Mapping) else "").strip()
    provider_id = str(lineage.get("provider_id") if isinstance(lineage, Mapping) else "").strip()
    return (
        str(row.get("universe_ref") or "").strip() == "binance_spot_all_symbols"
        or str(row.get("source_market_segment") or "").strip() == "spot"
        or endpoint_id in {"spot_daily_bar", "spot_intraday_bar"}
        or provider_id == "crypto_binance_spot_market"
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
        if {"metric", "value", "chain"}.issubset(required):
            required.discard("value_unit")
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

    base, quote = _normalize_crypto_pair(ticker=ticker, currency=currency)
    return _SymbolParts(
        symbol_id=f"{base}{quote}",
        exchange=str(_MARKET_DEFAULTS[market]["exchange"]),
        currency=quote,
        base_asset=base,
        quote_asset=quote,
    )


def _normalize_crypto_pair(*, ticker: str, currency: str) -> tuple[str, str]:
    if _has_explicit_crypto_quote(ticker):
        base, _quote = _crypto_pair(ticker=ticker, currency=currency or "USDT")
        mapping = map_asset_to_symbol(base, quote_asset="USDT")
        return mapping.base_asset, mapping.quote_asset
    mapping = map_asset_to_symbol(ticker, quote_asset="USDT")
    return mapping.base_asset, mapping.quote_asset


def _has_explicit_crypto_quote(ticker: str) -> bool:
    normalized = ticker.replace("-", "/").replace(".", "/")
    if "/" in normalized:
        left, right = normalized.split("/", 1)
        return bool(left.strip()) and right.strip().upper() in set(KNOWN_QUOTE_ASSETS)
    return any(ticker.endswith(quote) and len(ticker) > len(quote) for quote in KNOWN_QUOTE_ASSETS)


def _crypto_pair(*, ticker: str, currency: str) -> tuple[str, str]:
    normalized = ticker.replace("-", "/").replace(".", "/")
    if "/" in normalized:
        base, quote = normalized.split("/", 1)
        base = base.strip().upper()
        quote = quote.strip().upper()
        if base and quote:
            return base, quote
    for quote in KNOWN_QUOTE_ASSETS:
        if ticker.endswith(quote) and len(ticker) > len(quote):
            return ticker[: -len(quote)], quote
    return ticker, "USDT"


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
    crypto_lens_payload: Mapping[str, Any] | None = None,
) -> str:
    ticker = str(tool_input.get("ticker") or "").strip()
    crypto_base_asset = _crypto_base_asset_from_tool_input(tool_input=tool_input, market=market)
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
        for result in ready_results[:8]:
            dataset = _result_dataset(result.request_id) or result.request_id.split(":")[-1]
            lines.append(
                f"- {_dataset_label(dataset, market=market)}："
                f"{len(result.rows)} 行，字段覆盖 {', '.join(_row_fields(result.rows)) or '未声明'}。"
            )
            date_range = _row_date_range(result.rows)
            if date_range:
                lines.append(f"  - 时间覆盖 {date_range[0]} 至 {date_range[1]}，下列摘要优先展示最新记录。")
            for row in _recent_rows(result.rows):
                summary = _row_summary(row, domain=domain, dataset=dataset)
                if summary:
                    lines.append(f"  - {summary}")

    if domain == "fundamental" and market == Market.CN_A:
        lines.append(
            "A股财报口径提示：收入、净利润、经营现金流通常是报告期累计流量；"
            "资产、负债是期末时点值。资料包未明确给出单季度推导值时，不得把累计数直接写成单季度。"
        )

    if domain == "news" and ready_results:
        lines.append(
            "新闻资料包中的媒体/搜索线索只能证明来源返回过相关线索；"
            "其中涉及机构资金、宏观、链上或事件的说法，未经官方、交易所、监管或原始数据交叉验证前，不能升级为报告事实或投资结论。"
        )

    if domain == "social" and market == Market.HK:
        if ready_results:
            lines.append(
                "HK 社交资料包只包含媒体聚合/公开搜索发现线索；这些线索不是正式事实源，"
                "也不是完整社交情绪样本，不能升级为情绪方向、讨论量或平台观点。"
            )
        else:
            lines.append(
                "HK 社交资料包未取得可用公开讨论/热度线索；只能把社交证据视为缺口，"
                "不能补写情绪方向、讨论量或平台观点。"
            )

    if domain == "market":
        lines.extend(_chart_brief_lines(chart_payload or {}))
        if market == Market.CRYPTO:
            lines.extend(_crypto_lens_brief_lines(crypto_lens_payload or {}, chart_payload=chart_payload or {}, base_asset=crypto_base_asset))

    sample_limit_lines = _sample_limit_lines(results, market=market, crypto_base_asset=crypto_base_asset)
    if sample_limit_lines:
        lines.append("样本限制：")
        lines.extend(f"- {item}" for item in sample_limit_lines[:8])

    gap_lines = _gap_lines(results, market=market, crypto_base_asset=crypto_base_asset)
    if gap_lines:
        lines.append("数据缺口：")
        lines.extend(f"- {item}" for item in gap_lines[:12])

    attempt_count = sum(len(item.attempt_refs) for item in results)
    dataset_count = sum(len(item.dataset_refs) for item in results)
    raw_count = sum(len(item.raw_refs) for item in results)
    domain_timed_out = _domain_timeout_results(results)
    if domain_timed_out and dataset_count == 0 and raw_count == 0 and attempt_count == 0:
        lines.append(
            "审计状态：本资料域因预取超时，未拿到可引用的数据集或原始材料；"
            "本域的引用数量不能代表其它资料域或全局外部来源调用情况。"
        )
    else:
        lines.append(
            "审计状态："
            f"已形成 {dataset_count} 个标准化数据引用、"
            f"{raw_count} 个原始或元数据引用、"
            f"{attempt_count} 个来源尝试记录。"
        )
    if not ready_results:
        if domain_timed_out:
            lines.append(
                "这只说明本资料包未收到可审计数据，不等于其它资料域没有调用外部来源，"
                "也不能写成全局外部来源没有尝试。"
            )
        else:
            lines.append("没有可用数据集时，只能写数据不可用和影响范围，不得用模型常识补出真实行情、基本面、新闻或舆情事实。")
    return "\n".join(lines)


def _crypto_base_asset_from_tool_input(*, tool_input: Mapping[str, Any], market: Market) -> str:
    if market != Market.CRYPTO:
        return ""
    ticker = str(tool_input.get("ticker") or "").strip().upper()
    currency = str(tool_input.get("currency") or "USDT").strip().upper()
    if not ticker:
        return ""
    base_asset, _quote_asset = _normalize_crypto_pair(ticker=ticker, currency=currency or "USDT")
    return base_asset


def _sample_limit_lines(results: Sequence[DataResult], *, market: Market, crypto_base_asset: str = "") -> list[str]:
    lines: list[str] = []
    for result in results:
        if not result.rows:
            continue
        reasons: list[str] = []
        subject_gap: DataGap | None = None
        for gap in result.gaps:
            reason = str(getattr(gap.reason, "value", gap.reason))
            if reason not in _ROW_LEVEL_LIMIT_REASONS:
                continue
            if subject_gap is None:
                subject_gap = gap
            reasons.append(_gap_reason_label(reason))
        if reasons:
            subject = _gap_subject_label(result, gap=subject_gap, market=market)
            if _is_non_btc_crypto_base(crypto_base_asset) and _is_crypto_liquidation_subject(subject):
                continue
            date_range = _row_date_range(result.rows)
            range_text = f"，覆盖 {date_range[0]} 至 {date_range[1]}" if date_range else ""
            reason_text = "、".join(_dedupe(reasons))
            lines.append(
                f"{subject}：已有 {len(result.rows)} 行{range_text}，"
                f"样本或来源限制：{reason_text}；先分析现有样本，把限制作为置信度限制。"
            )
    if market == Market.CRYPTO:
        proxy_rows = _crypto_taker_proxy_rows(results)
        if proxy_rows and _has_gap_subject(results, market=market, subject="标准 CVD"):
            date_range = _row_date_range(proxy_rows)
            range_text = f"，覆盖 {date_range[0]} 至 {date_range[1]}" if date_range else ""
            lines.append(
                f"标准 CVD：标准 CVD 序列未返回；已有主动买卖量代理 {len(proxy_rows)} 行{range_text}，"
                "可先分析主动买卖量差额，把它作为代理指标。"
            )
    return list(_dedupe(lines))


def _is_non_btc_crypto_base(base_asset: str) -> bool:
    return bool(base_asset) and base_asset.upper() != "BTC"


def _is_crypto_liquidation_subject(subject: str) -> bool:
    return subject in {"清算热力图", "强平统计", "清算地图"}


def _is_crypto_liquidation_fields(fields: Sequence[str]) -> bool:
    return bool(
        {
            "long_liquidation",
            "short_liquidation",
            "liquidation_value",
            "liquidation_price",
            "liquidation_size",
        }
        & {str(field) for field in fields}
    )


def _crypto_taker_proxy_rows(results: Sequence[DataResult]) -> tuple[Mapping[str, Any], ...]:
    rows: list[Mapping[str, Any]] = []
    for result in results:
        for row in result.rows:
            if not isinstance(row, Mapping):
                continue
            if row.get("taker_buy_volume") is not None and row.get("taker_sell_volume") is not None:
                rows.append(row)
    return tuple(rows)


def _has_gap_subject(results: Sequence[DataResult], *, market: Market, subject: str) -> bool:
    return any(
        _gap_subject_label(result, gap=gap, market=market) == subject
        for result in results
        for gap in result.gaps
    )


def _domain_timeout_results(results: Sequence[DataResult]) -> bool:
    if not results:
        return False
    for result in results:
        if result.status != DataResultStatus.ERROR:
            return False
        if not any("report_prefetch_domain_timeout" in str(gap.human_readable or "") for gap in result.gaps):
            return False
    return True


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
                lines.extend(_chart_indicator_interpretation_lines(summary if isinstance(summary, Mapping) else {}, indicators))
        return lines
    reason = _human_error(str(chart_payload.get("chart_error") or "market pack did not generate chart assets"))
    return [f"图表资产：缺失，原因：{reason}。"]


def _chart_indicator_interpretation_lines(summary: Mapping[str, Any], indicators: Mapping[str, Any]) -> list[str]:
    lines: list[str] = []
    latest_close = _numeric_value(summary.get("latest_close"))
    ma = indicators.get("ma")
    if latest_close is not None and isinstance(ma, Mapping):
        ma_positions: list[str] = []
        for key in ("ma5", "ma10", "ma20"):
            value = _numeric_value(ma.get(key))
            if value is None:
                continue
            position = "高于" if latest_close > value else "低于" if latest_close < value else "等于"
            ma_positions.append(f"{position} {key.upper()}({_display_value(value)})")
        if ma_positions:
            lines.append(f"本地均线位置：最新收盘价 {_display_value(latest_close)}，" + "，".join(ma_positions) + "。")
    kdj = indicators.get("kdj")
    if isinstance(kdj, Mapping):
        k = _numeric_value(kdj.get("k"))
        d = _numeric_value(kdj.get("d"))
        if k is not None or d is not None:
            lines.append(
                "本地 KD 状态："
                f"K={_display_value(k)}({_oscillator_zone(k)})，"
                f"D={_display_value(d)}({_oscillator_zone(d)})；"
                "超卖阈值为低于20，接近阈值不能写成已经低于阈值。"
            )
    return lines


def _numeric_value(value: object) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _oscillator_zone(value: float | None) -> str:
    if value is None:
        return "未提供"
    if value < 20:
        return "超卖"
    if value <= 25:
        return "接近超卖"
    if value > 80:
        return "超买"
    if value >= 75:
        return "接近超买"
    return "中性"


def _crypto_lens_brief_lines(
    crypto_lens_payload: Mapping[str, Any],
    *,
    chart_payload: Mapping[str, Any],
    base_asset: str = "",
) -> list[str]:
    if not crypto_lens_payload:
        return ["CryptoLens 指标材料：缺失，原因：市场资料包没有生成结构化指标分析。"]
    if crypto_lens_payload.get("crypto_lens_status") == "error":
        return [f"CryptoLens 指标材料：缺失，原因：{_human_error(str(crypto_lens_payload.get('crypto_lens_error') or 'analysis failed'))}。"]

    analysis = crypto_lens_payload.get("crypto_lens_analysis")
    if not isinstance(analysis, Mapping):
        return ["CryptoLens 指标材料：缺失，原因：分析结果结构无效。"]

    lines = [
        f"CryptoLens 指标材料：{_status_zh(str(analysis.get('status') or 'partial'))}。",
        "指标覆盖：",
        "| 模块 | 状态 | 已返回材料 | 对结论的影响 |",
        "|---|---|---|---|",
    ]
    for item in _crypto_lens_coverage_rows(analysis, chart_payload=chart_payload, base_asset=base_asset):
        lines.append(f"| {item[0]} | {item[1]} | {item[2]} | {item[3]} |")
    evidence_paths = [str(item) for item in crypto_lens_payload.get("crypto_lens_evidence_paths") or () if str(item)]
    if evidence_paths:
        lines.append(f"指标证据文件：{evidence_paths[0]}")
    return lines


def _crypto_lens_coverage_rows(
    analysis: Mapping[str, Any],
    *,
    chart_payload: Mapping[str, Any],
    base_asset: str = "",
) -> list[tuple[str, str, str, str]]:
    technical = _analysis_section(analysis, "technical_patterns")
    derivatives = _analysis_section(analysis, "derivatives_context")
    liquidation = _analysis_section(analysis, "liquidation_context")
    onchain = _analysis_section(analysis, "onchain_context")
    ahr999 = _analysis_section(analysis, "ahr999_context")
    technical_evidence = _section_evidence(technical)
    technical_patterns = technical_evidence.get("patterns")
    pattern_evidence = technical_patterns if isinstance(technical_patterns, Mapping) else {}
    derivatives_evidence = _section_evidence(derivatives)
    liquidation_evidence = _section_evidence(liquidation)
    onchain_evidence = _section_evidence(onchain)
    ahr999_evidence = _section_evidence(ahr999)
    chart_indicators = {}
    chart_analysis = chart_payload.get("technical_analysis")
    if isinstance(chart_analysis, Mapping) and isinstance(chart_analysis.get("indicators"), Mapping):
        chart_indicators = dict(chart_analysis["indicators"])

    return [
        _coverage_row("布林带", _has_nested(chart_indicators, ("boll",)), "technical_analysis.indicators.boll", _compact_json(chart_indicators.get("boll")) if chart_indicators.get("boll") else "未取得", "可用于描述当前通道位置；不要编造目标价或回测结论"),
        _coverage_row("维加斯通道", bool(_non_empty_mapping(technical_evidence.get("vegas"))), "technical_patterns.evidence.vegas", _vegas_evidence_text(technical_evidence.get("vegas")), "可用于描述当前趋势结构；不要编造统计结论或回测结论"),
        _coverage_row("双线反转", bool(_non_empty_mapping(technical_evidence.get("double_line_reversal"))), "technical_patterns.evidence.double_line_reversal", _double_line_evidence_text(technical_evidence.get("double_line_reversal")), "可用于描述 EMA20/EMA50 当前结构；不要编造反转统计或回测结论"),
        _coverage_row("AMD/SMC", _pattern_ready(pattern_evidence.get("amd")), "technical_patterns.evidence.patterns.amd", _pattern_evidence_text("amd", pattern_evidence.get("amd")), "输出当前摆动结构和流动性池代理；不包含逐笔订单流确认"),
        _coverage_row("123 突破", _pattern_ready(pattern_evidence.get("rule_123")), "technical_patterns.evidence.patterns.rule_123", _pattern_evidence_text("rule_123", pattern_evidence.get("rule_123")), "输出三点结构状态和突破位；只反映最近摆动结构"),
        _coverage_row("FVG", bool(_non_empty_mapping(technical_evidence.get("fvg"))), "technical_patterns.evidence.fvg", _fvg_evidence_text(technical_evidence.get("fvg")), "可用于描述已返回缺口位置；不要编造回补目标或交易动作"),
        _coverage_row("OB 订单块", _pattern_ready(pattern_evidence.get("order_block")), "technical_patterns.evidence.patterns.order_block", _pattern_evidence_text("order_block", pattern_evidence.get("order_block")), "输出候选订单块区间和结构突破位；不包含成交确认或支撑强度评级"),
        _coverage_row("RSI", _has_nested(chart_indicators, ("rsi",)), "technical_analysis.indicators.rsi", _compact_json(chart_indicators.get("rsi")) if chart_indicators.get("rsi") else "未取得", "可用于描述当前动量读数；不要编造反转统计或交易动作"),
        _coverage_row("MACD", _has_nested(chart_indicators, ("macd",)), "technical_analysis.indicators.macd", _compact_json(chart_indicators.get("macd")) if chart_indicators.get("macd") else "未取得", "可用于描述当前动能读数；不要编造趋势统计或交易动作"),
        _coverage_row("KD", bool(_non_empty_mapping(technical_evidence.get("kd_9_3_3"))), "technical_patterns.evidence.kd_9_3_3", _kd_evidence_text(technical_evidence.get("kd_9_3_3")), "可用于描述当前摆动读数；不要编造交易动作"),
        _coverage_row("TD 9/13", bool(_non_empty_mapping(technical_evidence.get("td_sequential"))), "technical_patterns.evidence.td_sequential", _td_evidence_text(technical_evidence.get("td_sequential")), "可用于描述当前计数；不要编造衰竭统计或交易动作"),
        _coverage_row("谐波形态", _pattern_ready(pattern_evidence.get("harmonic")), "technical_patterns.evidence.patterns.harmonic", _pattern_evidence_text("harmonic", pattern_evidence.get("harmonic")), "输出最近摆动比例筛选状态和比例值"),
        _coverage_row("交易密集带/成交量分布", _pattern_ready(pattern_evidence.get("volume_profile")), "technical_patterns.evidence.patterns.volume_profile", _pattern_evidence_text("volume_profile", pattern_evidence.get("volume_profile")), "输出 POC 和价值区间；基于 OHLCV 近似分桶"),
        _liquidation_coverage_row(liquidation_evidence, base_asset=base_asset),
        _coverage_row("CVD代理/主动买卖量", derivatives_evidence.get("cvd_proxy") is not None, "derivatives_context.evidence.cvd_proxy", _value_with_unit(derivatives_evidence.get("cvd_proxy"), derivatives_evidence.get("cvd_proxy_unit")), "标准 CVD 未返回时只能作为当前主动买卖量差代理"),
        _coverage_row("资金费率", derivatives_evidence.get("funding") is not None, "derivatives_context.evidence.funding", _value_with_unit(derivatives_evidence.get("funding"), derivatives_evidence.get("funding_unit")), "可用于描述当前费率水平和单位；不要编造历史分位或统计结论"),
        _coverage_row("OI/多空比", derivatives_evidence.get("oi") is not None or derivatives_evidence.get("long_short_ratio") is not None, "derivatives_context.evidence.oi / long_short_ratio", _oi_long_short_text(derivatives_evidence), "可用于描述当前持仓结构；资料包会明示已知未平仓量单位；不要编造历史分位或统计结论"),
        _coverage_row("宏观", True, "macro_context.evidence", "宏观/新闻由新闻资料包负责，行情资料包不重复判断", "不要写成宏观缺失；应回看新闻/宏观资料包"),
        _coverage_row("链上", bool(_non_empty_mapping(onchain_evidence)), "onchain_context.evidence", _onchain_evidence_text(onchain_evidence), "只保留当前读数；单位或样本不足时不推导流入/流出含义"),
        _ahr999_coverage_row(ahr999_evidence, base_asset=base_asset),
    ]


def _liquidation_coverage_row(evidence: Mapping[str, Any], *, base_asset: str = "") -> tuple[str, str, str, str]:
    if _is_non_btc_crypto_base(base_asset):
        return (
            "清算地图",
            "不适用",
            f"{base_asset.upper()} 不写清算地图；当前清算地图按 BTC 专用口径使用",
            "不作为本标的市场结构或交易结论依据",
        )
    return _coverage_row(
        "清算地图",
        bool(_non_empty_mapping(evidence)),
        "liquidation_context.evidence",
        _liquidation_evidence_text(evidence),
        "只描述簇价格、规模、单位和样本限制；不能作为方向性价格依据",
    )


def _ahr999_coverage_row(evidence: Mapping[str, Any], *, base_asset: str = "") -> tuple[str, str, str, str]:
    if _is_non_btc_crypto_base(base_asset):
        return (
            "AHR999",
            "不适用",
            f"{base_asset.upper()} 不写 AHR999；该指标按 BTC 估值指数使用",
            "不作为本标的估值或交易结论依据",
        )
    if _non_empty_mapping(evidence):
        return _coverage_row(
            "AHR999",
            True,
            "ahr999_context.evidence",
            _ahr999_evidence_text(evidence),
            "BTC 估值指数读数；不要编造历史分位、定投或抄底结论",
        )
    return (
        "AHR999",
        "由基本面/链上资料包负责",
        "行情资料包未取得 AHR999；如基本面资料包有读数，以基本面资料包为准",
        "不要写成全局缺失；不要编造历史分位、定投或抄底结论",
    )


def _analysis_section(analysis: Mapping[str, Any], key: str) -> Mapping[str, Any]:
    section = analysis.get(key)
    return section if isinstance(section, Mapping) else {}


def _liquidation_evidence_text(evidence: Mapping[str, Any]) -> str:
    if not _non_empty_mapping(evidence):
        return "未取得"
    parts: list[str] = []
    price = evidence.get("largest_cluster_price")
    size = evidence.get("largest_cluster_size")
    if price is not None:
        parts.append(f"最大清算簇价格 {_value_with_unit(price, evidence.get('largest_cluster_price_unit'))}")
    if size is not None:
        parts.append(f"最大清算簇规模 {_value_with_unit(size, evidence.get('largest_cluster_size_unit'))}")
    side = evidence.get("largest_cluster_side")
    if side is not None:
        parts.append(f"方向 {_display_value(side)}")
    heatmap_count = evidence.get("heatmap_sample_count")
    invalid_count = evidence.get("invalid_heatmap_sample_count")
    if heatmap_count is not None:
        parts.append(f"清算热力图样本 {_display_value(heatmap_count)}")
        valid_count = _number_delta(heatmap_count, invalid_count)
        if valid_count is not None:
            parts.append(f"清算热力图有效样本 {valid_count:g}")
    if invalid_count is not None:
        parts.append(f"剔除异常热力图价格点 {_display_value(invalid_count)}")
    event_count = evidence.get("sample_count")
    if event_count is not None:
        parts.append(f"普通强平额样本 {_display_value(event_count)}")
    long_total = evidence.get("long_liquidation_total")
    short_total = evidence.get("short_liquidation_total")
    total = evidence.get("liquidation_value_total")
    if total is not None:
        parts.append(f"普通强平总额 {_display_value(total)}")
    if long_total is not None or short_total is not None:
        parts.append(f"多头强平 {_display_value(long_total or 0)}，空头强平 {_display_value(short_total or 0)}")
    return "；".join(parts) or "未取得"


def _oi_long_short_text(evidence: Mapping[str, Any]) -> str:
    parts: list[str] = []
    oi = evidence.get("oi")
    oi_unit = str(evidence.get("oi_unit") or "").strip()
    if oi is not None:
        rendered = _value_with_unit(oi, oi_unit)
        if oi_unit:
            parts.append(f"未平仓量 {rendered}（单位已明示：{oi_unit}）")
        else:
            parts.append(f"未平仓量 {rendered}（单位未确认）")
    long_short_ratio = evidence.get("long_short_ratio")
    if long_short_ratio is not None:
        parts.append(f"多空比 {_display_value(long_short_ratio)}")
    return "；".join(parts) or "未取得"


def _number_delta(value: object, minus: object) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    if minus is None:
        return float(value)
    if not isinstance(minus, (int, float)):
        return None
    return float(value) - float(minus)


def _section_evidence(section: Mapping[str, Any]) -> Mapping[str, Any]:
    evidence = section.get("evidence")
    return evidence if isinstance(evidence, Mapping) else {}


def _coverage_row(module: str, has_data: bool, path: str, value: str, impact: str) -> tuple[str, str, str, str]:
    if has_data:
        status = "可分析"
    elif value.startswith("当前统一数据层未提供结构化"):
        status = "未覆盖/未实现"
    else:
        status = "缺失/不可用"
    _ = path
    return (module, status, value, impact)


def _pattern_ready(value: object) -> bool:
    if not isinstance(value, Mapping) or not value:
        return False
    status = str(value.get("status") or "")
    return not status.startswith("insufficient")


def _reader_status(value: object) -> str:
    raw = str(value or "").strip()
    labels = {
        "ready": "可用",
        "candidate": "有候选",
        "none": "无信号",
        "open": "未回补",
        "filled": "已回补",
        "partially_filled": "部分回补",
        "unknown": "未确认",
        "no_pattern": "未识别到有效结构",
        "no_candidate": "未匹配到有效候选",
        "no_recent_break_of_structure": "近期没有确认的结构突破",
        "no_source_candle": "未找到结构突破前的来源K线",
        "insufficient": "样本不足",
        "insufficient_ohlc": "K线样本不足",
        "insufficient_swings": "摆动点样本不足",
        "insufficient_swing_types": "摆动点类型不足",
        "invalid_pivot_geometry": "转折点结构无效",
        "pending_breakout": "上破待确认",
        "confirmed_breakout": "上破已确认",
        "pending_breakdown": "下破待确认",
        "confirmed_breakdown": "下破已确认",
        "bullish_cross": "向上交叉",
        "bearish_cross": "向下交叉",
        "above_lines": "价格在双线上方",
        "below_lines": "价格在双线下方",
        "between_lines": "价格位于双线之间",
        "above_blue_band": "价格在蓝带上方",
        "below_blue_band": "价格在蓝带下方",
        "inside_blue_band": "价格位于蓝带内",
        "major_trend_bullish": "长期趋势偏多",
        "major_trend_bearish": "长期趋势偏空",
        "bullish": "看涨",
        "bearish": "看跌",
        "bullish_reversal": "看涨反转",
        "bearish_reversal": "看跌反转",
        "bullish_reversal_countdown_13": "TD 看涨反转 13 计数完成",
        "bearish_reversal_countdown_13": "TD 看跌反转 13 计数完成",
        "bullish_reversal_setup_9": "TD 看涨反转 9 计数完成",
        "bearish_reversal_setup_9": "TD 看跌反转 9 计数完成",
        "near_oversold": "接近超卖",
        "oversold": "超卖",
        "near_overbought": "接近超买",
        "overbought": "超买",
        "neutral": "中性",
        "low": "低",
        "medium": "中等",
        "high": "高",
        "higher_high_higher_low": "高点抬高、低点抬高",
        "lower_high_lower_low": "高点下移、低点下移",
        "mixed_range": "区间震荡或结构转换",
        "bullish_structure": "偏多结构",
        "bearish_structure": "偏空结构",
        "range_or_transition": "区间或转换结构",
        "markup": "上行推进阶段",
        "markdown": "下跌推进阶段",
        "accumulation_or_distribution_unconfirmed": "吸筹/派发阶段未确认",
    }
    if raw in labels:
        return labels[raw]
    if "_" in raw:
        return "未识别状态"
    return raw or "未取得"


def _vegas_evidence_text(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "未取得"
    return (
        f"位置 {_reader_status(value.get('band_position'))}，"
        f"主趋势 {_reader_status(value.get('major_trend'))}，"
        f"EMA144 {_display_value(value.get('ema144'))}，"
        f"EMA169 {_display_value(value.get('ema169'))}，"
        f"距蓝带中线 {_display_value(value.get('distance_to_blue_band_pct'))}%"
    )


def _double_line_evidence_text(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "未取得"
    return (
        f"状态 {_reader_status(value.get('state'))}，"
        f"EMA20 {_display_value(value.get('fast_value'))}，"
        f"EMA50 {_display_value(value.get('slow_value'))}，"
        f"置信度 {_reader_status(value.get('confidence'))}"
    )


def _fvg_evidence_text(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "未取得"
    candidates = value.get("candidates")
    candidate_count = len(candidates) if isinstance(candidates, Sequence) and not isinstance(candidates, (str, bytes, bytearray)) else value.get("candidate_count")
    parts = [
        f"候选 {_display_value(candidate_count)} 个",
        f"未回补 {_display_value(value.get('open_count'))} 个",
    ]
    nearest_above = value.get("nearest_above")
    nearest_below = value.get("nearest_below")
    if isinstance(nearest_above, Mapping):
        parts.append(f"最近上方缺口中线 {_display_value(nearest_above.get('mid'))}")
    if isinstance(nearest_below, Mapping):
        parts.append(f"最近下方缺口中线 {_display_value(nearest_below.get('mid'))}")
    return "，".join(parts)


def _kd_evidence_text(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "未取得"
    return (
        f"K {_display_value(value.get('k'))}（{_reader_status(value.get('k_state'))}），"
        f"D {_display_value(value.get('d'))}（{_reader_status(value.get('d_state'))}），"
        f"J {_display_value(value.get('j'))}"
    )


def _td_evidence_text(value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "未取得"
    return (
        f"方向 {_reader_status(value.get('setup_direction'))}，"
        f"启动计数 {_display_value(value.get('setup_count'))}，"
        f"倒数计数 {_display_value(value.get('countdown_count'))}，"
        f"信号 {_reader_status(value.get('signal'))}，"
        f"置信度 {_reader_status(value.get('confidence'))}"
    )


def _onchain_evidence_text(evidence: Mapping[str, Any]) -> str:
    if not _non_empty_mapping(evidence):
        return "未取得"
    parts: list[str] = []
    if evidence.get("exchange_netflow") is not None:
        parts.append(f"交易所净流量 {_value_with_unit(evidence.get('exchange_netflow'), evidence.get('exchange_netflow_unit'))}")
    if evidence.get("exchange_balance") is not None:
        parts.append(f"交易所余额 {_value_with_unit(evidence.get('exchange_balance'), evidence.get('exchange_balance_unit'))}")
    if evidence.get("active_addresses") is not None:
        parts.append(f"活跃地址 {_value_with_unit(evidence.get('active_addresses'), evidence.get('active_addresses_unit'))}")
    if evidence.get("stablecoin_exchange_netflow") is not None:
        parts.append(
            f"稳定币交易所净流量 {_value_with_unit(evidence.get('stablecoin_exchange_netflow'), evidence.get('stablecoin_exchange_netflow_unit'))}"
        )
    return "；".join(parts) or "已返回链上读数"


def _ahr999_evidence_text(evidence: Mapping[str, Any]) -> str:
    if not _non_empty_mapping(evidence):
        return "未取得"
    value = evidence.get("ahr999") or evidence.get("value")
    if value is None:
        return "已返回 AHR999 材料，但当前读数未取得"
    return f"AHR999 当前读数 {_display_value(value)}（无量纲/指数读数）"


def _pattern_evidence_text(kind: str, value: object) -> str:
    if not isinstance(value, Mapping) or not value:
        return "未取得"
    status = str(value.get("status") or "unknown")
    if kind == "volume_profile":
        if status != "ready":
            return f"状态 {_reader_status(status)}，样本 {_display_value(value.get('sample_count'))}"
        return (
            f"POC {_display_value(value.get('poc'))}，"
            f"价值区间 {_display_value(value.get('value_area_low'))}-{_display_value(value.get('value_area_high'))}，"
            f"样本 {_display_value(value.get('sample_count'))}"
        )
    if kind == "order_block":
        if status != "candidate":
            return f"状态 {_reader_status(status)}"
        return (
            f"状态 {_reader_status(status)}，"
            f"方向 {_reader_status(value.get('direction'))}，"
            f"候选区间 {_display_value(value.get('zone_low'))}-{_display_value(value.get('zone_high'))}，"
            f"结构突破位 {_display_value(value.get('bos_level'))}"
        )
    if kind == "rule_123":
        if status in {"no_pattern", "insufficient_swings"}:
            return f"状态 {_reader_status(status)}，摆动点数量 {_display_value(value.get('swing_count'))}"
        return (
            f"状态 {_reader_status(status)}，"
            f"方向 {_reader_status(value.get('direction'))}，"
            f"突破位 {_display_value(value.get('breakout_level'))}"
        )
    if kind == "amd":
        if status != "ready":
            return f"状态 {_reader_status(status)}，摆动点数量 {_display_value(value.get('swing_count'))}"
        return (
            f"结构 {_reader_status(value.get('structure'))}，"
            f"阶段 {_reader_status(value.get('phase_proxy'))}，"
            f"买侧流动性 {_pivot_price_text(value.get('buy_side_liquidity'))}，"
            f"卖侧流动性 {_pivot_price_text(value.get('sell_side_liquidity'))}"
        )
    if kind == "harmonic":
        ratios = value.get("ratios") if isinstance(value.get("ratios"), Mapping) else {}
        ratio_text = (
            f"AB/XA {_display_value(ratios.get('ab_xa'))}，"
            f"BC/AB {_display_value(ratios.get('bc_ab'))}，"
            f"CD/BC {_display_value(ratios.get('cd_bc'))}"
            if ratios
            else "比例未取得"
        )
        return f"状态 {_reader_status(status)}，形态 {_reader_status(value.get('pattern'))}，方向 {_reader_status(value.get('direction'))}，{ratio_text}"
    return _compact_json(value)


def _pivot_price_text(value: object) -> str:
    if not isinstance(value, Mapping):
        return "未取得"
    return f"{_display_value(value.get('price'))}@{_display_value(value.get('time'))}"


def _non_empty_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) and any(nested is not None for nested in value.values()) else {}


def _has_nested(value: Mapping[str, Any], keys: tuple[str, ...]) -> bool:
    current: object = value
    for key in keys:
        if not isinstance(current, Mapping) or key not in current:
            return False
        current = current[key]
    return current is not None


def _crypto_lens_payload(
    *,
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
    results: Sequence[DataResult],
) -> dict[str, Any]:
    try:
        ticker = _chart_ticker(tool_input=tool_input, rows=[row for result in results for row in result.rows])
        _base, quote = _crypto_pair(ticker=str(tool_input.get("ticker") or ticker), currency=str(tool_input.get("currency") or "USDT"))
        analysis = analyze_crypto_lens_data_results(
            results,
            ticker=ticker,
            quote=quote,
            run_id=str(runtime_context.get("run_id") or "run"),
            call_id=str(runtime_context.get("call_id") or "call"),
            as_of=_resolve_as_of(tool_input.get("current_date")).isoformat(),
            start_date=str(tool_input.get("start_date") or ""),
            end_date=str(tool_input.get("end_date") or ""),
        )
        payload = analysis.as_dict()
        evidence_paths = _write_crypto_lens_evidence(runtime_context=runtime_context, payload=payload)
        return {
            "crypto_lens_status": analysis.status,
            "crypto_lens_analysis": payload,
            "crypto_lens_evidence_paths": evidence_paths,
        }
    except Exception as exc:  # noqa: BLE001
        return {"crypto_lens_status": "error", "crypto_lens_error": str(exc)}


def _write_crypto_lens_evidence(*, runtime_context: Mapping[str, Any], payload: Mapping[str, Any]) -> tuple[str, ...]:
    evidence_root_value = str(runtime_context.get("evidence_root") or "").strip()
    if not evidence_root_value:
        return ()
    evidence_root = Path(evidence_root_value).expanduser()
    run_id = _safe_path_token(str(runtime_context.get("run_id") or "run"))
    call_id = _safe_path_token(str(runtime_context.get("call_id") or "call"))
    path = evidence_root / "data-layer" / "crypto-lens" / run_id / call_id / "analysis.json"
    _write_json(path, payload)
    return (str(path),)


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


def _gap_lines(results: Sequence[DataResult], *, market: Market, crypto_base_asset: str = "") -> list[str]:
    lines: list[str] = []
    has_crypto_cvd_proxy = market == Market.CRYPTO and bool(_crypto_taker_proxy_rows(results))
    for result in results:
        for gap in result.gaps:
            dataset = _gap_subject_label(result, gap=gap, market=market)
            reason = str(getattr(gap.reason, "value", gap.reason))
            if result.rows and reason in _ROW_LEVEL_LIMIT_REASONS:
                continue
            if _is_non_btc_crypto_base(crypto_base_asset) and _is_crypto_liquidation_subject(dataset):
                continue
            if has_crypto_cvd_proxy and dataset == "标准 CVD":
                continue
            required = "、".join(_field_label(field) for field in gap.required_fields)
            detail = f"{dataset}：{_gap_reason_label(reason)}"
            if required:
                detail = f"{detail}，缺少 {required}"
            human = _human_error(gap.human_readable)
            if human and human != _gap_reason_label(reason):
                detail = f"{detail}，说明：{human}"
            lines.append(detail)
    return list(_dedupe(lines))


def _gap_subject_label(result: DataResult, *, market: Market, gap: DataGap | None = None) -> str:
    dataset = _result_dataset(result.request_id) or result.request_id.split(":")[-1]
    default = _dataset_label(dataset, market=market)
    if market != Market.CRYPTO:
        return default

    data_type = str(gap.data_type if gap is not None else dataset)
    fields = _gap_context_fields(result, gap=gap)
    granularity = str(gap.granularity if gap is not None else "")
    if data_type == "crypto_derivative_metric":
        if {"options_open_interest", "options_volume"} & fields:
            return "期权未平仓量/成交量"
        if "cvd" in fields:
            return "标准 CVD"
        if {"liquidation_price", "liquidation_size"} & fields:
            return "清算热力图"
        if {"long_liquidation", "short_liquidation", "liquidation_value"} & fields:
            return "强平统计"
        if {"taker_buy_volume", "taker_sell_volume", "taker_buy_sell_ratio"} & fields:
            return "主动买卖量"
        if "long_short_ratio" in fields:
            return "多空比"
        if "funding_rate" in fields:
            return "资金费率"
        if "open_interest" in fields:
            return "未平仓量"
        if "net_inflow" in fields:
            return "交易所净流入"
        if "etf_flow_usd" in fields:
            return "ETF 资金流"
    if data_type == "order_book_snapshot" and granularity == "1h":
        return "订单簿聚合深度"
    if data_type == "crypto_onchain_metric":
        row_metrics = {str(row.get("metric") or "") for row in result.rows}
        if "ahr999" in row_metrics:
            return "AHR999/链上日频指标"
        if "whale_transfer" in row_metrics:
            return "大额转账/链上事件"
        if "spot_coin_netflow" in row_metrics:
            return "交易所余额/链上净流入"
        if granularity == "event":
            return "链上事件指标"
        if granularity == "realtime":
            return "链上实时指标"
        return "链上日频指标"
    return default


def _gap_context_fields(result: DataResult, *, gap: DataGap | None = None) -> set[str]:
    fields = set(gap.required_fields if gap is not None else ())
    fields.update(_result_spec_fields(result))
    for row in result.rows:
        fields.update(str(key) for key in row)
    return fields


def _result_spec_fields(result: DataResult) -> tuple[str, ...]:
    parts = result.request_id.split(":")
    if len(parts) < 3:
        return ()
    domain = parts[-3]
    dataset = parts[-1]
    try:
        ordinal = int(parts[-2])
    except ValueError:
        return ()
    specs = _CRYPTO_DOMAIN_DATASETS.get(domain, ())
    if not 1 <= ordinal <= len(specs):
        return ()
    data_type, _granularity, fields = specs[ordinal - 1]
    if data_type != dataset:
        return ()
    return fields


def _row_fields(rows: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields and key not in _ROW_FIELD_EXCLUDE:
                fields.append(str(key))
    return tuple(_field_label(field) for field in fields[:12])


def _recent_rows(rows: Sequence[Mapping[str, Any]], *, limit: int = 5) -> tuple[Mapping[str, Any], ...]:
    dated_rows = [(sort_text, row) for row in rows if (sort_text := _row_date_sort_text(row))]
    if not dated_rows:
        return tuple(rows[-limit:])
    return tuple(row for _, row in sorted(dated_rows, key=lambda item: item[0], reverse=True)[:limit])


def _row_date_range(rows: Sequence[Mapping[str, Any]]) -> tuple[str, str] | None:
    dates = [_row_date_sort_text(row) for row in rows]
    dates = [item for item in dates if item]
    if not dates:
        return None
    return (min(dates)[:10], max(dates)[:10])


def _row_date_sort_text(row: Mapping[str, Any]) -> str:
    for key in ("timestamp", "published_at", "period_start", "date", "event_date", "period"):
        value = row.get(key)
        if value is not None and str(value).strip():
            if isinstance(value, datetime):
                return value.isoformat()
            if isinstance(value, date):
                return value.isoformat()
            return str(value).strip()
    return ""


def _row_summary(row: Mapping[str, Any], *, domain: str, dataset: str = "") -> str:
    date_value = row.get("date") or row.get("period_start") or row.get("published_at") or row.get("timestamp")
    parts: list[str] = []
    if date_value:
        parts.append(f"时间 {str(date_value)[:19]}")
    if any(key in row for key in ("revenue", "net_income", "assets", "liabilities", "cash_flow")):
        for key in ("revenue", "net_income", "assets", "liabilities", "cash_flow"):
            value = row.get(key)
            if value is not None:
                parts.append(f"{_field_label(key)} {value}")
        basis_note = _financial_statement_basis_note(row)
        if basis_note:
            parts.append(basis_note)
        return "，".join(parts[:8])
    if any(key in row for key in ("pe", "pb", "ps")):
        for key in ("pe", "pb", "ps", "market_cap", "market_cap_unit", "price"):
            value = row.get(key)
            if value is not None:
                parts.append(f"{_field_label(key)} {value}")
        return "，".join(parts[:8])
    if dataset == "valuation_metric" and any(
        key in row for key in ("price", "market_cap", "fdv", "circulating_supply", "total_supply", "volume")
    ):
        for value_key, unit_key in (
            ("price", "price_unit"),
            ("market_cap", "market_cap_unit"),
            ("fdv", "fdv_unit"),
            ("circulating_supply", "supply_unit"),
            ("total_supply", "supply_unit"),
            ("volume", "volume_unit"),
        ):
            value = row.get(value_key)
            if value is not None:
                parts.append(f"{_field_label(value_key)} {_value_with_unit(value, row.get(unit_key))}")
        return "，".join(parts[:8])
    if dataset == "crypto_derivative_metric":
        for value_key, unit_key in (
            ("open_interest", "open_interest_unit"),
            ("funding_rate", "funding_rate_unit"),
            ("long_short_ratio", ""),
            ("taker_buy_volume", "taker_volume_unit"),
            ("taker_sell_volume", "taker_volume_unit"),
            ("taker_buy_sell_ratio", ""),
            ("long_liquidation", "liquidation_value_unit"),
            ("short_liquidation", "liquidation_value_unit"),
            ("liquidation_value", "liquidation_value_unit"),
            ("liquidation_price", "liquidation_price_unit"),
            ("liquidation_size", "liquidation_size_unit"),
            ("net_inflow", "net_inflow_unit"),
            ("options_open_interest", "options_open_interest_unit"),
            ("options_volume", "options_volume_unit"),
            ("cvd", ""),
            ("etf_flow_usd", "price_unit"),
        ):
            value = row.get(value_key)
            if value is not None:
                parts.append(f"{_field_label(value_key)} {_value_with_unit(value, row.get(unit_key) if unit_key else None)}")
        side = row.get("side")
        if side is not None:
            parts.append(f"{_field_label('side')} {side}")
        return "，".join(parts[:8])
    if dataset == "crypto_onchain_metric" and any(key in row for key in ("metric", "value", "chain")):
        metric = row.get("metric")
        value = row.get("value")
        chain = row.get("chain")
        if metric is not None:
            parts.append(f"{_field_label('metric')} {metric}")
        if value is not None:
            parts.append(f"{_field_label('value')} {_value_with_unit(value, row.get('value_unit'))}")
        if chain is not None:
            parts.append(f"{_field_label('chain')} {chain}")
        return "，".join(parts[:8])
    if domain == "news":
        source = row.get("source")
        title = row.get("title")
        summary = row.get("summary")
        source_roles = tuple(str(item) for item in row.get("source_roles") or ())
        quality_flags = tuple(str(item) for item in row.get("quality_flags") or ())
        discovery_line = (
            "discovery" in source_roles
            or "discovery_not_formal_fact_source" in quality_flags
            or str(source).strip().lower() == "google news"
        )
        parts.append("媒体/搜索线索已返回")
        if source is not None:
            source_label = "媒体聚合搜索线索" if discovery_line else source
            parts.append(f"来源 {source_label}")
        if title is not None and not discovery_line:
            parts.append(f"标题 {title}")
        if summary is not None and not discovery_line:
            parts.append(f"摘要 {str(summary)[:160]}")
        if discovery_line:
            parts.append("公开搜索线索，不能单独作为正式事实源")
        if "symbol_relevance_low" in quality_flags:
            parts.append("疑似非标的结果")
        return "，".join(parts[:6])
    if domain == "social":
        source = row.get("source")
        title = row.get("title")
        parts.append("舆情/互动线索已返回")
        if source is not None:
            parts.append(f"来源 {source}")
        for key in ("score", "sentiment", "keyword", "topic", "reason", "question", "answer", "name"):
            value = row.get(key)
            if value is not None:
                rendered = str(value)
                parts.append(f"{_field_label(key)} {rendered[:120]}")
        if title is not None:
            parts.append(f"标题 {title}")
        return "，".join(parts[:8])
    for value_key, unit_key in (
        ("open", ""),
        ("high", ""),
        ("low", ""),
        ("close", ""),
        ("price", ""),
        ("change_pct", ""),
        ("volume", "volume_unit"),
        ("amount", "amount_unit"),
        ("market_cap", "market_cap_unit"),
        ("float_market_cap", ""),
        ("fdv", "fdv_unit"),
        ("circulating_supply", "supply_unit"),
        ("total_supply", "supply_unit"),
        ("tvl", ""),
        ("revenue", ""),
        ("revenue_basis", ""),
        ("net_income", ""),
        ("net_income_basis", ""),
        ("assets", ""),
        ("assets_basis", ""),
        ("liabilities", ""),
        ("liabilities_basis", ""),
        ("cash_flow", ""),
        ("cash_flow_basis", ""),
        ("roe", ""),
        ("roa", ""),
        ("gross_margin", ""),
        ("debt_ratio", ""),
        ("eps", ""),
        ("pe", ""),
        ("pb", ""),
        ("ps", ""),
        ("main_net", ""),
        ("small_net", ""),
        ("mid_net", ""),
        ("large_net", ""),
        ("super_net", ""),
        ("sector_name", ""),
        ("turnover_rate", ""),
        ("large_order_net", ""),
        ("open_interest", "open_interest_unit"),
        ("funding_rate", "funding_rate_unit"),
        ("long_short_ratio", ""),
        ("long_liquidation", "liquidation_value_unit"),
        ("short_liquidation", "liquidation_value_unit"),
        ("liquidation_value", "liquidation_value_unit"),
        ("net_inflow", "net_inflow_unit"),
        ("metric", ""),
        ("value", "value_unit"),
        ("chain", ""),
        ("source", ""),
        ("sentiment", ""),
        ("score", ""),
    ):
        value = row.get(value_key)
        if value is not None:
            unit = row.get(unit_key) if unit_key else None
            rendered = _value_with_unit(value, unit) if unit is not None else value
            parts.append(f"{_field_label(value_key)} {rendered}")
    return "，".join(parts[:8])


def _financial_statement_basis_note(row: Mapping[str, Any]) -> str:
    basis = {
        str(row.get("revenue_basis") or ""),
        str(row.get("net_income_basis") or ""),
        str(row.get("cash_flow_basis") or ""),
    }
    point = {str(row.get("assets_basis") or ""), str(row.get("liabilities_basis") or "")}
    if "period_cumulative" in basis and "period_end_point_in_time" in point:
        return "口径：收入/净利润/现金流为报告期累计流量，资产/负债为期末时点值"
    return ""


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
        "intraday_bar": "日内分时",
        "quote_snapshot": "实时行情快照",
        "order_book_snapshot": "盘口快照",
        "capital_flow": "资金流",
        "sector_snapshot": "行业/板块资金快照",
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
        "price_unit": "价格单位",
        "change": "涨跌额",
        "change_pct": "涨跌幅",
        "amount": "成交额",
        "amount_unit": "金额单位",
        "fdv": "完全稀释估值",
        "fdv_unit": "完全稀释估值单位",
        "circulating_supply": "流通供应量",
        "total_supply": "总供应量",
        "supply_unit": "供应量单位",
        "tvl": "总锁仓量",
        "volume_unit": "成交量单位",
        "chains": "链",
        "category": "类别",
        "open_interest": "未平仓量",
        "funding_rate": "资金费率",
        "long_short_ratio": "多空比",
        "long_liquidation": "多头清算额",
        "short_liquidation": "空头清算额",
        "liquidation_value": "清算总额",
        "liquidation_price": "清算簇价格",
        "liquidation_size": "清算簇规模",
        "liquidation_price_unit": "清算簇价格单位",
        "liquidation_size_unit": "清算簇规模单位",
        "liquidation_value_unit": "清算金额单位",
        "net_inflow": "净流入",
        "net_inflow_unit": "净流入单位",
        "open_interest_unit": "未平仓量单位",
        "funding_rate_unit": "资金费率单位",
        "taker_volume_unit": "主动买卖量单位",
        "options_open_interest": "期权未平仓量",
        "options_volume": "期权成交量",
        "cvd": "CVD",
        "side": "方向",
        "metric": "指标",
        "value": "数值",
        "value_unit": "数值单位",
        "chain": "链",
        "bid_price": "买一价",
        "bid_size": "买一量",
        "ask_price": "卖一价",
        "ask_size": "卖一量",
        "bids_usd": "买盘聚合金额",
        "bids_quantity": "买盘聚合数量",
        "asks_usd": "卖盘聚合金额",
        "asks_quantity": "卖盘聚合数量",
        "revenue": "收入",
        "revenue_basis": "收入口径",
        "net_income": "净利润",
        "net_income_basis": "净利润口径",
        "assets": "资产",
        "assets_basis": "资产口径",
        "liabilities": "负债",
        "liabilities_basis": "负债口径",
        "cash_flow": "现金流",
        "cash_flow_basis": "现金流口径",
        "roe": "净资产收益率",
        "roa": "资产收益率",
        "gross_margin": "毛利率",
        "debt_ratio": "负债率",
        "eps": "每股收益",
        "pe": "市盈率",
        "pb": "市净率",
        "ps": "市销率",
        "market_cap": "市值",
        "market_cap_unit": "市值单位",
        "float_market_cap": "流通市值",
        "ev_ebitda": "企业价值倍数",
        "event_type": "事件类型",
        "event_date": "事件日期",
        "trade_date": "交易日期",
        "seat": "席位",
        "buy_amount": "买入金额",
        "sell_amount": "卖出金额",
        "main_net": "主力净流入",
        "small_net": "小单净流入",
        "mid_net": "中单净流入",
        "large_net": "大单净流入",
        "super_net": "超大单净流入",
        "sector_name": "行业/板块名称",
        "keyword": "关键词",
        "topic": "主题",
        "reason": "原因",
        "question": "问题",
        "answer": "回答",
        "name": "名称",
        "turnover_rate": "换手率",
        "large_order_net": "大单净额",
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


def _value_with_unit(value: object, unit: object) -> str:
    rendered = _display_value(value)
    unit_text = str(unit or "").strip()
    return f"{rendered} {unit_text}" if unit_text and rendered != "未提供" else rendered


def _status_zh(status: str) -> str:
    return {
        "ready": "可用",
        "partial": "部分可用",
        "insufficient": "不足",
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
