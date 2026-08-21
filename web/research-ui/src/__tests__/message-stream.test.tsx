import { render, screen, waitFor, within } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ChatMessageForUser, ConfirmationCard } from '../api/contracts';
import { MessageStream } from '../components/MessageStream';

function message(index: number): ChatMessageForUser {
  return {
    messageId: `msg-${index}`,
    contextKind: 'normal_chat',
    actor: index % 2 === 0 ? 'assistant' : 'user',
    kind: 'plain',
    text: `message ${index}`,
    createdAt: '2026-06-25T10:00:00.000Z',
  };
}

describe('MessageStream', () => {
  it('scrolls to the newest message when messages are appended', async () => {
    const initialMessages = Array.from({ length: 20 }, (_, index) => message(index));
    const { rerender } = render(<MessageStream items={initialMessages} />);
    const stream = screen.getByTestId('message-stream');

    Object.defineProperty(stream, 'scrollHeight', { configurable: true, value: 1200 });
    stream.scrollTop = 0;

    rerender(<MessageStream items={[...initialMessages, message(20)]} />);

    await waitFor(() => {
      expect(stream.scrollTop).toBe(1200);
    });
  });

  it('does not offer the crypto market in report confirmation', () => {
    const item: ChatMessageForUser = {
      ...message(1),
      actor: 'system',
      kind: 'confirmation_card',
      cardId: 'card-1',
    };
    const card: ConfirmationCard = {
      id: 'card-1',
      draftId: 'draft-1',
      title: '请确认是否创建完整报告',
      summaryLines: [],
      instrumentCode: '600519.SH',
      instrumentName: '贵州茅台',
      market: 'CN_A',
      dataSourceSummary: 'ready',
      actions: ['confirm', 'cancel'],
      status: 'active',
      createdAt: '2026-06-25T10:00:00.000Z',
    };

    render(<MessageStream items={[item]} confirmationCards={{ 'card-1': card }} />);

    const market = screen.getByRole('combobox', { name: '市场' });
    expect(within(market).queryByRole('option', { name: 'CRYPTO' })).not.toBeInTheDocument();
  });

  it('replaces a crypto confirmation with the supported-market notice', () => {
    const item: ChatMessageForUser = {
      ...message(1),
      actor: 'system',
      kind: 'confirmation_card',
      cardId: 'card-crypto',
    };
    const card: ConfirmationCard = {
      id: 'card-crypto',
      draftId: 'draft-crypto',
      title: '请确认是否创建完整报告',
      summaryLines: [],
      instrumentCode: 'BTC',
      instrumentName: 'Bitcoin',
      market: 'CRYPTO',
      dataSourceSummary: 'ready',
      actions: ['confirm', 'cancel'],
      status: 'active',
      createdAt: '2026-06-25T10:00:00.000Z',
    };

    render(<MessageStream items={[item]} confirmationCards={{ 'card-crypto': card }} />);

    expect(screen.getByText('当前版本仅支持 A股、港股和美股报告。')).toBeInTheDocument();
    expect(screen.queryByRole('combobox', { name: '市场' })).not.toBeInTheDocument();
    expect(screen.queryByText('Bitcoin')).not.toBeInTheDocument();
    expect(screen.queryByText('CRYPTO')).not.toBeInTheDocument();
  });
});
