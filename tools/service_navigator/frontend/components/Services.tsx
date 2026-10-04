import { type FormEvent, useState } from "react";
import { HeartPulse, Pencil, Radar, Trash2 } from "lucide-react";
import { apiGet, apiPost } from "../../../../frontend/src/api/client";
import { API, type Run, type Service, type Target } from "../types";
import { Field, Modal, Progress } from "./shared";

export function TargetPool({
  targets,
  services,
  active,
  onScan,
  onEditTarget,
  onDeleteTarget,
  onEditService,
}: {
  targets: Target[];
  services: Service[];
  active?: Run;
  onScan: (id?: string) => void;
  onEditTarget: (item: Target) => void;
  onDeleteTarget: (item: Target) => void;
  onEditService: (item: Service) => void;
}) {
  const [health, setHealth] = useState<Service | null>(null);
  const [snapshots, setSnapshots] = useState<
    {
      checkedAt: string;
      status: string;
      statusCode: number | null;
      latencyMs: number | null;
      error: string;
    }[]
  >([]);
  const openHealth = async (item: Service) => {
    try {
      const result = await apiGet<{ snapshots: typeof snapshots }>(
        `${API}/services/${item.id}/health/snapshots`,
      );
      setSnapshots(result.snapshots);
      setHealth(item);
    } catch {
      setSnapshots([]);
      setHealth(item);
    }
  };
  return (
    <>
      {!targets.length && <p className="sn-empty">尚未添加扫描目标。</p>}
      {targets.map((target) => {
        const targetServices = services.filter((item) => item.targetId === target.id);
        return (
        <section key={target.id} className="sn-target-service-group">
          <header className="sn-target-service-heading">
            <div>
              <h3>{target.label}<small>{target.address}</small></h3>
              <p>{targetServices.length} 个已发现服务{target.customPorts ? ` · 额外端口：${target.customPorts}` : ""}</p>
              {active?.targetId === target.id && <Progress run={active} />}
            </div>
            <div className="sn-target-service-actions">
              <button className="sn-icon-button" disabled={!!active} onClick={() => onScan(target.id)} title="扫描此目标" aria-label={`扫描 ${target.label}`}><Radar size={15} /></button>
              <button className="sn-icon-button" onClick={() => onEditTarget(target)} title="编辑目标" aria-label={`编辑 ${target.label}`}><Pencil size={14} /></button>
              <button className="sn-icon-button danger" onClick={() => onDeleteTarget(target)} title="删除目标" aria-label={`删除 ${target.label}`}><Trash2 size={14} /></button>
            </div>
          </header>
          <table className="sn-table">
            <thead>
              <tr>
                <th>服务</th>
                <th>端点</th>
                <th>状态</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {targetServices.length ? targetServices.map((item) => (
                  <tr key={item.id}>
                    <td>
                      <strong>
                        {item.displayName || item.httpTitle || item.serviceName}
                      </strong>
                      <small>
                        {item.serviceType === "http" ? "HTTP 服务" : "端口服务"}
                      </small>
                    </td>
                    <td>
                      <code>
                        {item.protocol}/{item.port}
                      </code>
                    </td>
                    <td>
                      <span
                        className={`sn-badge ${item.healthStatus === "healthy" ? "green" : item.healthStatus === "unhealthy" ? "red" : "muted"}`}
                      >
                        {item.healthEnabled
                          ? item.healthStatus === "healthy"
                            ? "健康"
                            : item.healthStatus === "unhealthy"
                              ? "异常"
                              : "尚未检测"
                          : item.state}
                      </span>
                    </td>
                    <td>
                      {item.healthEnabled && (
                        <button
                          className="sn-btn-link"
                          onClick={() => void openHealth(item)}
                        >
                          <HeartPulse size={14} />
                          检测
                        </button>
                      )}
                      <button
                        className="sn-btn-link"
                        onClick={() => onEditService(item)}
                      >
                        <Pencil size={14} />
                        整理
                      </button>
                    </td>
                  </tr>
                )) : <tr><td className="sn-target-service-empty" colSpan={4}>尚未发现服务，扫描该目标后会显示在这里。</td></tr>}
            </tbody>
          </table>
        </section>
        );
      })}
      {health && (
        <Modal
          title={`${health.displayName || health.serviceName} · 健康度`}
          onClose={() => setHealth(null)}
        >
          <div className="sn-health-dialog">
            <div className="sn-health-dialog-current">
              <span
                className={`sn-badge ${health.healthStatus === "healthy" ? "green" : health.healthStatus === "unhealthy" ? "red" : "amber"}`}
              >
                {health.healthStatus === "healthy"
                  ? "健康"
                  : health.healthStatus === "unhealthy"
                    ? "异常"
                    : "尚未检测"}
              </span>
              <button
                className="secondary-button"
                onClick={() =>
                  void apiPost(
                    `${API}/services/${health.id}/health/check`,
                    {},
                  ).then(() => openHealth(health))
                }
              >
                立即检测
              </button>
            </div>
            <h3>最近快照</h3>
            <div className="sn-snapshot-list">
              {snapshots.length ? (
                snapshots.map((snapshot, index) => (
                  <div key={`${snapshot.checkedAt}-${index}`}>
                    <span className="sn-badge">{snapshot.status}</span>
                    <span>
                      <strong>
                        {new Date(snapshot.checkedAt).toLocaleString()}
                      </strong>
                      <small>
                        {snapshot.statusCode
                          ? `HTTP ${snapshot.statusCode}`
                          : snapshot.error || "无状态码"}{" "}
                        · {snapshot.latencyMs ?? "—"} ms
                      </small>
                    </span>
                  </div>
                ))
              ) : (
                <p className="sn-muted">暂无快照。</p>
              )}
            </div>
          </div>
        </Modal>
      )}
    </>
  );
}

