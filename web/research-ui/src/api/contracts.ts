export type MarketProfile = 'CN_A' | 'US' | 'HK' | 'CRYPTO';
export type ChatContextKind =
  | 'normal_chat'
  | 'task_following'
  | 'report_reading'
  | 'intent_confirming';
export type IntentKind = 'report' | 'scheduled_report' | 'price_alert';
export type ReportTaskStatus =
  | 'draft'
  | 'confirmed'
  | 'queued'
  | 'running'
  | 'saving_report'
  | 'pdf_exporting'
  | 'succeeded'
  | 'failed'
  | 'cancelled';
export type UserVisibleSeverity = 'info' | 'success' | 'warning' | 'error';
export type ReportRetentionDays = 7 | 14 | 30;

export type ChatMessageKind =
  | 'plain'
  | 'confirmation_card'
  | 'task_progress'
  | 'report_completed'
  | 'report_failed'
  | 'selection_result'
  | 'selection_refreshing'
  | 'selection_failed'
  | 'selection_unavailable'
  | 'price_alert'
  | 'file_send_failed';

export interface UserFacingFailure {
  code:
    | 'INVALID_INPUT'
    | 'CONFIRMATION_REQUIRED'
    | 'DRAFT_EXPIRED'
    | 'QUEUE_FULL'
    | 'DUPLICATE_TASK'
    | 'TASK_NOT_FOUND'
    | 'TASK_NOT_CANCELLABLE'
    | 'REPORT_NOT_FOUND'
    | 'REPORT_NOT_READY'
    | 'REPORT_CONTEXT_TOO_LONG'
    | 'NOTIFICATION_UNAVAILABLE'
    | 'FILE_SEND_UNSUPPORTED'
    | 'ASSISTANT_UNAVAILABLE'
    | 'REPORT_MODEL_NOT_READY'
    | 'MAINTENANCE_LOCKED'
    | 'REPORT_EXPORT_FAILED'
    | 'DATASOURCE_TEST_FAILED'
    | 'PDF_EXPORT_FAILED'
    | 'LICENSE_BLOCKED'
    | 'PROFILE_STRATEGY_UNAPPROVED'
    | 'SCHEDULE_NOT_FOUND'
    | 'ALERT_NOT_FOUND'
    | 'UNAUTHORIZED'
    | 'CONFLICT';
  message: string;
  severity: UserVisibleSeverity;
}

export interface ChatMessageForUser {
  messageId: string;
  contextKind: ChatContextKind;
  actor: 'user' | 'assistant' | 'system';
  kind: ChatMessageKind;
  text: string;
  cardId?: string | null;
  reportId?: string | null;
  taskId?: string | null;
  selection?: SelectionMessageMetadata | null;
  createdAt: string;
}

export interface SelectionMessageMetadata {
  code: 'completed' | 'unavailable' | 'failed' | string;
  workflowRunId?: string | null;
  evidencePath?: string | null;
  unavailableCode?: string | null;
  failureReason?: string | null;
  readerReportMarkdown?: string | null;
  readerReportPath?: string | null;
  dataRefresh?: {
    status: string;
    selectionRunId?: string | null;
    tradeDate?: string | null;
    reason?: string | null;
    errorCode?: string | null;
  } | null;
}

export interface SelectionReportForUser {
  id: string;
  title: string;
  generatedAt: string;
  summarySnippet: string;
  markdown: string;
}

export interface SelectionProgressForUser {
  kind?: 'selection_workflow' | 'data_refresh';
  status: 'running' | 'completed' | 'failed';
  statusLabel: string;
  command: string;
  stageLabel: string;
  currentAction: string;
  percent: number;
  workerStatusLabels: string[];
  completedRoleLabels?: string[];
  waitingRoleLabels?: string[];
  startedAt: string;
  finishedAt?: string | null;
  workflowRunId?: string | null;
}

export interface SelectionRefreshSnapshotForUser {
  selectionProgress?: SelectionProgressForUser | null;
}

export interface LicenseStatusForUser {
  status: string;
  allowsReportGeneration: boolean;
  allowsDataRefresh: boolean;
  expiresAt?: string | null;
  graceUntil?: string | null;
  deviceIdHash?: string | null;
  licenseSuffix?: string | null;
  message: string;
}

