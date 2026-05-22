import type {
  AskReportQuestionInput,
  AskReportQuestionOutput,
  ChannelStatusForUser,
  ConfirmIntentDraftInput,
  ConfirmIntentDraftOutput,
  CreateIntentDraftInput,
  CreateIntentDraftOutput,
  DeleteSavedReportOutput,
  GetReportChartEvidenceOutput,
  ListDataSourcesOutput,
  ListSavedReportsOutput,
  LoadLlmSettingsOutput,
  PriceAlertForUser,
  PriceAlertInput,
  ReportDetailForUser,
  ReportQueueSnapshotForUser,
  RunPriceAlertNowOutput,
  RunScheduledReportNowOutput,
  SaveChannelConfigViaOpenClawInput,
  SaveChannelConfigViaOpenClawOutput,
  SaveDataSourceInstanceInput,
  SaveLlmConfigViaOpenClawInput,
  SaveLlmConfigViaOpenClawOutput,
  ScheduledReportForUser,
  ScheduledReportInput,
  SendChatMessageInput,
  SendChatMessageOutput,
  TestDataSourceInput,
  TestDataSourceOutput,
  TestLlmViaOpenClawInput,
  TestLlmViaOpenClawOutput,
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

export function getReportQueueSnapshot() {
  return requestJson<ReportQueueSnapshotForUser>('/api/ui/get-report-queue-snapshot');
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

export function getChannelStatus(options?: { includeQr?: boolean; refreshQr?: boolean; pollLogin?: boolean }) {
  const query = new URLSearchParams({ probe: 'true' });
  if (options?.includeQr) {
    query.set('includeQr', 'true');
  }
  if (options?.refreshQr) {
    query.set('refreshQr', 'true');
  }
  if (options?.pollLogin) {
    query.set('pollLogin', 'true');
  }
  return requestJson<ChannelStatusForUser>(`/api/ui/get-channel-status?${query.toString()}`);
}

export function loadLlmSettings() {
  return requestJson<LoadLlmSettingsOutput>('/api/ui/load-llm-settings');
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

export function testLlmViaOpenClaw(input: TestLlmViaOpenClawInput) {
  return requestJson<TestLlmViaOpenClawOutput>('/api/ui/test-llm-via-openclaw', {
    method: 'POST',
    body: JSON.stringify(input),
  });
}

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
