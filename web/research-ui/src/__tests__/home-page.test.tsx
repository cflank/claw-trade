import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { HomePage } from '../routes/HomePage';

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function mockWorkspaceFetch(options: { confirmImmediateFailure?: boolean; confirmWithoutMessages?: boolean } = {}) {
  const originalFetch = globalThis.fetch;
  let queueCount = 0;
  const confirmBodies: Array<Record<string, unknown>> = [];
  const deleteBodies: Array<Record<string, unknown>> = [];

  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);

    if (url.includes('/api/ui/list-saved-reports')) {
      return json({
        items: [
          {
            id: 'report-1',
            instrumentCode: '600519.SH',
            instrumentName: '贵州茅台',
            market: 'CN_A',
            title: '茅台投研报告',
            generatedAt: '2026-05-19T10:00:00.000Z',
            summarySnippet: '结论偏积极，关注估值与渠道恢复。',
          },
        ],
      });
    }

    if (url.includes('/api/ui/get-channel-status')) {
      return json({
        channelKind: 'wechat_clawbot',
        onboardingState: 'completed',
        state: 'disconnected',
        displayName: '微信 ClawBot',
        accountLabel: null,
        canSendText: false,
        canSendFile: false,
      });
    }

    if (url.includes('/api/ui/get-report-queue-snapshot')) {
      queueCount += 1;
      return json({
        runningTask: {
          taskId: 'task-1',
          source: 'manual',
          status: 'running',
          statusLabel: '生成中',
          instrumentCode: '600519.SH',
          market: 'CN_A',
          companyName: '贵州茅台',
          currencySymbol: '¥',
          startDate: '2026-05-01',
          endDate: '2026-05-19',
          currentDate: '2026-05-19',
          queuePosition: null,
          progress: {
            percent: queueCount > 1 ? 55 : 42,
            stageLabel: '投资辩论中',
            roleLabel: '多头研究员',
            currentAction: '正在整理观点交锋结论',
            completedRoleLabels: ['市场分析员', '基本面分析员'],
            waitingRoleLabels: ['空头研究员', '研究经理'],
            workerStatusLabels: ['市场分析员：已完成', '多头研究员：执行中', '空头研究员：等待启动'],
          },
          createdAt: '2026-05-19T09:55:00.000Z',
        },
        queuedTasks: [],
        queueLimit: 10,
        queuedCount: 0,
        isFull: false,
      });
    }

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
        markdown:
          '# 投资结论\n\n建议继续跟踪渠道修复。\n\n![趋势图](/api/ui/get-report-asset?reportId=report-1&assetPath=assets%2Fchart.png)\n\n| 指标 | 结论 |\n| --- | --- |\n| 渠道 | 修复中 |',
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
          pdfAvailable: true,
          createdAt: '2026-05-19T10:00:00.000Z',
        },
        dataSourceEvents: [],
        chartEvidence: { summary: 'ready', items: [] },
        assets: [
          { kind: 'markdown', available: true, status: 'ready', updatedAt: '2026-05-19T10:00:00.000Z' },
          { kind: 'pdf', available: true, status: 'ready', updatedAt: '2026-05-19T10:00:00.000Z' },
        ],
      });
    }

    if (url.includes('/api/ui/get-report-chart-evidence')) {
      return json({
        reportId: 'report-1',
        summary: 'partial',
        items: [
          {
            id: 'chart-1',
            reportId: 'report-1',
            chartType: 'kline',
            title: '日线图',
            status: 'ready',
            userMessage: '已包含图表',
            capturedAt: '2026-05-19T10:00:00.000Z',
          },
        ],
      });
    }

    if (url.includes('/api/ui/ask-report-question') && init?.method === 'POST') {
      return json({ text: '**当前结论**来自已保存报告正文。\n\n- 继续跟踪渠道修复。' });
    }

    if (url.includes('/api/ui/delete-saved-report') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      deleteBodies.push(body);
      return json({ deleted: true, reportId: body.reportId });
    }

    if (url.includes('/api/ui/send-chat-message') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as { contextId: string; text: string };
      if (body.text.includes('做一份 BTC 报告')) {
        return json({
          context: {
            contextId: body.contextId,
            kind: 'intent_confirming',
            title: '确认任务',
            activeTaskId: null,
            activeReportId: null,
          },
          messages: [
            {
              messageId: 'msg-u2',
              contextKind: 'intent_confirming',
              actor: 'user',
              kind: 'plain',
              text: body.text,
              createdAt: '2026-05-19T10:09:00.000Z',
            },
            {
              messageId: 'msg-s2',
              contextKind: 'intent_confirming',
              actor: 'system',
              kind: 'confirmation_card',
              text: '请确认是否创建投研报告',
              cardId: 'card-1',
              createdAt: '2026-05-19T10:09:01.000Z',
            },
          ],
          confirmationCard: {
            id: 'card-1',
            draftId: 'draft-1',
            title: '请确认是否创建投研报告',
            summaryLines: ['类型：投研报告', '标的：BTC', '市场：CRYPTO', '通知方式：站内提醒'],
            dataSourceSummary: 'partial',
            actions: ['confirm', 'cancel'],
            status: 'active',
            createdAt: '2026-05-19T10:09:01.000Z',
          },
          queueSnapshot: {
            runningTask: null,
            queuedTasks: [],
            queueLimit: 10,
            queuedCount: 0,
            isFull: false,
          },
        });
      }
      if (body.text.includes('展示完成卡')) {
        return json({
          context: {
            contextId: body.contextId,
            kind: 'task_following',
            title: '任务跟进',
            activeTaskId: 'task-2',
            activeReportId: null,
          },
          messages: [
            {
              messageId: 'msg-r1',
              contextKind: 'task_following',
              actor: 'system',
              kind: 'report_completed',
              text: '报告已完成，可查看完整内容。',
              reportId: 'report-1',
              createdAt: '2026-05-19T10:10:00.000Z',
            },
          ],
        });
      }
      return json({
        context: {
          contextId: body.contextId,
          kind: 'normal_chat',
          title: '普通聊天',
          activeTaskId: null,
          activeReportId: null,
        },
        messages: [
          {
            messageId: 'msg-u1',
            contextKind: 'normal_chat',
            actor: 'user',
            kind: 'plain',
            text: body.text,
            createdAt: '2026-05-19T10:08:00.000Z',
          },
          {
            messageId: 'msg-a1',
            contextKind: 'normal_chat',
            actor: 'assistant',
            kind: 'plain',
            text: '收到，正在分析。',
            createdAt: '2026-05-19T10:08:01.000Z',
          },
        ],
        queueSnapshot: {
          runningTask: null,
          queuedTasks: [],
          queueLimit: 10,
          queuedCount: 0,
          isFull: false,
        },
      });
    }

    if (url.includes('/api/ui/confirm-intent-draft') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      confirmBodies.push(body);
      if (body.decision === 'cancel') {
        return json({
          status: 'cancelled',
          messages: [
            {
              messageId: 'msg-cancel',
              contextKind: 'normal_chat',
              actor: 'system',
              kind: 'plain',
              text: '已取消本次创建。',
              createdAt: '2026-05-19T10:09:09.000Z',
            },
          ],
        });
      }
      if (options.confirmImmediateFailure) {
        return json({
          status: 'confirmed',
          task: {
            taskId: 'task-failed',
            source: 'manual',
            status: 'failed',
            statusLabel: '失败',
            instrumentCode: 'BTC',
            instrumentName: 'Bitcoin',
            market: 'CRYPTO',
            companyName: 'Bitcoin',
            currencySymbol: '$',
            startDate: '2026-05-01',
            endDate: '2026-05-19',
            currentDate: '2026-05-19',
            queuePosition: null,
            progress: null,
            reportId: null,
            failure: {
              code: 'ASSISTANT_UNAVAILABLE',
              message: '助手服务暂不可用，请稍后重试。',
              severity: 'error',
            },
            createdAt: '2026-05-19T10:09:10.000Z',
            finishedAt: '2026-05-19T10:09:10.000Z',
          },
          queueSnapshot: {
            runningTask: null,
            queuedTasks: [],
            queueLimit: 10,
            queuedCount: 0,
            isFull: false,
          },
        });
      }
      const payload = {
        status: 'confirmed',
        task: {
          taskId: 'task-2',
          source: 'manual',
          status: 'queued',
          statusLabel: '排队中',
          instrumentCode: 'BTC',
          instrumentName: 'Bitcoin',
          market: 'CRYPTO',
          companyName: 'Bitcoin',
          currencySymbol: '$',
          startDate: '2026-05-01',
          endDate: '2026-05-19',
          currentDate: '2026-05-19',
          queuePosition: 1,
          progress: null,
          reportId: null,
          createdAt: '2026-05-19T10:09:10.000Z',
        },
        queueSnapshot: {
          runningTask: null,
          queuedTasks: [],
          queueLimit: 10,
          queuedCount: 1,
          isFull: false,
        },
        messages: [
          {
            messageId: 'msg-queued',
            contextKind: 'task_following',
            actor: 'system',
            kind: 'task_progress',
            text: '报告已进入队列。',
            createdAt: '2026-05-19T10:09:11.000Z',
          },
        ],
      };
      if (options.confirmWithoutMessages) {
        delete (payload as { messages?: unknown }).messages;
      }
      return json(payload);
    }

    return json({});
  }) as typeof fetch;

  return {
    restore: () => {
      globalThis.fetch = originalFetch;
    },
    getQueueCount: () => queueCount,
    getConfirmBodies: () => confirmBodies,
    getDeleteBodies: () => deleteBodies,
  };
}

