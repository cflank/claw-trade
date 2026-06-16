from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claw_trade.selection.models import SelectionWorkerDispatch, SelectionWorkerId

_SELECTION_DISPATCH_COMMAND_FILE = "selection-dispatch-command.json"
_FORBIDDEN_PROTOCOL_TERMS = (
    "raw/debug",
    "provider envelope",
    "mongo",
    "openviking",
    "viking://",
    "material_id",
    "manifest",
    "lineage",
    "sha256",
    "hash",
    "refs",
    "runtime wrapper",
)
_CANDIDATE_CACHE_BODY_MARKERS = (
    "# A股候选缓存",
    "# 候选池数据缓存",
    "## 本轮范围",
    "## 候选事实表",
    "| 排名 | 股票代码 | 股票名称",
    "| 排名 | 代码 | 公司",
)
_CANDIDATE_CACHE_TOOL_REQUIRED_LABELS = (
    "总分",
    "分项得分",
    "策略来源",
    "策略变体",
    "命中字段",
    "实际指标值",
    "风险扣分",
    "数据缺口扣分",
    "tie-break",
    "权重版本",
    "策略配置版本",
)
_SKEPTIC_PROMPT_MATERIAL_MARKERS = ("【approved_strategist_l1】",)
_MANAGER_PROMPT_MATERIAL_MARKERS = (
    "【approved_strategist_l1】",
    "【approved_skeptic_l1】",
    "【candidate_cache_summary】",
)
_PORTFOLIO_MANAGER_PROMPT_MATERIAL_MARKERS = (
    "【approved_manager_l1】",
    "【approved_strategist_l1】",
    "【approved_skeptic_l1】",
    "【candidate_cache_summary】",
)


@dataclass(frozen=True)
class SelectionDispatchEvidence:
    dispatch: SelectionWorkerDispatch
    provider_request_path: Path
    visible_tools_path: Path
    command_snapshot_path: Path
    provider_requests_jsonl_path: Path | None = None


@dataclass(frozen=True)
class SelectionEvidenceValidationResult:
    ok: bool
    category: str
    reason: str | None = None
    paths: tuple[Path, ...] = ()


def _validation_passed(category: str) -> SelectionEvidenceValidationResult:
    return SelectionEvidenceValidationResult(ok=True, category=category)


def _validation_failed(
    *,
    category: str,
    reason: str,
    paths: tuple[Path, ...],
) -> SelectionEvidenceValidationResult:
    return SelectionEvidenceValidationResult(
        ok=False,
        category=category,
        reason=reason,
        paths=paths,
    )


def evidence_from_dispatch(dispatch: SelectionWorkerDispatch) -> SelectionDispatchEvidence:
    return SelectionDispatchEvidence(
        dispatch=dispatch,
        provider_request_path=dispatch.evidence_dir / "provider-request.json",
        visible_tools_path=dispatch.evidence_dir / "visible-tools.json",
        command_snapshot_path=dispatch.evidence_dir / _SELECTION_DISPATCH_COMMAND_FILE,
        provider_requests_jsonl_path=dispatch.evidence_dir / "provider-requests.jsonl",
    )


def validate_selection_dispatch_evidence(evidence: SelectionDispatchEvidence) -> SelectionEvidenceValidationResult:
    command_payload = _read_json_object(
        evidence.command_snapshot_path,
        category="selection_provider_payload",
        label="dispatch command snapshot",
    )
    if isinstance(command_payload, SelectionEvidenceValidationResult):
        return command_payload

    command_result = _validate_dispatch_command_snapshot(
        evidence.dispatch,
        command_payload,
        evidence.command_snapshot_path,
    )
    if not command_result.ok:
        return command_result

    provider_request = _read_json_object(
        evidence.provider_request_path,
        category="selection_provider_payload",
        label="provider request",
    )
    if isinstance(provider_request, SelectionEvidenceValidationResult):
        return provider_request

    visible_tools = _read_json_object(
        evidence.visible_tools_path,
        category="selection_provider_payload",
        label="visible tools",
    )
    if isinstance(visible_tools, SelectionEvidenceValidationResult):
        return visible_tools

    binding_result = _validate_runtime_markers(
        dispatch=evidence.dispatch,
        provider_request=provider_request,
        visible_tools=visible_tools,
        provider_request_path=evidence.provider_request_path,
        visible_tools_path=evidence.visible_tools_path,
    )
    if not binding_result.ok:
        return binding_result

    tools_result = _validate_tools_matrix(
        dispatch=evidence.dispatch,
        provider_request=provider_request,
        visible_tools=visible_tools,
        provider_request_path=evidence.provider_request_path,
        visible_tools_path=evidence.visible_tools_path,
    )
    if not tools_result.ok:
        return tools_result

    boundary_result = _validate_material_boundary(
        dispatch=evidence.dispatch,
        provider_request=provider_request,
        provider_request_path=evidence.provider_request_path,
    )
    if not boundary_result.ok:
        return boundary_result

    sequence_result = _validate_provider_request_sequence_boundary(evidence)
    if not sequence_result.ok:
        return sequence_result

    return _validation_passed("selection_provider_payload")


