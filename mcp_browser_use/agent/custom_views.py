from dataclasses import dataclass
from typing import Type

from browser_use.agent.views import AgentOutput
from browser_use.controller.registry.views import ActionModel
from pydantic import BaseModel, ConfigDict, Field, create_model


@dataclass
class CustomAgentStepInfo:
    step_number: int
    max_steps: int
    task: str
    add_infos: str
    memory: str
    task_progress: str
    future_plans: str


class CustomAgentBrain(BaseModel):
    """Текущее состояние агента"""

    prev_action_evaluation: str
    important_contents: str
    task_progress: str
    future_plans: str
    thought: str
    summary: str


class CustomAgentOutput(AgentOutput):
    """Модель вывода агента

    @dev: эта модель расширяется кастомными действиями в AgentService.
    Можно использовать поля, не указанные в модели явно, если они зарегистрированы в DynamicActions.
    """

    model_config = ConfigDict(arbitrary_types_allowed=True)

    current_state: CustomAgentBrain
    action: list[ActionModel]

    @staticmethod
    def type_with_custom_actions(
        custom_actions: Type[ActionModel],
    ) -> Type["CustomAgentOutput"]:
        """Расширить действия кастомными"""
        return create_model(
            "CustomAgentOutput",
            __base__=CustomAgentOutput,
            action=(
                list[custom_actions],
                Field(...),
            ),  # Аннотированное поле без значения по умолчанию
            __module__=CustomAgentOutput.__module__,
        )
