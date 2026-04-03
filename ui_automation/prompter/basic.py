import os
from abc import ABC, abstractmethod
from typing import Dict, List

import yaml

from ui_automation.utils import print_with_color


class BasicPrompter(ABC):
    """
    Абстрактный базовый класс для промптеров.
    """

    def __init__(
        self, is_visual: bool, prompt_template: str, example_prompt_template: str
    ):
        """
        Инициализация `BasicPrompter`.
        :param is_visual: Запрос для визуальной модели?
        :param prompt_template: Путь к шаблону промпта.
        :param example_prompt_template: Путь к шаблону примеров промпта.
        """
        self.is_visual = is_visual
        if prompt_template:
            self.prompt_template = self.load_prompt_template(prompt_template, is_visual)
        else:
            self.prompt_template = ""
        if example_prompt_template:
            self.example_prompt_template = self.load_prompt_template(
                example_prompt_template, is_visual
            )
        else:
            self.example_prompt_template = ""

    @staticmethod
    def load_prompt_template(template_path: str, is_visual=None) -> Dict[str, str]:
        """
        Load the prompt template.
        :return: The prompt template.
        """

        if is_visual == None:
            path = template_path
        else:
            path = template_path.format(
                mode="visual" if is_visual == True else "nonvisual"
            )

        if not path:
            return {}

        if os.path.exists(path):
            try:
                prompt = yaml.safe_load(open(path, "r", encoding="utf-8"))
            except yaml.YAMLError as exc:
                print_with_color(f"Ошибка загрузки шаблона промпта: {exc}", "yellow")
        else:
            raise FileNotFoundError(f"Шаблон промпта не найден по пути {path}")

        return prompt

    @staticmethod
    def prompt_construction(
        system_prompt: str, user_content: List[Dict[str, str]]
    ) -> List:
        """
        Формирует промпт для суммаризации опыта в пример.
        :param user_content: Содержимое пользователя.
        :return: Промпт для суммаризации опыта в пример.
        """

        system_message = {"role": "system", "content": system_prompt}

        user_message = {"role": "user", "content": user_content}

        prompt_message = [system_message, user_message]

        return prompt_message

    @staticmethod
    def retrived_documents_prompt_helper(
        header: str, separator: str, documents: List[str]
    ) -> str:
        """
        Формирует текст промпта для найденных документов.
        :param header: Заголовок секции.
        :param separator: Разделитель между документами.
        :param documents: Список документов.
        :return: Сформированный текст промпта.
        """

        if header:
            prompt = "\n<{header}:>\n".format(header=header)
        else:
            prompt = ""
        for i, document in enumerate(documents):
            if separator:
                prompt += "[{separator} {i}:]".format(separator=separator, i=i + 1)
                prompt += "\n"
            prompt += document
            prompt += "\n\n"
        return prompt

    @abstractmethod
    def system_prompt_construction(self) -> str:
        """
        Формирует системный промпт для LLM.
        """

        pass

    @abstractmethod
    def user_prompt_construction(self) -> str:
        """
        Формирует текстовый пользовательский промпт для LLM на основе поля `user` в шаблоне.
        """

        pass

    @abstractmethod
    def user_content_construction(self) -> str:
        """
        Формирует полное содержимое пользователя для LLM, включая текст и изображения.
        """

        pass

    def examples_prompt_helper(self) -> str:
        """
        Вспомогательная функция для формирования примеров в промпте для in-context обучения.
        """

        pass

    def api_prompt_helper(self) -> str:
        """
        Вспомогательная функция для формирования списка API и описаний в промпте.
        """

        pass
