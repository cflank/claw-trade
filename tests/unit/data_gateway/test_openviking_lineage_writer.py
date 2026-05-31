from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from claw_trade.artifacts.refs import ApprovedMaterial, L2Entry, L2Index
from claw_trade.data_gateway.models import (
    FreshnessPolicy,
    Market,
    PackAuditPayload,
    PackDomain,
    PackRequest,
    PrioritySource,
    ProviderAttempt,
    ProviderKind,
    ProviderStatus,
    Readiness,
    ReadinessStatus,
    SourceRole,
)
from claw_trade.data_gateway.openviking.lineage import OpenBBMongoLineageWriter
from claw_trade.data_gateway.packs.materializer import materialize_domain_pack_result
from claw_trade.workflow.models import ExportResult, RunRequest, RunStatus, Stage, WorkflowState


@dataclass
class _Backend:
    linked: list[dict[str, object]]

    def ensure_namespace(self, namespace: str) -> None:
        _ = namespace

    def fetch_content_by_uri(self, uri: str) -> bytes:
        _ = uri
        return b"ok"

    def fetch_l2_index_by_uri(self, uri: str | None) -> L2Index:
        return L2Index(entries=(), empty_reason="no_evidence", index_uri=uri, index_sha256="c" * 64, index_size_bytes=2)

    def fetch_stat_by_uri(self, uri: str):  # pragma: no cover - not used
        _ = uri
        raise AssertionError("not used")

    def fetch_receipt_by_path(self, receipt_path: Path):  # pragma: no cover - not used
        _ = receipt_path
        raise AssertionError("not used")

    def link_relation(self, relation: dict[str, object]) -> dict[str, object]:
        self.linked.append(relation)
        return {"status": "ok"}


class _Collection:
    def __init__(self, rows: tuple[dict[str, Any], ...]) -> None:
        self._rows = rows

    def find(self, query: dict[str, object]):
        return [row for row in self._rows if all(row.get(key) == value for key, value in query.items())]


class _Database:
    def __init__(
        self,
        rows: tuple[dict[str, Any], ...],
        *,
        crypto_lens_rows: tuple[dict[str, Any], ...] = (),
    ) -> None:
        self._collections = {
            "openbb_provider_attempts": _Collection(rows),
            "crypto_lens_analysis_evidence": _Collection(crypto_lens_rows),
        }

    def __getitem__(self, name: str) -> _Collection:
        return self._collections.get(name, _Collection(()))


class _Manifest:
    def __init__(self, materials: tuple[ApprovedMaterial, ...]) -> None:
        self._materials = materials

    def all_for_run(self, run_id: str) -> tuple[ApprovedMaterial, ...]:
        return tuple(material for material in self._materials if material.run_id == run_id)


def test_lineage_writer_links_final_report_to_provider_refs(tmp_path: Path) -> None:
    from claw_trade.artifacts.openviking_client import OpenVikingClient

    run_id = "run-lineage"
    state = _state(tmp_path, run_id=run_id)
    _write_export_claims(state)
    market = _material(run_id, "market_analyst", Stage.FRONTLINE, "call-market")
    pm = _material(run_id, "portfolio_manager", Stage.PORTFOLIO_DECISION, "call-pm")
    final = _material(run_id, "report_polisher", Stage.FINAL_REPORT, "call-final")
    backend = _Backend(linked=[])
    writer = OpenBBMongoLineageWriter(
        openviking=OpenVikingClient(backend=backend),
        database=_Database((_attempt_doc(run_id=run_id, call_id="call-market"),)),
        now_text=lambda: "2026-05-18T00:00:00Z",
    )
    export_result = ExportResult.passed(
        state=state,
        final_report_path=state.run_dir / "reports" / "final-report.md",
        guard_path=state.run_dir / "reports" / "export-guard-results.json",
    )

    result = writer.link_after_export(
        state=state,
        manifest=_Manifest((market, pm, final)),  # type: ignore[arg-type]
        export_result=export_result,
    )

    assert result.ok is True
    assert result.relation_count > 0
    assert backend.linked
    linked_text = "\n".join(str(item) for item in backend.linked)
    assert "mongo://openbb_provider_attempts/attempt-1" in linked_text
    assert "mongo://openbb_raw_payloads/raw-1" in linked_text
    assert (state.run_dir / "openviking" / "lineage-relations.json").exists()


