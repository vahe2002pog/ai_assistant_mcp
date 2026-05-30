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
const themeToggle = $("#theme-toggle");
const voiceBtn = $("#voice-btn");

let currentConvId = null;
const inflightConvs = new Set();      // conv_id'шники с активным запросом
const abortCtrls = new Map();         // conv_id → AbortController
let pendingNewSend = null;            // { ctrl } — отправка в ещё-не-созданный чат
let lastStatusText = "";
let pendingImages = [];
let pendingDocs = [];
let pendingQuote = "";

const quoteChip = document.getElementById("quote-chip");
const quoteChipText = document.getElementById("quote-chip-text");
const quoteChipRm = document.getElementById("quote-chip-rm");

function readStoredTheme() {
  try {
    const saved = localStorage.getItem("compass_theme");
    if (saved === "light" || saved === "dark") return saved;
  } catch (_) {}
  const item = document.cookie.split("; ").find(x => x.startsWith("compass_theme="));
  if (item) {
    const saved = decodeURIComponent(item.split("=").slice(1).join("="));
    if (saved === "light" || saved === "dark") return saved;
  }
  return "";
}

function storeTheme(theme) {
  try { localStorage.setItem("compass_theme", theme); } catch (_) {}
  document.cookie = `compass_theme=${encodeURIComponent(theme)}; path=/; max-age=31536000; SameSite=Lax`;
}

async function saveTheme(theme) {
  try {
    await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ui_theme: theme }),
    });
  } catch (_) {}
}

const initialStoredTheme = readStoredTheme();

function applyTheme(theme, options = {}) {
  const next = theme === "light" ? "light" : "dark";
  document.documentElement.classList.toggle("light", next === "light");
  document.documentElement.classList.toggle("dark", next !== "light");
  if (options.persist !== false) storeTheme(next);
  if (themeToggle) {
    themeToggle.title = next === "light" ? "Включить тёмную тему" : "Включить светлую тему";
    themeToggle.setAttribute("aria-label", themeToggle.title);
  }
}
applyTheme(initialStoredTheme || "dark", { persist: false });

function currentTheme() {
  return document.documentElement.classList.contains("light") ? "light" : "dark";
}

if (themeToggle) {
  themeToggle.addEventListener("click", () => {
    const next = currentTheme() === "light" ? "dark" : "light";
    applyTheme(next);
    saveTheme(next);
    voiceFetch("/config", { chat_url: location.origin, ui_theme: next }).catch(() => {});
  });
}

function setPendingQuote(text) {
  pendingQuote = (text || "").trim();
  if (quoteChip) {
    if (pendingQuote) {
      quoteChipText.textContent = pendingQuote;
      quoteChip.classList.remove("hidden");
    } else {
      quoteChipText.textContent = "";
      quoteChip.classList.add("hidden");
    }
  }
}
if (quoteChipRm) {
  quoteChipRm.addEventListener("click", () => setPendingQuote(""));
}
let lastLocalMsgKey = null; // для подавления эха из SSE

function addPendingImage(img) {
  if (!img || !img.dataUrl) return;
  if (pendingImages.some(x => x.dataUrl === img.dataUrl)) return;
  pendingImages.push(img);
}

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
    ? "Дождитесь ответа или прервите запрос…"
    : "Напишите сообщение…";
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
      <img class="welcome-mark" src="/src/svg/orange/compass-orange-default.svg" alt="" />
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
  if (text) {
    if (role === "assistant") content.innerHTML = renderMarkdown(text);
    else content.textContent = text;
  } else content.style.display = "none";
  const body = msg.querySelector(".body");
  if (attachments && attachments.length) {
    const row = el("div", "attach-row");
    attachments.forEach(a => {
      const isDoc = a.kind === "doc" || (a.mime && !String(a.mime).startsWith("image/"))
                    || (a.url && !/^data:image\/|\.(png|jpe?g|gif|webp|bmp)(\?|$)/i.test(a.url));
      if (isDoc) {
        const chip = el("a", "attach-doc");
        chip.href = a.url; chip.target = "_blank"; chip.rel = "noopener";
        const ic = el("div", "attach-doc-ic");
        ic.textContent = ((a.name || "file").split(".").pop() || "FILE").slice(0, 5).toUpperCase();
        const nm = el("div", "attach-doc-name");
        nm.textContent = a.name || "файл";
        chip.appendChild(ic); chip.appendChild(nm);
        row.appendChild(chip);
      } else {
        const img = document.createElement("img");
        img.src = a.url; img.className = "attach-thumb"; img.alt = a.name || "";
        row.appendChild(img);
      }
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
    case "text": {
      const tx = document.createElement("div");
      tx.className = "block-text";
      tx.innerHTML = renderMarkdown(b.text || "");
      box.appendChild(tx);
      break;
    }
    case "list": {
      const ul = el("ul", "block-list");
      (b.items || []).forEach(i => {
        const li = el("li");
        li.innerHTML = inlineMarkdown(String(i));
        ul.appendChild(li);
      });
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
      cols.forEach(c => {
        const th = el("th");
        th.innerHTML = inlineMarkdown(String(c));
        trh.appendChild(th);
      });
      thead.appendChild(trh); table.appendChild(thead);
      const tbody = el("tbody");
      rows.forEach(r => {
        const tr = el("tr");
        cols.forEach(c => {
          const td = el("td");
          td.innerHTML = inlineMarkdown(r[c] !== undefined ? String(r[c]) : "");
          tr.appendChild(td);
        });
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
      (b.file_paths || []).forEach(p => {
        const li = el("li", "file-row");
        li.appendChild(makeFileLink(p));
        const btn = el("button", "file-reveal", "📂");
        btn.type = "button";
        btn.title = "Показать в проводнике";
        btn.addEventListener("click", (e) => {
          e.preventDefault(); e.stopPropagation();
          openLocalFile(p, true);
        });
        li.appendChild(btn);
        ul.appendChild(li);
      });
      box.appendChild(ul); break;
    }
    default: return null;
  }
  return box;
}

// ── Локальные файлы: кликабельные ссылки ────────────────────────────
const LOCAL_PATH_RE = /(?:(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\[^\s\\/<>"'|?*]+\\)[^\s<>"'|?*]+[^\s<>"'|?*.,;:!?)\]]/g;

async function openLocalFile(path, reveal = false) {
  try {
    await fetch("/api/open-file", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path, reveal: !!reveal }),
    });
  } catch (_) {}
}

function makeFileLink(path) {
  const a = document.createElement("a");
  a.href = "#";
  a.className = "file-link";
  a.textContent = path;
  a.title = "ЛКМ — открыть, Ctrl+ЛКМ — показать в проводнике";
  a.addEventListener("click", (e) => {
    e.preventDefault();
    openLocalFile(path, e.ctrlKey || e.metaKey || e.shiftKey);
  });
  return a;
}

