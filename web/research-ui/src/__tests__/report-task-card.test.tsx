import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { HomePage } from '../routes/HomePage';

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function mockFetchForTaskCard() {
  const originalFetch = globalThis.fetch;
  const confirmBodies: Array<Record<string, unknown>> = [];
  const draftBodies: Array<Record<string, unknown>> = [];

  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url.includes('/api/ui/list-saved-reports')) {
      return json({ items: [] });
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
      return json({
        runningTask: null,
        queuedTasks: [],
        queueLimit: 10,
        queuedCount: 0,
        isFull: false,
      });
    }
    if (url.includes('/api/ui/send-chat-message') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as { contextId: string; text: string };
      if (body.text === '/report BTC') {
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
              messageId: 'msg-u1',
              contextKind: 'intent_confirming',
              actor: 'user',
              kind: 'plain',
              text: body.text,
              createdAt: '2026-05-19T10:09:00.000Z',
            },
            {
              messageId: 'msg-c1',
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
            dataSourceSummary: 'unknown',
            actions: ['confirm', 'cancel'],
            status: 'active',
            createdAt: '2026-05-19T10:09:01.000Z',
          },
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
            messageId: 'msg-u2',
            contextKind: 'normal_chat',
            actor: 'user',
            kind: 'plain',
            text: body.text,
            createdAt: '2026-05-19T10:10:00.000Z',
          },
          {
            messageId: 'msg-a2',
            contextKind: 'normal_chat',
            actor: 'assistant',
            kind: 'plain',
            text: '普通聊天回复',
            createdAt: '2026-05-19T10:10:01.000Z',
          },
        ],
      });
    }
    if (url.includes('/api/ui/create-intent-draft') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      draftBodies.push(body);
      const text = String(body.text ?? '');
      if (text === '/report TSLA') {
        return json({
          draft: {
            draftId: 'draft-tsla',
            kind: 'report',
            instrumentCode: 'TSLA',
            instrumentName: 'Tesla',
            market: 'US',
            notification: { channel: 'in_app', enabled: true },
            status: 'draft',
          },
          confirmationCard: {
            id: 'card-tsla',
            draftId: 'draft-tsla',
            title: '请确认是否创建完整报告',
            summaryLines: ['标的：TSLA', '名称：Tesla', '市场：US'],
            instrumentCode: 'TSLA',
            instrumentName: 'Tesla',
            market: 'US',
            dataSourceSummary: 'unknown',
            actions: ['confirm', 'cancel'],
            status: 'active',
            createdAt: '2026-05-19T10:11:00.000Z',
          },
        });
      }
      return json({
        draft: {
          draftId: 'draft-btc',
          kind: 'report',
          instrumentCode: 'BTC',
          instrumentName: 'Bitcoin',
          market: 'CRYPTO',
          notification: { channel: 'in_app', enabled: true },
          status: 'draft',
        },
        confirmationCard: {
          id: 'card-btc',
          draftId: 'draft-btc',
          title: '请确认是否创建完整报告',
          summaryLines: ['标的：BTC', '名称：Bitcoin', '市场：CRYPTO'],
          instrumentCode: 'BTC',
          instrumentName: 'Bitcoin',
          market: 'CRYPTO',
          dataSourceSummary: 'unknown',
          actions: ['confirm', 'cancel'],
          status: 'active',
          createdAt: '2026-05-19T10:11:00.000Z',
        },
      });
    }
    if (url.includes('/api/ui/confirm-intent-draft') && init?.method === 'POST') {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      confirmBodies.push(body);
      return json({
        status: 'confirmed',
        task: {
          taskId: 'task-1',
          source: 'manual',
          status: 'queued',
          statusLabel: '排队中',
          instrumentCode: String((body.overrides as Record<string, unknown>)?.instrumentCode ?? 'BTC'),
          instrumentName: null,
          market: String((body.overrides as Record<string, unknown>)?.market ?? 'CRYPTO'),
          companyName: 'x',
          currencySymbol: '$',
          startDate: '2026-05-01',
          endDate: '2026-05-19',
          currentDate: '2026-05-19',
          queuePosition: 1,
          progress: null,
          reportId: null,
          createdAt: '2026-05-19T10:12:00.000Z',
        },
        messages: [
          {
            messageId: 'msg-queued',
            contextKind: 'task_following',
            actor: 'system',
            kind: 'task_progress',
            text: '报告已进入队列。',
            createdAt: '2026-05-19T10:12:01.000Z',
          },
        ],
      });
    }
    return json({});
  }) as typeof fetch;

  return {
    restore: () => {
      globalThis.fetch = originalFetch;
    },
    confirmBodies,
    draftBodies,
  };
}

