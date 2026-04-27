from __future__ import annotations

import os
import threading
from typing import Any, Dict

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
from ui_automation.agents.agent.tool_agent import _strip_task_done_mentions as _strip_task_done

# Web-инструменты HostAgent использует сам, без отдельного подагента.
_WEB_TOOLS_MODULES = ["mcp_modules.tools_web", "mcp_modules.tools_weather"]
_WEB_SYSTEM_PROMPT = """/no_think
Ты — веб-агент. Ищешь информацию в интернете и проверяешь погоду.

Инструменты:
- web_search(query, max_results=3, fetch_pages=True) — DuckDuckGo + извлечение текста страниц через Scrapling.
- web_extract(urls, stealthy=False) — полный текст конкретных URL (stealthy=True для антибот-сайтов).
- get_weather — погода для города.

ПРАВИЛА:
1. web_search с fetch_pages=True — дефолт. fetch_pages=False — только если нужны быстрые сниппеты.
2. Не вызывай open_url/browser_search_google для поиска — только если пользователь явно попросил открыть ссылку.
3. После ответа — task_done(summary="краткий ответ пользователю").
"""

_CHAT_SYSTEM_PROMPT = (
    "/no_think\n"
    "Ты — Компас, умный персональный ассистент на русском языке. "
    "Отвечай кратко, по делу и дружелюбно. Если знаешь ответ на знаниевый вопрос — "
    "отвечай сразу. Если вопрос требует актуальных данных (новости, цены, курсы, погода) "
    "или ты не уверен — честно скажи и предложи поискать. Не выдумывай факты."
)


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
                    return _strip_task_done(text)
                reasoning = getattr(msg, "reasoning_content", None) or ""
                if reasoning.strip():
                    return _strip_task_done(reasoning.strip())
            except openai.APIConnectionError:
                return "Ошибка: нет соединения с моделью."
            except Exception as e:
                print(f"[ChatAgent] ошибка: {e}", flush=True)
                if not extra:
                    return f"Ошибка LLM: {e}"
        return "Не удалось получить ответ от модели."

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

    def dispatch(self, task: str, context_hint: str = "", conv_id: Any = None,
                 chat_history: str = "") -> str:
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

        # chat_history отдельным параметром идёт в Planner; для worker'ов подклеиваем
        # его в context_hint, чтобы chat-агент видел предыдущие реплики.
        if chat_history:
            context_hint = f"[История текущего чата]\n{chat_history}\n\n" + (context_hint or "")

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
            chat_history=chat_history,
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
            # Берём реальный ответ chat-агента, а не summary планировщика
            # (планировщик в DoneMarker часто пишет «ответ отправлен», теряя текст).
            chat_answers = [
                r.summary for r in trace.step_results
                if r.status == StepStatus.SUCCESS and r.summary
            ]
            voice_text = "\n\n".join(chat_answers) if chat_answers else final
            voice_text = _strip_task_done(voice_text)
            response = AssistantResponse(voice=voice_text)
            utils.print_with_color(f"[HostAgent] voice: {response.voice}", "cyan")
            return response

        # Планировщик в DoneMarker часто пишет короткое summary и теряет
        # фактический развёрнутый ответ воркера (таблицы, перечни фактов).
        # Если среди успешных шагов есть результат существенно богаче, чем final —
        # скармливаем форматтеру именно его.
        step_texts = [
            r.summary for r in trace.step_results
            if r.status == StepStatus.SUCCESS and r.summary
        ]
        raw_for_formatter = final
        if step_texts:
            best = max(step_texts, key=len)
            if len(best) > max(len(final) * 2, len(final) + 200):
                raw_for_formatter = best

        from ui_automation.agents.agent.response_formatter import ResponseFormatter
        from ui_automation import sources as _sources
        raw_for_formatter = _strip_task_done(raw_for_formatter)
        response = ResponseFormatter().format(
            raw_for_formatter, user_query=task,
            available_sources=_sources.items(),
        )
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
