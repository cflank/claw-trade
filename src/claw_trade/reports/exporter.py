from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import shutil
from typing import Any, Callable, Protocol

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
)
from claw_trade.workflow.models import ExportResult, Stage, WorkflowState
from claw_trade.workflow.workers import all_worker_ids

_PM_WORKER_ID = "portfolio_manager"
_REPORT_POLISHER_WORKER_ID = "report_polisher"
_REPORT_WORKER_ORDER = all_worker_ids()
# 导出输入边界：报告必须覆盖完整报告链路的 approved materials，不能按“现有多少算多少”降级。
_REPORT_WORKERS_SET = set(_REPORT_WORKER_ORDER)
_WORKER_SECTION_TITLES: dict[str, str] = {
    "market_analyst": "图表与技术面分析",
    "fundamental_analyst": "基本面分析",
    "news_analyst": "新闻与宏观事件分析",
    "social_analyst": "社媒与情绪分析",
    "bull_researcher": "投资辩论（多头观点）",
    "bear_researcher": "投资辩论（空头观点）",
    "research_manager": "研究经理综合决策",
    "trader": "交易执行计划",
    "risk_challenger": "风险辩论（挑战方）",
    "risk_guardian": "风险辩论（防守方）",
    "risk_moderator": "风险辩论（中立整合）",
    "portfolio_manager": "最终裁决 / 最终投资决策",
    "report_polisher": "读者版最终报告",
}


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
    category: str | None
    reason: str | None
    paths: tuple[Path, ...]

    @classmethod
    def passed(
        cls,
        *,
        materials: tuple[ApprovedMaterial, ...],
        report_materials: tuple[ReportMaterial, ...],
    ) -> ReportMaterialsResult:
        return cls(
            ok=True,
            materials=materials,
            report_materials=report_materials,
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
            category=category,
            reason=reason,
            paths=paths,
        )


@dataclass(frozen=True)
class RenderedReport:
    text: str
    claim_links: tuple[ExportClaim, ...]


@dataclass(frozen=True)
class WorkerAppendix:
    material: ApprovedMaterial
    relative_path: Path
    title: str
    text: str


@dataclass(frozen=True)
class ReportImageAsset:
    source_path: Path
    relative_path: Path
    alt_text: str
    cleanup_source: bool


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

    workers_present = {material.worker_id for material in materials}
    missing_workers = sorted(required_report_workers() - workers_present)
    if missing_workers:
        return ReportMaterialsResult.failed(
            category="export_missing_material",
            reason=f"缺少 approved materials: {missing_workers}",
            paths=(state.run_dir / "openviking" / "approved-manifest.json",),
        )

    ordered_materials = _ordered_materials(tuple(materials))
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

    pm_materials = [material for material in ordered_materials if material.worker_id == _PM_WORKER_ID]
    if not pm_materials:
        return ReportMaterialsResult.failed(
            category="export_missing_material",
            reason="缺少 portfolio_manager approved material",
            paths=(state.run_dir / "openviking" / "approved-manifest.json",),
        )
    return ReportMaterialsResult.passed(
        materials=ordered_materials,
        report_materials=tuple(report_materials),
    )

def render_final_report(
    materials: tuple[ApprovedMaterial, ...],
    *,
    report_materials: tuple[ReportMaterial, ...] = (),
    pm_report_text: str | None = None,
) -> RenderedReport:
    ordered_materials = _ordered_materials(materials)
    report_text_by_material_id = _report_text_by_material_id(report_materials)
    claim_links = _claim_links_from_materials(ordered_materials)
    polisher_text = _latest_report_text(report_materials, _REPORT_POLISHER_WORKER_ID).strip()
    if polisher_text:
        return RenderedReport(text=polisher_text + "\n", claim_links=claim_links)

    lines: list[str] = ["# 最终投资报告", ""]
    pm_text = _latest_report_text(report_materials, _PM_WORKER_ID)
    if not pm_text:
        pm_text = (pm_report_text or "").strip()
    lines.extend(
        (
            f"## {_WORKER_SECTION_TITLES[_PM_WORKER_ID]}",
            pm_text or "（PM 原文缺失）",
        )
    )

    for material in ordered_materials:
        if material.worker_id in {_PM_WORKER_ID, _REPORT_POLISHER_WORKER_ID}:
            continue
        lines.append("")
        lines.append(f"## {_section_title(material)}")
        section_text = report_text_by_material_id.get(material.material_id, "").strip()
        lines.append(section_text or "（报告原文缺失）")

    return RenderedReport(text="\n".join(lines).strip() + "\n", claim_links=claim_links)


