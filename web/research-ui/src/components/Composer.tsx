import { FormEvent, useState } from 'react';

export function Composer({
  onSend,
  disabled,
  placeholder,
  buttonLabel,
}: {
  onSend: (text: string) => Promise<void>;
  disabled?: boolean;
  placeholder: string;
  buttonLabel: string;
}) {
  const [text, setText] = useState('');

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
      <input
        aria-label="输入消息"
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
