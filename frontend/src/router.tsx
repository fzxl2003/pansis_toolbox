import { createBrowserRouter } from 'react-router-dom';

import { AppShell } from './components/AppShell';
import { HomePage } from './pages/HomePage';
import { LoginPage } from './pages/LoginPage';
import { SettingsPage } from './pages/SettingsPage';
import { ToolPage } from './pages/ToolPage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'login', element: <LoginPage /> },
      { path: 'tools/:toolId', element: <ToolPage /> },
      { path: 'settings', element: <SettingsPage /> },
    ],
  },
]);