export interface ConfirmationCard {
  id: string;
  draftId: string;
  title: string;
  summaryLines: string[];
  instrumentCode?: string;
  instrumentName?: string;
  market?: MarketProfile;
  validationState?: 'matched' | 'mismatch';
  validationMessage?: string | null;
  suggestedMarket?: MarketProfile | null;
  dataSourceSummary: 'ready' | 'partial' | 'unknown';
  actions: Array<'confirm' | 'cancel'>;
  status: 'active' | 'confirmed' | 'cancelled' | 'expired';
  createdAt: string;
}

export interface ChatContextForUser {
  contextId: string;
  kind: ChatContextKind;
  title: string;
  activeTaskId?: string | null;
  activeReportId?: string | null;
}

export interface ReportProgressUiState {
  percent: number;
  stageLabel: string;
  roleLabel?: string | null;
  currentAction: string;
  completedRoleLabels: string[];
  waitingRoleLabels: string[];
  workerStatusLabels?: string[];
}

export interface ReportTaskForUser {
  taskId: string;
  source: 'manual' | 'scheduled';
  status: ReportTaskStatus;
  statusLabel: string;
  instrumentCode: string;
  instrumentName?: string | null;
  market: MarketProfile;
  companyName: string;
  currencySymbol: string;
  startDate: string;
  endDate: string;
  currentDate: string;
  queuePosition?: number | null;
  progress?: ReportProgressUiState | null;
  reportId?: string | null;
  failure?: UserFacingFailure | null;
  createdAt: string;
  startedAt?: string | null;
  finishedAt?: string | null;
}

export interface ReportQueueSnapshotForUser {
  runningTask?: ReportTaskForUser | null;
  queuedTasks: ReportTaskForUser[];
  lastTerminalTask?: ReportTaskForUser | null;
  queueLimit: number;
  queuedCount: number;
  isFull: boolean;
}

export interface ReportAssetForUser {
  kind: 'markdown' | 'pdf';
  available: boolean;
  status: 'ready' | 'failed' | 'not_requested';
  userMessage?: string | null;
  updatedAt?: string | null;
}

export interface SavedReportForUser {
  id: string;
  instrumentCode: string;
  instrumentName?: string | null;
  market: MarketProfile;
  title: string;
  generatedAt: string;
  summarySnippet: string;
  originContextId?: string | null;
  canForwardToChannel?: boolean;
}

export interface DataSourceHealthEventForUser {
  displayName: string;
  status:
    | 'auth_missing'
    | 'auth_invalid'
    | 'unreachable'
    | 'rate_limited'
    | 'schema_invalid'
    | 'empty';
  userMessage: string;
  impact: string;
  occurredAt: string;
}

export interface ReportCompletionSummary {
  id: string;
  reportId: string;
  instrumentCode: string;
  generatedAt: string;
  finalConclusion: string;
  coreReasons: string[];
  mainRisks: string[];
  failedConfiguredDataSources: DataSourceHealthEventForUser[];
  fullReportAvailable: boolean;
  pdfAvailable: boolean;
  createdAt: string;
}

export interface ChartEvidenceForUser {
  id: string;
  reportId: string;
  chartType: 'kline' | 'returns_curve' | 'drawdown' | 'volume' | 'macd' | 'rsi' | 'other';
  title: string;
  status: 'ready' | 'missing' | 'failed';
  userMessage: string;
  capturedAt: string;
}

export interface ReportDetailForUser {
  report: SavedReportForUser;
  markdown: string;
  completionSummary: ReportCompletionSummary;
  dataSourceEvents: DataSourceHealthEventForUser[];
  chartEvidence: { summary: 'ready' | 'partial' | 'missing'; items: ChartEvidenceForUser[] };
  assets: ReportAssetForUser[];
}

export interface ScheduledReportForUser {
  scheduledReportId: string;
  instrumentCode: string;
  instrumentName?: string | null;
  market: MarketProfile;
  frequency: 'daily' | 'weekly';
  timeOfDay: string;
  weekday?: number | null;
  notification: { channel: 'wechat_clawbot' | 'in_app'; enabled: boolean };
  state: 'draft' | 'active' | 'due' | 'enqueued' | 'paused' | 'deleted';
  nextRunAt?: string | null;
  lastRunTaskId?: string | null;
  cronJobId?: string | null;
  lastCronRunId?: string | null;
  syncErrorMessage?: string | null;
  updatedAt?: string | null;
}