function linkifyPaths(html) {
  // Заменяем абсолютные локальные пути в готовом HTML на <a class="file-link" data-path="...">.
  // Не трогаем то, что уже внутри <a>...</a> или <code>...</code>.
  return html.replace(
    /(<(a|code)\b[^>]*>[\s\S]*?<\/\2>)|((?:(?<![A-Za-z])[A-Za-z]:[\\\/]|\\\\[^\s\\\/<>"'|?*]+\\)[^\s<>"'|?*]+[^\s<>"'|?*.,;:!?)\]])/g,
    (m, tagged, _t, path) => {
      if (tagged) return tagged;
      const safe = String(path).replace(/"/g, "&quot;");
      return `<a href="#" class="file-link" data-path="${safe}" title="ЛКМ — открыть, Ctrl+ЛКМ — показать в проводнике">${path}</a>`;
    },
  );
}

// Делегированный обработчик для ссылок, добавленных через innerHTML.
document.addEventListener("click", (e) => {
  const a = e.target.closest && e.target.closest("a.file-link[data-path]");
  if (!a) return;
  e.preventDefault();
  openLocalFile(a.dataset.path, e.ctrlKey || e.metaKey || e.shiftKey);
});

function escHtml(s) {
  return String(s).replace(/[&<>"']/g, c => (
    {"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c]
  ));
}

function inlineMarkdown(src) {
  let s = escHtml(src);
  s = s.replace(/`([^`\n]+)`/g, (_, x) => `<code>${x}</code>`);
  s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
    (_, t, u) => `<a href="${u}" target="_blank" rel="noopener">${t}</a>`);
  s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
  s = s.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
  s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  s = s.replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");
  s = s.replace(/(^|[\s(])(https?:\/\/[^\s<]+)/g,
    (_, pre, u) => `${pre}<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
  return linkifyPaths(s);
}

