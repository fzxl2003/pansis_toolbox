import "./public/clock.js";
import "./public/search.js";
import {
  breakpoint,
  currentPage,
  dashboard,
  data,
  modal,
  setCurrentPage,
} from "./public/state.js";
import { closeModal } from "./public/modal.js";
import { render } from "./public/render.js";
import { setPageEditing } from "./public/page-editing.js";
import { openOwnerEditor } from "./public/owner-editor.js";
import { bindPageControls } from "./public/page-controls.js";
import { enableLayoutEditing } from "./public/layout.js";
import { ownerApi } from "./public/owner-api.js";

render();
document.querySelectorAll("[data-page]").forEach((button) =>
  button.addEventListener("click", () => {
    setCurrentPage(button.dataset.page || "all");
    document
      .querySelectorAll("[data-page]")
      .forEach((item) => item.classList.toggle("active", item === button));
    render();
  }),
);
document
  .querySelector("[data-close-modal]")
  ?.addEventListener("click", closeModal);
modal?.addEventListener("click", (event) => {
  if (event.target === modal) closeModal();
});
document.addEventListener("click", async (event) => {
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
  ?.addEventListener("click", () =>
    setPageEditing(
      !document
        .querySelector(".sn-sidebar")
        ?.classList.contains("is-editing"),
    ),
  );
document
  .querySelector("[data-layout-edit]")
  ?.addEventListener("click", enableLayoutEditing);
bindPageControls();

let previousBreakpoint = breakpoint();
window.addEventListener("resize", () => {
  const next = breakpoint();
  if (next !== previousBreakpoint) {
    previousBreakpoint = next;
    render();
  }
});
if (localStorage.getItem("sn-sidebar-collapsed") === "1")
  dashboard?.classList.add("is-collapsed");
if (localStorage.getItem("sn-sidebar-editing") === "1") setPageEditing(true);
document
  .querySelector("[data-toggle-sidebar]")
  ?.addEventListener("click", () => {
    const collapsed = dashboard?.classList.toggle("is-collapsed");
    localStorage.setItem("sn-sidebar-collapsed", collapsed ? "1" : "0");
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
