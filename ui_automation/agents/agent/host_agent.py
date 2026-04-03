from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List

import openai

from ui_automation import utils
from ui_automation.agents.memory.blackboard import Blackboard

# ── LLM config (shared with tool_agent.py) ───────────────────────────────────
_API_BASE  = os.environ.get("API_BASE",  "http://localhost:8000/v1")
_API_KEY   = os.environ.get("API_KEY",   "llama")
_API_MODEL = os.environ.get("API_MODEL", "Qwen3.5-9B-abliterated-vision-Q4_K_M")
_NO_THINK  = {"chat_template_kwargs": {"enable_thinking": False}}

# ── Keyword routing tables ────────────────────────────────────────────────────
_BROWSER_KW = (
    "browser", "вкладк", "сайт", "страниц", "http", "www.",
    "twitch", "youtube", "ютуб", "telegram", "вк.com", "vk.com",
    "кликни на сайт", "нажми на сайт", "прокрути страниц",
    "перейди на", "открой сайт", "закрой вкладк",
)
_WEB_KW = (
    "найди информацию", "поищи", "погода", "курс валют", "курс доллар",
    "новости", "что такое", "кто такой", "когда был", "сколько стоит",
    "wikipedia", "вики", "актуальн", "последние данные",
)
_OFFICE_KW = (
    "excel", "word", "powerpoint", ".docx", ".xlsx", ".pptx", ".doc", ".xls",
    "ячейк", "формул", "лист excel", "книг excel", "таблиц в excel",
    "документ word", "абзац", "найди в документ", "заголовок документ",
    "прочитай excel", "запиши в excel", "прочитай word", "запиши в word",
    "открой excel", "открой word", "открой powerpoint",
)
_SYSTEM_KW = (
    "запусти приложение", "открой приложение", "закрой приложение",
    "проводник", "блокнот", "калькулятор", "диспетчер задач",
    "громкость", "звук", "медиа", "пауза", "следующий трек",
    "управляй окном", "свернуть окно", "развернуть окно",
    "список окон", "окна", "процесс",
)
_VISION_KW = (
    "что на экране", "что написано", "прочитай экран", "прочитай текст с экрана",
    "скопируй текст", "скопируй с экрана", "что за окно", "что открыто",
    "найди текст на экране", "определи текст", "распознай текст",
    "скриншот", "что изображено", "что на картинке", "посмотри на экран",
    "что в правом", "что в левом", "что в верхнем", "что в нижнем",
    "текст на экране", "надпись на экране",
)

_VALID_AGENTS = {"browser", "web", "office", "system", "vision"}


@dataclass
class TaskItem:
    """Единица работы в стеке задач HostAgent."""
    task: str
    agents: List[str] = field(default_factory=lambda: ["system"])

    def __repr__(self) -> str:
        return f"TaskItem(agents={self.agents}, task={self.task[:60]!r})"


