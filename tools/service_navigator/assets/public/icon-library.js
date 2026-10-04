import { data, modalContent } from "./state.js";
import { escapeHtml, serviceName } from "./utils.js";
import { closeModal, iconEdit, showModal } from "./modal.js";
import { ownerApi } from "./owner-api.js";
import { setPageEditing } from "./page-editing.js";

export function iconPreviewNode(icon) {
  const node = document.createElement("span");
  node.className = "sn-nav-icon-image";
  const url =
    icon?.iconUrl ||
    (icon?.iconSource === "favicon"
      ? (data.services || []).find(
          (entry) => entry.id === icon.faviconServiceId,
        )?.faviconUrl || ""
      : "");
  if (url) {
    const image = new Image();
    image.src = url;
    image.alt = "";
    node.append(image);
  } else {
    const fallback = document.createElement("span");
    fallback.className = "sn-nav-fallback";
    fallback.textContent =
      icon?.iconSource === "text" || icon?.destinationType === "external"
        ? icon.iconText || icon.name?.slice(0, 1) || "A"
        : "◌";
    fallback.style.color =
      icon?.iconSource === "text" || icon?.destinationType === "external"
        ? icon.iconColor || ""
        : "";
    node.append(fallback);
  }
  return node;
}

export function openGlobalIconLibrary() {
  setPageEditing(false);
  const custom = (data.icons || []).filter((icon) => !icon.detectedServiceId);
  const detected = (data.icons || []).filter((icon) => icon.detectedServiceId);
  showModal(
    `<h2>管理图标</h2><div class="sn-library-actions"><button class="sn-editor-save" data-add-icon>添加自定义图标</button></div><section class="sn-editor-section"><h3>自定义图标</h3><div class="sn-icon-library" data-custom-icons></div></section><section class="sn-editor-section"><h3>已探测服务图标</h3><div class="sn-icon-library" data-detected-icons></div></section>`,
  );
  const renderGroup = (box, icons, groupByTarget = false) => {
    box.replaceChildren();
    if (!icons.length) {
      box.innerHTML = '<p class="sn-empty">暂无图标</p>';
      return;
    }
    const renderIcon = (icon, container) => {
      const row = document.createElement("article");
      row.className = "sn-library-icon";
      row.append(iconPreviewNode(icon));
      const body = document.createElement("div");
      body.innerHTML = "<strong></strong>";
      body.querySelector("strong").textContent = icon.name;
      row.append(body);
      const actions = document.createElement("div");
      const edit = document.createElement("button");
      edit.textContent = "编辑";
      edit.addEventListener("click", () => openGlobalIconForm(icon));
      actions.append(edit);
      if (!icon.detectedServiceId) {
        const remove = document.createElement("button");
        remove.className = "danger";
        remove.textContent = "删除";
        remove.addEventListener("click", async () => {
          if (
            !confirm(
              `删除全局图标「${icon.name}」？所有页面上的对应摆放也会一起删除。`,
            )
          )
            return;
          await ownerApi(`/navigation/icons/${icon.id}`, "DELETE");
          location.reload();
        });
        actions.append(remove);
      }
      row.append(actions);
      container.append(row);
    };
    if (!groupByTarget) {
      icons.forEach((icon) => renderIcon(icon, box));
      return;
    }
    const targetById = new Map((data.targetPages || []).map((target) => [target.targetId, target]));
    const groups = new Map();
    for (const icon of icons) {
      const targetId = icon.services?.[0]?.targetId || "unassigned";
      if (!groups.has(targetId)) groups.set(targetId, []);
      groups.get(targetId).push(icon);
    }
    const orderedIds = [
      ...(data.targetPages || []).map((target) => target.targetId).filter((id) => groups.has(id)),
      ...[...groups.keys()].filter((id) => !targetById.has(id)),
    ];
    for (const targetId of orderedIds) {
      const target = targetById.get(targetId);
      const group = document.createElement("section");
      group.className = "sn-icon-library-target";
      const heading = document.createElement("header");
      heading.innerHTML = "<strong></strong><small></small>";
      heading.querySelector("strong").textContent = target?.name || groups.get(targetId)[0].services?.[0]?.targetLabel || "未命名扫描目标";
      heading.querySelector("small").textContent = target?.address || groups.get(targetId)[0].services?.[0]?.targetAddress || "";
      const entries = document.createElement("div");
      entries.className = "sn-icon-library-target-entries";
      groups.get(targetId).forEach((icon) => renderIcon(icon, entries));
      group.append(heading, entries);
      box.append(group);
    }
  };
  renderGroup(modalContent.querySelector("[data-custom-icons]"), custom);
  renderGroup(modalContent.querySelector("[data-detected-icons]"), detected, true);
  modalContent
    .querySelector("[data-add-icon]")
    .addEventListener("click", () => openGlobalIconForm(null));
}

