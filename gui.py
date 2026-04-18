"""
Графический интерфейс ассистента (Tkinter / Win32).

Окно чата: вопрос → ответ. Снизу — поле ввода и строка статуса,
показывающая, что ассистент делает прямо сейчас.

Запуск: python main.py --gui
"""
from __future__ import annotations

import io
import queue
import re
import sys
import threading
import tkinter as tk
from tkinter import scrolledtext, ttk
from typing import Optional


_TOOL_LINE_RE = re.compile(r"^\s*\[([a-zA-Z_][\w]*)\((.*)\)\]\s*$")
_AGENT_LINE_RE = re.compile(r"^\s*\[([A-Z][A-Za-z]+Agent)\]\s+(.+)$")
_ASSISTANT_RE = re.compile(r"^\s*Ассистент:\s*(.*)$")

# Короткие понятные описания того, что делает ассистент
_TOOL_STATUS = {
    "tavily_search": "Ищу в интернете…",
    "tavily_extract": "Читаю страницу…",
    "open_app": "Запускаю приложение…",
    "open_url": "Открываю ссылку…",
    "open_bookmark": "Открываю закладку…",
    "search_bookmarks": "Ищу закладку…",
    "list_bookmarks_browsers": "Смотрю браузеры…",
    "read_file": "Читаю файл…",
    "write_file": "Записываю файл…",
    "list_directory": "Смотрю папку…",
    "delete_file": "Удаляю файл…",
    "get_weather": "Смотрю погоду…",
    "control_volume": "Настраиваю громкость…",
    "control_media": "Управляю медиа…",
    "browser_navigate": "Перехожу на сайт…",
    "browser_click": "Кликаю на странице…",
    "browser_input_text": "Ввожу текст на сайте…",
    "browser_get_state": "Смотрю страницу…",
    "browser_scroll": "Прокручиваю страницу…",
    "ui_list_windows": "Смотрю открытые окна…",
    "ui_click": "Кликаю по интерфейсу…",
    "ui_click_element": "Нажимаю кнопку…",
    "ui_send_keys": "Нажимаю клавиши…",
    "ui_focus_window": "Переключаюсь на окно…",
    "ui_wait_for_window": "Жду появления окна…",
    "ui_screenshot": "Делаю снимок экрана…",
    "task_done": "Готово.",
}

_AGENT_STATUS = {
    "HostAgent": "Думаю, как выполнить задачу…",
    "BrowserAgent": "Работаю с браузером…",
    "WebAgent": "Ищу в интернете…",
    "SystemAgent": "Работаю с системой…",
    "OfficeAgent": "Работаю с документом…",
    "VisionAgent": "Смотрю на экран…",
}


def _tool_to_status(tool: str) -> str:
    if tool in _TOOL_STATUS:
        return _TOOL_STATUS[tool]
    if tool.startswith("browser_"):
        return "Работаю с браузером…"
    if tool.startswith("ui_"):
        return "Работаю с интерфейсом…"
    return f"Выполняю: {tool}…"


class _StdoutRelay(io.TextIOBase):
    """Прокси stdout: пишет в исходный поток и кладёт строки в очередь."""

    def __init__(self, original, q: "queue.Queue[str]") -> None:
        self._orig = original
        self._q = q
        self._buf = ""

    def write(self, s: str) -> int:
        try:
            self._orig.write(s)
            self._orig.flush()
        except Exception:
            pass
        self._buf += s
        while "\n" in self._buf:
            line, self._buf = self._buf.split("\n", 1)
            if line.strip():
                self._q.put(line)
        return len(s)

    def flush(self) -> None:
        try:
            self._orig.flush()
        except Exception:
            pass