function renderMarkdown(src) {
  const esc = escHtml;
  const lines = String(src).split("\n");
  const out = [];
  let i = 0;

  const inline = (s) => {
    // inline code
    s = s.replace(/`([^`\n]+)`/g, (_, x) => `<code>${esc(x)}</code>`);
    // links [text](url)
    s = s.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g,
      (_, t, u) => `<a href="${u}" target="_blank" rel="noopener">${esc(t)}</a>`);
    // bold **x** / __x__
    s = s.replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
    s = s.replace(/__([^_\n]+)__/g, "<strong>$1</strong>");
    // italic *x* / _x_  (не цепляем ** — они уже ушли)
    s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    s = s.replace(/(^|[^_])_([^_\n]+)_/g, "$1<em>$2</em>");
    // autolink «голых» http(s)
    s = s.replace(/(^|[\s(])(https?:\/\/[^\s<]+)/g,
      (_, pre, u) => `${pre}<a href="${u}" target="_blank" rel="noopener">${u}</a>`);
    return s;
  };

  while (i < lines.length) {
    let line = lines[i];
    const trimmed = line.trim();

    if (!trimmed) { out.push(""); i++; continue; }

    // Fenced code ```lang ... ```
    const fence = trimmed.match(/^```(\w*)\s*$/);
    if (fence) {
      const buf = [];
      i++;
      while (i < lines.length && !/^```\s*$/.test(lines[i].trim())) {
        buf.push(lines[i]); i++;
      }
      if (i < lines.length) i++;
      out.push(`<pre><code>${esc(buf.join("\n"))}</code></pre>`);
      continue;
    }

    // GFM tables: | col | col | ... с разделителем |---|---|
    const isTableRow = (s) => /^\s*\|.*\|\s*$/.test(s);
    const isTableSep = (s) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?\s*$/.test(s);
    if (isTableRow(line) && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const splitRow = (s) => s.trim().replace(/^\||\|$/g, "").split("|").map(c => c.trim());
      const header = splitRow(line);
      i += 2;
      const rows = [];
      while (i < lines.length && isTableRow(lines[i])) { rows.push(splitRow(lines[i])); i++; }
      const th = header.map(c => `<th>${inline(esc(c))}</th>`).join("");
      const trs = rows.map(r =>
        "<tr>" + r.map(c => `<td>${inline(esc(c))}</td>`).join("") + "</tr>"
      ).join("");
      out.push(`<table class="md-table"><thead><tr>${th}</tr></thead><tbody>${trs}</tbody></table>`);
      continue;
    }

    // Headings: #, ##, ###
    const h = trimmed.match(/^(#{1,6})\s+(.*)$/);
    if (h) {
      const lvl = h[1].length;
      out.push(`<h${lvl}>${inline(esc(h[2]))}</h${lvl}>`);
      i++; continue;
    }

    // Bullet list
    if (/^[-*•]\s+/.test(trimmed)) {
      const items = [];
      while (i < lines.length && /^\s*[-*•]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*[-*•]\s+/, ""));
        i++;
      }
      out.push("<ul>" + items.map(x => `<li>${inline(esc(x))}</li>`).join("") + "</ul>");
      continue;
    }

    // Ordered list
    if (/^\d+[.)]\s+/.test(trimmed)) {
      const items = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*\d+[.)]\s+/, ""));
        i++;
      }
      out.push("<ol>" + items.map(x => `<li>${inline(esc(x))}</li>`).join("") + "</ol>");
      continue;
    }

    // Paragraph — собираем подряд идущие непустые не-спец строки
    const buf = [];
    while (i < lines.length && lines[i].trim()
           && !/^(#{1,6}\s|[-*•]\s|\d+[.)]\s)/.test(lines[i].trim())) {
      buf.push(lines[i]);
      i++;
    }
    out.push(`<p>${buf.map(x => inline(esc(x))).join("<br>")}</p>`);
  }
  return linkifyPaths(out.join(""));
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
      if (id === voiceConvId) voiceConvId = null;
      loadConversations();
    } catch (_) {}
  });
  evtSource.addEventListener("config_updated", (e) => {
    try {
      const cfg = JSON.parse(e.data);
      currentCfg = cfg;
      updatePill(cfg);
      if (cfg.ui_theme && cfg.ui_theme !== currentTheme()) {
        applyTheme(cfg.ui_theme, { persist: false });
        voiceFetch("/config", { chat_url: location.origin, ui_theme: cfg.ui_theme }).catch(() => {});
      }
      if (!settingsPanel.classList.contains("hidden")) {
        selProvider.value = cfg.provider;
        selBase.value = cfg.base_url;
        setSafetyToggle(cfg.safety_mode || "strict");
        updateEditVisibility();
      }
    } catch (_) {}
  });
  evtSource.addEventListener("open_conversation", (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.id != null) openConversation(d.id);
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
      if (d.source === "voice" && d.conversation_id != null) {
        voiceConvId = d.conversation_id;
        if (!pendingNewSend && currentConvId == null) {
          currentConvId = d.conversation_id;
          loadConversations();
        }
        if (d.role === "user") {
          markVoiceBusy(d.conversation_id, "Думаю...");
        } else if (d.role === "assistant" && !VOICE_BUSY_STATES.has(voiceState)) {
          clearVoiceBusy(d.conversation_id);
        }
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
const MAX_DOC_SIZE = 20 * 1024 * 1024; // 20 MB
fileInput.addEventListener("change", async (e) => {
  const files = Array.from(e.target.files || []);
  for (const f of files) {
    const dataUrl = await new Promise(res => {
      const r = new FileReader();
      r.onload = () => res(r.result);
      r.readAsDataURL(f);
    });
    if (f.type.startsWith("image/")) {
      addPendingImage({ dataUrl, name: f.name });
    } else {
      if (f.size > MAX_DOC_SIZE) {
        alert(`Файл "${f.name}" больше 20 МБ и не будет прикреплён.`);
        continue;
      }
      pendingDocs.push({ dataUrl, name: f.name, mime: f.type || "", size: f.size });
    }
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
        addPendingImage({ dataUrl: r.result, name: blob.name || "paste.png" });
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
  pendingDocs.forEach((doc, idx) => {
    const box = el("div", "preview-item preview-doc");
    box.title = `${doc.name} (${Math.round(doc.size / 1024)} КБ)`;
    const ic = el("div", "preview-doc-ic");
    ic.textContent = (doc.name.split(".").pop() || "FILE").slice(0, 5).toUpperCase();
    const nm = el("div", "preview-doc-name");
    nm.textContent = doc.name;
    const rm = el("button", "preview-rm", "×");
    rm.type = "button";
    rm.addEventListener("click", () => {
      pendingDocs.splice(idx, 1);
      renderAttachPreview();
    });
    box.appendChild(ic); box.appendChild(nm); box.appendChild(rm);
    attachPreview.appendChild(box);
  });
  attachPreview.classList.toggle("empty",
    pendingImages.length === 0 && pendingDocs.length === 0);
}

// ── Отправка ─────────────────────────────────────────────────────────
async function send(text) {
  if (!text.trim() && !pendingImages.length && !pendingDocs.length) return;
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
  const docs = pendingDocs.slice();
  pendingImages = [];
  pendingDocs = [];
  renderAttachPreview();

  const previewAtt = [
    ...imgs.map(i => ({ url: i.dataUrl, name: i.name })),
    ...docs.map(d => ({ url: d.dataUrl, name: d.name, mime: d.mime, kind: "doc" })),
  ];
  appendMessage("user", text, null, previewAtt);
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
        documents: docs.map(d => ({ dataUrl: d.dataUrl, name: d.name, mime: d.mime })),
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
  if (id != null && id === voiceBusyConvId) {
    try { await voiceFetch("/session/stop", {}); } catch (_) {}
  }
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
  if (!text && !pendingImages.length && !pendingDocs.length && !pendingQuote) return;
  const quote = pendingQuote;
  setPendingQuote("");
  inputEl.value = "";
  autosize();
  let payload = text;
  if (quote) {
    const header = "[Цитата пользователя — это справочный текст, не цель действий]";
    const footer = "[/Цитата]";
    payload = `${header}\n${quote}\n${footer}\n\n${text}`.trim();
  }
  send(payload);
});

inputEl.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    form.requestSubmit();
  }
});

// ── Настройки модели ───────────────────────────────────────────────────
// Voice mode ---------------------------------------------------------------
const VOICE_DAEMON_URL = "http://127.0.0.1:8766";
let voiceSource = null;
let voiceState = "unknown";
let browserVoiceRecorder = null;
let voiceConvId = null;
let voiceBusyConvId = null;
const VOICE_BUSY_STATES = new Set(["transcribing", "thinking", "speaking"]);

function isDesktopVoiceMode() {
  return new URLSearchParams(location.search).get("bridge") === "1" || !!window.pywebview;
}

async function voiceFetch(path, payload = null) {
  const body = payload
    ? { ui_theme: currentTheme(), ...payload }
    : null;
  const opts = body
    ? {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      }
    : {};
  const r = await fetch(VOICE_DAEMON_URL + path, opts);
  const data = await r.json().catch(() => ({}));
  if (!r.ok || data.ok === false) throw new Error(data.error || `voice daemon ${r.status}`);
  return data;
}

async function ensureVoiceConversation({ open = false } = {}) {
  if (voiceConvId != null) {
    if (open && currentConvId !== voiceConvId) await openConversation(voiceConvId);
    return voiceConvId;
  }
  const r = await fetch("/api/voice-conversation");
  const d = await r.json();
  voiceConvId = d.id;
  if (open) await openConversation(voiceConvId);
  return voiceConvId;
}

function setVoiceButtonState(state, detail = "") {
  if (!voiceBtn) return;
  voiceState = state || "unknown";
  voiceBtn.classList.remove("recording", "transcribing", "thinking", "speaking", "error");
  if (["recording", "transcribing", "thinking", "speaking", "error"].includes(voiceState)) {
    voiceBtn.classList.add(voiceState);
  }
  const titles = {
    wake_listening: "Voice ready",
    idle: "Voice ready",
    recording: "Listening...",
    transcribing: "Transcribing...",
    thinking: "Thinking...",
    speaking: "Speaking...",
    error: detail || "Voice error",
    offline: "Voice daemon offline",
  };
  voiceBtn.title = titles[voiceState] || "Voice request";
  voiceBtn.setAttribute("aria-label", voiceBtn.title);
  voiceBtn.disabled = voiceState === "offline";
}

function voiceStatusLabel(state, data = {}) {
  if (data.status_text) return data.status_text;
  if (state === "transcribing") return "Распознаю голос...";
  if (state === "thinking") return "Думаю...";
  if (state === "speaking") return "Озвучиваю ответ...";
  return "";
}

function markVoiceBusy(convId, status = "") {
  if (convId == null) return;
  voiceConvId = convId;
  voiceBusyConvId = convId;
  inflightConvs.add(convId);
  if (status) setStatus(status);
  refreshSendBtn();
  applyStatus();
}

function clearVoiceBusy(convId = voiceBusyConvId) {
  if (convId == null) return;
  inflightConvs.delete(convId);
  abortCtrls.delete(convId);
  if (voiceBusyConvId === convId) voiceBusyConvId = null;
  if (currentConvId === convId) setStatus("");
  refreshSendBtn();
  applyStatus();
}

function connectVoiceEvents() {
  if (!voiceBtn) return;
  try { if (voiceSource) voiceSource.close(); } catch (_) {}
  try {
    voiceSource = new EventSource(VOICE_DAEMON_URL + "/events");
  } catch (_) {
    setVoiceButtonState("offline");
    return;
  }
  voiceSource.addEventListener("state", (e) => {
    try {
      const d = JSON.parse(e.data);
      setVoiceButtonState(d.state, d.detail || "");
      if (d.conversation_id != null) {
        voiceConvId = d.conversation_id;
        if (VOICE_BUSY_STATES.has(d.state)) {
          markVoiceBusy(d.conversation_id, voiceStatusLabel(d.state, d));
        } else if (voiceBusyConvId === d.conversation_id && ["listening", "wake_listening", "idle", "error"].includes(d.state)) {
          clearVoiceBusy(d.conversation_id);
        }
      }
    } catch (_) {}
  });
  voiceSource.addEventListener("chat_done", (e) => {
    try {
      const d = JSON.parse(e.data);
      if (d.conversation_id != null) {
        voiceConvId = d.conversation_id;
        markVoiceBusy(d.conversation_id, d.reply ? "Озвучиваю ответ..." : "");
      }
      if (currentConvId == null && d.conversation_id != null) {
        openConversation(d.conversation_id);
      } else {
        loadConversations();
      }
    } catch (_) {}
  });
  voiceSource.onerror = () => {
    setVoiceButtonState("offline");
    setTimeout(connectVoiceEvents, 2500);
  };
}

function encodeWav(chunks, sampleRate) {
  const length = chunks.reduce((n, c) => n + c.length, 0);
  const data = new Float32Array(length);
  let offset = 0;
  chunks.forEach(c => { data.set(c, offset); offset += c.length; });

  const buffer = new ArrayBuffer(44 + data.length * 2);
  const view = new DataView(buffer);
  const writeString = (pos, s) => {
    for (let i = 0; i < s.length; i++) view.setUint8(pos + i, s.charCodeAt(i));
  };
  writeString(0, "RIFF");
  view.setUint32(4, 36 + data.length * 2, true);
  writeString(8, "WAVE");
  writeString(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, "data");
  view.setUint32(40, data.length * 2, true);
  let p = 44;
  for (let i = 0; i < data.length; i++, p += 2) {
    const s = Math.max(-1, Math.min(1, data[i]));
    view.setInt16(p, s < 0 ? s * 0x8000 : s * 0x7fff, true);
  }
  return buffer;
}

function arrayBufferToBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

async function startBrowserVoiceRecording() {
  if (browserVoiceRecorder) {
    browserVoiceRecorder.stop();
    return;
  }
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("Browser microphone API is unavailable");
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const AudioCtx = window.AudioContext || window.webkitAudioContext;
  const ctx = new AudioCtx();
  const source = ctx.createMediaStreamSource(stream);
  const processor = ctx.createScriptProcessor(4096, 1, 1);
  const chunks = [];
  let speechStarted = false;
  let voicedMs = 0;
  let candidateRun = 0;
  let lastVoiceAt = performance.now();
  const startedAt = performance.now();
  let stopped = false;
  const minSpeechMs = 650;
  const minStartBlocks = 3;
  const voiceRmsThreshold = 0.014;

  const stop = async () => {
    if (stopped) return;
    stopped = true;
    try { processor.disconnect(); } catch (_) {}
    try { source.disconnect(); } catch (_) {}
    stream.getTracks().forEach(t => t.stop());
    try { await ctx.close(); } catch (_) {}
    browserVoiceRecorder = null;
    setVoiceButtonState("transcribing");
    if (!chunks.length || !speechStarted || voicedMs < minSpeechMs) {
      setVoiceButtonState("wake_listening");
      return;
    }
    const wav = encodeWav(chunks, ctx.sampleRate);
    try {
      await voiceFetch("/audio/upload", {
        data: "data:audio/wav;base64," + arrayBufferToBase64(wav),
        mime: "audio/wav",
        conversation_id: voiceConvId,
        chat_url: location.origin,
      });
    } catch (e) {
      setVoiceButtonState("error", e.message);
      appendMessage("assistant", "[Voice error] " + e.message);
    }
  };

  processor.onaudioprocess = (e) => {
    if (stopped) return;
    const input = e.inputBuffer.getChannelData(0);
    const copy = new Float32Array(input);
    chunks.push(copy);
    let sum = 0;
    for (let i = 0; i < copy.length; i++) sum += copy[i] * copy[i];
    const rms = Math.sqrt(sum / Math.max(1, copy.length));
    const now = performance.now();
    if (rms >= voiceRmsThreshold) {
      candidateRun += 1;
      if (candidateRun >= minStartBlocks) {
        speechStarted = true;
        lastVoiceAt = now;
        voicedMs += (copy.length / ctx.sampleRate) * 1000;
      }
    } else {
      candidateRun = 0;
    }
    const speechConfirmed = voicedMs >= minSpeechMs;
    if ((speechConfirmed && now - lastVoiceAt > 1300) ||
        (!speechConfirmed && now - startedAt > 10000) ||
        now - startedAt > 30000) {
      stop();
    }
  };

  source.connect(processor);
  processor.connect(ctx.destination);
  browserVoiceRecorder = { stop };
  setVoiceButtonState("recording");
}

if (voiceBtn) {
  connectVoiceEvents();
  voiceBtn.addEventListener("click", async () => {
    try {
      if (isDesktopVoiceMode()) {
        if (voiceState === "recording") {
          await voiceFetch("/listen/stop", {});
        } else {
          await voiceFetch("/listen/start", {
            conversation_id: voiceConvId,
            chat_url: location.origin,
          });
        }
      } else {
        await startBrowserVoiceRecording();
      }
    } catch (e) {
      setVoiceButtonState("error", e.message);
      appendMessage("assistant", "[Voice error] " + e.message);
    }
  });
}

const modelPill = $("#model-pill");
const settingsPanel = $("#settings-panel");
const selProvider = $("#sel-provider");
const mainEdit = $("#main-edit");
const mainModelSummary = $("#main-model-summary");
const baseRow = $("#base-row");
const selBase = $("#sel-base");
const selFolder = $("#sel-folder");
const folderRow = $("#folder-row");
const apiKeyRow = $("#apikey-row");
const selApiKey = $("#sel-apikey");

function updateFolderVisibility() {
  folderRow.classList.toggle("hidden", selProvider.value !== "yandex");
}
const selModel = $("#sel-model");
const selModelCustom = $("#sel-model-custom");
const modelSelectRow = $("#model-select-row");
const modelCustomRow = $("#model-custom-row");
const selVisionProvider = $("#sel-vision-provider");
const selVisionBase = $("#sel-vision-base");
const selVisionApiKey = $("#sel-vision-apikey");
const selVisionModel = $("#sel-vision-model");
const selVisionModelCustom = $("#sel-vision-model-custom");
const visionToggle = $("#vision-toggle");
const visionToggleLabel = $("#vision-toggle-label");
const visionBlock = $("#vision-block");
const visionEdit = $("#vision-edit");
const visionModelSummary = $("#vision-model-summary");
const visionBaseRow = $("#vision-base-row");
const visionApiKeyRow = $("#vision-apikey-row");
const visionModelSelectRow = $("#vision-model-select-row");
const visionModelCustomRow = $("#vision-model-custom-row");
const refreshVisionModelsBtn = $("#refresh-vision-models");
const ollamaBlock = $("#ollama-block");
const ollamaState = $("#ollama-state");
const ollamaRecommended = $("#ollama-recommended");
const ollamaCustomName = $("#ollama-custom-name");
const ollamaAddBtn = $("#ollama-add");
const ollamaSearchBtn = $("#ollama-search");
const ollamaStatus = $("#ollama-status");
const visionOllamaBlock = $("#vision-ollama-block");
const visionOllamaState = $("#vision-ollama-state");
const visionOllamaRecommended = $("#vision-ollama-recommended");
const visionOllamaCustomName = $("#vision-ollama-custom-name");
const visionOllamaAddBtn = $("#vision-ollama-add");
const visionOllamaSearchBtn = $("#vision-ollama-search");
const visionOllamaStatus = $("#vision-ollama-status");

function setSafetyToggle(mode) {
  // Safety mode is always enabled; the old UI toggle was removed.
}

function getSafetyModeFromToggle() {
  return "strict";
}

function setSwitch(btn, label, on, onText, offText) {
  btn.classList.toggle("on", on);
  btn.classList.toggle("off", !on);
  btn.setAttribute("aria-checked", on ? "true" : "false");
  if (label) label.textContent = on ? onText : offText;
}

function setVisionEnabled(on) {
  visionEnabled = !!on;
  setSwitch(
    visionToggle,
    visionToggleLabel,
    visionEnabled,
    "Отдельная Vision модель",
    "Отдельная Vision модель"
  );
  visionBlock.classList.toggle("hidden", !visionEnabled);
  if (!visionEnabled) visionEditMode = false;
  updateEditVisibility();
}

function providerMeta(id) {
  return providers.find(p => p.id === id) || {};
}

function providerNeedsEdit(id, p = providerMeta(id)) {
  if (id === "ollama") return !(p.model || (currentCfg && currentCfg.provider === "ollama" && currentCfg.model));
  const hasModel = !!(p.model || (currentCfg && currentCfg.provider === id && currentCfg.model));
  const hasKey = !!p.api_key_set || !p.needs_key;
  return !hasModel || !hasKey;
}

function visionProviderNeedsEdit(id, p = providerMeta(id)) {
  if (!id) return false;
  const hasModel = !!(p.vision_model || (currentCfg && currentCfg.vision_provider === id && currentCfg.vision_model));
  const hasKey = !!p.api_key_set || !p.needs_key;
  return !hasModel || !hasKey;
}

function updateSummaries() {
  const p = providerMeta(selProvider.value);
  const model = (selModelCustom.value.trim() || selModel.value || p.model || (currentCfg && currentCfg.model) || "").trim();
  mainModelSummary.textContent = model ? `Модель: ${model}` : "Модель не выбрана";
  if (visionEnabled) {
    const vp = providerMeta(selVisionProvider.value);
    const vm = (selVisionModelCustom.value.trim() || selVisionModel.value || (currentCfg && currentCfg.vision_model) || vp.vision_model || "").trim();
    const label = selVisionProvider.value ? (vp.label || selVisionProvider.value) : "как основной";
    visionModelSummary.textContent = vm ? `${label}: ${vm}` : `${label}: модель не выбрана`;
  }
}

function updateProviderRows() {
  const isLocalOllama = selProvider.value === "ollama";
  baseRow.classList.toggle("hidden", isLocalOllama);
  apiKeyRow.classList.toggle("hidden", isLocalOllama);
  modelSelectRow.classList.toggle("hidden", isLocalOllama);
  modelCustomRow.classList.toggle("hidden", isLocalOllama);
  folderRow.classList.toggle("hidden", selProvider.value !== "yandex" || isLocalOllama);
  ollamaBlock.classList.toggle("hidden", !isLocalOllama);
}

function updateVisionRows() {
  const isLocalOllama = selVisionProvider.value === "ollama";
  const hasProvider = !!selVisionProvider.value;
  visionBaseRow.classList.toggle("hidden", !hasProvider || isLocalOllama);
  visionApiKeyRow.classList.toggle("hidden", !hasProvider || isLocalOllama);
  visionModelSelectRow.classList.toggle("hidden", isLocalOllama);
  visionModelCustomRow.classList.toggle("hidden", isLocalOllama);
  visionOllamaBlock.classList.toggle("hidden", !isLocalOllama);
}

function updateEditVisibility() {
  updateProviderRows();
  updateVisionRows();
  mainEdit.classList.toggle("hidden", !mainEditMode);
  visionEdit.classList.toggle("hidden", !visionEnabled || !visionEditMode);
  mainEditBtn.textContent = mainEditMode ? "Сохранить" : "Редактировать";
  visionEditBtn.textContent = visionEditMode ? "Сохранить" : "Редактировать";
  updateSummaries();
}

function setMainEditMode(on) {
  mainEditMode = !!on;
  updateEditVisibility();
}

function setVisionEditMode(on) {
  visionEditMode = !!on;
  updateEditVisibility();
}

function updateVisionProviderRowsVisibility() {
  updateVisionRows();
}

function updateOllamaVisibility() {
  updateProviderRows();
}
const refreshModelsBtn = $("#refresh-models");
const mainEditBtn = $("#main-settings-edit");
const visionEditBtn = $("#vision-settings-edit");
const saveBtn = $("#settings-save");
const cancelBtn = $("#settings-cancel");

let providers = [];
let currentCfg = null;
let mainEditMode = false;
let visionEditMode = false;
let visionEnabled = false;

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
    const provOpts = providers.map(p => `<option value="${p.id}">${p.label}</option>`).join("");
    selProvider.innerHTML = provOpts;
    selProvider.value = currentCfg.provider;
    selBase.value = currentCfg.base_url;
    selFolder.value = currentCfg.folder || "";
    selApiKey.value = "";
    selApiKey.placeholder = currentCfg.api_key_set ? "••• ключ сохранён (введите, чтобы заменить)" : "токен провайдера";
    setSafetyToggle(currentCfg.safety_mode || "strict");
    if (!initialStoredTheme && currentCfg.ui_theme) {
      applyTheme(currentCfg.ui_theme);
    }

    // Vision: "" = использовать основной; иначе один из providers.
    selVisionProvider.innerHTML =
      `<option value="">(как основной)</option>` + provOpts;
    selVisionProvider.value = currentCfg.vision_provider || "";
    selVisionBase.value = currentCfg.vision_base_url
      || (currentCfg.vision_provider
          ? (providers.find(p => p.id === currentCfg.vision_provider) || {}).base_url || ""
          : "");
    selVisionApiKey.value = "";
    selVisionApiKey.placeholder = currentCfg.vision_api_key_set
      ? "••• ключ сохранён (введите, чтобы заменить)"
      : "токен провайдера";
    mainEditMode = providerNeedsEdit(selProvider.value);
    visionEnabled = !!(currentCfg.vision_provider || currentCfg.vision_model);
    visionEditMode = visionEnabled && visionProviderNeedsEdit(selVisionProvider.value || currentCfg.provider);
    setVisionEnabled(visionEnabled);
    updateEditVisibility();

    updatePill(currentCfg);
    if (mainEditMode || selProvider.value === "ollama") await refreshModels();
    await refreshOllamaStatus();
    if (visionEnabled && visionEditMode) await refreshVisionModels();
  } catch (_) {}
}

const _escHtml = (s) => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
const _normModel = (m) => typeof m === "string"
  ? { id: m, vision: false, installed: true, recommended: false, state: "installed", error: "", size_label: "" }
  : { id: m.id, vision: !!m.vision, installed: m.installed !== false, recommended: !!m.recommended, state: m.state || "", error: m.error || "", size_label: m.size_label || "" };
const _renderOpt = (m) => {
  const x = _normModel(m);
  const badge = x.vision ? " 🖼" : "";
  const missing = x.installed ? "" : " · скачать";
  const title = x.installed
    ? (x.vision ? "поддерживает изображения" : "только текст")
    : "модель ещё не загружена";
  return `<option value="${_escHtml(x.id)}" title="${title}">${_escHtml(x.id)}${badge}${missing}</option>`;
};

async function _fetchModels(baseUrl, apiKey, provider) {
  let url = "/api/models?base_url=" + encodeURIComponent(baseUrl);
  if (provider) url += "&provider=" + encodeURIComponent(provider);
  if (apiKey) url += "&api_key=" + encodeURIComponent(apiKey);
  const r = await fetch(url);
  return r.json();
}

async function refreshModels() {
  selModel.innerHTML = `<option value="">(загрузка…)</option>`;
  try {
    const d = await _fetchModels(selBase.value, selApiKey.value.trim(), selProvider.value);
    const list = d.models || [];
    const groups = d.groups;
    if (!list.length) {
      selModel.innerHTML = `<option value="">(не найдено)</option>`;
      return;
    }
    if (groups && groups.length) {
      selModel.innerHTML = groups.map(g =>
        `<optgroup label="${_escHtml(g.label)} (${g.models.length})">` +
        g.models.map(_renderOpt).join("") +
        `</optgroup>`
      ).join("");
    } else {
      selModel.innerHTML = list.map(_renderOpt).join("");
    }
    const ids = list.map(m => _normModel(m).id);
    const desiredModel = (selModelCustom.value.trim() || (currentCfg && currentCfg.model) || "").trim();
    if (desiredModel && ids.includes(desiredModel)) {
      selModel.value = desiredModel;
      selModelCustom.value = "";
    } else if (desiredModel) {
      selModelCustom.value = desiredModel;
    }
    if (selProvider.value === "ollama") refreshOllamaStatus();
  } catch (_) {
    selModel.innerHTML = `<option value="">(ошибка)</option>`;
  }
}

function renderOllamaRows(container, d, selected, onSelect, target = "main") {
  if (!container || !d) return;
  const models = d.models || [];
  const icons = {
    idle: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>`,
    downloading: `<svg width="13" height="13" viewBox="0 0 24 24" fill="currentColor"><rect x="5" y="5" width="14" height="14" rx="2"/></svg>`,
    installed: `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 6h18"/><path d="M8 6V4h8v2"/><path d="M19 6l-1 14H6L5 6"/></svg>`,
  };
  container.innerHTML = models.map(m => {
    const x = _normModel(m);
    const state = x.state || (x.installed ? "installed" : "idle");
    const action = state === "downloading" ? "cancel" : x.installed ? "delete" : "pull";
    const title = action === "cancel" ? "Остановить" : action === "delete" ? "Удалить" : "Загрузить";
    const size = x.size_label ? `<span class="ollama-size">${_escHtml(x.size_label)}</span>` : "";
    return `<div class="ollama-row ${x.installed ? "installed" : ""} ${selected === x.id ? "selected" : ""}" data-model="${_escHtml(x.id)}">
      <button class="ollama-name" type="button" title="${_escHtml(x.id)}"><span>${_escHtml(x.id)}</span>${size}</button>
      <button class="ollama-action" type="button" data-action="${action}" title="${title}">${icons[state] || icons.idle}</button>
    </div>`;
  }).join("");
  container.querySelectorAll(".ollama-name").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const row = btn.closest(".ollama-row");
      onSelect(row.dataset.model || "");
    });
  });
  container.querySelectorAll(".ollama-action").forEach(btn => {
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      const model = btn.closest(".ollama-row").dataset.model || "";
      const action = btn.dataset.action;
      if (action === "cancel") cancelOllamaPull();
      else if (action === "delete") deleteOllamaModel(model);
      else pullOllamaModel(model, target);
    });
  });
}

