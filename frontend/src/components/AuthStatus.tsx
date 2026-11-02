import { LogOut, UserCircle } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { fetchMe, logout, onAuthStateChange, type AuthUser } from '../api/auth';

export function AuthStatus() {
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    let authChanged = false;
    const unsubscribe = onAuthStateChange((state) => {
      authChanged = true;
      setUser(state.user);
    });

    fetchMe()
      .then((state) => {
        if (!authChanged) setUser(state.user);
      })
      .catch(() => {
        if (!authChanged) setUser(null);
      });

    return unsubscribe;
  }, []);

  async function signOut() {
    await logout();
  }

  if (!user) {
    return (
      <Link className="auth-status button-like" to="/login">
        <UserCircle size={17} />
        未登录
      </Link>
    );
  }

  return (
    <button className="auth-status button-like" type="button" onClick={signOut} title="退出登录">
      <UserCircle size={17} />
      <span className="auth-name">{user.displayName}</span>
      {user.isSuperAdmin && <span className="auth-role" style={{ background: '#1a73e8' }}>超级管理员</span>}
      {!user.isSuperAdmin && user.role === 'admin' && <span className="auth-role">管理员</span>}
      <LogOut size={15} />
    </button>
  );
}
