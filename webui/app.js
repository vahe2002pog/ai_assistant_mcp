// Чат-фронтенд Компаса. Хранит историю в БД, синхронизируется через SSE.

const $ = (sel) => document.querySelector(sel);
const messagesEl = $("#messages");
const inputEl = $("#input");
const sendBtn = $("#send");
const form = $("#composer");
const statusBar = $("#status-bar");
const statusText = $("#status-text");
const connStatus = $("#conn-status");
const historyEl = $("#history");
const newChatBtn = $("#new-chat");
const attachBtn = $("#attach-btn");
const fileInput = $("#file-input");
const attachPreview = $("#attach-preview");

let currentConvId = null;
const inflightConvs = new Set();      // conv_id'шники с активным запросом
const abortCtrls = new Map();         // conv_id → AbortController
let pendingNewSend = null;            // { ctrl } — отправка в ещё-не-созданный чат
let lastStatusText = "";
let pendingImages = [];
let lastLocalMsgKey = null; // для подавления эха из SSE

function isBusyConv(id) {
  if (id == null) return pendingNewSend != null;
  return inflightConvs.has(id);
}
function isCurrentBusy() { return isBusyConv(currentConvId); }

const SEND_ICON = `<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="19" x2="12" y2="5"/><polyline points="5 12 12 5 19 12"/></svg>`;
const STOP_ICON = `<svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>`;

function refreshSendBtn() {
  const showStop = isCurrentBusy();
  if (showStop) {
    sendBtn.classList.add("stop");
    sendBtn.title = "Прервать";
    sendBtn.innerHTML = STOP_ICON;
    sendBtn.type = "button";
  } else {
    sendBtn.classList.remove("stop");
    sendBtn.title = "Отправить";
    sendBtn.innerHTML = SEND_ICON;
    sendBtn.type = "submit";
  }
  inputEl.disabled = showStop;
  inputEl.placeholder = showStop
    ? "Дождись ответа или прерви запрос…"
    : "Напиши сообщение…";
}

function autosize() {
  inputEl.style.height = "auto";
  inputEl.style.height = Math.min(inputEl.scrollHeight, 220) + "px";
}
inputEl.addEventListener("input", autosize);

function clearWelcome() {
  const w = messagesEl.querySelector(".welcome");
  if (w) w.remove();
}

function showWelcome() {
  messagesEl.innerHTML = `
    <div class="welcome">
      <h1>Чем могу помочь?</h1>
      <p>Открою приложение, найду файл, что-то в интернете или управлю браузером.</p>
    </div>`;
}

function el(tag, cls, text) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function appendMessage(role, text, response = null, attachments = []) {
  clearWelcome();
  const msg = document.createElement("div");
  msg.className = `msg ${role}`;
  const avatar = role === "user" ? "Вы" : "";
  msg.innerHTML = `
    <div class="avatar">${avatar}</div>
    <div class="body">
      <div class="role">${role === "user" ? "Вы" : "Компас"}</div>
      <div class="content"></div>
    </div>`;
  const content = msg.querySelector(".content");
  if (text) content.textContent = text;
  else content.style.display = "none";
  const body = msg.querySelector(".body");
  if (attachments && attachments.length) {
    const row = el("div", "attach-row");
    attachments.forEach(a => {
      const img = document.createElement("img");
      img.src = a.url; img.className = "attach-thumb"; img.alt = a.name || "";
      row.appendChild(img);
    });
    body.insertBefore(row, content);
  }
  if (role === "assistant" && response && response.screen && Array.isArray(response.screen.blocks)) {
    const screenEl = renderScreen(response.screen.blocks);
    if (screenEl) body.appendChild(screenEl);
  }
  messagesEl.appendChild(msg);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return msg;
}

function renderScreen(blocks) {
  if (!blocks.length) return null;
  const wrap = document.createElement("div");
  wrap.className = "screen";
  for (const b of blocks) {
    const ele = renderBlock(b);
    if (ele) wrap.appendChild(ele);
  }
  return wrap.children.length ? wrap : null;
}

