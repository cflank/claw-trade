from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
import re
import sys
from typing import Any, Mapping

import pandas as pd


_REPO_ROOT = Path(__file__).resolve().parents[4]
_TECHLAB_SCRIPTS = _REPO_ROOT / "agents" / "market_analyst" / "skills" / "alphaear-techlab" / "scripts"
_INDICATOR_ENGINE = _TECHLAB_SCRIPTS / "indicator_engine.py"
_CHART_ENGINE = _TECHLAB_SCRIPTS / "chart_engine.py"
_US_TICKER_RE = re.compile(r"^[A-Za-z0-9.^=-][A-Za-z0-9.^=_-]{0,31}$")

_POSITIVE_TERMS = {
    "beat",
    "beats",
    "growth",
    "upgrade",
    "upgrades",
    "record",
    "strong",
    "surge",
    "surges",
    "profit",
    "profits",
    "optimistic",
}
_NEGATIVE_TERMS = {
    "miss",
    "misses",
    "downgrade",
    "downgrades",
    "weak",
    "drop",
    "drops",
    "decline",
    "declines",
    "lawsuit",
    "probe",
    "risk",
    "warning",
    "loss",
    "losses",
}

_ORIGINAL_US_MARKET_INDICATORS = (
    "close_50_sma",
    "close_200_sma",
    "close_10_ema",
    "macd",
    "macds",
    "macdh",
    "rsi",
    "boll",
    "boll_ub",
    "boll_lb",
    "atr",
    "vwma",
)

_ORIGINAL_US_INDICATOR_DESCRIPTIONS = {
    "close_50_sma": "50 SMA: A medium-term trend indicator.",
    "close_200_sma": "200 SMA: A long-term trend benchmark.",
    "close_10_ema": "10 EMA: A responsive short-term average.",
    "macd": "MACD: Computes momentum via differences of EMAs.",
    "macds": "MACD Signal: An EMA smoothing of the MACD line.",
    "macdh": "MACD Histogram: Shows the gap between the MACD line and its signal.",
    "rsi": "RSI: Measures momentum to flag overbought/oversold conditions.",
    "boll": "Bollinger Middle: A 20 SMA serving as the basis for Bollinger Bands.",
    "boll_ub": "Bollinger Upper Band.",
    "boll_lb": "Bollinger Lower Band.",
    "atr": "ATR: Averages true range to measure volatility.",
    "vwma": "VWMA: A moving average weighted by volume.",
}


def run_us_market_data_pack(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    context = _normalize_context(runtime_context, expected_worker="market_analyst", expected_tool="us_market_data_pack")
    request = _normalize_input(tool_input, default_window_days=180)
    if request["market"] != "US":
        return _error_pack("us_market_pack.v1", "market", request, context, "TOOL_PARAMS_INVALID", "US market data pack requires market=US")

    try:
        frame = _download_history(request["ticker"], request["start_date"], request["end_date"], warmup_days=260)
        requested_frame = frame[(frame["date"] >= request["start_date"]) & (frame["date"] <= request["end_date"])].copy()
        if requested_frame.empty:
            return _error_pack("us_market_pack.v1", "market", request, context, "NO_PRICE_ROWS", "yfinance returned no OHLCV rows for the requested US date window")

        chart_paths: list[str] = []
        indicators: dict[str, Any] = {}
        original_indicators: dict[str, Any] = {}
        technical_summary: dict[str, Any] = {}
        warnings: list[str] = []
        original_indicators = _original_us_market_indicators(frame, curr_date=request["end_date"], look_back_days=30)
        missing_indicator_names = list(original_indicators.get("missing_latest") or [])
        if missing_indicator_names:
            warnings.append(f"missing original TradingAgents indicators: {', '.join(missing_indicator_names)}")
        try:
            indicator_module = _load_module(_INDICATOR_ENGINE, "claw_trade_us_indicator_engine")
            chart_module = _load_module(_CHART_ENGINE, "claw_trade_us_chart_engine")
            bundle = indicator_module.analyze_market_frame(frame, ticker=request["ticker"])
            indicators = _jsonable(getattr(bundle, "indicators", {}))
            technical_summary = _jsonable(getattr(bundle, "summary", {}))
            output_dir = Path(context["evidence_root"]) / "techlab" / "charts-local"
            rendered = chart_module.render_market_charts(getattr(bundle, "chart_frame"), ticker=request["ticker"], output_dir=output_dir)
            chart_paths = [str(Path(item)) for item in rendered]
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"technical chart or indicator generation failed: {_redact(str(exc))}")

        latest = requested_frame.iloc[-1].to_dict()
        previous_close = _previous_close(requested_frame)
        price_rows = [_row_to_payload(row) for row in requested_frame.tail(20).to_dict(orient="records")]
        provider_attempts = [_attempt("yfinance", "download", "success", raw_count=len(frame), accepted_count=len(requested_frame))]
        missing_items = []
        if not chart_paths:
            missing_items.append("technical chart files")
        missing_items.extend(f"indicator:{name}" for name in missing_indicator_names)
        pack = {
            "ok": True,
            "schema_version": "us_market_pack.v1",
            "tool_name": "us_market_data_pack",
            "domain": "market",
            "input": request,
            "quality": {
                "status": "complete" if chart_paths and not missing_indicator_names else "partial",
                "warnings": warnings,
            },
            "provider_attempts": provider_attempts,
            "domain_data": {
                "latest_price": _row_to_payload(latest),
                "previous_close": previous_close,
                "recent_price_rows": price_rows,
                "original_tradingagents_indicators": original_indicators,
                "indicators": indicators,
                "technical_summary": technical_summary,
                "chart_files": chart_paths,
                "missing_items": missing_items,
            },
        }
        pack["reader_brief"] = _market_brief(request, latest, previous_close, original_indicators, indicators, technical_summary, chart_paths, warnings)
        _write_pack_files(context, "us_market_data_pack", pack, {"history_tail": price_rows, "original_tradingagents_indicators": original_indicators})
        return pack
    except Exception as exc:  # noqa: BLE001
        return _error_pack("us_market_pack.v1", "market", request, context, "YFINANCE_MARKET_FAILED", _redact(str(exc)))


