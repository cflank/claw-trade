import { useState } from 'react';
import type { ChatContextForUser, ChatMessageForUser } from '../api/contracts';

const CONTEXT_LABEL: Record<ChatContextForUser['kind'], string> = {
  normal_chat: '普通聊天',
  intent_confirming: '任务确认',
  task_following: '任务跟进',
  report_reading: '报告阅读',
};

function formatDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

export function ChatPanel({
  context,
  messages,
  sending,
  onSend,
}: {
  context: ChatContextForUser;
  messages: ChatMessageForUser[];
  sending: boolean;
  onSend: (text: string) => Promise<void>;
}) {
  const [text, setText] = useState('');

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = text.trim();
    if (!value || sending) {
      return;
    }
    setText('');
    await onSend(value);
  }

  return (
    <section className="ct-main">
      <div className="ct-main-header">
        <h1>投研对话</h1>
        <div className="ct-context-pill">{CONTEXT_LABEL[context.kind]}</div>
      </div>
      <div className="ct-messages" data-testid="chat-message-stream">
        {messages.map((message) => (
          <article key={message.messageId} className={`ct-message ct-${message.actor}`}>
            <div className="ct-message-meta">
              <span>{message.actor === 'user' ? '你' : message.actor === 'assistant' ? '助手' : '系统'}</span>
              <time>{formatDate(message.createdAt)}</time>
            </div>
            <p>{message.text}</p>
          </article>
        ))}
        {messages.length === 0 ? <p className="ct-empty">输入你的问题，开始投研协作。</p> : null}
      </div>
      <form className="ct-composer" onSubmit={submit}>
        <input
          aria-label="输入消息"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="输入问题，或提交报告任务需求"
        />
        <button type="submit" disabled={sending}>
          {sending ? '发送中' : '发送'}
        </button>
      </form>
    </section>
  );
}
