from __future__ import annotations

import hashlib
import json
from pathlib import Path

from claw_trade.artifacts.openviking_client import OpenVikingClient
from claw_trade.artifacts.refs import (
    MaterialReceipt,
    MaterialReceiptVerification,
    make_material_target,
)
from claw_trade.guards.fundamental_claim_gate import CLAIM_INPUT_INVALID, UNSUPPORTED_CLAIM
from claw_trade.guards.fundamental_claim_rules import CLAIM_DICTIONARY_REVISION_ID
from claw_trade.guards.fundamental_gate_inputs import (
    OPENVIKING_RECEIPT_INVALID,
    VISIBLE_TOOLS_INVALID,
    build_fundamental_gate_inputs,
    build_material_gate_input,
)
from claw_trade.runtime.evidence_reader import ProviderEvidence
from claw_trade.workflow.models import ReadPolicy, Stage, WorkerCall

from tests.fakes.openviking_store import FakeOpenVikingStore


def test_material_gate_input_passes_when_visible_tools_and_receipt_valid(tmp_path: Path) -> None:
    call, evidence = _sample_call_and_evidence(tmp_path)
    _write_provider_and_visible_tools(
        evidence=evidence,
        tools=("claw_get_fundamental_pack", "openviking_write_material"),
    )
    openviking = _seed_receipt_and_client(call=call, evidence=evidence, receipt_content=b"fundamental report")
    result = build_material_gate_input(call=call, evidence=evidence, openviking=openviking)
    assert result.visible_tools_ok is True
    assert result.receipt_ok is True
    assert result.reason_codes == ()
    assert result.visible_tools_guard.ok is True
    assert result.receipt_guard.ok is True


def test_material_gate_input_emits_visible_tools_invalid_reason_code(tmp_path: Path) -> None:
    call, evidence = _sample_call_and_evidence(tmp_path)
    _write_provider_and_visible_tools(
        evidence=evidence,
        tools=(
            "claw_get_fundamental_pack",
            "openviking_write_material",
            "tushare.fina_indicator",
        ),
    )
    openviking = _seed_receipt_and_client(call=call, evidence=evidence, receipt_content=b"fundamental report")
    result = build_material_gate_input(call=call, evidence=evidence, openviking=openviking)
    assert result.visible_tools_ok is False
    assert result.receipt_ok is True
    assert VISIBLE_TOOLS_INVALID in result.reason_codes
    assert result.visible_tools_guard.ok is False
    assert result.visible_tools_guard.reason is not None


def test_material_gate_input_emits_openviking_receipt_invalid_for_hash_mismatch(tmp_path: Path) -> None:
    call, evidence = _sample_call_and_evidence(tmp_path)
    _write_provider_and_visible_tools(
        evidence=evidence,
        tools=("claw_get_fundamental_pack", "openviking_write_material"),
    )
    openviking = _seed_receipt_and_client(
        call=call,
        evidence=evidence,
        receipt_content=b"fundamental report",
        stored_content=b"fundamental rep0rt",
    )
    result = build_material_gate_input(call=call, evidence=evidence, openviking=openviking)
    assert result.visible_tools_ok is True
    assert result.receipt_ok is False
    assert OPENVIKING_RECEIPT_INVALID in result.reason_codes
    assert result.receipt_guard.ok is False
    assert result.receipt_guard.reason is not None and "sha256" in result.receipt_guard.reason


