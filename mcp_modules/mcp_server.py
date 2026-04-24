"""
MCP сервер — точка входа.
Регистрирует все субагент-инструменты и запускает FastMCP сервер.

Субагенты и их инструменты:
  WebAgent       → tools_web.py        (Tavily поиск, extract, open_url)
  FileAgent      → tools_files.py      (чтение, запись, копирование файлов)
  AppAgent       → tools_apps.py       (запуск приложений)
  WeatherAgent   → tools_weather.py    (погода)
  MediaAgent     → tools_media.py      (громкость, медиаконтроль)
  BrowserAgent   → tools_browser.py    (управление браузером через расширение)
    UIAgent        → tools_uiautomation.py (окна, клики, ввод — через pywinauto/Compass)
"""

import sys
import os

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_modules.mcp_core import mcp

# ── регистрация инструментов каждого субагента ────────────────────────────────
import mcp_modules.tools_web           # WebAgent
import mcp_modules.tools_files         # FileAgent
import mcp_modules.tools_apps          # AppAgent
import mcp_modules.tools_weather       # WeatherAgent
import mcp_modules.tools_media         # MediaAgent
import mcp_modules.tools_browser       # BrowserAgent
import mcp_modules.tools_uiautomation  # UIAgent (заменяет mcp_uiautomation)
import mcp_modules.tools_llama         # LlamaAgent (llama.cpp без указания модели)
import mcp_modules.tools_office        # OfficeAgent (Excel/Word/PowerPoint/Outlook через COM)

if __name__ == "__main__":
    mcp.run(transport="stdio")