export interface PriceAlertForUser {
  priceAlertId: string;
  instrumentCode: string;
  instrumentName?: string | null;
  market: MarketProfile;
  condition: {
    type: 'price_threshold' | 'percent_change';
    operator: 'above' | 'below' | 'up_by' | 'down_by';
    value: number;
    window?: '24h' | 'intraday' | null;
  };
  notification: { channel: 'wechat_clawbot' | 'in_app'; enabled: boolean };
  state: 'draft' | 'active' | 'checking' | 'triggered' | 'error' | 'paused' | 'closed' | 'deleted';
  lastCheckedAt?: string | null;
  triggeredAt?: string | null;
  lastErrorMessage?: string | null;
  scanBucket?: string | null;
  lastQuote?: {
    currentPrice?: number | string | null;
    percentChange?: number | string | null;
    percentChange24h?: number | string | null;
    percentChangeIntraday?: number | string | null;
    quoteTimestamp?: string | null;
    evidenceRef?: string | null;
  } | null;
  lastQuoteEvidenceRef?: string | null;
  lastScanRunId?: string | null;
  notificationDedupeKey?: string | null;
  lastNotificationResult?: Record<string, unknown> | null;
  updatedAt?: string | null;
}

export interface ListScheduledReportsOutput {
  items: ScheduledReportForUser[];
}

export interface ListPriceAlertsOutput {
  items: PriceAlertForUser[];
}

export interface MaintenanceTaskDiagnosticsOutput {
  checkedAt: string;
  selectionRefresh: Array<{
    kind: 'selection_data_refresh';
    market: MarketProfile;
    status: string;
    runId?: string | null;
    lastResult?: string | null;
    failureReason?: string | null;
    startedAt?: string | null;
    finishedAt?: string | null;
  }>;
  scheduledReportWakes?: Array<Record<string, unknown>>;
  priceAlertScanBuckets: Array<{
    bucketKey: string;
    market: MarketProfile;
    frequency: string;
    enabled: boolean;
    cronJobId?: string | null;
    lastScanRunId?: string | null;
    lastScanSummary?: Record<string, unknown> | null;
    lastErrorMessage?: string | null;
    skippedReason?: string | null;
    updatedAt?: string | null;
  }>;
  dataMaintenance: Array<Record<string, unknown>>;
  reportCleanup: Record<string, unknown>;
}

export interface DataSourceInstanceForUser {
  instanceId: string;
  supportedType: string;
  group: string;
  displayName: string;
  enabled: boolean;
  apiKeyMasked?: string | null;
  endpointUrl?: string | null;
  state: 'draft' | 'testing' | 'validated' | 'enabled' | 'disabled' | 'degraded' | 'rejected';
  lastSuccessAt?: string | null;
  lastTestAt?: string | null;
  rateLimitMaxCalls?: number | null;
  rateLimitWindowSeconds?: number | null;
  rateLimitSafetyMargin?: number | null;
  rateLimitOverflow?: 'wait' | 'fail_fast' | null;
  rateLimitWaitTimeoutSeconds?: number | null;
}

export interface DataSourceInstanceDraftInput {
  instanceId?: string | null;
  supportedType: string;
  group: string;
  displayName: string;
  enabled: boolean;
  apiKeyReplacement?: string | null;
  endpointUrl?: string | null;
  state?: DataSourceInstanceForUser['state'];
  requiresKey?: boolean;
  rateLimitMaxCalls?: number | string | null;
  rateLimitWindowSeconds?: number | string | null;
  rateLimitSafetyMargin?: number | string | null;
  rateLimitOverflow?: 'wait' | 'fail_fast' | '' | null;
  rateLimitWaitTimeoutSeconds?: number | string | null;
}

export interface ChannelStatusForUser {
  channelKind: 'wechat_clawbot';
  onboardingState: 'onboarding' | 'skipped' | 'completed';
  state: 'unknown' | 'disconnected' | 'connecting' | 'connected' | 'error' | 'reconnecting';
  displayName: string;
  accountLabel?: string | null;
  lastConnectedAt?: string | null;
  lastErrorMessage?: string | null;
  canSendText: boolean;
  canSendFile: boolean;
  qrCodeImageDataUrl?: string | null;
  qrCodeExpiresAt?: string | null;
  qrCodeRefreshRequired?: boolean;
  replacementRequired?: boolean;
}

