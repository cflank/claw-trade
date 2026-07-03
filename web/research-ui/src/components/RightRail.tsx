import type {
  ChannelStatusForUser,
  PriceAlertForUser,
  PdfExportForUser,
  ReportDetailForUser,
  ReportQueueSnapshotForUser,
  ReportTaskForUser,
  SavedReportForUser,
  ScheduledReportForUser,
  SelectionProgressForUser,
} from '../api/contracts';

type RawMaintenanceMarket = 'CN_A' | 'CRYPTO';

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

function frequencyLabel(item: ScheduledReportForUser) {
  if (item.frequency === 'weekly') {
    return `每周 ${item.weekday ?? '—'} ${item.timeOfDay}`;
  }
  return `每天 ${item.timeOfDay}`;
}

function conditionLabel(alert: PriceAlertForUser) {
  const operator = {
    above: '高于',
    below: '低于',
    up_by: '上涨超过',
    down_by: '下跌超过',
  }[alert.condition.operator];
  const suffix = alert.condition.type === 'percent_change' ? '%' : '';
  return `${operator} ${alert.condition.value}${suffix}`;
}

function quoteLabel(alert: PriceAlertForUser) {
  const price = alert.lastQuote?.currentPrice;
  if (price === undefined || price === null || price === '') {
    return '暂无报价';
  }
  return `${price}`;
}

function scanResultLabel(alert: PriceAlertForUser) {
  if (alert.state === 'closed') {
    return alert.triggeredAt ? '已触发' : '已关闭';
  }
  if (alert.state === 'paused' || alert.state === 'closed' || alert.state === 'deleted') {
    return `已跳过：${alert.state}`;
  }
  return alert.lastScanRunId ? '已扫描' : '等待扫描';
}

function notificationLabel(alert: PriceAlertForUser) {
  if (alert.lastErrorMessage) {
    return alert.lastErrorMessage;
  }
  const result = alert.lastNotificationResult ?? {};
  const channel = String(result.channel ?? '');
  const fallbackFrom = String(result.fallback_from ?? '');
  if (alert.state === 'closed') {
    if (fallbackFrom === 'wechat_clawbot') {
      return '通知：微信发送失败，已在页面显示';
    }
    if (channel === 'wechat_clawbot') {
      return '通知：已发送微信';
    }
    if (channel === 'in_app') {
      return '通知：已触发，已在页面显示';
    }
    return '通知：已触发';
  }
  return `通知：${alert.notificationDedupeKey ?? '暂无结果'}`;
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

function channelStateLabel(state?: string | null) {
  switch (state) {
    case 'connected':
      return '已连接';
    case 'connecting':
      return '连接中';
    case 'reconnecting':
      return '重连中';
    case 'disconnected':
      return '待连接';
    case 'error':
      return '不可用';
    default:
      return '检查中';
  }
}

function selectionProgressTitle(progress: SelectionProgressForUser) {
  if (progress.stageLabel.includes('原始行情') || progress.statusLabel.includes('原始行情')) {
    return '原始行情补数据';
  }
  if (progress.kind === 'data_refresh' || progress.statusLabel.includes('补数据') || progress.stageLabel.includes('数据')) {
    return '选股数据刷新';
  }
  return '选股任务进度';
}

function failedRawMarkets(progress: SelectionProgressForUser): RawMaintenanceMarket[] {
  if (progress.status !== 'failed' || selectionProgressTitle(progress) !== '原始行情补数据') {
    return [];
  }
  const text = [progress.currentAction, ...progress.workerStatusLabels].join('\n');
  return [
    ...(text.includes('A股') ? (['CN_A'] as const) : []),
    ...(text.includes('加密币') ? (['CRYPTO'] as const) : []),
  ];
}

function SelectionTaskBlock({
  progress,
  onCancelSelection,
  cancellingSelectionId,
  onRetryRawDataMaintenance,
  retryingRawMarket,
}: {
  progress?: SelectionProgressForUser | null;
  onCancelSelection?: (progress: SelectionProgressForUser) => void;
  cancellingSelectionId?: string | null;
  onRetryRawDataMaintenance?: (market: RawMaintenanceMarket) => void;
  retryingRawMarket?: RawMaintenanceMarket | null;
}) {
  if (!progress) {
    return null;
  }
  const percent = Math.max(0, Math.min(100, Math.round(progress.percent)));
  const canCancel = !String(progress.workflowRunId ?? '').startsWith('raw-data-maintenance:');
  const retryMarkets = failedRawMarkets(progress);
  return (
    <section className="ct-right-section" data-testid="right-rail-selection-section">
      <h2>{selectionProgressTitle(progress)}</h2>
      <article className="ct-task-item">
        <div className="ct-task-head">
          <strong>{progress.command}</strong>
          <span className={statusClass(progress.status === 'failed' ? 'failed' : progress.status === 'completed' ? 'ready' : 'info')}>
            {progress.statusLabel}
          </span>
        </div>
        <div className="ct-task-meta">
          <span>{progress.stageLabel}</span>
          <span>{formatDate(progress.startedAt)}</span>
        </div>
        <div className="ct-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={percent}>
          <div className="ct-progress-fill" style={{ width: `${percent}%` }} />
        </div>
        <p className="ct-task-action">{progress.currentAction}</p>
        <ul className="ct-task-status-list">
          {progress.workerStatusLabels.map((item) => (
            <li key={`${progress.startedAt}-${item}`}>{item}</li>
          ))}
        </ul>
        {progress.workflowRunId ? <div className="ct-small">工作流：{progress.workflowRunId}</div> : null}
        {progress.status === 'running' && onCancelSelection && canCancel ? (
          <button
            type="button"
            className="ct-text-button ct-task-stop-button"
            onClick={() => onCancelSelection(progress)}
            disabled={Boolean(progress.workflowRunId && cancellingSelectionId === progress.workflowRunId)}
          >
            {progress.workflowRunId && cancellingSelectionId === progress.workflowRunId ? '处理中' : '停止选股'}
          </button>
        ) : null}
        {onRetryRawDataMaintenance
          ? retryMarkets.map((market) => (
              <button
                key={market}
                type="button"
                className="ct-text-button ct-task-stop-button"
                onClick={() => onRetryRawDataMaintenance(market)}
                disabled={retryingRawMarket === market}
              >
                {retryingRawMarket === market ? '启动中' : `重新补${market === 'CN_A' ? 'A股' : '加密币'}`}
              </button>
            ))
          : null}
      </article>
    </section>
  );
}

function TaskBlock({
  queue,
  onCancelTask,
  cancellingTaskId,
}: {
  queue: ReportQueueSnapshotForUser;
  onCancelTask?: (task: ReportTaskForUser) => void;
  cancellingTaskId?: string | null;
}) {
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
            {onCancelTask ? (
              <button
                type="button"
                className={`ct-text-button${task.status === 'running' ? ' ct-task-stop-button' : ''}`}
                onClick={() => onCancelTask(task)}
                disabled={cancellingTaskId === task.taskId}
              >
                {cancellingTaskId === task.taskId ? '处理中' : task.status === 'running' ? '停止任务' : '取消排队'}
              </button>
            ) : null}
          </article>
        );
      })}
      {rows.length === 0 ? <p className="ct-empty">暂无运行中或排队中的报告任务</p> : null}
    </section>
  );
}

