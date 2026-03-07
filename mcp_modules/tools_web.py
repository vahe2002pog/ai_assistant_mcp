from duckduckgo_search import DDGS
from .mcp_core import mcp
import os
import urllib.parse

@mcp.tool
def web_search(query: str, max_results: int = 5) -> str:
    """
    Выполняет поиск в интернете по запросу и возвращает список результатов.
    Используй этот инструмент, чтобы отвечать на вопросы о текущих событиях, 
    погоде, новостях или фактах, которых ты не знаешь.
    
    Args:
        query (str): Поисковый запрос.
        max_results (int): Количество результатов (по умолчанию 5, максимум 10).
        
    Returns:
        str: Текст с результатами поиска (заголовок, ссылка и краткое описание).
    """
    try:
        results = []
        # Используем контекстный менеджер DDGS
        with DDGS() as ddgs:
            # text() ищет обычные веб-страницы
            for r in ddgs.text(query, max_results=max_results):
                title = r.get("title", "Нет заголовка")
                href = r.get("href", "")
                body = r.get("body", "Нет описания")
                
                results.append(f"📌 {title}\n🔗 {href}\n📝 {body}\n")
                
        if not results:
            return f"По запросу '{query}' ничего не найдено."
            
        return f"Результаты поиска по запросу '{query}':\n\n" + "\n".join(results)
        
    except Exception as e:
        return f"Ошибка при поиске в интернете: {e}"


@mcp.tool
def open_url(url: str) -> str:
    """
    Открывает указанный URL-адрес или веб-сайт в браузере пользователя по умолчанию.
    
    Используй этот инструмент, если пользователь просит:
    - "Открой ютуб"
    - "Зайди на википедию"
    - "Покажи мне сайт github.com"
    
    Args:
        url (str): Полный URL-адрес (например, 'https://youtube.com', 'https://ya.ru') 
                   или просто домен ('vk.com').
                   
    Returns:
        str: Сообщение о том, что команда на открытие отправлена.
    """
    # Небольшая санитария: если агент передал просто "youtube.com", добавляем https://
    clean_url = url.strip()
    if not clean_url.startswith(('http://', 'https://')):
        clean_url = f"https://{clean_url}"
        
    # Базовая валидация URL, чтобы убедиться, что это похоже на ссылку
    parsed = urllib.parse.urlparse(clean_url)
    if not parsed.netloc:
        return f"Ошибка: '{url}' не похоже на корректный веб-адрес."

    # Возвращаем СПЕЦИАЛЬНУЮ команду, которую перехватит main.py (как с файлами)
    return f"__OPEN_URL_COMMAND__:{clean_url}"


@mcp.tool
def browser_search(query: str) -> str:
    """
    Запускает поиск в системном браузере и поисковике по умолчанию (Google).

    Этот инструмент полезен, когда DuckDuckGo в `web_search` вернул неудовлетворительный
    ответ и нужно показать полные результаты поиска непосредственно пользователю.

    Args:
        query (str): Строка поиска, например "какая погода" или "openai gpt".

    Returns:
        str: Специальная команда для main.py, которая откроет поиск в браузере.
    """
    if not isinstance(query, str) or not query.strip():
        return "Ошибка: запрос должен быть непустой строкой."
    # Используем Google как основной поисковик по умолчанию
    url = "https://www.google.com/search?q=" + urllib.parse.quote(query)
    return f"__OPEN_URL_COMMAND__:{url}"
