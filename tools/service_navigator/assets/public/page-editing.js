import { sidebar } from "./state.js";

export function setPageEditing(active) {
  sidebar?.classList.toggle("is-editing", active);
  document.body.classList.remove("sn-layout-editing");
  if (active) localStorage.setItem("sn-sidebar-editing", "1");
  else localStorage.removeItem("sn-sidebar-editing");
  const button = document.querySelector("[data-toggle-page-edit]");
  if (button) button.textContent = active ? "完成编辑" : "编辑导航";
}
export function preservePageEditing() {
  if (sidebar?.classList.contains("is-editing"))
    localStorage.setItem("sn-sidebar-editing", "1");
}
