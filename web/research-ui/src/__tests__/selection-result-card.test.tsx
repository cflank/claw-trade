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
      readerReportMarkdown: '# 选股结果报告\n\n## 候选事实表\n\n| 排名 | 股票代码 | 股票名称 | 总分 |\n| --- | --- | --- | ---: |\n| 1 | 600519.SH | 贵州茅台 | 91 |',
    },
    createdAt: '2026-05-28T10:00:00.000Z',
  };
}

describe('selection result card', () => {
  it('renders a brief selection card while hiding protocol and internal scoring lines', () => {
    render(
      <MessageStream
        items={[
          selectionMessage(`\`/select\` 已完成，本轮仅进入等待确认，不会自动启动 \`/report\`。

简报：
- 进入 \`/report\`：600519.SH 贵州茅台
- 观察：000001.SZ 平安银行
- 放弃：300750.SZ 宁德时代

进入 \`/report\`：
- 600519.SH 贵州茅台：经营质量与现金流稳定，值得进入深度报告验证。

观察：
- 000001.SZ 平安银行：还需后续财报与景气数据确认。

放弃：
- 300750.SZ 宁德时代：当前证据链分歧较大且不够完整。

total score 91 hidden
subscore 质量 30 hidden
strategy source/variant cn_a.value/v1 hidden
hit fields ROE, revenue_growth hidden
metric values ROE=31%, revenue_growth=12% hidden
risk/data-gap penalties 2 hidden
tie-break liquidity hidden
config/weight version cn_a.selection_weights.v1 hidden

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
    expect(screen.getByText('不会自动启动 /report。需要你确认候选标的后才会进入正式报告。')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: '查看完整选股报告（含策略分析）' })).toBeInTheDocument();

    const bodyText = document.body.textContent ?? '';
    expect(bodyText).not.toContain('total score');
    expect(bodyText).not.toContain('subscore');
    expect(bodyText).not.toContain('strategy source');
    expect(bodyText).not.toContain('hit fields');
    expect(bodyText).not.toContain('metric values');
    expect(bodyText).not.toContain('risk/data-gap');
    expect(bodyText).not.toContain('tie-break');
    expect(bodyText).not.toContain('config/weight');
    expect(bodyText).not.toContain('cn_a.selection');
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
