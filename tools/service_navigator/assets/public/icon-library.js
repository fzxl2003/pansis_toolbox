import { data, modalContent } from "./state.js";
import { escapeHtml, serviceName } from "./utils.js";
import { closeModal, iconEdit, showModal } from "./modal.js";
import { ownerApi } from "./owner-api.js";
import { setPageEditing } from "./page-editing.js";

export function iconPreviewNode(icon) {
  const node = document.createElement("span");
  node.className = "sn-nav-icon-image";
  const url =
    icon?.iconSource === "custom"
      ? icon.iconUrl || ""
      : icon?.iconSource === "favicon"
        ? (data.services || []).find(
            (entry) => entry.id === icon.faviconServiceId,
          )?.faviconUrl || ""
        : "";
  if (url) {
    const image = new Image();
    image.src = url;
    image.alt = "";
    node.append(image);
  } else {
    const fallback = document.createElement("span");
    fallback.className = "sn-nav-fallback";
    fallback.textContent =
      icon?.iconSource === "text"
        ? icon.iconText || icon.name?.slice(0, 1) || "A"
        : "◌";
    fallback.style.color =
      icon?.iconSource === "text" ? icon.iconColor || "" : "";
    node.append(fallback);
  }
  return node;
}

export function openGlobalIconLibrary() {
  setPageEditing(false);
  const custom = (data.icons || []).filter((icon) => !icon.detectedServiceId);
  const detected = (data.icons || []).filter((icon) => icon.detectedServiceId);
  showModal(
    `<h2>管理图标</h2><p class="sn-modal-note">这里维护全局图标库。各页面只添加、摆放和移除图标；编辑一次图标，所有引用位置同步更新。</p><div class="sn-library-actions"><button class="sn-editor-save" data-add-icon>添加自定义图标</button></div><section class="sn-editor-section"><h3>自定义图标</h3><div class="sn-icon-library" data-custom-icons></div></section><section class="sn-editor-section"><h3>已探测服务图标</h3><div class="sn-icon-library" data-detected-icons></div></section>`,
  );
  const renderGroup = (box, icons) => {
    box.replaceChildren();
    if (!icons.length) {
      box.innerHTML = '<p class="sn-empty">暂无图标</p>';
      return;
    }
    for (const icon of icons) {
      const row = document.createElement("article");
      row.className = "sn-library-icon";
      row.append(iconPreviewNode(icon));
      const body = document.createElement("div");
      body.innerHTML = "<strong></strong><small></small>";
      body.querySelector("strong").textContent = icon.name;
      body.querySelector("small").textContent =
        `${icon.services.length} 个服务 · ${icon.iconSource === "custom" ? "上传图片" : icon.iconSource === "favicon" ? "favicon" : "文字"}`;
      row.append(body);
      const actions = document.createElement("div");
      actions.innerHTML =
        '<button>编辑</button><button class="danger">删除</button>';
      actions
        .querySelectorAll("button")[0]
        .addEventListener("click", () => openGlobalIconForm(icon));
      actions
        .querySelectorAll("button")[1]
        .addEventListener("click", async () => {
          if (
            !confirm(
              `删除全局图标「${icon.name}」？所有页面上的对应摆放也会一起删除。`,
            )
          )
            return;
          await ownerApi(`/navigation/icons/${icon.id}`, "DELETE");
          location.reload();
        });
      row.append(actions);
      box.append(row);
    }
  };
  renderGroup(modalContent.querySelector("[data-custom-icons]"), custom);
  renderGroup(modalContent.querySelector("[data-detected-icons]"), detected);
  modalContent
    .querySelector("[data-add-icon]")
    .addEventListener("click", () => openGlobalIconForm(null));
}

