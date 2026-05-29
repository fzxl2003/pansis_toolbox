import { LoginPanel } from '../components/LoginPanel';

export function LoginPage() {
  return (
    <div className="page-stack">
      <header className="page-header">
        <div>
          <p className="eyebrow">Account</p>
          <h1>登录</h1>
        </div>
      </header>
      <LoginPanel redirectTo="/" />
    </div>
  );
}
