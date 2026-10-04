import { breakpoint, content, currentPage, data } from "./state.js";
import { render } from "./render.js";
import { ownerApi } from "./owner-api.js";
import { iconPreviewNode, openGlobalIconForm } from "./icon-library.js";

const SIZES = {
  small: [1, 1],
  medium: [2, 2],
  large: [4, 4],
  wide: [4, 2],
};

let editingPageId = "";
let draggedItemId = "";
let draggedPointerOffset = { x: 0, y: 0 };
let resizeState = null;
let suppressCardClickUntil = 0;
let inspectorDismissListener = null;

function currentCanvasPage() {
  return (data.pages || []).find((page) => page.id === currentPage) || null;
}

function activePage() {
  return editingPageId === currentPage ? currentCanvasPage() : null;
}

export function isPageCanvasEditing() {
  return Boolean(activePage());
}

export function refreshCanvasEditButton() {
  const button = document.querySelector("[data-page-canvas-edit]");
  if (!button) return;
  const page = currentCanvasPage();
  button.hidden = !page;
  button.textContent = isPageCanvasEditing() ? "完成编辑" : "编辑页面";
}

export function startPageCanvasEditing() {
  const page = currentCanvasPage();
  if (!page) return;
  if (breakpoint() !== 16) {
    alert("请在桌面宽度下编辑页面布局；窄屏会自动适配。");
    return;
  }
  editingPageId = page.id;
  document.body.classList.add("sn-page-canvas-editing");
  refreshCanvasEditButton();
  renderCanvasEditor();
}

export function stopPageCanvasEditing() {
  if (!editingPageId) return;
  editingPageId = "";
  draggedItemId = "";
  draggedPointerOffset = { x: 0, y: 0 };
  resizeState = null;
  document.body.classList.remove("sn-page-canvas-editing");
  removeEditorChrome();
  refreshCanvasEditButton();
  render();
}

export function togglePageCanvasEditing() {
  if (isPageCanvasEditing()) stopPageCanvasEditing();
  else startPageCanvasEditing();
}

function removeEditorChrome() {
  document.querySelector(".sn-canvas-toolbar")?.remove();
  document.querySelector(".sn-canvas-add-panel")?.remove();
  closeCanvasInspector();
  content?.querySelector(".sn-canvas-drop-preview")?.remove();
}

function closeCanvasInspector() {
  if (inspectorDismissListener) {
    document.removeEventListener("pointerdown", inspectorDismissListener);
    inspectorDismissListener = null;
  }
  document.querySelector(".sn-canvas-inspector")?.remove();
  document.querySelectorAll(".sn-nav-icon.is-selected").forEach((node) =>
    node.classList.remove("is-selected"),
  );
}

function placement(item) {
  return item.layouts?.["16"] || { x: 0, y: 0 };
}

function fits(page, itemId, size, x, y) {
  const [width, height] = SIZES[size];
  if (x < 0 || y < 0 || x + width > 16) return false;
  for (const item of page.items) {
    if (item.id === itemId) continue;
    const [otherWidth, otherHeight] = SIZES[item.size] || SIZES.small;
    const other = placement(item);
    const overlaps =
      x < other.x + otherWidth &&
      x + width > other.x &&
      y < other.y + otherHeight &&
      y + height > other.y;
    if (overlaps) return false;
  }
  return true;
}

function canvasPayload(page, changed = {}) {
  return page.items.map((item) => {
    const next = changed[item.id] || {};
    const point = next.point || placement(item);
    return {
      itemId: item.id,
      size: next.size || item.size,
      x: point.x,
      y: point.y,
    };
  });
}

async function saveCanvas(page, changed) {
  const payload = canvasPayload(page, changed);
  const previous = new Map();
  for (const item of page.items) {
    const next = changed[item.id];
    if (!next) continue;
    previous.set(item.id, { size: item.size, layouts: item.layouts });
    if (next.size) item.size = next.size;
    if (next.point) {
      item.layouts = { ...(item.layouts || {}), "16": next.point };
    }
  }
  // Render before the request completes so the card never snaps back to its old cell.
  renderCanvasEditor();
  try {
    await ownerApi(`/navigation/pages/${page.id}/canvas`, "PUT", { items: payload });
  } catch (error) {
    for (const item of page.items) {
      const original = previous.get(item.id);
      if (original) {
        item.size = original.size;
        item.layouts = original.layouts;
      }
    }
    renderCanvasEditor();
    throw error;
  }
}