def test_lineage_writer_links_cn_a_seven_frontline_provider_refs(tmp_path: Path) -> None:
    from claw_trade.artifacts.openviking_client import OpenVikingClient

    run_id = "run-lineage"
    state = _state(tmp_path, run_id=run_id, market="CN_A", ticker="600519", currency="CNY")
    _write_export_claims(state)
    frontline = (
        _material(run_id, "market_analyst", Stage.FRONTLINE, "call-market"),
        _material(run_id, "fundamental_analyst", Stage.FRONTLINE, "call-fundamental"),
        _material(run_id, "news_analyst", Stage.FRONTLINE, "call-news"),
        _material(run_id, "social_analyst", Stage.FRONTLINE, "call-social"),
        _material(run_id, "policy_analyst", Stage.FRONTLINE, "call-policy"),
        _material(run_id, "hot_money_tracker", Stage.FRONTLINE, "call-hot-money"),
        _material(run_id, "lockup_watcher", Stage.FRONTLINE, "call-lockup"),
    )
    pm = _material(run_id, "portfolio_manager", Stage.PORTFOLIO_DECISION, "call-pm")
    final = _material(run_id, "report_polisher", Stage.FINAL_REPORT, "call-final")
    backend = _Backend(linked=[])
    writer = OpenBBMongoLineageWriter(
        openviking=OpenVikingClient(backend=backend),
        database=_Database(
            (
                _attempt_doc(run_id=run_id, call_id="call-market", worker_id="market_analyst", pack="market"),
                _attempt_doc(
                    run_id=run_id,
                    call_id="call-fundamental",
                    worker_id="fundamental_analyst",
                    pack="fundamental",
                ),
                _attempt_doc(run_id=run_id, call_id="call-news", worker_id="news_analyst", pack="news"),
                _attempt_doc(run_id=run_id, call_id="call-social", worker_id="social_analyst", pack="social"),
                _attempt_doc(run_id=run_id, call_id="call-policy", worker_id="policy_analyst", pack="policy"),
                _attempt_doc(
                    run_id=run_id,
                    call_id="call-hot-money",
                    worker_id="hot_money_tracker",
                    pack="hot_money",
                ),
                _attempt_doc(run_id=run_id, call_id="call-lockup", worker_id="lockup_watcher", pack="lockup"),
            )
        ),
        now_text=lambda: "2026-05-18T00:00:00Z",
    )
    export_result = ExportResult.passed(
        state=state,
        final_report_path=state.run_dir / "reports" / "final-report.md",
        guard_path=state.run_dir / "reports" / "export-guard-results.json",
    )

    result = writer.link_after_export(
        state=state,
        manifest=_Manifest((*frontline, pm, final)),  # type: ignore[arg-type]
        export_result=export_result,
    )

    assert result.ok is True
    assert result.relation_count > 0
    linked_text = "\n".join(str(item) for item in backend.linked)
    assert "policy_analyst" in linked_text
    assert "hot_money_tracker" in linked_text
    assert "lockup_watcher" in linked_text
    assert "audit://run-lineage/call-policy/policy" in linked_text
    assert "audit://run-lineage/call-hot-money/hot_money" in linked_text
    assert "audit://run-lineage/call-lockup/lockup" in linked_text