export interface WechatReconnectForUser {
  operationId: string;
  phase: 'preparing' | 'awaiting_scan' | 'committing' | 'restoring' | 'completed' | 'needs_attention';
  outcome?: 'switched' | 'restored' | null;
  lastErrorCode?: string | null;
  lastErrorMessage?: string | null;
  updatedAt?: string | null;
  qrCodeImageDataUrl?: string | null;
}

export interface WechatNotificationBindingCodeForUser {
  code: string;
  expiresAt: string;
  message: string;
}

export interface LlmConfigDraft {
  provider:
    | 'deepseek'
    | 'qwen'
    | 'glm'
    | 'kimi'
    | 'minimax'
    | 'doubao'
    | 'ernie'
    | 'hunyuan'
    | 'openai_compatible';
  apiKeyReplacement?: string | null;
  apiKeyMasked?: string | null;
  endpointUrl?: string | null;
  defaultModel: string;
  status: 'idle' | 'saving' | 'testing' | 'saved' | 'error';
  lastTestMessage?: string | null;
  updatedAt?: string | null;
  reportModelStatus?: ReportModelStatusForUser | null;
  embedding?: EmbeddingLlmConfigDraft | null;
}

export interface ReportModelStatusForUser {
  state: 'unconfigured' | 'saved_unverified' | 'failed' | 'ready';
  blocked: boolean;
  ready: boolean;
  userMessage: string;
  checkedAt?: string | null;
}

export interface EmbeddingLlmConfigDraft {
  provider: string;
  model: string;
  apiKeyReplacement?: string | null;
  apiKeyMasked?: string | null;
  endpointUrl?: string | null;
  dimension?: string | null;
  enabled: boolean;
}

export interface PdfExportForUser {
  reportId: string;
  state: 'not_requested' | 'exporting' | 'ready' | 'failed';
  available: boolean;
  userMessage?: string | null;
  updatedAt?: string | null;
}

export interface SendReportFileViaChannelInput {
  requestId: string;
  reportId: string;
  channelKind?: 'wechat_clawbot';
  originContextId?: string | null;
}

export interface SendReportFileViaChannelOutput {
  sent: true;
  messageId?: string | null;
  userMessage: string;
}

export interface SendChatMessageInput {
  requestId: string;
  contextId: string;
  text: string;
}

export interface SendChatMessageOutput {
  context: ChatContextForUser;
  messages: ChatMessageForUser[];
  confirmationCard?: ConfirmationCard;
  confirmationCards?: Record<string, ConfirmationCard>;
  queueSnapshot?: ReportQueueSnapshotForUser;
  assistantReply?: string;
  selection?: SelectionMessageMetadata;
}

export interface ClearChatSessionInput {
  requestId: string;
  contextId: string;
}

export type ClearChatSessionOutput = SendChatMessageOutput;

export interface ConfirmSelectionReportInput {
  requestId: string;
  selectWorkflowRunId: string;
  ticker: string;
  originContextId?: string | null;
}

export interface ConfirmSelectionReportOutput {
  code: string;
  reportTaskId?: string | null;
  reportRunId?: string | null;
  reportHandoffDedupeKey: string;
  queuePayload: Record<string, unknown>;
  deduped: boolean;
  task?: ReportTaskForUser | null;
  queueSnapshot?: ReportQueueSnapshotForUser;
}

export interface CancelReportTaskInput {
  requestId: string;
  taskId: string;
}

export interface CancelReportTaskOutput {
  task: ReportTaskForUser;
  queueSnapshot: ReportQueueSnapshotForUser;
  message: string;
}

export interface CancelSelectionProgressInput {
  requestId: string;
  workflowRunId: string;
}

export interface CancelSelectionProgressOutput {
  cancelled: boolean;
  selectionProgress: null;
  message: string;
}

export interface RetryRawDataMaintenanceInput {
  requestId: string;
  market: 'CN_A' | 'CRYPTO';
}

export interface RetryRawDataMaintenanceOutput {
  status: 'started';
  market: 'CN_A' | 'CRYPTO';
  cronRunId: string;
}

