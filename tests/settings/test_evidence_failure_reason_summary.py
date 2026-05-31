from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from claw_trade.ui_backend.llm_settings_bridge import LlmSettingsBridge


class _FakeGateway:
    def __init__(self) -> None:
        self._revision = 0
        self._auth_payload: dict[str, Any] = {"ok": True, "message": "ok"}
        self._config: dict[str, object] = {
            "active_provider": "",
            "agents": {"defaults": {"model": ""}},
            "models": {"providers": {}},
        }

    def set_auth_result(self, *, ok: bool, message: str) -> None:
        self._auth_payload = {"ok": ok, "message": message}

    def config_schema_lookup(self, *, path: str) -> dict[str, str]:
        return {"path": path}

    def config_get(self, *, paths):  # type: ignore[no-untyped-def]
        _ = paths
        self._revision += 1
        return {"revision": f"rev-{self._revision}", "parsed": self._config}

    def config_patch(self, *, expected_settings_version, patch):  # type: ignore[no-untyped-def]
        _ = expected_settings_version
        provider, provider_patch = next(iter(patch["models"]["providers"].items()))
        providers = self._config["models"]["providers"]  # type: ignore[index]
        providers[provider] = {
            **providers.get(provider, {}),  # type: ignore[union-attr]
            **provider_patch,
        }
        self._config["active_provider"] = provider
        self._config["agents"]["defaults"]["model"] = patch["agents"]["defaults"]["model"]  # type: ignore[index]
        self._revision += 1
        return {"newHash": f"hash-{self._revision}"}

    def models_auth_status(
        self,
        *,
        provider: str,
        model: str | None = None,
        endpoint_url: str | None = None,
        probe: bool = True,
    ) -> dict[str, Any]:
        _ = provider, model, endpoint_url, probe
        return dict(self._auth_payload)

    def models_probe_status(
        self,
        *,
        provider: str,
        model: str | None = None,
        endpoint_url: str | None = None,
    ) -> dict[str, Any]:
        _ = endpoint_url
        status = "ok" if bool(self._auth_payload.get("ok")) else "auth"
        return {
            "auth": {
                "probes": {
                    "results": [
                        {
                            "provider": provider,
                            "model": model or "",
                            "status": status,
                        }
                    ]
                }
            }
        }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _bridge(tmp_path: Path, gateway: _FakeGateway, run_root: Path) -> LlmSettingsBridge:
    return LlmSettingsBridge(
        gateway,
        embedding_env_path=tmp_path / ".env.local",
        report_model_status_path=tmp_path / "report-model-status.json",
        run_root=run_root,
    )


