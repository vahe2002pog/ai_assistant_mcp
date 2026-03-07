import os
import sqlite3
import time
import json
from typing import Dict, Optional

# SQLite-backed cache: хранит значения между процессами.
MAX_CACHE = 200
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "cache.db")
LOG_PATH = os.path.join(BASE_DIR, "cache_debug.log")


def _log(msg: str) -> None:
    try:
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        pid = os.getpid()
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{ts} PID:{pid} {msg}\n")
    except Exception:
        pass


def _get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=5, isolation_level=None)
    # WAL improves concurrent reads/writes across processes
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn


def _init_db() -> None:
    conn = _get_conn()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                value TEXT NOT NULL,
                created_ts INTEGER NOT NULL
            )
            """
        )
        # Уникальный индекс по значению — предотвращает дублирование
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cache_value ON cache(value)")
        
        # Таблица истории действий (для функции отмены)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS action_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_ts INTEGER NOT NULL
            )
            """
        )
        
        cur = conn.execute("SELECT COUNT(1) FROM cache")
        total = cur.fetchone()[0]
        if total > MAX_CACHE:
            to_remove = total - MAX_CACHE
            conn.execute(
                "DELETE FROM cache WHERE id IN (SELECT id FROM cache ORDER BY id ASC LIMIT ?)",
                (to_remove,)
            )
    finally:
        conn.close()


# Инициализация при импорте
_init_db()
_log("INIT sqlite cache ready")


def cache_put(value: str) -> int:
    """Добавляет значение в sqlite-кэш и возвращает id записи."""
    ts = int(time.time())
    conn = _get_conn()
    try:
        # Если значение уже есть — возвращаем его id и обновляем timestamp
        cur = conn.execute("SELECT id FROM cache WHERE value=?", (value,))
        row = cur.fetchone()
        if row:
            key = row[0]
            conn.execute("UPDATE cache SET created_ts=? WHERE id=?", (ts, key))
            _log(f"EXISTS key={key} value={value}")
        else:
            cur = conn.execute("INSERT INTO cache (value, created_ts) VALUES (?, ?)", (value, ts))
            key = cur.lastrowid

        cur = conn.execute("SELECT COUNT(1) FROM cache")
        total = cur.fetchone()[0]
        if total > MAX_CACHE:
            to_remove = total - MAX_CACHE
            conn.execute(
                "DELETE FROM cache WHERE id IN (SELECT id FROM cache ORDER BY id ASC LIMIT ?)",
                (to_remove,)
            )
        _log(f"PUT key={key} value={value} total={total}")
        return key
    finally:
        conn.close()


def cache_get(key: int) -> Optional[str]:
    """Возвращает значение по id или None."""
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT value FROM cache WHERE id=?", (key,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def cache_list() -> Dict[int, str]:
    """Возвращает словарь {id: preview} для отображения ассистенту."""
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT id, value FROM cache ORDER BY id")
        rows = cur.fetchall()
        result: Dict[int, str] = {}
        for k, v in rows:
            if isinstance(v, str) and os.path.exists(v):
                result[k] = os.path.basename(v)
            else:
                result[k] = str(v)
        _log(f"LIST keys={list(result.keys())}")
        return result
    finally:
        conn.close()


def cache_clear() -> None:
    """Очищает весь кэш."""
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM cache")
        _log("CLEAR cache cleared")
    finally:
        conn.close()


# === НОВЫЕ ФУНКЦИИ ДЛЯ РАБОТЫ С ИСТОРИЕЙ (UNDO) ===

def history_push(action_type: str, payload: dict) -> None:
    """Записывает действие в БД для возможности отмены."""
    ts = int(time.time())
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO action_history (action_type, payload, created_ts) VALUES (?, ?, ?)",
            (action_type, json.dumps(payload), ts)
        )
        _log(f"HISTORY PUSH: {action_type}")
    finally:
        conn.close()


def history_pop() -> Optional[dict]:
    """Извлекает и удаляет последнее действие из БД."""
    conn = _get_conn()
    try:
        # Берем последнюю запись
        cur = conn.execute("SELECT id, action_type, payload FROM action_history ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            return None
        
        record_id, action_type, payload = row
        # Удаляем её из истории
        conn.execute("DELETE FROM action_history WHERE id=?", (record_id,))
        
        return {"type": action_type, "payload": json.loads(payload)}
    finally:
        conn.close()
