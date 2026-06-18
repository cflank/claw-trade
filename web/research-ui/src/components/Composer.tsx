import { FormEvent, useId, useState } from 'react';
import type { WorkerChatWorkerForUser } from '../api/contracts';
import { WorkerSelector } from './WorkerSelector';

type ComposerProps = {
  onSend: (text: string) => Promise<void>;
  disabled?: boolean;
  placeholder: string;
  buttonLabel: string;
  hint?: string;
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
  workerChatEnabled,
  workers = [],
  selectedWorkerId,
  onWorkerChange,
}: ComposerProps) {
  const [text, setText] = useState('');
  const [workerSelectorOpen, setWorkerSelectorOpen] = useState(false);
  const hintId = useId();
  const showWorkerSelector = Boolean(workerChatEnabled && workers.length > 0 && onWorkerChange);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = text.trim();
    if (!value || disabled) {
      return;
    }
    setText('');
    setWorkerSelectorOpen(false);
    await onSend(value);
  }

  function updateText(value: string) {
    setText(value);
    if (showWorkerSelector && value.includes('@')) {
      setWorkerSelectorOpen(true);
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
      <input
        aria-label="输入消息"
        aria-describedby={hint ? hintId : undefined}
        placeholder={placeholder}
        disabled={disabled}
        value={text}
        onChange={(event) => updateText(event.target.value)}
      />
      <button type="submit" disabled={disabled || !text.trim()}>
        {buttonLabel}
      </button>
    </form>
  );
}
