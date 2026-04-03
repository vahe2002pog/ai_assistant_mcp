from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum
from logging import Logger
from typing import Any, Dict, List, Optional, Type, Union

from pywinauto.controls.uiawrapper import UIAWrapper

from ui_automation.utils import is_json_serializable, print_with_color


class ContextNames(Enum):
    """
    Имена ключей контекста сессии.
    """

    ID = "ID"  # The ID of the session
    MODE = "MODE"  # The mode of the session
    LOG_PATH = "LOG_PATH"  # The folder path to store the logs
    REQUEST = "REQUEST"  # The current request
    SUBTASK = "SUBTASK"  # The current subtask processed by the AppAgent
    PREVIOUS_SUBTASKS = (
        "PREVIOUS_SUBTASKS"  # The previous subtasks processed by the AppAgent
    )
    HOST_MESSAGE = "HOST_MESSAGE"  # The message from the HostAgent sent to the AppAgent
    REQUEST_LOGGER = "REQUEST_LOGGER"  # The logger for the LLM request
    LOGGER = "LOGGER"  # The logger for the session
    EVALUATION_LOGGER = "EVALUATION_LOGGER"  # The logger for the evaluation
    ROUND_STEP = "ROUND_STEP"  # The step of all rounds
    SESSION_STEP = "SESSION_STEP"  # The step of the current session
    CURRENT_ROUND_ID = "CURRENT_ROUND_ID"  # The ID of the current round
    APPLICATION_WINDOW = "APPLICATION_WINDOW"  # The window of the application
    APPLICATION_PROCESS_NAME = (
        "APPLICATION_PROCESS_NAME"  # The process name of the application
    )
    APPLICATION_ROOT_NAME = "APPLICATION_ROOT_NAME"  # The root name of the application
    CONTROL_REANNOTATION = "CONTROL_REANNOTATION"  # The re-annotation of the control provided by the AppAgent
    SESSION_COST = "SESSION_COST"  # The cost of the session
    ROUND_COST = "ROUND_COST"  # The cost of all rounds
    ROUND_SUBTASK_AMOUNT = (
        "ROUND_SUBTASK_AMOUNT"  # The amount of subtasks in all rounds
    )
    CURRENT_ROUND_STEP = "CURRENT_ROUND_STEP"  # The step of the current round
    CURRENT_ROUND_COST = "CURRENT_ROUND_COST"  # The cost of the current round
    CURRENT_ROUND_SUBTASK_AMOUNT = (
        "CURRENT_ROUND_SUBTASK_AMOUNT"  # The amount of subtasks in the current round
    )
    STRUCTURAL_LOGS = "STRUCTURAL_LOGS"  # The structural logs of the session

    @property
    def default_value(self) -> Any:
        """
        Возвращает значение по умолчанию для имени контекста на основе его типа.
        :return: Значение по умолчанию.
        """
        if (
            self == ContextNames.LOG_PATH
            or self == ContextNames.REQUEST
            or self == ContextNames.APPLICATION_PROCESS_NAME
            or self == ContextNames.APPLICATION_ROOT_NAME
            or self == ContextNames.MODE
            or self == ContextNames.SUBTASK
        ):
            return ""
        elif (
            self == ContextNames.SESSION_STEP
            or self == ContextNames.CURRENT_ROUND_ID
            or self == ContextNames.CURRENT_ROUND_STEP
            or self == ContextNames.CURRENT_ROUND_SUBTASK_AMOUNT
            or self == ContextNames.ID
        ):
            return 0
        elif (
            self == ContextNames.SESSION_COST or self == ContextNames.CURRENT_ROUND_COST
        ):
            return 0.0
        elif (
            self == ContextNames.ROUND_STEP
            or self == ContextNames.ROUND_COST
            or self == ContextNames.ROUND_SUBTASK_AMOUNT
        ):
            return {}
        elif (
            self == ContextNames.CONTROL_REANNOTATION
            or self == ContextNames.HOST_MESSAGE
            or self == ContextNames.PREVIOUS_SUBTASKS
        ):
            return []
        elif (
            self == ContextNames.REQUEST_LOGGER
            or self == ContextNames.LOGGER
            or self == ContextNames.EVALUATION_LOGGER
        ):
            return None  # Assuming Logger should be initialized elsewhere
        elif self == ContextNames.APPLICATION_WINDOW:
            return None  # Assuming UIAWrapper should be initialized elsewhere
        elif self == ContextNames.STRUCTURAL_LOGS:
            return defaultdict(lambda: defaultdict(list))
        else:
            return None

    @property
    def type(self) -> Type:
        """
        Возвращает ожидаемый тип значения для этого имени контекста.
        :return: Тип значения.
        """
        if (
            self == ContextNames.LOG_PATH
            or self == ContextNames.REQUEST
            or self == ContextNames.APPLICATION_PROCESS_NAME
            or self == ContextNames.APPLICATION_ROOT_NAME
            or self == ContextNames.MODE
            or self == ContextNames.SUBTASK
        ):
            return str
        elif (
            self == ContextNames.SESSION_STEP
            or self == ContextNames.CURRENT_ROUND_ID
            or self == ContextNames.CURRENT_ROUND_STEP
            or self == ContextNames.ID
            or self == ContextNames.ROUND_SUBTASK_AMOUNT
        ):
            return int
        elif (
            self == ContextNames.SESSION_COST or self == ContextNames.CURRENT_ROUND_COST
        ):
            return float
        elif (
            self == ContextNames.ROUND_STEP
            or self == ContextNames.ROUND_COST
            or self == ContextNames.CURRENT_ROUND_SUBTASK_AMOUNT
            or self == ContextNames.STRUCTURAL_LOGS
        ):
            return dict
        elif (
            self == ContextNames.CONTROL_REANNOTATION
            or self == ContextNames.HOST_MESSAGE
            or self == ContextNames.PREVIOUS_SUBTASKS
        ):
            return list
        elif (
            self == ContextNames.REQUEST_LOGGER
            or self == ContextNames.LOGGER
            or self == ContextNames.EVALUATION_LOGGER
        ):
            return Logger
        elif self == ContextNames.APPLICATION_WINDOW:
            return UIAWrapper
        else:
            return Any


