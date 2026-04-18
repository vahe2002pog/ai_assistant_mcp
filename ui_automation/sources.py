"""Сбор URL-источников, увиденных в выходе инструментов за один запрос.

ToolAgent добавляет сюда все http(s) ссылки из результатов вызовов (tavily_search,
tavily_extract и т.п.). web_server очищает список перед dispatch и после
его завершения пришивает в ответ блок «Источники».
"""
import re
import threading
from typing import List
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)
_TRAIL_PUNCT = ".,;:!?\u2026"
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+$", re.IGNORECASE)

_local = threading.local()


def _is_valid_url(url: str) -> bool:
    """Отбрасывает мусор: слишком длинные, без нормального хоста,
    с пробелами или служебные URL."""
    if not url or len(url) < 10 or len(url) > 500:
        return False
    try:
        p = urlparse(url)
    except Exception:
        return False
    if p.scheme not in ("http", "https"):
        return False
    host = (p.hostname or "").lower()
    if not host or not _HOST_RE.match(host):
        return False
    if host in ("localhost", "127.0.0.1", "0.0.0.0"):
        return False
    return True


def _get_list() -> List[str]:
    lst = getattr(_local, "urls", None)
    if lst is None:
        lst = []
        _local.urls = lst
    return lst


def reset() -> None:
    _local.urls = []
    _local.seen = set()


def add_from_text(text: str) -> None:
    if not text:
        return
    seen = getattr(_local, "seen", None)
    if seen is None:
        seen = set(); _local.seen = seen
    lst = _get_list()
    for raw in _URL_RE.findall(text):
        url = raw.rstrip(_TRAIL_PUNCT)
        if url in seen:
            continue
        if not _is_valid_url(url):
            continue
        seen.add(url)
        lst.append(url)


def collect() -> List[str]:
    return list(_get_list())
