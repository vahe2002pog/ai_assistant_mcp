"""
VisionAgent — читает экран и распознаёт текст/объекты с помощью vision-LLM.

При каждом вызове execute():
  1. Делает скриншот всего экрана (или указанного окна).
  2. Отправляет изображение в LLM вместе с задачей пользователя.
  3. LLM анализирует изображение и использует инструменты:
       screen_capture        — повторный скриншот (целый экран или окно)
       screen_capture_region — скриншот региона для уточнения
       clipboard_copy        — скопировать найденный текст
  4. Возвращает результат (summary из task_done).

Вызывается HostAgent при запросах типа:
  «что написано на экране», «скопируй текст из ...», «что за окно открыто»,
  «определи текст в правом углу», а также для получения контекста другими агентами.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple

import openai

from ui_automation.agents.agent.tool_agent import (
    ToolAgent,
    _TASK_DONE, _TASK_DONE_SCHEMA,
    _build_tool_schema, _resolve_special,
    _MAX_TOOL_CALLS,
)
from ui_automation import llm_config as _llm

import asyncio
import inspect


class VisionAgent(ToolAgent):
    """
    Sub-agent for screen reading and visual object identification.
    Overrides execute() to embed a screenshot into the first LLM message.
    """

    TOOLS_MODULES = ["mcp_modules.tools_vision"]

    SYSTEM_PROMPT = """/no_think
Ты — агент компьютерного зрения. Анализируешь скриншот экрана.

Тебе передаётся изображение экрана вместе с задачей пользователя. Твоя цель:
1. Внимательно прочитай изображение.
2. Найди то, что просит пользователь: текст, надпись, кнопку, окно, элемент интерфейса.
3. Если нужен более крупный план — вызови screen_capture_region с координатами области.
4. Если пользователь просит скопировать текст — вызови clipboard_copy с найденным текстом.
5. Вызови task_done(summary="...") с точным найденным текстом или описанием объекта.

ПРАВИЛА:
- ПЕРЕД каждым tool_call в поле content напиши ОДНУ короткую строку:
  «Вижу: <что на скриншоте>. Делаю: <действие>. Жду: <результат>».
- Если текст на изображении не читается — сделай screen_capture снова или укажи region.
- У тебя жёсткий лимит tool_call'ов. Не делай больше 2–3 screen_capture подряд —
  если не видно, честно верни task_done с «не найдено».
- Отвечай на языке пользователя (русский если спрашивают по-русски).
- В summary всегда указывай точный найденный текст или «не найдено».
"""

    def execute(self, task: str, window_title: str = "") -> str:
        """
        Capture screenshot (или подхватить пути к приложенным файлам из текста
        задачи), embed as base64 image in the LLM message, run tool loop.

        :param task: Natural language user request.
        :param window_title: Optional window title to capture (empty = full screen).
        :return: Summary string from task_done().
        """
        from mcp_modules.tools_vision import capture_base64

        # ── 1. Собираем изображения ───────────────────────────────────────────
        # Если в задаче есть ссылки на приложенные пользователем файлы
        # (png/jpg/jpeg/webp/gif/bmp), грузим их вместо скриншота.
        import base64 as _b64, re as _re
        attached_paths: List[str] = []
        for m in _re.finditer(
            r'([A-Za-z]:[\\/][^\s,\]\[<>"\']+?\.(?:png|jpe?g|webp|gif|bmp))',
            task, flags=_re.IGNORECASE,
        ):
            p = m.group(1)
            if os.path.isfile(p) and p not in attached_paths:
                attached_paths.append(p)

        images_b64: List[str] = []
        if attached_paths:
            for p in attached_paths:
                try:
                    with open(p, "rb") as f:
                        images_b64.append(_b64.b64encode(f.read()).decode("ascii"))
                    print(f"  [VisionAgent] использую приложённый файл: {p}", flush=True)
                except Exception as e:
                    print(f"  [VisionAgent] не смог прочитать {p}: {e}", flush=True)

        if not images_b64:
            try:
                images_b64.append(capture_base64(window_title))
            except Exception as e:
                print(f"  [VisionAgent] Не удалось сделать скриншот: {e}", flush=True)

        image_ok = bool(images_b64)

        # ── 2. Build initial messages with vision content ─────────────────────
        first_line = task.split("\n")[0]
        print(f"\n[{self.name}] {first_line}", flush=True)

        if image_ok:
            user_content: Any = [
                {"type": "image_url",
                 "image_url": {"url": f"data:image/png;base64,{b}"}}
                for b in images_b64
            ]
            user_content.append({"type": "text", "text": task})
        else:
            user_content = task

        messages: List[Dict] = [
            {"role": "system", "content": self.SYSTEM_PROMPT},
            {"role": "user",   "content": user_content},
        ]

        # ── 3. Tool-calling loop ──────────────────────────────────────────────
        tool_calls_used = 0
        budget_warned = False
        while True:
            if tool_calls_used >= _MAX_TOOL_CALLS:
                if not budget_warned:
                    messages.append({
                        "role": "system",
                        "content": (
                            f"Достигнут лимит действий ({_MAX_TOOL_CALLS}). "
                            "Немедленно вызови task_done(summary=\"...\")."
                        ),
                    })
                    budget_warned = True
                else:
                    return (
                        f"Ошибка: превышен лимит действий vision-агента "
                        f"({_MAX_TOOL_CALLS})."
                    )
            try:
                from ui_automation.agents.agent.tool_agent import _chat_with_tools
                response = _chat_with_tools(
                    model=_llm.get_vision_model(),
                    messages=messages,
                    tools=self._schemas,
                    temperature=0.1,
                    extra_body=_llm.get_vision_extra_body(),
                    client=_llm.get_vision_client(),
                )
            except openai.APIConnectionError:
                return "Ошибка: нет соединения с моделью."
            except Exception as e:
                return f"Ошибка LLM: {e}"

            msg = response.choices[0].message

            entry: Dict = {"role": "assistant", "content": msg.content or ""}
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
                # For screen_capture / screen_capture_region — embed result image
                result = self._call_tool(fn_name, args)
                print(f"  → {result}", flush=True)

                tool_msg: Any = result

                # If the tool saved a new screenshot, embed it as vision message
                if fn_name in ("screen_capture", "screen_capture_region") and "сохранён:" in result:
                    path = result.split("сохранён:")[-1].strip()
                    try:
                        import base64 as _b64
                        with open(path, "rb") as f:
                            new_b64 = _b64.b64encode(f.read()).decode("ascii")
                        tool_msg = json.dumps([
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{new_b64}"},
                            },
                            {"type": "text", "text": f"Новый скриншот ({fn_name})."},
                        ], ensure_ascii=False)
                    except Exception:
                        pass  # fall back to plain text result

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": tool_msg,
                })

            if done:
                return result_summary
