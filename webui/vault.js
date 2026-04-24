// Хранилище: Сценарии и Документы. Модальное окно поверх чата.
(() => {
  const modal = document.getElementById("vault-modal");
  if (!modal) return;
  const openBtn = document.getElementById("open-vault");
  const tabs = modal.querySelectorAll(".vault-tab");
  const panes = modal.querySelectorAll("[data-pane]");

  function escapeHtml(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    }[c]));
  }

  function open() {
    modal.classList.remove("hidden");
    loadScenarios();
    loadDocs();
  }
  function close() { modal.classList.add("hidden"); }
  if (openBtn) openBtn.addEventListener("click", open);
  modal.addEventListener("click", (e) => {
    if (e.target.matches("[data-close]")) close();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !modal.classList.contains("hidden")) close();
  });
  tabs.forEach((t) => t.addEventListener("click", () => {
    tabs.forEach((x) => x.classList.toggle("active", x === t));
    panes.forEach((p) => p.classList.toggle("hidden", p.dataset.pane !== t.dataset.tab));
  }));

  // ── Сценарии ───────────────────────────────────────────────────
  const scnList = document.getElementById("scn-list");
  const scnForm = document.getElementById("scn-form");
  const scnName = document.getElementById("scn-name");
  const scnTriggers = document.getElementById("scn-triggers");
  const scnTags = document.getElementById("scn-tags");
  const scnBody = document.getElementById("scn-body");
  const scnStatus = document.getElementById("scn-status");
  const scnNew = document.getElementById("scn-new");
  const scnDelete = document.getElementById("scn-delete");
  let currentScn = null;

  function resetScnForm() {
    currentScn = null;
    scnName.value = "";
    scnTriggers.value = "";
    scnTags.value = "scenario";
    scnBody.value = "";
    scnDelete.classList.add("hidden");
    scnStatus.textContent = "";
    scnList.querySelectorAll(".vault-row").forEach((r) => r.classList.remove("active"));
  }

  async function loadScenarios() {
    try {
      const r = await fetch("/api/vault/scenarios");
      const j = await r.json();
      renderScnList(j.items || []);
    } catch (e) {
      scnList.innerHTML = '<div class="vault-empty">Ошибка загрузки</div>';
    }
  }

  function renderScnList(items) {
    if (!items.length) {
      scnList.innerHTML = '<div class="vault-empty">Нет сценариев. Создай первый.</div>';
      return;
    }
    scnList.innerHTML = items.map((it) => {
      const fm = it.frontmatter || {};
      const trigs = Array.isArray(fm.triggers) ? fm.triggers : [];
      const chips = trigs.map((t) => '<span class="chip">' + escapeHtml(t) + "</span>").join("");
      return (
        '<div class="vault-row" data-rel="' + escapeHtml(it.rel_path) + '">' +
          '<div class="vault-row-title">' + escapeHtml(it.name) + "</div>" +
          '<div class="vault-row-chips">' + chips + "</div>" +
          '<div class="vault-row-preview">' + escapeHtml(it.preview || "") + "</div>" +
        "</div>"
      );
    }).join("");
    scnList.querySelectorAll(".vault-row").forEach((row) => {
      row.addEventListener("click", () => openScn(row.dataset.rel));
    });
  }

  async function openScn(rel) {
    try {
      const r = await fetch("/api/vault/note/" + encodeURIComponent(rel));
      if (!r.ok) throw new Error("not found");
      const n = await r.json();
      currentScn = rel;
      scnName.value = n.name || "";
      const fm = n.frontmatter || {};
      scnTriggers.value = Array.isArray(fm.triggers) ? fm.triggers.join(", ") : "";
      scnTags.value = Array.isArray(fm.tags) ? fm.tags.join(", ") : "scenario";
      scnBody.value = n.body || "";
      scnDelete.classList.remove("hidden");
      scnStatus.textContent = "";
      scnList.querySelectorAll(".vault-row").forEach((r2) =>
        r2.classList.toggle("active", r2.dataset.rel === rel));
    } catch (e) {
      scnStatus.textContent = "Ошибка: " + e;
    }
  }

  if (scnNew) scnNew.addEventListener("click", resetScnForm);

  if (scnForm) scnForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = scnName.value.trim();
    const body = scnBody.value.trim();
    if (!name || !body) return;
    const triggers = scnTriggers.value.split(",").map((s) => s.trim()).filter(Boolean);
    const tags = scnTags.value.split(",").map((s) => s.trim()).filter(Boolean);
    scnStatus.textContent = "Сохраняю…";
    try {
      const r = await fetch("/api/vault/scenarios", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ name, body, triggers, tags }),
      });
      const j = await r.json();
      if (!r.ok) throw new Error(j.error || "save failed");
      scnStatus.textContent = "Сохранено ✓";
      await loadScenarios();
    } catch (err) {
      scnStatus.textContent = "Ошибка: " + err;
    }
  });

  if (scnDelete) scnDelete.addEventListener("click", async () => {
    if (!currentScn) return;
    if (!confirm("Удалить сценарий?")) return;
    const r = await fetch("/api/vault/note/" + encodeURIComponent(currentScn), { method: "DELETE" });
    if (r.ok) { resetScnForm(); loadScenarios(); }
  });

  // ── Документы ──────────────────────────────────────────────────
  const docList = document.getElementById("doc-list");
  const docDetail = document.getElementById("doc-detail");
  const docUpload = document.getElementById("doc-upload");
  const docStatus = document.getElementById("doc-upload-status");

  async function loadDocs() {
    try {
      const r = await fetch("/api/vault/documents");
      const j = await r.json();
      renderDocs(j.items || []);
    } catch (e) {
      docList.innerHTML = '<div class="vault-empty">Ошибка</div>';
    }
  }

  function renderDocs(items) {
    if (!items.length) {
      docList.innerHTML = '<div class="vault-empty">Нет документов. Загрузи первый.</div>';
      return;
    }
    docList.innerHTML = items.map((it) => {
      const fm = it.frontmatter || {};
      return (
        '<div class="vault-row" data-rel="' + escapeHtml(it.rel_path) + '">' +
          '<div class="vault-row-title">' + escapeHtml(it.name) + "</div>" +
          '<div class="vault-row-meta">' + escapeHtml(fm.ingested || "") + "</div>" +
          '<div class="vault-row-preview">' + escapeHtml(it.preview || "") + "</div>" +
        "</div>"
      );
    }).join("");
    docList.querySelectorAll(".vault-row").forEach((row) => {
      row.addEventListener("click", () => openDoc(row.dataset.rel));
    });
  }

  async function openDoc(rel) {
    docList.querySelectorAll(".vault-row").forEach((r) =>
      r.classList.toggle("active", r.dataset.rel === rel));
    try {
      const r = await fetch("/api/vault/note/" + encodeURIComponent(rel));
      if (!r.ok) throw new Error("not found");
      const n = await r.json();
      const fm = n.frontmatter || {};
      docDetail.innerHTML =
        '<div class="vault-doc-head">' +
          "<h3>" + escapeHtml(n.name) + "</h3>" +
          '<div class="vault-doc-meta">Источник: <code>' + escapeHtml(fm.source || "") + "</code></div>" +
          '<div class="vault-doc-meta">Загружен: ' + escapeHtml(fm.ingested || "") + "</div>" +
          '<button class="btn-danger" id="doc-del">Удалить</button>' +
        "</div>" +
        '<pre class="vault-doc-body">' + escapeHtml(n.body || "") + "</pre>";
      const delBtn = document.getElementById("doc-del");
      delBtn.addEventListener("click", async () => {
        if (!confirm("Удалить документ из хранилища?")) return;
        const rr = await fetch("/api/vault/note/" + encodeURIComponent(rel), { method: "DELETE" });
        if (rr.ok) {
          docDetail.innerHTML = '<p class="vault-empty">Удалено.</p>';
          loadDocs();
        }
      });
    } catch (e) {
      docDetail.innerHTML = '<p class="vault-empty">Ошибка</p>';
    }
  }

  function readAsB64(file) {
    return new Promise((resolve, reject) => {
      const fr = new FileReader();
      fr.onload = () => resolve(String(fr.result));
      fr.onerror = reject;
      fr.readAsDataURL(file);
    });
  }

  if (docUpload) docUpload.addEventListener("change", async () => {
    const files = Array.from(docUpload.files || []);
    if (!files.length) return;
    docStatus.textContent = "Загружаю " + files.length + "…";
    for (const f of files) {
      try {
        const b64 = await readAsB64(f);
        const r = await fetch("/api/vault/documents", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ data: b64, name: f.name }),
        });
        if (!r.ok) { const j = await r.json(); throw new Error(j.error || "failed"); }
      } catch (e) {
        docStatus.textContent = "Ошибка (" + f.name + "): " + e;
      }
    }
    docStatus.textContent = "Готово ✓";
    docUpload.value = "";
    loadDocs();
  });
})();
