import { Navigate, useParams } from 'react-router-dom';

export function LegacyReportRoute() {
  const { reportId } = useParams<{ reportId: string }>();
  if (!reportId) {
    return <Navigate to="/" replace />;
  }
  return <Navigate to={`/?reportId=${encodeURIComponent(reportId)}`} replace />;
}
