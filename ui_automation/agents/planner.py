"""
Planner — один LLM-вызов, превращающий запрос пользователя в Plan из StepSpec.

Ответственность:
  • Выдать Plan с ordered list шагов (граф через depends_on, но пока линейный).
  • НЕ исполнять и НЕ оценивать результат — это делает Controller.
  • Не знать про конкретные tools — только про AgentType.

Нижний уровень действий (action_type = "click" / "type") пока не
генерируется: сохраняем старый формат подзадач («открой Word и напиши…»),
где action_type = "subtask" и parameters["task"] = исходный текст подзадачи.
Это даёт обратную совместимость с ToolAgent.execute(str).
"""
from __future__ import annotations

import json
from typing import Callable, Dict, List, Optional

from ui_automation import utils, llm_config as _llm
from ui_automation.agents.contracts import (
    AgentType, Plan, StepResult, StepSpec, Target,
)

_VALID_AGENTS = {t.value for t in AgentType}

_PROMPT_TEMPLATE = (
    "/no_think\n"
    "Разбей задачу пользователя на минимальный список подзадач.\n"
    "Для каждой подзадачи укажи список агентов из: chat, browser, web, system, vision.\n"
    "Верни ТОЛЬКО валидный JSON-объект вида:\n"
    "{\"tasks\": [{\"task\": \"описание\", \"agents\": [\"тип\"]}, ...]}\n\n"
    "Агенты:\n"
    "  chat    — разговор/знания без поиска\n"
    "  browser — действия в веб-браузере (вкладки, сайты, формы, клики по DOM)\n"
    "  web     — поиск актуальной информации (Tavily, погода)\n"
    "  system  — приложения, файлы, медиа, громкость, окна\n"
    "  vision  — только если пользователь явно просит посмотреть на экран\n\n"
    "РАЗГРАНИЧЕНИЕ browser vs system:\n"
    "  • Упоминание браузера/вкладки/сайта → browser.\n"
    "  • Любое другое приложение (Steam, Word, …) → system.\n"
    "  • «Запусти Chrome» без навигации → system.\n"
    "  • «Открой сайт X», «во вкладке» → browser.\n\n"
    "МИНИМУМ подзадач: простой запрос = одна подзадача. Не добавляй шагов,\n"
    "которых нет в запросе пользователя.\n\n"
    "Задача: {task}{context_block}\n\nJSON:"
)

_BROWSER_MARKERS = (
    "сайт", "вкладк", "url", "http", "браузер",
    "chrome", "firefox", "edge", "в хроме", "в фаерфокс",
)


