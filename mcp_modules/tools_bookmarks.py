"""
Инструменты для работы с закладками браузеров.
"""
from database import bookmarks_search, bookmarks_count, bookmarks_list_browsers
from .mcp_core import mcp


@mcp.tool
def search_bookmarks(query: str) -> str:
    """
    Поиск закладок браузеров по названию или URL.

    Args:
        query: Поисковый запрос (название страницы, домен, ключевые слова).

    Returns:
        str: Список найденных закладок с URL и браузером.
    """
    matches = bookmarks_search(query)
    if not matches:
        return f"Закладки по запросу '{query}' не найдены."

    lines = [f"Найдено закладок: {len(matches)}"]
    for title, url, browser, folder in matches:
        folder_str = f" [{folder}]" if folder else ""
        lines.append(f"  - {title}{folder_str} ({browser})\n    {url}")
    return "\n".join(lines)


@mcp.tool
def open_bookmark(query: str) -> str:
    """
    Открывает закладку браузера по названию или URL в браузере по умолчанию.

    Args:
        query: Название страницы или часть URL.

    Returns:
        str: Команда на открытие URL или список совпадений.
    """
    matches = bookmarks_search(query, limit=5)
    if not matches:
        return f"Закладка '{query}' не найдена."

    title, url, browser, folder = matches[0]

    # Точное совпадение по названию — открываем сразу
    if len(matches) == 1 or title.lower() == query.lower():
        return f"__OPEN_URL_COMMAND__:{url}"

    # Несколько вариантов — выбираем первый, если запрос очень точен
    for t, u, b, f in matches:
        if t.lower() == query.lower() or u.lower() == query.lower():
            return f"__OPEN_URL_COMMAND__:{u}"

    # Показываем варианты
    lines = [f"Найдено несколько закладок по запросу '{query}':"]
    for t, u, b, f in matches:
        folder_str = f" [{f}]" if f else ""
        lines.append(f"  - {t}{folder_str} ({b}): {u}")
    lines.append("Уточни название закладки.")
    return "\n".join(lines)


@mcp.tool
def list_bookmarks_browsers() -> str:
    """
    Показывает список браузеров с количеством сохранённых закладок.

    Returns:
        str: Статистика закладок по браузерам.
    """
    total = bookmarks_count()
    if total == 0:
        return "База закладок пуста."

    browsers = bookmarks_list_browsers()
    lines = [f"Всего закладок: {total}"]
    for browser, cnt in browsers:
        lines.append(f"  - {browser}: {cnt}")
    return "\n".join(lines)
