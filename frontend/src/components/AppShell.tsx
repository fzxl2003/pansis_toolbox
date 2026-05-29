import { Box, Home, Settings } from 'lucide-react';
import { NavLink, Outlet } from 'react-router-dom';

import { AuthStatus } from './AuthStatus';

export function AppShell() {
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <Box size={22} />
          <span>pansis toolbox</span>
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
        <Outlet />
      </main>
    </div>
  );
}
