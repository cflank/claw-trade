export function InlineErrorState({ message }: { message: string }) {
  return <div className="ct-error-state">{message}</div>;
}

export function ReportErrorState({ statusCode }: { statusCode: number }) {
  if (statusCode === 404) {
    return <div className="ct-error-state">这份报告不存在或暂不可查看。</div>;
  }
  if (statusCode === 403) {
    return <div className="ct-error-state">你暂时没有权限查看这份报告。</div>;
  }
  return <div className="ct-error-state">报告加载失败，请稍后重试。</div>;
}