describe('report task card', () => {
  const restoreList: Array<() => void> = [];

  afterEach(() => {
    while (restoreList.length) {
      restoreList.pop()?.();
    }
  });

  it('does not open confirmation card for non-/report chat', async () => {
    const mocked = mockFetchForTaskCard();
    restoreList.push(mocked.restore);
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '帮我做一份 BTC 报告' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    expect(await screen.findByText('普通聊天回复')).toBeInTheDocument();
    expect(screen.queryByText('请确认是否创建完整报告')).not.toBeInTheDocument();
  });

  it('re-identifies symbol and refreshes name/market before confirm', async () => {
    const mocked = mockFetchForTaskCard();
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
    expect(card).toHaveClass('ct-task-confirm');
    expect(scoped.getByText('标的')).toBeInTheDocument();
    expect(scoped.getByText('名称')).toBeInTheDocument();
    expect(scoped.getByText('市场')).toBeInTheDocument();
    expect(scoped.getByText('BTC')).toBeInTheDocument();
    expect(scoped.getByText('Bitcoin')).toBeInTheDocument();
    expect(scoped.getByRole('combobox', { name: '市场' })).toHaveValue('CRYPTO');
    expect(screen.queryByText('报告方案')).not.toBeInTheDocument();
    expect(screen.queryByText('计价单位')).not.toBeInTheDocument();
    expect(screen.queryByText('profile')).not.toBeInTheDocument();
    expect(screen.queryByText('默认币种')).not.toBeInTheDocument();
    expect(screen.queryByText('worker')).not.toBeInTheDocument();
    expect(screen.queryByText('debate')).not.toBeInTheDocument();
    expect(screen.queryByText('risk')).not.toBeInTheDocument();

    const symbolEditor = screen.getByLabelText('修改标的');
    fireEvent.change(symbolEditor, { target: { value: 'TSLA' } });
    expect(await screen.findByText('请先点击“更新标的”完成重新识别。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '更新标的' }));

    await screen.findByText('TSLA');
    expect(screen.getByText('Tesla')).toBeInTheDocument();
    expect(screen.getByRole('combobox', { name: '市场' })).toHaveValue('US');

    fireEvent.click(screen.getByRole('button', { name: '确认' }));
    expect(await screen.findByText('报告已进入队列。')).toBeInTheDocument();
    expect((mocked.confirmBodies[0].overrides as Record<string, unknown>).instrumentCode).toBe('TSLA');
    expect((mocked.confirmBodies[0].overrides as Record<string, unknown>).market).toBe('US');
  });

  it('re-validates symbol-market match after market switch', async () => {
    const mocked = mockFetchForTaskCard();
    restoreList.push(mocked.restore);
    render(
      <MemoryRouter>
        <HomePage />
      </MemoryRouter>,
    );

    const input = await screen.findByLabelText('输入消息');
    fireEvent.change(input, { target: { value: '/report BTC' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));
    await screen.findByText('BTC');

    fireEvent.change(screen.getByLabelText('市场'), { target: { value: 'US' } });
    expect(await screen.findByText('当前标的识别为 CRYPTO 市场，请修改标的或选择匹配市场。')).toBeInTheDocument();
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '确认' })).toBeDisabled();
    });

    fireEvent.change(screen.getByLabelText('市场'), { target: { value: 'CRYPTO' } });
    await waitFor(() => {
      expect(screen.getByRole('button', { name: '确认' })).not.toBeDisabled();
    });
    expect(mocked.draftBodies.length).toBeGreaterThanOrEqual(2);
  });
});
