from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Protocol

from claw_trade.artifacts.manifest import ApprovedManifest
from claw_trade.artifacts.openviking_client import OpenVikingReadResult
from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim
from claw_trade.guards.common import GuardResult, combine_guard_results
from claw_trade.guards.export_claims import (
    EXPORT_CLAIM_ALLOWED_KINDS,
    EXPORT_CLAIM_SCHEMA_VERSION,
    ExportClaim,
    ExportClaimMapping,
    validate_export_claims_are_supported,
    validate_export_mapping_identity,
    validate_export_pm_fields,
)
from claw_trade.guards.pm_owner import PMDecision, load_pm_decision_for_material
from claw_trade.workflow.models import ExportResult, WorkflowState
from claw_trade.workflow.workers import all_worker_ids

_PM_WORKER_ID = "portfolio_manager"
_REPORT_WORKER_ORDER = all_worker_ids()
# 导出输入边界：报告必须覆盖固定 12 worker 的 approved materials，不能按“现有多少算多少”降级。
_REPORT_WORKERS_SET = set(_REPORT_WORKER_ORDER)


class OpenVikingApprovedL1Reader(Protocol):
    def read_approved_l1(self, material: ApprovedMaterial) -> OpenVikingReadResult: ...


@dataclass(frozen=True)
class ReportMaterial:
    material: ApprovedMaterial
    l1_text: str


@dataclass(frozen=True)
class ReportMaterialsResult:
    ok: bool
    materials: tuple[ApprovedMaterial, ...]
    report_materials: tuple[ReportMaterial, ...]
    pm_decision: PMDecision | None
    category: str | None
    reason: str | None
    paths: tuple[Path, ...]

    @classmethod
    def passed(
        cls,
        *,
        materials: tuple[ApprovedMaterial, ...],
        report_materials: tuple[ReportMaterial, ...],
        pm_decision: PMDecision,
    ) -> ReportMaterialsResult:
        return cls(
            ok=True,
            materials=materials,
            report_materials=report_materials,
            pm_decision=pm_decision,
            category=None,
            reason=None,
            paths=(),
        )

    @classmethod
    def failed(
        cls,
        *,
        category: str,
        reason: str,
        paths: tuple[Path, ...],
    ) -> ReportMaterialsResult:
        return cls(
            ok=False,
            materials=(),
            report_materials=(),
            pm_decision=None,
            category=category,
            reason=reason,
            paths=paths,
        )


@dataclass(frozen=True)
class RenderedReport:
    text: str
    claim_links: tuple[ExportClaim, ...]


def required_report_workers() -> set[str]:
    return set(_REPORT_WORKERS_SET)


def load_report_materials(
    state: WorkflowState,
    manifest: ApprovedManifest,
    openviking: OpenVikingApprovedL1Reader,
) -> ReportMaterialsResult:
    materials = manifest.all_for_run(state.run_id)
    if not materials:
        return ReportMaterialsResult.failed(
            category="export_missing_material",
            reason="缺少 approved materials: 全部 worker 都不存在",
            paths=(state.run_dir / "openviking" / "approved-manifest.json",),
        )

    material_by_worker = {material.worker_id: material for material in materials}
    missing_workers = sorted(required_report_workers() - set(material_by_worker))
    if missing_workers:
        return ReportMaterialsResult.failed(
            category="export_missing_material",
            reason=f"缺少 approved materials: {missing_workers}",
            paths=(state.run_dir / "openviking" / "approved-manifest.json",),
        )

    ordered_materials = tuple(material_by_worker[worker_id] for worker_id in _REPORT_WORKER_ORDER)
    report_materials: list[ReportMaterial] = []
    for material in ordered_materials:
        # 导出边界：L1 只能从 OpenViking 正式 URI 读取，不能读 runs/<run>/openviking 或 call 目录本地审计副本。
        read_result = openviking.read_approved_l1(material)
        if not read_result.ok or read_result.content is None:
            return ReportMaterialsResult.failed(
                category="export_openviking_read",
                reason=(
                    f"读取 approved material 的 OpenViking L1 失败: worker={material.worker_id} "
                    f"category={read_result.error_category} reason={read_result.error_message}"
                ),
                paths=(state.run_dir / "openviking" / "approved-manifest.json", material.hard_gate_result_path),
            )
        try:
            l1_text = read_result.content.decode("utf-8")
        except UnicodeDecodeError as exc:
            return ReportMaterialsResult.failed(
                category="export_openviking_read",
                reason=f"读取 approved material 的 OpenViking L1 解码失败: worker={material.worker_id} ({exc})",
                paths=(state.run_dir / "openviking" / "approved-manifest.json", material.hard_gate_result_path),
            )
        report_materials.append(ReportMaterial(material=material, l1_text=l1_text))

    pm_material = material_by_worker[_PM_WORKER_ID]
    # PM owner 边界：PM 决策只能来自 pm-decision.json 证据文件，不能从 L1 自然语言或机器块反推。
    pm_decision, pm_guard = load_pm_decision_for_material(pm_material)
    if not pm_guard.ok:
        return ReportMaterialsResult.failed(
            category=pm_guard.category,
            reason=pm_guard.reason or "PM decision block 校验失败",
            paths=pm_guard.paths or (pm_material.hard_gate_result_path,),
        )
    return ReportMaterialsResult.passed(
        materials=ordered_materials,
        report_materials=tuple(report_materials),
        pm_decision=pm_decision,
    )


