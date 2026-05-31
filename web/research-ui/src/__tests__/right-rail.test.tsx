import { describe, expect, it } from 'vitest';
import { render, screen, within } from '@testing-library/react';
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
});
