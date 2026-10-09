import { initializeTheme } from "./public/theme.js";
import "./public/clock.js";
import "./public/search.js";
import {
  breakpoint,
  currentPage,
  dashboard,
  data,
  modal,
  readNavigationCookie,
  setCurrentPage,
} from "./public/state.js";
import { closeModal, showModal } from "./public/modal.js";
import { render } from "./public/render.js";
import { setPageEditing } from "./public/page-editing.js";
import { openOwnerEditor } from "./public/owner-editor.js";
import { bindPageControls } from "./public/page-controls.js";
import {
  isPageCanvasEditing,
  refreshCanvasEditButton,
  stopPageCanvasEditing,
  togglePageCanvasEditing,
} from "./public/page-canvas.js";
import { ownerApi } from "./public/owner-api.js";

function saveNavigationCookie(name, value) {
  document.cookie = `${name}=${encodeURIComponent(value)}; max-age=31536000; path=/; samesite=lax`;
}

const visiblePageIds = [
  ...(data.targetPages || []).filter((page) => page.visible).map((page) => page.id),
  ...(data.pages || []).filter((page) => page.visible).map((page) => page.id),
];
let pageStepper;

function updatePageStepper() {
  if (!pageStepper) return;
  const index = visiblePageIds.indexOf(currentPage);
  pageStepper.previous.disabled = index <= 0;
  pageStepper.next.disabled = index < 0 || index >= visiblePageIds.length - 1;
}

initializeTheme(data.appearance);

document.body.classList.toggle(
  "sn-card-blur-off",
  data.appearance?.cardBlur === false,
);

function selectPage(pageId) {
  if (!pageId) return;
  stopPageCanvasEditing();
  setCurrentPage(pageId);
  saveNavigationCookie("sn-current-page", pageId);
  document
    .querySelectorAll("[data-page]")
    .forEach((item) => item.classList.toggle("active", item.dataset.page === pageId));
  render();
  refreshCanvasEditButton();
  updatePageStepper();
}

function createPageStepper() {
  const sidebar = document.querySelector(".sn-sidebar");
  if (!sidebar || visiblePageIds.length < 2) return;
  const previous = document.createElement("button");
  previous.type = "button";
  previous.className = "sn-collapsed-page-step sn-page-step-previous";
  previous.title = "上一页";
  previous.setAttribute("aria-label", "上一页");
  previous.textContent = "⌃";
  const next = document.createElement("button");
  next.type = "button";
  next.className = "sn-collapsed-page-step sn-page-step-next";
  next.title = "下一页";
  next.setAttribute("aria-label", "下一页");
  next.textContent = "⌄";
  previous.addEventListener("click", () => {
    const index = visiblePageIds.indexOf(currentPage);
    selectPage(visiblePageIds[index - 1]);
  });
  next.addEventListener("click", () => {
    const index = visiblePageIds.indexOf(currentPage);
    selectPage(visiblePageIds[index + 1]);
  });
  sidebar.append(previous, next);
  pageStepper = { previous, next };
  updatePageStepper();
}

render();
document.querySelectorAll("[data-page]").forEach((button) =>
  button.addEventListener("click", () => selectPage(button.dataset.page || "all")),
);
createPageStepper();
document
  .querySelector("[data-close-modal]")
  ?.addEventListener("click", closeModal);
modal?.addEventListener("click", (event) => {
  if (event.target === modal) closeModal();
});
document.addEventListener("click", async (event) => {
  const login = event.target.closest("[data-login]");
  if (login) {
    showModal(
      '<h2>登录</h2><form class="sn-public-login-form"><label>用户名<input name="username" required autocomplete="username"></label><label>密码<input name="password" type="password" required autocomplete="current-password"></label><p class="sn-error" data-login-error></p><button class="sn-editor-save">登录</button></form>',
    );
    const form = document.querySelector(".sn-public-login-form");
    form.addEventListener("submit", async (submitEvent) => {
      submitEvent.preventDefault();
      const error = form.querySelector("[data-login-error]");
      error.textContent = "";
      const response = await fetch("/api/auth/login", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(Object.fromEntries(new FormData(form))),
      });
      if (response.ok) {
        location.reload();
        return;
      }
      const body = await response.json().catch(() => ({}));
      error.textContent = body.error?.message || "登录失败";
    });
    return;
  }
  const logout = event.target.closest("[data-logout]");
  if (logout) {
    await fetch(logout.dataset.logout, {
      method: "POST",
      credentials: "include",
    });
    location.reload();
  }
});
document
  .querySelector("[data-open-editor]")
  ?.addEventListener("click", () => openOwnerEditor());
document
  .querySelector("[data-toggle-page-edit]")
  ?.addEventListener("click", () => {
    stopPageCanvasEditing();
    setPageEditing(!document.querySelector(".sn-sidebar")?.classList.contains("is-editing"));
  });
document
  .querySelector("[data-page-canvas-edit]")
  ?.addEventListener("click", togglePageCanvasEditing);
document.querySelector("[data-exit-editing]")?.addEventListener("click", () => {
  closeModal();
  setPageEditing(false);
  stopPageCanvasEditing();
});
const ownerControls = document.querySelector("[data-owner-controls]");
const ownerControlsToggle = document.querySelector("[data-toggle-owner-controls]");
function setOwnerControlsCollapsed(collapsed) {
  if (!ownerControls || !ownerControlsToggle) return;
  ownerControls.classList.toggle("is-collapsed", collapsed);
  ownerControlsToggle.title = collapsed ? "显示编辑工具" : "隐藏编辑工具";
  ownerControlsToggle.setAttribute("aria-label", ownerControlsToggle.title);
  localStorage.setItem("sn-owner-controls-collapsed", collapsed ? "1" : "0");
}
ownerControlsToggle?.addEventListener("click", () =>
  setOwnerControlsCollapsed(!ownerControls?.classList.contains("is-collapsed")),
);
if (localStorage.getItem("sn-owner-controls-collapsed") === "1")
  setOwnerControlsCollapsed(true);
bindPageControls();

let previousBreakpoint = breakpoint();
window.addEventListener("resize", () => {
  const next = breakpoint();
  if (next !== previousBreakpoint) {
    previousBreakpoint = next;
    if (next !== 16 && isPageCanvasEditing()) stopPageCanvasEditing();
    render();
  }
});
if (readNavigationCookie("sn-sidebar-collapsed") === "1")
  dashboard?.classList.add("is-collapsed");
if (document.body.dataset.owner === "true" && localStorage.getItem("sn-sidebar-editing") === "1") setPageEditing(true);
refreshCanvasEditButton();
updatePageStepper();
if (document.body.dataset.backgroundSource === "bing") {
  const now = new Date();
  const nextDay = new Date(now);
  nextDay.setHours(24, 0, 2, 0);
  window.setTimeout(() => location.reload(), nextDay.getTime() - now.getTime());
}
document
  .querySelector("[data-toggle-sidebar]")
  ?.addEventListener("click", () => {
    const collapsed = dashboard?.classList.toggle("is-collapsed");
    saveNavigationCookie("sn-sidebar-collapsed", collapsed ? "1" : "0");
  });
document
  .querySelector("[data-toggle-catalog]")
  ?.addEventListener("click", async () => {
    try {
      await ownerApi("/catalog-visibility", "PUT", {
        visible: data.catalogVisible === false,
      });
      location.reload();
    } catch (error) {
      alert(error.message || "保存失败");
    }
  });
