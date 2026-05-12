from __future__ import annotations

import os
import re
import json
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from ui_automation.logging_config import log_warning

_TRUSTED_TOOL_CALL: ContextVar[bool] = ContextVar("trusted_tool_call", default=False)
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_PATH = os.path.join(_ROOT, "llm_config.json")


@dataclass(frozen=True)
class SafetyDecision:
    allowed: bool
    reason: str = ""
    severity: str = "low"


READ_ONLY_TOOLS = {
    "browser_get_state", "browser_extract_content", "get_file_info",
    "list_apps", "list_directory", "read_file", "search_files", "view_cache",
    "ui_find_elements", "ui_find_inputs", "ui_find_window", "ui_get_foreground",
    "ui_get_text", "ui_list_interactive", "ui_list_processes", "ui_list_windows",
    "ui_screenshot", "office_available_apps", "office_docs_search",
    "office_is_available", "office_running_apps", "excel_get_sheets",
    "excel_read_sheet", "word_read_document",
}

DESTRUCTIVE_TOOLS = {
    "delete_item", "undo_last_action", "move_file", "rename_item",
    "edit_file", "create_item", "copy_item",
}

UI_MUTATING_TOOLS = {
    "ui_click", "ui_click_by_index", "ui_click_element", "ui_send_keys",
    "ui_type_text", "ui_close_window", "ui_clipboard_set",
    "office_close_dialogs", "office_quit", "office_visible",
}

CODE_EXEC_TOOLS = {"office_run_python", "com_run_python"}

EXPLICIT_DELETE_WORDS = (
    "delete", "remove", "trash", "erase", "удали", "удалить", "сотри",
    "стереть", "в корзину", "перемести в корзину",
)
EXPLICIT_WRITE_WORDS = (
    "create", "write", "save", "rename", "move", "copy", "edit", "append",
    "создай", "запиши", "сохрани", "переименуй", "перемести", "скопируй",
    "измени", "добавь", "редактируй",
)
EXPLICIT_CLOSE_WORDS = ("close", "quit", "закрой", "закрыть", "выйди", "заверши")

RISKY_HOTKEYS = (
    "alt+f4", "%{f4}", "ctrl+w", "ctrl+q", "shift+delete", "win+r",
    "{lwin}r", "ctrl+alt+delete",
)

PROTECTED_PATH_MARKERS = (
    "\\windows", "\\program files", "\\program files (x86)", "\\programdata",
    "\\system32", "\\syswow64", "\\appdata", "\\.ssh", "\\.gnupg",
    "\\microsoft\\credentials", "\\microsoft\\windows\\start menu",
)

DANGEROUS_CODE_PATTERNS = (
    r"\bos\.remove\b", r"\bos\.rmdir\b", r"\bshutil\.rmtree\b",
    r"\bsubprocess\b", r"\bos\.system\b", r"\bShellExecute\b",
    r"\bTerminateProcess\b", r"\bkill\b", r"\bReg(Delete|Write|Set)\b",
    r"\bWScript\.Shell\b", r"\bScripting\.FileSystemObject\b",
    r"\b(eval|exec)\s*\(", r"open\s*\([^)]*,\s*['\"][wa+]",
)

SECRET_PATTERNS = (
    r"sk-[A-Za-z0-9_-]{20,}",
    r"(?i)\b(api[_-]?key|password|passwd|secret|token)\s*[:=]\s*\S{8,}",
    r"(?i)\bBearer\s+[A-Za-z0-9._-]{20,}",
)


def blocked_message(reason: str) -> str:
    return f"Блокировано safety-guard: {reason}"


def get_safety_mode() -> str:
    env = os.environ.get("COMPASS_SAFETY_MODE")
    if env:
        return _normalize_mode(env)
    try:
        with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f) or {}
        return _normalize_mode(str(data.get("safety_mode") or "strict"))
    except Exception:
        return "strict"


