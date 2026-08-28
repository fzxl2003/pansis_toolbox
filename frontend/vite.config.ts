import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

const webProxyTarget = 'http://127.0.0.1:8000';
const webProxyProxy = {
  target: webProxyTarget,
  ws: true,
  // Preserve the browser-facing :5173 Host.  The web-proxy backend turns it
  // into X-Forwarded-Host/Proto for Rammerhead URL rewriting, so subsequent
  // links keep using the sole public development port instead of :8000.
  changeOrigin: false,
};

export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      react: path.resolve(__dirname, 'node_modules/react'),
      'react/jsx-runtime': path.resolve(__dirname, 'node_modules/react/jsx-runtime'),
      'react-dom/client': path.resolve(__dirname, 'node_modules/react-dom/client'),
      'react-router-dom': path.resolve(__dirname, 'node_modules/react-router-dom'),
      'lucide-react': path.resolve(__dirname, 'node_modules/lucide-react'),
      '@xterm/xterm': path.resolve(__dirname, 'node_modules/@xterm/xterm'),
      '@xterm/addon-fit': path.resolve(__dirname, 'node_modules/@xterm/addon-fit'),
    },
  },
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        ws: true,
        changeOrigin: true,
      },
      '/tb': {
        target: 'http://127.0.0.1:8000',
        ws: true,
        changeOrigin: true,
      },
      // TensorBoard sessions owned by tensorboard_progress_monitor.  This is
      // a root-level FastAPI proxy path, so the Vite dev server must forward it
      // just like the standalone dashboard's /tb path.
      '/tpm-tb': {
        target: 'http://127.0.0.1:8000',
        ws: true,
        changeOrigin: true,
      },
      '/web-proxy': {
        ...webProxyProxy,
      },
      // Rammerhead rewrites navigations to /<32-hex-session-id>/... and
      // loads these service endpoints at the origin root.  Without forwarding
      // them Vite's SPA fallback serves index.html after the first click.
      // A ``!s!utf-8`` directive may immediately follow the session id for
      // rewritten cross-origin scripts/styles, e.g. Bilibili's CDN assets.
      // ``*<window-id>`` identifies a target=_blank navigation.
      '^/[0-9a-fA-F]{32}(?:/|!|\\*|$)': webProxyProxy,
      '/rammerhead.js': webProxyProxy,
      '/hammerhead.js': webProxyProxy,
      '/worker-hammerhead.js': webProxyProxy,
      '/transport-worker.js': webProxyProxy,
      '/task.js': webProxyProxy,
      '/iframe-task.js': webProxyProxy,
      '/messaging': webProxyProxy,
      '/syncLocalStorage': webProxyProxy,
    },
    fs: {
      allow: ['..'],
    },
  },
});
