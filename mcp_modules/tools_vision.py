"""
Vision инструменты — скриншоты и работа с буфером обмена.

Используются VisionAgent для захвата экрана и копирования текста.
"""

from __future__ import annotations

import base64
import os
import tempfile
from typing import Optional

from .mcp_core import mcp


def _grab_image(window_title: str = "", region: Optional[tuple] = None) -> bytes:
    """Internal: capture screen or window, return PNG bytes."""
    from PIL import ImageGrab

    if region:
        img = ImageGrab.grab(bbox=region)
    elif window_title:
        try:
            from pywinauto import Desktop
            wins = Desktop(backend="uia").windows()
            match = None
            for w in wins:
                try:
                    if window_title.lower() in w.window_text().lower():
                        match = w
                        break
                except Exception:
                    pass
            if match:
                r = match.rectangle()
                img = ImageGrab.grab(bbox=(r.left, r.top, r.right, r.bottom))
            else:
                img = ImageGrab.grab()
        except Exception:
            img = ImageGrab.grab()
    else:
        img = ImageGrab.grab()

    import io
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


@mcp.tool
def screen_capture(window_title: str = "", save_path: str = "") -> str:
    """
    Делает скриншот всего экрана или конкретного окна, возвращает путь к файлу.

    Args:
        window_title: Часть заголовка окна (если пусто — весь экран).
        save_path: Путь для сохранения PNG (если пусто — временный файл).
    """
    try:
        png_bytes = _grab_image(window_title)
        if not save_path:
            save_path = os.path.join(tempfile.gettempdir(), "vision_screenshot.png")
        with open(save_path, "wb") as f:
            f.write(png_bytes)
        return f"Скриншот сохранён: {save_path}"
    except Exception as e:
        return f"Ошибка скриншота: {e}"


@mcp.tool
def screen_capture_region(x: int, y: int, width: int, height: int, save_path: str = "") -> str:
    """
    Делает скриншот прямоугольной области экрана.

    Args:
        x: Левый край области в пикселях.
        y: Верхний край области в пикселях.
        width: Ширина области в пикселях.
        height: Высота области в пикселях.
        save_path: Путь для сохранения PNG (если пусто — временный файл).
    """
    try:
        png_bytes = _grab_image(region=(x, y, x + width, y + height))
        if not save_path:
            save_path = os.path.join(tempfile.gettempdir(), "vision_region.png")
        with open(save_path, "wb") as f:
            f.write(png_bytes)
        return f"Скриншот области сохранён: {save_path}"
    except Exception as e:
        return f"Ошибка скриншота области: {e}"


@mcp.tool
def clipboard_copy(text: str) -> str:
    """
    Копирует текст в буфер обмена Windows.

    Args:
        text: Текст для копирования.
    """
    try:
        import ctypes

        CF_UNICODETEXT = 13
        GMEM_MOVEABLE = 0x0002

        data = text.encode("utf-16-le") + b"\x00\x00"
        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32

        user32.OpenClipboard(0)
        user32.EmptyClipboard()
        h = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        ptr = kernel32.GlobalLock(h)
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
        user32.CloseClipboard()
        return f"Скопировано в буфер обмена ({len(text)} символов)"
    except Exception as e:
        return f"Ошибка копирования: {e}"


def capture_base64(window_title: str = "") -> str:
    """
    Internal helper: returns full-screen (or window) screenshot as base64 PNG string.
    Used by VisionAgent to embed image in LLM vision message.
    """
    png_bytes = _grab_image(window_title)
    return base64.b64encode(png_bytes).decode("ascii")
