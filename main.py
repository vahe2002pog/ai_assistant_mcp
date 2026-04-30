"""
Компас — умный персональный помощник для Windows

Режимы запуска:
    python main.py                          # интерактивный чат (по умолчанию)
    python main.py -r "открой Excel"        # разовый запрос
    python main.py --agent                  # режим автоматизации рабочего стола
    python main.py --agent -r "запрос"      # агент с конкретным запросом
    python main.py --mcp-only               # только MCP сервер (stdio)
    python main.py --list-tools             # список доступных инструментов
"""

from __future__ import annotations

import argparse
import inspect
import os
import sys
from typing import Any, Callable, Dict, List, Optional, Tuple, get_type_hints

# ── Загрузка .env ДО всех импортов ───────────────────────────────────────────
from dotenv import load_dotenv
load_dotenv()

# ── Подавляем HTTP-логи httpx/openai (INFO:httpx:...) ─────────────────────────
import logging
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("openai").setLevel(logging.WARNING)

# ── Корень проекта в sys.path ─────────────────────────────────────────────────
_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
os.environ.setdefault("no_proxy", "localhost,127.0.0.1,::1")


# ─────────────────────────────────────────────────────────────────────────────
#  Построение схем инструментов для OpenAI tool calling
# ─────────────────────────────────────────────────────────────────────────────

_PY_TO_JSON: Dict[Any, str] = {
    str: "string", int: "integer", float: "number", bool: "boolean",
}


def _ann_to_schema(ann: Any) -> Dict:
    """Конвертирует аннотацию типа Python в JSON Schema объект."""
    import typing

    origin = getattr(ann, "__origin__", None)

    # list[X] или List[X]
    if origin in (list, List):
        args = getattr(ann, "__args__", (str,))
        return {"type": "array", "items": _ann_to_schema(args[0] if args else str)}

    # Optional[X] = Union[X, None]
    if origin is typing.Union:
        args = [a for a in ann.__args__ if a is not type(None)]
        if len(args) == 1:
            return _ann_to_schema(args[0])
        # Union с несколькими типами → string как fallback
        return {"type": "string"}

    # Базовые типы
    return {"type": _PY_TO_JSON.get(ann, "string")}


def _build_tool_schema(name: str, fn: Callable) -> Dict:
    """Строит OpenAI-совместимую схему инструмента из сигнатуры функции."""
    try:
        hints = get_type_hints(fn)
    except Exception:
        hints = {}

    sig = inspect.signature(fn)
    properties: Dict[str, Dict] = {}
    required: List[str] = []

    for pname, param in sig.parameters.items():
        if pname in ("self", "cls"):
            continue
        ann = hints.get(pname, str)
        schema = _ann_to_schema(ann)

        # Описание из докстринга (ищем "pname:" в тексте)
        doc = (fn.__doc__ or "").strip()
        desc = ""
        for line in doc.splitlines():
            stripped = line.strip()
            if stripped.lower().startswith(pname + ":") or stripped.lower().startswith(pname + " ("):
                desc = stripped.split(":", 1)[-1].strip()
                break
        if desc:
            schema["description"] = desc

        properties[pname] = schema
        if param.default is inspect.Parameter.empty:
            required.append(pname)

    # Первая строка докстринга — описание инструмента
    short_doc = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else name

    return {
        "type": "function",
        "function": {
            "name": name,
            "description": short_doc,
            "parameters": {
                "type": "object",
                "properties": properties,
                **({"required": required} if required else {}),
            },
        },
    }


def _build_tools() -> Tuple[Dict[str, Callable], List[Dict]]:
    """
    Импортирует все mcp_modules и возвращает (registry, openai_schemas).
    registry: {tool_name: callable}
    openai_schemas: список схем для параметра tools= в OpenAI API
    """
    _MODULES = [
        "mcp_modules.tools_web",
        "mcp_modules.tools_files",
        "mcp_modules.tools_apps",
        "mcp_modules.tools_weather",
        "mcp_modules.tools_media",
        "mcp_modules.tools_browser",
        "mcp_modules.tools_uiautomation",
        "mcp_modules.tools_bookmarks",
        "mcp_modules.tools_office",
    ]

    import importlib
    registry: Dict[str, Callable] = {}

    for mod_name in _MODULES:
        try:
            mod = importlib.import_module(mod_name)
            for fn_name, fn in inspect.getmembers(mod, inspect.isfunction):
                if fn.__module__ != mod.__name__:
                    continue
                if fn_name.startswith("_"):
                    continue
                registry[fn_name] = fn
        except Exception as e:
            print(f"[Предупреждение] Не удалось загрузить {mod_name}: {e}")

    schemas = [_build_tool_schema(name, fn) for name, fn in registry.items()]
    return registry, schemas

