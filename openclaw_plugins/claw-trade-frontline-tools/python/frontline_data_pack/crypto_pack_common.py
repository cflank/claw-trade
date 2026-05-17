from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any, Callable, Mapping

import requests


MARKET_CRYPTO = "CRYPTO"
DEFAULT_TIMEOUT_SECONDS = 15
TICKER_RE = re.compile(r"^[A-Z0-9][A-Z0-9._-]{0,31}$")

FetchJson = Callable[..., Any]
FetchText = Callable[..., str]


def normalize_frontline_context(
    runtime_context: Mapping[str, Any],
    *,
    tool_name: str,
    worker_id: str,
) -> dict[str, str]:
    if not isinstance(runtime_context, Mapping):
        raise ValueError("runtime_context must be a mapping")
    required = ("run_id", "stage", "worker_id", "call_id", "tool_name", "evidence_root")
    normalized: dict[str, str] = {}
    for field in required:
        value = runtime_context.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"runtime_context.{field} is required")
        normalized[field] = value.strip()
    if normalized["stage"] != "frontline":
        raise ValueError(f"{tool_name} requires stage=frontline")
    if normalized["worker_id"] != worker_id:
        raise ValueError(f"{tool_name} requires worker_id={worker_id}")
    if normalized["tool_name"] != tool_name:
        raise ValueError(f"runtime_context.tool_name must be {tool_name}")
    for optional in ("current_date", "start_date", "end_date"):
        value = runtime_context.get(optional)
        if isinstance(value, str) and value.strip():
            normalized[optional] = value.strip()
    return normalized


def normalize_crypto_input(tool_input: Mapping[str, Any], context: Mapping[str, str]) -> dict[str, Any]:
    if not isinstance(tool_input, Mapping):
        raise ValueError("tool_input must be a mapping")
    ticker = required_text(tool_input, "ticker").upper()
    if not TICKER_RE.fullmatch(ticker):
        raise ValueError("tool_input.ticker must be a single crypto symbol")
    market = required_text(tool_input, "market").upper()
    if market != MARKET_CRYPTO:
        raise ValueError(f"tool_input.market must be {MARKET_CRYPTO}")
    aliases = tool_input.get("aliases")
    if aliases is not None:
        if not isinstance(aliases, list) or not all(isinstance(item, str) for item in aliases):
            raise ValueError("tool_input.aliases must be a string list")
        aliases = [item.strip() for item in aliases if item.strip()]
    else:
        aliases = []
    current_date = context.get("current_date")
    return {
        "ticker": ticker,
        "market": market,
        "company_name": optional_text(tool_input, "company_name") or ticker,
        "industry": optional_text(tool_input, "industry"),
        "start_date": context.get("start_date") or optional_text(tool_input, "start_date"),
        "end_date": context.get("end_date") or optional_text(tool_input, "end_date") or current_date,
        "aliases": aliases,
    }


