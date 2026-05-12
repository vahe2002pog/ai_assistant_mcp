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
import re
from typing import Callable, List, Optional

from ui_automation import utils, llm_config as _llm
from ui_automation.agents.contracts import (
    AgentType, StepResult, StepSpec, StepStatus, Target,
)

_VALID_AGENTS = {t.value for t in AgentType}
_PARALLEL_SAFE_AGENTS = {AgentType.WEB.value, AgentType.CHAT.value}
_APP_WEB_FALLBACK_TERMS = (
    "музык", "песн", "трек", "плейлист", "радио", "аудио",
    "фото", "изображ", "картин", "галере", "редактор",
    "видео", "ютуб", "youtube", "клип", "фильм", "кино",
    "почт", "mail", "карта", "maps", "диск", "drive",
)
_APP_FAILURE_HINTS = (
    "не найден", "не найдена", "не найдено", "не найдены",
    "не удалось", "ошибка", "failed", "not found", "missing",
)


_WORKER_LIMIT_HINTS = (
    "превышен лимит действий подагента",
    "достигнут лимит действий подагента",
    "exceeded",
    "tool call limit",
)
_IMAGE_PATH_RE = re.compile(
    r'([A-Za-z]:[\\/][^\s,\]\[<>"\']+?\.(?:png|jpe?g|webp|gif|bmp))',
    re.IGNORECASE,
)


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    low = (text or "").lower()
    return any(needle in low for needle in needles)


def _last_worker_limit_failure(results: List[StepResult]) -> str:
    if not results:
        return ""
    last = results[-1]
    if last.status != StepStatus.FAILURE:
        return ""
    text = " ".join(
        part for part in (
            last.summary,
            last.error.message if last.error else "",
        )
        if part
    )
    return text if _contains_any(text, _WORKER_LIMIT_HINTS) else ""


def _attached_image_step(goal: str, history: List[StepSpec]) -> Optional[StepSpec]:
    if not _IMAGE_PATH_RE.search(goal or ""):
        return None
    if any(step.agent == AgentType.VISION for step in history):
        return None
    return StepSpec(
        agent=AgentType.VISION,
        action_type="subtask",
        target=Target(),
        parameters={"task": (goal or "").strip()},
        expected_outcome="Распознано содержимое прикреплённого изображения.",
        requires_verification=False,
        max_retries=0,
    )


def _web_request_from_failed_step(task_text: str, goal: str) -> str:
    """Prefer the concrete failed app action, not the whole user/scenario goal."""
    request = (task_text or "").strip() or (goal or "").strip()
    request = re.sub(
        r"(?i)^\s*(?:запусти|открой|включи)\s+"
        r"(?:локальное\s+)?приложение\s*(?:по\s+(?:смысловому\s+)?алиасу)?\s*:?\s*",
        "",
        request,
    ).strip()
    return request or (goal or "").strip()


def _web_fallback_after_app_failure(
    goal: str,
    history: List[StepSpec],
    results: List[StepResult],
) -> Optional[StepSpec]:
    """Route service-like app failures to web instead of repeating app/path search."""
    if not history or not results:
        return None
    last_step = history[-1]
    last_result = results[-1]
    if last_step.agent != AgentType.SYSTEM or last_result.status != StepStatus.FAILURE:
        return None

    task_text = str(last_step.parameters.get("task") or last_step.free_text or "")
    error_text = " ".join(
        part for part in (
            last_result.summary,
            last_result.error.message if last_result.error else "",
        )
        if part
    )
    combined = f"{goal}\n{task_text}\n{error_text}"
    if not _contains_any(combined, _APP_FAILURE_HINTS):
        return None
    if not _contains_any(combined, _APP_WEB_FALLBACK_TERMS):
        return None
    web_request = _web_request_from_failed_step(task_text, goal)

    return StepSpec(
        agent=AgentType.WEB,
        action_type="subtask",
        target=Target(),
        parameters={
            "task": (
                "Локальное приложение или действие в нем не сработало. "
                "Открой подходящий веб-сервис или официальный сайт и выполни "
                f"именно этот неуспешный запрос: {web_request}"
            ),
            "ignore_context_hint": True,
            "ignore_prev_results": True,
        },
        expected_outcome="Открыт подходящий веб-сервис или официальный сайт для выполнения запроса.",
        requires_verification=False,
        max_retries=0,
    )


