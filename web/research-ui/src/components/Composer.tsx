import { FormEvent, useId, useState } from 'react';

export function Composer({
  onSend,
  disabled,
  placeholder,
  buttonLabel,
  hint,
}: {
  onSend: (text: string) => Promise<void>;
  disabled?: boolean;
  placeholder: string;
  buttonLabel: string;
  hint?: string;
}) {
  const [text, setText] = useState('');
  const hintId = useId();

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = text.trim();
    if (!value || disabled) {
      return;
    }
    setText('');
    await onSend(value);
  }

  return (
    <form className="ct-composer" onSubmit={submit}>
      {hint ? (
        <p className="ct-composer-hint" id={hintId}>
          {hint}
        </p>
      ) : null}
      <input
        aria-label="输入消息"
        aria-describedby={hint ? hintId : undefined}
        placeholder={placeholder}
        disabled={disabled}
        value={text}
        onChange={(event) => setText(event.target.value)}
      />
      <button type="submit" disabled={disabled || !text.trim()}>
        {buttonLabel}
      </button>
    </form>
  );
}
