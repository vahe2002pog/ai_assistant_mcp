from __future__ import annotations

import subprocess
import sys
import threading
import urllib.parse
from typing import Any

from .mcp_core import mcp


_DEFAULT_TIMEOUT = 15
_TEXT_LIMIT = 8000
_IGNORE_TAGS = ("script", "style", "nav", "footer", "noscript", "aside")

# Браузеры Scrapling (Playwright) ставятся отдельной командой `scrapling install`.
# Делаем это лениво, один раз за процесс, если фетч упал из-за отсутствия браузера.
_SCRAPLING_BOOTSTRAPPED = False
_SCRAPLING_LOCK = threading.Lock()


def _looks_like_missing_browser(err: BaseException) -> bool:
    msg = (str(err) or "").lower()
    return any(s in msg for s in (
        "executable doesn't exist",
        "playwright install",
        "browsertype.launch",
        "looks like playwright",
        "no such file",
    ))


def _missing_module_name(err: BaseException) -> str | None:
    """Возвращает имя пакета из 'No module named X', иначе None."""
    if isinstance(err, ModuleNotFoundError) and err.name:
        return err.name
    msg = str(err) or ""
    marker = "No module named "
    if marker in msg:
        tail = msg.split(marker, 1)[1].strip().strip("'\"")
        return tail.split(".")[0] if tail else None
    return None


# Какие пакеты ставить под какие отсутствующие модули.
_MODULE_TO_PIP = {
    "curl_cffi": "curl_cffi",
    "playwright": "playwright",
    "lxml": "lxml",
    "browserforge": "browserforge",
    "tldextract": "tldextract",
    "msgspec": "msgspec",
    "camoufox": "camoufox",
}


def _ensure_pip_module(mod: str) -> tuple[bool, str]:
    pip_name = _MODULE_TO_PIP.get(mod, mod)
    print(f"[web] pip install {pip_name} — ставлю недостающий пакет…", flush=True)
    return _run([sys.executable, "-m", "pip", "install", pip_name])


def _run(cmd: list[str], timeout: int = 600) -> tuple[bool, str]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except Exception as ex:
        return False, f"запуск {cmd!r} не удался: {ex}"
    return proc.returncode == 0, (proc.stdout or "") + (proc.stderr or "")


def _ensure_playwright_module() -> tuple[bool, str]:
    """Гарантирует, что пакет playwright установлен в текущий venv."""
    try:
        import playwright  # noqa: F401
        return True, "already installed"
    except ImportError:
        pass
    print("[web] pip install playwright — ставлю недостающий пакет…", flush=True)
    return _run([sys.executable, "-m", "pip", "install", "playwright"])


def _bootstrap_scrapling() -> tuple[bool, str]:
    """Один раз за процесс ставит playwright + браузеры. Возвращает (ok, log)."""
    global _SCRAPLING_BOOTSTRAPPED
    with _SCRAPLING_LOCK:
        if _SCRAPLING_BOOTSTRAPPED:
            return True, "already bootstrapped"

        ok, log_pw = _ensure_playwright_module()
        if not ok:
            return False, f"playwright pip install: {log_pw}"

        print("[web] scrapling install — устанавливаю браузеры (одноразово)…", flush=True)
        ok, log_sc = _run([sys.executable, "-m", "scrapling", "install"])
        if not ok:
            # Фолбэк: ставим браузеры напрямую через playwright.
            print("[web] scrapling install упал, пробую `playwright install chromium`…", flush=True)
            ok, log_sc = _run([sys.executable, "-m", "playwright", "install", "chromium"])

        if ok:
            _SCRAPLING_BOOTSTRAPPED = True
        return ok, log_pw + "\n" + log_sc


_DDG_OPERATORS = ("site:", "intitle:", "inurl:", "filetype:", " OR ", " AND ", "-", '"')


