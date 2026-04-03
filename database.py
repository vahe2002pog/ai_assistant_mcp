import os
import sqlite3
import time
import json
from typing import Dict, Optional

MAX_CACHE = 200
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE_DIR, "cache.db")


def _get_conn():
    conn = sqlite3.connect(DB_PATH, timeout=5, isolation_level=None)
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA synchronous=NORMAL;")
    except Exception:
        pass
    return conn


def _init_db() -> None:
    conn = _get_conn()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                value TEXT NOT NULL,
                created_ts INTEGER NOT NULL
            )
        """)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cache_value ON cache(value)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS apps (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                path TEXT NOT NULL UNIQUE
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS app_aliases (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                app_id INTEGER NOT NULL,
                alias TEXT NOT NULL,
                FOREIGN KEY (app_id) REFERENCES apps(id) ON DELETE CASCADE
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_alias ON app_aliases(alias)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS action_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                action_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_ts INTEGER NOT NULL
            )
        """)
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


_init_db()


def cache_put(value: str) -> int:
    ts = int(time.time())
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT id FROM cache WHERE value=?", (value,))
        row = cur.fetchone()
        if row:
            key = row[0]
            conn.execute("UPDATE cache SET created_ts=? WHERE id=?", (ts, key))
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
        return key
    finally:
        conn.close()


def cache_get(key: int) -> Optional[str]:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT value FROM cache WHERE id=?", (key,))
        row = cur.fetchone()
        return row[0] if row else None
    finally:
        conn.close()


def cache_list() -> Dict[int, str]:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT id, value FROM cache ORDER BY id")
        rows = cur.fetchall()
        result: Dict[int, str] = {}
        for k, v in rows:
            result[k] = str(v)
        return result
    finally:
        conn.close()


def cache_clear() -> None:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM cache")
    finally:
        conn.close()


def history_push(action_type: str, payload: dict) -> None:
    ts = int(time.time())
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT INTO action_history (action_type, payload, created_ts) VALUES (?, ?, ?)",
            (action_type, json.dumps(payload), ts)
        )
    finally:
        conn.close()


def history_get_last() -> Optional[dict]:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT action_type, payload FROM action_history ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            return None
        action_type, payload = row
        return {"type": action_type, "payload": json.loads(payload)}
    finally:
        conn.close()


def history_remove_last() -> None:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM action_history WHERE id = (SELECT MAX(id) FROM action_history)")
    finally:
        conn.close()


def history_pop() -> Optional[dict]:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT id, action_type, payload FROM action_history ORDER BY id DESC LIMIT 1")
        row = cur.fetchone()
        if not row:
            return None
        record_id, action_type, payload = row
        conn.execute("DELETE FROM action_history WHERE id=?", (record_id,))
        return {"type": action_type, "payload": json.loads(payload)}
    finally:
        conn.close()


def apps_clear() -> None:
    conn = _get_conn()
    try:
        conn.execute("DELETE FROM app_aliases")
        conn.execute("DELETE FROM apps")
    finally:
        conn.close()


def apps_put(name: str, path: str) -> None:
    conn = _get_conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO apps (name, path) VALUES (?, ?)",
            (name, path),
        )
    finally:
        conn.close()


def apps_put_many(items: list) -> None:
    conn = _get_conn()
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO apps (name, path) VALUES (?, ?)",
            items,
        )
    finally:
        conn.close()


def apps_add_aliases(path: str, aliases: list) -> None:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT id FROM apps WHERE path=?", (path,))
        row = cur.fetchone()
        if not row:
            return
        app_id = row[0]
        conn.executemany(
            "INSERT OR IGNORE INTO app_aliases (app_id, alias) VALUES (?, ?)",
            [(app_id, a.lower()) for a in aliases if a],
        )
    finally:
        conn.close()


def apps_add_aliases_bulk(items: list) -> None:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT id, path FROM apps")
        path_to_id = {row[1]: row[0] for row in cur.fetchall()}
        rows = []
        for path, aliases in items:
            app_id = path_to_id.get(path)
            if app_id:
                for a in aliases:
                    if a:
                        rows.append((app_id, a.lower()))
        if rows:
            conn.executemany(
                "INSERT OR IGNORE INTO app_aliases (app_id, alias) VALUES (?, ?)",
                rows,
            )
    finally:
        conn.close()


def apps_search(query: str) -> list:
    conn = _get_conn()
    try:
        q = query.lower()
        cur = conn.execute(
            """
            SELECT DISTINCT a.name, a.path FROM apps a
            LEFT JOIN app_aliases al ON al.app_id = a.id
            WHERE a.name LIKE ? OR al.alias LIKE ?
            ORDER BY
                CASE
                    WHEN LOWER(a.name) = ? THEN 0
                    WHEN al.alias = ? THEN 1
                    WHEN LOWER(a.name) LIKE ? THEN 2
                    ELSE 3
                END
            LIMIT 10
            """,
            (f"%{q}%", f"%{q}%", q, q, f"{q}%"),
        )
        return cur.fetchall()
    finally:
        conn.close()


def apps_list_all() -> list:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT name, path FROM apps ORDER BY name")
        return cur.fetchall()
    finally:
        conn.close()


def apps_get_paths_with_aliases() -> set:
    conn = _get_conn()
    try:
        cur = conn.execute(
            "SELECT DISTINCT a.path FROM apps a INNER JOIN app_aliases al ON al.app_id = a.id"
        )
        return {row[0] for row in cur.fetchall()}
    finally:
        conn.close()


def apps_count() -> int:
    conn = _get_conn()
    try:
        cur = conn.execute("SELECT COUNT(1) FROM apps")
        return cur.fetchone()[0]
    finally:
        conn.close()
