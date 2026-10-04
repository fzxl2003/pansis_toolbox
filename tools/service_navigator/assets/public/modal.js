import { modal, modalContent } from "./state.js";

export const iconEdit = { file: null, previewUrl: "" };
export function showModal(markup, className = "") {
  modalContent.closest(".sn-public-modal-card")?.classList.toggle(
    "sn-icon-form-modal",
    className === "icon-form",
  );
  modalContent.innerHTML = markup;
  modal.hidden = false;
}
export function closeModal() {
  if (iconEdit.previewUrl) {
    URL.revokeObjectURL(iconEdit.previewUrl);
    iconEdit.previewUrl = "";
  }
  iconEdit.file = null;
  modal.hidden = true;
  modalContent.replaceChildren();
}
