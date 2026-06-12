import { Home, Settings, Sparkles } from 'lucide-react';
import { useState } from 'react';
import { NavLink, Outlet } from 'react-router-dom';

import { AuthStatus } from './AuthStatus';

export type ShellDisplayMode = 'standard' | 'fullscreen';

export function AppShell() {
  const [displayMode, setDisplayMode] = useState<ShellDisplayMode>('standard');

  return (
    <div className={displayMode === 'fullscreen' ? 'app-shell shell-fullscreen' : 'app-shell'}>
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark"><Sparkles size={20} /></span>
          <span className="brand-copy">
            <span>pansis toolbox</span>
            <small>AI workspace</small>
          </span>
        </div>
        <nav className="nav-list" aria-label="主导航">
          <NavLink to="/" className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}>
            <Home size={18} />
            <span>首页</span>
          </NavLink>
          <NavLink to="/settings" className={({ isActive }) => (isActive ? 'nav-item active' : 'nav-item')}>
            <Settings size={18} />
            <span>设置</span>
          </NavLink>
        </nav>
        <AuthStatus />
      </aside>
      <main className="main-content">
        <Outlet context={{ setDisplayMode }} />
      </main>
    </div>
  );
}