export interface ChannelChatSnapshotForUser {
  channelKind: 'wechat_clawbot';
  context?: ChatContextForUser | null;
  messages?: ChatMessageForUser[];
  confirmationCards?: Record<string, ConfirmationCard>;
}

export interface CreateIntentDraftInput {
  requestId: string;
  sourceMessageId?: string;
  text: string;
}

export interface IntentDraftForUser {
  draftId: string;
  kind: IntentKind;
  instrumentCode: string;
  instrumentName?: string | null;
  market: MarketProfile;
  reportDateRange?: { startDate: string; endDate: string } | null;
  schedule?: { frequency: 'daily' | 'weekly'; timeOfDay: string; weekday?: number | null } | null;
  priceCondition?: {
    type: 'price_threshold' | 'percent_change';
    operator: 'above' | 'below' | 'up_by' | 'down_by';
    value: number;
    window?: '24h' | 'intraday' | null;
  } | null;
  notification: { channel: 'wechat_clawbot' | 'in_app'; enabled: boolean };
  status: 'draft' | 'confirmed' | 'cancelled' | 'expired';
}

export interface CreateIntentDraftOutput {
  draft: IntentDraftForUser;
  confirmationCard: ConfirmationCard;
}

export interface ConfirmIntentDraftInput {
  requestId: string;
  draftId: string;
  decision: 'confirm' | 'cancel';
  overrides?: Record<string, unknown>;
  contextId?: string | null;
}

export interface ConfirmIntentDraftOutput {
  status: 'confirmed' | 'cancelled';
  context?: ChatContextForUser;
  task?: ReportTaskForUser;
  scheduledReport?: ScheduledReportForUser;
  priceAlert?: PriceAlertForUser;
  messages?: ChatMessageForUser[];
  queueSnapshot?: ReportQueueSnapshotForUser;
}

export interface AskReportQuestionInput {
  requestId: string;
  reportId: string;
  text: string;
}

export interface AskReportQuestionOutput {
  text: string;
}

export type WorkerChatMode = 'generic_worker_chat' | 'report_worker_chat';

export interface WorkerChatWorkerForUser {
  workerId: string;
  displayName: string;
  default: boolean;
  aliases: string[];
}

export interface ListWorkerChatWorkersOutput {
  workers: WorkerChatWorkerForUser[];
}

export interface SendWorkerChatInput {
  requestId: string;
  mode: WorkerChatMode;
  workerId: string;
  text: string;
  conversationId: string;
  reportId?: string | null;
}

export interface WorkerChatReplyForUser {
  kind: 'worker_chat_reply';
  workerDisplayName: string;
  text: string;
  mode: WorkerChatMode;
}

export interface ListSavedReportsOutput {
  items: SavedReportForUser[];
  nextCursor?: string;
}

export interface DeleteSavedReportOutput {
  deleted: boolean;
  reportId: string;
  userMessage: string;
  cleanup: {
    deletedRunIds: string[];
    skippedRunIds: string[];
    failedRunIds: string[];
    deletedBytesApprox: number;
    warnings: string[];
  };
}

export interface DeleteSavedReportsOutput {
  deletedRunIds: string[];
  skippedRunIds: string[];
  failedRunIds: string[];
  deletedBytesApprox: number;
  warnings: string[];
  userMessage: string;
  runs: {
    reportId: string;
    status: string;
    deletedBytesApprox: number;
    warnings: string[];
    userMessage: string;
  }[];
}

export interface ReportCleanupSettingsForUser {
  reportRetentionDays: ReportRetentionDays;
}

export interface SelectionAutoRefreshSettingsForUser {
  enabled: boolean;
}

export interface GetReportCleanupSettingsOutput {
  reportCleanup: ReportCleanupSettingsForUser;
}

export interface GetSelectionAutoRefreshSettingsOutput {
  selectionAutoRefresh: SelectionAutoRefreshSettingsForUser;
}

export interface SaveReportCleanupSettingsInput {
  requestId: string;
  reportRetentionDays: ReportRetentionDays;
}

export interface SaveSelectionAutoRefreshSettingsInput {
  requestId: string;
  enabled: boolean;
}

export interface SaveReportCleanupSettingsOutput {
  reportCleanup: ReportCleanupSettingsForUser;
}

