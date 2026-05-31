import { useState } from 'react';
import type {
  ChannelStatusForUser,
  DataSourceInstanceDraftInput,
  DataSourceInstanceForUser,
  LlmConfigDraft,
} from '../api/contracts';
import {
  LLM_PROVIDER_PRESETS,
  modelOptionsForProvider,
  normalizeLlmModelValue,
  presetPatchForProvider,
} from './llmCatalog';

function formatDate(value?: string | null) {
  if (!value) {
    return '—';
  }
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

function channelStateLabel(value?: ChannelStatusForUser['state']) {
  switch (value) {
    case 'connected':
      return '已连接';
    case 'connecting':
      return '连接中';
    case 'reconnecting':
      return '重连中';
    case 'disconnected':
      return '待连接';
    case 'error':
      return '不可用';
    default:
      return '未知';
  }
}

function qrHelpText(channel: ChannelStatusForUser | null) {
  if (channel?.state === 'connected') {
    return '当前微信账号已连接，可接收通知。';
  }
  if (channel?.qrCodeImageDataUrl) {
    return '请先在微信端启用插件后，返回本页扫描二维码完成连接。';
  }
  if (channel?.qrCodeRefreshRequired) {
    return '当前通道未返回二维码，点击刷新二维码再试。';
  }
  return '未获取到二维码时，可先进入浏览器工作台。';
}

function dataSourceStateLabel(value: DataSourceInstanceForUser['state']) {
  switch (value) {
    case 'enabled':
      return '已启用';
    case 'validated':
      return '已验证';
    case 'testing':
      return '检测中';
    case 'disabled':
      return '已停用';
    case 'degraded':
      return '异常';
    case 'rejected':
      return '未通过';
    default:
      return '草稿';
  }
}

function recentDataSourceTestLabel(state: DataSourceInstanceForUser['state'], lastTestAt?: string | null) {
  const label = dataSourceStateLabel(state);
  if (!lastTestAt) {
    return label;
  }
  return `${label}（${formatDate(lastTestAt)}）`;
}

function dataSourceKeyLabel(item: DataSourceInstanceForUser) {
  return item.apiKeyMasked ? '已填写' : '未填写';
}

function dataSourceEndpointLabel(item: DataSourceInstanceForUser) {
  return item.endpointUrl?.trim() ? item.endpointUrl : '默认';
}

function embeddingDraft(llm: LlmConfigDraft) {
  return (
    llm.embedding ?? {
      provider: '',
      model: '',
      endpointUrl: '',
      dimension: '',
      apiKeyReplacement: '',
      enabled: false,
    }
  );
}

function embeddingStatusLabel(embedding: ReturnType<typeof embeddingDraft>) {
  const providerConfigured = Boolean(embedding.provider?.trim());
  const modelConfigured = Boolean(embedding.model?.trim());
  if (providerConfigured && modelConfigured) {
    return '已配置（可选）';
  }
  if (providerConfigured || modelConfigured) {
    return '配置不完整（可选，不阻断报告）';
  }
  return '未配置（可选）';
}

function embeddingStatusTone(embedding: ReturnType<typeof embeddingDraft>) {
  return embedding.enabled ? 'ready' : 'pending';
}

function modelStatusState(llm: LlmConfigDraft) {
  return llm.reportModelStatus?.state ?? 'unconfigured';
}

function modelStatusLabel(llm: LlmConfigDraft) {
  switch (modelStatusState(llm)) {
    case 'ready':
      return '可用';
    case 'failed':
      return '测试失败';
    case 'saved_unverified':
      return '已保存未验证';
    default:
      return '未配置';
  }
}

function modelStatusMessage(llm: LlmConfigDraft) {
  const fromStatus = llm.reportModelStatus?.userMessage?.trim();
  const sanitize = (value: string) => {
    const lowered = value.toLowerCase();
    if (lowered.includes('provider attempt') || lowered.includes('runtime') || lowered.includes('gateway')) {
      return '报告模型连接测试失败，请检查服务商、模型、API Key 和接口地址后重试。';
    }
    return value;
  };
  if (fromStatus) {
    return sanitize(fromStatus);
  }
  if (llm.lastTestMessage?.trim()) {
    return sanitize(llm.lastTestMessage.trim());
  }
  if (modelStatusState(llm) === 'ready') {
    return '报告模型可用。';
  }
  if (modelStatusState(llm) === 'failed') {
    return '报告模型连接测试失败，请检查模型配置后重试。';
  }
  if (modelStatusState(llm) === 'saved_unverified') {
    return '报告模型已保存但尚未测试通过，请先执行模型测试。';
  }
  return '请先填写报告模型并执行测试。';
}

function modelStatusTone(llm: LlmConfigDraft) {
  switch (modelStatusState(llm)) {
    case 'ready':
      return 'ready';
    case 'failed':
      return 'error';
    default:
      return 'pending';
  }
}

function channelStatusTone(channel: ChannelStatusForUser | null) {
  switch (channel?.state) {
    case 'connected':
      return 'ready';
    case 'error':
      return 'error';
    default:
      return 'pending';
  }
}

function dataSourceStatusTone(state: DataSourceInstanceForUser['state']) {
  switch (state) {
    case 'enabled':
    case 'validated':
      return 'ready';
    case 'degraded':
    case 'rejected':
      return 'error';
    default:
      return 'pending';
  }
}

type MarketSourceTab = {
  id: string;
  title: string;
  categories: Array<{
    id: string;
    title: string;
    types: string[];
  }>;
};

type SettingsMainTab = 'model' | 'general' | 'data';

const SETTINGS_MAIN_TABS: Array<{ id: SettingsMainTab; title: string }> = [
  { id: 'model', title: '模型' },
  { id: 'general', title: '通用' },
  { id: 'data', title: '数据源' },
];

const DATA_SOURCE_MARKETS: MarketSourceTab[] = [
  {
    id: 'cn-a',
    title: 'A股',
    categories: [
      { id: 'quotes', title: '行情', types: ['tushare', 'wind', 'choice', 'ifind', 'joinquant', 'ricequant'] },
      {
        id: 'fundamental',
        title: '基本面',
        types: ['tushare', 'wind', 'choice', 'ifind', 'csmar', 'resset', 'joinquant', 'ricequant'],
      },
      { id: 'news', title: '新闻公告', types: ['tushare', 'wind', 'choice', 'ifind'] },
    ],
  },
  {
    id: 'hk',
    title: '港股',
    categories: [
      {
        id: 'quotes',
        title: '行情',
        types: ['longport', 'bloomberg', 'lseg_refinitiv'],
      },
      {
        id: 'fundamental',
        title: '基本面',
        types: ['longport', 'bloomberg', 'lseg_refinitiv', 'factset', 'morningstar', 'sp_capital_iq'],
      },
      { id: 'news', title: '新闻公告', types: ['finnhub', 'newsapi', 'reuters', 'dow_jones', 'bloomberg', 'lseg_refinitiv'] },
    ],
  },
  {
    id: 'us',
    title: '美股',
    categories: [
      {
        id: 'quotes',
        title: '行情',
        types: [
          'polygon',
          'tiingo',
          'finnhub',
          'iex_cloud',
          'twelve_data',
          'alpha_vantage',
          'eodhd',
          'marketstack',
          'intrinio',
          'bloomberg',
          'lseg_refinitiv',
          'tradingview',
          'barchart',
          'finazon',
        ],
      },
      {
        id: 'fundamental',
        title: '基本面',
        types: [
          'fmp',
          'nasdaq_data_link',
          'intrinio',
          'eodhd',
          'finnhub',
          'bloomberg',
          'lseg_refinitiv',
          'factset',
          'morningstar',
          'sp_capital_iq',
        ],
      },
      { id: 'filings', title: '公告披露', types: ['intrinio', 'fmp', 'finnhub', 'eodhd'] },
      { id: 'news', title: '新闻', types: ['benzinga', 'finnhub', 'newsapi', 'reuters', 'dow_jones', 'bloomberg', 'lseg_refinitiv'] },
      { id: 'social', title: '社交舆情', types: ['x', 'reddit', 'finnhub'] },
    ],
  },
  {
    id: 'global',
    title: '全球市场/宏观',
    categories: [
      {
        id: 'quotes',
        title: '跨市场行情',
        types: [
          'twelve_data',
          'marketstack',
          'eodhd',
          'alpha_vantage',
          'bloomberg',
          'lseg_refinitiv',
          'tradingview',
          'barchart',
          'finazon',
        ],
      },
      {
        id: 'fundamental',
        title: '跨市场基本面',
        types: ['fmp', 'intrinio', 'eodhd', 'finnhub', 'nasdaq_data_link', 'factset', 'morningstar', 'sp_capital_iq'],
      },
      {
        id: 'macro',
        title: '宏观经济',
        types: ['fred', 'bea', 'eia'],
      },
      { id: 'news', title: '全球新闻', types: ['newsapi', 'benzinga', 'finnhub', 'reuters', 'dow_jones', 'bloomberg', 'lseg_refinitiv'] },
      { id: 'social', title: '全球社交舆情', types: ['x', 'reddit'] },
    ],
  },
  {
    id: 'crypto',
    title: '加密货币',
    categories: [
      {
        id: 'quotes',
        title: '行情交易所',
        types: ['coingecko_pro', 'coinmarketcap', 'cryptocompare', 'kaiko', 'amberdata', 'twelve_data', 'alpha_vantage'],
      },
      {
        id: 'fundamental',
        title: '链上与基本面',
        types: [
          'coinglass',
          'glassnode',
          'santiment',
          'messari',
          'coingecko_pro',
          'coinmetrics',
          'dune',
          'nansen',
          'token_terminal',
          'the_graph',
          'amberdata',
          'kaiko',
        ],
      },
      { id: 'derivatives', title: '衍生品与资金', types: ['coinglass', 'amberdata', 'kaiko'] },
      { id: 'news', title: '新闻与事件', types: ['cryptocompare', 'newsapi', 'messari'] },
      { id: 'social', title: '社交情绪', types: ['lunarcrush', 'x', 'reddit', 'santiment'] },
    ],
  },
];

function dataSourceByType(dataSources: DataSourceInstanceForUser[]) {
  return new Map(dataSources.map((item) => [item.supportedType, item]));
}

function selectedSourceForCategory(
  category: MarketSourceTab['categories'][number],
  sourceMap: Map<string, DataSourceInstanceForUser>,
  dataSourceDraft: DataSourceInstanceDraftInput,
) {
  if (category.types.includes(dataSourceDraft.supportedType)) {
    const selected = sourceMap.get(dataSourceDraft.supportedType);
    if (selected) {
      return selected;
    }
  }
  const enabled = category.types.map((type) => sourceMap.get(type)).find((item) => item?.enabled);
  return enabled ?? category.types.map((type) => sourceMap.get(type)).find(Boolean) ?? null;
}

export function SettingsSections({
  channel,
  llm,
  dataSources,
  dataSourceDraft,
  sectionErrors,
  channelActionBusy,
  channelActionMessage,
  llmActionBusy,
  llmActionMessage,
  embeddingActionBusy,
  embeddingActionMessage,
  embeddingActionOk,
  dataSourceActionBusy,
  dataSourceActionMessage,
  resetActionBusy,
  resetActionMessage,
  onReconnectChannel,
  onDisconnectChannel,
  onSkipWechatSetup,
  onRefreshChannel,
  onLlmChange,
  onSaveLlm,
  onTestLlm,
  onSaveEmbedding,
  onTestEmbedding,
  onDataSourceDraftChange,
  onEditDataSource,
  onSaveDataSource,
  onTestDataSource,
  onResetSettings,
}: {
  channel: ChannelStatusForUser | null;
  llm: LlmConfigDraft;
  dataSources: DataSourceInstanceForUser[];
  dataSourceDraft: DataSourceInstanceDraftInput;
  sectionErrors: { channel?: string; llm?: string; embedding?: string; dataSources?: string; reset?: string };
  channelActionBusy: boolean;
  channelActionMessage: string;
  llmActionBusy: boolean;
  llmActionMessage: string;
  embeddingActionBusy: boolean;
  embeddingActionMessage: string;
  embeddingActionOk: boolean;
  dataSourceActionBusy: boolean;
  dataSourceActionMessage: string;
  resetActionBusy: boolean;
  resetActionMessage: string;
  onReconnectChannel: () => void;
  onDisconnectChannel: () => void;
  onSkipWechatSetup: () => void;
  onRefreshChannel: () => void;
  onLlmChange: (patch: Partial<LlmConfigDraft>) => void;
  onSaveLlm: () => void;
  onTestLlm: () => void;
  onSaveEmbedding: () => void;
  onTestEmbedding: () => void;
  onDataSourceDraftChange: (patch: Partial<DataSourceInstanceDraftInput>) => void;
  onEditDataSource: (item: DataSourceInstanceForUser) => void;
  onSaveDataSource: () => void;
  onTestDataSource: () => void;
  onResetSettings: () => void;
}) {
  const embedding = embeddingDraft(llm);
  const modelFieldError = modelStatusState(llm) === 'failed';
  const dataSourceMessageTone = dataSourceStatusTone(dataSourceDraft.state ?? 'draft');
  const modelOptions = modelOptionsForProvider(llm.provider, llm.defaultModel);
  const selectedModel = normalizeLlmModelValue(llm.provider, llm.defaultModel);
  const [activeSettingsTab, setActiveSettingsTab] = useState<SettingsMainTab>('model');
  const [activeSourceMarket, setActiveSourceMarket] = useState(DATA_SOURCE_MARKETS[0].id);
  const sourceMap = dataSourceByType(dataSources);
  const visibleMarkets = DATA_SOURCE_MARKETS.map((market) => ({
    ...market,
    categories: market.categories.map((category) => ({
      ...category,
      sources: category.types.map((type) => sourceMap.get(type)).filter(Boolean) as DataSourceInstanceForUser[],
      selected: selectedSourceForCategory(category, sourceMap, dataSourceDraft),
    })).filter((category) => category.sources.length > 0),
  })).filter((market) => market.categories.length > 0);
  const activeMarket = visibleMarkets.find((item) => item.id === activeSourceMarket) ?? visibleMarkets[0] ?? null;
  const sourceCategories = activeMarket?.categories ?? [];
  const hasDataSources = dataSources.length > 0;
  const canEditDataSource = hasDataSources && Boolean(dataSourceDraft.supportedType);
  const selectSourceMarket = (marketId: string) => {
    setActiveSourceMarket(marketId);
    const market = visibleMarkets.find((item) => item.id === marketId);
    const firstSource = market?.categories.flatMap((category) => category.sources)[0] ?? null;
    if (firstSource && firstSource.supportedType !== dataSourceDraft.supportedType) {
      onEditDataSource(firstSource);
    }
  };
  const selectDataSource = (supportedType: string) => {
    const item = sourceMap.get(supportedType);
    if (item) {
      onEditDataSource(item);
    }
  };
  return (
    <main className="ct-settings" data-testid="settings-page">
      <div className="ct-settings-tabs" role="tablist" aria-label="设置分类">
        {SETTINGS_MAIN_TABS.map((tab) => (
          <button
            type="button"
            role="tab"
            id={`settings-tab-${tab.id}`}
            aria-selected={activeSettingsTab === tab.id}
            aria-controls={`settings-panel-${tab.id}`}
            className={`ct-settings-tab${activeSettingsTab === tab.id ? ' is-active' : ''}`}
            key={tab.id}
            onClick={() => setActiveSettingsTab(tab.id)}
          >
            {tab.title}
          </button>
        ))}
      </div>
      <div
        className="ct-settings-tab-panel"
        id={`settings-panel-${activeSettingsTab}`}
        role="tabpanel"
        aria-labelledby={`settings-tab-${activeSettingsTab}`}
      >
        {activeSettingsTab === 'model' ? (
          <>
      <section className="ct-settings-section" data-testid="settings-section-report-model">
        <div className="ct-section-head">
          <h2>报告模型</h2>
          <span className={`ct-status-pill ct-status-${modelStatusTone(llm)}`}>{modelStatusLabel(llm)}</span>
        </div>
        <p className="ct-section-desc">它负责写报告、分析和决策。保存后还要测试通过，才允许生成报告。</p>
        <div className="ct-model-grid">
          <label className={`ct-field${modelFieldError ? ' is-error' : ''}`}>
            <span>服务商</span>
            <select
              value={llm.provider}
              onChange={(event) => onLlmChange(presetPatchForProvider(event.target.value as LlmConfigDraft['provider']))}
            >
              {LLM_PROVIDER_PRESETS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className={`ct-field${modelFieldError ? ' is-error' : ''}`}>
            <span>模型</span>
            <select
              value={selectedModel}
              onChange={(event) => onLlmChange({ defaultModel: event.target.value })}
            >
              {modelOptions.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className={`ct-field${modelFieldError ? ' is-error' : ''}`}>
            <span>接口地址</span>
            <input
              value={llm.endpointUrl ?? ''}
              onChange={(event) => onLlmChange({ endpointUrl: event.target.value })}
              placeholder="留空使用默认接口"
            />
          </label>
          <label className={`ct-field${modelFieldError ? ' is-error' : ''}`}>
            <span>API Key</span>
            <input
              type="password"
              value={llm.apiKeyReplacement ?? ''}
              onChange={(event) => onLlmChange({ apiKeyReplacement: event.target.value })}
              placeholder={llm.apiKeyMasked ? `${llm.apiKeyMasked}，留空不更换` : '填写新密钥'}
            />
          </label>
        </div>
        <div className="ct-kv">
          <span>状态</span>
          <span>{modelStatusLabel(llm)}</span>
        </div>
        <div className="ct-kv">
          <span>说明</span>
          <span>{modelStatusMessage(llm)}</span>
        </div>
        <div className="ct-button-row ct-settings-actions">
          <button type="button" className="ct-button" onClick={onTestLlm} disabled={llmActionBusy}>
            {llmActionBusy ? '报告模型测试中...' : '测试报告模型连接'}
          </button>
          <button type="button" className="ct-button ct-button-secondary" onClick={onSaveLlm} disabled={llmActionBusy}>
            保存报告模型配置
          </button>
        </div>
        {llmActionMessage ? (
          <div className={`ct-inline-alert ${modelFieldError ? 'is-error' : 'is-success'}`}>{llmActionMessage}</div>
        ) : null}
        {sectionErrors.llm ? <div className="ct-inline-alert is-error">{sectionErrors.llm}</div> : null}
      </section>

      <section className="ct-settings-section" data-testid="settings-section-embedding">
        <div className="ct-section-head">
          <h2>Embedding</h2>
          <span className={`ct-status-pill ct-status-${embeddingStatusTone(embedding)}`}>
            {embedding.enabled ? '已启用' : '未启用'}
          </span>
        </div>
        <p className="ct-section-desc">
          它帮系统按意思找回相关材料，不只靠关键词；打开后报告更容易用到之前保存的上下文。没配也能生成报告。
        </p>
        <div className="ct-kv">
          <span>状态</span>
          <span>{embeddingStatusLabel(embedding)}</span>
        </div>
        <div className="ct-model-grid">
          <label className="ct-field">
            <span>Embedding 服务商</span>
            <input
              value={embedding.provider}
              onChange={(event) =>
                onLlmChange({ embedding: { ...embedding, provider: event.target.value } })
              }
              placeholder="例如 openai / jina / minimax"
            />
          </label>
          <label className="ct-field">
            <span>Embedding 模型</span>
            <input
              value={embedding.model}
              onChange={(event) => onLlmChange({ embedding: { ...embedding, model: event.target.value } })}
              placeholder="例如 text-embedding-3-small"
            />
          </label>
          <label className="ct-field">
            <span>Embedding 接口地址</span>
            <input
              value={embedding.endpointUrl ?? ''}
              onChange={(event) =>
                onLlmChange({ embedding: { ...embedding, endpointUrl: event.target.value } })
              }
              placeholder="留空使用 provider 默认接口"
            />
          </label>
          <label className="ct-field">
            <span>Embedding 维度</span>
            <input
              value={embedding.dimension ?? ''}
              onChange={(event) =>
                onLlmChange({ embedding: { ...embedding, dimension: event.target.value } })
              }
              placeholder="可选，例如 1024"
            />
          </label>
          <label className="ct-field">
            <span>Embedding 密钥</span>
            <input
              type="password"
              value={embedding.apiKeyReplacement ?? ''}
              onChange={(event) =>
                onLlmChange({ embedding: { ...embedding, apiKeyReplacement: event.target.value } })
              }
              placeholder={embedding.apiKeyMasked ? `${embedding.apiKeyMasked}，留空不更换` : '填写新密钥'}
            />
          </label>
        </div>
        <div className="ct-button-row ct-settings-actions">
          <button type="button" className="ct-button" onClick={onTestEmbedding} disabled={embeddingActionBusy}>
            {embeddingActionBusy ? 'Embedding 测试中...' : '测试 Embedding 连接'}
          </button>
          <button type="button" className="ct-button ct-button-secondary" onClick={onSaveEmbedding} disabled={embeddingActionBusy}>
            保存 Embedding 配置
          </button>
        </div>
        {embeddingActionMessage ? (
          <div className={`ct-inline-alert ${embeddingActionOk ? 'is-success' : 'is-error'}`}>
            {embeddingActionMessage}
          </div>
        ) : null}
        {sectionErrors.embedding ? <div className="ct-inline-alert is-error">{sectionErrors.embedding}</div> : null}
      </section>
          </>
        ) : null}

        {activeSettingsTab === 'data' ? (
      <section className="ct-settings-section" data-testid="settings-section-data-sources">
        <div className="ct-section-head">
          <h2>增强数据源</h2>
        </div>
        <p className="ct-section-desc">这里只配置需要填写密钥的外部增强源；系统默认源和内部接入层不在这里显示。</p>
        {hasDataSources ? (
        <div className="ct-source-market-tabs" role="tablist" aria-label="数据源市场">
          {visibleMarkets.map((market) => (
            <button
              type="button"
              role="tab"
              aria-selected={activeMarket?.id === market.id}
              className={`ct-source-market-tab${activeMarket?.id === market.id ? ' is-active' : ''}`}
              key={market.id}
              onClick={() => selectSourceMarket(market.id)}
            >
              {market.title}
            </button>
          ))}
        </div>
        ) : null}
        <div className="ct-source-category-grid">
          {sourceCategories.map((category) => {
            const selected = category.selected;
            const active = selected?.supportedType === dataSourceDraft.supportedType;
            return (
              <div
                className={`ct-source-category-card${active ? ' is-active' : ''}`}
                key={category.id}
                data-testid={`data-source-category-${category.id}`}
              >
                <div className="ct-source-category-head">
                  <h3>{category.title}</h3>
                  {selected ? (
                    <span className={`ct-status-pill ct-status-${dataSourceStatusTone(selected.state)}`}>
                      {selected.enabled ? '已启用' : dataSourceStateLabel(selected.state)}
                    </span>
                  ) : null}
                </div>
                {selected ? (
                  <>
                    <label className="ct-field ct-source-select-field">
                      <span>此功能可配置的增强源</span>
                      <select
                        aria-label={`${category.title}可配置增强源`}
                        value={selected.supportedType}
                        onChange={(event) => selectDataSource(event.target.value)}
                      >
                        {category.sources.map((item) => (
                          <option key={item.instanceId} value={item.supportedType}>
                            {item.displayName}
                          </option>
                        ))}
                      </select>
                    </label>
                    <div className="ct-source-summary">
                      <span>{`API Key：${dataSourceKeyLabel(selected)}`}</span>
                      <span>{`接口地址：${dataSourceEndpointLabel(selected)}`}</span>
                      <span>{`启用开关：${selected.enabled ? '已启用' : '未启用'}`}</span>
                      <span>{`最近测试结果：${recentDataSourceTestLabel(selected.state, selected.lastTestAt)}`}</span>
                    </div>
                    <p className="ct-source-edit-hint">
                      选中后，在下方“配置详情”里填写这个源。
                    </p>
                  </>
                ) : null}
              </div>
            );
          })}
        </div>
        {canEditDataSource ? (
        <div className="ct-source-editor-panel" aria-live="polite">
          <div className="ct-source-editor-head">
            <div>
              <h3>配置详情：{dataSourceDraft.displayName}</h3>
              <p>上面选中哪个增强源，这里就编辑哪个源；同一个源只保存一份配置。</p>
            </div>
            <span className={`ct-status-pill ct-status-${dataSourceMessageTone}`}>
              {dataSourceStateLabel(dataSourceDraft.state ?? 'draft')}
            </span>
          </div>
          <div className="ct-form-grid">
            <label className="ct-field">
              <span>API Key / Token</span>
              <input
                type="password"
                value={dataSourceDraft.apiKeyReplacement ?? ''}
                onChange={(event) => onDataSourceDraftChange({ apiKeyReplacement: event.target.value })}
                placeholder="留空则不更换"
              />
            </label>
            <label className="ct-field">
              <span>接口地址</span>
              <input
                value={dataSourceDraft.endpointUrl ?? ''}
                onChange={(event) => onDataSourceDraftChange({ endpointUrl: event.target.value })}
                placeholder="可选"
              />
            </label>
            <label className="ct-check-row">
              <span>启用开关</span>
              <input
                type="checkbox"
                checked={dataSourceDraft.enabled}
                onChange={(event) => onDataSourceDraftChange({ enabled: event.target.checked })}
              />
              <span>启用这个增强源</span>
            </label>
          </div>
          <div className="ct-button-row ct-settings-actions">
            <button type="button" className="ct-button" onClick={onTestDataSource} disabled={dataSourceActionBusy}>
              {dataSourceActionBusy ? '处理中...' : '测试数据源'}
            </button>
            <button
              type="button"
              className="ct-button ct-button-secondary"
              onClick={onSaveDataSource}
              disabled={dataSourceActionBusy}
            >
              保存数据源
            </button>
          </div>
        </div>
        ) : null}
        {dataSourceActionMessage ? (
          <div className={`ct-inline-alert ${dataSourceMessageTone === 'error' ? 'is-error' : 'is-success'}`}>
            {dataSourceActionMessage}
          </div>
        ) : null}
        {dataSources.length === 0 ? <p className="ct-empty ct-empty-section">暂无数据源配置</p> : null}
        {sectionErrors.dataSources ? <div className="ct-inline-alert is-error">{sectionErrors.dataSources}</div> : null}
      </section>
        ) : null}

        {activeSettingsTab === 'general' ? (
          <>
      <section className="ct-settings-section" data-testid="settings-section-wechat">
        <div className="ct-section-head">
          <h2>微信通知</h2>
          <span className={`ct-status-pill ct-status-${channelStatusTone(channel)}`}>
            {channelStateLabel(channel?.state)}
          </span>
        </div>
        <p className="ct-section-desc">未登录不阻断工作台使用，扫码连接后可接收通知。</p>
        <div className="ct-wechat-onboarding ct-wechat-channel">
          <div className="ct-qr-frame" aria-label="微信登录二维码区域">
            {channel?.qrCodeImageDataUrl ? (
              <img src={channel.qrCodeImageDataUrl} alt="微信登录二维码" />
            ) : (
              <span>{channel?.state === 'connected' ? '已连接' : '等待二维码'}</span>
            )}
          </div>
          <div className="ct-wechat-copy ct-wechat-channel-copy">
            <p>请先在微信端启用插件：</p>
            <p>我 → 设置 → 插件 → 微信 ClawBot</p>
            <p>按微信端提示安装或启用后，返回本页扫描二维码完成连接。</p>
            <p>{qrHelpText(channel)}</p>
            {channel?.qrCodeExpiresAt ? <p>二维码有效期至 {formatDate(channel.qrCodeExpiresAt)}</p> : null}
          </div>
        </div>
        {channel?.lastErrorMessage ? <div className="ct-small">{channel.lastErrorMessage}</div> : null}
        <div className="ct-button-row ct-settings-actions">
          <button
            type="button"
            className="ct-button ct-button-secondary"
            onClick={onReconnectChannel}
            disabled={channelActionBusy}
          >
            {channelActionBusy ? '处理中...' : '重新连接'}
          </button>
          <button
            type="button"
            className="ct-button ct-button-secondary"
            onClick={onDisconnectChannel}
            disabled={channelActionBusy || channel?.state !== 'connected'}
          >
            解除连接
          </button>
          <button
            type="button"
            className="ct-button ct-button-secondary"
            onClick={onRefreshChannel}
            disabled={channelActionBusy}
          >
            刷新二维码
          </button>
          <button
            type="button"
            className="ct-button ct-button-secondary"
            onClick={onSkipWechatSetup}
            disabled={channelActionBusy}
          >
            稍后设置
          </button>
        </div>
        {channelActionMessage ? <div className="ct-inline-alert is-success">{channelActionMessage}</div> : null}
        {sectionErrors.channel ? <div className="ct-inline-alert is-error">{sectionErrors.channel}</div> : null}
      </section>

      <section className="ct-settings-section" data-testid="settings-section-reset">
        <div className="ct-section-head">
          <h2>恢复默认设置</h2>
        </div>
        <p className="ct-section-desc">清空本页保存的模型、Embedding、增强数据源和微信通知连接设置；历史报告不会删除。</p>
        <div className="ct-button-row ct-settings-actions">
          <button
            type="button"
            className="ct-button ct-button-secondary ct-button-danger"
            onClick={onResetSettings}
            disabled={resetActionBusy}
          >
            {resetActionBusy ? '恢复中...' : '恢复默认设置'}
          </button>
        </div>
        {resetActionMessage ? <div className="ct-inline-alert is-success">{resetActionMessage}</div> : null}
        {sectionErrors.reset ? <div className="ct-inline-alert is-error">{sectionErrors.reset}</div> : null}
      </section>
          </>
        ) : null}
      </div>
    </main>
  );
}
