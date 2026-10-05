import { sidebar, dashboard } from "./state.js";

let editing = false;
let restoreCollapsed = false;

export function syncEditingControls() {
  const active = Boolean(sidebar?.classList.contains("is-editing") || document.body.classList.contains("sn-page-canvas-editing"));
  if (active && !editing) {
    restoreCollapsed = Boolean(dashboard?.classList.contains("is-collapsed"));
    dashboard?.classList.remove("is-collapsed");
  } else if (!active && editing && restoreCollapsed) {
    dashboard?.classList.add("is-collapsed");
  }
  editing = active;
  document.body.classList.toggle("sn-editing", active);
  const exit = document.querySelector("[data-exit-editing]");
  if (exit) exit.hidden = !active;
}
