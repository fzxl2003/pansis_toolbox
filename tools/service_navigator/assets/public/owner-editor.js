import { data, modalContent, sidebar } from "./state.js";
import { showModal } from "./modal.js";
import { ownerApi } from "./owner-api.js";
import { setPageEditing } from "./page-editing.js";

export function openOwnerEditor() {
  setPageEditing(false);
  const appearance = data.appearance || {
    theme: "auto",
    accentColor: "#4f7cff",
    cardOpacity: 84,
    cardBlur: true,
    backgroundOverlayOpacity: 50,
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
    `<h2>导航外观</h2><p class="sn-modal-note">统一设置导航的色彩、背景与卡片质感。</p><section class="sn-editor-section sn-appearance-section"><h3>外观</h3><div class="sn-appearance-grid"><label class="sn-editor-field">主题<select data-site-theme><option value="auto">跟随系统</option><option value="background">跟随背景</option><option value="light">亮色</option><option value="dark">暗黑</option></select></label><label class="sn-editor-field">卡片透明度<span class="sn-range-field"><input data-card-opacity type="range" min="0" max="100" step="1"><output data-card-opacity-value></output></span></label><div class="sn-editor-field sn-card-blur-field"><span>卡片模糊</span><label class="sn-card-blur-toggle"><input data-card-blur type="checkbox"><span>开启背景模糊</span></label><small>统一应用于固定服务卡片与自定义图标卡片。</small></div></div><div class="sn-editor-field"><span>主题色</span><select data-accent-mode aria-label="主题色模式"><option value="custom">自定义</option><option value="background">根据背景自动取色</option></select><div class="sn-accent-picker"><div data-accent-presets></div><label><input data-accent-color type="color" aria-label="自定义主题色"><input data-accent-hex maxlength="7" placeholder="#4f7cff" aria-label="主题色十六进制值"></label></div></div></section><section class="sn-editor-section sn-background-section"><h3>背景</h3><label class="sn-editor-field">背景来源<select data-background-source><option value="default">默认背景</option><option value="bing">每日 Bing 壁纸（每天自动更新）</option><option value="custom">上传图片</option></select></label><label class="sn-editor-field">背景蒙版透明度<span class="sn-range-field"><input data-background-overlay-opacity type="range" min="0" max="100" step="1"><output data-background-overlay-opacity-value></output></span><small>0% 为完全透明，100% 为完全遮挡背景。</small></label><label class="sn-editor-field sn-background-upload" data-background-upload-row hidden>选择图片<input data-background-upload type="file" accept=".png,.jpg,.jpeg,.webp"></label></section><div class="sn-editor-footer"><button class="sn-editor-save" data-save-appearance>保存外观</button></div>`,
  );
  const themeInput = modalContent.querySelector("[data-site-theme]");
  const accentMode = modalContent.querySelector("[data-accent-mode]");
  accentMode.value = appearance.accentColorMode === "background" ? "background" : "custom";
  const colorInput = modalContent.querySelector("[data-accent-color]");
  const hexInput = modalContent.querySelector("[data-accent-hex]");
  const presetBox = modalContent.querySelector("[data-accent-presets]");
  const opacityInput = modalContent.querySelector("[data-card-opacity]");
  const opacityValue = modalContent.querySelector("[data-card-opacity-value]");
  const cardBlurInput = modalContent.querySelector("[data-card-blur]");
  const overlayOpacityInput = modalContent.querySelector("[data-background-overlay-opacity]");
  const overlayOpacityValue = modalContent.querySelector("[data-background-overlay-opacity-value]");
  const backgroundSource = modalContent.querySelector("[data-background-source]");
  const uploadRow = modalContent.querySelector("[data-background-upload-row]");
  themeInput.value = ["auto", "light", "dark", "background"].includes(appearance.theme)
    ? appearance.theme
    : "auto";
  colorInput.value = /^#[0-9a-f]{6}$/i.test(appearance.accentColor)
    ? appearance.accentColor
    : "#4f7cff";
  hexInput.value = colorInput.value;
  const savedCardOpacity = Number(appearance.cardOpacity);
  opacityInput.value = String(Math.min(100, Math.max(0, Number.isFinite(savedCardOpacity) ? savedCardOpacity : 84)));
  const showOpacity = () => { opacityValue.value = `${opacityInput.value}%`; opacityValue.textContent = `${opacityInput.value}%`; };
  showOpacity();
  opacityInput.addEventListener("input", showOpacity);
  cardBlurInput.checked = appearance.cardBlur !== false;
  const savedOverlayOpacity = Number(appearance.backgroundOverlayOpacity);
  overlayOpacityInput.value = String(Math.min(100, Math.max(0, Number.isFinite(savedOverlayOpacity) ? savedOverlayOpacity : 50)));
  const showOverlayOpacity = () => { overlayOpacityValue.value = `${overlayOpacityInput.value}%`; overlayOpacityValue.textContent = `${overlayOpacityInput.value}%`; };
  showOverlayOpacity();
  overlayOpacityInput.addEventListener("input", showOverlayOpacity);
  backgroundSource.value = ["default", "bing", "custom"].includes(appearance.backgroundSource) ? appearance.backgroundSource : "default";
  const toggleUpload = () => { uploadRow.hidden = backgroundSource.value !== "custom"; };
  toggleUpload();
  backgroundSource.addEventListener("change", toggleUpload);
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
  const updateAccentMode = () => {
    for (const control of [colorInput, hexInput, ...presetBox.querySelectorAll("button")]) control.disabled = accentMode.value === "background";
  };
  accentMode.addEventListener("change", updateAccentMode);
  updateAccentMode();
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
          accentColorMode: accentMode.value,
          cardOpacity: Number(opacityInput.value),
          cardBlur: cardBlurInput.checked,
          backgroundOverlayOpacity: Number(overlayOpacityInput.value),
        });
        const file = modalContent.querySelector("[data-background-upload]").files?.[0];
        if (backgroundSource.value === "custom" && file) {
          const form = new FormData();
          form.append("file", file);
          const response = await fetch(
            "/api/tools/service-navigator/background/upload",
            { method: "POST", credentials: "include", body: form },
          );
          if (!response.ok) throw new Error("背景上传失败");
        } else {
          await ownerApi("/background", "PUT", { source: backgroundSource.value });
        }
        location.reload();
      } catch (error) {
        alert(error.message || "外观保存失败");
      }
    });
}
