"""
Ядро MCP сервера и вспомогательные функции для работы с Windows API.
"""

import os
import sys
import ctypes
import logging
from ctypes import wintypes
from uuid import UUID
from fastmcp import FastMCP

# Добавляем родительскую папку в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import KNOWN_FOLDERS, env_map

# Настройка логов для отладки в консоли сервера
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("mcp_server")

# Инициализация FastMCP сервера
mcp = FastMCP("PC_Modules")


# Структура GUID для Windows API
class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


def guid_from_string(guid_string):
    """Преобразует строковый GUID в структуру GUID для Windows API."""
    u = UUID(guid_string)
    data4 = (ctypes.c_ubyte * 8).from_buffer_copy(u.bytes[8:])
    return GUID(u.time_low, u.time_mid, u.time_hi_version, data4)


def get_known_folder(folderid):
    """Получает путь к системной папке через SHGetKnownFolderPath."""
    SHGetKnownFolderPath = ctypes.windll.shell32.SHGetKnownFolderPath
    SHGetKnownFolderPath.argtypes = [
        ctypes.POINTER(GUID),
        wintypes.DWORD,
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.LPWSTR),
    ]
    SHGetKnownFolderPath.restype = ctypes.c_long

    guid = guid_from_string(folderid)
    path_ptr = wintypes.LPWSTR()

    result = SHGetKnownFolderPath(ctypes.byref(guid), 0, None, ctypes.byref(path_ptr))

    if result == 0:
        path = path_ptr.value
        # Освобождаем память, выделенную Windows API
        ctypes.windll.ole32.CoTaskMemFree(path_ptr)
        return path

    return None


def get_system_path(folder_name: str) -> str:
    """Получает системный путь через SHGetKnownFolderPath (GUID) или переменные окружения."""
    # Нормализация названия папки
    folder_name_lower = folder_name.lower().strip()

    # Прямой путь
    if os.path.exists(folder_name):
        return folder_name

    # Попытка получить через GUID константу
    folder_guid = KNOWN_FOLDERS.get(folder_name_lower)
    if folder_guid:
        try:
            path = get_known_folder(folder_guid)
            if path:
                logger.debug(f"SHGetKnownFolderPath: {folder_name} -> {path}")
                return path
        except Exception as e:
            logger.debug(f"SHGetKnownFolderPath не сработала для {folder_name}: {e}")

    # Fallback: переменные окружения для стандартных папок
    if folder_name_lower in env_map:
        env_var, sub_folder = env_map[folder_name_lower]
        base_path = os.getenv(env_var)
        if base_path:
            full_path = os.path.join(base_path, sub_folder)
            if os.path.exists(full_path):
                logger.debug(f"Fallback env: {folder_name} -> {full_path}")
                return full_path

    # Если ничего не сработало
    logger.error(f"Не удалось получить путь для '{folder_name}'")
    return None
