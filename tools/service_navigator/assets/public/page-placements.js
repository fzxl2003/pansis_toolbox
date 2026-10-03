import { data, modalContent } from "./state.js";
import { escapeHtml } from "./utils.js";
import { showModal } from "./modal.js";
import { ownerApi } from "./owner-api.js";
import { iconPreviewNode } from "./icon-library.js";

export function openPagePlacementManager(page) {
  const available = (data.icons || []).filter(
    (icon) => !page.items.some((item) => item.iconId === icon.id),
  );
  showModal(
    `<h2>${escapeHtml(page.name)} · 管理摆放</h2><p class="sn-modal-note">从全局图标库选择图标加入此页面；位置在页面上拖拽调整，移除摆放不会删除图标库中的图标。</p><section class="sn-editor-section"><h3>添加图标</h3><form class="sn-placement-form" data-placement-form><select data-placement-icon required></select><select data-placement-size><option value="small">小图标 · 1×1</option><option value="medium">中图标 · 2×2</option><option value="large">大图标 · 4×4</option><option value="wide">宽卡片 · 4×2</option></select><button class="sn-editor-save">添加到页面</button></form><div class="sn-placement-list" data-placement-list></div></section>`,
  );
  const iconSelect = modalContent.querySelector("[data-placement-icon]");
  for (const icon of available) {
    const option = document.createElement("option");
    option.value = icon.id;
    option.textContent = icon.name;
    iconSelect.append(option);
  }
  if (!available.length)
    iconSelect.append(new Option("图标库中没有可用图标", ""));
  const sizeSelect = modalContent.querySelector("[data-placement-size]");
  const list = modalContent.querySelector("[data-placement-list]");
  for (const item of page.items) {
    const icon = (data.icons || []).find((entry) => entry.id === item.iconId);
    const row = document.createElement("article");
    row.className = "sn-library-icon";
    row.append(iconPreviewNode(icon));
    const body = document.createElement("div");
    body.innerHTML = "<strong></strong><small></small>";
    body.querySelector("strong").textContent = item.name;
    body.querySelector("small").textContent =
      `${{ small: "小 · 1×1", medium: "中 · 2×2", large: "大 · 4×4", wide: "宽 · 4×2" }[item.size] || "小 · 1×1"} · ${item.services.length} 个服务`;
    row.append(body);
    const select = document.createElement("select");
    for (const [value, label] of [
      ["small", "小"],
      ["medium", "中"],
      ["large", "大"],
      ["wide", "宽"],
    ]) {
      const option = document.createElement("option");
      option.value = value;
      option.textContent = label;
      select.append(option);
    }
    select.value = item.size;
    select.addEventListener("change", async () => {
      try {
        await ownerApi(`/navigation/items/${item.id}`, "PUT", {
          size: select.value,
        });
        location.reload();
      } catch (error) {
        alert(error.message || "尺寸保存失败");
      }
    });
    const remove = document.createElement("button");
    remove.textContent = "移除";
    remove.className = "danger";
    remove.addEventListener("click", async () => {
      await ownerApi(`/navigation/items/${item.id}`, "DELETE");
      location.reload();
    });
    const actions = document.createElement("div");
    actions.append(select, remove);
    row.append(actions);
    list.append(row);
  }
  if (!page.items.length)
    list.innerHTML = '<p class="sn-empty">这个页面暂无图标。</p>';
  modalContent
    .querySelector("[data-placement-form]")
    .addEventListener("submit", async (event) => {
      event.preventDefault();
      if (!iconSelect.value) {
        alert("请先在“管理图标”中添加图标。");
        return;
      }
      try {
        await ownerApi("/navigation/items", "POST", {
          pageId: page.id,
          iconId: iconSelect.value,
          size: sizeSelect.value,
        });
        location.reload();
      } catch (error) {
        alert(error.message || "图标摆放失败");
      }
    });
}
