"""
Controller — детерминированный исполнитель Plan'а.

Никаких LLM-вызовов: берёт Plan, по порядку (с учётом depends_on)
зовёт worker'ов, собирает ExecutionTrace. Решения «всё ли ок»
делегируются Verifier'у (stage 3) и/или HostAgent'у.

Контракт worker'а:
    Worker = Callable[[StepSpec, str], StepResult]
    где второй аргумент — строковый контекст («результаты предыдущих шагов»)
    для обратной совместимости с существующими ToolAgent/BrowserAgent.
"""
from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Callable, Dict, List, Optional

from ui_automation import utils
from ui_automation.agents.contracts import (
    AgentType, ErrorClass, ErrorInfo, ExecutionTrace,
    Plan, StepResult, StepSpec, StepStatus,
    VerificationResult, VerificationVerdict,
)

Worker = Callable[[StepSpec, str], StepResult]
_PARALLEL_SAFE_AGENTS = {AgentType.WEB, AgentType.CHAT}


class _VerifierProto:
    """Duck-type: verifier must expose .verify(step, result) -> VerificationResult."""
    def verify(self, step: StepSpec, result: StepResult) -> VerificationResult: ...


class BudgetExceeded(Exception):
    pass


class Controller:
    """Детерминированный исполнитель. Stateless между запусками."""

    def __init__(
        self,
        workers: Dict[AgentType, Worker],
        on_step_done: Optional[Callable[[StepResult, ExecutionTrace], None]] = None,
        is_cancelled: Optional[Callable[[], bool]] = None,
        verifier: Optional["_VerifierProto"] = None,
    ) -> None:
        self._workers = workers
        self._on_step_done = on_step_done or (lambda r, t: None)
        self._is_cancelled = is_cancelled or (lambda: False)
        self._verifier = verifier

    # ── Public ────────────────────────────────────────────────────────────────

    def execute(
        self,
        goal: str,
        planner,
        perceiver,
        budget_steps: int = 15,
        context_hint: str = "",
        chat_history: str = "",
    ) -> ExecutionTrace:
        """ReAct-loop: восприятие → планирование → действие → верификация → повтор.

        Plan.steps заполняется ИНКРЕМЕНТАЛЬНО, по одному шагу за итерацию.
        Останов: planner вернул DoneMarker, бюджет исчерпан, отмена,
        SECURITY-ошибка, или N последовательных провалов подряд.
        """
        from ui_automation.agents.planner import DoneMarker, ParallelSteps  # локально, избегаем циклов

        plan = Plan(user_request=goal, steps=[], budget_steps=budget_steps,
                    notes=f"context_hint_len={len(context_hint or '')}")
        trace = ExecutionTrace(
            task_id=plan.task_id, user_request=goal, plan=plan,
        )

        utils.print_with_color(f"[Controller] goal: {goal}", "magenta")

        last_step: Optional[StepSpec] = None
        last_result: Optional[StepResult] = None
        consecutive_failures = 0
        prev_summaries: List[str] = []

        for iteration in range(budget_steps):
            if self._is_cancelled():
                trace.final_status = StepStatus.CANCELLED
                trace.final_summary = "Отменено пользователем."
                break

            # 1. Perceive.
            perception = perceiver.perceive(last_step, last_result)
            utils.print_with_color(
                f"[Perceiver] {perception}", "cyan"
            )

            # 2. Plan next step.
            next_item = planner.next_step(
                goal=goal,
                history=plan.steps,
                results=trace.step_results,
                perception=perception,
                chat_history=chat_history,
                context_hint=context_hint,
            )
            if isinstance(next_item, DoneMarker):
                trace.final_status = StepStatus.SUCCESS
                trace.final_summary = next_item.summary
                utils.print_with_color(
                    f"[Planner] DONE: {next_item.summary}", "green"
                )
                break

            if isinstance(next_item, ParallelSteps):
                steps = next_item.steps
                if not self._parallel_safe(steps):
                    trace.final_status = StepStatus.FAILURE
                    trace.final_summary = (
                        "Планировщик попытался запустить небезопасный parallel batch. "
                        "Параллельно разрешены только независимые web/chat-шаги."
                    )
                    break

                if len(plan.steps) + len(steps) > budget_steps:
                    trace.final_status = StepStatus.FAILURE
                    trace.final_summary = (
                        f"Планировщик вернул parallel batch на {len(steps)} шагов, "
                        f"но бюджет ({budget_steps}) будет превышен."
                    )
                    break

                for step in steps:
                    plan.steps.append(step)

                prev_ctx = self._build_prev_ctx(prev_summaries)
                if context_hint:
                    prev_ctx = (prev_ctx + "\n\n" + context_hint).strip()

                utils.print_with_color(
                    f"[Controller] parallel batch: {len(steps)} steps"
                    + (f" | {next_item.reason}" if next_item.reason else ""),
                    "magenta",
                )
                batch_results = self._run_parallel_steps(
                    steps, prev_ctx, iteration + 1, budget_steps
                )

                for result in batch_results:
                    trace.add_result(result)
                    self._on_step_done(result, trace)
                    if result.summary:
                        prev_summaries.append(result.summary)

                last_step = steps[-1] if steps else last_step
                last_result = batch_results[-1] if batch_results else last_result

                if all(r.status == StepStatus.SUCCESS for r in batch_results):
                    consecutive_failures = 0
                else:
                    consecutive_failures += 1

                security_error = next(
                    (
                        r.error for r in batch_results
                        if r.status == StepStatus.FAILURE
                        and r.error
                        and r.error.error_class == ErrorClass.SECURITY
                    ),
                    None,
                )
                if security_error:
                    trace.final_status = StepStatus.FAILURE
                    trace.final_summary = (
                        f"Остановка по security-гарду: {security_error.message}"
                    )
                    break

                if consecutive_failures >= 3:
                    trace.final_status = StepStatus.FAILURE
                    trace.final_summary = (
                        "Остановка: 3 провала подряд, Planner не смог скорректировать курс."
                    )
                    break
                continue

            step: StepSpec = next_item
            plan.steps.append(step)

            # 3. Act.
            prev_ctx = self._build_prev_ctx(prev_summaries)
            if context_hint:
                prev_ctx = (prev_ctx + "\n\n" + context_hint).strip()
            result = self._run_step(step, prev_ctx, iteration + 1, budget_steps)

            # 4. Verify (optional).
            if (self._verifier is not None
                    and step.requires_verification
                    and result.status == StepStatus.SUCCESS):
                try:
                    v = self._verifier.verify(step, result)
                    trace.add_verification(v)
                    utils.print_with_color(
                        f"[Controller] verify: {v.verdict.value} — {v.reason}",
                        "cyan",
                    )
                    if v.verdict == VerificationVerdict.REJECTED:
                        result.status = StepStatus.FAILURE
                        result.error = ErrorInfo(
                            error_class=ErrorClass.STATE,
                            message=f"verifier rejected: {v.reason}",
                        )
                except Exception as e:
                    utils.print_with_color(f"[Controller] verifier crashed: {e}", "yellow")

            trace.add_result(result)
            self._on_step_done(result, trace)
            last_step, last_result = step, result

            if result.summary:
                prev_summaries.append(result.summary)

            if (step.agent == AgentType.CHAT
                    and result.status == StepStatus.SUCCESS):
                trace.final_status = StepStatus.SUCCESS
                trace.final_summary = result.summary or ""
                break

            # 5. Accounting.
            if result.status == StepStatus.SUCCESS:
                consecutive_failures = 0
            else:
                consecutive_failures += 1

            if (result.status == StepStatus.FAILURE
                    and result.error
                    and result.error.error_class == ErrorClass.SECURITY):
                trace.final_status = StepStatus.FAILURE
                trace.final_summary = (
                    f"Остановка по security-гарду: {result.error.message}"
                )
                break

            if consecutive_failures >= 3:
                trace.final_status = StepStatus.FAILURE
                trace.final_summary = (
                    "Остановка: 3 провала подряд, Planner не смог скорректировать курс."
                )
                break
        else:
            # Бюджет исчерпан без DoneMarker'а.
            trace.final_status = StepStatus.FAILURE
            trace.final_summary = (
                f"Превышен бюджет шагов ({budget_steps}) без завершения цели."
            )

        # Итоговый статус если не проставили явно.
        if trace.final_status == StepStatus.PENDING:
            trace.final_status = self._aggregate_status(trace.step_results)
            trace.final_summary = self._aggregate_summary(trace.step_results)

        trace.finished_at = time.time()
        utils.print_with_color(
            f"[Controller] done: {trace.final_status.value} "
            f"(steps={len(trace.step_results)}, "
            f"{trace.finished_at - trace.started_at:.1f}s)",
            "green",
        )
        return trace

    # ── Internal ──────────────────────────────────────────────────────────────

    def _run_step(
        self, step: StepSpec, prev_ctx: str, step_num: int, total: int
    ) -> StepResult:
        utils.print_with_color(
            f"[Controller] [{step_num}/{total}] {step.agent.value} | "
            f"{step.parameters.get('task', step.action_type)}",
            "magenta",
        )
        worker = self._workers.get(step.agent)
        if worker is None:
            return StepResult(
                step_id=step.step_id,
                status=StepStatus.FAILURE,
                error=ErrorInfo(
                    error_class=ErrorClass.SEMANTIC,
                    message=f"No worker registered for agent={step.agent.value}",
                ),
                finished_at=time.time(),
            )

        started = time.time()
        last_error: Optional[ErrorInfo] = None
        for attempt in range(step.max_retries + 1):
            try:
                result = worker(step, prev_ctx)
                result.retries_used = attempt
                if result.finished_at is None:
                    result.finished_at = time.time()
                if result.status in (StepStatus.SUCCESS, StepStatus.PARTIAL,
                                      StepStatus.NEEDS_CLARIFICATION):
                    return result
                # FAILURE — ретраим только transient
                last_error = result.error
                if (result.error is None
                        or result.error.error_class != ErrorClass.TRANSIENT):
                    return result
                utils.print_with_color(
                    f"[Controller] transient failure, retry {attempt + 1}"
                    f"/{step.max_retries}", "yellow"
                )
                time.sleep(min(2 ** attempt, 4))
            except Exception as e:
                last_error = ErrorInfo(
                    error_class=ErrorClass.UNKNOWN,
                    message=str(e),
                    details={"attempt": attempt},
                )
                utils.print_with_color(
                    f"[Controller] worker exception: {e}", "red"
                )

        return StepResult(
            step_id=step.step_id,
            status=StepStatus.FAILURE,
            error=last_error or ErrorInfo(message="unknown failure"),
            started_at=started,
            finished_at=time.time(),
            retries_used=step.max_retries,
        )

    def _run_parallel_steps(
        self, steps: List[StepSpec], prev_ctx: str, step_num: int, total: int
    ) -> List[StepResult]:
        """Run a validated batch of independent web/chat steps concurrently."""
        if not steps:
            return []

        results: List[Optional[StepResult]] = [None] * len(steps)
        with ThreadPoolExecutor(max_workers=len(steps)) as pool:
            futures = {
                pool.submit(self._run_step, step, prev_ctx, step_num, total): i
                for i, step in enumerate(steps)
            }
            for fut in as_completed(futures):
                idx = futures[fut]
                try:
                    results[idx] = fut.result()
                except Exception as e:
                    step = steps[idx]
                    results[idx] = StepResult(
                        step_id=step.step_id,
                        status=StepStatus.FAILURE,
                        error=ErrorInfo(
                            error_class=ErrorClass.UNKNOWN,
                            message=str(e),
                        ),
                        finished_at=time.time(),
                    )

        return [r for r in results if r is not None]

    @staticmethod
    def _parallel_safe(steps: List[StepSpec]) -> bool:
        if len(steps) < 2 or len(steps) > 4:
            return False
        seen_tasks = set()
        for step in steps:
            if step.agent not in _PARALLEL_SAFE_AGENTS:
                return False
            task = str(step.parameters.get("task") or step.free_text or "").strip().lower()
            if not task or task in seen_tasks:
                return False
            seen_tasks.add(task)
            if step.requires_confirmation or step.requires_verification:
                return False
        return True

    @staticmethod
    def _dependencies_satisfied(step: StepSpec, trace: ExecutionTrace) -> bool:
        if not step.depends_on:
            return True
        done_ok = {
            r.step_id for r in trace.step_results
            if r.status in (StepStatus.SUCCESS, StepStatus.PARTIAL)
        }
        return all(dep in done_ok for dep in step.depends_on)

    @staticmethod
    def _build_prev_ctx(prev_summaries: List[str]) -> str:
        if not prev_summaries:
            return ""
        return "\n\n[Результаты предыдущих шагов]\n" + "\n".join(
            f"Шаг {i + 1}: {s}" for i, s in enumerate(prev_summaries)
        )

    @staticmethod
    def _aggregate_status(results: List[StepResult]) -> StepStatus:
        if not results:
            return StepStatus.FAILURE
        statuses = {r.status for r in results}
        if StepStatus.FAILURE in statuses:
            # Любой успешный → PARTIAL
            if any(r.status == StepStatus.SUCCESS for r in results):
                return StepStatus.PARTIAL
            return StepStatus.FAILURE
        if StepStatus.NEEDS_CLARIFICATION in statuses:
            return StepStatus.NEEDS_CLARIFICATION
        if StepStatus.PARTIAL in statuses:
            return StepStatus.PARTIAL
        return StepStatus.SUCCESS

    @staticmethod
    def _aggregate_summary(results: List[StepResult]) -> str:
        if not results:
            return ""
        if len(results) == 1:
            return results[0].summary
        return "\n".join(
            f"Шаг {i + 1}: {r.summary}" for i, r in enumerate(results) if r.summary
        )


__all__ = ["Controller", "Worker", "BudgetExceeded"]