_NEXT_STEP_PROMPT = """/no_think
Ты — инкрементальный планировщик. Твоя работа — выдать СЛЕДУЮЩИЙ один шаг,
видя цель пользователя, что уже сделано, и текущее состояние экрана/браузера.

Верни СТРОГО один JSON-объект, один из двух форматов:

  {"done": true, "summary": "краткий итог для пользователя"}
  — если цель уже достигнута; summary увидит пользователь.

  {"task": "что конкретно сделать на этом шаге",
   "agent": "system|browser|web|vision|chat",
   "expected": "что должно появиться/измениться на экране после шага"}
  — если нужно ещё действие.

АГЕНТЫ и их инструменты (шаг отдаётся агенту целиком — он сам выберет tool):
  system  — Windows: приложения, окна, файлы, медиа, UIA вне браузера.
    tools: open_app(name), ui_list_windows, ui_focus_window, ui_wait_for_window,
           ui_click_element(text), ui_click(x,y), ui_send_keys(keys),
           ui_list_interactive, ui_get_text, ui_screenshot,
           read_file, write_file, list_directory, delete_file, copy_file, move_file,
           control_volume, control_media.
    ⇒ «открой/запусти приложение X» — это ОДИН шаг к system с task="Открой X".
       НЕ расписывай «нажать Пуск → напечатать имя → Enter» — у system есть open_app.
  browser — веб-браузер: вкладки, DOM, формы, навигация, клики по элементам страницы.
  web     — Tavily-поиск в интернете, погода.
  vision  — посмотреть/прочитать/описать что-то на экране (по скриншоту).
  chat    — разговор/знания без инструментов.

ПРАВИЛА:
- Один шаг = одна высокоуровневая задача для агента (агент сам разобьёт её
  на вызовы tools). Не микроменеджь клики, если есть профильный tool.
- task — чистое описание действия, БЕЗ метаданных контекста (не включай
  «[Открытые окна]», списки окон и т.п. — агент увидит своё восприятие сам).
- СНАЧАЛА посмотри [foreground] и дерево элементов в восприятии. Шаг должен
  ИСХОДИТЬ из того, что реально видно, а НЕ из плана в голове.
- Если foreground не то окно, которое нужно — первый шаг это переключение
  фокуса (ui_focus_window / клик по задаче в панели).
- Если видно приветственное/стартовое окно приложения — СНАЧАЛА приведи его
  в рабочее состояние (создать новый документ, закрыть welcome-экран),
  и только потом вводи данные.
- Если последний шаг в истории со статусом failure — СЛЕДУЮЩИЙ шаг обязан
  его исправлять (причина в reason верификатора), а не продолжать план.
- Если из восприятия видно, что цель достигнута — верни done.
- НЕ повторяй только что выполненный шаг буквально.
- expected — конкретно и проверяемо (название окна, текст кнопки/пункта меню).

Цель пользователя:
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


class Planner:
    """LLM-планировщик. Stateless."""

    def __init__(self, llm_call: Optional[Callable[[str], str]] = None) -> None:
        # llm_call можно подменить в тестах; по умолчанию — реальный клиент.
        self._llm_call = llm_call or self._default_llm_call

    # ── Public ────────────────────────────────────────────────────────────────

    def plan(self, request: str, context_hint: str = "",
             budget_steps: int = 20) -> Plan:
        """
        Строит Plan. На любой ошибке падает в single-step fallback
        (agent выбирается грубой эвристикой).
        """
        context_block = f"\n\nКонтекст:\n{context_hint}" if context_hint else ""
        prompt = _PROMPT_TEMPLATE.format(task=request, context_block=context_block)

        raw = ""
        try:
            raw = self._llm_call(prompt)
        except Exception as e:
            utils.print_with_color(f"[Planner] LLM error: {e}", "yellow")

        steps: List[StepSpec] = []
        if raw:
            try:
                items = self._extract_tasks(raw)
                steps = self._items_to_steps(items)
            except Exception as e:
                utils.print_with_color(
                    f"[Planner] parse error: {e} | raw={raw}", "yellow"
                )

        if not steps:
            steps = [self._fallback_step(request)]

        return Plan(
            user_request=request,
            steps=steps,
            budget_steps=budget_steps,
            notes="" if raw else "LLM unavailable; fallback single-step plan",
        )

    # ── Incremental (ReAct) ───────────────────────────────────────────────────

    def next_step(
        self,
        goal: str,
        history: List[StepSpec],
        results: List[StepResult],
        perception: str,
    ) -> "StepSpec | DoneMarker":
        """Выдаёт СЛЕДУЮЩИЙ шаг на основе цели, истории и актуального восприятия."""
        history_block = self._format_history(history, results)
        prompt = (
            _NEXT_STEP_PROMPT
            .replace("{goal}", goal)
            .replace("{history_len}", str(len(history)))
            .replace("{history}", history_block or "(пусто)")
            .replace("{perception}", perception or "(нет данных)")
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
        except Exception as e:
            utils.print_with_color(
                f"[Planner.next_step] parse error: {e} | raw={raw}", "yellow"
            )
            return DoneMarker("Не удалось разобрать ответ планировщика.")

        if obj.get("done") is True:
            return DoneMarker(str(obj.get("summary", "")).strip() or "Готово.")

        task_text = str(obj.get("task", "")).strip()
        agent_str = str(obj.get("agent", "")).strip().lower()
        expected = str(obj.get("expected", "")).strip() or task_text

        if not task_text or agent_str not in _VALID_AGENTS:
            return DoneMarker(f"Планировщик вернул некорректный шаг: {obj!r}")

        agent_str = self._normalize_agents(task_text, [agent_str])[0]
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

    # ── Internal ──────────────────────────────────────────────────────────────

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

    @staticmethod
    def _extract_tasks(raw: str) -> List[Dict]:
        o_s, o_e = raw.find("{"), raw.rfind("}") + 1
        if o_s != -1 and o_e > o_s:
            try:
                obj = json.loads(raw[o_s:o_e])
                if isinstance(obj, dict):
                    if isinstance(obj.get("tasks"), list):
                        return obj["tasks"]
                    if "task" in obj:
                        return [obj]
            except Exception:
                pass
        a_s, a_e = raw.find("["), raw.rfind("]") + 1
        if a_s != -1 and a_e > a_s:
            arr = json.loads(raw[a_s:a_e])
            if isinstance(arr, list):
                return arr
        raise ValueError("no tasks in payload")

    @classmethod
    def _items_to_steps(cls, items: List[Dict]) -> List[StepSpec]:
        """TaskItem-подобный dict → один или несколько StepSpec.

        Если у подзадачи несколько агентов (например ["system", "browser"]),
        превращаем их в цепочку шагов с depends_on.
        """
        steps: List[StepSpec] = []
        for item in items:
            task_text = (item.get("task") or "").strip()
            if not task_text:
                continue
            raw_agents = item.get("agents") or []
            agents = [a for a in raw_agents if a in _VALID_AGENTS] or ["chat"]
            agents = cls._normalize_agents(task_text, agents)

            prev_id: Optional[str] = None
            for agent in agents:
                step = StepSpec(
                    agent=AgentType(agent),
                    action_type="subtask",
                    target=Target(),
                    parameters={"task": task_text},
                    expected_outcome=task_text,
                    requires_verification=False,  # stage 3 включит для UI-агентов
                    depends_on=[prev_id] if prev_id else [],
                )
                steps.append(step)
                prev_id = step.step_id
        return steps

    @staticmethod
    def _normalize_agents(task_text: str, agents: List[str]) -> List[str]:
        """Исправляет типичные ошибки LLM-классификации browser↔system."""
        tl = task_text.lower()
        has_browser_marker = any(k in tl for k in _BROWSER_MARKERS)
        if "browser" in agents and not has_browser_marker:
            agents = ["system" if a == "browser" else a for a in agents]
        if "system" in agents and has_browser_marker:
            agents = ["browser" if a == "system" else a for a in agents]
        return agents

    @staticmethod
    def _fallback_step(request: str) -> StepSpec:
        """Одно-шаговый план на случай недоступности LLM."""
        t = request.lower()
        if any(k in t for k in _BROWSER_MARKERS):
            agent = AgentType.BROWSER
        elif any(v in t.split()[:1] for v in (
            "открой", "запусти", "закрой", "сверни", "нажми",
            "кликни", "введи", "напиши", "сохрани", "удали",
        )):
            agent = AgentType.SYSTEM
        else:
            agent = AgentType.CHAT
        return StepSpec(
            agent=agent,
            action_type="subtask",
            parameters={"task": request},
            expected_outcome=request,
            requires_verification=False,
        )


__all__ = ["Planner"]
