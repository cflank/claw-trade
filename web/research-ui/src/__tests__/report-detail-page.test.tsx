import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router-dom';
import { LegacyReportRoute } from '../routes/LegacyReportRoute';
import { ReportDetailPage } from '../routes/ReportDetailPage';

const WORKERS = [
  { workerId: 'portfolio_manager', displayName: '组合经理', default: true, aliases: ['组合经理', 'PM'] },
  { workerId: 'market_analyst', displayName: '市场分析师', default: false, aliases: ['市场分析师', '市场'] },
];

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function LocationProbe() {
  const location = useLocation();
  return <div data-testid="location">{`${location.pathname}${location.search}`}</div>;
}

describe('legacy report route', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('redirects /reports/:reportId back to workspace with query reportId', async () => {
    render(
      <MemoryRouter initialEntries={['/reports/report-2']}>
        <Routes>
          <Route path="/reports/:reportId" element={<LegacyReportRoute />} />
          <Route path="/" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('location')).toHaveTextContent('/?reportId=report-2');
  });

  it('loads report detail and sends report worker chat without calling legacy ask endpoint', async () => {
    const workerChatBodies: Array<Record<string, unknown>> = [];
    const askReportQuestionBodies: Array<Record<string, unknown>> = [];
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/api/ui/get-report-detail')) {
        return json({
          report: {
            id: 'report-1',
            instrumentCode: '600519.SH',
            instrumentName: '贵州茅台',
            market: 'CN_A',
            title: '茅台投研报告',
            generatedAt: '2026-05-19T10:00:00.000Z',
            summarySnippet: '结论偏积极，关注估值与渠道恢复。',
          },
          markdown: '# 投资结论\n\n建议继续跟踪渠道修复。',
          completionSummary: {
            id: 'summary-1',
            reportId: 'report-1',
            instrumentCode: '600519.SH',
            generatedAt: '2026-05-19T10:00:00.000Z',
            finalConclusion: '继续观察估值和增长匹配度',
            coreReasons: ['现金流稳定'],
            mainRisks: ['消费需求波动'],
            failedConfiguredDataSources: [],
            fullReportAvailable: true,
            pdfAvailable: false,
            createdAt: '2026-05-19T10:00:00.000Z',
          },
          dataSourceEvents: [],
          chartEvidence: { summary: 'ready', items: [] },
          assets: [],
        });
      }
      if (url.includes('/api/ui/get-report-chart-evidence')) {
        return json({ reportId: 'report-1', summary: 'ready', items: [] });
      }
      if (url.includes('/api/ui/list-worker-chat-workers')) {
        return json({ workers: WORKERS });
      }
      if (url.includes('/api/ui/send-worker-chat') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        workerChatBodies.push(body);
        return json({
          kind: 'worker_chat_reply',
          workerDisplayName: '组合经理',
          text: '收到，正在分析。',
          mode: body.mode,
        });
      }
      if (url.includes('/api/ui/ask-report-question') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body)) as Record<string, unknown>;
        askReportQuestionBodies.push(body);
        return json({ text: 'legacy response' });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter initialEntries={['/detail/report-1']}>
        <Routes>
          <Route path="/detail/:reportId" element={<ReportDetailPage />} />
        </Routes>
      </MemoryRouter>,
    );

    const reportBody = await screen.findByTestId('report-body');
    expect(within(reportBody).getByText(/建议继续跟踪渠道修复/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '报告 worker 聊天' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('和组合经理聊这份报告')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '组合经理' })).not.toBeInTheDocument();
    expect(screen.queryByText(/追问/)).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('输入消息'), { target: { value: '核心结论是什么？' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('收到，正在分析。')).toBeInTheDocument();
    await waitFor(() => expect(workerChatBodies).toHaveLength(1));
    expect(workerChatBodies.at(0)).toMatchObject({
      mode: 'report_worker_chat',
      workerId: 'portfolio_manager',
      reportId: 'report-1',
      text: '核心结论是什么？',
      conversationId: 'report-report-1',
    });
    expect(askReportQuestionBodies).toHaveLength(0);
    expect(within(reportBody).queryByText('收到，正在分析。')).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('输入消息'), { target: { value: '@' } });
    const input = screen.getByLabelText('输入消息');
    const marketOption = await screen.findByRole('option', { name: '@市场分析师' });
    marketOption.focus();
    fireEvent.click(marketOption);
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: `${(input as HTMLInputElement).value}市场怎么看？` } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(workerChatBodies).toHaveLength(2));
    expect(workerChatBodies.at(1)).toMatchObject({
      mode: 'report_worker_chat',
      workerId: 'market_analyst',
      reportId: 'report-1',
      text: '市场怎么看？',
      conversationId: 'report-report-1',
    });
  });
});
