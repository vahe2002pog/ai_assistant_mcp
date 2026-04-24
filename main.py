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
import asyncio
import inspect
import json
import os
import subprocess
import sys
import time
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

# ── Настройки модели ──────────────────────────────────────────────────────────
_API_BASE  = os.environ.get("API_BASE",  "http://localhost:8000/v1")
_API_KEY   = os.environ.get("API_KEY",   "llama")
_API_MODEL = os.environ.get("API_MODEL", "Qwen3.5-9B-abliterated-vision-Q4_K_M")

# Отключает режим размышлений (thinking) у Qwen3 при использовании llama-server с --jinja
_NO_THINK  = {"chat_template_kwargs": {"enable_thinking": False}}

# ── Системный промпт ──────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """Ты — Компас, умный персональный ассистент для Windows.

Твои возможности:
- Поиск информации в интернете (tavily_search, tavily_extract)
- Управление файлами и папками (read_file, write_file, list_directory, delete_file)
- Запуск приложений (open_app)
- Закладки браузеров (search_bookmarks, open_bookmark, list_bookmarks_browsers)
- Управление браузером (browser_navigate, browser_click, browser_get_state)
- Погода (get_weather)
- Управление звуком и медиа (control_volume, control_media)
- Управление окнами и UI (ui_list_windows, ui_click, ui_send_keys и др.)

КРИТИЧЕСКИ ВАЖНЫЕ ПРАВИЛА:
0. ДЕЛАЙ ТОЛЬКО ТО, О ЧЁМ ПРОСИТ ПОЛЬЗОВАТЕЛЬ. Не добавляй лишних шагов.
   Пример: «открой Word» → open_app("Word") → task_done. НЕ создавай документ, НЕ вводи текст, НЕ сохраняй файл, если об этом не просили.
