from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import WorkerCall

_TOOL_CALLS_STATUS = {"none", "recorded"}
_CALL_STATUS = {"success", "error"}
_REQUIRED_CALL_FIELDS = ("tool_name", "action", "status", "result_sha256")
_FRONTLINE_PACK_TOOLS = {
    "claw_get_market_pack",
    "claw_get_fundamental_pack",
    "claw_get_news_pack",
    "claw_get_social_pack",
    "claw_get_policy_pack",
    "claw_get_hot_money_pack",
    "claw_get_lockup_pack",
}
_CRYPTO_FRONTLINE_REQUIRED_PACK_TOOLS = {
    "market_analyst": "claw_get_market_pack",
    "fundamental_analyst": "claw_get_fundamental_pack",
    "news_analyst": "claw_get_news_pack",
    "social_analyst": "claw_get_social_pack",
}


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
        required_tool = _required_crypto_frontline_pack_tool(call)
        if required_tool is not None:
            return guard_failed(
                category="tool_calls",
                reason=f"CRYPTO frontline worker 未调用必需资料包工具: {required_tool}",
                paths=(evidence.tool_calls_path,),
            )
        return guard_passed(category="tool_calls")

    seen_tools: set[str] = set()
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
        if _is_frontline_pack_tool_failure(call, item):
            return guard_failed(
                category="tool_calls",
                reason=f"frontline 资料包工具调用失败: {item['tool_name']}",
                paths=(evidence.tool_calls_path,),
            )
        seen_tools.add(item["tool_name"].strip())
    required_tool = _required_crypto_frontline_pack_tool(call)
    if required_tool is not None and required_tool not in seen_tools:
        return guard_failed(
            category="tool_calls",
            reason=f"CRYPTO frontline worker 未调用必需资料包工具: {required_tool}",
            paths=(evidence.tool_calls_path,),
        )
    return guard_passed(category="tool_calls")


def _is_frontline_pack_tool_failure(call: WorkerCall, item: dict[str, Any]) -> bool:
    # Guard source: AGENTS Truthfulness Hard Gates; 2026-06-01 human request to stop
    # treating failed data-layer/tool calls as successful report evidence.
    if call.stage.value != "frontline":
        return False
    tool_name = str(item.get("tool_name") or "").strip()
    return tool_name in _FRONTLINE_PACK_TOOLS and item.get("status") == "error"


def _required_crypto_frontline_pack_tool(call: WorkerCall) -> str | None:
    # Guard source: 2026-05-16 user CRYPTO authenticity request; AGENTS Truthfulness Hard Gates
    # require missing tool data to fail visibly instead of passing as fake/silent success.
    if call.profile != "CRYPTO" or call.stage.value != "frontline":
        return None
    tool_name = _CRYPTO_FRONTLINE_REQUIRED_PACK_TOOLS.get(call.worker_id)
    if tool_name is None or tool_name not in call.allowed_tools:
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
