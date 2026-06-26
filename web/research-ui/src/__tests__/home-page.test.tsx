import { afterEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { HomePage } from '../routes/HomePage';

const WORKERS = [
  { workerId: 'portfolio_manager', displayName: '组合经理', default: true, aliases: ['组合经理', 'PM'] },
  { workerId: 'research_manager', displayName: '研究经理', default: false, aliases: ['研究经理'] },
  { workerId: 'market_analyst', displayName: '市场分析师', default: false, aliases: ['市场分析师', '市场'] },
  { workerId: 'fundamental_analyst', displayName: '基本面分析师', default: false, aliases: ['基本面分析师', '基本面'] },
  { workerId: 'news_analyst', displayName: '新闻分析师', default: false, aliases: ['新闻分析师', '新闻'] },
  { workerId: 'social_analyst', displayName: '情绪分析师', default: false, aliases: ['情绪分析师', '情绪'] },
  { workerId: 'risk_moderator', displayName: '风险经理', default: false, aliases: ['风险经理', '风险'] },
];

const DEFAULT_SAVED_REPORTS = [
  {
    id: 'report-1',
    instrumentCode: '600519.SH',
    instrumentName: '贵州茅台',
    market: 'CN_A',
    title: '茅台投研报告',
    generatedAt: '2026-05-19T10:00:00.000Z',
    summarySnippet: '结论偏积极，关注估值与渠道恢复。',
  },
];

type MockSavedReport = (typeof DEFAULT_SAVED_REPORTS)[number] & { canForwardToChannel?: boolean };
type MockDeleteSavedReportResponse = {
  deleted: boolean;
  reportId: unknown;
  userMessage?: string;
  cleanup?: {
    deletedRunIds: string[];
    skippedRunIds: string[];
    failedRunIds: string[];
    deletedBytesApprox: number;
    warnings: string[];
  };
};

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
    chatSessionSnapshot?: unknown | (() => unknown);
    selectionRefreshSnapshot?: unknown | (() => unknown);
    selectSendResponse?: Promise<Response>;
    chatMessageResponse?: Promise<Response>;
    workerChatResponse?: Promise<Response>;
    workerChatListFails?: boolean;
    workerChatWorkers?: typeof WORKERS;
    savedReports?: MockSavedReport[];
    deleteSavedReportResponses?: Record<string, MockDeleteSavedReportResponse>;
    deleteSavedReportFailures?: Record<string, Response | Error>;
    channelStatus?: Record<string, unknown>;
    sendReportFileResponse?: Promise<Response>;
  } = {},
) {
  const originalFetch = globalThis.fetch;
  let queueCount = 0;
  const confirmBodies: Array<Record<string, unknown>> = [];
  const cancelBodies: Array<Record<string, unknown>> = [];
  const cancelSelectionBodies: Array<Record<string, unknown>> = [];
  const deleteBodies: Array<Record<string, unknown>> = [];
  const clearChatBodies: Array<Record<string, unknown>> = [];
  const chatBodies: Array<Record<string, unknown>> = [];
  const workerChatBodies: Array<Record<string, unknown>> = [];
  const askReportQuestionBodies: Array<Record<string, unknown>> = [];
  const sendReportFileBodies: Array<Record<string, unknown>> = [];

  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);

    if (url.includes('/api/ui/list-saved-reports')) {
      return json({
        items: options.savedReports ?? DEFAULT_SAVED_REPORTS,
      });
    }

    if (url.includes('/api/ui/get-channel-chat-snapshot')) {
      const snapshot =
        typeof options.channelChatSnapshot === 'function'
          ? options.channelChatSnapshot()
          : options.channelChatSnapshot;
      return json(snapshot ?? { channelKind: 'wechat_clawbot', messages: [], confirmationCards: {} });
    }

    if (url.includes('/api/ui/get-chat-session')) {
      const snapshot =
        typeof options.chatSessionSnapshot === 'function'
          ? options.chatSessionSnapshot()
          : options.chatSessionSnapshot;
      return json(
        snapshot ?? {
          context: {
            contextId: 'normal-chat',
            kind: 'normal_chat',
            title: '普通聊天',
            activeTaskId: null,
            activeReportId: null,
          },
          messages: [],
        },
      );
    }

    if (url.includes('/api/ui/get-channel-status')) {
      return json(
        options.channelStatus ?? {
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'disconnected',
          displayName: '微信 ClawBot',
          accountLabel: null,
          canSendText: false,
          canSendFile: false,
        },
      );
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

    if (url.includes('/api/ui/list-worker-chat-workers')) {
      if (options.workerChatListFails) {
        return new Response(JSON.stringify({ message: 'worker menu failed' }), {
          status: 500,
          headers: { 'Content-Type': 'application/json' },
        });
      }
      return json({ workers: options.workerChatWorkers ?? WORKERS });
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
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      askReportQuestionBodies.push(body);
      return json({ text: '**当前结论**来自已保存报告正文。\n\n- 继续跟踪渠道修复。' });
    }

    if (url.includes('/api/ui/delete-saved-reports') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      deleteBodies.push(body);
      const reportIds = Array.isArray(body.reportIds) ? body.reportIds.map((item) => String(item)) : [];
      for (const reportId of reportIds) {
        const configuredFailure = options.deleteSavedReportFailures?.[reportId];
        if (configuredFailure instanceof Error) {
          throw configuredFailure;
        }
        if (configuredFailure) {
          return configuredFailure;
        }
      }

      const runs = reportIds.map((reportId) => {
        const configuredResponse = options.deleteSavedReportResponses?.[reportId];
        if (!configuredResponse || configuredResponse.deleted) {
          return {
            reportId,
            status: 'deleted',
            deletedBytesApprox: configuredResponse?.cleanup?.deletedBytesApprox ?? 128,
            warnings: configuredResponse?.cleanup?.warnings ?? [],
            userMessage: configuredResponse?.userMessage ?? '报告已删除。',
          };
        }
        const cleanup = configuredResponse.cleanup;
        const status = cleanup?.failedRunIds.includes(reportId) ? 'failed' : 'skipped';
        return {
          reportId,
          status,
          deletedBytesApprox: cleanup?.deletedBytesApprox ?? 0,
          warnings: cleanup?.warnings ?? [],
          userMessage: configuredResponse.userMessage ?? '',
        };
      });
      return json({
        deletedRunIds: runs.filter((item) => item.status === 'deleted').map((item) => item.reportId),
        skippedRunIds: runs.filter((item) => item.status === 'skipped').map((item) => item.reportId),
        failedRunIds: runs.filter((item) => item.status === 'failed').map((item) => item.reportId),
        deletedBytesApprox: runs.reduce((total, item) => total + item.deletedBytesApprox, 0),
        warnings: runs.flatMap((item) => item.warnings),
        userMessage: '批量删除已处理。',
        runs,
      });
    }

    if (url.includes('/api/ui/delete-saved-report') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      deleteBodies.push(body);
      const configuredFailure = options.deleteSavedReportFailures?.[String(body.reportId)];
      if (configuredFailure instanceof Error) {
        throw configuredFailure;
      }
      if (configuredFailure) {
        return configuredFailure;
      }
      const configuredResponse = options.deleteSavedReportResponses?.[String(body.reportId)];
      return json(
        configuredResponse ?? {
          deleted: true,
          reportId: body.reportId,
          userMessage: '报告已删除。',
          cleanup: {
            deletedRunIds: [String(body.reportId)],
            skippedRunIds: [],
            failedRunIds: [],
            deletedBytesApprox: 128,
            warnings: [],
          },
        },
      );
    }

    if (url.includes('/api/ui/cancel-report-task') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      cancelBodies.push(body);
      return json({
        task: {
          taskId: body.taskId,
          source: 'manual',
          status: 'cancelled',
          statusLabel: '已取消',
          instrumentCode: '600519.SH',
          market: 'CN_A',
          companyName: '贵州茅台',
          currencySymbol: '¥',
          startDate: '2026-05-01',
          endDate: '2026-05-19',
          currentDate: '2026-05-19',
          queuePosition: null,
          createdAt: '2026-05-19T09:55:00.000Z',
          finishedAt: '2026-05-19T10:00:00.000Z',
        },
        queueSnapshot: {
          runningTask: null,
          queuedTasks: [],
          lastTerminalTask: null,
          queueLimit: 10,
          queuedCount: 0,
          isFull: false,
        },
        message: '已停止报告任务。',
      });
    }

    if (url.includes('/api/ui/cancel-selection-progress') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      cancelSelectionBodies.push(body);
      return json({ cancelled: true, selectionProgress: null, message: '已停止选股任务。' });
    }

    if (url.includes('/api/ui/send-report-file-via-channel') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      sendReportFileBodies.push(body);
      return options.sendReportFileResponse ?? json({ sent: true, messageId: 'msg-1', userMessage: '完整报告已发送。' });
    }

    if (url.includes('/api/ui/send-worker-chat') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      workerChatBodies.push(body);
      if (options.workerChatResponse) {
        return options.workerChatResponse;
      }
      const worker = WORKERS.find((item) => item.workerId === body.workerId);
      return json({
        kind: 'worker_chat_reply',
        workerDisplayName: worker?.displayName ?? 'worker',
        text: '收到，正在分析。',
        mode: body.mode,
      });
    }

    if (url.includes('/api/ui/clear-chat-session') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      clearChatBodies.push(body);
      return json({
        context: {
          contextId: body.contextId ?? 'normal-chat',
          kind: 'normal_chat',
          title: '普通聊天',
          activeTaskId: null,
          activeReportId: null,
        },
        messages: [],
      });
    }

    if (url.includes('/api/ui/send-chat-message') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as { contextId: string; text: string };
      chatBodies.push(body);
      if (options.chatMessageResponse) {
        return options.chatMessageResponse;
      }
      if (body.text.trim().toLowerCase().startsWith('/select') && options.selectSendResponse) {
        return options.selectSendResponse;
      }
      if (body.text.trim().toLowerCase() === '/help') {
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
              messageId: 'msg-help-u1',
              contextKind: 'normal_chat',
              actor: 'user',
              kind: 'plain',
              text: body.text,
              createdAt: '2026-05-19T10:08:00.000Z',
            },
            {
              messageId: 'msg-help-s1',
              contextKind: 'normal_chat',
              actor: 'system',
              kind: 'plain',
              text: [
                '可用命令：',
                '',
                '/report <标的>',
                '  生成完整投资报告。',
                '  示例：/report TSLA',
                '',
                '/select [市场] [refresh|刷新] [YYYY-MM-DD]',
                '  查看或刷新选股结果；不写市场时默认 A股。',
                '  市场：1/cn_a/A股 = A股；2/crypto/加密 = 加密。',
                '  示例：/select、/select 1、/select 2、/select crypto、/select 2 refresh',
                '',
                '/help',
                '  查看命令详细用法。',
              ].join('\n'),
              createdAt: '2026-05-19T10:08:01.000Z',
            },
          ],
        });
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
    getCancelBodies: () => cancelBodies,
    getCancelSelectionBodies: () => cancelSelectionBodies,
    getDeleteBodies: () => deleteBodies,
    getClearChatBodies: () => clearChatBodies,
    getChatBodies: () => chatBodies,
    getWorkerChatBodies: () => workerChatBodies,
    getAskReportQuestionBodies: () => askReportQuestionBodies,
    getSendReportFileBodies: () => sendReportFileBodies,
  };
}

