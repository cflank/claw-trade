from pathlib import Path

import pytest

from claw_trade.artifacts.manifest import ApprovedManifest, ArtifactFlowError, ManifestStore
from claw_trade.artifacts.refs import ApprovedMaterial, L1Claim, L2Entry, L2Index
from claw_trade.workflow.models import Stage


def test_add_rejects_duplicate_material_and_call_and_worker(tmp_path: Path) -> None:
    material = fake_approved_material(
        material_id="mat-frontline-market-1",
        worker_id="market_analyst",
        stage=Stage.FRONTLINE,
        call_id="call-1",
        hard_gate_result_path=write_gate_result(tmp_path, "market-1.json"),
    )
    manifest = ApprovedManifest.empty().add(material)

    with pytest.raises(ArtifactFlowError, match="重复 material_id"):
        manifest.add(material)

    with pytest.raises(ArtifactFlowError, match="重复 worker"):
        manifest.add(
            fake_approved_material(
                material_id="mat-frontline-market-2",
                worker_id="market_analyst",
                stage=Stage.FRONTLINE,
                call_id="call-2",
                hard_gate_result_path=write_gate_result(tmp_path, "market-2.json"),
            )
        )

    manifest.add(
        fake_approved_material(
            material_id="mat-frontline-market-turn-1",
            worker_id="market_analyst",
            stage=Stage.FRONTLINE,
            call_id="call-2b",
            hard_gate_result_path=write_gate_result(tmp_path, "market-turn-1.json"),
            turn_index=1,
            round_index=2,
            role_turn_index=2,
        )
    )

    with pytest.raises(ArtifactFlowError, match="重复 call"):
        manifest.add(
            fake_approved_material(
                material_id="mat-frontline-fundamental-1",
                worker_id="fundamental_analyst",
                stage=Stage.FRONTLINE,
                call_id="call-1",
                hard_gate_result_path=write_gate_result(tmp_path, "fundamental-1.json"),
            )
        )


def test_add_updates_current_manifest_when_return_value_is_ignored(tmp_path: Path) -> None:
    manifest = ApprovedManifest.empty()
    material = fake_approved_material(
        material_id="mat-investment-manager",
        worker_id="research_manager",
        stage=Stage.INVESTMENT_DECISION,
        call_id="call-7",
        hard_gate_result_path=write_gate_result(tmp_path, "manager.json"),
    )

    manifest.add(material)

    refs = manifest.for_downstream_stage(Stage.TRADE_DECISION)
    assert len(refs) == 1
    assert refs[0].material_id == "mat-investment-manager"
    assert refs[0].worker_id == "research_manager"


def test_capabilities_for_downstream_stage_use_manifest_scoped_hash(tmp_path: Path) -> None:
    manifest = (
        ApprovedManifest.empty()
        .add(
            fake_approved_material(
                "mat-frontline-market",
                "market_analyst",
                Stage.FRONTLINE,
                "call-1",
                hard_gate_result_path=write_gate_result(tmp_path, "market.json"),
            )
        )
        .add(
            fake_approved_material(
                "mat-frontline-fundamental",
                "fundamental_analyst",
                Stage.FRONTLINE,
                "call-2",
                hard_gate_result_path=write_gate_result(tmp_path, "fundamental.json"),
            )
        )
        .add(
            fake_approved_material(
                "mat-frontline-news",
                "news_analyst",
                Stage.FRONTLINE,
                "call-3",
                hard_gate_result_path=write_gate_result(tmp_path, "news.json"),
            )
        )
        .add(
            fake_approved_material(
                "mat-frontline-social",
                "social_analyst",
                Stage.FRONTLINE,
                "call-4",
                hard_gate_result_path=write_gate_result(tmp_path, "social.json"),
            )
        )
    )

    capabilities = manifest.capabilities_for_downstream_stage(Stage.INVESTMENT_DEBATE)

    assert len(capabilities) == 4
    for capability in capabilities:
        assert capability.capability_id
        assert capability.material_id
        assert capability.allowed_l1_uri.endswith("/report.md")
        assert capability.allowed_l1_sha256.startswith("sha-")
        assert capability.allowed_l2_prefix.endswith("/evidence/")
        assert capability.manifest_entry_sha256


