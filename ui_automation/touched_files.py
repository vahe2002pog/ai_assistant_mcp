"""Сбор путей к файлам/папкам, с которыми ассистент работал в течение запроса.

ToolAgent после каждого вызова инструмента вызывает `record_from_tool(name, args, result)`,
который вычисляет затронутый путь по имени тула. web_server / gui перед dispatch
делают `reset()`, после dispatch получают список через `items()` и подмешивают
его в финальный ответ как `FilesBlock`.

Также есть `extract_paths_from_text(text)` — регуляркой выдёргивает абсолютные
Windows-пути из произвольного текста (используется когда пользователь просто
спрашивает «дай путь к файлу» — путь приедет в voice/screen-блоке текстом).
"""
from __future__ import annotations

import os
import re
import threading
from typing import Dict, List, Optional, Tuple


_local = threading.local()


def _state() -> dict:
    st = getattr(_local, "st", None)
    if st is None:
        st = {"items": [], "seen": set(), "candidates": []}
        _local.st = st
    return st


def reset() -> None:
    _local.st = {"items": [], "seen": set(), "candidates": []}


def add_candidate(path: str) -> None:
    """Путь, который ассистент УВИДЕЛ (list_directory и т.п.), но не «трогал».
    Будет промоутирован в items() только если basename упомянут в финальном тексте."""
    if not path:
        return
    try:
        norm = os.path.normpath(path)
    except Exception:
        return
    if not (len(norm) >= 2 and (norm[1:2] == ":" or norm.startswith("\\\\"))):
        return
    st = _state()
    st["candidates"].append(norm)


def promote_candidates_from_text(text: str) -> None:
    """Если basename кандидата встречается в тексте — добавляет его в items()."""
    if not text:
        return
    st = _state()
    if not st["candidates"]:
        return
    text_low = text.lower()
    for p in st["candidates"]:
        base = os.path.basename(p).lower()
        if not base:
            continue
        # Сравнение по полному basename (с расширением) или по stem.
        stem = os.path.splitext(base)[0]
        if base in text_low or (len(stem) >= 3 and stem in text_low):
            add(p, "найден")


def add(path: str, action: str = "") -> None:
    if not path:
        return
    try:
        norm = os.path.normpath(path)
    except Exception:
        return
    # Берём только абсолютные пути — относительные не имеют смысла для пользователя.
    if not (len(norm) >= 2 and (norm[1:2] == ":" or norm.startswith("\\\\"))):
        return
    st = _state()
    if norm in st["seen"]:
        return
    st["seen"].add(norm)
    st["items"].append({"path": norm, "action": action,
                        "exists": os.path.exists(norm),
                        "is_dir": os.path.isdir(norm)})


def items() -> List[dict]:
    return list(_state()["items"])


def paths() -> List[str]:
    return [it["path"] for it in _state()["items"]]


# ── Извлечение из текста ──────────────────────────────────────────────────────
# Windows-абсолютные пути: C:\... или C:/..., \\server\share\...
_WIN_PATH_RE = re.compile(
    r"(?:(?<![A-Za-z])[A-Za-z]:[\\/]|\\\\[^\s\\/<>\"'|?*]+\\)"
    r"[^\s<>\"'|?*]+(?:[^\s<>\"'|?*.,;:!?…)\]])",
)


def extract_paths_from_text(text: str) -> List[str]:
    """Возвращает список абсолютных Windows-путей, упомянутых в тексте."""
    if not text:
        return []
    out: List[str] = []
    seen: set = set()
    for m in _WIN_PATH_RE.finditer(text):
        p = m.group(0).strip().rstrip(".,;:!?…)»\"'")
        if p in seen:
            continue
        seen.add(p)
        out.append(p)
    return out


# ── Парсеры результата tool'ов ────────────────────────────────────────────────
# Возвращают (path, action) или None.

def _cache_get(file_id) -> Optional[str]:
    try:
        from database import cache_get  # type: ignore
        if isinstance(file_id, str) and file_id.isdigit():
            file_id = int(file_id)
        if isinstance(file_id, int):
            return cache_get(file_id)
        if isinstance(file_id, str):
            return file_id
    except Exception:
        return None
    return None


def _system_path(directory: str) -> Optional[str]:
    try:
        from mcp_modules.mcp_core import get_system_path  # type: ignore
        return get_system_path(directory)
    except Exception:
        return directory if (directory and os.path.isabs(directory)) else None