describe('home page', () => {
  const restoreList: Array<() => void> = [];

  afterEach(() => {
    while (restoreList.length) {
      const restore = restoreList.pop();
      restore?.();
    }
    window.sessionStorage.clear();
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

  it('restores normal chat messages when the workspace remounts', async () => {
    const mocked = mockWorkspaceFetch({
      chatSessionSnapshot: {
        context: {
          contextId: 'normal-chat',
          kind: 'normal_chat',
          title: '普通聊天',
          activeTaskId: null,
          activeReportId: null,
        },
        messages: [
          {
            messageId: 'normal-user-1',
            contextKind: 'normal_chat',
            actor: 'user',
            kind: 'plain',
            text: '刚才的问题',
            createdAt: '2026-05-19T10:08:00.000Z',
          },
          {
            messageId: 'normal-assistant-1',
            contextKind: 'normal_chat',
            actor: 'assistant',
            kind: 'plain',
            text: '刚才的回答',
            createdAt: '2026-05-19T10:08:01.000Z',
          },
        ],
      },
    });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('刚才的问题')).toBeInTheDocument();
    expect(screen.getByText('刚才的回答')).toBeInTheDocument();
  });

  it('keeps local worker chat messages after leaving and returning to the page', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);
    const firstRender = render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    await screen.findByTestId('workspace-layout');
    fireEvent.change(screen.getByLabelText('输入消息'), {
      target: { value: '@市场分析师 看一下盘面' },
    });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getWorkerChatBodies()).toHaveLength(1));
    expect(await screen.findByText('@市场分析师 看一下盘面')).toBeInTheDocument();
    expect(screen.getByText('市场分析师：收到，正在分析。')).toBeInTheDocument();

    firstRender.unmount();
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('@市场分析师 看一下盘面')).toBeInTheDocument();
    expect(screen.getByText('市场分析师：收到，正在分析。')).toBeInTheDocument();
  });

  it('clears the current chat through the backend and removes local cached chat', async () => {
    const mocked = mockWorkspaceFetch({
      chatSessionSnapshot: {
        context: {
          contextId: 'normal-chat',
          kind: 'normal_chat',
          title: '普通聊天',
          activeTaskId: null,
          activeReportId: null,
        },
        messages: [
          {
            messageId: 'normal-user-clear',
            contextKind: 'normal_chat',
            actor: 'user',
            kind: 'plain',
            text: '需要清掉的问题',
            createdAt: '2026-05-19T10:08:00.000Z',
          },
        ],
      },
    });
    restoreList.push(mocked.restore);
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('需要清掉的问题')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '清除聊天' }));

    await waitFor(() => expect(mocked.getClearChatBodies()).toHaveLength(1));
    expect(mocked.getClearChatBodies()[0]).toMatchObject({ contextId: 'normal-chat' });
    expect(screen.queryByText('需要清掉的问题')).not.toBeInTheDocument();
    expect(screen.getByText('还没有聊天内容')).toBeInTheDocument();
    expect(window.sessionStorage.getItem('claw-trade:home-chat-state:v1') ?? '').not.toContain('需要清掉的问题');
  });

  it('stops the running report task from the right rail', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '停止任务' }));

    await waitFor(() => expect(mocked.getCancelBodies()).toHaveLength(1));
    expect(mocked.getCancelBodies()[0]).toMatchObject({ taskId: 'task-1' });
    expect(await screen.findByText('已停止报告任务。')).toBeInTheDocument();
    expect(screen.getByText('暂无运行中或排队中的报告任务')).toBeInTheDocument();
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

  it('stops backend selection progress from the right rail', async () => {
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
          completedRoleLabels: [],
          waitingRoleLabels: [],
          startedAt: '2026-06-04T10:00:00Z',
          workflowRunId: 'sel-refresh-active-1',
        },
      },
    });
    restoreList.push(mocked.restore);
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '停止选股' }));

    await waitFor(() => expect(mocked.getCancelSelectionBodies()).toHaveLength(1));
    expect(mocked.getCancelSelectionBodies()[0]).toMatchObject({ workflowRunId: 'sel-refresh-active-1' });
    expect(screen.getByText('已停止选股任务。')).toBeInTheDocument();
    expect(screen.queryByText('选股数据刷新')).not.toBeInTheDocument();
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

  it('refreshes report model status after the startup settings load fails', async () => {
    const originalFetch = globalThis.fetch;
    const intervalCallbacks: Array<() => void> = [];
    let llmCalls = 0;
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
      if (url.includes('/api/ui/load-llm-settings')) {
        llmCalls += 1;
        if (llmCalls === 1) {
          return new Response(JSON.stringify({ message: 'gateway warming up' }), {
            status: 503,
            headers: { 'Content-Type': 'application/json' },
          });
        }
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
      if (url.includes('/api/ui/get-chat-session')) {
        return json({ context: { contextId: 'normal-chat', kind: 'normal_chat', title: '普通聊天' }, messages: [] });
      }
      if (url.includes('/api/ui/get-channel-chat-snapshot')) {
        return json({ channelKind: 'wechat_clawbot', messages: [], confirmationCards: {} });
      }
      if (url.includes('/api/ui/get-selection-refresh-snapshot')) {
        return json({ selectionProgress: null });
      }
      if (url.includes('/api/ui/list-worker-chat-workers')) {
        return json({ workers: [] });
      }
      if (url.includes('/api/ui/list-scheduled-reports') || url.includes('/api/ui/list-price-alerts')) {
        return json({ items: [] });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('llm-config-warning')).toBeInTheDocument();
    expect(llmCalls).toBe(1);

    await act(async () => {
      intervalCallbacks.at(0)?.();
    });

    await waitFor(() => expect(screen.queryByTestId('llm-config-warning')).not.toBeInTheDocument());
    expect(llmCalls).toBeGreaterThan(1);
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

  it('sends ordinary main composer messages through normal chat', async () => {
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
    await waitFor(() => expect(mocked.getChatBodies()).toHaveLength(1));
    expect(mocked.getChatBodies().at(0)).toMatchObject({
      text: '帮我看下茅台',
      contextId: 'normal-chat',
    });
    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
  });

  it('shows a local normal chat pending reply before the slow response returns', async () => {
    let resolveChat: (response: Response) => void = () => undefined;
    const chatMessageResponse = new Promise<Response>((resolve) => {
      resolveChat = resolve;
    });
    const mocked = mockWorkspaceFetch({ chatMessageResponse });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '帮我看下茅台' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('帮我看下茅台')).toBeInTheDocument();
    expect(screen.getByText('正在回复...')).toBeInTheDocument();
    expect(screen.queryByText('收到，正在分析。')).not.toBeInTheDocument();

    await act(async () => {
      resolveChat(
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
              messageId: 'msg-u1',
              contextKind: 'normal_chat',
              actor: 'user',
              kind: 'plain',
              text: '帮我看下茅台',
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
        }),
      );
    });

    expect(await screen.findByText('收到，正在分析。')).toBeInTheDocument();
    expect(screen.queryByText('正在回复...')).not.toBeInTheDocument();
    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
  });

  it('renders normal chat replies from the send response without polling while sending', async () => {
    const intervalDelays: number[] = [];
    vi.spyOn(window, 'setInterval').mockImplementation(((_handler: Parameters<typeof window.setInterval>[0], timeout?: number) => {
      intervalDelays.push(Number(timeout));
      return 1;
    }) as typeof window.setInterval);
    vi.spyOn(window, 'clearInterval').mockImplementation(() => undefined);

    let resolveChat: (response: Response) => void = () => undefined;
    const chatMessageResponse = new Promise<Response>((resolve) => {
      resolveChat = resolve;
    });
    const finalPayload = {
      context: {
        contextId: 'normal-chat',
        kind: 'normal_chat',
        title: '普通聊天',
        activeTaskId: null,
        activeReportId: null,
      },
      messages: [
        {
          messageId: 'msg-u-pending',
          contextKind: 'normal_chat',
          actor: 'user',
          kind: 'plain',
          text: '帮我看下茅台',
          createdAt: '2026-05-19T10:08:00.000Z',
        },
        {
          messageId: 'msg-a-pending',
          contextKind: 'normal_chat',
          actor: 'assistant',
          kind: 'plain',
          text: '最终回答',
          createdAt: '2026-05-19T10:08:05.000Z',
        },
      ],
    };
    const mocked = mockWorkspaceFetch({
      chatMessageResponse,
      chatSessionSnapshot: () => ({ ...finalPayload, messages: [] }),
    });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '帮我看下茅台' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('正在回复...')).toBeInTheDocument();
    await waitFor(() => expect(mocked.getChatBodies()).toHaveLength(1));
    expect(intervalDelays).not.toContain(500);

    await act(async () => {
      resolveChat(json(finalPayload));
    });

    expect(await screen.findByText('最终回答')).toBeInTheDocument();
    expect(screen.queryByText('正在回复...')).not.toBeInTheDocument();
    expect(mocked.getChatBodies()).toHaveLength(1);
  });

  it('keeps local worker chat messages when later channel polling returns messages', async () => {
    let channelHasMessages = false;
    const intervalCallbacks: Array<() => void> = [];
    vi.spyOn(window, 'setInterval').mockImplementation(((handler: Parameters<typeof window.setInterval>[0]) => {
      if (typeof handler === 'function') {
        intervalCallbacks.push(() => handler());
      }
      return 1;
    }) as typeof window.setInterval);
    vi.spyOn(window, 'clearInterval').mockImplementation(() => undefined);
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

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '@组合经理 帮我看下茅台' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('组合经理：收到，正在分析。')).toBeInTheDocument();
    expect(screen.getByText('@组合经理 帮我看下茅台')).toBeInTheDocument();
    const queueCountBeforeRefresh = mocked.getQueueCount();
    channelHasMessages = true;

    await act(async () => {
      intervalCallbacks.at(0)?.();
    });
    await waitFor(() => expect(mocked.getQueueCount()).toBeGreaterThan(queueCountBeforeRefresh));

    expect(screen.getByText('组合经理：收到，正在分析。')).toBeInTheDocument();
    expect(screen.getByText('@组合经理 帮我看下茅台')).toBeInTheDocument();
    expect(screen.queryByText('微信里发来的问题')).not.toBeInTheDocument();
  });

  it('keeps local worker chat messages after a later normal chat response', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '@市场分析师 看一下市场结构' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('@市场分析师 看一下市场结构')).toBeInTheDocument();
    expect(await screen.findByText('市场分析师：收到，正在分析。')).toBeInTheDocument();
    const nextInput = screen.getByLabelText('输入消息');
    await waitFor(() => expect(nextInput).not.toBeDisabled());

    fireEvent.change(nextInput, { target: { value: '普通消息' } });
    await waitFor(() => expect(screen.getByRole('button', { name: '发送' })).not.toBeDisabled());
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getChatBodies()).toHaveLength(1));
    expect(await screen.findByText('普通消息')).toBeInTheDocument();
    expect(screen.getByText('@市场分析师 看一下市场结构')).toBeInTheDocument();
    expect(screen.getByText('市场分析师：收到，正在分析。')).toBeInTheDocument();
    expect(mocked.getWorkerChatBodies()).toHaveLength(1);
    expect(mocked.getChatBodies()).toHaveLength(1);
  });

  it('keeps slash commands available when no worker chat workers are available', async () => {
    const mocked = mockWorkspaceFetch({ workerChatWorkers: [] });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Worker chat 暂无可用 worker，请刷新页面后重试。')).toBeInTheDocument();
    const input = screen.getByLabelText('输入消息');
    expect(input).not.toBeDisabled();

    fireEvent.change(input, { target: { value: '帮我看下茅台' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
    await waitFor(() => expect(mocked.getChatBodies()).toHaveLength(1));
    expect(mocked.getChatBodies().at(0)).toMatchObject({ text: '帮我看下茅台' });

    fireEvent.change(input, { target: { value: '/select' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getChatBodies()).toHaveLength(2));
    expect(mocked.getChatBodies().at(1)).toMatchObject({ text: '/select' });
  });

  it('does not send an unfinished worker mention when no workers are available', async () => {
    const mocked = mockWorkspaceFetch({ workerChatWorkers: [] });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Worker chat 暂无可用 worker，请刷新页面后重试。')).toBeInTheDocument();
    const input = screen.getByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '@' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(input).toHaveValue('@');
    expect(mocked.getChatBodies()).toHaveLength(0);
    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
  });

  it('keeps slash commands available when the worker chat menu fails to load', async () => {
    const mocked = mockWorkspaceFetch({ workerChatListFails: true });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('Worker chat 菜单加载失败，请刷新页面后重试。')).toBeInTheDocument();
    const input = screen.getByLabelText('输入消息');
    expect(input).not.toBeDisabled();

    fireEvent.change(input, { target: { value: '帮我看下茅台' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
    await waitFor(() => expect(mocked.getChatBodies()).toHaveLength(1));
    expect(mocked.getChatBodies().at(0)).toMatchObject({ text: '帮我看下茅台' });

    fireEvent.change(input, { target: { value: '/select' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getChatBodies()).toHaveLength(2));
    expect(mocked.getChatBodies().at(1)).toMatchObject({ text: '/select' });
  });

  it('keeps select commands on the original chat message flow', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '/select' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getChatBodies()).toHaveLength(1));
    expect(mocked.getChatBodies().at(0)).toMatchObject({
      contextId: 'normal-chat',
      text: '/select',
    });
    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
  });

  it('keeps select market shortcuts on the original chat message flow', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '/select 2 refresh' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getChatBodies()).toHaveLength(1));
    expect(mocked.getChatBodies().at(0)).toMatchObject({
      contextId: 'normal-chat',
      text: '/select 2 refresh',
    });
    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
  });

  it('renders help command output as readable multi-line text', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '/help' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getChatBodies()).toHaveLength(1));
    const help = await screen.findByText(/可用命令：/);
    expect(help).toHaveClass('ct-message-text');
    expect(help.textContent).toContain('/report <标的>\n  生成完整投资报告。');
    expect(help.textContent).toContain('/select [市场] [refresh|刷新] [YYYY-MM-DD]');
    expect(help.textContent).toContain('/select 1、/select 2、/select crypto、/select 2 refresh');
    expect(help.textContent).toContain('/help\n  查看命令详细用法。');
  });

  it('shows backend input errors in the chat bubble for invalid select commands', async () => {
    const mocked = mockWorkspaceFetch({
      selectSendResponse: Promise.resolve(
        new Response(JSON.stringify({ code: 'INVALID_INPUT', message: '请输入完整的 /select 指令。' }), {
          status: 400,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '/select US' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getChatBodies()).toHaveLength(1));
    expect(mocked.getChatBodies().at(0)).toMatchObject({ text: '/select US' });
    expect(await screen.findAllByText('请输入完整的 /select 指令。')).not.toHaveLength(0);
    expect(screen.queryByText('回复失败，请稍后重试。')).not.toBeInTheDocument();
  });

  it('sends leading worker mentions to that worker', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '@市场分析师 看一下市场结构' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('@市场分析师 看一下市场结构')).toBeInTheDocument();
    expect(input).toHaveValue('@市场分析师 ');
    expect(await screen.findByText('市场分析师：收到，正在分析。')).toBeInTheDocument();
    await waitFor(() => expect(mocked.getWorkerChatBodies()).toHaveLength(1));
    expect(mocked.getWorkerChatBodies().at(0)).toMatchObject({
      mode: 'generic_worker_chat',
      workerId: 'market_analyst',
      text: '看一下市场结构',
      conversationId: 'normal-chat',
    });
    expect(mocked.getChatBodies()).toHaveLength(0);

    fireEvent.change(input, { target: { value: '@市场分析师 继续看成交量' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getWorkerChatBodies()).toHaveLength(2));
    expect(mocked.getWorkerChatBodies().at(1)).toMatchObject({
      mode: 'generic_worker_chat',
      workerId: 'market_analyst',
      text: '继续看成交量',
      conversationId: 'normal-chat',
    });
  });

  it('inserts a worker mention from the normal chat composer @ picker', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    await waitFor(() => expect(screen.queryByRole('button', { name: '组合经理' })).not.toBeInTheDocument());

    fireEvent.change(input, { target: { value: '@' } });
    fireEvent.click(await screen.findByRole('option', { name: '@市场分析师' }));

    expect(input).toHaveValue('@市场分析师 ');
  });

  it('inserts a slash command from the normal chat composer command picker', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '/' } });
    const selectOption = await screen.findByRole('option', { name: /\/select/ });
    expect(selectOption.textContent).toContain('/select 1、/select 2、/select crypto');
    fireEvent.click(await screen.findByRole('option', { name: /\/report/ }));

    expect(input).toHaveValue('/report ');
    expect(screen.queryByRole('listbox', { name: '命令列表' })).not.toBeInTheDocument();
    expect(mocked.getChatBodies()).toHaveLength(0);
  });

  it('supports keyboard selection in the normal chat slash command picker', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '/' } });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(input).toHaveValue('/alert ');
    expect(mocked.getChatBodies()).toHaveLength(0);
  });

  it('filters the normal chat slash command picker by typed query', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '/he' } });

    expect(await screen.findByRole('option', { name: /\/help/ })).toBeInTheDocument();
    expect(screen.queryByRole('option', { name: /\/report/ })).not.toBeInTheDocument();
    expect(mocked.getChatBodies()).toHaveLength(0);
  });

  it('supports keyboard selection in the normal chat @ picker', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '@' } });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(input).toHaveValue('@市场分析师 ');
    expect(mocked.getChatBodies()).toHaveLength(0);
    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
  });

  it('supports tab selection in the normal chat @ picker', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '@' } });
    fireEvent.keyDown(input, { key: 'ArrowDown' });
    fireEvent.keyDown(input, { key: 'Tab' });

    expect(input).toHaveValue('@研究经理 ');
    expect(mocked.getChatBodies()).toHaveLength(0);
    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
  });

  it('wraps upward keyboard selection in the normal chat @ picker', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '@' } });
    fireEvent.keyDown(input, { key: 'ArrowUp' });
    fireEvent.keyDown(input, { key: 'Enter' });

    expect(input).toHaveValue('@风险经理 ');
    expect(mocked.getChatBodies()).toHaveLength(0);
    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
  });

  it('does not send an unfinished worker mention while workers are available', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '@' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(input).toHaveValue('@组合经理 ');
    expect(mocked.getChatBodies()).toHaveLength(0);
    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
  });

  it('does not send an unmatched worker mention while workers are available', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '@不存在' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(input).toHaveValue('@不存在');
    expect(mocked.getChatBodies()).toHaveLength(0);
    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
  });

  it('keeps non-leading worker mentions on normal chat', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: 'abc @市场分析师 看一下市场结构' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getChatBodies()).toHaveLength(1));
    expect(mocked.getChatBodies().at(0)).toMatchObject({ text: 'abc @市场分析师 看一下市场结构' });
    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
  });

  it('rejects unknown leading worker mentions without fallback', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '@不存在 看一下市场结构' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('没有找到这个 worker。')).toBeInTheDocument();
    expect(mocked.getChatBodies()).toHaveLength(0);
    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
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
    expect(screen.getByText('策略评审：执行中')).toBeInTheDocument();
    expect(screen.getByText('组合经理：等待启动')).toBeInTheDocument();

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

  it('stops local select progress and ignores the late select response', async () => {
    let resolveSelect!: (response: Response) => void;
    const selectSendResponse = new Promise<Response>((resolve) => {
      resolveSelect = resolve;
    });
    const mocked = mockWorkspaceFetch({ selectSendResponse });
    restoreList.push(mocked.restore);
    vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '/select' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    fireEvent.click(await screen.findByRole('button', { name: '停止选股' }));

    expect(screen.getByText('已停止选股任务。')).toBeInTheDocument();
    expect(screen.queryByText('选股任务进度')).not.toBeInTheDocument();

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
              messageId: 'msg-select-late-result',
              contextKind: 'normal_chat',
              actor: 'system',
              kind: 'selection_result',
              text: '`/select` 已完成，本轮仅进入等待确认。',
              createdAt: '2026-05-19T10:09:00.000Z',
            },
          ],
        }),
      );
      await selectSendResponse;
    });

    expect(screen.queryByText(/已完成，本轮仅进入等待确认/)).not.toBeInTheDocument();
    expect(mocked.getCancelSelectionBodies()).toHaveLength(0);
  });

  it('keeps local select worker progress when polling returns empty progress mid-run', async () => {
    let resolveSelect!: (response: Response) => void;
    const selectSendResponse = new Promise<Response>((resolve) => {
      resolveSelect = resolve;
    });
    let selectionRefreshCalls = 0;
    const intervalCallbacks: Array<() => void> = [];
    vi.spyOn(window, 'setInterval').mockImplementation(((handler: Parameters<typeof window.setInterval>[0]) => {
      if (typeof handler === 'function') {
        intervalCallbacks.push(() => handler());
      }
      return 1;
    }) as typeof window.setInterval);
    vi.spyOn(window, 'clearInterval').mockImplementation(() => undefined);
    const mocked = mockWorkspaceFetch({
      selectSendResponse,
      selectionRefreshSnapshot: () => {
        selectionRefreshCalls += 1;
        return { selectionProgress: null };
      },
    });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    const refreshCallsAfterLoad = selectionRefreshCalls;
    fireEvent.change(input, { target: { value: '/select' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('选股任务进度')).toBeInTheDocument();
    expect(screen.getByText('策略评审：执行中')).toBeInTheDocument();
    expect(intervalCallbacks.length).toBeGreaterThan(0);

    await act(async () => {
      intervalCallbacks.at(0)?.();
    });

    await waitFor(() => expect(selectionRefreshCalls).toBeGreaterThan(refreshCallsAfterLoad));

    expect(screen.getByText('选股任务进度')).toBeInTheDocument();
    expect(screen.getByText('策略评审：执行中')).toBeInTheDocument();

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
              messageId: 'msg-select-user-empty-progress',
              contextKind: 'normal_chat',
              actor: 'user',
              kind: 'plain',
              text: '/select',
              createdAt: '2026-05-19T10:08:00.000Z',
            },
            {
              messageId: 'msg-select-result-empty-progress',
              contextKind: 'normal_chat',
              actor: 'system',
              kind: 'selection_result',
              text: '`/select` 已完成，本轮仅进入等待确认，不会自动启动 `/report`。',
              createdAt: '2026-05-19T10:09:00.000Z',
            },
          ],
          selection: {
            code: 'completed',
            workflowRunId: 'select-test-run-empty-progress',
            evidencePath: 'runs/selection/workflows/select-test-run-empty-progress/selection-workflow-evidence.json',
            unavailableCode: null,
            failureReason: null,
            readerReportMarkdown: null,
          },
        }),
      );
      await selectSendResponse;
    });
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

  it('shows completed report brief from current chat polling after confirmation', async () => {
    let chatHasCompletion = false;
    const intervalCallbacks: Array<() => void> = [];
    vi.spyOn(window, 'setInterval').mockImplementation(((handler: Parameters<typeof window.setInterval>[0]) => {
      if (typeof handler === 'function') {
        intervalCallbacks.push(() => handler());
      }
      return 1;
    }) as typeof window.setInterval);
    vi.spyOn(window, 'clearInterval').mockImplementation(() => undefined);
    const mocked = mockWorkspaceFetch({
      chatSessionSnapshot: () =>
        chatHasCompletion
          ? {
              context: {
                contextId: 'normal-chat',
                kind: 'report_reading',
                title: 'BTC 报告',
                activeTaskId: 'task-2',
                activeReportId: 'report-1',
              },
              messages: [
                {
                  messageId: 'msg-completed',
                  contextKind: 'report_reading',
                  actor: 'system',
                  kind: 'report_completed',
                  text: '报告已完成。\n最终结论：维持观察，等待突破确认。\n核心理由：日线趋势改善\n主要风险：估值波动',
                  reportId: 'report-1',
                  createdAt: '2026-05-19T10:10:00.000Z',
                },
              ],
            }
          : {
              context: {
                contextId: 'normal-chat',
                kind: 'task_following',
                title: '任务跟进：BTC',
                activeTaskId: 'task-2',
                activeReportId: null,
              },
              messages: [],
            },
    });
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
    expect(mocked.getConfirmBodies().at(0)).toMatchObject({ decision: 'confirm', contextId: 'normal-chat' });

    chatHasCompletion = true;
    await act(async () => {
      intervalCallbacks.at(-1)?.();
    });

    expect(await screen.findByText('正式报告已完成')).toBeInTheDocument();
    expect(screen.getByText(/最终结论：维持观察/)).toBeInTheDocument();
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
    fireEvent.change(input, { target: { value: '/report 展示完成卡' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText(/最终结论：维持观察/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '查看完整报告' }));
    expect(await screen.findByTestId('reading-report-body')).toBeInTheDocument();
    expect(screen.queryByTestId('report-detail-page')).not.toBeInTheDocument();
  });

  it('opens reading mode and sends report worker chat with the default worker', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const entry = await screen.findByRole('button', { name: /600519\.SH/ });
    fireEvent.click(entry);

    const reportBody = await screen.findByTestId('reading-report-body');
    expect(reportBody).toBeInTheDocument();
    expect(screen.getByRole('img', { name: '趋势图' })).toHaveAttribute(
      'src',
      '/api/ui/get-report-asset?reportId=report-1&assetPath=assets%2Fchart.png',
    );
    expect(screen.getByRole('table')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: '指标' })).toBeInTheDocument();
    const qaList = screen.getByTestId('reading-qa-list');
    expect(qaList).toBeInTheDocument();
    expect(Boolean(reportBody.compareDocumentPosition(qaList) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);
    expect(screen.getByRole('region', { name: '报告 worker 聊天' })).toBeInTheDocument();
    expect(screen.getByPlaceholderText('和组合经理聊这份报告')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '组合经理' })).not.toBeInTheDocument();
    expect(screen.queryByText(/追问/)).not.toBeInTheDocument();

    const askInput = screen.getByLabelText('输入消息');
    fireEvent.change(askInput, { target: { value: '核心结论是什么？' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('核心结论是什么？')).toBeInTheDocument();
    expect(screen.getByText('你')).toBeInTheDocument();
    expect(screen.getAllByText('组合经理').length).toBeGreaterThan(0);
    expect(await screen.findByText('收到，正在分析。')).toBeInTheDocument();
    await waitFor(() => expect(mocked.getWorkerChatBodies()).toHaveLength(1));
    expect(mocked.getWorkerChatBodies().at(0)).toMatchObject({
      mode: 'report_worker_chat',
      workerId: 'portfolio_manager',
      reportId: 'report-1',
      text: '核心结论是什么？',
      conversationId: 'report-report-1',
    });
    expect(mocked.getAskReportQuestionBodies()).toHaveLength(0);
    expect(within(reportBody).getByText(/建议继续跟踪渠道修复/)).toBeInTheDocument();
    expect(within(reportBody).queryByText('收到，正在分析。')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '返回聊天' }));

    expect(screen.queryByTestId('reading-report-body')).not.toBeInTheDocument();
    expect(screen.getByTestId('message-stream')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('输入问题，或提交报告任务需求')).toBeInTheDocument();
  });

  it('sends report worker chat to the worker chosen from the reading @ picker', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /600519\.SH/ }));
    expect(await screen.findByTestId('reading-report-body')).toBeInTheDocument();

    const input = screen.getByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '@' } });
    const marketOption = await screen.findByRole('option', { name: '@市场分析师' });
    marketOption.focus();
    fireEvent.click(marketOption);

    expect(input).toHaveValue('@市场分析师 ');
    expect(input).toHaveFocus();
    fireEvent.change(input, { target: { value: `${(input as HTMLInputElement).value}市场怎么看？` } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getWorkerChatBodies()).toHaveLength(1));
    expect(mocked.getWorkerChatBodies().at(0)).toMatchObject({
      mode: 'report_worker_chat',
      workerId: 'market_analyst',
      reportId: 'report-1',
      text: '市场怎么看？',
    });
    expect(mocked.getAskReportQuestionBodies()).toHaveLength(0);
  });

  it('uses the default report worker when opening a report', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /600519\.SH/ }));
    expect(await screen.findByTestId('reading-report-body')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('和组合经理聊这份报告')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '组合经理' })).not.toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('输入消息'), { target: { value: '核心结论是什么？' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getWorkerChatBodies()).toHaveLength(1));
    expect(mocked.getWorkerChatBodies().at(0)).toMatchObject({
      mode: 'report_worker_chat',
      workerId: 'portfolio_manager',
      reportId: 'report-1',
    });
  });

  it('lets a leading worker mention override the report worker for one question', async () => {
    const mocked = mockWorkspaceFetch();
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /600519\.SH/ }));
    expect(await screen.findByTestId('reading-report-body')).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText('输入消息'), { target: { value: '@市场分析师 市场怎么看？' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(mocked.getWorkerChatBodies()).toHaveLength(1));
    expect(mocked.getWorkerChatBodies().at(0)).toMatchObject({
      mode: 'report_worker_chat',
      workerId: 'market_analyst',
      text: '市场怎么看？',
      reportId: 'report-1',
    });
    expect(mocked.getAskReportQuestionBodies()).toHaveLength(0);
  });

  it('disables report worker chat when no workers are available', async () => {
    const mocked = mockWorkspaceFetch({ workerChatWorkers: [] });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: /600519\.SH/ }));
    expect(await screen.findByTestId('reading-report-body')).toBeInTheDocument();
    expect(screen.getByText('Worker chat 暂无可用 worker，请刷新页面后重试。')).toBeInTheDocument();
    expect(screen.getByPlaceholderText('Worker chat 暂不可用')).toBeDisabled();

    expect(mocked.getWorkerChatBodies()).toHaveLength(0);
    expect(mocked.getAskReportQuestionBodies()).toHaveLength(0);
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
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const deleteButton = await screen.findByRole('button', { name: /删除 茅台投研报告/ });
    fireEvent.click(deleteButton);

    expect(confirmSpy).toHaveBeenCalledWith(
      '永久删除「茅台投研报告」？将删除报告文件、图表、运行证据、worker 输出和相关本地缓存。',
    );
    await waitFor(() => {
      expect(mocked.getDeleteBodies().at(0)?.reportId).toBe('report-1');
    });
    expect(screen.queryByText('茅台投研报告')).not.toBeInTheDocument();
  });

  it('keeps a saved report visible when delete cleanup skips it', async () => {
    const mocked = mockWorkspaceFetch({
      deleteSavedReportResponses: {
        'report-1': {
          deleted: false,
          reportId: 'report-1',
          userMessage: '报告仍在发送中，暂未删除。',
          cleanup: {
            deletedRunIds: [],
            skippedRunIds: ['report-1'],
            failedRunIds: [],
            deletedBytesApprox: 0,
            warnings: [],
          },
        },
      },
    });
    restoreList.push(mocked.restore);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const deleteButton = await screen.findByRole('button', { name: /删除 茅台投研报告/ });
    fireEvent.click(deleteButton);

    expect(confirmSpy).toHaveBeenCalledWith(
      '永久删除「茅台投研报告」？将删除报告文件、图表、运行证据、worker 输出和相关本地缓存。',
    );
    await waitFor(() => {
      expect(mocked.getDeleteBodies().at(0)?.reportId).toBe('report-1');
    });
    expect(screen.getByText('茅台投研报告')).toBeInTheDocument();
    expect(screen.getByText('报告仍在发送中，暂未删除。')).toBeInTheDocument();
  });

  it('forwards a saved report PDF to connected WeChat from history', async () => {
    const mocked = mockWorkspaceFetch({
      savedReports: [{ ...DEFAULT_SAVED_REPORTS[0], canForwardToChannel: true }],
      channelStatus: {
        channelKind: 'wechat_clawbot',
        onboardingState: 'completed',
        state: 'connected',
        displayName: '微信 ClawBot',
        accountLabel: '测试号',
        canSendText: true,
        canSendFile: true,
      },
    });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '转发 茅台投研报告' }));

    await waitFor(() => {
      expect(mocked.getSendReportFileBodies().at(0)).toMatchObject({
        reportId: 'report-1',
        channelKind: 'wechat_clawbot',
      });
    });
    expect(await screen.findByText('完整报告已发送。')).toBeInTheDocument();
  });

  it('shows a clear report forward error when the channel send fails', async () => {
    const message = '完整报告文件暂不可发送，请在设备界面查看。';
    const mocked = mockWorkspaceFetch({
      savedReports: [{ ...DEFAULT_SAVED_REPORTS[0], canForwardToChannel: true }],
      channelStatus: {
        channelKind: 'wechat_clawbot',
        onboardingState: 'completed',
        state: 'connected',
        displayName: '微信 ClawBot',
        accountLabel: '测试号',
        canSendText: true,
        canSendFile: true,
      },
      sendReportFileResponse: Promise.resolve(
        new Response(JSON.stringify({ message, code: 'FILE_SEND_UNSUPPORTED' }), {
          status: 409,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    });
    restoreList.push(mocked.restore);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    fireEvent.click(await screen.findByRole('button', { name: '转发 茅台投研报告' }));

    await waitFor(() => {
      expect(screen.getAllByText(message).length).toBeGreaterThan(0);
    });
    expect(mocked.getSendReportFileBodies()).toHaveLength(1);
    expect(screen.getByRole('button', { name: '转发 茅台投研报告' })).toBeEnabled();
  });

  it('deletes only searched reports from history', async () => {
    const mocked = mockWorkspaceFetch({
      savedReports: [
        ...DEFAULT_SAVED_REPORTS,
        {
          id: 'report-2',
          instrumentCode: 'AAPL',
          instrumentName: '苹果',
          market: 'US',
          title: '苹果投研报告',
          generatedAt: '2026-05-19T11:00:00.000Z',
          summarySnippet: '关注新品周期。',
        },
      ],
    });
    restoreList.push(mocked.restore);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('茅台投研报告')).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('搜索报告'), { target: { value: '苹果' } });
    fireEvent.click(screen.getByRole('button', { name: '删除搜索结果' }));

    expect(confirmSpy).toHaveBeenCalledWith(
      '删除当前搜索结果中的 1 份正式报告？每个目标报告运行都会被硬删除。将删除报告文件、图表、运行证据、worker 输出和相关本地缓存。',
    );
    await waitFor(() => {
      expect(mocked.getDeleteBodies().at(0)?.reportIds).toEqual(['report-2']);
    });
    expect(screen.queryByText('苹果投研报告')).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('搜索报告'), { target: { value: '' } });
    expect(screen.getByText('茅台投研报告')).toBeInTheDocument();
  });

  it('clears all saved reports when no search is active', async () => {
    const mocked = mockWorkspaceFetch({
      savedReports: [
        ...DEFAULT_SAVED_REPORTS,
        {
          id: 'report-2',
          instrumentCode: 'AAPL',
          instrumentName: '苹果',
          market: 'US',
          title: '苹果投研报告',
          generatedAt: '2026-05-19T11:00:00.000Z',
          summarySnippet: '关注新品周期。',
        },
      ],
    });
    restoreList.push(mocked.restore);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('茅台投研报告')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '清空' }));

    expect(confirmSpy).toHaveBeenCalledWith(
      '清空 2 份正式报告？每个目标报告运行都会被硬删除。将删除报告文件、图表、运行证据、worker 输出和相关本地缓存。',
    );
    await waitFor(() => {
      expect(mocked.getDeleteBodies().at(0)?.reportIds).toEqual(['report-1', 'report-2']);
    });
    expect(screen.queryByText('茅台投研报告')).not.toBeInTheDocument();
    expect(screen.queryByText('苹果投研报告')).not.toBeInTheDocument();
  });

  it('keeps skipped reports visible during bulk clear and reports partial failure', async () => {
    const mocked = mockWorkspaceFetch({
      savedReports: [
        ...DEFAULT_SAVED_REPORTS,
        {
          id: 'report-2',
          instrumentCode: 'AAPL',
          instrumentName: '苹果',
          market: 'US',
          title: '苹果投研报告',
          generatedAt: '2026-05-19T11:00:00.000Z',
          summarySnippet: '关注新品周期。',
        },
        {
          id: 'report-3',
          instrumentCode: 'MSFT',
          instrumentName: '微软',
          market: 'US',
          title: '微软投研报告',
          generatedAt: '2026-05-19T12:00:00.000Z',
          summarySnippet: '关注云业务增长。',
        },
      ],
      deleteSavedReportResponses: {
        'report-2': {
          deleted: false,
          reportId: 'report-2',
          userMessage: '报告仍在发送中，暂未删除。',
          cleanup: {
            deletedRunIds: [],
            skippedRunIds: ['report-2'],
            failedRunIds: [],
            deletedBytesApprox: 0,
            warnings: [],
          },
        },
        'report-3': {
          deleted: false,
          reportId: 'report-3',
          userMessage: '清理本地缓存失败，暂未删除。',
          cleanup: {
            deletedRunIds: [],
            skippedRunIds: [],
            failedRunIds: ['report-3'],
            deletedBytesApprox: 0,
            warnings: [],
          },
        },
      },
    });
    restoreList.push(mocked.restore);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('茅台投研报告')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '清空' }));

    expect(confirmSpy).toHaveBeenCalledWith(
      '清空 3 份正式报告？每个目标报告运行都会被硬删除。将删除报告文件、图表、运行证据、worker 输出和相关本地缓存。',
    );
    await waitFor(() => {
      expect(mocked.getDeleteBodies().at(0)?.reportIds).toEqual(['report-1', 'report-2', 'report-3']);
    });
    expect(screen.queryByText('茅台投研报告')).not.toBeInTheDocument();
    expect(screen.getByText('苹果投研报告')).toBeInTheDocument();
    expect(screen.getByText('微软投研报告')).toBeInTheDocument();
    expect(
      screen.getByText('已删除 1 份报告，2 份未删除。原因：报告仍在发送中，暂未删除。；清理本地缓存失败，暂未删除。'),
    ).toBeInTheDocument();
  });

  it('keeps all reports visible when the bulk clear request throws', async () => {
    const mocked = mockWorkspaceFetch({
      savedReports: [
        ...DEFAULT_SAVED_REPORTS,
        {
          id: 'report-2',
          instrumentCode: 'AAPL',
          instrumentName: '苹果',
          market: 'US',
          title: '苹果投研报告',
          generatedAt: '2026-05-19T11:00:00.000Z',
          summarySnippet: '关注新品周期。',
        },
      ],
      deleteSavedReportFailures: {
        'report-1': new Error('网络删除失败。'),
      },
    });
    restoreList.push(mocked.restore);
    const confirmSpy = vi.spyOn(window, 'confirm').mockReturnValue(true);

    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('茅台投研报告')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '清空' }));

    expect(confirmSpy).toHaveBeenCalledWith(
      '清空 2 份正式报告？每个目标报告运行都会被硬删除。将删除报告文件、图表、运行证据、worker 输出和相关本地缓存。',
    );
    await waitFor(() => {
      expect(mocked.getDeleteBodies().at(0)?.reportIds).toEqual(['report-1', 'report-2']);
    });
    expect(screen.getByText('茅台投研报告')).toBeInTheDocument();
    expect(screen.getByText('苹果投研报告')).toBeInTheDocument();
    expect(screen.getByText('1 份报告未删除。原因：网络删除失败。')).toBeInTheDocument();
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
