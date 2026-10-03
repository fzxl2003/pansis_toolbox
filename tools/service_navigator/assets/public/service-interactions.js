import { data, search } from "./state.js";
import { escapeHtml, serviceName } from "./utils.js";
import { closeModal, showModal } from "./modal.js";

function cookieKey(item) {
  return `sn-nav-default-${location.pathname.replace(/[^a-z0-9]/gi, "_")}-${item.id}`;
}
function getDefault(item) {
  const prefix = `${encodeURIComponent(cookieKey(item))}=`;
  const value = document.cookie
    .split("; ")
    .find((row) => row.startsWith(prefix));
  if (!value) return null;
  try {
    const parsed = JSON.parse(decodeURIComponent(value.slice(prefix.length)));
    return parsed.revision === item.preferenceRevision ? parsed : null;
  } catch {
    return null;
  }
}
function saveDefault(item, candidate) {
  document.cookie = `${encodeURIComponent(cookieKey(item))}=${encodeURIComponent(JSON.stringify({ revision: item.preferenceRevision, id: candidate.id }))}; Max-Age=31536000; Path=${location.pathname}; SameSite=Lax`;
}
function clearDefault(item) {
  document.cookie = `${encodeURIComponent(cookieKey(item))}=; Max-Age=0; Path=${location.pathname}; SameSite=Lax`;
}
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
async function openItem(item) {
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
  const defaultChoice = getDefault(item);
  const defaultCandidate =
    defaultChoice &&
    candidates.find((candidate) => candidate.id === defaultChoice.id);
  if (defaultCandidate) {
    window.open(defaultCandidate.url, "_blank", "noopener");
    return;
  }
  const statuses = await Promise.all(
    candidates.map(async (candidate) => ({
      ...candidate,
      status: await probe(candidate),
    })),
  );
  const usable = statuses.filter(
    (candidate) => candidate.status === "reachable",
  );
  if (usable.length === 1) {
    window.open(usable[0].url, "_blank", "noopener");
    return;
  }
  openHttpModal(item, statuses);
}
function openHttpModal(item, candidates) {
  showModal(
    `<h2>${escapeHtml(item.name)}</h2><p class="sn-modal-note">以下检测来自当前浏览器；无法检测的地址仍可手动打开。</p><div class="sn-candidate-list"></div><button class="sn-clear-default" type="button">清除我的默认跳转</button>`,
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
    row.innerHTML = `<div><strong></strong><small></small><em class="${candidate.status}">${status}</em></div><label><input type="checkbox">默认跳转</label><button type="button">打开</button>`;
    row.querySelector("strong").textContent = candidate.name;
    row.querySelector("small").textContent = candidate.url;
    row
      .querySelector("button")
      .addEventListener("click", () =>
        chooseHttp(
          item,
          candidate,
          Boolean(row.querySelector("input").checked),
        ),
      );
    list.append(row);
  }
  modalContent
    .querySelector(".sn-clear-default")
    .addEventListener("click", () => {
      clearDefault(item);
      closeModal();
    });
}
function chooseHttp(item, candidate, shouldDefault) {
  if (shouldDefault) saveDefault(item, candidate);
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
    const command =
      service.command ||
      service.connectionCommand ||
      `${service.targetAddress}:${service.port}`;
    row.innerHTML =
      '<strong></strong><small></small><code></code><button type="button">复制命令</button>';
    row.querySelector("strong").textContent = service.serviceName || "端口服务";
    row.querySelector("small").textContent =
      `${service.targetLabel || ""} · ${service.targetAddress}:${service.port}`;
    row.querySelector("code").textContent = command;
    row.querySelector("button").addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(command);
        row.querySelector("button").textContent = "已复制";
      } catch {
        window.prompt("复制连接命令", command);
      }
    });
    list.append(row);
  }
}
