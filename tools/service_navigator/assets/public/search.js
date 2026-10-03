import { search } from "./state.js";

let searchEngine = "google";
const engineTrigger = document.querySelector("[data-engine-trigger]");
const engineMenu = document.querySelector("[data-engine-menu]");
const selectedEngine = document.querySelector("[data-selected-engine]");
const engineMarks = {
  google: { name: "Google", mark: "G", className: "sn-engine-google" },
  baidu: { name: "百度", mark: "⌘", className: "sn-engine-baidu" },
  bing: { name: "必应", mark: "B", className: "sn-engine-bing" },
};
function chooseSearchEngine(value) {
  searchEngine = engineMarks[value] ? value : "google";
  const selected = engineMarks[searchEngine];
  if (selectedEngine) {
    selectedEngine.textContent = selected.mark;
    selectedEngine.className = `sn-engine-mark ${selected.className}`;
  }
  engineMenu.hidden = true;
  engineTrigger?.setAttribute("aria-expanded", "false");
  search?.focus();
}
engineTrigger?.addEventListener("click", () => {
  const willOpen = engineMenu.hidden;
  engineMenu.hidden = !willOpen;
  engineTrigger.setAttribute("aria-expanded", String(willOpen));
});
document
  .querySelectorAll("[data-engine]")
  .forEach((button) =>
    button.addEventListener("click", () =>
      chooseSearchEngine(button.dataset.engine || "google"),
    ),
  );
document.addEventListener("click", (event) => {
  if (
    engineMenu &&
    !engineMenu.hidden &&
    !event.target.closest(".sn-search-area")
  ) {
    engineMenu.hidden = true;
    engineTrigger?.setAttribute("aria-expanded", "false");
  }
});
document.querySelector("[data-web-search]")?.addEventListener("click", () => {
  const query = (search?.value || "").trim();
  if (!query) return;
  const base =
    searchEngine === "baidu"
      ? "https://www.baidu.com/s?wd="
      : searchEngine === "bing"
        ? "https://www.bing.com/search?q="
        : "https://www.google.com/search?q=";
  window.open(base + encodeURIComponent(query), "_blank", "noopener");
});
search?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    document.querySelector("[data-web-search]")?.click();
  }
});