def build_worker_appendices(
    materials: tuple[ApprovedMaterial, ...],
    *,
    report_materials: tuple[ReportMaterial, ...] = (),
) -> tuple[WorkerAppendix, ...]:
    report_text_by_material_id = _report_text_by_material_id(report_materials)
    appendix_materials = tuple(
        material for material in _ordered_materials(materials) if material.worker_id != _REPORT_POLISHER_WORKER_ID
    )
    appendices: list[WorkerAppendix] = []
    for index, material in enumerate(appendix_materials, start=1):
        title = _section_title(material)
        section_text = report_text_by_material_id.get(material.material_id, "").strip() or "（报告原文缺失）"
        file_name = f"{index:02d}-{_safe_filename_token(material.worker_id)}.md"
        appendices.append(
            WorkerAppendix(
                material=material,
                relative_path=Path("worker-appendix") / file_name,
                title=title,
                text=f"# {title}\n\n{section_text}\n",
            )
        )
    return tuple(appendices)


def build_export_claim_mapping(
    rendered: RenderedReport,
    materials: tuple[ApprovedMaterial, ...],
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
    )


def run_export_guards(
    mapping: ExportClaimMapping,
    materials: tuple[ApprovedMaterial, ...],
    state: WorkflowState | None = None,
) -> GuardResult:
    checks: list[GuardResult] = []
    if state is not None:
        checks.append(validate_export_mapping_identity(mapping, state))
    checks.append(validate_export_claims_are_supported(mapping, materials))
    return combine_guard_results(tuple(checks))


def persist_export_outputs(
    state: WorkflowState,
    rendered: RenderedReport,
    image_assets: tuple[ReportImageAsset, ...],
    worker_appendices: tuple[WorkerAppendix, ...],
    mapping: ExportClaimMapping,
    guard: GuardResult,
    chart_cleanup: Callable[[WorkflowState, tuple[ReportImageAsset, ...]], _CopyResult] | None = None,
) -> ExportResult:
    reports_dir = state.run_dir / "reports"
    reports_dir.mkdir(parents=True, exist_ok=True)
    final_report_path = reports_dir / "final-report.md"
    mapping_path = reports_dir / "export-claims.json"
    guard_path = reports_dir / "export-guard-results.json"

    copy_result = _copy_report_image_assets(reports_dir=reports_dir, image_assets=image_assets)
    if not copy_result.ok:
        return ExportResult.failed(
            state=state,
            category=copy_result.category or "export_report_assets",
            reason=copy_result.reason or "报告图表资产复制失败",
            paths=copy_result.paths or (reports_dir,),
        )

    final_report_path.write_text(rendered.text, encoding="utf-8")
    appendix_result = _write_worker_appendices(reports_dir=reports_dir, appendices=worker_appendices)
    if not appendix_result.ok:
        return ExportResult.failed(
            state=state,
            category=appendix_result.category or "export_worker_appendix",
            reason=appendix_result.reason or "worker 原文附录写入失败",
            paths=appendix_result.paths or (reports_dir / "worker-appendix",),
        )
    _write_json(mapping_path, mapping)
    _write_json(guard_path, guard)

    fail_paths: list[Path] = [final_report_path, mapping_path, guard_path, *appendix_result.paths]
    for path in guard.paths:
        if path not in fail_paths:
            fail_paths.append(path)
    if not guard.ok:
        return ExportResult.failed(
            state=state,
            category=guard.category or "export_truthfulness",
            reason=guard.reason or "export guard 失败",
            paths=tuple(fail_paths),
        )

    cleanup_result = (chart_cleanup or _cleanup_report_chart_references)(state, image_assets)
    if not cleanup_result.ok:
        cleanup_paths = list(cleanup_result.paths)
        for path in fail_paths:
            if path not in cleanup_paths:
                cleanup_paths.append(path)
        return ExportResult.failed(
            state=state,
            category=cleanup_result.category or "export_chart_cleanup",
            reason=cleanup_result.reason or "临时图表引用清理失败",
            paths=tuple(cleanup_paths),
        )

    # 导出结果由 runner/store 统一写 export-result.json，避免 exporter 与 store 双写不一致。
    return ExportResult.passed(state=state, final_report_path=final_report_path, guard_path=guard_path)


