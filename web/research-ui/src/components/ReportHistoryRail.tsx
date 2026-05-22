import { useMemo, useState } from 'react';
import type { SavedReportForUser } from '../api/contracts';

function formatDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

export function ReportHistoryRail({
  items,
  activeReportId,
  onOpenReport,
}: {
  items: SavedReportForUser[];
  activeReportId?: string | null;
  onOpenReport: (report: SavedReportForUser) => void;
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
    <aside className="ct-panel ct-left" data-testid="report-history-rail">
      <div className="ct-panel-title">正式报告历史</div>
      <div className="ct-rail-search">
        <input
          aria-label="搜索报告"
          placeholder="按标的或标题搜索"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
      </div>
      <ul className="ct-history-list">
        {visible.map((report) => (
          <li key={report.id} className="ct-history-item">
            <button
              type="button"
              className={report.id === activeReportId ? 'is-active' : ''}
              onClick={() => onOpenReport(report)}
            >
              <div className="ct-history-line-1">{report.instrumentCode}</div>
              <div className="ct-history-line-2">{report.title}</div>
              <div className="ct-history-line-3">{formatDate(report.generatedAt)}</div>
              <div className="ct-history-line-4">{report.summarySnippet}</div>
            </button>
          </li>
        ))}
      </ul>
      {visible.length === 0 ? <p className="ct-empty">暂无已保存报告</p> : null}
    </aside>
  );
}
