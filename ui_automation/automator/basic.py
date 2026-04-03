from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Dict, List, Type


class ReceiverBasic(ABC):
    """
    Абстрактный интерфейс приёмника (receiver).
    """

    _command_registry: Dict[str, Type[CommandBasic]] = {}

    @property
    def command_registry(self) -> Dict[str, Type[CommandBasic]]:
        """
        Возвращает реестр команд, поддерживаемых приёмником.
        """
        return self._command_registry

    def register_command(self, command_name: str, command: CommandBasic) -> None:
        """
        Регистрирует команду в реестре приёмника.
        :param command_name: Имя команды.
        :param command: Класс команды.
        """

        self.command_registry[command_name] = command

    @property
    def supported_command_names(self) -> List[str]:
        """
        Возвращает список поддерживаемых имён команд.
        """
        return list(self.command_registry.keys())

    def self_command_mapping(self) -> Dict[str, CommandBasic]:
        """
        Возвращает отображение команда->приёмник для всех поддерживаемых команд.
        """
        return {command_name: self for command_name in self.supported_command_names}

    @classmethod
    def register(cls, command_class: Type[CommandBasic]) -> Type[CommandBasic]:
        """
        Декоратор для регистрации класса команды в реестре.
        :param command_class: Класс команды для регистрации.
        :return: Класс команды.
        """
        cls._command_registry[command_class.name()] = command_class
        return command_class

    @property
    def type_name(self):

        return self.__class__.__name__


class CommandBasic(ABC):
    """
    Абстрактный интерфейс команды.
    """

    def __init__(self, receiver: ReceiverBasic, params: Dict = None) -> None:
        """
        Инициализация команды.
        :param receiver: Приёмник, выполняющий команду.
        """
        self.receiver = receiver
        self.params = params if params is not None else {}

    @abstractmethod
    def execute(self):
        """
        Выполняет команду.
        """
        pass

    def undo(self):
        """
        Отменяет команду (undo).
        """
        pass

    def redo(self):
        """
        Повторно выполняет команду (redo).
        """
        self.execute()

    @classmethod
    @abstractmethod
    def name(cls):
        return cls.__class__.__name__


class ReceiverFactory(ABC):
    """
    Абстрактная фабрика для создания приёмников.
    """

    @abstractmethod
    def create_receiver(self, *args, **kwargs):
        pass

    @classmethod
    def name(cls) -> str:
        """
        Возвращает имя класса фабрики приёмников.
        """
        return cls.__class__.__name__

    @classmethod
    def is_api(cls) -> bool:
        """
        Определяет, создаёт ли фабрика API-приёмник.
        """
        return False