export function openGlobalIconForm(icon, restoreFile = false, onSaved = null) {
  if (!restoreFile) iconEdit.file = null;
  const detected = Boolean(icon?.detectedServiceId);
  const current = icon || {
    name: "",
    iconSource: "text",
    iconText: "A",
    iconColor: "#4f7cff",
    serviceIds: [],
  };
  const association = detected
    ? '<section class="sn-icon-services sn-locked-service-field"><span>关联服务</span><div data-icon-services></div></section>'
    : '<label class="sn-icon-services">关联服务<div data-icon-services></div></label>';
  showModal(
    `<h2>${detected ? "编辑已探测服务图标" : icon ? "编辑自定义图标" : "添加自定义图标"}</h2><form class="sn-icon-form" data-icon-form><label>名称<input name="name" required></label><label>图标类型<select name="source"><option value="text">文字图标</option><option value="favicon">服务 favicon</option><option value="custom">上传图片</option></select></label>${association}<div data-source-fields></div><button class="sn-icon-preview" type="button" data-icon-preview><span data-icon-preview-image></span><span>图标预览</span><small>点击查看各尺寸效果</small></button><div><button class="sn-editor-save" type="submit">保存</button></div></form>`,
  );
  const form = modalContent.querySelector("[data-icon-form]");
  form.name.value = current.name;
  form.source.value = ["text", "favicon", "custom"].includes(current.iconSource)
    ? current.iconSource
    : "text";
  const servicesBox = form.querySelector("[data-icon-services]");
  if (detected) {
    const service = (data.services || []).find(
      (entry) => entry.id === current.detectedServiceId,
    );
    servicesBox.innerHTML = `<article class="sn-locked-service"><strong>${escapeHtml(serviceName(service || {}))}</strong><small>${service?.serviceType === "http" ? "HTTP 服务" : "端口服务"} · TCP/${service?.port || "—"}</small><small>${escapeHtml(service?.targetLabel || "未命名扫描目标")}${service?.targetAddress ? ` · ${escapeHtml(service.targetAddress)}` : ""}</small></article>`;
  } else
    for (const service of data.services || []) {
      const label = document.createElement("label");
      label.innerHTML = '<input type="checkbox"><span></span>';
      const input = label.querySelector("input");
      input.value = service.id;
      input.checked = current.serviceIds.includes(service.id);
      label.querySelector("span").textContent =
        `${service.serviceType === "http" ? "HTTP" : "端口"} · ${serviceName(service)} · ${service.port}`;
      servicesBox.append(label);
    }
  const linkedServices = (data.services || []).filter((entry) =>
    detected
      ? entry.id === current.detectedServiceId
      : current.serviceIds.includes(entry.id),
  );
  const previewUrl = () => {
    if (form.source.value === "custom") {
      if (iconEdit.file) {
        if (iconEdit.previewUrl) URL.revokeObjectURL(iconEdit.previewUrl);
        iconEdit.previewUrl = URL.createObjectURL(iconEdit.file);
        return iconEdit.previewUrl;
      }
      return icon?.iconUrl || "";
    }
    if (form.source.value === "favicon") {
      const select = form.querySelector('[name="favicon"]');
      return select
        ? (data.services || []).find((entry) => entry.id === select.value)
            ?.faviconUrl || ""
        : "";
    }
    return "";
  };
  function renderPreview() {
    const box = form.querySelector("[data-icon-preview-image]");
    box.replaceChildren();
    const url = previewUrl();
    if (url) {
      const image = new Image();
      image.src = url;
      image.alt = "";
      box.append(image);
    } else {
      const fallback = document.createElement("span");
      fallback.className = "sn-nav-fallback";
      fallback.textContent =
        form.source.value === "text"
          ? form.querySelector('[name="text"]')?.value ||
            current.name.slice(0, 1) ||
            "A"
          : "◌";
      fallback.style.color =
        form.source.value === "text"
          ? form.querySelector('[name="color"]')?.value || ""
          : "";
      box.append(fallback);
    }
  }
  const sourceFields = () => {
    const box = form.querySelector("[data-source-fields]");
    box.replaceChildren();
    if (form.source.value === "text")
      box.insertAdjacentHTML(
        "beforeend",
        '<label>图标文字<input name="text" maxlength="4" placeholder="A"></label><label>图标颜色<input name="color" type="color"></label>',
      );
    if (form.source.value === "favicon")
      box.insertAdjacentHTML(
        "beforeend",
        '<label>favicon 服务<select name="favicon"></select></label>',
      );
    if (form.source.value === "custom")
      box.insertAdjacentHTML(
        "beforeend",
        '<label data-source-custom>上传图片<input name="file" type="file" accept=".png,.jpg,.jpeg,.webp,.ico"></label>',
      );
    const text = form.querySelector('[name="text"]');
    const color = form.querySelector('[name="color"]');
    const favicon = form.querySelector('[name="favicon"]');
    const file = form.querySelector('[name="file"]');
    if (text) {
      text.value = current.iconText || "A";
      text.addEventListener("input", renderPreview);
    }
    if (color) {
      color.value = /^#[0-9a-f]{6}$/i.test(current.iconColor)
        ? current.iconColor
        : "#4f7cff";
      color.addEventListener("input", renderPreview);
    }
    if (favicon) {
      for (const service of linkedServices) {
        const option = document.createElement("option");
        option.value = service.id;
        option.textContent = `${serviceName(service)} · ${service.targetAddress || service.targetLabel || service.port}`;
        favicon.append(option);
      }
      if (!favicon.options.length)
        favicon.append(new Option("当前关联服务暂无 favicon", ""));
      favicon.value =
        current.faviconServiceId &&
        linkedServices.some((entry) => entry.id === current.faviconServiceId)
          ? current.faviconServiceId
          : linkedServices[0]?.id || "";
      favicon.addEventListener("change", renderPreview);
    }
    if (file) {
      if (iconEdit.file) {
        const transfer = new DataTransfer();
        transfer.items.add(iconEdit.file);
        file.files = transfer.files;
      }
      file.addEventListener("change", () => {
        iconEdit.file = file.files?.[0] || null;
        renderPreview();
      });
    }
    renderPreview();
  };
  sourceFields();
  form.source.addEventListener("change", sourceFields);
  form
    .querySelector("[data-icon-preview]")
    .addEventListener("click", () =>
      openGlobalIconSizePreview(
        {
          name: form.name.value,
          iconSource: form.source.value,
          iconText: form.querySelector('[name="text"]')?.value || "",
          iconColor: form.querySelector('[name="color"]')?.value || "",
          faviconServiceId: form.querySelector('[name="favicon"]')?.value || "",
          iconUrl: previewUrl(),
        },
        icon,
        restoreFile,
        onSaved,
      ),
    );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const serviceIds = detected
      ? [current.detectedServiceId]
      : [...form.querySelectorAll("[data-icon-services] input:checked")].map(
          (input) => input.value,
        );
    const body = {
      name: form.name.value,
      iconSource: form.source.value,
      iconText: form.querySelector('[name="text"]')?.value || "",
      iconColor: form.querySelector('[name="color"]')?.value || "",
      faviconServiceId:
        form.source.value === "favicon"
          ? form.querySelector('[name="favicon"]')?.value || ""
          : "",
      serviceIds,
      detectedServiceId: detected ? current.detectedServiceId : "",
    };
    try {
      const result = icon
        ? await ownerApi(`/navigation/icons/${icon.id}`, "PUT", body)
        : await ownerApi("/navigation/icons", "POST", body);
      const iconId = icon?.id || result.icon?.id;
      if (iconEdit.file && iconId) {
        const upload = new FormData();
        upload.append("file", iconEdit.file);
        const response = await fetch(
          `/api/tools/service-navigator/navigation/icons/${iconId}/icon`,
          { method: "POST", credentials: "include", body: upload },
        );
        if (!response.ok) throw new Error("图标上传失败");
        const uploaded = await response.json();
        result.icon = uploaded.icon || result.icon;
      }
      if (onSaved && result.icon) {
        closeModal();
        onSaved(result.icon);
      } else location.reload();
    } catch (error) {
      alert(error.message || "图标保存失败");
    }
  });
}

function openGlobalIconSizePreview(draft, icon, restoreFile = false, onSaved = null) {
  showModal(
    `<h2>图标尺寸预览</h2><p class="sn-modal-note">保存后可在页面上选择摆放尺寸。</p><div class="sn-icon-size-preview" data-size-preview></div><button class="sn-editor-save" data-return-icon-form>返回编辑</button>`,
  );
  const box = modalContent.querySelector("[data-size-preview]");
  for (const [size] of [
    ["small"],
    ["medium"],
    ["large"],
    ["wide"],
  ]) {
    const card = document.createElement("article");
    card.className = `sn-nav-icon sn-size-${size}`;
    card.append(iconPreviewNode(draft));
    if (size !== "small") {
      const body = document.createElement("span");
      body.className = "sn-nav-card-body";
      body.innerHTML = "<strong></strong><small></small>";
      body.querySelector("strong").textContent = draft.name || "服务图标";
      body.querySelector("small").textContent = "tcp/4000 · 网页 服务";
      const status = document.createElement("em");
      status.textContent = "在线";
      card.append(body, status);
    }
    box.append(card);
  }
  modalContent
    .querySelector("[data-return-icon-form]")
    .addEventListener("click", () => openGlobalIconForm(icon, restoreFile, onSaved));
}
