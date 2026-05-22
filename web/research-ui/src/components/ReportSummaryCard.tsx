export function ReportSummaryCard({
  summary,
  reportId,
  onOpenReport,
}: {
  summary: string;
  reportId: string;
  onOpenReport?: (reportId: string) => void;
}) {
  return (
    <section className="ct-report-summary-card">
      <h3>正式报告已完成</h3>
      <p>{summary}</p>
      <button type="button" onClick={() => onOpenReport?.(reportId)}>
        查看完整报告
      </button>
    </section>
  );
}
