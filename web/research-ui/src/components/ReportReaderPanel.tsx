import { useMemo, useState } from 'react';
import ReactMarkdown from 'react-markdown';
import type { ReportDetailForUser } from '../api/contracts';

function extractToc(markdown: string) {
  const lines = markdown.split('\n');
  const toc: Array<{ level: number; text: string }> = [];
  for (const line of lines) {
    const matched = /^(#{1,3})\s+(.+)$/.exec(line.trim());
    if (matched) {
      toc.push({ level: matched[1].length, text: matched[2] });
    }
  }
  return toc;
}

function formatDate(value: string) {
  return new Date(value).toLocaleString('zh-CN', { hour12: false });
}

export function ReportReaderPanel({
  detail,
  sending,
  onAsk,
}: {
  detail: ReportDetailForUser;
  sending: boolean;
  onAsk: (text: string) => Promise<void>;
}) {
  const [text, setText] = useState('');
  const toc = useMemo(() => extractToc(detail.markdown), [detail.markdown]);

  async function submit(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const value = text.trim();
    if (!value || sending) {
      return;
    }
    setText('');
    await onAsk(value);
  }

  return (
    <section className="ct-main">
      <div className="ct-main-header">
        <h1>{detail.report.title}</h1>
        <div className="ct-context-pill">报告阅读</div>
      </div>
      <div className="ct-report-meta">
        <span>{detail.report.instrumentCode}</span>
        <span>{detail.report.market}</span>
        <time>{formatDate(detail.report.generatedAt)}</time>
      </div>
      <article className="ct-report-prose" data-testid="report-reader-markdown">
        <ReactMarkdown>{detail.markdown}</ReactMarkdown>
      </article>
      <form className="ct-composer" onSubmit={submit}>
        <input
          aria-label="报告追问输入"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder="围绕当前报告继续追问"
        />
        <button type="submit" disabled={sending}>
          {sending ? '发送中' : '追问'}
        </button>
      </form>
      <div className="ct-report-toc-inline">
        {toc.map((item) => (
          <span key={`${item.level}-${item.text}`}>{item.text}</span>
        ))}
      </div>
    </section>
  );
}