function renderBlock(b) {
  const box = el("div", "block block-" + (b.type || "text"));
  if (b.title) box.appendChild(el("div", "block-title", b.title));
  switch (b.type) {
    case "text":
      box.appendChild(el("div", "block-text", b.text || ""));
      break;
    case "list": {
      const ul = el("ul", "block-list");
      (b.items || []).forEach(i => ul.appendChild(el("li", "", String(i))));
      box.appendChild(ul);
      break;
    }
    case "table": {
      const rows = b.rows || [];
      if (!rows.length) return null;
      const cols = Array.from(rows.reduce((s, r) => {
        Object.keys(r || {}).forEach(k => s.add(k)); return s;
      }, new Set()));
      const table = el("table", "block-table");
      const thead = el("thead"); const trh = el("tr");
      cols.forEach(c => trh.appendChild(el("th", "", c)));
      thead.appendChild(trh); table.appendChild(thead);
      const tbody = el("tbody");
      rows.forEach(r => {
        const tr = el("tr");
        cols.forEach(c => tr.appendChild(el("td", "", r[c] !== undefined ? String(r[c]) : "")));
        tbody.appendChild(tr);
      });
      table.appendChild(tbody); box.appendChild(table);
      break;
    }
    case "links": {
      const ul = el("ul", "block-links");
      (b.links || []).forEach(url => {
        const li = el("li");
        const a = el("a", "", url);
        a.href = url; a.target = "_blank"; a.rel = "noreferrer noopener";
        li.appendChild(a); ul.appendChild(li);
      });
      box.appendChild(ul); break;
    }
    case "files": {
      const ul = el("ul", "block-files");
      (b.file_paths || []).forEach(p => ul.appendChild(el("li", "", p)));
      box.appendChild(ul); break;
    }
    default: return null;
  }
  return box;
}

function setStatus(text) {
  lastStatusText = text || "";
  applyStatus();
}

function applyStatus() {
  const visible = lastStatusText && isCurrentBusy();
  if (!visible) { statusBar.classList.add("hidden"); return; }
  statusText.textContent = lastStatusText;
  statusBar.classList.remove("hidden");
}

// ── SSE ──────────────────────────────────────────────────────────────
let evtSource = null;
function connectEvents() {
  try { if (evtSource) evtSource.close(); } catch (_) {}
  evtSource = new EventSource("/api/events");
  evtSource.onopen = () => {
    connStatus.textContent = "● онлайн";
    connStatus.className = "conn-status online";
  };
  evtSource.onerror = () => {
    connStatus.textContent = "● оффлайн";
    connStatus.className = "conn-status offline";
    setTimeout(connectEvents, 2000);
  };
  evtSource.addEventListener("status", (e) => {
    try { setStatus(JSON.parse(e.data).text || ""); } catch (_) {}
  });
  evtSource.addEventListener("conv_created", () => loadConversations());
  evtSource.addEventListener("conv_updated", () => loadConversations());
  evtSource.addEventListener("conv_deleted", (e) => {
    try {
      const { id } = JSON.parse(e.data);
      if (id === currentConvId) { currentConvId = null; showWelcome(); }
      loadConversations();
    } catch (_) {}
  });
  evtSource.addEventListener("config_updated", (e) => {
    try {
      const cfg = JSON.parse(e.data);
      currentCfg = cfg;
      updatePill(cfg);
      if (!settingsPanel.classList.contains("hidden")) {
        selProvider.value = cfg.provider;
        selBase.value = cfg.base_url;
      }
    } catch (_) {}
  });
  evtSource.addEventListener("msg_added", (e) => {
    try {
      const d = JSON.parse(e.data);
      // Если мы ждём conv_id для только что отправленного сообщения — усыновим его.
      if (pendingNewSend && currentConvId == null && d.conversation_id != null) {
        const id = d.conversation_id;
        abortCtrls.set(id, pendingNewSend.ctrl);
        inflightConvs.add(id);
        pendingNewSend = null;
        currentConvId = id;
        refreshSendBtn();
        applyStatus();
        loadConversations();
      }
      if (d.conversation_id !== currentConvId) return;
      const key = `${d.role}|${(d.content || "").slice(0, 80)}`;
      if (key === lastLocalMsgKey) { lastLocalMsgKey = null; return; }
      appendMessage(d.role, d.content || "", d.response || null, d.attachments || []);
    } catch (_) {}
  });
}
connectEvents();

