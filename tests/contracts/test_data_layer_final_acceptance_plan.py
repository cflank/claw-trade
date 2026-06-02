from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PLAN_PATH = REPO_ROOT / "scripts" / "validation" / "data_layer_acceptance_plan.py"


def _load_plan_module():
    spec = importlib.util.spec_from_file_location("data_layer_acceptance_plan", PLAN_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _plan():
    return _load_plan_module().final_acceptance_plan()


def test_t14_plan_orders_focused_checks_before_scoped_integration() -> None:
    plan = _plan()

    assert plan.task_id == "T14"
    assert [batch.batch_id for batch in plan.batches] == [
        "focused-unit-contract",
        "scoped-integration",
        "authenticity-audit",
    ]
    assert plan.batches[0].required_commands[0].startswith("uv run pytest")
    assert "test_data_api_contract.py" in " ".join(plan.batches[0].required_commands)
    assert "test_provider_capabilities.py" in " ".join(plan.batches[0].required_commands)
    assert "tests/integration/" in " ".join(plan.batches[1].required_commands)


def test_t14_plan_requires_agents_runtime_preflight_and_wrapped_live_commands() -> None:
    plan = _plan()
    preflight_text = " ".join(plan.runtime_preflight)

    assert "AGENTS 12.1 preflight table" in preflight_text
    assert "OpenViking 1933 health" in preflight_text
    assert "OpenClaw gateway 18789 health" in preflight_text
    assert "scripts/start-control-runtime.sh -- <command>" in preflight_text
    runtime_slots = {"report-command", "a-share-select-command", "ui-provider-display"}
    for slot in plan.runtime_proof_slots:
        if slot.slot_id not in runtime_slots:
            continue
        assert slot.runtime_command_template.startswith("scripts/start-control-runtime.sh -- ")


def test_t14_plan_keeps_required_future_runtime_proof_slots() -> None:
    plan = _plan()
    slots = {slot.slot_id: slot for slot in plan.runtime_proof_slots}

    assert {
        "report-command",
        "a-share-select-command",
        "ui-provider-display",
        "crypto-daily-bar-incremental",
        "cn-a-live-provider",
        "us-live-provider",
        "hk-live-provider",
    } <= set(slots)
    assert slots["report-command"].entrypoint == "/report"
    assert slots["a-share-select-command"].entrypoint == "A-share /select"
    assert "real browser screenshot for UI-facing acceptance" in slots["ui-provider-display"].required_evidence
    assert slots["crypto-daily-bar-incremental"].status == "verified"
    assert "latest normalized daily_bar date 2026-05-31" in slots["crypto-daily-bar-incremental"].required_evidence
    assert slots["us-live-provider"].status == "out_of_scope"
    assert slots["hk-live-provider"].status == "out_of_scope"


def test_t14_plan_collects_first_but_preserves_early_stop_exceptions() -> None:
    plan = _plan()

    assert "continue through recoverable item failures" in plan.collect_first_rule
    for expected in (
        "data authenticity is untrustworthy",
        "architecture boundary drift appears",
        "PM authority or worker conclusion ownership is at risk",
        "legacy data path success appears",
        "runtime preflight fails",
    ):
        assert expected in plan.early_stop_exceptions


def test_t14_plan_includes_cutover_evidence_chain_and_status_audits() -> None:
    plan = _plan()
    checks = " ".join(plan.audit_checks)

    assert "context-aware authenticity keyword scan" in checks
    assert "new data layer cutover contract output" in checks
    assert "evidence-chain audit" in checks
    assert "Mongo collection audit" in checks
    assert "OpenViking role audit" in checks
    assert "cache_hit/shared_result/rate_limited/cached_empty/cooldown_skipped are not remote_success" in checks


def test_t14_plan_allows_only_eight_authorized_mongo_collections() -> None:
    plan = _plan()
    assert plan.allowed_mongo_collections == (
        "normalized_datasets",
        "raw_payloads",
        "provider_attempts",
        "provider_rate_limits",
        "single_flight_calls",
        "provider_result_cache",
        "dataset_manifests",
        "maintenance_jobs",
    )


def test_t14_plan_keeps_openviking_out_of_provider_storage() -> None:
    plan = _plan()

    assert plan.openviking_allowed_roles == ("materials", "lineage", "readback", "summary")


def test_t14_plan_records_scope_notes_without_stale_blockers() -> None:
    plan = _plan()
    notes = {note.note_id: note for note in plan.scope_notes}

    assert plan.status == "planned"
    assert plan.blockers == ()
    assert "crypto-source" in notes
    assert "Binance Public Data / REST Kline" in notes["crypto-source"].detail
    assert "CoinGecko Pro" in notes["crypto-source"].detail
    assert "report-select-boundary" in notes
