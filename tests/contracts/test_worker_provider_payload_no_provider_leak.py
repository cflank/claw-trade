from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

DATA_NEED_TOOL_NAME = "claw_request_data"
FORBIDDEN_WORKER_VISIBLE_KEYS = {"provider", "path", "api_name", "url", "header", "token"}
FRONTLINE_WORKERS = {"market_analyst", "fundamental_analyst", "news_analyst", "social_analyst"}
REQUIRED_RUNTIME_MARKERS = {"run_id", "call_id", "worker_id", "stage", "profile", "openclaw_run_id"}


def test_worker_visible_data_need_tool_payload_does_not_expose_provider_execution_details() -> None:
    path = _provider_payload_capture_path()
    payload = _read_json_object(path)
    assert payload is not None, (
        f"real OpenClaw provider payload capture for claw_request_data is missing at {path}; "
        "schema-only proof is insufficient"
    )
    assert _is_real_capture_for_tool(payload, DATA_NEED_TOOL_NAME), (
        f"real OpenClaw provider payload capture at {path} does not expose {DATA_NEED_TOOL_NAME}"
    )

    forbidden = _forbidden_worker_visible_keys(payload, FORBIDDEN_WORKER_VISIBLE_KEYS)
    assert forbidden == set(), (str(path), sorted(forbidden))


def _provider_payload_capture_path() -> Path:
    repo_root = Path(__file__).resolve().parents[2]
    configured = os.environ.get("CLAW_TRADE_PROVIDER_PAYLOAD_CAPTURE")
    if configured:
        return Path(configured).expanduser()
    return repo_root / ".runtime" / "data-need-provider-payload-proof" / "provider-request.json"


def _read_json_object(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _is_real_capture_for_tool(payload: dict[str, Any], tool_name: str) -> bool:
    if payload.get("source") != "provider_request_capture":
        return False
    if not _non_empty_str(payload.get("captured_at")):
        return False
    if not isinstance(payload.get("sequence"), int) or payload["sequence"] <= 0:
        return False
    provider_model = payload.get("provider_model")
    if not isinstance(provider_model, dict):
        return False
    if not _non_empty_str(provider_model.get("id")) or not _non_empty_str(provider_model.get("api")):
        return False
    body = payload.get("payload")
    if not isinstance(body, dict):
        return False
    if not _non_empty_str(body.get("model")):
        return False
    if not isinstance(body.get("messages"), list) or not body["messages"]:
        return False
    if not isinstance(body.get("tools"), list) or not body["tools"]:
        return False
    markers = payload.get("runtime_markers")
    if not isinstance(markers, dict):
        return False
    if REQUIRED_RUNTIME_MARKERS - set(markers):
        return False
    if markers.get("stage") != "frontline":
        return False
    if markers.get("worker_id") not in FRONTLINE_WORKERS:
        return False
    for field in REQUIRED_RUNTIME_MARKERS:
        if not _non_empty_str(markers.get(field)):
            return False
    return tool_name in _tool_names(body.get("tools"))


def _non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _tool_names(tools: Any) -> set[str]:
    names: set[str] = set()
    if not isinstance(tools, list):
        return names
    for item in tools:
        if not isinstance(item, dict):
            continue
        function = item.get("function")
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if isinstance(name, str) and name.strip():
            names.add(name.strip())
    return names


def _forbidden_worker_visible_keys(provider_payload: dict[str, Any], forbidden: set[str]) -> set[str]:
    body = provider_payload.get("payload")
    assert isinstance(body, dict), "provider payload proof must include payload object"
    found = _forbidden_object_keys(body.get("tools"), forbidden)
    found.update(_forbidden_message_terms(body.get("messages"), forbidden))
    return found


def _forbidden_object_keys(value: Any, forbidden: set[str]) -> set[str]:
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            if key in forbidden:
                found.add(key)
            if key in {"properties", "$defs", "definitions"} and isinstance(child, dict):
                found.update(set(child) & forbidden)
            found.update(_forbidden_object_keys(child, forbidden))
    elif isinstance(value, list):
        for child in value:
            if isinstance(child, str) and child in forbidden:
                found.add(child)
            found.update(_forbidden_object_keys(child, forbidden))
    return found


def _forbidden_message_terms(value: Any, forbidden: set[str]) -> set[str]:
    found: set[str] = set()
    for text in _message_text_fragments(value):
        for key in forbidden:
            if re.search(rf"(?<![A-Za-z0-9_]){re.escape(key)}(?![A-Za-z0-9_])", text):
                found.add(key)
    return found


def _message_text_fragments(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        fragments: list[str] = []
        for item in value:
            fragments.extend(_message_text_fragments(item))
        return tuple(fragments)
    if isinstance(value, dict):
        content = value.get("content")
        if isinstance(content, (str, list, dict)):
            return _message_text_fragments(content)
        text = value.get("text")
        if isinstance(text, str):
            return (text,)
    return ()
