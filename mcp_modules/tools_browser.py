"""
Browser tools — низкоуровневое управление браузером через browser-use.
Все инструменты регистрируются в общем mcp-объекте.
"""

import asyncio
import os
import sys
import traceback
import logging
from typing import Optional

from mcp_modules.mcp_core import mcp

# ── Глобальные переменные браузера ──────────────────────────────────────────
_global_agent = None
_global_browser = None
_global_browser_context = None


def _get_env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")


async def _safe_cleanup():
    global _global_browser, _global_browser_context, _global_agent
    try:
        if _global_browser_context:
            await _global_browser_context.close()
    except Exception:
        pass
    try:
        if _global_browser:
            await _global_browser.close()
    except Exception:
        pass
    finally:
        _global_browser = None
        _global_browser_context = None
        _global_agent = None


async def _ensure_browser():
    """Инициализирует браузер и контекст, если ещё не запущены.
    Если задан CHROME_PATH — подключается к существующему Chrome через CDP (порт 9222).
    """
    global _global_browser, _global_browser_context

    from browser_use import BrowserConfig
    from browser_use.browser.context import BrowserContextConfig, BrowserContextWindowSize
    from mcp_browser_use.browser.custom_browser import CustomBrowser

    window_w = int(os.getenv("BROWSER_WINDOW_WIDTH", "1280"))
    window_h = int(os.getenv("BROWSER_WINDOW_HEIGHT", "720"))

    if not _global_browser:
        # Подключаемся к уже открытому Chrome по CDP (порт 9222)
        _global_browser = CustomBrowser(
            config=BrowserConfig(
                cdp_url="http://localhost:9222",
            )
        )

    if not _global_browser_context:
        _global_browser_context = await _global_browser.new_context(
            config=BrowserContextConfig(
                no_viewport=False,
                browser_window_size=BrowserContextWindowSize(width=window_w, height=window_h),
                highlight_elements=False,
            )
        )

    return _global_browser_context


# ── Tools ────────────────────────────────────────────────────────────────────

@mcp.tool()
async def run_browser_agent(task: str, add_infos: str = "") -> str:
    """Запускает AI-агента для выполнения сложной многошаговой задачи в браузере.
    Используй для: авторизации, заполнения форм, скачивания файлов, бронирования, регистрации."""
    global _global_agent, _global_browser, _global_browser_context

    from mcp_browser_use.agent.custom_agent import CustomAgent
    from mcp_browser_use.agent.custom_prompts import CustomAgentMessagePrompt, CustomSystemPrompt
    from mcp_browser_use.browser.custom_browser import CustomBrowser
    from mcp_browser_use.controller.custom_controller import CustomController
    from mcp_browser_use.utils import utils
    from mcp_browser_use.utils.agent_state import AgentState

    agent_state = AgentState()
    agent_state.clear_stop()

    try:
        model_provider = os.getenv("MCP_MODEL_PROVIDER", "ollama")
        model_name = os.getenv("MCP_MODEL_NAME", "qwen2.5vl:3b")
        temperature = float(os.getenv("MCP_TEMPERATURE", "0.7"))
        max_steps = int(os.getenv("MCP_MAX_STEPS", "50"))
        use_vision = _get_env_bool("MCP_USE_VISION", False)
        max_actions_per_step = int(os.getenv("MCP_MAX_ACTIONS_PER_STEP", "5"))
        tool_calling_method = os.getenv("MCP_TOOL_CALLING_METHOD", "auto")

        await _ensure_browser()

        llm = utils.get_llm_model(
            provider=model_provider, model_name=model_name, temperature=temperature
        )

        controller = CustomController()
        _global_agent = CustomAgent(
            task=task,
            add_infos=add_infos,
            use_vision=use_vision,
            llm=llm,
            browser=_global_browser,
            browser_context=_global_browser_context,
            controller=controller,
            system_prompt_class=CustomSystemPrompt,
            agent_prompt_class=CustomAgentMessagePrompt,
            max_actions_per_step=max_actions_per_step,
            agent_state=agent_state,
            tool_calling_method=tool_calling_method,
        )

        history = await _global_agent.run(max_steps=max_steps)
        return history.final_result() or f"Задача завершена. {history}"

    except asyncio.CancelledError:
        return "Задача отменена"
    except Exception as e:
        logging.error(f"run_browser_agent error: {e}\n{traceback.format_exc()}")
        return f"Ошибка выполнения задачи: {e}"
    finally:
        # Сбрасываем только агента — браузер и контекст остаются живыми
        _global_agent = None


@mcp.tool()
async def browser_navigate(url: str) -> str:
    """Открыть URL в браузере."""
    ctx = await _ensure_browser()
    page = await ctx.get_current_page()
    await page.goto(url)
    await page.wait_for_load_state()
    return f"Открыта страница: {url}"


