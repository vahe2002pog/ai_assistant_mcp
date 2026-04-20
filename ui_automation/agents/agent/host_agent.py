from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List

import openai

from ui_automation import utils, llm_config as _llm
from ui_automation.agents.memory.blackboard import Blackboard
from ui_automation.agents.contracts import (
    AgentType, ErrorClass, ErrorInfo, ExecutionTrace, Plan,
    StepResult, StepSpec, StepStatus,
)
from ui_automation.agents.controller import Controller, Worker
from ui_automation.agents.planner import Planner
from ui_automation.agents.verifier import Verifier
from ui_automation.agents.trace_store import TraceStore

# ── Keyword routing tables ────────────────────────────────────────────────────
_BROWSER_KW = (
    "browser", "вкладк", "сайт", "страниц", "http", "www.",
    "вк.com", "vk.com",
    "кликни на сайт", "нажми на сайт", "прокрути страниц",
    "перейди на сайт", "открой сайт", "закрой вкладк",
    "в браузере", "в хроме", "в firefox", "в edge",
)
_WEB_KW = (
    "найди информацию", "поищи", "погода", "курс валют", "курс доллар",
    "новости", "что такое", "кто такой", "когда был", "сколько стоит",
    "wikipedia", "вики", "актуальн", "последние данные",
)
_SYSTEM_KW = (
    "запусти приложение", "открой приложение", "закрой приложение",
    "проводник", "блокнот", "калькулятор", "диспетчер задач",
    "громкость", "звук", "медиа", "пауза", "следующий трек",
    "управляй окном", "свернуть окно", "развернуть окно",
    "список окон", "окна", "процесс",
    "steam", "discord", "telegram", "spotify", "vlc", "notepad",
    "в библиотеке", "в приложении", "в программе",
)
# Только явные запросы на просмотр экрана — vision используется в крайнем случае
_VISION_KW = (
    "что на экране", "прочитай экран", "прочитай текст с экрана",
    "скопируй с экрана", "посмотри на экран",
    "распознай текст", "скриншот экрана",
    "что изображено на экране", "текст на экране",
)

# Разговорные/знаниевые маркеры — отдаём ChatAgent, без поиска и инструментов.
_CHAT_KW = (
    "привет", "здорово", "здравствуй", "hi", "hello",
    "как дела", "как ты", "как настроение",
    "спасибо", "благодарю", "пока", "до свидания",
    "расскажи анекдот", "расскажи шутку", "пошути",
    "кто ты", "что ты умеешь", "как тебя зовут", "что ты такое",
    "как ты думаешь", "что ты думаешь", "твоё мнение", "твое мнение",
    "объясни простыми словами", "в чём смысл", "в чем смысл",
)

_VALID_AGENTS = {"browser", "web", "system", "vision", "chat"}

# Web-инструменты HostAgent использует сам, без отдельного подагента.
_WEB_TOOLS_MODULES = ["mcp_modules.tools_web", "mcp_modules.tools_weather"]
_WEB_SYSTEM_PROMPT = """/no_think
Ты — веб-агент. Ищешь информацию в интернете и проверяешь погоду.

Инструменты:
- tavily_search(query, search_depth="basic") — быстрый поиск, сниппеты + ссылки.
- tavily_extract — полный текст страницы (только если сниппетов не хватает).
- get_weather — погода для города.

ПРАВИЛА:
1. tavily_search(search_depth="basic") — дефолт (1–2 сек). advanced — ТОЛЬКО если basic не помог.
2. Не вызывай open_url/browser_search для поиска — только если пользователь явно попросил открыть ссылку.
3. После ответа — task_done(summary="краткий ответ пользователю").
"""

_CHAT_SYSTEM_PROMPT = (
    "/no_think\n"
    "Ты — Компас, умный персональный ассистент на русском языке. "
    "Отвечай кратко, по делу и дружелюбно. Если знаешь ответ на знаниевый вопрос — "
    "отвечай сразу. Если вопрос требует актуальных данных (новости, цены, курсы, погода) "
    "или ты не уверен — честно скажи и предложи поискать. Не выдумывай факты."
)


