from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

from claw_trade.artifacts.manifest import make_material_id
from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, MaterialReceipt
from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import Stage, WorkerCall

if TYPE_CHECKING:
    from claw_trade.guards.export_claims import ExportClaimMapping

PM_DECISION_SCHEMA_VERSION = "control.pm_decision.v1"
PM_DECISION_FILENAME = "pm-decision.json"
PM_WORKER_ID = "portfolio_manager"
PM_STAGE = Stage.PORTFOLIO_DECISION
PM_ALLOWED_RATINGS = frozenset({"buy", "hold", "sell", "neutral", "not_rated"})
PM_DECISION_REQUIRED_FIELDS = (
    "schema_version",
    "run_id",
    "call_id",
    "worker_id",
    "stage",
    "material_id",
    "rating",
    "final_conclusion",
    "execution_conditions",
    "risk_conditions",
    "source_claim_ids",
    "source_l1_sha256",
    "l1_uri",
)


@dataclass(frozen=True)
class PMDecision:
    schema_version: str
    run_id: str
    call_id: str
    worker_id: str
    stage: Stage
    material_id: str
    rating: str
    final_conclusion: str
    execution_conditions: tuple[str, ...]
    risk_conditions: tuple[str, ...]
    source_claim_ids: tuple[str, ...]
    source_l1_sha256: str
    l1_uri: str


@dataclass(frozen=True)
class PMDecisionResult:
    ok: bool
    decision: PMDecision | None
    reason: str | None

    @classmethod
    def passed(cls, decision: PMDecision) -> PMDecisionResult:
        return cls(ok=True, decision=decision, reason=None)

    @classmethod
    def failed(cls, reason: str) -> PMDecisionResult:
        return cls(ok=False, decision=None, reason=reason)


def parse_pm_decision_evidence(path: Path) -> PMDecisionResult:
    if not path.exists() or not path.is_file():
        return PMDecisionResult.failed(f"{PM_DECISION_FILENAME} 不存在或不是文件")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return PMDecisionResult.failed(f"{PM_DECISION_FILENAME} JSON 非法: {exc}")
    except OSError as exc:
        return PMDecisionResult.failed(f"{PM_DECISION_FILENAME} 读取失败: {exc}")
    if not isinstance(payload, dict):
        return PMDecisionResult.failed(f"{PM_DECISION_FILENAME} 根对象必须是 object")
    return _parse_pm_decision_payload(payload, label=PM_DECISION_FILENAME)


def parse_pm_decision_block(pm_l1_text: str, material: ApprovedMaterial) -> PMDecisionResult:
    payload_result = _extract_pm_decision_payload(pm_l1_text)
    if not payload_result.ok:
        return payload_result
    payload = payload_result.decision
    assert payload is not None
    identity_guard = validate_pm_decision_identity(payload, material)
    if not identity_guard.ok:
        return PMDecisionResult.failed(identity_guard.reason or "PM decision 身份校验失败")
    return payload_result


def require_pm_decision_block(pm_l1_text: str, material: ApprovedMaterial) -> tuple[PMDecision, GuardResult]:
    parsed = parse_pm_decision_block(pm_l1_text=pm_l1_text, material=material)
    if not parsed.ok or parsed.decision is None:
        return _empty_pm_decision(material), _failed_pm_owner(
            parsed.reason or "PM decision block 解析失败",
            paths=(Path(material.l1_uri),),
        )
    return parsed.decision, guard_passed(category="pm_owner")


def compare_pm_decision_fields(left: PMDecision, right: PMDecision) -> GuardResult:
    if left.rating != right.rating:
        return _failed_pm_owner("PM rating 被改写", paths=(Path("pm_decision"),))
    if left.final_conclusion != right.final_conclusion:
        return _failed_pm_owner("PM final_conclusion 被改写", paths=(Path("pm_decision"),))
    if left.execution_conditions != right.execution_conditions:
        return _failed_pm_owner("PM execution_conditions 被改写", paths=(Path("pm_decision"),))
    if left.risk_conditions != right.risk_conditions:
        return _failed_pm_owner("PM risk_conditions 被改写", paths=(Path("pm_decision"),))
    return guard_passed(category="pm_owner")


