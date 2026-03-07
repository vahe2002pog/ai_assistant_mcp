"""
Главный входной файл MCP сервера.
Собирает все модули и запускает сервер.
"""

import sys
import os

# Добавляем родительскую папку в sys.path для импортов
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Импортируем ядро сервера
from mcp_modules.mcp_core import mcp

# Импортируем все инструменты (это регистрирует их в mcp)
import mcp_modules.tools_apps
import mcp_modules.tools_files
import mcp_modules.tools_web
import mcp_modules.tools_weather

if __name__ == "__main__":
    mcp.run(transport="stdio")