# ─────────────────────────────────────────────────────────────────────────────
#  Основной чат
# ─────────────────────────────────────────────────────────────────────────────

def _ensure_apps_scanned() -> None:
    """
    Проверяет базу приложений и при необходимости запускает сканирование.
    - База пуста: сканирует синхронно без LLM, затем LLM-алиасы в фоне.
    - База есть, есть приложения без LLM-алиасов: генерирует только их в фоне.
    - База есть, все алиасы заполнены: ничего не делает.
    """
    import threading

    try:
        from database import apps_count
        count = apps_count()
    except Exception as e:
        print(f"[Предупреждение] База приложений недоступна: {e}")
        return

    if count == 0:
        print("Первый запуск: сканирование установленных приложений…")
        try:
            from app_scanner import scan_and_save
            total = scan_and_save(llm=False)
            print(f"Найдено приложений: {total}")
        except Exception as e:
            print(f"[Предупреждение] Ошибка сканирования приложений: {e}")
            return
        threading.Thread(target=_bg_llm_aliases, daemon=True).start()
    else:
        print(f"База приложений: {count} записей")

    # Сканирование закладок браузеров (быстро, всегда обновляем)
    threading.Thread(target=_bg_scan_bookmarks, daemon=True).start()

    # Проверяем есть ли приложения без LLM-алиасов
    try:
        from app_scanner import has_new_apps_for_llm
        if has_new_apps_for_llm():
            threading.Thread(target=_bg_llm_aliases, daemon=True).start()
    except Exception:
        pass


def _bg_llm_aliases() -> None:
    """Фоновая генерация LLM-алиасов для приложений без алиасов."""
    try:
        from app_scanner import generate_llm_aliases_for_new
        generate_llm_aliases_for_new()
    except Exception:
        pass


def _bg_scan_bookmarks() -> None:
    """Фоновое сканирование закладок браузеров с базовыми алиасами + LLM для новых."""
    try:
        from browser_bookmarks_scanner import scan_and_save, has_new_bookmarks_for_llm, generate_llm_aliases_for_new
        total = scan_and_save(llm=False)
        if total:
            print(f"[Закладки] Сохранено: {total}", flush=True)
        if has_new_bookmarks_for_llm():
            import threading
            threading.Thread(target=_bg_llm_bookmark_aliases, daemon=True).start()
    except Exception as e:
        print(f"[Предупреждение] Ошибка сканирования закладок: {e}", flush=True)


def _bg_llm_bookmark_aliases() -> None:
    """Фоновая генерация LLM-алиасов для закладок без кэша."""
    try:
        from browser_bookmarks_scanner import generate_llm_aliases_for_new
        generate_llm_aliases_for_new()
    except Exception:
        pass


def _rag_retrieve(query: str, top_k: int = 3) -> str:
    """Собирает релевантный контекст из Obsidian-vault (Scenarios/Experience/Knowledge/Attachments)."""
    try:
        from ui_automation.rag import vault_manager
        return vault_manager.format_context(query, k_per_folder=max(2, top_k - 1))
    except Exception:
        return ""


def _match_scenario(query: str):
    """Пробует найти сценарий по точному триггеру. Возвращает заметку или None."""
    try:
        from ui_automation.rag import vault_manager
        return vault_manager.match_scenario_by_trigger(query)
    except Exception:
        return None


