const WS_URL = "ws://127.0.0.1:9009";
let ws = null;
let reconnectTimer = null;
let keepAliveTimer = null;

function connect() {
  clearTimeout(reconnectTimer);
  ws = new WebSocket(WS_URL);

  ws.onopen = () => {
    console.log("[Bridge] Connected to MCP server");
    updateIcon(true);
    startKeepAlive();
  };

  ws.onmessage = async (event) => {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }
    // Пропускаем ping-сообщения
    if (msg.type === "ping") {
      ws.send(JSON.stringify({ type: "pong" }));
      return;
    }
    let result;
    try {
      result = await handleCommand(msg);
    } catch (e) {
      result = { error: String(e) };
    }
    try {
      ws.send(JSON.stringify({ id: msg.id, result }));
    } catch (e) {
      console.error("[Bridge] Send error:", e);
    }
  };

  ws.onclose = () => {
    updateIcon(false);
    stopKeepAlive();
    reconnectTimer = setTimeout(connect, 3000);
  };

  ws.onerror = (e) => {
    console.error("[Bridge] WS error:", e);
    ws.close();
  };
}

// Keepalive — предотвращает засыпание service worker
function startKeepAlive() {
  stopKeepAlive();
  keepAliveTimer = setInterval(() => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify({ type: "ping" }));
    }
    // Дополнительно будим service worker через chrome.runtime
    chrome.runtime.getPlatformInfo(() => {});
  }, 20000);
}

function stopKeepAlive() {
  if (keepAliveTimer) {
    clearInterval(keepAliveTimer);
    keepAliveTimer = null;
  }
}

async function getActiveTab() {
  // lastFocused окно — то, в котором пользователь реально работает.
  // currentWindow в MV3 service worker ненадёжен: возвращает случайное Chrome-окно.
  try {
    const win = await chrome.windows.getLastFocused({ windowTypes: ["normal"], populate: true });
    if (win && win.tabs) {
      const active = win.tabs.find(t => t.active);
      if (active) return { tab: active, windowId: win.id, tabs: win.tabs };
    }
  } catch {}
  const [tab] = await chrome.tabs.query({ active: true, lastFocusedWindow: true });
  const tabs = tab ? await chrome.tabs.query({ windowId: tab.windowId }) : [];
  return { tab, windowId: tab && tab.windowId, tabs };
}

