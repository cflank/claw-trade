from __future__ import annotations

from pathlib import Path
from typing import Any

from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.runtime.evidence_reader import EvidenceReader, ProviderEvidence
from claw_trade.workflow.models import WorkerCall


def validate_visible_tools(call: WorkerCall, evidence: ProviderEvidence) -> GuardResult:
    reader = EvidenceReader()
    try:
        provider_request = reader.read_provider_request(evidence.provider_request_path)
    except ValueError as exc:
        return guard_failed(
            category="visible_tools",
            reason=f"provider request 读取失败: {exc}",
            paths=(evidence.provider_request_path,),
        )
    try:
        visible_tools = reader.read_visible_tools(evidence.visible_tools_path)
    except ValueError as exc:
        return guard_failed(
            category="visible_tools",
            reason=f"visible tools 读取失败: {exc}",
            paths=(evidence.visible_tools_path,),
        )

    source = visible_tools.get("source")
    # visible_tools 必须声明来自 provider_request，防止用本地推断列表冒充“模型真实可见工具”。
    if source != "provider_request":
        return guard_failed(
            category="visible_tools",
            reason=f"visible tools source 不可信: {source}",
            paths=(evidence.visible_tools_path,),
        )

    if not _same_provider_request_source(visible_tools, evidence):
        return guard_failed(
            category="visible_tools",
            reason="visible tools provider_request_path 与本次 evidence.provider_request_path 不一致",
            paths=(evidence.visible_tools_path, evidence.provider_request_path),
        )

    provider_tool_names, provider_error = _extract_provider_tool_names(provider_request)
    if provider_error is not None:
        return guard_failed(
            category="visible_tools",
            reason=provider_error,
            paths=(evidence.provider_request_path,),
        )
    visible_tool_names, visible_error = _normalize_tool_names(visible_tools.get("tools"), "visible_tools.tools")
    if visible_error is not None:
        return guard_failed(
            category="visible_tools",
            reason=visible_error,
            paths=(evidence.visible_tools_path,),
        )

    if provider_tool_names != visible_tool_names:
        return guard_failed(
            category="visible_tools",
            reason="provider request 与 visible tools 工具名不一致，来源不可验证",
            paths=(evidence.provider_request_path, evidence.visible_tools_path),
        )

    expected_tool_names, expected_error = _normalize_tool_names(call.allowed_tools, "call.allowed_tools")
    if expected_error is not None:
        return guard_failed(
            category="visible_tools",
            reason=expected_error,
            paths=(evidence.visible_tools_path,),
        )

    # 这里必须比较“模型实际可见工具名”，不能用本地 allowlist、日志或重构文本替代 provider payload 证据。
    if visible_tool_names != expected_tool_names:
        return guard_failed(
            category="visible_tools",
            reason="visible tools 与 call.allowed_tools 不一致",
            paths=(evidence.visible_tools_path, evidence.provider_request_path),
        )
    return guard_passed(category="visible_tools")


def _same_provider_request_source(visible_tools: dict[str, object], evidence: ProviderEvidence) -> bool:
    # 必须绑定到当前 call 的 provider_request_path，避免把其它 run/call 的工具快照混入当前判定。
    raw_path = visible_tools.get("provider_request_path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return False
    expected_path = evidence.provider_request_path.resolve()
    visible_path = Path(raw_path.strip())
    if visible_path.is_absolute():
        return visible_path.resolve() == expected_path

    cwd_resolved = (Path.cwd() / visible_path).resolve()
    if cwd_resolved == expected_path:
        return True
    evidence_dir_resolved = (evidence.visible_tools_path.parent / visible_path).resolve()
    return evidence_dir_resolved == expected_path


def _extract_provider_tool_names(provider_request: dict[str, object]) -> tuple[frozenset[str], str | None]:
    payload = provider_request.get("payload")
    if not isinstance(payload, dict):
        return frozenset(), "provider request payload 缺失或格式非法"
    return _normalize_tool_names(payload.get("tools"), "provider_request.payload.tools")


def _normalize_tool_names(raw_tools: object, field_name: str) -> tuple[frozenset[str], str | None]:
    if not isinstance(raw_tools, (list, tuple)):
        return frozenset(), f"{field_name} 必须是列表"
    names: set[str] = set()
    for index, item in enumerate(raw_tools):
        name, error = _tool_name_from_entry(item)
        if error is not None:
            return frozenset(), f"{field_name}[{index}] {error}"
        if name is None or not name.strip():
            return frozenset(), f"{field_name}[{index}] 工具名为空"
        names.add(name.strip())
    if not names:
        return frozenset(), f"{field_name} 不能为空"
    return frozenset(names), None


def _tool_name_from_entry(entry: Any) -> tuple[str | None, str | None]:
    if isinstance(entry, str):
        return entry, None
    if not isinstance(entry, dict):
        return None, "工具项必须是字符串或对象"

    name = entry.get("name")
    if isinstance(name, str):
        return name, None

    function = entry.get("function")
    if isinstance(function, dict):
        function_name = function.get("name")
        if isinstance(function_name, str):
            return function_name, None
    return None, "无法提取工具 name"
