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
from typing import Any, Callable, Dict, List, Optional, Tuple

import openai

from ui_automation import cancel as _cancel
from ui_automation import sources as _sources
from ui_automation import llm_config as _llm

# ── Контекст-компакция ────────────────────────────────────────────────────────
_CTX_LIMIT_TOKENS = int(os.environ.get("AGENT_CTX_LIMIT", "50000"))
_CTX_KEEP_TURNS   = int(os.environ.get("AGENT_CTX_KEEP_TURNS", "4"))


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
            args_preview = (fn.get("arguments") or "")[:120]
            lines.append(f"- {fn.get('name','')}({args_preview})")
        for t in turn[1:]:
            r = str(t.get("content") or "")[:150].replace("\n", " ")
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
  Файлы:        read_file, write_file, list_directory, delete_file, copy_file, move_file
  Медиа:        control_volume, control_media

ТИПОВЫЕ СЦЕНАРИИ:

1) «Открой X»: → open_app("X") → task_done. Всё.
2) «Сфокусируйся на X»: → ui_focus_window(title_re="X") → task_done.
3) «Нажми кнопку Y в X»: → ui_focus_window → ui_click_element(text="Y", title_re="X") → task_done.
4) «Введи текст T в X»: → ui_focus_window → ui_send_keys(keys="T", title_re="X") → task_done.
5) «Закрой X»: ui_send_keys(keys="Alt+F4", title_re="X") → task_done.

ui_send_keys — ТОЛЬКО текст или горячие клавиши ("Привет", "Ctrl+S", "Alt+F4", "Enter").
Чтобы нажать на элемент по имени — ui_click_element(text=...).

ПРАВИЛА:
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
        try:
            if inspect.iscoroutinefunction(fn):
                result = asyncio.run(fn(**args))
            else:
                result = fn(**args)
            return _resolve_special(str(result) if result is not None else "")
        except Exception as e:
            return f"Ошибка {name}: {e}"

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

        while True:
            if _cancel.is_cancelled():
                return "Отменено пользователем."
            messages = _compact_messages(messages)
            try:
                response = _llm.get_client().chat.completions.create(
                    model=_llm.get_model(),
                    messages=messages,
                    tools=self._schemas,
                    tool_choice="required",
                    temperature=0.3,
                    extra_body=_llm.get_extra_body(),
                )
            except openai.APIConnectionError:
                return "Ошибка: нет соединения с моделью."
            except Exception as e:
                return f"Ошибка LLM: {e}"

            msg = response.choices[0].message

            # Record assistant message
            entry: Dict[str, Any] = {"role": "assistant", "content": msg.content or ""}
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
                return msg.content or ""

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
                print(f"  [{fn_name}({args_repr[:100]})]", flush=True)

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

                result = self._call_tool(fn_name, args)
                preview = result[:400] + ("…" if len(result) > 400 else "")
                print(f"  → {preview}", flush=True)

                # Собираем URL из выдачи поисковых инструментов (для блока «Источники»).
                if fn_name in ("tavily_search", "tavily_extract",
                               "tavily_crawl", "tavily_map"):
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
                return result_summary