def run_us_fundamentals_data_pack(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    context = _normalize_context(runtime_context, expected_worker="fundamental_analyst", expected_tool="us_fundamentals_data_pack")
    request = _normalize_input(tool_input, default_window_days=365)
    if request["market"] != "US":
        return _error_pack("us_fundamental_pack.v1", "fundamental", request, context, "TOOL_PARAMS_INVALID", "US fundamentals data pack requires market=US")

    try:
        ticker_obj = _yfinance().Ticker(request["ticker"])
        info = dict(getattr(ticker_obj, "info", {}) or {})
        if not info:
            return _error_pack("us_fundamental_pack.v1", "fundamental", request, context, "NO_FUNDAMENTAL_DATA", "yfinance returned no company fundamentals")
        fields = _select_fundamental_fields(info)
        income_statement = _statement_payload(getattr(ticker_obj, "quarterly_income_stmt", None), curr_date=request["end_date"])
        balance_sheet = _statement_payload(getattr(ticker_obj, "quarterly_balance_sheet", None), curr_date=request["end_date"])
        cashflow = _statement_payload(getattr(ticker_obj, "quarterly_cashflow", None), curr_date=request["end_date"])
        missing_items = [
            name
            for name, value in {
                "trailing PE": fields.get("trailing_pe"),
                "EPS TTM": fields.get("trailing_eps"),
                "revenue TTM": fields.get("revenue_ttm"),
                "free cash flow": fields.get("free_cashflow"),
                "price to book": fields.get("price_to_book"),
                "return on equity": fields.get("return_on_equity"),
                "quarterly income statement": income_statement.get("csv"),
                "quarterly balance sheet": balance_sheet.get("csv"),
                "quarterly cash flow statement": cashflow.get("csv"),
            }.items()
            if value in (None, {}, [])
        ]
        provider_attempts = [
            _attempt("yfinance", "Ticker.info", "success", raw_count=len(info), accepted_count=len(fields)),
            _attempt("yfinance", "Ticker.quarterly_income_stmt", "success", raw_count=_statement_cell_count(income_statement), accepted_count=_statement_cell_count(income_statement)),
            _attempt("yfinance", "Ticker.quarterly_balance_sheet", "success", raw_count=_statement_cell_count(balance_sheet), accepted_count=_statement_cell_count(balance_sheet)),
            _attempt("yfinance", "Ticker.quarterly_cashflow", "success", raw_count=_statement_cell_count(cashflow), accepted_count=_statement_cell_count(cashflow)),
        ]
        pack = {
            "ok": True,
            "schema_version": "us_fundamental_pack.v1",
            "tool_name": "us_fundamentals_data_pack",
            "domain": "fundamental",
            "input": request,
            "quality": {
                "status": "partial" if missing_items else "complete",
                "warnings": [f"missing: {', '.join(missing_items)}"] if missing_items else [],
            },
            "provider_attempts": provider_attempts,
            "domain_data": {
                "company_profile": fields,
                "quarterly_income_statement": income_statement,
                "quarterly_balance_sheet": balance_sheet,
                "quarterly_cash_flow": cashflow,
                "missing_items": missing_items,
            },
        }
        pack["reader_brief"] = _fundamental_brief(request, fields, income_statement, balance_sheet, cashflow, missing_items)
        _write_pack_files(
            context,
            "us_fundamentals_data_pack",
            pack,
            {
                "info": _safe_mapping(info),
                "quarterly_income_statement_csv": income_statement.get("csv"),
                "quarterly_balance_sheet_csv": balance_sheet.get("csv"),
                "quarterly_cash_flow_csv": cashflow.get("csv"),
            },
        )
        return pack
    except Exception as exc:  # noqa: BLE001
        return _error_pack("us_fundamental_pack.v1", "fundamental", request, context, "YFINANCE_FUNDAMENTALS_FAILED", _redact(str(exc)))


def run_us_get_stock_data(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    context = _normalize_context(runtime_context, expected_worker="market_analyst", expected_tool="get_stock_data")
    symbol = _symbol_input(tool_input)
    start_date = _parse_date(str(tool_input.get("start_date") or "").strip(), fallback=date.today() - timedelta(days=180)).isoformat()
    end_date = _parse_date(str(tool_input.get("end_date") or "").strip(), fallback=date.today()).isoformat()
    try:
        ticker = _yfinance().Ticker(symbol)
        data = ticker.history(start=start_date, end=end_date)
        if not isinstance(data, pd.DataFrame) or data.empty:
            return _error_pack("us_get_stock_data.v1", "market", {"ticker": symbol, "market": "US", "start_date": start_date, "end_date": end_date}, context, "NO_PRICE_ROWS", f"No data found for symbol '{symbol}' between {start_date} and {end_date}")
        text = _stock_data_text(symbol, start_date, end_date, data)
        chart_paths, chart_warnings = _render_original_market_chart_files(context=context, symbol=symbol, start_date=start_date, end_date=end_date)
        pack = _tool_text_pack("us_get_stock_data.v1", "get_stock_data", "market", {"symbol": symbol, "start_date": start_date, "end_date": end_date}, text, raw_count=len(data), accepted_count=len(data))
        pack["domain_data"]["chart_files"] = chart_paths
        if chart_warnings:
            pack["quality"]["status"] = "partial"
            pack["quality"]["warnings"] = [*pack["quality"].get("warnings", []), *chart_warnings]
        _write_pack_files(context, "get_stock_data", pack, {"csv": data.to_csv(), "chart_files": chart_paths})
        return pack
    except Exception as exc:  # noqa: BLE001
        return _error_pack("us_get_stock_data.v1", "market", {"ticker": symbol, "market": "US", "start_date": start_date, "end_date": end_date}, context, "YFINANCE_STOCK_DATA_FAILED", _redact(str(exc)))


def run_us_get_indicators(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    context = _normalize_context(runtime_context, expected_worker="market_analyst", expected_tool="get_indicators")
    symbol = _symbol_input(tool_input)
    indicator = str(tool_input.get("indicator") or "").strip().lower()
    if indicator not in _ORIGINAL_US_MARKET_INDICATORS:
        message = f"Indicator {indicator or '<empty>'} is not supported. Please choose from: {list(_ORIGINAL_US_MARKET_INDICATORS)}"
        return _error_pack("us_get_indicators.v1", "market", {"ticker": symbol, "market": "US"}, context, "INDICATOR_UNSUPPORTED", message)
    curr_date = _parse_date(str(tool_input.get("curr_date") or "").strip(), fallback=date.today()).isoformat()
    look_back_days = _positive_int(tool_input.get("look_back_days"), default=30)
    try:
        start_date = (date.fromisoformat(curr_date) - timedelta(days=(365 * 5) + look_back_days + 10)).isoformat()
        frame = _download_history(symbol, start_date, curr_date, warmup_days=0)
        if frame.empty:
            return _error_pack("us_get_indicators.v1", "market", {"ticker": symbol, "market": "US", "curr_date": curr_date}, context, "NO_PRICE_ROWS", f"No data found for symbol '{symbol}' before {curr_date}")
        indicators = _original_us_market_indicators(frame, curr_date=curr_date, look_back_days=look_back_days)
        text = _indicator_text(indicator, curr_date=curr_date, look_back_days=look_back_days, indicators=indicators)
        pack = _tool_text_pack("us_get_indicators.v1", "get_indicators", "market", {"symbol": symbol, "indicator": indicator, "curr_date": curr_date, "look_back_days": look_back_days}, text, raw_count=len(frame), accepted_count=len(frame))
        _write_pack_files(context, "get_indicators", pack, {"indicator": indicator, "values": indicators.get("windows", {}).get(indicator, [])})
        return pack
    except Exception as exc:  # noqa: BLE001
        return _error_pack("us_get_indicators.v1", "market", {"ticker": symbol, "market": "US", "curr_date": curr_date}, context, "YFINANCE_INDICATOR_FAILED", _redact(str(exc)))


def run_us_get_fundamentals(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    context = _normalize_context(runtime_context, expected_worker="fundamental_analyst", expected_tool="get_fundamentals")
    ticker = _ticker_input(tool_input)
    curr_date = str(tool_input.get("curr_date") or "").strip() or date.today().isoformat()
    try:
        info = dict(getattr(_yfinance().Ticker(ticker), "info", {}) or {})
        if not info:
            return _error_pack("us_get_fundamentals.v1", "fundamental", {"ticker": ticker, "market": "US"}, context, "NO_FUNDAMENTAL_DATA", f"No fundamentals data found for symbol '{ticker}'")
        fields = _select_fundamental_fields(info)
        text = f"# Company Fundamentals for {ticker}\n# Data retrieved on: {_now_text()}\n\n{_fundamental_fields_text(fields)}"
        pack = _tool_text_pack("us_get_fundamentals.v1", "get_fundamentals", "fundamental", {"ticker": ticker, "curr_date": curr_date}, text, raw_count=len(info), accepted_count=len(fields))
        _write_pack_files(context, "get_fundamentals", pack, {"info": _safe_mapping(info)})
        return pack
    except Exception as exc:  # noqa: BLE001
        return _error_pack("us_get_fundamentals.v1", "fundamental", {"ticker": ticker, "market": "US"}, context, "YFINANCE_FUNDAMENTALS_FAILED", _redact(str(exc)))


def run_us_get_balance_sheet(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    return _run_us_statement_tool(
        tool_input,
        runtime_context,
        expected_tool="get_balance_sheet",
        schema_version="us_get_balance_sheet.v1",
        title="Balance Sheet",
        quarterly_attr="quarterly_balance_sheet",
        annual_attr="balance_sheet",
    )


def run_us_get_cashflow(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    return _run_us_statement_tool(
        tool_input,
        runtime_context,
        expected_tool="get_cashflow",
        schema_version="us_get_cashflow.v1",
        title="Cash Flow",
        quarterly_attr="quarterly_cashflow",
        annual_attr="cashflow",
    )


def run_us_get_income_statement(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    return _run_us_statement_tool(
        tool_input,
        runtime_context,
        expected_tool="get_income_statement",
        schema_version="us_get_income_statement.v1",
        title="Income Statement",
        quarterly_attr="quarterly_income_stmt",
        annual_attr="income_stmt",
    )


def run_us_news_data_pack(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    context = _normalize_context(runtime_context, expected_worker="news_analyst", expected_tool="us_news_data_pack")
    request = _normalize_input(tool_input, default_window_days=14)
    if request["market"] != "US":
        return _error_pack("us_news_pack.v1", "news", request, context, "TOOL_PARAMS_INVALID", "US news data pack requires market=US")

    try:
        company_items = _fetch_news_items(request["ticker"], source_role="company")
        macro_items: list[dict[str, Any]] = []
        for symbol in ("^GSPC", "^IXIC", "^VIX"):
            macro_items.extend(_fetch_news_items(symbol, source_role="market_index")[:4])
        company_items = _filter_news_by_date(company_items, request["start_date"], request["end_date"])[:12]
        macro_items = _filter_news_by_date(macro_items, request["start_date"], request["end_date"])[:12]
        missing_items = []
        if not company_items:
            missing_items.append("company-specific news")
        if not macro_items:
            missing_items.append("broader market or macro news")
        if not company_items and not macro_items:
            return _error_pack("us_news_pack.v1", "news", request, context, "NO_NEWS_DATA", "yfinance returned no company or market news in the requested window")
        pack = {
            "ok": True,
            "schema_version": "us_news_pack.v1",
            "tool_name": "us_news_data_pack",
            "domain": "news",
            "input": request,
            "quality": {
                "status": "partial" if missing_items else "complete",
                "warnings": [f"missing: {', '.join(missing_items)}"] if missing_items else [],
            },
            "provider_attempts": [
                _attempt("yfinance", "Ticker.news", "success", raw_count=len(company_items), accepted_count=len(company_items)),
                _attempt("yfinance", "index.news", "success", raw_count=len(macro_items), accepted_count=len(macro_items)),
            ],
            "domain_data": {
                "company_news": company_items,
                "market_news": macro_items,
                "missing_items": missing_items,
            },
        }
        pack["reader_brief"] = _news_brief(request, company_items, macro_items, missing_items)
        _write_pack_files(context, "us_news_data_pack", pack, {"company_news": company_items, "market_news": macro_items})
        return pack
    except Exception as exc:  # noqa: BLE001
        return _error_pack("us_news_pack.v1", "news", request, context, "YFINANCE_NEWS_FAILED", _redact(str(exc)))


def run_us_get_news(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    context = _normalize_context(
        runtime_context,
        expected_worker=("news_analyst", "social_analyst"),
        expected_tool="get_news",
    )
    ticker = _ticker_input(tool_input)
    end_date = _parse_date(str(tool_input.get("end_date") or "").strip(), fallback=date.today())
    start_date = _parse_date(str(tool_input.get("start_date") or "").strip(), fallback=end_date - timedelta(days=7))
    try:
        items = _filter_news_by_date(
            _fetch_news_items(ticker, source_role="company"),
            start_date.isoformat(),
            end_date.isoformat(),
        )
        text = _news_items_text(
            f"## {ticker} News, from {start_date.isoformat()} to {end_date.isoformat()}:",
            items,
            empty_text=f"No news found for {ticker} between {start_date.isoformat()} and {end_date.isoformat()}",
        )
        pack = _tool_text_pack(
            "us_get_news.v1",
            "get_news",
            "news",
            {"ticker": ticker, "start_date": start_date.isoformat(), "end_date": end_date.isoformat()},
            text,
            raw_count=len(items),
            accepted_count=len(items),
        )
        _write_pack_files(context, "get_news", pack, {"news_items": items})
        return pack
    except Exception as exc:  # noqa: BLE001
        return _error_pack("us_get_news.v1", "news", {"ticker": ticker, "market": "US"}, context, "YFINANCE_NEWS_FAILED", _redact(str(exc)))


def run_us_get_global_news(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    context = _normalize_context(runtime_context, expected_worker="news_analyst", expected_tool="get_global_news")
    curr_date = _parse_date(str(tool_input.get("curr_date") or "").strip(), fallback=date.today())
    look_back_days = _positive_int(tool_input.get("look_back_days"), default=7)
    limit = _positive_int(tool_input.get("limit"), default=15)
    start_date = curr_date - timedelta(days=look_back_days)
    try:
        items: list[dict[str, Any]] = []
        for symbol in ("^GSPC", "^IXIC", "^VIX", "SPY"):
            items.extend(_fetch_news_items(symbol, source_role="market_index"))
        items = _dedupe_news_items(_filter_news_by_date(items, start_date.isoformat(), curr_date.isoformat()))[:limit]
        text = _news_items_text(
            f"## Global Market News, from {start_date.isoformat()} to {curr_date.isoformat()}:",
            items,
            empty_text=f"No global market news found between {start_date.isoformat()} and {curr_date.isoformat()}",
        )
        pack = _tool_text_pack(
            "us_get_global_news.v1",
            "get_global_news",
            "news",
            {"curr_date": curr_date.isoformat(), "look_back_days": look_back_days, "limit": limit},
            text,
            raw_count=len(items),
            accepted_count=len(items),
        )
        _write_pack_files(context, "get_global_news", pack, {"news_items": items})
        return pack
    except Exception as exc:  # noqa: BLE001
        return _error_pack("us_get_global_news.v1", "news", {"market": "US", "curr_date": curr_date.isoformat()}, context, "YFINANCE_GLOBAL_NEWS_FAILED", _redact(str(exc)))


def run_us_social_sentiment_pack(tool_input: Mapping[str, Any], runtime_context: Mapping[str, Any]) -> dict[str, Any]:
    context = _normalize_context(runtime_context, expected_worker="social_analyst", expected_tool="us_social_sentiment_pack")
    request = _normalize_input(tool_input, default_window_days=14)
    if request["market"] != "US":
        return _error_pack("us_social_pack.v1", "social", request, context, "TOOL_PARAMS_INVALID", "US social sentiment data pack requires market=US")

    try:
        news_items = _filter_news_by_date(_fetch_news_items(request["ticker"], source_role="company"), request["start_date"], request["end_date"])[:20]
        proxy = _score_news_proxy_sentiment(news_items)
        missing_items = ["direct social-media posts", "platform-native sentiment feed"]
        if not news_items:
            missing_items.append("company news proxy")
        pack = {
            "ok": True,
            "schema_version": "us_social_pack.v1",
            "tool_name": "us_social_sentiment_pack",
            "domain": "social",
            "input": request,
            "quality": {
                "status": "partial",
                "warnings": ["No approved US social-media API is configured; sentiment is limited to a news-title proxy."],
            },
            "provider_attempts": [_attempt("yfinance", "Ticker.news", "success", raw_count=len(news_items), accepted_count=len(news_items))],
            "domain_data": {
                "news_proxy_items": news_items,
                "news_proxy_sentiment": proxy,
                "missing_items": missing_items,
            },
        }
        pack["reader_brief"] = _social_brief(request, news_items, proxy, missing_items)
        _write_pack_files(context, "us_social_sentiment_pack", pack, {"news_proxy_items": news_items})
        return pack
    except Exception as exc:  # noqa: BLE001
        return _error_pack("us_social_pack.v1", "social", request, context, "YFINANCE_SOCIAL_PROXY_FAILED", _redact(str(exc)))


def _run_us_statement_tool(
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
    *,
    expected_tool: str,
    schema_version: str,
    title: str,
    quarterly_attr: str,
    annual_attr: str,
) -> dict[str, Any]:
    context = _normalize_context(runtime_context, expected_worker="fundamental_analyst", expected_tool=expected_tool)
    ticker = _ticker_input(tool_input)
    freq = str(tool_input.get("freq") or "quarterly").strip().lower()
    curr_date = _parse_date(str(tool_input.get("curr_date") or "").strip(), fallback=date.today()).isoformat()
    if freq not in {"annual", "quarterly"}:
        return _error_pack(schema_version, "fundamental", {"ticker": ticker, "market": "US"}, context, "TOOL_PARAMS_INVALID", "freq must be annual or quarterly")
    try:
        ticker_obj = _yfinance().Ticker(ticker)
        statement = getattr(ticker_obj, quarterly_attr if freq == "quarterly" else annual_attr, None)
        payload = _statement_payload(statement, curr_date=curr_date)
        if not payload:
            return _error_pack(schema_version, "fundamental", {"ticker": ticker, "market": "US"}, context, "NO_STATEMENT_DATA", f"No {title.lower()} data found for symbol '{ticker}'")
        text = f"# {title} data for {ticker} ({freq})\n# Data retrieved on: {_now_text()}\n\n{payload.get('csv')}"
        pack = _tool_text_pack(schema_version, expected_tool, "fundamental", {"ticker": ticker, "freq": freq, "curr_date": curr_date}, text, raw_count=_statement_cell_count(payload), accepted_count=_statement_cell_count(payload))
        _write_pack_files(context, expected_tool, pack, {"csv": payload.get("csv"), "rows": payload.get("rows")})
        return pack
    except Exception as exc:  # noqa: BLE001
        return _error_pack(schema_version, "fundamental", {"ticker": ticker, "market": "US"}, context, f"YFINANCE_{expected_tool.upper()}_FAILED", _redact(str(exc)))


def _tool_text_pack(
    schema_version: str,
    tool_name: str,
    domain: str,
    request: Mapping[str, Any],
    reader_brief: str,
    *,
    raw_count: int,
    accepted_count: int,
) -> dict[str, Any]:
    return {
        "ok": True,
        "schema_version": schema_version,
        "tool_name": tool_name,
        "domain": domain,
        "input": dict(request),
        "quality": {"status": "complete", "warnings": []},
        "provider_attempts": [_attempt("yfinance", tool_name, "success", raw_count=raw_count, accepted_count=accepted_count)],
        "domain_data": {"text": reader_brief},
        "reader_brief": reader_brief,
    }


def _normalize_context(runtime_context: Mapping[str, Any], *, expected_worker: str | tuple[str, ...], expected_tool: str) -> dict[str, str]:
    if not isinstance(runtime_context, Mapping):
        raise ValueError("runtime_context must be an object")
    context = {key: str(runtime_context.get(key) or "").strip() for key in ("run_id", "stage", "worker_id", "call_id", "dispatch_id", "tool_name", "evidence_root")}
    expected_workers = (expected_worker,) if isinstance(expected_worker, str) else expected_worker
    if context["worker_id"] not in expected_workers:
        raise ValueError(f"worker mismatch: expected {', '.join(expected_workers)}, got {context['worker_id'] or '<empty>'}")
    if context["tool_name"] != expected_tool:
        raise ValueError(f"tool mismatch: expected {expected_tool}, got {context['tool_name'] or '<empty>'}")
    if context["stage"] != "frontline":
        raise ValueError(f"stage must be frontline, got {context['stage'] or '<empty>'}")
    if not context["run_id"] or not context["call_id"] or not context["evidence_root"]:
        raise ValueError("runtime_context missing run_id, call_id, or evidence_root")
    return context


def _normalize_input(tool_input: Mapping[str, Any], *, default_window_days: int) -> dict[str, Any]:
    if not isinstance(tool_input, Mapping):
        raise ValueError("tool_input must be an object")
    ticker = str(tool_input.get("ticker") or "").strip().upper()
    if not ticker or not _US_TICKER_RE.match(ticker):
        raise ValueError("ticker is required and must be a yfinance-compatible US symbol")
    market = str(tool_input.get("market") or "").strip().upper()
    company_name = str(tool_input.get("company_name") or "").strip() or ticker
    end_date = _parse_date(str(tool_input.get("end_date") or "").strip(), fallback=date.today())
    start_date = _parse_date(str(tool_input.get("start_date") or "").strip(), fallback=end_date - timedelta(days=default_window_days))
    if end_date < start_date:
        raise ValueError("end_date cannot be earlier than start_date")
    aliases = tool_input.get("aliases")
    return {
        "ticker": ticker,
        "market": market,
        "company_name": company_name,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "aliases": aliases if isinstance(aliases, list) else [],
    }


def _parse_date(value: str, *, fallback: date) -> date:
    if not value:
        return fallback
    return date.fromisoformat(value)


def _symbol_input(tool_input: Mapping[str, Any]) -> str:
    symbol = str(tool_input.get("symbol") or tool_input.get("ticker") or "").strip().upper()
    if not symbol or not _US_TICKER_RE.match(symbol):
        raise ValueError("symbol is required and must be a yfinance-compatible US symbol")
    return symbol


def _ticker_input(tool_input: Mapping[str, Any]) -> str:
    ticker = str(tool_input.get("ticker") or tool_input.get("symbol") or "").strip().upper()
    if not ticker or not _US_TICKER_RE.match(ticker):
        raise ValueError("ticker is required and must be a yfinance-compatible US symbol")
    return ticker


def _positive_int(value: Any, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return parsed if parsed > 0 else default


def _download_history(ticker: str, start_date: str, end_date: str, *, warmup_days: int) -> pd.DataFrame:
    yf = _yfinance()
    start = date.fromisoformat(start_date) - timedelta(days=warmup_days)
    end_exclusive = date.fromisoformat(end_date) + timedelta(days=1)
    raw = yf.download(
        ticker,
        start=start.isoformat(),
        end=end_exclusive.isoformat(),
        auto_adjust=True,
        progress=False,
        threads=False,
    )
    frame = _normalize_history_frame(raw, ticker=ticker)
    return frame


def _normalize_history_frame(raw: pd.DataFrame, *, ticker: str) -> pd.DataFrame:
    if not isinstance(raw, pd.DataFrame) or raw.empty:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    frame = raw.copy()
    if isinstance(frame.columns, pd.MultiIndex):
        ticker_upper = ticker.upper()
        selected = None
        for level in range(frame.columns.nlevels):
            for value in frame.columns.get_level_values(level):
                if str(value).upper() == ticker_upper:
                    selected = frame.xs(value, axis=1, level=level)
                    break
            if selected is not None:
                frame = selected
                break
        if isinstance(frame.columns, pd.MultiIndex):
            frame.columns = [str(item[0]) for item in frame.columns]
    frame = frame.reset_index()
    columns = {str(column).strip().lower().replace(" ", "_"): column for column in frame.columns}
    rename = {
        columns.get("date") or columns.get("datetime"): "date",
        columns.get("open"): "open",
        columns.get("high"): "high",
        columns.get("low"): "low",
        columns.get("close"): "close",
        columns.get("volume"): "volume",
    }
    rename = {source: target for source, target in rename.items() if source is not None}
    frame = frame.rename(columns=rename)
    missing = {"date", "open", "high", "low", "close", "volume"} - set(frame.columns)
    if missing:
        raise ValueError(f"yfinance history missing columns: {sorted(missing)}")
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date.astype("string")
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["date", "open", "high", "low", "close"]).sort_values("date")
    frame["volume"] = frame["volume"].fillna(0)
    return frame[["date", "open", "high", "low", "close", "volume"]].reset_index(drop=True)


def _previous_close(frame: pd.DataFrame) -> float | None:
    if len(frame) < 2:
        return None
    return _round_float(frame.iloc[-2]["close"])


def _row_to_payload(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "date": str(row.get("date") or ""),
        "open": _round_float(row.get("open")),
        "high": _round_float(row.get("high")),
        "low": _round_float(row.get("low")),
        "close": _round_float(row.get("close")),
        "volume": _round_float(row.get("volume"), digits=0),
    }


def _original_us_market_indicators(frame: pd.DataFrame, *, curr_date: str, look_back_days: int) -> dict[str, Any]:
    enriched = frame.copy()
    close = pd.to_numeric(enriched["close"], errors="coerce")
    high = pd.to_numeric(enriched["high"], errors="coerce")
    low = pd.to_numeric(enriched["low"], errors="coerce")
    volume = pd.to_numeric(enriched["volume"], errors="coerce").fillna(0)

    enriched["close_50_sma"] = close.rolling(50, min_periods=50).mean()
    enriched["close_200_sma"] = close.rolling(200, min_periods=200).mean()
    enriched["close_10_ema"] = close.ewm(span=10, adjust=False, min_periods=10).mean()

    ema12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    enriched["macd"] = ema12 - ema26
    enriched["macds"] = enriched["macd"].ewm(span=9, adjust=False, min_periods=9).mean()
    enriched["macdh"] = enriched["macd"] - enriched["macds"]

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.rolling(14, min_periods=14).mean()
    avg_loss = loss.rolling(14, min_periods=14).mean()
    rs = avg_gain / avg_loss.mask(avg_loss == 0)
    enriched["rsi"] = 100 - (100 / (1 + rs))
    enriched.loc[avg_loss == 0, "rsi"] = 100

    boll_mid = close.rolling(20, min_periods=20).mean()
    boll_std = close.rolling(20, min_periods=20).std(ddof=0)
    enriched["boll"] = boll_mid
    enriched["boll_ub"] = boll_mid + (2 * boll_std)
    enriched["boll_lb"] = boll_mid - (2 * boll_std)

    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    enriched["atr"] = true_range.rolling(14, min_periods=14).mean()

    volume_sum = volume.rolling(10, min_periods=10).sum()
    enriched["vwma"] = (close * volume).rolling(10, min_periods=10).sum() / volume_sum.mask(volume_sum == 0)

    latest: dict[str, Any] = {}
    latest_rows = enriched[enriched["date"] <= curr_date]
    latest_row = latest_rows.iloc[-1] if not latest_rows.empty else None
    for indicator in _ORIGINAL_US_MARKET_INDICATORS:
        latest[indicator] = _round_float(latest_row[indicator]) if latest_row is not None else None

    end = date.fromisoformat(curr_date)
    start = end - timedelta(days=look_back_days)
    by_date = {str(row["date"]): row for row in enriched.to_dict(orient="records")}
    windows: dict[str, list[dict[str, Any]]] = {indicator: [] for indicator in _ORIGINAL_US_MARKET_INDICATORS}
    current = end
    while current >= start:
        row = by_date.get(current.isoformat())
        for indicator in _ORIGINAL_US_MARKET_INDICATORS:
            value = _round_float(row.get(indicator)) if row else None
            windows[indicator].append({"date": current.isoformat(), "value": value})
        current -= timedelta(days=1)

    return {
        "source_semantics": "original TradingAgents yfinance get_stock_data + get_indicators",
        "look_back_days": look_back_days,
        "latest": latest,
        "windows": windows,
        "missing_latest": [indicator for indicator, value in latest.items() if value is None],
    }


def _stock_data_text(symbol: str, start_date: str, end_date: str, data: pd.DataFrame) -> str:
    frame = data.copy()
    if getattr(frame.index, "tz", None) is not None:
        frame.index = frame.index.tz_localize(None)
    for column in ("Open", "High", "Low", "Close", "Adj Close"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce").round(2)
    return (
        f"# Stock data for {symbol} from {start_date} to {end_date}\n"
        f"# Total records: {len(frame)}\n"
        f"# Data retrieved on: {_now_text()}\n\n"
        f"{frame.to_csv()}"
    )


def _indicator_text(indicator: str, *, curr_date: str, look_back_days: int, indicators: Mapping[str, Any]) -> str:
    windows = indicators.get("windows") if isinstance(indicators.get("windows"), Mapping) else {}
    rows = windows.get(indicator) if isinstance(windows, Mapping) else None
    if not isinstance(rows, list):
        rows = []
    end = date.fromisoformat(curr_date)
    start = end - timedelta(days=look_back_days)
    values = "\n".join(f"{row.get('date')}: {_indicator_value_text(row.get('value'))}" for row in rows)
    return (
        f"## {indicator} values from {start.isoformat()} to {curr_date}:\n\n"
        f"{values}\n\n"
        f"{_ORIGINAL_US_INDICATOR_DESCRIPTIONS[indicator]}"
    )


def _render_original_market_chart_files(
    *,
    context: Mapping[str, str],
    symbol: str,
    start_date: str,
    end_date: str,
) -> tuple[list[str], list[str]]:
    try:
        frame = _download_history(symbol, start_date, end_date, warmup_days=260)
        if frame.empty:
            return [], ["technical chart generation skipped: no normalized price rows"]
        indicator_module = _load_module(_INDICATOR_ENGINE, "claw_trade_us_indicator_engine")
        chart_module = _load_module(_CHART_ENGINE, "claw_trade_us_chart_engine")
        bundle = indicator_module.analyze_market_frame(frame, ticker=symbol)
        output_dir = Path(context["evidence_root"]) / "techlab" / "charts-local"
        rendered = chart_module.render_market_charts(getattr(bundle, "chart_frame"), ticker=symbol, output_dir=output_dir)
        return [str(Path(item)) for item in rendered], []
    except Exception as exc:  # noqa: BLE001
        return [], [f"technical chart generation failed: {_redact(str(exc))}"]


def _select_fundamental_fields(info: Mapping[str, Any]) -> dict[str, Any]:
    fields = {
        "name": info.get("longName") or info.get("shortName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "currency": info.get("currency"),
        "market_cap": info.get("marketCap"),
        "enterprise_value": info.get("enterpriseValue"),
        "trailing_pe": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "peg_ratio": info.get("pegRatio"),
        "price_to_book": info.get("priceToBook"),
        "trailing_eps": info.get("trailingEps"),
        "forward_eps": info.get("forwardEps"),
        "dividend_yield": info.get("dividendYield"),
        "beta": info.get("beta"),
        "fifty_two_week_high": info.get("fiftyTwoWeekHigh"),
        "fifty_two_week_low": info.get("fiftyTwoWeekLow"),
        "fifty_day_average": info.get("fiftyDayAverage"),
        "two_hundred_day_average": info.get("twoHundredDayAverage"),
        "return_on_equity": info.get("returnOnEquity"),
        "return_on_assets": info.get("returnOnAssets"),
        "profit_margins": info.get("profitMargins"),
        "operating_margins": info.get("operatingMargins"),
        "gross_margins": info.get("grossMargins"),
        "revenue_growth": info.get("revenueGrowth"),
        "earnings_growth": info.get("earningsGrowth"),
        "debt_to_equity": info.get("debtToEquity"),
        "current_ratio": info.get("currentRatio"),
        "book_value": info.get("bookValue"),
        "total_cash": info.get("totalCash"),
        "total_debt": info.get("totalDebt"),
        "revenue_ttm": info.get("totalRevenue"),
        "gross_profit": info.get("grossProfits"),
        "ebitda": info.get("ebitda"),
        "net_income": info.get("netIncomeToCommon"),
        "free_cashflow": info.get("freeCashflow"),
        "operating_cashflow": info.get("operatingCashflow"),
        "business_summary": _trim_text(info.get("longBusinessSummary"), 700),
    }
    return {key: _jsonable(value) for key, value in fields.items() if value not in (None, "", [], {})}


def _statement_payload(statement: Any, *, curr_date: str) -> dict[str, Any]:
    if not isinstance(statement, pd.DataFrame) or statement.empty:
        return {}
    filtered = _filter_statement_columns(statement, curr_date=curr_date)
    if filtered.empty:
        return {}
    payload: dict[str, Any] = {
        "columns": [str(column) for column in filtered.columns],
        "csv": filtered.to_csv(),
        "rows": {},
    }
    for row_name in filtered.index:
        row_payload = {}
        for column in filtered.columns:
            converted = _jsonable(filtered.loc[row_name, column])
            if converted is not None:
                row_payload[str(column)] = converted
        if row_payload:
            payload["rows"][str(row_name)] = row_payload
    return payload


def _filter_statement_columns(statement: pd.DataFrame, *, curr_date: str) -> pd.DataFrame:
    cutoff = pd.Timestamp(curr_date)
    column_dates = pd.to_datetime(statement.columns, errors="coerce")
    mask = column_dates <= cutoff
    return statement.loc[:, mask]


def _statement_cell_count(payload: Mapping[str, Any]) -> int:
    rows = payload.get("rows")
    if not isinstance(rows, Mapping):
        return 0
    return sum(len(row) for row in rows.values() if isinstance(row, Mapping))


def _fetch_news_items(symbol: str, *, source_role: str) -> list[dict[str, Any]]:
    ticker_obj = _yfinance().Ticker(symbol)
    raw_items = getattr(ticker_obj, "news", []) or []
    items: list[dict[str, Any]] = []
    for raw in raw_items:
        item = _extract_news_item(raw, symbol=symbol, source_role=source_role)
        if item is not None:
            items.append(item)
    return items


def _extract_news_item(raw: Any, *, symbol: str, source_role: str) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    content = raw.get("content") if isinstance(raw.get("content"), Mapping) else raw
    title = str(content.get("title") or "").strip()
    if not title:
        return None
    provider = content.get("provider")
    publisher = ""
    if isinstance(provider, Mapping):
        publisher = str(provider.get("displayName") or provider.get("name") or "").strip()
    publisher = publisher or str(content.get("publisher") or raw.get("publisher") or "").strip()
    published_at = _extract_news_date(content, raw)
    summary = _trim_text(content.get("summary") or content.get("description") or raw.get("summary"), 500)
    link = _extract_news_link(content, raw)
    return {
        "symbol": symbol,
        "source_role": source_role,
        "title": title,
        "publisher": publisher,
        "published_at": published_at,
        "summary": summary,
        "link": link,
    }


def _extract_news_link(content: Mapping[str, Any], raw: Mapping[str, Any]) -> str | None:
    for key in ("canonicalUrl", "clickThroughUrl"):
        value = content.get(key) if key in content else raw.get(key)
        if isinstance(value, Mapping):
            url = str(value.get("url") or "").strip()
            if url:
                return url
    for key in ("link", "url"):
        value = content.get(key) if key in content else raw.get(key)
        text = str(value or "").strip()
        if text:
            return text
    return None


def _extract_news_date(content: Mapping[str, Any], raw: Mapping[str, Any]) -> str | None:
    for key in ("pubDate", "displayTime", "providerPublishTime"):
        value = content.get(key) if key in content else raw.get(key)
        if value is None:
            continue
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
        text = str(value).strip()
        if not text:
            continue
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            if len(text) >= 10:
                return text[:10]
    return None


def _filter_news_by_date(items: list[dict[str, Any]], start_date: str, end_date: str) -> list[dict[str, Any]]:
    filtered = []
    for item in items:
        published = item.get("published_at")
        if isinstance(published, str) and re.match(r"^\d{4}-\d{2}-\d{2}$", published):
            if published < start_date or published > end_date:
                continue
        filtered.append(item)
    return filtered


def _dedupe_news_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = (str(item.get("title") or ""), str(item.get("link") or ""))
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _news_items_text(title: str, items: list[dict[str, Any]], *, empty_text: str) -> str:
    if not items:
        return empty_text
    parts = [title]
    for item in items:
        publisher = item.get("publisher") or "publisher unavailable"
        parts.append(
            f"### {item.get('title')} (source: {publisher})\n"
            f"{item.get('summary') or ''}\n"
            f"Link: {item.get('link') or 'unavailable'}"
        )
    return "\n\n".join(parts)


def _score_news_proxy_sentiment(items: list[dict[str, Any]]) -> dict[str, Any]:
    positive = 0
    negative = 0
    for item in items:
        text = f"{item.get('title', '')} {item.get('summary', '')}".lower()
        words = set(re.findall(r"[a-z]+", text))
        positive += len(words & _POSITIVE_TERMS)
        negative += len(words & _NEGATIVE_TERMS)
    if positive > negative:
        label = "positive-news-proxy"
    elif negative > positive:
        label = "negative-news-proxy"
    else:
        label = "neutral-news-proxy"
    return {
        "method": "keyword count over yfinance company news titles and summaries",
        "label": label,
        "positive_hits": positive,
        "negative_hits": negative,
        "item_count": len(items),
        "limitations": "This is not direct social-media sentiment.",
    }


def _market_brief(
    request: Mapping[str, Any],
    latest: Mapping[str, Any],
    previous_close: float | None,
    original_indicators: Mapping[str, Any],
    indicators: Mapping[str, Any],
    technical_summary: Mapping[str, Any],
    chart_paths: list[str],
    warnings: list[str],
) -> str:
    latest_close = _round_float(latest.get("close"))
    change = None
    if previous_close:
        change = round(((float(latest_close) - previous_close) / previous_close) * 100, 2) if latest_close is not None else None
    parts = [
        f"{request['company_name']} ({request['ticker']}) US market data from {request['start_date']} to {request['end_date']}.",
        f"Latest OHLCV row: date {latest.get('date')}, open {_round_float(latest.get('open'))}, high {_round_float(latest.get('high'))}, low {_round_float(latest.get('low'))}, close {latest_close}, volume {_round_float(latest.get('volume'), digits=0)}.",
    ]
    if previous_close is not None and change is not None:
        parts.append(f"Previous close was {previous_close}; latest close changed {change}% from the previous available row.")
    if original_indicators:
        latest_indicators = original_indicators.get("latest") if isinstance(original_indicators.get("latest"), Mapping) else {}
        parts.append(
            "Original TradingAgents yfinance indicator latest values: "
            + _compact_mapping(latest_indicators, max_items=len(_ORIGINAL_US_MARKET_INDICATORS))
            + "."
        )
        windows = original_indicators.get("windows") if isinstance(original_indicators.get("windows"), Mapping) else {}
        for indicator in _ORIGINAL_US_MARKET_INDICATORS:
            window_rows = windows.get(indicator) if isinstance(windows, Mapping) else None
            if not isinstance(window_rows, list):
                continue
            values = "\n".join(f"{row.get('date')}: {_indicator_value_text(row.get('value'))}" for row in window_rows)
            parts.append(
                f"## {indicator} values from "
                f"{(date.fromisoformat(request['end_date']) - timedelta(days=int(original_indicators.get('look_back_days') or 30))).isoformat()} "
                f"to {request['end_date']}:\n\n{values}\n\n{_ORIGINAL_US_INDICATOR_DESCRIPTIONS[indicator]}"
            )
    if technical_summary:
        parts.append(f"Local chart summary: trend={technical_summary.get('trend')}, volume_state={technical_summary.get('volume_state')}, support_levels={technical_summary.get('support_levels')}, resistance_levels={technical_summary.get('resistance_levels')}.")
    if indicators:
        parts.append(f"Additional local chart indicators include MA={indicators.get('ma')}, MACD={indicators.get('macd')}, RSI={indicators.get('rsi')}, Bollinger={indicators.get('boll')}, ATR={indicators.get('atr')}.")
    if chart_paths:
        parts.append(f"Generated {len(chart_paths)} local technical chart files for report asset export.")
    if warnings:
        parts.append(f"Evidence gaps: {'; '.join(warnings)}.")
    return "\n\n".join(parts)


def _fundamental_brief(
    request: Mapping[str, Any],
    fields: Mapping[str, Any],
    income_statement: Mapping[str, Any],
    balance_sheet: Mapping[str, Any],
    cashflow: Mapping[str, Any],
    missing_items: list[str],
) -> str:
    parts = [
        f"# Company Fundamentals for {request['ticker']}",
        f"Original TradingAgents yfinance fundamentals for {request['company_name']} ({request['ticker']}).",
        _fundamental_fields_text(fields),
    ]
    if income_statement:
        parts.append(f"# Income Statement data for {request['ticker']} (quarterly)\n\n{income_statement.get('csv')}")
    if balance_sheet:
        parts.append(f"# Balance Sheet data for {request['ticker']} (quarterly)\n\n{balance_sheet.get('csv')}")
    if cashflow:
        parts.append(f"# Cash Flow data for {request['ticker']} (quarterly)\n\n{cashflow.get('csv')}")
    if missing_items:
        parts.append(f"Evidence gaps: {', '.join(missing_items)}.")
    return "\n\n".join(parts)


def _news_brief(
    request: Mapping[str, Any],
    company_items: list[dict[str, Any]],
    macro_items: list[dict[str, Any]],
    missing_items: list[str],
) -> str:
    parts = [
        f"{request['company_name']} ({request['ticker']}) US news from {request['start_date']} to {request['end_date']}.",
    ]
    if company_items:
        parts.append("Company news: " + "; ".join(_headline(item) for item in company_items[:8]) + ".")
    if macro_items:
        parts.append("Broader market news from S&P 500, Nasdaq, and VIX proxies: " + "; ".join(_headline(item) for item in macro_items[:8]) + ".")
    if missing_items:
        parts.append(f"Evidence gaps: {', '.join(missing_items)}.")
    return "\n\n".join(parts)


def _social_brief(
    request: Mapping[str, Any],
    news_items: list[dict[str, Any]],
    proxy: Mapping[str, Any],
    missing_items: list[str],
) -> str:
    parts = [
        f"{request['company_name']} ({request['ticker']}) US public-sentiment evidence is partial.",
        f"Configured source only provides a news-title proxy: label={proxy.get('label')}, positive_hits={proxy.get('positive_hits')}, negative_hits={proxy.get('negative_hits')}, item_count={proxy.get('item_count')}.",
    ]
    if news_items:
        parts.append("News proxy items: " + "; ".join(_headline(item) for item in news_items[:8]) + ".")
    parts.append(f"Evidence gaps: {', '.join(missing_items)}. Do not describe this as direct social-media sentiment.")
    return "\n\n".join(parts)


def _headline(item: Mapping[str, Any]) -> str:
    published = item.get("published_at") or "date unavailable"
    publisher = item.get("publisher") or "publisher unavailable"
    return f"{published} {publisher}: {item.get('title')}"


def _write_pack_files(context: Mapping[str, str], pack_name: str, pack: Mapping[str, Any], raw_payload: Mapping[str, Any]) -> None:
    evidence_root = Path(context["evidence_root"])
    raw_dir = evidence_root / "provider_raw" / "yfinance"
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / f"{pack_name}.json").write_text(json.dumps(_jsonable(raw_payload), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / f"{pack_name}.json").write_text(json.dumps(_jsonable(pack), ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def _error_pack(schema_version: str, domain: str, request: Mapping[str, Any], context: Mapping[str, str], code: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "schema_version": schema_version,
        "tool_name": context.get("tool_name"),
        "domain": domain,
        "input": dict(request),
        "quality": {"status": "failed", "warnings": [message]},
        "provider_attempts": [_attempt("yfinance", domain, "error", raw_count=0, accepted_count=0, error_message=message)],
        "error": {"code": code, "message": message},
        "reader_brief": f"US {domain} data pack failed: {code}. {message}",
    }


def _attempt(
    provider: str,
    endpoint: str,
    status: str,
    *,
    raw_count: int,
    accepted_count: int,
    error_message: str | None = None,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    return {
        "provider": provider,
        "endpoint": endpoint,
        "role": "primary",
        "status": status,
        "started_at": now,
        "finished_at": now,
        "elapsed_ms": 0,
        "timeout_ms": 0,
        "raw_count": raw_count,
        "accepted_count": accepted_count,
        "error_message_redacted": _redact(error_message) if error_message else None,
    }


def _yfinance():
    import yfinance as yf

    return yf


def _load_module(path: Path, module_name: str) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"unable to load module: {path}")
    module = importlib.util.module_from_spec(spec)
    previous = sys.modules.get(module_name)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        if previous is None:
            sys.modules.pop(module_name, None)
        else:
            sys.modules[module_name] = previous
        raise
    return module


def _round_float(value: Any, *, digits: int = 4) -> float | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except TypeError:
        pass
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def _jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, pd.Timestamp):
        return value.isoformat()
    if hasattr(value, "item"):
        try:
            return _jsonable(value.item())
        except Exception:  # noqa: BLE001
            pass
    if isinstance(value, float) and pd.isna(value):
        return None
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _safe_mapping(payload: Mapping[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in payload.items():
        if "token" in str(key).lower() or "secret" in str(key).lower() or "key" == str(key).lower():
            continue
        safe[str(key)] = _jsonable(value)
    return safe


def _compact_mapping(payload: Mapping[str, Any], *, max_items: int = 12) -> str:
    parts = []
    for key, value in list(payload.items())[:max_items]:
        text = _trim_text(value, 160)
        parts.append(f"{key}={text}")
    return "; ".join(parts) if parts else "no usable fields"


def _indicator_value_text(value: Any) -> str:
    rounded = _round_float(value)
    return "N/A" if rounded is None else str(rounded)


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _fundamental_fields_text(fields: Mapping[str, Any]) -> str:
    labels = [
        ("Name", "name"),
        ("Sector", "sector"),
        ("Industry", "industry"),
        ("Market Cap", "market_cap"),
        ("PE Ratio (TTM)", "trailing_pe"),
        ("Forward PE", "forward_pe"),
        ("PEG Ratio", "peg_ratio"),
        ("Price to Book", "price_to_book"),
        ("EPS (TTM)", "trailing_eps"),
        ("Forward EPS", "forward_eps"),
        ("Dividend Yield", "dividend_yield"),
        ("Beta", "beta"),
        ("52 Week High", "fifty_two_week_high"),
        ("52 Week Low", "fifty_two_week_low"),
        ("50 Day Average", "fifty_day_average"),
        ("200 Day Average", "two_hundred_day_average"),
        ("Revenue (TTM)", "revenue_ttm"),
        ("Gross Profit", "gross_profit"),
        ("EBITDA", "ebitda"),
        ("Net Income", "net_income"),
        ("Profit Margin", "profit_margins"),
        ("Operating Margin", "operating_margins"),
        ("Return on Equity", "return_on_equity"),
        ("Return on Assets", "return_on_assets"),
        ("Debt to Equity", "debt_to_equity"),
        ("Current Ratio", "current_ratio"),
        ("Book Value", "book_value"),
        ("Free Cash Flow", "free_cashflow"),
    ]
    lines = []
    for label, key in labels:
        value = fields.get(key)
        if value is not None:
            lines.append(f"{label}: {value}")
    return "\n".join(lines) if lines else "No usable company fundamentals fields returned."


def _trim_text(value: Any, max_chars: int = 300) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if len(text) <= max_chars else text[: max_chars - 1].rstrip() + "."


def _redact(text: str | None) -> str:
    source = str(text or "")
    source = re.sub(r"(?i)(api[_-]?key|token|authorization|password|passwd|signature|secret)\s*[=:]\s*\S+", r"\1=***", source)
    return source[:500]


__all__ = [
    "run_us_market_data_pack",
    "run_us_fundamentals_data_pack",
    "run_us_get_stock_data",
    "run_us_get_indicators",
    "run_us_get_fundamentals",
    "run_us_get_balance_sheet",
    "run_us_get_cashflow",
    "run_us_get_income_statement",
    "run_us_get_news",
    "run_us_get_global_news",
    "run_us_news_data_pack",
    "run_us_social_sentiment_pack",
]
