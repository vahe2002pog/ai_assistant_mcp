"""
UI Automation инструменты — управление окнами и UI через pywinauto.

Субагент: UIAgent
Инструменты: поиск окон, клики, ввод текста, скриншоты, управление окнами.
"""

from __future__ import annotations

import os
import re
import time
from typing import Optional

import pywinauto
from pywinauto import Desktop
from pywinauto.keyboard import send_keys as _send_keys

from .mcp_core import mcp


# ─── перевод человекочитаемых клавиш в синтаксис pywinauto ──────────────────

# Модификаторы → pywinauto-префиксы
_MODIFIERS = {"ctrl": "^", "control": "^", "alt": "%", "shift": "+"}

# Именованные клавиши → pywinauto-токены
_NAMED = {
    "enter": "{ENTER}", "return": "{ENTER}",
    "tab":   "{TAB}",
    "esc":   "{ESC}",   "escape": "{ESC}",
    "del":   "{DELETE}", "delete": "{DELETE}",
    "backspace": "{BACKSPACE}", "bs": "{BACKSPACE}",
    "home":  "{HOME}",  "end":   "{END}",
    "pgup":  "{PGUP}",  "pgdn":  "{PGDN}",
    "up":    "{UP}",    "down":  "{DOWN}",
    "left":  "{LEFT}",  "right": "{RIGHT}",
    "ins":   "{INSERT}", "insert": "{INSERT}",
    "win":   "{LWIN}",
    "space": " ",
    **{f"f{i}": f"{{F{i}}}" for i in range(1, 13)},
}


def _is_hotkey(keys: str) -> bool:
    """Возвращает True если строка — горячая клавиша вида 'Ctrl+X' или 'Enter'."""
    # Уже в pywinauto-формате — отправляем напрямую
    if any(c in keys for c in ("^", "%", "{")):
        return True
    parts = [p.strip().lower() for p in keys.split("+")]
    # Хотя бы одна часть — модификатор или именованная клавиша
    return any(p in _MODIFIERS or p in _NAMED for p in parts)


def _hotkey_to_pw(keys: str) -> str:
    """
    Переводит 'Ctrl+Shift+N', 'Alt+F4', 'Enter', 'Delete' → pywinauto send_keys.
    Уже готовый формат (^, %, {}) возвращает без изменений.
    """
    if any(c in keys for c in ("^", "%", "{")):
        return keys  # уже в нужном формате

    parts = [p.strip() for p in keys.split("+")]
    mods = ""
    key_part = ""
    for p in parts:
        low = p.lower()
        if low in _MODIFIERS:
            mods += _MODIFIERS[low]
        else:
            key_part = p

    if not key_part and mods:
        return mods  # только модификаторы — маловероятно, но не падаем

    low = key_part.lower()
    if low in _NAMED:
        return mods + _NAMED[low]
    if len(key_part) == 1:
        return mods + key_part.lower()
    # Неизвестное слово — оборачиваем в фигурные скобки
    return mods + f"{{{key_part.upper()}}}"


def _type_keys_with_text(keys: str) -> None:
    """
    Разбирает строку на чередующиеся части: обычный текст и {специальные клавиши}.
    - Обычный текст вставляется через буфер обмена (Ctrl+V) — сохраняет пробелы и Unicode.
    - Специальные клавиши отправляются через send_keys.
    Примеры:
        "Hello World{ENTER}" → clipboard_paste("Hello World") + send_keys("{ENTER}")
        "Ctrl+S"             → send_keys("^s")
        "{CTRL}a{DELETE}"    → send_keys("{CTRL}a{DELETE}")
    """
    import re
    import win32clipboard

    def _paste_text(text: str) -> None:
        """Помещает текст в буфер и вставляет через Ctrl+V."""
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32clipboard.CF_UNICODETEXT, text)
        win32clipboard.CloseClipboard()
        time.sleep(0.05)
        _send_keys("^v")
        time.sleep(0.05)

    # Горячая клавиша (Ctrl+A, Alt+F4, Enter, Delete...) — отправляем напрямую
    if _is_hotkey(keys):
        _send_keys(_hotkey_to_pw(keys))
        return

    # Обычный текст с возможными {SPECIAL} вставками
    # Разбиваем на токены: {ENTER}, {TAB} и т.д. vs обычный текст
    tokens = re.split(r'(\{[^}]+\})', keys)
    plain_buf = ""
    for token in tokens:
        if token.startswith("{") and token.endswith("}"):
            if plain_buf:
                _paste_text(plain_buf)
                plain_buf = ""
            _send_keys(token)
        else:
            plain_buf += token
    if plain_buf:
        _paste_text(plain_buf)