async function handleCommand(msg) {
  const { command, params } = msg;

  switch (command) {

    case "get_state": {
      const { tab, tabs } = await getActiveTab();
      const elements = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: getInteractiveElements,
      });
      return {
        url: tab.url,
        title: tab.title,
        tabs: tabs.map(t => ({ id: t.id, url: t.url, title: t.title })),
        elements: elements[0].result,
      };
    }

    case "get_all_tabs": {
      const allTabs = await chrome.tabs.query({});
      return {
        tabs: allTabs.map(t => ({ id: t.id, windowId: t.windowId, url: t.url, title: t.title, active: t.active })),
      };
    }

    case "navigate": {
      const { tab } = await getActiveTab();
      await chrome.tabs.update(tab.id, { url: params.url });
      await waitForLoad(tab.id);
      return { ok: true };
    }

    case "click": {
      const { tab } = await getActiveTab();
      const res = await chrome.scripting.executeScript({
        target: { tabId: tab.id, allFrames: true },
        func: (index) => {
          const el = document.querySelector(`[data-mcp-index="${index}"]`);
          if (!el) return false;
          el.scrollIntoView({ block: "center", inline: "center" });
          const rect = el.getBoundingClientRect();
          const cx = rect.left + rect.width / 2;
          const cy = rect.top + rect.height / 2;
          const opts = { bubbles: true, cancelable: true, view: window, clientX: cx, clientY: cy, button: 0 };
          try { el.focus({ preventScroll: true }); } catch {}
          el.dispatchEvent(new PointerEvent("pointerdown", opts));
          el.dispatchEvent(new MouseEvent("mousedown", opts));
          el.dispatchEvent(new PointerEvent("pointerup", opts));
          el.dispatchEvent(new MouseEvent("mouseup", opts));
          el.dispatchEvent(new MouseEvent("click", opts));
          return true;
        },
        args: [params.index],
      });
      return { ok: res.some(r => r.result) };
    }

    case "input_text": {
      const { tab } = await getActiveTab();
      const res = await chrome.scripting.executeScript({
        target: { tabId: tab.id, allFrames: true },
        func: (index, text) => {
          const el = document.querySelector(`[data-mcp-index="${index}"]`);
          if (!el) return false;
          el.scrollIntoView({ block: "center" });
          try { el.focus({ preventScroll: true }); } catch {}

          // contenteditable (Gmail, Notion, etc.)
          if (el.isContentEditable) {
            el.textContent = "";
            document.execCommand && document.execCommand("insertText", false, text);
            if (el.textContent !== text) {
              el.textContent = text;
              el.dispatchEvent(new InputEvent("input", { bubbles: true, data: text, inputType: "insertText" }));
            }
            return true;
          }

          // native input/textarea — обходим React value tracker
          const proto = el instanceof HTMLTextAreaElement
            ? HTMLTextAreaElement.prototype
            : HTMLInputElement.prototype;
          const setter = Object.getOwnPropertyDescriptor(proto, "value")?.set;
          if (setter) setter.call(el, text);
          else el.value = text;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
          return true;
        },
        args: [params.index, params.text],
      });
      return { ok: res.some(r => r.result) };
    }

    case "extract_content": {
      const { tab } = await getActiveTab();
      const res = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => document.body.innerText,
      });
      return { content: res[0].result };
    }

    case "scroll": {
      const { tab } = await getActiveTab();
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: (amount) => window.scrollBy(0, amount),
        args: [params.amount],
      });
      return { ok: true };
    }

    case "go_back": {
      const { tab } = await getActiveTab();
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => history.back(),
      });
      return { ok: true };
    }

    case "send_keys": {
      const { tab } = await getActiveTab();
      await chrome.scripting.executeScript({
        target: { tabId: tab.id, allFrames: true },
        func: (keys) => {
          // поддержка сочетаний "Ctrl+Enter", "Shift+Tab"
          const parts = String(keys).split("+").map(s => s.trim());
          const key = parts.pop();
          const mods = {
            ctrlKey: parts.some(p => /^(ctrl|control)$/i.test(p)),
            shiftKey: parts.some(p => /^shift$/i.test(p)),
            altKey: parts.some(p => /^alt$/i.test(p)),
            metaKey: parts.some(p => /^(meta|cmd|command|win)$/i.test(p)),
          };
          const keyMap = { Space: " ", Spacebar: " " };
          const k = keyMap[key] || key;
          const codeMap = {
            Enter: "Enter", Tab: "Tab", Escape: "Escape", Esc: "Escape",
            Backspace: "Backspace", Delete: "Delete",
            ArrowUp: "ArrowUp", ArrowDown: "ArrowDown",
            ArrowLeft: "ArrowLeft", ArrowRight: "ArrowRight",
            " ": "Space",
          };
          const code = codeMap[k] || (k.length === 1 ? "Key" + k.toUpperCase() : k);
          const target = document.activeElement && document.activeElement !== document.body
            ? document.activeElement
            : document.body;
          const opts = { key: k, code, bubbles: true, cancelable: true, composed: true, ...mods };
          target.dispatchEvent(new KeyboardEvent("keydown", opts));
          target.dispatchEvent(new KeyboardEvent("keypress", opts));

          // для Enter в input/textarea — submit form если keydown не отменили
          if (k === "Enter" && !mods.ctrlKey && !mods.shiftKey && !mods.altKey) {
            const form = target.form;
            if (form && typeof form.requestSubmit === "function") {
              try { form.requestSubmit(); } catch { form.submit && form.submit(); }
            }
          }
          target.dispatchEvent(new KeyboardEvent("keyup", opts));
          return true;
        },
        args: [params.keys],
      });
      return { ok: true };
    }

    case "new_tab": {
      const tab = await chrome.tabs.create({ url: params.url });
      return { tab_id: tab.id };
    }

    case "switch_tab": {
      await chrome.tabs.update(params.tab_id, { active: true });
      return { ok: true };
    }

    case "close_tab": {
      const { tab } = await getActiveTab();
      await chrome.tabs.remove(tab.id);
      return { ok: true };
    }

    default:
      return { error: `Unknown command: ${command}` };
  }
}

function getInteractiveElements() {
  const selectors = "a, button, input, select, textarea, [role='button'], [role='link'], [tabindex]";
  const elements = Array.from(document.querySelectorAll(selectors));
  return elements.slice(0, 200).map((el, i) => {
    el.setAttribute("data-mcp-index", i);
    const tag = el.tagName.toLowerCase();
    const text = (el.innerText || el.value || el.placeholder || el.getAttribute("aria-label") || "").trim().slice(0, 100);
    const type = el.getAttribute("type") || "";
    return { index: i, tag, text, type };
  });
}

function waitForLoad(tabId) {
  return new Promise(resolve => {
    const listener = (id, info) => {
      if (id === tabId && info.status === "complete") {
        chrome.tabs.onUpdated.removeListener(listener);
        resolve();
      }
    };
    chrome.tabs.onUpdated.addListener(listener);
    setTimeout(resolve, 5000);
  });
}

function updateIcon(connected) {
  chrome.action.setBadgeText({ text: connected ? "ON" : "OFF" });
  chrome.action.setBadgeBackgroundColor({ color: connected ? "#22c55e" : "#ef4444" });
}

chrome.runtime.onMessage.addListener((msg, sender, sendResponse) => {
  if (msg.type === "get_status") {
    sendResponse({ connected: ws !== null && ws.readyState === WebSocket.OPEN });
  }
  return true;
});

// Держим service worker живым через chrome.alarms
chrome.alarms.create("keepalive", { periodInMinutes: 0.4 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "keepalive") {
    if (!ws || ws.readyState !== WebSocket.OPEN) {
      connect();
    }
  }
});

connect();
