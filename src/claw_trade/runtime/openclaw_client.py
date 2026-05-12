from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from claw_trade.artifacts.refs import MaterialReadRef, MaterialTarget, OpenVikingReadCapability
from claw_trade.guards.common import GuardResult
from claw_trade.runtime.evidence_reader import OpenClawResult
from claw_trade.workflow.models import OpenClawCommand, ReadPolicy, Stage, WorkerCall


@dataclass(frozen=True)
class ProbeResult:
    ok: bool
    reason: str | None = None

    @classmethod
    def passed(cls) -> ProbeResult:
        return cls(ok=True, reason=None)

    @classmethod
    def failed(cls, reason: str) -> ProbeResult:
        return cls(ok=False, reason=reason)


class OpenClawRunner(Protocol):
    def probe(self) -> ProbeResult: ...

    def run_worker(self, payload: dict[str, object]) -> dict[str, object]: ...


class OpenClawClient:
    def __init__(self, runner: OpenClawRunner) -> None:
        self._runner = runner

    def probe(self) -> ProbeResult:
        try:
            return self._runner.probe()
        except Exception as exc:
            return ProbeResult.failed(f"openclaw probe 失败: {exc}")

    def run_worker(self, command: OpenClawCommand) -> OpenClawResult:
        # 这里只有“叫醒一个 OpenClaw worker 并拿回结果”的职责，不承接 12-worker DAG。
        try:
            payload = self._runner.run_worker(serialize_openclaw_command_payload(command))
        except Exception as exc:
            return _failed_openclaw_result(f"openclaw 运行失败: {exc}")
        try:
            result = parse_openclaw_result(payload)
        except Exception as exc:
            # 结果不可解析时必须失败，不能靠本地重构字段“补齐成功结果”。
            return _failed_openclaw_result(f"openclaw 结果解析失败: {exc}")

        shape = _validate_openclaw_result_shape_internal(
            result=result,
            run_id=command.run_id,
            call_id=command.call_id,
            worker_id=command.worker_id,
            stage=command.stage,
            evidence_dir=command.evidence_dir,
            full_run=not command.stop_after_first_response,
        )
        if not shape.ok:
            return _failed_openclaw_result(shape.reason or "openclaw 结果结构校验失败")
        return result


def serialize_read_policy(policy: ReadPolicy) -> dict[str, object]:
    if policy.default_layer is None:
        raise ValueError("read_policy.default_layer 不能为空")
    if policy.allow_l2_when is None:
        raise ValueError("read_policy.allow_l2_when 不能为空")
    if policy.forbid_compact_as_writing_source is None:
        raise ValueError("read_policy.forbid_compact_as_writing_source 不能为空")
    return {
        "default_layer": _required_str(policy.default_layer, "read_policy.default_layer"),
        "allow_l2_when": list(policy.allow_l2_when),
        "forbid_compact_as_writing_source": policy.forbid_compact_as_writing_source,
    }