def test_lineage_writer_accepts_tool_call_scoped_provider_attempts(tmp_path: Path) -> None:
    from claw_trade.artifacts.openviking_client import OpenVikingClient

    run_id = "run-lineage"
    state = _state(tmp_path, run_id=run_id)
    _write_export_claims(state)
    market = _material(run_id, "market_analyst", Stage.FRONTLINE, "call-market")
    pm = _material(run_id, "portfolio_manager", Stage.PORTFOLIO_DECISION, "call-pm")
    final = _material(run_id, "report_polisher", Stage.FINAL_REPORT, "call-final")
    backend = _Backend(linked=[])
    writer = OpenBBMongoLineageWriter(
        openviking=OpenVikingClient(backend=backend),
        database=_Database((_attempt_doc(run_id=run_id, call_id="call-market__tool-call-00"),)),
        now_text=lambda: "2026-05-18T00:00:00Z",
    )
    export_result = ExportResult.passed(
        state=state,
        final_report_path=state.run_dir / "reports" / "final-report.md",
        guard_path=state.run_dir / "reports" / "export-guard-results.json",
    )

    result = writer.link_after_export(
        state=state,
        manifest=_Manifest((market, pm, final)),  # type: ignore[arg-type]
        export_result=export_result,
    )

    assert result.ok is True
    linked_text = "\n".join(str(item) for item in backend.linked)
    assert "mongo://openbb_provider_attempts/attempt-1" in linked_text


def test_lineage_writer_rebuilds_crypto_lens_analysis_refs_from_mongo(tmp_path: Path) -> None:
    from claw_trade.artifacts.openviking_client import OpenVikingClient

    run_id = "run-lineage"
    state = _state(tmp_path, run_id=run_id, market="CRYPTO", ticker="BTC", currency="USDT")
    _write_export_claims(state)
    market = _material(run_id, "market_analyst", Stage.FRONTLINE, "call-market")
    pm = _material(run_id, "portfolio_manager", Stage.PORTFOLIO_DECISION, "call-pm")
    final = _material(run_id, "report_polisher", Stage.FINAL_REPORT, "call-final")
    backend = _Backend(linked=[])
    writer = OpenBBMongoLineageWriter(
        openviking=OpenVikingClient(backend=backend),
        database=_Database(
            (_attempt_doc(run_id=run_id, call_id="call-market"),),
            crypto_lens_rows=(
                {
                    "_id": "crypto_lens:run-lineage:call-market:evidence",
                    "run_id": run_id,
                    "call_id": "call-market",
                    "referenced_normalized_refs": ("mongo://openbb_normalized/norm-1",),
                },
            ),
        ),
        now_text=lambda: "2026-05-18T00:00:00Z",
    )
    export_result = ExportResult.passed(
        state=state,
        final_report_path=state.run_dir / "reports" / "final-report.md",
        guard_path=state.run_dir / "reports" / "export-guard-results.json",
    )

    result = writer.link_after_export(
        state=state,
        manifest=_Manifest((market, pm, final)),  # type: ignore[arg-type]
        export_result=export_result,
    )

    assert result.ok is True
    linked_text = "\n".join(str(item) for item in backend.linked)
    assert "mongo://crypto_lens_analysis_evidence/crypto_lens:run-lineage:call-market:evidence" in linked_text
    assert "pack_audit_to_provider_attempt" in linked_text
    assert "crypto_lens_analysis_evidence/crypto_lens" in linked_text


def test_lineage_writer_fails_when_frontline_provider_attempts_are_missing(tmp_path: Path) -> None:
    from claw_trade.artifacts.openviking_client import OpenVikingClient

    run_id = "run-lineage"
    state = _state(tmp_path, run_id=run_id)
    _write_export_claims(state)
    market = _material(run_id, "market_analyst", Stage.FRONTLINE, "call-market")
    pm = _material(run_id, "portfolio_manager", Stage.PORTFOLIO_DECISION, "call-pm")
    final = _material(run_id, "report_polisher", Stage.FINAL_REPORT, "call-final")
    writer = OpenBBMongoLineageWriter(
        openviking=OpenVikingClient(backend=_Backend(linked=[])),
        database=_Database(()),
        now_text=lambda: "2026-05-18T00:00:00Z",
    )
    export_result = ExportResult.passed(
        state=state,
        final_report_path=state.run_dir / "reports" / "final-report.md",
        guard_path=state.run_dir / "reports" / "export-guard-results.json",
    )

    result = writer.link_after_export(
        state=state,
        manifest=_Manifest((market, pm, final)),  # type: ignore[arg-type]
        export_result=export_result,
    )

    assert result.ok is False
    assert result.category == "openviking_lineage"
    assert "缺少 OpenBB provider attempts" in (result.reason or "")


