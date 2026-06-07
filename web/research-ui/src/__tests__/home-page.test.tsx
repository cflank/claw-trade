import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { HomePage } from '../routes/HomePage';

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function mockWorkspaceFetch(
  options: {
    confirmImmediateFailure?: boolean;
    confirmWithoutMessages?: boolean;
    channelChatSnapshot?: unknown | (() => unknown);
    selectionRefreshSnapshot?: unknown | (() => unknown);
    selectSendResponse?: Promise<Response>;
  } = {},
) {
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

    if (url.includes('/api/ui/get-channel-chat-snapshot')) {
      const snapshot =
        typeof options.channelChatSnapshot === 'function'
          ? options.channelChatSnapshot()
          : options.channelChatSnapshot;
      return json(snapshot ?? { channelKind: 'wechat_clawbot', messages: [], confirmationCards: {} });
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

    if (url.includes('/api/ui/load-llm-settings')) {
      return json({
        draft: {
          provider: 'deepseek',
          apiKeyMasked: 'sk-****',
          endpointUrl: 'https://api.example.com',
          defaultModel: 'deepseek-chat',
          status: 'saved',
          reportModelStatus: {
            state: 'ready',
            blocked: false,
            ready: true,
            userMessage: '报告模型可用。',
            checkedAt: '2026-05-20T10:00:00Z',
          },
        },
        schemaVersion: 'v1',
        settingsVersion: 's1',
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

    if (url.includes('/api/ui/get-selection-refresh-snapshot')) {
      const snapshot =
        typeof options.selectionRefreshSnapshot === 'function'
          ? options.selectionRefreshSnapshot()
          : options.selectionRefreshSnapshot;
      return json(snapshot ?? { selectionProgress: null });
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
      if (body.text.trim().toLowerCase().startsWith('/select') && options.selectSendResponse) {
        return options.selectSendResponse;
      }
      if (body.text.includes('/report BTC')) {
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
              text: '请确认是否创建完整报告',
              cardId: 'card-1',
              createdAt: '2026-05-19T10:09:01.000Z',
            },
          ],
          confirmationCard: {
            id: 'card-1',
            draftId: 'draft-1',
            title: '请确认是否创建完整报告',
            summaryLines: ['标的：BTC', '名称：Bitcoin', '市场：CRYPTO'],
            instrumentCode: 'BTC',
            instrumentName: 'Bitcoin',
            market: 'CRYPTO',
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
              text: '报告已完成。\n最终结论：维持观察，等待突破确认。\n核心理由：日线趋势改善\n主要风险：估值波动',
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
    vi.useRealTimers();
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

  it('shows the report instrument format hint near the chat input only', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const hint = await screen.findByText(
      '格式提示：A股 600519.SH；港股 00700.HK；美股 AAPL；加密 AR/USDT。裸 AR 按美股，写加密请用 AR/USDT。',
    );
    expect(hint).toBeInTheDocument();
    expect(screen.getByLabelText('输入消息')).toHaveAccessibleDescription(hint.textContent ?? '');

    const entry = await screen.findByRole('button', { name: /600519\.SH/ });
    fireEvent.click(entry);

    expect(await screen.findByTestId('reading-report-body')).toBeInTheDocument();
    expect(screen.queryByText(hint.textContent ?? '')).not.toBeInTheDocument();
  });

  it('shows backend selection data refresh progress in the right rail', async () => {
    const mocked = mockWorkspaceFetch({
      selectionRefreshSnapshot: {
        selectionProgress: {
          kind: 'data_refresh',
          status: 'running',
          statusLabel: '补数据中',
          command: '/select 2026-06-04',
          stageLabel: '拉取/补齐行情数据',
          currentAction: '正在读取本地仓库并补齐缺失行情。',
          percent: 35,
          workerStatusLabels: ['拉取/补齐行情数据：补数据中'],
          completedRoleLabels: ['排队准备', '申请执行锁', '数据作业启动'],
          waitingRoleLabels: ['标准化输入', '构建特征'],
          startedAt: '2026-06-04T10:00:00Z',
          finishedAt: null,
          workflowRunId: 'sel-refresh-active-1',
        },
      },
    });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('选股数据刷新')).toBeInTheDocument();
    expect(screen.getByText('/select 2026-06-04')).toBeInTheDocument();
    expect(screen.getByText('拉取/补齐行情数据')).toBeInTheDocument();
    expect(screen.getByText('正在读取本地仓库并补齐缺失行情。')).toBeInTheDocument();
    expect(screen.getByText('拉取/补齐行情数据：补数据中')).toBeInTheDocument();
    expect(screen.getByText('工作流：sel-refresh-active-1')).toBeInTheDocument();
  });

  it('keeps workspace visible and shows a model warning without onboarding dialogs', async () => {
    const originalFetch = globalThis.fetch;
    restoreList.push(() => {
      globalThis.fetch = originalFetch;
    });
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/list-saved-reports')) {
        return json({ items: [] });
      }
      if (url.includes('/api/ui/get-report-queue-snapshot')) {
        return json({ runningTask: null, queuedTasks: [], queueLimit: 10, queuedCount: 0, isFull: false });
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
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: null,
            endpointUrl: '',
            defaultModel: '',
            status: 'idle',
            reportModelStatus: {
              state: 'unconfigured',
              blocked: true,
              ready: false,
              userMessage: '请先在设置中填写报告模型（服务商、模型、API Key），并完成测试。',
              checkedAt: null,
            },
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('workspace-layout')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: '打开设备界面' })).toHaveAttribute('href', '/api/ui/open-device-interface');
    expect(screen.queryByTestId('report-model-onboarding-wrap')).not.toBeInTheDocument();
    expect(screen.queryByTestId('report-model-onboarding-dialog')).not.toBeInTheDocument();
    expect(screen.queryByTestId('report-model-onboarding')).not.toBeInTheDocument();
    expect(screen.queryByTestId('wechat-onboarding-dialog')).not.toBeInTheDocument();
    const warning = await screen.findByTestId('llm-config-warning');
    expect(within(warning).getByText('报告模型还没配置成功')).toBeInTheDocument();
    expect(within(warning).getByText('请先在设置中填写报告模型（服务商、模型、API Key），并完成测试。')).toBeInTheDocument();
    expect(within(warning).getByRole('link', { name: '去设置模型' })).toHaveAttribute('href', '/settings');
    const pageText = (document.body.textContent ?? '').toLowerCase();
    expect(pageText).not.toContain('provider 健康摘要');
    expect(pageText).not.toContain('运行服务状态摘要');
    expect(pageText).not.toContain('最近 live run 缺口摘要');
    expect(pageText).not.toContain('证据链失败原因摘要');
    expect(pageText).not.toContain('provider attempt');
    expect(pageText).not.toContain('raw payload');
    expect(pageText).not.toContain('uri');
    expect(pageText).not.toContain('hash');
    expect(pageText).not.toContain('l1');
    expect(pageText).not.toContain('l2');
    expect(pageText).not.toContain('receipt');
    expect(pageText).not.toContain('/runs/');
  });

  it('refreshes wechat status from the workspace timer without requesting qr login', async () => {
    const originalFetch = globalThis.fetch;
    const channelUrls: string[] = [];
    const intervalCallbacks: Array<() => void> = [];
    restoreList.push(() => {
      globalThis.fetch = originalFetch;
    });
    vi.spyOn(window, 'setInterval').mockImplementation(((handler: Parameters<typeof window.setInterval>[0]) => {
      if (typeof handler === 'function') {
        intervalCallbacks.push(() => handler());
      }
      return 1;
    }) as typeof window.setInterval);
    vi.spyOn(window, 'clearInterval').mockImplementation(() => undefined);
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/list-saved-reports')) {
        return json({ items: [] });
      }
      if (url.includes('/api/ui/get-report-queue-snapshot')) {
        return json({ runningTask: null, queuedTasks: [], queueLimit: 10, queuedCount: 0, isFull: false });
      }
      if (url.includes('/api/ui/get-channel-status')) {
        channelUrls.push(url);
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
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: null,
            endpointUrl: '',
            defaultModel: '',
            status: 'idle',
            reportModelStatus: {
              state: 'unconfigured',
              blocked: true,
              ready: false,
              userMessage: '请先在设置中填写报告模型（服务商、模型、API Key），并完成测试。',
              checkedAt: null,
            },
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('workspace-layout')).toBeInTheDocument();
    const channelRequestCount = channelUrls.length;
    expect(intervalCallbacks.length).toBeGreaterThan(0);

    await act(async () => {
      intervalCallbacks.at(0)?.();
    });

    expect(channelUrls.length).toBeGreaterThan(channelRequestCount);
    const periodicChannelUrls = channelUrls.slice(channelRequestCount);
    expect(periodicChannelUrls.some((url) => url.includes('/api/ui/get-channel-status'))).toBe(true);
    expect(periodicChannelUrls.every((url) => !url.includes('probe=true'))).toBe(true);
    expect(periodicChannelUrls.every((url) => !url.includes('includeQr=true'))).toBe(true);
    expect(periodicChannelUrls.every((url) => !url.includes('pollLogin=true'))).toBe(true);
    expect(periodicChannelUrls.every((url) => !url.includes('refreshQr=true'))).toBe(true);
  });

  it('does not pile up workspace channel status requests when one is still running', async () => {
    const originalFetch = globalThis.fetch;
    const channelUrls: string[] = [];
    const intervalCallbacks: Array<() => void> = [];
    restoreList.push(() => {
      globalThis.fetch = originalFetch;
    });
    vi.spyOn(window, 'setInterval').mockImplementation(((handler: Parameters<typeof window.setInterval>[0]) => {
      if (typeof handler === 'function') {
        intervalCallbacks.push(() => handler());
      }
      return 1;
    }) as typeof window.setInterval);
    vi.spyOn(window, 'clearInterval').mockImplementation(() => undefined);
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/list-saved-reports')) {
        return json({ items: [] });
      }
      if (url.includes('/api/ui/get-report-queue-snapshot')) {
        return json({ runningTask: null, queuedTasks: [], queueLimit: 10, queuedCount: 0, isFull: false });
      }
      if (url.includes('/api/ui/get-channel-status')) {
        channelUrls.push(url);
        return new Promise<Response>(() => undefined);
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            endpointUrl: 'https://api.example.com',
            defaultModel: 'deepseek-chat',
            status: 'saved',
            reportModelStatus: {
              state: 'ready',
              blocked: false,
              ready: true,
              userMessage: '报告模型可用。',
              checkedAt: '2026-05-23T12:00:00Z',
            },
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('workspace-layout')).toBeInTheDocument();
    await waitFor(() => expect(channelUrls).toHaveLength(1));

    await act(async () => {
      intervalCallbacks.at(0)?.();
      intervalCallbacks.at(0)?.();
    });

    expect(channelUrls).toHaveLength(1);
    expect(channelUrls[0]).toBe('/api/ui/get-channel-status');
  });

  it('does not open the wechat dialog when report model config already exists', async () => {
    const originalFetch = globalThis.fetch;
    const channelUrls: string[] = [];
    restoreList.push(() => {
      globalThis.fetch = originalFetch;
    });
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/list-saved-reports')) {
        return json({ items: [] });
      }
      if (url.includes('/api/ui/get-report-queue-snapshot')) {
        return json({ runningTask: null, queuedTasks: [], queueLimit: 10, queuedCount: 0, isFull: false });
      }
      if (url.includes('/api/ui/get-channel-status')) {
        channelUrls.push(url);
        return json({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'disconnected',
          displayName: '微信 ClawBot',
          accountLabel: null,
          canSendText: false,
          canSendFile: false,
          qrCodeImageDataUrl: null,
          qrCodeExpiresAt: null,
          qrCodeRefreshRequired: false,
        });
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            endpointUrl: 'https://api.example.com',
            defaultModel: 'deepseek-chat',
            status: 'saved',
            reportModelStatus: {
              state: 'ready',
              blocked: false,
              ready: true,
              userMessage: '报告模型可用。',
              checkedAt: '2026-05-23T12:00:00Z',
            },
            embedding: {
              provider: 'openai',
              model: '',
              endpointUrl: '',
              dimension: '',
              enabled: false,
            },
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('workspace-layout')).toBeInTheDocument();
    expect(screen.queryByTestId('report-model-onboarding')).not.toBeInTheDocument();
    expect(screen.queryByTestId('wechat-onboarding-dialog')).not.toBeInTheDocument();
    expect(screen.queryByAltText('微信登录二维码')).not.toBeInTheDocument();
    expect(channelUrls.some((url) => url.includes('includeQr=true'))).toBe(false);
    expect(channelUrls.some((url) => url.includes('refreshQr=true'))).toBe(false);
    const bodyText = document.body.textContent ?? '';
    expect(bodyText).not.toContain('OpenViking');
    expect(bodyText).not.toContain('gateway');
    expect(bodyText).not.toContain('运行服务状态摘要');
    expect(bodyText).not.toContain('证据链失败原因摘要');
    expect(bodyText).not.toContain('18789');
    expect(bodyText).not.toContain('1933');
    expect(channelUrls[0]).toBe('/api/ui/get-channel-status');
  });

  it('does not re-enable wechat automatically after the user disconnected it', async () => {
    const originalFetch = globalThis.fetch;
    const channelUrls: string[] = [];
    restoreList.push(() => {
      globalThis.fetch = originalFetch;
    });
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/list-saved-reports')) {
        return json({ items: [] });
      }
      if (url.includes('/api/ui/get-report-queue-snapshot')) {
        return json({ runningTask: null, queuedTasks: [], queueLimit: 10, queuedCount: 0, isFull: false });
      }
      if (url.includes('/api/ui/get-channel-status')) {
        channelUrls.push(url);
        return json({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'disconnected',
          displayName: '微信 ClawBot',
          accountLabel: null,
          canSendText: false,
          canSendFile: false,
          qrCodeImageDataUrl: null,
          qrCodeExpiresAt: null,
          qrCodeRefreshRequired: true,
          lastErrorMessage: '微信已解除连接，请点击刷新二维码重新扫码。',
        });
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            endpointUrl: 'https://api.example.com',
            defaultModel: 'deepseek-chat',
            status: 'saved',
            reportModelStatus: {
              state: 'ready',
              blocked: false,
              ready: true,
              userMessage: '报告模型可用。',
              checkedAt: '2026-05-23T12:00:00Z',
            },
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('workspace-layout')).toBeInTheDocument();
    expect(screen.queryByTestId('wechat-onboarding-dialog')).not.toBeInTheDocument();
    expect(screen.queryByAltText('微信登录二维码')).not.toBeInTheDocument();
    expect(channelUrls.some((url) => url.includes('includeQr=true'))).toBe(false);
    expect(channelUrls.some((url) => url.includes('refreshQr=true'))).toBe(false);
  });

  it('does not enable or refresh wechat from the home page when gateway is unavailable', async () => {
    const originalFetch = globalThis.fetch;
    const channelUrls: string[] = [];
    restoreList.push(() => {
      globalThis.fetch = originalFetch;
    });
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/list-saved-reports')) {
        return json({ items: [] });
      }
      if (url.includes('/api/ui/get-report-queue-snapshot')) {
        return json({ runningTask: null, queuedTasks: [], queueLimit: 10, queuedCount: 0, isFull: false });
      }
      if (url.includes('/api/ui/get-channel-status')) {
        channelUrls.push(url);
        return json({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'disconnected',
          displayName: '微信 ClawBot',
          accountLabel: null,
          canSendText: false,
          canSendFile: false,
          qrCodeImageDataUrl: null,
          qrCodeRefreshRequired: true,
          lastErrorMessage: '请先启用微信 ClawBot 插件。',
        });
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            endpointUrl: 'https://api.example.com',
            defaultModel: 'deepseek-chat',
            status: 'saved',
            reportModelStatus: {
              state: 'ready',
              blocked: false,
              ready: true,
              userMessage: '报告模型可用。',
              checkedAt: '2026-05-23T12:00:00Z',
            },
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('workspace-layout')).toBeInTheDocument();
    expect(screen.queryByTestId('wechat-onboarding-dialog')).not.toBeInTheDocument();
    expect(screen.queryByAltText('微信登录二维码')).not.toBeInTheDocument();
    expect(channelUrls.some((url) => url.includes('includeQr=true'))).toBe(false);
    expect(channelUrls.some((url) => url.includes('refreshQr=true'))).toBe(false);
    expect(screen.queryByText('服务暂时不可用，请稍后重试。')).not.toBeInTheDocument();
  });

  it('does not poll wechat login from the home page', async () => {
    const originalFetch = globalThis.fetch;
    const channelUrls: string[] = [];
    restoreList.push(() => {
      globalThis.fetch = originalFetch;
    });
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/list-saved-reports')) {
        return json({ items: [] });
      }
      if (url.includes('/api/ui/get-report-queue-snapshot')) {
        return json({ runningTask: null, queuedTasks: [], queueLimit: 10, queuedCount: 0, isFull: false });
      }
      if (url.includes('/api/ui/get-channel-status')) {
        channelUrls.push(url);
        return json({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'disconnected',
          displayName: '微信 ClawBot',
          accountLabel: null,
          canSendText: false,
          canSendFile: false,
          qrCodeImageDataUrl: null,
          qrCodeExpiresAt: null,
          qrCodeRefreshRequired: false,
        });
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            endpointUrl: 'https://api.example.com',
            defaultModel: 'deepseek-chat',
            status: 'saved',
            reportModelStatus: {
              state: 'ready',
              blocked: false,
              ready: true,
              userMessage: '报告模型可用。',
              checkedAt: '2026-05-23T12:00:00Z',
            },
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('workspace-layout')).toBeInTheDocument();
    expect(screen.queryByTestId('wechat-onboarding-dialog')).not.toBeInTheDocument();
    expect(screen.queryByAltText('微信登录二维码')).not.toBeInTheDocument();

    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 2100));
    });

    expect(channelUrls.some((url) => url.includes('pollLogin=true'))).toBe(false);
    expect(channelUrls.some((url) => url.includes('includeQr=true'))).toBe(false);
  }, 8000);

  it('does not open onboarding dialogs when report model and wechat are both configured', async () => {
    const originalFetch = globalThis.fetch;
    restoreList.push(() => {
      globalThis.fetch = originalFetch;
    });
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/list-saved-reports')) {
        return json({ items: [] });
      }
      if (url.includes('/api/ui/get-report-queue-snapshot')) {
        return json({ runningTask: null, queuedTasks: [], queueLimit: 10, queuedCount: 0, isFull: false });
      }
      if (url.includes('/api/ui/get-channel-status')) {
        return json({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'connected',
          displayName: '微信 ClawBot',
          accountLabel: '已绑定账号',
          canSendText: true,
          canSendFile: true,
        });
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            endpointUrl: 'https://api.example.com',
            defaultModel: 'deepseek-chat',
            status: 'saved',
            reportModelStatus: {
              state: 'ready',
              blocked: false,
              ready: true,
              userMessage: '报告模型可用。',
              checkedAt: '2026-05-23T12:00:00Z',
            },
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('workspace-layout')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.queryByTestId('report-model-onboarding-dialog')).not.toBeInTheDocument();
      expect(screen.queryByTestId('wechat-onboarding-dialog')).not.toBeInTheDocument();
    });
    expect(screen.getByRole('link', { name: '打开设备界面' })).toBeInTheDocument();
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
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            endpointUrl: 'https://api.example.com',
            defaultModel: 'deepseek-chat',
            status: 'saved',
            reportModelStatus: {
              state: 'ready',
              blocked: false,
              ready: true,
              userMessage: '报告模型可用。',
              checkedAt: '2026-05-20T10:00:00Z',
            },
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
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

  it('shows local progress while a select command is still running', async () => {
    let resolveSelect!: (response: Response) => void;
    const selectSendResponse = new Promise<Response>((resolve) => {
      resolveSelect = resolve;
    });
    const mocked = mockWorkspaceFetch({ selectSendResponse });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '/select' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(screen.getAllByText('/select').length).toBeGreaterThan(0));
    expect(await screen.findByText(/正在运行选股工作流/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '选股中' })).toBeDisabled();
    expect(screen.getByText('选股任务进度')).toBeInTheDocument();
    expect(screen.getByText('选股工作流执行中')).toBeInTheDocument();
    expect(screen.getByText('策略评审：已纳入本轮选股流程')).toBeInTheDocument();
    expect(screen.getByText('组合经理：已纳入本轮选股流程')).toBeInTheDocument();

    await act(async () => {
      resolveSelect(
        json({
          context: {
            contextId: 'normal-chat',
            kind: 'normal_chat',
            title: '普通聊天',
            activeTaskId: null,
            activeReportId: null,
          },
          messages: [
            {
              messageId: 'msg-select-user',
              contextKind: 'normal_chat',
              actor: 'user',
              kind: 'plain',
              text: '/select',
              createdAt: '2026-05-19T10:08:00.000Z',
            },
            {
              messageId: 'msg-select-result',
              contextKind: 'normal_chat',
              actor: 'system',
              kind: 'selection_result',
              text:
                '`/select` 已完成，本轮仅进入等待确认，不会自动启动 `/report`。\n\n简报：\n- 进入 `/report`：600519.SH 贵州茅台\n- 观察：000858.SZ 五粮液\n- 放弃：300750.SZ 宁德时代\n\n进入 `/report`：\n- 600519.SH 贵州茅台：经营质量与现金流稳定，值得进入深度报告验证。\n\n观察：\n- 000858.SZ 五粮液：还需后续财报与景气数据确认。\n\n放弃：\n- 300750.SZ 宁德时代：当前证据链分歧较大且不够完整。',
              createdAt: '2026-05-19T10:09:00.000Z',
            },
          ],
          selection: {
            code: 'completed',
            workflowRunId: 'select-test-run',
            evidencePath: 'runs/selection/workflows/select-test-run/selection-workflow-evidence.json',
            unavailableCode: null,
            failureReason: null,
            readerReportMarkdown:
              '# 选股结果报告\n\n## 执行结论\n本轮 `/select` 已完成。\n\n## 候选事实表\n| 排名 | 股票代码 | 股票名称 | 总分 | 实际指标值 |\n| --- | --- | --- | ---: | --- |\n| 1 | 600519.SH | 贵州茅台 | 91 | ROE 31%，成交额 12 亿元，收盘价 1680 元 |\n\n## 数据质量摘要\n数据覆盖可读。',
          },
        }),
      );
      await selectSendResponse;
    });

    await waitFor(() => expect(screen.getByRole('button', { name: '发送' })).toBeDisabled());
    expect(screen.getByLabelText('输入消息')).toBeEnabled();
    expect(screen.getByText(/已完成，本轮仅进入等待确认/)).toBeInTheDocument();
    expect(screen.queryByText('选股完成')).not.toBeInTheDocument();
    expect(screen.queryByText('等待确认候选')).not.toBeInTheDocument();
    expect(screen.queryByText('选股任务进度')).not.toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看完整选股报告（含策略分析）' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /选股结果报告/ })).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '查看完整选股报告（含策略分析）' }));
    const selectionReport = await screen.findByTestId('reading-selection-report-body');
    expect(selectionReport).toHaveTextContent('候选事实表');
    expect(selectionReport).toHaveTextContent('600519.SH');
    expect(selectionReport).not.toHaveTextContent('策略配置版本');
    expect(selectionReport).not.toHaveTextContent('权重版本');
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
    fireEvent.change(input, { target: { value: '/report BTC' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    const cardTitle = await screen.findByText('请确认是否创建完整报告');
    const card = cardTitle.closest('section');
    expect(card).not.toBeNull();
    const scoped = within(card as HTMLElement);
    expect(scoped.getByText('标的')).toBeInTheDocument();
    expect(scoped.getByText('BTC')).toBeInTheDocument();
    expect(scoped.getByRole('combobox', { name: '市场' })).toHaveValue('CRYPTO');

    fireEvent.click(screen.getByRole('button', { name: '确认' }));
    expect(await screen.findByText('报告已进入队列。')).toBeInTheDocument();
    expect(mocked.getConfirmBodies().at(0)?.decision).toBe('confirm');

    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    const secondInput = screen.getByLabelText('输入消息');
    fireEvent.change(secondInput, { target: { value: '/report BTC' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    expect(await screen.findByText('请确认是否创建完整报告')).toBeInTheDocument();
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
    fireEvent.change(input, { target: { value: '/report BTC' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('请确认是否创建完整报告')).toBeInTheDocument();
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
    fireEvent.change(input, { target: { value: '/report BTC' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('请确认是否创建完整报告')).toBeInTheDocument();
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

    expect(await screen.findByText(/最终结论：维持观察/)).toBeInTheDocument();
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

  it('keeps report reading visible while channel chat polling receives messages', async () => {
    let channelHasMessages = false;
    const intervalCallbacks: Array<() => void> = [];
    const mocked = mockWorkspaceFetch({
      channelChatSnapshot: () =>
        channelHasMessages
          ? {
              channelKind: 'wechat_clawbot',
              context: {
                contextId: 'wechat_clawbot:account-1:sender-1',
                kind: 'normal_chat',
                title: '微信聊天',
                activeTaskId: null,
                activeReportId: null,
              },
              messages: [
                {
                  messageId: 'wx-u1',
                  contextKind: 'normal_chat',
                  actor: 'user',
                  kind: 'plain',
                  text: '微信里发来的问题',
                  createdAt: '2026-05-19T10:10:00.000Z',
                },
              ],
              confirmationCards: {},
            }
          : { channelKind: 'wechat_clawbot', messages: [], confirmationCards: {} },
    });
    restoreList.push(mocked.restore);
    vi.spyOn(window, 'setInterval').mockImplementation(((handler: Parameters<typeof window.setInterval>[0]) => {
      if (typeof handler === 'function') {
        intervalCallbacks.push(() => handler());
      }
      return 1;
    }) as typeof window.setInterval);
    vi.spyOn(window, 'clearInterval').mockImplementation(() => undefined);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const entry = await screen.findByRole('button', { name: /600519\.SH/ });
    fireEvent.click(entry);
    expect(await screen.findByTestId('reading-report-body')).toBeInTheDocument();

    channelHasMessages = true;
    await act(async () => {
      intervalCallbacks.at(0)?.();
    });

    expect(screen.getByTestId('reading-report-body')).toBeInTheDocument();
    expect(screen.queryByText('微信里发来的问题')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '返回聊天' }));
    await act(async () => {
      intervalCallbacks.at(0)?.();
    });

    expect(await screen.findByText('微信里发来的问题')).toBeInTheDocument();
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
    const intervalCallbacks: Array<() => void> = [];
    restoreList.push(mocked.restore);
    vi.spyOn(window, 'setInterval').mockImplementation(((handler: Parameters<typeof window.setInterval>[0]) => {
      if (typeof handler === 'function') {
        intervalCallbacks.push(() => handler());
      }
      return 1;
    }) as typeof window.setInterval);
    vi.spyOn(window, 'clearInterval').mockImplementation(() => undefined);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    await screen.findByTestId('workspace-layout');
    expect(screen.getByText(/投资辩论中/)).toBeInTheDocument();
    const initialCount = mocked.getQueueCount();
    expect(intervalCallbacks.length).toBeGreaterThan(0);

    await act(async () => {
      intervalCallbacks.at(0)?.();
    });
    await waitFor(() => {
      expect(mocked.getQueueCount()).toBeGreaterThan(initialCount);
    });
  });

  it('shows latest wechat channel conversation in the message stream', async () => {
    const mocked = mockWorkspaceFetch({
      channelChatSnapshot: {
        channelKind: 'wechat_clawbot',
        context: {
          contextId: 'wechat_clawbot:account-1:sender-1',
          kind: 'normal_chat',
          title: '微信聊天',
          activeTaskId: null,
          activeReportId: null,
        },
        messages: [
          {
            messageId: 'wx-u1',
            contextKind: 'normal_chat',
            actor: 'user',
            kind: 'plain',
            text: '微信里发来的问题',
            createdAt: '2026-05-19T10:10:00.000Z',
          },
          {
            messageId: 'wx-a1',
            contextKind: 'normal_chat',
            actor: 'assistant',
            kind: 'plain',
            text: '微信通道回复',
            createdAt: '2026-05-19T10:10:02.000Z',
          },
        ],
        confirmationCards: {},
      },
    });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('微信里发来的问题')).toBeInTheDocument();
    expect(screen.getByText('微信通道回复')).toBeInTheDocument();
    expect(screen.getByText('微信聊天')).toBeInTheDocument();
  });
});
