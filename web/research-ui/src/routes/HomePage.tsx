import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { useSearchParams } from 'react-router-dom';
import remarkGfm from 'remark-gfm';
import { AppShell } from '../components/AppShell';
import { Composer } from '../components/Composer';
import { InlineErrorState } from '../components/ErrorStates';
import { HistoryRail } from '../components/HistoryRail';
import { MessageStream } from '../components/MessageStream';
import { RightRail } from '../components/RightRail';
import { withLlmProviderDefaults } from '../components/llmCatalog';
import {
  cancelReportTask,
  cancelSelectionProgress,
  confirmIntentDraft,
  confirmSelectionReport,
  createIntentDraft,
  deleteSavedReport,
  deleteSavedReports,
  getChannelChatSnapshot,
  getChannelStatus,
  getChatSession,
  getReportChartEvidence,
  getReportDetail,
  getReportQueueSnapshot,
  getSelectionRefreshSnapshot,
  listSavedReports,
  listWorkerChatWorkers,
  loadLlmSettings,
  sendChatMessage,
  sendReportFileViaChannel,
  sendWorkerChat,
  type ChannelChatSnapshotForUser,
  type ChannelStatusForUser,
  type ChatContextForUser,
  type ConfirmationCard,
  type ChatMessageForUser,
  type ConfirmIntentDraftOutput,
  type LlmConfigDraft,
  type ReportDetailForUser,
  type ReportQueueSnapshotForUser,
  type ReportTaskForUser,
  type SavedReportForUser,
  type SelectionProgressForUser,
  type SelectionReportForUser,
  type SendChatMessageOutput,
  type WorkerChatWorkerForUser,
} from '../api/workspace';

const DEFAULT_CONTEXT: ChatContextForUser = {
  contextId: 'normal-chat',
  kind: 'normal_chat',
  title: '普通聊天',
  activeTaskId: null,
  activeReportId: null,
};
const REPORT_INPUT_FORMAT_HINT = '格式提示：A股 600519.SH；港股 00700.HK；美股 AAPL；加密 AR/USDT。裸 AR 按美股，写加密请用 AR/USDT。';
const DEVICE_UI_HREF = '/api/ui/open-device-interface';
const WORKER_CHAT_UNAVAILABLE_MESSAGE = 'Worker chat 暂无可用 worker，请刷新页面后重试。';
const WORKER_CHAT_LOAD_FAILED_MESSAGE = 'Worker chat 菜单加载失败，请刷新页面后重试。';
const REPORT_DELETE_SCOPE_COPY = '将删除报告文件、图表、运行证据、worker 输出和相关本地缓存。';

const DEFAULT_QUEUE: ReportQueueSnapshotForUser = {
  runningTask: null,
  queuedTasks: [],
  lastTerminalTask: null,
  queueLimit: 10,
  queuedCount: 0,
  isFull: false,
};

const DEFAULT_LLM_DRAFT: LlmConfigDraft = {
  provider: 'deepseek',
  defaultModel: '',
  status: 'idle',
  reportModelStatus: {
    state: 'unconfigured',
    blocked: true,
    ready: false,
    userMessage: '请先在设置中填写报告模型并完成测试。',
    checkedAt: null,
  },
  embedding: {
    provider: '',
    model: '',
    endpointUrl: '',
    dimension: '',
    apiKeyReplacement: '',
    enabled: false,
  },
};

const SELECTION_WORKER_LABELS = ['策略评审', '反方评审', '整合排序', '组合经理'];

type PendingChatCommand = 'select' | 'chat';

type ReportQaEntry = {
  id: string;
  question: string;
  answer: string;
  workerDisplayName: string;
  status: 'pending' | 'answered' | 'failed';
};

type ReportForwardState = Record<string, { kind: 'success' | 'error'; message: string } | undefined>;

function nextRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function failedTaskMessage(task: NonNullable<ConfirmIntentDraftOutput['task']>): ChatMessageForUser {
  return {
    messageId: `local-failed-${task.taskId}`,
    contextKind: 'task_following',
    actor: 'system',
    kind: 'report_failed',
    text: task.failure?.message ?? '报告任务启动失败，请检查后台运行时。',
    taskId: task.taskId,
    createdAt: new Date().toISOString(),
  };
}

function taskAcceptedMessage(task: NonNullable<ConfirmIntentDraftOutput['task']>): ChatMessageForUser {
  const isRunning = task.status === 'running';
  return {
    messageId: `local-task-accepted-${task.taskId}-${task.status}`,
    contextKind: 'task_following',
    actor: 'system',
    kind: 'task_progress',
    text: isRunning ? '报告任务已启动，正在生成。' : '报告已进入队列。',
    taskId: task.taskId,
    createdAt: new Date().toISOString(),
  };
}

function cancelledTaskMessage(task: ReportTaskForUser, text: string): ChatMessageForUser {
  return {
    messageId: `local-cancelled-task-${task.taskId}-${Date.now()}`,
    contextKind: 'task_following',
    actor: 'system',
    kind: 'task_progress',
    text,
    taskId: task.taskId,
    createdAt: new Date().toISOString(),
  };
}

function cancelledSelectionMessage(text: string): ChatMessageForUser {
  return {
    messageId: `local-cancelled-selection-${Date.now()}`,
    contextKind: 'normal_chat',
    actor: 'system',
    kind: 'task_progress',
    text,
    createdAt: new Date().toISOString(),
  };
}

function cancelledDraftMessage(card: ConfirmationCard): ChatMessageForUser {
  return {
    messageId: `local-cancelled-${card.id}`,
    contextKind: 'normal_chat',
    actor: 'system',
    kind: 'plain',
    text: '已取消本次创建。',
    createdAt: new Date().toISOString(),
  };
}

function selectionReportStartedMessage(ticker: string, reportTaskId?: string | null): ChatMessageForUser {
  return {
    messageId: `local-selection-report-started-${reportTaskId ?? ticker}-${Date.now()}`,
    contextKind: 'task_following',
    actor: 'system',
    kind: 'task_progress',
    text: `${ticker} 已确认进入 /report。`,
    taskId: reportTaskId ?? undefined,
    createdAt: new Date().toISOString(),
  };
}