export interface SaveSelectionAutoRefreshSettingsOutput {
  selectionAutoRefresh: SelectionAutoRefreshSettingsForUser;
}

export interface GetReportChartEvidenceOutput {
  reportId: string;
  items: ChartEvidenceForUser[];
  summary: 'ready' | 'partial' | 'missing';
}

export interface ListDataSourcesOutput {
  supportedTypes: DataSourceInstanceForUser['supportedType'][];
  instances: DataSourceInstanceForUser[];
}

export interface LoadLlmSettingsOutput {
  draft: LlmConfigDraft;
  schemaVersion: string;
  settingsVersion: string;
}

export interface SaveChannelConfigViaOpenClawInput {
  requestId: string;
  channelKind: 'wechat_clawbot';
  configPatch: Record<string, unknown>;
  expectedSettingsVersion?: string;
}

export interface SaveChannelConfigViaOpenClawOutput {
  status: ChannelStatusForUser;
  restartRequired?: boolean;
}

export interface SaveLlmConfigViaOpenClawInput {
  requestId: string;
  draft: Partial<LlmConfigDraft> & { provider: LlmConfigDraft['provider']; defaultModel: string };
  expectedSettingsVersion: string;
}

export interface SaveLlmConfigViaOpenClawOutput {
  status: 'saved';
  updatedAt: string;
  settingsVersion?: string;
  reportModelStatus?: ReportModelStatusForUser | null;
}

export interface SaveEmbeddingConfigViaOpenVikingInput {
  requestId: string;
  embedding: EmbeddingLlmConfigDraft;
}

export interface SaveEmbeddingConfigViaOpenVikingOutput {
  status: 'saved';
  updatedAt: string;
}

export interface TestLlmViaOpenClawInput {
  requestId: string;
  provider?: LlmConfigDraft['provider'];
  model?: string;
  endpointUrl?: string | null;
  apiKeyReplacement?: string | null;
}

export interface TestLlmViaOpenClawOutput {
  ok: boolean;
  userMessage: string;
  checkedAt: string;
}

export interface TestEmbeddingViaOpenVikingInput {
  requestId: string;
  embedding: EmbeddingLlmConfigDraft;
}

export interface TestEmbeddingViaOpenVikingOutput {
  ok: boolean;
  userMessage: string;
  checkedAt: string;
  error?: {
    code: string;
    action: string;
    retryable: boolean;
  } | null;
}

export interface ResetSettingsToDefaultsInput {
  requestId: string;
}

export interface ResetSettingsToDefaultsOutput {
  status: 'reset';
  userMessage: string;
  llm: {
    status: 'reset';
    updatedAt: string;
    settingsVersion?: string;
  };
  dataSources: ListDataSourcesOutput & {
    status: 'reset';
    updatedAt: string;
  };
  channel: ChannelStatusForUser;
  reportCleanup: ReportCleanupSettingsForUser;
  selectionAutoRefresh: SelectionAutoRefreshSettingsForUser;
}

export interface FactoryResetInput {
  requestId: string;
  confirmation: 'RESET_CLAW_TRADE';
}

export interface CheckForUpdateInput {
  requestId: string;
}

export type UpdateActionStatus =
  | 'idle'
  | 'not_configured'
  | 'checking_manifest'
  | 'check_failed'
  | 'update_available'
  | 'up_to_date'
  | 'downloading'
  | 'verify_failed'
  | 'install_failed'
  | 'install_incomplete'
  | 'restart_scheduled'
  | 'restarting'
  | 'health_checking'
  | 'installed'
  | 'rollback_started'
  | 'rollback_succeeded'
  | 'rollback_failed'
  | 'installed_needs_restart';

export interface CheckForUpdateOutput {
  status: UpdateActionStatus;
  currentVersion: string | null;
  latestVersion: string | null;
  archive: string | null;
  userMessage: string;
}

export interface InstallUpdateInput {
  requestId: string;
}

export interface InstallUpdateOutput {
  status: UpdateActionStatus;
  version: string | null;
  userMessage: string;
}

export interface FactoryResetOutput {
  status: 'completed';
  resetPaths: string[];
  preservedPaths: string[];
  auditLog: string;
  finishedAt: string;
  userMessage: string;
}

