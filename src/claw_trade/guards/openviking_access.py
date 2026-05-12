from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claw_trade.artifacts.manifest import ApprovedManifest, ArtifactFlowError
from claw_trade.artifacts.refs import OpenVikingReadCapability, validate_viking_uri_shape
from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import Stage, WorkerCall

_OPENVIKING_READ_TOOL = "openviking_read_with_capability"
_OPENVIKING_WRITE_TOOL = "openviking_write_material"
_TOOL_CALL_STATUS = {"success", "error"}


def validate_openviking_runtime_reads(
    call: WorkerCall,
    evidence: ProviderEvidence,
    manifest: ApprovedManifest,
) -> GuardResult:
    # 这里审的是 OpenClaw 真实记录下来的工具调用，不审 prompt 文本或 worker 自述。
    tool_calls = _load_tool_calls(evidence.tool_calls_path)
    if isinstance(tool_calls, GuardResult):
        return tool_calls
    calls = tool_calls.get("calls")
    if not isinstance(calls, list):
        return guard_failed(
            category="openviking_runtime_reads",
            reason="tool-calls calls 必须是数组",
            paths=(evidence.tool_calls_path,),
        )

    try:
        caps = manifest.capabilities_for_worker_call(
            stage=call.stage,
            worker_id=call.worker_id,
            run_id=call.run_id,
        )
    except (ArtifactFlowError, ValueError) as exc:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"manifest capability 加载失败: {exc}",
            paths=(evidence.tool_calls_path,),
            early_stop=True,
        )
    caps_by_id = {cap.capability_id: cap for cap in caps}

    for index, item in enumerate(calls):
        if not isinstance(item, dict):
            return guard_failed(
                category="openviking_runtime_reads",
                reason=f"tool-calls calls[{index}] 必须是对象",
                paths=(evidence.tool_calls_path,),
            )
        read_guard = _validate_read_record(index, item, caps_by_id, evidence.tool_calls_path)
        if read_guard is not None:
            return read_guard
        write_guard = _validate_write_record(index, item, call, evidence.tool_calls_path)
        if write_guard is not None:
            return write_guard
    return guard_passed(category="openviking_runtime_reads")


def _validate_read_record(
    index: int,
    record: dict[str, Any],
    caps_by_id: dict[str, OpenVikingReadCapability],
    path: Path,
) -> GuardResult | None:
    tool_name = record.get("tool_name")
    action = record.get("action")
    if tool_name != _OPENVIKING_READ_TOOL:
        return None
    if action != "read":
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"OpenViking read 工具 action 非法: calls[{index}].action={action!r}",
            paths=(path,),
        )

    status = _require_non_empty_str(record, "status")
    if status is None:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"OpenViking read 记录缺字段: calls[{index}].status",
            paths=(path,),
        )
    if status not in _TOOL_CALL_STATUS:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"OpenViking read status 非法: calls[{index}].status={status!r}",
            paths=(path,),
        )

    if status == "error":
        # 失败读只要求保留错误事实，不能为了凑字段伪造 capability/uri 成功记录。
        if _require_non_empty_str(record, "error") is None:
            return guard_failed(
                category="openviking_runtime_reads",
                reason=f"OpenViking read 失败记录缺错误信息: calls[{index}].error",
                paths=(path,),
            )
        return None

    return _validate_success_read_record(index, record, caps_by_id, path)


def _validate_success_read_record(
    index: int,
    record: dict[str, Any],
    caps_by_id: dict[str, OpenVikingReadCapability],
    path: Path,
) -> GuardResult | None:
    # 成功读必须回到 manifest 授权链路里校验，避免 worker 用同名材料读到别人的内容。
    required = ("capability_id", "material_id", "uri", "result_sha256")
    values = {field: _require_non_empty_str(record, field) for field in required}
    for field, value in values.items():
        if value is None:
            return guard_failed(
                category="openviking_runtime_reads",
                reason=f"OpenViking read 记录缺字段: calls[{index}].{field}",
                paths=(path,),
            )

    capability_id = values["capability_id"]
    material_id = values["material_id"]
    uri = values["uri"]
    if capability_id is None or material_id is None or uri is None:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"OpenViking read 记录缺字段: calls[{index}]",
            paths=(path,),
        )

    cap = caps_by_id.get(capability_id)
    if cap is None:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"OpenViking read capability 不在 manifest: {capability_id}",
            paths=(path,),
        )
    if material_id != cap.material_id:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"OpenViking read material_id 与 capability 绑定不一致: {material_id} != {cap.material_id}",
            paths=(path,),
        )

    uri_guard = _validate_uri_against_capability(uri, cap)
    if uri_guard is not None:
        return uri_guard

    if uri != cap.allowed_l1_uri:
        prefix = cap.allowed_l2_prefix
        if prefix is None or not uri.startswith(prefix):
            return guard_failed(
                category="openviking_runtime_reads",
                reason=f"OpenViking L2 read URI 不在 capability 允许前缀内: {uri}",
                paths=(path,),
            )
        l2_index_sha256 = _require_non_empty_str(record, "l2_index_sha256")
        if l2_index_sha256 is None:
            return guard_failed(
                category="openviking_runtime_reads",
                reason=f"OpenViking L2 read 记录缺字段: calls[{index}].l2_index_sha256",
                paths=(path,),
            )
    return None