def _build_ddg_query(query: str, exact_phrase: bool) -> str:
    """Если exact_phrase=True и в запросе ещё нет операторов/кавычек — оборачиваем
    его в "...", чтобы DDG требовал точного вхождения фразы. Это сильно снижает
    мусорные совпадения по одному слову (словари, омонимы, неподходящие сайты)."""
    q = (query or "").strip()
    if not exact_phrase or not q:
        return q
    if any(op in q for op in _DDG_OPERATORS):
        return q  # пользователь/LLM уже задали операторы — не вмешиваемся
    if len(q.split()) < 2:
        return q  # одно слово в кавычках — бессмысленно
    return f'"{q}"'


def _ddg_search(query: str, max_results: int, region: str = "wt-wt",
                safesearch: str = "moderate") -> list[dict[str, Any]]:
    """Возвращает [{title, href, body}, ...] из DuckDuckGo (через пакет ddgs).

    В fallback пробуем устаревший duckduckgo_search — но его Bing-фолбэк
    отдаёт мусор, так что основная ставка на ddgs."""
    try:
        from ddgs import DDGS  # новый пакет (ранее duckduckgo_search)
    except ImportError:
        from duckduckgo_search import DDGS  # type: ignore
    return list(DDGS().text(query, max_results=max_results,
                            region=region, safesearch=safesearch) or [])


def _do_scrape(url: str, timeout: int, stealthy: bool):
    if stealthy:
        from scrapling.fetchers import StealthyFetcher
        return StealthyFetcher.fetch(url, headless=True, network_idle=True, timeout=timeout * 1000)
    from scrapling.fetchers import Fetcher
    return Fetcher.get(url, timeout=timeout)


_INSTALLED_MODS: set[str] = set()


def _scrape_text(url: str, timeout: int = _DEFAULT_TIMEOUT, stealthy: bool = False) -> str:
    """Скачивает страницу через Scrapling и возвращает чистый текст.

    Лениво доставляет недостающие зависимости (curl_cffi, playwright, lxml)
    и playwright-браузеры — каждую максимум один раз за процесс.
    """
    for _ in range(4):
        try:
            page = _do_scrape(url, timeout, stealthy)
            return page.get_all_text(ignore_tags=_IGNORE_TAGS) or ""
        except Exception as ex:
            mod = _missing_module_name(ex)
            if mod and mod in _MODULE_TO_PIP and mod not in _INSTALLED_MODS:
                ok, log = _ensure_pip_module(mod)
                _INSTALLED_MODS.add(mod)
                if not ok:
                    raise RuntimeError(f"pip install {mod} не удался: {log.strip()[:400]}") from ex
                continue
            if stealthy and not _SCRAPLING_BOOTSTRAPPED and _looks_like_missing_browser(ex):
                ok, log = _bootstrap_scrapling()
                if not ok:
                    raise RuntimeError(f"scrapling install не удался: {log.strip()[:400]}") from ex
                continue
            raise
    raise RuntimeError("scrape: исчерпан лимит автоустановок зависимостей")