function renderOllamaStatus(d) {
  if (!d) return;
  const pull = d.pull || {};
  const status = pull.active ? (pull.status || `Загружаю ${pull.model}...`) : (pull.error || pull.status || "");
  if (selProvider.value === "ollama" && ollamaBlock) {
    ollamaState.textContent = d.running ? "запущена" : "остановлена";
    ollamaStatus.textContent = status;
    renderOllamaRows(
      ollamaRecommended,
      d,
      (selModel.value || (currentCfg && currentCfg.model) || "").trim(),
      (model) => {
        selModelCustom.value = "";
        selModel.value = model;
        updateSummaries();
        renderOllamaStatus(d);
      },
      "main"
    );
  }
  if (visionEnabled && selVisionProvider.value === "ollama" && visionOllamaBlock) {
    visionOllamaState.textContent = d.running ? "запущена" : "остановлена";
    visionOllamaStatus.textContent = status;
    renderOllamaRows(
      visionOllamaRecommended,
      d,
      (selVisionModelCustom.value.trim() || selVisionModel.value || (currentCfg && currentCfg.vision_model) || "").trim(),
      (model) => {
        selVisionModel.value = "";
        selVisionModelCustom.value = model;
        if (visionOllamaCustomName) visionOllamaCustomName.value = "";
        updateSummaries();
        renderOllamaStatus(d);
      },
      "vision"
    );
  }
}

