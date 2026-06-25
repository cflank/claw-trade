import { FormEvent, KeyboardEvent, useId, useRef, useState } from 'react';
import type { WorkerChatWorkerForUser } from '../api/contracts';
import { WorkerSelector } from './WorkerSelector';

type ComposerProps = {
  onSend: (text: string) => Promise<void>;
  disabled?: boolean;
  placeholder: string;
  buttonLabel: string;
  hint?: string;
  commandMenuEnabled?: boolean;
  workerChatEnabled?: boolean;
  workers?: WorkerChatWorkerForUser[];
  selectedWorkerId?: string;
  onWorkerChange?: (workerId: string) => void;
};

export function Composer({
  onSend,
  disabled,
  placeholder,
  buttonLabel,
  hint,
  commandMenuEnabled,
  workerChatEnabled,
  workers = [],
  selectedWorkerId,
  onWorkerChange,
}: ComposerProps) {
  const [text, setText] = useState('');
  const [workerSelectorOpen, setWorkerSelectorOpen] = useState(false);
  const [activeMentionIndex, setActiveMentionIndex] = useState(0);
  const [activeCommandIndex, setActiveCommandIndex] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const hintId = useId();
  const showWorkerSelector = Boolean(workerChatEnabled && workers.length > 0 && onWorkerChange);
  const mentionQuery = workerChatEnabled && !onWorkerChange ? leadingMentionQuery(text) : null;
  const commandQuery = commandMenuEnabled ? leadingCommandQuery(text) : null;
  const visibleMentionWorkers =
    mentionQuery === null ? [] : workers.filter((worker) => workerMatchesMention(worker, mentionQuery));
  const visibleCommands =
    commandQuery === null ? [] : COMMAND_MENU_ITEMS.filter((item) => commandMatchesQuery(item, commandQuery));
  const exactCommand = commandQuery === null ? null : exactCommandForQuery(commandQuery);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (commandQuery !== null && exactCommand === null) {
      if (visibleCommands.length > 0) {
        insertCommand(visibleCommands[activeCommandIndex] ?? visibleCommands[0]);
      }
      return;
    }
    if (mentionQuery !== null) {
      if (visibleMentionWorkers.length > 0) {
        insertWorkerMention(visibleMentionWorkers[activeMentionIndex] ?? visibleMentionWorkers[0]);
      }
      return;
    }
    const value = text.trim();
    if (!value || disabled) {
      return;
    }
    setText(workerChatEnabled && !onWorkerChange ? nextVisibleWorkerMention(value, workers) : '');
    setWorkerSelectorOpen(false);
    await onSend(value);
  }

  function updateText(value: string) {
    setText(value);
    setActiveMentionIndex(0);
    setActiveCommandIndex(0);
    if (showWorkerSelector && value.trimStart().startsWith('@')) {
      setWorkerSelectorOpen(true);
    }
  }

  function insertWorkerMention(worker: WorkerChatWorkerForUser) {
    setText((current) => current.replace(/^\s*@\S*/, `@${worker.displayName}`) + ' ');
    inputRef.current?.focus();
  }

  function insertCommand(command: CommandMenuItem) {
    setText((current) => current.replace(/^\s*\/\S*/, command.insertText));
    inputRef.current?.focus();
  }

  function handleInputKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (commandQuery !== null) {
      if (event.key === 'ArrowDown') {
        event.preventDefault();
        event.stopPropagation();
        if (visibleCommands.length > 0) {
          setActiveCommandIndex((current) => (current + 1) % visibleCommands.length);
        }
      } else if (event.key === 'ArrowUp') {
        event.preventDefault();
        event.stopPropagation();
        if (visibleCommands.length > 0) {
          setActiveCommandIndex((current) => (current - 1 + visibleCommands.length) % visibleCommands.length);
        }
      } else if (event.key === 'Enter' || event.key === 'Tab') {
        if (event.key === 'Enter' && exactCommand !== null) {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        if (visibleCommands.length > 0) {
          insertCommand(visibleCommands[activeCommandIndex] ?? visibleCommands[0]);
        }
      } else if (event.key === 'Escape') {
        event.preventDefault();
        setText('');
      }
      return;
    }
    if (mentionQuery === null) {
      return;
    }
    if (event.key === 'ArrowDown') {
      event.preventDefault();
      event.stopPropagation();
      if (visibleMentionWorkers.length > 0) {
        setActiveMentionIndex((current) => (current + 1) % visibleMentionWorkers.length);
      }
    } else if (event.key === 'ArrowUp') {
      event.preventDefault();
      event.stopPropagation();
      if (visibleMentionWorkers.length > 0) {
        setActiveMentionIndex((current) => (current - 1 + visibleMentionWorkers.length) % visibleMentionWorkers.length);
      }
    } else if (event.key === 'Enter' || event.key === 'Tab') {
      event.preventDefault();
      event.stopPropagation();
      if (visibleMentionWorkers.length > 0) {
        insertWorkerMention(visibleMentionWorkers[activeMentionIndex] ?? visibleMentionWorkers[0]);
      }
    } else if (event.key === 'Escape') {
      event.preventDefault();
      setText('');
    }
  }

  return (
    <form className={`ct-composer${showWorkerSelector ? ' ct-composer-with-worker' : ''}`} onSubmit={submit}>
      {hint ? (
        <p className="ct-composer-hint" id={hintId}>
          {hint}
        </p>
      ) : null}
      {showWorkerSelector && onWorkerChange ? (
        <WorkerSelector
          workers={workers}
          selectedWorkerId={selectedWorkerId}
          onWorkerChange={onWorkerChange}
          disabled={disabled}
          open={workerSelectorOpen}
          onOpenChange={setWorkerSelectorOpen}
        />
      ) : null}
      <div className="ct-composer-field">
        <input
          ref={inputRef}
          aria-label="输入消息"
          aria-describedby={hint ? hintId : undefined}
          placeholder={placeholder}
          disabled={disabled}
          value={text}
          onChange={(event) => updateText(event.target.value)}
          onKeyDown={handleInputKeyDown}
        />
        {mentionQuery !== null ? (
          <div className="ct-worker-selector-menu ct-worker-mention-menu">
            <div className="ct-worker-selector-list" role="listbox" aria-label="worker 列表">
              {visibleMentionWorkers.map((worker, index) => (
                <button
                  key={worker.workerId}
                  type="button"
                  role="option"
                  aria-selected={index === activeMentionIndex}
                  className="ct-worker-selector-option"
                  onClick={() => insertWorkerMention(worker)}
                >
                  @{worker.displayName}
                </button>
              ))}
              {visibleMentionWorkers.length === 0 ? <div className="ct-worker-selector-empty">没有匹配角色</div> : null}
            </div>
          </div>
        ) : null}
        {commandQuery !== null ? (
          <div className="ct-worker-selector-menu ct-command-menu">
            <div className="ct-worker-selector-list" role="listbox" aria-label="命令列表">
              {visibleCommands.map((command, index) => (
                <button
                  key={command.command}
                  type="button"
                  role="option"
                  aria-selected={index === activeCommandIndex}
                  className="ct-worker-selector-option ct-command-option"
                  onClick={() => insertCommand(command)}
                >
                  <span className="ct-command-name">{command.command}</span>
                  <span className="ct-command-description">{command.description}</span>
                  <span className="ct-command-usage">{command.usage}</span>
                </button>
              ))}
              {visibleCommands.length === 0 ? <div className="ct-worker-selector-empty">没有匹配命令</div> : null}
            </div>
          </div>
        ) : null}
      </div>
      <button type="submit" disabled={disabled || !text.trim()}>
        {buttonLabel}
      </button>
    </form>
  );
}