def render_final_report(materials: tuple[ApprovedMaterial, ...], pm_decision: PMDecision) -> RenderedReport:
    ordered_materials = _ordered_materials(materials)
    claim_links: list[ExportClaim] = []
    lines: list[str] = [
        "# 最终投资报告",
        "",
        "## PM 最终决策",
        f"- 评级：{pm_decision.rating}",
        f"- 最终结论：{pm_decision.final_conclusion}",
        "- 执行条件：",
    ]
    if pm_decision.execution_conditions:
        for item in pm_decision.execution_conditions:
            lines.append(f"  - {item}")
    else:
        lines.append("  - （无）")
    lines.append("- 风险条件：")
    if pm_decision.risk_conditions:
        for item in pm_decision.risk_conditions:
            lines.append(f"  - {item}")
    else:
        lines.append("  - （无）")

    for material in ordered_materials:
        lines.extend(
            (
                "",
                f"## {material.worker_id}",
                f"- 阶段：{material.stage.value}",
                f"- L1 URI：{material.l1_uri}",
                f"- L1 SHA256：{material.l1_sha256}",
                f"- L2 Index：{material.l2_index_uri or material.l2_index.empty_reason or '无'}",
                "- 已批准声明：",
            )
        )
        if material.l1_claims:
            for claim in material.l1_claims:
                lines.append(f"  - [{claim.claim_id}] {claim.text}")
                claim_links.append(_claim_link_from_material(material, claim))
        else:
            lines.append("  - （无）")

    claim_links.extend(_pm_claim_links(pm_decision=pm_decision, materials=ordered_materials))
    return RenderedReport(text="\n".join(lines).strip() + "\n", claim_links=tuple(claim_links))


def build_export_claim_mapping(
    rendered: RenderedReport,
    materials: tuple[ApprovedMaterial, ...],
    pm_decision: PMDecision,
) -> ExportClaimMapping:
    if not materials:
        raise ValueError("build_export_claim_mapping 需要非空 materials")
    # 这里仅使用渲染阶段显式携带的 claim link，禁止从 final report 自然语言反向猜测 claim。
    deduped_claims = _dedupe_claim_links(rendered.claim_links)
    return ExportClaimMapping(
        schema_version=EXPORT_CLAIM_SCHEMA_VERSION,
        run_id=materials[0].run_id,
        final_report_path="reports/final-report.md",
        claims=deduped_claims,
        pm_decision={
            "source_material_id": pm_decision.material_id,
            "rating": pm_decision.rating,
            "final_conclusion": pm_decision.final_conclusion,
            "execution_conditions": list(pm_decision.execution_conditions),
            "risk_conditions": list(pm_decision.risk_conditions),
        },
    )


def run_export_guards(
    mapping: ExportClaimMapping,
    materials: tuple[ApprovedMaterial, ...],
    pm_decision: PMDecision,
    state: WorkflowState | None = None,
) -> GuardResult:
    checks: list[GuardResult] = []
    if state is not None:
        checks.append(validate_export_mapping_identity(mapping, state))
    checks.append(validate_export_claims_are_supported(mapping, materials))
    # PM owner 防线：export 只允许复述 PM 结构化决策，不允许改写关键字段。
    checks.append(validate_export_pm_fields(mapping, pm_decision))
    return combine_guard_results(tuple(checks))


def persist_export_outputs(
    state: WorkflowState,
    rendered: RenderedReport,
    mapping: ExportClaimMapping,
    guard: GuardResult,
) -> ExportResult:
    reports_dir = state.run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    final_report_path = reports_dir / "final-report.md"
    mapping_path = reports_dir / "export-claims.json"
    guard_path = reports_dir / "export-guard-results.json"

    final_report_path.write_text(rendered.text, encoding="utf-8")
    _write_json(mapping_path, mapping)
    _write_json(guard_path, guard)

    if guard.ok:
        # 导出结果由 runner/store 统一写 export-result.json，避免 exporter 与 store 双写不一致。
        return ExportResult.passed(state=state, final_report_path=final_report_path, guard_path=guard_path)

    fail_paths: list[Path] = [final_report_path, mapping_path, guard_path]
    for path in guard.paths:
        if path not in fail_paths:
            fail_paths.append(path)
    return ExportResult.failed(
        state=state,
        category=guard.category or "export_truthfulness",
        reason=guard.reason or "export guard 失败",
        paths=tuple(fail_paths),
    )