# ─── вспомогательные функции ─────────────────────────────────────────────────

def _all_windows():
    """Возвращает все видимые окна верхнего уровня."""
    try:
        return Desktop(backend="uia").windows()
    except Exception:
        return []


def _iter_all_wins():
    """
    Итерирует все окна: верхний уровень + их дочерние окна (диалоги, панели).
    Сначала возвращает верхний уровень (приоритет), потом дочерние.
    """
    top = _all_windows()
    yield from top
    for w in top:
        try:
            for child in w.descendants(control_type="Window"):
                yield child
        except Exception:
            continue


def _find_win(title_re: str = "", class_name: str = ""):
    """
    Находит окно по title_re (re.search, без учёта регистра) и/или class_name.
    Ищет сначала среди верхнеуровневых, затем среди дочерних (диалоги).
    """
    pat = re.compile(title_re, re.IGNORECASE) if title_re else None
    for w in _iter_all_wins():
        try:
            title = w.window_text() or ""
            cls   = w.class_name()  or ""
            if pat and not pat.search(title):
                continue
            if class_name and class_name.lower() not in cls.lower():
                continue
            return w
        except Exception:
            continue
    return None


# ─── снимок состояния окна ───────────────────────────────────────────────────

def _snapshot(title_re: str) -> set[str]:
    """
    Возвращает множество непустых текстов всех элементов окна.
    Используется для сравнения состояния до/после действия.
    """
    if not title_re:
        return set()
    win = _find_win(title_re)
    if win is None:
        return set()
    texts: set[str] = set()
    try:
        t = win.window_text()
        if t and t.strip():
            texts.add(t.strip())
    except Exception:
        pass
    try:
        for child in win.descendants():
            try:
                t = child.window_text()
                if t and t.strip():
                    texts.add(t.strip())
            except Exception:
                pass
    except Exception:
        pass
    return texts


def _diff_report(before: set[str], after: set[str], action_desc: str) -> str:
    """Формирует отчёт об изменениях состояния окна."""
    added   = sorted(after - before)
    removed = sorted(before - after)

    lines = [f"Выполнено: {action_desc}"]
    if not added and not removed:
        lines.append("Состояние окна не изменилось.")
    else:
        if added:
            lines.append("Появилось:")
            lines.extend(f"  + {t}" for t in added[:20])
        if removed:
            lines.append("Исчезло:")
            lines.extend(f"  - {t}" for t in removed[:20])
    return "\n".join(lines)


# ─── инструменты ─────────────────────────────────────────────────────────────

@mcp.tool
def ui_list_windows() -> str:
    """
    Возвращает список всех видимых окон и диалогов с заголовками и именами классов.
    Включает дочерние окна (диалоги, панели) — например, окно шрифта в Word.
    """
    try:
        lines = []
        seen = set()
        top = _all_windows()

        for w in top:
            try:
                title = w.window_text()
                cls = w.class_name()
                if title and title not in seen:
                    seen.add(title)
                    lines.append(f"• '{title}' [{cls}]")
            except Exception:
                pass
            # Дочерние окна (диалоги)
            try:
                for child in w.descendants(control_type="Window"):
                    try:
                        t = child.window_text()
                        c = child.class_name()
                        if t and t not in seen and t != w.window_text():
                            seen.add(t)
                            lines.append(f"  ↳ '{t}' [{c}]")
                    except Exception:
                        pass
            except Exception:
                pass

        return "Открытые окна:\n" + "\n".join(lines) if lines else "Нет видимых окон."
    except Exception as e:
        return f"Ошибка: {e}"


