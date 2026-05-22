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
import {
  askReportQuestion,
  confirmIntentDraft,
  deleteSavedReport,
  getChannelStatus,
  getReportChartEvidence,
  getReportDetail,
  getReportQueueSnapshot,
  listSavedReports,
  sendChatMessage,
  type ChannelStatusForUser,
  type ChatContextForUser,
  type ConfirmationCard,
  type ChatMessageForUser,
  type ConfirmIntentDraftOutput,
  type ReportDetailForUser,
  type ReportQueueSnapshotForUser,
  type SavedReportForUser,
} from '../api/workspace';

const DEFAULT_CONTEXT: ChatContextForUser = {
  contextId: 'normal-chat',
  kind: 'normal_chat',
  title: '普通聊天',
  activeTaskId: null,
  activeReportId: null,
};

const DEFAULT_QUEUE: ReportQueueSnapshotForUser = {
  runningTask: null,
  queuedTasks: [],
  lastTerminalTask: null,
  queueLimit: 10,
  queuedCount: 0,
  isFull: false,
};

type ReportQaEntry = {
  id: string;
  question: string;
  answer: string;
  status: 'pending' | 'answered' | 'failed';
};

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

export function HomePage() {
  const [searchParams] = useSearchParams();
  const [context, setContext] = useState<ChatContextForUser>(DEFAULT_CONTEXT);
  const [messages, setMessages] = useState<ChatMessageForUser[]>([]);
  const [confirmationCards, setConfirmationCards] = useState<Record<string, ConfirmationCard>>({});
  const [queueSnapshot, setQueueSnapshot] = useState<ReportQueueSnapshotForUser>(DEFAULT_QUEUE);
  const [savedReports, setSavedReports] = useState<SavedReportForUser[]>([]);
  const [channelStatus, setChannelStatus] = useState<ChannelStatusForUser | null>(null);
  const [activeDetail, setActiveDetail] = useState<ReportDetailForUser | null>(null);
  const [qaEntries, setQaEntries] = useState<ReportQaEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [cardSubmittingId, setCardSubmittingId] = useState<string | null>(null);
  const [error, setError] = useState('');
  const consumedReportIdRef = useRef<string | null>(null);
  const notifiedTerminalTaskIdsRef = useRef<Set<string>>(new Set());

  const mergeConfirmationCard = useCallback((card: ConfirmationCard | undefined) => {
    if (!card) {
      return;
    }
    setConfirmationCards((current) => ({ ...current, [card.id]: card }));
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

  const loadWorkspace = useCallback(async () => {
    setError('');
    try {
      const [historyResult, queueResult] = await Promise.all([
        listSavedReports(),
        getReportQueueSnapshot(),
      ]);
      setSavedReports(historyResult.items);
      applyQueueSnapshot(queueResult);
    } catch (loadError) {
      setError((loadError as Error).message);
    } finally {
      setLoading(false);
    }
    getChannelStatus()
      .then((channelResult) => setChannelStatus(channelResult))
      .catch(() => {
        // 微信状态不应阻塞主聊天窗口。
      });
  }, [applyQueueSnapshot]);

  useEffect(() => {
    void loadWorkspace();
  }, [loadWorkspace]);

  const refreshWorkspace = useCallback(async () => {
    try {
      const [historyResult, queueResult] = await Promise.all([
        listSavedReports(),
        getReportQueueSnapshot(),
      ]);
      setSavedReports(historyResult.items);
      applyQueueSnapshot(queueResult);
    } catch {
      // 保留当前 UI 状态，轮询失败不打断用户操作。
    }
    getChannelStatus()
      .then((channelResult) => setChannelStatus(channelResult))
      .catch(() => {
        // 保留当前微信状态，轮询失败不打断用户操作。
      });
  }, [applyQueueSnapshot]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      void refreshWorkspace();
    }, 4000);
    return () => window.clearInterval(timer);
  }, [refreshWorkspace]);

  const openReport = useCallback(async (report: SavedReportForUser) => {
    setError('');
    setQaEntries([]);
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
  }, []);

  const printReportAsPdf = useCallback(() => {
    window.print();
  }, []);

  const returnToChat = useCallback(() => {
    setError('');
    setActiveDetail(null);
    setQaEntries([]);
    setContext(DEFAULT_CONTEXT);
  }, []);

  const activeReportId = context.activeReportId ?? activeDetail?.report.id ?? null;

  const deleteReport = useCallback(
    async (report: SavedReportForUser) => {
      const shouldDelete = window.confirm(`从历史中移除「${report.title}」？底层运行证据会保留。`);
      if (!shouldDelete) {
        return;
      }
      setError('');
      try {
        await deleteSavedReport(nextRequestId(), report.id);
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

  const onSendChat = useCallback(
    async (text: string) => {
      setSending(true);
      setError('');
      try {
        const result = await sendChatMessage({
          requestId: nextRequestId(),
          contextId: context.contextId,
          text,
        });
        setContext(result.context);
        setMessages(result.messages);
        mergeConfirmationCard(result.confirmationCard);
        if (result.queueSnapshot) {
          applyQueueSnapshot(result.queueSnapshot);
        }
        setActiveDetail(null);
      } catch (sendError) {
        setError((sendError as Error).message);
      } finally {
        setSending(false);
      }
    },
    [applyQueueSnapshot, context.contextId, mergeConfirmationCard],
  );

  const onAskReport = useCallback(
    async (text: string) => {
      if (!activeDetail) {
        return;
      }
      setSending(true);
      setError('');
      const requestId = nextRequestId();
      const entryId = `qa-${requestId}`;
      setQaEntries((current) => [
        ...current,
        {
          id: entryId,
          question: text,
          answer: '',
          status: 'pending',
        },
      ]);
      try {
        const reply = await askReportQuestion({
          requestId,
          reportId: activeDetail.report.id,
          text,
        });
        setQaEntries((current) =>
          current.map((entry) =>
            entry.id === entryId ? { ...entry, answer: reply.text, status: 'answered' } : entry,
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
    [activeDetail],
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
    [openReport, savedReports],
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
        if (result.task) {
          setContext({
            contextId: `task-${result.task.taskId}`,
            kind: 'task_following',
            title: `任务跟进：${result.task.instrumentCode}`,
            activeTaskId: result.task.taskId,
            activeReportId: result.task.reportId ?? null,
          });
        }
        setActiveDetail(null);
        await refreshWorkspace();
      } catch (decisionError) {
        setError((decisionError as Error).message);
      } finally {
        setCardSubmittingId(null);
      }
    },
    [applyQueueSnapshot, refreshWorkspace, showFailedTask],
  );

  const queueHeadline = useMemo(() => {
    const running = queueSnapshot.runningTask ? 1 : 0;
    const queued = queueSnapshot.queuedTasks.length;
    return `${running} 运行中 · ${queued} 排队中`;
  }, [queueSnapshot.queuedTasks.length, queueSnapshot.runningTask]);

  return (
    <AppShell>
      <main className="ct-workspace" data-testid="workspace-layout">
        <HistoryRail
          items={savedReports}
          activeReportId={activeReportId}
          onOpenReport={(report) => void openReport(report)}
          onDeleteReport={(report) => void deleteReport(report)}
        />

        <section className="ct-center-panel">
          <header className="ct-center-head">
            <h1>{activeDetail ? activeDetail.report.title : '投研工作台'}</h1>
            <div className="ct-center-head-actions">
              <span className="ct-context-label">{context.kind === 'report_reading' ? '报告阅读' : '聊天会话'}</span>
              {activeDetail ? (
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

          {activeDetail ? (
            <>
              <article className="ct-report-prose" data-testid="reading-report-body">
                <ReactMarkdown remarkPlugins={[remarkGfm]}>{activeDetail.markdown}</ReactMarkdown>
              </article>
              <div className="ct-qa-list" data-testid="reading-qa-list">
                {qaEntries.map((entry) => (
                  <section className={`ct-qa-item ct-qa-${entry.status}`} key={entry.id}>
                    <div className="ct-qa-row">
                      <span>你</span>
                      <p>{entry.question}</p>
                    </div>
                    <div className="ct-qa-row">
                      <span>助手</span>
                      {entry.status === 'pending' ? (
                        <p>正在回答...</p>
                      ) : (
                        <div className="ct-qa-answer-markdown">
                          <ReactMarkdown remarkPlugins={[remarkGfm]}>{entry.answer}</ReactMarkdown>
                        </div>
                      )}
                    </div>
                  </section>
                ))}
              </div>
              <Composer
                onSend={onAskReport}
                disabled={sending}
                placeholder="围绕当前报告继续追问"
                buttonLabel={sending ? '追问中' : '追问'}
              />
            </>
          ) : (
            <>
              <MessageStream
                items={messages}
                confirmationCards={confirmationCards}
                cardSubmittingId={cardSubmittingId}
                onConfirmCard={(card) => applyCardDecision(card, 'confirm')}
                onCancelCard={(card) => applyCardDecision(card, 'cancel')}
                onOpenReport={(reportId) => void openReportById(reportId)}
              />
              <Composer
                onSend={onSendChat}
                disabled={sending}
                placeholder="输入问题，或提交报告任务需求"
                buttonLabel={sending ? '发送中' : '发送'}
              />
            </>
          )}
        </section>

        <RightRail
          queue={queueSnapshot}
          detail={activeDetail}
          channel={channelStatus}
          latestReport={savedReports[0] ?? null}
          onPrintReport={activeDetail ? printReportAsPdf : undefined}
        />
      </main>
    </AppShell>
  );
}
