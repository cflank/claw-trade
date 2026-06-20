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
      userMessage: null,
      lastErrorMessage: null,
      checkedAt: null,
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
});