@mcp.tool
def ui_find_window(title_re: str = "", class_name: str = "") -> str:
    """
    Ищет окно по заголовку (регулярное выражение) или имени класса.
    Возвращает информацию о найденном окне.

    Args:
        title_re: Регулярное выражение для поиска по заголовку (например, ".*Chrome.*").
        class_name: Имя класса окна (например, "Notepad").
    """
    win = _find_win(title_re, class_name)
    if win is None:
        return f"Окно не найдено (title_re={title_re!r}, class_name={class_name!r})."
    try:
        rect = win.rectangle()
        return (
            f"Найдено: '{win.window_text()}' [{win.class_name()}]\n"
            f"Позиция: x={rect.left}, y={rect.top}, w={rect.width()}, h={rect.height()}"
        )
    except Exception as e:
        return f"Ошибка при получении свойств: {e}"


@mcp.tool
def ui_get_foreground() -> str:
    """Возвращает заголовок и класс активного (переднего) окна."""
    try:
        import uiautomation as auto
        win = auto.GetForegroundControl()
        return f"Активное окно: '{win.Name}' [{win.ClassName}]"
    except Exception as e:
        return f"Ошибка: {e}"


@mcp.tool
def ui_click(title_re: str = "", x: Optional[int] = None, y: Optional[int] = None,
             button: str = "left", double: bool = False) -> str:
    """
    Кликает по элементу в окне или по абсолютным экранным координатам.
    Автоматически фиксирует состояние окна до и после и сообщает об изменениях.

    Args:
        title_re: Заголовок целевого окна (регулярное выражение).
        x: Абсолютная X-координата экрана (если не указан title_re).
        y: Абсолютная Y-координата экрана.
        button: 'left' | 'right' | 'middle' (по умолчанию 'left').
        double: Двойной клик (по умолчанию False).
    """
    try:
        import pyautogui
        before = _snapshot(title_re)

        if x is not None and y is not None:
            if double:
                pyautogui.doubleClick(x, y, button=button)
            else:
                pyautogui.click(x, y, button=button)
            desc = f"Клик {'двойной ' if double else ''}по ({x}, {y})"
        else:
            win = _find_win(title_re)
            if win is None:
                return f"Окно '{title_re}' не найдено."
            win.set_focus()
            rect = win.rectangle()
            cx, cy = rect.left + rect.width() // 2, rect.top + rect.height() // 2
            if double:
                pyautogui.doubleClick(cx, cy, button=button)
            else:
                pyautogui.click(cx, cy, button=button)
            desc = f"Клик по центру окна '{win.window_text()}' ({cx}, {cy})"

        time.sleep(0.4)
        after = _snapshot(title_re)
        return _diff_report(before, after, desc)
    except Exception as e:
        return f"Ошибка клика: {e}"


@mcp.tool
def ui_click_element(text: str, title_re: str = "", double: bool = False) -> str:
    """
    Находит элемент управления с заданным текстом в окне и кликает по нему.
    Используй вместо ui_click когда знаешь текст кнопки/пункта меню/ссылки.
    Например: ui_click_element(text="Новый документ", title_re="Word")

    Args:
        text: Текст элемента (кнопки, пункта меню, ссылки и т.д.).
        title_re: Заголовок окна (регулярное выражение).
        double: Двойной клик (по умолчанию False).
    """
    import pyautogui

    win = _find_win(title_re) if title_re else None
    search_root = win if win is not None else None

    # Ищем через pywinauto descendants
    try:
        candidates = []
        if search_root is not None:
            for child in search_root.descendants():
                try:
                    ct = child.window_text()
                    if ct and text.lower() in ct.lower():
                        candidates.append(child)
                except Exception:
                    pass
        else:
            from pywinauto import Desktop
            desktop = Desktop(backend="uia")
            for w in desktop.windows():
                try:
                    for child in w.descendants():
                        try:
                            ct = child.window_text()
                            if ct and text.lower() in ct.lower():
                                candidates.append(child)
                        except Exception:
                            pass
                except Exception:
                    pass

        if not candidates:
            return f"Элемент с текстом '{text}' не найден в окне '{title_re}'."

        # Берём первый видимый
        target = candidates[0]
        rect = target.rectangle()
        cx = rect.left + rect.width() // 2
        cy = rect.top + rect.height() // 2

        before = _snapshot(title_re)
        if search_root:
            search_root.set_focus()
            time.sleep(0.1)

        if double:
            pyautogui.doubleClick(cx, cy)
        else:
            pyautogui.click(cx, cy)

        time.sleep(0.4)
        after = _snapshot(title_re)
        desc = f"Клик по '{target.window_text()}' ({cx}, {cy})"
        return _diff_report(before, after, desc)
    except Exception as e:
        return f"Ошибка клика по элементу: {e}"


