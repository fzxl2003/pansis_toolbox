// ============================================================
// Servers Overview Panel — Docker Manager
// ============================================================

import { Server } from 'lucide-react';
import type { AuthUser } from '../../../frontend/src/api/auth';
import { permColor, permLabel } from './utils';
import type { DmServer } from './types';

export function ServersOverviewPanel({ servers, me }: { servers: DmServer[]; me: AuthUser }) {
  return (
    <div style={{ display: 'grid', gap: 16 }}>
      {servers.length === 0 ? (
        <div className="dm-empty"><Server size={32} /> 暂无可访问的服务器</div>
      ) : (
        <div className="dm-card-grid">
          {servers.map((s) => (
            <div key={s.id} className="dm-card">
              <div className="dm-card-header">
                <span className="dm-card-title">
                  <Server size={15} style={{ display: 'inline', marginRight: 6, verticalAlign: 'middle' }} />
                  {s.name}
                </span>
                <span className={`dm-perm-badge ${permColor(s.permissionLevel)}`}>{permLabel(s.permissionLevel)}</span>
              </div>
              <div className="dm-card-meta">
                <span>🖥 {s.host}:{s.port}</span>
                <span>👤 {s.sshUsername}</span>
                <span style={{ color: '#94a3b8', fontSize: 12 }}>添加于 {s.createdAt.slice(0, 10)}</span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
