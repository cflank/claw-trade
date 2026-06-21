from __future__ import annotations

import json
from pathlib import Path
from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import WorkerCall

_TOOL_CALLS_STATUS = {"none", "recorded"}
_CALL_STATUS = {"success", "error"}
_REQUIRED_CALL_FIELDS = ("tool_name", "action", "status", "result_sha256")
_CRYPTO_FRONTLINE_REQUIRED_DATA_TOOL = "claw_request_data"


def validate_tool_calls(call: WorkerCall, evidence: ProviderEvidence) -> GuardResult:
    tool_calls = _load_tool_calls(evidence.tool_calls_path)
    if isinstance(tool_calls, GuardResult):
        return tool_calls

    source = tool_calls.get("source")
    # tool-calls 必须直接来自模型工具事件；不能用 Python/日志重放结果替代真实调用轨迹。
    if source != "model_tool_events":
        return guard_failed(
            category="tool_calls",
            reason=f"tool-calls source 非法: {source!r}",
            paths=(evidence.tool_calls_path,),
        )

    status = tool_calls.get("status")
    if status not in _TOOL_CALLS_STATUS:
        return guard_failed(
            category="tool_calls",
            reason=f"tool-calls status 非法: {status!r}",
            paths=(evidence.tool_calls_path,),
        )
    # 运行结果字段和审计文件必须一致，防止把别的 call 的文件误判为当前成功证据。
    if evidence.tool_calls_status not in _TOOL_CALLS_STATUS:
        return guard_failed(
            category="tool_calls",
            reason=f"evidence.tool_calls_status 非法: {evidence.tool_calls_status!r}",
            paths=(evidence.tool_calls_path,),
        )
    if evidence.tool_calls_status != status:
        return guard_failed(
            category="tool_calls",
            reason=f"tool-calls status 与 evidence 不一致: {status!r}/{evidence.tool_calls_status!r}",
            paths=(evidence.tool_calls_path,),
        )

    identity_guard = _validate_identity(call, evidence, tool_calls)
    if identity_guard is not None:
        return identity_guard

    calls = tool_calls.get("calls")
    if not isinstance(calls, list):
        return guard_failed(
            category="tool_calls",
            reason="tool-calls calls 必须是数组",
            paths=(evidence.tool_calls_path,),
        )
    if status == "none":
        if calls:
            return guard_failed(
                category="tool_calls",
                reason="tool-calls status=none 时 calls 必须为空",
                paths=(evidence.tool_calls_path,),
            )
        required_tool = _required_crypto_frontline_data_tool(call)
        if required_tool is not None:
            return guard_failed(
                category="tool_calls",
                reason=f"CRYPTO frontline worker 未调用必需数据工具: {required_tool}",
                paths=(evidence.tool_calls_path,),
            )
        return guard_passed(category="tool_calls")

    seen_tools: set[str] = set()
    required_tool_error = False
    for index, item in enumerate(calls):
        if not isinstance(item, dict):
            return guard_failed(
                category="tool_calls",
                reason=f"tool-calls calls[{index}] 必须是对象",
                paths=(evidence.tool_calls_path,),
            )
        for field in _REQUIRED_CALL_FIELDS:
            value = item.get(field)
            if not isinstance(value, str) or not value.strip():
                return guard_failed(
                    category="tool_calls",
                    reason=f"tool-calls calls[{index}].{field} 缺失或为空",
                    paths=(evidence.tool_calls_path,),
                )
        if item.get("status") not in _CALL_STATUS:
            return guard_failed(
                category="tool_calls",
                reason=f"tool-calls calls[{index}].status 非法: {item.get('status')!r}",
                paths=(evidence.tool_calls_path,),
            )
        tool_name = item["tool_name"].strip()
        seen_tools.add(tool_name)
        if tool_name == _required_crypto_frontline_data_tool(call) and item.get("status") == "error":
            required_tool_error = True
    required_tool = _required_crypto_frontline_data_tool(call)
    if required_tool is not None and required_tool not in seen_tools:
        return guard_failed(
            category="tool_calls",
            reason=f"CRYPTO frontline worker 未调用必需数据工具: {required_tool}",
            paths=(evidence.tool_calls_path,),
        )
    if required_tool is not None and required_tool_error:
        # Guard source: 2026-06-20 user request after BTC report produced empty evidence
        # from data_need_runtime_blocked; required data-tool errors must fail visibly.
        return guard_failed(
            category="tool_calls",
            reason=f"CRYPTO frontline worker 必需数据工具调用失败: {required_tool}",
            paths=(evidence.tool_calls_path,),
        )
    return guard_passed(category="tool_calls")

def _required_crypto_frontline_data_tool(call: WorkerCall) -> str | None:
    # Guard source: 2026-05-16 user CRYPTO authenticity request; AGENTS Truthfulness Hard Gates
    # require missing tool data to fail visibly instead of passing as fake/silent success.
    if call.profile != "CRYPTO" or call.stage.value != "frontline":
        return None
    if call.worker_id not in {"market_analyst", "fundamental_analyst", "news_analyst", "social_analyst"}:
        return None
    tool_name = _CRYPTO_FRONTLINE_REQUIRED_DATA_TOOL
    if tool_name not in call.allowed_tools:
        return None
    return tool_name


def _validate_identity(
    call: WorkerCall,
    evidence: ProviderEvidence,
    payload: dict[str, Any],
) -> GuardResult | None:
    # run/call/worker/stage/openclaw_run_id 一起校验，防止跨阶段或跨 worker 的调用记录被误当作当前证据。
    checks = (
        ("run_id", call.run_id),
        ("call_id", call.call_id),
        ("worker_id", call.worker_id),
        ("stage", call.stage.value),
        ("openclaw_run_id", evidence.openclaw_run_id),
    )
    for field, expected in checks:
        actual = payload.get(field)
        if actual != expected:
            return guard_failed(
                category="tool_calls",
                reason=f"tool-calls {field} 不匹配: {actual!r} != {expected!r}",
                paths=(evidence.tool_calls_path,),
            )
    return None


def _load_tool_calls(path: Path) -> dict[str, Any] | GuardResult:
    try:
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return guard_failed(
            category="tool_calls",
            reason=f"tool-calls JSON 解析失败: {exc}",
            paths=(path,),
        )
    except OSError as exc:
        return guard_failed(
            category="tool_calls",
            reason=f"tool-calls 读取失败: {exc}",
            paths=(path,),
        )
    if not isinstance(payload, dict):
        return guard_failed(
            category="tool_calls",
            reason="tool-calls 顶层必须是对象",
            paths=(path,),
        )
    return payload
