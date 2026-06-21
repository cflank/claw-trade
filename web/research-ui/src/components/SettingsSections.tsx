import { useState } from 'react';
import type {
  ChannelStatusForUser,
  DataSourceInstanceDraftInput,
  DataSourceInstanceForUser,
  LlmConfigDraft,
  ReportCleanupSettingsForUser,
  ReportRetentionDays,
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

type SettingsMainTab = 'model' | 'general' | 'data';

const SETTINGS_MAIN_TABS: Array<{ id: SettingsMainTab; title: string }> = [
  { id: 'model', title: '模型' },
  { id: 'general', title: '通用' },
  { id: 'data', title: '数据源' },
];

const DATA_SOURCE_TYPES = ['tushare', 'finnhub', 'fred', 'coingecko', 'coingecko_pro', 'coinglass', 'glassnode'];
const REPORT_RETENTION_OPTIONS: Array<{ value: ReportRetentionDays; label: string }> = [
  { value: 7, label: '7 天' },
  { value: 14, label: '14 天' },
  { value: 30, label: '1 个月' },
];

function dataSourceByType(dataSources: DataSourceInstanceForUser[]) {
  return new Map(dataSources.map((item) => [item.supportedType, item]));
}

export function SettingsSections({
  channel,
  llm,
  dataSources,
  dataSourceDraft,
  reportCleanup,
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
  cleanupActionBusy,
  cleanupActionMessage,
  resetActionBusy,
  resetActionMessage,
  onReconnectChannel,
  onDisconnectChannel,
  onSkipWechatSetup,
  onRefreshChannel,
  onSettingsTabChange,
  onLlmChange,
  onSaveLlm,
  onTestLlm,
  onSaveEmbedding,
  onTestEmbedding,
  onDataSourceDraftChange,
  onEditDataSource,
  onSaveDataSource,
  onTestDataSource,
  onReportCleanupChange,
  onSaveReportCleanup,
  onResetSettings,
}: {
  channel: ChannelStatusForUser | null;
  llm: LlmConfigDraft;
  dataSources: DataSourceInstanceForUser[];
  dataSourceDraft: DataSourceInstanceDraftInput;
  reportCleanup: ReportCleanupSettingsForUser;
  sectionErrors: {
    channel?: string;
    llm?: string;
    embedding?: string;
    dataSources?: string;
    reportCleanup?: string;
    reset?: string;
  };
  channelActionBusy: boolean;
  channelActionMessage: string;
  llmActionBusy: boolean;
  llmActionMessage: string;
  embeddingActionBusy: boolean;
  embeddingActionMessage: string;
  embeddingActionOk: boolean;
  dataSourceActionBusy: boolean;
  dataSourceActionMessage: string;
  cleanupActionBusy: boolean;
  cleanupActionMessage: string;
  resetActionBusy: boolean;
  resetActionMessage: string;
  onReconnectChannel: () => void;
  onDisconnectChannel: () => void;
  onSkipWechatSetup: () => void;
  onRefreshChannel: () => void;
  onSettingsTabChange?: (tab: SettingsMainTab) => void;
  onLlmChange: (patch: Partial<LlmConfigDraft>) => void;
  onSaveLlm: () => void;
  onTestLlm: () => void;
  onSaveEmbedding: () => void;
  onTestEmbedding: () => void;
  onDataSourceDraftChange: (patch: Partial<DataSourceInstanceDraftInput>) => void;
  onEditDataSource: (item: DataSourceInstanceForUser) => void;
  onSaveDataSource: () => void;
  onTestDataSource: () => void;
  onReportCleanupChange: (reportRetentionDays: ReportRetentionDays) => void;
  onSaveReportCleanup: () => void;
  onResetSettings: () => void;
}) {
  const embedding = embeddingDraft(llm);
  const modelFieldError = modelStatusState(llm) === 'failed';
  const dataSourceMessageTone = dataSourceStatusTone(dataSourceDraft.state ?? 'draft');
  const modelOptions = modelOptionsForProvider(llm.provider, llm.defaultModel);
  const selectedModel = normalizeLlmModelValue(llm.provider, llm.defaultModel);
  const [activeSettingsTab, setActiveSettingsTab] = useState<SettingsMainTab>('model');
  const visibleDataSources = DATA_SOURCE_TYPES.map((type) => dataSources.find((item) => item.supportedType === type)).filter(
    Boolean,
  ) as DataSourceInstanceForUser[];
  const sourceMap = dataSourceByType(visibleDataSources);
  const hasDataSources = visibleDataSources.length > 0;
  const canEditDataSource = hasDataSources && sourceMap.has(dataSourceDraft.supportedType);
  const selectSettingsTab = (tabId: SettingsMainTab) => {
    setActiveSettingsTab(tabId);
    onSettingsTabChange?.(tabId);
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
            onClick={() => selectSettingsTab(tab.id)}
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
          <span className="ct-status-pill ct-status-pending">
            {hasDataSources ? `${visibleDataSources.length} 个 API 源` : '暂无源'}
          </span>
        </div>
        <p className="ct-section-desc">这里列出当前可配置的 API 数据源；同一个源只保存一份配置。</p>
        <div className="ct-source-grid">
          {visibleDataSources.map((source) => {
            const active = source.supportedType === dataSourceDraft.supportedType;
            return (
              <div
                className={`ct-source-card${active ? ' is-active' : ''}`}
                key={source.instanceId}
                data-testid={`data-source-card-${source.supportedType}`}
              >
                <div className="ct-source-card-head">
                  <h3>{source.displayName}</h3>
                  <span className={`ct-status-pill ct-status-${dataSourceStatusTone(source.state)}`}>
                    {source.enabled ? '已启用' : dataSourceStateLabel(source.state)}
                  </span>
                </div>
                <div className="ct-source-summary">
                  <span>{`API Key：${dataSourceKeyLabel(source)}`}</span>
                  <span>{`接口地址：${dataSourceEndpointLabel(source)}`}</span>
                  <span>{`启用开关：${source.enabled ? '已启用' : '未启用'}`}</span>
                  <span>{`最近测试结果：${recentDataSourceTestLabel(source.state, source.lastTestAt)}`}</span>
                </div>
                <button
                  type="button"
                  className="ct-text-button ct-source-edit-button"
                  onClick={() => selectDataSource(source.supportedType)}
                >
                  编辑
                </button>
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
            <label className="ct-field">
              <span>限流次数</span>
              <input
                type="number"
                min="1"
                value={dataSourceDraft.rateLimitMaxCalls ?? ''}
                onChange={(event) => onDataSourceDraftChange({ rateLimitMaxCalls: event.target.value })}
                placeholder="例如 10"
              />
            </label>
            <label className="ct-field">
              <span>限流窗口秒数</span>
              <input
                type="number"
                min="1"
                value={dataSourceDraft.rateLimitWindowSeconds ?? ''}
                onChange={(event) => onDataSourceDraftChange({ rateLimitWindowSeconds: event.target.value })}
                placeholder="例如 60"
              />
            </label>
            <label className="ct-field">
              <span>安全余量</span>
              <input
                type="number"
                min="0"
                value={dataSourceDraft.rateLimitSafetyMargin ?? ''}
                onChange={(event) => onDataSourceDraftChange({ rateLimitSafetyMargin: event.target.value })}
                placeholder="默认 0"
              />
            </label>
            <label className="ct-field">
              <span>超额策略</span>
              <select
                value={dataSourceDraft.rateLimitOverflow ?? ''}
                onChange={(event) =>
                  onDataSourceDraftChange({
                    rateLimitOverflow: event.target.value as DataSourceInstanceDraftInput['rateLimitOverflow'],
                  })
                }
              >
                <option value="">使用默认</option>
                <option value="wait">等待下一窗口</option>
                <option value="fail_fast">立即失败</option>
              </select>
            </label>
            <label className="ct-field">
              <span>最长等待秒数</span>
              <input
                type="number"
                min="0"
                value={dataSourceDraft.rateLimitWaitTimeoutSeconds ?? ''}
                onChange={(event) => onDataSourceDraftChange({ rateLimitWaitTimeoutSeconds: event.target.value })}
                placeholder="例如 75"
              />
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
        {visibleDataSources.length === 0 ? <p className="ct-empty ct-empty-section">暂无数据源配置</p> : null}
        {sectionErrors.dataSources ? <div className="ct-inline-alert is-error">{sectionErrors.dataSources}</div> : null}
      </section>
        ) : null}

        {activeSettingsTab === 'general' ? (
          <>
      <section className="ct-settings-section" data-testid="settings-section-report-cleanup">
        <div className="ct-section-head">
          <h2>报告保留时间</h2>
        </div>
        <p className="ct-section-desc">设置历史报告自动保留多久；已保存报告会按这个时间清理。</p>
        <label className="ct-field">
          <span>报告保留时间</span>
          <select
            value={reportCleanup.reportRetentionDays}
            onChange={(event) => onReportCleanupChange(Number(event.target.value) as ReportRetentionDays)}
          >
            {REPORT_RETENTION_OPTIONS.map((item) => (
              <option key={item.value} value={item.value}>
                {item.label}
              </option>
            ))}
          </select>
        </label>
        <div className="ct-button-row ct-settings-actions">
          <button
            type="button"
            className="ct-button ct-button-secondary"
            onClick={onSaveReportCleanup}
            disabled={cleanupActionBusy}
          >
            {cleanupActionBusy ? '保存中...' : '保存报告保留时间'}
          </button>
        </div>
        {cleanupActionMessage ? <div className="ct-inline-alert is-success">{cleanupActionMessage}</div> : null}
        {sectionErrors.reportCleanup ? <div className="ct-inline-alert is-error">{sectionErrors.reportCleanup}</div> : null}
      </section>

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
        <p className="ct-section-desc">清空本页保存的模型、Embedding、增强数据源、报告保留时间和微信通知连接设置；历史报告不会删除。</p>
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