@dataclass
class Context:
    """
    Класс контекста, хранящий состояние сессии и агентов.
    """

    _context: Dict[str, Any] = field(
        default_factory=lambda: {name.name: name.default_value for name in ContextNames}
    )

    def get(self, key: ContextNames) -> Any:
        """
        Возвращает значение из контекста по имени ключа.
        :param key: Имя ключа контекста.
        :return: Значение из контекста.
        """
        # Sync the current round step and cost
        self._sync_round_values()
        return self._context.get(key.name)

    def set(self, key: ContextNames, value: Any) -> None:
        """
        Устанавливает значение в контексте по ключу.
        :param key: Имя ключа контекста.
        :param value: Устанавливаемое значение.
        """
        if key.name in self._context:
            self._context[key.name] = value
            # Sync the current round step and cost
            if key == ContextNames.CURRENT_ROUND_STEP:
                self.current_round_step = value
            if key == ContextNames.CURRENT_ROUND_COST:
                self.current_round_cost = value
            if key == ContextNames.CURRENT_ROUND_SUBTASK_AMOUNT:
                self.current_round_subtask_amount = value
        else:
            raise KeyError(f"Key '{key}' is not a valid context name.")

    def _sync_round_values(self):
        """
        Синхронизирует значения шага и стоимости текущего раунда.
        """
        self.set(ContextNames.CURRENT_ROUND_STEP, self.current_round_step)
        self.set(ContextNames.CURRENT_ROUND_COST, self.current_round_cost)
        self.set(
            ContextNames.CURRENT_ROUND_SUBTASK_AMOUNT, self.current_round_subtask_amount
        )

    def update_dict(self, key: ContextNames, value: Dict[str, Any]) -> None:
        """
        Обновляет словарь в заданном ключе контекста, слияя переданный словарь.
        :param key: Ключ контекста для обновления.
        :param value: Словарь для добавления.
        """
        if key.name in self._context:
            context_value = self._context[key.name]
            if isinstance(value, dict) and isinstance(context_value, dict):
                self._context[key.name].update(value)
            else:
                raise TypeError(
                    f"Value for key '{key.name}' is {key.value}, requires a dictionary."
                )
        else:
            raise KeyError(f"Key '{key.name}' is not a valid context name.")

    @property
    def current_round_cost(self) -> Optional[float]:
        """
        Возвращает стоимость текущего раунда.
        """
        return self._context.get(ContextNames.ROUND_COST.name).get(
            self._context.get(ContextNames.CURRENT_ROUND_ID.name), 0
        )

    @current_round_cost.setter
    def current_round_cost(self, value: Optional[float]) -> None:
        """
        Устанавливает стоимость текущего раунда.
        :param value: Значение стоимости.
        """
        current_round_id = self._context.get(ContextNames.CURRENT_ROUND_ID.name)
        self._context[ContextNames.ROUND_COST.name][current_round_id] = value

    @property
    def current_round_step(self) -> int:
        """
        Возвращает текущий шаг в раунде.
        """
        return self._context.get(ContextNames.ROUND_STEP.name).get(
            self._context.get(ContextNames.CURRENT_ROUND_ID.name), 0
        )

    @current_round_step.setter
    def current_round_step(self, value: int) -> None:
        """
        Устанавливает текущий шаг раунда.
        :param value: Значение шага.
        """
        current_round_id = self._context.get(ContextNames.CURRENT_ROUND_ID.name)
        self._context[ContextNames.ROUND_STEP.name][current_round_id] = value

    @property
    def current_round_subtask_amount(self) -> int:
        """
        Возвращает количество подзадач в текущем раунде.
        """
        return self._context.get(ContextNames.ROUND_SUBTASK_AMOUNT.name).get(
            self._context.get(ContextNames.CURRENT_ROUND_ID.name), 0
        )

    @current_round_subtask_amount.setter
    def current_round_subtask_amount(self, value: int) -> None:
        """
        Устанавливает количество подзадач в текущем раунде.
        :param value: Значение для установки.
        """
        current_round_id = self._context.get(ContextNames.CURRENT_ROUND_ID.name)
        self._context[ContextNames.ROUND_SUBTASK_AMOUNT.name][current_round_id] = value

    def add_to_structural_logs(self, data: Dict[str, Any]) -> None:
        """
        Добавляет запись в структурированные логи сессии.
        :param data: Данные для добавления.
        """

        round_key = data.get("Round", None)
        subtask_key = data.get("SubtaskIndex", None)

        if round_key is None or subtask_key is None:
            return

        remaining_items = {key: data[key] for key in data}
        self._context[ContextNames.STRUCTURAL_LOGS.name][round_key][subtask_key].append(
            remaining_items
        )

    def filter_structural_logs(
        self, round_key: int, subtask_key: int, keys: Union[str, List[str]]
    ) -> Union[List[Any], List[Dict[str, Any]]]:
        """
        Фильтрует структурированные логи по ключам.
        :param round_key: Номер раунда.
        :param subtask_key: Номер подзадачи.
        :param keys: Ключи для извлечения.
        :return: Отфильтрованные логи.
        """

        structural_logs = self._context[ContextNames.STRUCTURAL_LOGS.name][round_key][
            subtask_key
        ]

        if isinstance(keys, str):
            return [log[keys] for log in structural_logs]
        elif isinstance(keys, list):
            return [{key: log[key] for key in keys} for log in structural_logs]
        else:
            raise TypeError(f"Keys should be a string or a list of strings.")

    def to_dict(self, ensure_serializable: bool = False) -> Dict[str, Any]:
        """
        Преобразует контекст в словарь.
        :param ensure_serializable: Заменять несериализуемые значения на None.
        :return: Словарь с данными контекста.
        """

        import copy

        context_dict = copy.deepcopy(self._context)

        if ensure_serializable:

            for key in ContextNames:
                if key.name in context_dict:
                    print_with_color(
                        f"Предупреждение: Значение Context.{key.name} не сериализуемо.",
                        "yellow",
                    )
                    if not is_json_serializable(context_dict[key.name]):

                        context_dict[key.name] = None

        return context_dict

    def from_dict(self, context_dict: Dict[str, Any]) -> None:
        """
        Load the context from a dictionary.
        :param context_dict: The dictionary of the context.
        """
        for key in ContextNames:
            if key.name in context_dict:
                self._context[key.name] = context_dict.get(key.name)

        # Sync the current round step and cost
        self._sync_round_values()