async function refreshOllamaStatus() {
  if (selProvider.value !== "ollama" && (!visionEnabled || selVisionProvider.value !== "ollama")) return;
  updateOllamaVisibility();
  updateVisionRows();
  try {
    const d = await (await fetch("/api/ollama/status")).json();
    renderOllamaStatus(d);
    if (d.pull && d.pull.active) setTimeout(refreshOllamaStatus, 2500);
  } catch (_) {
    if (ollamaState) ollamaState.textContent = "недоступна";
    if (visionOllamaState) visionOllamaState.textContent = "недоступна";
  }
}

async function pullOllamaModel(name = "", target = "main") {
  const custom = target === "vision" ? visionOllamaCustomName : ollamaCustomName;
  const statusEl = target === "vision" ? visionOllamaStatus : ollamaStatus;
  const model = (name || (custom && custom.value.trim()) || selModel.value || "").trim();
  if (!model) return;
  if (target === "vision") {
    selVisionModel.value = "";
    selVisionModelCustom.value = model;
  } else {
    selModelCustom.value = model;
  }
  if (statusEl) statusEl.textContent = `Загружаю ${model}...`;
  try {
    const r = await fetch("/api/ollama/pull", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    });
    const d = await r.json();
    renderOllamaStatus(d);
    if (!r.ok && d.error && statusEl) statusEl.textContent = d.error;
    setTimeout(async () => {
      await refreshOllamaStatus();
      await refreshModels();
      if (visionEnabled) await refreshVisionModels();
    }, 2500);
  } catch (e) {
    if (statusEl) statusEl.textContent = e.message;
  }
}
async function cancelOllamaPull() {
  const d = await (await fetch("/api/ollama/cancel", { method: "POST" })).json();
  renderOllamaStatus(d);
  await refreshModels();
  if (visionEnabled) await refreshVisionModels();
}