def _is_error_result(result: str) -> bool:
    if not result:
        return True
    low = result.lower()
    return low.startswith(("ошибка", "error")) or "не найден" in low


def record_from_tool(name: str, args: Dict, result: str) -> None:
    """По имени тула, аргументам и результату определяет затронутый путь."""
    if _is_error_result(result):
        return

    try:
        # ── обзорные тулы — все увиденные пути идут в candidates ────────────
        if name in ("list_directory", "view_cache"):
            # Формат строк: "<id>: <path>"
            for line in (result or "").splitlines():
                m = re.match(r"\s*\d+:\s*(.+?)\s*$", line)
                if m:
                    p = m.group(1).strip()
                    # list_directory нормализует слеши на '/' — вернём обратно.
                    p = p.replace("/", os.sep) if os.sep == "\\" else p
                    add_candidate(p)
            return

        # ── files ───────────────────────────────────────────────────────────
        if name == "create_item":
            directory = args.get("directory", "")
            nm = args.get("name", "")
            base = _system_path(directory)
            if base and nm:
                add(os.path.join(base, nm),
                    "создана папка" if args.get("is_folder") else "создан файл")
            return

        if name == "rename_item":
            new_name = args.get("new_name", "")
            old = _cache_get(args.get("file_id"))
            if old and new_name:
                new_path = os.path.join(os.path.dirname(old), new_name)
                add(new_path, "переименован")
            return

        if name == "copy_item":
            # В тексте: "Успешно скопировано в '<dest>'."
            m = re.search(r"скопировано в ['\"]([^'\"]+)['\"]", result)
            if m:
                add(m.group(1), "скопирован")
            return

        if name == "move_file":
            src = _cache_get(args.get("file_id"))
            dest_dir = _system_path(args.get("destination_folder", "")) \
                       or args.get("destination_folder", "")
            if src and dest_dir:
                add(os.path.join(dest_dir, os.path.basename(src)), "перемещён")
            return

        if name in ("read_file", "edit_file", "get_file_info"):
            p = _cache_get(args.get("file_id"))
            if p:
                actions = {"read_file": "прочитан",
                           "edit_file": "изменён",
                           "get_file_info": "просмотрены свойства"}
                add(p, actions.get(name, ""))
            return

        if name == "delete_item":
            p = _cache_get(args.get("file_id"))
            if p:
                add(p, "удалён в корзину")
            return

        if name in ("execute_open_file", "open_folder"):
            fid = args.get("file_id") or args.get("folder_id")
            p = _cache_get(fid) if fid is not None else None
            if not p:
                # Резервный парс из результата.
                m = re.search(r"__OPEN_(?:FILE|FOLDER)_COMMAND__:(.+)$", result)
                if m:
                    p = m.group(1).strip()
                else:
                    m = re.search(r"Открыт(?:а)?\s+(?:файл|папка):?\s*(.+)$", result)
                    if m:
                        p = m.group(1).strip()
            if p:
                add(p, "открыт")
            return

        # ── office ──────────────────────────────────────────────────────────
        # Office-тулы часто принимают `path`. Берём напрямую.
        path_arg = args.get("path") or args.get("file_path") or args.get("filename")
        if path_arg:
            actions_by_tool = {
                "word_create_document": "создан документ Word",
                "excel_create_workbook": "создана книга Excel",
                "ppt_create": "создана презентация",
                "word_read_document": "прочитан документ Word",
                "excel_read_sheet": "прочитан лист Excel",
                "ppt_read_slides": "прочитана презентация",
                "word_write_text": "изменён документ Word",
                "word_find_replace": "изменён документ Word",
                "excel_write_cell": "изменена книга Excel",
                "excel_write_range": "изменена книга Excel",
                "excel_apply_formula": "изменена книга Excel",
                "ppt_add_slide": "изменена презентация",
                "ppt_add_textbox": "изменена презентация",
            }
            if name in actions_by_tool:
                # path_arg может быть относительным (Desktop/foo.docx) — попробуем resolve
                resolved = path_arg
                if not os.path.isabs(resolved):
                    try:
                        from mcp_modules.tools_office import resolve_path  # type: ignore
                        resolved = resolve_path(path_arg)
                    except Exception:
                        pass
                add(resolved, actions_by_tool[name])
            return
    except Exception:
        return
