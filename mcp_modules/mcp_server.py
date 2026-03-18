"""
Главный входной файл MCP сервера.
Собирает все модули и запускает сервер.
"""

import sys
import os

# Принудительно установить кодировку ввода/вывода в UTF-8 для корректной
# работы обмена по stdio между процессами на Windows (избегает UnicodeDecodeError
# при чтении stdout клиента). Устанавливаем переменную окружения и, если
# возможно, перенастраиваем stdout/stderr.
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    # Начиная с Python 3.7 можно перенастроить stdout напрямую
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

# Добавляем родительскую папку в sys.path для импортов
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Импортируем ядро сервера
from mcp_modules.mcp_core import mcp

# Импортируем все инструменты (это регистрирует их в mcp)
import mcp_modules.tools_apps
import mcp_modules.tools_files
import mcp_modules.tools_web
import mcp_modules.tools_weather
import mcp_modules.tools_media
import mcp_modules.tools_browser  # noqa: F401 — side-effect import (регистрирует browser tools в mcp)
# Подключаем локально скопированные инструменты UIAutomation (если есть)
# try:
from mcp_uiautomation.tools import (
    register_discovery_tools,
    register_interaction_tools,
    register_query_tools,
    register_pattern_tools,
    register_helper_tools,
)

# Регистрируем инструменты в общем объекте mcp
register_discovery_tools(mcp)
register_interaction_tools(mcp)
register_query_tools(mcp)
register_pattern_tools(mcp)
register_helper_tools(mcp)
# except Exception as e:
#     print(f"Не удалось подключить mcp_uiautomation: {e}")

if __name__ == "__main__":
    mcp.run(transport="stdio")
