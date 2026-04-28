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
let pendingDocs = [];
let pendingQuote = "";

const quoteChip = document.getElementById("quote-chip");
const quoteChipText = document.getElementById("quote-chip-text");
const quoteChipRm = document.getElementById("quote-chip-rm");

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
      pendingImages.push({ dataUrl, name: f.name });
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
const modelPill = $("#model-pill");
const settingsPanel = $("#settings-panel");
const selProvider = $("#sel-provider");
const selBase = $("#sel-base");
const selFolder = $("#sel-folder");
const folderRow = $("#folder-row");
const selApiKey = $("#sel-apikey");

function updateFolderVisibility() {
  folderRow.classList.toggle("hidden", selProvider.value !== "yandex");
}
const selModel = $("#sel-model");
const selModelCustom = $("#sel-model-custom");
const selVisionProvider = $("#sel-vision-provider");
const selVisionBase = $("#sel-vision-base");
const selVisionApiKey = $("#sel-vision-apikey");
const selVisionModel = $("#sel-vision-model");
const selVisionModelCustom = $("#sel-vision-model-custom");
const visionBaseRow = $("#vision-base-row");
const visionApiKeyRow = $("#vision-apikey-row");
const refreshVisionModelsBtn = $("#refresh-vision-models");

function updateVisionProviderRowsVisibility() {
  // Пусто = "как основной" — прячем base/key.
  const isCustom = !!selVisionProvider.value;
  visionBaseRow.classList.toggle("hidden", !isCustom);
  visionApiKeyRow.classList.toggle("hidden", !isCustom);
}
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
    const provOpts = providers.map(p => `<option value="${p.id}">${p.label}</option>`).join("");
    selProvider.innerHTML = provOpts;
    selProvider.value = currentCfg.provider;
    selBase.value = currentCfg.base_url;
    selFolder.value = currentCfg.folder || "";
    updateFolderVisibility();
    selApiKey.value = "";
    selApiKey.placeholder = currentCfg.api_key_set ? "••• ключ сохранён (введите, чтобы заменить)" : "токен провайдера";

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
    updateVisionProviderRowsVisibility();

    updatePill(currentCfg);
    await refreshModels();
    await refreshVisionModels();
  } catch (_) {}
}

const _escHtml = (s) => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
const _normModel = (m) => typeof m === "string" ? { id: m, vision: false } : { id: m.id, vision: !!m.vision };
const _renderOpt = (m) => {
  const x = _normModel(m);
  const badge = x.vision ? " 🖼" : "";
  const title = x.vision ? "поддерживает изображения" : "только текст";
  return `<option value="${_escHtml(x.id)}" title="${title}">${_escHtml(x.id)}${badge}</option>`;
};

async function _fetchModels(baseUrl, apiKey) {
  let url = "/api/models?base_url=" + encodeURIComponent(baseUrl);
  if (apiKey) url += "&api_key=" + encodeURIComponent(apiKey);
  const r = await fetch(url);
  return r.json();
}

async function refreshModels() {
  selModel.innerHTML = `<option value="">(загрузка…)</option>`;
  try {
    const d = await _fetchModels(selBase.value, selApiKey.value.trim());
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
    if (currentCfg && ids.includes(currentCfg.model)) {
      selModel.value = currentCfg.model;
    }
  } catch (_) {
    selModel.innerHTML = `<option value="">(ошибка)</option>`;
  }
}

async function refreshVisionModels() {
  selVisionModel.innerHTML = `<option value="">(загрузка…)</option>`;
  try {
    // Если vision-провайдер не выбран — берём модели основного (с его base/key).
    const useCustom = !!selVisionProvider.value;
    const base = useCustom ? selVisionBase.value : selBase.value;
    const key = useCustom ? selVisionApiKey.value.trim() : selApiKey.value.trim();
    const d = await _fetchModels(base, key);
    const list = (d.models || []).map(_normModel);
    // Только vision-модели.
    const visionOnly = list.filter(x => x.vision);
    if (!visionOnly.length) {
      selVisionModel.innerHTML = `<option value="">(нет vision-моделей)</option>`;
    } else {
      selVisionModel.innerHTML = `<option value="">(не выбрана — использовать основную)</option>`
        + visionOnly.map(_renderOpt).join("");
    }
    if (currentCfg) {
      const vids = visionOnly.map(x => x.id);
      selVisionModel.value = vids.includes(currentCfg.vision_model) ? currentCfg.vision_model : "";
    }
  } catch (_) {
    selVisionModel.innerHTML = `<option value="">(ошибка)</option>`;
  }
}

selProvider.addEventListener("change", () => {
  const p = providers.find(x => x.id === selProvider.value);
  if (p) selBase.value = p.base_url;
  selApiKey.value = "";
  selApiKey.placeholder = (p && p.api_key_set)
    ? "••• ключ сохранён (введите, чтобы заменить)"
    : "токен провайдера";
  updateFolderVisibility();
  refreshModels();
});
refreshModelsBtn.addEventListener("click", refreshModels);
selBase.addEventListener("change", refreshModels);
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
  updateVisionProviderRowsVisibility();
  refreshVisionModels();
});
selVisionBase.addEventListener("change", refreshVisionModels);
selVisionApiKey.addEventListener("change", refreshVisionModels);
refreshVisionModelsBtn.addEventListener("click", refreshVisionModels);

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
  const visionModel = (selVisionModelCustom.value.trim() || selVisionModel.value || "").trim();
  const body = {
    provider: selProvider.value,
    base_url: selBase.value.trim(),
    model,
    vision_model: visionModel,
    folder: selFolder.value.trim(),
    vision_provider: selVisionProvider.value,
    vision_base_url: selVisionProvider.value ? selVisionBase.value.trim() : "",
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
      if (p) p.api_key_set = true;
    }
    updatePill(currentCfg);
    selModelCustom.value = "";
    selVisionModelCustom.value = "";
    selApiKey.value = "";
    selVisionApiKey.value = "";
    selVisionApiKey.placeholder = currentCfg.vision_api_key_set
      ? "••• ключ сохранён (введите, чтобы заменить)"
      : "токен провайдера";
    selApiKey.placeholder = currentCfg.api_key_set ? "••• ключ сохранён (введите, чтобы заменить)" : "токен провайдера";
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
