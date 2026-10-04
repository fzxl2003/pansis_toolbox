import { data, modalContent, search } from "./state.js";
import { escapeHtml, serviceName } from "./utils.js";
import { closeModal, showModal } from "./modal.js";

function httpCandidates(item) {
  const seen = new Set();
  return item.services.flatMap((service) => {
    const url =
      service.url || service.navigationUrl || service.detectedUrl || "";
    if (!url || seen.has(url)) return [];
    seen.add(url);
    return [
      {
        id: `${service.id}:${url}`,
        url,
        name: serviceName(service),
        target: `${service.targetLabel || ""} · ${service.targetAddress || ""}`,
      },
    ];
  });
}
async function probe(candidate) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 3500);
  try {
    await fetch(candidate.url, {
      method: "GET",
      mode: "no-cors",
      cache: "no-store",
      redirect: "follow",
      signal: controller.signal,
    });
    return "reachable";
  } catch {
    return location.protocol === "https:" && candidate.url.startsWith("http:")
      ? "unknown"
      : "failed";
  } finally {
    clearTimeout(timer);
  }
}
export async function openItem(item) {
  if (item.serviceType !== "http") {
    openPortModal(item);
    return;
  }
  const candidates = httpCandidates(item);
  if (!candidates.length) {
    showModal(
      `<h2>${escapeHtml(item.name)}</h2><p class="sn-modal-note">该图标没有可用的 HTTP 导航地址。</p>`,
    );
    return;
  }
  if (item.services.length === 1) {
    window.open(candidates[0].url, "_blank", "noopener");
    return;
  }
  const statuses = await Promise.all(
    candidates.map(async (candidate) => ({
      ...candidate,
      status: await probe(candidate),
    })),
  );
  openHttpModal(item, statuses);
}
function openHttpModal(item, candidates) {
  showModal(
    `<h2>${escapeHtml(item.name)}</h2><div class="sn-candidate-list"></div>`,
  );
  const list = modalContent.querySelector(".sn-candidate-list");
  for (const candidate of candidates) {
    const row = document.createElement("div");
    row.className = "sn-candidate";
    const status =
      candidate.status === "reachable"
        ? "可达"
        : candidate.status === "failed"
          ? "不可达"
          : "无法检测";
    row.innerHTML = `<div><strong></strong><small></small><em class="${candidate.status}">${status}</em></div><button type="button">打开</button>`;
    row.querySelector("strong").textContent = candidate.name;
    row.querySelector("small").textContent = candidate.url;
    row
      .querySelector("button")
      .addEventListener("click", () => chooseHttp(candidate));
    list.append(row);
  }
}
function chooseHttp(candidate) {
  window.open(candidate.url, "_blank", "noopener");
  closeModal();
}
function openPortModal(item) {
  showModal(
    `<h2>${escapeHtml(item.name)}</h2><div class="sn-port-list"></div>`,
  );
  const list = modalContent.querySelector(".sn-port-list");
  for (const service of item.services) {
    const row = document.createElement("div");
    row.className = "sn-port-row";
    const command = service.commandDescription || `${service.targetAddress}:${service.port}`;
    row.innerHTML =
      '<strong></strong><small></small><code></code><button type="button">复制命令说明</button>';
    row.querySelector("strong").textContent =
      service.name || service.displayName || service.serviceName || "端口服务";
    row.querySelector("small").textContent =
      `${service.targetLabel || ""} · ${service.targetAddress}:${service.port}`;
    if (service.description) {
      const description = document.createElement("p");
      description.className = "sn-service-description";
      description.textContent = service.description;
      row.querySelector("small").after(description);
    }
    row.querySelector("code").textContent = command;
    row.querySelector("button").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(command);
        row.querySelector("button").textContent = "已复制";
      } catch {
        window.prompt("复制命令说明", command);
      }
    });
    list.append(row);
  }
}
