import { useEffect, useMemo, useState } from 'react';
import { AppShell } from '../components/AppShell';
import {
  getAdvancedDiagnosticsEvidenceFailureReasonSummary,
  getAdvancedDiagnosticsLiveRunGapSummary,
  getMaintenanceTaskDiagnostics,
  getAdvancedDiagnosticsProviderHealth,
  getAdvancedDiagnosticsRuntimeServiceStatus,
  type AdvancedDiagnosticsEvidenceFailureReasonSummaryOutput,
  type AdvancedDiagnosticsLiveRunGapSummaryOutput,
  type MaintenanceTaskDiagnosticsOutput,
  type AdvancedDiagnosticsProviderHealthOutput,
  type AdvancedDiagnosticsRuntimeServiceStatusOutput,
} from '../api/workspace';

type DiagnosticsState =
  | {
      loading: true;
      provider: null;
      runtime: null;
      liveRun: null;
      evidence: null;
      maintenance: MaintenanceTaskDiagnosticsOutput | null;
      error: '';
    }
  | {
      loading: false;
      provider: AdvancedDiagnosticsProviderHealthOutput | null;
      runtime: AdvancedDiagnosticsRuntimeServiceStatusOutput | null;
      liveRun: AdvancedDiagnosticsLiveRunGapSummaryOutput | null;
      evidence: AdvancedDiagnosticsEvidenceFailureReasonSummaryOutput | null;
      maintenance: MaintenanceTaskDiagnosticsOutput | null;
      error: string;
    };

const FALLBACK_MESSAGE = '当前暂无法读取 provider 健康摘要，请稍后重试。';
const RUNTIME_FALLBACK_MESSAGE = '当前暂无法读取运行服务状态摘要，请稍后重试。';
const LIVE_RUN_FALLBACK_MESSAGE = '当前暂无法读取最近 live run 缺口摘要，请稍后重试。';
const EVIDENCE_FALLBACK_MESSAGE = '当前暂无法读取证据链失败原因摘要，请稍后重试。';

function sanitizeDiagnosticsMessage(raw: string) {
  const message = raw.trim();
  if (!message) {
    return FALLBACK_MESSAGE;
  }
  const lowered = message.toLowerCase();
  if (
    lowered.includes('provider attempt') ||
    lowered.includes('raw payload') ||
    lowered.includes('request body') ||
    lowered.includes('provider request')
  ) {
    return 'provider 健康检查失败，网关调用返回异常。请检查模型配置后重试。';
  }
  return message;
}

function severityLabel(value: 'info' | 'success' | 'warning' | 'error' | undefined) {
  switch (value) {
    case 'info':
      return '待检查';
    case 'success':
      return '健康';
    case 'warning':
      return '异常';
    case 'error':
      return '失败';
    default:
      return '待检查';
  }
}

function sanitizeRuntimeDiagnosticsMessage(raw: string) {
  const message = raw.trim();
  if (!message) {
    return RUNTIME_FALLBACK_MESSAGE;
  }
  const lowered = message.toLowerCase();
  if (
    lowered.includes('18789') ||
    lowered.includes('1933') ||
    lowered.includes('gateway') ||
    lowered.includes('scope') ||
    lowered.includes('openviking') ||
    lowered.includes('/runtime/dev-services')
  ) {
    return '运行服务存在异常，请先重启本地运行时并重试。';
  }
  return message;
}

function sanitizeLiveRunGapMessage(raw: string) {
  const message = raw.trim();
  if (!message) {
    return LIVE_RUN_FALLBACK_MESSAGE;
  }
  const lowered = message.toLowerCase();
  if (
    lowered.includes('provider attempt') ||
    lowered.includes('raw evidence') ||
    lowered.includes('raw payload') ||
    lowered.includes('/runtime/') ||
    lowered.includes('/runs/')
  ) {
    return '最近 live run 缺口摘要读取异常，请重新执行 /report 后重试。';
  }
  return message;
}

