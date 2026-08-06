import type {
  AdvancedDiagnosticsEvidenceFailureReasonSummaryOutput,
  AskReportQuestionInput,
  AskReportQuestionOutput,
  AdvancedDiagnosticsLiveRunGapSummaryOutput,
  AdvancedDiagnosticsProviderHealthOutput,
  AdvancedDiagnosticsRuntimeServiceStatusOutput,
  ChannelChatSnapshotForUser,
  ChannelStatusForUser,
  CheckForUpdateInput,
  CheckForUpdateOutput,
  ClearChatSessionInput,
  ClearChatSessionOutput,
  CancelReportTaskInput,
  CancelReportTaskOutput,
  CancelSelectionProgressInput,
  CancelSelectionProgressOutput,
  ConfirmSelectionReportInput,
  ConfirmSelectionReportOutput,
  ConfirmIntentDraftInput,
  ConfirmIntentDraftOutput,
  CreateIntentDraftInput,
  CreateIntentDraftOutput,
  DeleteSavedReportOutput,
  DeleteSavedReportsOutput,
  GetReportChartEvidenceOutput,
  GetReportCleanupSettingsOutput,
  GetSelectionAutoRefreshSettingsOutput,
  LicenseStatusForUser,
  InstallUpdateInput,
  InstallUpdateOutput,
  FactoryResetInput,
  FactoryResetOutput,
  ListDataSourcesOutput,
  ListPriceAlertsOutput,
  ListScheduledReportsOutput,
  ListWorkerChatWorkersOutput,
  ListSavedReportsOutput,
  LoadLlmSettingsOutput,
  MaintenanceTaskDiagnosticsOutput,
  ProductionMaintenanceStatusOutput,
  PriceAlertForUser,
  PriceAlertInput,
  ReportDetailForUser,
  ReportModelStatusForUser,
  ReportQueueSnapshotForUser,
  ResetSettingsToDefaultsInput,
  ResetSettingsToDefaultsOutput,
  RetryRawDataMaintenanceInput,
  RetryRawDataMaintenanceOutput,
  RunPriceAlertNowOutput,
  RunScheduledReportNowOutput,
  SaveChannelConfigViaOpenClawInput,
  SaveChannelConfigViaOpenClawOutput,
  SaveReportCleanupSettingsInput,
  SaveReportCleanupSettingsOutput,
  SaveSelectionAutoRefreshSettingsInput,
  SaveSelectionAutoRefreshSettingsOutput,
  SaveEmbeddingConfigViaOpenVikingInput,
  SaveEmbeddingConfigViaOpenVikingOutput,
  SaveDataSourceInstanceInput,
  SaveLlmConfigViaOpenClawInput,
  SaveLlmConfigViaOpenClawOutput,
  ScheduledReportForUser,
  ScheduledReportInput,
  SelectionRefreshSnapshotForUser,
  SendReportFileViaChannelInput,
  SendReportFileViaChannelOutput,
  SendChatMessageInput,
  SendChatMessageOutput,
  SendWorkerChatInput,
  TestDataSourceInput,
  TestDataSourceOutput,
  TestEmbeddingViaOpenVikingInput,
  TestEmbeddingViaOpenVikingOutput,
  TestLlmViaOpenClawInput,
  TestLlmViaOpenClawOutput,
  WorkerChatReplyForUser,
  WechatNotificationBindingCodeForUser,
  WechatReconnectForUser,
} from './contracts';

type JsonRecord = Record<string, unknown>;
const GENERIC_SERVICE_ERROR = '服务暂时不可用，请稍后重试。';
const GENERIC_RESPONSE_ERROR = '服务返回格式异常，请稍后重试。';
const INTERNAL_MESSAGE_PATTERNS = [
  /unexpected token/i,
  /syntaxerror/i,
  /<!doctype html/i,
  /<html/i,
  /json/i,
  /internal/i,
  /openclaw/i,
  /openviking/i,
  /runid/i,
  /provider attempt/i,
];

