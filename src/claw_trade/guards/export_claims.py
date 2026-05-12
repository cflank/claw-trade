from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from claw_trade.artifacts.refs import ApprovedMaterial
from claw_trade.guards.common import GuardResult, guard_failed, guard_passed
from claw_trade.guards.pm_owner import PMDecision, validate_export_does_not_rewrite_pm
from claw_trade.workflow.models import WorkflowState

EXPORT_CLAIM_SCHEMA_VERSION = "control.export_claims.v1"
EXPORT_CLAIM_ALLOWED_KINDS = frozenset(
    {
        "rating",
        "trade_action",
        "risk_condition",
        "valuation",
        "news",
        "sentiment",
        "chart",
        "tool_success",
        "source",
        "other",
    }
)


@dataclass(frozen=True)
class ExportClaim:
    export_claim_id: str
    text: str
    kind: str
    source_material_ids: tuple[str, ...]
    source_claim_ids: tuple[str, ...]
    source_l1_sha256: tuple[str, ...]


@dataclass(frozen=True)
class ExportClaimMapping:
    schema_version: str
    run_id: str
    final_report_path: str
    claims: tuple[ExportClaim, ...]
    pm_decision: dict[str, object] | None


@dataclass(frozen=True)
class ExportClaimMappingResult:
    ok: bool
    mapping: ExportClaimMapping | None
    reason: str | None

    @classmethod
    def passed(cls, mapping: ExportClaimMapping) -> ExportClaimMappingResult:
        return cls(ok=True, mapping=mapping, reason=None)

    @classmethod
    def failed(cls, reason: str) -> ExportClaimMappingResult:
        return cls(ok=False, mapping=None, reason=reason)


