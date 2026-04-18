"""Флаги отмены запросов — по ключу (conversation_id) + thread-local scope.

Каждый чат имеет свой Event, так что «стоп» в чате A не прерывает чат B.
Активный ключ хранится в thread-local: web_server устанавливает его перед
вызовом HostAgent.dispatch, а агенты читают is_cancelled() без знания ключа.
"""
import threading
from typing import Optional

_lock = threading.Lock()
_events: dict[str, threading.Event] = {}
_local = threading.local()
_default = threading.Event()


def _get(key: Optional[str]) -> threading.Event:
    if key is None:
        return _default
    with _lock:
        ev = _events.get(key)
        if ev is None:
            ev = threading.Event()
            _events[key] = ev
        return ev


def _current_event() -> threading.Event:
    return _get(getattr(_local, "key", None))


def set_scope(key: Optional[str]) -> None:
    """Устанавливает ключ отмены для текущего потока (вызывается на входе dispatch)."""
    _local.key = key


def clear_scope() -> None:
    _local.key = None


def request_cancel(key: Optional[str] = None) -> None:
    """Просит отменить запрос. Если key не задан — отменяет scope текущего потока."""
    if key is None:
        _current_event().set()
    else:
        _get(key).set()


def clear(key: Optional[str] = None) -> None:
    if key is None:
        _current_event().clear()
    else:
        _get(key).clear()


def request_cancel_all() -> None:
    """Отменяет все известные ключи и дефолтный scope."""
    _default.set()
    with _lock:
        for ev in _events.values():
            ev.set()


def is_cancelled(key: Optional[str] = None) -> bool:
    """Проверяет флаг отмены. Без key — для текущего thread-scope."""
    if key is None:
        return _current_event().is_set()
    return _get(key).is_set()


class Cancelled(Exception):
    pass


def check() -> None:
    if is_cancelled():
        raise Cancelled("Отменено пользователем")