def _validate_write_record(
    index: int,
    record: dict[str, Any],
    call: WorkerCall,
    path: Path,
) -> GuardResult | None:
    tool_name = record.get("tool_name")
    action = record.get("action")
    if tool_name != _OPENVIKING_WRITE_TOOL:
        return None
    if action != "write":
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"OpenViking write 工具 action 非法: calls[{index}].action={action!r}",
            paths=(path,),
        )

    uri = _require_non_empty_str(record, "uri")
    status = _require_non_empty_str(record, "status")
    result_sha256 = _require_non_empty_str(record, "result_sha256")
    if uri is None or status is None or result_sha256 is None:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"OpenViking write 记录缺字段: calls[{index}]",
            paths=(path,),
        )
    if status not in _TOOL_CALL_STATUS:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"OpenViking write status 非法: calls[{index}].status={status!r}",
            paths=(path,),
        )

    # 这里必须阻断“写时伪造 material/capability”行为：写入阶段还没 approved material，不允许伪造已批准身份。
    if record.get("capability_id") not in (None, ""):
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"OpenViking write 记录禁止携带 capability_id: calls[{index}]",
            paths=(path,),
        )
    if record.get("material_id") not in (None, ""):
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"OpenViking write 记录禁止携带 material_id: calls[{index}]",
            paths=(path,),
        )
    if uri != call.material_target.l1_uri:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"OpenViking write URI 越权: {uri} != {call.material_target.l1_uri}",
            paths=(path,),
        )
    return None


def _validate_uri_against_capability(uri: str, cap: OpenVikingReadCapability) -> GuardResult | None:
    try:
        run_id, stage, worker_id, call_id = _identity_from_l1_uri(cap.allowed_l1_uri)
    except ValueError as exc:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"manifest capability L1 URI 非法: {exc}",
            paths=(),
        )
    uri_guard = validate_viking_uri_shape(
        uri=uri,
        run_id=run_id,
        stage=stage,
        worker_id=worker_id,
        call_id=call_id,
    )
    if not uri_guard.ok:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=uri_guard.reason or "OpenViking read URI 形状非法",
            paths=(),
        )
    if uri == cap.allowed_l1_uri:
        return None
    if cap.allowed_l2_prefix and uri.startswith(cap.allowed_l2_prefix):
        return None
    return guard_failed(
        category="openviking_runtime_reads",
        reason=f"OpenViking read URI 不在 manifest capability 范围: {uri}",
        paths=(),
    )


def _identity_from_l1_uri(uri: str) -> tuple[str, Stage, str, str]:
    prefix = "viking://resources/workflow/"
    if not uri.startswith(prefix):
        raise ValueError(f"非法 capability L1 URI: {uri}")
    parts = uri[len(prefix) :].split("/")
    if len(parts) < 5:
        raise ValueError(f"capability L1 URI 层级不足: {uri}")
    return (parts[0], Stage(parts[1]), parts[2], parts[3])


def _require_non_empty_str(record: dict[str, Any], field: str) -> str | None:
    value = record.get(field)
    if not isinstance(value, str) or not value.strip():
        return None
    return value


def _load_tool_calls(path: Path) -> dict[str, Any] | GuardResult:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"tool-calls JSON 解析失败: {exc}",
            paths=(path,),
        )
    except OSError as exc:
        return guard_failed(
            category="openviking_runtime_reads",
            reason=f"tool-calls 读取失败: {exc}",
            paths=(path,),
        )
    if not isinstance(payload, dict):
        return guard_failed(
            category="openviking_runtime_reads",
            reason="tool-calls 顶层必须是对象",
            paths=(path,),
        )
    return payload