export function openGlobalIconForm(icon, restoreFile = false, onSaved = null, draft = null) {
  if (!restoreFile) iconEdit.file = null;
  const detected = Boolean(icon?.detectedServiceId);
  const current = { ...(icon || {
    name: "",
    iconSource: "text",
    iconText: "A",
    iconColor: "#4f7cff",
    serviceIds: [],
    destinationType: "service",
    externalUrl: "",
  }), ...(draft || {}) };
  showModal(
    `<h2>${detected ? "编辑已探测服务图标" : icon ? "编辑自定义图标" : "添加自定义图标"}</h2><form class="sn-icon-form" data-icon-form><label>名称<input name="name" required></label><label>目标类型<select name="destination"><option value="service">关联服务</option><option value="external">外部链接</option></select></label><label>图标类型<select name="source"><option value="text">文字图标</option><option value="favicon">服务 favicon</option><option value="custom">上传图片</option></select></label><div data-destination-fields></div><div data-source-fields></div><button class="sn-icon-preview" type="button" data-icon-preview><span data-icon-preview-image></span><span>图标预览</span><small>点击查看各尺寸效果</small></button><div><button class="sn-editor-save" type="submit">保存</button></div></form>`,
    "icon-form",
  );
  const form = modalContent.querySelector("[data-icon-form]");
  form.name.value = current.name;
  form.source.value = ["text", "favicon", "custom"].includes(current.iconSource)
    ? current.iconSource
    : "text";
  form.destination.value = detected ? "service" : current.destinationType || "service";
  form.destination.disabled = detected;
  const linkedServices = () => (data.services || []).filter((entry) =>
    detected
      ? entry.id === current.detectedServiceId
      : [...form.querySelectorAll("[data-icon-services] input:checked")].some((input) => input.value === entry.id),
  );
  const destinationFields = () => {
    const box = form.querySelector("[data-destination-fields]");
    box.replaceChildren();
    form.querySelector('[name="source"] option[value="favicon"]').textContent =
      form.destination.value === "external" ? "网站 favicon" : "服务 favicon";
    if (form.destination.value === "external") {
      box.innerHTML = '<label>外部链接 URL<input name="external-url" type="url" required placeholder="https://example.com"></label>';
      box.querySelector('[name="external-url"]').value = current.externalUrl || "";
      return;
    }
    box.innerHTML = detected
      ? '<section class="sn-icon-services sn-locked-service-field"><span>关联服务</span><div data-icon-services></div></section>'
      : '<label class="sn-icon-services">关联服务<div data-icon-services></div></label>';
    const servicesBox = box.querySelector("[data-icon-services]");
    if (detected) {
      const service = (data.services || []).find((entry) => entry.id === current.detectedServiceId);
      servicesBox.innerHTML = `<article class="sn-locked-service"><strong>${escapeHtml(serviceName(service || {}))}</strong><small>${service?.serviceType === "http" ? "HTTP 服务" : "端口服务"} · TCP/${service?.port || "—"}</small><small>${escapeHtml(service?.targetLabel || "未命名扫描目标")}${service?.targetAddress ? ` · ${escapeHtml(service.targetAddress)}` : ""}</small></article>`;
      return;
    }
    const targets = data.targetPages || [];
    const targetById = new Map(targets.map((target) => [target.targetId, target]));
    const grouped = new Map();
    for (const service of data.services || []) {
      const key = service.targetId || "unassigned";
      if (!grouped.has(key)) grouped.set(key, { target: targetById.get(key), services: [] });
      grouped.get(key).services.push(service);
    }
    const groups = [
      ...targets.filter((target) => grouped.has(target.targetId)).map((target) => grouped.get(target.targetId)),
      ...[...grouped.entries()].filter(([id]) => !targetById.has(id)).map(([, group]) => group),
    ];
    for (const group of groups) {
      const section = document.createElement("section");
      section.className = "sn-icon-service-target";
      section.innerHTML = "<header><strong></strong><small></small></header><div class=\"sn-icon-service-list\"></div>";
      section.querySelector("strong").textContent = group.target?.name || group.services[0]?.targetLabel || "未命名扫描目标";
      section.querySelector("small").textContent = group.target?.address || group.services[0]?.targetAddress || "";
      const list = section.querySelector(".sn-icon-service-list");
      for (const service of group.services) {
        const label = document.createElement("label");
        label.innerHTML = '<input type="checkbox"><span></span>';
        const input = label.querySelector("input");
        input.value = service.id;
        input.checked = current.serviceIds.includes(service.id);
        label.querySelector("span").textContent = `${service.serviceType === "http" ? "HTTP" : "端口"} · ${serviceName(service)} · ${service.port}`;
        list.append(label);
      }
      servicesBox.append(section);
    }
  };
  destinationFields();
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
      if (form.destination.value === "external") return current.iconUrl || "";
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
        form.source.value === "text" || form.destination.value === "external"
          ? form.querySelector('[name="text"]')?.value ||
            current.name.slice(0, 1) ||
            "A"
          : "◌";
      fallback.style.color =
        form.source.value === "text" || form.destination.value === "external"
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
    if (form.source.value === "favicon" && form.destination.value === "service")
      box.insertAdjacentHTML(
        "beforeend",
        '<label>favicon 服务<select name="favicon"></select></label>',
      );
    if (form.source.value === "favicon" && form.destination.value === "external")
      box.insertAdjacentHTML(
        "beforeend",
        '<p class="sn-modal-note">保存时会自动抓取网站 favicon；抓取失败时显示文字占位图标。</p>',
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
      for (const service of linkedServices()) {
        const option = document.createElement("option");
        option.value = service.id;
        option.textContent = `${serviceName(service)} · ${service.targetAddress || service.targetLabel || service.port}`;
        favicon.append(option);
      }
      if (!favicon.options.length)
        favicon.append(new Option("当前关联服务暂无 favicon", ""));
      favicon.value =
        current.faviconServiceId &&
        linkedServices().some((entry) => entry.id === current.faviconServiceId)
          ? current.faviconServiceId
          : linkedServices()[0]?.id || "";
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
  form.destination.addEventListener("change", () => {
    if (form.destination.value === "external" && form.source.value === "text")
      form.source.value = "favicon";
    destinationFields();
    sourceFields();
  });
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
          destinationType: form.destination.value,
          externalUrl: form.querySelector('[name="external-url"]')?.value || "",
          serviceIds: form.destination.value === "service" && detected
            ? [current.detectedServiceId]
            : form.destination.value === "service" ? [...form.querySelectorAll("[data-icon-services] input:checked")].map(
                (input) => input.value,
              ) : [],
          iconUrl: previewUrl(),
        },
        icon,
        restoreFile,
        onSaved,
      ),
    );
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const destinationType = form.destination.value;
    const serviceIds = destinationType === "service" && detected
      ? [current.detectedServiceId]
      : destinationType === "service" ? [...form.querySelectorAll("[data-icon-services] input:checked")].map(
          (input) => input.value,
        ) : [];
    const body = {
      name: form.name.value,
      iconSource: form.source.value,
      iconText: form.querySelector('[name="text"]')?.value || "",
      iconColor: form.querySelector('[name="color"]')?.value || "",
      faviconServiceId:
        destinationType === "service" && form.source.value === "favicon"
          ? form.querySelector('[name="favicon"]')?.value || ""
          : "",
      serviceIds,
      detectedServiceId: detected ? current.detectedServiceId : "",
      destinationType,
      externalUrl: destinationType === "external"
        ? form.querySelector('[name="external-url"]')?.value || ""
        : "",
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
      body.querySelector("small").textContent =
        draft.destinationType === "external" ? "外部链接" : "tcp/4000 · 网页 服务";
      const status = document.createElement("em");
      status.textContent = draft.destinationType === "external" ? "打开链接" : "在线";
      card.append(body, status);
    }
    box.append(card);
  }
  modalContent
    .querySelector("[data-return-icon-form]")
    .addEventListener("click", () => openGlobalIconForm(icon, true, onSaved, draft));
}
