from abc import ABC, abstractmethod

from langchain_community.vectorstores import FAISS

from ui_automation.config.config import get_offline_learner_indexer_config
from ui_automation.rag import web_search
from ui_automation.utils import print_with_color, get_hugginface_embedding


class RetrieverFactory:
    """
    Фабрика для создания разных типов ретриверов.
    """

    @staticmethod
    def create_retriever(retriever_type: str, *args, **kwargs):
        """
        Создаёт ретривер по указанному типу.
        :param retriever_type: Тип ретривера для создания.
        :return: Созданный ретривер.
        """
        if retriever_type == "offline":
            return OfflineDocRetriever(*args, **kwargs)
        elif retriever_type == "experience":
            return ExperienceRetriever(*args, **kwargs)
        elif retriever_type == "online":
            return OnlineDocRetriever(*args, **kwargs)
        elif retriever_type == "demonstration":
            return DemonstrationRetriever(*args, **kwargs)
        else:
            raise ValueError("Invalid retriever type: {}".format(retriever_type))


class Retriever(ABC):
    """
    Базовый класс для получения документов.
    """

    def __init__(self) -> None:
        """
        Инициализация нового экземпляра `Retriever`.
        """

        self.indexer = self.get_indexer()

        pass

    @abstractmethod
    def get_indexer(self):
        """
        Возвращает индексатор.
        :return: Экземпляр индексатора.
        """
        pass

    def retrieve(self, query: str, top_k: int, filter=None):
        """
        Получает документы по запросу.
        :param query: Запрос для поиска документов.
        :param top_k: Количество возвращаемых документов.
        :param filter: Фильтр для найденных документов.
        :return: Список найденных документов.
        """
        if not self.indexer:
            return []

        results = self.indexer.similarity_search(query, top_k, filter=filter)

        if not results:
            return []
        else:
            return results


class OfflineDocRetriever(Retriever):
    """
    Ретривер для офлайн-индексаторов (локальные индексы).
    """

    def __init__(self, app_name: str) -> None:
        """
        Инициализирует `OfflineDocRetriever`.
        :param app_name: Имя приложения.
        """
        self.app_name = app_name
        indexer_path = self.get_offline_indexer_path()
        self.indexer = self.get_indexer(indexer_path)

    def get_offline_indexer_path(self):
        """
        Получить путь до файла офлайн-индексатора.
        :return: Путь до офлайн-индексатора или `None`.
        """
        offline_records = get_offline_learner_indexer_config()
        for key in offline_records:
            if key.lower() in self.app_name.lower():
                return offline_records[key]

        return None

    def get_indexer(self, path: str):
        """
        Загрузить индексатор по указанному пути.
        :param path: Путь для загрузки индексатора.
        :return: Загруженный индексатор или `None` при ошибке.
        """

        if path:
            print_with_color(
                "Загрузка офлайн-индексатора из {path}...".format(path=path), "cyan"
            )
        else:
            return None

        try:
            db = FAISS.load_local(
                path, get_hugginface_embedding(), allow_dangerous_deserialization=True
            )
            return db
        except Exception as e:
            print_with_color(
                "Предупреждение: не удалось загрузить индексатор опыта из {path}, ошибка: {error}.".format(
                    path=path, error=e
                ),
                "yellow",
            )
            return None


class ExperienceRetriever(Retriever):
    """
    Ретривер для индексаторов с опытом/историей (experience).
    """

    def __init__(self, db_path) -> None:
        """
        Инициализация `ExperienceRetriever`.
        :param db_path: Путь к базе/индексу.
        """
        self.indexer = self.get_indexer(db_path)

    def get_indexer(self, db_path: str):
        """
        Загрузить индексатор опыта по пути `db_path`.
        :param db_path: Путь к базе/индексу.
        """

        try:
            db = FAISS.load_local(
                db_path,
                get_hugginface_embedding(),
                allow_dangerous_deserialization=True,
            )
            return db
        except Exception as e:
            print_with_color(
                "Предупреждение: не удалось загрузить индексатор опыта из {path}, ошибка: {error}.".format(
                    path=db_path, error=e
                ),
                "yellow",
            )
            return None


class OnlineDocRetriever(Retriever):
    """
    Ретривер для онлайн-поиска (использует веб-поиск).
    """

    def __init__(self, query: str, top_k: int) -> None:
        """
        Инициализирует онлайн-ретривер.
        :param query: Запрос для поиска.
        :param top_k: Количество документов для поиска.
        """
        self.query = query
        self.indexer = self.get_indexer(top_k)

    def get_indexer(self, top_k: int):
        """
        Create an online search indexer.
        :param top_k: The number of documents to retrieve.
        :return: The created indexer.
        """

        bing_retriever = web_search.BingSearchWeb()
        result_list = bing_retriever.search(self.query, top_k=top_k)
        documents = bing_retriever.create_documents(result_list)
        if len(documents) == 0:
            return None
        try:
            indexer = bing_retriever.create_indexer(documents)
            print_with_color(
                "Онлайн-индексатор успешно создан для {num} найденных результатов.".format(
                    num=len(documents)
                ),
                "cyan",
            )
        except Exception as e:
            print_with_color(
                "Предупреждение: не удалось создать онлайн-индексатор, ошибка: {error}.".format(
                    error=e
                ),
                "yellow",
            )
            return None

        return indexer


class DemonstrationRetriever(Retriever):
    """
    Ретривер для демонстрационных данных (demonstration).
    """

    def __init__(self, db_path) -> None:
        """
        Инициализация `DemonstrationRetriever`.
        :param db_path: Путь к базе/индексу демонстраций.
        """
        self.indexer = self.get_indexer(db_path)

    def get_indexer(self, db_path: str):
        """
        Загрузить демонстрационный индексатор по `db_path`.
        :param db_path: Путь к базе/индексу демонстраций.
        """

        try:
            db = FAISS.load_local(
                db_path,
                get_hugginface_embedding(),
                allow_dangerous_deserialization=True,
            )
            return db
        except Exception as e:
            print_with_color(
                "Предупреждение: не удалось загрузить индексатор опыта из {path}, ошибка: {error}.".format(
                    path=db_path, error=e
                ),
                "yellow",
            )
            return None