def build_openclaw_command(call: WorkerCall) -> OpenClawCommand:
    if call.worker_id is None:
        raise ValueError("worker_id 不能为空")
    if call.stage is None:
        raise ValueError("stage 不能为空")
    runtime_vars = {
        "ticker": _required_str(call.ticker, "runtime_vars.ticker"),
        "company_name": _required_str(call.company_name, "runtime_vars.company_name"),
        "market": _required_str(call.market, "runtime_vars.market"),
        "currency": _required_str(call.currency, "runtime_vars.currency"),
        "currency_symbol": _required_str(call.currency_symbol, "runtime_vars.currency_symbol"),
        "current_date": _required_str(call.current_date, "runtime_vars.current_date"),
        "start_date": _required_str(call.start_date, "runtime_vars.start_date"),
        "end_date": _required_str(call.end_date, "runtime_vars.end_date"),
        **_required_runtime_var_map(call.prompt_runtime_vars),
    }
    # 控制权边界：这里只做字段翻译，不新增任何业务分析内容。
    command = OpenClawCommand(
        agent=_required_str(call.worker_id, "agent"),
        worker_id=_required_str(call.worker_id, "worker_id"),
        profile=_required_str(call.profile, "profile"),
        stage=_required_stage_value(call.stage, "stage"),
        run_id=_required_str(call.run_id, "run_id"),
        call_id=_required_str(call.call_id, "call_id"),
        runtime_vars=runtime_vars,
        allowed_tools=call.allowed_tools,
        upstream_materials=tuple(_serialize_material_ref(item) for item in call.upstream_materials),
        openviking_read_capabilities=tuple(
            _serialize_read_capability(item) for item in call.openviking_read_capabilities
        ),
        material_target=_serialize_material_target(call.material_target),
        read_policy=serialize_read_policy(call.read_policy),
        evidence_dir=call.evidence_dir,
        stop_after_first_response=call.stop_after_first_response,
        system_context_policy=_required_str(call.system_context_policy, "system_context_policy"),
    )
    if command.agent != command.worker_id:
        raise ValueError("OpenClawCommand.agent 必须等于 worker_id")
    return command


def _required_runtime_var_map(values: dict[str, str]) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, value in values.items():
        runtime_key = _required_str(key, "runtime_vars.key")
        if not isinstance(value, str):
            raise TypeError(f"runtime_vars.{runtime_key} 必须是 str")
        out[runtime_key] = value
    return out


def serialize_openclaw_command_payload(command: OpenClawCommand) -> dict[str, object]:
    return {
        "agent": command.agent,
        "worker_id": command.worker_id,
        "profile": command.profile,
        "stage": command.stage,
        "run_id": command.run_id,
        "call_id": command.call_id,
        "runtime_vars": dict(command.runtime_vars),
        "allowed_tools": list(command.allowed_tools),
        "upstream_materials": list(command.upstream_materials),
        "openviking_read_capabilities": list(command.openviking_read_capabilities),
        "material_target": dict(command.material_target),
        "read_policy": dict(command.read_policy),
        "evidence_dir": str(command.evidence_dir),
        "stop_after_first_response": command.stop_after_first_response,
        "system_context_policy": command.system_context_policy,
    }


def parse_openclaw_result(payload: dict[str, object]) -> OpenClawResult:
    if not isinstance(payload, dict):
        raise TypeError("openclaw payload 必须是 dict")
    return OpenClawResult(
        status=_required_str(payload.get("status"), "status"),
        openclaw_run_id=_optional_str(payload.get("openclaw_run_id"), "openclaw_run_id"),
        provider_request_id=_optional_str(payload.get("provider_request_id"), "provider_request_id"),
        provider_request_id_status=_optional_str(
            payload.get("provider_request_id_status"),
            "provider_request_id_status",
        ),
        workspace_evidence_path=_optional_path(payload.get("workspace_evidence_path"), "workspace_evidence_path"),
        provider_request_path=_optional_path(payload.get("provider_request_path"), "provider_request_path"),
        visible_tools_path=_optional_path(payload.get("visible_tools_path"), "visible_tools_path"),
        first_response_path=_optional_path(payload.get("first_response_path"), "first_response_path"),
        tool_calls_status=_optional_str(payload.get("tool_calls_status"), "tool_calls_status"),
        tool_calls_path=_optional_path(payload.get("tool_calls_path"), "tool_calls_path"),
        raw_output_path=_optional_path(payload.get("raw_output_path"), "raw_output_path"),
        openviking_receipt_path=_optional_path(payload.get("openviking_receipt_path"), "openviking_receipt_path"),
        failure_reason=_optional_str(payload.get("failure_reason"), "failure_reason"),
    )


