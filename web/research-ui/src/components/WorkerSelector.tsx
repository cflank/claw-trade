import { useMemo, useState } from 'react';
import type { WorkerChatWorkerForUser } from '../api/contracts';

type WorkerSelectorProps = {
  workers: WorkerChatWorkerForUser[];
  selectedWorkerId?: string;
  onWorkerChange: (workerId: string) => void;
  disabled?: boolean;
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
};

function normalizeQuery(value: string) {
  return value.trim().replace(/^@+/, '').toLowerCase();
}

function resolveSelectedWorker(workers: WorkerChatWorkerForUser[], selectedWorkerId?: string) {
  return (
    workers.find((worker) => worker.workerId === selectedWorkerId) ??
    workers.find((worker) => worker.default) ??
    workers[0] ??
    null
  );
}

function workerMatchesQuery(worker: WorkerChatWorkerForUser, rawQuery: string) {
  const query = normalizeQuery(rawQuery);
  if (!query) {
    return true;
  }
  const values = [worker.displayName, worker.workerId, ...worker.aliases];
  return values.some((value) => normalizeQuery(value).includes(query));
}

export function WorkerSelector({
  workers,
  selectedWorkerId,
  onWorkerChange,
  disabled,
  open,
  onOpenChange,
}: WorkerSelectorProps) {
  const [internalOpen, setInternalOpen] = useState(false);
  const [query, setQuery] = useState('');
  const isOpen = open ?? internalOpen;
  const selectedWorker = resolveSelectedWorker(workers, selectedWorkerId);
  const visibleWorkers = useMemo(
    () => workers.filter((worker) => workerMatchesQuery(worker, query)),
    [query, workers],
  );

  function setOpen(nextOpen: boolean) {
    if (disabled && nextOpen) {
      return;
    }
    if (!nextOpen) {
      setQuery('');
    }
    if (open === undefined) {
      setInternalOpen(nextOpen);
    }
    onOpenChange?.(nextOpen);
  }

  function selectWorker(workerId: string) {
    onWorkerChange(workerId);
    setOpen(false);
  }

  if (!selectedWorker) {
    return null;
  }

  return (
    <div className="ct-worker-selector">
      <button
        type="button"
        className="ct-worker-selector-trigger"
        aria-haspopup="listbox"
        aria-expanded={isOpen}
        disabled={disabled}
        onClick={() => setOpen(!isOpen)}
      >
        {selectedWorker.displayName}
      </button>
      {isOpen ? (
        <div className="ct-worker-selector-menu">
          <input
            type="search"
            aria-label="筛选 worker"
            placeholder="搜索角色"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
          <div className="ct-worker-selector-list" role="listbox" aria-label="worker 列表">
            {visibleWorkers.map((worker) => (
              <button
                key={worker.workerId}
                type="button"
                role="option"
                aria-selected={worker.workerId === selectedWorker.workerId}
                className="ct-worker-selector-option"
                onClick={() => selectWorker(worker.workerId)}
              >
                {worker.displayName}
              </button>
            ))}
            {visibleWorkers.length === 0 ? <div className="ct-worker-selector-empty">没有匹配角色</div> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}
