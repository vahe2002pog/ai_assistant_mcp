"""
Browser tools — управление браузером через Chrome расширение.
Команды отправляются через HTTP к ws_bridge.py (порт 9010).
"""
import urllib.request
import urllib.parse
import json
from mcp_modules.mcp_core import mcp

_BRIDGE_URL = "http://127.0.0.1:9010"


def _send(command: str, params: dict = None, timeout: float = 15.0) -> dict:
    """Синхронный HTTP запрос к WS bridge."""
    body = json.dumps({"command": command, "params": params or {}, "timeout": timeout}).encode()
    req = urllib.request.Request(
        f"{_BRIDGE_URL}/command",
        data=body,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout + 2) as resp:
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        return {"error": json.loads(e.read()).get("error", str(e))}
    except Exception as e:
        return {"error": str(e)}


async def send_command(command: str, params: dict = None) -> dict:
    import asyncio
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(None, lambda: _send(command, params))


@mcp.tool()
async def browser_get_state() -> str:
    """Получить текущее состояние браузера: URL, заголовок, вкладки и интерактивные элементы с индексами.
    Вызывай перед browser_click или browser_input_text для получения актуальных индексов."""
    state = await send_command("get_state")
    if "error" in state:
        return f"Ошибка: {state['error']}"

    lines = [
        f"URL: {state['url']}",
        f"Title: {state['title']}",
        f"Tabs: {len(state['tabs'])}",
        "",
        "Интерактивные элементы (используй index с browser_click / browser_input_text):",
    ]
    for el in state.get("elements", []):
        lines.append(f"  [{el['index']}] <{el['tag']}> {el['text']}")

    return "\n".join(lines)


@mcp.tool()
async def browser_navigate(url: str) -> str:
    """Открыть URL в текущей вкладке браузера."""
    result = await send_command("navigate", {"url": url})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Открыта страница: {url}"


@mcp.tool()
async def browser_click(index: int) -> str:
    """Кликнуть на интерактивный элемент по индексу из browser_get_state."""
    result = await send_command("click", {"index": index})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Клик на [{index}]"


@mcp.tool()
async def browser_input_text(index: int, text: str) -> str:
    """Ввести текст в поле ввода по индексу из browser_get_state."""
    result = await send_command("input_text", {"index": index, "text": text})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f'Введён текст "{text}" в элемент [{index}]'


@mcp.tool()
async def browser_extract_content() -> str:
    """Извлечь текстовый контент текущей страницы."""
    result = await send_command("extract_content")
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return result.get("content", "Контент не найден.")


@mcp.tool()
async def browser_scroll_down(amount: int = 500) -> str:
    """Прокрутить страницу вниз. amount — количество пикселей (по умолчанию 500)."""
    result = await send_command("scroll", {"amount": amount})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Прокрутка вниз на {amount} px"


@mcp.tool()
async def browser_scroll_up(amount: int = 500) -> str:
    """Прокрутить страницу вверх. amount — количество пикселей (по умолчанию 500)."""
    result = await send_command("scroll", {"amount": -amount})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Прокрутка вверх на {amount} px"


@mcp.tool()
async def browser_go_back() -> str:
    """Перейти назад в истории браузера."""
    result = await send_command("go_back")
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return "Назад"


@mcp.tool()
async def browser_send_keys(keys: str) -> str:
    """Отправить клавиши в активный элемент. Примеры: 'Enter', 'Escape'."""
    result = await send_command("send_keys", {"keys": keys})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Нажаты клавиши: {keys}"


@mcp.tool()
async def browser_open_tab(url: str) -> str:
    """Открыть URL в новой вкладке."""
    result = await send_command("new_tab", {"url": url})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Открыта новая вкладка: {url}"


@mcp.tool()
async def browser_switch_tab(tab_id: int) -> str:
    """Переключиться на вкладку по ID из browser_get_state."""
    result = await send_command("switch_tab", {"tab_id": tab_id})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f"Переключено на вкладку {tab_id}"


@mcp.tool()
async def browser_close_tab() -> str:
    """Закрыть текущую вкладку."""
    result = await send_command("close_tab")
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return "Вкладка закрыта"


@mcp.tool()
async def browser_search_google(query: str) -> str:
    """Поиск в Google по запросу в текущей вкладке."""
    url = f"https://www.google.com/search?q={urllib.parse.quote(query)}"
    result = await send_command("navigate", {"url": url})
    if "error" in result:
        return f"Ошибка: {result['error']}"
    return f'Поиск Google: "{query}"'
