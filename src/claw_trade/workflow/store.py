from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from claw_trade.artifacts.openviking_client import OpenVikingAccessRecord
from claw_trade.artifacts.refs import L2Index, MaterialReceipt
from claw_trade.guards.common import GuardResult
from claw_trade.runtime.evidence_reader import OpenClawResult
from claw_trade.workflow.models import (
    BatchScope,
    Decision,
    DecisionKind,
    ExportResult,
    FailureRecord,
    RunRequest,
    RunStatus,
    Stage,
    StageBatch,
    StageBatchResult,
    StopPoint,
    WorkerCall,
    WorkerResult,
    WorkerStatus,
    WorkflowEntryPoint,
    WorkflowState,
)

if TYPE_CHECKING:
    from claw_trade.reports.exporter import ExportClaimMapping


class WorkflowStore:
    def __init__(self, root: Path | str) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def run_dir(self, run_id: str) -> Path:
        safe_run_id = self._safe_token(run_id, "run_id")
        return self._safe_join(self._root, safe_run_id)

    def call_dir(self, call: WorkerCall) -> Path:
        safe_call_id = self._safe_token(call.call_id, "call_id")
        return self._safe_join(self.run_dir(call.run_id) / "calls", safe_call_id)

    def evidence_dir(self, call: WorkerCall) -> Path:
        return self.call_dir(call)

    def create_run(self, request: RunRequest) -> WorkflowState:
        run_id = self._make_run_id()
        run_dir = self.run_dir(run_id)
        (run_dir / "calls").mkdir(parents=True, exist_ok=False)
        (run_dir / "openviking").mkdir(parents=True, exist_ok=False)
        (run_dir / "reports").mkdir(parents=True, exist_ok=False)

        now = self._now_text()
        state = WorkflowState(
            run_id=run_id,
            request=request,
            status=RunStatus.CREATED,
            run_dir=run_dir,
            openviking_namespace=f"workflow/{run_id}",
            created_at=now,
            updated_at=now,
            active_stage=None,
            completed_workers=(),
            failed_workers=(),
            failure_reason=None,
            last_decision_path=None,
        )

        self._write_json(run_dir / "request.json", request)
        self._write_json(run_dir / "state.json", state)

        # runs/<run>/openviking 只是本地审计副本，不是正式材料权威。
        self._write_json(
            run_dir / "openviking" / "approved-manifest.json",
            {"audit_only": True, "note": "本地审计副本，不是正式材料权威", "materials": []},
        )
        self._write_json(
            run_dir / "openviking" / "receipts.json",
            {"audit_only": True, "note": "本地审计副本，不是正式材料权威", "receipts": []},
        )
        self._write_json(
            run_dir / "openviking" / "access-audit.json",
            {"audit_only": True, "note": "本地审计副本，不是正式材料权威", "events": []},
        )
        self._write_json(
            run_dir / "openviking" / "l1-index.json",
            {"audit_only": True, "note": "本地审计副本，不是正式材料权威", "items": []},
        )
        self._write_json(
            run_dir / "openviking" / "l2-index.json",
            {"audit_only": True, "note": "本地审计副本，不是正式材料权威", "items": []},
        )
        self._write_json(
            run_dir / "openviking" / "l1-l2-index.json",
            {"audit_only": True, "note": "本地审计副本，不是正式材料权威", "items": []},
        )
        return state

    def load_state(self, run_id: str) -> WorkflowState:
        payload = self._read_json(self.run_dir(run_id) / "state.json")
        return self._workflow_state_from_dict(payload)

    def save_state(self, state: WorkflowState) -> None:
        self._write_json(self.run_dir(state.run_id) / "state.json", state)

    def save_decision(self, run_id: str, decision: Decision) -> Path:
        decisions_dir = self.run_dir(run_id) / "decisions"
        decisions_dir.mkdir(parents=True, exist_ok=True)
        path = decisions_dir / f"{self._decision_stamp()}-{decision.kind.value}.json"
        self._write_json(path, decision)
        return path

    def save_call(self, call: WorkerCall) -> Path:
        call_dir = self.call_dir(call)
        call_dir.mkdir(parents=True, exist_ok=True)
        path = call_dir / "call.json"
        self._write_json(path, call)
        return path

    def save_openclaw_result(self, call: WorkerCall, result: OpenClawResult) -> Path:
        call_dir = self.call_dir(call)
        call_dir.mkdir(parents=True, exist_ok=True)
        path = call_dir / "openclaw-result.json"
        self._write_json(path, result)
        return path

    def save_worker_result(self, result: WorkerResult) -> Path:
        self._safe_token(result.call_id, "call_id")
        call_dir = self._safe_join(self.run_dir(result.run_id) / "calls", result.call_id)
        call_dir.mkdir(parents=True, exist_ok=True)
        path = call_dir / "result.json"
        self._write_json(path, result)
        return path

    def list_worker_results(self, run_id: str) -> tuple[WorkerResult, ...]:
        calls_dir = self.run_dir(run_id) / "calls"
        if not calls_dir.exists():
            return ()
        results: list[WorkerResult] = []
        for result_path in sorted(calls_dir.glob("*/result.json")):
            payload = self._read_json(result_path)
            results.append(self._worker_result_from_dict(payload))
        return tuple(results)

    def save_failure(self, failure: FailureRecord) -> Path:
        failures_dir = self.run_dir(failure.run_id) / "failures"
        failures_dir.mkdir(parents=True, exist_ok=True)
        path = failures_dir / f"{self._decision_stamp()}-{failure.category}.json"
        self._write_json(path, failure)
        return path

    def save_stage_batch_result(self, result: StageBatchResult) -> Path:
        stage_dir = self.run_dir(result.run_id) / "stage-batches"
        stage_dir.mkdir(parents=True, exist_ok=True)
        path = stage_dir / f"{self._decision_stamp()}-{result.stage.value}.json"
        self._write_json(path, result)
        return path

    def save_export_result(self, result: ExportResult) -> Path:
        path = self.run_dir(result.run_id) / "reports" / "export-result.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(path, result)
        return path

    def load_export_result(self, run_id: str) -> ExportResult | None:
        path = self.run_dir(run_id) / "reports" / "export-result.json"
        if not path.exists():
            return None
        payload = self._read_json(path)
        return self._export_result_from_dict(payload)

    def save_guard_result(self, call: WorkerCall, guard: GuardResult) -> Path:
        call_dir = self.call_dir(call)
        call_dir.mkdir(parents=True, exist_ok=True)
        path = call_dir / "guard-results.json"
        self._write_json(path, guard)
        return path

    def save_export_guard_result(self, run_id: str, guard: GuardResult) -> Path:
        path = self.run_dir(run_id) / "reports" / "export-guard-results.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(path, guard)
        return path

    def append_receipt_audit(self, run_id: str, receipt: MaterialReceipt, guard: GuardResult) -> Path:
        path = self.run_dir(run_id) / "openviking" / "receipts.json"
        payload = self._read_json(path)
        receipts = list(payload.get("receipts", []))
        receipts.append(
            self._to_jsonable(
                {
                    "recorded_at": self._now_text(),
                    "receipt": receipt,
                    "guard": guard,
                }
            )
        )
        payload["receipts"] = receipts
        self._write_json(path, payload)
        return path

    def append_access_audit(self, call: WorkerCall, records: tuple[OpenVikingAccessRecord, ...]) -> Path:
        path = self.run_dir(call.run_id) / "openviking" / "access-audit.json"
        payload = self._read_json(path)
        events = list(payload.get("events", []))
        events.append(
            self._to_jsonable(
                {
                    "recorded_at": self._now_text(),
                    "run_id": call.run_id,
                    "call_id": call.call_id,
                    "worker_id": call.worker_id,
                    "stage": call.stage,
                    "records": records,
                }
            )
        )
        payload["events"] = events
        self._write_json(path, payload)
        return path

    def save_l1_l2_index(self, call: WorkerCall, index: L2Index) -> Path:
        # 这里写的是审计索引和校验结果，正式材料权威仍在 OpenViking URI、receipt、stat/read 和内容指纹。
        run_dir = self.run_dir(call.run_id)
        l1_l2_path = run_dir / "openviking" / "l1-l2-index.json"
        l2_path = run_dir / "openviking" / "l2-index.json"
        l1_path = run_dir / "openviking" / "l1-index.json"

        l1_l2_payload = self._read_json(l1_l2_path)
        l2_payload = self._read_json(l2_path)
        l1_payload = self._read_json(l1_path)

        record = self._to_jsonable(
            {
                "recorded_at": self._now_text(),
                "run_id": call.run_id,
                "call_id": call.call_id,
                "worker_id": call.worker_id,
                "stage": call.stage,
                "l1_uri": call.material_target.l1_uri,
                "l2_index": index,
            }
        )
        l1_l2_items = list(l1_l2_payload.get("items", []))
        l1_l2_items.append(record)
        l1_l2_payload["items"] = l1_l2_items

        l2_items = list(l2_payload.get("items", []))
        l2_items.append(record)
        l2_payload["items"] = l2_items

        l1_items = list(l1_payload.get("items", []))
        l1_items.append(
            self._to_jsonable(
                {
                    "recorded_at": self._now_text(),
                    "run_id": call.run_id,
                    "call_id": call.call_id,
                    "worker_id": call.worker_id,
                    "stage": call.stage,
                    "l1_uri": call.material_target.l1_uri,
                }
            )
        )
        l1_payload["items"] = l1_items

        self._write_json(l1_l2_path, l1_l2_payload)
        self._write_json(l2_path, l2_payload)
        self._write_json(l1_path, l1_payload)
        return l1_l2_path

    def save_export_claim_mapping(self, run_id: str, mapping: ExportClaimMapping | object) -> Path:
        path = self.run_dir(run_id) / "reports" / "export-claims.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        self._write_json(path, mapping)
        return path

    def _write_json(self, path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(self._to_jsonable(payload), f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")

    def _read_json(self, path: Path) -> dict[str, Any]:
        with path.open(encoding="utf-8") as f:
            value = json.load(f)
        if not isinstance(value, dict):
            raise ValueError(f"JSON 根对象必须是 dict: {path}")
        return value

    def _to_jsonable(self, value: Any) -> Any:
        if is_dataclass(value):
            return {k: self._to_jsonable(v) for k, v in asdict(value).items()}
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, Path):
            return str(value)
        if isinstance(value, tuple):
            return [self._to_jsonable(v) for v in value]
        if isinstance(value, list):
            return [self._to_jsonable(v) for v in value]
        if isinstance(value, dict):
            return {str(k): self._to_jsonable(v) for k, v in value.items()}
        return value

    def _workflow_state_from_dict(self, payload: dict[str, Any]) -> WorkflowState:
        request = self._run_request_from_dict(self._as_dict(payload["request"], "request"))
        return WorkflowState(
            run_id=self._as_str(payload["run_id"], "run_id"),
            request=request,
            status=RunStatus(self._as_str(payload["status"], "status")),
            run_dir=Path(self._as_str(payload["run_dir"], "run_dir")),
            openviking_namespace=self._as_str(payload["openviking_namespace"], "openviking_namespace"),
            created_at=self._as_str(payload["created_at"], "created_at"),
            updated_at=self._as_str(payload["updated_at"], "updated_at"),
            active_stage=self._stage_or_none(payload.get("active_stage")),
            completed_workers=tuple(str(x) for x in payload.get("completed_workers", [])),
            failed_workers=tuple(str(x) for x in payload.get("failed_workers", [])),
            failure_reason=self._optional_str(payload.get("failure_reason"), "failure_reason"),
            last_decision_path=self._path_or_none(payload.get("last_decision_path")),
        )

    def _run_request_from_dict(self, payload: dict[str, Any]) -> RunRequest:
        return RunRequest(
            ticker=self._as_str(payload["ticker"], "ticker"),
            company_name=self._as_str(payload["company_name"], "company_name"),
            market=self._as_str(payload["market"], "market"),
            profile=self._as_str(payload["profile"], "profile"),
            currency=self._as_str(payload["currency"], "currency"),
            currency_symbol=self._as_str(payload["currency_symbol"], "currency_symbol"),
            current_date=self._as_str(payload["current_date"], "current_date"),
            start_date=self._as_str(payload["start_date"], "start_date"),
            end_date=self._as_str(payload["end_date"], "end_date"),
            data_gateway=self._as_str(payload.get("data_gateway", "data_gateway"), "data_gateway"),
            stop_point=StopPoint(self._as_str(payload.get("stop_point", StopPoint.NONE.value), "stop_point")),
            target_worker_id=self._optional_str(payload.get("target_worker_id"), "target_worker_id"),
            target_stage=self._stage_or_none(payload.get("target_stage")),
            entry_point=WorkflowEntryPoint(
                self._as_str(
                    payload.get("entry_point", WorkflowEntryPoint.GENERIC.value),
                    "entry_point",
                )
            ),
            max_debate_rounds=self._as_int(payload.get("max_debate_rounds", 1), "max_debate_rounds"),
            max_risk_discuss_rounds=self._as_int(
                payload.get("max_risk_discuss_rounds", 1),
                "max_risk_discuss_rounds",
            ),
            frontline_execution_mode=self._as_str(
                payload.get("frontline_execution_mode", "parallel"),
                "frontline_execution_mode",
            ),
        )

    def _worker_result_from_dict(self, payload: dict[str, Any]) -> WorkerResult:
        failure_payload = payload.get("failure")
        failure = self._failure_from_dict(self._as_dict(failure_payload, "failure")) if failure_payload else None
        return WorkerResult(
            run_id=self._as_str(payload["run_id"], "run_id"),
            call_id=self._as_str(payload["call_id"], "call_id"),
            worker_id=self._as_str(payload["worker_id"], "worker_id"),
            stage=Stage(self._as_str(payload["stage"], "stage")),
            status=WorkerStatus(self._as_str(payload["status"], "status")),
            openclaw_result_path=self._path_or_none(payload.get("openclaw_result_path")),
            approved_material_id=self._optional_str(payload.get("approved_material_id"), "approved_material_id"),
            failure=failure,
            turn_index=self._as_int(payload.get("turn_index", 0), "turn_index"),
            round_index=self._as_int(payload.get("round_index", 1), "round_index"),
            role_turn_index=self._as_int(payload.get("role_turn_index", 1), "role_turn_index"),
        )

    def _export_result_from_dict(self, payload: dict[str, Any]) -> ExportResult:
        failure_payload = payload.get("failure")
        failure = self._failure_from_dict(self._as_dict(failure_payload, "failure")) if failure_payload else None
        return ExportResult(
            run_id=self._as_str(payload["run_id"], "run_id"),
            status=self._as_str(payload["status"], "status"),
            final_report_path=self._path_or_none(payload.get("final_report_path")),
            export_guard_result_path=self._path_or_none(payload.get("export_guard_result_path")),
            unsupported_claims=tuple(str(x) for x in payload.get("unsupported_claims", [])),
            failure=failure,
        )

    def _failure_from_dict(self, payload: dict[str, Any]) -> FailureRecord:
        return FailureRecord(
            run_id=self._as_str(payload["run_id"], "run_id"),
            call_id=self._optional_str(payload.get("call_id"), "call_id"),
            worker_id=self._optional_str(payload.get("worker_id"), "worker_id"),
            stage=self._stage_or_none(payload.get("stage")),
            category=self._as_str(payload["category"], "category"),
            reason=self._as_str(payload["reason"], "reason"),
            evidence_paths=tuple(Path(str(x)) for x in payload.get("evidence_paths", [])),
            early_stop=bool(payload.get("early_stop", False)),
            human_action_required=self._optional_str(payload.get("human_action_required"), "human_action_required"),
            turn_index=self._as_int(payload.get("turn_index", 0), "turn_index"),
            round_index=self._as_int(payload.get("round_index", 1), "round_index"),
            role_turn_index=self._as_int(payload.get("role_turn_index", 1), "role_turn_index"),
        )

    def _decision_from_dict(self, payload: dict[str, Any]) -> Decision:
        batch_payload = payload.get("batch")
        failure_payload = payload.get("failure")
        batch = self._stage_batch_from_dict(self._as_dict(batch_payload, "batch")) if batch_payload else None
        failure = self._failure_from_dict(self._as_dict(failure_payload, "failure")) if failure_payload else None
        return Decision(
            kind=DecisionKind(self._as_str(payload["kind"], "kind")),
            stage=self._stage_or_none(payload.get("stage")),
            batch=batch,
            next_status=self._run_status_or_none(payload.get("next_status")),
            reason=self._optional_str(payload.get("reason"), "reason"),
            failure=failure,
        )

    def _stage_batch_from_dict(self, payload: dict[str, Any]) -> StageBatch:
        return StageBatch(
            run_id=self._as_str(payload["run_id"], "run_id"),
            stage=Stage(self._as_str(payload["stage"], "stage")),
            worker_ids=tuple(str(x) for x in payload.get("worker_ids", [])),
            scope=BatchScope(self._as_str(payload["scope"], "scope")),
            collect_first=bool(payload.get("collect_first", False)),
            stop_point=StopPoint(self._as_str(payload.get("stop_point", StopPoint.NONE.value), "stop_point")),
            turn_index=self._as_int(payload.get("turn_index", 0), "turn_index"),
            round_index=self._as_int(payload.get("round_index", 1), "round_index"),
            role_turn_index=self._as_int(payload.get("role_turn_index", 1), "role_turn_index"),
        )

    def _run_status_or_none(self, value: Any) -> RunStatus | None:
        if value is None:
            return None
        return RunStatus(self._as_str(value, "run_status"))

    def _stage_or_none(self, value: Any) -> Stage | None:
        if value is None:
            return None
        return Stage(self._as_str(value, "stage"))

    def _path_or_none(self, value: Any) -> Path | None:
        if value is None:
            return None
        return Path(self._as_str(value, "path"))

    def _safe_token(self, value: str, field: str) -> str:
        token = value.strip()
        if not token:
            raise ValueError(f"{field} 不能为空")
        if token in {".", ".."}:
            raise ValueError(f"{field} 非法: {token}")
        if "/" in token or "\\" in token:
            raise ValueError(f"{field} 非法: {token}")
        return token

    def _safe_join(self, base: Path, leaf: str) -> Path:
        path = (base / leaf).resolve()
        try:
            path.relative_to(base.resolve())
        except ValueError as exc:
            raise ValueError(f"路径越界: {path}") from exc
        return path

    def _decision_stamp(self) -> str:
        return datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")

    def _make_run_id(self) -> str:
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        return f"run-{stamp}-{uuid4().hex[:8]}"

    def _now_text(self) -> str:
        return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")

    def _as_dict(self, value: Any, field: str) -> dict[str, Any]:
        if not isinstance(value, dict):
            raise ValueError(f"{field} 必须是对象")
        return value

    def _as_str(self, value: Any, field: str) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field} 必须是非空字符串")
        return value

    def _as_int(self, value: Any, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field} 必须是整数")
        return value

    def _optional_str(self, value: Any, field: str) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError(f"{field} 必须是字符串或 null")
        text = value.strip()
        return text if text else None