def test_bear_worker_call_sources_include_frontline_and_bull_argument(tmp_path: Path) -> None:
    manifest = (
        ApprovedManifest.empty()
        .add(
            fake_approved_material(
                "mat-frontline-market",
                "market_analyst",
                Stage.FRONTLINE,
                "call-1",
                hard_gate_result_path=write_gate_result(tmp_path, "market-bear.json"),
            )
        )
        .add(
            fake_approved_material(
                "mat-frontline-fundamental",
                "fundamental_analyst",
                Stage.FRONTLINE,
                "call-2",
                hard_gate_result_path=write_gate_result(tmp_path, "fundamental-bear.json"),
            )
        )
        .add(
            fake_approved_material(
                "mat-frontline-news",
                "news_analyst",
                Stage.FRONTLINE,
                "call-3",
                hard_gate_result_path=write_gate_result(tmp_path, "news-bear.json"),
            )
        )
        .add(
            fake_approved_material(
                "mat-frontline-social",
                "social_analyst",
                Stage.FRONTLINE,
                "call-4",
                hard_gate_result_path=write_gate_result(tmp_path, "social-bear.json"),
            )
        )
        .add(
            fake_approved_material(
                "mat-debate-bull",
                "bull_researcher",
                Stage.INVESTMENT_DEBATE,
                "call-5",
                hard_gate_result_path=write_gate_result(tmp_path, "bull-bear.json"),
            )
        )
    )

    bull_refs = manifest.for_worker_call(Stage.INVESTMENT_DEBATE, worker_id="bull_researcher")
    bear_refs = manifest.for_worker_call(Stage.INVESTMENT_DEBATE, worker_id="bear_researcher", turn_index=1)

    assert [ref.worker_id for ref in bull_refs] == [
        "market_analyst",
        "fundamental_analyst",
        "news_analyst",
        "social_analyst",
    ]
    assert [ref.worker_id for ref in bear_refs] == [
        "market_analyst",
        "fundamental_analyst",
        "news_analyst",
        "social_analyst",
        "bull_researcher",
    ]


def test_debate_worker_call_sources_preserve_prior_rounds_in_turn_order(tmp_path: Path) -> None:
    manifest = ApprovedManifest.empty()
    for material_id, worker_id, stage, call_id, turn_index, round_index in (
        ("mat-frontline-market", "market_analyst", Stage.FRONTLINE, "call-1", 0, 1),
        ("mat-frontline-fundamental", "fundamental_analyst", Stage.FRONTLINE, "call-2", 0, 1),
        ("mat-frontline-news", "news_analyst", Stage.FRONTLINE, "call-3", 0, 1),
        ("mat-frontline-social", "social_analyst", Stage.FRONTLINE, "call-4", 0, 1),
        ("mat-debate-bull-r1", "bull_researcher", Stage.INVESTMENT_DEBATE, "call-5", 0, 1),
        ("mat-debate-bear-r1", "bear_researcher", Stage.INVESTMENT_DEBATE, "call-6", 1, 1),
        ("mat-debate-bull-r2", "bull_researcher", Stage.INVESTMENT_DEBATE, "call-7", 2, 2),
    ):
        manifest.add(
            fake_approved_material(
                material_id,
                worker_id,
                stage,
                call_id,
                hard_gate_result_path=write_gate_result(tmp_path, f"{material_id}.json"),
                turn_index=turn_index,
                round_index=round_index,
                role_turn_index=round_index,
            )
        )

    refs = manifest.for_worker_call(Stage.INVESTMENT_DEBATE, worker_id="bear_researcher", turn_index=3)

    assert [(ref.worker_id, ref.turn_index) for ref in refs] == [
        ("market_analyst", 0),
        ("fundamental_analyst", 0),
        ("news_analyst", 0),
        ("social_analyst", 0),
        ("bull_researcher", 0),
        ("bear_researcher", 1),
        ("bull_researcher", 2),
    ]


