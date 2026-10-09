import { useEffect, useState } from "react";
import { apiGet, apiPost, apiPut } from "../../../../frontend/src/api/client";
import { API, type Service } from "../types";
import { Field, Panel } from "./shared";

export function HealthSettings({
  services,
  onChanged,
  onError,
}: {
  services: Service[];
  onChanged: () => void;
  onError: (reason: unknown) => void;
}) {
  const [settings, setSettings] = useState<{
    checkIntervalSeconds: number;
    emailRecipients: string[];
    confirmCount: number;
    repeatIntervalSeconds: number;
    maxRepeatCount: number;
  } | null>(null);
  useEffect(() => {
    void apiGet<{ settings: NonNullable<typeof settings> }>(
      `${API}/health/settings`,
    )
      .then((result) => setSettings(result.settings))
      .catch(onError);
  }, [onError]);
  if (!settings) return <section className="sn-panel">加载健康设置…</section>;
  return (
    <section className="sn-panel">
      <Panel
        title="健康度告警设置"
        subtitle={`已启用 HTTP 健康检测：${services.filter((item) => item.healthEnabled).length} 个服务`}
        action={
          <button
            className="secondary-button"
            onClick={() =>
              void apiPost(`${API}/health/checks`, {})
                .then(onChanged)
                .catch(onError)
            }
          >
            立即检测全部
          </button>
        }
      />
      <form
        className="sn-form-grid"
        onSubmit={(event) => {
          event.preventDefault();
          void apiPut(`${API}/health/settings`, settings)
            .then(onChanged)
            .catch(onError);
        }}
      >
        <Field label="检测间隔（秒）">
          <input
            type="number"
            min="60"
            max="86400"
            value={settings.checkIntervalSeconds}
            onChange={(event) =>
              setSettings({
                ...settings,
                checkIntervalSeconds: Number(event.target.value),
              })
            }
          />
        </Field>
        <Field label="连续失败确认次数">
          <input
            type="number"
            min="1"
            max="20"
            value={settings.confirmCount}
            onChange={(event) =>
              setSettings({
                ...settings,
                confirmCount: Number(event.target.value),
              })
            }
          />
        </Field>
        <Field label="重复告警冷却（秒）">
          <input
            type="number"
            min="0"
            value={settings.repeatIntervalSeconds}
            onChange={(event) =>
              setSettings({
                ...settings,
                repeatIntervalSeconds: Number(event.target.value),
              })
            }
          />
        </Field>
        <Field label="最大重复告警次数">
          <input
            type="number"
            min="0"
            value={settings.maxRepeatCount}
            onChange={(event) =>
              setSettings({
                ...settings,
                maxRepeatCount: Number(event.target.value),
              })
            }
          />
        </Field>
        <Field label="邮件收件人" full>
          <textarea
            value={settings.emailRecipients.join("\n")}
            onChange={(event) =>
              setSettings({
                ...settings,
                emailRecipients: event.target.value
                  .split(/[\n,]/)
                  .map((value) => value.trim())
                  .filter(Boolean),
              })
            }
          />
        </Field>
        <div className="sn-form-actions">
          <button className="primary-button">保存健康设置</button>
        </div>
      </form>
    </section>
  );
}
