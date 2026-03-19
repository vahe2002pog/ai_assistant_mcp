import asyncio
from typing import TypedDict, Annotated, List

from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, AIMessage
from langchain_ollama import ChatOllama
from utils import handle_tool_command
from config import formatter_prompt, FORMATTER_MODEL
from langgraph.graph import StateGraph, START, END, add_messages
from langgraph.prebuilt import ToolNode
from langgraph.checkpoint.memory import MemorySaver

LLM_TIMEOUT = 120.0

# ─── Группы инструментов ────────────────────────────────────────────────────

FILE_TOOL_NAMES = {
    "list_directory", "view_cache", "execute_open_file", "open_folder",
    "create_item", "rename_item", "copy_item", "move_file",
    "read_file", "edit_file", "get_file_info", "delete_item",
    "undo_last_action", "open_recycle_bin",
}

BROWSER_TOOL_NAMES = {
    "browser_get_state", "browser_navigate", "browser_click", "browser_input_text",
    "browser_extract_content", "browser_scroll_down", "browser_scroll_up",
    "browser_go_back", "browser_send_keys", "browser_open_tab",
    "browser_switch_tab", "browser_close_tab", "open_url", "browser_search",
}

WEB_SEARCH_TOOL_NAMES = {
    "tavily_search", "tavily_extract", "tavily_crawl", "tavily_map",
}

UI_TOOL_PREFIX = "ui_"

SYSTEM_TOOL_NAMES = {
    "open_app", "control_volume", "control_media", "get_weather",
}

# ─── Промпты ─────────────────────────────────────────────────────────────────

FILE_AGENT_PROMPT = """Ты — специализированный агент управления файлами и папками на Windows.

ПРАВИЛА РАБОТЫ С КЭШЕМ И ID:
- Инструмент list_directory() возвращает элементы с числовыми ID:
    1: Документы
    2: report.txt
- Эти ID ОБЯЗАТЕЛЬНО использовать в последующих командах.
- Правильный порядок:
    1. вызвать list_directory()
    2. получить ID нужного элемента
    3. использовать ID в execute_open_file() или open_folder()
- Запрещено использовать имя файла вместо ID, если ID уже получен.
- Кэш — внутренний механизм. В ответе не упоминай про него.

УДАЛЕНИЕ:
- Для удаления используй delete_item() — файлы перемещаются в корзину.
- Если нужно восстановить — используй undo_last_action().

Выполняй задачу полностью. Отвечай кратко на русском языке: что было сделано и результат."""

BROWSER_AGENT_PROMPT = """Ты — специализированный агент управления браузером через Chrome-расширение.

ПРАВИЛА:
- Перед взаимодействием со страницей всегда вызывай browser_get_state() для получения актуального состояния.
- Для навигации используй browser_navigate(url).
- Для ввода текста: сначала browser_click() на поле, затем browser_input_text().
- При работе с вкладками: browser_open_tab(), browser_switch_tab(), browser_close_tab().
- Для извлечения содержимого страницы используй browser_extract_content().
- После каждого действия проверяй результат через browser_get_state().

Выполняй задачу полностью. Отвечай кратко на русском языке: что было сделано и текущее состояние браузера."""

WEB_SEARCH_AGENT_PROMPT = """Ты — специализированный агент поиска информации в интернете.

ПРАВИЛА:
- После tavily_search ВСЕГДА выполняй tavily_extract для получения актуального содержимого страницы.
- Если берёшь информацию с сайта — ОБЯЗАТЕЛЬНО укажи ссылку на источник.
- Для глубокого изучения сайта используй tavily_crawl.
- Для создания карты сайта используй tavily_map.
- Отвечай только на основе найденных данных, не выдумывай.

Выполняй задачу полностью. Отвечай кратко на русском языке: результат поиска с указанием источника."""

