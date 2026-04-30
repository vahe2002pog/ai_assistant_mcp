"""
Planner — инкрементальный планировщик (ReAct).

Ответственность:
  • По (goal, history, perception, context_hint) выдать СЛЕДУЮЩИЙ один шаг (StepSpec)
    или DoneMarker, если цель достигнута.
  • НЕ исполнять и НЕ оценивать результат — это делает Controller.
  • Не знать про конкретные tools — только про AgentType.

Весь выбор агента делает LLM. Эвристик, keyword-роутинга, безопасных
дефолтов типа «упало — это chat» здесь нет: при ошибке LLM возвращается
DoneMarker с описанием проблемы, цикл завершается.
"""
from __future__ import annotations

import json
from typing import Callable, List, Optional

from ui_automation import utils, llm_config as _llm
from ui_automation.agents.contracts import (
    AgentType, StepResult, StepSpec, Target,
)

_VALID_AGENTS = {t.value for t in AgentType}
_PARALLEL_SAFE_AGENTS = {AgentType.WEB.value, AgentType.CHAT.value}


_NEXT_STEP_PROMPT = """/no_think
Ты — планировщик. Видишь цель, историю шагов и текущее восприятие — выдаёшь
СЛЕДУЮЩИЙ один шаг или маркер завершения. Не исполняй и не оценивай результат.

Верни СТРОГО один JSON:
  {"done": true, "summary": "краткий итог для пользователя"}
  {"task": "цель шага", "agent": "system|browser|web|vision|chat", "expected": "проверяемый признак"}
  {"parallel": [
    {"task": "независимая цель 1", "agent": "web", "expected": "проверяемый признак 1"},
    {"task": "независимая цель 2", "agent": "chat", "expected": "проверяемый признак 2"}
  ], "reason": "почему шаги независимы"}

АГЕНТЫ (выбери одного, остальное — забота агента):

  system — Windows: приложения, окна, файлы, медиа, UIA, MS Office через COM.
    Сюда идут «открой приложение», «нажми/введи в окне», операции с
    .xlsx/.docx/.pptx, отправка почты, файловые операции.
    tools:
      apps:    open_app, list_apps
      uia:     ui_list_windows, ui_find_window, ui_get_foreground, ui_focus_window,
               ui_wait_for_window, ui_close_window, ui_maximize_window, ui_minimize_window,
               ui_click, ui_click_element, ui_click_by_index, ui_send_keys, ui_type_text,
               ui_get_text, ui_screenshot, ui_list_interactive, ui_find_inputs,
               ui_find_elements, ui_list_processes, ui_clipboard_get, ui_clipboard_set
      files:   execute_open_file, open_folder, list_directory, search_files, view_cache, create_item,
               rename_item, copy_item, move_file, read_file, edit_file, get_file_info,
               delete_item, undo_last_action, open_recycle_bin
      media:   control_volume, control_media
      office:  office_launch, office_quit, office_visible, office_available_apps,
               office_running_apps, office_is_available, office_close_dialogs,
               office_docs_search, office_run_python, com_run_python,
               excel_create_workbook, excel_get_sheets, excel_read_sheet, excel_write_cell,
               excel_write_range, excel_apply_formula,
               word_create_document, word_read_document, word_write_text,
               word_find_replace, word_get_tables,
               ppt_create, ppt_add_slide, ppt_add_textbox, ppt_read_slides,
               outlook_send_mail, outlook_list_inbox

  browser — содержимое веб-страницы: DOM, формы, клики по элементам, вкладки.
    tools: browser_get_state, browser_navigate, browser_click, browser_input_text,
           browser_extract_content, browser_scroll_down, browser_scroll_up,
           browser_go_back, browser_send_keys, browser_open_tab, browser_switch_tab,
           browser_close_tab, browser_search_google

  web — открыть сайт/URL/закладку, веб-поиск, погода. Бери всегда, когда нужны
    актуальные факты или пользователь говорит «открой <сайт>», «найди», «погугли».
    Для сайтов НЕ используй system/browser.
    tools: open_url, web_search, web_extract, get_weather,
           search_bookmarks, open_bookmark, list_bookmarks_browsers

  vision — посмотреть/прочитать/описать содержимое экрана по скриншоту.
    tools: screen_capture, screen_capture_region, capture_base64, clipboard_copy

  chat — текстовый ответ пользователю: разговор, мнение, тривиальные знания
    (определения, простая математика), уточняющий вопрос. Без интернета и без
    системы. Для любых фактов, зависящих от года/даты/места, — web, не chat.
    tools: (нет — просто текстовый ответ LLM)

ПРАВИЛА:
- Один шаг = одна цель для одного агента. Не склеивай разных агентов.
- parallel используй ТОЛЬКО для независимых web/chat-шагов, которые не читают/не меняют
  одно и то же окно, браузер, файл или состояние ОС. Не параллель system/browser/vision.
  При сомнении верни один обычный шаг.
- Учитывай контекст из памяти/RAG и сценарии. Если в `[Релевантный опыт]`,
  `[Знание]` или `[Сценарий пользователя]` уже есть достаточная инструкция,
  планируй шаг с опорой на неё, не игнорируй этот блок.
- Для текущих фактов, цен, погоды и новостей всё равно выбирай web, даже если
  похожий старый опыт найден в памяти.
- task — цель в одной фразе, НЕ пошаговая инструкция: агент сам выберет tools.
- Если последний шаг — failure, следующий обязан исправлять причину (см. reason).
- Не повторяй только что выполненный шаг буквально.
- Если из восприятия видно, что цель достигнута — done.
- expected — конкретно проверяемо (название окна, текст элемента, факт в ответе).
- После chat-шага СРАЗУ done: ответ уже виден пользователю. Один chat-шаг = один
  полный ответ; не дроби и не добавляй «повторить/уточнить».
- Фразы «ответь в чат», «напиши здесь», «выведи сюда» НЕ означают chat-агента —
  это указание КУДА вывести; результат любого агента и так виден в чате.

История диалога (предыдущие сообщения в этом чате):
{chat_history}

Контекст из памяти, сценариев и окружения:
{context_hint}

Цель пользователя (текущее сообщение):
{goal}

История шагов ({history_len}):
{history}

Текущее восприятие:
{perception}

JSON:"""