async function deleteOllamaModel(model) {
  if (!model) return;
  const r = await fetch("/api/ollama/delete", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ model }),
  });
  const d = await r.json();
  renderOllamaStatus(d);
  if (!r.ok && d.error) {
    if (ollamaStatus) ollamaStatus.textContent = d.error;
    if (visionOllamaStatus) visionOllamaStatus.textContent = d.error;
  }
  await refreshModels();
  if (visionEnabled) await refreshVisionModels();
}

async function refreshVisionModels() {
  selVisionModel.innerHTML = `<option value="">(загрузка…)</option>`;
  try {
    if (selVisionProvider.value === "ollama") {
      selVisionModel.innerHTML = "";
      await refreshOllamaStatus();
      return;
    }
    // Если vision-провайдер не выбран — берём модели основного (с его base/key).
    const useCustom = !!selVisionProvider.value;
    const base = useCustom ? selVisionBase.value : selBase.value;
    const key = useCustom ? selVisionApiKey.value.trim() : selApiKey.value.trim();
    const d = await _fetchModels(base, key, useCustom ? selVisionProvider.value : selProvider.value);
    const list = (d.models || []).map(_normModel);
    // Не фильтруем список: некоторые провайдеры не отдают корректный vision-флаг,
    // но модель всё равно может принимать изображения.
    if (!list.length) {
      selVisionModel.innerHTML = `<option value="">(не найдено)</option>`;
    } else {
      selVisionModel.innerHTML = `<option value="">(не выбрана — использовать основную)</option>`
        + list.map(_renderOpt).join("");
    }
    if (currentCfg) {
      const vids = list.map(x => x.id);
      selVisionModel.value = vids.includes(currentCfg.vision_model) ? currentCfg.vision_model : "";
    }
  } catch (_) {
    selVisionModel.innerHTML = `<option value="">(ошибка)</option>`;
  }
}

