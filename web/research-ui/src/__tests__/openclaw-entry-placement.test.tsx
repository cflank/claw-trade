import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { HomePage } from '../routes/HomePage';
import { SettingsPage } from '../routes/SettingsPage';

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('openclaw entry placement', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
  });

  it('does not render openclaw main entry text across settings page surface', async () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/get-channel-status')) {
        return json({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'disconnected',
          displayName: '微信 ClawBot',
          accountLabel: null,
          canSendText: false,
          canSendFile: false,
          qrCodeImageDataUrl: 'data:image/png;base64,settings-qr',
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
            status: 'idle',
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      if (url.includes('/api/ui/list-data-sources')) {
        return json({ supportedTypes: ['newsapi'], instances: [] });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter initialEntries={['/settings']}>
        <SettingsPage />
      </MemoryRouter>,
    );

    await screen.findByTestId('settings-page');
    const text = document.body.textContent ?? '';
    expect(text).not.toContain('打开 OpenClaw 主界面');
    expect(text).not.toContain('设备界面');
    expect(text).not.toContain('OpenClaw gateway');
    expect(text).not.toContain('Channel ID');
    expect(text).not.toContain('scope');
    expect(text).not.toContain('插件包名');
    expect(document.querySelector('a[href="/api/ui/open-device-interface"]')).toBeNull();
  });

  it('renders entry in chat tool area and keeps non-/report chat behavior', async () => {
    const calls: string[] = [];
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(url);
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
      if (url.includes('/api/ui/list-worker-chat-workers')) {
        return json({
          workers: [{ workerId: 'portfolio_manager', displayName: '组合经理', default: true, aliases: ['组合经理', 'PM'] }],
        });
      }
      if (url.includes('/api/ui/send-worker-chat') && init?.method === 'POST') {
        return json({
          kind: 'worker_chat_reply',
          workerDisplayName: '组合经理',
          text: '收到，正在分析。',
          mode: 'generic_worker_chat',
        });
      }
      if (url.includes('/api/ui/send-chat-message') && init?.method === 'POST') {
        return json({
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
              text: '你好',
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
      if (url.includes('/api/ui/confirm-intent-draft')) {
        return json({ status: 'unexpected' });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter initialEntries={['/']}>
        <HomePage />
      </MemoryRouter>,
    );

    const headerActions = await screen.findByText('聊天会话');
    const actionWrap = headerActions.closest('.ct-center-head-actions');
    const entry = within(actionWrap as HTMLElement).getByRole('link', { name: '打开设备界面' });
    expect(entry).toHaveAttribute('href', '/api/ui/open-device-interface');

    fireEvent.change(screen.getByLabelText('输入消息'), { target: { value: '你好' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => {
      expect(screen.getByText('收到，正在分析。')).toBeInTheDocument();
    });
    expect(calls.some((url) => url.includes('/api/ui/confirm-intent-draft'))).toBe(false);
  });
});
