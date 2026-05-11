from __future__ import annotations

from dataclasses import fields, is_dataclass
from datetime import UTC, datetime
import json
import sys
from typing import Any, Mapping

from orchestrator import SocialToolRuntimeContext, build_social_sentiment_pack
from profile import SocialToolInput

TOOL_NAME = "social_social_sentiment_pack"
TOOL_WORKER_ID = "social_analyst"
_EXIT_SUCCESS = 0
_EXIT_FAILED = 1
_EXIT_PROTOCOL_ERROR = 2


def run_social_data_pack(
    tool_input: SocialToolInput | Mapping[str, Any] | Any,
    context: SocialToolRuntimeContext | Mapping[str, Any] | Any,
) -> dict[str, Any]:
    runtime_context = _coerce_runtime_context(context)
    normalized_tool_input = _coerce_tool_input(tool_input)
    pack = build_social_sentiment_pack(normalized_tool_input, runtime_context)
    return pack.to_dict()


def _coerce_tool_input(value: SocialToolInput | Mapping[str, Any] | Any) -> SocialToolInput:
    if isinstance(value, SocialToolInput):
        return value
    payload = _to_payload(value, "tool_input")
    return SocialToolInput(
        ticker=_require_non_empty_str(payload.get("ticker"), field_name="ticker"),
        market=_require_non_empty_str(payload.get("market"), field_name="market"),
        company_name=_optional_str(payload.get("company_name")),
        industry=_optional_str(payload.get("industry")),
        start_date=_optional_str(payload.get("start_date")),
        end_date=_optional_str(payload.get("end_date")),
        approved_artifact_refs=_coerce_approved_refs(payload.get("approved_artifact_refs")),
        aliases=_coerce_aliases(payload.get("aliases")),
    )


def _coerce_runtime_context(
    value: SocialToolRuntimeContext | Mapping[str, Any] | Any,
) -> SocialToolRuntimeContext:
    if isinstance(value, SocialToolRuntimeContext):
        if value.current_time.strip() != "":
            return value
        return SocialToolRuntimeContext(
            run_id=value.run_id,
            stage=value.stage,
            worker_id=value.worker_id,
            call_id=value.call_id,
            tool_name=value.tool_name,
            evidence_root=value.evidence_root,
            current_time=_now_iso(),
        )

    payload = _to_payload(value, "runtime_context")
    current_time = _optional_str(payload.get("current_time")) or _now_iso()
    return SocialToolRuntimeContext(
        run_id=_require_non_empty_str(payload.get("run_id"), field_name="run_id"),
        stage=_require_non_empty_str(payload.get("stage"), field_name="stage"),
        worker_id=_require_non_empty_str(payload.get("worker_id"), field_name="worker_id"),
        call_id=_require_non_empty_str(payload.get("call_id"), field_name="call_id"),
        tool_name=_require_non_empty_str(payload.get("tool_name"), field_name="tool_name"),
        evidence_root=_require_non_empty_str(payload.get("evidence_root"), field_name="evidence_root"),
        current_time=current_time,
    )


def _to_payload(value: Mapping[str, Any] | Any, field_name: str) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item_field.name: getattr(value, item_field.name) for item_field in fields(value)}
    raise ValueError(f"{field_name} 必须是对象")


def _coerce_approved_refs(value: object) -> tuple[object, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("approved_artifact_refs 必须是数组")
    return tuple(value)


def _coerce_aliases(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError("aliases 必须是数组")
    aliases: list[str] = []
    for raw_alias in value:
        alias = _optional_str(raw_alias)
        if alias is not None:
            aliases.append(alias)
    return tuple(aliases)


def _require_non_empty_str(value: object, *, field_name: str) -> str:
    if not isinstance(value, str) or value.strip() == "":
        raise ValueError(f"{field_name} 必须是非空字符串")
    return value.strip()


def _optional_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if text == "":
        return None
    return text


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _parse_stdin_payload(stdin_text: str) -> dict[str, Any]:
    payload = json.loads(stdin_text)
    if not isinstance(payload, dict):
        raise ValueError("stdin JSON 必须是对象")
    if "tool_input" not in payload or "runtime_context" not in payload:
        raise ValueError("stdin JSON 必须包含 tool_input 和 runtime_context")
    return payload


def main() -> int:
    try:
        payload = _parse_stdin_payload(sys.stdin.read())
        response = run_social_data_pack(payload["tool_input"], payload["runtime_context"])
        sys.stdout.write(json.dumps(response, ensure_ascii=False))
        sys.stdout.write("\n")
        return _EXIT_SUCCESS if bool(response.get("ok")) else _EXIT_FAILED
    except Exception as exc:
        sys.stdout.write(
            json.dumps(
                {
                    "ok": False,
                    "quality": {
                        "status": "failed",
                        "warnings": [{"code": "SOCIAL_INVALID_INPUT", "message": str(exc)}],
                    },
                },
                ensure_ascii=False,
            )
        )
        sys.stdout.write("\n")
        return _EXIT_PROTOCOL_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