def export_final_report(
    state: WorkflowState,
    manifest: ApprovedManifest,
    openviking: OpenVikingApprovedL1Reader,
    report_image_assets: tuple[Path, ...] | None = None,
    chart_cleanup: Callable[[WorkflowState, tuple[ReportImageAsset, ...]], _CopyResult] | None = None,
) -> ExportResult:
    loaded = load_report_materials(state=state, manifest=manifest, openviking=openviking)
    if not loaded.ok:
        return ExportResult.failed(
            state=state,
            category=loaded.category or "export_missing_material",
            reason=loaded.reason or "报告导出材料装载失败",
            paths=loaded.paths or (state.run_dir / "openviking" / "approved-manifest.json",),
        )
    rendered = render_final_report(
        materials=loaded.materials,
        report_materials=loaded.report_materials,
        pm_report_text=_pm_report_text(loaded.report_materials),
    )
    worker_appendices = build_worker_appendices(
        materials=loaded.materials,
        report_materials=loaded.report_materials,
    )
    image_assets = _collect_report_image_assets(
        state=state,
        materials=loaded.materials,
        report_image_assets=report_image_assets,
    )
    if not image_assets:
        return ExportResult.failed(
            state=state,
            category="export_report_assets",
            reason="报告导出失败：未找到可复制的图表资产",
            paths=(state.run_dir / "calls",),
        )
    rendered = _attach_report_image_assets(rendered=rendered, image_assets=image_assets)
    mapping = build_export_claim_mapping(
        rendered=rendered,
        materials=loaded.materials,
    )
    guard = run_export_guards(mapping=mapping, materials=loaded.materials, state=state)
    return persist_export_outputs(
        state=state,
        rendered=rendered,
        image_assets=image_assets,
        worker_appendices=worker_appendices,
        mapping=mapping,
        guard=guard,
        chart_cleanup=chart_cleanup,
    )


class FinalReportExporter:
    def __init__(
        self,
        *,
        openviking: OpenVikingApprovedL1Reader,
        report_image_assets: tuple[Path, ...] | None = None,
        chart_cleanup: Callable[[WorkflowState, tuple[ReportImageAsset, ...]], _CopyResult] | None = None,
    ) -> None:
        self.openviking = openviking
        self.report_image_assets = report_image_assets
        self.chart_cleanup = chart_cleanup

    def export(self, state: WorkflowState, manifest: ApprovedManifest) -> ExportResult:
        return export_final_report(
            state=state,
            manifest=manifest,
            openviking=self.openviking,
            report_image_assets=self.report_image_assets,
            chart_cleanup=self.chart_cleanup,
        )


def _ordered_materials(materials: tuple[ApprovedMaterial, ...]) -> tuple[ApprovedMaterial, ...]:
    return tuple(sorted(materials, key=_report_material_order_key))


def _report_material_order_key(material: ApprovedMaterial) -> tuple[int, int, int, str]:
    stage_order = {
        Stage.FRONTLINE: 0,
        Stage.INVESTMENT_DEBATE: 1,
        Stage.INVESTMENT_DECISION: 2,
        Stage.TRADE_DECISION: 3,
        Stage.RISK_DEBATE: 4,
        Stage.PORTFOLIO_DECISION: 5,
        Stage.FINAL_REPORT: 6,
    }
    worker_order = {worker_id: index for index, worker_id in enumerate(_REPORT_WORKER_ORDER)}
    return (
        stage_order.get(material.stage, 99),
        material.turn_index,
        worker_order.get(material.worker_id, 999),
        material.call_id,
    )


def _pm_report_text(report_materials: tuple[ReportMaterial, ...]) -> str:
    for item in reversed(report_materials):
        if item.material.worker_id == _PM_WORKER_ID:
            return item.l1_text
    return ""


def _report_text_by_material_id(report_materials: tuple[ReportMaterial, ...]) -> dict[str, str]:
    return {item.material.material_id: item.l1_text for item in report_materials}


def _latest_report_text(report_materials: tuple[ReportMaterial, ...], worker_id: str) -> str:
    for item in reversed(report_materials):
        if item.material.worker_id == worker_id:
            return item.l1_text
    return ""


def _section_title(material: ApprovedMaterial) -> str:
    title = _WORKER_SECTION_TITLES.get(material.worker_id, material.worker_id)
    if material.stage in {Stage.INVESTMENT_DEBATE, Stage.RISK_DEBATE} and material.round_index > 1:
        return f"{title}（第{material.round_index}轮）"
    return title


def _claim_link_from_material(material: ApprovedMaterial, claim: L1Claim) -> ExportClaim:
    export_kind = _normalize_export_claim_kind(claim.kind)
    return ExportClaim(
        export_claim_id=f"export-claim-{material.worker_id}-t{material.turn_index:02d}-{claim.claim_id}",
        text=claim.text,
        kind=export_kind,
        source_material_ids=(material.material_id,),
        source_claim_ids=(claim.claim_id,),
        source_l1_sha256=(material.l1_sha256,),
    )


