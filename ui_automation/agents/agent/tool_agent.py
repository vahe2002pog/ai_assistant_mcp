"""
ToolAgent — lightweight base agent that executes tasks via LLM + tool-calling loop.

Subclasses define:
  TOOLS_MODULES  — list of module names to import tools from
  SYSTEM_PROMPT  — agent-specific system prompt

Usage:
    class MyAgent(ToolAgent):
        TOOLS_MODULES = ["mcp_modules.tools_foo"]
        SYSTEM_PROMPT = "You are a foo agent..."

    agent = MyAgent("my_agent")
    result = agent.execute("do something")
"""

from __future__ import annotations

import asyncio
import inspect
import json
import os
import subprocess
import time
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

import openai

from ui_automation import cancel as _cancel
from ui_automation import sources as _sources
from ui_automation import touched_files as _touched
from ui_automation import llm_config as _llm

# ── Контекст-компакция ────────────────────────────────────────────────────────
_CTX_LIMIT_TOKENS = int(os.environ.get("AGENT_CTX_LIMIT", "50000"))
_CTX_KEEP_TURNS   = int(os.environ.get("AGENT_CTX_KEEP_TURNS", "4"))

# ── Budget: макс. tool_call'ов внутри одного execute() ───────────────────────
# Крупные значения → worker блуждает по UI; малые → Controller чаще
# перепланирует, что дешевле и даёт Perceiver'у свежий снимок мира.
_MAX_TOOL_CALLS = int(os.environ.get("AGENT_MAX_TOOL_CALLS", "6"))

# ── Perceive-cache: короткоживущий кэш read-only "снимков мира" ──────────────
# Идея: если worker только что вызвал ui_get_foreground, и через долю секунды
# зовёт его снова с теми же аргументами — отдаём из кэша, не трогая Win32/UIA.
# Кэш процесс-глобальный: несколько worker'ов в рамках одного шага могут
# переиспользовать снимок друг друга. TTL маленький — 2 сек, ровно чтобы
# покрыть серию чтений внутри одного action.
_PERCEIVE_CACHE_TTL = float(os.environ.get("AGENT_PERCEIVE_TTL", "2.0"))
_PERCEIVE_CACHEABLE = {
    "ui_get_foreground", "ui_list_windows", "ui_list_interactive",
    "ui_find_elements", "ui_find_inputs", "ui_get_text",
    "browser_get_state",
}
_perceive_cache: Dict[Tuple[str, str], Tuple[float, str]] = {}
_perceive_cache_lock = threading.Lock()


def _perceive_cache_get(name: str, args_key: str) -> Optional[str]:
    with _perceive_cache_lock:
        hit = _perceive_cache.get((name, args_key))
        if hit is None:
            return None
        ts, value = hit
        if (time.time() - ts) > _PERCEIVE_CACHE_TTL:
            _perceive_cache.pop((name, args_key), None)
            return None
        return value


def _perceive_cache_put(name: str, args_key: str, value: str) -> None:
    with _perceive_cache_lock:
        _perceive_cache[(name, args_key)] = (time.time(), value)


def _perceive_cache_invalidate() -> None:
    """Любое mutating-действие (клик, ввод, навигация) делает снимки устаревшими."""
    with _perceive_cache_lock:
        _perceive_cache.clear()


def _estimate_tokens(messages: List[Dict]) -> int:
    """Грубая оценка: ~3 символа на токен для смеси рус/англ."""
    total = 0
    for m in messages:
        c = m.get("content")
        if c:
            total += len(str(c))
        for tc in (m.get("tool_calls") or []):
            fn = tc.get("function", {}) or {}
            total += len(fn.get("name", "")) + len(fn.get("arguments", ""))
    return total // 3


