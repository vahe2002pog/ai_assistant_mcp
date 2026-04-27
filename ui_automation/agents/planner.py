"""
Planner — инкрементальный планировщик (ReAct).

Ответственность:
  • По (goal, history, perception) выдать СЛЕДУЮЩИЙ один шаг (StepSpec)
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

АГЕНТЫ (шаг отдаётся агенту целиком — он сам выберет tool):
  system  — Windows: приложения, окна, файлы, медиа, UIA вне браузера,
            + MS Office (Excel/Word/PowerPoint/Outlook) через COM.
    tools: open_app(name), ui_list_windows, ui_focus_window, ui_wait_for_window,
           ui_click_element(text), ui_click(x,y), ui_send_keys(keys),
           ui_list_interactive, ui_get_text, ui_screenshot,
           read_file, write_file, list_directory, delete_file, copy_file, move_file,
           control_volume, control_media,
           office_launch/quit/visible, excel_*, word_*, ppt_*, outlook_*,
           office_run_python (универсальный COM-exec).
    ⇒ «открой/запусти приложение X» — ОДИН шаг к system с task="Открой X".
       НЕ расписывай «Пуск → имя → Enter» — у system есть open_app.
    ⇒ ЗАДАЧИ С MS OFFICE-ФАЙЛАМИ (Excel/Word/PowerPoint/Outlook) решаются
       ЧЕРЕЗ COM, а НЕ через UI-автоматизацию. Это значит:
         • НЕ нужен open_app + ui_focus_window + клики/send_keys для
           редактирования документа — COM-тул делает всё напрямую.
         • «Запиши 42 в A1 файла report.xlsx» — ОДИН шаг к system
           (task="Запиши 42 в A1 report.xlsx"). Не разбивай на
           «открой Excel» → «кликни A1» → «введи 42».
         • «Отправь письмо X с темой Y» — ОДИН шаг к system (outlook_send_mail).
         • «Прочитай лист 'Итоги' из foo.xlsx» — ОДИН шаг (excel_read_sheet).
         • Только если пользователь ЯВНО требует открытое окно приложения
           («открой Excel», «покажи файл»), тогда первый шаг — open_app.
  browser — веб-браузер: вкладки, DOM, формы, навигация, клики по элементам страницы.
  web     — Tavily-поиск в интернете и погода. ИСПОЛЬЗУЙ всегда, когда:
            • нужна актуальная информация (новости, цены, курсы, расписания,
              события, статистика, погода);
            • пользователь просит «найди», «погугли», «поищи», «что известно про»;
            • знаниевый вопрос, в котором ответ может зависеть от года/даты
              или ты не уверен в актуальности своих знаний.
            web ВСЕГДА предпочтительнее chat, если факт требует проверки.
  vision  — посмотреть/прочитать/описать что-то на экране (по скриншоту).
  chat    — ответ выводится прямо в окно чата ассистента (это и есть «чат»:
            текстовый интерфейс, где пользователь переписывается с тобой).
            Используй chat ТОЛЬКО для: чистого разговора (приветствие, мнение,
            эмоции), тривиальных знаниевых вопросов, не зависящих от
            времени (определения, общеизвестные факты, простая математика).
            ВАЖНО: фразы «ответь в чат», «напиши в чате», «сообщи в чат»,
            «скажи здесь», «выведи сюда» НЕ означают «использовать chat-агент».
            Это лишь указание, КУДА вывести ответ — а ответ всё равно виден
            в чате независимо от агента (web/system/vision тоже показываются).
            Сначала смотри, нужны ли актуальные данные:
              • Если ответ требует фактов (адреса, цены, расписание,
                новости, события, контакты, конкретные места) — web.
              • Если факт может зависеть от года/даты или ты не уверен в
                актуальности своих знаний — web.
              • Если есть хоть малейшее сомнение — web, не chat.
            chat НЕ имеет доступа к интернету и НЕ должен «обещать поискать
            позже» — если для ответа нужен поиск, сразу выбирай web.

ПРАВИЛА:
- Один шаг = ОДНО атомарное действие агента + ОДНА проверка результата.
  Агент внутри шага имеет жёсткий лимит tool_call'ов (~6), поэтому крупные
  составные задачи он не потянет — ты обязан разбивать их здесь.
- ЗАПРЕЩЕНО склеивать действия в одном task. Плохо: «открой Excel и введи
  данные в A1». Хорошо: сначала {"task":"Открой Excel",...}, на следующей
  итерации, увидев открытое окно, — {"task":"Введи '42' в ячейку A1",...}.
- Глаголы-индикаторы составного шага (должны быть разделены): «и», «затем»,
  «после чего», «а потом», «заполни форму» (это N отдельных шагов),
  «настрой X» (это серия шагов). Одно предложение = одно действие.
- task — чистое описание действия, БЕЗ метаданных контекста.
- СНАЧАЛА смотри [foreground] и дерево элементов в восприятии. Шаг должен
  ИСХОДИТЬ из того, что реально видно.
- Если foreground не то окно — первый шаг это переключение фокуса.
- Если виден welcome/стартовый экран приложения — СНАЧАЛА приведи его
  в рабочее состояние, и только потом вводи данные.
- Если последний шаг в истории со статусом failure — СЛЕДУЮЩИЙ шаг обязан
  его исправлять (причина в reason верификатора).
- Если из восприятия видно, что цель достигнута — верни done.
- НЕ повторяй только что выполненный шаг буквально.
- expected — конкретно и проверяемо (название окна, текст кнопки/пункта меню).
- ПОСЛЕ шага chat, который уже содержит ответ пользователю, СРАЗУ верни
  {"done": true, ...}. Не добавляй шаги «ждать ввода», «уточнить»,
  «повторить ответ» — chat-ответ уже виден пользователю в чате.
  Если нужно задать уточняющий вопрос — это и есть единственный chat-шаг,
  после которого DONE; ответ пользователя придёт в следующем запросе.
- ОДИН chat-шаг = ОДИН полный ответ. Не разбивай ответ на несколько chat-шагов.

История диалога (предыдущие сообщения в этом чате):
{chat_history}

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
    ) -> "StepSpec | DoneMarker":
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

        task_text = str(obj.get("task", "")).strip()
        agent_str = str(obj.get("agent", "")).strip().lower()
        expected = str(obj.get("expected", "")).strip() or task_text

        if not task_text or agent_str not in _VALID_AGENTS:
            return DoneMarker(f"Планировщик вернул некорректный шаг: {obj!r}")

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


__all__ = ["Planner", "DoneMarker"]