@dataclass
class TaskItem:
    """Единица работы в стеке задач HostAgent."""
    task: str
    agents: List[str] = field(default_factory=lambda: ["chat"])

    def __repr__(self) -> str:
        return f"TaskItem(agents={self.agents}, task={self.task})"


class HostAgent:
    """
    Оркестратор: принимает запрос, декомпозирует на подзадачи, сам отвечает
    на разговорные/знаниевые вопросы и ищет в интернете, а для управления
    системой/браузером/экраном делегирует ToolAgent / BrowserAgent / VisionAgent.
    """

    def __init__(self, name: str = "HostAgent") -> None:
        self._name = name
        # Отдельный Blackboard на каждый чат — чтобы параллельные запросы
        # из разных разговоров не засоряли друг другу «историю задач».
        self._blackboards: Dict[Any, Blackboard] = {}
        self._bb_lock = threading.Lock()
        self._default_bb = Blackboard()
        self.appagent_dict: Dict = {}
        self._active_appagent = None
        self._trace_store = TraceStore()

    def _get_blackboard(self, conv_id: Any = None) -> Blackboard:
        if conv_id is None:
            return self._default_bb
        with self._bb_lock:
            bb = self._blackboards.get(conv_id)
            if bb is None:
                bb = Blackboard()
                self._blackboards[conv_id] = bb
            return bb

    @property
    def name(self) -> str:
        return self._name

    # ── Sub-agent factory ─────────────────────────────────────────────────────

    def _make_agent(self, agent_type: str):
        """Делегируемые подагенты: browser, vision, system (базовый ToolAgent).
        chat/web обрабатываются самим HostAgent без sub-agent'а."""
        from ui_automation.agents.agent.tool_agent import ToolAgent
        if agent_type == "browser":
            from ui_automation.agents.agent.browser_agent import BrowserAgent
            return BrowserAgent("BrowserAgent", host=self)
        if agent_type == "vision":
            from ui_automation.agents.agent.vision_agent import VisionAgent
            return VisionAgent("VisionAgent", host=self)
        if agent_type == "web":
            return ToolAgent("WebAgent", host=self,
                             tools_modules=_WEB_TOOLS_MODULES,
                             system_prompt=_WEB_SYSTEM_PROMPT)
        # system и всё остальное — базовый ToolAgent
        return ToolAgent("SystemAgent", host=self)

    # ── Inline chat (без инструментов) ───────────────────────────────────────

    def _run_chat(self, task: str) -> str:
        """Простой LLM-ответ без инструментов. Вызывается напрямую из dispatch."""
        print(f"\n[ChatAgent] {task.split(chr(10))[0]}", flush=True)
        messages = [
            {"role": "system", "content": _CHAT_SYSTEM_PROMPT},
            {"role": "user",   "content": task},
        ]
        for extra in (_llm.get_extra_body(), {}):
            try:
                kwargs = dict(
                    model=_llm.get_model(),
                    messages=messages,
                    temperature=0.7,
                    max_tokens=1024,
                )
                if extra:
                    kwargs["extra_body"] = extra
                resp = _llm.get_client().chat.completions.create(**kwargs)
                msg = resp.choices[0].message
                text = (msg.content or "").strip()
                if text:
                    return text
                reasoning = getattr(msg, "reasoning_content", None) or ""
                if reasoning.strip():
                    return reasoning.strip()
            except openai.APIConnectionError:
                return "Ошибка: нет соединения с моделью."
            except Exception as e:
                print(f"[ChatAgent] ошибка: {e}", flush=True)
                if not extra:
                    return f"Ошибка LLM: {e}"
        return "Не удалось получить ответ от модели."

    # ── Task planning & classification ───────────────────────────────────────

    def _plan_llm_call(self, prompt: str) -> str:
        """LLM-вызов для планирования с ретраями: json_format → без него, с extra_body → без."""
        messages = [{"role": "user", "content": prompt}]
        attempts = [
            {"response_format": {"type": "json_object"}, "extra": True},
            {"response_format": None,                    "extra": True},
            {"response_format": None,                    "extra": False},
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
                raw = (getattr(m, "content", None) or "").strip() \
                      or (getattr(m, "reasoning_content", None) or "").strip()
                if raw:
                    return raw
            except Exception as e:
                utils.print_with_color(f"[HostAgent] plan_tasks attempt error: {e}", "yellow")
        return ""

    @staticmethod
    def _extract_tasks(raw: str) -> List[Dict]:
        """Извлекает список подзадач из ответа LLM (объект {tasks:[...]} или голый массив)."""
        a_start, a_end = raw.find("["), raw.rfind("]") + 1
        o_start, o_end = raw.find("{"), raw.rfind("}") + 1
        if o_start != -1 and o_end > o_start:
            try:
                obj = json.loads(raw[o_start:o_end])
                if isinstance(obj, dict):
                    if isinstance(obj.get("tasks"), list):
                        return obj["tasks"]
                    if "task" in obj:
                        return [obj]
            except Exception:
                pass
        if a_start != -1 and a_end > a_start:
            arr = json.loads(raw[a_start:a_end])
            if isinstance(arr, list):
                return arr
        raise ValueError("no tasks in payload")

    def _plan_tasks(self, task: str, context_hint: str = "") -> List[TaskItem]:
        """
        Use LLM to decompose task into an ordered list of TaskItems.
        Falls back to single-item list on any failure.
        """
        context_block = f"\n\nКонтекст:\n{context_hint}" if context_hint else ""
        prompt = (
            "/no_think\n"
            "Разбей задачу пользователя на минимальный список подзадач.\n"
            "Для каждой подзадачи укажи список агентов из: chat, browser, web, system, vision.\n"
            "Верни ТОЛЬКО валидный JSON-объект вида:\n"
            "{\"tasks\": [{\"task\": \"описание\", \"agents\": [\"тип\"]}, ...]}\n\n"
            "Агенты:\n"
            "  chat    — просто поговорить или ответить на знаниевый вопрос без поиска\n"
            "            (приветствия, мнение, объяснения, общие факты)\n"
            "  browser — ВСЁ что происходит внутри веб-браузера: вкладки, URL-навигация,\n"
            "            ввод текста в поля на сайте, клики по элементам страницы, формы,\n"
            "            прокрутка, чтение содержимого страницы. Если действие идёт\n"
            "            «в браузере», «во вкладке», «на сайте», «в Chrome/Firefox/Edge» —\n"
            "            это browser, а НЕ system.\n"
            "  web     — поиск АКТУАЛЬНОЙ информации в интернете (новости, цены, погода)\n"
            "  system  — действия в обычных приложениях (НЕ браузере): запуск, фокус, клики,\n"
            "            ввод текста, любые действия внутри Steam, Discord, Telegram,\n"
            "            проводника, блокнота, калькулятора и т.д., файлы, звук\n"
            "  vision  — ТОЛЬКО если пользователь явно просит посмотреть/прочитать/распознать что-то на экране\n\n"
            "РАЗГРАНИЧЕНИЕ browser vs system:\n"
            "  • Упоминание браузера/вкладки/сайта → browser.\n"
            "  • Упоминание другого приложения (Steam, Word, ...) → system.\n"
            "  • «Запусти Chrome» (именно процесс, без навигации) → system.\n"
            "  • «Открой сайт X», «перейди на X», «напиши во вкладке» → browser.\n\n"
            "ГЛАВНОЕ ПРАВИЛО ДЕКОМПОЗИЦИИ — минимум подзадач:\n"
            "  • Простой запрос = ОДНА подзадача. Не дроби без необходимости.\n"
            "  • «открой X», «запусти X» → ОДНА подзадача «открыть X» (агент system сам всё сделает).\n"
            "  • НЕ добавляй служебных шагов «найти в базе», «проверить открыто ли», «подготовить» —\n"
            "    sub-agent делает это сам внутри одной подзадачи.\n"
            "  • НЕ добавляй действий, которых НЕТ в запросе пользователя (не создавай документ,\n"
            "    не сохраняй файл, не вводи текст, если об этом не просили).\n"
            "  • Дроби на несколько подзадач ТОЛЬКО если пользователь явно указал несколько\n"
            "    разных действий («открой Word и напиши текст» = 2 подзадачи).\n\n"
            f"Задача: {task}{context_block}\n\nJSON:"
        )
        raw = self._plan_llm_call(prompt)
        if raw:
            try:
                items_raw = self._extract_tasks(raw)
                stack: List[TaskItem] = []
                for item in items_raw:
                    t = (item.get("task") or "").strip()
                    if not t:
                        continue
                    raw_agents = item.get("agents", [])
                    agents = [a for a in raw_agents if a in _VALID_AGENTS] or ["chat"]
                    tl = t.lower()
                    _BROWSER_MARKERS = ("сайт", "вкладк", "url", "http", "браузер", "chrome", "firefox", "edge", "в хроме", "в фаерфокс")
                    has_browser_marker = any(k in tl for k in _BROWSER_MARKERS)
                    if "browser" in agents and not has_browser_marker:
                        agents = ["system" if a == "browser" else a for a in agents]
                    # Обратная нормализация: действие явно во вкладке/браузере, но LLM
                    # отнёс его к system — исправляем на browser (у него DOM-инструменты,
                    # Win32 SendInput в веб-поля доставляется ненадёжно).
                    if "system" in agents and has_browser_marker:
                        agents = ["browser" if a == "system" else a for a in agents]
                    stack.append(TaskItem(task=t, agents=agents))
                if stack:
                    return stack
            except Exception as e:
                utils.print_with_color(f"[HostAgent] plan_tasks parse error: {e} | raw={raw}", "yellow")

        return [TaskItem(task=task, agents=[self.classify_task(task)])]

    def classify_task(self, task: str) -> str:
        """
        Classify a task as 'chat', 'browser', 'web', 'system', or 'vision'.

        Strategy:
          1. Keyword scan (fast, deterministic)
          2. LLM fallback for ambiguous cases
        """
        t = task.lower()

        if any(k in t for k in _CHAT_KW):
            return "chat"

        if any(k in t for k in _BROWSER_KW):
            return "browser"

        if any(k in t for k in _WEB_KW):
            return "web"

        if any(k in t for k in _SYSTEM_KW):
            return "system"

        # Vision — только при явном запросе на просмотр экрана
        if any(k in t for k in _VISION_KW):
            return "vision"

        # Императивный глагол действия → system (работа с приложением/ОС).
        _ACTION_VERBS = (
            "создай", "нажми", "кликни", "введи", "напиши", "вставь",
            "закрой", "заверши", "выйди", "сверни", "разверни", "переключись",
            "сохрани", "удали", "скопируй", "вырежи", "перемести", "переименуй",
            "запусти", "останови", "включи", "выключи", "открой",
        )
        first = t.split()[0] if t.split() else ""
        if first in _ACTION_VERBS:
            return "system"

        # Нет глагола-действия — это знаниевый/разговорный запрос.
        # LLM-классификатор решит: chat (если уверен в ответе) или web
        # (если нужны актуальные данные).
        return self._llm_classify(task)

    def _llm_classify(self, task: str) -> str:
        """Single LLM call to classify task type."""
        prompt = (
            "/no_think\n"
            "Определи тип задачи — ответь ТОЛЬКО одним словом без пояснений:\n"
            "  chat    — разговор, приветствие, мнение, знаниевый вопрос на который\n"
            "            можно уверенно ответить БЕЗ актуальных данных\n"
            "            (общие факты, определения, объяснения, история)\n"
            "  web     — нужны АКТУАЛЬНЫЕ данные: новости, цены, курсы, погода,\n"
            "            расписания, события, статистика за последнее время\n"
            "  browser — ТОЛЬКО веб-браузер (Chrome/Firefox/Edge): вкладки, URL-навигация\n"
            "  system  — ВСЁ что связано с приложениями: любые действия в любом приложении\n"
            "  vision  — ТОЛЬКО если пользователь явно просит посмотреть/прочитать/распознать что-то на экране\n\n"
            "АБСОЛЮТНОЕ ПРАВИЛО: любая работа с приложениями — ТОЛЬКО system, никогда browser.\n"
            "Если вопрос знаниевый и ответ не зависит от «сегодня/сейчас» — это chat, не web.\n\n"
            f"Задача: {task}\n\nОтвет:"
        )
        try:
            resp = _llm.get_client().chat.completions.create(
                model=_llm.get_model(),
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=5,
                extra_body=_llm.get_extra_body(),
            )
            answer = resp.choices[0].message.content.strip().lower()
            for candidate in ("chat", "browser", "web", "system", "vision"):
                if candidate in answer:
                    return candidate
        except Exception as e:
            utils.print_with_color(f"[HostAgent] classify LLM error: {e}", "yellow")
        # Безопасный фоллбек: разговор без инструментов лучше, чем отказ.
        return "chat"

    # ── Blackboard context ────────────────────────────────────────────────────

    def _blackboard_context(self, conv_id: Any = None) -> str:
        """Returns a summary of the last 3 tasks from Blackboard, or empty string."""
        try:
            items = self._get_blackboard(conv_id).requests.content
            if not items:
                return ""
            lines = []
            for item in items[-3:]:
                d = item.to_dict()
                task_text = d.get("task", "")
                result_text = d.get("result", "")
                agent_text = d.get("agent", "")
                if task_text:
                    lines.append(
                        f"• [{agent_text}] {task_text}"
                        + (f" → {result_text}" if result_text else "")
                    )
            return "\n".join(lines)
        except Exception:
            return ""

    # ── Main entry point ──────────────────────────────────────────────────────

    def dispatch(self, task: str, context_hint: str = "", conv_id: Any = None) -> str:
        """
        Main entry point for chat-mode requests.

        1. Injects Blackboard cross-turn context.
        2. Calls _plan_tasks() to decompose the request into an ordered stack.
        3. Executes each TaskItem sequentially; items with multiple agents run
           them one after another, passing the previous result as context.
        4. Returns combined summary.
        """
        bb_ctx = self._blackboard_context(conv_id)
        if bb_ctx:
            context_hint = f"[История предыдущих задач]\n{bb_ctx}\n\n" + context_hint

        if context_hint:
            utils.print_with_color(context_hint + ("…" if len(context_hint) > 800 else ""), "cyan")

        # Инкрементальный ReAct: Planner генерирует шаги по ходу выполнения,
        # смотря на реальное состояние экрана/браузера после каждого шага.
        from ui_automation import cancel as _cancel
        from ui_automation.agents.perceiver import Perceiver

        planner = Planner()
        perceiver = Perceiver()
        workers = self._build_workers(context_hint)
        controller = Controller(
            workers=workers,
            on_step_done=lambda r, t: self._record_blackboard(conv_id, r, t),
            is_cancelled=_cancel.is_cancelled,
            verifier=Verifier(),
        )
        trace: ExecutionTrace = controller.execute(
            goal=task,
            planner=planner,
            perceiver=perceiver,
            context_hint=context_hint,
        )

        # Персистим трассу — для реплея и метрик.
        try:
            self._trace_store.save(trace)
        except Exception as e:
            utils.print_with_color(f"[HostAgent] trace save failed: {e}", "yellow")

        final = trace.final_summary or ""
        utils.print_with_color(f"\n[HostAgent] Готово ({trace.final_status.value}):\n{final}", "green")

        # Опыт в RAG — фоном, не блокируем ответ.
        agent_types = [s.agent.value for s in trace.plan.steps]
        _save_experience_async(task, final, agent_types)

        # Если задача состояла только из chat-агентов — отдаём ответ как есть,
        # без форматтера (он превратит дружелюбный текст в казённый «voice»).
        only_chat = bool(trace.plan.steps) and all(
            s.agent == AgentType.CHAT for s in trace.plan.steps
        )
        if only_chat:
            from ui_automation.agents.agent.response_formatter import AssistantResponse
            response = AssistantResponse(voice=final)
            utils.print_with_color(f"[HostAgent] voice: {response.voice}", "cyan")
            return response

        from ui_automation.agents.agent.response_formatter import ResponseFormatter
        response = ResponseFormatter().format(final, user_query=task)
        utils.print_with_color(f"[HostAgent] voice: {response.voice}", "cyan")
        return response

    # ── Worker adapters (legacy sub-agents → Controller.Worker) ───────────────

    def _build_workers(self, context_hint: str) -> Dict[AgentType, Worker]:
        """Оборачивает существующие sub-agents (str→str) в Worker (StepSpec→StepResult)."""
        def _subtask_text(step: StepSpec, prev: str) -> str:
            text = step.parameters.get("task") or step.free_text or step.expected_outcome
            if prev:
                text = f"{text}{prev}"
            if context_hint:
                text = f"{text}\n\n{context_hint}"
            return text

        def _classify_error(msg: str) -> ErrorClass:
            low = msg.lower()
            if any(k in low for k in ("timeout", "connection", "connectionreset", "temporarily")):
                return ErrorClass.TRANSIENT
            if any(k in low for k in ("not found", "не найден", "missing")):
                return ErrorClass.SEMANTIC
            return ErrorClass.UNKNOWN

        def _legacy_worker(agent_type_str: str) -> Worker:
            def run(step: StepSpec, prev: str) -> StepResult:
                import time as _t
                started = _t.time()
                subtask = _subtask_text(step, prev)
                try:
                    if agent_type_str == "chat":
                        out = self._run_chat(subtask)
                    else:
                        sub = self._make_agent(agent_type_str)
                        self.appagent_dict[sub.name] = sub
                        self._active_appagent = sub
                        out = sub.execute(subtask)
                    return StepResult(
                        step_id=step.step_id,
                        status=StepStatus.SUCCESS,
                        summary=str(out or "").strip(),
                        started_at=started,
                        finished_at=_t.time(),
                    )
                except Exception as e:
                    msg = str(e)
                    return StepResult(
                        step_id=step.step_id,
                        status=StepStatus.FAILURE,
                        error=ErrorInfo(
                            error_class=_classify_error(msg),
                            message=msg,
                        ),
                        started_at=started,
                        finished_at=_t.time(),
                    )
            return run

        return {
            AgentType.CHAT:    _legacy_worker("chat"),
            AgentType.WEB:     _legacy_worker("web"),
            AgentType.SYSTEM:  _legacy_worker("system"),
            AgentType.BROWSER: _legacy_worker("browser"),
            AgentType.VISION:  _legacy_worker("vision"),
        }

    def _record_blackboard(self, conv_id: Any, r: StepResult, trace: ExecutionTrace) -> None:
        step = next((s for s in trace.plan.steps if s.step_id == r.step_id), None)
        if step is None:
            return
        self._get_blackboard(conv_id).add_requests({
            "task": (step.parameters.get("task") or step.expected_outcome or ""),
            "agent": step.agent.value,
            "result": (r.summary or ""),
        })


def _save_experience_async(task: str, result: str, agent_types: list) -> None:
    """Сохраняет опыт выполнения задачи в RAG vectordb/experience (фоновый поток)."""
    def _worker():
        try:
            from ui_automation.rag.experience_manager import save_experience
            save_experience(task, result, agent_types)
        except Exception as e:
            utils.print_with_color(f"[RAG] Не удалось сохранить опыт: {e}", "yellow")

    threading.Thread(target=_worker, daemon=True).start()