def validate_pm_decision_identity(decision: PMDecision, material: ApprovedMaterial) -> GuardResult:
    path = Path(material.l1_uri)
    if decision.run_id != material.run_id:
        return _failed_pm_owner("PM decision run_id 与 material 不一致", paths=(path,))
    if decision.call_id != material.call_id:
        return _failed_pm_owner("PM decision call_id 与 material 不一致", paths=(path,))
    if decision.worker_id != material.worker_id:
        return _failed_pm_owner("PM decision worker_id 与 material 不一致", paths=(path,))
    if decision.stage != material.stage:
        return _failed_pm_owner("PM decision stage 与 material 不一致", paths=(path,))
    if decision.material_id != material.material_id:
        return _failed_pm_owner("PM decision material_id 与 material 不一致", paths=(path,))
    if decision.source_l1_sha256 != material.l1_sha256:
        return _failed_pm_owner("PM decision source_l1_sha256 与 material 不一致", paths=(path,))
    if decision.l1_uri != material.l1_uri:
        return _failed_pm_owner("PM decision l1_uri 与 material 不一致", paths=(path,))

    # 这里必须只信 approved material 的结构化 claim id，禁止从正文猜来源。
    if not decision.source_claim_ids:
        return _failed_pm_owner("PM decision source_claim_ids 至少包含 1 个 claim_id", paths=(path,))
    claim_ids = {claim.claim_id for claim in material.l1_claims}
    for claim_id in decision.source_claim_ids:
        if claim_id not in claim_ids:
            return _failed_pm_owner(f"PM decision source_claim_id 不存在: {claim_id}", paths=(path,))
    return guard_passed(category="pm_owner")


def validate_pm_owner(
    *,
    call: WorkerCall,
    evidence: ProviderEvidence,
    receipt: MaterialReceipt,
    claims: tuple[L1Claim, ...],
) -> tuple[PMDecision | None, GuardResult]:
    pm_path = call.evidence_dir / PM_DECISION_FILENAME
    parsed = parse_pm_decision_evidence(pm_path)
    if not parsed.ok or parsed.decision is None:
        return None, _failed_pm_owner(parsed.reason or "PM decision 解析失败", paths=(pm_path,))
    decision = parsed.decision

    if evidence.worker_id != PM_WORKER_ID:
        return None, _failed_pm_owner("ProviderEvidence worker_id 不是 portfolio_manager", paths=_evidence_paths(evidence))
    if evidence.stage != PM_STAGE:
        return None, _failed_pm_owner("ProviderEvidence stage 不是 portfolio_decision", paths=_evidence_paths(evidence))
    if decision.run_id != evidence.run_id:
        return None, _failed_pm_owner("PM decision run_id 与 ProviderEvidence 不一致", paths=(pm_path,))
    if decision.call_id != evidence.call_id:
        return None, _failed_pm_owner("PM decision call_id 与 ProviderEvidence 不一致", paths=(pm_path,))
    if decision.worker_id != evidence.worker_id:
        return None, _failed_pm_owner("PM decision worker_id 与 ProviderEvidence 不一致", paths=(pm_path,))
    if decision.stage != evidence.stage:
        return None, _failed_pm_owner("PM decision stage 与 ProviderEvidence 不一致", paths=(pm_path,))
    if decision.material_id != receipt_material_id(call, receipt):
        return None, _failed_pm_owner("PM decision material_id 与 receipt/call 不一致", paths=(pm_path,))
    if decision.source_l1_sha256 != receipt.sha256:
        return None, _failed_pm_owner("PM decision source_l1_sha256 与 receipt 不一致", paths=(pm_path,))
    if decision.l1_uri != receipt.uri:
        return None, _failed_pm_owner("PM decision l1_uri 与 receipt 不一致", paths=(pm_path,))
    if not decision.source_claim_ids:
        return None, _failed_pm_owner("PM decision source_claim_ids 至少包含 1 个 claim_id", paths=(pm_path,))
    claim_ids = {claim.claim_id for claim in claims}
    for claim_id in decision.source_claim_ids:
        if claim_id not in claim_ids:
            return None, _failed_pm_owner(f"PM decision source_claim_id 不存在: {claim_id}", paths=(pm_path,))
    return decision, guard_passed(category="pm_owner")


