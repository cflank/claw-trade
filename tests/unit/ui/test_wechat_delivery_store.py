from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest

from claw_trade.ui_backend.wechat_delivery_store import WechatDeliveryStore


def test_delivery_intent_persists_before_report_completion_without_sensitive_text(tmp_path: Path) -> None:
    path = tmp_path / "wechat-delivery.json"
    store = WechatDeliveryStore(path, now_provider=lambda: "2026-08-07T09:00:00Z")

    store.create_waiting_report("intent-1")
    assert WechatDeliveryStore(path).get_delivery_status("intent-1")["state"] == "waiting_report"
    store.mark_report_pending("intent-1", report_id="report-1", text_footer="期间扣费估算：¥0.03")

    reopened = WechatDeliveryStore(path, now_provider=lambda: "2026-08-07T09:01:00Z")
    assert reopened.get_delivery_status("intent-1") == {
        "intentId": "intent-1",
        "reportId": "report-1",
        "state": "pending",
        "attemptCount": 0,
        "lastError": None,
        "resultUnknown": False,
    }
    raw = path.read_text(encoding="utf-8")
    assert "资金回流" not in raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_report_link_is_durable_before_report_completion(tmp_path: Path) -> None:
    path = tmp_path / "wechat-delivery.json"
    store = WechatDeliveryStore(path)
    store.create_waiting_report("intent-1")

    store.link_report("intent-1", report_id="report-1")

    assert WechatDeliveryStore(path).get_delivery_status("intent-1") == {
        "intentId": "intent-1",
        "reportId": "report-1",
        "state": "waiting_report",
        "attemptCount": 0,
        "lastError": None,
        "resultUnknown": False,
    }


def test_failed_report_finishes_waiting_delivery_intent(tmp_path: Path) -> None:
    store = WechatDeliveryStore(tmp_path / "wechat-delivery.json")
    store.create_waiting_report("intent-failed")

    store.mark_report_failed("intent-failed", error="report_failed")

    assert store.get_delivery_status("intent-failed") == {
        "intentId": "intent-failed",
        "reportId": None,
        "state": "failed",
        "attemptCount": 0,
        "lastError": "report_failed",
        "resultUnknown": False,
    }


def test_binding_code_is_hashed_expires_and_can_only_be_consumed_once(tmp_path: Path) -> None:
    path = tmp_path / "wechat-delivery.json"
    store = WechatDeliveryStore(path, now_provider=lambda: "2026-08-07T09:00:00Z")
    store.save_binding_challenge(code="AB12CD34", expires_at="2026-08-07T09:05:00Z")

    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "AB12CD34" not in path.read_text(encoding="utf-8")
    assert raw["binding_challenge"]["code_hash"]
    assert store.consume_binding_challenge(
        code="WRONG",
        account_id="account-1",
        sender_id="sender-1",
        now="2026-08-07T09:01:00Z",
    ) is False
    assert store.consume_binding_challenge(
        code="AB12CD34",
        account_id="account-1",
        sender_id="sender-1",
        now="2026-08-07T09:01:00Z",
    ) is True
    assert store.consume_binding_challenge(
        code="AB12CD34",
        account_id="account-1",
        sender_id="sender-1",
        now="2026-08-07T09:01:00Z",
    ) is False


def test_expired_binding_code_does_not_create_binding(tmp_path: Path) -> None:
    store = WechatDeliveryStore(tmp_path / "wechat-delivery.json", now_provider=lambda: "2026-08-07T09:10:00Z")
    store.save_binding_challenge(code="AB12CD34", expires_at="2026-08-07T09:05:00Z")

    assert store.consume_binding_challenge(
        code="AB12CD34",
        account_id="account-1",
        sender_id="sender-1",
        now="2026-08-07T09:10:00Z",
    ) is False
    assert store.get_binding() is None


def test_atomic_replace_failure_preserves_previous_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "wechat-delivery.json"
    store = WechatDeliveryStore(path, now_provider=lambda: "2026-08-07T09:00:00Z")
    store.create_waiting_report("intent-1")
    previous = path.read_bytes()

    def _fail_replace(_source: Path, _target: Path) -> None:
        raise OSError("replace failed")

    monkeypatch.setattr("claw_trade.ui_backend.wechat_delivery_store.os.replace", _fail_replace)

    with pytest.raises(OSError, match="replace failed"):
        store.mark_report_pending("intent-1", report_id="report-1")

    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


def test_completion_replay_does_not_reset_unknown_delivery_to_pending(tmp_path: Path) -> None:
    store = WechatDeliveryStore(tmp_path / "wechat-delivery.json")
    store.create_waiting_report("intent-1")
    store.mark_report_pending("intent-1", report_id="report-1")
    store.begin_attempt("intent-1")
    store.mark_unknown("intent-1", error="send_result_unknown")

    store.mark_report_pending("intent-1", report_id="report-1")

    assert store.get_delivery_status("intent-1")["state"] == "unknown"
    with pytest.raises(ValueError, match="delivery_intent_report_mismatch"):
        store.mark_report_pending("intent-1", report_id="different-report")


def test_manual_retry_request_is_atomically_reserved_and_persisted(tmp_path: Path) -> None:
    path = tmp_path / "wechat-delivery.json"
    store = WechatDeliveryStore(path)

    first = store.begin_manual_retry("retry-1")
    concurrent = store.begin_manual_retry("retry-1")
    store.complete_manual_retry("retry-1", attempted=2, sent=1)
    restarted = WechatDeliveryStore(path).begin_manual_retry("retry-1")

    assert first == {"started": True, "in_progress": True, "attempted": 0, "sent": 0}
    assert concurrent == {"started": False, "in_progress": True, "attempted": 0, "sent": 0}
    assert restarted == {"started": False, "in_progress": False, "attempted": 2, "sent": 1}


def test_binding_code_request_is_idempotent_across_restart(tmp_path: Path) -> None:
    path = tmp_path / "wechat-delivery.json"
    first = WechatDeliveryStore(path).get_or_create_binding_challenge(
        "binding-request",
        code="AB12CD34",
        expires_at="2026-08-07T09:05:00Z",
    )
    repeated = WechatDeliveryStore(path).get_or_create_binding_challenge(
        "binding-request",
        code="DEADBEEF",
        expires_at="2026-08-07T09:10:00Z",
    )

    assert repeated == first == {
        "code": "AB12CD34",
        "expires_at": "2026-08-07T09:05:00Z",
    }


def test_older_idempotent_binding_response_remains_usable_after_new_request(tmp_path: Path) -> None:
    store = WechatDeliveryStore(tmp_path / "wechat-delivery.json")
    store.get_or_create_binding_challenge(
        "binding-request-1",
        code="AB12CD34",
        expires_at="2026-08-07T09:05:00Z",
    )
    store.get_or_create_binding_challenge(
        "binding-request-2",
        code="DEADBEEF",
        expires_at="2026-08-07T09:05:00Z",
    )

    assert store.consume_binding_challenge(
        code="AB12CD34",
        account_id="account-1",
        sender_id="sender-1",
        now="2026-08-07T09:01:00Z",
    ) is True
    assert store.consume_binding_challenge(
        code="DEADBEEF",
        account_id="account-1",
        sender_id="sender-1",
        now="2026-08-07T09:01:00Z",
    ) is False
