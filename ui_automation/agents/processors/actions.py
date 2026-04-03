
import sys

sys.path.append("./")


import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from pywinauto.controls.uiawrapper import UIAWrapper

from ui_automation import utils
from ui_automation.automator.puppeteer import AppPuppeteer
from ui_automation.automator.ui_control.screenshot import PhotographerDecorator
from ui_automation.config.config import Config


@dataclass
class BaseControlLog:
    """
    Данные логов элементов управления для HostAgent.
    """

    control_name: str = ""
    control_class: str = ""
    control_type: str = ""
    control_automation_id: str = ""
    control_friendly_class_name: str = ""
    control_matched: bool = True
    control_coordinates: Dict[str, int] = field(default_factory=dict)

    def is_empty(self) -> bool:

        return self == BaseControlLog()


@dataclass
class ActionExecutionLog:
    """
    Лог выполнения действия.
    """

    status: str = ""
    error: str = ""
    traceback: str = ""
    return_value: Any = None


class OneStepAction:

    def __init__(
        self,
        function: str = "",
        args: Dict[str, Any] = {},
        control_label: str = "",
        control_text: str = "",
        after_status: str = "",
        results: Optional[ActionExecutionLog] = None,
        configs=Config.get_instance().config_data,
    ):
        self._function = function
        self._args = args
        self._control_label = control_label
        self._control_text = control_text
        self._after_status = after_status
        self._results = ActionExecutionLog() if results is None else results
        self._configs = configs
        self._control_log = BaseControlLog()

    @property
    def function(self) -> str:
        """
        Возвращает имя функции.
        :return: Имя функции.
        """
        return self._function

    @property
    def args(self) -> Dict[str, Any]:
        """
        Возвращает аргументы функции.
        :return: Словарь аргументов.
        """
        return self._args

    @property
    def control_label(self) -> str:
        """
        Возвращает метку элемента управления.
        :return: Метка элемента управления.
        """
        return self._control_label

    @property
    def control_text(self) -> str:
        """
        Возвращает текст элемента управления.
        :return: Текст элемента управления.
        """
        return self._control_text

    @property
    def after_status(self) -> str:
        """
        Возвращает статус после выполнения действия.
        :return: Статус.
        """
        return self._after_status

    @property
    def control_log(self) -> BaseControlLog:
        """
        Возвращает лог элемента управления.
        :return: Экземпляр `BaseControlLog`.
        """
        return self._control_log

    @control_log.setter
    def control_log(self, control_log: BaseControlLog) -> None:
        """
        Устанавливает лог элемента управления.
        :param control_log: Объект лога элемента управления.
        """
        self._control_log = control_log

    @property
    def results(self) -> ActionExecutionLog:
        """
        Возвращает результаты выполнения действия.
        :return: Экземпляр `ActionExecutionLog`.
        """
        return self._results

    @results.setter
    def results(self, results: ActionExecutionLog) -> None:
        """
        Устанавливает результаты выполнения действия.
        :param results: Экземпляр `ActionExecutionLog`.
        """
        self._results = results

    @property
    def command_string(self) -> str:
        """
        Генерирует строку вызова функции.
        :return: Строка вызова функции.
        """
        # Format the arguments
        args_str = ", ".join(f"{k}={v!r}" for k, v in self.args.items())

        # Return the function call string
        return f"{self.function}({args_str})"

    def is_same_action(self, action_to_compare: Dict[str, Any]) -> bool:
        """
        Проверяет, являются ли два действия одинаковыми.
        :param action_to_compare: Действие для сравнения.
        :return: True, если действия совпадают.
        """

        return (
            self.function == action_to_compare.get("Function")
            and self.args == action_to_compare.get("Args")
            and self.control_text == action_to_compare.get("ControlText")
        )

    def count_repeat_times(self, previous_actions: List[Dict[str, Any]]) -> int:
        """
        Подсчитывает, сколько раз такое же действие встречалось в предыдущих действиях.
        :param previous_actions: Список предыдущих действий.
        :return: Количество повторений.
        """

        count = 0
        for action in previous_actions[::-1]:
            if self.is_same_action(action):
                count += 1
            else:
                break
        return count

    def to_dict(
        self, previous_actions: Optional[List[Dict[str, Any]]]
    ) -> Dict[str, Any]:
        """
        Преобразует действие в словарь.
        :param previous_actions: Список предыдущих действий.
        :return: Словарное представление действия.
        """

        action_dict = {
            "Function": self.function,
            "Args": self.args,
            "ControlLabel": self.control_label,
            "ControlText": self.control_text,
            "Status": self.after_status,
            "Results": asdict(self.results),
        }

        # Add the repetitive times of the same action in the previous actions if the previous actions are provided.
        if previous_actions:
            action_dict["RepeatTimes"] = self.count_repeat_times(previous_actions)

        return action_dict

    def to_string(self, previous_actions: Optional[List["OneStepAction"]]) -> str:
        """
        Преобразует действие в строковое представление (JSON).
        :param previous_actions: Список предыдущих действий.
        :return: JSON-строка с действием.
        """
        return json.dumps(self.to_dict(previous_actions), ensure_ascii=False)

    def _control_validation(self, control: UIAWrapper) -> bool:
        """
        Валидирует доступность элемента управления для выполнения действия.
        :param control: Контрольный элемент.
        :return: True, если элемент доступен.
        """
        try:
            control.is_enabled()
            if control.is_enabled() and control.is_visible():
                return True
            else:
                return False
        except:
            return False

    def execute(self, puppeteer: AppPuppeteer) -> Any:
        """
        Выполняет действие через пуппетера.
        :param puppeteer: Экземпляр `AppPuppeteer`.
        """
        return puppeteer.execute_command(self.function, self.args)

    def action_flow(
        self,
        puppeteer: AppPuppeteer,
        control_dict: Dict[str, UIAWrapper],
        application_window: UIAWrapper,
    ) -> Tuple[ActionExecutionLog, BaseControlLog]:
        """
        Выполняет полный поток действия: валидация контроля, выполнение и сбор лога.
        :param puppeteer: Пуппетер для управления приложением.
        :param control_dict: Словарь меток элементов управления.
        :param application_window: Окно приложения.
        :return: Лог выполнения действия и лог контроля.
        """
        control_selected: UIAWrapper = control_dict.get(self.control_label, None)

        # Если элемент выбран, но недоступен — возвращаем ошибку.
        if control_selected is not None and not self._control_validation(
            control_selected
        ):
            self.results = ActionExecutionLog(
                status="error",
                traceback="Control is not available.",
                error="Control is not available.",
            )
            self._control_log = BaseControlLog()

            return self.results

        # Создаём приёмник для управления элементом.
        puppeteer.receiver_manager.create_ui_control_receiver(
            control_selected, application_window
        )

        if self.function:

            if self._configs.get("SHOW_VISUAL_OUTLINE_ON_SCREEN", True):
                if control_selected:
                    control_selected.draw_outline(colour="red", thickness=3)
                    time.sleep(self._configs.get("RECTANGLE_TIME", 0))

            self._control_log = self._get_control_log(
                control_selected=control_selected, application_window=application_window
            )

            try:
                return_value = self.execute(puppeteer=puppeteer)
                if not utils.is_json_serializable(return_value):
                    return_value = ""

                self.results = ActionExecutionLog(
                    status="success",
                    return_value=return_value,
                )

            except Exception as e:

                import traceback

                self.results = ActionExecutionLog(
                    status="error",
                    traceback=traceback.format_exc(),
                    error=str(e),
                )
            return self.results

    def _get_control_log(
        self,
        control_selected: Optional[UIAWrapper],
        application_window: UIAWrapper,
    ) -> BaseControlLog:
        """
        Собирает данные лога для выбранного элемента управления.
        :param control_selected: Выбранный элемент.
        :param application_window: Окно приложения.
        :return: Объект `BaseControlLog`.
        """

        if not control_selected or not application_window:
            return BaseControlLog()

        control_coordinates = PhotographerDecorator.coordinate_adjusted(
            application_window.rectangle(), control_selected.rectangle()
        )

        control_log = BaseControlLog(
            control_name=control_selected.element_info.name,
            control_class=control_selected.element_info.class_name,
            control_type=control_selected.element_info.control_type,
            control_matched=control_selected.element_info.name == self.control_text,
            control_automation_id=control_selected.element_info.automation_id,
            control_friendly_class_name=control_selected.friendly_class_name(),
            control_coordinates={
                "left": control_coordinates[0],
                "top": control_coordinates[1],
                "right": control_coordinates[2],
                "bottom": control_coordinates[3],
            },
        )

        return control_log

    def print_result(self) -> None:
        """
        Выводит результат выполнения действия в консоль с цветами.
        """

        utils.print_with_color(
            "Выбранный элемент🕹️: {control_text}, метка: {label}".format(
                control_text=self.control_text, label=self.control_label
            ),
            "yellow",
        )
        utils.print_with_color(
            "Применено действие⚒️: {action}".format(action=self.command_string), "blue"
        )

        result_color = "red" if self.results.status != "success" else "green"

        utils.print_with_color(
            "Результат выполнения📜: {result}".format(result=asdict(self.results)),
            result_color,
        )

    def get_operation_point_list(self) -> List[Tuple[int]]:
        """
        Возвращает список точек операции для действия (например, путь клика).
        :return: Список координат (x, y).
        """

        if "path" in self.args:
            return [(point["x"], point["y"]) for point in self.args["path"]]
        elif "x" in self.args and "y" in self.args:
            return [(self.args["x"], self.args["y"])]
        else:
            return []


