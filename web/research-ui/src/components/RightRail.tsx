import type {
  ChannelStatusForUser,
  PdfExportForUser,
  ReportDetailForUser,
  ReportQueueSnapshotForUser,
  SavedReportForUser,
} from '../api/contracts';

function statusClass(value: string) {
  if (value === 'failed' || value === 'error') {
    return 'is-danger';
  }
  if (value === 'ready' || value === 'connected') {
    return 'is-success';
  }
  return 'is-info';
}

function formatDate(value?: string | null) {
  if (!value) {
    return '—';
  }
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

function inferPdf(detail: ReportDetailForUser): PdfExportForUser {
  const pdf = detail.assets.find((item) => item.kind === 'pdf');
  return {
    reportId: detail.report.id,
    state: pdf?.status === 'ready' ? 'ready' : pdf?.status === 'failed' ? 'failed' : 'not_requested',
    available: Boolean(pdf?.available),
    userMessage: pdf?.userMessage ?? null,
    updatedAt: pdf?.updatedAt ?? null,
  };
}

function TaskBlock({ queue }: { queue: ReportQueueSnapshotForUser }) {
  const rows = [
    ...(queue.runningTask ? [queue.runningTask] : []),
    ...queue.queuedTasks,
  ].filter((task) => task.status === 'running' || task.status === 'queued');

  return (
    <section className="ct-right-section" data-testid="right-rail-task-section">
      <h2>当前任务进度</h2>
      {rows.map((task) => {
        const progress = task.progress;
        const percent = Math.max(0, Math.min(100, Math.round(progress?.percent ?? 0)));
        return (
          <article key={task.taskId} className="ct-task-item">
            <div className="ct-task-head">
              <strong>{task.instrumentCode}</strong>
              <span>{task.statusLabel}</span>
            </div>
            <div className="ct-task-meta">
              <span>{progress?.stageLabel ?? '等待处理中'}</span>
              <span>{progress?.roleLabel ?? '—'}</span>
            </div>
            <div className="ct-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
              <div className="ct-progress-fill" style={{ width: `${percent}%` }} />
            </div>
            <p className="ct-task-action">{progress?.currentAction ?? '正在推进任务流程'}</p>
            {progress?.workerStatusLabels?.length ? (
              <ul className="ct-task-status-list">
                {progress.workerStatusLabels.map((item) => (
                  <li key={`${task.taskId}-${item}`}>{item}</li>
                ))}
              </ul>
            ) : null}
            <div className="ct-task-list-line">
              <span>已完成：{(progress?.completedRoleLabels ?? []).join('、') || '暂无'}</span>
            </div>
            <div className="ct-task-list-line">
              <span>待执行：{(progress?.waitingRoleLabels ?? []).join('、') || '暂无'}</span>
            </div>
          </article>
        );
      })}
      {rows.length === 0 ? <p className="ct-empty">暂无运行中或排队中的报告任务</p> : null}
    </section>
  );
}

function ReportBlock({ detail, onPrintReport }: { detail: ReportDetailForUser; onPrintReport?: () => void }) {
  const pdf = inferPdf(detail);

  return (
    <>
      <section className="ct-right-section" data-testid="right-rail-report-section">
        <h2>报告目录</h2>
        <ul className="ct-plain-list">
          <li>{detail.report.title}</li>
          <li>{detail.report.instrumentCode}</li>
          <li>{detail.report.market}</li>
        </ul>
      </section>
      <section className="ct-right-section" data-testid="right-rail-datasource-section">
        <h2>数据源状态</h2>
        <ul className="ct-plain-list">
          {detail.dataSourceEvents.map((item) => (
            <li key={`${item.displayName}-${item.occurredAt}`}>
              <strong>{item.displayName}</strong>
              <div>{item.userMessage}</div>
              <div className="ct-small">{item.impact}</div>
            </li>
          ))}
        </ul>
        {detail.dataSourceEvents.length === 0 ? <p className="ct-empty">本次报告未发现已配置来源异常</p> : null}
      </section>
      <section className="ct-right-section" data-testid="right-rail-chart-section">
        <h2>图表状态</h2>
        <div className="ct-kv">
          <span>汇总</span>
          <span className={statusClass(detail.chartEvidence.summary)}>{detail.chartEvidence.summary}</span>
        </div>
        <ul className="ct-plain-list">
          {detail.chartEvidence.items.map((item) => (
            <li key={item.id}>
              <strong>{item.title}</strong>
              <div className={statusClass(item.status)}>{item.status}</div>
              <div>{item.userMessage}</div>
              <div className="ct-small">{formatDate(item.capturedAt)}</div>
            </li>
          ))}
        </ul>
      </section>
      <section className="ct-right-section" data-testid="right-rail-pdf-section">
        <h2>导出状态</h2>
        <div className="ct-kv">
          <span>PDF</span>
          <span className={statusClass(pdf.state)}>{pdf.state}</span>
        </div>
        <div className="ct-small">{pdf.userMessage ?? '可在此查看导出可用性'}</div>
        <button type="button" className="ct-text-button" onClick={onPrintReport}>
          保存为 PDF
        </button>
      </section>
    </>
  );
}

function ChatSummaryBlock({
  channel,
  latestReport,
}: {
  channel: ChannelStatusForUser | null;
  latestReport: SavedReportForUser | null;
}) {
  return (
    <>
      <section className="ct-right-section">
        <h2>微信通知</h2>
        <div className="ct-kv">
          <span>{channel?.displayName ?? '微信 ClawBot'}</span>
          <span className={statusClass(channel?.state ?? 'unknown')}>{channel?.state ?? 'unknown'}</span>
        </div>
        <div className="ct-small">
          {channel?.lastErrorMessage ??
            (channel?.state === 'connected' ? '可接收文字提醒' : '未连接时仅在设备界面查看消息')}
        </div>
      </section>
      <section className="ct-right-section">
        <h2>最近完成报告</h2>
        {latestReport ? (
          <div className="ct-small">
            <div>{latestReport.instrumentCode}</div>
            <div>{latestReport.summarySnippet}</div>
          </div>
        ) : (
          <p className="ct-empty">暂无完成报告</p>
        )}
      </section>
    </>
  );
}

export function RightRail({
  queue,
  detail,
  channel,
  latestReport,
  onPrintReport,
}: {
  queue: ReportQueueSnapshotForUser;
  detail: ReportDetailForUser | null;
  channel: ChannelStatusForUser | null;
  latestReport: SavedReportForUser | null;
  onPrintReport?: () => void;
}) {
  return (
    <aside className="ct-panel ct-right" data-testid="right-rail">
      <div className="ct-panel-title">{detail ? '报告详情' : '任务概览'}</div>
      {detail ? <ReportBlock detail={detail} onPrintReport={onPrintReport} /> : <TaskBlock queue={queue} />}
      {!detail ? <ChatSummaryBlock channel={channel} latestReport={latestReport} /> : null}
    </aside>
  );
}