def _get_windows_context() -> str:
    """Возвращает список открытых окон и диалогов для контекста агента."""
    # Сначала пробуем быстрый и надёжный способ через Win32 API (без pywinauto),
    # он быстрее и не зависает при перечислении дочерних элементов.
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        titles: list[tuple[str, str]] = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum_proc(hwnd, lParam):
            try:
                if not user32.IsWindowVisible(hwnd):
                    return True
                length = user32.GetWindowTextLengthW(hwnd)
                if length == 0:
                    return True
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                title = buf.value
                if not title or not title.strip():
                    return True
                clsbuf = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, clsbuf, 256)
                cls = clsbuf.value or ""
                titles.append((title.strip(), cls))
            except Exception:
                pass
            return True

        user32.EnumWindows(_enum_proc, 0)

        # Убираем дубликаты, сохраняя порядок
        seen = set()
        lines: list[str] = []
        for t, c in titles:
            if t in seen:
                continue
            seen.add(t)
            lines.append(f"• {t} [{c}]")

        # Попытка получить список вкладок браузера через расширение (если доступно)
        try:
            import mcp_modules.tools_browser as _tb
            try:
                resp = _tb._send_sync("get_all_tabs", None, timeout=2.0)
                if isinstance(resp, dict) and "tabs" in resp:
                    tabs = resp.get("tabs") or []
                    if tabs:
                        lines.append("")
                        lines.append("Браузерные вкладки (все окна):")
                        prev_window = None
                        for t in tabs:
                            wid = t.get("windowId")
                            if wid != prev_window:
                                prev_window = wid
                                lines.append(f"  [Окно {wid}]")
                            title = t.get("title") or "<без заголовка>"
                            url = t.get("url") or ""
                            active_mark = " *" if t.get("active") else ""
                            lines.append(f"  • {title} — {url}{active_mark}")
            except Exception:
                pass
        except Exception:
            pass

        return "\n".join(lines)
    except Exception:
        # fallback на прежний pywinauto-способ если Win32 API недоступен
        try:
            from mcp_modules.tools_uiautomation import _all_windows
            lines = []
            seen: set = set()
            for w in _all_windows():
                try:
                    title = w.window_text()
                    cls = w.class_name()
                    if title and title not in seen:
                        seen.add(title)
                        lines.append(f"• {title} [{cls}]")
                except Exception:
                    pass
            return "\n".join(lines)
        except Exception:
            return ""


def _make_host_agent():
    """Create and return a HostAgent instance for chat mode."""
    from ui_automation.agents.agent.host_agent import HostAgent

    return HostAgent()


