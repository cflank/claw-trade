import { useCallback, useEffect, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import { useParams } from 'react-router-dom';
import { AppShell } from '../components/AppShell';
import { Composer } from '../components/Composer';
import { InlineErrorState, ReportErrorState } from '../components/ErrorStates';
import { askReportQuestion, getReportDetail, getReportChartEvidence, type ReportDetailForUser } from '../api/workspace';

function nextRequestId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  return `req-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export function ReportDetailPage() {
  const { reportId = '' } = useParams<{ reportId: string }>();
  const [detail, setDetail] = useState<ReportDetailForUser | null>(null);
  const [loading, setLoading] = useState(true);
  const [statusCode, setStatusCode] = useState<number | null>(null);
  const [error, setError] = useState('');
  const [answer, setAnswer] = useState('');
  const [sending, setSending] = useState(false);

  const load = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      const [detailResult, chartResult] = await Promise.all([
        getReportDetail(reportId),
        getReportChartEvidence(reportId),
      ]);
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
    setSending(true);
    setError('');
    try {
      const reply = await askReportQuestion({
        requestId: nextRequestId(),
        reportId: detail.report.id,
        text,
      });
      setAnswer(reply.text);
    } catch (askError) {
      setError((askError as Error).message);
    } finally {
      setSending(false);
    }
  }

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
            <article className="ct-report-prose" data-testid="report-body">
              <ReactMarkdown>{detail.markdown}</ReactMarkdown>
            </article>
            <div className="ct-report-qa">
              <h2>报告问答</h2>
              <Composer
                onSend={askQuestion}
                disabled={sending}
                placeholder="围绕这份报告继续提问"
                buttonLabel={sending ? '追问中' : '追问'}
              />
              {answer ? <div className="ct-report-answer">{answer}</div> : null}
              {error ? <InlineErrorState message={error} /> : null}
            </div>
          </section>
        ) : null}
      </main>
    </AppShell>
  );
}
