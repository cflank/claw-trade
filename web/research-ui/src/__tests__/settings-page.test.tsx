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

describe('settings-wechat settings page', () => {
  const originalFetch = globalThis.fetch;
  const originalConfirm = window.confirm;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    window.confirm = originalConfirm;
    vi.useRealTimers();
  });

  it('renders separate report model and embedding sections', async () => {
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
          supportedTypes: ['finnhub', 'coingecko_pro', 'coinglass'],
          instances: [
            {
              instanceId: 'ds-1',
              supportedType: 'finnhub',
              group: 'global_data',
              displayName: 'Finnhub',
              enabled: true,
              state: 'enabled',
            },
            {
              instanceId: 'ds-2',
              supportedType: 'coingecko_pro',
              group: 'crypto_data',
              displayName: 'CoinGecko Pro',
              enabled: false,
              state: 'draft',
            },
            {
              instanceId: 'ds-3',
              supportedType: 'coinglass',
              group: 'crypto_data',
              displayName: 'Coinglass',
              enabled: false,
              state: 'draft',
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

    expect(await screen.findByRole('heading', { name: '设置' })).toBeInTheDocument();
    expect(screen.getByText('管理报告模型、增强数据源和微信通知。')).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '模型' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '通用' })).toBeInTheDocument();
    expect(screen.getByRole('tab', { name: '数据源' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '报告模型' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Embedding' })).toBeInTheDocument();
    const modelSection = screen.getByRole('heading', { name: '报告模型' }).closest('section') as HTMLElement;
    expect(within(modelSection).getByLabelText('服务商')).toHaveValue('deepseek');
    expect(within(modelSection).getByRole('option', { name: 'OpenAI' })).toBeInTheDocument();
    expect(within(modelSection).getByRole('option', { name: 'Anthropic Claude' })).toBeInTheDocument();
    expect(within(modelSection).getByRole('option', { name: 'Google Gemini' })).toBeInTheDocument();
    expect(within(modelSection).getByLabelText('模型')).toHaveValue('deepseek/deepseek-chat');
    expect(within(modelSection).getByLabelText('接口地址')).toHaveValue('https://api.example.com');
    fireEvent.click(screen.getByRole('tab', { name: '通用' }));
    expect(await screen.findByText('微信通知')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: '恢复默认设置' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '恢复默认设置' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新连接' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '解除连接' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '刷新二维码' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '稍后设置' })).toBeInTheDocument();
    expect(await screen.findByAltText('微信登录二维码')).toHaveAttribute(
      'src',
      'data:image/png;base64,settings-qr',
    );
    fireEvent.click(screen.getByRole('tab', { name: '数据源' }));
    expect(screen.getByRole('heading', { name: '增强数据源' })).toBeInTheDocument();
    const settingsScope = screen.getByTestId('settings-page');
    expect(within(settingsScope).queryByText('打开 OpenClaw 主界面')).not.toBeInTheDocument();
    expect(within(settingsScope).queryByText('provider 健康摘要')).not.toBeInTheDocument();
    expect(within(settingsScope).queryByText('运行服务状态摘要')).not.toBeInTheDocument();
    expect(within(settingsScope).queryByText('最近 live run 缺口摘要')).not.toBeInTheDocument();
    expect(within(settingsScope).queryByText('证据链失败原因摘要')).not.toBeInTheDocument();
    expect(settingsScope.textContent?.toLowerCase() ?? '').not.toContain('18789');
    expect(settingsScope.textContent?.toLowerCase() ?? '').not.toContain('1933');
    expect(settingsScope.textContent?.toLowerCase() ?? '').not.toContain('gateway');
    expect(settingsScope.textContent?.toLowerCase() ?? '').not.toContain('scope');
    expect(settingsScope.textContent?.toLowerCase() ?? '').not.toContain('/runs/');
    expect(settingsScope.textContent?.toLowerCase() ?? '').not.toContain('uri');
    expect(settingsScope.textContent?.toLowerCase() ?? '').not.toContain('hash');
    expect(settingsScope.textContent?.toLowerCase() ?? '').not.toContain('l1');
    expect(settingsScope.textContent?.toLowerCase() ?? '').not.toContain('l2');
    expect(settingsScope.textContent?.toLowerCase() ?? '').not.toContain('receipt');
    expect(settingsScope.textContent ?? '').not.toContain('/runtime/dev-services/openviking/ov.conf');
    const dataSourceSection = screen.getByRole('heading', { name: '增强数据源' }).closest('section') as HTMLElement;
    expect(within(dataSourceSection).getAllByText('此功能可配置的增强源')).toHaveLength(3);
    expect(within(dataSourceSection).queryByRole('tab', { name: 'A股' })).not.toBeInTheDocument();
    expect(within(dataSourceSection).getByRole('tab', { name: '港股' })).toHaveAttribute('aria-selected', 'true');
    expect(within(dataSourceSection).getByRole('tab', { name: '美股' })).toBeInTheDocument();
    expect(within(dataSourceSection).getByRole('tab', { name: '全球市场/宏观' })).toBeInTheDocument();
    expect(within(dataSourceSection).getByRole('tab', { name: '加密货币' })).toBeInTheDocument();
    expect(within(dataSourceSection).getByText('新闻公告')).toBeInTheDocument();
    expect(within(dataSourceSection).getByText('行情')).toBeInTheDocument();
    expect(within(dataSourceSection).getByText('基本面')).toBeInTheDocument();
    expect(within(dataSourceSection).queryByText('社交舆情')).not.toBeInTheDocument();
    expect(screen.queryByText('代理地址')).not.toBeInTheDocument();
    expect(screen.queryByText('请求头名')).not.toBeInTheDocument();
    expect(screen.queryByText('优先级')).not.toBeInTheDocument();
    expect(dataSourceSection.querySelectorAll('.ct-source-category-card')).toHaveLength(3);
    expect(dataSourceSection.querySelectorAll('.ct-source-row')).toHaveLength(0);
    expect(screen.queryByText('Custom Vendor')).not.toBeInTheDocument();
  });

  it('shows report model defaults before slow saved settings load finishes', () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/load-llm-settings')) {
        return new Promise<Response>(() => undefined);
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
          qrCodeImageDataUrl: null,
          qrCodeRefreshRequired: false,
        });
      }
      if (url.includes('/api/ui/list-data-sources')) {
        return json({
          supportedTypes: ['tushare'],
          instances: [
            {
              instanceId: 'builtin-tushare',
              supportedType: 'tushare',
              group: 'cn_a_data',
              displayName: 'Tushare',
              enabled: false,
              state: 'draft',
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

    const modelSection = screen.getByRole('heading', { name: '报告模型' }).closest('section') as HTMLElement;
    expect(within(modelSection).getByLabelText('服务商')).toHaveValue('deepseek');
    expect(within(modelSection).getByLabelText('模型')).toHaveValue('deepseek/deepseek-chat');
    expect(within(modelSection).getByLabelText('接口地址')).toHaveValue('https://api.deepseek.com');
  });

  it('does not invent data source defaults before slow saved settings load finishes', () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/list-data-sources')) {
        return new Promise<Response>(() => undefined);
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: null,
            defaultModel: '',
            status: 'idle',
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
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
          qrCodeImageDataUrl: null,
          qrCodeRefreshRequired: false,
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole('tab', { name: '数据源' }));
    const dataSourceSection = screen.getByRole('heading', { name: '增强数据源' }).closest('section') as HTMLElement;
    expect(within(dataSourceSection).getByText('暂无数据源配置')).toBeInTheDocument();
    expect(within(dataSourceSection).queryByLabelText('行情可配置增强源')).not.toBeInTheDocument();
    expect(within(dataSourceSection).queryByLabelText('接口地址')).not.toBeInTheDocument();
  });

  it('resets settings after explicit confirmation', async () => {
    const seenUrls: string[] = [];
    window.confirm = vi.fn(() => true);
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      seenUrls.push(url);
      if (url.includes('/api/ui/get-channel-status')) {
        return json({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'connected',
          displayName: '微信 ClawBot',
          accountLabel: '测试号',
          canSendText: true,
          canSendFile: true,
          qrCodeImageDataUrl: null,
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
              checkedAt: '2026-05-20T12:00:00Z',
            },
            embedding: {
              provider: 'openai',
              model: 'text-embedding-3-small',
              apiKeyMasked: 'emb-****',
              enabled: true,
            },
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      if (url.includes('/api/ui/list-data-sources')) {
        return json({
          supportedTypes: ['tushare'],
          instances: [
            {
              instanceId: 'ds-tushare',
              supportedType: 'tushare',
              group: 'cn_a_data',
              displayName: 'Tushare',
              enabled: true,
              apiKeyMasked: 'ts-****',
              endpointUrl: 'https://api.tushare.pro',
              state: 'enabled',
            },
          ],
        });
      }
      if (url.includes('/api/ui/reset-settings-to-defaults')) {
        return json({
          status: 'reset',
          userMessage: '设置已恢复默认。',
          llm: { status: 'reset', updatedAt: '2026-05-25T10:00:00Z', settingsVersion: 'v_reset' },
          dataSources: {
            status: 'reset',
            updatedAt: '2026-05-25T10:00:00Z',
            supportedTypes: ['tushare'],
            instances: [
              {
                instanceId: 'builtin-tushare',
                supportedType: 'tushare',
                group: 'cn_a_data',
                displayName: 'Tushare',
                enabled: false,
                apiKeyMasked: null,
                endpointUrl: null,
                state: 'draft',
                lastSuccessAt: null,
                lastTestAt: null,
              },
            ],
          },
          channel: {
            channelKind: 'wechat_clawbot',
            onboardingState: 'completed',
            state: 'disconnected',
            displayName: '微信 ClawBot',
            accountLabel: null,
            canSendText: false,
            canSendFile: false,
            qrCodeImageDataUrl: null,
            qrCodeRefreshRequired: true,
          },
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );

    await screen.findByRole('heading', { name: '报告模型' });
    fireEvent.click(screen.getByRole('tab', { name: '通用' }));
    await screen.findByRole('heading', { name: '恢复默认设置' });
    fireEvent.click(screen.getByRole('button', { name: '恢复默认设置' }));

    expect(await screen.findByText('设置已恢复默认。')).toBeInTheDocument();
    expect(seenUrls.some((url) => url.includes('/api/ui/reset-settings-to-defaults'))).toBe(true);
    expect(window.confirm).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole('tab', { name: '模型' }));
    const modelSection = screen.getByRole('heading', { name: '报告模型' }).closest('section') as HTMLElement;
    expect(within(modelSection).getAllByText('未配置').length).toBeGreaterThanOrEqual(1);
    const embeddingSection = screen.getByRole('heading', { name: 'Embedding' }).closest('section') as HTMLElement;
    expect(within(embeddingSection).getByText('未启用')).toBeInTheDocument();
  });

  it('loads WeChat QR when opening general settings and still supports manual refresh', async () => {
    const seenUrls: string[] = [];
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      seenUrls.push(url);
      if (url.includes('/api/ui/get-channel-status')) {
        const refreshed = url.includes('refreshQr=true');
        const includesQr = url.includes('includeQr=true');
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
            : includesQr
              ? 'data:image/png;base64,settings-qr-auto'
              : null,
          qrCodeRefreshRequired: !refreshed && !includesQr,
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

    await screen.findByRole('heading', { name: '报告模型' });
    expect(seenUrls.filter((url) => url.includes('includeQr=true'))).toHaveLength(0);
    fireEvent.click(screen.getByRole('tab', { name: '通用' }));
    await waitFor(() => {
      expect(screen.getByAltText('微信登录二维码')).toHaveAttribute(
        'src',
        'data:image/png;base64,settings-qr-auto',
      );
    });
    expect(seenUrls.some((url) => url.includes('includeQr=true') && !url.includes('refreshQr=true'))).toBe(true);
    expect(seenUrls.some((url) => url.includes('refreshQr=true'))).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: '刷新二维码' }));

    await waitFor(() => {
      expect(screen.getByAltText('微信登录二维码')).toHaveAttribute(
        'src',
        'data:image/png;base64,settings-qr-next',
      );
    });
    expect(seenUrls.some((url) => url.includes('refreshQr=true'))).toBe(true);
  });

  it('renders report model four states and keeps save separate from test success', async () => {
    let testCalls = 0;
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
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            endpointUrl: 'https://api.example.com',
            defaultModel: 'deepseek-chat',
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
      if (url.includes('/api/ui/save-llm-config-via-openclaw')) {
        return json({ status: 'saved', updatedAt: '2026-05-20T12:01:00Z', settingsVersion: 'v-new' });
      }
      if (url.includes('/api/ui/test-llm-via-openclaw')) {
        testCalls += 1;
        if (testCalls === 1) {
          return json({
            ok: false,
            userMessage: 'gateway timeout: provider attempt #2',
            checkedAt: '2026-05-20T12:02:00Z',
          });
        }
        return json({ ok: true, userMessage: '报告模型连接测试通过。', checkedAt: '2026-05-20T12:03:00Z' });
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

    const modelSection = (await screen.findByRole('heading', { name: '报告模型' })).closest('section');
    expect(modelSection).not.toBeNull();
    const section = modelSection as HTMLElement;
    expect(within(section).getAllByText('未配置').length).toBeGreaterThanOrEqual(1);
    expect(within(section).getByLabelText('服务商')).toBeInTheDocument();
    expect(within(section).getByLabelText('模型')).toBeInTheDocument();
    expect(within(section).getByLabelText('API Key')).toBeInTheDocument();
    expect(within(section).getByLabelText('接口地址')).toBeInTheDocument();

    fireEvent.click(within(section).getByRole('button', { name: '保存报告模型配置' }));
    expect(await screen.findByText('模型配置已保存。')).toBeInTheDocument();
    expect(within(section).getAllByText('已保存未验证').length).toBeGreaterThanOrEqual(1);

    fireEvent.click(within(section).getByRole('button', { name: '测试报告模型连接' }));
    expect(
      (await screen.findAllByText('报告模型连接测试失败，请检查服务商、模型、API Key 和接口地址后重试。')).length,
    ).toBeGreaterThanOrEqual(1);
    expect(within(section).getAllByText('测试失败').length).toBeGreaterThanOrEqual(1);
    expect(screen.queryByText('provider 健康摘要')).not.toBeInTheDocument();
    expect(screen.queryByText('证据链失败原因摘要')).not.toBeInTheDocument();
    expect(screen.queryByText(/provider attempt/i)).not.toBeInTheDocument();
    expect(screen.queryByText(/gateway/i)).not.toBeInTheDocument();

    fireEvent.click(within(section).getByRole('button', { name: '测试报告模型连接' }));
    expect(await screen.findByText('报告模型连接测试通过。')).toBeInTheDocument();
    expect(within(section).getAllByText('可用').length).toBeGreaterThanOrEqual(1);
  });

  it('keeps model surfaces free of forbidden setting terms in visible UI text', async () => {
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
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            endpointUrl: 'https://api.example.com',
            defaultModel: 'deepseek-chat',
            status: 'saved',
            reportModelStatus: {
              state: 'saved_unverified',
              blocked: true,
              ready: false,
              userMessage: '报告模型已保存但尚未测试通过，请先执行模型测试。',
              checkedAt: '2026-05-20T10:00:00Z',
            },
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

    await screen.findByRole('heading', { name: '报告模型' });
    const text = (document.body.textContent ?? '').toLowerCase();
    const forbidden = ['profile', '默认币种', '报告方案', 'worker', 'debate', 'risk', 'openclaw gateway'];
    for (const term of forbidden) {
      expect(text).not.toContain(term);
    }
  });

  it('waits for a real QR login response before showing channel unavailable', async () => {
    vi.useFakeTimers();
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/get-channel-status')) {
        if (!url.includes('refreshQr=true')) {
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
          });
        }
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
      await vi.advanceTimersByTimeAsync(0);
    });
    fireEvent.click(screen.getByRole('tab', { name: '通用' }));
    fireEvent.click(screen.getByRole('button', { name: '刷新二维码' }));

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
        if (url.includes('refreshQr=true')) {
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
        if (url.includes('pollLogin=true')) {
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

    await screen.findByRole('heading', { name: '报告模型' });
    fireEvent.click(screen.getByRole('tab', { name: '通用' }));
    expect(screen.queryByAltText('微信登录二维码')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '刷新二维码' }));
    expect(await screen.findByAltText('微信登录二维码')).toHaveAttribute(
      'src',
      'data:image/png;base64,settings-qr',
    );

    await waitFor(() => expect(screen.getByRole('button', { name: '解除连接' })).toBeEnabled(), {
      timeout: 4000,
    });
    expect(screen.getByText('当前微信账号已连接，可接收通知。')).toBeInTheDocument();
    expect(seenUrls.filter((url) => url.includes('/api/ui/get-channel-status')).length).toBeGreaterThanOrEqual(2);
    expect(seenUrls.some((url) => url.includes('pollLogin=true'))).toBe(true);
  }, 8000);

  it('supports reconnect and disconnect actions from the WeChat section', async () => {
    const calls: Array<{ url: string; body?: string }> = [];
    let status = 'disconnected';
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push({ url, body: typeof init?.body === 'string' ? init.body : undefined });
      if (url.includes('/api/ui/get-channel-status')) {
        return json({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: status,
          displayName: '微信 ClawBot',
          accountLabel: status === 'connected' ? '测试号' : null,
          canSendText: status === 'connected',
          canSendFile: false,
          qrCodeImageDataUrl: 'data:image/png;base64,settings-qr',
          qrCodeRefreshRequired: false,
        });
      }
      if (url.includes('/api/ui/save-channel-config-via-openclaw')) {
        const body = JSON.parse(String(init?.body || '{}')) as { configPatch?: { enabled?: boolean } };
        status = body.configPatch?.enabled ? 'connected' : 'disconnected';
        return json({
          status: {
            channelKind: 'wechat_clawbot',
            onboardingState: 'completed',
            state: status,
            displayName: '微信 ClawBot',
            accountLabel: status === 'connected' ? '测试号' : null,
            canSendText: status === 'connected',
            canSendFile: false,
            qrCodeImageDataUrl: 'data:image/png;base64,settings-qr',
            qrCodeRefreshRequired: false,
          },
          restartRequired: false,
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

    await screen.findByRole('heading', { name: '报告模型' });
    fireEvent.click(screen.getByRole('tab', { name: '通用' }));
    expect(await screen.findByRole('button', { name: '重新连接' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: '重新连接' }));
    await screen.findByText('已提交重新连接，请重新扫码。');

    fireEvent.click(screen.getByRole('button', { name: '解除连接' }));
    await screen.findByText('已解除连接。');

    const saveCalls = calls.filter((item) => item.url.includes('/api/ui/save-channel-config-via-openclaw'));
    expect(saveCalls.length).toBeGreaterThanOrEqual(2);
    expect(saveCalls[0]?.body).toContain('"enabled":true');
    expect(saveCalls[1]?.body).toContain('"enabled":false');
  });

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
        return json({ ok: true, userMessage: '报告模型连接测试通过。', checkedAt: '2026-05-20T12:00:00Z' });
      }
      if (url.includes('/api/ui/test-embedding-via-openviking')) {
        return json({
          ok: true,
          userMessage: 'Embedding 连接测试通过，已经能按意思生成检索向量。',
          checkedAt: '2026-05-20T12:00:30Z',
        });
      }
      if (url.includes('/api/ui/save-embedding-config-via-openviking')) {
        return json({ status: 'saved', updatedAt: '2026-05-20T12:00:45Z' });
      }
      if (url.includes('/api/ui/save-llm-config-via-openclaw')) {
        return json({ status: 'saved', updatedAt: '2026-05-20T12:01:00Z', settingsVersion: 'v-new' });
      }
      if (url.includes('/api/ui/list-data-sources')) {
        return json({
          supportedTypes: ['tushare'],
          instances: [
            {
              instanceId: 'builtin-tushare',
              supportedType: 'tushare',
              group: 'cn_a_data',
              displayName: 'Tushare',
              enabled: false,
              state: 'draft',
            },
          ],
        });
      }
      if (url.includes('/api/ui/test-data-source')) {
        return json({
          state: 'validated',
          healthEvent: {
            displayName: 'Tushare',
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
          instanceId: 'ds-tushare',
          supportedType: 'tushare',
          group: 'cn_a_data',
          displayName: 'Tushare',
          enabled: true,
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

    const modelSection = (await screen.findByRole('heading', { name: '报告模型' })).closest('section');
    expect(modelSection).not.toBeNull();
    fireEvent.change(within(modelSection as HTMLElement).getByLabelText('模型'), {
      target: { value: 'deepseek/deepseek-reasoner' },
    });
    fireEvent.change(within(modelSection as HTMLElement).getByLabelText('API Key'), {
      target: { value: 'sk-from-form' },
    });
    const embeddingSection = screen.getByRole('heading', { name: 'Embedding' }).closest('section') as HTMLElement;
    fireEvent.change(within(embeddingSection).getByLabelText('Embedding 服务商'), {
      target: { value: 'openai' },
    });
    fireEvent.change(within(embeddingSection).getByLabelText('Embedding 模型'), {
      target: { value: 'text-embedding-3-small' },
    });
    fireEvent.change(within(embeddingSection).getByLabelText('Embedding 接口地址'), {
      target: { value: 'https://embedding.example/v1' },
    });
    fireEvent.click(within(embeddingSection).getByRole('button', { name: '测试 Embedding 连接' }));
    expect(await screen.findByText('Embedding 连接测试通过，已经能按意思生成检索向量。')).toBeInTheDocument();
    fireEvent.click(within(embeddingSection).getByRole('button', { name: '保存 Embedding 配置' }));
    expect(await screen.findByText('Embedding 配置已保存。')).toBeInTheDocument();
    fireEvent.click(within(modelSection as HTMLElement).getByRole('button', { name: '测试报告模型连接' }));
    expect(await screen.findByText('报告模型连接测试通过。')).toBeInTheDocument();
    fireEvent.click(within(modelSection as HTMLElement).getByRole('button', { name: '保存报告模型配置' }));
    expect(await screen.findByText('模型配置已保存。')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: '数据源' }));
    const dataSection = screen.getByRole('heading', { name: '增强数据源' }).closest('section');
    expect(dataSection).not.toBeNull();
    fireEvent.change(within(dataSection as HTMLElement).getByLabelText('API Key / Token'), {
      target: { value: 'tushare-key' },
    });
    fireEvent.change(within(dataSection as HTMLElement).getByLabelText('限流次数'), {
      target: { value: '10' },
    });
    fireEvent.change(within(dataSection as HTMLElement).getByLabelText('限流窗口秒数'), {
      target: { value: '60' },
    });
    fireEvent.change(within(dataSection as HTMLElement).getByLabelText('安全余量'), {
      target: { value: '1' },
    });
    fireEvent.change(within(dataSection as HTMLElement).getByLabelText('超额策略'), {
      target: { value: 'wait' },
    });
    fireEvent.change(within(dataSection as HTMLElement).getByLabelText('最长等待秒数'), {
      target: { value: '75' },
    });
    fireEvent.click(within(dataSection as HTMLElement).getByRole('checkbox'));
    fireEvent.click(within(dataSection as HTMLElement).getByRole('button', { name: '测试数据源' }));
    expect(await screen.findByText('数据源测试通过。')).toBeInTheDocument();
    fireEvent.click(within(dataSection as HTMLElement).getByRole('button', { name: '保存数据源' }));
    expect(await screen.findByText('数据源配置已保存。')).toBeInTheDocument();

    const llmSave = calls.find((call) => call.url.includes('/api/ui/save-llm-config-via-openclaw'));
    expect(llmSave?.body).toContain('deepseek-reasoner');
    expect(llmSave?.body).not.toContain('text-embedding-3-small');
    expect(llmSave?.body).not.toContain('https://embedding.example/v1');
    expect(llmSave?.body).toContain('v-old');
    const llmTest = calls.find((call) => call.url.includes('/api/ui/test-llm-via-openclaw'));
    expect(llmTest?.body).toContain('sk-from-form');
    expect(llmTest?.body).not.toContain('text-embedding-3-small');
    const embeddingTest = calls.find((call) => call.url.includes('/api/ui/test-embedding-via-openviking'));
    expect(embeddingTest?.body).toContain('text-embedding-3-small');
    expect(embeddingTest?.body).toContain('https://embedding.example/v1');
    const embeddingSave = calls.find((call) => call.url.includes('/api/ui/save-embedding-config-via-openviking'));
    expect(embeddingSave?.body).toContain('text-embedding-3-small');
    expect(embeddingSave?.body).toContain('https://embedding.example/v1');
    expect(embeddingSave?.body).not.toContain('deepseek-reasoner');
    const dataSave = calls.find((call) => call.url.includes('/api/ui/save-data-source-instance'));
    expect(dataSave?.body).toContain('"supportedType":"tushare"');
    expect(dataSave?.body).toContain('tushare-key');
    expect(dataSave?.body).toContain('"rateLimitMaxCalls":"10"');
    expect(dataSave?.body).toContain('"rateLimitWindowSeconds":"60"');
    expect(dataSave?.body).toContain('"rateLimitSafetyMargin":"1"');
    expect(dataSave?.body).toContain('"rateLimitOverflow":"wait"');
    expect(dataSave?.body).toContain('"rateLimitWaitTimeoutSeconds":"75"');
  });

  it('settings-embedding shows optional embedding guidance without runtime internals', async () => {
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
              checkedAt: '2026-05-23T10:00:00Z',
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

    const embeddingSection = (await screen.findByRole('heading', { name: 'Embedding' })).closest('section');
    expect(embeddingSection).not.toBeNull();
    const section = embeddingSection as HTMLElement;
    expect(
      within(section).getByText(
        '它帮系统按意思找回相关材料，不只靠关键词；打开后报告更容易用到之前保存的上下文。没配也能生成报告。',
      ),
    ).toBeInTheDocument();
    expect(within(section).getByText('配置不完整（可选，不阻断报告）')).toBeInTheDocument();
    expect(within(section).getByRole('button', { name: '测试 Embedding 连接' })).toBeInTheDocument();
    expect(within(section).getByRole('button', { name: '保存 Embedding 配置' })).toBeInTheDocument();
    expect(section.querySelector('.ct-model-grid')).not.toBeNull();

    const bodyText = document.body.textContent ?? '';
    expect(bodyText).not.toContain('OpenViking');
    expect(bodyText).not.toContain('gateway');
    expect(bodyText).not.toContain('18789');
    expect(bodyText).not.toContain('1933');
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
          supportedTypes: ['tushare', 'finnhub'],
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

    expect(await screen.findByRole('heading', { name: '报告模型' })).toBeInTheDocument();
    expect(screen.getByText('助手服务暂不可用，请稍后重试。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '通用' }));
    expect(await screen.findByText('微信通知')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '重新连接' })).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '数据源' }));
    expect(screen.getByRole('heading', { name: '增强数据源' })).toBeInTheDocument();
    const dataSection = screen.getByRole('heading', { name: '增强数据源' }).closest('section') as HTMLElement;
    expect(within(dataSection).getByText('暂无数据源配置')).toBeInTheDocument();
    expect(within(dataSection).queryByText('此功能可配置的增强源')).not.toBeInTheDocument();
    expect(dataSection.querySelectorAll('.ct-source-category-card')).toHaveLength(0);
    expect(dataSection.querySelectorAll('.ct-source-row')).toHaveLength(0);
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
          supportedTypes: ['tushare', 'finnhub'],
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

    expect(screen.getByRole('heading', { name: '报告模型' })).toBeInTheDocument();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(9000);
    });

    expect(screen.queryByText('加载中...')).not.toBeInTheDocument();
    expect(screen.getByText('助手服务暂不可用，请稍后重试。')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('tab', { name: '数据源' }));
    expect(screen.getByRole('heading', { name: '增强数据源' })).toBeInTheDocument();
    const dataSection = screen.getByRole('heading', { name: '增强数据源' }).closest('section') as HTMLElement;
    expect(within(dataSection).getByText('暂无数据源配置')).toBeInTheDocument();
    expect(within(dataSection).queryByText('此功能可配置的增强源')).not.toBeInTheDocument();
    expect(dataSection.querySelectorAll('.ct-source-category-card')).toHaveLength(0);
    expect(dataSection.querySelectorAll('.ct-source-row')).toHaveLength(0);
  });
});