@mcp.tool
def ui_send_keys(keys: str, title_re: str = "") -> str:
    """
    Отправляет нажатия клавиш в окно или глобально.
    Поддерживает специальные клавиши: {ENTER}, {TAB}, {ESC}, {CTRL}, {ALT}, {WIN}, {F1}-{F12}.
    Автоматически фиксирует состояние окна до и после и сообщает об изменениях.

    Args:
        keys: Строка клавиш. Пример: "Hello{ENTER}", "{CTRL}c", "{ALT}{F4}".
        title_re: Заголовок целевого окна (если пусто — текущее активное окно).
    """
    try:
        before = _snapshot(title_re)

        if title_re:
            win = _find_win(title_re)
            if win is None:
                return f"Окно '{title_re}' не найдено."
            win.set_focus()
            time.sleep(0.1)
        _type_keys_with_text(keys)

        time.sleep(0.4)
        after = _snapshot(title_re)
        return _diff_report(before, after, f"Клавиши {keys!r}")
    except Exception as e:
        return f"Ошибка отправки клавиш: {e}"


@mcp.tool
def ui_type_text(text: str, title_re: str = "", interval: float = 0.0) -> str:
    """
    Вводит текст в окно посимвольно (для полей ввода).
    Автоматически фиксирует состояние окна до и после и сообщает об изменениях.

    Args:
        text: Текст для ввода.
        title_re: Заголовок целевого окна.
        interval: Задержка между символами в секундах (по умолчанию 0).
    """
    try:
        import pyautogui
        before = _snapshot(title_re)

        if title_re:
            win = _find_win(title_re)
            if win:
                win.set_focus()
                time.sleep(0.1)
        pyautogui.typewrite(text, interval=interval)

        time.sleep(0.4)
        after = _snapshot(title_re)
        return _diff_report(before, after, f"Ввод текста {text!r}")
    except Exception as e:
        return f"Ошибка ввода текста: {e}"


@mcp.tool
def ui_get_text(title_re: str) -> str:
    """
    Получает текстовое содержимое окна.

    Args:
        title_re: Заголовок окна (регулярное выражение).
    """
    try:
        win = _find_win(title_re)
        if win is None:
            return f"Окно '{title_re}' не найдено."
        texts = []
        try:
            texts.append(win.window_text())
        except Exception:
            pass
        try:
            for child in win.descendants():
                t = child.window_text()
                if t and t.strip():
                    texts.append(t.strip())
        except Exception:
            pass
        return "\n".join(dict.fromkeys(texts)) if texts else "(нет текста)"
    except Exception as e:
        return f"Ошибка получения текста: {e}"


@mcp.tool
def ui_screenshot(title_re: str = "", save_path: str = "") -> str:
    """
    Делает скриншот окна или всего экрана.

    Args:
        title_re: Заголовок окна (если пусто — весь экран).
        save_path: Путь для сохранения PNG (если пусто — временный файл).
    """
    try:
        from PIL import ImageGrab
        import tempfile

        if not save_path:
            save_path = os.path.join(tempfile.gettempdir(), "assistant_screenshot.png")

        if title_re:
            win = _find_win(title_re)
            if win is None:
                return f"Окно '{title_re}' не найдено."
            rect = win.rectangle()
            img = ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom))
        else:
            img = ImageGrab.grab()

        img.save(save_path)
        return f"Скриншот сохранён: {save_path}"
    except Exception as e:
        return f"Ошибка скриншота: {e}"


@mcp.tool
def ui_close_window(title_re: str) -> str:
    """
    Закрывает окно по заголовку.

    Args:
        title_re: Заголовок окна (регулярное выражение).
    """
    try:
        win = _find_win(title_re)
        if win is None:
            return f"Окно '{title_re}' не найдено."
        win.close()
        return f"Окно '{title_re}' закрыто."
    except Exception as e:
        return f"Ошибка закрытия: {e}"