@mcp.tool
def web_search(query: str, max_results: int = 3, fetch_pages: bool = True,
               char_limit: int = _TEXT_LIMIT,
               exact_phrase: bool = True,
               region: str = "wt-wt", safesearch: str = "moderate") -> str:
    """
    Ищет информацию в интернете: DuckDuckGo выдаёт список URL и сниппетов,
    Scrapling загружает каждую страницу и извлекает чистый текст.

    Используй для любых вопросов, требующих актуальных данных (новости, цены,
    курсы, факты). Сниппеты обычно достаточны; полный текст подгружается, если
    fetch_pages=True (по умолчанию).

    exact_phrase=True (по умолчанию) оборачивает многословный запрос в кавычки —
    DDG требует точного вхождения фразы и реже выдаёт нерелевантные страницы
    (словари, омонимы). Отключи только если ищешь по отдельным ключевым словам.

    region: 'wt-wt' (мир), 'ru-ru', 'us-en' и т.п. — задаёт регион выдачи.

    Args:
        query (str): Поисковый запрос.
        max_results (int): Сколько результатов вернуть (по умолчанию 3, рекомендуется ≤5).
        fetch_pages (bool): Если True — догружает полный текст каждой страницы.
                            Если False — возвращает только сниппеты DDG (быстро).

    Returns:
        str: Текст с результатами (заголовок, URL, сниппет, при fetch_pages — текст страницы).
    """
    ddg_query = _build_ddg_query(query, exact_phrase)
    print(f"Вызван web_search query={ddg_query!r} max_results={max_results} "
          f"fetch_pages={fetch_pages} region={region}")
    try:
        hits = _ddg_search(ddg_query, max_results, region=region, safesearch=safesearch)
    except Exception as e:
        return f"Ошибка DuckDuckGo: {e}"

    # Фолбэк: если точная фраза ничего не дала — повторяем без кавычек.
    if not hits and exact_phrase and ddg_query != query:
        try:
            hits = _ddg_search(query, max_results, region=region, safesearch=safesearch)
        except Exception:
            pass

    if not hits:
        return f"По запросу '{query}' ничего не найдено."

    raw_results: list[dict[str, Any]] = []
    blocks: list[str] = []
    for r in hits:
        title = r.get("title") or "Без заголовка"
        href = r.get("href") or r.get("url") or ""
        snippet = r.get("body") or ""

        full_text = ""
        if fetch_pages and href:
            try:
                full_text = _scrape_text(href)[:char_limit]
            except Exception as ex:
                full_text = f"(не удалось загрузить: {ex})"

        raw_results.append({
            "title": title,
            "url": href,
            "content": snippet,
            "full_text": full_text,
        })

        block = f"📌 {title}\n🔗 {href}\n📝 {snippet}"
        if full_text:
            block += f"\n📄 {full_text}"
        blocks.append(block + "\n")

    try:
        from ui_automation.rag.web_search_manager import save_search_results_async
        save_search_results_async(query, raw_results, source="web_search")
    except Exception:
        pass

    return f"Результаты поиска по запросу '{query}':\n\n" + "\n".join(blocks)


@mcp.tool
def web_extract(urls: list[str], stealthy: bool = False,
                char_limit: int = _TEXT_LIMIT) -> str:
    """
    Загружает указанные URL и возвращает их чистый текст (без скриптов/nav/footer).
    Прямой веб-скрейпинг через Scrapling.

    Args:
        urls (list[str]): Список URL для извлечения.
        stealthy (bool): Если True — использует StealthyFetcher (Playwright со stealth)
                         для сайтов с антибот-защитой (Cloudflare и т.п.). Медленнее.

    Returns:
        str: Извлечённый текст по каждому URL.
    """
    print(f"Вызван web_extract urls={urls} stealthy={stealthy}")
    if not urls:
        return "Ошибка: список URL пуст."

    raw_items: list[dict[str, Any]] = []
    blocks: list[str] = []
    for url in urls:
        try:
            text = _scrape_text(url, stealthy=stealthy)
        except Exception as ex:
            blocks.append(f"🔗 {url}\n❌ Ошибка: {ex}\n")
            continue
        raw_items.append({"url": url, "content": text})
        blocks.append(f"🔗 {url}\n📄 {text[:char_limit]}\n")

    try:
        from ui_automation.rag.web_search_manager import save_extract_results
        save_extract_results(urls, raw_items)
    except Exception:
        pass

    if not blocks:
        return "Не удалось извлечь контент."
    return "Извлечённый контент:\n\n" + "\n".join(blocks)


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
    clean_url = url.strip()
    if not clean_url.startswith(('http://', 'https://')):
        clean_url = f"https://{clean_url}"

    parsed = urllib.parse.urlparse(clean_url)
    if not parsed.netloc:
        return f"Ошибка: '{url}' не похоже на корректный веб-адрес."

    return f"__OPEN_URL_COMMAND__:{clean_url}"