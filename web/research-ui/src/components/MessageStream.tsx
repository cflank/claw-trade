import type { ChatMessageForUser, ConfirmationCard } from '../api/contracts';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { EmptyMessageState } from './EmptyStates';
import { ReportSummaryCard } from './ReportSummaryCard';

const ACTOR_LABEL: Record<ChatMessageForUser['actor'], string> = {
  user: '你',
  assistant: '研究助理',
  system: '系统',
};

const KIND_LABEL: Record<ChatMessageForUser['kind'], string> = {
  plain: '普通消息',
  confirmation_card: '确认提示',
  task_progress: '任务进展',
  report_completed: '报告完成',
  report_failed: '报告失败',
  selection_result: '选股结果',
  selection_unavailable: '选股不可用',
  price_alert: '价格提醒',
  file_send_failed: '发送失败',
};

const INTERNAL_RUNTIME_NAME_PATTERNS = [
  new RegExp(`\\b${'open'}${'viking'}\\b`, 'i'),
  new RegExp(`\\b${'open'}${'claw'}\\b`, 'i'),
];

const SELECTION_PROTOCOL_LINE_PATTERNS = [
  ...INTERNAL_RUNTIME_NAME_PATTERNS,
  /\bmongo\b/i,
  /\bprovider\b/i,
  /\braw payload\b/i,
  /\brun[_-]?id\b/i,
  /\bworkflowrunid\b/i,
  /\bevidencepath\b/i,
  /\bmaterial[_-]?id\b/i,
  /\bl1[_-]?(uri|prefix)?\b/i,
  /\bl2[_-]?(uri|prefix)?\b/i,
  /\buri\b/i,
  /\brefs?\b/i,
  /\bhash\b/i,
  /\bmanifest\b/i,
  /\blineage\b/i,
  /\breceipt\b/i,
  /local:\/\/|\/runs\//i,
  /\btotal\s+score\b/i,
  /\bsubscore\b/i,
  /\bstrategy\s+(source|variant)\b/i,
  /\bhit\s+fields\b/i,
  /\bmetric\s+values\b/i,
  /\brisk\/data-gap\b/i,
  /\btie-break\b/i,
  /\bconfig\/weight\b/i,
  /cn_a\.selection/i,
  /策略配置版本|权重版本|策略变体|排序\s*tie-break\s*字段/i,
  /\d+\s*策略命中|策略命中|总分\s*\d|\bRPS\d*\b|\bMa\d+\b/i,
];

function selectionDisplayText(text: string) {
  const safeLines = text
    .split(/\r?\n/)
    .map((line) => line.trimEnd())
    .filter((line) => isSelectionCandidateLine(line) || !SELECTION_PROTOCOL_LINE_PATTERNS.some((pattern) => pattern.test(line)));
  const safeText = safeLines.join('\n').trim();
  return safeText || '`/select` 结果暂不可展示，请稍后重试。';
}

function isSelectionCandidateLine(line: string) {
  const cleaned = line.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, '').trim();
  return /^[A-Z0-9]{2,8}\.(?:SH|SZ|BJ|HK|US)\b/.test(cleaned);
}

