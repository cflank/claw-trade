import type { ChatMessageForUser, ConfirmationCard } from '../api/contracts';
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
  price_alert: '价格提醒',
  file_send_failed: '发送失败',
};

function formatDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

function summaryLabel(value: ConfirmationCard['dataSourceSummary']) {
  if (value === 'ready') {
    return '数据源就绪';
  }
  if (value === 'partial') {
    return '数据源部分可用';
  }
  return '数据源待确认';
}

function MessageBody({
  item,
  card,
  cardSubmittingId,
  onConfirmCard,
  onCancelCard,
  onOpenReport,
}: {
  item: ChatMessageForUser;
  card?: ConfirmationCard;
  cardSubmittingId?: string | null;
  onConfirmCard?: (card: ConfirmationCard) => Promise<void> | void;
  onCancelCard?: (card: ConfirmationCard) => Promise<void> | void;
  onOpenReport?: (reportId: string) => void;
}) {
  if (item.kind === 'confirmation_card' && card) {
    const isBusy = cardSubmittingId === card.id;
    return (
      <section className="ct-confirmation-card" data-testid={`confirmation-card-${card.id}`}>
        <h3>{card.title}</h3>
        <ul>
          {card.summaryLines.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
        <p className="ct-small">{summaryLabel(card.dataSourceSummary)}</p>
        <div className="ct-card-actions">
          <button
            type="button"
            disabled={isBusy || card.status !== 'active'}
            onClick={() => onConfirmCard?.(card)}
          >
            {isBusy ? '处理中' : '确认'}
          </button>
          <button
            type="button"
            className="is-ghost"
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
  return <p>{item.text}</p>;
}

export function MessageStream({
  items,
  confirmationCards,
  cardSubmittingId,
  onConfirmCard,
  onCancelCard,
  onOpenReport,
}: {
  items: ChatMessageForUser[];
  confirmationCards?: Record<string, ConfirmationCard>;
  cardSubmittingId?: string | null;
  onConfirmCard?: (card: ConfirmationCard) => Promise<void> | void;
  onCancelCard?: (card: ConfirmationCard) => Promise<void> | void;
  onOpenReport?: (reportId: string) => void;
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
        <article key={item.messageId} className={`ct-message ct-message-${item.actor}`}>
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
            onOpenReport={onOpenReport}
          />
        </article>
      ))}
    </section>
  );
}
