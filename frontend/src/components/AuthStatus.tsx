import { LogOut, UserCircle } from 'lucide-react';
import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';

import { fetchMe, logout, type AuthUser } from '../api/auth';

export function AuthStatus() {
  const [user, setUser] = useState<AuthUser | null>(null);

  useEffect(() => {
    fetchMe().then((state) => setUser(state.user)).catch(() => setUser(null));
  }, []);

  async function signOut() {
    await logout();
    setUser(null);
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
      {user.role === 'admin' && <span className="auth-role">管理员</span>}
      <LogOut size={15} />
    </button>
  );
}