def _claim_links_from_materials(materials: tuple[ApprovedMaterial, ...]) -> tuple[ExportClaim, ...]:
    return tuple(
        _claim_link_from_material(material, claim)
        for material in materials
        for claim in material.l1_claims
    )


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


def _write_worker_appendices(*, reports_dir: Path, appendices: tuple[WorkerAppendix, ...]) -> _CopyResult:
    appendix_dir = reports_dir / "worker-appendix"
    written_paths: list[Path] = []
    try:
        if appendix_dir.exists():
            shutil.rmtree(appendix_dir)
        appendix_dir.mkdir(parents=True, exist_ok=True)
        for appendix in appendices:
            path = reports_dir / appendix.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(appendix.text, encoding="utf-8")
            written_paths.append(path)
        index_path = appendix_dir / "index.md"
        index_path.write_text(_render_worker_appendix_index(appendices), encoding="utf-8")
        written_paths.append(index_path)
    except OSError as exc:
        return _CopyResult(
            ok=False,
            category="export_worker_appendix",
            reason=f"worker 原文附录写入失败: {exc}",
            paths=tuple(written_paths + [appendix_dir]),
        )
    return _CopyResult(ok=True, paths=tuple(written_paths))


def _render_worker_appendix_index(appendices: tuple[WorkerAppendix, ...]) -> str:
    lines = [
        "# Worker 原文附录索引",
        "",
        "| order | worker | stage | section | file | chars |",
        "|---:|---|---|---|---|---:|",
    ]
    for index, appendix in enumerate(appendices, start=1):
        file_name = appendix.relative_path.name
        lines.append(
            f"| {index} | `{appendix.material.worker_id}` | `{appendix.material.stage.value}` | "
            f"{appendix.title} | [{file_name}]({file_name}) | {len(appendix.text)} |"
        )
    return "\n".join(lines) + "\n"


@dataclass(frozen=True)
class _CopyResult:
    ok: bool
    category: str | None = None
    reason: str | None = None
    paths: tuple[Path, ...] = ()


def _copy_report_image_assets(*, reports_dir: Path, image_assets: tuple[ReportImageAsset, ...]) -> _CopyResult:
    if not image_assets:
        return _CopyResult(ok=True)
    assets_dir = reports_dir / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)
    copied_paths: list[Path] = []
    try:
        for asset in image_assets:
            destination = reports_dir / asset.relative_path
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(asset.source_path, destination)
            copied_paths.append(destination)
    except OSError as exc:
        return _CopyResult(
            ok=False,
            category="export_report_assets",
            reason=f"报告图表资产复制失败: {exc}",
            paths=tuple(copied_paths + [asset.source_path]),
        )
    return _CopyResult(ok=True)


def _attach_report_image_assets(rendered: RenderedReport, image_assets: tuple[ReportImageAsset, ...]) -> RenderedReport:
    image_block = _render_report_image_asset_block(image_assets)
    report_text = rendered.text.rstrip()
    updated_text = _attach_image_assets_to_technical_indicator_section(report_text, image_block)
    if updated_text != report_text:
        return RenderedReport(text=updated_text.strip() + "\n", claim_links=rendered.claim_links)

    market_title = f"## {_WORKER_SECTION_TITLES['market_analyst']}"
    next_section_title = f"## {_WORKER_SECTION_TITLES['fundamental_analyst']}"
    next_section_marker = f"\n{next_section_title}\n"

    if market_title in report_text and next_section_marker in report_text:
        before_next_section, _, after_next_section = report_text.partition(next_section_marker)
        updated_market_section = _attach_image_assets_to_technical_indicator_section(before_next_section, image_block)
        if updated_market_section != before_next_section:
            report_text = f"{updated_market_section.rstrip()}\n\n{next_section_title}\n{after_next_section}"
        else:
            report_text = f"{before_next_section.rstrip()}\n\n{image_block}\n\n{next_section_title}\n{after_next_section}"
    else:
        report_text = f"{report_text}\n\n{image_block}"

    return RenderedReport(text=report_text.strip() + "\n", claim_links=rendered.claim_links)


def _render_report_image_asset_block(image_assets: tuple[ReportImageAsset, ...]) -> str:
    lines = ["### 技术图表"]
    for asset in image_assets:
        lines.append(f"![{asset.alt_text}]({asset.relative_path.as_posix()})")
    return "\n".join(lines)


