from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path

from claw_trade.artifacts.manifest import ApprovedManifest
from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, L2Entry, L2Index
from claw_trade.guards.export_claims import ExportClaim, ExportClaimMapping, parse_export_claim_mapping
from claw_trade.reports.exporter import (
    build_export_claim_mapping,
    export_final_report,
    load_report_materials,
    render_final_report,
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
)


def test_export_final_report_fails_when_required_material_missing(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-missing")
    manifest, reader = _build_manifest_and_reader(state, missing_worker="trader")

    result = export_final_report(state=state, manifest=manifest, openviking=reader)

    assert result.status == "failed"
    assert result.failure is not None
    assert result.failure.category == "export_missing_material"
    assert "trader" in (result.failure.reason or "")


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
    assert "## 图表资产" in report_text
    assert "assets/market-01-market-structure.png" in report_text
    assert "viking://resources/workflow/" not in "\n".join(line for line in report_text.splitlines() if line.startswith("!["))
    copied_asset = state.run_dir / "reports" / "assets" / "market-01-market-structure.png"
    assert copied_asset.exists()
    assert copied_asset.read_bytes() == b"\x89PNG\r\n\x1a\nreport-asset-pass"
    assert not source_chart.exists()
    # export-result.json 由 runner/store 写，exporter 不双写。
    assert not (state.run_dir / "reports" / "export-result.json").exists()


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
    assert "## 图表资产" in report_text
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


def test_run_export_guards_fails_when_pm_fields_rewritten(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-pm-rewrite")
    manifest, reader = _build_manifest_and_reader(state)
    loaded = load_report_materials(state=state, manifest=manifest, openviking=reader)
    assert loaded.ok and loaded.pm_decision is not None

    rendered = render_final_report(materials=loaded.materials, pm_decision=loaded.pm_decision)
    mapping = build_export_claim_mapping(rendered=rendered, materials=loaded.materials, pm_decision=loaded.pm_decision)
    tampered = ExportClaimMapping(
        schema_version=mapping.schema_version,
        run_id=mapping.run_id,
        final_report_path=mapping.final_report_path,
        claims=mapping.claims,
        pm_decision={
            "source_material_id": loaded.pm_decision.material_id,
            "rating": "sell",
            "final_conclusion": loaded.pm_decision.final_conclusion,
            "execution_conditions": list(loaded.pm_decision.execution_conditions),
            "risk_conditions": list(loaded.pm_decision.risk_conditions),
        },
    )

    guard = run_export_guards(
        mapping=tampered,
        materials=loaded.materials,
        pm_decision=loaded.pm_decision,
        state=state,
    )

    assert not guard.ok
    assert guard.category == "pm_owner"


def test_run_export_guards_fails_when_claim_source_mapping_missing(tmp_path: Path) -> None:
    state = _sample_state(tmp_path, run_id="run-claim-missing")
    manifest, reader = _build_manifest_and_reader(state)
    loaded = load_report_materials(state=state, manifest=manifest, openviking=reader)
    assert loaded.ok and loaded.pm_decision is not None

    rendered = render_final_report(materials=loaded.materials, pm_decision=loaded.pm_decision)
    mapping = build_export_claim_mapping(rendered=rendered, materials=loaded.materials, pm_decision=loaded.pm_decision)
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
        pm_decision=mapping.pm_decision,
    )

    guard = run_export_guards(
        mapping=broken_mapping,
        materials=loaded.materials,
        pm_decision=loaded.pm_decision,
        state=state,
    )

    assert not guard.ok
    assert guard.category == "export_truthfulness"


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


def _sample_state(tmp_path: Path, *, run_id: str) -> WorkflowState:
    run_dir = tmp_path / "runs" / run_id
    (run_dir / "reports").mkdir(parents=True, exist_ok=True)
    request = RunRequest(
        ticker="AAPL",
        company_name="Apple",
        market="US",
        profile="US",
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

    return ApprovedMaterial(
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


def _l1_content_bytes(material: ApprovedMaterial) -> bytes:
    return f"{material.worker_id} approved material".encode("utf-8")


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
        if worker_id == "portfolio_manager":
            _write_pm_decision_evidence(material)
    return (
        manifest,
        _ControlledReader(
            content_by_material_id=content_by_material_id,
            broken_worker=broken_worker,
            broken_category=broken_category,
            broken_reason=broken_reason,
        ),
    )


def _write_pm_decision_evidence(material: ApprovedMaterial) -> None:
    payload = {
        "schema_version": "control.pm_decision.v1",
        "run_id": material.run_id,
        "call_id": material.call_id,
        "worker_id": "portfolio_manager",
        "stage": "portfolio_decision",
        "material_id": material.material_id,
        "rating": "buy",
        "final_conclusion": "维持买入。",
        "execution_conditions": ["回调分批加仓"],
        "risk_conditions": ["若业绩不及预期则止损"],
        "source_claim_ids": [material.l1_claims[0].claim_id],
        "source_l1_sha256": material.l1_sha256,
        "l1_uri": material.l1_uri,
    }
    pm_path = material.hard_gate_result_path.parent / "pm-decision.json"
    pm_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
