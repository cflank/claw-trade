from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claw_trade.workflow.models import Stage, WorkerCall


@dataclass(frozen=True)
class OpenClawResult:
    status: str
    openclaw_run_id: str | None
    provider_request_id: str | None
    provider_request_id_status: str | None
    workspace_evidence_path: Path | None
    provider_request_path: Path | None
    visible_tools_path: Path | None
    first_response_path: Path | None
    tool_calls_status: str | None
    tool_calls_path: Path | None
    raw_output_path: Path | None
    openviking_receipt_path: Path | None
    failure_reason: str | None


@dataclass(frozen=True)
class ProviderEvidence:
    run_id: str
    call_id: str
    worker_id: str
    stage: Stage
    openclaw_run_id: str
    provider_request_id: str | None
    provider_request_id_status: str
    workspace_evidence_path: Path
    provider_request_path: Path
    visible_tools_path: Path
    first_response_path: Path
    tool_calls_status: str
    tool_calls_path: Path
    raw_output_path: Path | None
    openviking_receipt_path: Path | None

    def __post_init__(self) -> None:
        if not isinstance(self.stage, Stage):
            raise TypeError("stage must be Stage")


@dataclass(frozen=True)
class EvidenceReadResult:
    ok: bool
    evidence: ProviderEvidence | None
    reason: str | None
    paths: tuple[Path, ...]

    @classmethod
    def passed(cls, evidence: ProviderEvidence) -> EvidenceReadResult:
        return cls(ok=True, evidence=evidence, reason=None, paths=())

    @classmethod
    def failed(cls, reason: str, paths: tuple[Path, ...]) -> EvidenceReadResult:
        return cls(ok=False, evidence=None, reason=reason, paths=paths)