function ScheduledReportsBlock({
  items,
  onAction,
  actionId,
}: {
  items: ScheduledReportForUser[];
  onAction?: (action: 'pause' | 'resume' | 'delete' | 'run', scheduledReportId: string) => void;
  actionId?: string | null;
}) {
  const rows = items.filter((item) => item.state !== 'closed' && item.state !== 'deleted');

  return (
    <section className="ct-right-section" data-testid="right-rail-scheduled-reports-section">
      <h2>定时报表管理</h2>
      {rows.map((item) => {
        const busy = actionId === item.scheduledReportId;
        return (
          <article key={item.scheduledReportId} className="ct-task-item">
            <div className="ct-task-head">
              <strong>{item.instrumentCode}</strong>
              <span>{busy ? '处理中' : item.state}</span>
            </div>
            <div className="ct-task-meta">
              <span>{item.market}</span>
              <span>{frequencyLabel(item)}</span>
            </div>
            <div className="ct-task-list-line">
              <span>最近运行：{item.lastRunTaskId ?? '暂无'}</span>
            </div>
            <div className="ct-task-list-line">
              <span>cron：{item.cronJobId ?? '未同步'} / {item.lastCronRunId ?? '暂无 wake'}</span>
            </div>
            <div className="ct-small">{item.syncErrorMessage ?? `下次运行：${formatDate(item.nextRunAt)}`}</div>
            {onAction && item.state !== 'deleted' ? (
              <div className="ct-inline-actions">
                {item.state === 'paused' ? (
                  <button
                    type="button"
                    className="ct-text-button"
                    onClick={() => onAction('resume', item.scheduledReportId)}
                    disabled={busy}
                  >
                    {busy ? '处理中' : '恢复'}
                  </button>
                ) : (
                  <button
                    type="button"
                    className="ct-text-button"
                    onClick={() => onAction('pause', item.scheduledReportId)}
                    disabled={busy}
                  >
                    {busy ? '处理中' : '暂停'}
                  </button>
                )}
                <button
                  type="button"
                  className="ct-text-button"
                  onClick={() => onAction('run', item.scheduledReportId)}
                  disabled={busy}
                >
                  手动运行
                </button>
                <button
                  type="button"
                  className="ct-text-button"
                  onClick={() => onAction('delete', item.scheduledReportId)}
                  disabled={busy}
                >
                  删除
                </button>
              </div>
            ) : null}
          </article>
        );
      })}
      {rows.length === 0 ? <p className="ct-empty">暂无定时报表</p> : null}
    </section>
  );
}

