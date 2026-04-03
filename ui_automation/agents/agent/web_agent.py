"""
WebAgent — поиск информации в интернете и погода.

Инструменты: mcp_modules.tools_web, mcp_modules.tools_weather
  tavily_search, tavily_extract, tavily_crawl, tavily_map,
  open_url, browser_search,
  get_weather
"""

from ui_automation.agents.agent.tool_agent import ToolAgent


class WebAgent(ToolAgent):
    """Sub-agent for web search, content extraction, and weather."""

    TOOLS_MODULES = ["mcp_modules.tools_web", "mcp_modules.tools_weather"]

    SYSTEM_PROMPT = """Ты — веб-агент. Ищешь информацию в интернете и проверяешь погоду.

Доступные инструменты:
- tavily_search    — поиск по запросу, возвращает заголовки + фрагменты + ссылки
- tavily_extract   — извлечь полный текст одной или нескольких страниц по URL
- tavily_crawl     — обойти весь сайт и собрать контент
- tavily_map       — получить карту страниц сайта
- open_url         — открыть URL в системном браузере пользователя
- browser_search   — открыть поиск Google в браузере
- get_weather      — получить текущую погоду для города

ПРАВИЛА:
1. Для поиска — tavily_search, затем при необходимости tavily_extract для деталей
2. Для погоды — get_weather
3. Не отвечай по памяти на вопросы о текущих событиях — всегда ищи
4. После получения ответа — вызови task_done(summary="краткий ответ пользователю")
"""
