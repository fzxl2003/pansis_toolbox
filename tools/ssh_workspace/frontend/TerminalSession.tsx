// ============================================================
// SSH Workspace Tool — Terminal Session (single xterm + WebSocket)
// ============================================================

import { useEffect, useRef, useState } from 'react';
import { Terminal } from '@xterm/xterm';
import { FitAddon } from '@xterm/addon-fit';
import { AlertCircle, Loader2, RotateCw, SquareTerminal } from 'lucide-react';

import { buildTerminalWsUrl } from './utils';
import type { TerminalApi, TerminalTab } from './types';

export type TerminalSessionProps = {
  tab: TerminalTab;
  serverName: string;
  active: boolean;
  registerApi: (api: TerminalApi | null) => void;
};

type ConnStatus = 'connecting' | 'connected ' | 'error' | 'closed';

export function TerminalSession({ tab, serverName, active, registerApi }: TerminalSessionProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const resizeTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const [status, setStatus] = useState<ConnStatus>('connecting');
  const [errorMsg, setErrorMsg] = useState('');
  // reconnectKey forces the main effect to re-run (re-create terminal + WS)
  const [reconnectKey, setReconnectKey] = useState(0);

  // ---- Initialize terminal & websocket ----
  useEffect(() => {
    if (!containerRef.current) return;

    // Create xterm instance
    const term = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", Menlo, monospace',
      theme: {
        background: '#0f172a',
        foreground: '#e2e8f0',
        cursor: '#e2e8f0',
        selectionBackground: '#334155',
        black: '#1e293b',
        red: '#f87171',
        green: '#4ade80',
        yellow: '#facc15',
        blue: '#60a5fa',
        magenta: '#c084fc',
        cyan: '#22d3ee',
        white: '#e2e8f0',
        brightBlack: '#475569',
        brightRed: '#fca5a5',
        brightGreen: '#86efac',
        brightYellow: '#fde047',
        brightBlue: '#93c5fd',
        brightMagenta: '#d8b4fe',
        brightCyan: '#67e8f9',
        brightWhite: '#f8fafc',
      },
    });
    const fit = new FitAddon();
    term.loadAddon(fit);
    term.open(containerRef.current);
    try { fit.fit(); } catch { /* ignore */ }

    termRef.current = term;
    fitRef.current = fit;

    const cols = term.cols || 80;
    const rows = term.rows || 24;

    // Connect WebSocket
    const url = buildTerminalWsUrl(tab.serverId, tab.mode, tab.screenSession, cols, rows, tab.initialCommand);
    const ws = new WebSocket(url);
    wsRef.current = ws;
    setStatus('connecting');
    setErrorMsg('');

    ws.onopen = () => {
      setStatus('connected');
    };

    ws.onmessage = (event) => {
      try {
        const msg = JSON.parse(event.data);
        if (msg.type === 'output') {
          term.write(msg.data);
        } else if (msg.type === 'status') {
          if (msg.status === 'connected') {
            setStatus('connected');
          } else if (msg.status === 'closed') {
            setStatus('closed');
          }
        } else if (msg.type === 'error') {
          gotServerError = true;
          setErrorMsg(msg.message || '连接错误');
          setStatus('error');
        }
      } catch {
        // non-JSON message, ignore
      }
    };

    // Track whether we received a specific server-side error message.
    // This prevents the generic onerror handler from overwriting it.
    let gotServerError = false;

    ws.onclose = (ev) => {
      if (!gotServerError && ev.code !== 1000) {
        if (ev.code === 4401) {
          setErrorMsg('未登录或登录已过期，请刷新页面重新登录');
        } else if (ev.code === 4404) {
          setErrorMsg('服务器不存在或不可访问');
        } else if (ev.reason) {
          setErrorMsg(ev.reason);
        }
      }
      setStatus((prev) => (prev === 'error' ? prev : 'closed'));
    };

    ws.onerror = () => {
      // Only set generic error if we haven't received a specific one from the server
      setErrorMsg((prev) => prev || 'WebSocket 连接失败，请检查网络或后端服务是否正常运行');
      setStatus('error');
    };

    // Send user input to server
    const inputDisp = term.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'input', data }));
      }
    });

    // Handle resize
    const resizeDisp = term.onResize(({ cols, rows }) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'resize', cols, rows }));
      }
    });

    // Ping to keep alive
    const pingTimer = setInterval(() => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: 'ping' }));
      }
    }, 25000);

    return () => {
      inputDisp.dispose();
      resizeDisp.dispose();
      clearInterval(pingTimer);
      try { ws.close(); } catch { /* ignore */ }
      try { term.dispose(); } catch { /* ignore */ }
      termRef.current = null;
      fitRef.current = null;
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab.id, tab.serverId, tab.mode, tab.screenSession, tab.initialCommand, reconnectKey]);

  // ---- Fit terminal when becoming active ----
  useEffect(() => {
    if (active && fitRef.current) {
      const timer = setTimeout(() => {
        try { fitRef.current?.fit(); } catch { /* ignore */ }
      }, 50);
      return () => clearTimeout(timer);
    }
  }, [active]);

  // ---- Resize observer ----
  useEffect(() => {
    if (!containerRef.current) return;
    const observer = new ResizeObserver(() => {
      if (resizeTimerRef.current) clearTimeout(resizeTimerRef.current);
      resizeTimerRef.current = setTimeout(() => {
        try { fitRef.current?.fit(); } catch { /* ignore */ }
      }, 100);
    });
    observer.observe(containerRef.current);
    return () => {
      observer.disconnect();
      if (resizeTimerRef.current) clearTimeout(resizeTimerRef.current);
    };
  }, []);

  // ---- Register terminal API when active ----
  useEffect(() => {
    if (!active) {
      registerApi(null);
      return;
    }
    registerApi({
      sendText: (text: string) => {
        const ws = wsRef.current;
        if (ws && ws.readyState === WebSocket.OPEN) {
          ws.send(JSON.stringify({ type: 'input', data: text }));
        }
      },
    });
    return () => { registerApi(null); };
  }, [active, registerApi]);

  // ---- Reconnect (triggers effect re-run via key change) ----
  function reconnect() {
    setReconnectKey((k) => k + 1);
  }

  const labelParts: string[] = [serverName];
  if (tab.mode === 'screen_existing') labelParts.push(tab.screenSession);
  if (tab.mode === 'screen_new') labelParts.push('新建 screen');

  return (
    <div className={`sw-term-session${active ? ' active' : ''}`}>
      <div className="sw-term-toolbar">
        <span className="sw-term-label">
          <SquareTerminal size={13} />
          {labelParts.join(' · ')}
        </span>
        <span className={`sw-term-status sw-term-status-${status}`}>
          {status === 'connecting' && <><Loader2 size={12} className="spin" /> 连接中</>}
          {status === 'connected' && <><span className="sw-dot ok" /> 已连接</>}
          {status === 'error' && <><AlertCircle size={12} /> 错误</>}
          {status === 'closed' && <><span className="sw-dot closed" /> 已断开</>}
        </span>
        {(status === 'closed' || status === 'error') && (
          <button className="sw-btn sw-btn-sm sw-btn-ghost" onClick={reconnect} type="button">
            <RotateCw size={12} /> 重连
          </button>
        )}
      </div>
      <div className="sw-term-wrapper">
        <div ref={containerRef} className="sw-term-container" />
        {status === 'error' && errorMsg && (
          <div className="sw-term-error-overlay">
            <AlertCircle size={24} />
            <span>{errorMsg}</span>
            <button className="sw-btn sw-btn-sm sw-btn-primary" onClick={reconnect} type="button">
              <RotateCw size={12} /> 重连
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