selProvider.addEventListener("change", () => {
  const p = providers.find(x => x.id === selProvider.value);
  if (p) selBase.value = p.base_url;
  selModelCustom.value = (p && p.model) ? p.model : "";
  if (p && p.model) selModel.value = p.model;
  selApiKey.value = "";
  selApiKey.placeholder = (p && p.api_key_set)
    ? "••• ключ сохранён (введите, чтобы заменить)"
    : "токен провайдера";
  mainEditMode = providerNeedsEdit(selProvider.value, p);
  updateEditVisibility();
  if (mainEditMode || selProvider.value === "ollama") refreshModels();
  refreshOllamaStatus();
});
refreshModelsBtn.addEventListener("click", refreshModels);
if (ollamaAddBtn) ollamaAddBtn.addEventListener("click", () => pullOllamaModel());
if (ollamaSearchBtn) ollamaSearchBtn.addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  window.open("https://ollama.com/search?c=vision&c=tools", "_blank", "noopener");
});
if (ollamaCustomName) ollamaCustomName.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    pullOllamaModel();
  }
});
if (visionOllamaAddBtn) visionOllamaAddBtn.addEventListener("click", () => pullOllamaModel("", "vision"));
if (visionOllamaSearchBtn) visionOllamaSearchBtn.addEventListener("click", (e) => {
  e.preventDefault();
  e.stopPropagation();
  window.open("https://ollama.com/search?c=vision&c=tools", "_blank", "noopener");
});
if (visionOllamaCustomName) visionOllamaCustomName.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    pullOllamaModel("", "vision");
  }
});
selBase.addEventListener("change", () => {
  refreshModels();
  updateSummaries();
});
selApiKey.addEventListener("change", () => {
  refreshModels();
  // Если vision использует основной провайдер — его модели тоже могут пересортироваться.
  if (!selVisionProvider.value) refreshVisionModels();
});

selVisionProvider.addEventListener("change", () => {
  const vp = selVisionProvider.value;
  if (vp) {
    const p = providers.find(x => x.id === vp);
    if (p) selVisionBase.value = p.base_url;
    selVisionApiKey.value = "";
    selVisionApiKey.placeholder = (p && p.api_key_set)
      ? "••• ключ сохранён (введите, чтобы заменить)"
      : "токен провайдера";
  } else {
    selVisionBase.value = "";
  }
  visionEditMode = visionProviderNeedsEdit(selVisionProvider.value || selProvider.value);
  updateVisionProviderRowsVisibility();
  updateEditVisibility();
  if (visionEditMode) refreshVisionModels();
});
selVisionBase.addEventListener("change", refreshVisionModels);
selVisionApiKey.addEventListener("change", refreshVisionModels);
refreshVisionModelsBtn.addEventListener("click", refreshVisionModels);
visionToggle.addEventListener("click", () => {
  setVisionEnabled(!visionEnabled);
  if (visionEnabled) {
    if (!selVisionProvider.value) selVisionProvider.value = selProvider.value;
    visionEditMode = visionProviderNeedsEdit(selVisionProvider.value || selProvider.value);
    if (visionEditMode) refreshVisionModels();
  }
  updateEditVisibility();
});
selModel.addEventListener("change", updateSummaries);
selModelCustom.addEventListener("input", updateSummaries);
selVisionModel.addEventListener("change", updateSummaries);
selVisionModelCustom.addEventListener("input", updateSummaries);

modelPill.addEventListener("click", (e) => {
  e.stopPropagation();
  settingsPanel.classList.toggle("hidden");
  updateEditVisibility();
});
document.addEventListener("click", (e) => {
  const path = typeof e.composedPath === "function" ? e.composedPath() : [];
  const insideSettings = path.includes(settingsPanel) || settingsPanel.contains(e.target);
  const onModelPill = path.includes(modelPill) || e.target === modelPill;
  if (!insideSettings && !onModelPill) {
    settingsPanel.classList.add("hidden");
  }
});
cancelBtn.addEventListener("click", () => settingsPanel.classList.add("hidden"));

mainEditBtn.addEventListener("click", async () => {
  if (mainEditMode) {
    await saveSettings({ closePanel: false });
  } else {
    setMainEditMode(true);
    await refreshModels();
  }
});

visionEditBtn.addEventListener("click", async () => {
  if (visionEditMode) {
    await saveSettings({ closePanel: false });
  } else {
    setVisionEditMode(true);
    await refreshVisionModels();
  }
});