def test_portfolio_manager_worker_call_sources_include_research_trader_and_risk(tmp_path: Path) -> None:
    sources = (
        ("mat-manager", "research_manager", Stage.INVESTMENT_DECISION, "call-7"),
        ("mat-trader", "trader", Stage.TRADE_DECISION, "call-8"),
        ("mat-risk-challenger", "risk_challenger", Stage.RISK_DEBATE, "call-9"),
        ("mat-risk-guardian", "risk_guardian", Stage.RISK_DEBATE, "call-10"),
        ("mat-risk-moderator", "risk_moderator", Stage.RISK_DEBATE, "call-11"),
    )
    manifest = ApprovedManifest.empty()
    for material_id, worker_id, stage, call_id in sources:
        manifest.add(
            fake_approved_material(
                material_id,
                worker_id,
                stage,
                call_id,
                hard_gate_result_path=write_gate_result(tmp_path, f"{worker_id}-portfolio.json"),
            )
        )

    refs = manifest.for_worker_call(Stage.PORTFOLIO_DECISION, worker_id="portfolio_manager")

    assert [(ref.worker_id, ref.stage) for ref in refs] == [
        ("research_manager", Stage.INVESTMENT_DECISION),
        ("trader", Stage.TRADE_DECISION),
        ("risk_challenger", Stage.RISK_DEBATE),
        ("risk_guardian", Stage.RISK_DEBATE),
        ("risk_moderator", Stage.RISK_DEBATE),
    ]


def test_for_downstream_stage_rejects_partial_records(tmp_path: Path) -> None:
    manifest = ApprovedManifest.empty().add(
        fake_approved_material(
            "mat-risk-challenger",
            "risk_challenger",
            Stage.RISK_DEBATE,
            "call-9",
            hard_gate_result_path=write_gate_result(tmp_path, "risk-challenger.json"),
        )
    )

    with pytest.raises(ArtifactFlowError, match="下游材料缺失"):
        manifest.for_downstream_stage(Stage.PORTFOLIO_DECISION)


def test_report_polisher_worker_call_sources_include_core_12_reports(tmp_path: Path) -> None:
    sources = (
        ("mat-frontline-market", "market_analyst", Stage.FRONTLINE, "call-1"),
        ("mat-frontline-fundamental", "fundamental_analyst", Stage.FRONTLINE, "call-2"),
        ("mat-frontline-news", "news_analyst", Stage.FRONTLINE, "call-3"),
        ("mat-frontline-social", "social_analyst", Stage.FRONTLINE, "call-4"),
        ("mat-debate-bull", "bull_researcher", Stage.INVESTMENT_DEBATE, "call-5"),
        ("mat-debate-bear", "bear_researcher", Stage.INVESTMENT_DEBATE, "call-6"),
        ("mat-manager", "research_manager", Stage.INVESTMENT_DECISION, "call-7"),
        ("mat-trader", "trader", Stage.TRADE_DECISION, "call-8"),
        ("mat-risk-challenger", "risk_challenger", Stage.RISK_DEBATE, "call-9"),
        ("mat-risk-guardian", "risk_guardian", Stage.RISK_DEBATE, "call-10"),
        ("mat-risk-moderator", "risk_moderator", Stage.RISK_DEBATE, "call-11"),
        ("mat-portfolio", "portfolio_manager", Stage.PORTFOLIO_DECISION, "call-12"),
    )
    manifest = ApprovedManifest.empty()
    for material_id, worker_id, stage, call_id in sources:
        manifest.add(
            fake_approved_material(
                material_id,
                worker_id,
                stage,
                call_id,
                hard_gate_result_path=write_gate_result(tmp_path, f"{worker_id}.json"),
            )
        )

    refs = manifest.for_worker_call(Stage.FINAL_REPORT, worker_id="report_polisher")

    assert [(ref.worker_id, ref.stage) for ref in refs] == [
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
    ]