function sanitizeEvidenceFailureMessage(raw: string) {
  const message = raw.trim();
  if (!message) {
    return EVIDENCE_FALLBACK_MESSAGE;
  }
  const lowered = message.toLowerCase();
  if (
    lowered.includes('uri') ||
    lowered.includes('hash') ||
    lowered.includes('l1') ||
    lowered.includes('l2') ||
    lowered.includes('provider attempt') ||
    lowered.includes('raw payload') ||
    lowered.includes('receipt')
  ) {
    return '证据链校验失败，请重新执行 /report 并按建议操作复查。';
  }
  return message;
}

function sanitizeEvidenceFailureAction(raw: string) {
  const message = raw.trim();
  if (!message) {
    return '先重新执行一次 /report；若仍失败，请开发者排查最近一次运行的证据链记录。';
  }
  const lowered = message.toLowerCase();
  if (
    lowered.includes('uri') ||
    lowered.includes('hash') ||
    lowered.includes('l1') ||
    lowered.includes('l2') ||
    lowered.includes('provider attempt') ||
    lowered.includes('raw payload') ||
    lowered.includes('receipt')
  ) {
    return '先重新执行一次 /report；若仍失败，请开发者排查最近一次运行的证据链记录。';
  }
  return message;
}

function formatMaintenanceDataJob(item: Record<string, unknown>) {
  const error = item.error;
  const errorMessage =
    typeof error === 'string'
      ? error.trim()
      : error && typeof error === 'object'
        ? String((error as { message?: unknown }).message ?? '').trim()
        : '';
  return `${String(item.market ?? '未知市场')}:${String(item.jobKind ?? '未知作业')} / ${String(item.status ?? 'unknown')} / ${String(
    item.maintenanceJobId || item.cronRunId || '暂无 run',
  )}${errorMessage ? ` / ${errorMessage}` : ''}`;
}