function isExplicitSelectCommand(text: string) {
  return /^\s*\/select(?:\s+(?:refresh|刷新))?(?:\s+\d{4}-\d{2}-\d{2})?\s*$/i.test(text);
}

function isWorkspaceCommand(text: string) {
  return /^\s*\//.test(text);
}

function defaultWorkerId(workers: WorkerChatWorkerForUser[]) {
  return workers.find((worker) => worker.default)?.workerId ?? workers[0]?.workerId ?? null;
}

function localWorkerChatMessages(
  requestId: string,
  text: string,
  assistantText: string,
  contextKind: ChatContextForUser['kind'],
): ChatMessageForUser[] {
  const createdAt = new Date().toISOString();
  return [
    {
      messageId: `local-worker-user-${requestId}`,
      contextKind,
      actor: 'user',
      kind: 'plain',
      text,
      createdAt,
    },
    {
      messageId: `local-worker-assistant-${requestId}`,
      contextKind,
      actor: 'assistant',
      kind: 'plain',
      text: assistantText,
      createdAt: new Date().toISOString(),
    },
  ];
}

function replaceLocalWorkerChatAssistantMessage(messages: ChatMessageForUser[], requestId: string, text: string) {
  const assistantMessageId = `local-worker-assistant-${requestId}`;
  return messages.map((message) =>
    message.messageId === assistantMessageId ? { ...message, text, createdAt: new Date().toISOString() } : message,
  );
}

function localSelectPendingMessages(text: string): ChatMessageForUser[] {
  const createdAt = new Date().toISOString();
  return [
    {
      messageId: `local-select-user-${Date.now()}`,
      contextKind: 'normal_chat',
      actor: 'user',
      kind: 'plain',
      text,
      createdAt,
    },
    {
      messageId: `local-select-progress-${Date.now()}`,
      contextKind: 'normal_chat',
      actor: 'system',
      kind: 'task_progress',
      text: '`/select` 已收到，正在运行选股工作流。通常需要 1-3 分钟，完成后会显示可确认的候选结果。',
      createdAt,
    },
  ];
}

function runningSelectionProgress(command: string): SelectionProgressForUser {
  const [firstWorker, ...waitingWorkers] = SELECTION_WORKER_LABELS;
  return {
    kind: 'selection_workflow',
    status: 'running',
    statusLabel: '选股中',
    command,
    stageLabel: '选股工作流执行中',
    currentAction: `正在运行${firstWorker}。`,
    percent: 25,
    workerStatusLabels: [`${firstWorker}：执行中`, ...waitingWorkers.map((label) => `${label}：等待启动`)],
    startedAt: new Date().toISOString(),
    finishedAt: null,
    workflowRunId: null,
  };
}

function attachSelectionMetadata(
  messages: ChatMessageForUser[],
  selection: ChatMessageForUser['selection'] | undefined,
) {
  if (!selection) {
    return messages;
  }
  let attached = false;
  return [...messages].reverse().map((message) => {
    if (!attached && (message.kind === 'selection_result' || message.kind === 'selection_unavailable')) {
      attached = true;
      return { ...message, selection };
    }
    return message;
  }).reverse();
}

function hasReportCompletedMessage(messages: ChatMessageForUser[]) {
  return messages.some((message) => message.kind === 'report_completed' && Boolean(message.reportId));
}

function selectionReportFromMessage(message: ChatMessageForUser): SelectionReportForUser | null {
  const markdown = message.selection?.readerReportMarkdown?.trim();
  const id = message.selection?.workflowRunId?.trim();
  if (!markdown || !id) {
    return null;
  }
  return {
    id,
    title: '选股结果报告',
    generatedAt: message.createdAt,
    summarySnippet: selectionSummarySnippet(message.text),
    markdown,
  };
}

function selectionSummarySnippet(text: string) {
  const line = text
    .split(/\r?\n/)
    .map((item) => item.trim())
    .find((item) => item.startsWith('- 进入 `/report`：') || item.startsWith('进入 `/report`：'));
  if (!line) {
    return '`/select` 已完成，等待确认候选。';
  }
  return line.replace(/^- /, '').slice(0, 80);
}

function modelStatusState(draft: LlmConfigDraft) {
  return draft.reportModelStatus?.state ?? 'unconfigured';
}

function sanitizeModelFailureMessage(raw: string) {
  const message = raw.trim();
  if (!message) {
    return '报告模型连接测试失败，请检查模型配置后重试。';
  }
  const lowered = message.toLowerCase();
  if ((lowered.includes('provider') && lowered.includes('attempt')) || lowered.includes('runtime') || lowered.includes('gateway')) {
    return '报告模型连接测试失败，请检查服务商、模型、API Key 和接口地址后重试。';
  }
  return message;
}

function modelWarningMessage(draft: LlmConfigDraft) {
  return sanitizeModelFailureMessage(
    draft.reportModelStatus?.userMessage?.trim() || draft.lastTestMessage?.trim() || '请先在设置里配置报告模型并完成测试。',
  );
}

function readSummaryField(card: ConfirmationCard, label: string) {
  const prefix = `${label}：`;
  const line = card.summaryLines.find((item) => item.startsWith(prefix));
  return line ? line.slice(prefix.length).trim() : '';
}

function asMarketProfile(value: string | undefined): 'CN_A' | 'US' | 'HK' | 'CRYPTO' | undefined {
  if (value === 'CN_A' || value === 'US' || value === 'HK' || value === 'CRYPTO') {
    return value;
  }
  return undefined;
}

function normalizeConfirmationCard(card: ConfirmationCard): ConfirmationCard {
  const instrumentCode = (card.instrumentCode ?? readSummaryField(card, '标的')).trim().toUpperCase();
  const instrumentName = (card.instrumentName ?? readSummaryField(card, '名称')).trim();
  const market = asMarketProfile(card.market ?? readSummaryField(card, '市场'));
  return {
    ...card,
    instrumentCode,
    instrumentName,
    market,
  };
}

