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

# ── LLM config (same env vars as main.py) ────────────────────────────────────
_API_BASE  = os.environ.get("API_BASE",  "http://localhost:8000/v1")
_API_KEY   = os.environ.get("API_KEY",   "llama")
_API_MODEL = os.environ.get("API_MODEL", "Qwen3.5-9B-abliterated-vision-Q4_K_M")
_NO_THINK  = {"chat_template_kwargs": {"enable_thinking": False}}

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

    TOOLS_MODULES: List[str] = []
    SYSTEM_PROMPT: str = (
        "Ты — полезный ассистент. Выполни задачу используя доступные инструменты. "
        "После завершения вызови task_done(summary='...')."
    )

    def __init__(self, name: str, host: Optional[Any] = None) -> None:
        self.name = name
        self.host = host
        self._client = openai.OpenAI(base_url=_API_BASE, api_key=_API_KEY)
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
            try:
                response = self._client.chat.completions.create(
                    model=_API_MODEL,
                    messages=messages,
                    tools=self._schemas,
                    tool_choice="required",
                    temperature=0.3,
                    extra_body=_NO_THINK,
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
