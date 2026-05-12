from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from typing import Any

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_DIR = os.path.join(_ROOT, "logs")
_ERROR_LOG = os.path.join(_LOG_DIR, "assistant_errors.log")
_SETUP_DONE = False


def setup_error_logging() -> str:
    """Configure process-wide error logging to logs/assistant_errors.log."""
    global _SETUP_DONE
    if _SETUP_DONE:
        return _ERROR_LOG

    os.makedirs(_LOG_DIR, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(logging.INFO)

    existing = [
        h for h in root.handlers
        if isinstance(h, RotatingFileHandler)
        and getattr(h, "baseFilename", "") == os.path.abspath(_ERROR_LOG)
    ]
    if not existing:
        handler = RotatingFileHandler(
            _ERROR_LOG,
            maxBytes=int(os.environ.get("COMPASS_ERROR_LOG_MAX_BYTES", "1048576")),
            backupCount=int(os.environ.get("COMPASS_ERROR_LOG_BACKUPS", "5")),
            encoding="utf-8",
        )
        handler.setLevel(logging.WARNING)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s [%(name)s] %(message)s"
        ))
        root.addHandler(handler)

    def _excepthook(exc_type, exc, tb):
        logging.getLogger("assistant.errors").exception(
            "unhandled exception", exc_info=(exc_type, exc, tb)
        )
        if getattr(sys, "__excepthook__", None):
            sys.__excepthook__(exc_type, exc, tb)

    def _threading_excepthook(args):
        logging.getLogger("assistant.errors").exception(
            "unhandled thread exception: %s",
            getattr(args.thread, "name", "<unknown>"),
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )
        original = getattr(threading, "__excepthook__", None)
        if original:
            original(args)

    sys.excepthook = _excepthook
    if hasattr(threading, "excepthook"):
        threading.excepthook = _threading_excepthook

    for noisy in ("httpx", "httpcore", "openai"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _SETUP_DONE = True
    return _ERROR_LOG


def log_error(context: str, exc: BaseException | None = None, **details: Any) -> None:
    setup_error_logging()
    logger = logging.getLogger("assistant.errors")
    suffix = ""
    if details:
        safe = {k: _redact(v) for k, v in details.items()}
        suffix = f" | details={safe!r}"
    if exc is None:
        logger.error("%s%s", context, suffix)
    else:
        logger.exception("%s%s", context, suffix, exc_info=(type(exc), exc, exc.__traceback__))


def log_warning(context: str, **details: Any) -> None:
    setup_error_logging()
    safe = {k: _redact(v) for k, v in details.items()}
    logging.getLogger("assistant.errors").warning("%s | details=%r", context, safe)


def _redact(value: Any) -> Any:
    text = str(value)
    if len(text) > 1200:
        text = text[:1200] + "...<truncated>"
    lowered = text.lower()
    secret_markers = ("api_key", "authorization", "bearer ", "password", "token", "secret")
    if any(marker in lowered for marker in secret_markers):
        return "<redacted>"
    return text
