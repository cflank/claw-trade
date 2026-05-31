import { describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { MessageStream } from '../components/MessageStream';
import type { ChatMessageForUser } from '../api/contracts';

function selectionMessage(text: string): ChatMessageForUser {
  return {
    messageId: 'selection-message-1',
    contextKind: 'normal_chat',
    actor: 'system',
    kind: 'selection_result',
    text,
    selection: {
      code: 'completed',
      workflowRunId: 'select-workflow-1',
      evidencePath: 'runs/selection/workflows/select-workflow-1/selection-workflow-evidence.json',
    },
    createdAt: '2026-05-28T10:00:00.000Z',
  };
}

describe('selection result card', () => {
  it('renders selection classes and candidate-pack labels while hiding protocol lines', () => {
    render(
      <MessageStream
        items={[
          selectionMessage(`\`/select\` 已完成，本轮仅进入等待确认，不会自动启动 \`/report\`。

进入 \`/report\`：
- 600519.SH 贵州茅台：total score 91；subscore 质量 30；strategy source/variant cn_a.value/v1；hit fields ROE, revenue_growth；metric values ROE=31%, revenue_growth=12%；risk/data-gap penalties 2；tie-break liquidity；config/weight version cn_a.selection_weights.v1

观察：
- 000001.SZ 平安银行：total score 75；subscore 估值 20

放弃：
- 300750.SZ 宁德时代：risk/data-gap penalties 8

provider attempt: hidden
Mongo protocol: hidden
OpenViking manifest refs hash lineage receipt hidden
local://selection/run/l1_uri hidden
raw payload: hidden`),
        ]}
      />,
    );

    expect(screen.getByText((content) => content.includes('选股结果'))).toBeInTheDocument();
    expect(screen.getAllByText((content) => content.includes('进入'))[0]).toBeInTheDocument();
    expect(screen.getByText('观察：')).toBeInTheDocument();
    expect(screen.getByText('放弃：')).toBeInTheDocument();
    expect(screen.getAllByText(/600519\.SH 贵州茅台/)[0]).toBeInTheDocument();
    expect(screen.getByText(/total score 91/)).toBeInTheDocument();
    expect(screen.getByText(/subscore 质量 30/)).toBeInTheDocument();
    expect(screen.getByText(/strategy source\/variant cn_a\.value\/v1/)).toBeInTheDocument();
    expect(screen.getByText(/hit fields ROE, revenue_growth/)).toBeInTheDocument();
    expect(screen.getByText(/metric values ROE=31%, revenue_growth=12%/)).toBeInTheDocument();
    expect(screen.getAllByText(/risk\/data-gap penalties/)).toHaveLength(2);
    expect(screen.getByText(/tie-break liquidity/)).toBeInTheDocument();
    expect(screen.getByText(/config\/weight version cn_a\.selection_weights\.v1/)).toBeInTheDocument();
    expect(screen.getByText('不会自动启动 /report。需要你确认候选标的后才会进入正式报告。')).toBeInTheDocument();

    const bodyText = document.body.textContent ?? '';
    expect(bodyText).not.toContain('provider attempt');
    expect(bodyText).not.toContain('Mongo protocol');
    expect(bodyText).not.toContain('OpenViking');
    expect(bodyText).not.toContain('manifest');
    expect(bodyText).not.toContain('refs');
    expect(bodyText).not.toContain('hash');
    expect(bodyText).not.toContain('lineage');
    expect(bodyText).not.toContain('receipt');
    expect(bodyText).not.toContain('local://selection');
    expect(bodyText).not.toContain('raw payload');
  });

  it('calls confirmation only from an explicit candidate button click', () => {
    const onConfirmSelectionCandidate = vi.fn();
    const item = selectionMessage(`进入 \`/report\`：
- 600519.SH 贵州茅台：确认进入报告

观察：
- 无

放弃：
- 无`);

    render(<MessageStream items={[item]} onConfirmSelectionCandidate={onConfirmSelectionCandidate} />);

    expect(onConfirmSelectionCandidate).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '确认进入 /report：600519.SH 贵州茅台' }));

    expect(onConfirmSelectionCandidate).toHaveBeenCalledWith(item, '600519.SH');
  });
});