def _scenario_fallback_step(context_hint: str, history: List[StepSpec]) -> Optional[StepSpec]:
    """Deterministic fallback when a matched user scenario is present."""
    if "[Сценарий пользователя" not in (context_hint or ""):
        return None

    lines: list[str] = []
    for raw_line in (context_hint or "").splitlines():
        line = raw_line.strip()
        m = re.match(r"^\d+[\.)]\s*(.+)$", line)
        if m:
            lines.append(m.group(1).strip())
    if not lines:
        return None

    done_tasks = {
        str(step.parameters.get("task") or step.free_text or step.expected_outcome or "").lower()
        for step in history
    }
    next_line = None
    for line in lines:
        low = line.lower()
        if not any(low in task or task in low for task in done_tasks if task):
            next_line = line
            break
    if not next_line:
        return None

    low = next_line.lower()
    agent = AgentType.SYSTEM
    if any(k in low for k in ("картин", "фото", "изображ", "скриншот", "посмотри")):
        agent = AgentType.VISION
    elif any(k in low for k in ("сайт", "url", "http", "в интернете", "погугли", "найди актуаль")):
        agent = AgentType.WEB
    elif low.startswith(("ответь", "напиши ответ", "объясни", "расскажи")):
        agent = AgentType.CHAT

    task_text = next_line
    m_vol = re.search(r"(?:громкость|volume)\D*(\d{1,3})", low)
    if m_vol:
        value = max(0, min(100, int(m_vol.group(1))))
        task_text = f"Установи системную громкость на {value}%"
        agent = AgentType.SYSTEM

    return StepSpec(
        agent=agent,
        action_type="subtask",
        target=Target(),
        parameters={"task": task_text},
        expected_outcome=f"Выполнен шаг сценария: {task_text}",
        requires_verification=agent in (AgentType.SYSTEM, AgentType.BROWSER),
    )


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
    Для просьб вида «включи музыку», «открой браузер», «запусти редактор» выбирай system
    и формулируй task как запуск приложения по смысловому алиасу: open_app/list_apps должны искать
    «музыка», «браузер», «редактор». Не планируй поиск файлов/путей для запуска приложений.
    НЕ выбирай system только из-за фразы «прочитай файл», если в context_hint уже есть
    `[Документ · ...]` или другой RAG-фрагмент с нужным именем/содержанием и пользователь просит
    извлечь/пересказать/найти сведения. В таком случае выбирай chat: он должен ответить по RAG.
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
- Контекст из памяти/RAG — вспомогательный, а не главный источник маршрутизации.
  Используй `[Контекст из хранилища/RAG]`, `[Знание]`, `[Документ · ...]`
  только когда пользователь явно спрашивает про сохранённые документы, память,
  хранилище, требования/выдержки из файла или сценарий. Не натягивай на RAG
  запросы про приложения, сайты, актуальные факты, экран и приложенные картинки.
- Сценарий пользователя можно учитывать как инструкцию, если он явно совпал с
  текущей задачей.
- Если в RAG-контексте есть `[Документ · ...]`, совпадающий с указанным пользователем файлом,
  и задача только извлечь факт/требование/выдержку, не ищи этот файл на диске. Верни chat-шаг,
  чтобы ответ был дан по уже найденному содержимому vault.
- Для текущих фактов, цен, погоды и новостей всё равно выбирай web, даже если
  похожий старый опыт найден в памяти.