class EvidenceReader:
    def require_provider_evidence(
        self,
        call: WorkerCall,
        result: OpenClawResult,
        full_run: bool,
    ) -> EvidenceReadResult:
        # 这里只接受 OpenClaw 已落盘的证据文件；日志、renderer/export 文本都不能替代 provider request 证据。
        evidence_dir = call.evidence_dir.resolve()
        if not evidence_dir.exists() or not evidence_dir.is_dir():
            return EvidenceReadResult.failed(
                reason="evidence_dir 不存在或不是目录",
                paths=(call.evidence_dir,),
            )

        if result.status != "succeeded":
            return EvidenceReadResult.failed(
                reason=f"openclaw_result.status 不是 succeeded: {result.status}",
                paths=(call.evidence_dir,),
            )

        openclaw_run_id = self._required_str(result.openclaw_run_id, "openclaw_run_id", call.evidence_dir)
        if isinstance(openclaw_run_id, EvidenceReadResult):
            return openclaw_run_id
        provider_request_id_status = self._required_str(
            result.provider_request_id_status,
            "provider_request_id_status",
            call.evidence_dir,
        )
        if isinstance(provider_request_id_status, EvidenceReadResult):
            return provider_request_id_status
        tool_calls_status = self._required_str(result.tool_calls_status, "tool_calls_status", call.evidence_dir)
        if isinstance(tool_calls_status, EvidenceReadResult):
            return tool_calls_status

        workspace_path = self._require_file_in_evidence_dir(
            result.workspace_evidence_path,
            evidence_dir,
            "workspace_evidence_path",
        )
        if isinstance(workspace_path, EvidenceReadResult):
            return workspace_path
        provider_request_path = self._require_file_in_evidence_dir(
            result.provider_request_path,
            evidence_dir,
            "provider_request_path",
        )
        if isinstance(provider_request_path, EvidenceReadResult):
            return provider_request_path
        visible_tools_path = self._require_file_in_evidence_dir(
            result.visible_tools_path,
            evidence_dir,
            "visible_tools_path",
        )
        if isinstance(visible_tools_path, EvidenceReadResult):
            return visible_tools_path
        first_response_path = self._require_file_in_evidence_dir(
            result.first_response_path,
            evidence_dir,
            "first_response_path",
        )
        if isinstance(first_response_path, EvidenceReadResult):
            return first_response_path
        tool_calls_path = self._require_file_in_evidence_dir(
            result.tool_calls_path,
            evidence_dir,
            "tool_calls_path",
        )
        if isinstance(tool_calls_path, EvidenceReadResult):
            return tool_calls_path

        raw_output_path: Path | None = None
        openviking_receipt_path: Path | None = None
        if full_run:
            # full_run 必须拿到 raw_output/receipt，first-response stop 才允许按需缺省。
            raw_path_result = self._require_file_in_evidence_dir(
                result.raw_output_path,
                evidence_dir,
                "raw_output_path",
            )
            if isinstance(raw_path_result, EvidenceReadResult):
                return raw_path_result
            raw_output_path = raw_path_result

            receipt_path_result = self._require_file_in_evidence_dir(
                result.openviking_receipt_path,
                evidence_dir,
                "openviking_receipt_path",
            )
            if isinstance(receipt_path_result, EvidenceReadResult):
                return receipt_path_result
            openviking_receipt_path = receipt_path_result
        else:
            if result.raw_output_path is not None:
                raw_path_result = self._require_file_in_evidence_dir(
                    result.raw_output_path,
                    evidence_dir,
                    "raw_output_path",
                )
                if isinstance(raw_path_result, EvidenceReadResult):
                    return raw_path_result
                raw_output_path = raw_path_result
            if result.openviking_receipt_path is not None:
                receipt_path_result = self._require_file_in_evidence_dir(
                    result.openviking_receipt_path,
                    evidence_dir,
                    "openviking_receipt_path",
                )
                if isinstance(receipt_path_result, EvidenceReadResult):
                    return receipt_path_result
                openviking_receipt_path = receipt_path_result

        workspace_result = self._read_json_object(workspace_path, "workspace evidence")
        if isinstance(workspace_result, EvidenceReadResult):
            return workspace_result
        provider_request_result = self._read_json_object(provider_request_path, "provider request")
        if isinstance(provider_request_result, EvidenceReadResult):
            return provider_request_result
        visible_tools_result = self._read_json_object(visible_tools_path, "visible tools")
        if isinstance(visible_tools_result, EvidenceReadResult):
            return visible_tools_result
        tool_calls_result = self._read_json_object(tool_calls_path, "tool calls")
        if isinstance(tool_calls_result, EvidenceReadResult):
            return tool_calls_result

        evidence = ProviderEvidence(
            run_id=call.run_id,
            call_id=call.call_id,
            worker_id=call.worker_id,
            stage=call.stage,
            openclaw_run_id=openclaw_run_id,
            provider_request_id=result.provider_request_id,
            provider_request_id_status=provider_request_id_status,
            workspace_evidence_path=workspace_path,
            provider_request_path=provider_request_path,
            visible_tools_path=visible_tools_path,
            first_response_path=first_response_path,
            tool_calls_status=tool_calls_status,
            tool_calls_path=tool_calls_path,
            raw_output_path=raw_output_path,
            openviking_receipt_path=openviking_receipt_path,
        )
        return EvidenceReadResult.passed(evidence)

    def read_workspace_evidence(self, path: Path) -> dict[str, object]:
        return self._read_json_or_raise(path, "workspace evidence")

    def read_provider_request(self, path: Path) -> dict[str, object]:
        return self._read_json_or_raise(path, "provider request")

    def read_visible_tools(self, path: Path) -> dict[str, object]:
        return self._read_json_or_raise(path, "visible tools")

    def read_tool_calls(self, path: Path) -> dict[str, object]:
        return self._read_json_or_raise(path, "tool calls")

    def _read_json_or_raise(self, path: Path, label: str) -> dict[str, object]:
        result = self._read_json_object(path, label)
        if isinstance(result, EvidenceReadResult):
            reason = result.reason or f"{label} 读取失败"
            raise ValueError(reason)
        return result

    def _read_json_object(self, path: Path, label: str) -> dict[str, object] | EvidenceReadResult:
        # 证据读取只做结构化解析，不做“容错重构”或内容补写，避免把不可信文本伪装成真实产物。
        try:
            payload: Any = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return EvidenceReadResult.failed(
                reason=f"{label} JSON 解析失败: {exc}",
                paths=(path,),
            )
        except OSError as exc:
            return EvidenceReadResult.failed(
                reason=f"{label} 读取失败: {exc}",
                paths=(path,),
            )
        if not isinstance(payload, dict):
            return EvidenceReadResult.failed(
                reason=f"{label} 必须是 JSON object",
                paths=(path,),
            )
        return payload

    def _require_file_in_evidence_dir(
        self,
        path: Path | None,
        evidence_dir: Path,
        field_name: str,
    ) -> Path | EvidenceReadResult:
        if path is None:
            return EvidenceReadResult.failed(
                reason=f"{field_name} 缺失",
                paths=(evidence_dir,),
            )
        resolved_path = path.resolve()
        try:
            resolved_path.relative_to(evidence_dir)
        except ValueError:
            # 这里必须阻断路径逃逸，防止读取其它 run/call 的证据污染当前判定。
            return EvidenceReadResult.failed(
                reason=f"{field_name} 不在本次 call evidence_dir 下",
                paths=(path,),
            )
        if not resolved_path.exists() or not resolved_path.is_file():
            return EvidenceReadResult.failed(
                reason=f"{field_name} 不存在或不是文件",
                paths=(resolved_path,),
            )
        return resolved_path

    def _required_str(
        self,
        value: str | None,
        field_name: str,
        evidence_dir: Path,
    ) -> str | EvidenceReadResult:
        if value is None or value == "":
            return EvidenceReadResult.failed(
                reason=f"{field_name} 缺失或为空",
                paths=(evidence_dir,),
            )
        return value


def provider_evidence_to_dict(evidence: ProviderEvidence) -> dict[str, object]:
    # 对外序列化时显式把 Stage 映射成字符串，避免对象边界退化成隐式 str()。
    return {
        "run_id": evidence.run_id,
        "call_id": evidence.call_id,
        "worker_id": evidence.worker_id,
        "stage": evidence.stage.value,
        "openclaw_run_id": evidence.openclaw_run_id,
        "provider_request_id": evidence.provider_request_id,
        "provider_request_id_status": evidence.provider_request_id_status,
        "workspace_evidence_path": evidence.workspace_evidence_path,
        "provider_request_path": evidence.provider_request_path,
        "visible_tools_path": evidence.visible_tools_path,
        "first_response_path": evidence.first_response_path,
        "tool_calls_status": evidence.tool_calls_status,
        "tool_calls_path": evidence.tool_calls_path,
        "raw_output_path": evidence.raw_output_path,
        "openviking_receipt_path": evidence.openviking_receipt_path,
    }
