import { createBrowserRouter } from 'react-router-dom';

import { AppShell } from './components/AppShell';
import { HomePage } from './pages/HomePage';
import { SettingsPage } from './pages/SettingsPage';
import { ToolPage } from './pages/ToolPage';

export const router = createBrowserRouter([
  {
    path: '/',
    element: <AppShell />,
    children: [
      { index: true, element: <HomePage /> },
      { path: 'tools/:toolId', element: <ToolPage /> },
      { path: 'settings', element: <SettingsPage /> },
    ],
  },
]);