def _validate_dispatch_command_snapshot(
    dispatch: SelectionWorkerDispatch,
    payload: dict[str, object],
    path: Path,
) -> SelectionEvidenceValidationResult:
    required_fields = {
        "run_id": dispatch.select_workflow_run_id,
        "call_id": dispatch.dispatch_id,
        "worker_id": dispatch.worker_id.value,
        "stage": dispatch.stage.value,
        "system_context_policy": "single_worker_minimal",
    }
    for field, expected in required_fields.items():
        value = payload.get(field)
        if not isinstance(value, str) or not value.strip():
            return _validation_failed(
                category="selection_provider_payload",
                reason=f"{field} missing in selection dispatch command snapshot",
                paths=(path,),
            )
        if value.strip() != expected:
            return _validation_failed(
                category="selection_provider_payload",
                reason=f"{field} mismatch in selection dispatch command snapshot",
                paths=(path,),
            )
    return _validation_passed("selection_provider_payload")


def _validate_runtime_markers(
    *,
    dispatch: SelectionWorkerDispatch,
    provider_request: dict[str, object],
    visible_tools: dict[str, object],
    provider_request_path: Path,
    visible_tools_path: Path,
) -> SelectionEvidenceValidationResult:
    if provider_request.get("source") != "provider_request_capture":
        return _validation_failed(
            category="selection_provider_payload",
            reason="provider request source must be provider_request_capture",
            paths=(provider_request_path,),
        )
    if visible_tools.get("source") != "provider_request":
        return _validation_failed(
            category="selection_provider_payload",
            reason="visible tools source must be provider_request",
            paths=(visible_tools_path,),
        )
    visible_provider_request_path = visible_tools.get("provider_request_path")
    if not isinstance(visible_provider_request_path, str) or not visible_provider_request_path.strip():
        return _validation_failed(
            category="selection_provider_payload",
            reason="visible tools provider_request_path missing",
            paths=(visible_tools_path,),
        )
    if Path(visible_provider_request_path).resolve() != provider_request_path.resolve():
        return _validation_failed(
            category="selection_provider_payload",
            reason="visible tools provider_request_path mismatch",
            paths=(provider_request_path, visible_tools_path),
        )

    provider_markers = provider_request.get("runtime_markers")
    if not isinstance(provider_markers, dict):
        return _validation_failed(
            category="selection_provider_payload",
            reason="provider request runtime_markers missing or invalid",
            paths=(provider_request_path,),
        )
    visible_markers = visible_tools.get("runtime_markers")
    if not isinstance(visible_markers, dict):
        return _validation_failed(
            category="selection_provider_payload",
            reason="visible tools runtime_markers missing or invalid",
            paths=(visible_tools_path,),
        )

    expected_fields = {
        "run_id": dispatch.select_workflow_run_id,
        "call_id": dispatch.dispatch_id,
        "worker_id": dispatch.worker_id.value,
        "stage": dispatch.stage.value,
    }
    for field, expected in expected_fields.items():
        provider_value = provider_markers.get(field)
        if provider_value != expected:
            return _validation_failed(
                category="selection_provider_payload",
                reason=f"provider request runtime_markers.{field} mismatch",
                paths=(provider_request_path,),
            )
        visible_value = visible_markers.get(field)
        if visible_value != expected:
            return _validation_failed(
                category="selection_provider_payload",
                reason=f"visible tools runtime_markers.{field} mismatch",
                paths=(visible_tools_path,),
            )
    return _validation_passed("selection_provider_payload")


