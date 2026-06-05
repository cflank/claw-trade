import { afterEach, describe, expect, it } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { mkdirSync, writeFileSync } from 'node:fs';
import path from 'node:path';
import { SettingsPage } from '../routes/SettingsPage';
import { AdvancedDiagnosticsPage } from '../routes/AdvancedDiagnosticsPage';

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

const artifactDir = path.resolve(process.cwd(), '..', '..', '.runtime', 'test-artifacts', 'settings-ui-text');

describe('settings forbidden surface', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('keeps ordinary settings surface free of forbidden terms and unapproved providers', async () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/get-channel-status')) {
        return json({
          channelKind: 'wechat_clawbot',
          onboardingState: 'completed',
          state: 'disconnected',
          displayName: '微信 ClawBot',
          canSendText: false,
          canSendFile: false,
          qrCodeImageDataUrl: 'data:image/png;base64,test-qr',
          qrCodeRefreshRequired: false,
        });
      }
      if (url.includes('/api/ui/load-llm-settings')) {
        return json({
          draft: {
            provider: 'deepseek',
            apiKeyMasked: 'sk-****',
            endpointUrl: 'https://api.deepseek.com',
            defaultModel: 'deepseek-chat',
            status: 'saved',
            reportModelStatus: {
              state: 'saved_unverified',
              blocked: true,
              ready: false,
              userMessage: '报告模型已保存但尚未测试通过，请先执行模型测试。',
              checkedAt: '2026-05-23T13:05:00Z',
            },
          },
          schemaVersion: 'v1',
          settingsVersion: 's1',
        });
      }
      if (url.includes('/api/ui/list-data-sources')) {
        return json({
          supportedTypes: [
            'tushare',
            'alpha_vantage',
            'finnhub',
            'fred',
            'coingecko_pro',
            'coinglass',
          ],
          instances: [
            {
              instanceId: 'builtin-tushare',
              supportedType: 'tushare',
              group: 'cn_a_data',
              displayName: 'Tushare',
              enabled: false,
              state: 'draft',
              endpointUrl: 'https://api.tushare.pro',
            },
            {
              instanceId: 'builtin-alpha-vantage',
              supportedType: 'alpha_vantage',
              group: 'global_data',
              displayName: 'Alpha Vantage',
              enabled: false,
              state: 'draft',
            },
            { instanceId: 'builtin-finnhub', supportedType: 'finnhub', group: 'global_data', displayName: 'Finnhub', enabled: false, state: 'draft' },
            { instanceId: 'builtin-fred', supportedType: 'fred', group: 'global_macro', displayName: 'FRED', enabled: false, state: 'draft' },
            {
              instanceId: 'builtin-coingecko-pro',
              supportedType: 'coingecko_pro',
              group: 'crypto_data',
              displayName: 'CoinGecko Pro',
              enabled: false,
              state: 'draft',
            },
            { instanceId: 'builtin-coinglass', supportedType: 'coinglass', group: 'crypto_data', displayName: 'Coinglass', enabled: false, state: 'draft' },
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

    await screen.findByRole('heading', { name: '设置' });
    fireEvent.click(screen.getByRole('tab', { name: '数据源' }));
    fireEvent.click(screen.getByRole('tab', { name: '加密货币' }));
    const text = (document.body.textContent ?? '').toLowerCase();
    const forbidden = [
      'profile',
      '默认市场',
      '默认币种',
      '报告方案',
      '计价单位',
      'worker',
      'debate',
      'risk',
      'channel id',
      'scope',
      'gateway',
      'provider attempt',
      'proxyurl',
      'headername',
      'priority',
      '代理地址',
      '请求头名',
      '优先级',
      '任意 http',
      'json 映射',
      'provider 优先级',
      'wechat_clawbot',
      'openclaw_plugins',
      '@openclaw',
      'plugin_package',
      '/report',
      '报告指令',
    ];
    for (const item of forbidden) {
      expect(text).not.toContain(item);
    }
    for (const approved of ['coingecko pro', 'coinglass']) {
      expect(text).toContain(approved);
    }
    for (const hiddenDefaultOrInternal of [
      'akshare',
      '东方财富',
      '雪球',
      'csmar',
      'wind',
      'iex cloud',
      'fmp',
      'polygon',
      'tiingo',
      'nasdaq data link',
      'newsapi',
      'x.com',
      'reddit',
      'openbb',
      'sec edgar',
      'binance',
      'okx',
      'ccxt',
      'coinmarketcap',
      'longport',
      'lunarcrush',
    ]) {
      expect(text).not.toContain(hiddenDefaultOrInternal);
    }
    for (const candidate of [
      'custom_http',
      'custom_json',
      'unknown_http_json',
    ]) {
      expect(text).not.toContain(candidate);
    }

    fireEvent.click(screen.getByRole('tab', { name: '通用' }));
    const generalText = (document.body.textContent ?? '').toLowerCase();
    expect(generalText).toContain('未登录不阻断工作台使用');
    expect(generalText).toContain('稍后设置');
    expect(generalText).not.toContain('先连接微信才能使用工作台');
    expect(generalText).not.toContain('必须先连接微信');
    expect(generalText).not.toContain('登录成功');
    expect(generalText).not.toContain('已登录');
    expect(generalText).not.toContain('连接成功');
    expect(generalText).not.toContain('在线中');
    expect(generalText).not.toContain('已在线');

    mkdirSync(artifactDir, { recursive: true });
    writeFileSync(path.join(artifactDir, 'frontend-settings-forbidden-surface.txt'), text + '\n', 'utf-8');
  });

  it('keeps advanced diagnostics as summary-only text', async () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/get-advanced-diagnostics-provider-health')) {
        return json({
          state: 'degraded',
          severity: 'warning',
          userMessage: 'gateway failed: provider attempt #2 raw payload request body leaked',
          checkedAt: '2026-05-23T13:15:00Z',
          source: 'openclaw.models.authStatus',
        });
      }
      if (url.includes('/api/ui/get-advanced-diagnostics-runtime-service-status')) {
        return json({
          state: 'degraded',
          severity: 'warning',
          userMessage: 'http://127.0.0.1:1933/health failed; gateway scope path leaked',
          checkedAt: '2026-05-23T13:15:00Z',
          source: 'runtime.health.http',
        });
      }
      if (url.includes('/api/ui/get-advanced-diagnostics-live-run-gap-summary')) {
        return json({
          state: 'gaps_detected',
          severity: 'warning',
          userMessage: 'provider attempt #3 raw evidence leaked',
          checkedAt: '2026-05-23T13:15:00Z',
          source: 'workflow.collect_first_report',
          recommendedAction: 'check /runtime/dev-services and retry',
        });
      }
      if (url.includes('/api/ui/get-advanced-diagnostics-evidence-failure-reason-summary')) {
        return json({
          state: 'failure_detected',
          severity: 'warning',
          userMessage: 'evidence failed: uri=... hash=... l1 l2 receipt leaked',
          checkedAt: '2026-05-23T13:15:00Z',
          source: 'workflow.evidence_chain',
          recommendedAction: 'check uri/hash/l1/l2',
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <AdvancedDiagnosticsPage />
      </MemoryRouter>,
    );

    const providerMessage = (await screen.findByTestId('provider-health-message')).textContent?.toLowerCase() ?? '';
    expect(providerMessage).not.toContain('provider attempt');
    expect(providerMessage).not.toContain('raw payload');
    expect(providerMessage).not.toContain('request body');

    const runtimeMessage = (await screen.findByTestId('runtime-status-message')).textContent?.toLowerCase() ?? '';
    expect(runtimeMessage).not.toContain('1933');
    expect(runtimeMessage).not.toContain('18789');
    expect(runtimeMessage).not.toContain('gateway');
    expect(runtimeMessage).not.toContain('scope');
    expect(runtimeMessage).not.toContain('/runtime/');

    const gapMessage = (await screen.findByTestId('live-run-gap-message')).textContent?.toLowerCase() ?? '';
    expect(gapMessage).not.toContain('provider attempt');
    expect(gapMessage).not.toContain('raw evidence');

    const failureMessage = (await screen.findByTestId('evidence-failure-message')).textContent?.toLowerCase() ?? '';
    expect(failureMessage).not.toContain('uri');
    expect(failureMessage).not.toContain('hash');
    expect(failureMessage).not.toContain('l1');
    expect(failureMessage).not.toContain('l2');
    expect(failureMessage).not.toContain('receipt');
  });
});
