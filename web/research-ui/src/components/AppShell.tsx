import { Link, useLocation } from 'react-router-dom';
import type { PropsWithChildren } from 'react';

export function AppShell({ children }: PropsWithChildren) {
  const location = useLocation();
  const inSettings = location.pathname === '/settings';
  const inAdvancedDiagnostics = location.pathname === '/advanced-diagnostics';
  const inWorkspace = !inSettings && !inAdvancedDiagnostics;

  return (
    <div className="ct-shell">
      <header className="ct-topbar">
        <div className="ct-brand">claw-trade 投研工作台</div>
        <nav className="ct-nav">
          <Link to="/" aria-current={inWorkspace ? 'page' : undefined}>
            工作台
          </Link>
          <Link to="/settings" aria-current={inSettings ? 'page' : undefined}>
            设置
          </Link>
          <Link to="/advanced-diagnostics" aria-current={inAdvancedDiagnostics ? 'page' : undefined}>
            高级诊断
          </Link>
        </nav>
      </header>
      {children}
    </div>
  );
}