def validate_openclaw_result_shape(result: OpenClawResult, call: WorkerCall) -> GuardResult:
    return _validate_openclaw_result_shape_internal(
        result=result,
        run_id=call.run_id,
        call_id=call.call_id,
        worker_id=call.worker_id,
        stage=call.stage.value,
        evidence_dir=call.evidence_dir,
        full_run=not call.stop_after_first_response,
    )


def _validate_openclaw_result_shape_internal(
    *,
    result: OpenClawResult,
    run_id: str,
    call_id: str,
    worker_id: str,
    stage: str,
    evidence_dir: Path,
    full_run: bool,
) -> GuardResult:
    status = result.status.strip().lower()
    if status not in {"succeeded", "failed"}:
        return GuardResult.failed(
            category="openclaw_result_shape",
            reason=f"status 非法: {result.status}",
            paths=(),
        )
    if status == "failed":
        if not (result.failure_reason and result.failure_reason.strip()):
            return GuardResult.failed(
                category="openclaw_result_shape",
                reason="status=failed 时 failure_reason 不能为空",
                paths=(),
            )
        return GuardResult.passed("openclaw_result_shape")

    required_text = (
        ("openclaw_run_id", result.openclaw_run_id),
        ("provider_request_id_status", result.provider_request_id_status),
        ("tool_calls_status", result.tool_calls_status),
    )
    for field_name, value in required_text:
        if value is None or not value.strip():
            return GuardResult.failed(
                category="openclaw_result_shape",
                reason=f"{field_name} 缺失",
                paths=(),
            )

    # 这里是运行证据硬边界：成功结果必须给出本回合证据文件，防止把日志或重构文本冒充真实证据。
    required_paths = (
        ("workspace_evidence_path", result.workspace_evidence_path),
        ("provider_request_path", result.provider_request_path),
        ("visible_tools_path", result.visible_tools_path),
        ("first_response_path", result.first_response_path),
        ("tool_calls_path", result.tool_calls_path),
    )
    for field_name, value in required_paths:
        check = _validate_evidence_path(
            field_name=field_name,
            value=value,
            evidence_dir=evidence_dir,
        )
        if check is not None:
            return GuardResult.failed(
                category="openclaw_result_shape",
                reason=f"{check}; run_id={run_id} call_id={call_id} worker_id={worker_id} stage={stage}",
                paths=(),
            )

    # T35 对齐：first_response 模式不要求 raw_output/receipt；完整运行必须两者都存在且落在 evidence_dir。
    if full_run:
        for field_name, value in (
            ("raw_output_path", result.raw_output_path),
            ("openviking_receipt_path", result.openviking_receipt_path),
        ):
            check = _validate_evidence_path(
                field_name=field_name,
                value=value,
                evidence_dir=evidence_dir,
            )
            if check is not None:
                return GuardResult.failed(
                    category="openclaw_result_shape",
                    reason=f"{check}; run_id={run_id} call_id={call_id} worker_id={worker_id} stage={stage}",
                    paths=(),
                )
    return GuardResult.passed("openclaw_result_shape")


def _validate_evidence_path(field_name: str, value: Path | None, evidence_dir: Path) -> str | None:
    if value is None:
        return f"{field_name} 缺失"
    evidence_root = _resolve_evidence_root(evidence_dir)
    actual = _resolve_evidence_path(evidence_root, value)
    if actual != evidence_root and evidence_root not in actual.parents:
        return f"{field_name} 不在 evidence_dir 内: {value}"
    if not actual.is_file():
        return f"{field_name} 指向的文件不存在: {value}"
    return None


def _resolve_evidence_root(evidence_dir: Path) -> Path:
    return evidence_dir.resolve()


def _resolve_evidence_path(evidence_root: Path, path: Path) -> Path:
    if path.is_absolute():
        return path.resolve()
    # 证据路径边界：相对路径只能按 evidence_dir 解析，不能回退到进程 cwd 推断。
    return (evidence_root / path).resolve()