type CommandMenuItem = {
  command: string;
  insertText: string;
  description: string;
  usage: string;
  keywords: string[];
};

const COMMAND_MENU_ITEMS: CommandMenuItem[] = [
  {
    command: '/report',
    insertText: '/report ',
    description: '生成完整投资报告',
    usage: '/report TSLA',
    keywords: ['report', '报告', '投资报告'],
  },
  {
    command: '/sched',
    insertText: '/sched ',
    description: '创建定时报告',
    usage: '/sched TSLA 每天 08:00',
    keywords: ['sched', 'schedule', '定时', '定时报告'],
  },
  {
    command: '/alert',
    insertText: '/alert ',
    description: '创建价格提醒',
    usage: '/alert BTC 高于 70000 提醒我',
    keywords: ['alert', '提醒', '价格提醒'],
  },
  {
    command: '/select',
    insertText: '/select ',
    description: '查看 A股或加密选股结果',
    usage: '/select 1、/select 2、/select crypto',
    keywords: ['select', '选股', '刷新', '1', '2', 'cn_a', 'a股', 'crypto', '加密'],
  },
  {
    command: '/maint',
    insertText: '/maint ',
    description: '查看维护状态摘要',
    usage: '/maint',
    keywords: ['maint', '维护', '状态'],
  },
  {
    command: '/help',
    insertText: '/help ',
    description: '查看命令详细用法',
    usage: '/help',
    keywords: ['help', '帮助', '命令'],
  },
];

function leadingCommandQuery(value: string) {
  const trimmed = value.trimStart();
  if (!trimmed.startsWith('/')) {
    return null;
  }
  const token = trimmed.slice(1);
  return /\s/.test(token) ? null : token.toLowerCase();
}

function commandMatchesQuery(command: CommandMenuItem, query: string) {
  if (!query) {
    return true;
  }
  return [command.command.slice(1), command.description, command.usage, ...command.keywords].some((value) =>
    value.toLowerCase().includes(query),
  );
}

function exactCommandForQuery(query: string) {
  return COMMAND_MENU_ITEMS.find((item) => item.command.slice(1) === query) ?? null;
}

function leadingMentionQuery(value: string) {
  const trimmed = value.trimStart();
  if (!trimmed.startsWith('@')) {
    return null;
  }
  const token = trimmed.slice(1);
  return /\s/.test(token) ? null : token.toLowerCase();
}

function workerMatchesMention(worker: WorkerChatWorkerForUser, query: string) {
  if (!query) {
    return true;
  }
  return [worker.workerId, worker.displayName, ...worker.aliases].some((value) => value.toLowerCase().includes(query));
}

function nextVisibleWorkerMention(value: string, workers: WorkerChatWorkerForUser[]) {
  const trimmed = value.trimStart();
  if (!trimmed.startsWith('@')) {
    return '';
  }
  const afterAt = trimmed.slice(1);
  const candidates = workers
    .flatMap((worker) =>
      [worker.displayName, worker.workerId, ...worker.aliases].map((raw) => ({
        worker,
        raw: raw.trim(),
        normalized: raw.trim().replace(/^@+/, '').toLowerCase(),
      })),
    )
    .filter((candidate) => candidate.normalized)
    .sort((left, right) => right.normalized.length - left.normalized.length);
  const normalizedInput = afterAt.trim().replace(/^@+/, '').toLowerCase();
  const match = candidates.find((candidate) => {
    if (!normalizedInput.startsWith(candidate.normalized)) {
      return false;
    }
    const rest = afterAt.slice(candidate.raw.length);
    return !rest || /^\s/.test(rest);
  });
  return match ? `@${match.worker.displayName} ` : '';
}
