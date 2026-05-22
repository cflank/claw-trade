export function EmptyMessageState() {
  return (
    <section className="ct-empty-state">
      <h3>还没有聊天内容</h3>
      <p>你可以直接提问，或用 /report 发起一份正式投研任务。</p>
    </section>
  );
}

export function EmptyTaskRail() {
  return <div className="ct-empty-state">暂无活任务</div>;
}

export function EmptyHistoryRail() {
  return <div className="ct-empty-state">暂无已保存报告</div>;
}
