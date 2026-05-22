import { afterEach, describe, expect, it, vi } from 'vitest';
import { act } from 'react';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SettingsPage } from '../routes/SettingsPage';

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function errorJson(message: string) {
  return new Response(JSON.stringify({ code: 'ASSISTANT_UNAVAILABLE', message }), {
    status: 503,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('settings page', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
  });

  it('renders only three user-facing sections', async () => {
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
        return json({
          supportedTypes: ['newsapi'],
          instances: [
            {
              instanceId: 'ds-1',
              supportedType: 'newsapi',
              group: 'cn_a_news',
              displayName: 'NewsAPI',
              enabled: true,
              priority: 1,
              state: 'enabled',
            },
          ],
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('微信通知')).toBeInTheDocument();
    expect(screen.getByText('模型')).toBeInTheDocument();
    expect(screen.getByText('数据源')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '启用微信通知' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新二维码' })).toBeInTheDocument();
    expect(await screen.findByAltText('微信登录二维码')).toHaveAttribute(
      'src',
      'data:image/png;base64,settings-qr',
    );
    expect(screen.getAllByRole('link', { name: '打开 OpenClaw 主界面' })[0]).toHaveAttribute(
      'href',
      '/api/ui/open-device-interface',
    );
    expect(screen.getByText('1 类')).toBeInTheDocument();
  });

  it('refreshes the displayed WeChat QR code from the settings action', async () => {
    const seenUrls: string[] = [];
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      seenUrls.push(url);
      if (url.includes('/api/ui/get-channel-status')) {
        const refreshed = url.includes('refreshQr=true');
        return json({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'disconnected',
          displayName: '微信 ClawBot',
          accountLabel: null,
          canSendText: false,
          canSendFile: false,
          qrCodeImageDataUrl: refreshed
            ? 'data:image/png;base64,settings-qr-next'
            : 'data:image/png;base64,settings-qr-first',
          qrCodeRefreshRequired: false,
        });
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            defaultModel: 'deepseek-chat',
            status: 'idle',
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      if (url.includes('/api/ui/list-data-sources')) {
        return json({ supportedTypes: [], instances: [] });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByAltText('微信登录二维码')).toHaveAttribute(
      'src',
      'data:image/png;base64,settings-qr-first',
    );
    fireEvent.click(screen.getByRole('button', { name: '刷新二维码' }));

    await waitFor(() => {
      expect(screen.getByAltText('微信登录二维码')).toHaveAttribute(
        'src',
        'data:image/png;base64,settings-qr-next',
      );
    });
    expect(seenUrls.some((url) => url.includes('includeQr=true'))).toBe(true);
    expect(seenUrls.some((url) => url.includes('refreshQr=true'))).toBe(true);
  });

  it('waits for a real QR login response before showing channel unavailable', async () => {
    vi.useFakeTimers();
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/get-channel-status')) {
        return new Promise<Response>((resolve) => {
          setTimeout(() => {
            resolve(
              json({
                channelKind: 'wechat_clawbot',
                onboardingState: 'completed',
                state: 'disconnected',
                displayName: '微信 ClawBot',
                accountLabel: null,
                canSendText: false,
                canSendFile: false,
                qrCodeImageDataUrl: 'data:image/png;base64,slow-real-qr',
                qrCodeRefreshRequired: false,
              }),
            );
          }, 10000);
        });
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            defaultModel: 'deepseek-chat',
            status: 'idle',
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      if (url.includes('/api/ui/list-data-sources')) {
        return json({ supportedTypes: [], instances: [] });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });
    expect(screen.queryByText('微信通道暂不可用，请稍后重试。')).not.toBeInTheDocument();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    expect(screen.getByAltText('微信登录二维码')).toHaveAttribute(
      'src',
      'data:image/png;base64,slow-real-qr',
    );
  }, 10000);

  it('polls the displayed QR login until WeChat is connected', async () => {
    const seenUrls: string[] = [];
    let channelStatusCalls = 0;
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      seenUrls.push(url);
      if (url.includes('/api/ui/get-channel-status')) {
        channelStatusCalls += 1;
        if (channelStatusCalls === 1) {
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
        return json({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'connected',
          displayName: '微信 ClawBot',
          accountLabel: '测试号',
          canSendText: true,
          canSendFile: false,
          qrCodeImageDataUrl: null,
          qrCodeRefreshRequired: false,
        });
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            defaultModel: 'deepseek-chat',
            status: 'idle',
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      if (url.includes('/api/ui/list-data-sources')) {
        return json({ supportedTypes: [], instances: [] });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByAltText('微信登录二维码')).toHaveAttribute(
      'src',
      'data:image/png;base64,settings-qr',
    );

    await waitFor(
      () => {
        expect(screen.getByRole('button', { name: '已连接' })).toBeDisabled();
      },
      { timeout: 4000 },
    );
    expect(screen.getByText('测试号')).toBeInTheDocument();
    expect(seenUrls.filter((url) => url.includes('/api/ui/get-channel-status')).length).toBeGreaterThanOrEqual(2);
    expect(seenUrls.some((url) => url.includes('pollLogin=true'))).toBe(true);
  }, 8000);

  it('submits model and data source settings from editable forms', async () => {
    const calls: Array<{ url: string; body?: string }> = [];
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, body: typeof init?.body === 'string' ? init.body : undefined });
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
            embedding: {
              provider: '',
              model: '',
              endpointUrl: '',
              dimension: '',
              enabled: false,
            },
          },
          schemaVersion: 'v1',
          settingsVersion: 'v-old',
        });
      }
      if (url.includes('/api/ui/test-llm-via-openclaw')) {
        return json({ ok: true, userMessage: '连接测试通过。', checkedAt: '2026-05-20T12:00:00Z' });
      }
      if (url.includes('/api/ui/save-llm-config-via-openclaw')) {
        return json({ status: 'saved', updatedAt: '2026-05-20T12:01:00Z', settingsVersion: 'v-new' });
      }
      if (url.includes('/api/ui/list-data-sources')) {
        return json({ supportedTypes: ['newsapi', 'tushare'], instances: [] });
      }
      if (url.includes('/api/ui/test-data-source')) {
        return json({
          state: 'validated',
          healthEvent: {
            displayName: 'NewsAPI',
            status: 'enabled',
            userMessage: '数据源测试通过。',
            impact: 'low',
            occurredAt: '2026-05-20T12:02:00Z',
          },
          canEnable: true,
        });
      }
      if (url.includes('/api/ui/save-data-source-instance')) {
        return json({
          instanceId: 'ds-news',
          supportedType: 'newsapi',
          group: 'custom',
          displayName: 'NewsAPI',
          enabled: true,
          priority: 10,
          state: 'validated',
          lastTestAt: '2026-05-20T12:03:00Z',
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );

    const modelSection = (await screen.findByRole('heading', { name: '模型' })).closest('section');
    expect(modelSection).not.toBeNull();
    fireEvent.change(within(modelSection as HTMLElement).getByLabelText('默认模型'), {
      target: { value: 'deepseek-reasoner' },
    });
    fireEvent.change(within(modelSection as HTMLElement).getByLabelText('Embedding 服务商'), {
      target: { value: 'openai' },
    });
    fireEvent.change(within(modelSection as HTMLElement).getByLabelText('Embedding 模型'), {
      target: { value: 'text-embedding-3-small' },
    });
    fireEvent.change(within(modelSection as HTMLElement).getByLabelText('Embedding 接口地址'), {
      target: { value: 'https://embedding.example/v1' },
    });
    fireEvent.click(within(modelSection as HTMLElement).getByRole('button', { name: '测试连接' }));
    expect(await screen.findByText('连接测试通过。')).toBeInTheDocument();
    fireEvent.click(within(modelSection as HTMLElement).getByRole('button', { name: '保存模型' }));
    expect(await screen.findByText('模型配置已保存。')).toBeInTheDocument();

    const dataSection = screen.getByRole('heading', { name: '数据源' }).closest('section');
    expect(dataSection).not.toBeNull();
    fireEvent.change(within(dataSection as HTMLElement).getByLabelText('类型'), {
      target: { value: 'newsapi' },
    });
    fireEvent.change(within(dataSection as HTMLElement).getByLabelText('名称'), {
      target: { value: 'NewsAPI' },
    });
    fireEvent.change(within(dataSection as HTMLElement).getByLabelText('密钥'), {
      target: { value: 'news-key' },
    });
    fireEvent.change(within(dataSection as HTMLElement).getByLabelText('优先级'), {
      target: { value: '10' },
    });
    fireEvent.click(within(dataSection as HTMLElement).getByLabelText('启用这个数据源'));
    fireEvent.click(within(dataSection as HTMLElement).getByRole('button', { name: '测试数据源' }));
    expect(await screen.findByText('数据源测试通过。')).toBeInTheDocument();
    fireEvent.click(within(dataSection as HTMLElement).getByRole('button', { name: '保存数据源' }));
    expect(await screen.findByText('数据源配置已保存。')).toBeInTheDocument();

    const llmSave = calls.find((call) => call.url.includes('/api/ui/save-llm-config-via-openclaw'));
    expect(llmSave?.body).toContain('deepseek-reasoner');
    expect(llmSave?.body).toContain('text-embedding-3-small');
    expect(llmSave?.body).toContain('https://embedding.example/v1');
    expect(llmSave?.body).toContain('v-old');
    const dataSave = calls.find((call) => call.url.includes('/api/ui/save-data-source-instance'));
    expect(dataSave?.body).toContain('NewsAPI');
    expect(dataSave?.body).toContain('news-key');
  });

  it('keeps channel and data source settings visible when model loading fails', async () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/get-channel-status')) {
        return json({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'error',
          displayName: '微信 ClawBot',
          accountLabel: null,
          canSendText: false,
          canSendFile: false,
          lastErrorMessage: '微信通知暂不可用，请在设备界面查看。',
        });
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return errorJson('助手服务暂不可用，请稍后重试。');
      }
      if (url.includes('/api/ui/list-data-sources')) {
        return json({
          supportedTypes: ['newsapi', 'tushare'],
          instances: [],
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText('微信通知')).toBeInTheDocument();
    expect(screen.getByText('助手服务暂不可用，请稍后重试。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '启用微信通知' })).toBeInTheDocument();
    expect(screen.getByText('2 类')).toBeInTheDocument();
  });

  it('times out slow model loading without hiding channel and data source settings', async () => {
    vi.useFakeTimers();
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
        });
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return new Promise<Response>(() => undefined);
      }
      if (url.includes('/api/ui/list-data-sources')) {
        return json({
          supportedTypes: ['newsapi'],
          instances: [],
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );

    expect(screen.getByText('微信通知')).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });

    expect(screen.queryByText('加载中...')).not.toBeInTheDocument();
    expect(screen.getByText('助手服务暂不可用，请稍后重试。')).toBeInTheDocument();
    expect(screen.getByText('1 类')).toBeInTheDocument();
  });
});
