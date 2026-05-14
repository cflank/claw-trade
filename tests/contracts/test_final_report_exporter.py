from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path

from claw_trade.artifacts.manifest import ApprovedManifest
from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, L2Entry, L2Index
from claw_trade.guards.export_claims import ExportClaim, ExportClaimMapping, parse_export_claim_mapping
from claw_trade.reports.exporter import (
    build_export_claim_mapping,
    build_worker_appendices,
    export_final_report,
    load_report_materials,
    render_final_report,
    ReportMaterial,
    run_export_guards,
)
from claw_trade.workflow.models import RunRequest, RunStatus, Stage, WorkflowState

_WORKER_STAGE: tuple[tuple[str, Stage], ...] = (
    ("market_analyst", Stage.FRONTLINE),
    ("fundamental_analyst", Stage.FRONTLINE),
    ("news_analyst", Stage.FRONTLINE),
    ("social_analyst", Stage.FRONTLINE),
    ("bull_researcher", Stage.INVESTMENT_DEBATE),
    ("bear_researcher", Stage.INVESTMENT_DEBATE),
    ("research_manager", Stage.INVESTMENT_DECISION),
    ("trader", Stage.TRADE_DECISION),
    ("risk_challenger", Stage.RISK_DEBATE),
    ("risk_guardian", Stage.RISK_DEBATE),
    ("risk_moderator", Stage.RISK_DEBATE),
    ("portfolio_manager", Stage.PORTFOLIO_DECISION),
    ("report_polisher", Stage.FINAL_REPORT),
)