export class ApiClientError extends Error {
  status?: number;
  constructor(message: string, status?: number) {
    super(message);
    this.name = 'ApiClientError';
    this.status = status;
  }
}

function isJsonResponse(response: Response) {
  const contentType = response.headers.get('content-type') ?? '';
  return contentType.toLowerCase().includes('application/json');
}

function sanitizeUserMessage(message: string) {
  const normalized = message.trim();
  if (!normalized) {
    return '';
  }
  if (INTERNAL_MESSAGE_PATTERNS.some((pattern) => pattern.test(normalized))) {
    return '';
  }
  return normalized;
}

async function readJsonPayload(response: Response): Promise<JsonRecord | null> {
  const text = await response.text();
  if (!text.trim()) {
    return null;
  }
  try {
    return JSON.parse(text) as JsonRecord;
  } catch {
    return null;
  }
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, {
    headers: {
      'Content-Type': 'application/json',
      ...(init?.headers ?? {}),
    },
    ...init,
  });

  if (!response.ok) {
    if (!isJsonResponse(response)) {
      throw new ApiClientError(GENERIC_SERVICE_ERROR, response.status);
    }
    const payload = await readJsonPayload(response);
    const rawMessage = typeof payload?.message === 'string' ? payload.message : '';
    const message = sanitizeUserMessage(rawMessage);
    throw new ApiClientError(message || GENERIC_SERVICE_ERROR, response.status);
  }

  if (!isJsonResponse(response)) {
    throw new ApiClientError(GENERIC_RESPONSE_ERROR, response.status);
  }

  const payload = await readJsonPayload(response);
  if (!payload) {
    throw new ApiClientError(GENERIC_RESPONSE_ERROR, response.status);
  }
  return payload as T;
}