def load_pm_decision_for_material(material: ApprovedMaterial) -> tuple[PMDecision | None, GuardResult]:
    pm_path = material.hard_gate_result_path.parent / PM_DECISION_FILENAME
    parsed = parse_pm_decision_evidence(pm_path)
    if not parsed.ok or parsed.decision is None:
        return None, _failed_pm_owner(parsed.reason or "PM decision 解析失败", paths=(pm_path,))
    decision = parsed.decision
    identity_guard = validate_pm_decision_identity(decision, material)
    if not identity_guard.ok:
        return None, identity_guard
    return decision, guard_passed(category="pm_owner")


def validate_export_does_not_rewrite_pm(
    decision: PMDecision,
    export_mapping: ExportClaimMapping,
) -> GuardResult:
    export_decision = _extract_export_pm_decision(export_mapping=export_mapping, baseline=decision)
    if isinstance(export_decision, GuardResult):
        return export_decision
    return compare_pm_decision_fields(decision, export_decision)


def _extract_pm_decision_payload(pm_l1_text: str) -> PMDecisionResult:
    payloads: list[dict[str, Any]] = []
    for lang, raw in _iter_fenced_blocks(pm_l1_text):
        candidate = raw.strip()
        if not candidate:
            continue
        if not _looks_like_pm_json(candidate, lang):
            continue
        parsed = _decode_json_object(candidate)
        if parsed is None:
            return PMDecisionResult.failed("PM decision block JSON 非法")
        if parsed.get("schema_version") == PM_DECISION_SCHEMA_VERSION:
            payloads.append(parsed)
            continue
        if "schema_version" in parsed:
            return PMDecisionResult.failed("PM decision block schema_version 不匹配")
        if _looks_like_pm_payload_dict(parsed):
            return PMDecisionResult.failed("PM decision block 缺少 schema_version")

    full_text = pm_l1_text.strip()
    if full_text.startswith("{") and full_text.endswith("}") and _looks_like_pm_json(full_text, ""):
        parsed_full = _decode_json_object(full_text)
        if parsed_full is None:
            return PMDecisionResult.failed("PM decision block JSON 非法")
        if parsed_full.get("schema_version") == PM_DECISION_SCHEMA_VERSION:
            payloads.append(parsed_full)
        elif "schema_version" in parsed_full:
            return PMDecisionResult.failed("PM decision block schema_version 不匹配")
        elif _looks_like_pm_payload_dict(parsed_full):
            return PMDecisionResult.failed("PM decision block 缺少 schema_version")

    if not payloads:
        return PMDecisionResult.failed("L1 缺少结构化 PM decision block")
    if len(payloads) > 1:
        return PMDecisionResult.failed("L1 出现多个结构化 PM decision block")
    return _parse_pm_decision_payload(payloads[0], label="PM decision block")