def test_add_rejects_material_without_hard_gate_path() -> None:
    manifest = ApprovedManifest.empty()
    material = fake_approved_material("mat-frontline-market", "market_analyst", Stage.FRONTLINE, "call-1")
    bad = ApprovedMaterial(
        material_id=material.material_id,
        run_id=material.run_id,
        call_id=material.call_id,
        worker_id=material.worker_id,
        stage=material.stage,
        target_name=material.target_name,
        l1_uri=material.l1_uri,
        l1_sha256=material.l1_sha256,
        l1_size_bytes=material.l1_size_bytes,
        l2_index_uri=material.l2_index_uri,
        l2_index=material.l2_index,
        l1_claims=material.l1_claims,
        approved_at=material.approved_at,
        hard_gate_result_path=Path(" "),
    )
    with pytest.raises(ArtifactFlowError, match="hard_gate_result_path"):
        manifest.add(bad)


def test_add_rejects_missing_hard_gate_result_file(tmp_path: Path) -> None:
    manifest = ApprovedManifest.empty()
    material = fake_approved_material(
        "mat-frontline-market",
        "market_analyst",
        Stage.FRONTLINE,
        "call-1",
        hard_gate_result_path=tmp_path / "guards" / "missing.json",
    )

    with pytest.raises(ArtifactFlowError, match="hard gate 结果文件不存在"):
        manifest.add(material)


def test_manifest_store_add_and_load_roundtrip(tmp_path: Path) -> None:
    store = ManifestStore(root_dir=tmp_path)
    gate_path = tmp_path / "guards" / "market-pass.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text('{"ok": true, "category": "runtime_guards"}', encoding="utf-8")
    material = fake_approved_material(
        "mat-frontline-market",
        "market_analyst",
        Stage.FRONTLINE,
        "call-1",
        hard_gate_result_path=gate_path,
    )
    store.add("run-1", material)

    loaded = store.load("run-1")
    items = loaded.all_for_run("run-1")
    assert len(items) == 1
    assert items[0].material_id == material.material_id
    manifest_path = tmp_path / "run-1" / "openviking" / "approved-manifest.json"
    assert manifest_path.exists()


def test_manifest_store_add_rejects_missing_hard_gate_result_file(tmp_path: Path) -> None:
    store = ManifestStore(root_dir=tmp_path)
    material = fake_approved_material(
        "mat-frontline-market",
        "market_analyst",
        Stage.FRONTLINE,
        "call-1",
        hard_gate_result_path=tmp_path / "guards" / "missing.json",
    )

    with pytest.raises(ArtifactFlowError, match="hard gate 结果文件不存在"):
        store.add("run-1", material)


