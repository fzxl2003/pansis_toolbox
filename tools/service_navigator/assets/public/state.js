export const data = document.querySelector("#sn-navigation-data")
  ? JSON.parse(document.querySelector("#sn-navigation-data").textContent || "{}")
  : { pages: [], services: [] };
export const content = document.querySelector("[data-navigation-content]");
export const search = document.querySelector("[data-search-input]");
export const modal = document.querySelector("[data-action-modal]");
export const modalContent = document.querySelector("[data-modal-content]");
export const sidebar = document.querySelector(".sn-sidebar");
export const dashboard = document.querySelector(".sn-dashboard");
export let currentPage =
  data.targetPages?.find((page) => page.visible)?.id ||
  data.pages?.[0]?.id ||
  "all";
export function setCurrentPage(value) {
  currentPage = value;
}
export const sizeSpan = { small: [1, 1], medium: [2, 2], large: [4, 4], wide: [4, 2] };
export function breakpoint() {
  const width = window.innerWidth;
  return width >= 1440 ? 16 : width >= 1080 ? 12 : width >= 700 ? 8 : 4;
}
document.querySelector(`[data-page="${currentPage}"]`)?.classList.add("active");