class HostAgent:
    """
    Оркестратор: принимает запрос, декомпозирует на подзадачи и
    делегирует BrowserAgent / WebAgent / OfficeAgent / SystemAgent.
    """

    def __init__(self, name: str = "HostAgent") -> None:
        self._name = name
        self._blackboard = Blackboard()
        self.appagent_dict: Dict = {}
        self._active_appagent = None

    @property
    def name(self) -> str:
        return self._name

    @property
    def blackboard(self) -> Blackboard:
        return self._blackboard

    # ── Sub-agent factory ─────────────────────────────────────────────────────

    def _make_agent(self, agent_type: str):
        """Instantiate a sub-agent by type string."""
        from ui_automation.agents.agent.browser_agent import BrowserAgent
        from ui_automation.agents.agent.web_agent import WebAgent
        from ui_automation.agents.agent.system_agent import SystemAgent
        from ui_automation.agents.agent.office_agent import OfficeAgent

        if agent_type == "browser":
            return BrowserAgent("BrowserAgent", host=self)
        elif agent_type == "web":
            return WebAgent("WebAgent", host=self)
        elif agent_type == "office":
            return OfficeAgent("OfficeAgent", host=self)
        elif agent_type == "vision":
            from ui_automation.agents.agent.vision_agent import VisionAgent
            return VisionAgent("VisionAgent", host=self)
        else:
            return SystemAgent("SystemAgent", host=self)

    # ── Task planning & classification ───────────────────────────────────────

    def _plan_tasks(self, task: str, context_hint: str = "") -> List[TaskItem]:
        """
        Use LLM to decompose task into an ordered list of TaskItems.
        Falls back to single-item list on any failure.
        """
        context_block = f"\n\nКонтекст:\n{context_hint}" if context_hint else ""
        prompt = (
            "Разбей задачу пользователя на минимальный список подзадач.\n"
            "Для каждой подзадачи укажи список агентов из: browser, web, office, system.\n"
            "Верни ТОЛЬКО валидный JSON-массив, без пояснений:\n"
            "[\n"
            "  {\"task\": \"описание подзадачи\", \"agents\": [\"тип_агента\"]},\n"
            "  ...\n"
            "]\n\n"
            "Агенты:\n"
            "  browser — клики/навигация/вкладки в браузере\n"
            "  web     — поиск информации в интернете, погода\n"
            "  office  — Excel, Word, PowerPoint\n"
            "  system  — приложения, файлы, UI Windows, звук\n"
            "  vision  — чтение/распознавание текста на экране, скриншот\n\n"
            f"Задача: {task}{context_block}\n\nJSON:"
        )
        try:
            client = openai.OpenAI(base_url=_API_BASE, api_key=_API_KEY)
            resp = client.chat.completions.create(
                model=_API_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=512,
                extra_body=_NO_THINK,
            )
            raw = resp.choices[0].message.content.strip()
            start = raw.find("[")
            end = raw.rfind("]") + 1
            if start == -1 or end == 0:
                raise ValueError("No JSON array found")
            items_raw: List[Dict] = json.loads(raw[start:end])
            stack: List[TaskItem] = []
            for item in items_raw:
                t = item.get("task", "").strip()
                if not t:
                    continue
                raw_agents = item.get("agents", [])
                agents = [a for a in raw_agents if a in _VALID_AGENTS] or ["system"]
                stack.append(TaskItem(task=t, agents=agents))
            if stack:
                return stack
        except Exception as e:
            utils.print_with_color(f"[HostAgent] plan_tasks error: {e}", "yellow")

        return [TaskItem(task=task, agents=[self.classify_task(task)])]

    def classify_task(self, task: str) -> str:
        """
        Classify a task as 'browser', 'web', 'office', or 'system'.

        Strategy:
          1. Keyword scan (fast, deterministic)
          2. LLM fallback for ambiguous cases
        """
        t = task.lower()

        if any(k in t for k in _VISION_KW):
            return "vision"

        if any(k in t for k in _BROWSER_KW):
            return "browser"

        if any(k in t for k in _WEB_KW):
            return "web"

        if any(k in t for k in _OFFICE_KW):
            return "office"

        if any(k in t for k in _SYSTEM_KW):
            return "system"

        return self._llm_classify(task)

    def _llm_classify(self, task: str) -> str:
        """Single LLM call to classify task type."""
        prompt = (
            "Определи тип задачи — ответь ТОЛЬКО одним словом без пояснений:\n"
            "  browser — управление браузером (клики, навигация, вкладки, сайты)\n"
            "  web     — поиск информации в интернете, погода, новости\n"
            "  office  — работа с Excel, Word, PowerPoint (чтение/запись данных, формулы)\n"
            "  system  — Windows UI, приложения, файлы, звук, проводник\n"
            "  vision  — прочитать/распознать текст на экране, скопировать с экрана\n\n"
            f"Задача: {task}\n\nОтвет:"
        )
        try:
            client = openai.OpenAI(base_url=_API_BASE, api_key=_API_KEY)
            resp = client.chat.completions.create(
                model=_API_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
                max_tokens=5,
                extra_body=_NO_THINK,
            )
            answer = resp.choices[0].message.content.strip().lower()
            for candidate in ("browser", "web", "office", "system"):
                if candidate in answer:
                    return candidate
        except Exception as e:
            utils.print_with_color(f"[HostAgent] classify LLM error: {e}", "yellow")
        return "system"

    # ── Blackboard context ────────────────────────────────────────────────────

    def _blackboard_context(self) -> str:
        """Returns a summary of the last 3 tasks from Blackboard, or empty string."""
        try:
            items = self._blackboard.requests.content
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
                        f"• [{agent_text}] {task_text[:80]}"
                        + (f" → {result_text[:120]}" if result_text else "")
                    )
            return "\n".join(lines)
        except Exception:
            return ""

    # ── Main entry point ──────────────────────────────────────────────────────

    def dispatch(self, task: str, context_hint: str = "") -> str:
        """
        Main entry point for chat-mode requests.

        1. Injects Blackboard cross-turn context.
        2. Calls _plan_tasks() to decompose the request into an ordered stack.
        3. Executes each TaskItem sequentially; items with multiple agents run
           them one after another, passing the previous result as context.
        4. Returns combined summary.
        """
        bb_ctx = self._blackboard_context()
        if bb_ctx:
            context_hint = f"[История предыдущих задач]\n{bb_ctx}\n\n" + context_hint

        if context_hint:
            utils.print_with_color(context_hint[:800] + ("…" if len(context_hint) > 800 else ""), "cyan")

        stack = self._plan_tasks(task, context_hint)
        total = len(stack)
        utils.print_with_color(
            f"[HostAgent] Стек ({total}): " + " → ".join(repr(s) for s in stack),
            "magenta",
        )

        step_results: List[str] = []

        for idx, item in enumerate(stack):
            step_num = f"[{idx + 1}/{total}]"

            prev_ctx = ""
            if step_results:
                prev_ctx = "\n\n[Результаты предыдущих шагов]\n" + "\n".join(
                    f"Шаг {i + 1}: {r}" for i, r in enumerate(step_results)
                )

            item_results: List[str] = []
            for agent_type in item.agents:
                utils.print_with_color(
                    f"[HostAgent] {step_num} агент={agent_type} | {item.task[:80]}",
                    "magenta",
                )
                agent = self._make_agent(agent_type)
                self.appagent_dict[agent.name] = agent
                self._active_appagent = agent

                full_subtask = item.task + prev_ctx
                if context_hint:
                    full_subtask += "\n\n" + context_hint

                result = agent.execute(full_subtask)
                item_results.append(result)
                prev_ctx += f"\n[{agent_type}]: {result}"

            combined = " | ".join(item_results) if item_results else ""
            step_results.append(combined)

            self._blackboard.add_requests({
                "task": item.task[:200],
                "agent": "+".join(item.agents),
                "result": combined[:300],
            })

        final = (
            "\n".join(f"Шаг {i + 1}: {r}" for i, r in enumerate(step_results))
            if len(step_results) > 1
            else (step_results[0] if step_results else "")
        )

        utils.print_with_color(f"\n[HostAgent] Готово:\n{final}", "green")

        from ui_automation.agents.agent.response_formatter import ResponseFormatter
        response = ResponseFormatter().format(final, user_query=task)
        utils.print_with_color(f"[HostAgent] voice: {response.voice}", "cyan")
        return response