// ── История ──────────────────────────────────────────────────────────
async function loadConversations() {
  try {
    const r = await fetch("/api/conversations");
    const { items } = await r.json();
    renderHistory(items || []);
  } catch (_) {}
}

function renderHistory(items) {
  historyEl.innerHTML = "";
  items.forEach(c => {
    const item = document.createElement("div");
    item.className = "history-item" + (c.id === currentConvId ? " active" : "");
    item.dataset.id = c.id;
    const title = el("span", "h-title");
    title.textContent = c.title || "Без названия";
    const del = el("button", "h-del", "×");
    del.type = "button"; del.title = "Удалить";
    item.appendChild(title); item.appendChild(del);
    item.addEventListener("click", (e) => {
      if (e.target === del) return;
      openConversation(c.id);
    });
    del.addEventListener("click", async (e) => {
      e.stopPropagation();
      await fetch(`/api/conversations/${c.id}`, { method: "DELETE" });
    });
    historyEl.appendChild(item);
  });
}

async function openConversation(id) {
  currentConvId = id;
  refreshSendBtn();
  applyStatus();
  messagesEl.innerHTML = "";
  try {
    const r = await fetch(`/api/conversations/${id}/messages`);
    const { items } = await r.json();
    if (!items.length) showWelcome();
    items.forEach(m => appendMessage(m.role, m.content || "", m.response || null, m.attachments || []));
  } catch (_) {}
  historyEl.querySelectorAll(".history-item").forEach(el => {
    el.classList.toggle("active", Number(el.dataset.id) === id);
  });
}

newChatBtn.addEventListener("click", () => {
  currentConvId = null;
  messagesEl.innerHTML = "";
  showWelcome();
  refreshSendBtn();
  applyStatus();
  historyEl.querySelectorAll(".history-item").forEach(el => el.classList.remove("active"));
  inputEl.focus();
});

// ── Изображения ──────────────────────────────────────────────────────
attachBtn.addEventListener("click", () => fileInput.click());
fileInput.addEventListener("change", async (e) => {
  const files = Array.from(e.target.files || []);
  for (const f of files) {
    if (!f.type.startsWith("image/")) continue;
    const dataUrl = await new Promise(res => {
      const r = new FileReader();
      r.onload = () => res(r.result);
      r.readAsDataURL(f);
    });
    pendingImages.push({ dataUrl, name: f.name });
  }
  renderAttachPreview();
  fileInput.value = "";
});

inputEl.addEventListener("paste", (e) => {
  const items = e.clipboardData?.items || [];
  for (const it of items) {
    if (it.type && it.type.startsWith("image/")) {
      const blob = it.getAsFile(); if (!blob) continue;
      const r = new FileReader();
      r.onload = () => {
        pendingImages.push({ dataUrl: r.result, name: blob.name || "paste.png" });
        renderAttachPreview();
      };
      r.readAsDataURL(blob);
      e.preventDefault();
    }
  }
});

function renderAttachPreview() {
  attachPreview.innerHTML = "";
  pendingImages.forEach((img, idx) => {
    const box = el("div", "preview-item");
    const i = document.createElement("img");
    i.src = img.dataUrl;
    const rm = el("button", "preview-rm", "×");
    rm.type = "button";
    rm.addEventListener("click", () => {
      pendingImages.splice(idx, 1);
      renderAttachPreview();
    });
    box.appendChild(i); box.appendChild(rm);
    attachPreview.appendChild(box);
  });
  attachPreview.classList.toggle("empty", pendingImages.length === 0);
}