def run_chat(request: str = "") -> None:
    """
    Основной чат-цикл.
    Схема: пользователь → HostAgent.dispatch() → sub-agent → результат
    HostAgent классифицирует задачу и делегирует BrowserAgent / WebAgent / SystemAgent.
    """
    _ensure_apps_scanned()
    _start_ws_bridge()

    print("Инициализация HostAgent…")
    host = _make_host_agent()
    print("HostAgent готов.")

    def _dispatch(user_input: str) -> None:
        windows_ctx = _get_windows_context()
        rag_ctx = _rag_retrieve(user_input)
        scenario = _match_scenario(user_input)
        context_hint = ""
        if scenario:
            context_hint += (
                f"[Сценарий пользователя — \"{scenario['name']}\"]\n"
                f"{scenario['body'].strip()}\n"
                "(Следуй шагам сценария, адаптируя под текущее состояние системы.)\n"
            )
        if windows_ctx:
            context_hint += f"[Открытые окна]\n{windows_ctx}\n"
        if rag_ctx:
            context_hint += f"[Релевантный опыт]\n{rag_ctx}"
        result = host.dispatch(user_input, context_hint=context_hint)
        # result — AssistantResponse (или строка при ошибке форматтера)
        if hasattr(result, "voice"):
            print(f"\nАссистент: {result.voice}")
            import json as _json
            print(_json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
            print()
        else:
            print(f"\nАссистент: {result}\n")

    # Режим разового запроса
    if request:
        _dispatch(request)
        return

    # Интерактивный цикл
    print("\nАссистент готов. Введи 'выход' для завершения.\n")
    while True:
        try:
            user_input = input("Вы: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nДо свидания!")
            break
        if not user_input:
            continue
        if user_input.lower() in ("выход", "exit", "quit", "/выход"):
            print("До свидания!")
            break
        _dispatch(user_input)


def _start_ws_bridge() -> None:
    """Запускает WebSocket мост для браузерного расширения в фоновом потоке (если ещё не запущен)."""
    try:
        import browser_extension.ws_server as _ws
        if _ws.is_running():
            return
        _ws.start_thread()
    except Exception as e:
        print(f"[Предупреждение] Не удалось запустить WS bridge: {e}", flush=True)


# ─────────────────────────────────────────────────────────────────────────────
#  MCP сервер
# ─────────────────────────────────────────────────────────────────────────────

def run_mcp_server() -> None:
    """
    Запускает MCP сервер инструментов (транспорт stdio).

    Субагенты и их инструменты:
      WebAgent     → tools_web.py          (поиск Tavily, extract, open_url)
      FileAgent    → tools_files.py        (чтение, запись, копирование файлов)
      AppsAgent    → tools_apps.py         (запуск приложений)
      WeatherAgent → tools_weather.py      (погода)
      MediaAgent   → tools_media.py        (звук, медиа)
      BrowserAgent → tools_browser.py      (браузер через расширение)
      UIAgent      → tools_uiautomation.py (окна, клики через pywinauto)
    """
    from mcp_modules.mcp_core import mcp

    _sub_agents = [
        "mcp_modules.tools_web",
        "mcp_modules.tools_files",
        "mcp_modules.tools_apps",
        "mcp_modules.tools_weather",
        "mcp_modules.tools_media",
        "mcp_modules.tools_browser",
        "mcp_modules.tools_uiautomation",
        "mcp_modules.tools_bookmarks",
        "mcp_modules.tools_office",
    ]
    for _mod in _sub_agents:
        __import__(_mod)

    mcp.run(transport="stdio")


def list_tools() -> None:
    """Выводит список всех доступных инструментов."""
    registry, _ = _build_tools()
    print(f"\nДоступных инструментов: {len(registry)}")
    for name in sorted(registry.keys()):
        fn = registry[name]
        doc = (fn.__doc__ or "").strip().splitlines()[0] if fn.__doc__ else ""
        print(f"  • {name}" + (f" — {doc}" if doc else ""))


# ─────────────────────────────────────────────────────────────────────────────
#  CLI
# ─────────────────────────────────────────────────────────────────────────────

def _build_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Компас — умный персональный помощник для Windows"
    )
    p.add_argument("--request", "-r", default="",
                   help="Разовый запрос (без интерактивного режима).")
    p.add_argument("--mcp-only", action="store_true",
                   help="Запустить только MCP сервер (stdio).")
    p.add_argument("--gui", action="store_true",
                   help="Запустить старый Tkinter-интерфейс (legacy).")
    p.add_argument("--web", action="store_true",
                   help="Запустить веб-интерфейс (локальный HTTP-сервер + окно Edge).")
    p.add_argument("--app", action="store_true",
                   help="Запустить как десктоп-приложение в окне pywebview (WebView2).")
    p.add_argument("--port", type=int, default=8765,
                   help="Порт веб-интерфейса (по умолчанию 8765).")
    p.add_argument("--no-open", action="store_true",
                   help="Не открывать окно/браузер автоматически (для --web).")
    p.add_argument("--browser", action="store_true",
                   help="Открыть веб-интерфейс в системном браузере, а не в окне Edge.")
    p.add_argument("--list-tools", action="store_true",
                   help="Показать список инструментов и выйти.")
    p.add_argument("--no-bridge", action="store_true",
                   help="Не запускать WebSocket мост браузерного расширения.")
    return p.parse_args()


def main() -> None:
    args = _build_args()

    if args.list_tools:
        list_tools()
        return

    if args.mcp_only:
        run_mcp_server()
        return

    if args.app:
        from web_server import run_app
        run_app(port=args.port)
        return

    if args.web:
        from web_server import run_web
        run_web(port=args.port, open_window=not args.no_open, standalone=not args.browser)
        return

    if args.gui:
        from gui import run_gui
        run_gui()
        return

    # По умолчанию — чат через HostAgent
    run_chat(request=args.request)


if __name__ == "__main__":
    main()