1. После выполнения задачи ВСЕГДА вызывай инструмент task_done(summary="...") — это единственный способ завершить ответ
2. Пока task_done не вызван — выполняй следующий шаг задачи
3. Если инструмент вернул ошибку — попробуй альтернативный подход, не сдавайся
4. После open_app: используй ui_wait_for_window, если таймаут — вызови ui_list_windows для поиска реального заголовка
5. Чтобы нажать кнопку или пункт меню — используй ui_click_element(text="Текст кнопки", title_re="Окно"), он сам найдёт координаты по тексту
6. Для ввода текста и горячих клавиш: ui_focus_window → ui_send_keys. Синтаксис: "Ctrl+N", "Ctrl+S", "Alt+F4", "Enter", "Escape", "Delete"
7. Заголовки Office: "Документ1 - Word", "Книга1 - Excel", "Презентация1 - PowerPoint"
8. Если задача принципиально невозможна — вызови task_done с объяснением
9. ВСЕГДА используй tavily_search для любого поиска информации в интернете — не отвечай по памяти на вопросы о текущих событиях, ценах, погоде (если нет get_weather), курсах валют и любых фактах требующих актуальных данных
10. РАБОТА С БРАУЗЕРОМ: для любых действий в браузере (перейти на сайт, кликнуть на элемент страницы, ввести текст на сайте, прокрутить страницу, управлять вкладками) ОБЯЗАТЕЛЬНО используй browser_* инструменты (browser_navigate, browser_get_state, browser_click, browser_input_text и др.). ЗАПРЕЩЕНО использовать ui_* инструменты для взаимодействия с содержимым браузера — ui_* только для нативных Windows-приложений (Word, Excel, проводник и т.п.).
"""


# ─────────────────────────────────────────────────────────────────────────────
#  Обработка специальных команд от инструментов
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_special(result: str) -> str:
    """Выполняет side-эффекты из магических строк, возвращаемых инструментами."""
    if not isinstance(result, str):
        return str(result)

    if result.startswith("__OPEN_URL_COMMAND__:"):
        url = result[len("__OPEN_URL_COMMAND__:"):]
        try:
            os.startfile(url)
        except Exception:
            subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
        return f"Открыт URL: {url}"

    if result.startswith("__OPEN_APP_COMMAND__:"):
        path = result[len("__OPEN_APP_COMMAND__:"):]
        try:
            subprocess.Popen([path], shell=False)
        except Exception:
            subprocess.Popen(path, shell=True)
        return f"Запущено: {path}"

    if result.startswith("__OPEN_FILE_COMMAND__:"):
        path = result[len("__OPEN_FILE_COMMAND__:"):]
        try:
            os.startfile(path)
        except Exception:
            subprocess.Popen(["cmd", "/c", "start", "", path], shell=False)
        return f"Открыт файл: {path}"

    if result.startswith("__VOLUME_COMMAND__:"):
        return _exec_volume(result[len("__VOLUME_COMMAND__:"):])

    if result.startswith("__MEDIA_COMMAND__:"):
        return _exec_media(result[len("__MEDIA_COMMAND__:"):])

    return result


def _exec_volume(spec: str) -> str:
    """__VOLUME_COMMAND__:action[:amount]"""
    parts = spec.split(":")
    action = parts[0]
    amount = float(parts[1]) if len(parts) > 1 else 0.1
    try:
        from ctypes import cast, POINTER
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        vol = cast(interface, POINTER(IAudioEndpointVolume))
        if action == "mute":
            vol.SetMute(not vol.GetMute(), None)
            return "Звук отключён" if vol.GetMute() else "Звук включён"
        elif action == "set":
            vol.SetMasterVolumeLevelScalar(max(0.0, min(1.0, amount)), None)
            return f"Громкость установлена: {int(amount * 100)}%"
        elif action == "up":
            v = min(1.0, vol.GetMasterVolumeLevelScalar() + amount)
            vol.SetMasterVolumeLevelScalar(v, None)
            return f"Громкость увеличена до {int(v * 100)}%"
        elif action == "down":
            v = max(0.0, vol.GetMasterVolumeLevelScalar() - amount)
            vol.SetMasterVolumeLevelScalar(v, None)
            return f"Громкость уменьшена до {int(v * 100)}%"
        return f"Неизвестное действие с громкостью: {action}"
    except Exception as e:
        return f"Ошибка управления звуком: {e}"


def _exec_media(action: str) -> str:
    """__MEDIA_COMMAND__:action"""
    import ctypes
    _VK = {"playpause": 0xB3, "next": 0xB0, "prev": 0xB1, "stop": 0xB2}
    vk = _VK.get(action)
    if vk is None:
        return f"Неизвестное медиадействие: {action}"
    try:
        ctypes.windll.user32.keybd_event(vk, 0, 0, 0)
        ctypes.windll.user32.keybd_event(vk, 0, 0x0002, 0)
        return f"Медиа: {action}"
    except Exception as e:
        return f"Ошибка медиаклавиши: {e}"


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


_TASK_DONE_TOOL = "task_done"
_TASK_DONE_SCHEMA = {
    "type": "function",
    "function": {
        "name": _TASK_DONE_TOOL,
        "description": "Вызови этот инструмент когда задача полностью выполнена или если она невозможна. Это ЕДИНСТВЕННЫЙ способ завершить выполнение.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Краткое описание того, что было сделано, или причина невозможности выполнения.",
                }
            },
            "required": ["summary"],
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
    # Добавляем сентинел-инструмент завершения
    schemas.append(_TASK_DONE_SCHEMA)
    return registry, schemas


# ─────────────────────────────────────────────────────────────────────────────
#  Исполнение вызова инструмента
# ─────────────────────────────────────────────────────────────────────────────

def _call_tool(registry: Dict[str, Callable], name: str, args: Dict) -> str:
    """Вызывает инструмент по имени и возвращает результат как строку."""
    fn = registry.get(name)
    if fn is None:
        return f"Инструмент '{name}' не найден."
    try:
        if inspect.iscoroutinefunction(fn):
            result = asyncio.run(fn(**args))
        else:
            result = fn(**args)
        return _resolve_special(str(result) if result is not None else "")
    except Exception as e:
        return f"Ошибка при выполнении {name}: {e}"


# ─────────────────────────────────────────────────────────────────────────────
#  Один цикл обработки: ассистент → (инструменты → ассистент)*
# ─────────────────────────────────────────────────────────────────────────────

def _process_turn(
    client,
    messages: List[Dict],
    openai_tools: List[Dict],
    registry: Dict[str, Callable],
) -> None:
    """
    Выполняет один разговорный ход.
    Цикл: запрос к LLM → выполнить tool_calls → снова запрос → ...
    Останавливается только когда LLM вызывает task_done().
    Если LLM отвечает текстом без инструментов — напоминаем вызвать task_done.
    """
    import openai as _openai

    while True:
        try:
            response = client.chat.completions.create(
                model=_API_MODEL,
                messages=messages,
                tools=openai_tools,
                tool_choice="required",
                temperature=0.7,
                extra_body=_NO_THINK,
            )
        except _openai.APIConnectionError:
            print("\n[Ошибка] Не удаётся подключиться к модели. Убедись, что llama-server запущен на порту 8000.")
            return
        except Exception as e:
            print(f"\n[Ошибка модели] {e}")
            return

        choice = response.choices[0]
        msg = choice.message

        # Формируем запись в историю
        assistant_entry: Dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
        if msg.tool_calls:
            assistant_entry["tool_calls"] = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.function.name, "arguments": tc.function.arguments},
                }
                for tc in msg.tool_calls
            ]
        messages.append(assistant_entry)

        if not msg.tool_calls:
            # tool_choice="required" — не должно случаться, но если модель всё же
            # вернула текст — показываем его и выходим
            if msg.content:
                print(f"\nАссистент: {msg.content}")
            return

        # Выполняем вызовы инструментов
        done = False
        for tc in msg.tool_calls:
            fn_name = tc.function.name
            try:
                args = json.loads(tc.function.arguments)
            except Exception:
                args = {}

            args_str = ", ".join(f"{k}={v!r}" for k, v in args.items())
            print(f"\n[{fn_name}({args_str})]", flush=True)

            # Сентинел завершения — единственный выход из цикла
            if fn_name == _TASK_DONE_TOOL:
                summary = args.get("summary", "")
                print(f"\nАссистент: {summary}\n", flush=True)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": "ok",
                })
                done = True
                continue

            result = _call_tool(registry, fn_name, args)
            print(f"→ {result}", flush=True)

            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

        if done:
            return
        # Продолжаем цикл — модель получит результаты инструментов и сделает следующий шаг


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
    try:
        host = _make_host_agent()
        print("HostAgent готов.")
    except Exception as e:
        print(f"[Предупреждение] HostAgent не инициализирован ({e}), fallback на flat-режим.")
        _run_chat_flat(request)
        return

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


def _run_chat_flat(request: str = "") -> None:
    """
    Резервный flat-режим (один LLM + все инструменты).
    Используется если HostAgent недоступен.
    """
    import openai

    print("Загрузка инструментов (flat-режим)…")
    registry, openai_tools = _build_tools()
    print(f"Загружено инструментов: {len(registry)}")

    client = openai.OpenAI(base_url=_API_BASE, api_key=_API_KEY)
    messages: List[Dict] = [{"role": "system", "content": _SYSTEM_PROMPT}]

    if request:
        windows_ctx = _get_windows_context()
        rag_ctx = _rag_retrieve(request)
        user_msg = request
        if windows_ctx:
            user_msg += f"\n\n[Открытые окна]\n{windows_ctx}"
        if rag_ctx:
            user_msg += f"\n\n[Релевантный опыт]\n{rag_ctx}"
        messages.append({"role": "user", "content": user_msg})
        _process_turn(client, messages, openai_tools, registry)
        return

    print("\nАссистент готов (flat). Введи 'выход' для завершения.\n")
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
        windows_ctx = _get_windows_context()
        rag_ctx = _rag_retrieve(user_input)
        scenario = _match_scenario(user_input)
        user_msg = user_input
        if scenario:
            user_msg += (
                f"\n\n[Сценарий пользователя — \"{scenario['name']}\"]\n"
                f"{scenario['body'].strip()}"
            )
        if windows_ctx:
            user_msg += f"\n\n[Открытые окна]\n{windows_ctx}"
        if rag_ctx:
            user_msg += f"\n\n[Релевантный опыт]\n{rag_ctx}"
        messages.append({"role": "user", "content": user_msg})
        _process_turn(client, messages, openai_tools, registry)


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
