from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

import requests

_HTTP_TIMEOUT_SECONDS = 15
_DEFAULT_HEADERS = {
    "User-Agent": "claw-trade-openbb-polymarket-adapter/1.0 (research@localhost)",
}
_PUBLIC_SEARCH_URL = "https://gamma-api.polymarket.com/public-search"
_SYMBOL_ALIASES: dict[str, tuple[str, ...]] = {
    "BTC": ("Bitcoin", "BTC"),
    "ETH": ("Ethereum", "ETH"),
    "SOL": ("Solana", "SOL"),
    "DOGE": ("Dogecoin", "DOGE"),
}


def fetch_polymarket_events(*, params: Mapping[str, Any], limit: int = 10) -> tuple[tuple[Mapping[str, Any], ...], str]:
    rows: list[Mapping[str, Any]] = []
    seen: set[str] = set()
    for query in _polymarket_queries(params):
        payload = _http_get_json(_PUBLIC_SEARCH_URL, params={"q": query, "limit": 20})
        for event in _events_from_payload(payload):
            row = _event_to_row(event)
            if row is None:
                continue
            key = str(event.get("slug") or event.get("id") or row["title"]).strip().lower()
            if key in seen:
                continue
            seen.add(key)
            rows.append(row)
            if len(rows) >= limit:
                return tuple(rows), _PUBLIC_SEARCH_URL
    return tuple(rows), _PUBLIC_SEARCH_URL


def _polymarket_queries(params: Mapping[str, Any]) -> tuple[str, ...]:
    ticker = str(params.get("ticker") or "").strip().upper()
    symbol = ticker.split(".", 1)[0].replace("-USD", "").replace("USDT", "")
    name = str(params.get("company_name") or "").strip()
    candidates: list[str] = []
    candidates.extend(_SYMBOL_ALIASES.get(symbol, ()))
    if name:
        candidates.append(name)
    if symbol:
        candidates.append(symbol)
    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.strip()
        if not normalized or normalized.lower() in seen:
            continue
        seen.add(normalized.lower())
        deduped.append(normalized)
    return tuple(deduped) or ("crypto",)


def _events_from_payload(payload: Any) -> tuple[Mapping[str, Any], ...]:
    if isinstance(payload, Mapping):
        events = payload.get("events")
        if isinstance(events, Sequence) and not isinstance(events, (str, bytes, bytearray)):
            return tuple(event for event in events if isinstance(event, Mapping))
    if isinstance(payload, Sequence) and not isinstance(payload, (str, bytes, bytearray)):
        return tuple(event for event in payload if isinstance(event, Mapping))
    return ()


def _event_to_row(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    if str(event.get("closed")).lower() == "true":
        return None
    if event.get("active") is False:
        return None

    title = str(event.get("title") or "").strip()
    if not title:
        return None

    market = _first_open_market(event)
    market_text = _market_text(market) if market is not None else ""
    row_title = "；".join(part for part in (title, market_text) if part)
    slug = str(event.get("slug") or "").strip()
    return {
        "title": row_title,
        "url": f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com",
        "published_at": event.get("endDate") or event.get("startDate") or event.get("updatedAt"),
        "summary": "polymarket_event_expectation",
    }


def _first_open_market(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    for market in _as_sequence(event.get("markets")):
        if not isinstance(market, Mapping):
            continue
        if str(market.get("closed")).lower() == "true" or market.get("active") is False:
            continue
        return market
    return None


def _market_text(market: Mapping[str, Any] | None) -> str:
    if market is None:
        return ""
    question = str(market.get("question") or "").strip()
    probability = _probability_text(market)
    return "；".join(part for part in (question, probability) if part)


def _probability_text(market: Mapping[str, Any]) -> str:
    outcomes = _json_list(market.get("outcomes"))
    prices = _json_list(market.get("outcomePrices"))
    parts: list[str] = []
    for outcome, price in zip(outcomes, prices):
        label = str(outcome).strip()
        probability = _as_float(price)
        if not label or probability is None:
            continue
        parts.append(f"{label}={probability * 100:.1f}%")
    return " / ".join(parts[:3])


def _json_list(value: Any) -> tuple[Any, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(value)
    if not isinstance(value, str) or not value.strip():
        return ()
    try:
        parsed = json.loads(value)
    except Exception:
        return ()
    if isinstance(parsed, Sequence) and not isinstance(parsed, (str, bytes, bytearray)):
        return tuple(parsed)
    return ()


def _as_float(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_sequence(value: Any) -> Sequence[Any]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return ()


def _http_get_json(url: str, *, params: Mapping[str, Any] | None = None) -> Any:
    response = requests.get(url, params=params, headers=_DEFAULT_HEADERS, timeout=_HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()