@mcp.tool
def ui_maximize_window(title_re: str) -> str:
    """Разворачивает окно на весь экран."""
    try:
        win = _find_win(title_re)
        if win is None:
            return f"Окно '{title_re}' не найдено."
        win.maximize()
        return f"Окно '{win.window_text()}' развёрнуто."
    except Exception as e:
        return f"Ошибка: {e}"


@mcp.tool
def ui_minimize_window(title_re: str) -> str:
    """Сворачивает окно в панель задач."""
    try:
        win = _find_win(title_re)
        if win is None:
            return f"Окно '{title_re}' не найдено."
        win.minimize()
        return f"Окно '{win.window_text()}' свёрнуто."
    except Exception as e:
        return f"Ошибка: {e}"


@mcp.tool
def ui_focus_window(title_re: str) -> str:
    """Переводит фокус на окно (выводит его на передний план)."""
    try:
        win = _find_win(title_re)
        if win is None:
            return f"Окно '{title_re}' не найдено."
        win.set_focus()
        return f"Фокус переведён на '{win.window_text()}'."
    except Exception as e:
        return f"Ошибка: {e}"


@mcp.tool
def ui_wait_for_window(title_re: str, timeout: int = 20) -> str:
    """
    Ожидает появления окна с заданным заголовком (поиск по подстроке, без учёта регистра).
    Если окно уже открыто — возвращает немедленно.

    Args:
        title_re: Подстрока или регулярное выражение заголовка окна.
        timeout: Максимальное время ожидания в секундах (по умолчанию 20).
    """
    start = time.time()
    while time.time() - start < timeout:
        win = _find_win(title_re)
        if win is not None:
            return f"Окно '{win.window_text()}' найдено (через {time.time()-start:.1f}с)."
        time.sleep(0.7)
    # Последняя попытка: показать все открытые окна для диагностики
    try:
        titles = [w.window_text() for w in _all_windows() if w.window_text()]
        hint = ", ".join(f"'{t}'" for t in titles[:8])
    except Exception:
        hint = "не удалось получить список окон"
    return f"Таймаут {timeout}с: окно '{title_re}' не найдено. Открытые окна: {hint}"


@mcp.tool
def ui_list_processes() -> str:
    """Возвращает список запущенных процессов (имя, PID)."""
    try:
        import psutil
        lines = [f"• {p.info['name']} (PID {p.info['pid']})"
                 for p in psutil.process_iter(["name", "pid"])
                 if p.info["name"]]
        return "Процессы:\n" + "\n".join(sorted(set(lines)))
    except Exception as e:
        return f"Ошибка: {e}"


@mcp.tool
def ui_clipboard_get() -> str:
    """Возвращает текущее содержимое буфера обмена."""
    try:
        import pyperclip
        text = pyperclip.paste()
        return f"Буфер обмена: {text!r}"
    except Exception:
        try:
            _send_keys("{CTRL}c")
            time.sleep(0.2)
            import win32clipboard
            win32clipboard.OpenClipboard()
            data = win32clipboard.GetClipboardData()
            win32clipboard.CloseClipboard()
            return f"Буфер обмена: {data!r}"
        except Exception as e:
            return f"Ошибка чтения буфера: {e}"


@mcp.tool
def ui_clipboard_set(text: str) -> str:
    """
    Устанавливает текст в буфер обмена.

    Args:
        text: Текст для помещения в буфер.
    """
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardText(text, win32clipboard.CF_UNICODETEXT)
        win32clipboard.CloseClipboard()
        return "Текст помещён в буфер обмена."
    except Exception as e:
        return f"Ошибка: {e}"


# ─── поиск элементов по типу ─────────────────────────────────────────────────

