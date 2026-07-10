import { readFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import { describe, expect, it } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import type {
  ChannelStatusForUser,
  DataSourceInstanceDraftInput,
  LlmConfigDraft,
  ReportCleanupSettingsForUser,
} from '../api/contracts';
import { SettingsSections } from '../components/SettingsSections';

const noop = () => {};

const channel: ChannelStatusForUser = {
  channelKind: 'wechat_clawbot',
  onboardingState: 'completed',
  state: 'disconnected',
  displayName: '微信 ClawBot',
  accountLabel: null,
  canSendText: false,
  canSendFile: false,
  qrCodeImageDataUrl: 'data:image/png;base64,settings-sections-qr',
  qrCodeRefreshRequired: false,
};

const llm: LlmConfigDraft = {
  provider: 'deepseek',
  defaultModel: 'deepseek-chat',
  status: 'saved',
  reportModelStatus: {
    state: 'saved_unverified',
    blocked: true,
    ready: false,
    userMessage: '报告模型已保存但尚未测试通过，请先执行模型测试。',
    checkedAt: '2026-05-23T08:00:00Z',
  },
  embedding: {
    provider: '',
    model: '',
    endpointUrl: '',
    dimension: '',
    apiKeyReplacement: '',
    enabled: false,
  },
};

const dataSourceDraft: DataSourceInstanceDraftInput = {
  supportedType: 'tushare',
  group: 'cn_a_data',
  displayName: 'Tushare',
  enabled: false,
  apiKeyReplacement: '',
  endpointUrl: '',
  state: 'draft',
  requiresKey: true,
};

const reportCleanup: ReportCleanupSettingsForUser = {
  reportRetentionDays: 14,
};

const fixedSources = [
  { instanceId: 'ds-tushare', supportedType: 'tushare', group: 'cn_a_data', displayName: 'Tushare' },
  { instanceId: 'ds-wind', supportedType: 'wind', group: 'cn_a_data', displayName: 'Wind' },
  { instanceId: 'ds-newsapi', supportedType: 'newsapi', group: 'global_news', displayName: 'NewsAPI' },
  { instanceId: 'ds-x', supportedType: 'x', group: 'global_social', displayName: 'X.com' },
  { instanceId: 'ds-reddit', supportedType: 'reddit', group: 'global_social', displayName: 'Reddit' },
  { instanceId: 'ds-fmp', supportedType: 'fmp', group: 'global_data', displayName: 'FMP' },
  { instanceId: 'ds-polygon', supportedType: 'polygon', group: 'global_data', displayName: 'Polygon' },
  { instanceId: 'ds-finnhub', supportedType: 'finnhub', group: 'global_data', displayName: 'Finnhub' },
  { instanceId: 'ds-tiingo', supportedType: 'tiingo', group: 'global_data', displayName: 'Tiingo' },
  { instanceId: 'ds-nasdaq', supportedType: 'nasdaq_data_link', group: 'global_data', displayName: 'Nasdaq Data Link' },
  { instanceId: 'ds-fred', supportedType: 'fred', group: 'global_macro', displayName: 'FRED' },
  { instanceId: 'ds-coingecko-pro', supportedType: 'coingecko_pro', group: 'crypto_data', displayName: 'CoinGecko Pro' },
  { instanceId: 'ds-coinglass', supportedType: 'coinglass', group: 'crypto_data', displayName: 'Coinglass' },
  { instanceId: 'ds-glassnode', supportedType: 'glassnode', group: 'crypto_data', displayName: 'Glassnode' },
  { instanceId: 'ds-future-api', supportedType: 'future_api_source', group: 'global_data', displayName: 'Future API Source' },
] as const;

const stylesPath = resolve(dirname(fileURLToPath(import.meta.url)), '../styles.css');
const stylesText = readFileSync(stylesPath, 'utf-8');

describe('settings-css-sections', () => {
  it('renders settings sections in model/general/data tabs and applies section classes', () => {
    render(
        <SettingsSections
          channel={channel}
          llm={llm}
          dataSources={fixedSources.map((item, index) => ({
            ...item,
            enabled: index === 0,
            apiKeyMasked: index === 0 ? 'ts-****' : null,
            endpointUrl: index === 0 ? 'https://api.tushare.pro' : null,
            state: index === 0 ? 'enabled' : 'draft',
            lastSuccessAt: index === 0 ? '2026-05-23T08:10:00Z' : null,
            lastTestAt: index === 0 ? '2026-05-23T08:10:00Z' : null,
          }))}
        dataSourceDraft={dataSourceDraft}
        reportCleanup={reportCleanup}
        selectionAutoRefreshEnabled
        sectionErrors={{}}
        channelActionBusy={false}
        channelActionMessage=""
        llmActionBusy={false}
        llmActionMessage=""
        embeddingActionBusy={false}
        embeddingActionMessage=""
        embeddingActionOk={false}
        dataSourceActionBusy={false}
        dataSourceActionMessage=""
        cleanupActionBusy={false}
        cleanupActionMessage=""
        selectionAutoRefreshActionBusy={false}
        selectionAutoRefreshActionMessage=""
        resetActionBusy={false}
        resetActionMessage=""
        factoryResetActionBusy={false}
        factoryResetActionMessage=""
        updateActionBusy={false}
        updateActionMessage=""
        updateApplyBusy={false}
        canInstallUpdate={false}
        productionMaintenance={null}
        onReconnectChannel={noop}
        onDisconnectChannel={noop}
        onSkipWechatSetup={noop}
        onRefreshChannel={noop}
        onLlmChange={noop}
        onSaveLlm={noop}
        onTestLlm={noop}
        onSaveEmbedding={noop}
        onTestEmbedding={noop}
        onDataSourceDraftChange={noop}
        onEditDataSource={noop}
        onSaveDataSource={noop}
        onTestDataSource={noop}
        onReportCleanupChange={noop}
        onSaveReportCleanup={noop}
        onSelectionAutoRefreshChange={noop}
        onSaveSelectionAutoRefresh={noop}
        onResetSettings={noop}
        onFactoryReset={noop}
        onCheckForUpdate={noop}
        onInstallUpdate={noop}
      />,
    );

    expect(screen.getByRole('tab', { name: '模型' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: '通用' })).toHaveAttribute('aria-selected', 'false');
    expect(screen.getByRole('tab', { name: '数据源' })).toHaveAttribute('aria-selected', 'false');
    const titles = screen.getAllByRole('heading', { level: 2 }).map((item) => item.textContent?.trim());
    expect(titles).toEqual(['报告模型', 'Embedding']);
    const modelSection = screen.getByTestId('settings-section-report-model');
    expect(modelSection).toBeInTheDocument();
    expect(screen.getByTestId('settings-section-embedding')).toBeInTheDocument();
    expect(screen.queryByTestId('settings-section-data-sources')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-section-wechat')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-section-reset')).not.toBeInTheDocument();
    expect(within(modelSection).getByRole('heading', { name: '费用估算' })).toBeInTheDocument();
    expect(within(modelSection).getByText(/DeepSeek 中文官方价格页/)).toBeInTheDocument();
    fireEvent.change(within(modelSection).getByLabelText('命中缓存输入'), { target: { value: '1000000' } });
    fireEvent.change(within(modelSection).getByLabelText('未命中缓存输入'), { target: { value: '1000000' } });
    fireEvent.change(within(modelSection).getByLabelText('输出'), { target: { value: '1000000' } });
    expect(within(modelSection).getByText('¥3.02')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: '数据源' }));
    expect(screen.getByRole('tab', { name: '数据源' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByTestId('settings-section-data-sources')).toBeInTheDocument();
    expect(screen.queryByTestId('settings-section-report-model')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-section-wechat')).not.toBeInTheDocument();
    expect(screen.queryByTestId('settings-section-reset')).not.toBeInTheDocument();

    const sourceSection = screen.getByTestId('settings-section-data-sources');
    expect(within(sourceSection).getAllByText('Tushare').length).toBeGreaterThanOrEqual(1);
    expect(within(sourceSection).queryByLabelText('名称')).not.toBeInTheDocument();
    expect(within(sourceSection).queryByLabelText('代理地址')).not.toBeInTheDocument();
    expect(within(sourceSection).queryByLabelText('请求头名')).not.toBeInTheDocument();
    expect(within(sourceSection).queryByLabelText('优先级')).not.toBeInTheDocument();
    expect(sourceSection.querySelectorAll('.ct-source-card')).toHaveLength(6);
    expect(sourceSection.querySelectorAll('.ct-source-category-card')).toHaveLength(0);
    expect(sourceSection.querySelectorAll('.ct-source-row')).toHaveLength(0);
    expect(sourceSection.querySelectorAll('.ct-source-list')).toHaveLength(0);
    expect(within(sourceSection).queryByRole('tab', { name: 'A股' })).not.toBeInTheDocument();
    expect(within(sourceSection).queryByRole('tab', { name: '美股' })).not.toBeInTheDocument();
    expect(within(sourceSection).queryByRole('tab', { name: '加密货币' })).not.toBeInTheDocument();
    expect(within(sourceSection).queryByLabelText(/可配置增强源/)).not.toBeInTheDocument();
    expect(within(sourceSection).getByTestId('data-source-card-finnhub')).toBeInTheDocument();
    expect(within(sourceSection).getByTestId('data-source-card-coinglass')).toBeInTheDocument();
    expect(within(sourceSection).getByText('配置详情：Tushare')).toBeInTheDocument();
    expect(stylesText).toMatch(/\.ct-source-editor-panel\s*{[^}]*border:\s*2px solid var\(--ct-primary\);/s);
    expect(stylesText).toMatch(/\.ct-settings-section\s*{[^}]*border:\s*1px solid var\(--ct-border-strong\);[^}]*box-shadow:\s*var\(--ct-shadow-panel\);/s);
    expect(stylesText).toMatch(/\.ct-section-head\s*{[^}]*padding-bottom:\s*12px;[^}]*border-bottom:\s*1px solid var\(--ct-border\);/s);
    expect(stylesText).toMatch(/\.ct-source-grid\s*{[^}]*grid-template-columns:\s*repeat\(2,\s*minmax\(0,\s*1fr\)\);/s);
    expect(stylesText).toMatch(/\.ct-source-card\s*{[^}]*border:\s*1px solid var\(--ct-border\);[^}]*border-radius:\s*var\(--ct-radius-sm\);/s);
    expect(stylesText).toMatch(/\.ct-source-card\.is-active\s*{[^}]*box-shadow:\s*inset 3px 0 0 var\(--ct-primary\);/s);
    expect(stylesText).not.toContain('.ct-source-market-tabs');
    expect(stylesText).not.toContain('.ct-source-category-grid');
    expect(stylesText).not.toContain('.ct-source-list');
    expect(stylesText).not.toContain('.ct-source-row');

    expect(within(sourceSection).queryByText('其它 API 源')).not.toBeInTheDocument();
    expect(within(sourceSection).queryByText('Future API Source')).not.toBeInTheDocument();
    expect(within(sourceSection).queryByText('FMP')).not.toBeInTheDocument();
    expect(within(sourceSection).queryByText('Polygon')).not.toBeInTheDocument();
    expect(within(sourceSection).queryByText('Tiingo')).not.toBeInTheDocument();
    expect(within(sourceSection).queryByText('Nasdaq Data Link')).not.toBeInTheDocument();
    expect(within(sourceSection).queryByText('NewsAPI')).not.toBeInTheDocument();
    expect(within(sourceSection).queryByText('X.com')).not.toBeInTheDocument();
    expect(within(sourceSection).queryByText('Reddit')).not.toBeInTheDocument();

    fireEvent.click(screen.getByRole('tab', { name: '通用' }));
    expect(screen.getByRole('tab', { name: '通用' })).toHaveAttribute('aria-selected', 'true');
    const cleanupSection = screen.getByTestId('settings-section-report-cleanup');
    const wechatSection = screen.getByTestId('settings-section-wechat');
    expect(screen.getByTestId('settings-section-reset')).toBeInTheDocument();
    expect(screen.queryByTestId('settings-section-data-sources')).not.toBeInTheDocument();
    expect(within(cleanupSection).getByLabelText('报告保留时间')).toHaveValue('14');
    expect(within(cleanupSection).getByRole('button', { name: '保存报告保留时间' })).toBeInTheDocument();
    expect(within(wechatSection).getByText('请先在微信端启用插件：')).toBeInTheDocument();
    expect(within(wechatSection).getByText('我 → 设置 → 插件 → 微信 ClawBot')).toBeInTheDocument();
    expect(within(wechatSection).queryByText('Channel ID')).not.toBeInTheDocument();
    expect(within(wechatSection).queryByText('gateway')).not.toBeInTheDocument();

    const allText = (screen.getByTestId('settings-page').textContent ?? '').toLowerCase();
    for (const forbidden of ['profile', '默认币种', '报告方案', 'tab', 'worker', 'debate', 'risk']) {
      expect(allText).not.toContain(forbidden);
    }
  });
});
