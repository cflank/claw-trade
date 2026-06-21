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
  forwardingReportId = null,
  forwardStatusByReportId = {},
  onOpenReport,
  onForwardReport,
  onDeleteReport,
  onDeleteReports,
  onOpenSelectionReport,
}: {
  items: SavedReportForUser[];
  selectionItems?: SelectionReportForUser[];
  activeReportId?: string | null;
  activeSelectionReportId?: string | null;
  forwardingReportId?: string | null;
  forwardStatusByReportId?: Record<string, { kind: 'success' | 'error'; message: string } | undefined>;
  onOpenReport: (report: SavedReportForUser) => void;
  onForwardReport?: (report: SavedReportForUser) => void;
  onDeleteReport: (report: SavedReportForUser) => void;
  onDeleteReports: (reports: SavedReportForUser[], scope: 'search' | 'all') => void;
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
        <button
          type="button"
          className="ct-history-bulk-delete"
          disabled={!visible.length}
          onClick={() => onDeleteReports(visible, normalized ? 'search' : 'all')}
        >
          {normalized ? '删除搜索结果' : '清空'}
        </button>
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
                  {forwardStatusByReportId[item.id] ? (
                    <div className={`ct-history-forward-status is-${forwardStatusByReportId[item.id]?.kind}`}>
                      {forwardStatusByReportId[item.id]?.message}
                    </div>
                  ) : null}
                </button>
                <div className="ct-history-actions">
                  {item.canForwardToChannel && onForwardReport ? (
                    <button
                      type="button"
                      className="ct-history-forward"
                      aria-label={`转发 ${item.title}`}
                      disabled={forwardingReportId === item.id}
                      onClick={() => onForwardReport(item)}
                    >
                      {forwardingReportId === item.id ? '发送中' : '转发'}
                    </button>
                  ) : null}
                  <button
                    type="button"
                    className="ct-history-delete"
                    aria-label={`删除 ${item.title}`}
                    title="永久删除报告文件、图表与运行证据"
                    onClick={() => onDeleteReport(item)}
                  >
                    删除
                  </button>
                </div>
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
