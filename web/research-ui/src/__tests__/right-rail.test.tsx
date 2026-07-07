import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, within } from '@testing-library/react';
import { RightRail } from '../components/RightRail';
import type { ChannelStatusForUser, ReportQueueSnapshotForUser } from '../api/contracts';

const EMPTY_QUEUE: ReportQueueSnapshotForUser = {
  runningTask: null,
  queuedTasks: [],
  lastTerminalTask: null,
  queueLimit: 10,
  queuedCount: 0,
  isFull: false,
};

describe('RightRail channel state text', () => {
  it('shows checking text instead of raw unknown when channel is missing', () => {
    render(<RightRail queue={EMPTY_QUEUE} detail={null} channel={null} latestReport={null} />);

    const section = screen.getByText('微信通知').closest('section');
    expect(section).not.toBeNull();
    const scope = within(section!);
    expect(scope.getByText('检查中')).toBeInTheDocument();
    expect(scope.queryByText('unknown')).not.toBeInTheDocument();
  });

  it('shows connected label when channel state is connected', () => {
    const channel: ChannelStatusForUser = {
      channelKind: 'wechat_clawbot',
      onboardingState: 'completed',
      state: 'connected',
      displayName: '微信 ClawBot',
      accountLabel: null,
      canSendText: true,
      canSendFile: false,
      qrCodeImageDataUrl: null,
      qrCodeExpiresAt: null,
      qrCodeRefreshRequired: false,
      lastErrorMessage: null,
    };
    render(<RightRail queue={EMPTY_QUEUE} detail={null} channel={channel} latestReport={null} />);
    expect(screen.getByText('已连接')).toBeInTheDocument();
  });

  it('shows stop and queue cancel controls for active report tasks', () => {
    const onCancelTask = vi.fn();
    const queue: ReportQueueSnapshotForUser = {
      runningTask: {
        taskId: 'task-running',
        source: 'manual',
        status: 'running',
        statusLabel: '生成中',
        instrumentCode: 'AAPL',
        market: 'US',
        companyName: 'Apple',
        currencySymbol: '$',
        startDate: '2026-05-01',
        endDate: '2026-05-19',
        currentDate: '2026-05-19',
        queuePosition: null,
        createdAt: '2026-05-19T09:55:00.000Z',
      },
      queuedTasks: [
        {
          taskId: 'task-queued',
          source: 'manual',
          status: 'queued',
          statusLabel: '排队中',
          instrumentCode: 'TSLA',
          market: 'US',
          companyName: 'Tesla',
          currencySymbol: '$',
          startDate: '2026-05-01',
          endDate: '2026-05-19',
          currentDate: '2026-05-19',
          queuePosition: 1,
          createdAt: '2026-05-19T09:56:00.000Z',
        },
      ],
      lastTerminalTask: null,
      queueLimit: 10,
      queuedCount: 1,
      isFull: false,
    };

    render(<RightRail queue={queue} detail={null} channel={null} latestReport={null} onCancelTask={onCancelTask} />);

    fireEvent.click(screen.getByRole('button', { name: '停止任务' }));
    fireEvent.click(screen.getByRole('button', { name: '取消排队' }));

    expect(onCancelTask).toHaveBeenNthCalledWith(1, expect.objectContaining({ taskId: 'task-running' }));
    expect(onCancelTask).toHaveBeenNthCalledWith(2, expect.objectContaining({ taskId: 'task-queued' }));
  });

  it('does not keep the latest failed report task in the active task rail', () => {
    const queue: ReportQueueSnapshotForUser = {
      runningTask: null,
      queuedTasks: [],
      lastTerminalTask: {
        taskId: 'task-failed',
        source: 'manual',
        status: 'failed',
        statusLabel: '失败',
        instrumentCode: 'BTC',
        market: 'CRYPTO',
        companyName: 'BTC',
        currencySymbol: 'USDT',
        startDate: '2026-05-01',
        endDate: '2026-05-19',
        currentDate: '2026-05-19',
        queuePosition: null,
        createdAt: '2026-05-19T09:55:00.000Z',
        finishedAt: '2026-05-19T10:00:00.000Z',
        failure: {
          code: 'ASSISTANT_UNAVAILABLE',
          message: '报告数据请求超时，请稍后重试。',
          severity: 'error',
        },
      },
      queueLimit: 10,
      queuedCount: 0,
      isFull: false,
    };

    render(<RightRail queue={queue} detail={null} channel={null} latestReport={null} onCancelTask={vi.fn()} />);

    const section = screen.getByTestId('right-rail-task-section');
    expect(within(section).getByText('暂无运行中或排队中的报告任务')).toBeInTheDocument();
    expect(within(section).queryByText('BTC')).not.toBeInTheDocument();
    expect(within(section).queryByText('失败')).not.toBeInTheDocument();
    expect(within(section).queryByText('报告数据请求超时，请稍后重试。')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '取消排队' })).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: '停止任务' })).not.toBeInTheDocument();
  });

  it('shows stop control for active selection progress', () => {
    const onCancelSelection = vi.fn();

    render(
      <RightRail
        queue={EMPTY_QUEUE}
        detail={null}
        channel={null}
        latestReport={null}
        selectionProgress={{
          kind: 'selection_workflow',
          status: 'running',
          statusLabel: '选股中',
          command: '/select',
          stageLabel: '选股工作流执行中',
          currentAction: '正在运行策略评审。',
          percent: 25,
          workerStatusLabels: ['策略评审：执行中'],
          completedRoleLabels: [],
          waitingRoleLabels: ['反方评审'],
          startedAt: '2026-06-04T10:00:00Z',
          workflowRunId: 'select-run-1',
        }}
        onCancelSelection={onCancelSelection}
      />,
    );

    fireEvent.click(screen.getByRole('button', { name: '停止选股' }));

    expect(onCancelSelection).toHaveBeenCalledWith(expect.objectContaining({ workflowRunId: 'select-run-1' }));
  });

  it('shows scheduled report management controls and cron evidence', () => {
    const onScheduledReportAction = vi.fn();

    render(
      <RightRail
        queue={EMPTY_QUEUE}
        detail={null}
        channel={null}
        latestReport={null}
        scheduledReports={[
          {
            scheduledReportId: 'schedule-1',
            instrumentCode: '600519.SH',
            instrumentName: '贵州茅台',
            market: 'CN_A',
            frequency: 'daily',
            timeOfDay: '08:00',
            weekday: null,
            notification: { channel: 'in_app', enabled: true },
            state: 'active',
            nextRunAt: '2026-06-25T00:00:00Z',
            cronJobId: 'cron-job-1',
            lastCronRunId: 'cron-run-1',
            lastRunTaskId: 'task-1',
          },
        ]}
        onScheduledReportAction={onScheduledReportAction}
      />,
    );

    expect(screen.getByText('定时报表管理')).toBeInTheDocument();
    expect(screen.getByText('600519.SH')).toBeInTheDocument();
    expect(screen.getByText('最近运行：task-1')).toBeInTheDocument();
    expect(screen.getByText('cron：cron-job-1 / cron-run-1')).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: '暂停' }));
    fireEvent.click(screen.getByRole('button', { name: '手动运行' }));
    fireEvent.click(screen.getByRole('button', { name: '删除' }));

    expect(onScheduledReportAction).toHaveBeenNthCalledWith(1, 'pause', 'schedule-1');
    expect(onScheduledReportAction).toHaveBeenNthCalledWith(2, 'run', 'schedule-1');
    expect(onScheduledReportAction).toHaveBeenNthCalledWith(3, 'delete', 'schedule-1');
  });

  it('shows price alert management controls and scan evidence', () => {
    const onPriceAlertAction = vi.fn();

    render(
      <RightRail
        queue={EMPTY_QUEUE}
        detail={null}
        channel={null}
        latestReport={null}
        priceAlerts={[
          {
            priceAlertId: 'alert-1',
            instrumentCode: 'BTC/USDT',
            instrumentName: 'Bitcoin',
            market: 'CRYPTO',
            condition: { type: 'price_threshold', operator: 'above', value: 70000, window: null },
            notification: { channel: 'in_app', enabled: true },
            state: 'active',
            lastCheckedAt: '2026-06-24T12:00:00Z',
            triggeredAt: null,
            lastErrorMessage: null,
            scanBucket: 'CRYPTO:3m',
            lastScanRunId: 'scan-1',
            lastQuote: { currentPrice: 71000 },
            notificationDedupeKey: 'dedupe-1',
          },
          {
            priceAlertId: 'alert-2',
            instrumentCode: 'ETH/USDT',
            instrumentName: 'Ethereum',
            market: 'CRYPTO',
            condition: { type: 'price_threshold', operator: 'below', value: 2500, window: null },
            notification: { channel: 'in_app', enabled: true },
            state: 'paused',
            lastCheckedAt: null,
            triggeredAt: null,
            lastErrorMessage: null,
            scanBucket: 'CRYPTO:3m',
            lastScanRunId: null,
            lastQuote: null,
            notificationDedupeKey: null,
          },
          {
            priceAlertId: 'alert-3',
            instrumentCode: 'SOL/USDT',
            instrumentName: 'Solana',
            market: 'CRYPTO',
            condition: { type: 'price_threshold', operator: 'above', value: 100, window: null },
            notification: { channel: 'wechat_clawbot', enabled: true },
            state: 'closed',
            lastCheckedAt: '2026-06-24T12:01:00Z',
            triggeredAt: '2026-06-24T12:01:00Z',
            lastErrorMessage: null,
            scanBucket: 'CRYPTO:3m',
            lastScanRunId: 'scan-2',
            lastQuote: { currentPrice: 110 },
            notificationDedupeKey: 'dedupe-2',
            lastNotificationResult: {
              channel: 'in_app',
              delivered: true,
              fallback_from: 'wechat_clawbot',
              channel_delivered: false,
              channel_error: 'missing_wechat_target',
            },
          },
          {
            priceAlertId: 'alert-4',
            instrumentCode: 'ADA/USDT',
            instrumentName: 'Cardano',
            market: 'CRYPTO',
            condition: { type: 'price_threshold', operator: 'above', value: 1, window: null },
            notification: { channel: 'wechat_clawbot', enabled: true },
            state: 'closed',
            lastCheckedAt: '2026-06-24T12:02:00Z',
            triggeredAt: '2026-06-24T12:02:00Z',
            lastErrorMessage: null,
            scanBucket: 'CRYPTO:3m',
            lastScanRunId: 'scan-3',
            lastQuote: { currentPrice: 1.1 },
            notificationDedupeKey: 'dedupe-3',
            lastNotificationResult: {
              channel: 'in_app',
              delivered: true,
              fallback_from: 'wechat_clawbot',
              channel_delivered: false,
              channel_error: 'wechat_send_failed',
            },
          },
        ]}
        onPriceAlertAction={onPriceAlertAction}
      />,
    );

    expect(screen.getByText('价格提醒管理')).toBeInTheDocument();
    expect(screen.getByText('BTC/USDT')).toBeInTheDocument();
    expect(screen.getByText('最近报价：71000')).toBeInTheDocument();
    expect(screen.getByText('扫描：CRYPTO:3m / scan-1')).toBeInTheDocument();
    expect(screen.getByText('扫描结果：已扫描')).toBeInTheDocument();
    expect(screen.getByText('扫描结果：已跳过：paused')).toBeInTheDocument();
    expect(screen.queryByText('SOL/USDT')).not.toBeInTheDocument();
    expect(screen.queryByText('ADA/USDT')).not.toBeInTheDocument();
    expect(screen.queryByText('扫描结果：已触发')).not.toBeInTheDocument();
    expect(screen.queryByText('通知：微信发送失败，已在页面显示')).not.toBeInTheDocument();

    fireEvent.click(screen.getAllByRole('button', { name: '暂停' })[0]);
    fireEvent.click(screen.getAllByRole('button', { name: '立即检查' })[0]);
    fireEvent.click(screen.getAllByRole('button', { name: '删除' })[0]);

    expect(onPriceAlertAction).toHaveBeenNthCalledWith(1, 'pause', 'alert-1');
    expect(onPriceAlertAction).toHaveBeenNthCalledWith(2, 'check', 'alert-1');
    expect(onPriceAlertAction).toHaveBeenNthCalledWith(3, 'delete', 'alert-1');
  });
});
