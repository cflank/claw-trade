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

describe('settings-enhanced-sources', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
    vi.useRealTimers();
  });

  it('renders only approved enhanced source and hides forbidden controls', async () => {
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
          source('alpha_vantage', 'Alpha Vantage'),
          source('finnhub', 'Finnhub'),
          source('fred', 'FRED', 'global_macro'),
          source('coingecko_pro', 'CoinGecko Pro', 'crypto_data'),
          source('coinglass', 'Coinglass', 'crypto_data'),
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
    expect(scoped.getAllByText('此功能可配置的增强源').length).toBeGreaterThanOrEqual(3);
    expect(scoped.getByRole('tab', { name: 'A股' })).toHaveAttribute('aria-selected', 'true');
    expect(scoped.getByRole('tab', { name: '港股' })).toBeInTheDocument();
    expect(scoped.getByRole('tab', { name: '美股' })).toBeInTheDocument();
    expect(scoped.getByRole('tab', { name: '全球市场/宏观' })).toBeInTheDocument();
    expect(scoped.getByRole('tab', { name: '加密货币' })).toBeInTheDocument();
    expect(scoped.getByTestId('data-source-category-quotes')).toBeInTheDocument();
    expect(scoped.getByTestId('data-source-category-fundamental')).toBeInTheDocument();
    expect(scoped.getByTestId('data-source-category-news')).toBeInTheDocument();
    expect(scoped.queryByTestId('data-source-category-social')).not.toBeInTheDocument();
    expect(scoped.getByLabelText('行情可配置增强源')).toHaveValue('tushare');
    expect(scoped.getByLabelText('基本面可配置增强源')).toHaveValue('tushare');
    expect(scoped.getByLabelText('新闻公告可配置增强源')).toHaveValue('tushare');
    expect(scoped.queryByLabelText('社交舆情可配置增强源')).not.toBeInTheDocument();
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
    for (const approved of ['Tushare']) {
      expect(scoped.getAllByText(approved).length).toBeGreaterThanOrEqual(1);
    }
    for (const hidden of ['OpenBB', 'AKShare', '东方财富', 'SEC EDGAR', 'CoinGecko', 'Binance', 'OKX', 'CCXT', 'LongPort', 'Wind']) {
      expect(scoped.queryByText(hidden)).not.toBeInTheDocument();
    }
    expect((section as HTMLElement).querySelectorAll('.ct-source-category-card')).toHaveLength(3);
    expect((section as HTMLElement).querySelectorAll('.ct-source-row')).toHaveLength(0);
    expect(scoped.queryByText('Custom Vendor')).not.toBeInTheDocument();
    expect(scoped.queryByText(/Custom HTTP|JSON 映射/i)).not.toBeInTheDocument();

    fireEvent.click(scoped.getByRole('tab', { name: '美股' }));
    expect(scoped.getAllByText('Alpha Vantage').length).toBeGreaterThanOrEqual(1);
    expect(scoped.getAllByText('Finnhub').length).toBeGreaterThanOrEqual(1);
    expect(scoped.queryByText('Polygon')).not.toBeInTheDocument();
    expect(scoped.queryByText('FMP')).not.toBeInTheDocument();
    expect(scoped.queryByText('Bloomberg')).not.toBeInTheDocument();

    fireEvent.click(scoped.getByRole('tab', { name: '全球市场/宏观' }));
    expect(scoped.getAllByText('FRED').length).toBeGreaterThanOrEqual(1);
    expect(scoped.queryByText('BEA')).not.toBeInTheDocument();
    expect(scoped.queryByText('EIA')).not.toBeInTheDocument();
    expect(scoped.queryByTestId('data-source-category-social')).not.toBeInTheDocument();

    fireEvent.click(scoped.getByRole('tab', { name: '加密货币' }));
    expect(scoped.getAllByText('CoinGecko Pro').length).toBeGreaterThanOrEqual(1);
    expect(scoped.getByText('配置详情：CoinGecko Pro')).toBeInTheDocument();
    expect(scoped.getAllByText('Coinglass').length).toBeGreaterThanOrEqual(1);
    expect(scoped.queryByText('Coin Metrics')).not.toBeInTheDocument();
    expect(scoped.queryByText('Dune')).not.toBeInTheDocument();

    fireEvent.click(scoped.getByRole('tab', { name: 'A股' }));
    const cnCard = scoped.getByTestId('data-source-category-fundamental');
    expect(scoped.queryByText('当前编辑')).not.toBeInTheDocument();
    expect(scoped.getByText('配置详情：Tushare')).toBeInTheDocument();
    expect(cnCard).toHaveClass('is-active');
    expect(within(cnCard).getByText('API Key：未填写')).toBeInTheDocument();
    expect(within(cnCard).getByText('接口地址：默认')).toBeInTheDocument();
    expect(within(cnCard).getByText('启用开关：未启用')).toBeInTheDocument();

    fireEvent.click(scoped.getByRole('tab', { name: '港股' }));
    expect(scoped.queryByTestId('data-source-category-social')).not.toBeInTheDocument();

    fireEvent.click(scoped.getByRole('tab', { name: '全球市场/宏观' }));
    const globalNewsCard = scoped.getByTestId('data-source-category-news');
    expect(within(globalNewsCard).getByLabelText('全球新闻可配置增强源')).toHaveValue('finnhub');
    expect(scoped.queryByTestId('data-source-category-social')).not.toBeInTheDocument();
  });

  it('shows actionable error when enabling enhanced source without passing real test', async () => {
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
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
