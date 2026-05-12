from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import WorkerCall

_DISALLOWED_SOURCES = frozenset(
    {
        "renderer_output",
        "export_report",
        "logs",
        "reconstructed_prompt",
        "reconstructed_provider_request",
    }
)
_RUNTIME_MARKER_FIELDS = frozenset(
    {
        "run_id",
        "call_id",
        "worker_id",
        "stage",
        "profile",
        "openclaw_run_id",
    }
)


def validate_provider_request(call: WorkerCall, evidence: ProviderEvidence) -> GuardResult:
    provider_request_path = evidence.provider_request_path
    payload_result = _read_json_object(provider_request_path)
    if isinstance(payload_result, GuardResult):
        return payload_result
    payload = payload_result

    source = _required_non_empty_str(payload.get("source"))
    if source is None:
        return _failed("source 缺失或为空", provider_request_path)
    # 这里必须硬阻断 source 漂移，防止把日志/导出/重构文本伪装成真实 provider request。
    if source != "provider_request_capture":
        if source in _DISALLOWED_SOURCES:
            return _failed(f"source 非法: {source}", provider_request_path)
        return _failed(
            f"source 必须是 provider_request_capture，实际为: {source}",
            provider_request_path,
        )

    marker_guard = _validate_runtime_marker(call, evidence, payload, provider_request_path)
    if not marker_guard.ok:
        return marker_guard

    payload_body = payload.get("payload")
    if not isinstance(payload_body, dict):
        return _failed("payload 必须是 JSON object", provider_request_path)

    messages = payload_body.get("messages")
    if not isinstance(messages, list) or not messages:
        return _failed("payload.messages 必须是非空列表", provider_request_path)
    if not all(_looks_like_message(item) for item in messages):
        return _failed("payload.messages 结构不可信", provider_request_path)

    prompt_guard = _validate_model_visible_prompt_text(call, messages, provider_request_path)
    if not prompt_guard.ok:
        return prompt_guard

    tools = payload_body.get("tools")
    if call.allowed_tools:
        if not isinstance(tools, list) or not tools:
            return _failed("payload.tools 必须是非空列表", provider_request_path)
    elif tools is None:
        tools = []
    elif not isinstance(tools, list):
        return _failed("payload.tools 必须是列表", provider_request_path)
    if not all(_looks_like_tool(item) for item in tools):
        return _failed("payload.tools 结构不可信", provider_request_path)

    return guard_passed(category="provider_request")


_FORBIDDEN_MODEL_VISIBLE_PROMPT_TOKENS = (
    "你正在执行当前分析师的一轮任务。",
    "请按用户消息中的角色、工具和报告格式要求完成本轮分析。",
    "只能依据本轮可见工具结果写报告，不要编造工具没有返回的数据或来源。",
    "[ApprovedMaterials]",
    "[RuntimeTarget]",
    "[ReportSubmission]",
    "[OpenVikingReadableMaterials]",
    "[OpenVikingWriteTarget]",
    "profile:",
    "profile_status:",
    "worker_id:",
    "material_id",
    "capability=",
    "capability_id",
    "l1_sha256",
    "l2_available",
    "viking://",
    "OpenViking",
    "OpenClaw",
    "openviking_read_with_capability",
    "openviking_write_material",
)


def _validate_model_visible_prompt_text(
    call: WorkerCall,
    messages: list[object],
    provider_request_path: Path,
) -> GuardResult:
    if call.profile not in {"US", "CN_A"}:
        return guard_passed(category="provider_request")
    text = "\n".join(_message_text(item) for item in messages)
    for token in _FORBIDDEN_MODEL_VISIBLE_PROMPT_TOKENS:
        if token in text:
            return _failed(f"模型可见 prompt 含机器协议或运行包装: {token}", provider_request_path)
    return guard_passed(category="provider_request")


def _message_text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    content = value.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
        return "\n".join(parts)
    return ""


def _validate_runtime_marker(
    call: WorkerCall,
    evidence: ProviderEvidence,
    payload: dict[str, object],
    provider_request_path: Path,
) -> GuardResult:
    markers = payload.get("runtime_markers")
    # OpenClaw 真实 single-worker capture 写入 runtime_markers 对象；不能接受旧字符串或上层重构标记。
    if not isinstance(markers, dict):
        return _failed("runtime_markers 缺失或不是 JSON object", provider_request_path)

    missing_fields = tuple(sorted(field for field in _RUNTIME_MARKER_FIELDS if field not in markers))
    if missing_fields:
        return _failed(f"runtime_markers 字段缺失: {', '.join(missing_fields)}", provider_request_path)

    expected_fields = {
        "run_id": call.run_id,
        "call_id": call.call_id,
        "worker_id": call.worker_id,
        "stage": call.stage.value,
        "profile": call.profile,
        "openclaw_run_id": evidence.openclaw_run_id,
    }
    for field, expected in expected_fields.items():
        actual = _required_non_empty_str(markers.get(field))
        if actual is None:
            return _failed(f"runtime_markers.{field} 缺失或为空", provider_request_path)
        if actual != expected:
            return _failed(
                f"runtime_markers.{field} 与当前调用不一致: expected={expected} actual={actual}",
                provider_request_path,
            )
    return guard_passed(category="provider_request")


def _read_json_object(path: Path) -> dict[str, object] | GuardResult:
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return _failed(f"provider request 读取失败: {exc}", path)
    except json.JSONDecodeError as exc:
        return _failed(f"provider request JSON 解析失败: {exc}", path)
    if not isinstance(raw, dict):
        return _failed("provider request 必须是 JSON object", path)
    return raw


def _required_non_empty_str(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    return text


def _looks_like_message(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    role = _required_non_empty_str(value.get("role"))
    if role is None:
        return False
    return "content" in value or "tool_calls" in value


def _looks_like_tool(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    direct_name = _required_non_empty_str(value.get("name"))
    if direct_name is not None:
        return True
    function_spec = value.get("function")
    if isinstance(function_spec, dict):
        function_name = _required_non_empty_str(function_spec.get("name"))
        return function_name is not None
    return False


def _failed(reason: str, path: Path) -> GuardResult:
    return guard_failed(category="provider_request", reason=reason, paths=(path.resolve(),), early_stop=False)
