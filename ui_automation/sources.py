"""Сбор URL-источников, увиденных в выходе инструментов за один запрос.

ToolAgent добавляет сюда все http(s) ссылки из результатов вызовов
(web_search, web_extract и т.п.) вместе с заголовком страницы. web_server
очищает список перед dispatch, после dispatch фильтрует по тому, что
реально попало в финальный ответ, и пришивает блок «Источники».
"""
import re
import threading
from typing import List, Tuple
from urllib.parse import urlparse

_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+", re.IGNORECASE)
# Главные ссылки web_search/web_extract всегда префиксируются «🔗 ».
_MARKER_URL_RE = re.compile(r"🔗\s*(https?://[^\s<>\"'\)\]]+)", re.IGNORECASE)
# Заголовок результата идёт строкой выше — «📌 <title>».
_PAIR_RE = re.compile(
    r"📌\s*(?P<title>[^\n]+?)\s*(?:\(Score:[^)]*\))?\s*\n+\s*🔗\s*(?P<url>https?://\S+)",
    re.IGNORECASE,
)
_TRAIL_PUNCT = ".,;:!?…"
_HOST_RE = re.compile(r"^[a-z0-9]([a-z0-9\-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9\-]*[a-z0-9])?)+$",
                      re.IGNORECASE)

_local = threading.local()


def _is_valid_url(url: str) -> bool:
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


def _registered_root(host: str) -> str:
    """Грубое eTLD+1: последние 2 части (вики.ру, example.com)."""
    parts = host.lower().split(".")
    return ".".join(parts[-2:]) if len(parts) >= 2 else host


def _state() -> dict:
    st = getattr(_local, "st", None)
    if st is None:
        st = {"items": [], "seen": set()}
        _local.st = st
    return st


def reset() -> None:
    _local.st = {"items": [], "seen": set()}


def add_from_text(text: str) -> None:
    """Извлекает (title, url) пары и одиночные URL из 🔗-маркеров."""
    if not text:
        return
    st = _state()
    items: List[dict] = st["items"]
    seen: set = st["seen"]

    # 1) Пары title+url (полная атрибуция).
    for m in _PAIR_RE.finditer(text):
        url = m.group("url").rstrip(_TRAIL_PUNCT)
        if url in seen or not _is_valid_url(url):
            continue
        title = (m.group("title") or "").strip()
        host = urlparse(url).hostname or ""
        items.append({"url": url, "title": title, "host": host.lower()})
        seen.add(url)

    # 2) Одиночные 🔗-маркеры без 📌 (например, web_extract).
    for raw in _MARKER_URL_RE.findall(text):
        url = raw.rstrip(_TRAIL_PUNCT)
        if url in seen or not _is_valid_url(url):
            continue
        host = urlparse(url).hostname or ""
        items.append({"url": url, "title": "", "host": host.lower()})
        seen.add(url)


def collect() -> List[str]:
    return [it["url"] for it in _state()["items"]]


def items() -> List[dict]:
    return list(_state()["items"])


def _significant_words(title: str) -> List[str]:
    """Ключевые слова заголовка длиной ≥4 (для нечёткой проверки совпадения)."""
    words = re.findall(r"[\wа-яёА-ЯЁ]{4,}", title or "", flags=re.UNICODE)
    return [w.lower() for w in words]


def filter_used(output_text: str, min_title_hits: int = 1) -> List[str]:
    """Возвращает URL, для которых в output_text встречается хотя бы один признак:
    сам URL, его host, eTLD+1 или ≥min_title_hits ключевых слов из заголовка.
    """
    if not output_text:
        return []
    text_low = output_text.lower()
    used: List[str] = []
    for it in _state()["items"]:
        url = it["url"]
        host = it["host"]
        if not host:
            continue
        if url.lower() in text_low:
            used.append(url); continue
        if host in text_low or _registered_root(host) in text_low:
            used.append(url); continue
        words = _significant_words(it.get("title", ""))
        if words:
            hits = sum(1 for w in words if w in text_low)
            if hits >= min_title_hits and hits >= max(1, len(words) // 3):
                used.append(url); continue
    # Сохраняем порядок добавления, без дублей.
    seen = set(); out = []
    for u in used:
        if u not in seen:
            seen.add(u); out.append(u)
    return out
