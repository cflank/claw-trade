import { useEffect, useState } from 'react';
import { AppShell } from '../components/AppShell';
import { SettingsSections } from '../components/SettingsSections';
import {
  getChannelStatus,
  listDataSources,
  loadLlmSettings,
  saveChannelConfigViaOpenClaw,
  saveDataSourceInstance,
  saveLlmConfigViaOpenClaw,
  testDataSource,
  testLlmViaOpenClaw,
  type ChannelStatusForUser,
  type DataSourceInstanceDraftInput,
  type DataSourceInstanceForUser,
  type LlmConfigDraft,
} from '../api/workspace';

type SectionErrors = {
  channel?: string;
  llm?: string;
  dataSources?: string;
};

const DEVICE_UI_HREF = '/api/ui/open-device-interface';
const SETTINGS_LOAD_TIMEOUT_MS = 8000;
const CHANNEL_STATUS_TIMEOUT_MS = 50000;
const CHANNEL_LOGIN_POLL_MS = 2000;
const DEFAULT_LLM_DRAFT: LlmConfigDraft = {
  provider: 'deepseek',
  defaultModel: '',
  status: 'idle',
  embedding: {
    provider: '',
    model: '',
    endpointUrl: '',
    dimension: '',
    apiKeyReplacement: '',
    enabled: false,
  },
};

