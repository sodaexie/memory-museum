"use strict";

const draftImages = [];
const draftLinks = [];
const maxImages = 6;
const maxLinks = 6;
const maxImageBytes = 5 * 1024 * 1024;
const maxTotalImageBytes = 15 * 1024 * 1024;
const supportedImageTypes = new Set(["image/jpeg", "image/png", "image/gif", "image/webp"]);

function webLink(value) {
  if (typeof value !== "string" || !/^https?:\/\//i.test(value)) return null;
  try {
    const url = new URL(value);
    return url.username || url.password || url.port === "0" || url.href.length > 2048 ? null : url;
  } catch {
    return null;
  }
}

function renderAttachmentList(id, items, makeItem) {
  const list = byId(id);
  list.replaceChildren(...items.map(makeItem));
  list.hidden = !list.childElementCount;
}

function picturePreview(src, alt) {
  const image = element("img");
  image.src = src;
  image.alt = alt;
  image.loading = "lazy";
  return image;
}

function renderDraftAttachments() {
  renderAttachmentList("draft-images", draftImages, ({ file, preview }, index) => {
    const item = element("li", "attachment-picture attachment-draft");
    const remove = action("移除", () => {
      URL.revokeObjectURL(preview);
      draftImages.splice(index, 1);
      renderDraftAttachments();
      byId("attach-image").focus();
    }, "text-button attachment-remove");
    remove.setAttribute("aria-label", `移除图片 ${file.name}`);
    item.append(picturePreview(preview, file.name), remove);
    return item;
  });
  renderAttachmentList("draft-links", draftLinks, (url, index) => {
    const item = element("li", "attachment-link-row");
    const remove = action("移除", () => {
      draftLinks.splice(index, 1);
      renderDraftAttachments();
      byId("attach-link").focus();
    }, "text-button");
    remove.setAttribute("aria-label", `移除链接 ${url.href}`);
    item.append(element("span", "attachment-url", url.href), remove);
    return item;
  });
}

function addDraftImages(files) {
  const bytes = [...draftImages.map(({ file }) => file), ...files].reduce((sum, file) => sum + file.size, 0);
  if (files.length + draftImages.length > maxImages || bytes > maxTotalImageBytes) {
    feedback("save-status", "每条日记最多 6 张图片，合计最多 15 MB。", true);
    return;
  }
  for (const file of files) {
    if (!file.size || file.size > maxImageBytes) {
      feedback("save-status", "请选择可读取、且不超过 5 MB 的图片。", true);
      return;
    }
    if (!supportedImageTypes.has(file.type) && !/\.(jpe?g|png|gif|webp)$/i.test(file.name)) {
      feedback("save-status", "请选择 JPG、PNG、GIF 或 WebP 图片。", true);
      return;
    }
  }
  draftImages.push(...files.map((file) => ({ file, preview: URL.createObjectURL(file) })));
  renderDraftAttachments();
  feedback("save-status");
}

function showLinkEditor(open, focus = true) {
  byId("link-editor").hidden = !open;
  byId("attach-link").setAttribute("aria-expanded", String(open));
  if (focus) byId(open ? "entry-link" : "attach-link").focus();
}

function addDraftLink(focus = true) {
  const input = byId("entry-link");
  const text = input.value.trim();
  const match = text.match(/https?:\/\/[^\s<>"'《》「」【】]+/i)?.[0];
  const candidate = match === text ? match : match?.replace(/[。！，；、）】”’？)\]}.,;!?]+$/u, "");
  const url = webLink(candidate);
  if (!url) {
    feedback("save-status", "请粘贴一个有效的网址，或带网址的帖子分享文字。", true);
    input.focus();
    return false;
  }
  if (!draftLinks.some((item) => item.href === url.href)) {
    if (draftLinks.length >= maxLinks) {
      feedback("save-status", "每条日记最多 6 个链接。", true);
      return false;
    }
    draftLinks.push(url);
  }
  input.value = "";
  showLinkEditor(false, focus);
  renderDraftAttachments();
  feedback("save-status");
  return true;
}

function encodeDraftPicture({ file }) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve({ name: file.name, data: reader.result.split(",", 2)[1] });
    reader.onerror = reader.onabort = () => reject(new Error("图片无法读取，请移除后重新添加。"));
    reader.readAsDataURL(file);
  });
}

function clearDraftAttachments() {
  for (const { preview } of draftImages) URL.revokeObjectURL(preview);
  draftImages.length = 0;
  draftLinks.length = 0;
  byId("entry-link").value = "";
  showLinkEditor(false, false);
  renderDraftAttachments();
}

function openAttachment(label, url, className) {
  const item = link(label, url, className);
  item.target = "_blank";
  item.rel = "noopener noreferrer";
  return item;
}

function renderEntryAttachments(entry) {
  const images = (entry?.attachments?.images ?? []).filter((image) =>
    typeof image?.file === "string" && /^[1-9][0-9]*\.(jpg|png|gif|webp)$/.test(image.file));
  const urls = (entry?.attachments?.links ?? []).map(webLink).filter(Boolean);
  renderAttachmentList("detail-images", images, (image) => {
    const item = element("li", "attachment-picture");
    const filename = encodeURIComponent(image.file);
    const src = `/api/entry/${encodeURIComponent(entry.id)}/images/${filename}`;
    const preview = openAttachment("", src, "attachment-image-link");
    preview.append(picturePreview(src, image.name || "日记图片"));
    item.append(preview);
    return item;
  });
  renderAttachmentList("detail-links", urls, (url) => {
    const item = element("li", "attachment-link-row");
    const anchor = openAttachment("", url.href, "attachment-source");
    anchor.append(element("span", "attachment-host", url.hostname), element("span", "attachment-url", url.href));
    item.append(anchor);
    return item;
  });
  byId("detail-attachments").hidden = !(images.length || urls.length);
}

function initAttachments() {
  const picker = byId("entry-images");
  byId("attach-image").addEventListener("click", () => picker.click());
  picker.addEventListener("change", () => {
    const files = [...picker.files];
    picker.value = "";
    addDraftImages(files);
  });
  byId("attach-link").addEventListener("click", () => showLinkEditor(byId("link-editor").hidden));
  byId("confirm-link").addEventListener("click", () => addDraftLink());
  byId("entry-link").addEventListener("keydown", (event) => {
    if (event.isComposing) return;
    if (event.key === "Enter") {
      event.preventDefault();
      addDraftLink();
    }
    if (event.key === "Escape") showLinkEditor(false);
  });
}
