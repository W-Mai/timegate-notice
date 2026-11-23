const form = document.querySelector("form");
const input = document.querySelector("#message");
const color = document.querySelector("#color");
const button = form.querySelector('button[type="submit"]');
const status = document.querySelector(".status");
const gate = document.querySelector(".gate");
const panels = gate.querySelectorAll(".lcd");
const stickerFile = document.querySelector("#sticker-file");
const chooseSticker = document.querySelector("#choose-sticker");
const removeSticker = document.querySelector("#remove-sticker");
const stickerName = document.querySelector("#sticker-name");
const stickerImage = document.createElement("img");
stickerImage.alt = "";

let previewDelay;
let revision = 0;
let active = false;
let valid = false;
let previewError = "";
let sending = false;
let uploading = false;
let sticker = null;
const phases = {
  idle: "",
  preparing: "正在保存当前屏幕…",
  downloading: "时光之门正在下载动画…",
  recovering: "正在等待恢复原屏幕…",
  restoring: "正在恢复原屏幕…",
  "restore-retry": "设备暂未完成恢复，正在重试…",
  restored: "30 秒已结束，设备已接受恢复指令。",
  "send-error": "动画发送失败，设备已接受恢复指令。",
  "light-error": "灯光设置失败，设备已接受屏幕恢复指令。",
  "layout-error": "无法读取当前屏幕布局，发送已取消。",
};
const busyPhases = new Set(["preparing", "downloading", "playing", "recovering", "restoring", "restore-retry"]);

function displayStatus(message, error = false) {
  if (status.textContent !== message) status.textContent = message;
  status.dataset.error = String(error);
}

function updateButton() {
  button.disabled = !valid || active || sending || uploading;
}

function payload() {
  return {text: input.value.trim(), color: color.value, sticker: sticker?.id};
}

async function result(response) {
  const data = await response.json();
  if (!response.ok) throw new Error(data.error);
  return data;
}

async function post(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body),
  });
  return result(response);
}

function showPreview(image, panel) {
  if (image) gate.style.setProperty("--preview", `url("${image}")`);
  else gate.style.removeProperty("--preview");
  stickerImage.parentElement?.classList.remove("has-sticker");
  if (sticker && Number.isInteger(panel) && panels[panel]) {
    panels[panel].classList.add("has-sticker");
    if (stickerImage.parentElement !== panels[panel]) panels[panel].append(stickerImage);
  } else {
    stickerImage.remove();
  }
}

async function preview() {
  const requesting = ++revision;
  valid = false;
  previewError = "";
  updateButton();
  const body = payload();
  if (!body.text && !body.sticker) {
    showPreview();
    return;
  }
  try {
    const data = await post("/api/preview", body);
    if (requesting !== revision) return;
    showPreview(data.image, data.stickerPanel);
    valid = true;
    if (!active && !sending && !uploading) displayStatus("");
  } catch (error) {
    if (requesting !== revision) return;
    showPreview();
    previewError = error.message;
    if (!active && !sending) displayStatus(previewError, true);
  }
  updateButton();
}

input.addEventListener("input", () => {
  clearTimeout(previewDelay);
  ++revision;
  valid = false;
  updateButton();
  previewDelay = setTimeout(preview, 110);
});
color.addEventListener("input", () => gate.style.setProperty("--light", color.value));
chooseSticker.addEventListener("click", () => stickerFile.click());
stickerFile.addEventListener("change", async () => {
  const file = stickerFile.files[0];
  stickerFile.value = "";
  if (!file) return;
  if (file.size > 4 * 1024 * 1024) {
    previewError = "表情包最大 4 MB";
    displayStatus(previewError, true);
    return;
  }
  uploading = true;
  chooseSticker.disabled = removeSticker.disabled = true;
  updateButton();
  displayStatus("正在处理表情包…");
  try {
    const response = await fetch("/api/sticker", {method: "POST", body: file});
    sticker = await result(response);
    stickerImage.src = sticker.image;
    stickerName.textContent = file.name;
    removeSticker.hidden = false;
    clearTimeout(previewDelay);
    await preview();
    if (!previewError && !active) displayStatus("");
  } catch (error) {
    previewError = error.message;
    displayStatus(previewError, true);
  } finally {
    uploading = false;
    chooseSticker.disabled = removeSticker.disabled = false;
    updateButton();
  }
});
removeSticker.addEventListener("click", () => {
  sticker = null;
  stickerImage.removeAttribute("src");
  stickerName.textContent = "";
  removeSticker.hidden = true;
  preview();
});

async function refresh() {
  if (sending) return;
  try {
    const response = await fetch("/api/status");
    if (!response.ok) return;
    const snapshot = await response.json();
    if (sending) return;
    active = busyPhases.has(snapshot.phase);
    const playing = snapshot.phase === "playing";
    gate.classList.toggle("is-playing", playing);
    updateButton();
    if (playing) {
      const remaining = Math.max(0, Math.ceil(snapshot.deadline - Date.now() / 1000));
      const warning = snapshot.lightError ? "灯光设置失败。" : "";
      displayStatus(`${warning}显示中 · ${remaining} 秒后恢复`, snapshot.lightError);
    } else if (snapshot.phase === "downloading") {
      displayStatus(`正在下载动画 · ${snapshot.fetched || 0}/5`);
    } else if ((!previewError && !uploading) || active) {
      displayStatus(phases[snapshot.phase] || "", snapshot.phase.endsWith("error"));
    }
  } catch {
    if (!sending) displayStatus("暂时连不上服务", true);
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!valid || active || sending || uploading) return;
  sending = true;
  updateButton();
  displayStatus("正在发送…");
  try {
    await post("/api/send", payload());
    active = true;
  } catch (error) {
    displayStatus(error.message, true);
  } finally {
    sending = false;
    updateButton();
  }
});

updateButton();
refresh();
setInterval(refresh, 750);