describe('home page', () => {
  const restoreList: Array<() => void> = [];

  afterEach(() => {
    while (restoreList.length) {
      const restore = restoreList.pop();
      restore?.();
    }
    vi.restoreAllMocks();
  });

  it('renders three-column workspace with history, message stream and right rail', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('workspace-layout')).toBeInTheDocument();
    expect(screen.getByTestId('history-rail')).toBeInTheDocument();
    expect(screen.getByTestId('message-stream')).toBeInTheDocument();
    expect(screen.getByTestId('right-rail')).toBeInTheDocument();
    expect(screen.getByText(/投资辩论中/)).toBeInTheDocument();
    expect(screen.getByText('多头研究员')).toBeInTheDocument();
    expect(screen.getByText('多头研究员：执行中')).toBeInTheDocument();
  });

  it('keeps the chat window visible when channel status is slow', async () => {
    const originalFetch = globalThis.fetch;
    restoreList.push(() => {
      globalThis.fetch = originalFetch;
    });
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/get-channel-status')) {
        return new Promise<Response>(() => undefined);
      }
      if (url.includes('/api/ui/list-saved-reports')) {
        return json({ items: [] });
      }
      if (url.includes('/api/ui/get-report-queue-snapshot')) {
        return json({
          runningTask: null,
          queuedTasks: [],
          queueLimit: 10,
          queuedCount: 0,
          isFull: false,
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('message-stream')).toBeInTheDocument();
    expect(await screen.findByLabelText('输入消息')).toBeInTheDocument();
    expect(screen.queryByText('加载中...')).not.toBeInTheDocument();
  });

  it('sends chat message and renders assistant reply', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '帮我看下茅台' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('收到，正在分析。')).toBeInTheDocument();
    expect(screen.getByText('帮我看下茅台')).toBeInTheDocument();
  });

  it('renders confirmation card and supports confirm/cancel actions', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '帮我做一份 BTC 报告' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('请确认是否创建投研报告')).toBeInTheDocument();
    expect(screen.getByText('标的：BTC')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '确认' }));
    expect(await screen.findByText('报告已进入队列。')).toBeInTheDocument();
    expect(mocked.getConfirmBodies().at(0)?.decision).toBe('confirm');

    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    const secondInput = screen.getByLabelText('输入消息');
    fireEvent.change(secondInput, { target: { value: '帮我做一份 BTC 报告' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(await screen.findByText('请确认是否创建投研报告')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '取消' }));
    expect(await screen.findByText('已取消本次创建。')).toBeInTheDocument();
    expect(mocked.getConfirmBodies().at(1)?.decision).toBe('cancel');
  });

  it('shows local task-start feedback when confirm response has no messages', async () => {
    const mocked = mockWorkspaceFetch({ confirmWithoutMessages: true });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '帮我做一份 BTC 报告' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('请确认是否创建投研报告')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    expect(await screen.findByText('报告已进入队列。')).toBeInTheDocument();
    expect(mocked.getConfirmBodies().at(0)?.decision).toBe('confirm');
  });

  it('shows an explicit failure when a confirmed report task fails immediately', async () => {
    const mocked = mockWorkspaceFetch({ confirmImmediateFailure: true });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '帮我做一份 BTC 报告' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('请确认是否创建投研报告')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '确认' }));

    expect(await screen.findAllByText('助手服务暂不可用，请稍后重试。')).toHaveLength(2);
    expect(screen.getByText('系统 · 报告失败')).toBeInTheDocument();
  });

  it('opens completed report inside workspace instead of jumping to standalone report page', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '展示完成卡' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('报告已完成，可查看完整内容。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看完整报告' }));
    expect(await screen.findByTestId('reading-report-body')).toBeInTheDocument();
    expect(screen.queryByTestId('report-detail-page')).not.toBeInTheDocument();
  });

  it('opens reading mode and supports report follow-up question', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const entry = await screen.findByRole('button', { name: /600519\.SH/ });
    fireEvent.click(entry);

    expect(await screen.findByTestId('reading-report-body')).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '趋势图' })).toHaveAttribute(
      'src',
      '/api/ui/get-report-asset?reportId=report-1&assetPath=assets%2Fchart.png',
    );
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '指标' })).toBeInTheDocument();
    expect(screen.getByTestId('reading-qa-list')).toBeInTheDocument();

    const askInput = screen.getByLabelText('输入消息');
    fireEvent.change(askInput, { target: { value: '核心结论是什么？' } });
    fireEvent.click(screen.getByRole('button', { name: '追问' }));

    expect(await screen.findByText('核心结论是什么？')).toBeInTheDocument();
    expect(screen.getByText('你')).toBeInTheDocument();
    expect(screen.getByText('助手')).toBeInTheDocument();
    const emphasized = await screen.findByText('当前结论');
    expect(emphasized.tagName).toBe('STRONG');
    expect(screen.getByText('继续跟踪渠道修复。')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '返回聊天' }));

    expect(screen.queryByTestId('reading-report-body')).not.toBeInTheDocument();
    expect(screen.getByTestId('message-stream')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('输入问题，或提交报告任务需求')).toBeInTheDocument();
  });

  it('prints the active report so the browser can save it as PDF', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);
    const printSpy = vi.spyOn(window, 'print').mockImplementation(() => undefined);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const entry = await screen.findByRole('button', { name: /600519\.SH/ });
    fireEvent.click(entry);
    const printButton = await screen.findByRole('button', { name: '保存为 PDF' });
    fireEvent.click(printButton);

    expect(printSpy).toHaveBeenCalledTimes(1);
  });

  it('removes a saved report from history after confirmation', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const deleteButton = await screen.findByRole('button', { name: /删除 茅台投研报告/ });
    fireEvent.click(deleteButton);

    await waitFor(() => {
      expect(mocked.getDeleteBodies().at(0)?.reportId).toBe('report-1');
    });
    expect(screen.queryByText('茅台投研报告')).not.toBeInTheDocument();
  });

  it('opens report in workspace when reportId query is provided', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter initialEntries={['/?reportId=report-1']}>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('reading-report-body')).toBeInTheDocument();
  });

  it('polls queue and history instead of loading only once', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    await screen.findByTestId('workspace-layout');
    expect(screen.getByText(/投资辩论中/)).toBeInTheDocument();
    const initialCount = mocked.getQueueCount();

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 4300));
    });
    expect(mocked.getQueueCount()).toBeGreaterThan(initialCount);
  }, 12000);
});