def _parse_pm_decision_payload(payload: dict[str, Any], *, label: str) -> PMDecisionResult:
    for field in PM_DECISION_REQUIRED_FIELDS:
        if field not in payload:
            return PMDecisionResult.failed(f"{label} 缺少字段: {field}")
    if payload["schema_version"] != PM_DECISION_SCHEMA_VERSION:
        return PMDecisionResult.failed(f"{label} schema_version 不匹配")

    run_id = _expect_str(payload, "run_id")
    if run_id is None:
        return PMDecisionResult.failed(f"{label} run_id 必须是字符串")
    call_id = _expect_str(payload, "call_id")
    if call_id is None:
        return PMDecisionResult.failed(f"{label} call_id 必须是字符串")
    worker_id = _expect_str(payload, "worker_id")
    if worker_id is None:
        return PMDecisionResult.failed(f"{label} worker_id 必须是字符串")
    if worker_id != PM_WORKER_ID:
        return PMDecisionResult.failed(f"{label} worker_id 必须是 portfolio_manager")
    stage_text = _expect_str(payload, "stage")
    if stage_text is None:
        return PMDecisionResult.failed(f"{label} stage 必须是字符串")
    if stage_text != PM_STAGE.value:
        return PMDecisionResult.failed(f"{label} stage 必须是 portfolio_decision")
    material_id = _expect_str(payload, "material_id")
    if material_id is None:
        return PMDecisionResult.failed(f"{label} material_id 必须是字符串")
    rating = _expect_str(payload, "rating")
    if rating is None:
        return PMDecisionResult.failed(f"{label} rating 必须是字符串")
    if rating not in PM_ALLOWED_RATINGS:
        return PMDecisionResult.failed(f"{label} rating 非法: {rating}")
    final_conclusion = _expect_str(payload, "final_conclusion")
    if final_conclusion is None:
        return PMDecisionResult.failed(f"{label} final_conclusion 必须是字符串")
    execution_conditions = _expect_str_list(payload, "execution_conditions")
    if execution_conditions is None:
        return PMDecisionResult.failed(f"{label} execution_conditions 必须是字符串数组")
    risk_conditions = _expect_str_list(payload, "risk_conditions")
    if risk_conditions is None:
        return PMDecisionResult.failed(f"{label} risk_conditions 必须是字符串数组")
    source_claim_ids = _expect_str_list(payload, "source_claim_ids")
    if source_claim_ids is None:
        return PMDecisionResult.failed(f"{label} source_claim_ids 必须是字符串数组")
    if not source_claim_ids:
        return PMDecisionResult.failed(f"{label} source_claim_ids 至少包含 1 个 claim_id")
    source_l1_sha256 = _expect_str(payload, "source_l1_sha256")
    if source_l1_sha256 is None:
        return PMDecisionResult.failed(f"{label} source_l1_sha256 必须是字符串")
    l1_uri = _expect_str(payload, "l1_uri")
    if l1_uri is None:
        return PMDecisionResult.failed(f"{label} l1_uri 必须是字符串")

    return PMDecisionResult.passed(
        PMDecision(
            schema_version=PM_DECISION_SCHEMA_VERSION,
            run_id=run_id,
            call_id=call_id,
            worker_id=worker_id,
            stage=PM_STAGE,
            material_id=material_id,
            rating=rating,
            final_conclusion=final_conclusion,
            execution_conditions=execution_conditions,
            risk_conditions=risk_conditions,
            source_claim_ids=source_claim_ids,
            source_l1_sha256=source_l1_sha256,
            l1_uri=l1_uri,
        )
    )


def _extract_export_pm_decision(
    export_mapping: ExportClaimMapping,
    baseline: PMDecision,
) -> PMDecision | GuardResult:
    payload: object | None
    if isinstance(export_mapping, Mapping):
        payload = export_mapping.get("pm_decision")
    else:
        payload = getattr(export_mapping, "pm_decision", None)
    if payload is None:
        return _failed_pm_owner("export mapping 缺少 pm_decision", paths=(Path("export-claims.json"),))
    source_material_id = _extract_str(payload, "source_material_id")
    if source_material_id is None:
        return _failed_pm_owner(
            "export mapping pm_decision.source_material_id 必须是字符串",
            paths=(Path("export-claims.json"),),
        )
    if source_material_id != baseline.material_id:
        return _failed_pm_owner(
            "export mapping pm_decision.source_material_id 与 PM material_id 不一致",
            paths=(Path("export-claims.json"),),
        )

    rating = _extract_str(payload, "rating")
    if rating is None:
        return _failed_pm_owner("export mapping pm_decision.rating 必须是字符串", paths=(Path("export-claims.json"),))
    if rating not in PM_ALLOWED_RATINGS:
        return _failed_pm_owner(
            f"export mapping pm_decision.rating 非法: {rating}",
            paths=(Path("export-claims.json"),),
        )
    final_conclusion = _extract_str(payload, "final_conclusion")
    if final_conclusion is None:
        return _failed_pm_owner(
            "export mapping pm_decision.final_conclusion 必须是字符串",
            paths=(Path("export-claims.json"),),
        )
    execution_conditions = _extract_str_list(payload, "execution_conditions")
    if execution_conditions is None:
        return _failed_pm_owner(
            "export mapping pm_decision.execution_conditions 必须是字符串数组",
            paths=(Path("export-claims.json"),),
        )
    risk_conditions = _extract_str_list(payload, "risk_conditions")
    if risk_conditions is None:
        return _failed_pm_owner(
            "export mapping pm_decision.risk_conditions 必须是字符串数组",
            paths=(Path("export-claims.json"),),
        )
    return PMDecision(
        schema_version=baseline.schema_version,
        run_id=baseline.run_id,
        call_id=baseline.call_id,
        worker_id=baseline.worker_id,
        stage=baseline.stage,
        material_id=baseline.material_id,
        rating=rating,
        final_conclusion=final_conclusion,
        execution_conditions=execution_conditions,
        risk_conditions=risk_conditions,
        source_claim_ids=baseline.source_claim_ids,
        source_l1_sha256=baseline.source_l1_sha256,
        l1_uri=baseline.l1_uri,
    )