export function HomePage() {
  const [searchParams] = useSearchParams();
  const [context, setContext] = useState<ChatContextForUser>(DEFAULT_CONTEXT);
  const [messages, setMessages] = useState<ChatMessageForUser[]>([]);
  const [confirmationCards, setConfirmationCards] = useState<Record<string, ConfirmationCard>>({});
  const [queueSnapshot, setQueueSnapshot] = useState<ReportQueueSnapshotForUser>(DEFAULT_QUEUE);
  const [savedReports, setSavedReports] = useState<SavedReportForUser[]>([]);
  const [channelStatus, setChannelStatus] = useState<ChannelStatusForUser | null>(null);
  const [activeDetail, setActiveDetail] = useState<ReportDetailForUser | null>(null);
  const [activeSelectionDetail, setActiveSelectionDetail] = useState<SelectionReportForUser | null>(null);
  const [selectionProgress, setSelectionProgress] = useState<SelectionProgressForUser | null>(null);
  const [qaEntries, setQaEntries] = useState<ReportQaEntry[]>([]);
  const [workerChatWorkers, setWorkerChatWorkers] = useState<WorkerChatWorkerForUser[]>([]);
  const [selectedWorkerId, setSelectedWorkerId] = useState<string | null>(null);
  const [selectedReportWorkerId, setSelectedReportWorkerId] = useState<string | null>(null);
  const [workerChatUnavailableMessage, setWorkerChatUnavailableMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [pendingChatCommand, setPendingChatCommand] = useState<PendingChatCommand | null>(null);
  const [cardSubmittingId, setCardSubmittingId] = useState<string | null>(null);
  const [selectionSubmittingKey, setSelectionSubmittingKey] = useState<string | null>(null);
  const [cancellingTaskId, setCancellingTaskId] = useState<string | null>(null);
  const [cancellingSelectionId, setCancellingSelectionId] = useState<string | null>(null);
  const [forwardingReportId, setForwardingReportId] = useState<string | null>(null);
  const [reportForwardState, setReportForwardState] = useState<ReportForwardState>({});
  const [error, setError] = useState('');
  const [modelDraft, setModelDraft] = useState<LlmConfigDraft>(DEFAULT_LLM_DRAFT);
  const consumedReportIdRef = useRef<string | null>(null);
  const notifiedTerminalTaskIdsRef = useRef<Set<string>>(new Set());
  const channelRefreshInFlightRef = useRef(false);
  const selectionRequestInFlightRef = useRef(false);
  const selectionCancelTokenRef = useRef(0);
  const localWorkerChatMessagesRef = useRef(false);
  const activeDetailRef = useRef<ReportDetailForUser | null>(null);
  const activeSelectionDetailRef = useRef<SelectionReportForUser | null>(null);

  useEffect(() => {
    activeDetailRef.current = activeDetail;
  }, [activeDetail]);

  useEffect(() => {
    activeSelectionDetailRef.current = activeSelectionDetail;
  }, [activeSelectionDetail]);

  const mergeConfirmationCard = useCallback((card: ConfirmationCard | undefined) => {
    if (!card) {
      return;
    }
    const normalized = normalizeConfirmationCard(card);
    setConfirmationCards((current) => ({ ...current, [normalized.id]: normalized }));
  }, []);

  const showFailedTask = useCallback((task: ReportQueueSnapshotForUser['lastTerminalTask']) => {
    if (!task || task.status !== 'failed' || notifiedTerminalTaskIdsRef.current.has(task.taskId)) {
      return;
    }
    notifiedTerminalTaskIdsRef.current.add(task.taskId);
    const message = failedTaskMessage(task);
    setMessages((current) => [...current, message]);
    setError(message.text);
  }, []);

  const applyQueueSnapshot = useCallback(
    (snapshot: ReportQueueSnapshotForUser) => {
      setQueueSnapshot(snapshot);
      showFailedTask(snapshot.lastTerminalTask);
    },
    [showFailedTask],
  );

  const applySelectionRefreshSnapshot = useCallback((snapshot: { selectionProgress?: SelectionProgressForUser | null } | null) => {
    if (snapshot?.selectionProgress) {
      setSelectionProgress(snapshot.selectionProgress);
      return;
    }
    if (selectionRequestInFlightRef.current) {
      return;
    }
    setSelectionProgress(null);
  }, []);

  const applyChannelChatSnapshot = useCallback((snapshot: ChannelChatSnapshotForUser | null | undefined) => {
    if (!snapshot?.context || !Array.isArray(snapshot.messages) || snapshot.messages.length === 0) {
      return;
    }
    if (activeDetailRef.current || activeSelectionDetailRef.current) {
      return;
    }
    if (localWorkerChatMessagesRef.current && !hasReportCompletedMessage(snapshot.messages)) {
      return;
    }
    setContext(snapshot.context);
    setMessages(snapshot.messages);
    if (snapshot.confirmationCards) {
      setConfirmationCards((current) => ({ ...current, ...snapshot.confirmationCards }));
    }
    setActiveDetail(null);
    setActiveSelectionDetail(null);
  }, []);

  const applyChatSessionSnapshot = useCallback((snapshot: SendChatMessageOutput | null | undefined) => {
    if (!snapshot?.context || !Array.isArray(snapshot.messages) || snapshot.messages.length === 0) {
      return;
    }
    if (activeDetailRef.current || activeSelectionDetailRef.current) {
      return;
    }
    if (localWorkerChatMessagesRef.current && !hasReportCompletedMessage(snapshot.messages)) {
      return;
    }
    setContext(snapshot.context);
    setMessages(snapshot.messages);
    if (snapshot.confirmationCards) {
      setConfirmationCards((current) => ({ ...current, ...snapshot.confirmationCards }));
    }
    setActiveDetail(null);
    setActiveSelectionDetail(null);
  }, []);

  const loadWorkspace = useCallback(async () => {
    setError('');
    try {
      const [historyResult, queueResult, llmResult, channelChatResult, selectionRefreshResult, workerChatResult] = await Promise.all([
        listSavedReports(),
        getReportQueueSnapshot(),
        loadLlmSettings().catch(() => null),
        getChannelChatSnapshot().catch(() => null),
        getSelectionRefreshSnapshot().catch(() => null),
        listWorkerChatWorkers()
          .then((result) => ({ workers: result.workers, failed: false }))
          .catch(() => ({ workers: [], failed: true })),
      ]);
      const workers = Array.isArray(workerChatResult.workers) ? workerChatResult.workers : [];
      setSavedReports(historyResult.items);
      setWorkerChatWorkers(workers);
      setWorkerChatUnavailableMessage(
        workers.length > 0 ? '' : workerChatResult.failed ? WORKER_CHAT_LOAD_FAILED_MESSAGE : WORKER_CHAT_UNAVAILABLE_MESSAGE,
      );
      setSelectedWorkerId((current) =>
        current && workers.some((worker) => worker.workerId === current) ? current : defaultWorkerId(workers),
      );
      setSelectedReportWorkerId((current) =>
        current && workers.some((worker) => worker.workerId === current) ? current : defaultWorkerId(workers),
      );
      applyQueueSnapshot(queueResult);
      applyChannelChatSnapshot(channelChatResult);
      applySelectionRefreshSnapshot(selectionRefreshResult);
      if (llmResult) {
        setModelDraft(withLlmProviderDefaults({ ...DEFAULT_LLM_DRAFT, ...llmResult.draft }));
      }
    } catch (loadError) {
      setError((loadError as Error).message);
    } finally {
      setLoading(false);
    }
    if (!channelRefreshInFlightRef.current) {
      channelRefreshInFlightRef.current = true;
      getChannelStatus({ probe: false })
        .then((channelResult) => setChannelStatus(channelResult))
        .catch(() => {
          // 微信状态不应阻塞主聊天窗口。
        })
        .finally(() => {
          channelRefreshInFlightRef.current = false;
        });
    }
  }, [applyChannelChatSnapshot, applyQueueSnapshot, applySelectionRefreshSnapshot]);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  const refreshWorkspace = useCallback(async () => {
    try {
      const shouldLoadCurrentChat = context.kind !== 'normal_chat';
      const [historyResult, queueResult, channelChatResult, selectionRefreshResult, currentChatResult] = await Promise.all([
        listSavedReports(),
        getReportQueueSnapshot(),
        getChannelChatSnapshot().catch(() => null),
        getSelectionRefreshSnapshot().catch(() => null),
        shouldLoadCurrentChat ? getChatSession(context.contextId).catch(() => null) : Promise.resolve(null),
      ]);
      setSavedReports(historyResult.items);
      applyQueueSnapshot(queueResult);
      if (currentChatResult?.messages?.length) {
        applyChatSessionSnapshot(currentChatResult);
      } else {
        applyChannelChatSnapshot(channelChatResult);
      }
      applySelectionRefreshSnapshot(selectionRefreshResult);
    } catch {
      // 保留当前 UI 状态，轮询失败不打断用户操作。
    }
    if (channelStatus?.qrCodeImageDataUrl || channelRefreshInFlightRef.current) {
      return;
    }
    channelRefreshInFlightRef.current = true;
    getChannelStatus({ probe: false })
      .then((channelResult) => setChannelStatus(channelResult))
      .catch(() => {
        // 保留当前微信状态，轮询失败不打断用户操作。
      })
      .finally(() => {
        channelRefreshInFlightRef.current = false;
      });
  }, [
    applyChannelChatSnapshot,
    applyChatSessionSnapshot,
    applyQueueSnapshot,
    applySelectionRefreshSnapshot,
    channelStatus?.qrCodeImageDataUrl,
    context.contextId,
    context.kind,
  ]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshWorkspace();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [refreshWorkspace]);

  const openReport = useCallback(async (report: SavedReportForUser) => {
    setError('');
    setQaEntries([]);
    setSelectedReportWorkerId(defaultWorkerId(workerChatWorkers));
    try {
      const [detailResult, chartResult] = await Promise.all([
        getReportDetail(report.id),
        getReportChartEvidence(report.id),
      ]);
      setContext({
        contextId: `report-${report.id}`,
        kind: 'report_reading',
        title: report.title,
        activeTaskId: null,
        activeReportId: report.id,
      });
      setActiveSelectionDetail(null);
      setActiveDetail({
        ...detailResult,
        chartEvidence: {
          summary: chartResult.summary,
          items: chartResult.items,
        },
      });
    } catch (openError) {
      setError((openError as Error).message);
    }
  }, [workerChatWorkers]);

  const printReportAsPdf = useCallback(() => {
    window.print();
  }, []);

  const returnToChat = useCallback(() => {
    setError('');
    setActiveDetail(null);
    setActiveSelectionDetail(null);
    setQaEntries([]);
    setContext(DEFAULT_CONTEXT);
  }, []);

  const activeReportId = context.activeReportId ?? activeDetail?.report.id ?? null;
  const activeSelectionReportId = activeSelectionDetail?.id ?? null;
  const selectedReportWorker = workerChatWorkers.find((worker) => worker.workerId === selectedReportWorkerId) ?? null;
  const reportWorkerChatDisabled = sending || !selectedReportWorkerId;
  const reportWorkerChatPlaceholder = selectedReportWorker
    ? `和${selectedReportWorker.displayName}聊这份报告`
    : 'Worker chat 暂不可用';
  const selectionReports = useMemo(() => {
    const reports = new Map<string, SelectionReportForUser>();
    for (const message of messages) {
      const report = selectionReportFromMessage(message);
      if (report) {
        reports.set(report.id, report);
      }
    }
    return [...reports.values()].reverse();
  }, [messages]);

  const openSelectionReport = useCallback((report: SelectionReportForUser) => {
    setError('');
    setQaEntries([]);
    setActiveDetail(null);
    setActiveSelectionDetail(report);
    setContext({
      contextId: `selection-${report.id}`,
      kind: 'normal_chat',
      title: report.title,
      activeTaskId: null,
      activeReportId: null,
    });
  }, []);

  const openSelectionReportFromMessage = useCallback(
    (message: ChatMessageForUser) => {
      const report = selectionReportFromMessage(message);
      if (report) {
        openSelectionReport(report);
      }
    },
    [openSelectionReport],
  );

  const deleteReport = useCallback(
    async (report: SavedReportForUser) => {
      const shouldDelete = window.confirm(`永久删除「${report.title}」？${REPORT_DELETE_SCOPE_COPY}`);
      if (!shouldDelete) {
        return;
      }
      setError('');
      try {
        const result = await deleteSavedReport(nextRequestId(), report.id);
        if (!result.deleted) {
          setError(result.userMessage);
          return;
        }
        setSavedReports((current) => current.filter((item) => item.id !== report.id));
        if (activeReportId === report.id) {
          setActiveDetail(null);
          setQaEntries([]);
          setContext(DEFAULT_CONTEXT);
        }
      } catch (deleteError) {
        setError((deleteError as Error).message);
      }
    },
    [activeReportId],
  );

  const deleteReports = useCallback(
    async (reports: SavedReportForUser[], scope: 'search' | 'all') => {
      if (!reports.length) {
        return;
      }
      const action = scope === 'search' ? `删除当前搜索结果中的 ${reports.length} 份正式报告` : `清空 ${reports.length} 份正式报告`;
      if (!window.confirm(`${action}？每个目标报告运行都会被硬删除。${REPORT_DELETE_SCOPE_COPY}`)) {
        return;
      }
      setError('');
      const reportById = new Map(reports.map((report) => [report.id, report]));
      let deletedIds: string[] = [];
      let failedMessages: string[] = [];
      try {
        const result = await deleteSavedReports(
          nextRequestId(),
          reports.map((report) => report.id),
        );
        deletedIds = result.deletedRunIds;
        failedMessages = result.runs
          .filter((item) => item.status !== 'deleted')
          .map((item) => {
            const message = item.userMessage.trim();
            return message || `${reportById.get(item.reportId)?.title ?? item.reportId} 未删除。`;
          });
        if (!failedMessages.length) {
          failedMessages = [...result.skippedRunIds, ...result.failedRunIds].map((reportId) => {
            const message = result.userMessage.trim();
            return message || `${reportById.get(reportId)?.title ?? reportId} 未删除。`;
          });
        }
      } catch (deleteError) {
        const message = (deleteError as Error).message.trim();
        failedMessages = [message || `${reports.length} 份报告删除失败。`];
      }

      if (deletedIds.length) {
        const deletedSet = new Set(deletedIds);
        setSavedReports((current) => current.filter((item) => !deletedSet.has(item.id)));
        if (activeReportId && deletedSet.has(activeReportId)) {
          setActiveDetail(null);
          setQaEntries([]);
          setContext(DEFAULT_CONTEXT);
        }
      }

      if (failedMessages.length) {
        const uniqueMessages = [...new Set(failedMessages)];
        const prefix = deletedIds.length
          ? `已删除 ${deletedIds.length} 份报告，${failedMessages.length} 份未删除。`
          : `${failedMessages.length} 份报告未删除。`;
        setError(`${prefix}原因：${uniqueMessages.join('；')}`);
      }
    },
    [activeReportId],
  );

  const forwardReportToWechat = useCallback(async (report: SavedReportForUser) => {
    setReportForwardState((current) => ({ ...current, [report.id]: undefined }));
    setError('');
    setForwardingReportId(report.id);
    try {
      const result = await sendReportFileViaChannel({
        requestId: nextRequestId(),
        reportId: report.id,
        channelKind: 'wechat_clawbot',
      });
      setReportForwardState((current) => ({
        ...current,
        [report.id]: { kind: 'success', message: result.userMessage || '已转发到微信。' },
      }));
    } catch (forwardError) {
      const message = (forwardError as Error).message || '完整报告文件暂不可发送，请在设备界面查看。';
      setReportForwardState((current) => ({
        ...current,
        [report.id]: { kind: 'error', message },
      }));
      setError(message);
    } finally {
      setForwardingReportId(null);
    }
  }, []);

  const cancelTask = useCallback(
    async (task: ReportTaskForUser) => {
      const isRunning = task.status === 'running';
      const question = isRunning
        ? `停止「${task.instrumentCode}」报告任务？当前模型请求可能会在后台结束，但不会继续调度后续阶段。`
        : `取消「${task.instrumentCode}」排队任务？`;
      if (!window.confirm(question)) {
        return;
      }
      setCancellingTaskId(task.taskId);
      setError('');
      try {
        const result = await cancelReportTask({
          requestId: nextRequestId(),
          taskId: task.taskId,
        });
        applyQueueSnapshot(result.queueSnapshot);
        setMessages((current) => [...current, cancelledTaskMessage(task, result.message)]);
        if (context.activeTaskId === task.taskId) {
          setContext(DEFAULT_CONTEXT);
        }
      } catch (cancelError) {
        setError((cancelError as Error).message);
      } finally {
        setCancellingTaskId(null);
      }
    },
    [applyQueueSnapshot, context.activeTaskId],
  );

  const cancelSelection = useCallback(async (progress: SelectionProgressForUser) => {
    if (!window.confirm(`停止「${progress.command}」？当前请求可能会在后台结束，但页面不会继续跟进本次结果。`)) {
      return;
    }
    selectionCancelTokenRef.current += 1;
    selectionRequestInFlightRef.current = false;
    setSending(false);
    setPendingChatCommand(null);
    setSelectionProgress(null);
    setMessages((current) => [...current, cancelledSelectionMessage('已停止选股任务。')]);
    if (!progress.workflowRunId) {
      return;
    }
    setCancellingSelectionId(progress.workflowRunId);
    setError('');
    try {
      await cancelSelectionProgress({
        requestId: nextRequestId(),
        workflowRunId: progress.workflowRunId,
      });
    } catch (cancelError) {
      setError((cancelError as Error).message);
    } finally {
      setCancellingSelectionId(null);
    }
  }, []);

  const onSendChat = useCallback(
    async (text: string) => {
      const isSelectCommand = isExplicitSelectCommand(text);
      const useCommandChat = isWorkspaceCommand(text);
      let pendingWorkerRequestId: string | null = null;
      const selectToken = isSelectCommand ? selectionCancelTokenRef.current + 1 : selectionCancelTokenRef.current;
      if (isSelectCommand) {
        selectionCancelTokenRef.current = selectToken;
      }
      setSending(true);
      setPendingChatCommand(isSelectCommand ? 'select' : 'chat');
      setError('');
      if (isSelectCommand) {
        selectionRequestInFlightRef.current = true;
        setMessages((current) => [...current, ...localSelectPendingMessages(text)]);
        setSelectionProgress(runningSelectionProgress(text));
      }
      try {
        if (!useCommandChat) {
          if (!selectedWorkerId) {
            throw new Error(workerChatUnavailableMessage || '请先选择一个 worker。');
          }
          const requestId = nextRequestId();
          pendingWorkerRequestId = requestId;
          localWorkerChatMessagesRef.current = true;
          setMessages((current) => [...current, ...localWorkerChatMessages(requestId, text, 'worker 正在分析...', context.kind)]);
          const reply = await sendWorkerChat({
            requestId,
            mode: 'generic_worker_chat',
            workerId: selectedWorkerId,
            text,
            conversationId: context.contextId,
          });
          setMessages((current) => replaceLocalWorkerChatAssistantMessage(current, requestId, reply.text));
          setActiveDetail(null);
          setActiveSelectionDetail(null);
          return;
        }
        const result = await sendChatMessage({
          requestId: nextRequestId(),
          contextId: context.contextId,
          text,
        });
        if (isSelectCommand && selectionCancelTokenRef.current !== selectToken) {
          return;
        }
        localWorkerChatMessagesRef.current = false;
        setContext(result.context);
        setMessages(attachSelectionMetadata(result.messages, result.selection));
        mergeConfirmationCard(result.confirmationCard);
        if (result.confirmationCards) {
          setConfirmationCards((current) => ({ ...current, ...result.confirmationCards }));
        }
        if (result.queueSnapshot) {
          applyQueueSnapshot(result.queueSnapshot);
        }
        if (isSelectCommand) {
          selectionRequestInFlightRef.current = false;
          const refreshSnapshot = await getSelectionRefreshSnapshot().catch(() => null);
          if (refreshSnapshot?.selectionProgress) {
            applySelectionRefreshSnapshot(refreshSnapshot);
          } else {
            setSelectionProgress(null);
          }
        }
        setActiveDetail(null);
        setActiveSelectionDetail(null);
      } catch (sendError) {
        if (isSelectCommand && selectionCancelTokenRef.current !== selectToken) {
          return;
        }
        if (isSelectCommand) {
          selectionRequestInFlightRef.current = false;
          setSelectionProgress(null);
        }
        const failedWorkerRequestId = pendingWorkerRequestId;
        if (failedWorkerRequestId) {
          setMessages((current) =>
            replaceLocalWorkerChatAssistantMessage(current, failedWorkerRequestId, 'worker 回复失败，请稍后重试。'),
          );
        }
        setError((sendError as Error).message);
      } finally {
        setSending(false);
        setPendingChatCommand(null);
      }
    },
    [
      applyQueueSnapshot,
      applySelectionRefreshSnapshot,
      context.contextId,
      context.kind,
      mergeConfirmationCard,
      selectedWorkerId,
      workerChatUnavailableMessage,
    ],
  );

  const onAskReport = useCallback(
    async (text: string) => {
      if (!activeDetail) {
        return;
      }
      if (!selectedReportWorkerId) {
        setError(workerChatUnavailableMessage || WORKER_CHAT_UNAVAILABLE_MESSAGE);
        return;
      }
      setSending(true);
      setError('');
      const requestId = nextRequestId();
      const entryId = `qa-${requestId}`;
      const workerDisplayName =
        workerChatWorkers.find((worker) => worker.workerId === selectedReportWorkerId)?.displayName ?? 'worker';
      setQaEntries((current) => [
        ...current,
        {
          id: entryId,
          question: text,
          answer: '',
          workerDisplayName,
          status: 'pending',
        },
      ]);
      try {
        const reply = await sendWorkerChat({
          requestId,
          mode: 'report_worker_chat',
          workerId: selectedReportWorkerId,
          reportId: activeDetail.report.id,
          text,
          conversationId: `report-${activeDetail.report.id}`,
        });
        setQaEntries((current) =>
          current.map((entry) =>
            entry.id === entryId
              ? { ...entry, answer: reply.text, workerDisplayName: reply.workerDisplayName, status: 'answered' }
              : entry,
          ),
        );
      } catch (askError) {
        const message = (askError as Error).message;
        setError(message);
        setQaEntries((current) =>
          current.map((entry) => (entry.id === entryId ? { ...entry, answer: message, status: 'failed' } : entry)),
        );
      } finally {
        setSending(false);
      }
    },
    [activeDetail, selectedReportWorkerId, workerChatUnavailableMessage, workerChatWorkers],
  );

  const openReportById = useCallback(
    async (reportId: string) => {
      const existing = savedReports.find((item) => item.id === reportId);
      if (existing) {
        await openReport(existing);
        return;
      }
      setError('');
      setQaEntries([]);
      setSelectedReportWorkerId(defaultWorkerId(workerChatWorkers));
      try {
        const [detailResult, chartResult] = await Promise.all([
          getReportDetail(reportId),
          getReportChartEvidence(reportId),
        ]);
        setContext({
          contextId: `report-${reportId}`,
          kind: 'report_reading',
          title: detailResult.report.title,
          activeTaskId: null,
          activeReportId: reportId,
        });
        setActiveSelectionDetail(null);
        setActiveDetail({
          ...detailResult,
          chartEvidence: {
            summary: chartResult.summary,
            items: chartResult.items,
          },
        });
      } catch (openError) {
        setError((openError as Error).message);
      }
    },
    [openReport, savedReports, workerChatWorkers],
  );

  useEffect(() => {
    const reportId = searchParams.get('reportId');
    if (!reportId) {
      consumedReportIdRef.current = null;
      return;
    }
    if (consumedReportIdRef.current === reportId) {
      return;
    }
    consumedReportIdRef.current = reportId;
    void openReportById(reportId);
  }, [openReportById, searchParams]);

  const applyCardDecision = useCallback(
    async (card: ConfirmationCard, decision: 'confirm' | 'cancel') => {
      setCardSubmittingId(card.id);
      setError('');
      try {
        const result = await confirmIntentDraft({
          requestId: nextRequestId(),
          draftId: card.draftId,
          decision,
          contextId: context.contextId,
          overrides:
            decision === 'confirm'
              ? {
                  instrumentCode: card.instrumentCode,
                  market: card.market,
                }
              : undefined,
        });
        setConfirmationCards((current) => ({
          ...current,
          [card.id]: { ...card, status: decision === 'confirm' ? 'confirmed' : 'cancelled' },
        }));
        if (result.messages) {
          setMessages(result.messages);
        } else if (decision === 'cancel') {
          setMessages((current) => [...current, cancelledDraftMessage(card)]);
        } else if (result.task && result.task.status !== 'failed') {
          setMessages((current) => [...current, taskAcceptedMessage(result.task!)]);
        }
        if (result.task?.status === 'failed') {
          showFailedTask(result.task);
        }
        if (result.queueSnapshot) {
          applyQueueSnapshot(result.queueSnapshot);
        }
        if (result.context) {
          setContext(result.context);
        } else if (result.task) {
          setContext({
            contextId: context.contextId,
            kind: 'task_following',
            title: `任务跟进：${result.task.instrumentCode}`,
            activeTaskId: result.task.taskId,
            activeReportId: result.task.reportId ?? null,
          });
        }
        setActiveDetail(null);
        setActiveSelectionDetail(null);
        await refreshWorkspace();
      } catch (decisionError) {
        setError((decisionError as Error).message);
      } finally {
        setCardSubmittingId(null);
      }
    },
    [applyQueueSnapshot, context.contextId, refreshWorkspace, showFailedTask],
  );

  const confirmSelectionCandidate = useCallback(
    async (item: ChatMessageForUser, ticker: string) => {
      const workflowRunId = item.selection?.workflowRunId?.trim();
      if (!workflowRunId) {
        setError('当前选股结果缺少确认信息，请重新执行 /select。');
        return;
      }
      const key = `${workflowRunId}:${ticker}`;
      setSelectionSubmittingKey(key);
      setError('');
      try {
        const result = await confirmSelectionReport({
          requestId: nextRequestId(),
          selectWorkflowRunId: workflowRunId,
          ticker,
          originContextId: context.contextId,
        });
        setMessages((current) => [...current, selectionReportStartedMessage(ticker, result.reportTaskId)]);
        if (result.task && result.task.status !== 'failed') {
          setMessages((current) => [...current, taskAcceptedMessage(result.task!)]);
        }
        if (result.task?.status === 'failed') {
          showFailedTask(result.task);
        }
        if (result.queueSnapshot) {
          applyQueueSnapshot(result.queueSnapshot);
        }
        if (result.task) {
          setContext({
            contextId: context.contextId,
            kind: 'task_following',
            title: `任务跟进：${result.task.instrumentCode}`,
            activeTaskId: result.task.taskId,
            activeReportId: result.task.reportId ?? null,
          });
        }
        setActiveDetail(null);
        setActiveSelectionDetail(null);
        await refreshWorkspace();
      } catch (selectionError) {
        setError((selectionError as Error).message);
      } finally {
        setSelectionSubmittingKey(null);
      }
    },
    [applyQueueSnapshot, context.contextId, refreshWorkspace, showFailedTask],
  );

  const onCardSymbolChange = useCallback((card: ConfirmationCard, value: string) => {
    const normalized = value.trim().toUpperCase();
    setConfirmationCards((current) => ({
      ...current,
      [card.id]: {
        ...current[card.id],
        instrumentCode: normalized,
        instrumentName: '',
        market: undefined,
        validationState: 'mismatch',
        validationMessage: normalized ? '请先点击“更新标的”完成重新识别。' : '请先输入标的代码。',
        suggestedMarket: null,
      },
    }));
  }, []);

  const onCardSymbolRefresh = useCallback(
    async (card: ConfirmationCard) => {
      const code = String(card.instrumentCode ?? '').trim().toUpperCase();
      if (!code) {
        return;
      }
      setCardSubmittingId(card.id);
      setError('');
      try {
        const result = await createIntentDraft({
          requestId: nextRequestId(),
          sourceMessageId: `card-${card.id}`,
          text: `/report ${code}`,
        });
        const refreshed = normalizeConfirmationCard(result.confirmationCard);
        setConfirmationCards((current) => ({
          ...current,
          [card.id]: {
            ...refreshed,
            id: card.id,
            status: 'active',
            validationState: 'matched',
            validationMessage: null,
            suggestedMarket: null,
          },
        }));
      } catch (refreshError) {
        setError((refreshError as Error).message);
      } finally {
        setCardSubmittingId(null);
      }
    },
    [],
  );

  const onCardMarketChange = useCallback(
    async (card: ConfirmationCard, market: 'CN_A' | 'US' | 'HK' | 'CRYPTO') => {
      const code = String(card.instrumentCode ?? '').trim().toUpperCase();
      setConfirmationCards((current) => ({
        ...current,
        [card.id]: {
          ...current[card.id],
          market,
          validationState: 'mismatch',
          validationMessage: '市场切换后正在重新校验，请稍候。',
          suggestedMarket: null,
        },
      }));
      if (!code) {
        return;
      }
      setCardSubmittingId(card.id);
      setError('');
      try {
        const result = await createIntentDraft({
          requestId: nextRequestId(),
          sourceMessageId: `card-${card.id}`,
          text: `/report ${code}`,
        });
        const refreshed = normalizeConfirmationCard(result.confirmationCard);
        const detectedMarket = refreshed.market;
        const mismatch = detectedMarket !== market;
        setConfirmationCards((current) => ({
          ...current,
          [card.id]: {
            ...current[card.id],
            instrumentName: refreshed.instrumentName,
            market,
            validationState: mismatch ? 'mismatch' : 'matched',
            validationMessage: mismatch
              ? `当前标的识别为 ${detectedMarket} 市场，请修改标的或选择匹配市场。`
              : null,
            suggestedMarket: mismatch ? detectedMarket ?? null : null,
          },
        }));
      } catch (validateError) {
        setError((validateError as Error).message);
      } finally {
        setCardSubmittingId(null);
      }
    },
    [],
  );

  const queueHeadline = useMemo(() => {
    const running = queueSnapshot.runningTask ? 1 : 0;
    const queued = queueSnapshot.queuedTasks.length;
    return `${running} 运行中 · ${queued} 排队中`;
  }, [queueSnapshot.queuedTasks.length, queueSnapshot.runningTask]);

  const readerTitle = activeDetail?.report.title ?? activeSelectionDetail?.title ?? '投研工作台';
  const contextLabel = activeDetail ? '报告阅读' : activeSelectionDetail ? '选股报告' : '聊天会话';
  const isReading = Boolean(activeDetail || activeSelectionDetail);
  const modelState = modelStatusState(modelDraft);
  const showModelWarning = !loading && !isReading && modelState !== 'ready';
  const modelWarningIsError = modelState === 'failed';
  const mainComposerDisabled = sending;

  return (
    <AppShell>
      <main className="ct-workspace" data-testid="workspace-layout">
        <HistoryRail
          items={savedReports}
          selectionItems={selectionReports}
          activeReportId={activeReportId}
          activeSelectionReportId={activeSelectionReportId}
          forwardingReportId={forwardingReportId}
          forwardStatusByReportId={reportForwardState}
          onOpenReport={(report) => void openReport(report)}
          onForwardReport={(report) => void forwardReportToWechat(report)}
          onDeleteReport={(report) => void deleteReport(report)}
          onDeleteReports={(reports, scope) => void deleteReports(reports, scope)}
          onOpenSelectionReport={openSelectionReport}
        />

        <section className="ct-center-panel">
          <header className="ct-center-head">
            <h1>{readerTitle}</h1>
            <div className="ct-center-head-actions">
              <span className="ct-context-label">{contextLabel}</span>
              {!isReading ? (
                <a className="ct-text-button" href={DEVICE_UI_HREF} target="_blank" rel="noreferrer">
                  打开设备界面
                </a>
              ) : null}
              {isReading ? (
                <button type="button" className="ct-text-button ct-report-back-button" onClick={returnToChat}>
                  返回聊天
                </button>
              ) : null}
            </div>
          </header>
          <section className="ct-workspace-summary">
            <div>
              <span className="ct-small">当前上下文</span>
              <strong>{context.title}</strong>
            </div>
            <div>
              <span className="ct-small">任务状态</span>
              <strong>{queueHeadline}</strong>
            </div>
            <div>
              <span className="ct-small">正式报告</span>
              <strong>{savedReports.length} 份</strong>
            </div>
          </section>
          {loading ? <div className="ct-notice">加载中...</div> : null}
          {error ? <InlineErrorState message={error} /> : null}
          {showModelWarning ? (
            <section
              className={`ct-model-warning${modelWarningIsError ? ' is-error' : ''}`}
              data-testid="llm-config-warning"
              role="status"
              aria-live="polite"
            >
              <div>
                <strong>报告模型还没配置成功</strong>
                <p>{modelWarningMessage(modelDraft)}</p>
              </div>
              <a className="ct-button-link" href="/settings">
                去设置模型
              </a>
            </section>
          ) : null}

          {activeDetail ? (
            <>
              <article className="ct-report-prose" data-testid="reading-report-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{activeDetail.markdown}</ReactMarkdown>
              </article>
              <section className="ct-reading-chat" aria-label="报告 worker 聊天">
                <div className="ct-qa-list" data-testid="reading-qa-list">
                  {qaEntries.map((entry) => (
                    <section className={`ct-qa-item ct-qa-${entry.status}`} key={entry.id}>
                      <div className="ct-qa-row">
                        <span>你</span>
                        <p>{entry.question}</p>
                      </div>
                      <div className="ct-qa-row">
                        <span>{entry.workerDisplayName}</span>
                        {entry.status === 'pending' ? (
                          <p>worker 正在分析...</p>
                        ) : (
                          <div className="ct-qa-answer-markdown">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>{entry.answer}</ReactMarkdown>
                          </div>
                        )}
                      </div>
                    </section>
                  ))}
                </div>
                {workerChatUnavailableMessage ? (
                  <div className="ct-notice" role="status">
                    {workerChatUnavailableMessage}
                  </div>
                ) : null}
                <Composer
                  onSend={onAskReport}
                  disabled={reportWorkerChatDisabled}
                  placeholder={reportWorkerChatPlaceholder}
                  buttonLabel={sending ? '发送中' : '发送'}
                  workerChatEnabled
                  workers={workerChatWorkers}
                  selectedWorkerId={selectedReportWorkerId ?? undefined}
                  onWorkerChange={(workerId) => setSelectedReportWorkerId(workerId)}
                />
              </section>
            </>
          ) : activeSelectionDetail ? (
            <article className="ct-report-prose" data-testid="reading-selection-report-body">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{activeSelectionDetail.markdown}</ReactMarkdown>
            </article>
          ) : (
            <>
              <MessageStream
                items={messages}
                confirmationCards={confirmationCards}
                cardSubmittingId={cardSubmittingId}
                onConfirmCard={(card) => applyCardDecision(card, 'confirm')}
                onCancelCard={(card) => applyCardDecision(card, 'cancel')}
                onCardSymbolChange={onCardSymbolChange}
                onCardSymbolRefresh={onCardSymbolRefresh}
                onCardMarketChange={onCardMarketChange}
                onOpenReport={(reportId) => void openReportById(reportId)}
                selectionSubmittingKey={selectionSubmittingKey}
                onConfirmSelectionCandidate={(item, ticker) => void confirmSelectionCandidate(item, ticker)}
                onOpenSelectionReport={openSelectionReportFromMessage}
              />
              {workerChatUnavailableMessage ? (
                <div className="ct-notice" role="status">
                  {workerChatUnavailableMessage}
                </div>
              ) : null}
              <Composer
                onSend={onSendChat}
                disabled={mainComposerDisabled}
                placeholder="输入问题，或提交报告任务需求"
                buttonLabel={pendingChatCommand === 'select' ? '选股中' : sending ? '发送中' : '发送'}
                hint={REPORT_INPUT_FORMAT_HINT}
                workerChatEnabled
                workers={workerChatWorkers}
                selectedWorkerId={selectedWorkerId ?? undefined}
                onWorkerChange={(workerId) => setSelectedWorkerId(workerId)}
              />
            </>
          )}
        </section>

        <RightRail
          queue={queueSnapshot}
          detail={activeDetail}
          selectionProgress={selectionProgress}
          channel={channelStatus}
          latestReport={savedReports[0] ?? null}
          onPrintReport={activeDetail ? printReportAsPdf : undefined}
          onCancelTask={cancelTask}
          cancellingTaskId={cancellingTaskId}
          onCancelSelection={cancelSelection}
          cancellingSelectionId={cancellingSelectionId}
        />
      </main>
    </AppShell>
  );
}
