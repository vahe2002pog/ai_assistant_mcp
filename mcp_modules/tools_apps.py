"""
Инструменты для запуска приложений.
"""
from database import apps_search
from .mcp_core import mcp


@mcp.tool
def open_app(app_name: str) -> str:
    """
    Запускает приложение по имени. Ищет в базе установленных приложений.

    Args:
        app_name: Имя приложения на любом языке.
                  Примеры: 'chrome', 'telegram', 'блокнот', 'notepad', 'word', 'vscode'.

    Returns:
        str: Команда на открытие или список похожих приложений.
    """
    matches = apps_search(app_name)

    if not matches:
        return f"Приложение '{app_name}' не найдено в базе."

    if len(matches) == 1:
        name, path = matches[0]
        return f"__OPEN_APP_COMMAND__:{path}"

    # Несколько совпадений — точное совпадение имени
    for name, path in matches:
        if name.lower() == app_name.lower():
            return f"__OPEN_APP_COMMAND__:{path}"

    # Показываем варианты
    lines = [f"Найдено несколько приложений по запросу '{app_name}':"]
    for name, path in matches:
        lines.append(f"  - {name} ({path})")
    lines.append("Уточни название приложения.")
    return "\n".join(lines)


@mcp.tool
def list_apps(query: str = "") -> str:
    """
    Поиск приложений в базе установленных программ.

    Args:
        query: Поисковый запрос (подстрока имени). Пустая строка — все приложения.

    Returns:
        str: Список найденных приложений с путями.
    """
    if query:
        matches = apps_search(query)
    else:
        from database import apps_list_all
        matches = apps_list_all()

    if not matches:
        return f"Приложения по запросу '{query}' не найдены." if query else "База приложений пуста."

    lines = [f"Найдено приложений: {len(matches)}"]
    for name, path in matches:
        lines.append(f"  - {name}: {path}")
    if len(matches) > 20:
        lines.append(f"  ... и ещё {len(matches) - 20}")
    return "\n".join(lines)
