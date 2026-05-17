from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Callable, Mapping, Sequence
from urllib.parse import quote

import pandas as pd

from .crypto_pack_common import (
    DEFAULT_TIMEOUT_SECONDS,
    blocked_attempt,
    env_text,
    finish_attempt,
    normalize_crypto_input,
    normalize_frontline_context,
    number_or_none,
    requests_json,
    utc_now,
    write_pack_files,
)
from .crypto_provider_cache import (
    commit_crypto_provider_cache,
    fetch_json_with_crypto_provider_cache,
    insert_crypto_provider_attempts,
    provider_attempt_gap_messages,
    resolve_crypto_provider_cache_collections,
    ttl_seconds_for_provider,
)


_SCHEMA_VERSION = "crypto_market_pack.v1"
_TOOL_NAME = "crypto_market_data_pack"
_WORKER_ID = "market_analyst"
_MARKET = "CRYPTO"
_DEFAULT_INTERVAL = "1d"
_DEFAULT_WINDOW_DAYS = 180
_WARMUP_DAYS = 260
_REPO_ROOT = Path(__file__).resolve().parents[4]
_CHART_ENGINE = _REPO_ROOT / "agents" / "market_analyst" / "skills" / "alphaear-techlab" / "scripts" / "chart_engine.py"
_BINANCE_BASE_URL_ENV = "CRYPTO_MARKET_BINANCE_BASE_URL"
_BINANCE_SYMBOL_OVERRIDES_ENV = "CRYPTO_MARKET_BINANCE_SYMBOL_OVERRIDES_JSON"
_QUOTE_ASSET_ENV = "CRYPTO_MARKET_QUOTE_ASSET"
_SUPPORTED_INTERVALS = {"1h", "4h", "1d"}
_SYMBOL_RE = re.compile(r"^[A-Z0-9]{2,40}$")
_BB_DEFAULT_CWD = Path("/mnt/d/src/BB/mcp/crypto-data-mcp")
_BB_CONTEXT_ENDPOINT = "build_trade_context"
_BB_CONTEXT_URL = "bb://crypto-data-mcp/build_trade_context"
_BB_CONTEXT_INCLUDE = ("market", "technical", "derivatives", "liquidation_map", "onchain", "macro", "events", "ahr999")

FetchJson = Callable[..., Any]
ChartRender = Callable[[pd.DataFrame, str, Path], Sequence[Path | str]]


def run_crypto_market_data_pack(
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
) -> dict[str, Any]:
    return build_crypto_market_data_pack(tool_input, runtime_context)


