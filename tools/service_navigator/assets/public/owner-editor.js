import { data, modalContent, sidebar } from "./state.js";
import { showModal } from "./modal.js";
import { ownerApi } from "./owner-api.js";
import { setPageEditing } from "./page-editing.js";

export function openOwnerEditor() {
  setPageEditing(false);
  const appearance = data.appearance || {
    theme: "auto",
    accentColor: "#4f7cff",
  };
  const presets = [
    "#4f7cff",
    "#42b983",
    "#0d9488",
    "#2563eb",
    "#7c3aed",
    "#db2777",
    "#dc2626",
    "#ea580c",
  ];
  showModal(
    `<h2>导航外观</h2><p class="sn-modal-note">页面和图标请在左侧栏的编辑模式中管理。</p><section class="sn-editor-section sn-appearance-section"><h3>外观</h3><label class="sn-editor-field">主题<select data-site-theme><option value="auto">跟随系统</option><option value="light">亮色</option><option value="dark">暗黑</option></select></label><div class="sn-editor-field"><span>主题色</span><div class="sn-accent-picker"><div data-accent-presets></div><label><input data-accent-color type="color" aria-label="自定义主题色"><input data-accent-hex maxlength="7" placeholder="#4f7cff" aria-label="主题色十六进制值"></label></div></div><button class="sn-editor-save" data-save-appearance>保存外观</button></section><section class="sn-editor-section"><h3>背景</h3><div class="sn-editor-actions"><button data-background="default">默认背景</button><button data-background="bing">每日 Bing 壁纸</button><label>上传背景<input data-background-upload type="file" accept=".png,.jpg,.jpeg,.webp"></label></div></section>`,
  );
  const themeInput = modalContent.querySelector("[data-site-theme]");
  const colorInput = modalContent.querySelector("[data-accent-color]");
  const hexInput = modalContent.querySelector("[data-accent-hex]");
  const presetBox = modalContent.querySelector("[data-accent-presets]");
  themeInput.value = ["auto", "light", "dark"].includes(appearance.theme)
    ? appearance.theme
    : "auto";
  colorInput.value = /^#[0-9a-f]{6}$/i.test(appearance.accentColor)
    ? appearance.accentColor
    : "#4f7cff";
  hexInput.value = colorInput.value;
  function selectAccent(value) {
    if (!/^#[0-9a-f]{6}$/i.test(value)) return;
    colorInput.value = value;
    hexInput.value = value.toLowerCase();
    presetBox
      .querySelectorAll("button")
      .forEach((button) =>
        button.classList.toggle(
          "active",
          button.dataset.accent === value.toLowerCase(),
        ),
      );
  }
  for (const color of presets) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "sn-accent-swatch";
    button.dataset.accent = color;
    button.style.backgroundColor = color;
    button.title = color;
    button.setAttribute("aria-label", `选择主题色 ${color}`);
    button.addEventListener("click", () => selectAccent(color));
    presetBox.append(button);
  }
  selectAccent(colorInput.value);
  colorInput.addEventListener("input", () => selectAccent(colorInput.value));
  hexInput.addEventListener("input", () => {
    if (/^#[0-9a-f]{6}$/i.test(hexInput.value)) selectAccent(hexInput.value);
  });
  modalContent
    .querySelector("[data-save-appearance]")
    .addEventListener("click", async () => {
      try {
        await ownerApi("/site", "PUT", {
          theme: themeInput.value,
          accentColor: colorInput.value,
        });
        location.reload();
      } catch (error) {
        alert(error.message || "外观保存失败");
      }
    });
  modalContent.querySelectorAll("[data-background]").forEach((button) =>
    button.addEventListener("click", async () => {
      await ownerApi("/background", "PUT", {
        source: button.dataset.background,
      });
      location.reload();
    }),
  );
  modalContent
    .querySelector("[data-background-upload]")
    .addEventListener("change", async (event) => {
      const file = event.target.files?.[0];
      if (!file) return;
      const form = new FormData();
      form.append("file", file);
      const response = await fetch(
        "/api/tools/service-navigator/background/upload",
        { method: "POST", credentials: "include", body: form },
      );
      if (!response.ok) alert("背景上传失败");
      else location.reload();
    });
}
