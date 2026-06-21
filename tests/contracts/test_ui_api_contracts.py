from __future__ import annotations

import pytest
from claw_trade.ui_contracts.api_contracts import (
    UI_API_CONTRACTS,
    get_ui_api_contract,
    validate_ui_api_response,
)


def test_ui_api_contract_registry_covers_core_phase_one_scope() -> None:
    names = {item.name for item in UI_API_CONTRACTS}
    assert "sendChatMessage" in names
    assert "createIntentDraft" in names
    assert "confirmIntentDraft" in names
    assert "enqueueReportTask" in names
    assert "cancelReportTask" in names
    assert "deleteSavedReport" in names
    assert "deleteSavedReports" in names
    assert "createScheduledReport" in names
    assert "createPriceAlert" in names


def test_ui_api_contract_is_transport_agnostic() -> None:
    for contract in UI_API_CONTRACTS:
        shape = contract.response_shape.lower()
        assert "http" not in shape
        assert "websocket" not in shape
        assert "json-rpc" not in shape


def test_ui_api_contract_has_forbidden_internal_fields() -> None:
    contract = get_ui_api_contract("confirmIntentDraft")
    assert "runId" in contract.forbidden_response_fields
    assert "dedupeKey" in contract.forbidden_response_fields


def test_delete_saved_report_contract_exposes_cleanup_result() -> None:
    contract = get_ui_api_contract("deleteSavedReport")
    assert contract.response_shape == (
        "{ deleted: boolean, reportId, userMessage, cleanup: { deletedRunIds, "
        "skippedRunIds, failedRunIds, deletedBytesApprox, warnings } }"
    )
    validate_ui_api_response(
        "deleteSavedReport",
        {
            "deleted": False,
            "reportId": "run-selected",
            "userMessage": "部分报告未删除。",
            "cleanup": {
                "deletedRunIds": [],
                "skippedRunIds": ["run-selected"],
                "failedRunIds": [],
                "deletedBytesApprox": 0,
                "warnings": [],
            },
        },
    )


def test_delete_saved_reports_contract_exposes_batch_cleanup_result() -> None:
    contract = get_ui_api_contract("deleteSavedReports")
    assert contract.response_shape == (
        "{ deletedRunIds, skippedRunIds, failedRunIds, deletedBytesApprox, warnings, "
        "userMessage, runs: { reportId, status, deletedBytesApprox, warnings, userMessage }[] }"
    )
    validate_ui_api_response(
        "deleteSavedReports",
        {
            "deletedRunIds": ["run-deleted"],
            "skippedRunIds": ["run-skipped"],
            "failedRunIds": [],
            "deletedBytesApprox": 128,
            "warnings": [],
            "userMessage": "已删除 1 份报告，1 份已跳过。",
            "runs": [
                {
                    "reportId": "run-deleted",
                    "status": "deleted",
                    "deletedBytesApprox": 128,
                    "warnings": [],
                    "userMessage": "报告已硬删除。",
                },
                {
                    "reportId": "run-skipped",
                    "status": "skipped",
                    "deletedBytesApprox": 0,
                    "warnings": [],
                    "userMessage": "运行仍受保护。",
                },
            ],
        },
    )


def test_validate_ui_api_response_rejects_internal_field() -> None:
    payload = {"task": {"reportTaskId": "task-1", "runId": "run-secret"}}
    with pytest.raises(ValueError, match="禁止字段"):
        validate_ui_api_response("confirmIntentDraft", payload)


def test_validate_ui_api_response_rejects_workflow_running_marker() -> None:
    payload = {"context": {"contextId": "ctx-1", "kind": "normal_chat", "workflowRunning": True}, "messages": []}
    with pytest.raises(ValueError, match="禁止字段"):
        validate_ui_api_response("sendChatMessage", payload)


def test_validate_ui_api_response_allows_reader_facing_report_terms() -> None:
    payload = {
        "report": {"id": "r1", "title": "BTC 报告"},
        "markdown": "BTC 的 L2 生态仍在早期，Layer 2 数据需要继续跟踪。",
    }
    validate_ui_api_response("getReportDetail", payload)


def test_validate_ui_api_response_accepts_safe_payload() -> None:
    payload = {
        "chatContext": {"chatContextId": "ctx-1", "kind": "normal_chat"},
        "messages": [{"messageId": "m1", "role": "assistant", "text": "你好"}],
    }
    validate_ui_api_response("sendChatMessage", payload)
