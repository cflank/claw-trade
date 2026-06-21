import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { SettingsPage } from '../routes/SettingsPage';

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function errorJson(message: string, status = 400) {
  return new Response(JSON.stringify({ code: 'INVALID_INPUT', message }), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

function reportCleanupJson() {
  return json({ reportCleanup: { reportRetentionDays: 7 } });
}

describe('settings-enhanced-sources', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
  });

  it('renders provider-backed enhanced sources by market and hides probe-only placeholders', async () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/get-report-cleanup-settings')) {
        return reportCleanupJson();
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
          qrCodeImageDataUrl: 'data:image/png;base64,s05-qr',
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
        const source = (supportedType: string, displayName: string, group = 'global_data') => ({
          instanceId: `builtin-${supportedType}`,
          supportedType,
          group,
          displayName,
          enabled: false,
          state: 'draft',
        });
        const instances = [
          source('tushare', 'Tushare', 'cn_a_data'),
          source('csmar', 'CSMAR', 'cn_a_data'),
          source('wind', 'Wind', 'cn_a_data'),
          source('iex_cloud', 'IEX Cloud'),
          source('fmp', 'FMP'),
          source('polygon', 'Polygon'),
          source('finnhub', 'Finnhub'),
          source('tiingo', 'Tiingo'),
          source('nasdaq_data_link', 'Nasdaq Data Link'),
          source('fred', 'FRED', 'global_macro'),
          source('bea', 'BEA', 'global_macro'),
          source('eia', 'EIA', 'global_macro'),
          source('newsapi', 'NewsAPI', 'global_news'),
          source('x', 'X.com', 'global_social'),
          source('reddit', 'Reddit', 'global_social'),
          source('coingecko_pro', 'CoinGecko Pro', 'crypto_data'),
          source('coinmarketcap', 'CoinMarketCap', 'crypto_data'),
          source('coinglass', 'Coinglass', 'crypto_data'),
          source('glassnode', 'Glassnode', 'crypto_data'),
          source('lunarcrush', 'LunarCrush', 'crypto_social'),
        ];
        return json({
          supportedTypes: instances.map((item) => item.supportedType),
          instances,
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
    fireEvent.click(screen.getByRole('tab', { name: '数据源' }));
    const section = (await screen.findByRole('heading', { name: '增强数据源' })).closest('section');
    expect(section).not.toBeNull();
    const scoped = within(section as HTMLElement);
    expect(scoped.getByText('6 个 API 源')).toBeInTheDocument();
    expect(scoped.queryByText('此功能可配置的增强源')).not.toBeInTheDocument();
    for (const market of ['A股', '港股', '美股', '全球市场/宏观', '加密货币']) {
      expect(scoped.queryByRole('tab', { name: market })).not.toBeInTheDocument();
    }
    for (const category of ['行情', '基本面', '新闻公告', '社交舆情', '衍生品与资金']) {
      expect(scoped.queryByText(category)).not.toBeInTheDocument();
    }
    expect(scoped.queryByLabelText(/可配置增强源/)).not.toBeInTheDocument();
    expect(scoped.queryByText('X.com')).not.toBeInTheDocument();
    expect(scoped.queryByText('Reddit')).not.toBeInTheDocument();
    expect(scoped.queryByText('首版批准')).not.toBeInTheDocument();
    expect(scoped.queryByText('数据源名称')).not.toBeInTheDocument();
    expect(scoped.queryByText('Custom Vendor')).not.toBeInTheDocument();
    expect(scoped.queryByText('类型')).not.toBeInTheDocument();
    expect(scoped.queryByText('代理地址')).not.toBeInTheDocument();
    expect(scoped.queryByText('请求头名')).not.toBeInTheDocument();
    expect(scoped.queryByText('优先级')).not.toBeInTheDocument();
    expect(scoped.queryByText(/任意 HTTP/i)).not.toBeInTheDocument();
    expect(scoped.queryByText(/JSON 映射/i)).not.toBeInTheDocument();
    expect(scoped.queryByText(/provider 优先级/i)).not.toBeInTheDocument();
    expect(scoped.queryByText(/适用范围/i)).not.toBeInTheDocument();
    for (const approved of ['Tushare', 'Finnhub', 'FRED', 'CoinGecko Pro', 'Coinglass', 'Glassnode']) {
      expect(scoped.getByTestId(`data-source-card-${approved === 'CoinGecko Pro' ? 'coingecko_pro' : approved.toLowerCase()}`)).toBeInTheDocument();
    }
    for (const hidden of ['CSMAR', 'Wind', 'AKShare', '东方财富', 'SEC EDGAR', 'CoinGecko', 'Binance', 'OKX', 'CCXT', 'LongPort']) {
      expect(scoped.queryByText(hidden)).not.toBeInTheDocument();
    }
    expect((section as HTMLElement).querySelectorAll('.ct-source-card')).toHaveLength(6);
    expect((section as HTMLElement).querySelectorAll('.ct-source-category-card')).toHaveLength(0);
    expect((section as HTMLElement).querySelectorAll('.ct-source-row')).toHaveLength(0);
    expect(scoped.queryByText('Custom Vendor')).not.toBeInTheDocument();
    expect(scoped.queryByText(/Custom HTTP|JSON 映射/i)).not.toBeInTheDocument();
    expect(scoped.queryByText('Alpha Vantage')).not.toBeInTheDocument();
    expect(scoped.queryByText('FMP')).not.toBeInTheDocument();
    expect(scoped.queryByText('Polygon')).not.toBeInTheDocument();
    expect(scoped.queryByText('Tiingo')).not.toBeInTheDocument();
    expect(scoped.queryByText('Nasdaq Data Link')).not.toBeInTheDocument();
    expect(scoped.queryByText('IEX Cloud')).not.toBeInTheDocument();
    expect(scoped.queryByText('BEA')).not.toBeInTheDocument();
    expect(scoped.queryByText('EIA')).not.toBeInTheDocument();
    expect(scoped.queryByText('NewsAPI')).not.toBeInTheDocument();
    expect(scoped.queryByText('X.com')).not.toBeInTheDocument();
    expect(scoped.queryByText('Reddit')).not.toBeInTheDocument();
    expect(scoped.queryByText('CoinMarketCap')).not.toBeInTheDocument();
    expect(scoped.queryByText('LunarCrush')).not.toBeInTheDocument();

    const tushareCard = scoped.getByTestId('data-source-card-tushare');
    expect(scoped.queryByText('当前编辑')).not.toBeInTheDocument();
    expect(scoped.getByText('配置详情：Tushare')).toBeInTheDocument();
    expect(tushareCard).toHaveClass('is-active');
    expect(within(tushareCard).getByText('API Key：未填写')).toBeInTheDocument();
    expect(within(tushareCard).getByText('接口地址：默认')).toBeInTheDocument();
    expect(within(tushareCard).getByText('启用开关：未启用')).toBeInTheDocument();

    const coinglassCard = scoped.getByTestId('data-source-card-coinglass');
    fireEvent.click(within(coinglassCard).getByRole('button', { name: '编辑' }));
    expect(scoped.getByText('配置详情：Coinglass')).toBeInTheDocument();
  });

  it('shows actionable error when enabling enhanced source without passing real test', async () => {
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/api/ui/get-report-cleanup-settings')) {
        return reportCleanupJson();
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
          qrCodeImageDataUrl: 'data:image/png;base64,s05-qr',
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
      if (url.includes('/api/ui/save-data-source-instance')) {
        const body = JSON.parse(String(init?.body || '{}'));
        expect(body.instance).not.toHaveProperty('proxyUrl');
        expect(body.instance).not.toHaveProperty('headerName');
        expect(body.instance).not.toHaveProperty('priority');
        if (body.instance?.enabled) {
          return errorJson('数据源连接或鉴权失败，请到设置页更新后重试。');
        }
        return json({
          instanceId: 'ds-3',
          supportedType: 'tushare',
          group: 'cn_a_data',
          displayName: 'Tushare',
          enabled: false,
          state: 'draft',
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
    fireEvent.click(screen.getByRole('tab', { name: '数据源' }));
    const section = (await screen.findByRole('heading', { name: '增强数据源' })).closest('section');
    expect(section).not.toBeNull();
    const scoped = within(section as HTMLElement);
    fireEvent.click(scoped.getByRole('checkbox'));
    fireEvent.click(scoped.getByRole('button', { name: '保存数据源' }));

    await waitFor(() => {
      expect(screen.getByText('数据源连接或鉴权失败，请到设置页更新后重试。')).toBeInTheDocument();
    });
  });
});