def test_export_final_report_fails_when_required_material_missing(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-missing")
    manifest, reader = _build_manifest_and_reader(state, missing_worker="trader")

    result = export_final_report(state=state, manifest=manifest, openviking=reader)

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.category == "export_missing_material"
    assert "trader" in (result.failure.reason or "")


def test_export_final_report_passes_with_pm_natural_language_only(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-cn-a-no-pm-json", profile="CN_A")
    manifest, reader = _build_manifest_and_reader(state)
    source_chart = state.run_dir / "calls" / "call-01" / "evidence" / "techlab" / "charts-local" / "market-structure.png"
    source_chart.parent.mkdir(parents=True, exist_ok=True)
    source_chart.write_bytes(b"\x89PNG\r\n\x1a\nreport-asset-cn-a")

    loaded = load_report_materials(state=state, manifest=manifest, openviking=reader)
    assert loaded.ok

    result = export_final_report(state=state, manifest=manifest, openviking=reader)

    assert result.status == "passed"
    assert result.final_report_path is not None
    report_text = result.final_report_path.read_text(encoding="utf-8")
    assert "# 贵州茅台（600519）投资研究报告" in report_text
    assert "组合经理最终裁决：维持审慎增持" in report_text


def test_export_final_report_passes_and_writes_outputs(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-pass")
    manifest, reader = _build_manifest_and_reader(state)
    source_chart = state.run_dir / "calls" / "call-01" / "evidence" / "techlab" / "charts-local" / "market-structure.png"
    source_chart.parent.mkdir(parents=True, exist_ok=True)
    source_chart.write_bytes(b"\x89PNG\r\n\x1a\nreport-asset-pass")

    result = export_final_report(state=state, manifest=manifest, openviking=reader)

    assert result.status == "passed"
    assert result.final_report_path == state.run_dir / "reports" / "final-report.md"
    assert result.export_guard_result_path == state.run_dir / "reports" / "export-guard-results.json"
    assert result.final_report_path is not None and result.final_report_path.exists()
    assert result.export_guard_result_path is not None and result.export_guard_result_path.exists()

    mapping_path = state.run_dir / "reports" / "export-claims.json"
    assert mapping_path.exists()
    parsed = parse_export_claim_mapping(mapping_path)
    assert parsed.ok
    assert parsed.mapping is not None
    assert parsed.mapping.run_id == state.run_id
    assert parsed.mapping.final_report_path == "reports/final-report.md"

    guard_payload = json.loads((state.run_dir / "reports" / "export-guard-results.json").read_text(encoding="utf-8"))
    assert guard_payload["ok"] is True
    report_text = result.final_report_path.read_text(encoding="utf-8") if result.final_report_path is not None else ""
    assert "### 技术图表" in report_text
    assert "assets/market-01-market-structure.png" in report_text
    assert "## 二、技术指标分析" in report_text
    assert "## 三、基本面分析" in report_text
    assert report_text.index("## 二、技术指标分析") < report_text.index("### 技术图表")
    assert report_text.index("### 技术图表") < report_text.index("## 三、基本面分析")
    assert "## 四、消息面与行业环境" in report_text
    assert "## 五、市场情绪与交易结构" in report_text
    forbidden_terms = (
        "viking://",
        "sha256",
        "SHA256",
        "L1 URI",
        "L2 Index",
        "material_id",
        "ApprovedMaterials",
        "provider payload",
        "receipt",
        "capability",
        "ReportSubmission",
        "RuntimeTarget",
    )
    for term in forbidden_terms:
        assert term not in report_text
    assert "viking://resources/workflow/" not in "\n".join(line for line in report_text.splitlines() if line.startswith("!["))
    copied_asset = state.run_dir / "reports" / "assets" / "market-01-market-structure.png"
    assert copied_asset.exists()
    assert copied_asset.read_bytes() == b"\x89PNG\r\n\x1a\nreport-asset-pass"
    appendix_dir = state.run_dir / "reports" / "worker-appendix"
    assert (appendix_dir / "index.md").exists()
    assert (appendix_dir / "01-market_analyst.md").exists()
    assert (appendix_dir / "12-portfolio_manager.md").exists()
    assert "技术面结论：量价结构改善" in (appendix_dir / "01-market_analyst.md").read_text(encoding="utf-8")
    assert "`market_analyst`" in (appendix_dir / "index.md").read_text(encoding="utf-8")
    assert not source_chart.exists()
    # export-result.json 由 runner/store 写，exporter 不双写。
    assert not (state.run_dir / "reports" / "export-result.json").exists()


def test_render_final_report_keeps_reader_report_clean_when_polisher_exists(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-clean-reader-report")
    manifest, reader = _build_manifest_and_reader(state)
    loaded = load_report_materials(state=state, manifest=manifest, openviking=reader)
    assert loaded.ok

    rendered = render_final_report(
        materials=loaded.materials,
        report_materials=loaded.report_materials,
    )

    assert rendered.text.startswith("# 贵州茅台（600519）投资研究报告\n")
    assert "组合经理最终裁决：维持审慎增持" in rendered.text
    assert "## 最终裁决 / 最终投资决策" not in rendered.text
    assert "## 读者版最终报告" not in rendered.text
    assert "## 图表与技术面分析" not in rendered.text
    assert "## 风险辩论（挑战方）" not in rendered.text

    claim_ids = {claim.export_claim_id for claim in rendered.claim_links}
    assert "export-claim-report_polisher-t00-claim-report_polisher" in claim_ids
    assert "export-claim-market_analyst-t00-claim-market_analyst" in claim_ids


def test_worker_appendices_preserve_raw_worker_sections_when_polisher_exists(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-worker-appendices")
    manifest, reader = _build_manifest_and_reader(state)
    loaded = load_report_materials(state=state, manifest=manifest, openviking=reader)
    assert loaded.ok

    appendices = build_worker_appendices(
        materials=loaded.materials,
        report_materials=loaded.report_materials,
    )

    assert len(appendices) == 12
    assert appendices[0].relative_path.as_posix() == "worker-appendix/01-market_analyst.md"
    assert appendices[-1].relative_path.as_posix() == "worker-appendix/12-portfolio_manager.md"
    assert "技术面结论：量价结构改善，趋势仍需成交量确认。" in appendices[0].text
    assert "组合经理最终裁决：维持审慎增持" in appendices[-1].text


def test_export_final_report_places_us_chart_assets_in_technical_market_analysis(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-us-technical-market-analysis", profile="US")
    manifest, reader = _build_manifest_and_reader(state)
    report_material = manifest.materials_for_stage(Stage.FINAL_REPORT, run_id=state.run_id)[0]
    reader._content_by_material_id[report_material.material_id] = (
        "# Apple (AAPL) Investment Research Report\n\n"
        "## 1. Investment Decision\n"
        "Portfolio manager final decision: hold with conditional execution.\n\n"
        "## 2. Technical Market Analysis\n"
        "### Trend Structure\n"
        "Price action remains constructive but needs volume confirmation.\n\n"
        "## 3. Fundamental Analysis\n"
        "Margins remain resilient while valuation needs monitoring.\n"
    ).encode("utf-8")
    source_chart = state.run_dir / "calls" / "call-01" / "evidence" / "techlab" / "charts-local" / "market-structure.png"
    source_chart.parent.mkdir(parents=True, exist_ok=True)
    source_chart.write_bytes(b"\x89PNG\r\n\x1a\nreport-asset-us")

    result = export_final_report(state=state, manifest=manifest, openviking=reader)

    assert result.status == "passed"
    assert result.final_report_path is not None
    report_text = result.final_report_path.read_text(encoding="utf-8")
    assert "## 2. Technical Market Analysis" in report_text
    assert "### 技术图表" in report_text
    assert "assets/market-01-market-structure.png" in report_text
    assert report_text.index("## 2. Technical Market Analysis") < report_text.index("### 技术图表")
    assert report_text.index("### 技术图表") < report_text.index("### Trend Structure")
    assert report_text.index("### 技术图表") < report_text.index("## 3. Fundamental Analysis")


def test_export_final_report_copies_chart_image_to_reports_assets_and_uses_relative_markdown_path(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-report-assets")
    manifest, reader = _build_manifest_and_reader(state)
    source_chart = state.run_dir / "calls" / "call-01" / "evidence" / "techlab" / "charts-local" / "market-structure.png"
    source_chart.parent.mkdir(parents=True, exist_ok=True)
    source_chart.write_bytes(b"\x89PNG\r\n\x1a\nreport-asset")

    result = export_final_report(state=state, manifest=manifest, openviking=reader)

    assert result.status == "passed"
    assert result.final_report_path is not None
    report_text = result.final_report_path.read_text(encoding="utf-8")
    image_lines = [line for line in report_text.splitlines() if line.startswith("![")]
    assert image_lines
    assert "assets/market-01-market-structure.png" in image_lines[0]
    assert "viking://" not in image_lines[0]
    assert str(source_chart) not in image_lines[0]

    copied_asset = state.run_dir / "reports" / "assets" / "market-01-market-structure.png"
    assert copied_asset.exists()
    assert copied_asset.read_bytes() == b"\x89PNG\r\n\x1a\nreport-asset"
    assert not source_chart.exists()
    assert copied_asset.exists()
    assert copied_asset.read_bytes() == b"\x89PNG\r\n\x1a\nreport-asset"


def test_export_final_report_discovers_frontline_pack_tool_chart_assets(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-pack-tool-assets")
    manifest, reader = _build_manifest_and_reader(state)
    source_chart = (
        state.run_dir
        / "calls"
        / "call-01"
        / "pack-tool-evidence"
        / "techlab"
        / "charts-local"
        / "600519.SH_indicator_panels.png"
    )
    source_chart.parent.mkdir(parents=True, exist_ok=True)
    source_chart.write_bytes(b"\x89PNG\r\n\x1a\npack-tool-chart")

    result = export_final_report(state=state, manifest=manifest, openviking=reader)

    assert result.status == "passed"
    assert result.final_report_path is not None
    report_text = result.final_report_path.read_text(encoding="utf-8")
    assert "### 技术图表" in report_text
    assert "assets/market-01-600519.SH_indicator_panels.png" in report_text

    copied_asset = state.run_dir / "reports" / "assets" / "market-01-600519.SH_indicator_panels.png"
    assert copied_asset.exists()
    assert copied_asset.read_bytes() == b"\x89PNG\r\n\x1a\npack-tool-chart"
    assert not source_chart.exists()


def test_export_final_report_fails_when_no_copyable_chart_asset(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-no-asset")
    manifest, reader = _build_manifest_and_reader(state)

    result = export_final_report(state=state, manifest=manifest, openviking=reader)

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.category == "export_report_assets"
    assert "未找到可复制的图表资产" in (result.failure.reason or "")


def test_export_final_report_cleanup_failure_is_exposed(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-cleanup-fail")
    manifest, reader = _build_manifest_and_reader(state)
    source_chart = state.run_dir / "calls" / "call-01" / "evidence" / "techlab" / "charts-local" / "market-structure.png"
    source_chart.parent.mkdir(parents=True, exist_ok=True)
    source_chart.write_bytes(b"\x89PNG\r\n\x1a\nreport-asset-cleanup")

    def _cleanup_fail(*_args):
        from claw_trade.reports import exporter as exporter_module

        return exporter_module._CopyResult(
            ok=False,
            category="export_chart_cleanup",
            reason="cleanup failed for test",
            paths=(source_chart,),
        )

    result = export_final_report(
        state=state,
        manifest=manifest,
        openviking=reader,
        chart_cleanup=_cleanup_fail,
    )

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.category == "export_chart_cleanup"
    assert "cleanup failed for test" in (result.failure.reason or "")


def test_run_export_guards_fails_when_claim_source_mapping_missing(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-claim-missing")
    manifest, reader = _build_manifest_and_reader(state)
    loaded = load_report_materials(state=state, manifest=manifest, openviking=reader)
    assert loaded.ok

    rendered = render_final_report(
        materials=loaded.materials,
        report_materials=loaded.report_materials,
    )
    mapping = build_export_claim_mapping(rendered=rendered, materials=loaded.materials)
    first = mapping.claims[0]
    broken_first = ExportClaim(
        export_claim_id=first.export_claim_id,
        text=first.text,
        kind="rating",
        source_material_ids=(),
        source_claim_ids=(),
        source_l1_sha256=(),
    )
    broken_mapping = ExportClaimMapping(
        schema_version=mapping.schema_version,
        run_id=mapping.run_id,
        final_report_path=mapping.final_report_path,
        claims=(broken_first,) + mapping.claims[1:],
    )

    guard = run_export_guards(
        mapping=broken_mapping,
        materials=loaded.materials,
        state=state,
    )

    assert not guard.ok
    assert guard.category == "export_truthfulness"


def test_render_final_report_preserves_repeated_debate_rounds(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-render-multi-round")
    bull_round_1 = _sample_material(state=state, worker_id="bull_researcher", stage=Stage.INVESTMENT_DEBATE, index=5)
    bear_round_1 = _sample_material(state=state, worker_id="bear_researcher", stage=Stage.INVESTMENT_DEBATE, index=6)
    bull_round_2 = _material_for_turn(
        state=state,
        material=bull_round_1,
        material_id="mat-bull-researcher-r2",
        call_id="call-14",
        turn_index=2,
        round_index=2,
        claim_id="claim-bull-researcher-r2",
    )
    pm_material = _sample_material(state=state, worker_id="portfolio_manager", stage=Stage.PORTFOLIO_DECISION, index=12)
    report_materials = (
        ReportMaterial(material=bull_round_1, l1_text="多头第一轮正文"),
        ReportMaterial(material=bear_round_1, l1_text="空头第一轮正文"),
        ReportMaterial(material=bull_round_2, l1_text="多头第二轮正文"),
        ReportMaterial(material=pm_material, l1_text="PM 最终裁决正文"),
    )

    rendered = render_final_report(
        materials=(bull_round_1, bear_round_1, bull_round_2, pm_material),
        report_materials=report_materials,
    )
    mapping = build_export_claim_mapping(
        rendered=rendered,
        materials=(bull_round_1, bear_round_1, bull_round_2, pm_material),
    )

    assert "多头第一轮正文" in rendered.text
    assert "多头第二轮正文" in rendered.text
    assert "## 投资辩论（多头观点）（第2轮）" in rendered.text
    claim_ids = {claim.export_claim_id for claim in mapping.claims}
    assert f"export-claim-bull_researcher-t00-{bull_round_1.l1_claims[0].claim_id}" in claim_ids
    assert "export-claim-bull_researcher-t02-claim-bull-researcher-r2" in claim_ids


def test_export_final_report_fails_when_openviking_reader_hash_mismatch(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-read-mismatch")
    manifest, reader = _build_manifest_and_reader(
        state,
        broken_worker="market_analyst",
        broken_category="hash_mismatch",
        broken_reason="hash mismatch",
    )

    result = export_final_report(state=state, manifest=manifest, openviking=reader)

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.category == "export_openviking_read"
    assert "hash_mismatch" in (result.failure.reason or "")


def _sample_state(tmp_path: Path, *, run_id: str, profile: str = "US") -> WorkflowState:
    run_dir = tmp_path / "runs" / run_id
    (run_dir / "reports").mkdir(parents=True, exist_ok=True)
    request = RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile=profile,
        currency="USD",
        currency_symbol="$",
        current_date="2026-05-04",
        start_date="2026-04-04",
        end_date="2026-05-04",
    )
    return WorkflowState(
        run_id=run_id,
        request=request,
        status=RunStatus.REPORT_EXPORTING,
        run_dir=run_dir,
        openviking_namespace=f"workflow/{run_id}",
        created_at="2026-05-04T12:00:00Z",
        updated_at="2026-05-04T12:00:00Z",
        active_stage=Stage.PORTFOLIO_DECISION,
    )


@dataclass(frozen=True)
class _ReaderResult:
    uri: str
    ok: bool
    content: bytes | None
    sha256: str | None
    size_bytes: int | None
    error_category: str | None
    error_message: str | None


class _ControlledReader:
    def __init__(
        self,
        *,
        content_by_material_id: dict[str, bytes],
        broken_worker: str | None = None,
        broken_category: str | None = None,
        broken_reason: str | None = None,
    ) -> None:
        self._content_by_material_id = content_by_material_id
        self._broken_worker = broken_worker
        self._broken_category = broken_category
        self._broken_reason = broken_reason

    def read_approved_l1(self, material: ApprovedMaterial) -> _ReaderResult:
        if self._broken_worker == material.worker_id:
            return _ReaderResult(
                uri=material.l1_uri,
                ok=False,
                content=None,
                sha256=None,
                size_bytes=None,
                error_category=self._broken_category,
                error_message=self._broken_reason,
            )
        content = self._content_by_material_id[material.material_id]
        return _ReaderResult(
            uri=material.l1_uri,
            ok=True,
            content=content,
            sha256=material.l1_sha256,
            size_bytes=material.l1_size_bytes,
            error_category=None,
            error_message=None,
        )


def _sample_material(state: WorkflowState, worker_id: str, stage: Stage, index: int) -> ApprovedMaterial:
    call_id = f"call-{index:02d}"
    call_dir = state.run_dir / "calls" / call_id
    call_dir.mkdir(parents=True, exist_ok=True)
    gate_path = call_dir / "approval-hard-gate.json"
    gate_path.write_text(
        json.dumps({"ok": True, "status": "passed", "category": "runtime_guards"}, ensure_ascii=False),
        encoding="utf-8",
    )
    claim_id = f"claim-{worker_id}"
    evidence_id = f"ev-{worker_id}"
    l1_uri = f"viking://resources/workflow/{state.run_id}/{stage.value}/{worker_id}/{call_id}/report.md"
    l2_index_uri = f"viking://resources/workflow/{state.run_id}/{stage.value}/{worker_id}/{call_id}/evidence/index.json"
    l2_entry_uri = f"viking://resources/workflow/{state.run_id}/{stage.value}/{worker_id}/{call_id}/evidence/{evidence_id}.json"

    material = ApprovedMaterial(
        material_id=f"mat-{worker_id}",
        run_id=state.run_id,
        call_id=call_id,
        worker_id=worker_id,
        stage=stage,
        target_name="report",
        l1_uri=l1_uri,
        l1_sha256=f"sha-{worker_id}",
        l1_size_bytes=256,
        l2_index_uri=l2_index_uri,
        l2_index=L2Index(
            entries=(
                L2Entry(
                    evidence_id=evidence_id,
                    uri=l2_entry_uri,
                    kind="source",
                    source="provider",
                    sha256=f"sha-l2-{worker_id}",
                    size_bytes=64,
                ),
            ),
            empty_reason=None,
            index_uri=l2_index_uri,
            index_sha256=f"sha-l2-index-{worker_id}",
            index_size_bytes=72,
        ),
        l1_claims=(
            L1Claim(
                claim_id=claim_id,
                kind="rating" if worker_id == "portfolio_manager" else "news",
                text=f"{worker_id} 的已批准声明",
                value="buy" if worker_id == "portfolio_manager" else "positive",
                required_evidence_kinds=("l2_evidence",),
                evidence_ids=(evidence_id,),
            ),
        ),
        approved_at="2026-05-04T12:00:00Z",
        hard_gate_result_path=gate_path,
    )
    return material


def _material_for_turn(
    *,
    state: WorkflowState,
    material: ApprovedMaterial,
    material_id: str,
    call_id: str,
    turn_index: int,
    round_index: int,
    claim_id: str,
) -> ApprovedMaterial:
    call_dir = state.run_dir / "calls" / call_id
    call_dir.mkdir(parents=True, exist_ok=True)
    gate_path = call_dir / "approval-hard-gate.json"
    gate_path.write_text(
        json.dumps({"ok": True, "status": "passed", "category": "runtime_guards"}, ensure_ascii=False),
        encoding="utf-8",
    )
    l1_uri = f"viking://resources/workflow/{state.run_id}/{material.stage.value}/{material.worker_id}/{call_id}/report.md"
    l2_index_uri = (
        f"viking://resources/workflow/{state.run_id}/{material.stage.value}/{material.worker_id}/{call_id}/evidence/index.json"
    )
    return replace(
        material,
        material_id=material_id,
        call_id=call_id,
        l1_uri=l1_uri,
        l1_sha256=f"sha-{material_id}",
        l1_size_bytes=256,
        l2_index_uri=l2_index_uri,
        l2_index=replace(
            material.l2_index,
            index_uri=l2_index_uri,
            index_sha256=f"sha-l2-index-{material_id}",
        ),
        l1_claims=(
            replace(
                material.l1_claims[0],
                claim_id=claim_id,
                text=f"{material.worker_id} 第二轮声明",
            ),
        ),
        hard_gate_result_path=gate_path,
        turn_index=turn_index,
        round_index=round_index,
        role_turn_index=round_index,
    )


def _l1_content_bytes(material: ApprovedMaterial) -> bytes:
    text_by_worker = {
        "market_analyst": "## 二、技术指标分析\n技术面结论：量价结构改善，趋势仍需成交量确认。",
        "fundamental_analyst": "基本面结论：盈利韧性尚可，估值处于历史中枢附近。",
        "news_analyst": "新闻结论：近期公司与行业信息偏中性，未见重大突发利空。",
        "social_analyst": "社媒结论：讨论热度抬升，情绪分化，需防短线波动。",
        "bull_researcher": "多头观点：核心竞争力与现金流能力支持中期配置价值。",
        "bear_researcher": "空头观点：估值安全边际有限，宏观扰动可能放大回撤。",
        "research_manager": "研究经理结论：维持审慎偏多，等待关键财报验证。",
        "trader": "交易计划：分批建仓，触发条件明确，执行时控制仓位节奏。",
        "risk_challenger": "风险挑战：若需求回落，盈利假设存在下修风险。",
        "risk_guardian": "风险防守：建议设置止损与仓位上限，避免单点暴露。",
        "risk_moderator": "风险整合：在可控风险前提下保留策略弹性。",
        "portfolio_manager": "组合经理最终裁决：维持审慎增持，按条件分步执行。",
        "report_polisher": (
            "# 贵州茅台（600519）投资研究报告\n\n"
            "## 一、投资结论与组合动作\n"
            "组合经理最终裁决：维持审慎增持，按条件分步执行。\n\n"
            "## 二、技术指标分析\n"
            "技术面结论：量价结构改善，趋势仍需成交量确认。\n\n"
            "## 三、基本面分析\n"
            "基本面结论：盈利韧性尚可，估值处于历史中枢附近。\n\n"
            "## 四、消息面与行业环境\n"
            "新闻结论：近期公司与行业信息偏中性，未见重大突发利空。\n\n"
            "## 五、市场情绪与交易结构\n"
            "社媒结论：讨论热度抬升，情绪分化，需防短线波动。\n\n"
            "## 六、交易计划与组合风险\n"
            "交易计划：分批建仓，触发条件明确，执行时控制仓位节奏。\n\n"
            "## 七、关键分歧与跟踪条件\n"
            "多空分歧集中在估值安全边际和需求验证。\n\n"
            "## 八、最终结论\n"
            "维持组合经理审慎增持结论，等待关键条件确认。"
        ),
    }
    return text_by_worker[material.worker_id].encode("utf-8")


def _build_manifest_and_reader(
    state: WorkflowState,
    missing_worker: str | None = None,
    broken_worker: str | None = None,
    broken_category: str | None = None,
    broken_reason: str | None = None,
) -> tuple[ApprovedManifest, _ControlledReader]:
    manifest = ApprovedManifest.empty()
    content_by_material_id: dict[str, bytes] = {}
    for index, (worker_id, stage) in enumerate(_WORKER_STAGE, start=1):
        if worker_id == missing_worker:
            continue
        material = _sample_material(state=state, worker_id=worker_id, stage=stage, index=index)
        manifest.add(material)
        content_by_material_id[material.material_id] = _l1_content_bytes(material=material)
    return (
        manifest,
        _ControlledReader(
            content_by_material_id=content_by_material_id,
            broken_worker=broken_worker,
            broken_category=broken_category,
            broken_reason=broken_reason,
        ),
    )
