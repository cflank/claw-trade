import { useCallback, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { useParams } from 'react-router-dom';
import { AppShell } from '../components/AppShell';
import { Composer } from '../components/Composer';
import { InlineErrorState, ReportErrorState } from '../components/ErrorStates';
import {
  getReportDetail,
  getReportChartEvidence,
  listWorkerChatWorkers,
  sendWorkerChat,
  type ReportDetailForUser,
  type WorkerChatWorkerForUser,
} from '../api/workspace';

const WORKER_CHAT_UNAVAILABLE_MESSAGE = 'Worker chat 暂无可用 worker，请刷新页面后重试。';
const WORKER_CHAT_LOAD_FAILED_MESSAGE = 'Worker chat 菜单加载失败，请刷新页面后重试。';

function nextRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function defaultWorkerId(workers: WorkerChatWorkerForUser[]) {
  return workers.find((worker) => worker.default)?.workerId ?? workers[0]?.workerId ?? null;
}

export function ReportDetailPage() {
  const { reportId = '' } = useParams<{ reportId: string }>();
  const [detail, setDetail] = useState<ReportDetailForUser | null>(null);
  const [workerChatWorkers, setWorkerChatWorkers] = useState<WorkerChatWorkerForUser[]>([]);
  const [selectedWorkerId, setSelectedWorkerId] = useState<string | null>(null);
  const [workerChatUnavailableMessage, setWorkerChatUnavailableMessage] = useState('');
  const [loading, setLoading] = useState(true);
  const [statusCode, setStatusCode] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [answer, setAnswer] = useState('');
  const [sending, setSending] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [detailResult, chartResult, workerChatResult] = await Promise.all([
        getReportDetail(reportId),
        getReportChartEvidence(reportId),
        listWorkerChatWorkers()
          .then((result) => ({ workers: result.workers, failed: false }))
          .catch(() => ({ workers: [], failed: true })),
      ]);
      const workers = Array.isArray(workerChatResult.workers) ? workerChatResult.workers : [];
      setWorkerChatWorkers(workers);
      setWorkerChatUnavailableMessage(
        workers.length > 0 ? '' : workerChatResult.failed ? WORKER_CHAT_LOAD_FAILED_MESSAGE : WORKER_CHAT_UNAVAILABLE_MESSAGE,
      );
      setSelectedWorkerId((current) =>
        current && workers.some((worker) => worker.workerId === current) ? current : defaultWorkerId(workers),
      );
      setStatusCode(null);
      setDetail({
        ...detailResult,
        chartEvidence: {
          summary: chartResult.summary,
          items: chartResult.items,
        },
      });
    } catch (loadError) {
      const value = loadError as Error & { status?: number };
      setStatusCode(value.status ?? 500);
    } finally {
      setLoading(false);
    }
  }, [reportId]);

  useEffect(() => {
    void load();
  }, [load]);

  async function askQuestion(text: string) {
    if (!detail) {
      return;
    }
    if (!selectedWorkerId) {
      setError(workerChatUnavailableMessage || WORKER_CHAT_UNAVAILABLE_MESSAGE);
      return;
    }
    setSending(true);
    setError('');
    try {
      const reply = await sendWorkerChat({
        requestId: nextRequestId(),
        mode: 'report_worker_chat',
        workerId: selectedWorkerId,
        reportId: detail.report.id,
        text,
        conversationId: `report-${detail.report.id}`,
      });
      setAnswer(reply.text);
    } catch (askError) {
      setError((askError as Error).message);
    } finally {
      setSending(false);
    }
  }

  const selectedWorker = workerChatWorkers.find((worker) => worker.workerId === selectedWorkerId) ?? null;
  const workerChatDisabled = sending || !selectedWorkerId;
  const workerChatPlaceholder = selectedWorker
    ? `和${selectedWorker.displayName}聊这份报告`
    : 'Worker chat 暂不可用';

  return (
    <AppShell>
      <main className="ct-report-layout" data-testid="report-detail-page">
        {loading ? <div className="ct-notice">加载报告中...</div> : null}
        {!loading && statusCode ? <ReportErrorState statusCode={statusCode} /> : null}
        {!loading && !statusCode && detail ? (
          <section className="ct-report-reader">
            <header className="ct-report-head">
              <h1>{detail.report.title}</h1>
              <p>
                {detail.report.instrumentCode} · {detail.report.market}
              </p>
            </header>
            <div className="ct-report-qa">
              <h2>报告 worker 聊天</h2>
              {workerChatUnavailableMessage ? (
                <div className="ct-notice" role="status">
                  {workerChatUnavailableMessage}
                </div>
              ) : null}
              <Composer
                onSend={askQuestion}
                disabled={workerChatDisabled}
                placeholder={workerChatPlaceholder}
                buttonLabel={sending ? '发送中' : '发送'}
                workerChatEnabled
                workers={workerChatWorkers}
                selectedWorkerId={selectedWorkerId ?? undefined}
                onWorkerChange={(workerId) => setSelectedWorkerId(workerId)}
              />
              {answer ? (
                <div className="ct-report-answer">
                  <ReactMarkdown>{answer}</ReactMarkdown>
                </div>
              ) : null}
              {error ? <InlineErrorState message={error} /> : null}
            </div>
            <article className="ct-report-prose" data-testid="report-body">
              <ReactMarkdown>{detail.markdown}</ReactMarkdown>
            </article>
          </section>
        ) : null}
      </main>
    </AppShell>
  );
}