export function AdvancedDiagnosticsPage() {
  const [state, setState] = useState<DiagnosticsState>({
    loading: true,
    provider: null,
    runtime: null,
    liveRun: null,
    evidence: null,
    maintenance: null,
    error: '',
  });

  useEffect(() => {
    let active = true;
    void getMaintenanceTaskDiagnostics()
      .then((maintenanceData) => {
        if (!active) {
          return;
        }
        setState((current) => ({ ...current, maintenance: maintenanceData }));
      })
      .catch(() => undefined);

    void Promise.all([
      getAdvancedDiagnosticsProviderHealth(),
      getAdvancedDiagnosticsRuntimeServiceStatus(),
      getAdvancedDiagnosticsLiveRunGapSummary(),
      getAdvancedDiagnosticsEvidenceFailureReasonSummary(),
    ])
      .then(([providerData, runtimeData, liveRunData, evidenceData]) => {
        if (!active) {
          return;
        }
        setState((current) => ({
          loading: false,
          provider: { ...providerData, userMessage: sanitizeDiagnosticsMessage(providerData.userMessage) },
          runtime: {
            ...runtimeData,
            userMessage: sanitizeRuntimeDiagnosticsMessage(runtimeData.userMessage),
          },
          liveRun: {
            ...liveRunData,
            userMessage: sanitizeLiveRunGapMessage(liveRunData.userMessage),
          },
          evidence: {
            ...evidenceData,
            userMessage: sanitizeEvidenceFailureMessage(evidenceData.userMessage),
            recommendedAction: sanitizeEvidenceFailureAction(evidenceData.recommendedAction),
          },
          maintenance: current.maintenance,
          error: '',
        }));
      })
      .catch((error) => {
        if (!active) {
          return;
        }
        setState((current) => ({
          loading: false,
          provider: null,
          runtime: null,
          liveRun: null,
          evidence: null,
          maintenance: current.maintenance,
          error: (error as Error).message || FALLBACK_MESSAGE,
        }));
      });
    return () => {
      active = false;
    };
  }, []);

  const message = useMemo(() => {
    if (state.loading) {
      return '正在读取 provider 健康摘要...';
    }
    if (state.error) {
      return state.error;
    }
    return state.provider?.userMessage || FALLBACK_MESSAGE;
  }, [state]);

  const runtimeMessage = useMemo(() => {
    if (state.loading) {
      return '正在读取运行服务状态摘要...';
    }
    if (state.error) {
      return state.error;
    }
    return state.runtime?.userMessage || RUNTIME_FALLBACK_MESSAGE;
  }, [state]);

  const liveRunMessage = useMemo(() => {
    if (state.loading) {
      return '正在读取最近 live run 缺口摘要...';
    }
    if (state.error) {
      return state.error;
    }
    return state.liveRun?.userMessage || LIVE_RUN_FALLBACK_MESSAGE;
  }, [state]);

  const evidenceMessage = useMemo(() => {
    if (state.loading) {
      return '正在读取证据链失败原因摘要...';
    }
    if (state.error) {
      return state.error;
    }
    return state.evidence?.userMessage || EVIDENCE_FALLBACK_MESSAGE;
  }, [state]);

  return (
    <AppShell>
      <main className="ct-settings-wrap" data-testid="advanced-diagnostics-page">
        <section className="ct-settings-section">
          <div className="ct-section-head">
            <h2>高级诊断</h2>
            <span
              className={`ct-status-pill ct-status-${state.provider?.severity ?? 'unknown'}`}
              data-testid="provider-health-status"
            >
              {state.provider ? severityLabel(state.provider.severity) : '待检查'}
            </span>
          </div>
          <div className="ct-kv">
            <span>诊断项</span>
            <span>provider 健康摘要</span>
          </div>
          <div className="ct-kv">
            <span>状态</span>
            <span>{state.provider ? severityLabel(state.provider.severity) : state.loading ? '读取中' : '读取失败'}</span>
          </div>
          <div className="ct-kv">
            <span>摘要</span>
            <span data-testid="provider-health-message">{message}</span>
          </div>
          <div className="ct-kv">
            <span>检查时间</span>
            <span>{state.provider?.checkedAt ?? '—'}</span>
          </div>
          <div className="ct-kv">
            <span>诊断项</span>
            <span>运行服务状态摘要</span>
          </div>
          <div className="ct-kv">
            <span>状态</span>
            <span data-testid="runtime-status-label">
              {state.runtime ? severityLabel(state.runtime.severity) : state.loading ? '读取中' : '读取失败'}
            </span>
          </div>
          <div className="ct-kv">
            <span>摘要</span>
            <span data-testid="runtime-status-message">{runtimeMessage}</span>
          </div>
          <div className="ct-kv">
            <span>检查时间</span>
            <span>{state.runtime?.checkedAt ?? '—'}</span>
          </div>
          <div className="ct-kv">
            <span>诊断项</span>
            <span>最近 live run 缺口摘要</span>
          </div>
          <div className="ct-kv">
            <span>状态</span>
            <span data-testid="live-run-gap-status-label">
              {state.liveRun ? severityLabel(state.liveRun.severity) : state.loading ? '读取中' : '读取失败'}
            </span>
          </div>
          <div className="ct-kv">
            <span>摘要</span>
            <span data-testid="live-run-gap-message">{liveRunMessage}</span>
          </div>
          <div className="ct-kv">
            <span>建议操作</span>
            <span>{state.liveRun?.recommendedAction ?? '—'}</span>
          </div>
          <div className="ct-kv">
            <span>最近 run</span>
            <span>{state.liveRun?.latestRun?.runId ?? '—'}</span>
          </div>
          <div className="ct-kv">
            <span>缺口数量</span>
            <span>{state.liveRun?.latestRun ? `${state.liveRun.latestRun.gapCount} 条` : '—'}</span>
          </div>
          <div className="ct-kv">
            <span>collect-first 覆盖</span>
            <span>
              {state.liveRun?.latestRun
                ? `${state.liveRun.latestRun.collectFirstCompliance.batchScope.stageCount} 个阶段 / ${state.liveRun.latestRun.collectFirstCompliance.completedItems} 条完成项`
                : '—'}
            </span>
          </div>
          <div className="ct-kv">
            <span>检查时间</span>
            <span>{state.liveRun?.checkedAt ?? '—'}</span>
          </div>
          <div className="ct-kv">
            <span>诊断项</span>
            <span>证据链失败原因摘要</span>
          </div>
          <div className="ct-kv">
            <span>状态</span>
            <span data-testid="evidence-failure-status-label">
              {state.evidence ? severityLabel(state.evidence.severity) : state.loading ? '读取中' : '读取失败'}
            </span>
          </div>
          <div className="ct-kv">
            <span>摘要</span>
            <span data-testid="evidence-failure-message">{evidenceMessage}</span>
          </div>
          <div className="ct-kv">
            <span>建议操作</span>
            <span data-testid="evidence-failure-action">{state.evidence?.recommendedAction ?? '—'}</span>
          </div>
          <div className="ct-kv">
            <span>最近 run</span>
            <span>{state.evidence?.latestRun?.runId ?? '—'}</span>
          </div>
          <div className="ct-kv">
            <span>检查时间</span>
            <span>{state.evidence?.checkedAt ?? '—'}</span>
          </div>
          <div className="ct-kv">
            <span>诊断项</span>
            <span>维护任务明细</span>
          </div>
          <div className="ct-kv">
            <span>selection 刷新</span>
            <span data-testid="maintenance-selection-refresh">
              {(state.maintenance?.selectionRefresh ?? [])
                .map((item) => `${item.market}: ${item.status} / ${item.runId ?? '暂无 run'}${item.failureReason ? ` / ${item.failureReason}` : ''}`)
                .join('；') || '—'}
            </span>
          </div>
          <div className="ct-kv">
            <span>定时报表 wake</span>
            <span data-testid="maintenance-scheduled-report-wake">
              {(state.maintenance?.scheduledReportWakes ?? []).length
                ? (state.maintenance?.scheduledReportWakes ?? [])
                    .map(
                      (item) =>
                        `${String(item.scheduledReportId ?? '未知计划')}: ${String(item.status ?? 'unknown')} / ${String(
                          item.cronRunId ?? '暂无 cron',
                        )}${item.deduped ? ' / 已去重' : ''}${item.skipped ? ' / 已跳过' : ''}${
                          item.lastRunTaskId ? ` / ${String(item.lastRunTaskId)}` : ''
                        }`,
                    )
                    .join('；')
                : '暂无定时报表 wake'}
            </span>
          </div>
          <div className="ct-kv">
            <span>价格扫描 bucket</span>
            <span data-testid="maintenance-price-scan">
              {(state.maintenance?.priceAlertScanBuckets ?? []).length
                ? (state.maintenance?.priceAlertScanBuckets ?? [])
                    .map(
                      (bucket) =>
                        `${bucket.bucketKey}: ${bucket.enabled ? '启用' : '跳过'} / ${bucket.lastScanRunId ?? '暂无扫描'}${
                          bucket.lastErrorMessage ? ` / ${bucket.lastErrorMessage}` : ''
                        }${bucket.skippedReason ? ` / ${bucket.skippedReason}` : ''}`,
                    )
                    .join('；')
                : '暂无扫描 bucket'}
            </span>
          </div>
          <div className="ct-kv">
            <span>数据维护</span>
            <span data-testid="maintenance-data-jobs">
              {(state.maintenance?.dataMaintenance ?? []).length
                ? (state.maintenance?.dataMaintenance ?? []).map((item) => formatMaintenanceDataJob(item)).join('；')
                : '暂无数据维护运行记录'}
            </span>
          </div>
          <div className="ct-kv">
            <span>报告清理</span>
            <span data-testid="maintenance-report-cleanup">
              {state.maintenance?.reportCleanup
                ? `${String(state.maintenance.reportCleanup.status ?? 'unknown')} / ${String(state.maintenance.reportCleanup.runId ?? '暂无 run')}${
                    state.maintenance.reportCleanup.error
                      ? ` / ${String((state.maintenance.reportCleanup.error as { message?: unknown }).message ?? '')}`
                      : ''
                  }`
                : '—'}
            </span>
          </div>
          {state.error ? <div className="ct-notice ct-notice-error">{state.error}</div> : null}
        </section>
      </main>
    </AppShell>
  );
}