function gridMetrics(grid) {
  const style = getComputedStyle(grid);
  const rect = grid.getBoundingClientRect();
  const gap = Number.parseFloat(style.columnGap) || 0;
  return {
    rect,
    gap,
    rowGap: Number.parseFloat(style.rowGap) || gap,
    rowHeight: Number.parseFloat(style.gridAutoRows) || 66,
    cellWidth: (rect.width - gap * 15) / 16,
  };
}

function pointFromDrop(grid, event, item) {
  const { rect, gap, rowGap, rowHeight, cellWidth } = gridMetrics(grid);
  const [width] = SIZES[item.size] || SIZES.small;
  return {
    x: Math.max(0, Math.min(16 - width, Math.floor((event.clientX - rect.left - draggedPointerOffset.x) / (cellWidth + gap)))),
    y: Math.max(0, Math.floor((event.clientY - rect.top - draggedPointerOffset.y) / (rowHeight + rowGap))),
  };
}

function showDropPreview(grid, item, size, point, valid) {
  let preview = grid.querySelector(".sn-canvas-drop-preview");
  if (!preview) {
    preview = document.createElement("div");
    preview.className = "sn-canvas-drop-preview";
    preview.setAttribute("aria-hidden", "true");
    grid.append(preview);
  }
  const [width, height] = SIZES[size] || SIZES.small;
  preview.classList.toggle("is-invalid", !valid);
  preview.style.gridColumn = `${point.x + 1} / span ${width}`;
  preview.style.gridRow = `${point.y + 1} / span ${height}`;
}

function clearDropPreview(grid) {
  grid?.querySelector(".sn-canvas-drop-preview")?.remove();
}

function nearestSize(width, height) {
  return Object.entries(SIZES).reduce(
    (winner, [size, span]) => {
      const distance = (span[0] - width) ** 2 + (span[1] - height) ** 2;
      return distance < winner.distance ? { size, distance } : winner;
    },
    { size: "small", distance: Infinity },
  ).size;
}

function resizeSizeFromPointer(grid, item, event) {
  const { cellWidth, gap, rowHeight, rowGap } = gridMetrics(grid);
  const card = content?.querySelector(`[data-item-id="${item.id}"]`);
  const rect = card?.getBoundingClientRect();
  if (!rect) return item.size;
  const width = Math.max(1, Math.min(4, Math.ceil((event.clientX - rect.left) / (cellWidth + gap))));
  const height = Math.max(1, Math.min(4, Math.ceil((event.clientY - rect.top) / (rowHeight + rowGap))));
  return nearestSize(width, height);
}

function iconUrl(icon) {
  if (icon.iconSource === "custom") return icon.iconUrl || "";
  if (icon.iconSource === "favicon")
    return (data.services || []).find((service) => service.id === icon.faviconServiceId)
      ?.faviconUrl || "";
  return "";
}

function hydrateItem(item, icon) {
  return {
    ...item,
    iconId: icon.id,
    iconUrl: iconUrl(icon),
    iconSource: icon.iconSource,
    iconText: icon.iconText,
    iconColor: icon.iconColor,
    serviceType: icon.services?.[0]?.serviceType || "port",
    services: icon.services || [],
  };
}

function syncGlobalIcon(updated) {
  const icon = (data.icons || []).find((entry) => entry.id === updated.id);
  if (icon) Object.assign(icon, updated);
  for (const page of data.pages || []) {
    for (const item of page.items || []) {
      if (item.iconId !== updated.id) continue;
      Object.assign(item, hydrateItem({ ...item, name: updated.name }, icon || updated));
    }
  }
  renderCanvasEditor();
}

function showInspector(card, page, item) {
  closeCanvasInspector();
  card.classList.add("is-selected");
  const inspector = document.createElement("section");
  inspector.className = "sn-canvas-inspector";
  inspector.innerHTML =
    '<strong>卡片设置</strong><div class="sn-canvas-sizes"></div><button type="button" data-edit-global-icon>编辑全局图标</button>';
  const sizes = inspector.querySelector(".sn-canvas-sizes");
  for (const [size, label] of [
    ["small", "小 1×1"],
    ["medium", "中 2×2"],
    ["large", "大 4×4"],
    ["wide", "宽 4×2"],
  ]) {
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = label;
    button.classList.toggle("active", item.size === size);
    button.addEventListener("click", async () => {
      if (item.size === size) return;
      const point = placement(item);
      if (!fits(page, item.id, size, point.x, point.y)) {
        alert("这个尺寸会占用旁边的卡片，请先腾出空间。");
        return;
      }
      try {
        await saveCanvas(page, { [item.id]: { size, point } });
      } catch (error) {
        alert(error.message || "尺寸保存失败");
      }
    });
    sizes.append(button);
  }
  inspector
    .querySelector("[data-edit-global-icon]")
    .addEventListener("click", () => {
      const icon = (data.icons || []).find((entry) => entry.id === item.iconId);
      if (icon) {
        closeCanvasInspector();
        openGlobalIconForm(icon, false, syncGlobalIcon);
      }
    });
  const rect = card.getBoundingClientRect();
  inspector.style.left = `${Math.min(window.innerWidth - 250, Math.max(12, rect.left))}px`;
  inspector.style.top = `${Math.min(window.innerHeight - 180, rect.bottom + 8)}px`;
  document.body.append(inspector);
  inspectorDismissListener = (event) => {
    if (inspector.contains(event.target) || card.contains(event.target)) return;
    closeCanvasInspector();
  };
  document.addEventListener("pointerdown", inspectorDismissListener);
}

