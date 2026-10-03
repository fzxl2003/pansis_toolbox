import { currentPage, data } from "./state.js";
import { ownerApi } from "./owner-api.js";

export function enableLayoutEditing() {
  const page = (data.pages || []).find((item) => item.id === currentPage);
  const grid = document.querySelector(".sn-icon-grid");
  if (!page || !grid) {
    alert("请先打开一个自定义页面，再调整图标位置。");
    return;
  }
  document.body.classList.add("sn-layout-editing");
  document.querySelectorAll(".sn-nav-icon").forEach((card, index) => {
    card.draggable = true;
    card.dataset.itemId = page.items[index]?.id || "";
    card.addEventListener("dragstart", (event) =>
      event.dataTransfer.setData("text/plain", card.dataset.itemId),
    );
  });
  grid.addEventListener("dragover", (event) => event.preventDefault());
  grid.addEventListener("drop", async (event) => {
    event.preventDefault();
    const id = event.dataTransfer.getData("text/plain");
    const rect = grid.getBoundingClientRect();
    const x = Math.max(
      0,
      Math.min(15, Math.floor((event.clientX - rect.left) / (rect.width / 16))),
    );
    const y = Math.max(0, Math.floor((event.clientY - rect.top) / 76));
    try {
      await ownerApi(`/navigation/pages/${page.id}/layouts/16`, "PUT", {
        placements: page.items.map((item) => ({
          itemId: item.id,
          x: item.id === id ? x : item.layouts?.["16"]?.x || 0,
          y: item.id === id ? y : item.layouts?.["16"]?.y || 0,
        })),
      });
      location.reload();
    } catch (error) {
      alert(error.message || "位置不可用");
    }
  });
  alert(
    "已进入可视化布局：拖动图标改变位置；图标内容请从左侧“编辑图标内容”修改。",
  );
}