class ChatGUI:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        root.title("Компас")
        root.geometry("720x620")
        root.minsize(520, 400)

        # История чата
        self.chat = scrolledtext.ScrolledText(
            root, wrap=tk.WORD, state=tk.DISABLED,
            font=("Segoe UI", 10), bg="#ffffff", padx=10, pady=10,
        )
        self.chat.pack(fill=tk.BOTH, expand=True, padx=8, pady=(8, 4))
        self.chat.tag_config("user", foreground="#1565c0", font=("Segoe UI", 10, "bold"))
        self.chat.tag_config("assistant", foreground="#2e7d32", font=("Segoe UI", 10, "bold"))
        self.chat.tag_config("msg", foreground="#202020")

        # Статус-строка
        self.status_var = tk.StringVar(value="Готов")
        status_frame = tk.Frame(root)
        status_frame.pack(fill=tk.X, padx=8)
        self.progress = ttk.Progressbar(status_frame, mode="indeterminate", length=120)
        self.progress.pack(side=tk.LEFT, padx=(0, 8), pady=2)
        tk.Label(
            status_frame, textvariable=self.status_var,
            anchor="w", font=("Segoe UI", 9), fg="#555",
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Поле ввода
        input_frame = tk.Frame(root)
        input_frame.pack(fill=tk.X, padx=8, pady=(4, 8))
        self.entry = tk.Text(input_frame, height=3, wrap=tk.WORD, font=("Segoe UI", 10))
        self.entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.entry.bind("<Return>", self._on_enter)
        self.entry.bind("<Shift-Return>", lambda e: None)
        self.send_btn = tk.Button(
            input_frame, text="Отправить", width=12, command=self._on_send,
        )
        self.send_btn.pack(side=tk.RIGHT, padx=(6, 0))

        # Межпоточная очередь сообщений
        self.msg_q: "queue.Queue[str]" = queue.Queue()
        self.host = None
        self.busy = False

        self.root.after(50, self._poll_queue)
        self.root.after(100, self._init_host)

        # Перехват stdout для статуса
        sys.stdout = _StdoutRelay(sys.stdout, self.msg_q)

        self.entry.focus_set()

    # ── Инициализация HostAgent в фоне ────────────────────────────────────
    def _init_host(self) -> None:
        self._set_status("Инициализация…", busy=True)
        threading.Thread(target=self._init_host_worker, daemon=True).start()

    def _init_host_worker(self) -> None:
        try:
            import main as _m
            _m._ensure_apps_scanned()
            _m._start_ws_bridge()
            self.host = _m._make_host_agent()
            self.msg_q.put("__READY__")
        except Exception as e:
            self.msg_q.put(f"__INIT_ERROR__{e}")

    # ── UI-хелперы ─────────────────────────────────────────────────────────
    def _append(self, who: str, text: str) -> None:
        self.chat.configure(state=tk.NORMAL)
        if self.chat.index("end-1c") != "1.0":
            self.chat.insert(tk.END, "\n\n")
        tag = "user" if who == "Вы" else "assistant"
        self.chat.insert(tk.END, f"{who}:\n", tag)
        self.chat.insert(tk.END, text, "msg")
        self.chat.configure(state=tk.DISABLED)
        self.chat.see(tk.END)

    def _set_status(self, text: str, busy: Optional[bool] = None) -> None:
        self.status_var.set(text)
        if busy is True and not self.busy:
            self.progress.start(12)
            self.busy = True
        elif busy is False and self.busy:
            self.progress.stop()
            self.busy = False

    def _set_enabled(self, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        self.entry.configure(state=state)
        self.send_btn.configure(state=state)
        if enabled:
            self.entry.focus_set()

    # ── Обработка отправки ────────────────────────────────────────────────
    def _on_enter(self, event) -> str:
        if event.state & 0x0001:  # Shift
            return ""
        self._on_send()
        return "break"

    def _on_send(self) -> None:
        if self.host is None:
            self._set_status("Ещё не готов, подожди…")
            return
        text = self.entry.get("1.0", tk.END).strip()
        if not text:
            return
        self.entry.delete("1.0", tk.END)
        self._append("Вы", text)
        self._set_enabled(False)
        self._set_status("Думаю…", busy=True)
        threading.Thread(target=self._dispatch_worker, args=(text,), daemon=True).start()

    def _dispatch_worker(self, user_input: str) -> None:
        try:
            import main as _m
            windows_ctx = _m._get_windows_context()
            rag_ctx = _m._rag_retrieve(user_input)
            hint = ""
            if windows_ctx:
                hint += f"[Открытые окна]\n{windows_ctx}\n"
            if rag_ctx:
                hint += f"[Релевантный опыт]\n{rag_ctx}"
            result = self.host.dispatch(user_input, context_hint=hint)
            voice = getattr(result, "voice", None) or str(result)
            self.msg_q.put(f"__REPLY__{voice}")
        except Exception as e:
            self.msg_q.put(f"__REPLY__[Ошибка] {e}")

    # ── Очередь: статус и ответы ──────────────────────────────────────────
    def _poll_queue(self) -> None:
        try:
            while True:
                line = self.msg_q.get_nowait()
                self._handle_line(line)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_queue)

    def _handle_line(self, line: str) -> None:
        if line == "__READY__":
            self._set_status("Готов", busy=False)
            return
        if line.startswith("__INIT_ERROR__"):
            self._set_status("Ошибка инициализации: " + line[len("__INIT_ERROR__"):], busy=False)
            return
        if line.startswith("__REPLY__"):
            reply = line[len("__REPLY__"):]
            self._append("Ассистент", reply)
            self._set_status("Готов", busy=False)
            self._set_enabled(True)
            return

        # Парсим stdout-строки для статуса
        m = _TOOL_LINE_RE.match(line)
        if m:
            tool = m.group(1)
            if tool == "task_done":
                self._set_status("Завершаю…", busy=True)
            else:
                self._set_status(_tool_to_status(tool), busy=True)
            return
        m = _AGENT_LINE_RE.match(line)
        if m:
            agent = m.group(1)
            self._set_status(_AGENT_STATUS.get(agent, f"{agent} работает…"), busy=True)
            return


def run_gui() -> None:
    root = tk.Tk()
    try:
        # Лучшая поддержка HiDPI на Windows
        from ctypes import windll
        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass
    ChatGUI(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui()
