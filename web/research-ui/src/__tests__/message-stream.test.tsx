import { render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import type { ChatMessageForUser } from '../api/contracts';
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
});