@mcp.tool()
async def browser_get_state() -> str:
    """Получить текущее состояние браузера: URL, заголовок и интерактивные элементы с индексами.
    Вызывай перед browser_click или browser_input_text для получения актуальных индексов."""
    ctx = await _ensure_browser()
    state = await ctx.get_state()

    lines = [
        f"URL: {state.url}",
        f"Title: {state.title}",
        f"Tabs: {len(state.tabs)}",
        "",
        "Интерактивные элементы (используй index с browser_click / browser_input_text):",
    ]

    for idx, elem in state.selector_map.items():
        tag = elem.tag_name
        text = elem.get_all_text_till_next_clickable_element(max_depth=2).strip()
        attrs = elem.attributes or {}
        placeholder = attrs.get("placeholder", "")
        elem_type = attrs.get("type", "")
        desc = text or placeholder or elem_type or tag
        lines.append(f"  [{idx}] <{tag}> {desc[:100]}")

    return "\n".join(lines)


@mcp.tool()
async def browser_click(index: int) -> str:
    """Кликнуть на интерактивный элемент по его индексу. Индексы берутся из browser_get_state."""
    ctx = await _ensure_browser()
    state = await ctx.get_state()

    if index not in state.selector_map:
        return f"Ошибка: элемент [{index}] не найден. Вызови browser_get_state для получения актуальных индексов."

    element_node = state.selector_map[index]
    await ctx._click_element_node(element_node)
    text = element_node.get_all_text_till_next_clickable_element(max_depth=2).strip()
    return f"Клик на [{index}]: {text[:100]}"


@mcp.tool()
async def browser_input_text(index: int, text: str) -> str:
    """Ввести текст в поле ввода по индексу. Индексы берутся из browser_get_state."""
    ctx = await _ensure_browser()
    state = await ctx.get_state()

    if index not in state.selector_map:
        return f"Ошибка: элемент [{index}] не найден. Вызови browser_get_state для получения актуальных индексов."

    element_node = state.selector_map[index]
    await ctx._input_text_element_node(element_node, text)
    return f'Введён текст "{text}" в элемент [{index}]'


@mcp.tool()
async def browser_search_google(query: str) -> str:
    """Поиск в Google по запросу в текущей вкладке."""
    ctx = await _ensure_browser()
    page = await ctx.get_current_page()
    await page.goto(f"https://www.google.com/search?q={query}&udm=14")
    await page.wait_for_load_state()
    return f'Поиск Google: "{query}"'


@mcp.tool()
async def browser_extract_content(include_links: bool = False) -> str:
    """Извлечь текстовый контент текущей страницы. include_links=True — вернуть markdown со ссылками."""
    from main_content_extractor import MainContentExtractor
    ctx = await _ensure_browser()
    page = await ctx.get_current_page()
    output_format = "markdown" if include_links else "text"
    content = MainContentExtractor.extract(
        html=await page.content(),
        output_format=output_format,
    )
    return content or "Контент не удалось извлечь."


@mcp.tool()
async def browser_scroll_down(amount: Optional[int] = None) -> str:
    """Прокрутить страницу вниз. Можно указать количество пикселей."""
    ctx = await _ensure_browser()
    page = await ctx.get_current_page()
    if amount is not None:
        await page.evaluate(f"window.scrollBy(0, {amount});")
        return f"Прокрутка вниз на {amount} px"
    else:
        await page.keyboard.press("PageDown")
        return "Прокрутка вниз на страницу"


@mcp.tool()
async def browser_scroll_up(amount: Optional[int] = None) -> str:
    """Прокрутить страницу вверх. Можно указать количество пикселей."""
    ctx = await _ensure_browser()
    page = await ctx.get_current_page()
    if amount is not None:
        await page.evaluate(f"window.scrollBy(0, -{amount});")
        return f"Прокрутка вверх на {amount} px"
    else:
        await page.keyboard.press("PageUp")
        return "Прокрутка вверх на страницу"


@mcp.tool()
async def browser_go_back() -> str:
    """Перейти назад в истории браузера."""
    ctx = await _ensure_browser()
    await ctx.go_back()
    return "Назад"


@mcp.tool()
async def browser_send_keys(keys: str) -> str:
    """Отправить клавиши или комбинации. Примеры: 'Enter', 'Escape', 'Control+a'."""
    ctx = await _ensure_browser()
    page = await ctx.get_current_page()
    await page.keyboard.press(keys)
    return f"Нажаты клавиши: {keys}"


@mcp.tool()
async def browser_open_tab(url: str) -> str:
    """Открыть URL в новой вкладке браузера."""
    ctx = await _ensure_browser()
    await ctx.create_new_tab(url)
    return f"Открыта новая вкладка: {url}"


@mcp.tool()
async def browser_switch_tab(page_id: int) -> str:
    """Переключиться на вкладку по ID (ID виден в выводе browser_get_state)."""
    ctx = await _ensure_browser()
    await ctx.switch_to_tab(page_id)
    page = await ctx.get_current_page()
    await page.wait_for_load_state()
    return f"Переключено на вкладку {page_id}"


@mcp.tool()
async def browser_close() -> str:
    """Закрыть браузер и освободить все ресурсы."""
    await _safe_cleanup()
    return "Браузер закрыт"