- task — цель в одной фразе, НЕ пошаговая инструкция: агент сам выберет tools.
- Если последний шаг — failure, следующий обязан исправлять причину (см. reason).
- Если system не смог открыть/использовать приложение для сервисного запроса
  (музыка, фото, видео, почта, карты, облако и т.п.), следующим шагом выбирай web:
  открой подходящий веб-сервис/официальный сайт и выполни запрос там. Не повторяй
  поиск exe/lnk и не ищи приложение по путям.
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
    __slots__ = ("summary", "success")

    def __init__(self, summary: str, success: bool = True) -> None:
        self.summary = summary
        self.success = success

    def __repr__(self) -> str:
        return f"DoneMarker({self.summary[:60]!r}, success={self.success!r})"


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
        attached_step = _attached_image_step(goal, history)
        if attached_step is not None:
            return attached_step

        web_fallback = _web_fallback_after_app_failure(goal, history, results)
        if web_fallback is not None:
            return web_fallback
        limit_failure = _last_worker_limit_failure(results)
        if limit_failure:
            return DoneMarker(
                (
                    "Подагент исчерпал лимит действий до завершения шага. "
                    "Я остановил перепланирование, чтобы не зациклиться на пустом шаге. "
                    f"Последний результат: {limit_failure}"
                ),
                success=False,
            )

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
            scenario_fallback = _scenario_fallback_step(context_hint, history)
            if scenario_fallback is not None:
                return scenario_fallback
            fallback = self._fallback_step_from_goal(goal)
            if fallback is not None:
                return fallback
            return DoneMarker(f"Ошибка планировщика: {e}", success=False)

        if not raw:
            scenario_fallback = _scenario_fallback_step(context_hint, history)
            if scenario_fallback is not None:
                return scenario_fallback
            fallback = self._fallback_step_from_goal(goal)
            if fallback is not None:
                return fallback
            return DoneMarker("Планировщик не ответил.", success=False)

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
            scenario_fallback = _scenario_fallback_step(context_hint, history)
            if scenario_fallback is not None:
                return scenario_fallback
            fallback = self._fallback_step_from_goal(goal)
            if fallback is not None:
                return fallback
            return DoneMarker("Не удалось разобрать ответ планировщика.", success=False)

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
            return DoneMarker(
                f"Планировщик вернул небезопасный parallel-шаг: {obj!r}",
                success=False,
            )

        step = self._step_from_obj(obj)
        if step is None:
            scenario_fallback = _scenario_fallback_step(context_hint, history)
            if scenario_fallback is not None:
                return scenario_fallback
            fallback = self._fallback_step_from_goal(goal)
            if fallback is not None:
                return fallback
            return DoneMarker(
                f"Планировщик вернул некорректный шаг: {obj!r}",
                success=False,
            )
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
    def _fallback_step_from_goal(goal: str) -> Optional[StepSpec]:
        """Build a deterministic keyboard-sequence step for newline domain lists."""
        low = (goal or "").lower()
        wants_enter = "enter" in low or "энтер" in low
        wants_typing = any(
            marker in low
            for marker in (
                "ввод", "введ", "клавиат", "напечат", "набрать",
                "type", "typing", "keyboard",
            )
        )
        keyboard_mode = wants_enter and wants_typing

        domain_re = re.compile(
            r"^\s*(?:https?://)?"
            r"([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
            r"(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+)"
            r"\s*$",
            re.IGNORECASE,
        )
        domains: list[str] = []
        seen: set[str] = set()
        for line in (goal or "").splitlines():
            match = domain_re.match(line)
            if not match:
                continue
            domain = match.group(1).lower()
            if domain in seen:
                continue
            seen.add(domain)
            domains.append(domain)

        if not keyboard_mode or len(domains) < 2:
            browser_terms = (
                "модеус", "modeus", "браузер", "вкладк", "страниц", "сайт",
                "портал", "личный кабинет", "кабинет", "лк", "youtube",
                "ютуб", "google", "chrome",
            )
            web_terms = (
                "найди", "поищи", "погугли", "актуальн", "новост", "погода",
                "цена", "курс", "расписан", "дата", "число",
            )
            system_terms = (
                "открой приложение", "запусти", "файл", "папк", "word", "excel",
                "powerpoint", "громкость", "окно", "нажми", "введи",
            )

            if any(term in low for term in browser_terms):
                agent = AgentType.BROWSER
                expected = "Найдена нужная информация на странице или честно указано, что она не найдена."
            elif any(term in low for term in web_terms):
                agent = AgentType.WEB
                expected = "Найдена актуальная информация из веб-источника или открыта нужная страница."
            elif any(term in low for term in system_terms):
                agent = AgentType.SYSTEM
                expected = "Выполнено действие в системе или возвращена понятная причина невозможности."
            else:
                agent = AgentType.CHAT
                expected = "Дан полезный ответ пользователю."

            return StepSpec(
                agent=agent,
                action_type="subtask",
                target=Target(),
                parameters={"task": (goal or "").strip()},
                expected_outcome=expected,
                requires_verification=agent in (AgentType.SYSTEM, AgentType.BROWSER),
                max_retries=1,
            )

        return StepSpec(
            agent=AgentType.SYSTEM,
            action_type="keyboard_sequence",
            target=Target(),
            parameters={
                "items": domains,
                "submit_key": "{ENTER}",
                "title_re": "",
            },
            expected_outcome=f"Введено {len(domains)} строк, Enter после каждой.",
            requires_verification=False,
            max_retries=0,
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
