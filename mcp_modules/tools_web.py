from tavily import TavilyClient
from .mcp_core import mcp
import os
import threading
import urllib.parse
from config import TAVILY_API_KEY

# Singleton-клиент (иначе на каждый вызов — новый HTTP-клиент и новый пул).
_CLIENT = None
_CLIENT_LOCK = threading.Lock()
# Глобальный таймаут для всех tavily-запросов (сек).
_TAVILY_TIMEOUT = 15


def _get_client():
    global _CLIENT
    if _CLIENT is not None:
        return _CLIENT
    with _CLIENT_LOCK:
        if _CLIENT is None:
            _CLIENT = TavilyClient(api_key=TAVILY_API_KEY)
    return _CLIENT


@mcp.tool
def tavily_search(query: str, max_results: int = 3, search_depth: str = "basic") -> str:
    """
    Выполняет поисковый запрос в интернете и возвращает релевантные, сжатые результаты,
    часто с фрагментами текста и ссылками.

    Важно: обычно сниппетов из результата достаточно для ответа. Вызывай tavily_extract
    только если сниппетов явно не хватает для конкретного факта.

    Args:
        query (str): Поисковый запрос.
        max_results (int): Количество результатов (по умолчанию 3, максимум 10).
        search_depth (str): Глубина поиска:
            "basic"    — быстрый поиск (1–2 сек, по умолчанию).
            "advanced" — глубокий поиск с суммаризацией (5–15 сек, использовать только
                         если "basic" не дал релевантных сниппетов).

    Returns:
        str: Текст с результатами поиска.
    """
    print(f"Вызван tavily_search с query: {query}, max_results: {max_results}, search_depth: {search_depth}")
    try:
        if not TAVILY_API_KEY:
            return "Ошибка: TAVILY_API_KEY не установлен."

        client = _get_client()
        response = client.search(
            query=query,
            max_results=max_results,
            search_depth=search_depth,
            timeout=_TAVILY_TIMEOUT,
        )
        
        results = []
        for r in response.get("results", []):
            title = r.get("title", "Нет заголовка")
            href = r.get("url", "")
            body = r.get("content", "Нет описания")
            score = r.get("score", 0)
            
            results.append(f"📌 {title} (Score: {score})\n🔗 {href}\n📝 {body}\n")
            
        if not results:
            return f"По запросу '{query}' ничего не найдено."
            
        return f"Результаты поиска Tavily по запросу '{query}':\n\n" + "\n".join(results)
        
    except Exception as e:
        return f"Ошибка при поиске Tavily: {e}"


@mcp.tool
def tavily_extract(urls: list[str]) -> str:
    """
    Этот инструмент позволяет получить полное содержимое одного или нескольких указанных URL-адресов, 
    возвращая чистый текст или разметку Markdown с веб-страниц. Это инструмент для прямого веб-скрейпинга.
    
    Args:
        urls (list[str]): Список URL-адресов для извлечения.
        
    Returns:
        str: Извлеченный контент в формате Markdown.
    """
    print(f"Вызван tavily_extract с urls: {urls}")
    try:
        if not TAVILY_API_KEY:
            return "Ошибка: TAVILY_API_KEY не установлен."
        
        client = _get_client()
        response = client.extract(urls=urls, timeout=_TAVILY_TIMEOUT)
        
        results = []
        for item in response.get("results", []):
            url = item.get("url", "")
            content = item.get("content", "Нет контента")
            results.append(f"🔗 {url}\n📄 {content}\n")
            
        if not results:
            return "Не удалось извлечь контент."

        return "Извлеченный контент:\n\n" + "\n".join(results)
    except Exception as e:
        return f"Ошибка при извлечении: {e}"


@mcp.tool
def tavily_crawl(url: str, max_requests_per_minute: int = 10) -> str:
    """
    Более мощный веб-сканер на основе графов, который систематически исследует весь веб-сайт 
    (например, сайт документации или базу знаний), переходя по ссылкам и извлекая контент параллельно 
    для создания исчерпывающей карты сайта или набора данных.
    
    Args:
        url (str): URL сайта для сканирования.
        max_requests_per_minute (int): Максимальное количество запросов в минуту (по умолчанию 10).
        
    Returns:
        str: Карта сайта с извлеченным контентом.
    """
    print(f"Вызван tavily_crawl с url: {url}, max_requests_per_minute: {max_requests_per_minute}")
    try:
        if not TAVILY_API_KEY:
            return "Ошибка: TAVILY_API_KEY не установлен."
        
        client = _get_client()
        response = client.crawl(
            url=url,
            max_requests_per_minute=max_requests_per_minute,
            timeout=_TAVILY_TIMEOUT * 4,
        )
        
        results = []
        for item in response.get("results", []):
            page_url = item.get("url", "")
            content = item.get("content", "Нет контента")
            results.append(f"🔗 {page_url}\n📄 {content[:500]}...\n")  # Ограничим для краткости
            
        if not results:
            return "Не удалось просканировать сайт."
            
        return f"Карта сайта {url}:\n\n" + "\n".join(results)
        
    except Exception as e:
        return f"Ошибка при сканировании: {e}"


@mcp.tool
def tavily_map(url: str, max_pages: int = 100) -> str:
    """
    Программа осуществляет обход веб-сайтов для создания структурированной карты содержимого сайта 
    с целью интеллектуального поиска.
    
    Args:
        url (str): URL сайта для создания карты.
        max_pages (int): Максимальное количество страниц (по умолчанию 100).
        
    Returns:
        str: Структурированная карта сайта.
    """
    print(f"Вызван tavily_map с url: {url}, max_pages: {max_pages}")
    try:
        if not TAVILY_API_KEY:
            return "Ошибка: TAVILY_API_KEY не установлен."
        
        client = _get_client()
        response = client.map(url=url, max_pages=max_pages, timeout=_TAVILY_TIMEOUT * 2)
        
        results = []
        for item in response.get("results", []):
            page_url = item.get("url", "")
            title = item.get("title", "Нет заголовка")
            results.append(f"📌 {title}\n🔗 {page_url}\n")
            
        if not results:
            return "Не удалось создать карту сайта."
            
        return f"Карта сайта {url}:\n\n" + "\n".join(results)
        
    except Exception as e:
        return f"Ошибка при создании карты: {e}"


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
