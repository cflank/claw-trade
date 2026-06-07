from __future__ import annotations

from claw_trade.ui_contracts.user_dto import (
    to_user_payload,
    toChatContextForUser,
    toChatMessageForUser,
    toIntentDraftForUser,
    toPriceAlertForUser,
    toReportQueueSnapshotForUser,
    toReportTaskForUser,
    toScheduledReportForUser,
)


def test_chat_message_mapper_outputs_design_fields_only() -> None:
    internal = {
        "id": "m1",
        "contextId": "ctx-secret",
        "contextKind": "normal_chat",
        "actor": "assistant",
        "kind": "plain",
        "text": "已收到",
        "createdAt": "2026-05-19T09:00:00Z",
        "runId": "run-x",
        "providerChannelId": "ch-1",
    }
    payload = to_user_payload(toChatMessageForUser(internal))
    assert payload == {
        "messageId": "m1",
        "contextKind": "normal_chat",
        "actor": "assistant",
        "kind": "plain",
        "text": "已收到",
        "cardId": None,
        "reportId": None,
        "taskId": None,
        "createdAt": "2026-05-19T09:00:00Z",
    }


def test_chat_context_mapper_hides_locked_workflow_id_and_unapproved_fields() -> None:
    internal = {
        "id": "ctx-1",
        "kind": "task_following",
        "title": "BTC 报告跟进",
        "activeTaskId": "task-1",
        "activeReportId": None,
        "lockedWorkflowRunId": "run-x",
        "workflowRunning": True,
    }
    payload = to_user_payload(toChatContextForUser(internal))
    assert payload == {
        "contextId": "ctx-1",
        "kind": "task_following",
        "title": "BTC 报告跟进",
        "activeTaskId": "task-1",
        "activeReportId": None,
    }
    assert "workflow_running" not in payload
    assert "workflowRunning" not in payload


def test_intent_mapper_outputs_design_fields_and_hides_dedupe_fields() -> None:
    internal = {
        "id": "draft-1",
        "kind": "report",
        "instrumentCode": "BTC",
        "instrumentName": "Bitcoin",
        "market": "CRYPTO",
        "reportDateRange": {"startDate": "2026-01-01", "endDate": "2026-05-19"},
        "schedule": None,
        "priceCondition": None,
        "notification": {"channel": "wechat_clawbot", "enabled": True},
        "status": "draft",
        "sourceMessageId": "msg-secret",
        "workflowSettings": {"maxDebateRounds": 1},
        "dedupeKey": "dedupe-secret",
    }
    payload = to_user_payload(toIntentDraftForUser(internal))
    assert set(payload) == {
        "draftId",
        "kind",
        "instrumentCode",
        "instrumentName",
        "market",
        "reportDateRange",
        "schedule",
        "priceCondition",
        "notification",
        "status",
    }
    assert payload["draftId"] == "draft-1"
    assert payload["market"] == "CRYPTO"


def test_report_task_and_snapshot_mapper_output_design_fields_only() -> None:
    task = {
        "id": "task-1",
        "source": "manual",
        "status": "queued",
        "instrumentCode": "BTC",
        "instrumentName": "Bitcoin",
        "market": "CRYPTO",
        "companyName": "Bitcoin",
        "currencySymbol": "$",
        "startDate": "2026-01-01",
        "endDate": "2026-05-19",
        "currentDate": "2026-05-19",
        "queuePosition": 1,
        "progress": None,
        "reportId": None,
        "failure": None,
        "createdAt": "2026-05-19T09:00:00Z",
        "startedAt": None,
        "finishedAt": None,
        "runId": "run-secret",
        "profile": "CRYPTO",
        "priority": 10,
        "dedupeKey": "dedupe-secret",
    }
    snapshot = {"runningTask": None, "queuedTasks": [task], "queuedCount": 1, "queueLimit": 10, "isFull": False}
    task_payload = to_user_payload(toReportTaskForUser(task))
    snapshot_payload = to_user_payload(toReportQueueSnapshotForUser(snapshot))
    assert task_payload["taskId"] == "task-1"
    assert task_payload["statusLabel"] == "排队中"
    assert "runId" not in task_payload
    assert "profile" not in task_payload
    assert "priority" not in task_payload
    assert "dedupeKey" not in task_payload
    assert snapshot_payload["queueLimit"] == 10
    assert snapshot_payload["isFull"] is False


def test_scheduled_and_price_alert_mapper_return_design_dto_only() -> None:
    schedule = {
        "id": "sr-1",
        "instrumentCode": "AAPL",
        "instrumentName": "Apple",
        "market": "US",
        "frequency": "daily",
        "timeOfDay": "09:30",
        "weekday": None,
        "notification": {"channel": "wechat_clawbot", "enabled": True},
        "state": "active",
        "nextRunAt": "2026-05-20T09:30:00Z",
        "lastRunTaskId": "hidden",
        "credentialRef": "secret",
    }
    alert = {
        "id": "pa-1",
        "instrumentCode": "BTC",
        "instrumentName": "Bitcoin",
        "market": "CRYPTO",
        "condition": {"type": "price_threshold", "operator": "above", "value": 70000, "window": "24h"},
        "notification": {"channel": "in_app", "enabled": True},
        "state": "active",
        "lastCheckedAt": None,
        "triggeredAt": None,
        "lastErrorMessage": None,
        "providerChannelId": "hidden",
    }
    schedule_payload = to_user_payload(toScheduledReportForUser(schedule))
    alert_payload = to_user_payload(toPriceAlertForUser(alert))
    assert set(schedule_payload) == {
        "scheduledReportId",
        "instrumentCode",
        "instrumentName",
        "market",
        "frequency",
        "timeOfDay",
        "weekday",
        "notification",
        "state",
        "nextRunAt",
    }
    assert set(alert_payload) == {
        "priceAlertId",
        "instrumentCode",
        "instrumentName",
        "market",
        "condition",
        "notification",
        "state",
        "lastCheckedAt",
        "triggeredAt",
        "lastErrorMessage",
    }