# Маппинг user-friendly типов → UIA control_type pywinauto
_ELEMENT_TYPES = {
    "button":    ["Button"],
    "кнопка":    ["Button"],
    "input":     ["Edit", "Document"],
    "поле":      ["Edit", "Document"],
    "ввод":      ["Edit", "Document"],
    "checkbox":  ["CheckBox"],
    "чекбокс":   ["CheckBox"],
    "флажок":    ["CheckBox"],
    "radio":     ["RadioButton"],
    "радио":     ["RadioButton"],
    "link":      ["Hyperlink"],
    "ссылка":    ["Hyperlink"],
    "list":      ["List", "ListItem"],
    "список":    ["List", "ListItem"],
    "combo":     ["ComboBox"],
    "выпадающий":["ComboBox"],
    "menu":      ["Menu", "MenuItem", "MenuBar"],
    "меню":      ["Menu", "MenuItem", "MenuBar"],
    "tab":       ["Tab", "TabItem"],
    "вкладка":   ["Tab", "TabItem"],
    "tree":      ["Tree", "TreeItem"],
    "дерево":    ["Tree", "TreeItem"],
    "image":     ["Image"],
    "картинка":  ["Image"],
    "text":      ["Text", "StaticText"],
    "текст":     ["Text", "StaticText"],
    "slider":    ["Slider"],
    "ползунок":  ["Slider"],
    "spinner":   ["Spinner"],
    "table":     ["Table", "DataGrid"],
    "таблица":   ["Table", "DataGrid"],
    "group":     ["Group"],
    "toolbar":   ["ToolBar"],
    "scroll":    ["ScrollBar"],
}

# Все UIA control_type для поиска "всех интерактивных"
_INTERACTIVE_TYPES = {
    "Button", "CheckBox", "RadioButton", "ComboBox",
    "Edit", "Document", "Hyperlink", "MenuItem",
    "ListItem", "TabItem", "TreeItem", "Slider",
    "Spinner", "ToolBar",
}


def _iter_elements(win, control_types: list[str]) -> list:
    """Возвращает список дочерних элементов заданных UIA-типов."""
    results = []
    try:
        for child in win.descendants():
            try:
                ct = child.element_info.control_type
                if ct in control_types:
                    results.append(child)
            except Exception:
                pass
    except Exception:
        pass
    return results


def _element_info(el, idx: int) -> str:
    """Форматирует строку описания элемента для вывода."""
    try:
        ct   = el.element_info.control_type or "?"
        name = el.window_text() or ""
        try:
            rect = el.rectangle()
            pos  = f"({rect.left},{rect.top})"
        except Exception:
            pos  = "(?)"
        # Дополнительно: enabled/visible
        try:
            enabled = "вкл" if el.is_enabled() else "выкл"
        except Exception:
            enabled = "?"
        label = f"[{idx}] {ct}"
        if name:
            label += f' "{name}"'
        label += f"  pos={pos}  {enabled}"
        return label
    except Exception:
        return f"[{idx}] (ошибка чтения элемента)"


@mcp.tool
def ui_find_inputs(title_re: str = "") -> str:
    """
    Находит все поля ввода текста в окне (Edit, Document).
    Возвращает список с индексами — используй индекс для ui_click_by_index.

    Args:
        title_re: Заголовок окна (регулярное выражение). Пусто = активное окно.
    """
    try:
        if title_re:
            win = _find_win(title_re)
            if win is None:
                return f"Окно '{title_re}' не найдено."
        else:
            win = _find_win("")
            if win is None:
                return "Нет активного окна."

        elements = _iter_elements(win, ["Edit", "Document"])
        if not elements:
            return f"Поля ввода не найдены в '{win.window_text()}'."

        lines = [f"Поля ввода в '{win.window_text()}' ({len(elements)}):"]
        for i, el in enumerate(elements):
            lines.append("  " + _element_info(el, i))
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка: {e}"


@mcp.tool
def ui_find_elements(element_type: str, title_re: str = "") -> str:
    """
    Находит все элементы заданного типа в окне.
    Типы: button/кнопка, input/поле, checkbox/флажок, radio, link/ссылка,
          combo/выпадающий, menu/меню, tab/вкладка, list/список,
          tree/дерево, slider/ползунок, text/текст, table/таблица и др.

    Args:
        element_type: Тип элемента (на русском или английском).
        title_re: Заголовок окна (регулярное выражение).
    """
    try:
        control_types = _ELEMENT_TYPES.get(element_type.lower().strip())
        if control_types is None:
            known = ", ".join(sorted(_ELEMENT_TYPES.keys()))
            return f"Неизвестный тип '{element_type}'. Известные типы: {known}"

        if title_re:
            win = _find_win(title_re)
            if win is None:
                return f"Окно '{title_re}' не найдено."
        else:
            top = _all_windows()
            win = top[0] if top else None
            if win is None:
                return "Нет активного окна."

        elements = _iter_elements(win, control_types)
        if not elements:
            return f"Элементы типа '{element_type}' не найдены в '{win.window_text()}'."

        lines = [f"Элементы '{element_type}' в '{win.window_text()}' ({len(elements)}):"]
        for i, el in enumerate(elements):
            lines.append("  " + _element_info(el, i))
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка: {e}"


