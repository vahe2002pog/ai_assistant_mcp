"""
Сканер закладок браузеров.
Поддерживает Chrome, Edge, Brave, Opera, Yandex Browser, Firefox.
Генерирует базовые и LLM-алиасы (аналогично app_scanner.py).
"""
import os
import re
import json
import glob as _glob
from database import (
    bookmarks_clear, bookmarks_put_many, bookmarks_count,
    bookmarks_add_aliases_bulk,
)

BASE_DIR = os.path.dirname(__file__)
_LLM_CACHE_PATH = os.path.join(BASE_DIR, "bookmark_aliases_cache.json")

_LOCAL = os.environ.get("LOCALAPPDATA", "")
_APPDATA = os.environ.get("APPDATA", "")

# Пути к User Data Chromium-браузеров
_CHROMIUM_PATHS = [
    ("Chrome",          os.path.join(_LOCAL,   r"Google\Chrome\User Data")),
    ("Edge",            os.path.join(_LOCAL,   r"Microsoft\Edge\User Data")),
    ("Brave",           os.path.join(_LOCAL,   r"BraveSoftware\Brave-Browser\User Data")),
    ("Opera",           os.path.join(_APPDATA, r"Opera Software\Opera Stable")),
    ("Opera GX",        os.path.join(_APPDATA, r"Opera Software\Opera GX Stable")),
    ("Yandex Browser",  os.path.join(_LOCAL,   r"Yandex\YandexBrowser\User Data")),
    ("Vivaldi",         os.path.join(_LOCAL,   r"Vivaldi\User Data")),
]

_FIREFOX_PROFILES = os.path.join(_APPDATA, r"Mozilla\Firefox\Profiles")

# Известные алиасы для популярных доменов
_KNOWN_DOMAIN_ALIASES: dict[str, list[str]] = {
    "youtube":    ["ютуб", "видео", "youtube"],
    "github":     ["гитхаб", "гит", "git", "репозиторий"],
    "google":     ["гугл", "поиск"],
    "mail":       ["почта", "майл"],
    "yandex":     ["яндекс"],
    "vk":         ["вк", "вконтакте"],
    "ok":         ["одноклассники"],
    "twitter":    ["твиттер", "x"],
    "x":          ["твиттер", "twitter"],
    "instagram":  ["инстаграм", "инста"],
    "facebook":   ["фейсбук", "фб"],
    "telegram":   ["телеграм", "тг"],
    "reddit":     ["реддит"],
    "wikipedia":  ["вики", "wikipedia", "энциклопедия"],
    "netflix":    ["нетфликс"],
    "spotify":    ["спотифай"],
    "amazon":     ["амазон"],
    "aliexpress": ["алиэкспресс", "али"],
    "ozon":       ["озон"],
    "wildberries":["вб", "wildberries"],
    "avito":      ["авито"],
    "habr":       ["хабр", "хабрахабр"],
    "stackoverflow": ["стековерфлоу", "стэковерфлоу", "so"],
    "openai":     ["опенаи", "chatgpt"],
    "chatgpt":    ["чатгпт", "гпт", "openai"],
    "claude":     ["клод"],
    "notion":     ["ноушн", "нотион"],
    "figma":      ["фигма"],
    "trello":     ["трелло"],
    "jira":       ["джира"],
    "confluence": ["конфлюенс"],
    "gitlab":     ["гитлаб"],
    "bitbucket":  ["битбакет"],
    "npm":        ["нпм", "node packages"],
    "pypi":       ["пипай", "python packages"],
    "docs.google":["гугл докс", "google docs"],
    "drive.google":["гугл диск", "google drive"],
    "maps.google": ["гугл карты", "google maps"],
    "translate.google": ["гугл переводчик", "переводчик"],
}