// ── Отправка ─────────────────────────────────────────────────────────
async function send(text) {
  if (!text.trim() && !pendingImages.length) return;
  if (isCurrentBusy()) return;

  const ctrl = new AbortController();
  const sendConvId = currentConvId;     // захватываем id до await'ов
  if (sendConvId == null) {
    pendingNewSend = { ctrl };
  } else {
    inflightConvs.add(sendConvId);
    abortCtrls.set(sendConvId, ctrl);
  }
  refreshSendBtn();

  const imgs = pendingImages.slice();
  pendingImages = [];
  renderAttachPreview();

  appendMessage("user", text, null, imgs.map(i => ({ url: i.dataUrl, name: i.name })));
  lastLocalMsgKey = `user|${(text || "").slice(0, 80)}`;
  setStatus("Думаю…");

  try {
    const resp = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      signal: ctrl.signal,
      body: JSON.stringify({
        message: text,
        conversation_id: sendConvId,
        images: imgs.map(i => i.dataUrl),
      }),
    });
    const data = await resp.json();
    if (data.error) {
      appendMessage("assistant", "[Ошибка] " + data.error);
    } else if (data.conversation_id) {
      loadConversations();
    }
  } catch (e) {
    if (e.name !== "AbortError") {
      appendMessage("assistant", "[Сетевая ошибка] " + e.message);
    }
  } finally {
    // Вычисляем фактический id (мог прийти через SSE-adoption).
    let finishedId = sendConvId;
    if (finishedId == null && pendingNewSend && pendingNewSend.ctrl === ctrl) {
      pendingNewSend = null;
    } else if (finishedId == null) {
      // adoption произошёл — ищем id по controller'у
      for (const [id, c] of abortCtrls) if (c === ctrl) { finishedId = id; break; }
    }
    if (finishedId != null) {
      inflightConvs.delete(finishedId);
      abortCtrls.delete(finishedId);
    }
    setStatus("");
    refreshSendBtn();
    applyStatus();
    if (!inputEl.disabled) inputEl.focus();
  }
}

async function cancelRequest() {
  const id = currentConvId;
  const ctrl = id != null ? abortCtrls.get(id) : pendingNewSend?.ctrl;
  try {
    await fetch("/api/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ conversation_id: id }),
    });
  } catch (_) {}
  try { if (ctrl) ctrl.abort(); } catch (_) {}
}

sendBtn.addEventListener("click", (e) => {
  if (isCurrentBusy()) {
    e.preventDefault();
    cancelRequest();
  }
});

form.addEventListener("submit", (e) => {
  e.preventDefault();
  if (isCurrentBusy()) return;
  const text = inputEl.value.trim();
  if (!text && !pendingImages.length) return;
  inputEl.value = "";
  autosize();
  send(text);
});

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

// ── Настройки модели ───────────────────────────────────────────────────
const modelPill = $("#model-pill");
const settingsPanel = $("#settings-panel");
const selProvider = $("#sel-provider");
const selBase = $("#sel-base");
const selModel = $("#sel-model");
const selModelCustom = $("#sel-model-custom");
const refreshModelsBtn = $("#refresh-models");
const saveBtn = $("#settings-save");
const cancelBtn = $("#settings-cancel");

let providers = [];
let currentCfg = null;

function updatePill(cfg) {
  if (!cfg) return;
  const prov = providers.find(p => p.id === cfg.provider);
  $("#provider-name").textContent = prov ? prov.label : cfg.provider;
  $("#model-name").textContent = cfg.model || "—";
}

async function loadConfig() {
  try {
    const r = await fetch("/api/config");
    const d = await r.json();
    providers = d.providers || [];
    currentCfg = d.config;
    selProvider.innerHTML = providers.map(p => `<option value="${p.id}">${p.label}</option>`).join("");
    selProvider.value = currentCfg.provider;
    selBase.value = currentCfg.base_url;
    updatePill(currentCfg);
    await refreshModels();
  } catch (_) {}
}

async function refreshModels() {
  selModel.innerHTML = `<option value="">(загрузка…)</option>`;
  try {
    const r = await fetch("/api/models?base_url=" + encodeURIComponent(selBase.value));
    const d = await r.json();
    const list = d.models || [];
    if (!list.length) {
      selModel.innerHTML = `<option value="">(не найдено)</option>`;
    } else {
      selModel.innerHTML = list.map(m => `<option value="${m}">${m}</option>`).join("");
      if (currentCfg && list.includes(currentCfg.model)) {
        selModel.value = currentCfg.model;
      }
    }
  } catch (_) {
    selModel.innerHTML = `<option value="">(ошибка)</option>`;
  }
}

selProvider.addEventListener("change", () => {
  const p = providers.find(x => x.id === selProvider.value);
  if (p) selBase.value = p.base_url;
  refreshModels();
});
refreshModelsBtn.addEventListener("click", refreshModels);
selBase.addEventListener("change", refreshModels);