export function ServiceModal({
  service,
  targets,
  setService,
  onClose,
  onSubmit,
}: {
  service: Service;
  targets: Target[];
  setService: (value: Service) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const http = service.serviceType === "http";
  const target = targets.find((item) => item.id === service.targetId);
  const templates = {
    generic: { label: "通用端口服务", description: "通过指定地址和端口访问该服务。", command: `${target?.address || "<host>"}:${service.port}` },
    ssh: { label: "SSH", description: "通过 SSH 安全远程登录服务器。", command: `ssh -p ${service.port} <user>@${target?.address || "<host>"}` },
    sftp: { label: "SFTP", description: "通过 SFTP 安全传输文件。", command: `sftp -P ${service.port} <user>@${target?.address || "<host>"}` },
    rdp: { label: "远程桌面（RDP）", description: "通过远程桌面客户端连接此主机。", command: `xfreerdp /v:${target?.address || "<host>"}:${service.port} /u:<user>` },
    vnc: { label: "VNC", description: "通过 VNC 客户端查看和控制远程桌面。", command: `vncviewer ${target?.address || "<host>"}:${service.port}` },
    ftp: { label: "FTP", description: "通过 FTP 客户端传输文件。", command: `ftp ${target?.address || "<host>"} ${service.port}` },
    smb: { label: "SMB 文件共享", description: "通过 SMB 客户端访问共享文件。", command: `smbclient //${target?.address || "<host>"}/<share> -p ${service.port} -U <user>` },
  } as const;
  return (
    <Modal title="整理服务" onClose={onClose}>
      <form className="sn-form-grid" onSubmit={onSubmit}>
        <Field label="展示名称">
          <input
            value={service.displayName}
            onChange={(event) =>
              setService({ ...service, displayName: event.target.value })
            }
          />
        </Field>
        <Field label="服务类型">
          <select
            value={service.serviceType}
            onChange={(event) =>
              setService({
                ...service,
                serviceType: event.target.value as Service["serviceType"],
              })
            }
          >
            <option value="http">HTTP 服务</option>
            <option value="port">端口服务</option>
          </select>
        </Field>
        {http ? (
          <>
            <Field label="网页导航 URL" full>
              <input
                value={service.navigationUrl}
                onChange={(event) =>
                  setService({ ...service, navigationUrl: event.target.value })
                }
                placeholder={service.detectedUrl}
              />
            </Field>
            <div className="sn-health-row">
              <label className="sn-check">
                <input
                  type="checkbox"
                  checked={service.healthEnabled}
                  onChange={(event) =>
                    setService({
                      ...service,
                      healthEnabled: event.target.checked,
                    })
                  }
                />
                启用 HTTP 健康检测
              </label>
              {service.healthEnabled && (
                <Field label="独立健康检查 URL">
                  <input
                    value={service.healthUrl}
                    onChange={(event) =>
                      setService({ ...service, healthUrl: event.target.value })
                    }
                  />
                </Field>
              )}
            </div>
          </>
        ) : (
          <>
            <Field label="连接模板">
              <select
                value={service.serviceTemplate}
                onChange={(event) => {
                  const serviceTemplate = event.target.value as Service["serviceTemplate"];
                  const preset = templates[serviceTemplate];
                  setService({ ...service, serviceTemplate, description: preset.description, commandDescription: preset.command });
                }}
              >
                {Object.entries(templates).map(([id, preset]) => <option key={id} value={id}>{preset.label}</option>)}
              </select>
            </Field>
            <Field label="命令说明" full>
              <textarea
                value={service.commandDescription}
                onChange={(event) =>
                  setService({ ...service, commandDescription: event.target.value })
                }
              />
            </Field>
          </>
        )}
        <Field label="描述" full>
          <textarea
            value={service.description}
            onChange={(event) =>
              setService({ ...service, description: event.target.value })
            }
          />
        </Field>
        <div className="sn-form-actions">
          <button className="secondary-button" type="button" onClick={onClose}>
            取消
          </button>
          <button className="primary-button">保存</button>
        </div>
      </form>
    </Modal>
  );
}