function createDataSourceDraft(supportedType = 'tushare'): DataSourceInstanceDraftInput {
  return {
    supportedType,
    group: 'custom',
    displayName: supportedType,
    enabled: false,
    apiKeyReplacement: '',
    endpointUrl: '',
    proxyUrl: '',
    headerName: '',
    priority: 100,
    state: 'draft',
    requiresKey: true,
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
    proxyUrl: item.proxyUrl ?? '',
    headerName: item.headerName ?? '',
    priority: item.priority,
    state: item.state,
    requiresKey: !item.apiKeyMasked,
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

export function SettingsPage() {
  const [channel, setChannel] = useState<ChannelStatusForUser | null>(null);
  const [llm, setLlm] = useState<LlmConfigDraft | null>(null);
  const [llmSettingsVersion, setLlmSettingsVersion] = useState('');
  const [dataSources, setDataSources] = useState<DataSourceInstanceForUser[]>([]);
  const [supportedTypes, setSupportedTypes] = useState<string[]>([]);
  const [dataSourceDraft, setDataSourceDraft] = useState<DataSourceInstanceDraftInput>(() =>
    createDataSourceDraft(),
  );
  const [loading, setLoading] = useState(true);
  const [sectionErrors, setSectionErrors] = useState<SectionErrors>({});
  const [channelActionBusy, setChannelActionBusy] = useState(false);
  const [channelActionMessage, setChannelActionMessage] = useState('');
  const [llmActionBusy, setLlmActionBusy] = useState(false);
  const [llmActionMessage, setLlmActionMessage] = useState('');
  const [dataSourceActionBusy, setDataSourceActionBusy] = useState(false);
  const [dataSourceActionMessage, setDataSourceActionMessage] = useState('');

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setSectionErrors({});
      setChannelActionMessage('');
      setLlmActionMessage('');
      setDataSourceActionMessage('');
      let pending = 3;
      const finishOne = () => {
        pending -= 1;
        if (active && pending <= 0) {
          setLoading(false);
        }
      };
      void withSettingsTimeout(
        getChannelStatus({ includeQr: true }),
        '微信通道暂不可用，请稍后重试。',
        CHANNEL_STATUS_TIMEOUT_MS,
      )
        .then((result) => {
          if (active) {
            setChannel(result);
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
            setLlm(result.draft);
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
            setSupportedTypes(result.supportedTypes);
            setDataSourceDraft((current) =>
              current.supportedType ? current : createDataSourceDraft(result.supportedTypes[0]),
            );
          }
        })
        .catch((loadError) => {
          if (active) {
            setSectionErrors((current) => ({ ...current, dataSources: (loadError as Error).message }));
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

  async function enableChannel() {
    setChannelActionBusy(true);
    setChannelActionMessage('');
    setSectionErrors((current) => ({ ...current, channel: undefined }));
    try {
      const result = await saveChannelConfigViaOpenClaw({
        requestId: `channel-${Date.now()}`,
        channelKind: 'wechat_clawbot',
        configPatch: { enabled: true },
      });
      setChannel(result.status);
      setChannelActionMessage(
        result.restartRequired
          ? '已提交启用，请重启后在 OpenClaw 主界面扫码。'
          : '已提交启用，请在 OpenClaw 主界面扫码。',
      );
    } catch (saveError) {
      setSectionErrors((current) => ({
        ...current,
        channel: (saveError as Error).message,
      }));
    } finally {
      setChannelActionBusy(false);
    }
  }

  async function saveLlm() {
    const draft = llm ?? DEFAULT_LLM_DRAFT;
    setLlmActionBusy(true);
    setLlmActionMessage('');
    setSectionErrors((current) => ({ ...current, llm: undefined }));
    try {
      const result = await withSettingsTimeout(
        saveLlmConfigViaOpenClaw({
          requestId: `llm-save-${Date.now()}`,
          draft,
          expectedSettingsVersion: llmSettingsVersion,
        }),
        '模型配置暂不可保存，请稍后重试。',
      );
      setLlm({ ...draft, status: 'saved', updatedAt: result.updatedAt });
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
        testLlmViaOpenClaw({
          requestId: `llm-test-${Date.now()}`,
          provider: draft.provider,
          model: draft.defaultModel,
          endpointUrl: draft.endpointUrl,
        }),
        '模型连接测试暂不可用，请稍后重试。',
      );
      setLlm({ ...draft, status: result.ok ? 'saved' : 'error', lastTestMessage: result.userMessage });
      setLlmActionMessage(result.userMessage);
    } catch (testError) {
      setSectionErrors((current) => ({ ...current, llm: (testError as Error).message }));
    } finally {
      setLlmActionBusy(false);
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
          instance: dataSourceDraft,
        }),
        '数据源暂不可保存，请稍后重试。',
      );
      setDataSources((current) => {
        const existing = current.findIndex((item) => item.instanceId === saved.instanceId);
        if (existing < 0) {
          return [...current, saved];
        }
        return current.map((item, index) => (index === existing ? saved : item));
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
          instanceDraft: dataSourceDraft,
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

  return (
    <AppShell>
      <div className="ct-settings-wrap">
        {loading ? <div className="ct-notice">加载中...</div> : null}
        <SettingsSections
          channel={channel}
          llm={llm ?? DEFAULT_LLM_DRAFT}
          dataSources={dataSources}
          supportedTypes={supportedTypes}
          dataSourceDraft={dataSourceDraft}
          sectionErrors={sectionErrors}
          channelActionBusy={channelActionBusy}
          channelActionMessage={channelActionMessage}
          llmActionBusy={llmActionBusy}
          llmActionMessage={llmActionMessage}
          dataSourceActionBusy={dataSourceActionBusy}
          dataSourceActionMessage={dataSourceActionMessage}
          deviceUiHref={DEVICE_UI_HREF}
          onEnableChannel={enableChannel}
          onRefreshChannel={refreshChannel}
          onLlmChange={(patch) =>
            setLlm((current) => ({
              ...(current ?? DEFAULT_LLM_DRAFT),
              ...patch,
              embedding: patch.embedding ?? current?.embedding ?? DEFAULT_LLM_DRAFT.embedding,
            }))
          }
          onSaveLlm={saveLlm}
          onTestLlm={testLlm}
          onDataSourceDraftChange={(patch) => setDataSourceDraft((current) => ({ ...current, ...patch }))}
          onEditDataSource={(item) => setDataSourceDraft(draftFromDataSource(item))}
          onNewDataSource={() => setDataSourceDraft(createDataSourceDraft(supportedTypes[0]))}
          onSaveDataSource={saveDataSource}
          onTestDataSource={testDataSourceDraft}
        />
      </div>
    </AppShell>
  );
}