def parse_export_claim_mapping(path: Path) -> ExportClaimMappingResult:
    if not path.exists():
        return ExportClaimMappingResult.failed(f"export claim mapping 文件不存在: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return ExportClaimMappingResult.failed("export claim mapping JSON 非法")
    except OSError as exc:
        return ExportClaimMappingResult.failed(f"export claim mapping 读取失败: {exc}")

    if not isinstance(payload, dict):
        return ExportClaimMappingResult.failed("export claim mapping 根对象必须是对象")

    required_fields = ("schema_version", "run_id", "final_report_path", "claims", "pm_decision")
    for field in required_fields:
        if field not in payload:
            return ExportClaimMappingResult.failed(f"export claim mapping 缺少字段: {field}")

    schema_version = _expect_str(payload, "schema_version")
    if schema_version is None:
        return ExportClaimMappingResult.failed("export claim mapping schema_version 必须是字符串")
    if schema_version != EXPORT_CLAIM_SCHEMA_VERSION:
        return ExportClaimMappingResult.failed("export claim mapping schema_version 不匹配")

    run_id = _expect_str(payload, "run_id")
    if run_id is None:
        return ExportClaimMappingResult.failed("export claim mapping run_id 必须是字符串")
    final_report_path = _expect_str(payload, "final_report_path")
    if final_report_path is None:
        return ExportClaimMappingResult.failed("export claim mapping final_report_path 必须是字符串")
    if not final_report_path.strip():
        return ExportClaimMappingResult.failed("export claim mapping final_report_path 不能为空")

    claims_raw = payload.get("claims")
    if not isinstance(claims_raw, list):
        return ExportClaimMappingResult.failed("export claim mapping claims 必须是数组")
    claims: list[ExportClaim] = []
    for index, claim_raw in enumerate(claims_raw):
        claim = _parse_claim_item(index, claim_raw)
        if isinstance(claim, str):
            return ExportClaimMappingResult.failed(claim)
        claims.append(claim)

    pm_decision = _parse_pm_decision(payload.get("pm_decision"))
    if isinstance(pm_decision, str):
        return ExportClaimMappingResult.failed(pm_decision)

    return ExportClaimMappingResult.passed(
        ExportClaimMapping(
            schema_version=EXPORT_CLAIM_SCHEMA_VERSION,
            run_id=run_id,
            final_report_path=final_report_path,
            claims=tuple(claims),
            pm_decision=pm_decision,
        )
    )


def validate_export_mapping_identity(mapping: ExportClaimMapping, state: WorkflowState) -> GuardResult:
    if mapping.run_id != state.run_id:
        return _failed_export_truthfulness("export claim mapping run_id 与 workflow state 不一致", paths=(Path("reports/export-claims.json"),))
    if not mapping.final_report_path.strip():
        return _failed_export_truthfulness("export claim mapping final_report_path 不能为空", paths=(Path("reports/export-claims.json"),))

    resolved_report = _resolve_final_report_path(mapping.final_report_path, state)
    if resolved_report is None:
        return _failed_export_truthfulness(
            "final_report_path 不在当前 run_dir/reports 目录内",
            paths=(Path(mapping.final_report_path),),
        )
    if resolved_report == (state.run_dir / "reports").resolve():
        return _failed_export_truthfulness(
            "final_report_path 不能是 reports 目录本身",
            paths=(Path(mapping.final_report_path),),
        )
    return guard_passed(category="export_truthfulness")


def validate_export_claims_are_supported(
    mapping: ExportClaimMapping,
    materials: tuple[ApprovedMaterial, ...],
) -> GuardResult:
    material_by_id = {material.material_id: material for material in materials}
    for claim in mapping.claims:
        if claim.kind not in EXPORT_CLAIM_ALLOWED_KINDS:
            return _failed_export_truthfulness(
                f"export claim kind 不支持: {claim.kind}",
                paths=(Path("reports/export-claims.json"),),
            )

        has_any_source = bool(claim.source_material_ids or claim.source_claim_ids or claim.source_l1_sha256)
        source_required = claim.kind != "other"
        if source_required and not has_any_source:
            return _failed_export_truthfulness(
                f"非 other 声明缺少来源: {claim.export_claim_id}",
                paths=(Path("reports/export-claims.json"),),
            )
        if has_any_source and (
            not claim.source_material_ids or not claim.source_claim_ids or not claim.source_l1_sha256
        ):
            return _failed_export_truthfulness(
                f"声明来源字段不完整: {claim.export_claim_id}",
                paths=(Path("reports/export-claims.json"),),
            )
        if not has_any_source:
            continue

        sha_set = set(claim.source_l1_sha256)
        source_claim_id_set = set(claim.source_claim_ids)
        referenced_materials: list[ApprovedMaterial] = []
        for material_id in claim.source_material_ids:
            material = material_by_id.get(material_id)
            if material is None:
                return _failed_export_truthfulness(
                    f"声明引用不存在的 material_id: {material_id}",
                    paths=(Path("reports/export-claims.json"),),
                )
            referenced_materials.append(material)

        referenced_sha_set = {material.l1_sha256 for material in referenced_materials}
        for source_sha in sha_set:
            if source_sha not in referenced_sha_set:
                return _failed_export_truthfulness(
                    f"声明引用未绑定 material 的 l1_sha256: {source_sha}",
                    paths=(Path("reports/export-claims.json"),),
                )

        claim_bindings: dict[str, set[str]] = {}
        for material in referenced_materials:
            if material.l1_sha256 not in sha_set:
                return _failed_export_truthfulness(
                    f"声明来源 SHA 不匹配 material: {material.material_id}",
                    paths=(Path("reports/export-claims.json"),),
                )
            material_claim_ids = {item.claim_id for item in material.l1_claims}
            if source_claim_id_set.isdisjoint(material_claim_ids):
                return _failed_export_truthfulness(
                    f"声明未指向 material 内 claim_id: {material.material_id}",
                    paths=(Path("reports/export-claims.json"),),
                )
            for source_claim_id in source_claim_id_set:
                if source_claim_id in material_claim_ids:
                    claim_bindings.setdefault(source_claim_id, set()).add(material.material_id)

        for source_claim_id in source_claim_id_set:
            bound_material_ids = claim_bindings.get(source_claim_id)
            if not bound_material_ids:
                return _failed_export_truthfulness(
                    f"声明 claim_id 未绑定到声明的 material: {source_claim_id}",
                    paths=(Path("reports/export-claims.json"),),
                )
            if not any(material_by_id[material_id].l1_sha256 in sha_set for material_id in bound_material_ids):
                return _failed_export_truthfulness(
                    f"声明 claim_id 找不到匹配的 material SHA: {source_claim_id}",
                    paths=(Path("reports/export-claims.json"),),
                )
    return guard_passed(category="export_truthfulness")


def validate_export_pm_fields(mapping: ExportClaimMapping, pm_decision: PMDecision | None) -> GuardResult:
    if pm_decision is None:
        if mapping.pm_decision is None:
            return guard_passed(category="pm_owner")
        return guard_failed(
            category="pm_owner",
            reason="CN_A 导出映射不应伪造结构化 pm_decision",
            paths=(Path("reports/export-claims.json"),),
        )

    # PM owner 防线：export 映射只能复述 PM 结构化决策，不能改写字段。
    guard = validate_export_does_not_rewrite_pm(pm_decision, mapping)
    if not guard.ok:
        return guard
    if mapping.pm_decision is None:
        return guard_failed(
            category="pm_owner",
            reason="export mapping 缺少 pm_decision",
            paths=(Path("reports/export-claims.json"),),
        )
    source_material_id = _expect_str(mapping.pm_decision, "source_material_id")
    if source_material_id is None:
        return guard_failed(
            category="pm_owner",
            reason="export mapping pm_decision.source_material_id 必须是字符串",
            paths=(Path("reports/export-claims.json"),),
        )
    if source_material_id != pm_decision.material_id:
        return guard_failed(
            category="pm_owner",
            reason="export mapping pm_decision.source_material_id 与 PM material_id 不一致",
            paths=(Path("reports/export-claims.json"),),
        )
    return guard_passed(category="pm_owner")


def _parse_claim_item(index: int, claim_raw: object) -> ExportClaim | str:
    if not isinstance(claim_raw, dict):
        return f"export claim[{index}] 必须是对象"
    required_fields = (
        "export_claim_id",
        "text",
        "kind",
        "source_material_ids",
        "source_claim_ids",
        "source_l1_sha256",
    )
    for field in required_fields:
        if field not in claim_raw:
            return f"export claim[{index}] 缺少字段: {field}"

    export_claim_id = _expect_str(claim_raw, "export_claim_id")
    if export_claim_id is None:
        return f"export claim[{index}] export_claim_id 必须是字符串"
    text = _expect_str(claim_raw, "text")
    if text is None:
        return f"export claim[{index}] text 必须是字符串"
    kind = _expect_str(claim_raw, "kind")
    if kind is None:
        return f"export claim[{index}] kind 必须是字符串"
    if kind not in EXPORT_CLAIM_ALLOWED_KINDS:
        return f"export claim[{index}] kind 不支持: {kind}"
    source_material_ids = _expect_str_list(claim_raw, "source_material_ids")
    if source_material_ids is None:
        return f"export claim[{index}] source_material_ids 必须是字符串数组"
    source_claim_ids = _expect_str_list(claim_raw, "source_claim_ids")
    if source_claim_ids is None:
        return f"export claim[{index}] source_claim_ids 必须是字符串数组"
    source_l1_sha256 = _expect_str_list(claim_raw, "source_l1_sha256")
    if source_l1_sha256 is None:
        return f"export claim[{index}] source_l1_sha256 必须是字符串数组"
    return ExportClaim(
        export_claim_id=export_claim_id,
        text=text,
        kind=kind,
        source_material_ids=source_material_ids,
        source_claim_ids=source_claim_ids,
        source_l1_sha256=source_l1_sha256,
    )


def _parse_pm_decision(pm_raw: object) -> dict[str, object] | None | str:
    if pm_raw is None:
        return None
    if not isinstance(pm_raw, dict):
        return "export claim mapping pm_decision 必须是对象"
    required_fields = (
        "source_material_id",
        "rating",
        "final_conclusion",
        "execution_conditions",
        "risk_conditions",
    )
    for field in required_fields:
        if field not in pm_raw:
            return f"export claim mapping pm_decision 缺少字段: {field}"
    if _expect_str(pm_raw, "source_material_id") is None:
        return "export claim mapping pm_decision.source_material_id 必须是字符串"
    if _expect_str(pm_raw, "rating") is None:
        return "export claim mapping pm_decision.rating 必须是字符串"
    if _expect_str(pm_raw, "final_conclusion") is None:
        return "export claim mapping pm_decision.final_conclusion 必须是字符串"
    if _expect_str_list(pm_raw, "execution_conditions") is None:
        return "export claim mapping pm_decision.execution_conditions 必须是字符串数组"
    if _expect_str_list(pm_raw, "risk_conditions") is None:
        return "export claim mapping pm_decision.risk_conditions 必须是字符串数组"
    return {
        "source_material_id": pm_raw["source_material_id"],
        "rating": pm_raw["rating"],
        "final_conclusion": pm_raw["final_conclusion"],
        "execution_conditions": list(pm_raw["execution_conditions"]),
        "risk_conditions": list(pm_raw["risk_conditions"]),
    }


def _resolve_final_report_path(raw_path: str, state: WorkflowState) -> Path | None:
    reports_root = (state.run_dir / "reports").resolve()
    candidate = Path(raw_path.strip())
    candidates: list[Path] = []
    if candidate.is_absolute():
        candidates.append(candidate.resolve())
    else:
        # 这里必须限定到本次 run/reports，防止路径穿越读到其他 run 或仓库任意位置。
        candidates.append((Path.cwd() / candidate).resolve())
        candidates.append((state.run_dir / candidate).resolve())
    for resolved in candidates:
        if _is_under_dir(resolved, reports_root):
            return resolved
    return None


def _is_under_dir(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


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


def _failed_export_truthfulness(reason: str, paths: tuple[Path, ...]) -> GuardResult:
    return guard_failed(category="export_truthfulness", reason=reason, paths=paths, early_stop=True)
