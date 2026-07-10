from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from urllib import error, parse, request

_DEEPSEEK_BALANCE_HOST = "api.deepseek.com"


@dataclass(frozen=True)
class DeepSeekBalance:
    currency: str
    total_balance: Decimal
    is_available: bool
    checked_at: str


def fetch_deepseek_cny_balance(
    *,
    api_key: str,
    endpoint_url: str | None = None,
    timeout_seconds: float = 5.0,
) -> DeepSeekBalance:
    key = api_key.strip()
    if not key:
        raise ValueError("deepseek_api_key_missing")
    http_request = request.Request(
        _deepseek_balance_url(endpoint_url),
        headers={
            "accept": "application/json",
            "authorization": f"Bearer {key}",
            "user-agent": "claw-trade-ui/llm-cost",
        },
        method="GET",
    )
    try:
        with request.urlopen(http_request, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise RuntimeError(f"deepseek_balance_http_{exc.code}") from exc
    except (OSError, error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError("deepseek_balance_unavailable") from exc
    return parse_deepseek_cny_balance_payload(payload)


def parse_deepseek_cny_balance_payload(payload: Mapping[str, Any]) -> DeepSeekBalance:
    infos = payload.get("balance_infos")
    if not isinstance(infos, list):
        raise ValueError("deepseek_balance_infos_missing")
    for item in infos:
        if not isinstance(item, Mapping):
            continue
        currency = str(item.get("currency") or "").strip().upper()
        if currency != "CNY":
            continue
        raw_total = str(item.get("total_balance") or "").strip()
        try:
            total = Decimal(raw_total)
        except (InvalidOperation, ValueError) as exc:
            raise ValueError("deepseek_cny_balance_invalid") from exc
        return DeepSeekBalance(
            currency="CNY",
            total_balance=total,
            is_available=bool(payload.get("is_available")),
            checked_at=datetime.now(tz=UTC).isoformat(),
        )
    raise ValueError("deepseek_cny_balance_missing")


def _deepseek_balance_url(endpoint_url: str | None) -> str:
    raw = (endpoint_url or "https://api.deepseek.com").strip() or "https://api.deepseek.com"
    parsed = parse.urlsplit(raw if "://" in raw else f"https://{raw}")
    host = (parsed.hostname or "").strip().lower()
    if parsed.scheme != "https" or host != _DEEPSEEK_BALANCE_HOST:
        raise ValueError("deepseek_balance_endpoint_unsupported")
    return parse.urlunsplit(("https", _DEEPSEEK_BALANCE_HOST, "/user/balance", "", ""))