def _validate_tools_matrix(
    *,
    dispatch: SelectionWorkerDispatch,
    provider_request: dict[str, object],
    visible_tools: dict[str, object],
    provider_request_path: Path,
    visible_tools_path: Path,
) -> SelectionEvidenceValidationResult:
    payload = provider_request.get("payload")
    if not isinstance(payload, dict):
        return _validation_failed(
            category="selection_provider_payload",
            reason="provider request payload missing",
            paths=(provider_request_path,),
        )
    provider_tools, provider_error = _extract_tool_names(payload.get("tools"))
    if provider_error is not None:
        return _validation_failed(
            category="selection_provider_payload",
            reason=f"provider request tools invalid: {provider_error}",
            paths=(provider_request_path,),
        )
    visible_tools_names, visible_error = _extract_tool_names(visible_tools.get("tools"))
    if visible_error is not None:
        return _validation_failed(
            category="selection_provider_payload",
            reason=f"visible tools invalid: {visible_error}",
            paths=(visible_tools_path,),
        )
    expected = frozenset(dispatch.allowed_tools)
    if provider_tools != visible_tools_names:
        return _validation_failed(
            category="selection_provider_payload",
            reason="provider request tools and visible tools are not identical",
            paths=(provider_request_path, visible_tools_path),
        )
    if provider_tools != expected:
        return _validation_failed(
            category="selection_provider_payload",
            reason="visible tools do not match fixed selection worker matrix",
            paths=(provider_request_path, visible_tools_path),
        )
    return _validation_passed("selection_provider_payload")


def _validate_material_boundary(
    *,
    dispatch: SelectionWorkerDispatch,
    provider_request: dict[str, object],
    provider_request_path: Path,
) -> SelectionEvidenceValidationResult:
    payload = provider_request.get("payload")
    if not isinstance(payload, dict):
        return _validation_failed(
            category="selection_provider_payload",
            reason="provider request payload missing",
            paths=(provider_request_path,),
        )
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        return _validation_failed(
            category="selection_provider_payload",
            reason="provider request payload.messages must be non-empty",
            paths=(provider_request_path,),
        )
    text = "\n".join(_flatten_message_text(messages))
    normalized_text = _normalize_whitespace(text)
    if dispatch.worker_id == SelectionWorkerId.STRATEGIST:
        for marker in _CANDIDATE_CACHE_BODY_MARKERS:
            if marker in text:
                return _validation_failed(
                    category="selection_provider_payload",
                    reason="strategist/skeptic first prompt preloaded full candidate cache body",
                    paths=(provider_request_path,),
                )
        return _validation_passed("selection_provider_payload")

    if dispatch.worker_id == SelectionWorkerId.SKEPTIC:
        for marker in _SKEPTIC_PROMPT_MATERIAL_MARKERS:
            if marker not in text:
                return _validation_failed(
                    category="selection_provider_payload",
                    reason=f"skeptic payload missing required material marker: {marker}",
                    paths=(provider_request_path,),
                )
        for material in dispatch.model_visible_materials:
            snippet = _material_snippet(material)
            if snippet and snippet not in normalized_text:
                return _validation_failed(
                    category="selection_provider_payload",
                    reason="skeptic payload missing required approved material body",
                    paths=(provider_request_path,),
                )
        return _validation_passed("selection_provider_payload")

    for forbidden in _FORBIDDEN_PROTOCOL_TERMS:
        if forbidden.lower() in text.lower():
            return _validation_failed(
                category="selection_provider_payload",
                reason=f"manager/pm payload contains forbidden protocol text: {forbidden}",
                paths=(provider_request_path,),
            )

    required_markers = _required_prompt_markers(dispatch.worker_id)
    for marker in required_markers:
        if marker not in text:
            return _validation_failed(
                category="selection_provider_payload",
                reason=f"manager/pm payload missing required material marker: {marker}",
                paths=(provider_request_path,),
            )

    for material in dispatch.model_visible_materials:
        snippet = _material_snippet(material)
        if snippet and snippet not in normalized_text:
            return _validation_failed(
                category="selection_provider_payload",
                reason="manager/pm payload missing required approved material body",
                paths=(provider_request_path,),
            )
    return _validation_passed("selection_provider_payload")