@mcp.tool
def ui_list_interactive(title_re: str = "", max_items: int = 60) -> str:
    """
    Показывает все интерактивные элементы окна с индексами и типами.
    Охватывает: кнопки, поля ввода, чекбоксы, радиокнопки, ссылки,
    выпадающие списки, пункты меню, вкладки, слайдеры и т.д.
    Используй индекс для ui_click_by_index или координаты pos= для ui_click.

    Args:
        title_re: Заголовок окна (регулярное выражение). Пусто = переднее окно.
        max_items: Максимальное количество элементов (по умолчанию 60).
    """
    try:
        if title_re:
            win = _find_win(title_re)
            if win is None:
                return f"Окно '{title_re}' не найдено."
        else:
            top = _all_windows()
            win = top[0] if top else None
            if win is None:
                return "Нет активного окна."

        elements = _iter_elements(win, list(_INTERACTIVE_TYPES))
        if not elements:
            return f"Интерактивных элементов не найдено в '{win.window_text()}'."

        # Сортируем по позиции (сверху-вниз, слева-направо)
        def _sort_key(el):
            try:
                r = el.rectangle()
                return (r.top, r.left)
            except Exception:
                return (9999, 9999)

        elements.sort(key=_sort_key)
        elements = elements[:max_items]

        lines = [f"Интерактивные элементы '{win.window_text()}' ({len(elements)}):"]
        for i, el in enumerate(elements):
            lines.append("  " + _element_info(el, i))

        if len(elements) == max_items:
            lines.append(f"  … (показано {max_items}, увеличь max_items для полного списка)")
        return "\n".join(lines)
    except Exception as e:
        return f"Ошибка: {e}"


@mcp.tool
def ui_click_by_index(index: int, title_re: str = "", element_type: str = "",
                      double: bool = False) -> str:
    """
    Кликает по элементу из списка ui_list_interactive / ui_find_elements по индексу.

    Args:
        index: Индекс элемента из списка (как показан ui_list_interactive).
        title_re: Заголовок окна (регулярное выражение).
        element_type: Тип элемента для фильтрации (как в ui_find_elements).
                      Если пусто — используется полный список интерактивных.
        double: Двойной клик (по умолчанию False).
    """
    import pyautogui

    try:
        if title_re:
            win = _find_win(title_re)
            if win is None:
                return f"Окно '{title_re}' не найдено."
        else:
            top = _all_windows()
            win = top[0] if top else None
            if win is None:
                return "Нет активного окна."

        if element_type:
            control_types = _ELEMENT_TYPES.get(element_type.lower().strip())
            if control_types is None:
                return f"Неизвестный тип '{element_type}'."
            elements = _iter_elements(win, control_types)
        else:
            elements = _iter_elements(win, list(_INTERACTIVE_TYPES))
            elements.sort(key=lambda el: (
                el.rectangle().top if hasattr(el, 'rectangle') else 9999,
                el.rectangle().left if hasattr(el, 'rectangle') else 9999,
            ))

        if index < 0 or index >= len(elements):
            return f"Индекс {index} вне диапазона (0–{len(elements)-1})."

        el = elements[index]
        rect = el.rectangle()
        cx = rect.left + rect.width() // 2
        cy = rect.top + rect.height() // 2

        before = _snapshot(title_re)
        try:
            win.set_focus()
            time.sleep(0.05)
        except Exception:
            pass

        if double:
            pyautogui.doubleClick(cx, cy)
        else:
            pyautogui.click(cx, cy)

        time.sleep(0.4)
        after = _snapshot(title_re)
        desc = f"Клик[{index}] по '{el.window_text() or el.element_info.control_type}' ({cx},{cy})"
        return _diff_report(before, after, desc)
    except Exception as e:
        return f"Ошибка: {e}"
