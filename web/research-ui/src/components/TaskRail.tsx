import type { ReportQueueSnapshotForUser, ReportTaskForUser } from '../api/contracts';
import { EmptyTaskRail } from './EmptyStates';

function formatDate(value?: string | null) {
  if (!value) {
    return '—';
  }
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

function progressPercent(task: ReportTaskForUser) {
  const value = task.progress?.percent ?? 0;
  return Math.max(4, Math.min(100, Math.round(value)));
}

function visibleTasks(snapshot: ReportQueueSnapshotForUser) {
  return [...(snapshot.runningTask ? [snapshot.runningTask] : []), ...snapshot.queuedTasks].filter(
    (task) => task.status === 'running' || task.status === 'queued',
  );
}

export function TaskRail({ queue }: { queue: ReportQueueSnapshotForUser }) {
  const tasks = visibleTasks(queue);
  if (!tasks.length) {
    return (
      <section className="ct-task-rail" data-testid="task-rail">
        <EmptyTaskRail />
      </section>
    );
  }

  return (
    <section className="ct-task-rail" data-testid="task-rail">
      <ul className="ct-task-list">
        {tasks.map((task) => (
          <li key={task.taskId} className="ct-task-item">
            <div className="ct-task-head">
              <strong>{task.instrumentCode}</strong>
              <span>{task.statusLabel}</span>
            </div>
            {task.queuePosition ? <div className="ct-task-meta">队列序号：{task.queuePosition}</div> : null}
            <div className="ct-task-meta">提交时间：{formatDate(task.createdAt)}</div>
            {task.progress?.stageLabel ? <div className="ct-task-meta">当前阶段：{task.progress.stageLabel}</div> : null}
            {task.progress?.roleLabel ? <div className="ct-task-meta">当前角色：{task.progress.roleLabel}</div> : null}
            <div className="ct-task-progress-track" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={progressPercent(task)}>
              <div className="ct-task-progress-fill" style={{ width: `${progressPercent(task)}%` }} />
            </div>
            <p className="ct-task-action">{task.progress?.currentAction ?? '正在推进当前任务'}</p>
            {task.progress?.workerStatusLabels?.length ? (
              <ul className="ct-task-status-list">
                {task.progress.workerStatusLabels.map((item) => (
                  <li key={`${task.taskId}-${item}`}>{item}</li>
                ))}
              </ul>
            ) : null}
            <p className="ct-task-meta">
              已完成：{task.progress?.completedRoleLabels?.join('、') || '暂无'}；待执行：
              {task.progress?.waitingRoleLabels?.join('、') || '暂无'}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