modelPill.addEventListener("click", (e) => {
  e.stopPropagation();
  settingsPanel.classList.toggle("hidden");
});
document.addEventListener("click", (e) => {
  if (!settingsPanel.contains(e.target) && e.target !== modelPill) {
    settingsPanel.classList.add("hidden");
  }
});
cancelBtn.addEventListener("click", () => settingsPanel.classList.add("hidden"));

saveBtn.addEventListener("click", async () => {
  const model = (selModelCustom.value.trim() || selModel.value || "").trim();
  const body = {
    provider: selProvider.value,
    base_url: selBase.value.trim(),
    model,
  };
  try {
    const r = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    currentCfg = d.config;
    updatePill(currentCfg);
    selModelCustom.value = "";
    settingsPanel.classList.add("hidden");
  } catch (err) {
    alert("Ошибка: " + err.message);
  }
});

loadConfig();
loadConversations();

// ── Контекстное меню для сообщений ──────────────────────────────────
const ctxMenu = $("#ctx-menu");
let ctxTarget = null;    // .msg element
let ctxSelection = "";   // выделенный текст
let ctxFull = "";        // полный текст сообщения

function getMsgText(msgEl) {
  const c = msgEl.querySelector(".content");
  return c ? c.textContent || "" : "";
}

function openCtxMenu(x, y) {
  ctxMenu.classList.remove("hidden");
  const { innerWidth: W, innerHeight: H } = window;
  const rect = ctxMenu.getBoundingClientRect();
  const left = Math.min(x, W - rect.width - 4);
  const top = Math.min(y, H - rect.height - 4);
  ctxMenu.style.left = left + "px";
  ctxMenu.style.top = top + "px";
}

function closeCtxMenu() {
  ctxMenu.classList.add("hidden");
  ctxTarget = null;
  ctxSelection = "";
  ctxFull = "";
}

messagesEl.addEventListener("contextmenu", (e) => {
  const msg = e.target.closest(".msg");
  if (!msg) return;
  e.preventDefault();
  ctxTarget = msg;
  ctxFull = getMsgText(msg);
  const sel = window.getSelection();
  const selText = sel && !sel.isCollapsed && msg.contains(sel.anchorNode)
    ? sel.toString() : "";
  ctxSelection = selText;

  const copyBtn = ctxMenu.querySelector('[data-action="copy"]');
  const replyBtn = ctxMenu.querySelector('[data-action="reply"]');
  copyBtn.textContent = selText ? "Копировать выделенное" : "Копировать сообщение";
  copyBtn.disabled = !(selText || ctxFull);
  replyBtn.disabled = !(selText || ctxFull);
  replyBtn.textContent = selText ? "Ответить на фрагмент" : "Ответить на сообщение";

  openCtxMenu(e.clientX, e.clientY);
});

ctxMenu.addEventListener("click", async (e) => {
  const btn = e.target.closest(".ctx-item");
  if (!btn || btn.disabled) return;
  const action = btn.dataset.action;
  const text = ctxSelection || ctxFull;

  if (action === "copy") {
    try { await navigator.clipboard.writeText(text); }
    catch (_) {
      const ta = document.createElement("textarea");
      ta.value = text; document.body.appendChild(ta);
      ta.select(); try { document.execCommand("copy"); } catch (_) {}
      ta.remove();
    }
  } else if (action === "reply") {
    const quoted = text.split("\n").map(l => "> " + l).join("\n");
    const prefix = inputEl.value && !inputEl.value.endsWith("\n") ? "\n" : "";
    inputEl.value = (inputEl.value || "") + prefix + quoted + "\n\n";
    inputEl.focus();
    inputEl.setSelectionRange(inputEl.value.length, inputEl.value.length);
    autosize();
  }
  closeCtxMenu();
});

function insertAtCursor(ta, str) {
  const s = ta.selectionStart ?? ta.value.length;
  const e = ta.selectionEnd ?? ta.value.length;
  ta.value = ta.value.slice(0, s) + str + ta.value.slice(e);
  const pos = s + str.length;
  ta.setSelectionRange(pos, pos);
}

document.addEventListener("click", (e) => {
  if (!ctxMenu.contains(e.target)) closeCtxMenu();
});
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeCtxMenu();
});
window.addEventListener("resize", closeCtxMenu);
messagesEl.addEventListener("scroll", closeCtxMenu);