def export_final_report(
    state: WorkflowState,
    manifest: ApprovedManifest,
    openviking: OpenVikingApprovedL1Reader,
) -> ExportResult:
    loaded = load_report_materials(state=state, manifest=manifest, openviking=openviking)
    if not loaded.ok or loaded.pm_decision is None:
        return ExportResult.failed(
            state=state,
            category=loaded.category or "export_missing_material",
            reason=loaded.reason or "报告导出材料装载失败",
            paths=loaded.paths or (state.run_dir / "openviking" / "approved-manifest.json",),
        )
    rendered = render_final_report(materials=loaded.materials, pm_decision=loaded.pm_decision)
    mapping = build_export_claim_mapping(
        rendered=rendered,
        materials=loaded.materials,
        pm_decision=loaded.pm_decision,
    )
    guard = run_export_guards(mapping=mapping, materials=loaded.materials, pm_decision=loaded.pm_decision, state=state)
    return persist_export_outputs(state=state, rendered=rendered, mapping=mapping, guard=guard)


class FinalReportExporter:
    def __init__(self, *, openviking: OpenVikingApprovedL1Reader) -> None:
        self.openviking = openviking

    def export(self, state: WorkflowState, manifest: ApprovedManifest) -> ExportResult:
        return export_final_report(state=state, manifest=manifest, openviking=self.openviking)


def _ordered_materials(materials: tuple[ApprovedMaterial, ...]) -> tuple[ApprovedMaterial, ...]:
    material_by_worker = {material.worker_id: material for material in materials}
    return tuple(material_by_worker[worker_id] for worker_id in _REPORT_WORKER_ORDER if worker_id in material_by_worker)


def _claim_link_from_material(material: ApprovedMaterial, claim: L1Claim) -> ExportClaim:
    export_kind = _normalize_export_claim_kind(claim.kind)
    return ExportClaim(
        export_claim_id=f"export-claim-{material.worker_id}-{claim.claim_id}",
        text=claim.text,
        kind=export_kind,
        source_material_ids=(material.material_id,),
        source_claim_ids=(claim.claim_id,),
        source_l1_sha256=(material.l1_sha256,),
    )


def _pm_claim_links(pm_decision: PMDecision, materials: tuple[ApprovedMaterial, ...]) -> tuple[ExportClaim, ...]:
    pm_material = next(material for material in materials if material.material_id == pm_decision.material_id)
    shared_sources = {
        "source_material_ids": (pm_material.material_id,),
        "source_claim_ids": pm_decision.source_claim_ids,
        "source_l1_sha256": (pm_decision.source_l1_sha256,),
    }
    claims: list[ExportClaim] = [
        ExportClaim(
            export_claim_id="export-claim-pm-rating",
            text=pm_decision.rating,
            kind="rating",
            **shared_sources,
        ),
        ExportClaim(
            export_claim_id="export-claim-pm-final-conclusion",
            text=pm_decision.final_conclusion,
            kind="other",
            **shared_sources,
        ),
    ]
    for index, condition in enumerate(pm_decision.execution_conditions, start=1):
        claims.append(
            ExportClaim(
                export_claim_id=f"export-claim-pm-execution-{index}",
                text=condition,
                kind="trade_action",
                **shared_sources,
            )
        )
    for index, condition in enumerate(pm_decision.risk_conditions, start=1):
        claims.append(
            ExportClaim(
                export_claim_id=f"export-claim-pm-risk-{index}",
                text=condition,
                kind="risk_condition",
                **shared_sources,
            )
        )
    return tuple(claims)


def _dedupe_claim_links(claims: tuple[ExportClaim, ...]) -> tuple[ExportClaim, ...]:
    seen: set[tuple[Any, ...]] = set()
    out: list[ExportClaim] = []
    for claim in claims:
        key = (
            claim.export_claim_id,
            claim.text,
            claim.kind,
            claim.source_material_ids,
            claim.source_claim_ids,
            claim.source_l1_sha256,
        )
        if key in seen:
            continue
        seen.add(key)
        out.append(claim)
    return tuple(out)


def _normalize_export_claim_kind(kind: str) -> str:
    if kind in EXPORT_CLAIM_ALLOWED_KINDS:
        return kind
    if kind == "target_price":
        return "valuation"
    return "other"


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(_to_jsonable(payload), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def _to_jsonable(value: object) -> object:
    if hasattr(value, "__dataclass_fields__"):
        return {k: _to_jsonable(v) for k, v in asdict(value).items()}
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, tuple):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, list):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _to_jsonable(v) for k, v in value.items()}
    return value
