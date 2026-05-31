import { createBrowserRouter } from 'react-router-dom';
import { HomePage } from './routes/HomePage';
import { LegacyReportRoute } from './routes/LegacyReportRoute';
import { SettingsPage } from './routes/SettingsPage';
import { AdvancedDiagnosticsPage } from './routes/AdvancedDiagnosticsPage';

export const router = createBrowserRouter([
  { path: '/', element: <HomePage /> },
  { path: '/reports/:reportId', element: <LegacyReportRoute /> },
  { path: '/settings', element: <SettingsPage /> },
  { path: '/advanced-diagnostics', element: <AdvancedDiagnosticsPage /> },
]);
