import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { sendWorkerChat } from '../api/client';
import type { WorkerChatWorkerForUser } from '../api/contracts';
import { Composer } from '../components/Composer';
import { WorkerSelector } from '../components/WorkerSelector';
import { useState } from 'react';

const WORKERS: WorkerChatWorkerForUser[] = [
  { workerId: 'portfolio_manager', displayName: '组合经理', default: true, aliases: ['组合经理', 'PM'] },
  { workerId: 'research_manager', displayName: '研究经理', default: false, aliases: ['研究经理'] },
  { workerId: 'market_analyst', displayName: '市场分析师', default: false, aliases: ['市场分析师', '市场'] },
  { workerId: 'fundamental_analyst', displayName: '基本面分析师', default: false, aliases: ['基本面分析师', '基本面'] },
  { workerId: 'news_analyst', displayName: '新闻分析师', default: false, aliases: ['新闻分析师', '新闻'] },
  { workerId: 'social_analyst', displayName: '情绪分析师', default: false, aliases: ['情绪分析师', '情绪'] },
  { workerId: 'risk_moderator', displayName: '风险经理', default: false, aliases: ['风险经理', '风险'] },
];

function json(payload: unknown) {
  return new Response(JSON.stringify(payload), {
    status: 200,
    headers: { 'Content-Type': 'application/json' },
  });
}

function WorkerChatHarness({
  onWorkerChange,
}: {
  onWorkerChange?: (workerId: string) => void;
}) {
  const [selectedWorkerId, setSelectedWorkerId] = useState('portfolio_manager');

  function handleWorkerChange(workerId: string) {
    setSelectedWorkerId(workerId);
    onWorkerChange?.(workerId);
  }

  return (
    <Composer
      onSend={(text) =>
        sendWorkerChat({
          requestId: 'req-worker-chat',
          mode: 'generic_worker_chat',
          workerId: selectedWorkerId,
          text,
          conversationId: 'main',
        }).then(() => undefined)
      }
      placeholder="输入消息"
      buttonLabel="发送"
      workerChatEnabled
      workers={WORKERS}
      selectedWorkerId={selectedWorkerId}
      onWorkerChange={handleWorkerChange}
    />
  );
}

describe('worker chat frontend', () => {
  const originalFetch = globalThis.fetch;

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  it('uses the default worker as 组合经理 in the picker', () => {
    render(<WorkerSelector workers={WORKERS} onWorkerChange={vi.fn()} />);

    expect(screen.getByRole('button', { name: /组合经理/ })).toBeInTheDocument();
  });

  it('opens the worker list from @ input and shows exactly seven workers', () => {
    render(
      <Composer
        onSend={async () => undefined}
        placeholder="输入消息"
        buttonLabel="发送"
        workerChatEnabled
        workers={WORKERS}
        selectedWorkerId="portfolio_manager"
        onWorkerChange={vi.fn()}
      />,
    );

    fireEvent.change(screen.getByLabelText('输入消息'), { target: { value: '@' } });

    expect(screen.getAllByRole('option')).toHaveLength(7);
  });

  it('sends worker chat with the worker selected from the list', async () => {
    const bodies: Array<Record<string, unknown>> = [];
    const onWorkerChange = vi.fn();
    globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return json({
        kind: 'worker_chat_reply',
        workerDisplayName: '市场分析师',
        text: '市场结构偏强。',
        mode: 'generic_worker_chat',
      });
    }) as typeof fetch;

    render(<WorkerChatHarness onWorkerChange={onWorkerChange} />);

    fireEvent.change(screen.getByLabelText('输入消息'), { target: { value: '@' } });
    fireEvent.click(screen.getByRole('option', { name: '市场分析师' }));
    expect(onWorkerChange).toHaveBeenCalledWith('market_analyst');

    fireEvent.change(screen.getByLabelText('输入消息'), { target: { value: '看一下市场结构' } });
    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(bodies[0]?.workerId).toBe('market_analyst'));
  });

  it('keeps the current selected worker when @市场分析师 is typed without choosing from the list', async () => {
    const bodies: Array<Record<string, unknown>> = [];
    const onWorkerChange = vi.fn();
    globalThis.fetch = (async (_input: RequestInfo | URL, init?: RequestInit) => {
      bodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>);
      return json({
        kind: 'worker_chat_reply',
        workerDisplayName: '组合经理',
        text: '继续按组合视角评估。',
        mode: 'generic_worker_chat',
      });
    }) as typeof fetch;

    render(<WorkerChatHarness onWorkerChange={onWorkerChange} />);

    fireEvent.change(screen.getByLabelText('输入消息'), { target: { value: '@市场分析师 看一下市场结构' } });
    expect(onWorkerChange).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole('button', { name: '发送' }));

    await waitFor(() => expect(bodies[0]?.workerId).toBe('portfolio_manager'));
  });
});