export function sendChatMessage(input: SendChatMessageInput) {
  return requestJson<SendChatMessageOutput>('/api/ui/send-chat-message', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function clearChatSession(input: ClearChatSessionInput) {
  return requestJson<ClearChatSessionOutput>('/api/ui/clear-chat-session', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function listWorkerChatWorkers() {
  return requestJson<ListWorkerChatWorkersOutput>('/api/ui/list-worker-chat-workers');
}

export function sendWorkerChat(input: SendWorkerChatInput) {
  return requestJson<WorkerChatReplyForUser>('/api/ui/send-worker-chat', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function getReportQueueSnapshot() {
  return requestJson<ReportQueueSnapshotForUser>('/api/ui/get-report-queue-snapshot');
}

export function getLicenseStatus() {
  return requestJson<LicenseStatusForUser>('/api/ui/get-license-status');
}

export function cancelReportTask(input: CancelReportTaskInput) {
  return requestJson<CancelReportTaskOutput>('/api/ui/cancel-report-task', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function cancelSelectionProgress(input: CancelSelectionProgressInput) {
  return requestJson<CancelSelectionProgressOutput>('/api/ui/cancel-selection-progress', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function retryRawDataMaintenance(input: RetryRawDataMaintenanceInput) {
  return requestJson<RetryRawDataMaintenanceOutput>('/api/ui/retry-raw-data-maintenance', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function getChatSession(contextId: string) {
  return requestJson<SendChatMessageOutput>(`/api/ui/get-chat-session?contextId=${encodeURIComponent(contextId)}`);
}

export function getSelectionRefreshSnapshot() {
  return requestJson<SelectionRefreshSnapshotForUser>('/api/ui/get-selection-refresh-snapshot');
}

export function createIntentDraft(input: CreateIntentDraftInput) {
  return requestJson<CreateIntentDraftOutput>('/api/ui/create-intent-draft', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function confirmIntentDraft(input: ConfirmIntentDraftInput) {
  return requestJson<ConfirmIntentDraftOutput>('/api/ui/confirm-intent-draft', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function confirmSelectionReport(input: ConfirmSelectionReportInput) {
  return requestJson<ConfirmSelectionReportOutput>('/api/ui/confirm-selection-report', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function listSavedReports(query?: string) {
  const suffix = query ? `?query=${encodeURIComponent(query)}` : '';
  return requestJson<ListSavedReportsOutput>(`/api/ui/list-saved-reports${suffix}`);
}

export function getReportDetail(reportId: string) {
  return requestJson<ReportDetailForUser>(
    `/api/ui/get-report-detail?reportId=${encodeURIComponent(reportId)}`,
  );
}

export function deleteSavedReport(requestId: string, reportId: string) {
  return requestJson<DeleteSavedReportOutput>('/api/ui/delete-saved-report', {
    method: 'POST',
    body: JSON.stringify({ requestId, reportId }),
  });
}

export function deleteSavedReports(requestId: string, reportIds: string[]) {
  return requestJson<DeleteSavedReportsOutput>('/api/ui/delete-saved-reports', {
    method: 'POST',
    body: JSON.stringify({ requestId, reportIds }),
  });
}

export function sendReportFileViaChannel(input: SendReportFileViaChannelInput) {
  return requestJson<SendReportFileViaChannelOutput>('/api/ui/send-report-file-via-channel', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function askReportQuestion(input: AskReportQuestionInput) {
  return requestJson<AskReportQuestionOutput>('/api/ui/ask-report-question', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function getReportChartEvidence(reportId: string) {
  return requestJson<GetReportChartEvidenceOutput>(
    `/api/ui/get-report-chart-evidence?reportId=${encodeURIComponent(reportId)}`,
  );
}

export function getChannelStatus(options?: {
  includeQr?: boolean;
  refreshQr?: boolean;
  pollLogin?: boolean;
  probe?: boolean;
}) {
  const query = new URLSearchParams();
  if (options?.probe !== false) {
    query.set('probe', 'true');
  }
  if (options?.includeQr) {
    query.set('includeQr', 'true');
  }
  if (options?.refreshQr) {
    query.set('refreshQr', 'true');
  }
  if (options?.pollLogin) {
    query.set('pollLogin', 'true');
  }
  const suffix = query.toString();
  return requestJson<ChannelStatusForUser>(`/api/ui/get-channel-status${suffix ? `?${suffix}` : ''}`);
}

export function startWechatReconnect(requestId: string) {
  return requestJson<WechatReconnectForUser>('/api/ui/start-wechat-reconnect', {
    method: 'POST',
    body: JSON.stringify({ requestId, channelKind: 'wechat_clawbot' }),
  });
}

export function getWechatReconnectState() {
  return requestJson<WechatReconnectForUser | null>('/api/ui/get-wechat-reconnect-state');
}

export function pollWechatReconnect(operationId: string) {
  return requestJson<WechatReconnectForUser>('/api/ui/poll-wechat-reconnect', {
    method: 'POST',
    body: JSON.stringify({ operationId, timeoutMs: 1500 }),
  });
}

export function cancelWechatReconnect(operationId: string) {
  return requestJson<WechatReconnectForUser>('/api/ui/cancel-wechat-reconnect', {
    method: 'POST',
    body: JSON.stringify({ operationId, channelKind: 'wechat_clawbot' }),
  });
}

export function recoverWechatReconnect(operationId: string) {
  return requestJson<WechatReconnectForUser>('/api/ui/recover-wechat-reconnect', {
    method: 'POST',
    body: JSON.stringify({ operationId, channelKind: 'wechat_clawbot' }),
  });
}

export function createWechatNotificationBindingCode(requestId: string) {
  return requestJson<WechatNotificationBindingCodeForUser>(
    '/api/ui/create-wechat-notification-binding-code',
    { method: 'POST', body: JSON.stringify({ requestId }) },
  );
}

export function retryWechatNotifications(requestId: string) {
  return requestJson<{ attempted: number; sent: number; inProgress?: boolean }>('/api/ui/retry-wechat-notifications', {
    method: 'POST',
    body: JSON.stringify({ requestId }),
  });
}

export function getChannelChatSnapshot() {
  return requestJson<ChannelChatSnapshotForUser>('/api/ui/get-channel-chat-snapshot');
}

export function loadLlmSettings() {
  return requestJson<LoadLlmSettingsOutput>('/api/ui/load-llm-settings');
}

export function getReportModelStatus() {
  return requestJson<ReportModelStatusForUser>('/api/ui/get-report-model-status');
}

export function getReportCleanupSettings() {
  return requestJson<GetReportCleanupSettingsOutput>('/api/ui/get-report-cleanup-settings');
}

export function getSelectionAutoRefreshSettings() {
  return requestJson<GetSelectionAutoRefreshSettingsOutput>('/api/ui/get-selection-auto-refresh-settings');
}

export function saveReportCleanupSettings(input: SaveReportCleanupSettingsInput) {
  return requestJson<SaveReportCleanupSettingsOutput>('/api/ui/save-report-cleanup-settings', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function saveSelectionAutoRefreshSettings(input: SaveSelectionAutoRefreshSettingsInput) {
  return requestJson<SaveSelectionAutoRefreshSettingsOutput>('/api/ui/save-selection-auto-refresh-settings', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function getAdvancedDiagnosticsProviderHealth() {
  return requestJson<AdvancedDiagnosticsProviderHealthOutput>(
    '/api/ui/get-advanced-diagnostics-provider-health',
  );
}

export function getAdvancedDiagnosticsRuntimeServiceStatus() {
  return requestJson<AdvancedDiagnosticsRuntimeServiceStatusOutput>(
    '/api/ui/get-advanced-diagnostics-runtime-service-status',
  );
}

export function getAdvancedDiagnosticsLiveRunGapSummary() {
  return requestJson<AdvancedDiagnosticsLiveRunGapSummaryOutput>(
    '/api/ui/get-advanced-diagnostics-live-run-gap-summary',
  );
}

export function getAdvancedDiagnosticsEvidenceFailureReasonSummary() {
  return requestJson<AdvancedDiagnosticsEvidenceFailureReasonSummaryOutput>(
    '/api/ui/get-advanced-diagnostics-evidence-failure-reason-summary',
  );
}

export function listDataSources() {
  return requestJson<ListDataSourcesOutput>('/api/ui/list-data-sources');
}

export function saveChannelConfigViaOpenClaw(input: SaveChannelConfigViaOpenClawInput) {
  return requestJson<SaveChannelConfigViaOpenClawOutput>('/api/ui/save-channel-config-via-openclaw', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function saveLlmConfigViaOpenClaw(input: SaveLlmConfigViaOpenClawInput) {
  return requestJson<SaveLlmConfigViaOpenClawOutput>('/api/ui/save-llm-config-via-openclaw', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function saveEmbeddingConfigViaOpenViking(input: SaveEmbeddingConfigViaOpenVikingInput) {
  return requestJson<SaveEmbeddingConfigViaOpenVikingOutput>('/api/ui/save-embedding-config-via-openviking', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function testLlmViaOpenClaw(input: TestLlmViaOpenClawInput) {
  return requestJson<TestLlmViaOpenClawOutput>('/api/ui/test-llm-via-openclaw', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function testEmbeddingViaOpenViking(input: TestEmbeddingViaOpenVikingInput) {
  return requestJson<TestEmbeddingViaOpenVikingOutput>('/api/ui/test-embedding-via-openviking', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function resetSettingsToDefaults(input: ResetSettingsToDefaultsInput) {
  return requestJson<ResetSettingsToDefaultsOutput>('/api/ui/reset-settings-to-defaults', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function getProductionMaintenanceStatus() {
  return requestJson<ProductionMaintenanceStatusOutput>('/api/ui/get-production-maintenance-status');
}

export function checkForUpdate(input: CheckForUpdateInput) {
  return requestJson<CheckForUpdateOutput>('/api/ui/check-for-update', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function installUpdate(input: InstallUpdateInput) {
  return requestJson<InstallUpdateOutput>('/api/ui/install-update', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function factoryReset(input: FactoryResetInput) {
  return requestJson<FactoryResetOutput>('/api/ui/factory-reset', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export const saveChannelConfig = saveChannelConfigViaOpenClaw;
export const saveReportModelConfig = saveLlmConfigViaOpenClaw;
export const saveEmbeddingConfig = saveEmbeddingConfigViaOpenViking;
export const testReportModelConnection = testLlmViaOpenClaw;
export const testEmbeddingConnection = testEmbeddingViaOpenViking;

export function testDataSource(input: TestDataSourceInput) {
  return requestJson<TestDataSourceOutput>('/api/ui/test-data-source', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function saveDataSourceInstance(input: SaveDataSourceInstanceInput) {
  return requestJson<ListDataSourcesOutput['instances'][number]>('/api/ui/save-data-source-instance', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function createScheduledReport(input: ScheduledReportInput) {
  return requestJson<ScheduledReportForUser>('/api/ui/create-scheduled-report', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function listScheduledReports() {
  return requestJson<ListScheduledReportsOutput>('/api/ui/list-scheduled-reports');
}

export function pauseScheduledReport(requestId: string, scheduledReportId: string) {
  return requestJson<ScheduledReportForUser>('/api/ui/pause-scheduled-report', {
    method: 'POST',
    body: JSON.stringify({ requestId, scheduledReportId }),
  });
}

export function resumeScheduledReport(requestId: string, scheduledReportId: string) {
  return requestJson<ScheduledReportForUser>('/api/ui/resume-scheduled-report', {
    method: 'POST',
    body: JSON.stringify({ requestId, scheduledReportId }),
  });
}

export function deleteScheduledReport(requestId: string, scheduledReportId: string) {
  return requestJson<{ deleted: true; scheduledReportId: string }>('/api/ui/delete-scheduled-report', {
    method: 'POST',
    body: JSON.stringify({ requestId, scheduledReportId }),
  });
}

export function runScheduledReportNow(requestId: string, scheduledReportId: string) {
  return requestJson<RunScheduledReportNowOutput>('/api/ui/run-scheduled-report-now', {
    method: 'POST',
    body: JSON.stringify({ requestId, scheduledReportId }),
  });
}

export function createPriceAlert(input: PriceAlertInput) {
  return requestJson<PriceAlertForUser>('/api/ui/create-price-alert', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

export function listPriceAlerts() {
  return requestJson<ListPriceAlertsOutput>('/api/ui/list-price-alerts');
}

export function pausePriceAlert(requestId: string, priceAlertId: string) {
  return requestJson<PriceAlertForUser>('/api/ui/pause-price-alert', {
    method: 'POST',
    body: JSON.stringify({ requestId, priceAlertId }),
  });
}

export function resumePriceAlert(requestId: string, priceAlertId: string) {
  return requestJson<PriceAlertForUser>('/api/ui/resume-price-alert', {
    method: 'POST',
    body: JSON.stringify({ requestId, priceAlertId }),
  });
}

export function deletePriceAlert(requestId: string, priceAlertId: string) {
  return requestJson<{ deleted: true; priceAlertId: string }>('/api/ui/delete-price-alert', {
    method: 'POST',
    body: JSON.stringify({ requestId, priceAlertId }),
  });
}

export function runPriceAlertNow(requestId: string, priceAlertId: string) {
  return requestJson<RunPriceAlertNowOutput>('/api/ui/run-price-alert-now', {
    method: 'POST',
    body: JSON.stringify({ requestId, priceAlertId }),
  });
}

export function getMaintenanceTaskDiagnostics() {
  return requestJson<MaintenanceTaskDiagnosticsOutput>('/api/ui/get-maintenance-task-diagnostics');
}