def _failed_openclaw_result(reason: str) -> OpenClawResult:
    return OpenClawResult(
        status="failed",
        openclaw_run_id=None,
        provider_request_id=None,
        provider_request_id_status=None,
        workspace_evidence_path=None,
        provider_request_path=None,
        visible_tools_path=None,
        first_response_path=None,
        tool_calls_status=None,
        tool_calls_path=None,
        raw_output_path=None,
        openviking_receipt_path=None,
        failure_reason=reason,
    )


def _serialize_material_ref(value: MaterialReadRef) -> dict[str, str | None]:
    return {
        "material_id": _required_str(value.material_id, "upstream_materials.material_id"),
        "capability_id": _required_str(value.capability_id, "upstream_materials.capability_id"),
        "worker_id": _required_str(value.worker_id, "upstream_materials.worker_id"),
        "stage": _required_stage_value(value.stage, "upstream_materials.stage"),
        "l1_uri": _required_str(value.l1_uri, "upstream_materials.l1_uri"),
        "l1_sha256": _required_str(value.l1_sha256, "upstream_materials.l1_sha256"),
        "l2_index_uri": _optional_str(value.l2_index_uri, "upstream_materials.l2_index_uri"),
        "l2_allowed_prefix": _optional_str(value.l2_allowed_prefix, "upstream_materials.l2_allowed_prefix"),
        "call_id": _required_str(value.call_id, "upstream_materials.call_id"),
    }


def _serialize_read_capability(value: OpenVikingReadCapability) -> dict[str, str | None]:
    return {
        "capability_id": _required_str(value.capability_id, "openviking_read_capabilities.capability_id"),
        "material_id": _required_str(value.material_id, "openviking_read_capabilities.material_id"),
        "allowed_l1_uri": _required_str(value.allowed_l1_uri, "openviking_read_capabilities.allowed_l1_uri"),
        "allowed_l1_sha256": _required_str(
            value.allowed_l1_sha256,
            "openviking_read_capabilities.allowed_l1_sha256",
        ),
        "allowed_l2_prefix": _optional_str(
            value.allowed_l2_prefix,
            "openviking_read_capabilities.allowed_l2_prefix",
        ),
        "allowed_l2_index_sha256": _optional_str(
            value.allowed_l2_index_sha256,
            "openviking_read_capabilities.allowed_l2_index_sha256",
        ),
        "manifest_entry_sha256": _required_str(
            value.manifest_entry_sha256,
            "openviking_read_capabilities.manifest_entry_sha256",
        ),
    }


def _serialize_material_target(value: MaterialTarget) -> dict[str, str]:
    return {
        "run_id": _required_str(value.run_id, "material_target.run_id"),
        "call_id": _required_str(value.call_id, "material_target.call_id"),
        "worker_id": _required_str(value.worker_id, "material_target.worker_id"),
        "stage": _required_stage_value(value.stage, "material_target.stage"),
        "target_name": _required_str(value.target_name, "material_target.target_name"),
        "l1_uri": _required_str(value.l1_uri, "material_target.l1_uri"),
        "l2_prefix": _required_str(value.l2_prefix, "material_target.l2_prefix"),
    }


def _required_str(value: object, field_name: str) -> str:
    if value is None:
        raise ValueError(f"{field_name} 缺失")
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是 str")
    if value == "":
        raise ValueError(f"{field_name} 不能为空")
    return value


def _optional_str(value: object | None, field_name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是 str 或 None")
    return value


def _optional_path(value: object | None, field_name: str) -> Path | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return value
    if not isinstance(value, str):
        raise TypeError(f"{field_name} 必须是 str/Path 或 None")
    if value == "":
        raise ValueError(f"{field_name} 不能为空字符串")
    return Path(value)


def _required_stage_value(value: object, field_name: str) -> str:
    if not isinstance(value, Stage):
        raise TypeError(f"{field_name} 必须是 Stage")
    return _required_str(value.value, field_name)