def test_materializer_does_not_synthesize_http_evidence_refs() -> None:
    pack_result = _minimal_pack_result_for_materializer()
    materialized = materialize_domain_pack_result(pack_result)

    assert materialized.http_evidence_refs == ()


def test_lineage_writer_records_blocked_remote_failures_without_blocking_report(tmp_path: Path) -> None:
    from claw_trade.artifacts.openviking_client import OpenVikingClient

    run_id = "run-lineage"
    state = _state(tmp_path, run_id=run_id)
    _write_export_claims(state)
    market = _material(run_id, "market_analyst", Stage.FRONTLINE, "call-market")
    pm = _material(run_id, "portfolio_manager", Stage.PORTFOLIO_DECISION, "call-pm")
    final = _material(run_id, "report_polisher", Stage.FINAL_REPORT, "call-final")
    backend = _Backend(linked=[])
    writer = OpenBBMongoLineageWriter(
        openviking=OpenVikingClient(backend=backend),
        database=_Database(
            (
                _attempt_doc(
                    run_id=run_id,
                    call_id="call-market",
                    status="remote_error",
                    raw_ref=None,
                    normalized_ref=None,
                ),
            )
        ),
        now_text=lambda: "2026-05-18T00:00:00Z",
    )
    export_result = ExportResult.passed(
        state=state,
        final_report_path=state.run_dir / "reports" / "final-report.md",
        guard_path=state.run_dir / "reports" / "export-guard-results.json",
    )

    result = writer.link_after_export(
        state=state,
        manifest=_Manifest((market, pm, final)),  # type: ignore[arg-type]
        export_result=export_result,
    )

    assert result.ok is True
    linked_text = "\n".join(str(item) for item in backend.linked)
    assert "mongo://openbb_provider_attempts/attempt-1" in linked_text
    audit_path = state.run_dir / "openviking" / "lineage-relations.json"
    audit_payload = json.loads(audit_path.read_text(encoding="utf-8"))
    assert audit_payload["pack_statuses"][0]["approval_status"] == "blocked"
    assert audit_payload["pack_statuses"][0]["attempt_statuses"][0]["status"] == "remote_error"


def test_lineage_writer_blocks_when_provider_evidence_write_failed(tmp_path: Path) -> None:
    from claw_trade.artifacts.openviking_client import OpenVikingClient

    run_id = "run-lineage"
    state = _state(tmp_path, run_id=run_id)
    _write_export_claims(state)
    market = _material(run_id, "market_analyst", Stage.FRONTLINE, "call-market")
    pm = _material(run_id, "portfolio_manager", Stage.PORTFOLIO_DECISION, "call-pm")
    final = _material(run_id, "report_polisher", Stage.FINAL_REPORT, "call-final")
    writer = OpenBBMongoLineageWriter(
        openviking=OpenVikingClient(backend=_Backend(linked=[])),
        database=_Database(
            (
                _attempt_doc(
                    run_id=run_id,
                    call_id="call-market",
                    status="evidence_write_failed",
                    raw_ref=None,
                    normalized_ref=None,
                ),
            )
        ),
        now_text=lambda: "2026-05-18T00:00:00Z",
    )
    export_result = ExportResult.passed(
        state=state,
        final_report_path=state.run_dir / "reports" / "final-report.md",
        guard_path=state.run_dir / "reports" / "export-guard-results.json",
    )

    result = writer.link_after_export(
        state=state,
        manifest=_Manifest((market, pm, final)),  # type: ignore[arg-type]
        export_result=export_result,
    )

    assert result.ok is False
    assert result.category == "openviking_lineage"
    assert "provider evidence 写入失败" in (result.reason or "")