def _required_prompt_markers(worker_id: SelectionWorkerId) -> tuple[str, ...]:
    if worker_id == SelectionWorkerId.MANAGER:
        return _MANAGER_PROMPT_MATERIAL_MARKERS
    if worker_id == SelectionWorkerId.PORTFOLIO_MANAGER:
        return _PORTFOLIO_MANAGER_PROMPT_MATERIAL_MARKERS
    return ()


def _validate_provider_request_sequence_boundary(evidence: SelectionDispatchEvidence) -> SelectionEvidenceValidationResult:
    path = evidence.provider_requests_jsonl_path
    if path is None or not path.exists():
        return _validation_passed("selection_provider_payload")
    entries = _read_provider_request_sequence(path)
    if isinstance(entries, SelectionEvidenceValidationResult):
        return entries
    saw_tool_result = False
    for entry in entries:
        marker_result = _validate_sequence_runtime_markers(
            dispatch=evidence.dispatch,
            entry=entry,
            path=path,
        )
        if not marker_result.ok:
            return marker_result
        payload = entry.get("payload")
        if not isinstance(payload, dict):
            return _validation_failed(
                category="selection_provider_payload",
                reason="provider request sequence payload missing",
                paths=(path,),
            )
        messages = payload.get("messages")
        if not isinstance(messages, list) or not messages:
            return _validation_failed(
                category="selection_provider_payload",
                reason="provider request sequence payload.messages must be non-empty",
                paths=(path,),
            )
        text = "\n".join(_flatten_message_text(messages))
        if evidence.dispatch.worker_id == SelectionWorkerId.STRATEGIST and _sequence_number(entry) == 1:
            for marker in _CANDIDATE_CACHE_BODY_MARKERS:
                if marker in text:
                    return _validation_failed(
                        category="selection_provider_payload",
                        reason="strategist first sequence preloaded full candidate cache body",
                        paths=(path,),
                    )
        if evidence.dispatch.worker_id in {SelectionWorkerId.MANAGER, SelectionWorkerId.PORTFOLIO_MANAGER}:
            for forbidden in _FORBIDDEN_PROTOCOL_TERMS:
                if forbidden.lower() in text.lower():
                    return _validation_failed(
                        category="selection_provider_payload",
                        reason=f"manager/pm provider sequence contains forbidden protocol text: {forbidden}",
                        paths=(path,),
                    )
        if evidence.dispatch.worker_id in {SelectionWorkerId.STRATEGIST, SelectionWorkerId.SKEPTIC}:
            for tool_text in _candidate_cache_tool_result_texts(messages):
                saw_tool_result = True
                tool_result = _validate_candidate_cache_tool_result_text(tool_text, path=path)
                if not tool_result.ok:
                    return tool_result
    if evidence.dispatch.worker_id in {SelectionWorkerId.STRATEGIST, SelectionWorkerId.SKEPTIC} and len(entries) > 1 and not saw_tool_result:
        return _validation_failed(
            category="selection_provider_payload",
            reason="strategist/skeptic provider sequence missing candidate cache tool result",
            paths=(path,),
        )
    return _validation_passed("selection_provider_payload")


def _read_provider_request_sequence(path: Path) -> tuple[dict[str, object], ...] | SelectionEvidenceValidationResult:
    entries: list[dict[str, object]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        return _validation_failed(
            category="selection_provider_payload",
            reason=f"provider request sequence read failed: {exc}",
            paths=(path,),
        )
    for index, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            payload: Any = json.loads(line)
        except json.JSONDecodeError as exc:
            return _validation_failed(
                category="selection_provider_payload",
                reason=f"provider request sequence line {index} json parse failed: {exc}",
                paths=(path,),
            )
        if not isinstance(payload, dict):
            return _validation_failed(
                category="selection_provider_payload",
                reason=f"provider request sequence line {index} must be JSON object",
                paths=(path,),
            )
        entries.append(payload)
    if not entries:
        return _validation_failed(
            category="selection_provider_payload",
            reason="provider request sequence is empty",
            paths=(path,),
        )
    return tuple(entries)


def _validate_sequence_runtime_markers(
    *,
    dispatch: SelectionWorkerDispatch,
    entry: dict[str, object],
    path: Path,
) -> SelectionEvidenceValidationResult:
    markers = entry.get("runtime_markers")
    if not isinstance(markers, dict):
        return _validation_failed(
            category="selection_provider_payload",
            reason="provider request sequence runtime_markers missing or invalid",
            paths=(path,),
        )
    expected_fields = {
        "run_id": dispatch.select_workflow_run_id,
        "call_id": dispatch.dispatch_id,
        "worker_id": dispatch.worker_id.value,
        "stage": dispatch.stage.value,
    }
    for field, expected in expected_fields.items():
        if markers.get(field) != expected:
            return _validation_failed(
                category="selection_provider_payload",
                reason=f"provider request sequence runtime_markers.{field} mismatch",
                paths=(path,),
            )
    return _validation_passed("selection_provider_payload")


def _sequence_number(entry: dict[str, object]) -> int | None:
    raw = entry.get("sequence")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, float):
        return int(raw)
    return None