function renderAddPanel(page) {
  const panel = document.createElement("section");
  panel.className = "sn-canvas-add-panel";
  panel.innerHTML = "<strong>添加图标</strong><div></div>";
  const list = panel.querySelector("div");
  const addIconButton = (icon, container) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sn-canvas-add-icon";
    button.append(iconPreviewNode(icon));
    const label = document.createElement("span");
    label.textContent = icon.name;
    button.append(label);
    button.addEventListener("click", async () => {
      try {
        const result = await ownerApi("/navigation/items", "POST", {
          pageId: page.id,
          iconId: icon.id,
          size: "medium",
        });
        page.items.push(hydrateItem(result.item, icon));
        renderCanvasEditor();
      } catch (error) {
        alert(error.message || "添加图标失败");
      }
    });
    container.append(button);
  };
  const custom = (data.icons || []).filter((icon) => !icon.detectedServiceId);
  if (custom.length) {
    const group = document.createElement("section");
    group.className = "sn-canvas-add-target";
    group.innerHTML = "<strong>自定义图标</strong><div></div>";
    custom.forEach((icon) => addIconButton(icon, group.querySelector("div")));
    list.append(group);
  }
  const targetById = new Map((data.targetPages || []).map((target) => [target.targetId, target]));
  const grouped = new Map();
  for (const icon of (data.icons || []).filter((icon) => icon.detectedServiceId)) {
    const targetId = icon.services?.[0]?.targetId || "unassigned";
    if (!grouped.has(targetId)) grouped.set(targetId, []);
    grouped.get(targetId).push(icon);
  }
  const orderedIds = [
    ...(data.targetPages || []).map((target) => target.targetId).filter((id) => grouped.has(id)),
    ...[...grouped.keys()].filter((id) => !targetById.has(id)),
  ];
  for (const targetId of orderedIds) {
    const target = targetById.get(targetId);
    const icons = grouped.get(targetId);
    const group = document.createElement("section");
    group.className = "sn-canvas-add-target";
    group.innerHTML = "<strong></strong><small></small><div></div>";
    group.querySelector("strong").textContent = target?.name || icons[0].services?.[0]?.targetLabel || "未命名扫描目标";
    group.querySelector("small").textContent = target?.address || icons[0].services?.[0]?.targetAddress || "";
    icons.forEach((icon) => addIconButton(icon, group.querySelector("div")));
    list.append(group);
  }
  document.body.append(panel);
}

function renderToolbar(page) {
  const toolbar = document.createElement("section");
  toolbar.className = "sn-canvas-toolbar";
  toolbar.innerHTML =
    '<button type="button" data-canvas-add>＋ 添加图标</button><div class="sn-canvas-trash" data-canvas-trash>⌫ 拖到这里移除</div><button type="button" class="primary" data-canvas-done>完成编辑</button>';
  toolbar.querySelector("[data-canvas-add]").addEventListener("click", () => {
    document.querySelector(".sn-canvas-add-panel")?.remove();
    renderAddPanel(page);
  });
  toolbar.querySelector("[data-canvas-done]").addEventListener("click", stopPageCanvasEditing);
  const trash = toolbar.querySelector("[data-canvas-trash]");
  trash.addEventListener("dragover", (event) => {
    event.preventDefault();
    trash.classList.add("is-over");
  });
  trash.addEventListener("dragleave", () => trash.classList.remove("is-over"));
  trash.addEventListener("drop", async (event) => {
    event.preventDefault();
    trash.classList.remove("is-over");
    const itemId = event.dataTransfer.getData("text/plain") || draggedItemId;
    if (!itemId) return;
    try {
      await ownerApi(`/navigation/items/${itemId}`, "DELETE");
      page.items.splice(page.items.findIndex((item) => item.id === itemId), 1);
      renderCanvasEditor();
    } catch (error) {
      alert(error.message || "移除图标失败");
    }
  });
  document.body.append(toolbar);
}

