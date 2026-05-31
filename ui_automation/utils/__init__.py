import importlib
import functools
import json
import os
import sys
import threading
from pathlib import Path
from typing import Optional, Any, Dict

from colorama import Fore, Style, init

# Инициализация colorama
init()


def print_with_color(text: str, color: str = "", end: str = "\n") -> None:
    """
    Печать текста указанным цветом с использованием ANSI-кодов библиотеки Colorama.

    :param text: Текст для вывода.
    :param color: Цвет текста (варианты: red, green, yellow, blue, magenta, cyan, white, black).
    """
    color_mapping = {
        "red": Fore.RED,
        "green": Fore.GREEN,
        "yellow": Fore.YELLOW,
        "blue": Fore.BLUE,
        "magenta": Fore.MAGENTA,
        "cyan": Fore.CYAN,
        "white": Fore.WHITE,
        "black": Fore.BLACK,
    }

    selected_color = color_mapping.get(color.lower(), "")
    colored_text = selected_color + text + Style.RESET_ALL

    print(colored_text, end=end)


def create_folder(folder_path: str) -> None:
    """
    Создаёт папку, если она не существует.

    :param folder_path: Путь к создаваемой папке.
    """
    if not os.path.exists(folder_path):
        os.makedirs(folder_path)


def check_json_format(string: str) -> bool:
    """
    Проверяет, можно ли корректно распарсить строку как JSON.
    :param string: Строка для проверки.
    :return: True, если строка валидный JSON, иначе False.
    """
    import json

    try:
        json.loads(string)
    except ValueError:
        return False
    return True


def json_parser(json_string: str) -> Dict[str, Any]:
    """
    Парсит JSON-строку в объект.
    :param json_string: JSON-строка для парсинга.
    :return: Распарсенный JSON-объект.
    """

    # Убирает обёртки ```json и ``` в начале и конце строки, если они есть.
    if json_string.startswith("```json"):
        json_string = json_string[7:-3]

    return json.loads(json_string)


def is_json_serializable(obj: Any) -> bool:
    """
    Проверяет, сериализуется ли объект в JSON.
    :param obj: Объект для проверки.
    :return: True, если объект сериализуем в JSON, иначе False.
    """
    try:
        json.dumps(obj)
        return True
    except TypeError:
        return False


def revise_line_breaks(args: Dict[str, Any]) -> Dict[str, Any]:
    """
    Заменяет последовательности '\\n' на реальные переносы строки '\n' в значениях аргументов.
    :param args: Словарь аргументов.
    :return: Словарь с заменёнными переносами строк.
    """
    if not args:
        return {}

    # Заменяем '\\n' на реальный символ перевода строки
    for key in args.keys():
        if isinstance(args[key], str):
            args[key] = args[key].replace("\\n", "\n")

    return args


def LazyImport(module_name: str) -> Any:
    """
    Импортирует модуль и сохраняет его в глобальном пространстве имён.
    :param module_name: Имя импортируемого модуля.
    :return: Импортированный модуль.
    """
    global_name = module_name.split(".")[-1]
    globals()[global_name] = importlib.import_module(module_name, __package__)
    return globals()[global_name]


def find_desktop_path() -> Optional[str]:
    """
    Находит путь к рабочему столу пользователя.
    """
    onedrive_path = os.environ.get("OneDrive")
    if onedrive_path:
        onedrive_desktop = os.path.join(onedrive_path, "Desktop")
        if os.path.exists(onedrive_desktop):
            return onedrive_desktop
    # Запасной вариант: локальный рабочий стол пользователя
    local_desktop = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.exists(local_desktop):
        return local_desktop
    return None


def append_string_to_file(file_path: str, string: str) -> None:
    """
    Дописывает строку в файл.
    :param file_path: Путь к файлу.
    :param string: Строка для добавления.
    """

    # Если файл не существует — создаём его.
    if not os.path.exists(file_path):
        with open(file_path, "w", encoding="utf-8") as file:
            pass

    # Дописываем строку в файл.
    with open(file_path, "a", encoding="utf-8") as file:
        file.write(string + "\n")


_EMB_CACHE: Dict[str, Any] = {}
_EMB_LOCK = threading.Lock()
_STDIO_DEVNULL_HANDLES = []
DEFAULT_EMBEDDING_MODEL = os.environ.get(
    "COMPASS_EMBEDDING_MODEL",
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
)


# Отключаем шумные прогресс-бары/логи до первого импорта transformers.
os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("TQDM_DISABLE", "1")


def _silence_st_logs() -> None:
    """Приглушаем шумные логи sentence-transformers / transformers / tqdm."""
    import logging
    for name in ("sentence_transformers", "sentence_transformers.SentenceTransformer",
                 "transformers", "transformers.modeling_utils",
                 "transformers.utils.loading_report", "huggingface_hub"):
        logging.getLogger(name).setLevel(logging.ERROR)
    os.environ.setdefault("TRANSFORMERS_VERBOSITY", "error")
    os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
    os.environ.setdefault("TQDM_DISABLE", "1")


def _ensure_stdio_streams() -> None:
    for name in ("stdout", "stderr"):
        if getattr(sys, name, None) is None:
            stream = open(os.devnull, "w", encoding="utf-8", errors="replace")
            setattr(sys, name, stream)
            _STDIO_DEVNULL_HANDLES.append(stream)


def _embedding_cache_dir() -> Path:
    override = os.environ.get("COMPASS_HF_HOME")
    if override:
        return Path(override)
    program_data = os.environ.get("PROGRAMDATA")
    if getattr(sys, "frozen", False) and program_data:
        return Path(program_data) / "Compass" / "hf_cache"
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface"))


def _prepare_embedding_cache() -> Path:
    cache_dir = _embedding_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("HF_HOME", str(cache_dir))
    os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", str(cache_dir / "sentence_transformers"))
    os.environ.setdefault("TRANSFORMERS_CACHE", str(cache_dir / "transformers"))
    return cache_dir


def get_hugginface_embedding(
    model_name: str = DEFAULT_EMBEDDING_MODEL,
):
    """
    Возвращает объект для получения эмбеддингов Hugging Face.
    Thread-safe singleton: одна модель на имя, не грузится повторно
    при параллельных вызовах из разных потоков.
    """
    cached = _EMB_CACHE.get(model_name)
    if cached is not None:
        return cached
    with _EMB_LOCK:
        cached = _EMB_CACHE.get(model_name)
        if cached is not None:
            return cached
        _ensure_stdio_streams()
        cache_dir = _prepare_embedding_cache()
        _silence_st_logs()
        from langchain_huggingface import HuggingFaceEmbeddings
        model_kwargs = {"device": "cpu"}
        cache_root = cache_dir / "hub"
        cache_name = "models--" + model_name.replace("/", "--")
        if (cache_root / cache_name).exists():
            model_kwargs["local_files_only"] = True
        inst = HuggingFaceEmbeddings(
            model_name=model_name,
            cache_folder=str(cache_dir),
            model_kwargs=model_kwargs,
            encode_kwargs={"normalize_embeddings": True},
        )
        _EMB_CACHE[model_name] = inst
        return inst


def get_huggingface_embedding(model_name: str = DEFAULT_EMBEDDING_MODEL):
    return get_hugginface_embedding(model_name)


def preload_huggingface_embedding() -> None:
    embeddings = get_hugginface_embedding()
    embeddings.embed_query("проверка индексации RAG")
