import { data, modalContent } from "./state.js";
import { showModal } from "./modal.js";
import { ownerApi } from "./owner-api.js";
import { preservePageEditing } from "./page-editing.js";
import { openPagePlacementManager } from "./page-placements.js";
import { openGlobalIconLibrary } from "./icon-library.js";

export function bindPageControls() {
  document
    .querySelector("[data-add-page]")
    ?.addEventListener("click", async () => {
      try {
        preservePageEditing();
        await ownerApi("/navigation/pages", "POST", { name: "新页面" });
        location.reload();
      } catch (error) {
        alert(error.message || "新增页面失败");
      }
    });
  document
    .querySelector("[data-page-icons]")
    ?.addEventListener("click", openGlobalIconLibrary);
  document.querySelectorAll("[data-page-edit]").forEach((button) =>
    button.addEventListener("click", () => {
      const page = (data.pages || []).find(
        (item) => item.id === button.dataset.pageEdit,
      );
      if (!page) return;
      showModal(
        `<h2>编辑页面</h2><form class="sn-target-page-form"><label>页面名称<input name="name" required></label><label class="sn-check"><input name="visible" type="checkbox">在访客侧边栏显示</label><div><button class="sn-editor-delete" type="button">删除页面</button><button type="button" class="sn-page-icons-button" data-manage-placements>管理页面图标</button><button class="sn-editor-save">保存</button></div></form>`,
      );
      const form = modalContent.querySelector(".sn-target-page-form");
      form.name.value = page.name;
      form.visible.checked = page.visible !== false;
      form
        .querySelector(".sn-editor-delete")
        .addEventListener("click", async () => {
          if (confirm(`删除页面「${page.name}」？`)) {
            preservePageEditing();
            await ownerApi(`/navigation/pages/${page.id}`, "DELETE");
            location.reload();
          }
        });
      form
        .querySelector("[data-manage-placements]")
        .addEventListener("click", () => openPagePlacementManager(page));
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          preservePageEditing();
          await ownerApi(`/navigation/pages/${page.id}`, "PUT", {
            name: form.name.value,
            visible: form.visible.checked,
          });
          location.reload();
        } catch (error) {
          alert(error.message || "页面保存失败");
        }
      });
    }),
  );
  document.querySelectorAll("[data-page-move]").forEach((button) =>
    button.addEventListener("click", async () => {
      const pages = [...(data.pages || [])];
      const index = pages.findIndex(
        (page) => page.id === button.dataset.pageId,
      );
      const next = index + (button.dataset.pageMove === "up" ? -1 : 1);
      if (index < 0 || next < 0 || next >= pages.length) return;
      preservePageEditing();
      [pages[index], pages[next]] = [pages[next], pages[index]];
      await ownerApi("/navigation/pages/order", "PUT", {
        pageIds: pages.map((page) => page.id),
      });
      location.reload();
    }),
  );
  document.querySelectorAll("[data-target-move]").forEach((button) =>
    button.addEventListener("click", async () => {
      const targets = [...(data.targetPages || [])];
      const index = targets.findIndex(
        (target) => target.targetId === button.dataset.targetId,
      );
      const next = index + (button.dataset.targetMove === "up" ? -1 : 1);
      if (index < 0 || next < 0 || next >= targets.length) return;
      preservePageEditing();
      [targets[index], targets[next]] = [targets[next], targets[index]];
      await ownerApi("/targets/order", "PUT", {
        targetIds: targets.map((target) => target.targetId),
      });
      location.reload();
    }),
  );
  document.querySelectorAll("[data-target-edit]").forEach((button) =>
    button.addEventListener("click", () => {
      const target = (data.targetPages || []).find(
        (item) => item.targetId === button.dataset.targetEdit,
      );
      if (!target) return;
      showModal(
        `<h2>编辑固定页面</h2><form class="sn-target-page-form"><label>页面名称<input name="name" required></label><label class="sn-check"><input name="visible" type="checkbox">在访客侧边栏显示</label><div><button class="sn-editor-save">保存</button></div></form>`,
      );
      const form = modalContent.querySelector(".sn-target-page-form");
      form.name.value = target.name;
      form.visible.checked = target.visible;
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        try {
          preservePageEditing();
          await ownerApi(`/targets/${target.targetId}`, "PUT", {
            label: form.name.value,
            address: target.address,
            customPorts: target.customPorts || "",
            showInNavigation: form.visible.checked,
          });
          location.reload();
        } catch (error) {
          alert(error.message || "固定页面保存失败");
        }
      });
    }),
  );
}