def _attach_image_assets_to_technical_indicator_section(report_text: str, image_block: str) -> str:
    heading_markers = (
        "\n## 二、技术指标分析\n",
        "\n## 技术指标分析\n",
        "\n### 技术指标分析\n",
        "\n## 2. Technical Market Analysis\n",
        "\n### 2. Technical Market Analysis\n",
        "\n## Technical Market Analysis\n",
        "\n### Technical Market Analysis\n",
        "\n## Technical Indicator Analysis\n",
        "\n### Technical Indicator Analysis\n",
    )
    marker_positions = ((report_text.find(marker), marker) for marker in heading_markers)
    matches = [(position, marker) for position, marker in marker_positions if position >= 0]
    if matches:
        position, marker = min(matches, key=lambda item: item[0])
        before_heading = report_text[:position]
        after_heading = report_text[position + len(marker) :]
        heading = marker.strip()
        return f"{before_heading.rstrip()}\n\n{heading}\n\n{image_block}\n\n{after_heading.lstrip()}"
    return report_text


def _collect_report_image_assets(
    *,
    state: WorkflowState,
    materials: tuple[ApprovedMaterial, ...],
    report_image_assets: tuple[Path, ...] | None,
) -> tuple[ReportImageAsset, ...]:
    source_paths: list[Path]
    if report_image_assets is None:
        source_paths = _discover_market_chart_images(state=state, materials=materials)
    else:
        source_paths = [Path(path) for path in report_image_assets]

    assets: list[ReportImageAsset] = []
    used_names: set[str] = set()
    for index, source_path in enumerate(source_paths, start=1):
        if not source_path.exists() or not source_path.is_file():
            continue
        suffix = source_path.suffix.lower()
        if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
            continue
        stem = _safe_filename_token(source_path.stem)
        base_name = f"market-{index:02d}-{stem}{suffix}"
        file_name = base_name
        dedupe_seq = 2
        while file_name in used_names:
            file_name = f"market-{index:02d}-{stem}-{dedupe_seq}{suffix}"
            dedupe_seq += 1
        used_names.add(file_name)
        assets.append(
            ReportImageAsset(
                source_path=source_path,
                relative_path=Path("assets") / file_name,
                alt_text=f"market-chart-{index}",
                cleanup_source=_is_transient_market_chart_source(state=state, source_path=source_path),
            )
        )
    return tuple(assets)


def _discover_market_chart_images(*, state: WorkflowState, materials: tuple[ApprovedMaterial, ...]) -> list[Path]:
    sources: list[Path] = []
    seen: set[str] = set()
    for material in materials:
        if material.worker_id != "market_analyst":
            continue
        call_dir = state.run_dir / "calls" / material.call_id
        candidate_dirs = (
            call_dir / "pack-tool-evidence" / "techlab" / "charts-local",
            call_dir / "evidence" / "techlab" / "charts-local",
            call_dir / "techlab" / "charts-local",
        )
        for chart_dir in candidate_dirs:
            if not chart_dir.exists() or not chart_dir.is_dir():
                continue
            for path in sorted(chart_dir.glob("*")):
                path_key = str(path.resolve())
                if path_key in seen:
                    continue
                seen.add(path_key)
                sources.append(path)
    return sources


_SAFE_ASSET_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_filename_token(value: str) -> str:
    cleaned = _SAFE_ASSET_TOKEN_RE.sub("-", value.strip()).strip("-._")
    return cleaned or "chart"


def _is_transient_market_chart_source(*, state: WorkflowState, source_path: Path) -> bool:
    try:
        resolved = source_path.resolve()
        calls_root = (state.run_dir / "calls").resolve()
    except OSError:
        return False
    if not _is_relative_to(resolved, calls_root):
        return False
    parts = tuple(item.lower() for item in resolved.parts)
    return "charts-local" in parts


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _cleanup_report_chart_references(state: WorkflowState, image_assets: tuple[ReportImageAsset, ...]) -> _CopyResult:
    cleaned_paths: list[Path] = []
    for asset in image_assets:
        if not asset.cleanup_source:
            continue
        source_path = asset.source_path
        try:
            if source_path.exists():
                source_path.unlink()
                cleaned_paths.append(source_path)
        except OSError as exc:
            return _CopyResult(
                ok=False,
                category="export_chart_cleanup",
                reason=f"临时图表引用清理失败: {exc}",
                paths=tuple(cleaned_paths + [source_path, state.run_dir / "reports" / "assets"]),
            )
    return _CopyResult(ok=True)


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
