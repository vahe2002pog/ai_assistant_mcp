"""
BrowserAgent — управляет браузером через Chrome-расширение (WebSocket).

Инструменты: mcp_modules.tools_browser
  browser_get_state, browser_navigate, browser_click, browser_input_text,
  browser_extract_content, browser_scroll_down, browser_scroll_up,
  browser_go_back, browser_send_keys, browser_open_tab, browser_switch_tab,
  browser_close_tab, browser_search_google
"""

from ui_automation.agents.agent.tool_agent import ToolAgent


class BrowserAgent(ToolAgent):
    """Sub-agent for all browser interactions via Chrome extension."""

    TOOLS_MODULES = ["mcp_modules.tools_browser"]

    SYSTEM_PROMPT = """Ты — браузерный агент. Управляешь браузером через Chrome расширение.

Доступные инструменты:
- browser_get_state       — текущий URL, заголовок, список вкладок, интерактивные элементы с индексами
- browser_navigate        — перейти по URL в текущей вкладке
- browser_click           — кликнуть на элемент по индексу из browser_get_state
- browser_input_text      — ввести текст в поле по индексу из browser_get_state
- browser_extract_content — извлечь весь текст страницы
- browser_scroll_down     — прокрутить вниз
- browser_scroll_up       — прокрутить вверх
- browser_go_back         — назад в истории
- browser_send_keys       — отправить клавишу (Enter, Escape, Tab)
- browser_open_tab        — открыть новую вкладку
- browser_switch_tab      — переключить вкладку по ID
- browser_close_tab       — закрыть текущую вкладку
- browser_search_google   — поиск Google в текущей вкладке

ПРАВИЛА:
1. ВСЕГДА начинай с browser_get_state — получи актуальные индексы элементов
2. После каждой навигации — вызывай browser_get_state снова
3. Для кликов и ввода используй только индексы из последнего browser_get_state
4. Если расширение не подключено — сообщи пользователю через task_done
5. После выполнения задачи — вызови task_done(summary="...")
"""
