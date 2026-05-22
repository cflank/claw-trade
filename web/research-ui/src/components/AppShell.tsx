import { Link, useLocation } from 'react-router-dom';
import type { PropsWithChildren } from 'react';

export function AppShell({ children }: PropsWithChildren) {
  const location = useLocation();
  const inSettings = location.pathname === '/settings';

  return (
    <div className="ct-shell">
      <header className="ct-topbar">
        <div className="ct-brand">claw-trade 投研工作台</div>
        <nav className="ct-nav">
          <Link to="/" aria-current={!inSettings ? 'page' : undefined}>
            工作台
          </Link>
          <Link to="/settings" aria-current={inSettings ? 'page' : undefined}>
            设置
          </Link>
          <a href="/api/ui/open-device-interface" target="_blank" rel="noreferrer">
            设备界面
          </a>
        </nav>
      </header>
      {children}
    </div>
  );
}