def test_build_fundamental_gate_inputs_emits_unsupported_claim_reason_code(tmp_path: Path) -> None:
    call, evidence = _sample_call_and_evidence(tmp_path)
    _write_provider_and_visible_tools(
        evidence=evidence,
        tools=("claw_get_fundamental_pack", "openviking_write_material"),
    )
    openviking = _seed_receipt_and_client(call=call, evidence=evidence, receipt_content=b"fundamental report")
    report_path = call.evidence_dir / "report.md"
    pack_path = call.evidence_dir / "fundamental-pack.json"
    report_path.write_text("我们给出目标价 2200 元，并维持买入评级。", encoding="utf-8")
    pack_path.write_text(
        json.dumps(
            {
                "facts": {
                    "valuation": {"pe_ttm": 21.2, "pb": 5.8, "total_mv": 2.4e12},
                    "financial_indicators": {
                        "roe": 0.3,
                        "roa": 0.18,
                        "gross_margin": 0.9,
                        "netprofit_margin": 0.5,
                        "debt_to_assets": 0.2,
                    },
                    "income_statement": {"revenue": 1500.0, "net_profit": 700.0, "eps": 30.2},
                    "business_segments": [{"bz_item": "白酒", "bz_sales": 1000.0}],
                },
                "field_sources": {
                    "valuation.pe_ttm": {"provider": "tushare"},
                    "valuation.pb": {"provider": "tushare"},
                    "valuation.total_mv": {"provider": "tushare"},
                    "financial_indicators.roe": {"provider": "tushare"},
                    "financial_indicators.roa": {"provider": "tushare"},
                    "financial_indicators.gross_margin": {"provider": "tushare"},
                    "financial_indicators.netprofit_margin": {"provider": "tushare"},
                    "financial_indicators.debt_to_assets": {"provider": "tushare"},
                    "income_statement.revenue": {"provider": "tushare"},
                    "income_statement.net_profit": {"provider": "tushare"},
                    "income_statement.eps": {"provider": "tushare"},
                    "business_segments.items": {"provider": "tushare"},
                },
                "evidence_capabilities": {
                    "target_price": {"status": "blocked"},
                    "rating": {"status": "blocked"},
                    "valuation_judgment": {"status": "blocked"},
                    "financial_trend": {"status": "available"},
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    gate_inputs = build_fundamental_gate_inputs(
        call=call,
        evidence=evidence,
        openviking=openviking,
        report_path=report_path,
        pack_path=pack_path,
    )
    assert gate_inputs.material_gate.receipt_ok is True
    assert gate_inputs.material_gate.visible_tools_ok is True
    assert gate_inputs.claim_gate.claim_guard.ok is False
    assert UNSUPPORTED_CLAIM in gate_inputs.claim_gate.reason_codes
    assert gate_inputs.claim_gate.dictionary_revision_id == CLAIM_DICTIONARY_REVISION_ID
    assert gate_inputs.claim_gate.report_path == report_path
    assert gate_inputs.claim_gate.pack_path == pack_path


def test_build_fundamental_gate_inputs_marks_claim_input_invalid_when_pack_missing(tmp_path: Path) -> None:
    call, evidence = _sample_call_and_evidence(tmp_path)
    _write_provider_and_visible_tools(
        evidence=evidence,
        tools=("claw_get_fundamental_pack", "openviking_write_material"),
    )
    openviking = _seed_receipt_and_client(call=call, evidence=evidence, receipt_content=b"fundamental report")
    report_path = call.evidence_dir / "report.md"
    report_path.write_text("证据不足，无法给出目标价。", encoding="utf-8")
    pack_path = call.evidence_dir / "missing-pack.json"

    gate_inputs = build_fundamental_gate_inputs(
        call=call,
        evidence=evidence,
        openviking=openviking,
        report_path=report_path,
        pack_path=pack_path,
    )
    assert gate_inputs.claim_gate.claim_guard.ok is False
    assert CLAIM_INPUT_INVALID in gate_inputs.claim_gate.reason_codes


def _sample_call_and_evidence(tmp_path: Path) -> tuple[WorkerCall, ProviderEvidence]:
    target = make_material_target("run-1", Stage.FRONTLINE, "fundamental_analyst", "call-1")
    evidence_dir = tmp_path / "runs" / "run-1" / "calls" / "call-1" / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    call = WorkerCall(
        call_id="call-1",
        run_id="run-1",
        worker_id="fundamental_analyst",
        stage=Stage.FRONTLINE,
        profile="CN_A",
        ticker="600519.SH",
        company_name="贵州茅台",
        market="CN_A",
        currency="CNY",
        currency_symbol="¥",
        current_date="2026-05-07",
        start_date="2026-01-01",
        end_date="2026-05-07",
        allowed_tools=("claw_get_fundamental_pack", "openviking_write_material"),
        upstream_materials=(),
        openviking_read_capabilities=(),
        material_target=target,
        read_policy=ReadPolicy(),
        evidence_dir=evidence_dir,
        stop_after_first_response=False,
    )

    workspace_path = evidence_dir / "workspace.json"
    provider_request_path = evidence_dir / "provider-request.json"
    visible_tools_path = evidence_dir / "visible-tools.json"
    first_response_path = evidence_dir / "first-response.json"
    tool_calls_path = evidence_dir / "tool-calls.json"
    raw_output_path = evidence_dir / "raw-output.md"
    receipt_path = evidence_dir / "openviking-receipt.json"
    _write_json(workspace_path, {"source": "openclaw_workspace_loader"})
    _write_json(first_response_path, {"source": "openclaw_first_model_event"})
    _write_json(tool_calls_path, {"source": "model_tool_events", "calls": []})
    raw_output_path.write_text("report content", encoding="utf-8")
    receipt_path.write_text("{}", encoding="utf-8")

    evidence = ProviderEvidence(
        run_id="run-1",
        call_id="call-1",
        worker_id="fundamental_analyst",
        stage=Stage.FRONTLINE,
        openclaw_run_id="oc-run-1",
        provider_request_id="req-1",
        provider_request_id_status="returned",
        workspace_evidence_path=workspace_path,
        provider_request_path=provider_request_path,
        visible_tools_path=visible_tools_path,
        first_response_path=first_response_path,
        tool_calls_status="recorded",
        tool_calls_path=tool_calls_path,
        raw_output_path=raw_output_path,
        openviking_receipt_path=receipt_path,
    )
    return call, evidence


def _write_provider_and_visible_tools(evidence: ProviderEvidence, tools: tuple[str, ...]) -> None:
    _write_json(
        evidence.provider_request_path,
        {"source": "provider_request_capture", "payload": {"tools": list(tools)}},
    )
    _write_json(
        evidence.visible_tools_path,
        {
            "source": "provider_request",
            "provider_request_path": str(evidence.provider_request_path),
            "tools": list(tools),
        },
    )


def _seed_receipt_and_client(
    *,
    call: WorkerCall,
    evidence: ProviderEvidence,
    receipt_content: bytes,
    stored_content: bytes | None = None,
) -> OpenVikingClient:
    store = FakeOpenVikingStore()
    store.seed_object(call.material_target.l1_uri, stored_content or receipt_content)
    receipt_path = evidence.openviking_receipt_path
    if receipt_path is None:
        raise ValueError("openviking_receipt_path 缺失")
    store.seed_receipt(receipt_path, _make_receipt(call, receipt_content))
    return OpenVikingClient(_FakeBackendAdapter(store))


def _make_receipt(call: WorkerCall, content: bytes) -> MaterialReceipt:
    digest = hashlib.sha256(content).hexdigest()
    target = call.material_target
    return MaterialReceipt(
        uri=target.l1_uri,
        run_id=target.run_id,
        call_id=target.call_id,
        worker_id=target.worker_id,
        stage=target.stage,
        target_name=target.target_name,
        sha256=digest,
        size_bytes=len(content),
        written_at="2026-05-07T10:00:00Z",
        receipt_id="receipt-1",
        source="openviking_adapter_verified_receipt",
        receipt_label="verified_openviking_write_receipt",
        receipt_origin="adapter_verified_non_native",
        is_openviking_native_receipt=False,
        verification=MaterialReceiptVerification(
            verified=True,
            method="openviking_write_then_stat_then_readback_sha_size_identity_check",
        ),
    )


class _FakeBackendAdapter:
    def __init__(self, store: FakeOpenVikingStore) -> None:
        self._store = store

    def ensure_namespace(self, namespace: str) -> None:
        return None

    def fetch_receipt_by_path(self, receipt_path: Path) -> MaterialReceipt:
        return self._store.read_receipt(receipt_path)

    def fetch_stat_by_uri(self, uri: str):
        return self._store.stat(uri)

    def fetch_content_by_uri(self, uri: str) -> bytes:
        return self._store.read(uri)

    def fetch_l2_index_by_uri(self, uri: str | None):
        return self._store.read_l2_index(uri)


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")