def _compact_messages(messages: List[Dict],
                      limit: int = _CTX_LIMIT_TOKENS,
                      keep_turns: int = _CTX_KEEP_TURNS) -> List[Dict]:
    """Сворачивает старые assistant/tool-пары в текстовую сводку, если контекст
    превышает лимит. Сохраняет system-промпт, первое user-сообщение и последние
    keep_turns полных turn-блоков (assistant + его tool-ответы)."""
    if _estimate_tokens(messages) < limit:
        return messages

    head: List[Dict] = []
    body: List[Dict] = []
    for m in messages:
        if not head and m.get("role") == "system":
            head.append(m); continue
        if len(head) == 1 and m.get("role") == "user":
            head.append(m); continue
        body.append(m)

    # Группируем body в turn'ы: assistant [+ tool_calls], затем его tool-ответы.
    turns: List[List[Dict]] = []
    i = 0
    while i < len(body):
        turn = [body[i]]; i += 1
        while i < len(body) and body[i].get("role") == "tool":
            turn.append(body[i]); i += 1
        turns.append(turn)

    if len(turns) <= keep_turns:
        return messages

    dropped = turns[:-keep_turns]
    kept    = turns[-keep_turns:]

    lines: List[str] = []
    for turn in dropped:
        asst = turn[0]
        for tc in (asst.get("tool_calls") or []):
            fn = tc.get("function", {}) or {}
            args_preview = (fn.get("arguments") or "")
            lines.append(f"- {fn.get('name','')}({args_preview})")
        for t in turn[1:]:
            r = str(t.get("content") or "").replace("\n", " ")
            lines.append(f"  → {r}")

    summary = {
        "role": "system",
        "content": "[Контекст сокращён. Краткая история предыдущих шагов:]\n"
                   + "\n".join(lines[-300:]),
    }
    result = head + [summary]
    for turn in kept:
        result.extend(turn)
    print(f"  [context compacted: {len(messages)}→{len(result)} messages, "
          f"dropped {len(dropped)} turns]", flush=True)
    return result


# ── task_done sentinel tool ───────────────────────────────────────────────────
_TASK_DONE = "task_done"


def _strip_task_done_mentions(text: str) -> str:
    """Убирает литеральные упоминания `task_done(...)` из текста — модель иногда
    пишет их словами вместо вызова tool'а, и эта строка просачивается в ответ."""
    if not text:
        return text
    import re as _re
    cleaned = _re.sub(r"task_done\s*\([^)]*\)\s*\.?\s*", "", text, flags=_re.IGNORECASE)
    return cleaned.strip()
_TASK_DONE_SCHEMA: Dict = {
    "type": "function",
    "function": {
        "name": _TASK_DONE,
        "description": "Вызови этот инструмент когда задача полностью выполнена или невозможна.",
        "parameters": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Краткое описание результата или причины невозможности.",
                }
            },
            "required": ["summary"],
        },
    },
}

# ── JSON Schema helpers (mirrored from main.py) ───────────────────────────────
_PY_TO_JSON: Dict[Any, str] = {
    str: "string", int: "integer", float: "number", bool: "boolean",
}


def _ann_to_schema(ann: Any) -> Dict:
    import typing
    origin = getattr(ann, "__origin__", None)
    if origin in (list, List):
        args = getattr(ann, "__args__", (str,))
        return {"type": "array", "items": _ann_to_schema(args[0] if args else str)}
    if origin is typing.Union:
        args = [a for a in ann.__args__ if a is not type(None)]
        if len(args) == 1:
            return _ann_to_schema(args[0])
        return {"type": "string"}
    return {"type": _PY_TO_JSON.get(ann, "string")}


def _build_tool_schema(name: str, fn: Callable) -> Dict:
    from typing import get_type_hints
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


# Кэш моделей, которые не умеют tool_choice="required" — чтобы не биться каждый раз.
_NO_REQUIRED_TOOL_CHOICE: set = set()


def _chat_with_tools(*, model, messages, tools, temperature, extra_body, client):
    """chat.completions.create с фолбэком tool_choice="required" → "auto"
    для моделей, где required не поддержан (например, deepseek-*)."""
    use_required = model not in _NO_REQUIRED_TOOL_CHOICE
    kwargs = dict(
        model=model, messages=messages, tools=tools,
        temperature=temperature, extra_body=extra_body,
    )
    if use_required:
        kwargs["tool_choice"] = "required"
    try:
        return client.chat.completions.create(**kwargs)
    except openai.BadRequestError as e:
        if use_required and "tool_choice" in str(e).lower():
            _NO_REQUIRED_TOOL_CHOICE.add(model)
            kwargs.pop("tool_choice", None)
            kwargs["tool_choice"] = "auto"
            return client.chat.completions.create(**kwargs)
        raise


