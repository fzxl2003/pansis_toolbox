import { breakpoint, content, currentPage, data, sizeSpan } from "./state.js";
import { healthLabel, serviceName } from "./utils.js";
import { openItem } from "./service-interactions.js";

export function render() {
  if (!content) return;
  const page =
    currentPage === "all"
      ? null
      : data.pages.find((item) => item.id === currentPage);
  content.replaceChildren();
  let count = 0;
  if (currentPage.startsWith("target:")) {
    const targetId = currentPage.slice(7);
    const target = (data.targetPages || []).find(
      (item) => item.targetId === targetId,
    );
    const section = document.createElement("section");
    section.className = "sn-target-group";
    section.innerHTML = "<header><h2></h2><small></small></header>";
    section.querySelector("h2").textContent = target?.name || "扫描目标";
    section.querySelector("small").textContent = target?.address || "";
    const grid = document.createElement("div");
    grid.className = "sn-all-service-grid";
    for (const item of (data.services || []).filter(
      (item) => item.targetId === targetId,
    )) {
      grid.append(serviceCard(item));
      count++;
    }
    section.append(grid);
    content.append(section);
  } else if (!page) {
    const groups = new Map();
    for (const item of data.services || []) {
      const key = item.targetId;
      if (!groups.has(key))
        groups.set(key, {
          label: item.targetLabel || "扫描目标",
          address: item.targetAddress || "",
          items: [],
        });
      groups.get(key).items.push(item);
    }
    for (const group of groups.values()) {
      const section = document.createElement("section");
      section.className = "sn-target-group";
      section.innerHTML = "<header><h2></h2><small></small></header>";
      section.querySelector("h2").textContent = group.label;
      section.querySelector("small").textContent = group.address;
      const grid = document.createElement("div");
      grid.className = "sn-all-service-grid";
      for (const item of group.items) {
        grid.append(serviceCard(item));
        count++;
      }
      section.append(grid);
      content.append(section);
    }
  } else {
    const grid = document.createElement("div");
    grid.className = "sn-icon-grid";
    grid.dataset.columns = String(breakpoint());
    for (const item of page.items || []) {
      const card = navigationCard(item);
      const layout = item.layouts?.[String(breakpoint())] || { x: 0, y: 0 };
      const span = sizeSpan[item.size] || sizeSpan.small;
      card.style.gridColumn = `${layout.x + 1} / span ${span[0]}`;
      card.style.gridRow = `${layout.y + 1} / span ${span[1]}`;
      grid.append(card);
      count++;
    }
    if (!count)
      grid.innerHTML = '<p class="sn-empty">这个页面还没有导航图标。</p>';
    content.append(grid);
  }
  if (!count)
    content.innerHTML = '<p class="sn-empty">暂时没有可展示的服务。</p>';
}

function navigationCard(item) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = `sn-nav-icon sn-size-${item.size}`;
  card.dataset.itemId = item.id;
  card.title = item.name;
  card.setAttribute("aria-label", item.name);
  const icon = document.createElement("span");
  icon.className = "sn-nav-icon-image";
  if (item.iconUrl) {
    const image = document.createElement("img");
    image.src = item.iconUrl;
    image.alt = "";
    icon.append(image);
  } else {
    const fallback = document.createElement("span");
    fallback.className = "sn-nav-fallback";
    fallback.textContent =
      item.iconSource === "text" ? item.iconText || item.name.slice(0, 1) : "◌";
    fallback.style.color = item.iconSource === "text" ? item.iconColor || "" : "";
    icon.append(fallback);
  }
  card.append(icon);
  if (item.size === "small") {
    const label = document.createElement("span");
    label.className = "sn-nav-small-label";
    label.textContent = item.name;
    card.append(label);
  } else {
    const primary = item.services[0] || {};
    const body = document.createElement("span");
    body.className = "sn-nav-card-body";
    body.innerHTML = "<strong></strong><small></small>";
    body.querySelector("strong").textContent = item.name;
    body.querySelector("small").textContent =
      item.services.length > 1
        ? `${item.services.length} 个服务`
        : `${primary.protocol || "tcp"}/${primary.port || "—"} · ${item.serviceType === "http" ? "网页" : "端口"} 服务`;
    const status = document.createElement("em");
    status.textContent =
      primary.healthMonitored && primary.healthStatus === "healthy"
        ? "健康"
        : primary.healthMonitored && primary.healthStatus === "unhealthy"
          ? "异常"
          : primary.state === "offline"
            ? "离线"
            : "在线";
    card.append(body, status);
  }
  card.addEventListener("click", () => openItem(item));
  return card;
}

function serviceCard(item) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "sn-all-service";
  card.innerHTML =
    '<span class="sn-all-icon"></span><span><strong></strong><small></small></span><em></em>';
  const icon = card.querySelector(".sn-all-icon");
  if (item.faviconUrl) {
    const img = document.createElement("img");
    img.src = item.faviconUrl;
    img.alt = "";
    icon.append(img);
  } else icon.textContent = item.serviceType === "http" ? "◌" : "⌁";
  card.querySelector("strong").textContent = serviceName(item);
  card.querySelector("small").textContent =
    `${item.protocol || "tcp"}/${item.port} · ${item.serviceType === "http" ? "网页" : "端口"} 服务`;
  card.querySelector("em").textContent =
    healthLabel(item) || (item.state === "offline" ? "离线" : "在线");
  card.addEventListener("click", () =>
    openItem({
      id: `service-${item.id}`,
      name: serviceName(item),
      serviceType: item.serviceType,
      services: [item],
    }),
  );
  return card;
}