def attempted_fetch(
    *,
    provider: str,
    endpoint: str,
    role: str,
    url: str,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    auth_mode: str,
    fetch_json: FetchJson,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> tuple[Any, dict[str, Any]]:
    started_at = utc_now()
    started = time.perf_counter()
    payload: Any = None
    error_code: str | None = None
    error_message: str | None = None
    try:
        payload = fetch_json(
            url,
            params=dict(params),
            headers=dict(headers),
            timeout=timeout,
            method=method,
            json_body=dict(json_body) if json_body is not None else None,
        )
    except requests.Timeout as exc:
        error_code = "timeout"
        error_message = str(exc)
    except Exception as exc:  # noqa: BLE001
        error_code = exc.__class__.__name__
        error_message = str(exc)
    elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
    attempt = {
        "provider": provider,
        "endpoint": endpoint,
        "role": role,
        "status": "error" if error_code else "success",
        "started_at": started_at,
        "finished_at": utc_now(),
        "elapsed_ms": elapsed_ms,
        "timeout_ms": timeout * 1000,
        "raw_count": raw_count(payload),
        "accepted_count": 0,
        "auth_mode": auth_mode,
        "payload_hash": payload_hash(payload) if payload is not None else None,
        "error_code": error_code,
        "error_message_redacted": redact(error_message) if error_message else None,
    }
    return payload, attempt


def blocked_attempt(
    *,
    provider: str,
    endpoint: str,
    role: str,
    auth_mode: str,
    error_code: str,
    error_message: str,
) -> dict[str, Any]:
    now = utc_now()
    return {
        "provider": provider,
        "endpoint": endpoint,
        "role": role,
        "status": "config_blocked",
        "started_at": now,
        "finished_at": now,
        "elapsed_ms": 0,
        "timeout_ms": 0,
        "raw_count": 0,
        "accepted_count": 0,
        "auth_mode": auth_mode,
        "payload_hash": None,
        "error_code": error_code,
        "error_message_redacted": redact(error_message),
    }


def finish_attempt(attempt: dict[str, Any], payload: Any, *, accepted_count: int, schema_valid: bool = True) -> None:
    attempt["raw_count"] = raw_count(payload)
    attempt["accepted_count"] = accepted_count
    if attempt.get("status") in {
        "cache_hit",
        "cache_miss",
        "cache_stale",
        "config_blocked",
        "error",
        "rate_limited",
        "schema_invalid",
    }:
        return
    if not schema_valid:
        attempt["status"] = "schema_invalid"
        attempt["error_code"] = "provider_response_schema_invalid"
        return
    if accepted_count <= 0:
        attempt["status"] = "empty"


def requests_json(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    method: str = "GET",
    json_body: Mapping[str, Any] | None = None,
) -> Any:
    if method.upper() == "POST":
        response = requests.post(url, params=params, headers=headers, json=json_body, timeout=timeout)
    else:
        response = requests.get(url, params=params, headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.json()


def attempted_fetch_text(
    *,
    provider: str,
    endpoint: str,
    role: str,
    url: str,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
    auth_mode: str,
    fetch_text: FetchText,
) -> tuple[str | None, dict[str, Any]]:
    started_at = utc_now()
    started = time.perf_counter()
    payload: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    try:
        payload = fetch_text(url, params=dict(params), headers=dict(headers), timeout=timeout)
    except requests.Timeout as exc:
        error_code = "timeout"
        error_message = str(exc)
    except Exception as exc:  # noqa: BLE001
        error_code = exc.__class__.__name__
        error_message = str(exc)
    elapsed_ms = max(0, int((time.perf_counter() - started) * 1000))
    attempt = {
        "provider": provider,
        "endpoint": endpoint,
        "role": role,
        "status": "error" if error_code else "success",
        "started_at": started_at,
        "finished_at": utc_now(),
        "elapsed_ms": elapsed_ms,
        "timeout_ms": timeout * 1000,
        "raw_count": 1 if payload else 0,
        "accepted_count": 0,
        "auth_mode": auth_mode,
        "payload_hash": payload_hash(payload) if payload is not None else None,
        "error_code": error_code,
        "error_message_redacted": redact(error_message) if error_message else None,
    }
    return payload, attempt


def requests_text(
    url: str,
    *,
    params: Mapping[str, Any],
    headers: Mapping[str, str],
    timeout: int,
) -> str:
    merged_headers = {"User-Agent": "claw-trade/crypto-data-pack"}
    merged_headers.update(dict(headers))
    response = requests.get(url, params=params, headers=merged_headers, timeout=timeout)
    response.raise_for_status()
    return response.text


def search_terms(request: Mapping[str, Any]) -> list[str]:
    terms = [str(request["ticker"]), str(request.get("company_name") or request["ticker"])]
    terms.extend(str(item) for item in request.get("aliases", []))
    deduped: list[str] = []
    seen: set[str] = set()
    for item in terms:
        text = item.strip()
        key = text.lower()
        if text and key not in seen:
            deduped.append(text)
            seen.add(key)
    return deduped


def matches_request(payload: Any, request: Mapping[str, Any]) -> bool:
    aliases = [item.lower() for item in search_terms(request)]
    text = json.dumps(jsonable(payload), ensure_ascii=False).lower()
    return any(alias and alias in text for alias in aliases)


def parse_json_env(env: Mapping[str, str], field: str) -> Any:
    value = env_text(env, field)
    if not value:
        return None
    return json.loads(value)


def configured_entries(env: Mapping[str, str], field: str, request: Mapping[str, Any]) -> list[dict[str, Any]]:
    payload = parse_json_env(env, field)
    if payload is None:
        return []
    if isinstance(payload, Mapping):
        rows = payload.get(str(request["ticker"])) or payload.get(str(request["ticker"]).upper())
        if rows is None:
            rows = payload.get(str(request.get("company_name") or "")) or payload.get("*")
    else:
        rows = payload
    if rows is None:
        return []
    if isinstance(rows, (str, Mapping)):
        rows = [rows]
    if not isinstance(rows, list):
        raise ValueError(f"{field} must be a JSON object, list, string, or source mapping")
    entries: list[dict[str, Any]] = []
    for item in rows:
        if isinstance(item, str) and item.strip():
            entries.append({"url": item.strip()})
        elif isinstance(item, Mapping):
            entries.append({str(key): value for key, value in item.items()})
        else:
            raise ValueError(f"{field} entries must be strings or objects")
    return entries


def write_pack_files(
    context: Mapping[str, str],
    *,
    tool_name: str,
    pack: Mapping[str, Any],
    raw_payload: Mapping[str, Any],
) -> None:
    evidence_root = Path(context["evidence_root"])
    evidence_root.mkdir(parents=True, exist_ok=True)
    (evidence_root / f"{tool_name}.json").write_text(
        json.dumps(jsonable(pack), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    raw_dir = evidence_root / "provider_raw" / tool_name
    raw_dir.mkdir(parents=True, exist_ok=True)
    (raw_dir / "raw_payload.json").write_text(
        json.dumps(jsonable(raw_payload), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


def required_text(payload: Mapping[str, Any], field: str) -> str:
    value = optional_text(payload, field)
    if value is None:
        raise ValueError(f"tool_input.{field} is required")
    return value


def optional_text(payload: Mapping[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"tool_input.{field} must be a string")
    stripped = value.strip()
    return stripped or None


def env_text(env: Mapping[str, str], field: str) -> str | None:
    value = env.get(field)
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def first_env(env: Mapping[str, str], names: tuple[str, ...]) -> tuple[str | None, str | None]:
    for name in names:
        value = env_text(env, name)
        if value:
            return value, name
    return None, None


def str_or_none(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def number_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number != number:
        return None
    return number


def compact_str_list(value: Any, *, limit: int) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            result.append(item.strip())
        if len(result) >= limit:
            break
    return result


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def payload_hash(payload: Any) -> str:
    data = json.dumps(jsonable(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(data).hexdigest()


def raw_count(payload: Any) -> int:
    if isinstance(payload, str):
        return 1 if payload else 0
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, Mapping):
        for key in ("coins", "data", "articles", "news_results", "organic_results", "results", "events", "markets", "value"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
        web = payload.get("web")
        if isinstance(web, Mapping) and isinstance(web.get("results"), list):
            return len(web["results"])
        pages = payload.get("webPages")
        if isinstance(pages, Mapping) and isinstance(pages.get("value"), list):
            return len(pages["value"])
        return 1
    return 0


def redact(value: str | None) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\b[A-Za-z0-9_=-]{20,}\b", "***", str(value))
    return text[:500]


def jsonable(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, list):
        return [jsonable(item) for item in value]
    if isinstance(value, tuple):
        return [jsonable(item) for item in value]
    return value