def _load_llm_cache() -> dict:
    if os.path.exists(_LLM_CACHE_PATH):
        try:
            with open(_LLM_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_llm_cache(cache: dict) -> None:
    with open(_LLM_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# ── Парсеры браузеров ─────────────────────────────────────────────────────────

def _parse_chromium_node(node: dict, folder: str, results: list) -> None:
    node_type = node.get("type")
    if node_type == "url":
        title = (node.get("name") or "").strip()
        url = (node.get("url") or "").strip()
        if url and url.startswith(("http://", "https://")):
            results.append((title or url, url, folder))
    elif node_type == "folder":
        subfolder = node.get("name") or ""
        path = f"{folder}/{subfolder}".strip("/") if folder else subfolder
        for child in node.get("children") or []:
            _parse_chromium_node(child, path, results)


def _scan_chromium_profile(profile_dir: str) -> list:
    # У залогиненных пользователей Chrome/Edge синхронизированные закладки
    # лежат в AccountBookmarks, а локальный Bookmarks может быть пустым.
    # Читаем оба файла и дедуплицируем по URL.
    results: list = []
    seen: set = set()
    for fname in ("Bookmarks", "AccountBookmarks"):
        bm_file = os.path.join(profile_dir, fname)
        if not os.path.isfile(bm_file):
            continue
        try:
            with open(bm_file, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            continue
        roots = data.get("roots") or {}
        for root_key in ("bookmark_bar", "other", "synced", "mobile"):
            root_node = roots.get(root_key)
            if not root_node:
                continue
            folder_name = root_node.get("name") or root_key
            tmp: list = []
            for child in root_node.get("children") or []:
                _parse_chromium_node(child, folder_name, tmp)
            for title, url, folder in tmp:
                if url in seen:
                    continue
                seen.add(url)
                results.append((title, url, folder))
    return results


def _scan_chromium_browser(browser_name: str, user_data_dir: str) -> list:
    if not os.path.isdir(user_data_dir):
        return []
    results = []
    for entry in os.listdir(user_data_dir):
        profile_dir = os.path.join(user_data_dir, entry)
        if not os.path.isdir(profile_dir):
            continue
        if entry == "Default" or entry.startswith("Profile"):
            for title, url, folder in _scan_chromium_profile(profile_dir):
                results.append((title, url, browser_name, folder))
    # Opera хранит закладки прямо в user_data_dir
    if not results:
        for title, url, folder in _scan_chromium_profile(user_data_dir):
            results.append((title, url, browser_name, folder))
    return results


def _scan_firefox() -> list:
    if not os.path.isdir(_FIREFOX_PROFILES):
        return []
    results = []
    for profile_dir in _glob.glob(os.path.join(_FIREFOX_PROFILES, "*")):
        if not os.path.isdir(profile_dir):
            continue
        db_path = os.path.join(profile_dir, "places.sqlite")
        if not os.path.isfile(db_path):
            continue
        import shutil, tempfile, sqlite3
        tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
        tmp.close()
        try:
            shutil.copy2(db_path, tmp.name)
            conn = sqlite3.connect(tmp.name, timeout=3)
            try:
                cur = conn.execute("""
                    SELECT b.title, p.url, f.title
                    FROM moz_bookmarks b
                    JOIN moz_places p ON p.id = b.fk
                    LEFT JOIN moz_bookmarks f ON f.id = b.parent
                    WHERE b.type = 1
                      AND p.url NOT LIKE 'place:%'
                      AND p.url LIKE 'http%'
                """)
                for title, url, folder in cur.fetchall():
                    title = (title or url or "").strip()
                    url = (url or "").strip()
                    folder = (folder or "").strip()
                    if url:
                        results.append((title or url, url, "Firefox", folder))
            finally:
                conn.close()
        except Exception:
            pass
        finally:
            try:
                os.unlink(tmp.name)
            except Exception:
                pass
    return results


# ── Алиасы ───────────────────────────────────────────────────────────────────

def _extract_domain(url: str) -> str:
    """Извлекает домен из URL (без www и TLD)."""
    try:
        # Убираем схему
        host = url.split("://", 1)[-1].split("/")[0].split("?")[0]
        host = host.lstrip("www.")
        # Ключевое слово — первая часть домена
        return host.split(".")[0].lower()
    except Exception:
        return ""


def _extract_full_domain(url: str) -> str:
    """Извлекает полный хост без схемы и path."""
    try:
        host = url.split("://", 1)[-1].split("/")[0].split("?")[0]
        return host.lstrip("www.").lower()
    except Exception:
        return ""


def _generate_aliases_basic(title: str, url: str) -> list[str]:
    """Генерирует базовые алиасы для закладки без LLM."""
    aliases: set[str] = set()

    domain = _extract_domain(url)
    full_domain = _extract_full_domain(url)

    if domain:
        aliases.add(domain)
    if full_domain and full_domain != domain:
        aliases.add(full_domain)

    # Слова из заголовка
    title_lower = title.lower()
    aliases.add(title_lower)
    for w in re.split(r'[\s\-_|·•–]+', title_lower):
        w = w.strip(".,!?():\"'")
        if len(w) >= 2:
            aliases.add(w)

    # Известные алиасы по домену
    for key, vals in _KNOWN_DOMAIN_ALIASES.items():
        if key == domain or full_domain.startswith(key + ".") or key in full_domain:
            aliases.update(vals)
            break

    # Убираем совсем короткие и URL-подобные
    return [a for a in aliases if 2 <= len(a) <= 60 and " " not in a or len(a) >= 4]


_LLM_ALIAS_PROMPT = """Ты генерируешь поисковые алиасы для закладок браузера.
Для каждой закладки придумай все возможные названия, которые пользователь может произнести голосом, включая:
- Русские транслитерации (youtube → ютуб, github → гитхаб)
- Сокращения и разговорные названия
- Тематические слова (если это видео → "видео", если документация → "доки")
- Ключевые слова из заголовка на русском

Входные данные — JSON массив объектов с полями "title" и "domain".
Верни JSON объект где ключ — title закладки, значение — массив алиасов.

ВАЖНО: Возвращай ТОЛЬКО JSON без маркдаун-разметки и пояснений.

Пример:
Вход: [{"title": "GitHub - torvalds/linux", "domain": "github"}, {"title": "YouTube", "domain": "youtube"}]
Выход: {"GitHub - torvalds/linux": ["гитхаб", "linux", "линукс", "торвальдс", "репозиторий"], "YouTube": ["ютуб", "видео", "youtube"]}"""

_LLM_BATCH_SIZE = 20


def _generate_aliases_llm(bookmarks: list) -> dict:
    """
    Генерирует алиасы через LLM для списка [(title, url), ...].
    Возвращает {url: [aliases]}.
    """
    from ui_automation import llm_config as _llm

    client     = _llm.get_client()
    api_model  = _llm.get_model()
    extra_body = _llm.get_extra_body()
    result = {}

    for i in range(0, len(bookmarks), _LLM_BATCH_SIZE):
        batch = bookmarks[i:i + _LLM_BATCH_SIZE]
        items = [
            {"title": title, "domain": _extract_domain(url)}
            for title, url in batch
        ]
        user_msg = f"Вход: {json.dumps(items, ensure_ascii=False)}"

        try:
            response = client.chat.completions.create(
                model=api_model,
                messages=[
                    {"role": "system", "content": _LLM_ALIAS_PROMPT},
                    {"role": "user",   "content": user_msg},
                ],
                temperature=0,
                max_tokens=2048,
                extra_body=extra_body,
            )
            raw = (response.choices[0].message.content or "").strip()
            content = re.sub(r'<think>[\s\S]*?</think>', '', raw, flags=re.DOTALL).strip()
            if not content:
                continue
            if content.startswith("```"):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content).strip()
            json_match = re.search(r'\{[\s\S]*\}', content)
            if not json_match:
                continue
            aliases_map = json.loads(json_match.group())
            for title, url in batch:
                llm_aliases = aliases_map.get(title, [])
                if isinstance(llm_aliases, list):
                    result[url] = [
                        a.lower().strip() for a in llm_aliases
                        if isinstance(a, str) and len(a.strip()) >= 2
                    ]
        except json.JSONDecodeError:
            pass
        except Exception:
            pass

    return result


# ── Основная функция ──────────────────────────────────────────────────────────

def scan_and_save(llm: bool = True) -> int:
    """
    Сканирует все браузеры и сохраняет закладки в БД.
    llm=False — только базовые алиасы.
    llm=True  — базовые + LLM-алиасы для новых закладок.
    Возвращает количество сохранённых закладок.
    """
    all_bookmarks: dict = {}  # url -> (title, url, browser, folder)

    for browser_name, user_data_dir in _CHROMIUM_PATHS:
        for title, url, browser, folder in _scan_chromium_browser(browser_name, user_data_dir):
            if url not in all_bookmarks:
                all_bookmarks[url] = (title, url, browser, folder)

    for title, url, browser, folder in _scan_firefox():
        if url not in all_bookmarks:
            all_bookmarks[url] = (title, url, browser, folder)

    bookmarks_clear()
    if not all_bookmarks:
        return 0

    bookmarks_put_many(list(all_bookmarks.values()))

    # Загружаем кэш LLM-алиасов
    llm_cache = _load_llm_cache()

    # Базовые алиасы для всех закладок
    alias_data = []
    for title, url, browser, folder in all_bookmarks.values():
        aliases = _generate_aliases_basic(title, url)
        if aliases:
            alias_data.append((url, aliases))
    if alias_data:
        bookmarks_add_aliases_bulk(alias_data)

    # Восстанавливаем LLM-алиасы из кэша
    current_urls = set(all_bookmarks.keys())
    cached_data = [
        (url, aliases) for url, aliases in llm_cache.items()
        if url in current_urls and aliases
    ]
    if cached_data:
        bookmarks_add_aliases_bulk(cached_data)

    if llm:
        _run_llm_for_new(all_bookmarks, llm_cache)

    return len(all_bookmarks)


def has_new_bookmarks_for_llm() -> bool:
    """Возвращает True если есть закладки без LLM-алиасов в кэше."""
    from database import bookmarks_search
    llm_cache = _load_llm_cache()
    # Проверяем первые 100 URL из БД
    import sqlite3
    from database import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH, timeout=3)
        cur = conn.execute("SELECT url FROM bookmarks LIMIT 100")
        urls = [row[0] for row in cur.fetchall()]
        conn.close()
    except Exception:
        return False
    return any(url not in llm_cache for url in urls)


def generate_llm_aliases_for_new() -> None:
    """Генерирует LLM-алиасы для закладок без кэша. Вызывается из фонового потока."""
    import sqlite3
    from database import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH, timeout=3)
        cur = conn.execute("SELECT title, url FROM bookmarks")
        rows = cur.fetchall()
        conn.close()
    except Exception:
        return
    all_bookmarks = {url: (title, url, "", "") for title, url in rows}
    llm_cache = _load_llm_cache()
    _run_llm_for_new(all_bookmarks, llm_cache, silent=True)


def _run_llm_for_new(all_bookmarks: dict, llm_cache: dict, silent: bool = False) -> None:
    """Генерирует LLM-алиасы только для закладок, которых нет в кэше."""
    new_bookmarks = [
        (title, url) for title, url, *_ in all_bookmarks.values()
        if url not in llm_cache
    ]
    if not new_bookmarks:
        return
    if not silent:
        print(f"[Закладки] Генерация LLM для {len(new_bookmarks)} закладок…")

    llm_aliases = _generate_aliases_llm(new_bookmarks)
    if not llm_aliases:
        return

    llm_cache.update(llm_aliases)
    _save_llm_cache(llm_cache)
    llm_data = [(url, aliases) for url, aliases in llm_aliases.items() if aliases]
    if llm_data:
        bookmarks_add_aliases_bulk(llm_data)
