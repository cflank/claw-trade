import { afterEach, describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AdvancedDiagnosticsPage } from '../routes/AdvancedDiagnosticsPage';

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

describe('advanced diagnostics page', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('renders provider health summary from advanced diagnostics endpoint', async () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/get-advanced-diagnostics-provider-health')) {
        return json({
          state: 'healthy',
          severity: 'success',
          userMessage: 'provider 健康检查通过。',
          checkedAt: '2026-05-23T12:00:00Z',
          provider: 'deepseek',
          model: 'deepseek-chat',
          source: 'openclaw.models.authStatus',
        });
      }
      if (url.includes('/api/ui/get-advanced-diagnostics-runtime-service-status')) {
        return json({
          state: 'healthy',
          severity: 'success',
          userMessage: '运行服务健康检查通过，当前可继续执行报告相关操作。',
          checkedAt: '2026-05-23T12:00:00Z',
          source: 'runtime.health.http',
        });
      }
      if (url.includes('/api/ui/get-advanced-diagnostics-live-run-gap-summary')) {
        return json({
          state: 'no_gaps',
          severity: 'success',
          userMessage: '最近 live run 未发现缺口。',
          checkedAt: '2026-05-23T12:00:00Z',
          source: 'workflow.collect_first_report',
          latestRun: {
            runId: 'run-20260523-001209-760fab0d',
            finishedAt: '2026-05-23T00:19:54Z',
            market: 'CRYPTO',
            entryPoint: 'report_command',
            collectFirstReportCount: 10,
            gapCount: 0,
            collectFirstCompliance: {
              batchScope: { stageCount: 7, stages: ['frontline', 'final_report'] },
              completedItems: 15,
              failuresCollected: 0,
              earlyStopExceptionUsed: false,
              exceptionEvidence: 0,
              batchFixGrouping: 0,
            },
          },
          recommendedAction: '可继续观察后续运行；若出现失败再回到高级诊断复查。',
        });
      }
      if (url.includes('/api/ui/get-advanced-diagnostics-evidence-failure-reason-summary')) {
        return json({
          state: 'no_failures',
          severity: 'success',
          userMessage: '最近 live run 未发现证据链失败。',
          checkedAt: '2026-05-23T12:00:00Z',
          source: 'workflow.evidence_chain',
          latestRun: {
            runId: 'run-20260523-001209-760fab0d',
            finishedAt: '2026-05-23T00:19:54Z',
            market: 'CRYPTO',
            entryPoint: 'report_command',
          },
          recommendedAction: '保持当前配置；后续若出现失败再回到高级诊断复查。',
        });
      }
      if (url.includes('/api/ui/get-maintenance-task-diagnostics')) {
        return json({
          checkedAt: '2026-06-24T12:00:00Z',
          selectionRefresh: [
            {
              kind: 'selection_data_refresh',
              market: 'CN_A',
              status: 'completed',
              runId: 'select-run-1',
              lastResult: '数据已准备',
              failureReason: null,
            },
          ],
          scheduledReportWakes: [
            {
              kind: 'scheduled_report',
              scheduledReportId: 'schedule-1',
              cronRunId: 'cron-run-2',
              status: 'ok',
              deduped: true,
              skipped: true,
              lastRunTaskId: 'task-1',
            },
          ],
          priceAlertScanBuckets: [
            {
              bucketKey: 'CRYPTO:3m',
              market: 'CRYPTO',
              frequency: '3m',
              enabled: true,
              lastScanRunId: 'scan-1',
              lastErrorMessage: null,
              skippedReason: null,
            },
          ],
          dataMaintenance: [{ kind: 'data_maintenance', market: 'CN_A', jobKind: 'eod', status: 'ok', maintenanceJobId: 'job-1' }],
          reportCleanup: { kind: 'report_cleanup', status: 'ok', runId: 'cleanup-1' },
        });
      }
      return json({});
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <AdvancedDiagnosticsPage />
      </MemoryRouter>,
    );

    expect(await screen.findByTestId('advanced-diagnostics-page')).toBeInTheDocument();
    expect(screen.getByText('provider 健康摘要')).toBeInTheDocument();
    expect(screen.getByText('运行服务状态摘要')).toBeInTheDocument();
    expect(screen.getByText('最近 live run 缺口摘要')).toBeInTheDocument();
    expect(screen.getByText('证据链失败原因摘要')).toBeInTheDocument();
    expect(screen.getByTestId('provider-health-message')).toHaveTextContent('provider 健康检查通过。');
    expect(screen.getByTestId('provider-health-status')).toHaveTextContent('健康');
    expect(screen.getByTestId('runtime-status-message')).toHaveTextContent('运行服务健康检查通过，当前可继续执行报告相关操作。');
    expect(screen.getByTestId('runtime-status-label')).toHaveTextContent('健康');
    expect(screen.getByTestId('live-run-gap-message')).toHaveTextContent('最近 live run 未发现缺口。');
    expect(screen.getByTestId('live-run-gap-status-label')).toHaveTextContent('健康');
    expect(screen.getByTestId('evidence-failure-message')).toHaveTextContent('最近 live run 未发现证据链失败。');
    expect(screen.getByTestId('evidence-failure-status-label')).toHaveTextContent('健康');
    expect(screen.getByTestId('maintenance-selection-refresh')).toHaveTextContent('CN_A: completed / select-run-1');
    expect(screen.getByTestId('maintenance-scheduled-report-wake')).toHaveTextContent(
      'schedule-1: ok / cron-run-2 / 已去重 / 已跳过 / task-1',
    );
    expect(screen.getByTestId('maintenance-price-scan')).toHaveTextContent('CRYPTO:3m: 启用 / scan-1');
    expect(screen.getByTestId('maintenance-data-jobs')).toHaveTextContent('CN_A:eod / ok / job-1');
    expect(screen.getByTestId('maintenance-report-cleanup')).toHaveTextContent('ok / cleanup-1');
  });

  it('does not show provider attempt or raw request details even if endpoint returns them', async () => {
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes('/api/ui/get-advanced-diagnostics-provider-health')) {
        return json({
          state: 'degraded',
          severity: 'warning',
          userMessage: 'gateway failed: provider attempt #2 raw payload request body leaked',
          checkedAt: '2026-05-23T12:00:00Z',
          provider: 'deepseek',
          model: 'deepseek-chat',
          source: 'openclaw.models.authStatus',
        });
      }
      if (url.includes('/api/ui/get-advanced-diagnostics-live-run-gap-summary')) {
        return json({
          state: 'gaps_detected',
          severity: 'warning',
          userMessage:
            'run failed: provider attempt #3 raw evidence leaked /runtime/dev-services/run-20260523/reports',
          checkedAt: '2026-05-23T12:00:00Z',
          source: 'workflow.collect_first_report',
          latestRun: {
            runId: 'run-20260523-001209-760fab0d',
            finishedAt: '2026-05-23T00:19:54Z',
            market: 'CRYPTO',
            entryPoint: 'report_command',
            collectFirstReportCount: 10,
            gapCount: 1,
            collectFirstCompliance: {
              batchScope: { stageCount: 7, stages: ['frontline', 'final_report'] },
              completedItems: 14,
              failuresCollected: 1,
              earlyStopExceptionUsed: false,
              exceptionEvidence: 0,
              batchFixGrouping: 1,
            },
          },
          recommendedAction: '优先排查缺口涉及阶段，再执行一次 /report 验证。',
        });
      }
      if (url.includes('/api/ui/get-advanced-diagnostics-evidence-failure-reason-summary')) {
        return json({
          state: 'failure_detected',
          severity: 'warning',
          userMessage: 'evidence failed: uri=viking://... hash=abc l1 l2 receipt leaked',
          checkedAt: '2026-05-23T12:00:00Z',
          source: 'workflow.evidence_chain',
          latestRun: {
            runId: 'run-20260523-001209-760fab0d',
            finishedAt: '2026-05-23T00:19:54Z',
            market: 'CRYPTO',
            entryPoint: 'report_command',
          },
          recommendedAction: 'check URI/hash/L1/L2 raw payload receipt before rerun',
        });
      }
      if (url.includes('/api/ui/get-maintenance-task-diagnostics')) {
        return json({
          checkedAt: '2026-06-24T12:00:00Z',
          selectionRefresh: [],
          priceAlertScanBuckets: [],
          dataMaintenance: [],
          reportCleanup: { kind: 'report_cleanup', status: 'not_run', runId: null },
        });
      }
      return json({
        state: 'degraded',
        severity: 'warning',
        userMessage: 'http://127.0.0.1:1933/health failed; gateway scope path /runtime/dev-services/openviking/ov.conf',
        checkedAt: '2026-05-23T12:00:00Z',
        source: 'runtime.health.http',
      });
    }) as typeof fetch;

    render(
      <MemoryRouter>
        <AdvancedDiagnosticsPage />
      </MemoryRouter>,
    );

    const message = await screen.findByTestId('provider-health-message');
    const text = message.textContent?.toLowerCase() ?? '';
    expect(text).not.toContain('provider attempt');
    expect(text).not.toContain('raw payload');
    expect(text).not.toContain('request body');

    const runtimeText = (await screen.findByTestId('runtime-status-message')).textContent?.toLowerCase() ?? '';
    expect(runtimeText).not.toContain('18789');
    expect(runtimeText).not.toContain('1933');
    expect(runtimeText).not.toContain('gateway');
    expect(runtimeText).not.toContain('scope');
    expect(runtimeText).not.toContain('openviking');
    expect(runtimeText).not.toContain('/runtime/dev-services');

    const liveRunText = (await screen.findByTestId('live-run-gap-message')).textContent?.toLowerCase() ?? '';
    expect(liveRunText).not.toContain('provider attempt');
    expect(liveRunText).not.toContain('raw evidence');
    expect(liveRunText).not.toContain('/runtime/');

    const evidenceText = (await screen.findByTestId('evidence-failure-message')).textContent?.toLowerCase() ?? '';
    expect(evidenceText).not.toContain('uri');
    expect(evidenceText).not.toContain('hash');
    expect(evidenceText).not.toContain('l1');
    expect(evidenceText).not.toContain('l2');
    expect(evidenceText).not.toContain('receipt');

    const evidenceAction = (await screen.findByTestId('evidence-failure-action')).textContent?.toLowerCase() ?? '';
    expect(evidenceAction).not.toContain('uri');
    expect(evidenceAction).not.toContain('hash');
    expect(evidenceAction).not.toContain('l1');
    expect(evidenceAction).not.toContain('l2');
    expect(evidenceAction).not.toContain('raw payload');
    expect(evidenceAction).not.toContain('receipt');
  });
});