def build_crypto_market_data_pack(
    tool_input: Mapping[str, Any],
    runtime_context: Mapping[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    fetch_json: FetchJson | None = None,
    chart_render: ChartRender | None = None,
    bb_trade_context_fetch: FetchJson | None = None,
    provider_cache_collection: Any | None = None,
    provider_attempts_collection: Any | None = None,
    provider_rate_limit_collection: Any | None = None,
) -> dict[str, Any]:
    env = os.environ if env is None else env
    fetch_json = requests_json if fetch_json is None else fetch_json
    context = normalize_frontline_context(runtime_context, tool_name=_TOOL_NAME, worker_id=_WORKER_ID)
    request = _normalize_market_input(tool_input, context, env=env)
    if provider_cache_collection is None and provider_attempts_collection is None and provider_rate_limit_collection is None:
        collections = resolve_crypto_provider_cache_collections(env)
        provider_cache_collection = collections.cache
        provider_attempts_collection = collections.attempts
        provider_rate_limit_collection = collections.rate_limits

    attempts: list[dict[str, Any]] = []
    raw_payload: dict[str, Any] = {}
    bb_summary, bb_attempts, bb_raw = _load_bb_trade_context(
        request,
        context=context,
        env=env,
        fetch_json=bb_trade_context_fetch,
        provider_cache_collection=provider_cache_collection,
        provider_attempts_collection=provider_attempts_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    attempts.extend(bb_attempts)
    if bb_raw is not None:
        raw_payload["bb_trade_context"] = bb_raw
    rows, load_attempts, raw = _load_binance_klines(
        request,
        context=context,
        env=env,
        fetch_json=fetch_json,
        provider_cache_collection=provider_cache_collection,
        provider_attempts_collection=provider_attempts_collection,
        provider_rate_limit_collection=provider_rate_limit_collection,
    )
    attempts.extend(load_attempts)
    raw_payload["binance_klines"] = raw

    data_gaps: list[str] = []
    data_gaps.extend(_bb_data_gap_messages(bb_summary))
    data_gaps.extend(provider_attempt_gap_messages(attempts))
    chart_paths: list[str] = []
    indicators: dict[str, Any] = {}
    technical_summary: dict[str, Any] = {}

    if rows.empty:
        data_gaps.append("Binance public OHLCV 未返回可用价格行；本地技术图表无法生成。")
        pack = _pack(
            request=request,
            attempts=attempts,
            data_gaps=data_gaps,
            rows=rows,
            indicators=indicators,
            technical_summary=technical_summary,
            chart_paths=chart_paths,
            bb_summary=bb_summary,
        )
        write_pack_files(context, tool_name=_TOOL_NAME, pack=pack, raw_payload=raw_payload)
        insert_crypto_provider_attempts(attempts, collection=provider_attempts_collection)
        return pack

    chart_frame = _indicator_frame(rows)
    indicators = _latest_indicators(chart_frame)
    technical_summary = _technical_summary(chart_frame)
    chart_paths, chart_gap = _render_charts(
        chart_frame=chart_frame,
        ticker=request["ticker"],
        evidence_root=Path(context["evidence_root"]),
        chart_render=chart_render,
    )
    if chart_gap:
        data_gaps.append(chart_gap)

    pack = _pack(
        request=request,
        attempts=attempts,
        data_gaps=data_gaps,
        rows=rows,
        indicators=indicators,
        technical_summary=technical_summary,
        chart_paths=chart_paths,
        bb_summary=bb_summary,
    )
    write_pack_files(context, tool_name=_TOOL_NAME, pack=pack, raw_payload=raw_payload)
    insert_crypto_provider_attempts(attempts, collection=provider_attempts_collection)
    return pack


def _normalize_market_input(tool_input: Mapping[str, Any], context: Mapping[str, str], *, env: Mapping[str, str]) -> dict[str, Any]:
    base = normalize_crypto_input(tool_input, context)
    end_date = _parse_date(base.get("end_date"), fallback=_today_utc())
    start_date = _parse_date(base.get("start_date"), fallback=end_date - timedelta(days=_DEFAULT_WINDOW_DAYS - 1))
    if start_date > end_date:
        raise ValueError("tool_input.start_date must be <= end_date")
    warmup_start = start_date - timedelta(days=_WARMUP_DAYS)
    interval = str(tool_input.get("interval") or env_text(env, "CRYPTO_MARKET_INTERVAL") or _DEFAULT_INTERVAL).strip()
    if interval not in _SUPPORTED_INTERVALS:
        raise ValueError(f"tool_input.interval must be one of {sorted(_SUPPORTED_INTERVALS)}")
    return {
        **base,
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "warmup_start_date": warmup_start.isoformat(),
        "interval": interval,
        "provider_symbol": _provider_symbol(base["ticker"], env=env),
        "quote_asset": env_text(env, _QUOTE_ASSET_ENV) or "USDT",
        "current_date": context.get("current_date"),
    }


def _load_binance_klines(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson,
    provider_cache_collection: Any | None,
    provider_attempts_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> tuple[pd.DataFrame, list[dict[str, Any]], Any]:
    base_url = (env_text(env, _BINANCE_BASE_URL_ENV) or "https://api.binance.com").rstrip("/")
    endpoint = "/api/v3/klines"
    params = {
        "symbol": request["provider_symbol"],
        "interval": request["interval"],
        "startTime": _date_to_millis(str(request["warmup_start_date"])),
        "endTime": _date_to_millis(str(request["end_date"]), end_of_day=True),
        "limit": 1000,
    }
    ttl_seconds = ttl_seconds_for_provider(
        provider="Binance",
        endpoint=endpoint,
        domain="market",
        request=request,
        env=env,
    )
    fetch_result = fetch_json_with_crypto_provider_cache(
        context=context,
        request=request,
        domain="market",
        provider="Binance",
        endpoint=endpoint,
        role="chart_ohlcv_supplement",
        source_role="chart_ohlcv_supplement_not_coinglass_replacement",
        url=f"{base_url}{endpoint}",
        params=params,
        headers={},
        timeout=DEFAULT_TIMEOUT_SECONDS,
        auth_mode="public",
        fetch_json=fetch_json,
        env=env,
        ttl_seconds=ttl_seconds,
        cache_collection=provider_cache_collection,
        attempts_collection=None,
        rate_limit_collection=provider_rate_limit_collection,
    )
    payload = fetch_result.payload
    attempts = fetch_result.attempts
    rows = _normalize_binance_rows(payload)
    requested = rows[(rows["date"] >= str(request["start_date"])) & (rows["date"] <= str(request["end_date"]))].copy()
    if attempts:
        finish_attempt(attempts[-1], payload, accepted_count=len(requested))
        commit_crypto_provider_cache(
            attempt=attempts[-1],
            payload=payload,
            collection=provider_cache_collection,
        )
    return rows, attempts, payload


def _load_bb_trade_context(
    request: Mapping[str, Any],
    *,
    context: Mapping[str, str],
    env: Mapping[str, str],
    fetch_json: FetchJson | None,
    provider_cache_collection: Any | None,
    provider_attempts_collection: Any | None,
    provider_rate_limit_collection: Any | None,
) -> tuple[dict[str, Any], list[dict[str, Any]], Any | None]:
    bb_input = _bb_trade_context_input(request, env=env)
    bb_cwd = _bb_mcp_cwd(env)
    if fetch_json is None and not (bb_cwd / "dist" / "tools" / "tradeContext.js").is_file():
        attempt = blocked_attempt(
            provider="BB",
            endpoint=_BB_CONTEXT_ENDPOINT,
            role="market_structure_context",
            auth_mode="local_mcp_cli",
            error_code="BB_MCP_DIST_MISSING",
            error_message=f"BB MCP build output not found under {bb_cwd}",
        )
        return _empty_bb_summary("BB MCP 本地 build 输出不可用，无法取得 BB/CoinGlass 市场结构资料。"), [attempt], None

    def _fetch(_url: str, *, params: Mapping[str, Any], headers: Mapping[str, str], timeout: int, method: str = "POST", json_body: Mapping[str, Any] | None = None) -> Any:
        _ = params, headers, method
        if fetch_json is not None:
            return fetch_json(_url, params={}, headers={}, timeout=timeout, method="POST", json_body=json_body)
        return _run_bb_trade_context(json_body or bb_input, env=env, timeout=timeout)

    fetch_result = fetch_json_with_crypto_provider_cache(
        context=context,
        request=request,
        domain="market",
        provider="BB",
        endpoint=_BB_CONTEXT_ENDPOINT,
        role="market_structure_context",
        source_role="bb_market_structure_compact_source",
        url=_BB_CONTEXT_URL,
        params={},
        headers={},
        timeout=_bb_timeout_seconds(env),
        auth_mode="local_mcp_cli",
        fetch_json=_fetch,
        env=env,
        method="POST",
        json_body=bb_input,
        ttl_seconds=ttl_seconds_for_provider(
            provider="BB",
            endpoint=_BB_CONTEXT_ENDPOINT,
            domain="market",
            request=request,
            env=env,
        ),
        cache_collection=provider_cache_collection,
        attempts_collection=provider_attempts_collection,
        rate_limit_collection=provider_rate_limit_collection,
    )
    payload = fetch_result.payload
    summary = _compact_bb_trade_context(payload)
    if fetch_result.attempts:
        finish_attempt(fetch_result.attempts[-1], payload, accepted_count=1 if summary["available"] else 0, schema_valid=isinstance(payload, Mapping))
        commit_crypto_provider_cache(
            attempt=fetch_result.attempts[-1],
            payload=payload,
            collection=provider_cache_collection,
        )
    return summary, fetch_result.attempts, payload


def _bb_trade_context_input(request: Mapping[str, Any], *, env: Mapping[str, str]) -> dict[str, Any]:
    return {
        "asset": request["ticker"],
        "direction": env_text(env, "CRYPTO_MARKET_BB_DIRECTION") or "neutral",
        "timeframe": env_text(env, "CRYPTO_MARKET_BB_TIMEFRAME") or "swing",
        "include": list(_BB_CONTEXT_INCLUDE),
        "required": ["market", "technical"],
        "missing_policy": "warn",
        "profile": env_text(env, "CRYPTO_MARKET_BB_PROFILE") or env_text(env, "BB_CRYPTO_PROFILE") or "live",
    }


def _run_bb_trade_context(bb_input: Mapping[str, Any], *, env: Mapping[str, str], timeout: int) -> Any:
    bb_cwd = _bb_mcp_cwd(env)
    script = """
import { buildTradeContext } from "./dist/tools/tradeContext.js";
const chunks = [];
for await (const chunk of process.stdin) chunks.push(chunk);
const payload = JSON.parse(Buffer.concat(chunks).toString("utf8"));
const result = await buildTradeContext(payload.input, payload.profile);
process.stdout.write(JSON.stringify(result));
"""
    profile = str(bb_input.get("profile") or "live")
    completed = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=str(bb_cwd),
        env={**os.environ, **dict(env), "NODE_NO_WARNINGS": "1"},
        input=json.dumps({"input": dict(bb_input), "profile": profile}, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=max(1, int(timeout)),
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(_redact(completed.stderr or completed.stdout or f"BB MCP exited {completed.returncode}"))
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("BB MCP returned non-JSON output") from exc


def _bb_mcp_cwd(env: Mapping[str, str]) -> Path:
    explicit = env_text(env, "BB_MCP_CWD")
    if explicit:
        return Path(explicit)
    server_path = env_text(env, "BB_MCP_SERVER_PATH")
    if server_path:
        path = Path(server_path)
        if path.name == "server.js" and path.parent.name == "dist":
            return path.parent.parent
        return path.parent
    return _BB_DEFAULT_CWD


def _bb_timeout_seconds(env: Mapping[str, str]) -> int:
    explicit_seconds = env_text(env, "CRYPTO_MARKET_BB_TIMEOUT_SECONDS")
    if explicit_seconds and explicit_seconds.isdigit():
        return max(1, int(explicit_seconds))
    timeout_ms = env_text(env, "BB_PROVIDER_TIMEOUT_MS")
    if timeout_ms and timeout_ms.isdigit():
        return max(60, int(int(timeout_ms) / 1000))
    return 60


def _compact_bb_trade_context(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return _empty_bb_summary("BB MCP 未返回可解析的 trade context JSON。")
    data = payload.get("data") if isinstance(payload.get("data"), Mapping) else {}
    readiness = payload.get("readiness") if isinstance(payload.get("readiness"), Mapping) else {}
    data_gaps = payload.get("data_gaps") if isinstance(payload.get("data_gaps"), list) else []
    conflicts = payload.get("conflicts") if isinstance(payload.get("conflicts"), list) else []
    status = str(payload.get("status") or "unknown")
    summary = {
        "available": bool(data),
        "status": status,
        "summary": _str_or_none(payload.get("summary")),
        "as_of": _str_or_none(payload.get("as_of")),
        "confidence": _str_or_none(payload.get("confidence")),
        "readiness": _compact_readiness(readiness),
        "sources_count": len(payload.get("sources") or []) if isinstance(payload.get("sources"), list) else 0,
        "data_gaps": [_compact_gap(gap) for gap in data_gaps[:10]],
        "conflicts": [_compact_gap(conflict) for conflict in conflicts[:6]],
        "market": _compact_bb_market(data.get("market")),
        "technical": _compact_bb_technical(data.get("technical")),
        "derivatives": _compact_bb_derivatives(data.get("derivatives")),
        "liquidation_map": _compact_bb_liquidation_map(data.get("liquidation_map")),
        "onchain": _compact_bb_onchain(data.get("onchain")),
        "macro": _compact_bb_macro(data.get("macro")),
        "ahr999": _compact_bb_ahr999(data.get("ahr999")),
    }
    if status not in {"success", "warning"}:
        summary["available"] = False
    return summary


def _empty_bb_summary(reason: str) -> dict[str, Any]:
    return {
        "available": False,
        "status": "error",
        "summary": reason,
        "as_of": None,
        "confidence": "low",
        "readiness": {"overall_level": "not_reliable", "overall_score": None, "domains": {}},
        "sources_count": 0,
        "data_gaps": [reason],
        "conflicts": [],
        "market": {},
        "technical": {},
        "derivatives": {},
        "liquidation_map": {},
        "onchain": {},
        "macro": {},
        "ahr999": {},
    }


def _compact_readiness(readiness: Mapping[str, Any]) -> dict[str, Any]:
    domains = readiness.get("domains") if isinstance(readiness.get("domains"), Mapping) else {}
    return {
        "overall_score": readiness.get("overall_score"),
        "overall_level": readiness.get("overall_level"),
        "domains": {str(key): value for key, value in domains.items()},
    }


def _compact_bb_market(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, Mapping) else {}
    return {
        "price": _first_path(data, ("price", "current_price", "current_price_usd")),
        "change_24h_pct": _first_path(data, ("change_24h_pct", "price_change_percentage_24h", "price_change_24h_pct")),
        "volume_24h": _first_path(data, ("volume_24h", "total_volume", "total_volume_usd")),
        "market_cap": _first_path(data, ("market_cap", "market_cap_usd")),
    }


def _compact_bb_technical(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, Mapping) else {}
    return {
        "timeframes": [_compact_timeframe(label, row) for label, row in _timeframe_items(data)[:4]],
        "tutorial_signal_summary": _compact_mapping(data.get("tutorial_signal_summary") or {}, max_items=10)
        if isinstance(data.get("tutorial_signal_summary"), Mapping)
        else {},
    }


def _compact_timeframe(label: str, row: Any) -> dict[str, Any]:
    frame = row if isinstance(row, Mapping) else {}
    indicators = frame.get("indicators") if isinstance(frame.get("indicators"), Mapping) else {}
    patterns = frame.get("patterns") if isinstance(frame.get("patterns"), Mapping) else {}
    return {
        "timeframe": label,
        "last_close": _first_path(frame, ("last_close", "close")),
        "rsi14": _first_path(indicators, ("rsi14", "rsi")),
        "macd_histogram": _first_path(indicators, ("macd_12_26_9.histogram", "macd.histogram", "macd.hist")),
        "bollinger": _first_mapping(indicators, ("bollinger_bands_20_2", "bollinger")),
        "kd": _first_mapping(indicators, ("kd_9_3_3", "kd", "kdj")),
        "td_sequential": _first_mapping(indicators, ("td_sequential",)),
        "vegas": _first_mapping(frame, ("vegas",)),
        "fvg": _first_mapping(patterns, ("fvg",)),
        "order_block": _first_mapping(patterns, ("order_block", "ob")),
        "volume_profile": _first_mapping(patterns, ("volume_profile",)),
        "amd": _first_mapping(patterns, ("amd", "smc")),
        "rule_123": _first_mapping(patterns, ("rule_123", "123")),
        "harmonic": _first_mapping(patterns, ("harmonic",)),
    }


def _timeframe_items(data: Mapping[str, Any]) -> list[tuple[str, Any]]:
    timeframes = data.get("timeframes")
    if isinstance(timeframes, Mapping):
        return [(str(key), value) for key, value in timeframes.items()]
    if isinstance(timeframes, list):
        items: list[tuple[str, Any]] = []
        for index, row in enumerate(timeframes):
            label = str(row.get("timeframe") or row.get("interval") or index) if isinstance(row, Mapping) else str(index)
            items.append((label, row))
        return items
    return []


def _compact_bb_derivatives(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, Mapping) else {}
    return {
        "funding_rate": _first_deep_value(data, ("funding_rate", "fundingRate")),
        "open_interest": _first_deep_value(data, ("sum_open_interest_value", "open_interest_value", "open_interest", "openInterest")),
        "long_short_ratio": _first_deep_value(data, ("long_short_ratio", "longShortRatio", "account_ratio", "long_short_account_ratio")),
        "cvd_proxy": _first_deep_mapping(data, ("cvd_proxy",)),
        "liquidations": _first_deep_mapping(data, ("liquidations",)),
        "cross_exchange": _first_deep_mapping(data, ("cross_exchange", "cross_exchange_check")),
    }


def _compact_bb_liquidation_map(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, Mapping) else {}
    above = _mapping_list(data, ("above_price_liquidity", "above", "nearest_above"))
    below = _mapping_list(data, ("below_price_liquidity", "below", "nearest_below"))
    clusters = _mapping_list(data, ("largest_clusters", "clusters", "top_clusters"))
    if not clusters:
        clusters = above[:3] + below[:3]
    if not clusters:
        clusters = _first_deep_list(data, ("clusters", "top_clusters", "liquidation_leverage_data"))
    compact_clusters = [_compact_cluster(item) for item in clusters[:5]]
    compact_above = [_compact_cluster(item) for item in above[:3]]
    compact_below = [_compact_cluster(item) for item in below[:3]]
    return {
        "clusters": [item for item in compact_clusters if item],
        "nearest_above": next((item for item in compact_above if item), _first_deep_mapping(data, ("nearest_above", "above"))),
        "nearest_below": next((item for item in compact_below if item), _first_deep_mapping(data, ("nearest_below", "below"))),
        "above_price_liquidity": [item for item in compact_above if item],
        "below_price_liquidity": [item for item in compact_below if item],
        "current_price": _first_path(data, ("current_price",)),
        "heatmap_model": _str_or_none(data.get("heatmap_model")),
    }


def _compact_cluster(item: Any) -> dict[str, Any]:
    if not isinstance(item, Mapping):
        return {}
    return {
        "price": _first_path(item, ("price", "level", "liquidation_price")),
        "value": _first_path(item, ("liquidation_value", "value", "amount", "notional")),
        "side": _str_or_none(item.get("side") or item.get("direction")),
    }


def _compact_bb_onchain(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, Mapping) else {}
    return {
        "mvrv": _first_deep_value(data, ("mvrv",)),
        "exchange_flow": _first_deep_mapping(data, ("exchange_flows", "exchange_net_position", "exchange_flow")),
        "summary": _first_deep_list(data, ("summary", "signals")),
    }


def _compact_bb_macro(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, Mapping) else {}
    return {
        "risk_window": _first_deep_value(data, ("risk_window", "risk")),
        "us_10y": _first_deep_value(data, ("DGS10", "us10y", "ten_year", "10y")),
        "series": _first_deep_mapping(data, ("series", "latest")),
    }


def _compact_bb_ahr999(value: Any) -> dict[str, Any]:
    data = value if isinstance(value, Mapping) else {}
    return {
        "value": _first_deep_value(data, ("ahr999", "ahr999_index", "value")),
        "dca_zone": _first_deep_value(data, ("dca_zone", "is_dca_zone")),
        "risk_zone": _first_deep_value(data, ("risk_zone", "is_risk_zone")),
    }


def _bb_data_gap_messages(summary: Mapping[str, Any]) -> list[str]:
    gaps: list[str] = []
    if not summary.get("available"):
        gaps.append(str(summary.get("summary") or "BB/CoinGlass 市场结构资料不可用。"))
    for item in summary.get("data_gaps") or []:
        if isinstance(item, str):
            gaps.append(item)
        elif isinstance(item, Mapping):
            text = " / ".join(str(part) for part in (item.get("domain"), item.get("reason"), item.get("impact")) if part)
            if text:
                gaps.append(text)
    return gaps[:10]


def _normalize_binance_rows(payload: Any) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    if not isinstance(payload, list):
        return _empty_frame()
    for item in payload:
        if not isinstance(item, list) or len(item) < 6:
            continue
        opened_at = number_or_none(item[0])
        if opened_at is None:
            continue
        rows.append(
            {
                "date": datetime.fromtimestamp(opened_at / 1000, tz=timezone.utc).date().isoformat(),
                "open": number_or_none(item[1]),
                "high": number_or_none(item[2]),
                "low": number_or_none(item[3]),
                "close": number_or_none(item[4]),
                "volume": number_or_none(item[5]),
            }
        )
    frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    if frame.empty:
        return _empty_frame()
    for column in ("open", "high", "low", "close", "volume"):
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close", "volume"]).sort_values("date").reset_index(drop=True)
    return frame if not frame.empty else _empty_frame()


def _indicator_frame(rows: pd.DataFrame) -> pd.DataFrame:
    frame = rows.copy()
    close = frame["close"]
    high = frame["high"]
    low = frame["low"]
    frame["ma5"] = close.rolling(5).mean()
    frame["ma10"] = close.rolling(10).mean()
    frame["ma20"] = close.rolling(20).mean()
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    frame["macd"] = ema12 - ema26
    frame["signal"] = frame["macd"].ewm(span=9, adjust=False).mean()
    frame["hist"] = frame["macd"] - frame["signal"]
    frame["rsi14"] = _rsi(close, length=14)
    frame["boll_mid"] = frame["ma20"]
    boll_std = close.rolling(20).std()
    frame["boll_upper"] = frame["boll_mid"] + 2 * boll_std
    frame["boll_lower"] = frame["boll_mid"] - 2 * boll_std
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    frame["atr14"] = true_range.rolling(14).mean()
    low_min = low.rolling(9).min()
    high_max = high.rolling(9).max()
    raw_k = ((close - low_min) / (high_max - low_min)) * 100
    frame["k"] = raw_k.ewm(alpha=1 / 3, adjust=False).mean()
    frame["d"] = frame["k"].ewm(alpha=1 / 3, adjust=False).mean()
    frame["j"] = 3 * frame["k"] - 2 * frame["d"]
    return frame


def _rsi(close: pd.Series, *, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(length).mean()
    loss = (-delta.clip(upper=0)).rolling(length).mean()
    rs = gain / loss.mask(loss == 0)
    return 100 - (100 / (1 + rs))


def _latest_indicators(frame: pd.DataFrame) -> dict[str, Any]:
    latest = frame.iloc[-1]
    return {
        "moving_average": {
            "ma5": _float(latest.get("ma5")),
            "ma10": _float(latest.get("ma10")),
            "ma20": _float(latest.get("ma20")),
        },
        "macd": {
            "macd": _float(latest.get("macd")),
            "signal": _float(latest.get("signal")),
            "hist": _float(latest.get("hist")),
        },
        "rsi14": _float(latest.get("rsi14")),
        "bollinger": {
            "upper": _float(latest.get("boll_upper")),
            "mid": _float(latest.get("boll_mid")),
            "lower": _float(latest.get("boll_lower")),
        },
        "atr14": _float(latest.get("atr14")),
        "kdj": {
            "k": _float(latest.get("k")),
            "d": _float(latest.get("d")),
            "j": _float(latest.get("j")),
        },
    }


def _technical_summary(frame: pd.DataFrame) -> dict[str, Any]:
    latest = frame.iloc[-1]
    first = frame.iloc[0]
    close = float(latest["close"])
    ma20 = _float(latest.get("ma20"))
    trend = "above_ma20" if ma20 is not None and close >= ma20 else ("below_ma20" if ma20 is not None else "ma20_unavailable")
    change = ((close - float(first["close"])) / float(first["close"])) * 100 if float(first["close"]) else None
    return {
        "trend_state": trend,
        "window_change_pct": _float(change),
        "latest_close": _float(close),
        "latest_volume": _float(latest.get("volume")),
        "support_proxy": _float(frame["low"].tail(20).min()),
        "resistance_proxy": _float(frame["high"].tail(20).max()),
    }


def _render_charts(
    *,
    chart_frame: pd.DataFrame,
    ticker: str,
    evidence_root: Path,
    chart_render: ChartRender | None,
) -> tuple[list[str], str | None]:
    output_dir = evidence_root / "techlab" / "charts-local"
    render = chart_render or _default_chart_render()
    try:
        paths = [Path(path) for path in render(chart_frame, ticker, output_dir)]
    except Exception as exc:  # noqa: BLE001
        return [], f"技术图表生成失败：{_redact(str(exc))}"
    existing = [str(path) for path in paths if path.exists() and path.is_file()]
    if not existing:
        return [], "技术图表生成未产出可复制的 PNG/JPG/WebP 文件。"
    return existing, None


def _default_chart_render() -> ChartRender:
    spec = importlib.util.spec_from_file_location("claw_trade_crypto_chart_engine", _CHART_ENGINE)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"chart_engine not loadable: {_CHART_ENGINE}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    render_market_charts = getattr(module, "render_market_charts", None)
    if not callable(render_market_charts):
        raise RuntimeError("chart_engine.render_market_charts is not callable")

    def _render(chart_frame: pd.DataFrame, ticker: str, output_dir: Path) -> Sequence[Path | str]:
        return render_market_charts(chart_frame, ticker=ticker, output_dir=output_dir)

    return _render


def _pack(
    *,
    request: Mapping[str, Any],
    attempts: list[dict[str, Any]],
    data_gaps: list[str],
    rows: pd.DataFrame,
    indicators: Mapping[str, Any],
    technical_summary: Mapping[str, Any],
    chart_paths: list[str],
    bb_summary: Mapping[str, Any],
) -> dict[str, Any]:
    readiness = _readiness(rows=rows, chart_paths=chart_paths, data_gaps=data_gaps, bb_summary=bb_summary)
    latest = _row_payload(rows.iloc[-1]) if not rows.empty else None
    previous_close = _previous_close(rows)
    requested_rows = _requested_rows(rows, request)
    visible_rows = requested_rows if not requested_rows.empty else rows
    pack = {
        "ok": readiness["status"] != "insufficient",
        "schema_version": _SCHEMA_VERSION,
        "tool_name": _TOOL_NAME,
        "domain": "market",
        "asset": request["ticker"],
        "market": _MARKET,
        "as_of": utc_now(),
        "input": dict(request),
        "data": {
            "source_role": "bb_market_structure_plus_chart_ohlcv_supplement",
            "bb_trade_context": dict(bb_summary),
            "latest_price": latest,
            "previous_close": previous_close,
            "recent_price_rows": [_row_payload(row) for _, row in visible_rows.tail(30).iterrows()],
            "indicators": dict(indicators),
            "technical_summary": dict(technical_summary),
            "chart_files": chart_paths,
        },
        "sources": [
            {
                "provider": "BB",
                "endpoint": _BB_CONTEXT_ENDPOINT,
                "role": "market_structure_context",
                "source_role": "bb_market_structure_compact_source",
                "sources_count": bb_summary.get("sources_count"),
            },
            {
                "provider": "Binance",
                "endpoint": "/api/v3/klines",
                "role": "chart_ohlcv_supplement",
                "symbol": request["provider_symbol"],
            }
        ],
        "provider_attempts": attempts,
        "data_gaps": data_gaps,
        "conflicts": [],
        "readiness": readiness,
        "quality": {
            "status": "complete" if readiness["status"] == "ready" else ("failed" if readiness["status"] == "insufficient" else "partial"),
            "warnings": data_gaps[:8],
        },
    }
    pack["reader_brief"] = _reader_brief(pack)
    return pack


def _readiness(
    *,
    rows: pd.DataFrame,
    chart_paths: list[str],
    data_gaps: list[str],
    bb_summary: Mapping[str, Any],
) -> dict[str, str]:
    bb_available = bool(bb_summary.get("available"))
    if rows.empty and not bb_available:
        return {"status": "insufficient", "reason": "BB/CoinGlass 市场结构和本地 OHLCV 都不可用，无法形成市场资料包。"}
    if rows.empty:
        return {"status": "partial", "reason": "BB/CoinGlass 市场结构可用，但本地 OHLCV 与技术图表缺失。"}
    if not bb_available:
        return {"status": "partial", "reason": "本地 OHLCV 与图表可用，但 BB/CoinGlass 衍生品、清算、链上和宏观结构缺失。"}
    if chart_paths:
        return {"status": "ready", "reason": "BB/CoinGlass 市场结构、公开交易所 OHLCV 和本地技术图表均已返回；剩余资料缺口作为置信度限制记录。"}
    return {"status": "partial", "reason": "BB/CoinGlass 市场结构和 OHLCV 已返回，但技术图表文件未生成或不可复制。"}


def _reader_brief(pack: Mapping[str, Any]) -> str:
    data = pack["data"]
    latest = data.get("latest_price") if isinstance(data, Mapping) else None
    latest_close = latest.get("close") if isinstance(latest, Mapping) else None
    bb_summary = data.get("bb_trade_context") if isinstance(data, Mapping) else {}
    bb_summary = bb_summary if isinstance(bb_summary, Mapping) else {}
    lines = [
        "## BB/CoinGlass 市场分析自然语言包",
        f"分析对象：{pack['asset']} / {pack.get('market') or 'CRYPTO'}。",
        f"资料包状态：{_status_zh(pack['readiness']['status'])}；{pack['readiness']['reason']}",
        "用途边界：以下内容是 BB/CoinGlass 与本地技术图表材料的自然语言转写，用于 market_analyst 生成自己的市场报告；它不是 trader 或 portfolio_manager 的最终投资裁决。",
        "资料边界：原始 BB 返回内容只保存为证据文件；market worker 只应阅读本自然语言材料和数据质量说明，不应把原始 JSON 当报告正文。",
    ]
    lines.extend(_bb_brief_lines(bb_summary))
    if latest_close is not None:
        lines.extend(
            [
                "",
                "## Binance OHLCV 与图表补充",
                f"Binance 最新收盘价为 {_brief_value(latest_close)}；本地生成图表文件数量为 {len(data.get('chart_files') or [])}。",
                "解释：Binance OHLCV 与本地图表只作为价格序列、技术图表和可复制图像资产补充；它不能替代 BB/CoinGlass 的衍生品、清算、链上、宏观和 AHR999 材料。",
            ]
        )
    if pack.get("data_gaps"):
        lines.extend(
            [
                "",
                "## 数据缺口与使用限制",
                *[f"- {_reader_gap(item)}" for item in pack["data_gaps"][:8]],
                "这些缺口会降低结论置信度。market_analyst 可以说明缺口影响，但不得补写缺失的清算、链上、事件或单位信息。",
            ]
        )
    return "\n".join(lines)


def _bb_brief_lines(summary: Mapping[str, Any]) -> list[str]:
    if not summary:
        return ["", "## BB/CoinGlass 详细市场材料", "BB/CoinGlass 材料不可用。"]
    readiness = summary.get("readiness") if isinstance(summary.get("readiness"), Mapping) else {}
    domains = readiness.get("domains") if isinstance(readiness.get("domains"), Mapping) else {}
    lines = [
        "",
        "## 数据状态",
        "BB/CoinGlass 摘要来源已转写为详细自然语言材料。",
        f"- 状态：{_status_zh(summary.get('status') or 'unknown')}；置信度：{_status_zh(summary.get('confidence') or 'unknown')}。",
        f"- 时间：{summary.get('as_of') or 'unknown'}。",
        f"- 资料就绪度：整体为 {_status_zh(readiness.get('overall_level') or 'unknown')}；评分为 {_brief_value(readiness.get('overall_score'))}；来源数量为 {summary.get('sources_count') or 0}。",
    ]
    if domains:
        lines.append("- 分域资料覆盖：" + "；".join(f"{_domain_label(key)}：{_status_zh(value)}" for key, value in domains.items()) + "。")
    market = summary.get("market") if isinstance(summary.get("market"), Mapping) else {}
    if market:
        lines.extend(_market_snapshot_lines(market))
    technical = summary.get("technical") if isinstance(summary.get("technical"), Mapping) else {}
    frames = technical.get("timeframes") if isinstance(technical.get("timeframes"), list) else []
    if frames:
        lines.extend(["", "## 多周期技术结构"])
    for frame in frames[:3]:
        if isinstance(frame, Mapping):
            lines.extend(_technical_frame_lines(frame))
    if frames:
        lines.extend(_indicator_coverage_lines(frames))
    derivatives = summary.get("derivatives") if isinstance(summary.get("derivatives"), Mapping) else {}
    liquidation_map = summary.get("liquidation_map") if isinstance(summary.get("liquidation_map"), Mapping) else {}
    if derivatives or liquidation_map:
        lines.extend(_derivatives_lines(derivatives, liquidation_map))
    onchain = summary.get("onchain") if isinstance(summary.get("onchain"), Mapping) else {}
    macro = summary.get("macro") if isinstance(summary.get("macro"), Mapping) else {}
    ahr999 = summary.get("ahr999") if isinstance(summary.get("ahr999"), Mapping) else {}
    if onchain or macro or ahr999:
        lines.extend(_risk_context_lines(onchain, macro, ahr999))
    if frames or derivatives or liquidation_map:
        lines.extend(_operation_framework_lines(summary, frames, derivatives, liquidation_map))
    conflicts = summary.get("conflicts") if isinstance(summary.get("conflicts"), list) else []
    if conflicts:
        lines.extend(["", "## 冲突", *[f"- {item}" for item in conflicts[:4]]])
    gaps = summary.get("data_gaps") if isinstance(summary.get("data_gaps"), list) else []
    if gaps:
        lines.extend(["", "## BB/CoinGlass 资料缺口", *[f"- {_reader_gap(item)}" for item in gaps[:8]]])
    return lines


def _market_snapshot_lines(market: Mapping[str, Any]) -> list[str]:
    change = number_or_none(market.get("change_24h_pct"))
    if change is None:
        change_text = "24h 涨跌幅缺失，不能判断短线涨跌强弱。"
    elif change > 0:
        change_text = f"24h 涨跌幅为 {_brief_value(change)}，短线价格相对前一日偏强。"
    elif change < 0:
        change_text = f"24h 涨跌幅为 {_brief_value(change)}，短线价格相对前一日偏弱。"
    else:
        change_text = "24h 涨跌幅接近 0，短线价格变化不明显。"
    return [
        "",
        "## 市场快照",
        f"- 当前价格：{_brief_value(market.get('price'))}。",
        f"- 24h 涨跌幅：{_brief_value(market.get('change_24h_pct'))}。",
        f"- 24h 成交量：{_brief_value(market.get('volume_24h'))}。",
        f"- 市值：{_brief_value(market.get('market_cap'))}。",
        f"- 解读：{change_text}成交量与市值只说明市场活跃度和规模，不能单独构成入场或离场依据。",
    ]


def _status_zh(value: Any) -> str:
    text = str(value or "unknown").strip()
    lowered = text.lower()
    labels = {
        "ready": "就绪",
        "partial": "部分覆盖",
        "insufficient": "不足",
        "not_reliable": "不可靠",
        "success": "成功",
        "failed": "失败",
        "empty": "空返回",
        "high": "高",
        "medium": "中",
        "low": "低",
        "unknown": "未知",
        "bullish": "偏多",
        "bearish": "偏空",
        "neutral": "中性",
        "buy": "买入侧",
        "sell": "卖出侧",
        "sell_pressure": "卖压",
        "buy_pressure": "买压",
        "above": "上方",
        "below": "下方",
        "below_blue_band": "低于蓝带",
        "above_blue_band": "高于蓝带",
        "accumulation": "吸筹",
        "manipulation": "扫流动性",
        "distribution": "派发",
    }
    return labels.get(lowered, text.replace("_", " "))


def _domain_label(value: Any) -> str:
    labels = {
        "market": "市场",
        "technical": "技术",
        "derivatives": "衍生品",
        "liquidation_map": "清算地图",
        "onchain": "链上",
        "macro": "宏观",
        "ahr999": "AHR999",
        "events": "事件",
    }
    text = str(value or "unknown")
    return labels.get(text, text.replace("_", " "))


def _bool_zh(value: Any) -> str:
    if value is True:
        return "是"
    if value is False:
        return "否"
    return _brief_value(value)


def _range_value(value: Any) -> str:
    if isinstance(value, Mapping):
        lower = _mapping_number(value, ("lower", "price"))
        upper = _mapping_number(value, ("upper", "price"))
        if lower is not None and upper is not None and lower != upper:
            return f"{_brief_value(lower)}-{_brief_value(upper)}"
        scalar = _scalar_from_mapping(value)
        return _brief_value(scalar)
    return _brief_value(value)


def _field_values(value: Mapping[str, Any], labels: Sequence[tuple[str, str]]) -> str:
    parts: list[str] = []
    for key, label in labels:
        item = _first_path(value, (key,))
        if item is not None:
            parts.append(f"{label} {_brief_value(item)}")
    return "；".join(parts) if parts else "缺失"


def _bollinger_values(value: Mapping[str, Any]) -> str:
    return _field_values(value, (("lower", "下轨"), ("middle", "中轨"), ("mid", "中轨"), ("upper", "上轨")))


def _kd_values(value: Mapping[str, Any]) -> str:
    return _field_values(value, (("k", "K 值"), ("K", "K 值"), ("d", "D 值"), ("D", "D 值")))


def _vegas_values(value: Mapping[str, Any]) -> str:
    if not value:
        return "缺失"
    parts: list[str] = []
    state = value.get("state") or value.get("major") or value.get("trend")
    if state is not None:
        parts.append(f"状态 {_status_zh(state)}")
    for key, label in (("ema144", "EMA144"), ("ema169", "EMA169"), ("blue_band", "蓝带"), ("yellow_band", "黄带")):
        item = value.get(key)
        if item is not None:
            parts.append(f"{label} {_range_value(item)}")
    return "；".join(parts) if parts else _known_mapping_text(value)


def _fvg_values(value: Mapping[str, Any]) -> str:
    if not value:
        return "缺失"
    parts: list[str] = []
    for key, label in (
        ("support", "下方支撑缺口"),
        ("resistance", "上方压力缺口"),
        ("nearest_above", "上方最近缺口"),
        ("nearest_below", "下方最近缺口"),
        ("bullish", "偏多缺口"),
        ("bearish", "偏空缺口"),
    ):
        item = value.get(key)
        if item is not None:
            parts.append(f"{label} {_range_value(item)}")
    return "；".join(parts) if parts else _known_mapping_text(value)


def _order_block_values(value: Mapping[str, Any]) -> str:
    if not value:
        return "缺失"
    parts: list[str] = []
    for key, label in (("bearish", "空头订单块"), ("bullish", "多头订单块"), ("supply", "供应区"), ("demand", "需求区")):
        item = value.get(key)
        if item is not None:
            parts.append(f"{label} {_range_value(item)}")
    return "；".join(parts) if parts else _known_mapping_text(value)


def _volume_profile_values(value: Mapping[str, Any]) -> str:
    if not value:
        return "缺失"
    parts: list[str] = []
    poc = _mapping_number(value, ("poc.price", "poc"))
    if poc is not None:
        parts.append(f"POC {_brief_value(poc)}")
    value_area = value.get("value_area")
    if value_area is not None:
        parts.append(f"价值区 {_range_value(value_area)}")
    value_area_low = _mapping_number(value, ("value_area_low",))
    if value_area_low is not None:
        parts.append(f"价值区低点 {_brief_value(value_area_low)}")
    volume_pct = _mapping_number(value, ("volume_pct",))
    if volume_pct is not None:
        parts.append(f"覆盖成交量占比 {_brief_value(volume_pct)}")
    return "；".join(parts) if parts else _known_mapping_text(value)


def _amd_values(value: Mapping[str, Any]) -> str:
    if not value:
        return "缺失"
    parts: list[str] = []
    for key, label in (("phase", "阶段"), ("direction", "方向"), ("confidence", "置信度"), ("range", "区间")):
        item = value.get(key)
        if item is not None:
            parts.append(f"{label} {_status_zh(item) if key in {'phase', 'direction', 'confidence'} else _range_value(item)}")
    return "；".join(parts) if parts else _known_mapping_text(value)


def _rule_123_values(value: Mapping[str, Any]) -> str:
    if not value:
        return "缺失"
    parts: list[str] = []
    for key, label in (
        ("trigger", "触发位"),
        ("invalid", "失效位"),
        ("point_1", "1 号点"),
        ("point_2", "2 号点"),
        ("point_3", "3 号点"),
        ("step_1", "第一步"),
        ("step_2", "第二步"),
        ("forming_step_1", "第一步形成状态"),
    ):
        item = value.get(key)
        if item is not None:
            parts.append(f"{label} {_status_zh(item) if isinstance(item, str) else _range_value(item)}")
    return "；".join(parts) if parts else _known_mapping_text(value)


def _td_values(value: Mapping[str, Any]) -> str:
    if not value:
        return "缺失"
    parts: list[str] = []
    direction = value.get("direction") or value.get("signal")
    if direction is not None:
        parts.append(f"方向 {_status_zh(direction)}")
    for key, label in (("setup", "设置计数"), ("countdown", "倒计数"), ("confidence", "置信度")):
        item = value.get(key)
        if item is not None:
            parts.append(f"{label} {_status_zh(item) if key == 'confidence' else _brief_value(item)}")
    return "；".join(parts) if parts else _known_mapping_text(value)


def _harmonic_values(value: Mapping[str, Any]) -> str:
    if not value:
        return "缺失"
    candidates = value.get("candidates")
    if isinstance(candidates, list):
        return "候选为空" if not candidates else f"候选数量 {len(candidates)}"
    return _known_mapping_text(value)


def _cvd_values(value: Mapping[str, Any]) -> str:
    if not value:
        return "缺失"
    parts: list[str] = []
    if value.get("value") is not None:
        parts.append(f"累计差值 {_brief_value(value.get('value'))}")
    if value.get("cumulative_delta") is not None:
        parts.append(f"累计差值 {_brief_value(value.get('cumulative_delta'))}")
    if value.get("latest_delta") is not None:
        parts.append(f"最近一段差值 {_brief_value(value.get('latest_delta'))}")
    if value.get("bias") is not None:
        parts.append(f"方向 {_status_zh(value.get('bias'))}")
    return "；".join(parts) if parts else _known_mapping_text(value)


def _exchange_flow_values(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "缺失"
    parts: list[str] = []
    for key, label in (("net_btc", "BTC 净流量"), ("net_usd", "美元净流量"), ("netflow", "净流量"), ("confidence", "置信度")):
        item = value.get(key)
        if item is not None:
            parts.append(f"{label} {_status_zh(item) if key == 'confidence' else _brief_value(item)}")
    return "；".join(parts) if parts else _known_mapping_text(value)


def _cluster_text(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "缺失"
    parts: list[str] = []
    price = _cluster_price(value)
    if price is not None:
        parts.append(f"价格 {_brief_value(price)}")
    amount = _mapping_number(value, ("value", "liquidation_value", "amount", "notional"))
    if amount is not None:
        parts.append(f"规模 {_brief_value(amount)}")
    side = value.get("side")
    if side is not None:
        parts.append(f"位置 {_status_zh(side)}")
    return "；".join(parts) if parts else _known_mapping_text(value)


def _indicator_value_text(key: str, value: Mapping[str, Any]) -> str:
    if key == "bollinger":
        return _bollinger_values(value)
    if key == "vegas":
        return _vegas_values(value)
    if key == "fvg":
        return _fvg_values(value)
    if key == "order_block":
        return _order_block_values(value)
    if key == "volume_profile":
        return _volume_profile_values(value)
    if key == "amd":
        return _amd_values(value)
    if key == "rule_123":
        return _rule_123_values(value)
    if key == "td_sequential":
        return _td_values(value)
    if key == "harmonic":
        return _harmonic_values(value)
    if key == "kd":
        return _kd_values(value)
    return _known_mapping_text(value)


def _known_mapping_text(value: Mapping[str, Any]) -> str:
    if not value:
        return "缺失"
    labels = {
        "price": "价格",
        "value": "数值",
        "amount": "规模",
        "notional": "名义规模",
        "side": "位置",
        "state": "状态",
        "major": "大级别状态",
        "trend": "趋势",
        "direction": "方向",
        "confidence": "置信度",
        "range": "区间",
        "support": "支撑",
        "resistance": "压力",
        "trigger": "触发位",
        "invalid": "失效位",
        "current": "当前值",
        "latest": "最新值",
        "close": "收盘价",
    }
    parts: list[str] = []
    for key, item in list(value.items())[:5]:
        label = labels.get(str(key), "补充项")
        if isinstance(item, Mapping):
            item_text = _known_mapping_text(item)
        elif isinstance(item, list):
            item_text = " / ".join(_brief_value(part) for part in item[:3] if not isinstance(part, Mapping)) or f"{len(item)} 项"
        elif isinstance(item, str):
            item_text = _status_zh(item)
        else:
            item_text = _brief_value(item)
        if item_text and item_text != "缺失":
            parts.append(f"{label} {item_text}")
    return "；".join(parts) if parts else "缺失"


def _reader_gap(value: Any) -> str:
    text = _compact_gap(value)
    replacements = {
        "data_gaps": "资料缺口",
        "data_gap": "资料缺口",
        "readiness": "资料就绪度",
        "provider_attempts": "数据源尝试记录",
        "provider payload": "原始返回内容",
        "stablecoin exchange netflow is a CEX proxy rather than wallet-labeled on-chain flow": "稳定币交易所净流量是 CEX 代理口径，不是钱包标签链上流",
        "governance/exchange announcement coverage is incomplete": "治理和交易所公告覆盖不完整",
        "4h AMD alignment": "4h AMD 多周期对齐",
        "insufficient": "不足",
        "partial": "部分覆盖",
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    return text


def _technical_frame_lines(frame: Mapping[str, Any]) -> list[str]:
    label = str(frame.get("timeframe") or "unknown")
    close = frame.get("last_close")
    bollinger = frame.get("bollinger") if isinstance(frame.get("bollinger"), Mapping) else {}
    td = frame.get("td_sequential") if isinstance(frame.get("td_sequential"), Mapping) else {}
    vegas = frame.get("vegas") if isinstance(frame.get("vegas"), Mapping) else {}
    fvg = frame.get("fvg") if isinstance(frame.get("fvg"), Mapping) else {}
    order_block = frame.get("order_block") if isinstance(frame.get("order_block"), Mapping) else {}
    volume_profile = frame.get("volume_profile") if isinstance(frame.get("volume_profile"), Mapping) else {}
    amd = frame.get("amd") if isinstance(frame.get("amd"), Mapping) else {}
    rule_123 = frame.get("rule_123") if isinstance(frame.get("rule_123"), Mapping) else {}
    harmonic = frame.get("harmonic") if isinstance(frame.get("harmonic"), Mapping) else {}
    kd = frame.get("kd") if isinstance(frame.get("kd"), Mapping) else {}
    rsi = frame.get("rsi14")
    macd_hist = frame.get("macd_histogram")
    return [
        "",
        f"### {label} 周期",
        f"- 数据：收盘价 {_brief_value(close)}；RSI14 为 {_brief_value(rsi)}；MACD 动能柱为 {_brief_value(macd_hist)}；KD 为 {_kd_values(kd)}。",
        f"- 布林带：{_bollinger_values(bollinger)}。{_bollinger_reading(close, bollinger)}",
        f"- Vegas：{_vegas_values(vegas)}。{_vegas_reading(vegas)}",
        f"- FVG：{_fvg_values(fvg)}。{_fvg_reading(fvg)}",
        f"- OB 订单块：{_order_block_values(order_block)}。{_order_block_reading(order_block)}",
        f"- 成交量分布 / POC：{_volume_profile_values(volume_profile)}。{_volume_profile_reading(close, volume_profile)}",
        f"- AMD/SMC：{_amd_values(amd)}。{_amd_reading(amd)}",
        f"- 123 结构：{_rule_123_values(rule_123)}。{_rule_123_reading(rule_123)}",
        f"- TD Sequential：{_td_values(td)}。{_td_reading(td)}",
        f"- 谐波形态：{_harmonic_values(harmonic)}。{_harmonic_reading(harmonic)}",
        f"- 动量解读：{_rsi_reading(rsi)}{_macd_reading(macd_hist)}{_kd_reading(kd)}",
        f"- 交易作用：{label} 周期材料用于识别当前价格相对趋势带、缺口、订单块、成交密集区和反转计数的位置；market_analyst 需要用这些证据判断是突破确认、支撑修复、压力反弹还是观望。",
        f"- 失效条件：如果价格有效跌破该周期下方 FVG / POC / 123 失效位，或无法收复上方 FVG / 布林中轨 / OB 压力，则该周期偏多修复读法应降级；反向同理，站回上方压力后空头读法降级。",
    ]


def _indicator_coverage_lines(frames: Sequence[Mapping[str, Any]]) -> list[str]:
    return [
        "",
        "## 指标逐项材料",
        _indicator_line("布林带", frames, "bollinger", "用于判断价格相对中轨、上轨、下轨的位置；接近下轨是修复条件，不等于直接反转。"),
        _indicator_line("维加斯通道", frames, "vegas", "用于识别价格相对长期均线带的位置；低于蓝带说明趋势压力仍在，高于蓝带说明趋势支撑更强。"),
        "- 双线反转：BB/CoinGlass 当前材料未提供明确双线反转字段；market_analyst 不得自行补写该信号，只能说明缺失。",
        _indicator_line("AMD/SMC", frames, "amd", "用于识别 accumulation / manipulation / distribution 区间；若多周期对齐不足，只能当区间观察，不能当确认入场。"),
        _indicator_line("123 突破", frames, "rule_123", "用于识别 swing 结构触发位与失效位；未触发时只能作为上方确认条件。"),
        _indicator_line("FVG", frames, "fvg", "用于识别上下方缺口支撑/阻力；价格夹在上下 FVG 中间时，优先等待突破或跌破确认。"),
        _indicator_line("OB 订单块", frames, "order_block", "用于识别潜在供应/需求区；上方 bearish OB 是压力，下方 bullish OB 是支撑候选。"),
        _indicator_scalar_line("RSI", frames, "rsi14", "用于判断超买超卖和动能极端；超卖只代表反弹条件，不等于直接买入。"),
        _indicator_scalar_line("MACD", frames, "macd_histogram", "用于判断动能柱方向；动能柱为负说明动能仍偏弱，回升才说明边际修复。"),
        _indicator_line("KD", frames, "kd", "用于观察短线摆动位置；低位钝化需要结合价格收复关键位确认。"),
        _indicator_line("TD 9/13", frames, "td_sequential", "用于观察计数完成后的反弹/回落条件；与周线方向冲突时，结论要降级。"),
        _indicator_line("谐波形态", frames, "harmonic", "用于识别比例结构；候选为空时不能用谐波给目标或入场。"),
        _indicator_line("成交量分布", frames, "volume_profile", "用于识别 POC 和价值区；价格在 POC 附近容易震荡，突破价值区才更有方向性。"),
    ]


def _indicator_line(name: str, frames: Sequence[Mapping[str, Any]], key: str, role: str) -> str:
    parts = []
    for frame in frames[:3]:
        if not isinstance(frame, Mapping):
            continue
        value = frame.get(key)
        if isinstance(value, Mapping) and value:
            parts.append(f"{frame.get('timeframe') or 'unknown'} 周期：{_indicator_value_text(key, value)}")
    if not parts:
        return f"- {name}：缺失。{role}"
    return f"- {name}：{'; '.join(parts)}。{role}"


def _indicator_scalar_line(name: str, frames: Sequence[Mapping[str, Any]], key: str, role: str) -> str:
    parts = []
    for frame in frames[:3]:
        if not isinstance(frame, Mapping):
            continue
        value = frame.get(key)
        if value is not None:
            parts.append(f"{frame.get('timeframe') or 'unknown'} 周期：{_brief_value(value)}")
    if not parts:
        return f"- {name}：缺失。{role}"
    return f"- {name}：{'; '.join(parts)}。{role}"


def _derivatives_lines(derivatives: Mapping[str, Any], liquidation_map: Mapping[str, Any]) -> list[str]:
    clusters = liquidation_map.get("clusters") if isinstance(liquidation_map.get("clusters"), list) else []
    cluster_text = "；".join(_cluster_text(cluster) for cluster in clusters[:3] if isinstance(cluster, Mapping)) or "缺失"
    cvd = derivatives.get("cvd_proxy") if isinstance(derivatives.get("cvd_proxy"), Mapping) else {}
    return [
        "",
        "## 衍生品与清算",
        f"- 资金费率：资料包原始读数为 {_brief_value(derivatives.get('funding_rate'))}。单位沿用资料包原文；资料包未确认单位时，不得自行换算百分比或年化。",
        f"- OI：{_brief_value(derivatives.get('open_interest'))}。",
        f"- 多空比：{_brief_value(derivatives.get('long_short_ratio'))}。",
        f"- CVD / 主动买卖量：{_cvd_values(cvd)}。{_cvd_reading(cvd)}",
        f"- 清算簇：{cluster_text}。",
        f"- 上方最近清算簇：{_cluster_text(liquidation_map.get('nearest_above'))}。",
        f"- 下方最近清算簇：{_cluster_text(liquidation_map.get('nearest_below'))}。",
        "- 交易作用：资金费率、OI、多空比、CVD 和清算簇只用于判断杠杆拥挤、主动买卖压力和潜在流动性磁铁；它们不能单独替代价格结构确认。",
        "- 失效条件：如果 CVD 持续走弱且价格跌破下方结构位，多头修复读法失效；如果价格放量站上上方清算/压力区，空头挤压风险上升。",
    ]


def _risk_context_lines(onchain: Mapping[str, Any], macro: Mapping[str, Any], ahr999: Mapping[str, Any]) -> list[str]:
    return [
        "",
        "## 链上 / 宏观 / AHR999",
        f"- MVRV：{_brief_value(onchain.get('mvrv'))}。",
        f"- 交易所流：{_exchange_flow_values(onchain.get('exchange_flow'))}。",
        f"- 宏观风险窗口：{_brief_value(macro.get('risk_window'))}。",
        f"- 美债 10Y：{_brief_value(macro.get('us_10y'))}。",
        f"- AHR999：{_brief_value(ahr999.get('value'))}；定投区：{_bool_zh(ahr999.get('dca_zone'))}；风险区：{_bool_zh(ahr999.get('risk_zone'))}。",
        "- 交易作用：链上、宏观和 AHR999 主要用于风险过滤和周期背景，不应替代短线技术触发。AHR999 偏低可以说明长期估值背景，但不能单独给出短线入场。",
        "- 失效条件：若宏观利率风险突然上升、交易所净流入放大或重大事件冲击出现，需要重新取数并降低原有技术结论权重。",
    ]


def _operation_framework_lines(
    summary: Mapping[str, Any],
    frames: Sequence[Mapping[str, Any]],
    derivatives: Mapping[str, Any],
    liquidation_map: Mapping[str, Any],
) -> list[str]:
    frame_4h = _frame_by_label(frames, "4h") or (frames[0] if frames else {})
    frame_1d = _frame_by_label(frames, "1d") or {}
    market = summary.get("market") if isinstance(summary.get("market"), Mapping) else {}
    current = market.get("price") or frame_4h.get("last_close")
    fvg_4h = frame_4h.get("fvg") if isinstance(frame_4h.get("fvg"), Mapping) else {}
    ob_4h = frame_4h.get("order_block") if isinstance(frame_4h.get("order_block"), Mapping) else {}
    vp_4h = frame_4h.get("volume_profile") if isinstance(frame_4h.get("volume_profile"), Mapping) else {}
    bb_4h = frame_4h.get("bollinger") if isinstance(frame_4h.get("bollinger"), Mapping) else {}
    rule_1d = frame_1d.get("rule_123") if isinstance(frame_1d.get("rule_123"), Mapping) else {}
    clusters = liquidation_map.get("clusters") if isinstance(liquidation_map.get("clusters"), list) else []
    nearest_above = liquidation_map.get("nearest_above")
    nearest_below = liquidation_map.get("nearest_below")
    above_liq = nearest_above if isinstance(nearest_above, Mapping) else _cluster_by_side(clusters, "above")
    below_liq = nearest_below if isinstance(nearest_below, Mapping) else _cluster_by_side(clusters, "below")
    long_trigger = _zone_upper(fvg_4h) or _mapping_number(bb_4h, ("middle", "mid")) or _mapping_number(rule_1d, ("trigger",))
    long_support = _zone_lower(fvg_4h) or _mapping_number(vp_4h, ("poc.price", "poc", "value_area_low"))
    invalid = _cluster_price(below_liq) or _mapping_number(rule_1d, ("invalid",)) or long_support
    target_1 = _zone_text(fvg_4h, prefer="resistance") or _brief_value(_mapping_number(bb_4h, ("middle", "mid")))
    target_2 = _zone_text(ob_4h, prefer="bearish") or _cluster_text(above_liq)
    short_trigger = _cluster_price(below_liq) or long_support
    return [
        "",
        "## 条件化操作框架（BB 技术材料，不是最终投资裁决）",
        f"- 当前参考价：{_brief_value(current)}。该价格只用于定位结构，不是交易指令。",
        f"- 偏多触发参考：优先观察价格能否有效收复 {_brief_value(long_trigger)} 或上方 FVG / 布林中轨 / POC 压力，并在回踩时不跌回 {_brief_value(long_support)}。若只是触及支撑但没有收复关键压力，只能视为反抽条件，不能写成确认多头。",
        f"- 偏多目标/压力参考：第一参考区为 {target_1}；第二参考区为 {target_2}；若上方清算簇存在，则 {_cluster_text(above_liq)} 可作为流动性磁铁观察，不是保证会到达。",
        f"- 偏多失效参考：若价格跌破 {_brief_value(invalid)} 且无法快速收回，说明下方 FVG / 清算 / 123 结构支撑失效，偏多修复框架应降级或作废。",
        f"- 偏空触发参考：若价格有效跌破 {_brief_value(short_trigger)} 后反抽失败，且 CVD 仍显示卖压，才有条件讨论空头延续；没有跌破结构前，不应把支撑区内震荡写成确认空头。",
        f"- 衍生品过滤：资金费率资料包原始读数为 {_brief_value(derivatives.get('funding_rate'))}，OI 为 {_brief_value(derivatives.get('open_interest'))}，多空比为 {_brief_value(derivatives.get('long_short_ratio'))}。这些数值只能说明杠杆背景和拥挤度线索；单位或交易所口径未确认时，不得换算成百分比、年化或美元结论。",
        "- 观望条件：如果价格继续卡在上方 FVG/OB 与下方 FVG/POC 之间，且清算簇或链上/事件材料缺失，market_analyst 应把结论写成等待确认，而不是直接追多或追空。",
        "- 有效期：market / technical / derivatives / liquidation 类材料对短线非常敏感，超过 5-15 分钟应重新取数；链上和事件材料的有效期更长，但遇到重大事件也要重取。",
    ]


def _frame_by_label(frames: Sequence[Mapping[str, Any]], label: str) -> Mapping[str, Any] | None:
    for frame in frames:
        if isinstance(frame, Mapping) and str(frame.get("timeframe") or "").lower() == label.lower():
            return frame
    return None


def _bollinger_reading(close: Any, bollinger: Mapping[str, Any]) -> str:
    close_num = number_or_none(close)
    lower = _mapping_number(bollinger, ("lower",))
    mid = _mapping_number(bollinger, ("middle", "mid"))
    upper = _mapping_number(bollinger, ("upper",))
    if close_num is None or (lower is None and mid is None and upper is None):
        return "布林带读数不完整，不能据此判断价格相对轨道。"
    if lower is not None and close_num <= lower:
        return "价格接近或低于下轨，说明短线有超跌/修复条件，但需要收复中轨确认。"
    if mid is not None and close_num < mid:
        return "价格位于中轨下方，说明反弹仍处于弱修复阶段。"
    if upper is not None and close_num >= upper:
        return "价格接近或高于上轨，说明短线偏强但也可能面临追高风险。"
    return "价格位于布林带内部，方向需要结合 FVG、OB、POC 和动量确认。"


def _vegas_reading(vegas: Mapping[str, Any]) -> str:
    text = _brief_mapping(vegas).lower()
    if "below" in text or "bearish" in text:
        return "价格处于 Vegas 压力侧，趋势修复需要重新站回通道。"
    if "above" in text or "bullish" in text:
        return "价格处于 Vegas 支撑侧，趋势背景相对偏强。"
    return "Vegas 方向不明确，只能作为辅助趋势材料。"


def _fvg_reading(fvg: Mapping[str, Any]) -> str:
    if not fvg:
        return "FVG 缺失，不能用缺口给支撑、阻力或触发位。"
    return "FVG 用来定义上下方缺口区：上方缺口是突破确认/压力观察区，下方缺口是回踩支撑/失效观察区。"


def _order_block_reading(order_block: Mapping[str, Any]) -> str:
    if not order_block:
        return "OB 缺失，不能用订单块给供应或需求区。"
    text = _brief_mapping(order_block).lower()
    if "bearish" in text:
        return "存在 bearish OB，说明上方供应区需要作为减仓、止盈或突破确认参考。"
    if "bullish" in text:
        return "存在 bullish OB，说明下方需求区可作为支撑观察，但仍需价格确认。"
    return "OB 已返回，但方向需要 market_analyst 结合价格位置解释。"


def _volume_profile_reading(close: Any, volume_profile: Mapping[str, Any]) -> str:
    if not volume_profile:
        return "成交量分布缺失，不能判断 POC 或价值区。"
    poc = _mapping_number(volume_profile, ("poc.price", "poc"))
    close_num = number_or_none(close)
    if poc is not None and close_num is not None:
        if abs(close_num - poc) / max(abs(poc), 1) < 0.01:
            return "价格接近 POC，说明市场正在成交密集区争夺，容易震荡。"
        if close_num > poc:
            return "价格位于 POC 上方，POC 可作为回踩支撑观察。"
        return "价格位于 POC 下方，POC 可作为反弹压力观察。"
    return "成交量分布已返回，但 POC/当前价不完整，方向作用有限。"


def _amd_reading(amd: Mapping[str, Any]) -> str:
    if not amd:
        return "AMD/SMC 缺失或未对齐，不能用它确认当前阶段。"
    return "AMD/SMC 可描述区间或阶段，但只有多周期对齐并出现触发后才可提高置信度。"


def _rule_123_reading(rule_123: Mapping[str, Any]) -> str:
    if not rule_123:
        return "123 结构缺失，不能用它给 swing 触发位。"
    return "123 结构提供触发位和失效位；触发位未站上前，只能作为后续确认条件。"


def _td_reading(td: Mapping[str, Any]) -> str:
    if not td:
        return "TD 计数缺失，不能使用 TD 9/13 推导。"
    direction = str(td.get("direction") or td.get("signal") or "").lower()
    countdown = _mapping_number(td, ("countdown",))
    if direction == "buy" and countdown == 13:
        return "买入侧倒计数 13 支持反弹条件，但需要价格结构确认，不能单独追多。"
    if direction == "sell" and countdown == 13:
        return "卖出侧倒计数 13 提示上方压力或回落风险，需要和趋势/阻力共振才有效。"
    return "TD 已返回，但未形成足够明确的 9/13 交易触发。"


def _harmonic_reading(harmonic: Mapping[str, Any]) -> str:
    if not harmonic:
        return "谐波候选缺失，不能用谐波给入场或目标。"
    candidates = harmonic.get("candidates")
    if isinstance(candidates, list) and not candidates:
        return "谐波候选为空，不能用谐波给入场或目标。"
    return "谐波材料已返回，需检查候选比例是否完整后才可使用。"


def _rsi_reading(rsi: Any) -> str:
    value = number_or_none(rsi)
    if value is None:
        return "RSI 缺失。"
    if value < 30:
        return f"RSI={_brief_value(value)}，处于超卖区，支持反抽条件但不等于趋势反转。"
    if value > 70:
        return f"RSI={_brief_value(value)}，处于超买区，追多风险上升。"
    return f"RSI={_brief_value(value)}，处于中性区间。"


def _macd_reading(macd_hist: Any) -> str:
    value = number_or_none(macd_hist)
    if value is None:
        return "MACD 动能柱缺失。"
    if value < 0:
        return f"MACD 动能柱为 {_brief_value(value)}，动能仍偏负。"
    if value > 0:
        return f"MACD 动能柱为 {_brief_value(value)}，动能偏正。"
    return "MACD 动能柱接近 0，动能方向不明显。"


def _kd_reading(kd: Mapping[str, Any]) -> str:
    if not kd:
        return "KD 缺失。"
    k = _mapping_number(kd, ("k", "K"))
    d = _mapping_number(kd, ("d", "D"))
    if k is None or d is None:
        return "KD 已返回但 K/D 数值不完整。"
    if k < 20 and d < 20:
        return f"KD K={_brief_value(k)}、D={_brief_value(d)}，处于低位区。"
    if k > 80 and d > 80:
        return f"KD K={_brief_value(k)}、D={_brief_value(d)}，处于高位区。"
    return f"KD K={_brief_value(k)}、D={_brief_value(d)}，处于中间区域。"


def _cvd_reading(cvd: Mapping[str, Any]) -> str:
    bias = str(cvd.get("bias") or cvd.get("direction") or "").lower()
    if "sell" in bias:
        return "CVD 指向卖方主动成交压力，反弹需要主动买盘修复。"
    if "buy" in bias:
        return "CVD 指向买方主动成交占优，可支持突破确认。"
    return "CVD 方向不明确，只能作为辅助材料。"


def _mapping_number(mapping: Mapping[str, Any], paths: Sequence[str]) -> float | None:
    for path in paths:
        value = _first_path(mapping, (path,))
        number = number_or_none(value)
        if number is not None:
            return number
    return None


def _zone_text(mapping: Mapping[str, Any], *, prefer: str | None = None) -> str:
    if not mapping:
        return "缺失"
    if prefer and prefer in mapping:
        return _brief_value(mapping.get(prefer))
    nearest_above = mapping.get("nearest_above") if isinstance(mapping.get("nearest_above"), Mapping) else None
    nearest_below = mapping.get("nearest_below") if isinstance(mapping.get("nearest_below"), Mapping) else None
    target = nearest_above or nearest_below
    if target:
        lower = _mapping_number(target, ("lower", "price"))
        upper = _mapping_number(target, ("upper", "price"))
        if lower is not None and upper is not None and lower != upper:
            return f"{_brief_value(lower)}-{_brief_value(upper)}"
        return _cluster_text(target)
    lower = _mapping_number(mapping, ("lower",))
    upper = _mapping_number(mapping, ("upper",))
    if lower is not None and upper is not None and lower != upper:
        return f"{_brief_value(lower)}-{_brief_value(upper)}"
    for key in ("resistance", "support", "bearish", "bullish", "range", "value_area"):
        value = mapping.get(key)
        if value is not None:
            return _brief_value(value)
    return _known_mapping_text(mapping)


def _zone_upper(mapping: Mapping[str, Any]) -> float | None:
    nearest_above = mapping.get("nearest_above") if isinstance(mapping.get("nearest_above"), Mapping) else None
    if nearest_above:
        value = _mapping_number(nearest_above, ("upper", "price"))
        if value is not None:
            return value
    for key in ("resistance", "upper", "bearish"):
        value = mapping.get(key)
        number = _last_number(value)
        if number is not None:
            return number
    return None


def _zone_lower(mapping: Mapping[str, Any]) -> float | None:
    nearest_below = mapping.get("nearest_below") if isinstance(mapping.get("nearest_below"), Mapping) else None
    if nearest_below:
        value = _mapping_number(nearest_below, ("lower", "price"))
        if value is not None:
            return value
    for key in ("support", "lower", "bullish"):
        value = mapping.get(key)
        number = _first_number(value)
        if number is not None:
            return number
    return None


def _first_number(value: Any) -> float | None:
    direct = number_or_none(value)
    if direct is not None:
        return direct
    numbers = re.findall(r"-?\d+(?:\.\d+)?", str(value))
    return float(numbers[0]) if numbers else None


def _last_number(value: Any) -> float | None:
    direct = number_or_none(value)
    if direct is not None:
        return direct
    numbers = re.findall(r"-?\d+(?:\.\d+)?", str(value))
    return float(numbers[-1]) if numbers else None


def _cluster_by_side(clusters: Sequence[Any], side: str) -> Mapping[str, Any]:
    for cluster in clusters:
        if isinstance(cluster, Mapping) and str(cluster.get("side") or "").lower() == side:
            return cluster
    for cluster in clusters:
        if isinstance(cluster, Mapping):
            return cluster
    return {}


def _cluster_price(cluster: Mapping[str, Any]) -> float | None:
    return _mapping_number(cluster, ("price", "level", "liquidation_price"))


def _str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _compact_gap(value: Any) -> str:
    if isinstance(value, Mapping):
        parts = [
            value.get("domain"),
            value.get("provider"),
            value.get("field"),
            value.get("reason"),
            value.get("impact"),
            value.get("message"),
        ]
        text = " / ".join(str(part).strip() for part in parts if part is not None and str(part).strip())
        return text[:300] if text else _brief_mapping(value)
    return str(value).strip()[:300]


def _first_path(data: Mapping[str, Any], paths: Sequence[str]) -> Any:
    for path in paths:
        current: Any = data
        found = True
        for part in str(path).split("."):
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and current is not None:
            return _compact_scalar(current)
    return None


def _first_mapping(data: Mapping[str, Any], paths: Sequence[str]) -> dict[str, Any]:
    for path in paths:
        current: Any = data
        found = True
        for part in str(path).split("."):
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and isinstance(current, Mapping):
            return _compact_mapping(current)
    return {}


def _mapping_list(data: Mapping[str, Any], paths: Sequence[str]) -> list[Any]:
    for path in paths:
        current: Any = data
        found = True
        for part in str(path).split("."):
            if isinstance(current, Mapping) and part in current:
                current = current[part]
            else:
                found = False
                break
        if found and isinstance(current, list):
            return current
        if found and isinstance(current, Mapping):
            return [current]
    return []


def _first_deep_value(data: Mapping[str, Any], keys: Sequence[str]) -> Any:
    lowered = {str(key).lower() for key in keys}
    for value in _walk_values(data):
        if not isinstance(value, tuple):
            continue
        key, item = value
        if key.lower() in lowered and item is not None and not isinstance(item, (Mapping, list)):
            return _compact_scalar(item)
        if key.lower() in lowered and isinstance(item, Mapping):
            scalar = _scalar_from_mapping(item)
            if scalar is not None:
                return scalar
    return None


def _first_deep_mapping(data: Mapping[str, Any], keys: Sequence[str]) -> dict[str, Any]:
    lowered = {str(key).lower() for key in keys}
    for value in _walk_values(data):
        if not isinstance(value, tuple):
            continue
        key, item = value
        if key.lower() in lowered and isinstance(item, Mapping):
            return _compact_mapping(item)
    return {}


def _first_deep_list(data: Mapping[str, Any], keys: Sequence[str]) -> list[Any]:
    lowered = {str(key).lower() for key in keys}
    for value in _walk_values(data):
        if not isinstance(value, tuple):
            continue
        key, item = value
        if key.lower() in lowered and isinstance(item, list):
            return item
    return []


def _walk_values(value: Any) -> list[tuple[str, Any]]:
    pending = [value]
    pairs: list[tuple[str, Any]] = []
    while pending:
        current = pending.pop(0)
        if isinstance(current, Mapping):
            for key, item in current.items():
                key_text = str(key)
                pairs.append((key_text, item))
                if isinstance(item, (Mapping, list)):
                    pending.append(item)
        elif isinstance(current, list):
            for item in current:
                if isinstance(item, (Mapping, list)):
                    pending.append(item)
    return pairs


def _compact_scalar(value: Any) -> Any:
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value).strip()
    return text[:160] if text else None


def _compact_mapping(value: Mapping[str, Any], *, max_items: int = 8) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key, item in value.items():
        if len(compact) >= max_items:
            break
        key_text = str(key)
        if isinstance(item, Mapping):
            compact[key_text] = _compact_mapping(item, max_items=4)
        elif isinstance(item, list):
            compact[key_text] = [_compact_scalar(part) for part in item[:4] if not isinstance(part, (Mapping, list))]
        else:
            compact[key_text] = _compact_scalar(item)
    return compact


def _scalar_from_mapping(value: Mapping[str, Any]) -> Any:
    for key in ("value", "current", "latest", "close", "price", "ahr999", "funding_rate", "long_short_ratio"):
        item = value.get(key)
        if item is not None and not isinstance(item, (Mapping, list)):
            return _compact_scalar(item)
    return None


def _brief_value(value: Any) -> str:
    if value is None or value == "":
        return "缺失"
    if isinstance(value, float):
        return f"{value:.6g}"
    if isinstance(value, Mapping):
        return _brief_mapping(value)
    if isinstance(value, list):
        return " / ".join(_brief_value(item) for item in value[:3]) or "缺失"
    return str(value)


def _brief_mapping(value: Any) -> str:
    if not isinstance(value, Mapping) or not value:
        return "缺失"
    parts: list[str] = []
    for key, item in list(value.items())[:5]:
        if isinstance(item, Mapping):
            item_text = _brief_mapping(item)
        elif isinstance(item, list):
            item_text = "/".join(_brief_value(part) for part in item[:3])
        else:
            item_text = _brief_value(item)
        if item_text and item_text != "缺失":
            parts.append(f"{key}={item_text}")
    return "，".join(parts) if parts else "缺失"


def _provider_symbol(ticker: str, *, env: Mapping[str, str]) -> str:
    overrides_payload = env_text(env, _BINANCE_SYMBOL_OVERRIDES_ENV)
    if overrides_payload:
        parsed = json.loads(overrides_payload)
        if isinstance(parsed, Mapping):
            value = parsed.get(ticker) or parsed.get(ticker.upper())
            if isinstance(value, str) and value.strip():
                symbol = value.strip().upper()
                if _SYMBOL_RE.fullmatch(symbol):
                    return symbol
                raise ValueError(f"{_BINANCE_SYMBOL_OVERRIDES_ENV}.{ticker} must be an exchange symbol")
    quote_asset = (env_text(env, _QUOTE_ASSET_ENV) or "USDT").upper()
    symbol = ticker.upper() if ticker.upper().endswith(quote_asset) else f"{ticker.upper()}{quote_asset}"
    if not _SYMBOL_RE.fullmatch(symbol):
        raise ValueError("derived Binance symbol is invalid")
    return symbol


def _parse_date(value: Any, *, fallback: date) -> date:
    if not isinstance(value, str) or not value.strip():
        return fallback
    return datetime.strptime(value.strip(), "%Y-%m-%d").date()


def _date_to_millis(value: str, *, end_of_day: bool = False) -> int:
    parsed = datetime.strptime(value, "%Y-%m-%d")
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999000)
    parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


def _row_payload(row: Any) -> dict[str, Any]:
    return {
        "date": str(row["date"]),
        "open": _float(row["open"]),
        "high": _float(row["high"]),
        "low": _float(row["low"]),
        "close": _float(row["close"]),
        "volume": _float(row["volume"]),
    }


def _previous_close(rows: pd.DataFrame) -> float | None:
    if len(rows) < 2:
        return None
    return _float(rows.iloc[-2]["close"])


def _requested_rows(rows: pd.DataFrame, request: Mapping[str, Any]) -> pd.DataFrame:
    if rows.empty:
        return rows
    return rows[(rows["date"] >= str(request["start_date"])) & (rows["date"] <= str(request["end_date"]))].copy()


def _float(value: Any, *, digits: int = 6) -> float | None:
    number = number_or_none(value)
    if number is None:
        return None
    return round(float(number), digits)


def _empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])


def _redact(value: str) -> str:
    return re.sub(r"\b[A-Za-z0-9_=-]{20,}\b", "***", value)[:500]


__all__ = [
    "build_crypto_market_data_pack",
    "run_crypto_market_data_pack",
]
