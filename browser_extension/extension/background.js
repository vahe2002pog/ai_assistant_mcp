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

async function handleCommand(msg) {
  const { command, params } = msg;

  switch (command) {

    case "get_state": {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const tabs = await chrome.tabs.query({ currentWindow: true });
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

    case "navigate": {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      await chrome.tabs.update(tab.id, { url: params.url });
      await waitForLoad(tab.id);
      return { ok: true };
    }

    case "click": {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const res = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: (index) => {
          const el = document.querySelector(`[data-mcp-index="${index}"]`);
          if (el) { el.click(); return true; }
          return false;
        },
        args: [params.index],
      });
      return { ok: res[0].result };
    }

    case "input_text": {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: (index, text) => {
          const el = document.querySelector(`[data-mcp-index="${index}"]`);
          if (!el) return false;
          el.focus();
          el.value = text;
          el.dispatchEvent(new Event("input", { bubbles: true }));
          el.dispatchEvent(new Event("change", { bubbles: true }));
          return true;
        },
        args: [params.index, params.text],
      });
      return { ok: true };
    }

    case "extract_content": {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      const res = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => document.body.innerText,
      });
      return { content: res[0].result };
    }

    case "scroll": {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: (amount) => window.scrollBy(0, amount),
        args: [params.amount],
      });
      return { ok: true };
    }

    case "go_back": {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: () => history.back(),
      });
      return { ok: true };
    }

    case "send_keys": {
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
      await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: (key) => document.activeElement.dispatchEvent(
          new KeyboardEvent("keydown", { key, bubbles: true })
        ),
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
      const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
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