class DoneMarker:
    """Маркер завершения задачи от next_step()."""
    __slots__ = ("summary",)

    def __init__(self, summary: str) -> None:
        self.summary = summary

    def __repr__(self) -> str:
        return f"DoneMarker({self.summary[:60]!r})"


class ParallelSteps:
    """Batch of independent steps that Controller may execute concurrently."""
    __slots__ = ("steps", "reason")

    def __init__(self, steps: List[StepSpec], reason: str = "") -> None:
        self.steps = steps
        self.reason = reason

    def __repr__(self) -> str:
        return f"ParallelSteps({len(self.steps)} steps, reason={self.reason[:60]!r})"


class Planner:
    """LLM-планировщик. Stateless."""

    def __init__(self, llm_call: Optional[Callable[[str], str]] = None) -> None:
        self._llm_call = llm_call or self._default_llm_call

    def next_step(
        self,
        goal: str,
        history: List[StepSpec],
        results: List[StepResult],
        perception: str,
        chat_history: str = "",
        context_hint: str = "",
    ) -> "StepSpec | ParallelSteps | DoneMarker":
        """Выдаёт СЛЕДУЮЩИЙ шаг на основе цели, истории и актуального восприятия.

        chat_history — текст предыдущих сообщений в чате (user/assistant), чтобы
        планировщик понимал контекст диалога, а не только последнее сообщение.
        """
        history_block = self._format_history(history, results)
        prompt = (
            _NEXT_STEP_PROMPT
            .replace("{goal}", goal)
            .replace("{history_len}", str(len(history)))
            .replace("{history}", history_block or "(пусто)")
            .replace("{perception}", perception or "(нет данных)")
            .replace("{chat_history}", chat_history.strip() or "(пусто)")
            .replace("{context_hint}", context_hint.strip() or "(пусто)")
        )

        try:
            raw = self._llm_call(prompt)
        except Exception as e:
            return DoneMarker(f"Ошибка планировщика: {e}")

        if not raw:
            return DoneMarker("Планировщик не ответил.")

        try:
            o_s, o_e = raw.find("{"), raw.rfind("}") + 1
            obj = json.loads(raw[o_s:o_e]) if o_s != -1 else {}
            if (isinstance(obj, dict)
                    and set(obj.keys()) <= {"type", "content"}
                    and isinstance(obj.get("content"), dict)):
                obj = obj["content"]
        except Exception as e:
            utils.print_with_color(
                f"[Planner.next_step] parse error: {e} | raw={raw}", "yellow"
            )
            return DoneMarker("Не удалось разобрать ответ планировщика.")

        if obj.get("done") is True:
            return DoneMarker(str(obj.get("summary", "")).strip() or "Готово.")

        if isinstance(obj.get("parallel"), list):
            steps = []
            for item in obj.get("parallel", [])[:4]:
                if not isinstance(item, dict):
                    continue
                step = self._step_from_obj(item)
                if step and step.agent.value in _PARALLEL_SAFE_AGENTS:
                    step.requires_verification = False
                    steps.append(step)
            if len(steps) >= 2:
                return ParallelSteps(
                    steps=steps,
                    reason=str(obj.get("reason", "")).strip(),
                )
            if len(steps) == 1:
                return steps[0]
            return DoneMarker(f"Планировщик вернул небезопасный parallel-шаг: {obj!r}")

        step = self._step_from_obj(obj)
        if step is None:
            return DoneMarker(f"Планировщик вернул некорректный шаг: {obj!r}")
        return step

    @staticmethod
    def _step_from_obj(obj: dict) -> Optional[StepSpec]:
        task_text = str(obj.get("task", "")).strip()
        agent_str = str(obj.get("agent", "")).strip().lower()
        expected = str(obj.get("expected", "")).strip() or task_text

        if not task_text or agent_str not in _VALID_AGENTS:
            return None

        needs_verify = agent_str in ("system", "browser")
        return StepSpec(
            agent=AgentType(agent_str),
            action_type="subtask",
            target=Target(),
            parameters={"task": task_text},
            expected_outcome=expected,
            requires_verification=needs_verify,
        )

    @staticmethod
    def _format_history(
        history: List[StepSpec], results: List[StepResult]
    ) -> str:
        lines = []
        for i, (step, res) in enumerate(zip(history, results), 1):
            task = step.parameters.get("task", step.action_type)
            status = res.status.value if res else "?"
            summary = (res.summary if res else "")[:140]
            line = f"{i}. [{step.agent.value}] {task[:80]} → {status}: {summary}"
            if res and res.error and res.error.message:
                line += f" | причина: {res.error.message[:160]}"
            lines.append(line)
        return "\n".join(lines)

    @staticmethod
    def _default_llm_call(prompt: str) -> str:
        """Три попытки: json_format+extra → extra → plain."""
        messages = [{"role": "user", "content": prompt}]
        attempts = [
            {"response_format": {"type": "json_object"}, "extra": True},
            {"response_format": None, "extra": True},
            {"response_format": None, "extra": False},
        ]
        for att in attempts:
            try:
                kw = dict(model=_llm.get_model(), messages=messages,
                          temperature=0.0, max_tokens=1024)
                if att["response_format"]:
                    kw["response_format"] = att["response_format"]
                if att["extra"]:
                    kw["extra_body"] = _llm.get_extra_body()
                resp = _llm.get_client().chat.completions.create(**kw)
                m = resp.choices[0].message
                text = ((getattr(m, "content", None) or "").strip()
                        or (getattr(m, "reasoning_content", None) or "").strip())
                if text:
                    return text
            except Exception as e:
                utils.print_with_color(
                    f"[Planner] attempt error: {e}", "yellow"
                )
        return ""


__all__ = ["Planner", "DoneMarker", "ParallelSteps"]