def _candidate_cache_tool_result_texts(messages: list[object]) -> tuple[str, ...]:
    texts: list[str] = []
    for message in messages:
        if not isinstance(message, dict):
            continue
        role = message.get("role")
        content_text = "\n".join(_flatten_text_value(message.get("content")))
        if role == "tool" and _contains_candidate_cache_body_marker(content_text):
            texts.append(content_text)
    return tuple(texts)


def _contains_candidate_cache_body_marker(text: str) -> bool:
    return any(marker in text for marker in _CANDIDATE_CACHE_BODY_MARKERS)


def _validate_candidate_cache_tool_result_text(text: str, *, path: Path) -> SelectionEvidenceValidationResult:
    lowered = text.lower()
    for forbidden in _FORBIDDEN_PROTOCOL_TERMS:
        if forbidden.lower() in lowered:
            return _validation_failed(
                category="selection_provider_payload",
                reason=f"candidate cache tool result contains forbidden protocol text: {forbidden}",
                paths=(path,),
            )
    for label in _CANDIDATE_CACHE_TOOL_REQUIRED_LABELS:
        if label not in text:
            return _validation_failed(
                category="selection_provider_payload",
                reason=f"candidate cache tool result missing required model-visible field: {label}",
                paths=(path,),
            )
    return _validation_passed("selection_provider_payload")


def _material_snippet(material: str) -> str:
    normalized = _normalize_whitespace(material)
    if not normalized:
        return ""
    return normalized[:80]


def _normalize_whitespace(text: str) -> str:
    return " ".join(text.split())


def _extract_tool_names(raw_tools: object) -> tuple[frozenset[str], str | None]:
    if raw_tools is None:
        return frozenset(), None
    if not isinstance(raw_tools, list):
        return frozenset(), "tools must be a list"
    names: set[str] = set()
    for index, tool in enumerate(raw_tools):
        name = _tool_name(tool)
        if name is None:
            return frozenset(), f"tools[{index}] missing name"
        names.add(name)
    return frozenset(names), None


def _tool_name(tool: Any) -> str | None:
    if isinstance(tool, str):
        value = tool.strip()
        return value or None
    if isinstance(tool, dict):
        direct = tool.get("name")
        if isinstance(direct, str) and direct.strip():
            return direct.strip()
        function = tool.get("function")
        if isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    return None


def _flatten_message_text(messages: list[object]) -> tuple[str, ...]:
    chunks: list[str] = []
    for message in messages:
        chunks.extend(_flatten_text_value(message))
    return tuple(chunks)


def _flatten_text_value(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_flatten_text_value(item))
        return tuple(parts)
    if isinstance(value, dict):
        if "content" in value:
            return _flatten_text_value(value.get("content"))
        text = value.get("text")
        if isinstance(text, str):
            return (text,)
        tool_calls = value.get("tool_calls")
        if tool_calls is not None:
            return _flatten_text_value(tool_calls)
    return ()


def _read_json_object(
    path: Path,
    *,
    category: str,
    label: str,
) -> dict[str, object] | SelectionEvidenceValidationResult:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        return _validation_failed(
            category=category,
            reason=f"{label} read failed: {exc}",
            paths=(path,),
        )
    except json.JSONDecodeError as exc:
        return _validation_failed(
            category=category,
            reason=f"{label} json parse failed: {exc}",
            paths=(path,),
        )
    if not isinstance(payload, dict):
        return _validation_failed(
            category=category,
            reason=f"{label} must be JSON object",
            paths=(path,),
        )
    return payload
