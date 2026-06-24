import { useEffect, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { AppShell } from '../components/AppShell';
import { SettingsSections } from '../components/SettingsSections';
import { withLlmProviderDefaults } from '../components/llmCatalog';
import {
  getChannelStatus,
  getReportCleanupSettings,
  getSelectionAutoRefreshSettings,
  listDataSources,
  loadLlmSettings,
  resetSettingsToDefaults,
  saveChannelConfig,
  saveDataSourceInstance,
  saveEmbeddingConfig,
  saveReportCleanupSettings,
  saveSelectionAutoRefreshSettings,
  saveReportModelConfig,
  testDataSource,
  testEmbeddingConnection,
  testReportModelConnection,
  type ChannelStatusForUser,
  type DataSourceInstanceDraftInput,
  type DataSourceInstanceForUser,
  type LlmConfigDraft,
  type ReportCleanupSettingsForUser,
  type ReportRetentionDays,
  type SelectionAutoRefreshSettingsForUser,
} from '../api/workspace';

type SectionErrors = {
  channel?: string;
  llm?: string;
  embedding?: string;
  dataSources?: string;
  reportCleanup?: string;
  selectionAutoRefresh?: string;
  reset?: string;
};

function shouldEnableWechatPlugin(channel: ChannelStatusForUser | null) {
  const message = channel?.lastErrorMessage ?? '';
  return channel?.state !== 'connected' && (message.includes('启用微信 ClawBot 插件') || message.includes('微信已解除连接'));
}

const SETTINGS_LOAD_TIMEOUT_MS = 8000;
const MODEL_TEST_TIMEOUT_MS = 90000;
const CHANNEL_STATUS_TIMEOUT_MS = 50000;
const CHANNEL_LOGIN_POLL_MS = 2000;
const DEFAULT_LLM_DRAFT: LlmConfigDraft = withLlmProviderDefaults({
  provider: 'deepseek',
  defaultModel: '',
  status: 'idle',
  reportModelStatus: {
    state: 'unconfigured',
    blocked: true,
    ready: false,
    userMessage: '请先在设置中填写报告模型并完成测试。',
    checkedAt: null,
  },
  embedding: {
    provider: '',
    model: '',
    endpointUrl: '',
    dimension: '',
    apiKeyReplacement: '',
    enabled: false,
  },
});
const DEFAULT_REPORT_CLEANUP: ReportCleanupSettingsForUser = { reportRetentionDays: 7 };
const DEFAULT_SELECTION_AUTO_REFRESH: SelectionAutoRefreshSettingsForUser = { enabled: true };

function currentEmbeddingDraft(draft: LlmConfigDraft): NonNullable<LlmConfigDraft['embedding']> {
  return (
    draft.embedding ?? {
      provider: '',
      model: '',
      endpointUrl: '',
      dimension: '',
      apiKeyReplacement: '',
      enabled: false,
    }
  );
}

function createDataSourceDraft(source?: DataSourceInstanceForUser): DataSourceInstanceDraftInput {
  return {
    instanceId: source?.instanceId ?? null,
    supportedType: source?.supportedType ?? '',
    group: source?.group ?? '',
    displayName: source?.displayName ?? '',
    enabled: source?.enabled ?? false,
    apiKeyReplacement: '',
    endpointUrl: source?.endpointUrl ?? '',
    state: source?.state ?? 'draft',
    requiresKey: true,
    rateLimitMaxCalls: source?.rateLimitMaxCalls ?? '',
    rateLimitWindowSeconds: source?.rateLimitWindowSeconds ?? '',
    rateLimitSafetyMargin: source?.rateLimitSafetyMargin ?? '',
    rateLimitOverflow: source?.rateLimitOverflow ?? '',
    rateLimitWaitTimeoutSeconds: source?.rateLimitWaitTimeoutSeconds ?? '',
  };
}

function draftFromDataSource(item: DataSourceInstanceForUser): DataSourceInstanceDraftInput {
  return {
    instanceId: item.instanceId,
    supportedType: item.supportedType,
    group: item.group,
    displayName: item.displayName,
    enabled: item.enabled,
    apiKeyReplacement: '',
    endpointUrl: item.endpointUrl ?? '',
    state: item.state,
    requiresKey: true,
    rateLimitMaxCalls: item.rateLimitMaxCalls ?? '',
    rateLimitWindowSeconds: item.rateLimitWindowSeconds ?? '',
    rateLimitSafetyMargin: item.rateLimitSafetyMargin ?? '',
    rateLimitOverflow: item.rateLimitOverflow ?? '',
    rateLimitWaitTimeoutSeconds: item.rateLimitWaitTimeoutSeconds ?? '',
  };
}

function toDataSourceDraftPayload(draft: DataSourceInstanceDraftInput): DataSourceInstanceDraftInput {
  return {
    instanceId: draft.instanceId ?? null,
    supportedType: draft.supportedType,
    group: draft.group,
    displayName: draft.displayName,
    enabled: draft.enabled,
    apiKeyReplacement: draft.apiKeyReplacement ?? '',
    endpointUrl: draft.endpointUrl ?? '',
    state: draft.state ?? 'draft',
    requiresKey: draft.requiresKey,
    rateLimitMaxCalls: draft.rateLimitMaxCalls ?? '',
    rateLimitWindowSeconds: draft.rateLimitWindowSeconds ?? '',
    rateLimitSafetyMargin: draft.rateLimitSafetyMargin ?? '',
    rateLimitOverflow: draft.rateLimitOverflow ?? '',
    rateLimitWaitTimeoutSeconds: draft.rateLimitWaitTimeoutSeconds ?? '',
  };
}

function withSettingsTimeout<T>(promise: Promise<T>, message: string, timeoutMs = SETTINGS_LOAD_TIMEOUT_MS) {
  let timer: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<T>((_, reject) => {
    timer = setTimeout(() => reject(new Error(message)), timeoutMs);
  });
  return Promise.race([promise, timeout]).finally(() => {
    if (timer) {
      clearTimeout(timer);
    }
  });
}

function sanitizeModelFailureMessage(raw: string) {
  const message = raw.trim();
  if (!message) {
    return '报告模型连接测试失败，请检查模型配置后重试。';
  }
  const lowered = message.toLowerCase();
  if (lowered.includes('provider attempt') || lowered.includes('runtime') || lowered.includes('gateway')) {
    return '报告模型连接测试失败，请检查服务商、模型、API Key 和接口地址后重试。';
  }
  return message;
}

function withSavedUnverifiedStatus(draft: LlmConfigDraft, checkedAt?: string | null): LlmConfigDraft {
  return {
    ...draft,
    reportModelStatus: {
      state: 'saved_unverified',
      blocked: true,
      ready: false,
      userMessage: '报告模型已保存但尚未测试通过，请先执行模型测试。',
      checkedAt: checkedAt ?? null,
    },
  };
}

export function SettingsPage() {
  const navigate = useNavigate();
  const [channel, setChannel] = useState<ChannelStatusForUser | null>(null);
  const [llm, setLlm] = useState<LlmConfigDraft | null>(null);
  const [llmSettingsVersion, setLlmSettingsVersion] = useState('');
  const [dataSources, setDataSources] = useState<DataSourceInstanceForUser[]>([]);
  const [dataSourceDraft, setDataSourceDraft] = useState<DataSourceInstanceDraftInput>(() =>
    createDataSourceDraft(),
  );
  const [reportCleanup, setReportCleanup] = useState<ReportCleanupSettingsForUser>(
    DEFAULT_REPORT_CLEANUP,
  );
  const [selectionAutoRefresh, setSelectionAutoRefresh] = useState<SelectionAutoRefreshSettingsForUser>(
    DEFAULT_SELECTION_AUTO_REFRESH,
  );
  const [loading, setLoading] = useState(true);
  const [sectionErrors, setSectionErrors] = useState<SectionErrors>({});
  const [channelActionBusy, setChannelActionBusy] = useState(false);
  const [channelActionMessage, setChannelActionMessage] = useState('');
  const [llmActionBusy, setLlmActionBusy] = useState(false);
  const [llmActionMessage, setLlmActionMessage] = useState('');
  const [embeddingActionBusy, setEmbeddingActionBusy] = useState(false);
  const [embeddingActionMessage, setEmbeddingActionMessage] = useState('');
  const [embeddingActionOk, setEmbeddingActionOk] = useState(false);
  const [dataSourceActionBusy, setDataSourceActionBusy] = useState(false);
  const [dataSourceActionMessage, setDataSourceActionMessage] = useState('');
  const [cleanupActionBusy, setCleanupActionBusy] = useState(false);
  const [cleanupActionMessage, setCleanupActionMessage] = useState('');
  const [selectionAutoRefreshActionBusy, setSelectionAutoRefreshActionBusy] = useState(false);
  const [selectionAutoRefreshActionMessage, setSelectionAutoRefreshActionMessage] = useState('');
  const [resetActionBusy, setResetActionBusy] = useState(false);
  const [resetActionMessage, setResetActionMessage] = useState('');
  const autoQrRequestedRef = useRef(false);

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setSectionErrors({});
      setChannelActionMessage('');
      setLlmActionMessage('');
      setDataSourceActionMessage('');
      setCleanupActionMessage('');
      setResetActionMessage('');
      let pending = 5;
      const finishOne = () => {
        pending -= 1;
        if (active && pending <= 0) {
          setLoading(false);
        }
      };
      void withSettingsTimeout(
        getChannelStatus({ probe: false }),
        '微信通道暂不可用，请稍后重试。',
        CHANNEL_STATUS_TIMEOUT_MS,
      )
        .then((result) => {
          if (active) {
            setChannel((current) =>
              current?.qrCodeImageDataUrl && !result.qrCodeImageDataUrl ? current : result,
            );
          }
        })
        .catch((loadError) => {
          if (active) {
            setSectionErrors((current) => ({ ...current, channel: (loadError as Error).message }));
          }
        })
        .finally(finishOne);
      void withSettingsTimeout(loadLlmSettings(), '助手服务暂不可用，请稍后重试。')
        .then((result) => {
          if (active) {
            setLlm(withLlmProviderDefaults({ ...DEFAULT_LLM_DRAFT, ...result.draft }));
            setLlmSettingsVersion(result.settingsVersion);
          }
        })
        .catch((loadError) => {
          if (active) {
            setSectionErrors((current) => ({ ...current, llm: (loadError as Error).message }));
          }
        })
        .finally(finishOne);
      void listDataSources()
        .then((result) => {
          if (active) {
            setDataSources(result.instances);
            setDataSourceDraft((current) =>
              result.instances.some((item) => item.supportedType === current.supportedType)
                ? current
                : createDataSourceDraft(result.instances[0]),
            );
          }
        })
        .catch((loadError) => {
          if (active) {
            setSectionErrors((current) => ({ ...current, dataSources: (loadError as Error).message }));
          }
        })
        .finally(finishOne);
      void withSettingsTimeout(
        getReportCleanupSettings(),
        '报告保留设置暂不可用，请稍后重试。',
      )
        .then((result) => {
          if (active) {
            setReportCleanup(result.reportCleanup);
          }
        })
        .catch((loadError) => {
          if (active) {
            setSectionErrors((current) => ({
              ...current,
              reportCleanup: (loadError as Error).message,
            }));
          }
        })
        .finally(finishOne);
      void withSettingsTimeout(
        getSelectionAutoRefreshSettings(),
        '选股刷新设置暂不可用，请稍后重试。',
      )
        .then((result) => {
          if (active) {
            setSelectionAutoRefresh(result.selectionAutoRefresh ?? DEFAULT_SELECTION_AUTO_REFRESH);
          }
        })
        .catch((loadError) => {
          if (active) {
            setSectionErrors((current) => ({
              ...current,
              selectionAutoRefresh: (loadError as Error).message,
            }));
          }
        })
        .finally(finishOne);
    }
    void load();
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!channel?.qrCodeImageDataUrl || channel.state === 'connected') {
      return undefined;
    }
    let active = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async () => {
      try {
        const result = await withSettingsTimeout(
          getChannelStatus({ includeQr: true, pollLogin: true }),
          '微信通道暂不可用，请稍后重试。',
          CHANNEL_STATUS_TIMEOUT_MS,
        );
        if (!active) {
          return;
        }
        setChannel(result);
        if (result.state !== 'connected' && result.qrCodeImageDataUrl) {
          timer = setTimeout(poll, CHANNEL_LOGIN_POLL_MS);
        }
      } catch (pollError) {
        if (!active) {
          return;
        }
        setSectionErrors((current) => ({ ...current, channel: (pollError as Error).message }));
        timer = setTimeout(poll, CHANNEL_LOGIN_POLL_MS * 2);
      }
    };
    timer = setTimeout(poll, CHANNEL_LOGIN_POLL_MS);
    return () => {
      active = false;
      if (timer) {
        clearTimeout(timer);
      }
    };
  }, [channel?.qrCodeImageDataUrl, channel?.state]);

  async function refreshChannel() {
    if (shouldEnableWechatPlugin(channel)) {
      await setChannelEnabled(true);
      return;
    }
    setChannelActionBusy(true);
    setChannelActionMessage('');
    setSectionErrors((current) => ({ ...current, channel: undefined }));
    try {
      const result = await withSettingsTimeout(
        getChannelStatus({ includeQr: true, refreshQr: true }),
        '微信通道暂不可用，请稍后重试。',
        CHANNEL_STATUS_TIMEOUT_MS,
      );
      setChannel(result);
    } catch (refreshError) {
      setSectionErrors((current) => ({
        ...current,
        channel: (refreshError as Error).message,
      }));
    } finally {
      setChannelActionBusy(false);
    }
  }

  async function loadChannelQrOnGeneralTab() {
    if (autoQrRequestedRef.current || channel?.state === 'connected' || channel?.qrCodeImageDataUrl) {
      return;
    }
    autoQrRequestedRef.current = true;
    setChannelActionMessage('');
    setSectionErrors((current) => ({ ...current, channel: undefined }));
    try {
      const result = await withSettingsTimeout(
        getChannelStatus({ includeQr: true }),
        '微信通道暂不可用，请稍后重试。',
        CHANNEL_STATUS_TIMEOUT_MS,
      );
      setChannel((current) =>
        current?.qrCodeImageDataUrl && !result.qrCodeImageDataUrl ? current : result,
      );
    } catch (loadError) {
      setSectionErrors((current) => ({
        ...current,
        channel: (loadError as Error).message,
      }));
    }
  }

  function handleSettingsTabChange(tab: 'model' | 'general' | 'data') {
    if (tab === 'general') {
      void loadChannelQrOnGeneralTab();
    }
  }

  async function setChannelEnabled(enabled: boolean) {
    setChannelActionBusy(true);
    setChannelActionMessage('');
    setSectionErrors((current) => ({ ...current, channel: undefined }));
    try {
      const result = await saveChannelConfig({
        requestId: `channel-${Date.now()}`,
        channelKind: 'wechat_clawbot',
        configPatch: { enabled },
      });
      setChannel(result.status);
      if (enabled) {
        setChannelActionMessage(result.restartRequired ? '已提交重新连接，请重启后重新扫码。' : '已提交重新连接，请重新扫码。');
      } else {
        setChannelActionMessage('已解除连接。');
      }
    } catch (saveError) {
      setSectionErrors((current) => ({
        ...current,
        channel: (saveError as Error).message,
      }));
    } finally {
      setChannelActionBusy(false);
    }
  }

  async function reconnectChannel() {
    await setChannelEnabled(true);
  }

  async function disconnectChannel() {
    await setChannelEnabled(false);
  }

  function skipWechatSetup() {
    navigate('/');
  }

  async function saveLlm() {
    const draft = llm ?? DEFAULT_LLM_DRAFT;
    const reportModelDraft = {
      provider: draft.provider,
      defaultModel: draft.defaultModel,
      endpointUrl: draft.endpointUrl,
      apiKeyReplacement: draft.apiKeyReplacement,
    };
    setLlmActionBusy(true);
    setLlmActionMessage('');
    setSectionErrors((current) => ({ ...current, llm: undefined }));
    try {
      const result = await withSettingsTimeout(
        saveReportModelConfig({
          requestId: `llm-save-${Date.now()}`,
          draft: reportModelDraft,
          expectedSettingsVersion: llmSettingsVersion,
        }),
        '模型配置暂不可保存，请稍后重试。',
      );
      setLlm(withSavedUnverifiedStatus({ ...draft, status: 'saved', updatedAt: result.updatedAt }, result.updatedAt));
      if (result.settingsVersion) {
        setLlmSettingsVersion(result.settingsVersion);
      }
      setLlmActionMessage('模型配置已保存。');
    } catch (saveError) {
      setSectionErrors((current) => ({ ...current, llm: (saveError as Error).message }));
    } finally {
      setLlmActionBusy(false);
    }
  }

  async function testLlm() {
    const draft = llm ?? DEFAULT_LLM_DRAFT;
    setLlmActionBusy(true);
    setLlmActionMessage('');
    setSectionErrors((current) => ({ ...current, llm: undefined }));
    try {
      const result = await withSettingsTimeout(
        testReportModelConnection({
          requestId: `llm-test-${Date.now()}`,
          provider: draft.provider,
          model: draft.defaultModel,
          endpointUrl: draft.endpointUrl,
          apiKeyReplacement: draft.apiKeyReplacement,
        }),
        '模型连接测试暂不可用，请稍后重试。',
        MODEL_TEST_TIMEOUT_MS,
      );
      const userMessage = result.ok
        ? result.userMessage
        : sanitizeModelFailureMessage(result.userMessage);
      setLlm({
        ...draft,
        status: result.ok ? 'saved' : 'error',
        lastTestMessage: userMessage,
        reportModelStatus: result.ok
          ? {
              state: 'ready',
              blocked: false,
              ready: true,
              userMessage: '报告模型可用。',
              checkedAt: result.checkedAt,
            }
          : {
              state: 'failed',
              blocked: true,
              ready: false,
              userMessage,
              checkedAt: result.checkedAt,
            },
      });
      setLlmActionMessage(userMessage);
    } catch (testError) {
      setSectionErrors((current) => ({ ...current, llm: (testError as Error).message }));
    } finally {
      setLlmActionBusy(false);
    }
  }

  async function testEmbedding() {
    const draft = llm ?? DEFAULT_LLM_DRAFT;
    setEmbeddingActionBusy(true);
    setEmbeddingActionMessage('');
    setEmbeddingActionOk(false);
    setSectionErrors((current) => ({ ...current, embedding: undefined }));
    try {
      const result = await withSettingsTimeout(
        testEmbeddingConnection({
          requestId: `embedding-test-${Date.now()}`,
          embedding: currentEmbeddingDraft(draft),
        }),
        'Embedding 测试暂不可用，请稍后重试。',
        MODEL_TEST_TIMEOUT_MS,
      );
      setEmbeddingActionOk(result.ok);
      setEmbeddingActionMessage(result.userMessage);
    } catch (testError) {
      setSectionErrors((current) => ({ ...current, embedding: (testError as Error).message }));
    } finally {
      setEmbeddingActionBusy(false);
    }
  }

  async function saveEmbedding() {
    const draft = llm ?? DEFAULT_LLM_DRAFT;
    const embedding = currentEmbeddingDraft(draft);
    setEmbeddingActionBusy(true);
    setEmbeddingActionMessage('');
    setEmbeddingActionOk(false);
    setSectionErrors((current) => ({ ...current, embedding: undefined }));
    try {
      await withSettingsTimeout(
        saveEmbeddingConfig({
          requestId: `embedding-save-${Date.now()}`,
          embedding,
        }),
        'Embedding 配置暂不可保存，请稍后重试。',
      );
      setLlm({
        ...draft,
        embedding: {
          ...embedding,
          enabled: Boolean(embedding.provider?.trim() && embedding.model?.trim()),
        },
      });
      setEmbeddingActionOk(true);
      setEmbeddingActionMessage('Embedding 配置已保存。');
    } catch (saveError) {
      setSectionErrors((current) => ({ ...current, embedding: (saveError as Error).message }));
    } finally {
      setEmbeddingActionBusy(false);
    }
  }

  async function saveDataSource() {
    setDataSourceActionBusy(true);
    setDataSourceActionMessage('');
    setSectionErrors((current) => ({ ...current, dataSources: undefined }));
    try {
      const saved = await withSettingsTimeout(
        saveDataSourceInstance({
          requestId: `data-source-save-${Date.now()}`,
          instance: toDataSourceDraftPayload(dataSourceDraft),
        }),
        '数据源暂不可保存，请稍后重试。',
      );
      setDataSources((current) => {
        if (current.some((item) => item.supportedType === saved.supportedType)) {
          return current.map((item) => (item.supportedType === saved.supportedType ? saved : item));
        }
        return [...current, saved];
      });
      setDataSourceDraft(draftFromDataSource(saved));
      setDataSourceActionMessage('数据源配置已保存。');
    } catch (saveError) {
      setSectionErrors((current) => ({ ...current, dataSources: (saveError as Error).message }));
    } finally {
      setDataSourceActionBusy(false);
    }
  }

  async function testDataSourceDraft() {
    setDataSourceActionBusy(true);
    setDataSourceActionMessage('');
    setSectionErrors((current) => ({ ...current, dataSources: undefined }));
    try {
      const result = await withSettingsTimeout(
        testDataSource({
          requestId: `data-source-test-${Date.now()}`,
          instanceDraft: toDataSourceDraftPayload(dataSourceDraft),
        }),
        '数据源测试暂不可用，请稍后重试。',
      );
      setDataSourceDraft((current) => ({ ...current, state: result.state }));
      setDataSourceActionMessage(result.healthEvent.userMessage);
    } catch (testError) {
      setSectionErrors((current) => ({ ...current, dataSources: (testError as Error).message }));
    } finally {
      setDataSourceActionBusy(false);
    }
  }

  async function saveReportCleanup() {
    setCleanupActionBusy(true);
    setCleanupActionMessage('');
    setSectionErrors((current) => ({ ...current, reportCleanup: undefined }));
    try {
      const result = await withSettingsTimeout(
        saveReportCleanupSettings({
          requestId: `report-cleanup-save-${Date.now()}`,
          reportRetentionDays: reportCleanup.reportRetentionDays,
        }),
        '报告保留设置暂不可保存，请稍后重试。',
      );
      setReportCleanup(result.reportCleanup);
      setCleanupActionMessage('报告保留时间已保存。');
    } catch (saveError) {
      setSectionErrors((current) => ({
        ...current,
        reportCleanup: (saveError as Error).message,
      }));
    } finally {
      setCleanupActionBusy(false);
    }
  }

  async function saveSelectionAutoRefresh() {
    setSelectionAutoRefreshActionBusy(true);
    setSelectionAutoRefreshActionMessage('');
    setSectionErrors((current) => ({ ...current, selectionAutoRefresh: undefined }));
    try {
      const result = await withSettingsTimeout(
        saveSelectionAutoRefreshSettings({
          requestId: `selection-auto-refresh-save-${Date.now()}`,
          enabled: selectionAutoRefresh.enabled,
        }),
        '选股刷新设置暂不可保存，请稍后重试。',
      );
      setSelectionAutoRefresh(result.selectionAutoRefresh ?? DEFAULT_SELECTION_AUTO_REFRESH);
      setSelectionAutoRefreshActionMessage('选股刷新设置已保存。');
    } catch (saveError) {
      setSectionErrors((current) => ({
        ...current,
        selectionAutoRefresh: (saveError as Error).message,
      }));
    } finally {
      setSelectionAutoRefreshActionBusy(false);
    }
  }

  async function resetSettings() {
    const confirmed = window.confirm(
      '恢复默认设置会清空本页保存的报告模型、Embedding、增强数据源、报告保留时间、选股刷新和微信通知连接设置，但不会删除历史报告。确定继续吗？',
    );
    if (!confirmed) {
      return;
    }
    setResetActionBusy(true);
    setResetActionMessage('');
    setSectionErrors((current) => ({ ...current, reset: undefined }));
    try {
      const result = await withSettingsTimeout(
        resetSettingsToDefaults({ requestId: `settings-reset-${Date.now()}` }),
        '设置暂不可恢复默认，请稍后重试。',
      );
      setLlm(DEFAULT_LLM_DRAFT);
      setLlmSettingsVersion(result.llm.settingsVersion ?? '');
      autoQrRequestedRef.current = false;
      setChannel(result.channel);
      setDataSources(result.dataSources.instances);
      setDataSourceDraft(createDataSourceDraft(result.dataSources.instances[0]));
      setReportCleanup(result.reportCleanup);
      setSelectionAutoRefresh(result.selectionAutoRefresh ?? DEFAULT_SELECTION_AUTO_REFRESH);
      setEmbeddingActionMessage('');
      setEmbeddingActionOk(false);
      setLlmActionMessage('');
      setDataSourceActionMessage('');
      setChannelActionMessage('');
      setCleanupActionMessage('');
      setSelectionAutoRefreshActionMessage('');
      setResetActionMessage(result.userMessage);
    } catch (resetError) {
      setSectionErrors((current) => ({ ...current, reset: (resetError as Error).message }));
    } finally {
      setResetActionBusy(false);
    }
  }

  return (
    <AppShell>
      <div className="ct-settings-wrap">
        <header className="ct-page-head">
          <div>
            <h1 className="ct-page-title">设置</h1>
            <p className="ct-page-subtitle">管理报告模型、增强数据源和微信通知。</p>
          </div>
        </header>
        {loading ? <div className="ct-notice">加载中...</div> : null}
        <SettingsSections
          channel={channel}
          llm={llm ?? DEFAULT_LLM_DRAFT}
          dataSources={dataSources}
          dataSourceDraft={dataSourceDraft}
          reportCleanup={reportCleanup}
          selectionAutoRefreshEnabled={selectionAutoRefresh.enabled ?? true}
          sectionErrors={sectionErrors}
          channelActionBusy={channelActionBusy}
          channelActionMessage={channelActionMessage}
          llmActionBusy={llmActionBusy}
          llmActionMessage={llmActionMessage}
          embeddingActionBusy={embeddingActionBusy}
          embeddingActionMessage={embeddingActionMessage}
          embeddingActionOk={embeddingActionOk}
          dataSourceActionBusy={dataSourceActionBusy}
          dataSourceActionMessage={dataSourceActionMessage}
          cleanupActionBusy={cleanupActionBusy}
          cleanupActionMessage={cleanupActionMessage}
          selectionAutoRefreshActionBusy={selectionAutoRefreshActionBusy}
          selectionAutoRefreshActionMessage={selectionAutoRefreshActionMessage}
          resetActionBusy={resetActionBusy}
          resetActionMessage={resetActionMessage}
          onReconnectChannel={reconnectChannel}
          onDisconnectChannel={disconnectChannel}
          onSkipWechatSetup={skipWechatSetup}
          onRefreshChannel={refreshChannel}
          onSettingsTabChange={handleSettingsTabChange}
          onLlmChange={(patch) =>
            setLlm((current) => ({
              ...(current ?? DEFAULT_LLM_DRAFT),
              ...patch,
              embedding: patch.embedding ?? current?.embedding ?? DEFAULT_LLM_DRAFT.embedding,
            }))
          }
          onSaveLlm={saveLlm}
          onTestLlm={testLlm}
          onSaveEmbedding={saveEmbedding}
          onTestEmbedding={testEmbedding}
          onDataSourceDraftChange={(patch) => setDataSourceDraft((current) => ({ ...current, ...patch }))}
          onEditDataSource={(item) => setDataSourceDraft(draftFromDataSource(item))}
          onSaveDataSource={saveDataSource}
          onTestDataSource={testDataSourceDraft}
          onReportCleanupChange={(reportRetentionDays: ReportRetentionDays) =>
            setReportCleanup({ reportRetentionDays })
          }
          onSaveReportCleanup={saveReportCleanup}
          onSelectionAutoRefreshChange={(enabled: boolean) => setSelectionAutoRefresh({ enabled })}
          onSaveSelectionAutoRefresh={saveSelectionAutoRefresh}
          onResetSettings={resetSettings}
        />
      </div>
    </AppShell>
  );
}