def _state(
    tmp_path: Path,
    *,
    run_id: str,
    market: str = "US",
    ticker: str = "AAPL",
    currency: str = "USD",
) -> WorkflowState:
    run_dir = tmp_path / "runs" / run_id
    (run_dir / "reports").mkdir(parents=True)
    (run_dir / "openviking").mkdir(parents=True)
    return WorkflowState(
        run_id=run_id,
        request=RunRequest(
            ticker=ticker,
            company_name="Apple" if market == "US" else "Bitcoin",
            market=market,
            profile=market,
            currency=currency,
            currency_symbol="$",
            current_date="2026-05-18",
            start_date="2026-04-18",
            end_date="2026-05-18",
        ),
        status=RunStatus.REPORT_EXPORTING,
        run_dir=run_dir,
        openviking_namespace=f"workflow/{run_id}",
        created_at="2026-05-18T00:00:00Z",
        updated_at="2026-05-18T00:00:00Z",
    )


def _material(run_id: str, worker_id: str, stage: Stage, call_id: str) -> ApprovedMaterial:
    return ApprovedMaterial(
        material_id=f"mat-{worker_id}",
        run_id=run_id,
        call_id=call_id,
        worker_id=worker_id,
        stage=stage,
        target_name="report",
        l1_uri=f"viking://resources/workflow/{run_id}/{stage.value}/{worker_id}/{call_id}/report.md",
        l1_sha256="a" * 64,
        l1_size_bytes=100,
        l2_index_uri=f"viking://resources/workflow/{run_id}/{stage.value}/{worker_id}/{call_id}/evidence/index.json",
        l2_index=L2Index(
            entries=(
                L2Entry(
                    evidence_id="e1",
                    uri=f"viking://resources/workflow/{run_id}/{stage.value}/{worker_id}/{call_id}/evidence/index.json",
                    kind="provider_attempts",
                    source="openbb",
                    sha256="b" * 64,
                    size_bytes=10,
                ),
            ),
            empty_reason=None,
            index_uri=f"viking://resources/workflow/{run_id}/{stage.value}/{worker_id}/{call_id}/evidence/index.json",
            index_sha256="c" * 64,
            index_size_bytes=10,
        ),
        l1_claims=(),
        approved_at="2026-05-18T00:00:00Z",
        hard_gate_result_path=Path("guard.json"),
    )


def _write_export_claims(state: WorkflowState) -> None:
    (state.run_dir / "reports" / "export-claims.json").write_text(
        (
            '{\n'
            '  "claims": [],\n'
            '  "final_report_path": "reports/final-report.md",\n'
            f'  "run_id": "{state.run_id}",\n'
            '  "schema_version": "control.export_claims.v1"\n'
            '}\n'
        ),
        encoding="utf-8",
    )


def _attempt_doc(
    *,
    run_id: str,
    call_id: str,
    worker_id: str = "market_analyst",
    pack: str = "market",
    status: str = "remote_success",
    raw_ref: str | None = "mongo://openbb_raw_payloads/raw-1",
    normalized_ref: str | None = "mongo://openbb_normalized/norm-1",
) -> dict[str, object]:
    attempt_id = "attempt-1" if worker_id == "market_analyst" and pack == "market" else f"attempt-{pack}"
    return {
        "_id": attempt_id,
        "attempt_id": attempt_id,
        "run_id": run_id,
        "call_id": call_id,
        "worker_id": worker_id,
        "pack": pack,
        "provider": "openbb.us",
        "adapter_id": "openbb.us.market",
        "adapter_kind": "openbb_native",
        "provider_kind": "openbb_native",
        "provider_config_version": "cfg",
        "endpoint": "equity.price.historical",
        "source_role": "market_data",
        "started_at": "2026-05-18T00:00:00Z",
        "finished_at": "2026-05-18T00:00:01Z",
        "status": status,
        "required": True,
        "attempt_required": True,
        "coverage_group": "market",
        "coverage_quorum": 1,
        "priority_source": "system_default",
        "user_preferred": False,
        "from_cache": False,
        "cache_status": None,
        "single_flight_role": "owner",
        "shared_from_attempt_id": None,
        "latency_ms": 20,
        "row_count": 3,
        "raw_ref": raw_ref,
        "normalized_ref": normalized_ref,
        "error_code": None,
        "error_message": None,
        "schema_id": "market.v1",
        "license_note": "approved",
    }