function PriceAlertsBlock({
  items,
  onAction,
}: {
  items: PriceAlertForUser[];
  onAction?: (action: 'pause' | 'resume' | 'delete' | 'check', priceAlertId: string) => void;
}) {
  const rows = items.filter((alert) => alert.state !== 'closed' && alert.state !== 'deleted');

  return (
    <section className="ct-right-section" data-testid="right-rail-price-alerts-section">
      <h2>价格提醒管理</h2>
      {rows.map((alert) => (
        <article key={alert.priceAlertId} className="ct-task-item">
          <div className="ct-task-head">
            <strong>{alert.instrumentCode}</strong>
            <span>{alert.state}</span>
          </div>
          <div className="ct-task-meta">
            <span>{alert.market}</span>
            <span>{conditionLabel(alert)}</span>
          </div>
          <div className="ct-task-list-line">
            <span>最近报价：{quoteLabel(alert)}</span>
          </div>
          <div className="ct-task-list-line">
            <span>最近检查：{formatDate(alert.lastCheckedAt)}</span>
          </div>
          <div className="ct-task-list-line">
            <span>扫描：{alert.scanBucket ?? '未分桶'} / {alert.lastScanRunId ?? '暂无'}</span>
          </div>
          <div className="ct-task-list-line">
            <span>扫描结果：{scanResultLabel(alert)}</span>
          </div>
          <div className="ct-small">{notificationLabel(alert)}</div>
          {onAction && alert.state !== 'deleted' && alert.state !== 'closed' ? (
            <div className="ct-inline-actions">
              {alert.state === 'paused' ? (
                <button type="button" className="ct-text-button" onClick={() => onAction('resume', alert.priceAlertId)}>
                  恢复
                </button>
              ) : (
                <button type="button" className="ct-text-button" onClick={() => onAction('pause', alert.priceAlertId)}>
                  暂停
                </button>
              )}
              <button type="button" className="ct-text-button" onClick={() => onAction('check', alert.priceAlertId)}>
                立即检查
              </button>
              <button type="button" className="ct-text-button" onClick={() => onAction('delete', alert.priceAlertId)}>
                删除
              </button>
            </div>
          ) : null}
        </article>
      ))}
      {rows.length === 0 ? <p className="ct-empty">暂无价格提醒</p> : null}
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
  const channelState = channel?.state ?? 'pending';
  const channelStateDisplay = channelStateLabel(channel?.state);
  return (
    <>
      <section className="ct-right-section">
        <h2>微信通知</h2>
        <div className="ct-kv">
          <span>{channel?.displayName ?? '微信 ClawBot'}</span>
          <span className={statusClass(channelState)}>{channelStateDisplay}</span>
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
  selectionProgress,
  scheduledReports,
  priceAlerts,
  channel,
  latestReport,
  onPrintReport,
  onCancelTask,
  cancellingTaskId,
  onCancelSelection,
  cancellingSelectionId,
  onRetryRawDataMaintenance,
  retryingRawMarket,
  scheduledReportActionId,
  onScheduledReportAction,
  onPriceAlertAction,
}: {
  queue: ReportQueueSnapshotForUser;
  detail: ReportDetailForUser | null;
  selectionProgress?: SelectionProgressForUser | null;
  scheduledReports?: ScheduledReportForUser[];
  priceAlerts?: PriceAlertForUser[];
  channel: ChannelStatusForUser | null;
  latestReport: SavedReportForUser | null;
  onPrintReport?: () => void;
  onCancelTask?: (task: ReportTaskForUser) => void;
  cancellingTaskId?: string | null;
  onCancelSelection?: (progress: SelectionProgressForUser) => void;
  cancellingSelectionId?: string | null;
  onRetryRawDataMaintenance?: (market: RawMaintenanceMarket) => void;
  retryingRawMarket?: RawMaintenanceMarket | null;
  scheduledReportActionId?: string | null;
  onScheduledReportAction?: (action: 'pause' | 'resume' | 'delete' | 'run', scheduledReportId: string) => void;
  onPriceAlertAction?: (action: 'pause' | 'resume' | 'delete' | 'check', priceAlertId: string) => void;
}) {
  return (
    <aside className="ct-panel ct-right" data-testid="right-rail">
      <div className="ct-panel-title">{detail ? '报告详情' : '任务概览'}</div>
      {detail ? (
        <ReportBlock detail={detail} onPrintReport={onPrintReport} />
      ) : (
        <>
          <TaskBlock queue={queue} onCancelTask={onCancelTask} cancellingTaskId={cancellingTaskId} />
          <SelectionTaskBlock
            progress={selectionProgress}
            onCancelSelection={onCancelSelection}
            cancellingSelectionId={cancellingSelectionId}
            onRetryRawDataMaintenance={onRetryRawDataMaintenance}
            retryingRawMarket={retryingRawMarket}
          />
          <ScheduledReportsBlock
            items={scheduledReports ?? []}
            onAction={onScheduledReportAction}
            actionId={scheduledReportActionId}
          />
          <PriceAlertsBlock items={priceAlerts ?? []} onAction={onPriceAlertAction} />
        </>
      )}
      {!detail ? <ChatSummaryBlock channel={channel} latestReport={latestReport} /> : null}
    </aside>
  );
}