function bindGrid(page) {
  const grid = content?.querySelector(".sn-icon-grid");
  if (!grid) return;
  for (const card of grid.querySelectorAll(".sn-nav-icon")) {
    const item = page.items.find((entry) => entry.id === card.dataset.itemId);
    if (!item) continue;
    card.draggable = true;
    card.addEventListener("click", (event) => {
      if (
        event.target.closest(".sn-canvas-resize") ||
        Date.now() < suppressCardClickUntil
      ) {
        event.preventDefault();
        event.stopImmediatePropagation();
        return;
      }
      event.preventDefault();
      event.stopImmediatePropagation();
      showInspector(card, page, item);
    }, true);
    card.addEventListener("dragstart", (event) => {
      draggedItemId = item.id;
      suppressCardClickUntil = Date.now() + 600;
      const rect = card.getBoundingClientRect();
      draggedPointerOffset = {
        x: Math.max(0, event.clientX - rect.left),
        y: Math.max(0, event.clientY - rect.top),
      };
      event.dataTransfer.effectAllowed = "move";
      event.dataTransfer.setData("text/plain", item.id);
      card.classList.add("is-dragging");
    });
    card.addEventListener("dragend", () => {
      card.classList.remove("is-dragging");
      suppressCardClickUntil = Date.now() + 400;
      draggedPointerOffset = { x: 0, y: 0 };
      clearDropPreview(grid);
    });
    const handle = document.createElement("span");
    handle.className = "sn-canvas-resize";
    handle.title = "拖动调整卡片尺寸";
    handle.setAttribute("aria-label", "拖动调整卡片尺寸");
    card.append(handle);
    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      event.stopPropagation();
      suppressCardClickUntil = Date.now() + 800;
      handle.setPointerCapture?.(event.pointerId);
      resizeState = { item, page, grid, size: item.size };
      const updatePreview = (moveEvent) => {
        if (!resizeState) return;
        const size = resizeSizeFromPointer(grid, item, moveEvent);
        const point = placement(item);
        resizeState.size = size;
        resizeState.valid = fits(page, item.id, size, point.x, point.y);
        showDropPreview(grid, item, size, point, resizeState.valid);
      };
      const completeResize = async () => {
        const state = resizeState;
        resizeState = null;
        suppressCardClickUntil = Date.now() + 400;
        window.removeEventListener("pointermove", updatePreview);
        window.removeEventListener("pointerup", completeResize);
        clearDropPreview(grid);
        if (!state || state.size === item.size) return;
        if (!state.valid) {
          alert("这个尺寸会占用旁边的卡片，请先腾出空间。");
          return;
        }
        try {
          await saveCanvas(page, { [item.id]: { size: state.size, point: placement(item) } });
        } catch (error) {
          alert(error.message || "尺寸保存失败");
        }
      };
      window.addEventListener("pointermove", updatePreview);
      window.addEventListener("pointerup", completeResize, { once: true });
      updatePreview(event);
    });
  }
  grid.addEventListener("dragover", (event) => {
    event.preventDefault();
    const item = page.items.find((entry) => entry.id === draggedItemId);
    if (!item) return;
    const metrics = gridMetrics(grid);
    // Keep a runway below the pointer so dragging near the bottom can extend
    // the canvas instead of being constrained by its previous content height.
    if (event.clientY > metrics.rect.bottom - metrics.rowHeight * 2) {
      grid.style.minHeight = `${grid.clientHeight + (metrics.rowHeight + metrics.rowGap) * 6}px`;
    }
    const point = pointFromDrop(grid, event, item);
    showDropPreview(grid, item, item.size, point, fits(page, item.id, item.size, point.x, point.y));
  });
  grid.addEventListener("dragleave", (event) => {
    if (!grid.contains(event.relatedTarget)) clearDropPreview(grid);
  });
  grid.addEventListener("drop", async (event) => {
    event.preventDefault();
    const itemId = event.dataTransfer.getData("text/plain") || draggedItemId;
    const item = page.items.find((entry) => entry.id === itemId);
    if (!item) return;
    const point = pointFromDrop(grid, event, item);
    const valid = fits(page, item.id, item.size, point.x, point.y);
    clearDropPreview(grid);
    if (!valid) return;
    try {
      await saveCanvas(page, { [item.id]: { point } });
    } catch (error) {
      alert(error.message || "位置保存失败");
    }
  });
}

function renderCanvasEditor() {
  if (!isPageCanvasEditing()) return;
  removeEditorChrome();
  render();
  const page = activePage();
  if (!page) return;
  bindGrid(page);
  renderToolbar(page);
}