def test_manifest_store_add_rejects_invalid_guard_result_json(tmp_path: Path) -> None:
    store = ManifestStore(root_dir=tmp_path)
    gate_path = tmp_path / "guards" / "broken.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text("{not-json", encoding="utf-8")
    material = fake_approved_material(
        "mat-frontline-market",
        "market_analyst",
        Stage.FRONTLINE,
        "call-1",
        hard_gate_result_path=gate_path,
    )

    with pytest.raises(ArtifactFlowError, match="不是合法 JSON"):
        store.add("run-1", material)


def test_manifest_store_add_rejects_non_pass_guard_result(tmp_path: Path) -> None:
    store = ManifestStore(root_dir=tmp_path)
    gate_path = tmp_path / "guards" / "failed.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text('{"ok": false, "status": "fail"}', encoding="utf-8")
    material = fake_approved_material(
        "mat-frontline-market",
        "market_analyst",
        Stage.FRONTLINE,
        "call-1",
        hard_gate_result_path=gate_path,
    )

    with pytest.raises(ArtifactFlowError, match="hard gate 未通过"):
        store.add("run-1", material)


def test_manifest_store_add_accepts_status_pass_contract(tmp_path: Path) -> None:
    store = ManifestStore(root_dir=tmp_path)
    gate_path = tmp_path / "guards" / "pass-status.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text('{"status": "pass", "category": "hard_gate"}', encoding="utf-8")
    material = fake_approved_material(
        "mat-frontline-market",
        "market_analyst",
        Stage.FRONTLINE,
        "call-1",
        hard_gate_result_path=gate_path,
    )

    store.add("run-1", material)
    assert store.load("run-1").all_for_run("run-1")


def test_manifest_store_add_rejects_illegal_guard_category(tmp_path: Path) -> None:
    store = ManifestStore(root_dir=tmp_path)
    gate_path = tmp_path / "guards" / "illegal-category.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text('{"ok": true, "status": "pass", "category": "illegal_gate"}', encoding="utf-8")
    material = fake_approved_material(
        "mat-frontline-market",
        "market_analyst",
        Stage.FRONTLINE,
        "call-1",
        hard_gate_result_path=gate_path,
    )

    with pytest.raises(ArtifactFlowError, match="hard gate 未通过"):
        store.add("run-1", material)


def test_manifest_store_add_rejects_missing_guard_category(tmp_path: Path) -> None:
    store = ManifestStore(root_dir=tmp_path)
    gate_path = tmp_path / "guards" / "missing-category.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text('{"ok": true}', encoding="utf-8")
    material = fake_approved_material(
        "mat-frontline-market",
        "market_analyst",
        Stage.FRONTLINE,
        "call-1",
        hard_gate_result_path=gate_path,
    )

    with pytest.raises(ArtifactFlowError, match="hard gate 未通过"):
        store.add("run-1", material)


def test_approved_manifest_add_rejects_illegal_guard_category(tmp_path: Path) -> None:
    manifest = ApprovedManifest.empty()
    gate_path = tmp_path / "guards" / "approved-manifest-illegal-category.json"
    gate_path.parent.mkdir(parents=True, exist_ok=True)
    gate_path.write_text('{"ok": true, "category": "illegal_gate"}', encoding="utf-8")
    material = fake_approved_material(
        "mat-frontline-market",
        "market_analyst",
        Stage.FRONTLINE,
        "call-1",
        hard_gate_result_path=gate_path,
    )

    with pytest.raises(ArtifactFlowError, match="hard gate 未通过"):
        manifest.add(material)


def fake_approved_material(
    material_id: str = "mat-default",
    worker_id: str = "market_analyst",
    stage: Stage = Stage.FRONTLINE,
    call_id: str = "call-1",
    hard_gate_result_path: Path | None = None,
    turn_index: int = 0,
    round_index: int = 1,
    role_turn_index: int = 1,
) -> ApprovedMaterial:
    l1_uri = f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/report.md"
    l2_index_uri = f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/evidence/index.json"
    evidence_uri = f"viking://resources/workflow/run-1/{stage.value}/{worker_id}/{call_id}/evidence/e1.json"

    return ApprovedMaterial(
        material_id=material_id,
        run_id="run-1",
        call_id=call_id,
        worker_id=worker_id,
        stage=stage,
        target_name="report",
        l1_uri=l1_uri,
        l1_sha256=f"sha-{material_id}",
        l1_size_bytes=512,
        l2_index_uri=l2_index_uri,
        l2_index=L2Index(
            entries=(
                L2Entry(
                    evidence_id="e1",
                    uri=evidence_uri,
                    kind="source",
                    source="api",
                    sha256=f"sha-{material_id}-e1",
                    size_bytes=64,
                ),
            ),
            empty_reason=None,
            index_uri=l2_index_uri,
            index_sha256=f"sha-{material_id}-index",
            index_size_bytes=32,
        ),
        l1_claims=(
            L1Claim(
                claim_id=f"claim-{material_id}",
                kind="source_claim",
                text="引用了来源",
                value=None,
                required_evidence_kinds=("source",),
                evidence_ids=("e1",),
            ),
        ),
        approved_at="2026-05-03T16:10:00Z",
        hard_gate_result_path=hard_gate_result_path or Path("runs/run-1/evidence/guards/result.json"),
        turn_index=turn_index,
        round_index=round_index,
        role_turn_index=role_turn_index,
    )


def write_gate_result(tmp_path: Path, name: str, payload: str = '{"ok": true, "category": "runtime_guards"}') -> Path:
    path = tmp_path / "guards" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    return path