def _resolve_special(result: str) -> str:
    """Handle magic command strings returned by some tools."""
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
    return result


# ── Base ToolAgent ────────────────────────────────────────────────────────────

class ToolAgent:
    """
    Lightweight agent: runs an LLM tool-calling loop over a defined set of tools.
    Subclasses set TOOLS_MODULES and SYSTEM_PROMPT.
    """

    # Дефолты — системный агент (приложения, окна, файлы, медиа).
    # Специализированные подтипы (BrowserAgent, VisionAgent, HostAgent с web)
    # переопределяют эти атрибуты через параметры конструктора или наследование.
    TOOLS_MODULES: List[str] = [
        "mcp_modules.tools_uiautomation",
        "mcp_modules.tools_apps",
        "mcp_modules.tools_files",
        "mcp_modules.tools_media",
        "mcp_modules.tools_office",
    ]
    SYSTEM_PROMPT: str = """/no_think
Ты — системный агент для Windows (приложения, окна, файлы, медиа).

ГЛАВНЫЙ ПРИНЦИП: делай МИНИМУМ действий. Выполняй ровно то, о чём просит пользователь, и вызывай task_done. Никаких исследовательских шагов «просто посмотреть что там».

ИНСТРУМЕНТЫ:
  Запуск:       open_app(name)
  Окна:         ui_list_windows, ui_focus_window, ui_wait_for_window
  Клики:        ui_click_element(text, title_re), ui_click(x, y)
  Клавиши:      ui_send_keys(keys, title_re)
  Осмотр:       ui_list_interactive, ui_get_text, ui_screenshot
  Файлы:        execute_open_file, open_folder, list_directory, search_files, view_cache,
                create_item, rename_item, copy_item, move_file, read_file, edit_file,
                get_file_info, delete_item, undo_last_action, open_recycle_bin
  Медиа:        control_volume, control_media
  Office (COM): office_launch/office_quit/office_visible/office_available_apps/office_running_apps,
                office_close_dialogs (снимает модалки, блокирующие COM),
                office_docs_search — поиск по документации Office COM в vault
  COM (не-Office): com_run_python(code, data) — универсальный COM-exec
                   (WScript.Shell, WMI, SAPI, Adobe, AutoCAD и т.п.)
                Excel:      excel_create_workbook, excel_get_sheets, excel_read_sheet,
                            excel_write_cell, excel_write_range, excel_apply_formula
                Word:       word_create_document, word_read_document, word_write_text,
                            word_find_replace, word_get_tables
                PowerPoint: ppt_create, ppt_add_slide, ppt_add_textbox, ppt_read_slides
                Outlook:    outlook_send_mail, outlook_list_inbox
                Универсально: office_run_python(code, data) — выполняет python-код
                              с доступом к Officer.Excel/.Word/.PowerPoint/.Outlook (COM-объекты).
                ВАЖНО: Office-тулы работают НАПРЯМУЮ через COM — не нужно open_app,
                ui_focus_window, клики и send_keys. Для задач типа «напиши в ячейку A1»
                или «отправь письмо» используй Office-тулы, а не UI-автоматизацию.

ПРИОРИТЕТ OFFICE-TOOLS (жёсткое правило):
Если в задаче упоминается Excel/Word/PowerPoint/Outlook или файл с расширением
.xlsx/.xls/.docx/.doc/.pptx/.ppt, или речь про ячейки/листы/формулы/слайды/письма —
ПЕРВЫМ делом пробуй Office-тул. НЕ вызывай open_app, ui_* и не шли клавиши для
редактирования таких документов — COM-тул сделает это без GUI за один вызов.
  • «создай новый .docx / .xlsx / .pptx»       → word_create_document /
                                                  excel_create_workbook / ppt_create
  • «запиши/измени/прочитай в .xlsx»           → excel_* (не UI)
  • «добавь слайд / текст / прочитай .pptx»    → ppt_*  (не UI)
  • «вставь/найди-замени/прочитай .docx»       → word_* (не UI)
  • «отправь письмо / проверь inbox»           → outlook_* (не UI)
  • «покажи/открой окно Excel»                 → office_launch (или open_app)
  • Нестандартный сценарий (формат, диаграммы, pivot) → office_run_python.

Если Office-тул вернул ошибку со словами «диалоговое окно», «dialog»,
«wdmain» или COM зависает — в Office открыта МОДАЛКА, которая блокирует
ВСЕ COM-вызовы, пока не закрыта. Дальше решай по контексту:

  A) Диалог — ЧАСТЬ задачи пользователя (он просил «сохрани», «открой
     файл N», «напечатай» и т.п.). Взаимодействуй с ним через UI:
       ui_list_interactive(title_re="Сохранение|Save As|Открытие|Печать")
       → ui_send_keys(keys="<путь>") или ui_click_element(text="Сохранить")
     Часто быстрее просто вызвать правильный COM-метод напрямую —
     вместо того чтобы кликать Save As-диалог, сделай
     office_run_python с doc.SaveAs2(resolve_path(path), FileFormat=16).

  B) Диалог — МУСОР (Protected View, «Восстановление документа», остался
     от прошлого запуска, подтверждение перезаписи и т.п.), пользователь
     про него не просил. Вызови office_close_dialogs(app_name=...) —
     он нажмёт Escape/Cancel.

НЕ вызывай office_close_dialogs для диалогов, которые пользователь сам
спровоцировал — это отменит его действие.

office_run_python: ВНУТРИ кода используй Officer.Word/.Excel/... и resolve_path(p).
НЕ пиши win32com.client.Dispatch — это создаёт дубль Word/Excel-процесса.
Не пиши os.path.join(expanduser('~'), 'Desktop') — на Windows с OneDrive там
пусто; используй resolve_path('Desktop/имя.docx').

Если сомневаешься в имени метода COM или константе (FileFormat,
wdCollapse, olItemType, msoShape*) — СНАЧАЛА office_docs_search(query="...")
и только потом пиши код в office_run_python. Это надёжнее, чем гадать:
в vault загружена документация Office COM, которую модель не знает наизусть.
После успешного Office-тула — СРАЗУ task_done. Не нужно делать ui_screenshot
«для проверки» — verifier отдельно проверит результат.

ОБЯЗАТЕЛЬНЫЙ ПРОТОКОЛ (соблюдай ВСЕГДА):
1. ПЕРЕД любым действием с побочным эффектом (клик/клавиши/ввод/закрытие) —
   если ты ещё не видел состояние нужного окна, СНАЧАЛА вызови
   ui_get_foreground или ui_list_interactive(title_re="..."). Исключение:
   open_app(X) — можно сразу, это сам по себе initial-шаг.
2. ПОСЛЕ действия, если планируешь следующее действие — сначала повторный
   ui_get_foreground/ui_list_interactive, чтобы убедиться, что состояние
   изменилось ожидаемым образом. Два клика подряд «вслепую» — запрещено.
3. ПЕРЕД каждым tool_call в поле content напиши ОДНУ короткую строку:
   «Вижу: <что на экране>. Делаю: <действие>. Жду: <что должно измениться>».
   Это твоё обязательство — без этой строки tool_call недействителен.

ТИПОВЫЕ СЦЕНАРИИ:

1) «Открой X»: → open_app("X") → task_done. Всё.
2) «Сфокусируйся на X»: → ui_get_foreground → ui_focus_window(title_re="X") → task_done.
3) «Нажми кнопку Y в X»: → ui_focus_window → ui_list_interactive(title_re="X")
   → ui_click_element(text="Y", title_re="X") → task_done.
4) «Введи текст T в X»: → ui_focus_window → ui_list_interactive
   → ui_send_keys(keys="T", title_re="X") → task_done.
5) «Закрой X»: ui_send_keys(keys="Alt+F4", title_re="X") → task_done.

ui_send_keys — ТОЛЬКО текст или горячие клавиши ("Привет", "Ctrl+S", "Alt+F4", "Enter").
Чтобы нажать на элемент по имени — ui_click_element(text=...).

ПРАВИЛА:
• У тебя жёсткий лимит tool_call'ов на подзадачу. Не блуждай по UI —
  если после 2–3 попыток не выходит, честно вызывай task_done с описанием
  того, что получилось и что нет. Controller перепланирует.
• Не повторяй один и тот же вызов с одинаковыми аргументами.
• Если ответ инструмента начинается с «Ошибка …» — задача НЕ выполнена. Попробуй
  другой способ (например, Ctrl+N вместо {CTRL}n, Alt+F4 вместо {ALT}{F4}).
  НЕ вызывай task_done с ложным «успешно выполнено».
• Если «Состояние окна не изменилось» — действие не применилось. Проверь фокус окна,
  попробуй другой синтаксис клавиш или другой путь. Только если явно нечего больше
  делать — вызывай task_done с честным описанием результата.
• НЕ используй ui_* для содержимого браузера — это работа browser-агента.
• Формат клавиш: "Ctrl+N", "Alt+F4", "Ctrl+Shift+S", "Enter" — НЕ {CTRL}n.
• Всегда завершай через task_done(summary="...").
• ВАЖНО: что класть в task_done.summary.
  - Если запрос — действие (открой/нажми/закрой/запиши/отправь): краткий
    отчёт о факте выполнения («Открыт Excel», «Письмо отправлено»).
  - Если запрос — чтение/перечисление/получение данных
    (какие приложения, что в файле, какие листы, прочитай ячейку,
    покажи список, сколько/какой/где): summary ДОЛЖЕН СОДЕРЖАТЬ САМИ
    ДАННЫЕ из вывода тула — список, значения, текст. НЕ пиши «список
    получен» / «данные прочитаны» — сам список/данные пользователь и
    хочет увидеть. Если данных много, включи их полностью; обрезать
    будет следующий слой.
"""

    def __init__(self, name: str, host: Optional[Any] = None,
                 tools_modules: Optional[List[str]] = None,
                 system_prompt: Optional[str] = None) -> None:
        self.name = name
        self.host = host
        if tools_modules is not None:
            self.TOOLS_MODULES = tools_modules
        if system_prompt is not None:
            self.SYSTEM_PROMPT = system_prompt
        self._registry, self._schemas = self._load_tools()

    # ── tool loading ──────────────────────────────────────────────────────────

    def _load_tools(self) -> Tuple[Dict[str, Callable], List[Dict]]:
        import importlib
        registry: Dict[str, Callable] = {}
        for mod_name in self.TOOLS_MODULES:
            try:
                mod = importlib.import_module(mod_name)
                for fn_name, fn in inspect.getmembers(mod, inspect.isfunction):
                    if fn.__module__ != mod.__name__:
                        continue
                    if fn_name.startswith("_"):
                        continue
                    registry[fn_name] = fn
            except Exception as e:
                print(f"[{self.name}] Не удалось загрузить {mod_name}: {e}")
        schemas = [_build_tool_schema(n, fn) for n, fn in registry.items()]
        schemas.append(_TASK_DONE_SCHEMA)
        return registry, schemas

    def _call_tool(self, name: str, args: Dict) -> str:
        fn = self._registry.get(name)
        if fn is None:
            return f"Инструмент '{name}' не найден."

        # ── Perceive-cache (read-only) ────────────────────────────────────────
        args_key = ""
        cacheable = name in _PERCEIVE_CACHEABLE
        if cacheable:
            try:
                args_key = json.dumps(args, sort_keys=True, ensure_ascii=False)
            except Exception:
                args_key = repr(args)
            cached = _perceive_cache_get(name, args_key)
            if cached is not None:
                print(f"  [cache hit: {name}]", flush=True)
                return cached

        try:
            if inspect.iscoroutinefunction(fn):
                result = asyncio.run(fn(**args))
            else:
                result = fn(**args)
            text = _resolve_special(str(result) if result is not None else "")
        except Exception as e:
            return f"Ошибка {name}: {e}"

        if cacheable:
            _perceive_cache_put(name, args_key, text)
        else:
            # Любое не-read-only действие делает прошлые снимки неактуальными.
            _perceive_cache_invalidate()

        # Регистрируем затронутый файл/папку — это попадёт в FilesBlock ответа.
        try:
            _touched.record_from_tool(name, args, text)
        except Exception:
            pass
        return text

    # ── main execution loop ───────────────────────────────────────────────────

    def execute(self, task: str) -> str:
        """
        Run the LLM tool-calling loop for the given task.
        Returns the summary from task_done(), or the last text response.
        """
        messages: List[Dict] = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user",   "content": task},
        ]
        # Print only the first line (the actual subtask), context blocks follow after blank line
        first_line = task.split("\n")[0]
        print(f"\n[{self.name}] {first_line}", flush=True)

        tool_calls_used = 0
        budget_warned = False

        while True:
            if _cancel.is_cancelled():
                return "Отменено пользователем."
            # Hard cap: если worker сделал слишком много шагов — отдаём
            # управление обратно Controller'у, который перепланирует с
            # учётом свежего perceive. Дешевле, чем позволить модели
            # блуждать дальше.
            if tool_calls_used >= _MAX_TOOL_CALLS:
                if not budget_warned:
                    messages.append({
                        "role": "system",
                        "content": (
                            f"Достигнут лимит действий подагента ({_MAX_TOOL_CALLS}). "
                            "Немедленно вызови task_done(summary=\"...\") с честным "
                            "описанием текущего состояния, даже если цель не достигнута."
                        ),
                    })
                    budget_warned = True
                else:
                    return (
                        f"Ошибка: превышен лимит действий подагента "
                        f"({_MAX_TOOL_CALLS}). Передаю планировщику для перепланирования."
                    )
            messages = _compact_messages(messages)
            try:
                response = _chat_with_tools(
                    model=_llm.get_model(),
                    messages=messages,
                    tools=self._schemas,
                    temperature=0.3,
                    extra_body=_llm.get_extra_body(),
                    client=_llm.get_client(),
                )
            except openai.APIConnectionError:
                return "Ошибка: нет соединения с моделью."
            except Exception as e:
                return f"Ошибка LLM: {e}"

            msg = response.choices[0].message

            # Record assistant message
            entry: Dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
            # DeepSeek thinking-режим требует, чтобы reasoning_content возвращался обратно.
            rc = getattr(msg, "reasoning_content", None)
            if rc:
                entry["reasoning_content"] = rc
            if msg.tool_calls:
                entry["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    }
                    for tc in msg.tool_calls
                ]
            messages.append(entry)

            if not msg.tool_calls:
                return _strip_task_done_mentions(msg.content or "")

            done = False
            result_summary = ""
            for tc in msg.tool_calls:
                if _cancel.is_cancelled():
                    return "Отменено пользователем."
                fn_name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments)
                except Exception:
                    args = {}

                args_repr = ", ".join(f"{k}={v!r}" for k, v in args.items())
                print(f"  [{fn_name}({args_repr})]", flush=True)

                if fn_name == _TASK_DONE:
                    result_summary = args.get("summary", "")
                    print(f"  → {result_summary}", flush=True)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": "ok",
                    })
                    done = True
                    continue

                tool_calls_used += 1
                result = self._call_tool(fn_name, args)
                preview = result + ("…" if len(result) > 400 else "")
                print(f"  → {preview}", flush=True)

                # Источники — только то, что реально цитируется.
                # web_search — первичные результаты поиска (оттуда агент берёт факты).
                # extract выдаёт сырой текст страниц — добавляем тоже.
                if fn_name in ("web_search", "web_extract"):
                    _sources.add_from_text(result)

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": result,
                })

                # Если инструмент вернул скриншот — добавить изображение отдельным user-сообщением,
                # чтобы vision-модель могла видеть экран и кликать по реальным координатам.
                # (tool-сообщения не поддерживают image_url, только user-сообщения)
                if fn_name == "ui_screenshot" and "сохранён:" in result:
                    path = result.split("сохранён:")[-1].strip()
                    try:
                        import base64 as _b64
                        with open(path, "rb") as f:
                            img_b64 = _b64.b64encode(f.read()).decode("ascii")
                        messages.append({
                            "role": "user",
                            "content": [
                                {
                                    "type": "image_url",
                                    "image_url": {"url": f"data:image/png;base64,{img_b64}"},
                                },
                                {
                                    "type": "text",
                                    "text": "Это скриншот. Найди нужный элемент на изображении и используй ui_click(x, y) с его реальными координатами.",
                                },
                            ],
                        })
                    except Exception:
                        pass

            if done:
                return _strip_task_done_mentions(result_summary)