def test_evidence_failure_reason_summary_reads_real_collect_first_failure_and_sanitizes_message(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-20260523-101500-aaaa1111"
    _write_json(
        run_dir / "state.json",
        {
            "run_id": "run-20260523-101500-aaaa1111",
            "updated_at": "2026-05-23T10:15:00Z",
            "request": {"entry_point": "report_command", "market": "US"},
        },
    )
    _write_json(
        run_dir / "reports" / "collect-first-risk_debate-t00.json",
        {
            "collect_first_compliance": {
                "batch_scope": {"stage": "risk_debate"},
                "completed_items": [],
                "failures_collected": [
                    {
                        "category": "openviking_receipt",
                        "reason": "receipt verify failed: URI=viking://x hash=abc L1 L2 provider attempt #2 raw payload",
                    }
                ],
                "early_stop_exception_used": False,
                "exception_evidence": [],
                "batch_fix_grouping": [],
            }
        },
    )
    _write_json(
        run_dir / "reports" / "export-guard-results.json",
        {
            "ok": True,
            "category": "ok",
            "reason": None,
        },
    )
    bridge = _bridge(tmp_path, gateway, run_root)

    summary = bridge.get_evidence_failure_reason_summary()
    assert summary["state"] == "failure_detected"
    assert summary["severity"] == "warning"
    assert summary["source"] == "workflow.evidence_chain"
    assert summary["latestRun"]["runId"] == "run-20260523-101500-aaaa1111"
    lowered_message = str(summary["userMessage"]).lower()
    assert "uri" not in lowered_message
    assert "hash" not in lowered_message
    assert "l1" not in lowered_message
    assert "l2" not in lowered_message
    assert "provider attempt" not in lowered_message
    assert "raw payload" not in lowered_message
    assert "receipt" not in lowered_message
    lowered_action = str(summary["recommendedAction"]).lower()
    assert "/report" in lowered_action
    assert "uri" not in lowered_action
    assert "hash" not in lowered_action
    assert "l1" not in lowered_action
    assert "l2" not in lowered_action
    assert "receipt" not in lowered_action


def test_evidence_failure_reason_summary_reports_no_failures_without_false_alarm(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-20260523-113000-bbbb2222"
    _write_json(
        run_dir / "state.json",
        {
            "run_id": "run-20260523-113000-bbbb2222",
            "updated_at": "2026-05-23T11:30:00Z",
            "request": {"entry_point": "report_command", "market": "CN_A"},
        },
    )
    _write_json(
        run_dir / "reports" / "collect-first-frontline-t00.json",
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
    _write_json(
        run_dir / "reports" / "export-guard-results.json",
        {
            "ok": True,
            "category": "ok",
            "reason": None,
        },
    )
    bridge = _bridge(tmp_path, gateway, run_root)

    summary = bridge.get_evidence_failure_reason_summary()
    assert summary["state"] == "no_failures"
    assert summary["severity"] == "success"
    assert "未发现证据链失败" in str(summary["userMessage"])


def test_evidence_failure_reason_summary_does_not_create_new_report_hard_block(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-20260523-120000-cccc3333"
    _write_json(
        run_dir / "state.json",
        {
            "run_id": "run-20260523-120000-cccc3333",
            "updated_at": "2026-05-23T12:00:00Z",
            "request": {"entry_point": "report_command", "market": "US"},
        },
    )
    _write_json(
        run_dir / "reports" / "collect-first-frontline-t00.json",
        {
            "collect_first_compliance": {
                "batch_scope": {"stage": "frontline"},
                "completed_items": [],
                "failures_collected": [
                    {
                        "category": "provider_evidence",
                        "reason": "provider request mismatch",
                    }
                ],
                "early_stop_exception_used": False,
                "exception_evidence": [],
                "batch_fix_grouping": [],
            }
        },
    )

    bridge = _bridge(tmp_path, gateway, run_root)
    bridge.save_llm_config_via_openclaw(
        draft={
            "provider": "deepseek",
            "defaultModel": "deepseek-chat",
            "endpointUrl": "https://api.example.com",
            "apiKeyReplacement": "sk-user-1",
        },
        expected_settings_version="v_1",
        request_id="s11-save-1",
    )
    gateway.set_auth_result(ok=True, message="ready")
    tested = bridge.test_llm_via_openclaw(
        {"provider": "deepseek", "model": "deepseek-chat", "endpointUrl": "https://api.example.com"},
        request_id="s11-test-1",
    )
    assert tested["ok"] is True
    readiness_before = bridge.get_report_model_readiness()
    assert readiness_before.ready is True

    summary = bridge.get_evidence_failure_reason_summary()
    readiness_after = bridge.get_report_model_readiness()

    assert summary["state"] == "failure_detected"
    assert readiness_after.ready is True
    bridge.assert_report_model_ready()


def test_evidence_failure_reason_summary_does_not_false_alarm_on_non_evidence_failure(tmp_path: Path) -> None:
    gateway = _FakeGateway()
    run_root = tmp_path / "runs"
    run_dir = run_root / "run-20260523-130000-dddd4444"
    _write_json(
        run_dir / "state.json",
        {
            "run_id": "run-20260523-130000-dddd4444",
            "updated_at": "2026-05-23T13:00:00Z",
            "request": {"entry_point": "report_command", "market": "US"},
        },
    )
    _write_json(
        run_dir / "reports" / "collect-first-frontline-t00.json",
        {
            "collect_first_compliance": {
                "batch_scope": {"stage": "frontline"},
                "completed_items": [],
                "failures_collected": [
                    {
                        "category": "runtime_health",
                        "reason": "security policy mismatch",
                    }
                ],
                "early_stop_exception_used": False,
                "exception_evidence": [],
                "batch_fix_grouping": [],
            }
        },
    )
    _write_json(
        run_dir / "reports" / "export-guard-results.json",
        {
            "ok": True,
            "category": "ok",
            "reason": None,
        },
    )

    bridge = _bridge(tmp_path, gateway, run_root)
    summary = bridge.get_evidence_failure_reason_summary()
    assert summary["state"] == "no_failures"
    assert summary["severity"] == "success"
