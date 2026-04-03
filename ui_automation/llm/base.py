import abc
from importlib import import_module
from typing import Dict


class BaseService(abc.ABC):
    @abc.abstractmethod
    def __init__(self, *args, **kwargs):
        pass

    @abc.abstractmethod
    def chat_completion(self, *args, **kwargs):
        pass

    @staticmethod
    def get_service(name: str, model_name: str = None) -> "BaseService":
        """
        Возвращает класс сервиса LLM по его имени.
        :param name: Имя сервиса.
        :return: Класс сервиса.
        """
        service_map = {
            "openai": "OpenAIService",
            "aoai": "OpenAIService",
            "azure_ad": "OpenAIService",
            "qwen": "QwenService",
            "ollama": "OllamaService",
            "gemini": "GeminiService",
            "claude": "ClaudeService",
            "custom": "CustomService",
            "operator": "OperatorServicePreview",
            "placeholder": "PlaceHolderService",
        }
        custom_service_map = {
            "llava": "LlavaService",
            "cogagent": "CogAgentService",
        }
        service_name = service_map.get(name, None)
        if service_name:
            if name in ["aoai", "azure_ad", "operator"]:
                module = import_module(".openai", package="ui_automation.llm")
            elif service_name == "CustomService":
                custom_model = "llava" if "llava" in model_name else model_name
                custom_service_name = custom_service_map.get(
                    "llava" if "llava" in custom_model else custom_model, None
                )
                if custom_service_name:
                    module = import_module("." + custom_model, package="ui_automation.llm")
                    service_name = custom_service_name
                else:
                    raise ValueError(f"Custom model {custom_model} not supported")
            else:
                module = import_module("." + name.lower(), package="ui_automation.llm")
            return getattr(module, service_name)
        else:
            raise ValueError(f"Service {name} not found.")

    def get_cost_estimator(
        self,
        api_type: str,
        model: str,
        prices: Dict[str, float],
        prompt_tokens: int,
        completion_tokens: int,
    ) -> float:
        """
        Рассчитывает приблизительную стоимость вызова модели по числу токенов в промпте и ответе.
        :param api_type: Тип используемого API.
        :param model: Имя модели.
        :param prices: Словарь с ценами по моделям.
        :param prompt_tokens: Число токенов в промпте.
        :param completion_tokens: Число токенов в ответе.
        :return: Оценочная стоимость использования модели.
        """

        if api_type.lower() == "openai":
            name = str(api_type + "/" + model)
        elif api_type.lower() in ["aoai", "azure_ad"]:
            name = str("azure/" + model)
        elif api_type.lower() == "qwen":
            name = str("qwen/" + model)
        elif api_type.lower() == "gemini":
            name = str("gemini/" + model)
        elif api_type.lower() == "claude":
            name = str("claude/" + model)
        else:
            name = model

        if name in prices:
            cost = (
                prompt_tokens * prices[name]["input"] / 1000
                + completion_tokens * prices[name]["output"] / 1000
            )
        else:
            return 0
        return cost