def set_safety_mode(mode: str) -> str:
    value = _normalize_mode(mode)
    try:
        try:
            with open(_CONFIG_PATH, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
        except Exception:
            data = {}
        data["safety_mode"] = value
        with open(_CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    finally:
        os.environ["COMPASS_SAFETY_MODE"] = value
    return value


def check_tool_call(name: str, args: dict[str, Any] | None, user_task: str = "") -> SafetyDecision:
    args = args or {}
    if _disabled():
        return SafetyDecision(True)
    if _TRUSTED_TOOL_CALL.get():
        return SafetyDecision(True)

    if name in READ_ONLY_TOOLS:
        return SafetyDecision(True)

    if name in CODE_EXEC_TOOLS:
        code = str(args.get("code") or "")
        if _dangerous_code(code):
            return _block(name, "произвольный код содержит опасные операции ОС/файлов/реестра", args)
        return SafetyDecision(True)

    if name == "browser_navigate":
        return _check_url(str(args.get("url") or ""), name, args)

    if name in {"browser_input_text", "ui_clipboard_set", "ui_type_text"}:
        text = str(args.get("text") or "")
        if _looks_like_secret(text):
            return _block(name, "похоже на ввод секрета или токена", {"text": "<redacted>"})
        return SafetyDecision(True)

    if name in {"browser_click", "browser_send_keys", "browser_close_tab"}:
        return SafetyDecision(True)

    if name in DESTRUCTIVE_TOOLS:
        return _check_file_mutation(name, args, user_task)

    if name in UI_MUTATING_TOOLS:
        return _check_ui_mutation(name, args, user_task)

    return SafetyDecision(True)


@contextmanager
def trusted_tool_call():
    token = _TRUSTED_TOOL_CALL.set(True)
    try:
        yield
    finally:
        _TRUSTED_TOOL_CALL.reset(token)


def check_step(step: Any, goal: str = "") -> SafetyDecision:
    if _disabled():
        return SafetyDecision(True)
    text = " ".join([
        str(getattr(step, "action_type", "")),
        str(getattr(step, "free_text", "") or ""),
        str(getattr(step, "parameters", {}) or {}),
    ]).lower()
    if any(tok in text for tok in ("delete", "remove", "удали", "удалить", "erase")):
        if not _has_intent(goal, EXPLICIT_DELETE_WORDS):
            return _block("controller_step", "деструктивный шаг без явного запроса пользователя", {"step": text})
    return SafetyDecision(True)


def _check_file_mutation(name: str, args: dict[str, Any], user_task: str) -> SafetyDecision:
    if name in {"delete_item", "undo_last_action"} and not _has_intent(user_task, EXPLICIT_DELETE_WORDS):
        return _block(name, "удаление/отмена с удалением требует явного запроса пользователя", args)

    if name not in {"delete_item", "undo_last_action"} and not _has_intent(user_task, EXPLICIT_WRITE_WORDS):
        return _block(name, "изменение файлов требует явного запроса пользователя", args)

    for path in _paths_from_args(name, args):
        if _is_protected_path(path):
            return _block(name, f"путь находится в защищённой области: {path}", args)

    return SafetyDecision(True)


def _check_ui_mutation(name: str, args: dict[str, Any], user_task: str) -> SafetyDecision:
    if name in {"ui_close_window", "office_quit", "office_close_dialogs"}:
        if not _has_intent(user_task, EXPLICIT_CLOSE_WORDS):
            return _block(name, "закрытие окон/диалогов требует явного запроса пользователя", args)

    keys = str(args.get("keys") or "").strip().lower().replace(" ", "")
    if keys and any(hotkey in keys for hotkey in RISKY_HOTKEYS):
        if not _has_intent(user_task, EXPLICIT_CLOSE_WORDS + EXPLICIT_DELETE_WORDS):
            return _block(name, f"опасная горячая клавиша: {args.get('keys')}", args)

    return SafetyDecision(True)


def _check_url(url: str, name: str, args: dict[str, Any]) -> SafetyDecision:
    parsed = urlparse(url)
    scheme = (parsed.scheme or "").lower()
    if scheme in {"javascript", "data", "file", "vbscript"}:
        return _block(name, f"опасная схема URL: {scheme}", args)
    if scheme and scheme not in {"http", "https", "chrome", "edge"}:
        return _block(name, f"неожиданная схема URL: {scheme}", args)
    host = (parsed.hostname or "").lower()
    if host in {"localhost", "127.0.0.1", "::1"} and os.environ.get("COMPASS_ALLOW_LOCAL_URLS") != "1":
        return _block(name, "локальные URL заблокированы без COMPASS_ALLOW_LOCAL_URLS=1", args)
    return SafetyDecision(True)


def _paths_from_args(name: str, args: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    try:
        from database import cache_get
    except Exception:
        cache_get = None

    file_id = args.get("file_id")
    if file_id is not None and cache_get is not None:
        try:
            p = cache_get(int(file_id))
            if p:
                paths.append(str(p))
        except Exception:
            pass

    for key in ("directory", "destination_folder", "file_path", "path"):
        if args.get(key):
            paths.append(str(args[key]))
    return paths


def _is_protected_path(path: str) -> bool:
    if not path:
        return False
    expanded = os.path.abspath(os.path.expandvars(os.path.expanduser(path))).lower()
    normalized = expanded.replace("/", "\\")
    home = os.path.abspath(os.path.expanduser("~")).lower().replace("/", "\\")
    if normalized.rstrip("\\") == home.rstrip("\\"):
        return True
    return any(marker in normalized for marker in PROTECTED_PATH_MARKERS)


def _dangerous_code(code: str) -> bool:
    return any(re.search(pattern, code, flags=re.IGNORECASE) for pattern in DANGEROUS_CODE_PATTERNS)


def _looks_like_secret(text: str) -> bool:
    if not text:
        return False
    return any(re.search(pattern, text) for pattern in SECRET_PATTERNS)


def _has_intent(user_task: str, words: tuple[str, ...]) -> bool:
    task = (user_task or "").lower()
    return any(word in task for word in words)


def _disabled() -> bool:
    return get_safety_mode() in {"off", "disabled", "0"} or os.environ.get("COMPASS_ALLOW_DANGEROUS_ACTIONS") == "1"


def _normalize_mode(mode: str) -> str:
    value = (mode or "").strip().lower()
    if value in {"off", "disabled", "0", "false", "unsafe"}:
        return "off"
    return "strict"


def _block(name: str, reason: str, args: dict[str, Any]) -> SafetyDecision:
    log_warning("safety block", tool=name, reason=reason, args=args)
    return SafetyDecision(False, reason=reason, severity="high")
