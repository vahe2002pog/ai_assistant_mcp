"""
Типизированный протокол сообщений между агентами.

Все сообщения Planner → Controller → Worker → Verifier идут через эти модели.
Свободный текст между агентами запрещён — только через поле `free_text`
конкретных моделей, и только там, где это явно семантически оправдано.

Модуль самодостаточный: ни от кого не зависит внутри проекта, можно безопасно
импортировать из любого места без циклов.
"""
from __future__ import annotations

import time
import uuid
from enum import Enum
from typing import Any, Dict, List, Literal, Optional, Union

from pydantic import BaseModel, Field, ConfigDict


# ── Перечисления ──────────────────────────────────────────────────────────────

class AgentType(str, Enum):
    """Кто может быть исполнителем шага."""
    SYSTEM = "system"   # UI Automation, приложения, файлы, медиа
    BROWSER = "browser" # DOM через расширение Chrome
    WEB = "web"         # Tavily / погода (внешний поиск)
    VISION = "vision"   # Скриншот + описание / верификация
    CHAT = "chat"       # Разговор/знания без инструментов


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILURE = "failure"
    PARTIAL = "partial"
    NEEDS_CLARIFICATION = "needs_clarification"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"


class ErrorClass(str, Enum):
    """Классы ошибок — определяют реакцию Controller'а."""
    TRANSIENT = "transient"   # retry с backoff
    STATE = "state"           # перепланировать (позвать Vision)
    SEMANTIC = "semantic"     # спросить пользователя
    SECURITY = "security"     # остановка, эскалация
    UNKNOWN = "unknown"


class VerificationVerdict(str, Enum):
    CONFIRMED = "confirmed"
    REJECTED = "rejected"
    UNCERTAIN = "uncertain"


# ── Базовый класс ─────────────────────────────────────────────────────────────

class _Msg(BaseModel):
    model_config = ConfigDict(extra="forbid", use_enum_values=False)


# ── Цель и действие ───────────────────────────────────────────────────────────

class Target(_Msg):
    """Структурированное описание цели действия.

    Не все поля обязательны — Planner заполняет то, что знает. Worker
    достраивает недостающее через UIA-дерево / DOM / Vision.
    """
    app: Optional[str] = None              # "Notepad", "chrome.exe"
    window_title: Optional[str] = None     # подстрока заголовка
    element_name: Optional[str] = None     # AutomationId / Name
    control_type: Optional[str] = None     # "Button", "Edit"
    selector: Optional[str] = None         # CSS/XPath для браузера
    url: Optional[str] = None
    path: Optional[str] = None             # файл/папка
    coordinates: Optional[List[int]] = None  # [x, y] — fallback


class StepSpec(_Msg):
    """Один шаг плана — команда от Planner к Worker через Controller."""
    step_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    parent_step_id: Optional[str] = None
    depends_on: List[str] = Field(default_factory=list)

    agent: AgentType
    action_type: str                  # "click", "type", "open_app", "search", ...
    target: Target = Field(default_factory=Target)
    parameters: Dict[str, Any] = Field(default_factory=dict)

    expected_outcome: str = ""        # нужно Verifier'у
    timeout_s: float = 30.0
    max_retries: int = 2
    requires_verification: bool = True
    requires_confirmation: bool = False  # деструктив → спросить юзера

    free_text: Optional[str] = None   # только для chat/web агентов


# ── Результаты ────────────────────────────────────────────────────────────────

class ErrorInfo(_Msg):
    error_class: ErrorClass = ErrorClass.UNKNOWN
    message: str = ""
    details: Dict[str, Any] = Field(default_factory=dict)


class Artifact(_Msg):
    """Побочный продукт шага: скриншот, текст, путь к файлу."""
    kind: Literal["screenshot", "text", "file", "url", "dom_snippet", "json"]
    data: Any                          # путь / строка / dict
    description: str = ""


class Observation(_Msg):
    """Структурированное наблюдение Worker'а (не текст!)."""
    ui_tree_snippet: Optional[Dict[str, Any]] = None
    dom_snippet: Optional[str] = None
    window_state: Optional[Dict[str, Any]] = None
    extra: Dict[str, Any] = Field(default_factory=dict)


class StepResult(_Msg):
    """Ответ Worker'а на StepSpec."""
    step_id: str
    status: StepStatus
    observations: Observation = Field(default_factory=Observation)
    artifacts: List[Artifact] = Field(default_factory=list)
    error: Optional[ErrorInfo] = None
    summary: str = ""                  # человекочитаемое, для форматтера/трассы
    started_at: float = Field(default_factory=time.time)
    finished_at: Optional[float] = None
    retries_used: int = 0

    @property
    def latency_s(self) -> float:
        return (self.finished_at or time.time()) - self.started_at


class VerificationResult(_Msg):
    """Вердикт Verifier'а (обычно Vision) по выполненному шагу."""
    step_id: str
    verdict: VerificationVerdict
    reason: str = ""
    screenshot_path: Optional[str] = None


# ── План и исполнение ─────────────────────────────────────────────────────────

class Plan(_Msg):
    """Граф шагов. Линейность выражается через пустые depends_on у первых
    шагов и заполненные у последующих. Параллельные шаги имеют пересекающиеся
    depends_on, но не зависят друг от друга."""
    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex[:8])
    user_request: str
    steps: List[StepSpec] = Field(default_factory=list)
    budget_steps: int = 20             # жёсткий бюджет, чтобы не крутиться
    notes: str = ""                    # свободный коммент Planner'а (для лога)


class ExecutionTrace(_Msg):
    """Полная трасса исполнения плана — для логов/реплея/метрик."""
    task_id: str
    user_request: str
    plan: Plan
    step_results: List[StepResult] = Field(default_factory=list)
    verifications: List[VerificationResult] = Field(default_factory=list)
    final_status: StepStatus = StepStatus.PENDING
    final_summary: str = ""
    started_at: float = Field(default_factory=time.time)
    finished_at: Optional[float] = None

    def add_result(self, r: StepResult) -> None:
        self.step_results.append(r)

    def add_verification(self, v: VerificationResult) -> None:
        self.verifications.append(v)


# ── Удобные union'ы ───────────────────────────────────────────────────────────

AnyMessage = Union[
    StepSpec, StepResult, VerificationResult, Plan, ExecutionTrace
]


__all__ = [
    "AgentType", "StepStatus", "ErrorClass", "VerificationVerdict",
    "Target", "StepSpec", "ErrorInfo", "Artifact", "Observation",
    "StepResult", "VerificationResult", "Plan", "ExecutionTrace",
    "AnyMessage",
]
