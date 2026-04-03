import json
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Union

from ui_automation.agents.memory.memory import Memory, MemoryItem
from ui_automation.automator.ui_control.screenshot import PhotographerFacade
from ui_automation.config.config import Config

configs = Config.get_instance().config_data


@dataclass
class ImageMemoryItemNames:
    """
    Переменные для элементов памяти изображений.
    """

    METADATA: str = "metadata"
    IMAGE_PATH: str = "image_path"
    IMAGE_STR: str = "image_str"


@dataclass
class ImageMemoryItem(MemoryItem):
    """
    Класс элемента памяти для изображений.
    """

    _memory_attributes = list(ImageMemoryItemNames.__annotations__.keys())


class Blackboard:
    """
    Шина (blackboard) для хранения данных и изображений, доступных всем агентам.
    """

    def __init__(self) -> None:
        """
        Инициализация чёрной доски (blackboard).
        """
        self._questions: Memory = Memory()
        self._requests: Memory = Memory()
        self._trajectories: Memory = Memory()
        self._screenshots: Memory = Memory()

        if configs.get("USE_CUSTOMIZATION", False):
            self.load_questions(
                configs.get("QA_PAIR_FILE", ""), configs.get("QA_PAIR_NUM", -1)
            )

    @property
    def questions(self) -> Memory:
        """
        Возвращает вопросы из blackboard.
        :return: Объект `Memory` с вопросами.
        """
        return self._questions

    @property
    def requests(self) -> Memory:
        """
        Возвращает запросы из blackboard.
        :return: Объект `Memory` с запросами.
        """
        return self._requests

    @property
    def trajectories(self) -> Memory:
        """
        Возвращает траектории из blackboard.
        :return: Объект `Memory` с траекториями.
        """
        return self._trajectories

    @property
    def screenshots(self) -> Memory:
        """
        Возвращает изображения из blackboard.
        :return: Объект `Memory` с изображениями.
        """
        return self._screenshots

    def add_data(
        self, data: Union[MemoryItem, Dict[str, str], str], memory: Memory
    ) -> None:
        """
        Добавляет данные в указанную память на blackboard.
        :param data: Данные для добавления (словарь, `MemoryItem` или строка).
        :param memory: Экземпляр `Memory`, в который добавляются данные.
        """

        if isinstance(data, dict):
            data_memory = MemoryItem()
            data_memory.add_values_from_dict(data)
            memory.add_memory_item(data_memory)
        elif isinstance(data, MemoryItem):
            memory.add_memory_item(data)
        elif isinstance(data, str):
            data_memory = MemoryItem()
            data_memory.add_values_from_dict({"text": data})
            memory.add_memory_item(data_memory)
        else:
            print(f"Предупреждение: неподдерживаемый тип данных: {type(data)} при добавлении.")

    def add_questions(self, questions: Union[MemoryItem, Dict[str, str]]) -> None:
        """
        Добавляет вопросы в blackboard.
        :param questions: Данные для добавления (словарь или `MemoryItem`).
        """

        self.add_data(questions, self.questions)

    def add_requests(self, requests: Union[MemoryItem, Dict[str, str]]) -> None:
        """
        Добавляет запросы в blackboard.
        :param requests: Данные для добавления (словарь или `MemoryItem`).
        """

        self.add_data(requests, self.requests)

    def add_trajectories(self, trajectories: Union[MemoryItem, Dict[str, str]]) -> None:
        """
        Добавляет траектории в blackboard.
        :param trajectories: Данные для добавления (словарь или `MemoryItem`).
        """

        self.add_data(trajectories, self.trajectories)

    def add_image(
        self,
        screenshot_path: str = "",
        metadata: Optional[Dict[str, str]] = None,
    ) -> None:
        """
        Добавляет изображение в blackboard.
        :param screenshot_path: Путь к изображению.
        :param metadata: Метаданные изображения.
        """

        if os.path.exists(screenshot_path):

            screenshot_str = PhotographerFacade().encode_image_from_path(
                screenshot_path
            )
        else:
            print(f"Путь к скриншоту {screenshot_path} не существует.")
            screenshot_str = ""

        image_memory_item = ImageMemoryItem()
        image_memory_item.add_values_from_dict(
            {
                ImageMemoryItemNames.METADATA: metadata.get(
                    ImageMemoryItemNames.METADATA
                ),
                ImageMemoryItemNames.IMAGE_PATH: screenshot_path,
                ImageMemoryItemNames.IMAGE_STR: screenshot_str,
            }
        )

        self.screenshots.add_memory_item(image_memory_item)

    def questions_to_json(self) -> str:
        """
        Преобразует вопросы в JSON-строку.
        :return: Вопросы в формате JSON.
        """
        return self.questions.to_json()

    def requests_to_json(self) -> str:
        """
        Преобразует запросы в JSON-строку.
        :return: Запросы в формате JSON.
        """
        return self.requests.to_json()

    def trajectories_to_json(self) -> str:
        """
        Преобразует траектории в JSON-строку.
        :return: Траектории в формате JSON.
        """
        return self.trajectories.to_json()

    def screenshots_to_json(self) -> str:
        """
        Преобразует скриншоты в JSON-строку.
        :return: Скриншоты в формате JSON.
        """
        return self.screenshots.to_json()

    def load_questions(self, file_path: str, last_k=-1) -> None:
        """
        Загружает данные вопросов из файла.
        :param file_path: Путь к файлу.
        :param last_k: Количество строк с конца файла. Если -1 — читать все строки.
        """
        qa_list = self.read_json_file(file_path, last_k)
        for qa in qa_list:
            self.add_questions(qa)

    def texts_to_prompt(self, memory: Memory, prefix: str) -> List[str]:
        """
        Преобразует данные памяти в структуру промпта.
        :return: Список частей промпта.
        """

        user_content = [
            {"type": "text", "text": f"{prefix}\n {json.dumps(memory.list_content)}"}
        ]

        return user_content

    def screenshots_to_prompt(self) -> List[str]:
        """
        Преобразует изображения в структуру промпта.
        :return: Список частей промпта.
        """

        user_content = []
        for screenshot_dict in self.screenshots.list_content:
            user_content.append(
                {
                    "type": "text",
                    "text": json.dumps(
                        screenshot_dict.get(ImageMemoryItemNames.METADATA, "")
                    ),
                }
            )
            user_content.append(
                {
                    "type": "image_url",
                    "image_url": {
                        "url": screenshot_dict.get(ImageMemoryItemNames.IMAGE_STR, "")
                    },
                }
            )

        return user_content

    def blackboard_to_dict(self) -> Dict[str, List[Dict[str, str]]]:
        """
        Преобразует содержимое blackboard в словарь.
        :return: Словарное представление blackboard.
        """
        blackboard_dict = {
            "questions": self.questions.to_list_of_dicts(),
            "requests": self.requests.to_list_of_dicts(),
            "trajectories": self.trajectories.to_list_of_dicts(),
            "screenshots": self.screenshots.to_list_of_dicts(),
        }

        return blackboard_dict

    def blackboard_to_json(self) -> str:
        """
        Преобразует blackboard в JSON-строку.
        :return: JSON-строка с содержимым blackboard.
        """
        return json.dumps(self.blackboard_to_dict())

    def blackboard_from_dict(
        self, blackboard_dict: Dict[str, List[Dict[str, str]]]
    ) -> None:
        """
        Загружает содержимое blackboard из словаря.
        :param blackboard_dict: Словарь с данными blackboard.
        """
        self.questions.from_list_of_dicts(blackboard_dict.get("questions", []))
        self.requests.from_list_of_dicts(blackboard_dict.get("requests", []))
        self.trajectories.from_list_of_dicts(blackboard_dict.get("trajectories", []))
        self.screenshots.from_list_of_dicts(blackboard_dict.get("screenshots", []))

    def blackboard_to_prompt(self) -> List[str]:
        """
        Преобразует blackboard в структуру промпта.
        :return: Список частей промпта.
        """
        prefix = [
            {
                "type": "text",
                "text": "[Чёрная доска:]",
            }
        ]

        blackboard_prompt = (
            prefix
            + self.texts_to_prompt(self.questions, "[Вопросы и ответы:]")
            + self.texts_to_prompt(self.requests, "[История запросов:]")
            + self.texts_to_prompt(
                self.trajectories, "[Ранее выполненные шаги траекторий:]")
            + self.screenshots_to_prompt()
        )

        return blackboard_prompt

    def is_empty(self) -> bool:
        """
        Проверяет, пуст ли blackboard.
        :return: True, если blackboard пуст, иначе False.
        """
        return (
            self.questions.is_empty()
            and self.requests.is_empty()
            and self.trajectories.is_empty()
            and self.screenshots.is_empty()
        )

    def clear(self) -> None:
        """
        Очищает содержимое blackboard.
        """
        self.questions.clear()
        self.requests.clear()
        self.trajectories.clear()
        self.screenshots.clear()

    @staticmethod
    def read_json_file(file_path: str, last_k=-1) -> Dict[str, str]:
        """
        Считывает JSON-строки из файла.
        :param file_path: Путь к файлу.
        :param last_k: Количество строк с конца файла. Если -1 — читать все строки.
        :return: Список объектов, считанных из файла.
        """

        data_list = []

        # Проверяем, существует ли файл
        if os.path.exists(file_path):
            # Открываем файл и читаем строки
            with open(file_path, "r", encoding="utf-8") as file:
                lines = file.readlines()

            # Если last_k != -1, оставляем только последние k строк
            if last_k != -1:
                lines = lines[-last_k:]

            # Парсим строки как JSON
            for line in lines:
                try:
                    data = json.loads(line.strip())
                    data_list.append(data)
                except json.JSONDecodeError:
                    print(f"Предупреждение: не удалось распарсить строку как JSON: {line}")

        return data_list


if __name__ == "__main__":

    blackboard = Blackboard()
    blackboard.add_data({"key1": "value1", "key2": "value2"})
    print(blackboard.blackboard_to_prompt())
