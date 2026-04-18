// WebSocket-мост для --app режима.
// Подключается к ws://127.0.0.1:9009 и выполняет команды в текущем окне webview,
// имитируя API Chrome-расширения (browser_extension/extension/background.js).
// Это позволяет ассистенту управлять своим собственным UI через стандартные
// browser_* инструменты (browser_get_state, browser_click, browser_input_text и т.п.).

(() => {
  const WS_URL = "ws://127.0.0.1:9009";
  const SELF_TAB = { id: 0, windowId: 0 };
  let ws = null;
  let reconnectTimer = null;

  function connect() {
    clearTimeout(reconnectTimer);
    try {
      ws = new WebSocket(WS_URL);
    } catch (e) {
      reconnectTimer = setTimeout(connect, 3000);
      return;
    }

    ws.onopen = () => console.log("[AppBridge] Connected");

    ws.onmessage = async (event) => {
      let msg;
      try { msg = JSON.parse(event.data); } catch { return; }
      if (msg.type === "ping") {
        try { ws.send(JSON.stringify({ type: "pong" })); } catch {}
        return;
      }
      let result;
      try {
        result = await handleCommand(msg.command, msg.params || {});
      } catch (e) {
        result = { error: String(e && e.message || e) };
      }
      try {
        ws.send(JSON.stringify({ id: msg.id, result }));
      } catch (e) {
        console.error("[AppBridge] send:", e);
      }
    };

    ws.onclose = () => {
      ws = null;
      reconnectTimer = setTimeout(connect, 3000);
    };
    ws.onerror = () => { try { ws && ws.close(); } catch {} };
  }

  // ── Сканирование интерактивных элементов страницы ──────────────────
  function getInteractiveElements() {
    const sel = "a, button, input, select, textarea, [role='button'], [role='link'], [tabindex]";
    const list = Array.from(document.querySelectorAll(sel));
    return list.slice(0, 200).map((el, i) => {
      el.setAttribute("data-mcp-index", i);
      const tag = el.tagName.toLowerCase();
      const text = (el.innerText || el.value || el.placeholder
        || el.getAttribute("aria-label") || "").trim().slice(0, 120);
      const type = el.getAttribute("type") || "";
      return { index: i, tag, text, type };
    });
  }

  function pageInfo() {
    return {
      url: location.href,
      title: document.title,
      tabs: [{ id: SELF_TAB.id, url: location.href, title: document.title }],
    };
  }

  async function handleCommand(command, params) {
    switch (command) {
      case "get_state": {
        return { ...pageInfo(), elements: getInteractiveElements() };
      }

      case "get_all_tabs": {
        return {
          tabs: [{
            id: SELF_TAB.id,
            windowId: SELF_TAB.windowId,
            url: location.href,
            title: document.title,
            active: true,
          }],
        };
      }

      case "navigate": {
        // Навигация уводит UI, разрешаем только в пределах origin-а.
        const target = String(params.url || "");
        try {
          const u = new URL(target, location.href);
          if (u.origin !== location.origin) {
            return { error: "navigate: cross-origin navigation disabled in --app" };
          }
          location.href = u.toString();
          return { ok: true };
        } catch (e) {
          return { error: "navigate: invalid url" };
        }
      }

      case "click": {
        const el = document.querySelector(`[data-mcp-index="${params.index}"]`);
        if (!el) return { ok: false, error: "element not found" };
        el.click();
        return { ok: true };
      }

      case "input_text": {
        const el = document.querySelector(`[data-mcp-index="${params.index}"]`);
        if (!el) return { ok: false, error: "element not found" };
        el.focus();
        if ("value" in el) {
          el.value = params.text;
        } else {
          el.textContent = params.text;
        }
        el.dispatchEvent(new Event("input", { bubbles: true }));
        el.dispatchEvent(new Event("change", { bubbles: true }));
        return { ok: true };
      }

      case "extract_content": {
        return { content: document.body.innerText };
      }

      case "scroll": {
        const amount = Number(params.amount) || 0;
        window.scrollBy(0, amount);
        // Прокрутка внутри .messages (основной скроллящийся контейнер)
        const m = document.getElementById("messages");
        if (m) m.scrollBy(0, amount);
        return { ok: true };
      }

      case "go_back": {
        history.back();
        return { ok: true };
      }

      case "send_keys": {
        const key = String(params.keys || "");
        const ae = document.activeElement || document.body;
        ae.dispatchEvent(new KeyboardEvent("keydown", { key, bubbles: true }));
        ae.dispatchEvent(new KeyboardEvent("keyup", { key, bubbles: true }));
        return { ok: true };
      }

      case "new_tab":
      case "switch_tab":
      case "close_tab": {
        return { error: `${command}: not supported in --app (single window)` };
      }

      default:
        return { error: `Unknown command: ${command}` };
    }
  }

  connect();
})();