function parseConfirmableSelectionTickers(text: string) {
  const tickers: Array<{ ticker: string; label: string }> = [];
  let inEnterReport = false;
  for (const rawLine of text.split(/\r?\n/)) {
    const line = rawLine.trim();
    const heading = line.replace(/^[#>*\-\s]+/, '').replace(/[：:]/g, '').replace(/\s+/g, '').toLowerCase();
    if (heading === '进入`/report`' || heading === '进入/report' || heading === '进入报告') {
      inEnterReport = true;
      continue;
    }
    if (heading === '观察' || heading === '放弃') {
      inEnterReport = false;
      continue;
    }
    if (!inEnterReport) {
      continue;
    }
    const cleaned = line.replace(/^\s*(?:[-*•]|\d+[.)])\s*/, '').trim();
    const match = cleaned.match(/^([A-Za-z0-9./_-]+)\s+([^：:|｜]+)?/);
    if (!match || match[1] === '无') {
      continue;
    }
    const ticker = match[1].toUpperCase();
    tickers.push({ ticker, label: match[2]?.trim() ? `${ticker} ${match[2].trim()}` : ticker });
  }
  return tickers;
}

function SelectionResultCard({
  item,
  selectionSubmittingKey,
  onConfirmSelectionCandidate,
  onOpenSelectionReport,
}: {
  item: ChatMessageForUser;
  selectionSubmittingKey?: string | null;
  onConfirmSelectionCandidate?: (item: ChatMessageForUser, ticker: string) => Promise<void> | void;
  onOpenSelectionReport?: (item: ChatMessageForUser) => void;
}) {
  const safeText = selectionDisplayText(item.text);
  const workflowRunId = item.selection?.workflowRunId?.trim();
  const hasReport = Boolean(item.selection?.readerReportMarkdown?.trim());
  const confirmable = item.kind === 'selection_result' && workflowRunId ? parseConfirmableSelectionTickers(safeText) : [];
  return (
    <section className={`ct-selection-card ${item.kind === 'selection_unavailable' ? 'is-unavailable' : ''}`}>
      <div className="ct-selection-markdown">
        <ReactMarkdown remarkPlugins={[remarkGfm]}>{safeText}</ReactMarkdown>
      </div>
      {item.kind === 'selection_result' ? (
        <p className="ct-selection-note">不会自动启动 /report。需要你确认候选标的后才会进入正式报告。</p>
      ) : null}
      {hasReport ? (
        <div className="ct-selection-actions ct-button-row">
          <button type="button" className="ct-button ct-button-secondary" onClick={() => onOpenSelectionReport?.(item)}>
            查看完整选股报告
          </button>
        </div>
      ) : null}
      {confirmable.length ? (
        <div className="ct-selection-actions ct-button-row">
          {confirmable.map((candidate) => {
            const key = `${workflowRunId}:${candidate.ticker}`;
            const busy = selectionSubmittingKey === key;
            return (
              <button
                type="button"
                className="ct-button"
                key={key}
                disabled={busy}
                onClick={() => onConfirmSelectionCandidate?.(item, candidate.ticker)}
              >
                {busy ? '确认中' : `确认进入 /report：${candidate.label}`}
              </button>
            );
          })}
        </div>
      ) : null}
    </section>
  );
}

function formatDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

function MessageBody({
  item,
  card,
  cardSubmittingId,
  onConfirmCard,
  onCancelCard,
  onCardSymbolChange,
  onCardSymbolRefresh,
  onCardMarketChange,
  onOpenReport,
  selectionSubmittingKey,
  onConfirmSelectionCandidate,
  onOpenSelectionReport,
}: {
  item: ChatMessageForUser;
  card?: ConfirmationCard;
  cardSubmittingId?: string | null;
  selectionSubmittingKey?: string | null;
  onConfirmCard?: (card: ConfirmationCard) => Promise<void> | void;
  onCancelCard?: (card: ConfirmationCard) => Promise<void> | void;
  onCardSymbolChange?: (card: ConfirmationCard, value: string) => void;
  onCardSymbolRefresh?: (card: ConfirmationCard) => Promise<void> | void;
  onCardMarketChange?: (card: ConfirmationCard, market: 'CN_A' | 'US' | 'HK' | 'CRYPTO') => Promise<void> | void;
  onOpenReport?: (reportId: string) => void;
  onConfirmSelectionCandidate?: (item: ChatMessageForUser, ticker: string) => Promise<void> | void;
  onOpenSelectionReport?: (item: ChatMessageForUser) => void;
}) {
  if (item.kind === 'confirmation_card' && card) {
    const isBusy = cardSubmittingId === card.id;
    const canConfirm = card.status === 'active' && card.validationState !== 'mismatch';
    return (
      <section className="ct-task-confirm ct-confirmation-card" data-testid={`confirmation-card-${card.id}`}>
        <h3 className="ct-task-confirm-title">{card.title}</h3>
        <div className="ct-task-fields">
          <div className="ct-task-field">
            <span className="ct-task-field-label">标的</span>
            <span className="ct-task-field-value">{card.instrumentCode ?? '-'}</span>
          </div>
          <div className="ct-task-field">
            <span className="ct-task-field-label">名称</span>
            <span className="ct-task-field-value">{card.instrumentName ?? '-'}</span>
          </div>
          <div className="ct-task-field">
            <span className="ct-task-field-label">市场</span>
            <span className="ct-task-field-value">
              <select
                className="ct-task-market-select"
                aria-label="市场"
                value={card.market ?? 'CN_A'}
                onChange={(event) =>
                  onCardMarketChange?.(card, event.target.value as 'CN_A' | 'US' | 'HK' | 'CRYPTO')
                }
                disabled={isBusy}
              >
                <option value="CN_A">CN_A</option>
                <option value="US">US</option>
                <option value="HK">HK</option>
                <option value="CRYPTO">CRYPTO</option>
              </select>
            </span>
          </div>
        </div>
        <div className="ct-task-edit-row ct-card-edit-row">
          <label className="ct-field">
            <span>修改标的</span>
            <input
              value={card.instrumentCode ?? ''}
              onChange={(event) => {
                const next = event.target.value.toUpperCase();
                onCardSymbolChange?.(card, next);
              }}
            />
          </label>
          <button
            type="button"
            className="ct-button ct-button-secondary"
            disabled={isBusy || !String(card.instrumentCode ?? '').trim()}
            onClick={() => onCardSymbolRefresh?.(card)}
          >
            更新标的
          </button>
        </div>
        {card.validationMessage ? <p className="ct-inline-alert is-error">{card.validationMessage}</p> : null}
        <div className="ct-task-confirm-actions ct-card-actions ct-button-row">
          <button
            type="button"
            className="ct-button"
            disabled={isBusy || !canConfirm}
            onClick={() => onConfirmCard?.(card)}
          >
            {isBusy ? '处理中' : '确认'}
          </button>
          <button
            type="button"
            className="ct-button ct-button-secondary"
            disabled={isBusy || card.status !== 'active'}
            onClick={() => onCancelCard?.(card)}
          >
            取消
          </button>
        </div>
      </section>
    );
  }

  if (item.kind === 'report_completed' && item.reportId) {
    return <ReportSummaryCard summary={item.text} reportId={item.reportId} onOpenReport={onOpenReport} />;
  }
  if (item.kind === 'selection_result' || item.kind === 'selection_unavailable') {
    return (
      <SelectionResultCard
        item={item}
        selectionSubmittingKey={selectionSubmittingKey}
        onConfirmSelectionCandidate={onConfirmSelectionCandidate}
        onOpenSelectionReport={onOpenSelectionReport}
      />
    );
  }
  return <p>{item.text}</p>;
}

export function MessageStream({
  items,
  confirmationCards,
  cardSubmittingId,
  onConfirmCard,
  onCancelCard,
  onCardSymbolChange,
  onCardSymbolRefresh,
  onCardMarketChange,
  onOpenReport,
  selectionSubmittingKey,
  onConfirmSelectionCandidate,
  onOpenSelectionReport,
}: {
  items: ChatMessageForUser[];
  confirmationCards?: Record<string, ConfirmationCard>;
  cardSubmittingId?: string | null;
  selectionSubmittingKey?: string | null;
  onConfirmCard?: (card: ConfirmationCard) => Promise<void> | void;
  onCancelCard?: (card: ConfirmationCard) => Promise<void> | void;
  onCardSymbolChange?: (card: ConfirmationCard, value: string) => void;
  onCardSymbolRefresh?: (card: ConfirmationCard) => Promise<void> | void;
  onCardMarketChange?: (card: ConfirmationCard, market: 'CN_A' | 'US' | 'HK' | 'CRYPTO') => Promise<void> | void;
  onOpenReport?: (reportId: string) => void;
  onConfirmSelectionCandidate?: (item: ChatMessageForUser, ticker: string) => Promise<void> | void;
  onOpenSelectionReport?: (item: ChatMessageForUser) => void;
}) {
  if (!items.length) {
    return (
      <section className="ct-message-stream" data-testid="message-stream">
        <EmptyMessageState />
      </section>
    );
  }

  return (
    <section className="ct-message-stream" data-testid="message-stream">
      {items.map((item) => (
        <article
          key={item.messageId}
          className={`ct-message ct-message-${item.actor}${item.kind === 'confirmation_card' ? ' ct-message-confirmation' : ''}`}
        >
          <header className="ct-message-head">
            <span>
              {ACTOR_LABEL[item.actor]} · {KIND_LABEL[item.kind]}
            </span>
            <time>{formatDate(item.createdAt)}</time>
          </header>
          <MessageBody
            item={item}
            card={item.cardId ? confirmationCards?.[item.cardId] : undefined}
            cardSubmittingId={cardSubmittingId}
            onConfirmCard={onConfirmCard}
            onCancelCard={onCancelCard}
            onCardSymbolChange={onCardSymbolChange}
            onCardSymbolRefresh={onCardSymbolRefresh}
            onCardMarketChange={onCardMarketChange}
            onOpenReport={onOpenReport}
            selectionSubmittingKey={selectionSubmittingKey}
            onConfirmSelectionCandidate={onConfirmSelectionCandidate}
            onOpenSelectionReport={onOpenSelectionReport}
          />
        </article>
      ))}
    </section>
  );
}
