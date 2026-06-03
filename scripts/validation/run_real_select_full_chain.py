from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from claw_trade.data_gateway.selection_batch import (
    build_selection_provider_batch_plan,
    fetch_selection_batch_from_data_gateway,
)
from claw_trade.runtime.openclaw_client import OpenClawClient
from claw_trade.runtime.openclaw_local_runner import create_default_runner
from claw_trade.selection.controller import SelectionController
from claw_trade.selection.data_job import SelectionDataJob
from claw_trade.selection.models import (
    SelectionMarket,
    SelectionProfile,
    SelectionRunPlan,
    SelectionTriggerSource,
)
from claw_trade.selection.store import restore_selection_run_store
from claw_trade.selection.strategy_config import (
    load_cn_a_selection_v1_strategy,
    load_cn_a_selection_v1_strategy_config_ref,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run real CN_A selection data job, then /select live OpenClaw workers."
    )
    parser.add_argument("--trade-date", required=True)
    parser.add_argument("--selection-run-id", default="")
    parser.add_argument("--request-id", default="")
    parser.add_argument("--selection-runs-root", default="runs/selection")
    parser.add_argument("--workflow-evidence-root", default="runs/selection/workflows")
    parser.add_argument("--output-json", required=True)
    args = parser.parse_args()

    selection_run_id = args.selection_run_id.strip() or f"sel-real-fullchain-{uuid4().hex[:12]}"
    request_id = args.request_id.strip() or f"select-real-fullchain-{uuid4().hex[:12]}"
    runs_root = Path(args.selection_runs_root)

    store = restore_selection_run_store(selection_runs_root=runs_root)
    provider_plan = build_selection_provider_batch_plan(
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=args.trade_date,
    )
    config_ref = load_cn_a_selection_v1_strategy_config_ref(SelectionMarket.CN_A, SelectionProfile.CN_A)
    if not config_ref:
        raise RuntimeError("CN_A selection strategy config ref is not approved")

    plan = SelectionRunPlan(
        selection_run_id=selection_run_id,
        market=SelectionMarket.CN_A,
        profile=SelectionProfile.CN_A,
        trade_date=args.trade_date,
        lookback_trading_days=provider_plan.lookback_trading_days,
        universe_scope=provider_plan.universe_scope,
        provider_batch_plan_ref=provider_plan.plan_id,
        approved_strategy_config_ref=config_ref,
        trigger_source=SelectionTriggerSource.MANUAL_RERUN,
    )

    data_job = SelectionDataJob(
        store=store,
        provider_fetch_batch=fetch_selection_batch_from_data_gateway,
        strategy_config_loader=load_cn_a_selection_v1_strategy,
        evidence_root=runs_root,
        now_fn=lambda: datetime.now(tz=UTC),
    )
    data_execution = data_job.run(plan)

    openclaw = OpenClawClient(create_default_runner())
    probe = openclaw.probe()
    if not probe.ok:
        payload = _base_payload(
            selection_run_id=selection_run_id,
            request_id=request_id,
            data_execution=data_execution,
        )
        payload["select_result"] = {
            "code": "blocked_ask_human",
            "failure_reason": f"openclaw_probe_failed:{probe.reason}",
        }
        _write_json(Path(args.output_json), payload)
        return 2

    controller = SelectionController(
        store=store,
        openclaw=openclaw,
        workflow_evidence_root=Path(args.workflow_evidence_root),
        now_fn=lambda: datetime.now(tz=UTC),
    )
    select_result = controller.handle_select_command(
        raw_text=f"/select {args.trade_date}",
        request_id=request_id,
    )

    payload = _base_payload(
        selection_run_id=selection_run_id,
        request_id=request_id,
        data_execution=data_execution,
    )
    payload["select_result"] = {
        "code": select_result.code.value,
        "ok": select_result.ok,
        "select_workflow_run_id": select_result.select_workflow_run_id,
        "evidence_path": str(select_result.evidence_path),
        "unavailable_code": select_result.unavailable_code.value if select_result.unavailable_code else None,
        "failure_reason": select_result.failure_reason,
        "chat_text": select_result.chat_text,
    }
    if select_result.decision is not None:
        payload["select_result"]["decision"] = {
            "enter_report": [item.ticker for item in select_result.decision.enter_report],
            "watch": [item.ticker for item in select_result.decision.watch],
            "reject": [item.ticker for item in select_result.decision.reject],
            "approved_material_id": select_result.decision.approved_material_id,
        }
    _write_json(Path(args.output_json), payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if select_result.ok else 2


def _base_payload(*, selection_run_id: str, request_id: str, data_execution: object) -> dict[str, object]:
    record = data_execution.record
    candidate_pack_ref = record.data_run.candidate_pack_ref
    return {
        "selection_run_id": selection_run_id,
        "request_id": request_id,
        "data_job": {
            "status": record.data_run.status.value,
            "failure_code": record.data_run.failure_code,
            "failure_reason": record.data_run.failure_reason,
            "trade_date": record.run_plan.trade_date,
            "provider_attempt_refs": list(data_execution.provider_attempt_refs),
            "normalized_ref_count": len(data_execution.normalized_refs),
            "top20_tickers": list(data_execution.top20_tickers),
            "candidate_pack_material_id": candidate_pack_ref.material_id if candidate_pack_ref else None,
            "candidate_pack_l1_uri": candidate_pack_ref.l1_uri if candidate_pack_ref else None,
            "candidate_pack_summary_ref": candidate_pack_ref.pack_summary_ref if candidate_pack_ref else None,
            "evidence_path": str(data_execution.evidence_path),
            "data_gaps": [
                {
                    "gap_code": gap.gap_code,
                    "severity": gap.severity.value,
                    "reader_message": gap.reader_message,
                    "attempt_refs": list(gap.attempt_refs),
                    "source_metadata": dict(gap.source_metadata or {}),
                }
                for gap in record.data_run.data_gaps
            ],
        },
    }


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
