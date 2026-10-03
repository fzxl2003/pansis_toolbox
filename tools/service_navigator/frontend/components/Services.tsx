import { type FormEvent, useState } from "react";
import { HeartPulse, Pencil } from "lucide-react";
import { apiGet, apiPost } from "../../../../frontend/src/api/client";
import { API, type Service, type Target } from "../types";
import { Field, Modal } from "./shared";

export function ServicesByTarget({
  targets,
  services,
  onEdit,
}: {
  targets: Target[];
  services: Service[];
  onEdit: (item: Service) => void;
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
      {targets.map((target) => (
        <section key={target.id} className="sn-target-service-group">
          <h3>
            {target.label}
            <small>{target.address}</small>
          </h3>
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
              {services
                .filter((item) => item.targetId === target.id)
                .map((item) => (
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
                        onClick={() => onEdit(item)}
                      >
                        <Pencil size={14} />
                        整理
                      </button>
                    </td>
                  </tr>
                ))}
            </tbody>
          </table>
        </section>
      ))}
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
  setService,
  onClose,
  onSubmit,
}: {
  service: Service;
  setService: (value: Service) => void;
  onClose: () => void;
  onSubmit: (event: FormEvent) => void;
}) {
  const http = service.serviceType === "http";
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
          <Field label="连接命令" full>
            <input
              value={service.connectionCommand}
              onChange={(event) =>
                setService({
                  ...service,
                  connectionCommand: event.target.value,
                })
              }
            />
          </Field>
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