def _extract_str(obj: object, field: str) -> str | None:
    if isinstance(obj, Mapping):
        value = obj.get(field)
    else:
        value = getattr(obj, field, None)
    if isinstance(value, str):
        return value
    return None


def _extract_str_list(obj: object, field: str) -> tuple[str, ...] | None:
    if isinstance(obj, Mapping):
        value = obj.get(field)
    else:
        value = getattr(obj, field, None)
    if not isinstance(value, (list, tuple)):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return tuple(value)


def _iter_fenced_blocks(text: str) -> tuple[tuple[str, str], ...]:
    lines = text.splitlines()
    blocks: list[tuple[str, str]] = []
    in_fence = False
    fence_lang = ""
    current: list[str] = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("```"):
            if not in_fence:
                in_fence = True
                fence_lang = stripped[3:].strip().lower()
                current = []
                continue
            blocks.append((fence_lang, "\n".join(current).strip()))
            in_fence = False
            fence_lang = ""
            current = []
            continue
        if in_fence:
            current.append(line)
    return tuple(blocks)


def _decode_json_object(raw: str) -> dict[str, Any] | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(value, dict):
        return None
    return value


def _looks_like_pm_json(raw: str, lang: str) -> bool:
    del lang
    if PM_DECISION_SCHEMA_VERSION in raw:
        return True
    return _has_pm_key_hints(raw)


def _has_pm_key_hints(raw: str) -> bool:
    text = raw.lower()
    hits = 0
    for key in (
        '"worker_id"',
        '"stage"',
        '"material_id"',
        '"final_conclusion"',
        '"execution_conditions"',
        '"risk_conditions"',
        '"source_claim_ids"',
    ):
        if key in text:
            hits += 1
    has_identity_hint = PM_WORKER_ID in text or PM_STAGE.value in text
    return has_identity_hint and hits >= 3


def _looks_like_pm_payload_dict(payload: dict[str, Any]) -> bool:
    hits = 0
    for key in PM_DECISION_REQUIRED_FIELDS:
        if key in payload:
            hits += 1
    has_identity_hint = payload.get("worker_id") == PM_WORKER_ID or payload.get("stage") == PM_STAGE.value
    return has_identity_hint and hits >= 4


def _expect_str(payload: dict[str, Any], field: str) -> str | None:
    value = payload.get(field)
    if isinstance(value, str):
        return value
    return None


def _expect_str_list(payload: dict[str, Any], field: str) -> tuple[str, ...] | None:
    value = payload.get(field)
    if not isinstance(value, list):
        return None
    if not all(isinstance(item, str) for item in value):
        return None
    return tuple(value)


def _failed_pm_owner(reason: str, paths: tuple[Path, ...]) -> GuardResult:
    return guard_failed(category="pm_owner", reason=reason, paths=paths)


def _empty_pm_decision(material: ApprovedMaterial) -> PMDecision:
    return PMDecision(
        schema_version=PM_DECISION_SCHEMA_VERSION,
        run_id=material.run_id,
        call_id=material.call_id,
        worker_id=PM_WORKER_ID,
        stage=PM_STAGE,
        material_id=material.material_id,
        rating="not_rated",
        final_conclusion="",
        execution_conditions=(),
        risk_conditions=(),
        source_claim_ids=(),
        source_l1_sha256=material.l1_sha256,
        l1_uri=material.l1_uri,
    )


def _evidence_paths(evidence: ProviderEvidence) -> tuple[Path, ...]:
    return (evidence.provider_request_path,)


def receipt_material_id(call: WorkerCall, receipt: MaterialReceipt) -> str:
    return make_material_id(call, receipt)
