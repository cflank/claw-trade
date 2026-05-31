from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge


class _FakeGateway:
    def config_schema_lookup(self, *, path: str) -> dict[str, str]:
        return {"path": path}

    def config_get(self, *, paths):  # type: ignore[no-untyped-def]
        _ = paths
        return {"revision": "rev-1", "parsed": {"active_provider": "", "agents": {"defaults": {"model": ""}}, "models": {"providers": {}}}}

    def config_patch(self, *, expected_settings_version, patch):  # type: ignore[no-untyped-def]
        _ = expected_settings_version, patch
        return {"newHash": "hash-1"}

    def models_auth_status(
        self,
        *,
        provider: str,
        model: str | None = None,
        endpoint_url: str | None = None,
        probe: bool = True,
    ) -> dict[str, Any]:
        _ = provider, model, endpoint_url, probe
        return {"ok": False, "message": "not used"}


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _bridge(tmp_path: Path, run_root: Path) -> LlmSettingsBridge:
    return LlmSettingsBridge(
        _FakeGateway(),
        embedding_env_path=tmp_path / ".env.local",
        report_model_status_path=tmp_path / "report-model-status.json",
        run_root=run_root,
    )


def test_live_run_gap_summary_uses_real_collect_first_record_and_aggregates_required_fields(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-20260523-090000-aaaa1111"
    _write_json(
        run_dir / "state.json",
        {
            "run_id": "run-20260523-090000-aaaa1111",
            "updated_at": "2026-05-23T09:15:00Z",
            "request": {"entry_point": "report_command", "market": "US"},
        },
    )
    _write_json(
        run_dir / "reports" / "collect-first-frontline-t00.json",
        {
            "collect_first_compliance": {
                "batch_scope": {"stage": "frontline"},
                "completed_items": [{"worker_id": "market_analyst"}],
                "failures_collected": [{"worker_id": "news_analyst"}, {"worker_id": "social_analyst"}],
                "early_stop_exception_used": False,
                "exception_evidence": [],
                "batch_fix_grouping": [{"root_cause": "network"}],
            }
        },
    )
    _write_json(
        run_dir / "reports" / "collect-first-risk_debate-t00.json",
        {
            "collect_first_compliance": {
                "batch_scope": {"stage": "risk_debate"},
                "completed_items": [{"worker_id": "risk_guardian"}, {"worker_id": "risk_moderator"}],
                "failures_collected": [],
                "early_stop_exception_used": True,
                "exception_evidence": [{"category": "provider_untrusted"}],
                "batch_fix_grouping": [],
            }
        },
    )
    bridge = _bridge(tmp_path, run_root)

    summary = bridge.get_live_run_gap_summary()
    assert summary["state"] == "gaps_detected"
    latest = summary["latestRun"]
    assert latest["runId"] == "run-20260523-090000-aaaa1111"
    assert latest["gapCount"] == 2
    assert latest["collectFirstReportCount"] == 2
    compliance = latest["collectFirstCompliance"]
    assert compliance["batchScope"]["stageCount"] == 2
    assert set(compliance["batchScope"]["stages"]) == {"frontline", "risk_debate"}
    assert compliance["completedItems"] == 3
    assert compliance["failuresCollected"] == 2
    assert compliance["earlyStopExceptionUsed"] is True
    assert compliance["exceptionEvidence"] == 1
    assert compliance["batchFixGrouping"] == 1


def test_live_run_gap_summary_returns_no_records_and_action_when_live_run_missing(tmp_path: Path) -> None:
    bridge = _bridge(tmp_path, tmp_path / "empty-runs")
    summary = bridge.get_live_run_gap_summary()
    assert summary["state"] == "no_records"
    assert summary["latestRun"] is None
    assert "执行一次 /report" in str(summary["recommendedAction"])


def test_live_run_gap_summary_ignores_non_report_entry_and_reports_no_gaps(tmp_path: Path) -> None:
    run_root = tmp_path / "runs"
    non_report = run_root / "run-20260523-080000-ignore"
    _write_json(
        non_report / "state.json",
        {
            "run_id": "run-20260523-080000-ignore",
            "updated_at": "2026-05-23T08:10:00Z",
            "request": {"entry_point": "normal_chat", "market": "US"},
        },
    )
    report_run = run_root / "run-20260523-100000-bbbb2222"
    _write_json(
        report_run / "state.json",
        {
            "run_id": "run-20260523-100000-bbbb2222",
            "updated_at": "2026-05-23T10:20:00Z",
            "request": {"entry_point": "report_command", "market": "HK"},
        },
    )
    _write_json(
        report_run / "reports" / "collect-first-frontline-t00.json",
        {
            "collect_first_compliance": {
                "batch_scope": {"stage": "frontline"},
                "completed_items": [{"worker_id": "market_analyst"}],
                "failures_collected": [],
                "early_stop_exception_used": False,
                "exception_evidence": [],
                "batch_fix_grouping": [],
            }
        },
    )
    bridge = _bridge(tmp_path, run_root)
    summary = bridge.get_live_run_gap_summary()
    assert summary["state"] == "no_gaps"
    assert summary["latestRun"]["runId"] == "run-20260523-100000-bbbb2222"
    assert summary["latestRun"]["gapCount"] == 0