def _minimal_pack_result_for_materializer():
    request = PackRequest(
        run_id="run-materializer",
        call_id="call-market",
        worker_id="market_analyst",
        market=Market.US,
        domain=PackDomain.MARKET,
        ticker="AAPL",
        company_name="Apple",
        start_date="2026-05-01",
        end_date="2026-05-18",
        current_date="2026-05-18",
        currency="USD",
        profile="US",
        freshness_policy=FreshnessPolicy(max_age_seconds=120),
    )
    attempt = ProviderAttempt(
        attempt_id="attempt-materializer",
        run_id=request.run_id,
        call_id=request.call_id,
        worker_id=request.worker_id,
        pack=request.domain.value,
        provider="openbb.us",
        adapter_id="openbb.us.market",
        adapter_kind="openbb_native",
        provider_kind=ProviderKind.OPENBB_NATIVE,
        provider_config_version="cfg-v1",
        endpoint="equity.price.historical",
        source_role=SourceRole.MARKET_DATA,
        started_at="2026-05-18T00:00:00Z",
        finished_at="2026-05-18T00:00:01Z",
        status=ProviderStatus.REMOTE_SUCCESS,
        required=True,
        attempt_required=True,
        coverage_group="market",
        coverage_quorum=1,
        priority_source=PrioritySource.SYSTEM_DEFAULT,
        user_preferred=False,
        from_cache=False,
        cache_status=None,
        single_flight_role="owner",
        shared_from_attempt_id=None,
        latency_ms=20,
        row_count=5,
        raw_ref="mongo://openbb_raw_payloads/raw-materializer",
        normalized_ref="mongo://openbb_normalized/norm-materializer",
        error_code=None,
        error_message=None,
        schema_id="market.v1",
        license_note="approved",
        source_metadata={"transport": "tcp"},
    )
    readiness = Readiness(
        status=ReadinessStatus.READY,
        coverage={"market": "ready"},
        required_domains=("market",),
        missing_domains=(),
        blocking_gap_ids=(),
        non_blocking_gap_ids=(),
        root_cause=None,
    )
    audit = PackAuditPayload(
        request=request,
        openbb_runtime_marker="openbb-v4.7.0",
        openbb_extension_version="claw.v1",
        run_provider_plan_id=request.run_id,
        call_specs=(),
        attempts=(attempt,),
        cache_receipts=(),
        data_gaps=(),
        conflicts=(),
        readiness=readiness,
        chart_assets=(),
        raw_refs=(attempt.raw_ref or "",),
        normalized_refs=(attempt.normalized_ref or "",),
        normalized_bundle_ref=None,
        payload_hash="sha256:test",
        generated_at="2026-05-18T00:00:00Z",
    )
    from claw_trade.data_gateway.models import DomainPackResult

    return DomainPackResult(
        request=request,
        reader_brief_md="market summary",
        compact_facts={},
        attempts=(attempt,),
        cache_receipts=(),
        data_gaps=(),
        conflicts=(),
        readiness=readiness,
        chart_assets=(),
        raw_refs=(attempt.raw_ref or "",),
        normalized_refs=(attempt.normalized_ref or "",),
        normalized_bundle_ref=None,
        audit_ref="viking://resources/workflow/run-materializer/frontline/market_analyst/call-market/evidence/pack_audit.json",
        audit_payload_hash="sha256:audit",
        audit_payload=audit,
    )
