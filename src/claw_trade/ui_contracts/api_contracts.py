from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class UiApiContract:
    name: str
    response_shape: str
    forbidden_response_fields: tuple[str, ...]


UI_API_CONTRACTS: tuple[UiApiContract, ...] = (
    UiApiContract("sendChatMessage", "ChatContextForUser + ChatMessageForUser[]", ("lockedWorkflowRunId", "workflowRunning")),
    UiApiContract("createIntentDraft", "IntentDraftForUser + ConfirmationCard", ("sourceMessageId", "workflowSettings", "dedupeKey")),
    UiApiContract("confirmIntentDraft", "ReportTaskForUser | ScheduledReportForUser | PriceAlertForUser", ("runId", "dedupeKey")),
    UiApiContract("enqueueReportTask", "ReportTaskForUser + ReportQueueSnapshotForUser", ("runId", "profile", "priority", "dedupeKey")),
    UiApiContract("cancelReportTask", "ReportTaskForUser + ReportQueueSnapshotForUser", ("runId", "lockedWorkflowRunId")),
    UiApiContract("getReportQueueSnapshot", "ReportQueueSnapshotForUser", ("failedTasks",)),
    UiApiContract("getSelectionRefreshSnapshot", "{ selectionProgress: SelectionProgressForUser | null }", ("providerAttempt", "rawPayload", "path", "hash")),
    UiApiContract("listSavedReports", "{ items: SavedReportForUser[], nextCursor? }", ("runId", "artifact", "path", "hash", "receipt")),
    UiApiContract("getReportDetail", "ReportDetailForUser", ("ReportArtifact", "path", "hash", "receipt")),
    UiApiContract(
        "deleteSavedReport",
        "{ deleted: boolean, reportId, userMessage, cleanup: { deletedRunIds, skippedRunIds, failedRunIds, deletedBytesApprox, warnings } }",
        ("runId", "artifact", "path", "hash", "receipt"),
    ),
    UiApiContract("askReportQuestion", "{ text: string }", ("runId", "lockedWorkflowRunId")),
    UiApiContract("createScheduledReport", "ScheduledReportForUser", ("lastRunTaskId",)),
    UiApiContract("pauseScheduledReport", "ScheduledReportForUser", ("runId",)),
    UiApiContract("resumeScheduledReport", "ScheduledReportForUser", ("runId",)),
    UiApiContract("deleteScheduledReport", "{ deleted: true, scheduledReportId }", ("runId",)),
    UiApiContract("runScheduledReportNow", "{ task: ReportTaskForUser, queueSnapshot: ReportQueueSnapshotForUser }", ("runId",)),
    UiApiContract("createPriceAlert", "PriceAlertForUser", ("runId",)),
    UiApiContract("pausePriceAlert", "PriceAlertForUser", ("runId",)),
    UiApiContract("resumePriceAlert", "PriceAlertForUser", ("runId",)),
    UiApiContract("deletePriceAlert", "{ deleted: true, priceAlertId }", ("runId",)),
    UiApiContract("runPriceAlertNow", "{ alert: PriceAlertForUser, triggered: boolean, message?: string }", ("runId",)),
    UiApiContract("getReportChartEvidence", "{ reportId, items: ChartEvidenceForUser[], summary }", ("artifact", "path", "hash", "receipt")),
    UiApiContract("listDataSources", "{ supportedTypes, instances: DataSourceInstanceForUser[] }", ("credentialRef", "path", "hash", "receipt")),
    UiApiContract("testDataSource", "{ state, healthEvent: DataSourceHealthEventForUser, canEnable }", ("receipt", "manifest", "rawPayload")),
    UiApiContract("saveDataSourceInstance", "DataSourceInstanceForUser", ("credentialRef", "realKey")),
    UiApiContract("getChannelStatus", "ChannelStatusForUser", ("providerChannelId", "path", "hash")),
    UiApiContract("getChannelChatSnapshot", "{ channelKind, context?, messages, confirmationCards }", ("providerChannelId", "path", "hash")),
    UiApiContract("saveChannelConfigViaOpenClaw", "{ status: ChannelStatusForUser, restartRequired?: boolean }", ("path", "hash")),
    UiApiContract("loadLlmSettings", "LlmConfigDraft(masked)", ("realKey", "path", "hash", "OpenClaw")),
    UiApiContract("saveLlmConfigViaOpenClaw", "{ ok: boolean, userMessage: string }", ("realKey", "rawPatchResult")),
    UiApiContract("testLlmViaOpenClaw", "{ ok, userMessage, checkedAt }", ("providerAttempt", "runtimeMarker")),
    UiApiContract("resetSettingsToDefaults", "{ status: reset, userMessage, llm, dataSources, channel }", ("realKey", "rawPatchResult", "providerChannelId")),
    UiApiContract("sendReportFileViaChannel", "{ sent: boolean, messageId?, userMessage }", ("localPath", "providerChannelId")),
    UiApiContract("exportReportPdf", "PdfExportForUser", ("artifact", "path", "hash")),
)

_CONTRACT_MAP = {contract.name: contract for contract in UI_API_CONTRACTS}


def get_ui_api_contract(name: str) -> UiApiContract:
    contract = _CONTRACT_MAP.get(name)
    if contract is None:
        raise KeyError(f"未知普通 UI API: {name}")
    return contract


def validate_ui_api_response(api_name: str, payload: Mapping[str, Any]) -> None:
    contract = get_ui_api_contract(api_name)
    forbidden_hits = _find_forbidden_fields(payload, contract.forbidden_response_fields)
    if forbidden_hits:
        hit_text = ", ".join(sorted(forbidden_hits))
        raise ValueError(f"{api_name} 响应包含禁止字段: {hit_text}")


def _find_forbidden_fields(payload: Any, fields: Sequence[str]) -> set[str]:
    target = {item.lower() for item in fields}
    hits: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, value in node.items():
                key_text = str(key)
                if key_text.lower() in target:
                    hits.add(key_text)
                walk(value)
            return
        if isinstance(node, Sequence) and not isinstance(node, (str, bytes, bytearray)):
            for item in node:
                walk(item)

    walk(payload)
    return hits