UI_AGENT_PROMPT = """Ты — специализированный агент автоматизации UI Windows-приложений.

ПРАВИЛА:
1. Перед любым действием вызывай ui_inspect_app() для получения полной структуры активного окна.
2. Если нужно окно не на переднем плане — сначала ui_list_windows(), затем ui_inspect_app(handle=<handle>).
3. Никогда не угадывай handle элемента — всегда бери из результатов ui_inspect_app() или ui_find_control().
4. Для получения скриншота используй ui_screenshot().

ВСТАВКА ТЕКСТА (КРИТИЧНО — 2 шага):
    Шаг 1: ui_clipboard_set(text="твой текст")
    Шаг 2: ui_paste_text(handle=<handle>)
Никогда не используй ui_send_keys() для длинного текста.

КЛИКИ:
- Предпочитай клик по handle элемента, а не по координатам.
- Координаты используй только если handle недоступен (handle=0, Modern UI).

Выполняй задачу полностью. Отвечай кратко на русском языке: какие действия выполнены и результат."""

SYSTEM_TOOL_NAMES = SYSTEM_TOOL_NAMES | {"list_apps"}

SYSTEM_AGENT_PROMPT = """Ты — специализированный агент системных операций Windows.

ВОЗМОЖНОСТИ:
- Запуск приложений: open_app(app_name) — ищет по имени в базе установленных приложений.
  Примеры: open_app("chrome"), open_app("telegram"), open_app("word").
  Если приложение не найдено — используй list_apps(query) для поиска похожих.
- Громкость: control_volume(action) — действия: "up", "down", "mute", "unmute".
- Медиа: control_media(action) — действия: "play_pause", "next", "prev", "stop".
- Погода: get_weather(city) — передавай название города на английском.

Выполняй задачу полностью. Отвечай кратко на русском языке: что было сделано и результат."""

MAIN_AGENT_PROMPT = """Ты — голосовой ассистент управления ПК на Windows. Всегда общайся и давай задания суб-агентам ТОЛЬКО на русском языке.

Твоя задача — полностью выполнить запрос пользователя, вызывая суб-агентов столько раз, сколько нужно.

СУПЕРАГЕНТЫ:
- file_agent       — работа с файлами и папками
- browser_agent    — управление браузером
- web_search_agent — поиск информации в интернете
- ui_agent         — автоматизация интерфейса Windows-приложений
- system_agent     — запуск приложений, громкость, медиа, погода

ПРАВИЛА ВЫПОЛНЕНИЯ:
- НИКОГДА не останавливайся после одного суб-агента, если задача ещё не выполнена полностью.
- Вызывай суб-агентов последовательно: результат одного передавай следующему.
- Пример: "найди в интернете и вставь в ворд" → сначала web_search_agent (найти), затем ui_agent (вставить).
- Передавай следующему суб-агенту полный контекст: что уже сделано и что нужно сделать.
- Заканчивай работу и отвечай пользователю ТОЛЬКО когда задача полностью выполнена.
- Не задавай уточняющих вопросов — действуй самостоятельно."""

# ─── Утилиты ────────────────────────────────────────────────────────────────

def _filter_tools(all_tools, names: set = None, prefix: str = None):
    result = []
    for t in all_tools:
        if names and t.name in names:
            result.append(t)
        elif prefix and t.name.startswith(prefix):
            result.append(t)
    return result


def _build_subagent_graph(system_prompt: str, tools: list, base_llm: ChatOllama):
    """Строит специализированный граф суб-агента со своим промптом и инструментами."""
    llm = base_llm.model_copy(update={"system": system_prompt})

    class _State(TypedDict):
        messages: Annotated[List, add_messages]

    async def executor_node(state):
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(llm.bind_tools(tools).invoke, state["messages"]),
                timeout=LLM_TIMEOUT,
            )
            return {"messages": [result]}
        except asyncio.TimeoutError:
            return {"messages": [AIMessage(content="Таймаут: LLM не ответил вовремя.")]}

    wf = StateGraph(_State)
    wf.add_node("executor", executor_node)

    if tools:
        wf.add_node("tools", ToolNode(tools, handle_tool_errors=True))
        wf.add_edge(START, "executor")
        wf.add_conditional_edges(
            "executor",
            lambda state: "tools" if state["messages"][-1].tool_calls else END,
        )
        wf.add_edge("tools", "executor")
    else:
        wf.add_edge(START, "executor")
        wf.add_edge("executor", END)

    return wf.compile()


# ─── Публичный API ───────────────────────────────────────────────────────────