class ActionSequence:
    """
    Последовательность одношаговых действий.
    """

    def __init__(self, actions: Optional[List[OneStepAction]] = []):

        if not actions:
            actions = []
            self._status = "FINISH"
        else:
            self._status = actions[0].after_status

        self._actions = actions
        self._length = len(actions)

    @property
    def actions(self) -> List[OneStepAction]:
        """
        Возвращает список действий.
        :return: Список `OneStepAction`.
        """
        return self._actions

    @property
    def length(self) -> int:
        """
        Возвращает длину последовательности действий.
        :return: Количество действий.
        """
        return len(self._actions)

    @property
    def status(self) -> str:
        """
        Возвращает статус последовательности действий.
        :return: Статус.
        """
        return self._status

    def add_action(self, action: OneStepAction) -> None:
        """
        Добавляет действие в последовательность.
        :param action: Экземпляр `OneStepAction`.
        """
        self._actions.append(action)

    def to_list_of_dicts(
        self,
        success_only: bool = False,
        previous_actions: Optional[List[Dict[str, Any]]] = None,
    ) -> List[Dict[str, Any]]:
        """
        Преобразует последовательность действий в список словарей.
        :param success_only: Учитывать только успешные действия.
        :param previous_actions: Список предыдущих действий для подсчёта повторов.
        :return: Список словарей действий.
        """

        action_list = []
        for action in self.actions:
            if success_only and action.results.status != "success":
                continue
            action_list.append(action.to_dict(previous_actions))
        return action_list

    def to_string(self, success_only: bool = False, previous_actions=None) -> str:
        """
        Преобразует последовательность действий в JSON-строку.
        :param success_only: Учитывать только успешные действия.
        :param previous_actions: Список предыдущих действий.
        :return: JSON-строка.
        """
        return json.dumps(
            self.to_list_of_dicts(success_only, previous_actions), ensure_ascii=False
        )

    def execute_all(
        self,
        puppeteer: AppPuppeteer,
        control_dict: Dict[str, UIAWrapper],
        application_window: UIAWrapper,
    ) -> None:
        """
        Выполняет все действия в последовательности.
        :param puppeteer: Экземпляр `AppPuppeteer`.
        :param control_dict: Словарь элементов управления.
        :param application_window: Окно приложения.
        """

        early_stop = False

        for action in self.actions:
            if early_stop:
                action.results = ActionExecutionLog(
                    status="error", error="Early stop due to error in previous actions."
                )

            else:
                self._status = action.after_status

                action.action_flow(puppeteer, control_dict, application_window)

                # Небольшая пауза, чтобы не перегружать UI.
                time.sleep(0.5)

            if action.results.status != "success":
                early_stop = True

    def get_results(self, success_only: bool = False) -> List[Dict[str, Any]]:
        """
        Возвращает результаты выполнения действий.
        :param success_only: Возвращать только успешные результаты.
        :return: Список результатов.
        """
        return [
            asdict(action.results)
            for action in self.actions
            if not success_only or action.results.status == "success"
        ]

    def get_control_logs(self, success_only: bool = False) -> List[Dict[str, Any]]:
        """
        Возвращает логи элементов управления для каждого действия.
        :param success_only: Учитывать только успешные действия.
        :return: Список логов элементов управления.
        """
        return [
            asdict(action.control_log)
            for action in self.actions
            if not success_only or action.results.status == "success"
        ]

    def get_success_control_coords(self) -> List[Dict[str, Any]]:
        """
        Возвращает координаты элементов управления для успешных действий.
        :return: Список координат.
        """
        return [
            action.control_log.control_coordinates
            for action in self.actions
            if action.results.status == "success" and not action.control_log.is_empty()
        ]

    def get_function_calls(self, is_success_only: bool = False) -> List[str]:
        """
        Возвращает строки вызовов функций, соответствующих действиям.
        :param is_success_only: Включать только успешные действия.
        :return: Список строк вызовов.
        """
        return [
            action.command_string
            for action in self.actions
            if not is_success_only or action.results.status == "success"
        ]

    def print_all_results(self, success_only: bool = False) -> None:
        """
        Печатает результаты выполнения всех действий.
        """
        index = 1
        for action in self.actions:
            if success_only and action.results.status != "success":
                continue
            if self.length > 1:
                utils.print_with_color(f"Action {index}:", "cyan")
            action.print_result()
            index += 1
        utils.print_with_color(f"Итоговый статус: {self.status}", "yellow")


if __name__ == "__main__":

    action1 = OneStepAction(
        function="click",
        args={"button": "left"},
        control_label="1",
        control_text="OK",
        after_status="success",
        results=ActionExecutionLog(status="success"),
    )

    action2 = OneStepAction(
        function="click",
        args={"button": "right"},
        control_label="2",
        control_text="NotOK",
        after_status="success",
        results=ActionExecutionLog(status="success"),
    )

    action_sequence = ActionSequence([action1, action2])

    previous_actions = [
        {"Function": "click", "Args": {"button": "left"}, "ControlText": "OK"},
        {"Function": "click", "Args": {"button": "right"}, "ControlText": "OK"},
        {"Function": "click", "Args": {"button": "left"}, "ControlText": "OK"},
        {"Function": "click", "Args": {"button": "left"}, "ControlText": "OK"},
    ]

    print(action_sequence.to_list_of_dicts(previous_actions=previous_actions))
