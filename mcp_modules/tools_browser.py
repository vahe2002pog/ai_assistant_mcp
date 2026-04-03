"""
Browser tools — управление браузером через Chrome расширение.
Команды отправляются напрямую через ws_server (WebSocket поток внутри процесса).
"""
import asyncio
import json
import urllib.parse
from mcp_modules.mcp_core import mcp


def _send_sync(command: str, params: dict = None, timeout: float = 15.0) -> dict:
    """Отправляет команду через ws_server (thread-safe)."""
    try:
        import browser_extension.ws_server as _ws

        # Запускаем поток если ещё не запущен
        if not _ws.is_running():
            _ws.start_thread()

        # Запускаем корутину в event loop ws_server
        import concurrent.futures
        import concurrent.futures
        future = asyncio.run_coroutine_threadsafe(
            _ws.send_command(command, params or {}, timeout=timeout),
            _ws._loop,
        )
        return future.result(timeout=timeout + 2)
    except concurrent.futures.TimeoutError:
        return {"error": "Chrome extension not connected"}
    except RuntimeError as e:
        return {"error": str(e) or "Chrome extension not connected"}
    except Exception as e:
        return {"error": str(e)}


async def _send(command: str, params: dict = None, timeout: float = 15.0) -> dict:
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _send_sync(command, params, timeout))


def _not_connected_msg() -> str:
    return (
        "Браузерное расширение не подключено.\n"
        "1. Запусти браузер\n"
        "2. Перейди в chrome://extensions → включи 'Режим разработчика'\n"
        "3. Загрузи папку browser_extension/extension\n"
        "4. Убедись что значок расширения показывает 'ON'"
    )


@mcp.tool()
async def browser_get_state() -> str:
    """Получить текущее состояние браузера: URL, заголовок, вкладки и интерактивные элементы с индексами.
    Вызывай перед browser_click или browser_input_text для получения актуальных индексов."""
    state = await _send("get_state")
    if "error" in state:
        if "not connected" in state["error"].lower():
            return _not_connected_msg()
        return f"Ошибка: {state['error']}"

    lines = [
        f"URL: {state['url']}",
        f"Заголовок: {state['title']}",
        f"Вкладок: {len(state['tabs'])}",
        "",
        "Интерактивные элементы (индекс используй в browser_click / browser_input_text):",
    ]
    for el in state.get("elements", []):
        tag  = el.get("tag", "")
        text = el.get("text", "")
        typ  = el.get("type", "")
        idx  = el.get("index", "?")
        label = f"  [{idx}] <{tag}"
        if typ:
            label += f' type="{typ}"'
        label += f"> {text}"
        lines.append(label)

    return "\n".join(lines)


@mcp.tool()
async def browser_navigate(url: str) -> str:
    """Открыть URL в текущей вкладке браузера.

    Args:
        url: Полный URL для перехода.
    """
    result = await _send("navigate", {"url": url})
    if "error" in result:
        if "not connected" in result["error"].lower():
            return _not_connected_msg()
        return f"Ошибка: {result['error']}"
    return f"Открыта страница: {url}"


@mcp.tool()
async def browser_click(index: int) -> str:
    """Кликнуть на интерактивный элемент по индексу из browser_get_state.

    Args:
        index: Индекс элемента из списка browser_get_state.
    """
    result = await _send("click", {"index": index})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Клик на [{index}]: {'успешно' if result.get('ok') else 'элемент не найден'}"


@mcp.tool()
async def browser_input_text(index: int, text: str) -> str:
    """Ввести текст в поле ввода по индексу из browser_get_state.

    Args:
        index: Индекс поля ввода из browser_get_state.
        text: Текст для ввода.
    """
    result = await _send("input_text", {"index": index, "text": text})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f'Введён текст "{text}" в элемент [{index}]'


@mcp.tool()
async def browser_extract_content() -> str:
    """Извлечь текстовый контент текущей страницы (весь видимый текст)."""
    result = await _send("extract_content")
    if "error" in result:
        return f"Ошибка: {result['error']}"
    content = result.get("content", "")
    return content or "Контент не найден."


@mcp.tool()
async def browser_scroll_down(amount: int = 500) -> str:
    """Прокрутить страницу вниз.

    Args:
        amount: Пикселей (по умолчанию 500).
    """
    result = await _send("scroll", {"amount": amount})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Прокрутка вниз на {amount} px"


@mcp.tool()
async def browser_scroll_up(amount: int = 500) -> str:
    """Прокрутить страницу вверх.

    Args:
        amount: Пикселей (по умолчанию 500).
    """
    result = await _send("scroll", {"amount": -amount})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Прокрутка вверх на {amount} px"


@mcp.tool()
async def browser_go_back() -> str:
    """Перейти назад в истории браузера."""
    result = await _send("go_back")
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return "Назад"


@mcp.tool()
async def browser_send_keys(keys: str) -> str:
    """Отправить клавишу в активный элемент страницы. Примеры: 'Enter', 'Escape', 'Tab'.

    Args:
        keys: Название клавиши.
    """
    result = await _send("send_keys", {"keys": keys})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Нажаты клавиши: {keys}"


@mcp.tool()
async def browser_open_tab(url: str) -> str:
    """Открыть URL в новой вкладке.

    Args:
        url: URL для открытия.
    """
    result = await _send("new_tab", {"url": url})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Открыта новая вкладка: {url}"


@mcp.tool()
async def browser_switch_tab(tab_id: int) -> str:
    """Переключиться на вкладку по ID из browser_get_state.

    Args:
        tab_id: ID вкладки из списка browser_get_state.
    """
    result = await _send("switch_tab", {"tab_id": tab_id})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Переключено на вкладку {tab_id}"


@mcp.tool()
async def browser_close_tab() -> str:
    """Закрыть текущую вкладку."""
    result = await _send("close_tab")
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return "Вкладка закрыта"


@mcp.tool()
async def browser_search_google(query: str) -> str:
    """Открыть поиск Google по запросу в текущей вкладке.

    Args:
        query: Поисковый запрос.
    """
    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    result = await _send("navigate", {"url": url})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f'Поиск Google: "{query}"'