export interface ProductionMaintenanceStatusOutput {
  factoryReset: {
    installRoot: string;
    sharedRoot: string;
    resetPaths: string[];
    preservedPaths: string[];
    confirmation: 'RESET_CLAW_TRADE';
    maintenanceLocked?: boolean;
  };
  update: {
    configured?: boolean;
    currentVersion?: string | null;
    publicKeyInstalled?: boolean;
    status: UpdateActionStatus;
    userMessage: string;
    latestVersion?: string | null;
    progressPercent?: number | null;
    progressLabel?: string | null;
    downloadReceivedBytes?: number | null;
    downloadTotalBytes?: number | null;
    updatedAt?: string | null;
  };
}

export interface AdvancedDiagnosticsProviderHealthOutput {
  state: 'not_configured' | 'healthy' | 'degraded';
  severity: 'info' | 'success' | 'warning' | 'error';
  userMessage: string;
  checkedAt: string;
  provider?: string | null;
  model?: string | null;
  source: string;
}

export interface AdvancedDiagnosticsRuntimeServiceStatusOutput {
  state: 'healthy' | 'degraded';
  severity: 'success' | 'warning' | 'error';
  userMessage: string;
  checkedAt: string;
  source: string;
}

export interface AdvancedDiagnosticsLiveRunGapSummaryOutput {
  state: 'no_records' | 'no_gaps' | 'gaps_detected';
  severity: 'info' | 'success' | 'warning' | 'error';
  userMessage: string;
  checkedAt: string;
  source: string;
  latestRun?: {
    runId: string;
    finishedAt?: string | null;
    market?: string | null;
    entryPoint?: string | null;
    collectFirstReportCount: number;
    gapCount: number;
    collectFirstCompliance: {
      batchScope: {
        stageCount: number;
        stages: string[];
      };
      completedItems: number;
      failuresCollected: number;
      earlyStopExceptionUsed: boolean;
      exceptionEvidence: number;
      batchFixGrouping: number;
    };
  } | null;
  recommendedAction: string;
}

export interface AdvancedDiagnosticsEvidenceFailureReasonSummaryOutput {
  state: 'no_records' | 'no_failures' | 'failure_detected';
  severity: 'info' | 'success' | 'warning' | 'error';
  userMessage: string;
  checkedAt: string;
  source: string;
  latestRun?: {
    runId: string;
    finishedAt?: string | null;
    market?: string | null;
    entryPoint?: string | null;
  } | null;
  recommendedAction: string;
}

export interface TestDataSourceInput {
  requestId: string;
  instanceDraft: DataSourceInstanceDraftInput;
}

export interface TestDataSourceOutput {
  state: DataSourceInstanceForUser['state'];
  healthEvent: DataSourceHealthEventForUser;
  canEnable: boolean;
}

export interface SaveDataSourceInstanceInput {
  requestId: string;
  instance: DataSourceInstanceDraftInput;
}

export interface ScheduledReportInput {
  requestId: string;
  instrumentCode: string;
  instrumentName?: string | null;
  market: MarketProfile;
  frequency: 'daily' | 'weekly';
  timeOfDay: string;
  weekday?: number | null;
  notification: { channel: 'wechat_clawbot' | 'in_app'; enabled: boolean };
}

export interface PriceAlertInput {
  requestId: string;
  instrumentCode: string;
  instrumentName?: string | null;
  market: MarketProfile;
  condition: PriceAlertForUser['condition'];
  notification: { channel: 'wechat_clawbot' | 'in_app'; enabled: boolean };
}

export interface RunScheduledReportNowQueuedOutput {
  task: ReportTaskForUser;
  queueSnapshot: ReportQueueSnapshotForUser;
  scheduledReport?: never;
  triggered?: never;
  cronRunId?: never;
}

export interface RunScheduledReportNowCronOutput {
  scheduledReport: ScheduledReportForUser;
  triggered: true;
  cronRunId?: string | null;
  queueSnapshot: ReportQueueSnapshotForUser;
  task?: never;
}

export type RunScheduledReportNowOutput =
  | RunScheduledReportNowCronOutput
  | RunScheduledReportNowQueuedOutput;

export interface RunPriceAlertNowOutput {
  alert: PriceAlertForUser;
  triggered: boolean;
  message?: string;
}