def create_main_agent(all_tools: list, llm: ChatOllama):
    """Создаёт главный агент с 5 суб-агентами через @tool обёртки."""
    file_tools    = _filter_tools(all_tools, names=FILE_TOOL_NAMES)
    browser_tools = _filter_tools(all_tools, names=BROWSER_TOOL_NAMES)
    search_tools  = _filter_tools(all_tools, names=WEB_SEARCH_TOOL_NAMES)
    ui_tools      = _filter_tools(all_tools, prefix=UI_TOOL_PREFIX)
    system_tools  = _filter_tools(all_tools, names=SYSTEM_TOOL_NAMES)

    file_graph    = _build_subagent_graph(FILE_AGENT_PROMPT,       file_tools,    llm)
    browser_graph = _build_subagent_graph(BROWSER_AGENT_PROMPT,    browser_tools, llm)
    search_graph  = _build_subagent_graph(WEB_SEARCH_AGENT_PROMPT, search_tools,  llm)
    ui_graph      = _build_subagent_graph(UI_AGENT_PROMPT,         ui_tools,      llm)
    system_graph  = _build_subagent_graph(SYSTEM_AGENT_PROMPT,     system_tools,  llm)

    async def _run_subagent(graph, task: str) -> str:
        """Запускает суб-агент и обрабатывает команды из результатов MCP-тулов."""
        result = await graph.ainvoke({"messages": [HumanMessage(content=task)]})
        for msg in result["messages"]:
            if hasattr(msg, "type") and msg.type == "tool":
                content = str(msg.content)
                await handle_tool_command(content)
        return result["messages"][-1].content

    @tool
    async def file_agent(task: str) -> str:
        """Управление файлами и папками: просмотр директорий, чтение, создание, редактирование, копирование, перемещение, удаление."""
        return await _run_subagent(file_graph, task)

    @tool
    async def browser_agent(task: str) -> str:
        """Управление браузером: навигация по URL, клики, ввод текста, скролл, работа с вкладками, получение состояния страниц."""
        return await _run_subagent(browser_graph, task)

    @tool
    async def web_search_agent(task: str) -> str:
        """Поиск актуальной информации в интернете, извлечение и анализ содержимого веб-страниц через Tavily."""
        return await _run_subagent(search_graph, task)

    @tool
    async def ui_agent(task: str) -> str:
        """Автоматизация UI Windows-приложений: поиск окон и элементов интерфейса, клики, ввод текста через буфер обмена, скриншоты."""
        return await _run_subagent(ui_graph, task)

    @tool
    async def system_agent(task: str) -> str:
        """Системные операции: запуск приложений, управление громкостью, управление медиа-плеером, получение прогноза погоды."""
        return await _run_subagent(system_graph, task)

    subagent_tools = [file_agent, browser_agent, web_search_agent, ui_agent, system_agent]
    main_llm = llm.model_copy(update={"system": MAIN_AGENT_PROMPT})
    fmt_llm = ChatOllama(model=FORMATTER_MODEL, temperature=0)

    class _MainState(TypedDict):
        messages: Annotated[List, add_messages]

    async def main_executor(state):
        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(main_llm.bind_tools(subagent_tools).invoke, state["messages"]),
                timeout=LLM_TIMEOUT,
            )
            return {"messages": [result]}
        except asyncio.TimeoutError:
            return {"messages": [AIMessage(content="Таймаут: LLM не ответил вовремя.")]}

    async def formatter_node(state):
        user_message = None
        for msg in reversed(state["messages"]):
            if msg.type == "human":
                user_message = msg.content
                break

        assistant_message = state["messages"][-1].content

        prompt = f"""{formatter_prompt}

Запрос пользователя:
{user_message}

Ответ ассистента:
{assistant_message}"""

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(fmt_llm.invoke, [HumanMessage(content=prompt)]),
                timeout=LLM_TIMEOUT,
            )
            return {"messages": [result]}
        except asyncio.TimeoutError:
            return {"messages": [AIMessage(content=assistant_message)]}

    wf = StateGraph(_MainState)
    wf.add_node("executor", main_executor)
    wf.add_node("tools", ToolNode(subagent_tools, handle_tool_errors=True))
    wf.add_node("formatter", formatter_node)
    wf.add_edge(START, "executor")
    wf.add_conditional_edges(
        "executor",
        lambda state: "tools" if state["messages"][-1].tool_calls else "formatter",
    )
    wf.add_edge("tools", "executor")
    wf.add_edge("formatter", END)

    return wf.compile(checkpointer=MemorySaver())