async function saveSettings({ closePanel = true } = {}) {
  const model = (selModelCustom.value.trim() || selModel.value || (currentCfg && currentCfg.model) || "").trim();
  const visionModel = (selVisionModelCustom.value.trim() || selVisionModel.value || (currentCfg && currentCfg.vision_model) || "").trim();
  const body = {
    provider: selProvider.value,
    base_url: selProvider.value === "ollama" ? "" : selBase.value.trim(),
    model,
    vision_model: visionEnabled ? visionModel : "",
    folder: selFolder.value.trim(),
    vision_provider: visionEnabled ? selVisionProvider.value : "",
    vision_base_url: visionEnabled && selVisionProvider.value && selVisionProvider.value !== "ollama" ? selVisionBase.value.trim() : "",
    safety_mode: getSafetyModeFromToggle(),
    ui_theme: currentTheme(),
  };
  const apiKey = selApiKey.value.trim();
  if (apiKey) body.api_key = apiKey;
  const visionApiKey = selVisionApiKey.value.trim();
  if (visionApiKey && selVisionProvider.value) body.vision_api_key = visionApiKey;
  try {
    const r = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    currentCfg = d.config;
    if (apiKey) {
      const p = providers.find(x => x.id === currentCfg.provider);
      if (p) {
        p.api_key_set = true;
        p.configured = true;
      }
    }
    const p = providers.find(x => x.id === currentCfg.provider);
    if (p) {
      p.model = currentCfg.model;
      p.base_url = currentCfg.base_url;
      p.configured = true;
    }
    updatePill(currentCfg);
    selModelCustom.value = "";
    selVisionModelCustom.value = "";
    setSafetyToggle(currentCfg.safety_mode || "strict");
    selApiKey.value = "";
    selVisionApiKey.value = "";
    selVisionApiKey.placeholder = currentCfg.vision_api_key_set
      ? "••• ключ сохранён (введите, чтобы заменить)"
      : "токен провайдера";
    selApiKey.placeholder = currentCfg.api_key_set ? "••• ключ сохранён (введите, чтобы заменить)" : "токен провайдера";
    mainEditMode = false;
    visionEditMode = false;
    setVisionEnabled(!!(currentCfg.vision_provider || currentCfg.vision_model));
    updateEditVisibility();
    if (closePanel) settingsPanel.classList.add("hidden");
  } catch (err) {
    alert("Ошибка: " + err.message);
  }
}

saveBtn.addEventListener("click", saveSettings);

loadConfig();
loadConversations();

// ── Контекстное меню для сообщений ──────────────────────────────────
const ctxMenu = $("#ctx-menu");
let ctxTarget = null;    // .msg element
let ctxInput = null;     // input/textarea element
let ctxSelection = "";   // выделенный текст
let ctxFull = "";        // полный текст сообщения
const TEXT_CONTROL_SELECTOR = [
  "textarea",
  "input:not([type])",
  "input[type='text']",
  "input[type='search']",
  "input[type='url']",
  "input[type='email']",
  "input[type='password']",
  "input[type='number']",
  "input[type='tel']"
].join(",");

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
  ctxInput = null;
  ctxSelection = "";
  ctxFull = "";
}

function setCtxItem(action, { text = "", visible = true, disabled = false } = {}) {
  const item = ctxMenu.querySelector(`[data-action="${action}"]`);
  if (!item) return;
  item.hidden = !visible;
  item.disabled = disabled;
  if (text) item.textContent = text;
}

function selectedTextFromControl(el) {
  if (!el || typeof el.selectionStart !== "number") return "";
  return el.value.slice(el.selectionStart, el.selectionEnd);
}

function replaceControlSelection(el, text) {
  const s = el.selectionStart ?? el.value.length;
  const e = el.selectionEnd ?? el.value.length;
  el.value = el.value.slice(0, s) + text + el.value.slice(e);
  const pos = s + text.length;
  el.setSelectionRange(pos, pos);
  el.dispatchEvent(new Event("input", { bubbles: true }));
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
  } catch (_) {
    const ta = document.createElement("textarea");
    ta.value = text;
    ta.style.position = "fixed";
    ta.style.opacity = "0";
    document.body.appendChild(ta);
    ta.select();
    try { document.execCommand("copy"); } catch (_) {}
    ta.remove();
  }
}

document.addEventListener("contextmenu", (e) => {
  const control = e.target.closest(TEXT_CONTROL_SELECTOR);
  if (!control) return;
  e.preventDefault();
  ctxInput = control;
  ctxTarget = null;
  ctxFull = "";
  ctxSelection = selectedTextFromControl(control);
  const editable = !control.disabled && !control.readOnly;
  const hasSelection = !!ctxSelection;
  const hasText = !!control.value;

  setCtxItem("copy", { text: "Копировать", visible: true, disabled: !hasSelection });
  setCtxItem("cut", { text: "Вырезать", visible: true, disabled: !editable || !hasSelection });
  setCtxItem("paste", { text: "Вставить", visible: true, disabled: !editable });
  setCtxItem("select-all", { text: "Выделить всё", visible: true, disabled: !hasText });
  setCtxItem("reply", { visible: false });

  openCtxMenu(e.clientX, e.clientY);
}, true);

messagesEl.addEventListener("contextmenu", (e) => {
  const msg = e.target.closest(".msg");
  if (!msg) return;
  e.preventDefault();
  ctxInput = null;
  ctxTarget = msg;
  ctxFull = getMsgText(msg);
  const sel = window.getSelection();
  const selText = sel && !sel.isCollapsed && msg.contains(sel.anchorNode)
    ? sel.toString() : "";
  ctxSelection = selText;

  setCtxItem("copy", {
    text: selText ? "Копировать выделенное" : "Копировать сообщение",
    visible: true,
    disabled: !(selText || ctxFull),
  });
  setCtxItem("cut", { visible: false });
  setCtxItem("paste", { visible: false });
  setCtxItem("select-all", { visible: false });
  setCtxItem("reply", {
    text: selText ? "Ответить на фрагмент" : "Ответить на сообщение",
    visible: true,
    disabled: !(selText || ctxFull),
  });

  openCtxMenu(e.clientX, e.clientY);
});

ctxMenu.addEventListener("click", async (e) => {
  const btn = e.target.closest(".ctx-item");
  if (!btn || btn.disabled) return;
  const action = btn.dataset.action;
  const text = ctxInput ? selectedTextFromControl(ctxInput) : (ctxSelection || ctxFull);

  if (action === "copy") {
    await copyText(text);
  } else if (action === "cut" && ctxInput) {
    await copyText(text);
    if (!ctxInput.readOnly && !ctxInput.disabled) {
      ctxInput.focus();
      replaceControlSelection(ctxInput, "");
    }
  } else if (action === "paste" && ctxInput) {
    ctxInput.focus();
    try { document.execCommand("paste"); } catch (_) {}
  } else if (action === "select-all" && ctxInput) {
    ctxInput.focus();
    ctxInput.select();
  } else if (action === "reply") {
    setPendingQuote(text);
    inputEl.focus();
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
