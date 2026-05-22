import type {
  ChannelStatusForUser,
  DataSourceInstanceDraftInput,
  DataSourceInstanceForUser,
  LlmConfigDraft,
} from '../api/contracts';

const LLM_PROVIDERS: Array<{ value: LlmConfigDraft['provider']; label: string }> = [
  { value: 'deepseek', label: 'DeepSeek' },
  { value: 'qwen', label: '通义千问' },
  { value: 'glm', label: '智谱 GLM' },
  { value: 'kimi', label: 'Kimi' },
  { value: 'minimax', label: 'MiniMax' },
  { value: 'doubao', label: '豆包' },
  { value: 'ernie', label: '文心' },
  { value: 'hunyuan', label: '混元' },
  { value: 'openai_compatible', label: '兼容接口' },
];

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
    return '当前微信账号已连接。';
  }
  if (channel?.qrCodeImageDataUrl) {
    return '请使用微信扫描二维码完成登录。';
  }
  if (channel?.qrCodeRefreshRequired) {
    return '当前通道未返回二维码，点击刷新二维码再试。';
  }
  return '等待微信通道返回二维码。';
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

export function SettingsSections({
  channel,
  llm,
  dataSources,
  supportedTypes,
  dataSourceDraft,
  sectionErrors,
  channelActionBusy,
  channelActionMessage,
  llmActionBusy,
  llmActionMessage,
  dataSourceActionBusy,
  dataSourceActionMessage,
  deviceUiHref,
  onEnableChannel,
  onRefreshChannel,
  onLlmChange,
  onSaveLlm,
  onTestLlm,
  onDataSourceDraftChange,
  onEditDataSource,
  onNewDataSource,
  onSaveDataSource,
  onTestDataSource,
}: {
  channel: ChannelStatusForUser | null;
  llm: LlmConfigDraft;
  dataSources: DataSourceInstanceForUser[];
  supportedTypes: string[];
  dataSourceDraft: DataSourceInstanceDraftInput;
  sectionErrors: { channel?: string; llm?: string; dataSources?: string };
  channelActionBusy: boolean;
  channelActionMessage: string;
  llmActionBusy: boolean;
  llmActionMessage: string;
  dataSourceActionBusy: boolean;
  dataSourceActionMessage: string;
  deviceUiHref: string;
  onEnableChannel: () => void;
  onRefreshChannel: () => void;
  onLlmChange: (patch: Partial<LlmConfigDraft>) => void;
  onSaveLlm: () => void;
  onTestLlm: () => void;
  onDataSourceDraftChange: (patch: Partial<DataSourceInstanceDraftInput>) => void;
  onEditDataSource: (item: DataSourceInstanceForUser) => void;
  onNewDataSource: () => void;
  onSaveDataSource: () => void;
  onTestDataSource: () => void;
}) {
  const embedding = embeddingDraft(llm);
  return (
    <main className="ct-settings" data-testid="settings-page">
      <section className="ct-settings-section">
        <div className="ct-section-head">
          <h2>微信通知</h2>
          <span className={`ct-status-pill ct-status-${channel?.state ?? 'unknown'}`}>
            {channelStateLabel(channel?.state)}
          </span>
        </div>
        <div className="ct-kv">
          <span>连接状态</span>
          <span>{channelStateLabel(channel?.state)}</span>
        </div>
        <div className="ct-kv">
          <span>账号</span>
          <span>{channel?.accountLabel ?? '未绑定'}</span>
        </div>
        <div className="ct-kv">
          <span>最近连接</span>
          <span>{formatDate(channel?.lastConnectedAt)}</span>
        </div>
        <div className="ct-kv">
          <span>通知能力</span>
          <span>
            {channel?.canSendText ? '文字可用' : '文字不可用'} /{' '}
            {channel?.canSendFile ? '文件可用' : '文件不可用'}
          </span>
        </div>
        <div className="ct-wechat-channel">
          <div className="ct-qr-frame" aria-label="微信登录二维码区域">
            {channel?.qrCodeImageDataUrl ? (
              <img src={channel.qrCodeImageDataUrl} alt="微信登录二维码" />
            ) : (
              <span>{channel?.state === 'connected' ? '已连接' : '等待二维码'}</span>
            )}
          </div>
          <div className="ct-wechat-channel-copy">
            <strong>微信通道</strong>
            <div className="ct-small">{qrHelpText(channel)}</div>
            {channel?.qrCodeExpiresAt ? (
              <div className="ct-small">二维码有效期至 {formatDate(channel.qrCodeExpiresAt)}</div>
            ) : null}
          </div>
        </div>
        <div className="ct-small">{channel?.lastErrorMessage ?? '扫码登录和异常处理会显示在这里。'}</div>
        <div className="ct-settings-actions">
          <button type="button" onClick={onEnableChannel} disabled={channelActionBusy || channel?.state === 'connected'}>
            {channelActionBusy ? '处理中...' : channel?.state === 'connected' ? '已连接' : '启用微信通知'}
          </button>
          <button type="button" className="is-ghost" onClick={onRefreshChannel} disabled={channelActionBusy}>
            刷新二维码
          </button>
          <a className="ct-button-link" href={deviceUiHref} target="_blank" rel="noreferrer">
            打开 OpenClaw 主界面
          </a>
        </div>
        <div className="ct-small">微信扫码登录需要在 OpenClaw 主界面完成；这里不会把未验证的登录状态显示成成功。</div>
        {channelActionMessage ? <div className="ct-notice">{channelActionMessage}</div> : null}
        {sectionErrors.channel ? <div className="ct-notice ct-notice-error">{sectionErrors.channel}</div> : null}
      </section>

      <section className="ct-settings-section">
        <h2>模型</h2>
        <div className="ct-form-grid">
          <label className="ct-field">
            <span>服务商</span>
            <select
              value={llm.provider}
              onChange={(event) => onLlmChange({ provider: event.target.value as LlmConfigDraft['provider'] })}
            >
              {LLM_PROVIDERS.map((item) => (
                <option key={item.value} value={item.value}>
                  {item.label}
                </option>
              ))}
            </select>
          </label>
          <label className="ct-field">
            <span>默认模型</span>
            <input
              value={llm.defaultModel}
              onChange={(event) => onLlmChange({ defaultModel: event.target.value })}
              placeholder="例如 deepseek-chat"
            />
          </label>
          <label className="ct-field">
            <span>接口地址</span>
            <input
              value={llm.endpointUrl ?? ''}
              onChange={(event) => onLlmChange({ endpointUrl: event.target.value })}
              placeholder="留空使用默认接口"
            />
          </label>
          <label className="ct-field">
            <span>密钥</span>
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
          <span>{llm.status}{llm.lastTestMessage ? ` · ${llm.lastTestMessage}` : ''}</span>
        </div>
        <div className="ct-kv">
          <span>语义检索</span>
          <span>{embedding.enabled ? '已启用' : '未启用'}</span>
        </div>
        <div className="ct-form-grid">
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
        <div className="ct-settings-actions">
          <button type="button" onClick={onTestLlm} disabled={llmActionBusy}>
            {llmActionBusy ? '处理中...' : '测试连接'}
          </button>
          <button type="button" className="is-ghost" onClick={onSaveLlm} disabled={llmActionBusy}>
            保存模型
          </button>
        </div>
        {llmActionMessage ? <div className="ct-notice">{llmActionMessage}</div> : null}
        {sectionErrors.llm ? <div className="ct-notice ct-notice-error">{sectionErrors.llm}</div> : null}
      </section>

      <section className="ct-settings-section">
        <div className="ct-section-head">
          <h2>数据源</h2>
          <button type="button" className="ct-text-button" onClick={onNewDataSource}>
            新增
          </button>
        </div>
        <div className="ct-kv">
          <span>可配置类型</span>
          <span>{supportedTypes.length > 0 ? `${supportedTypes.length} 类` : '暂未读取'}</span>
        </div>
        <div className="ct-form-grid">
          <label className="ct-field">
            <span>类型</span>
            <select
              value={dataSourceDraft.supportedType}
              onChange={(event) =>
                onDataSourceDraftChange({
                  supportedType: event.target.value,
                  displayName:
                    dataSourceDraft.displayName === dataSourceDraft.supportedType
                      ? event.target.value
                      : dataSourceDraft.displayName,
                })
              }
            >
              {(supportedTypes.length > 0 ? supportedTypes : [dataSourceDraft.supportedType]).map((item) => (
                <option key={item} value={item}>
                  {item}
                </option>
              ))}
            </select>
          </label>
          <label className="ct-field">
            <span>名称</span>
            <input
              value={dataSourceDraft.displayName}
              onChange={(event) => onDataSourceDraftChange({ displayName: event.target.value })}
              placeholder="显示在报告设置里的名称"
            />
          </label>
          <label className="ct-field">
            <span>密钥</span>
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
          <label className="ct-field">
            <span>代理地址</span>
            <input
              value={dataSourceDraft.proxyUrl ?? ''}
              onChange={(event) => onDataSourceDraftChange({ proxyUrl: event.target.value })}
              placeholder="可选"
            />
          </label>
          <label className="ct-field">
            <span>请求头名</span>
            <input
              value={dataSourceDraft.headerName ?? ''}
              onChange={(event) => onDataSourceDraftChange({ headerName: event.target.value })}
              placeholder="可选"
            />
          </label>
          <label className="ct-field">
            <span>优先级</span>
            <input
              type="number"
              value={dataSourceDraft.priority}
              onChange={(event) => onDataSourceDraftChange({ priority: Number(event.target.value) || 100 })}
            />
          </label>
          <label className="ct-check-row">
            <input
              type="checkbox"
              checked={dataSourceDraft.enabled}
              onChange={(event) => onDataSourceDraftChange({ enabled: event.target.checked })}
            />
            <span>启用这个数据源</span>
          </label>
        </div>
        <div className="ct-settings-actions">
          <button type="button" onClick={onTestDataSource} disabled={dataSourceActionBusy}>
            {dataSourceActionBusy ? '处理中...' : '测试数据源'}
          </button>
          <button type="button" className="is-ghost" onClick={onSaveDataSource} disabled={dataSourceActionBusy}>
            保存数据源
          </button>
        </div>
        {dataSourceActionMessage ? <div className="ct-notice">{dataSourceActionMessage}</div> : null}
        <ul className="ct-plain-list">
          {dataSources.map((item) => (
            <li key={item.instanceId}>
              <strong>{item.displayName}</strong>
              <div className="ct-small">{`${item.group} · ${item.supportedType}`}</div>
              <div className="ct-small">{`${item.enabled ? '已启用' : '已停用'} · ${dataSourceStateLabel(item.state)}`}</div>
              <button type="button" className="ct-text-button" onClick={() => onEditDataSource(item)}>
                编辑
              </button>
            </li>
          ))}
        </ul>
        {dataSources.length === 0 ? <p className="ct-empty">暂无数据源配置</p> : null}
        {sectionErrors.dataSources ? <div className="ct-notice ct-notice-error">{sectionErrors.dataSources}</div> : null}
      </section>
    </main>
  );
}
