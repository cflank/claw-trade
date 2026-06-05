import { useMemo, useState } from 'react';
import type { SavedReportForUser, SelectionReportForUser } from '../api/contracts';
import { EmptyHistoryRail } from './EmptyStates';

function formatDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

export function HistoryRail({
  items,
  selectionItems = [],
  activeReportId,
  activeSelectionReportId,
  onOpenReport,
  onDeleteReport,
  onOpenSelectionReport,
}: {
  items: SavedReportForUser[];
  selectionItems?: SelectionReportForUser[];
  activeReportId?: string | null;
  activeSelectionReportId?: string | null;
  onOpenReport: (report: SavedReportForUser) => void;
  onDeleteReport: (report: SavedReportForUser) => void;
  onOpenSelectionReport?: (report: SelectionReportForUser) => void;
}) {
  const [query, setQuery] = useState('');
  const normalized = query.trim().toLowerCase();
  const visible = useMemo(() => {
    if (!normalized) {
      return items;
    }
    return items.filter((item) => {
      const source = `${item.instrumentCode} ${item.instrumentName ?? ''} ${item.title}`.toLowerCase();
      return source.includes(normalized);
    });
  }, [items, normalized]);

  return (
    <aside className="ct-rail ct-history-rail" data-testid="history-rail">
      {selectionItems.length ? (
        <>
          <div className="ct-panel-title">选股报告</div>
          <ul className="ct-history-list">
            {selectionItems.map((item) => (
              <li key={item.id} className="ct-history-item">
                <button
                  type="button"
                  className={`ct-history-open${item.id === activeSelectionReportId ? ' is-active' : ''}`}
                  onClick={() => onOpenSelectionReport?.(item)}
                >
                  <div className="ct-history-code">/select</div>
                  <div className="ct-history-title">{item.title}</div>
                  <div className="ct-history-time">{formatDate(item.generatedAt)}</div>
                  <div className="ct-history-summary">{item.summarySnippet}</div>
                </button>
              </li>
            ))}
          </ul>
        </>
      ) : null}
      <div className="ct-panel-title">正式报告历史</div>
      <div className="ct-history-search">
        <input
          aria-label="搜索报告"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          placeholder="按标的或标题搜索"
        />
      </div>
      {visible.length ? (
        <ul className="ct-history-list">
          {visible.map((item) => (
            <li key={item.id} className="ct-history-item">
              <div className="ct-history-item-row">
                <button
                  type="button"
                  className={`ct-history-open${item.id === activeReportId ? ' is-active' : ''}`}
                  onClick={() => onOpenReport(item)}
                >
                  <div className="ct-history-code">{item.instrumentCode}</div>
                  <div className="ct-history-title">{item.title}</div>
                  <div className="ct-history-time">{formatDate(item.generatedAt)}</div>
                  <div className="ct-history-summary">{item.summarySnippet}</div>
                </button>
                <button
                  type="button"
                  className="ct-history-delete"
                  aria-label={`删除 ${item.title}`}
                  title="从历史中移除"
                  onClick={() => onDeleteReport(item)}
                >
                  删除
                </button>
              </div>
            </li>
          ))}
        </ul>
      ) : (
        <EmptyHistoryRail />
      )}
    </aside>
  );
}
