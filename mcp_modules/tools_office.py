"""
Office COM API tools — Excel, Word, PowerPoint, Outlook через win32com.client.

Порт функционала из D:\\Desktop\\OfficeMCP (Officer.py + OfficeMCP.py) в качестве
набора MCP-tool'ов.
"""

from __future__ import annotations

import os
from typing import Tuple

from .mcp_core import mcp
from .office_core import Officer, SUPPORTED_APPS


# ─────────────────────────────────────────────────────────────────
#  Пути: Desktop/Documents с учётом OneDrive-редиректа
# ─────────────────────────────────────────────────────────────────

def _known_folder(kind: str) -> str:
    """Возвращает реальный путь к Desktop/Documents (с учётом OneDrive)."""
    import ctypes
    from ctypes import wintypes
    # CSIDL: 0x0000=Desktop, 0x0005=Personal (Documents)
    csidl = {"desktop": 0x0000, "documents": 0x0005}.get(kind.lower(), 0x0000)
    buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
    try:
        ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buf)
        if buf.value and os.path.isdir(buf.value):
            return buf.value
    except Exception:
        pass
    # Фолбэки.
    home = os.path.expanduser("~")
    for cand in (
        os.path.join(home, "OneDrive", "Desktop" if kind == "desktop" else "Documents"),
        os.path.join(home, "OneDrive", "Рабочий стол" if kind == "desktop" else "Документы"),
        os.path.join(home, "Desktop" if kind == "desktop" else "Documents"),
    ):
        if os.path.isdir(cand):
            return cand
    return home


def _resolve_office_path(file_path: str) -> str:
    """Разворачивает ~ и специальные имена (Desktop/Documents) в абсолютный путь.
    Создаёт родительскую директорию, если её нет."""
    p = os.path.expandvars(os.path.expanduser(file_path or ""))
    if not p:
        return p
    # Если путь начинается с Desktop/Documents без абсолютного префикса — разворачиваем.
    parts = p.replace("\\", "/").split("/", 1)
    first = parts[0].lower()
    if first in ("desktop", "documents", "рабочий стол", "документы") and not os.path.isabs(p):
        kind = "desktop" if first in ("desktop", "рабочий стол") else "documents"
        p = os.path.join(_known_folder(kind), parts[1] if len(parts) > 1 else "")
    p = os.path.abspath(p)
    parent = os.path.dirname(p)
    if parent and not os.path.isdir(parent):
        try:
            os.makedirs(parent, exist_ok=True)
        except Exception:
            pass
    return p


@mcp.tool
def office_docs_search(query: str, k: int = 3) -> str:
    """Ищет релевантные фрагменты в vault-хранилище (документация Office COM,
    загруженная пользователем).

    Вызывай ПЕРЕД office_run_python, если не уверен в именах методов/константах
    COM API (Excel/Word/PowerPoint/Outlook). Возвращает top-k текстовых фрагментов
    из vault/Attachments и vault/Knowledge, отсортированных по релевантности.

    query: естественный запрос (например, "как добавить диаграмму в Excel",
           "Word Paragraph Style constants", "Outlook CreateItem types").
    k: сколько фрагментов вернуть (1..10).
    """
    try:
        from ui_automation.rag import vault_manager as _vm
    except Exception as e:
        return f"Ошибка office_docs_search: vault недоступен ({e})"
    k = max(1, min(10, int(k or 3)))
    docs = _vm.search(query, k=k)
    if not docs:
        return "(ничего не найдено в vault-хранилище)"
    parts: list[str] = []
    for i, d in enumerate(docs, 1):
        src = (getattr(d, "metadata", {}) or {}).get("source") or \
              (getattr(d, "metadata", {}) or {}).get("rel_path") or ""
        text = (getattr(d, "page_content", "") or "").strip()
        if len(text) > 1200:
            text = text[:1200] + "…"
        parts.append(f"── [{i}] {src} ──\n{text}")
    return "\n\n".join(parts)


@mcp.tool
def com_run_python(code: str, data: str = "") -> str:
    """Выполняет произвольный Python-код с доступом к COM-серверам Windows
    (не только Office). Используй, когда задача — не Office (Shell, WMI,
    SAPI, Adobe, AutoCAD и т.п.); для Office есть office_run_python.

    В namespace доступны:
      win32com       — модуль win32com (например, win32com.client.Dispatch(...))
      pythoncom      — низкоуровневый модуль
      Dispatch(p)    — ярлык win32com.client.Dispatch
      GetActive(p)   — ярлык win32com.client.GetActiveObject
      resolve_path(p) — абсолютный путь (OneDrive-aware), создаёт parent dir
      data           — входная строка
      output         — возвращаемое значение

    Типичные COM-ProgID:
      "WScript.Shell"         — env, shortcuts, Run, RegRead/Write
      "Shell.Application"     — Explorer-операции, zip-папки, корзина
      "winmgmts:"             — WMI (через GetObject, не Dispatch)
      "SAPI.SpVoice"          — TTS
      "Scripting.FileSystemObject" — файлы/папки через COM
      "InternetExplorer.Application" — устаревшее, но ещё работает
      "AcroExch.App" / "Photoshop.Application" / "AutoCAD.Application" — если установлены

    Пример:
      shell = Dispatch("WScript.Shell")
      output = shell.ExpandEnvironmentStrings("%USERPROFILE%")

    Если не уверен в ProgID/методах — используй office_docs_search.
    ВНИМАНИЕ: код выполняется в текущем процессе без песочницы.
    """
    try:
        import win32com
        import win32com.client
        import pythoncom
    except Exception as e:
        return f"Ошибка com_run_python: не удалось импортировать win32com ({e})"
    namespace = {
        "win32com": win32com,
        "pythoncom": pythoncom,
        "Dispatch": win32com.client.Dispatch,
        "GetActive": win32com.client.GetActiveObject,
        "resolve_path": _resolve_office_path,
        "data": data,
        "output": "",
        "__builtins__": __builtins__,
    }
    try:
        exec(code, namespace)
        return f"OK: {namespace.get('output', '')}"
    except Exception as e:
        return f"Ошибка com_run_python: {type(e).__name__}: {e}"


@mcp.tool
def office_close_dialogs(app_name: str = "") -> str:
    """Аварийно закрывает (Escape/Cancel) модальные диалоги Office.

    ИСПОЛЬЗУЙ ТОЛЬКО КАК ПОСЛЕДНЕЕ СРЕДСТВО — этот тул ОТМЕНЯЕТ диалог,
    а не подтверждает. Подходит для ситуаций, когда диалог НЕ нужен и
    блокирует COM (остался висеть от предыдущего запуска, Protected View,
    «Восстановление документа», подтверждение о перезаписи во время авто-
    сохранения).

    Если пользователь ПРОСИЛ действие, которое вызывает диалог (например,
    «сохрани файл» → окно «Сохранение документа») — НЕ вызывай этот тул.
    Взаимодействуй с диалогом через ui_* (ui_list_interactive(title_re=...),
    ui_click_element(text="Сохранить", title_re=...), ui_send_keys) или
    через нативный COM-метод (doc.SaveAs2(path) вместо Save As-диалога).

    app_name: Word | Excel | PowerPoint | Outlook. Пусто = все Office-процессы.
    """
    try:
        import win32gui
        import win32process
        import win32con
        import win32api

        targets = [app_name] if app_name else list(SUPPORTED_APPS)
        target_pids: set[int] = set()
        for name in targets:
            try:
                app = Officer.application(name)
                if app is None:
                    continue
                hwnd_main = app.Hwnd
                _, pid = win32process.GetWindowThreadProcessId(hwnd_main)
                target_pids.add(pid)
            except Exception:
                continue

        if not target_pids:
            return "Нет запущенных Office-процессов"

        closed: list[str] = []

        def _enum(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid not in target_pids:
                    return
                cls = win32gui.GetClassName(hwnd)
                title = win32gui.GetWindowText(hwnd)
                # Office main windows: OpusApp/XLMAIN/PPTFrameClass/rctrl_renwnd32.
                # Modal dialogs: #32770 (standard Windows dialog class) или
                # NUIDialog/bosa_sdm_* (встроенные Office-диалоги).
                is_dialog = (cls == "#32770"
                             or cls.startswith("NUIDialog")
                             or cls.startswith("bosa_sdm_"))
                if not is_dialog:
                    return
                # Попытка 1: Escape (Cancel для большинства диалогов).
                win32api.PostMessage(hwnd, win32con.WM_KEYDOWN, win32con.VK_ESCAPE, 0)
                win32api.PostMessage(hwnd, win32con.WM_KEYUP, win32con.VK_ESCAPE, 0)
                # Попытка 2: WM_CLOSE — на случай, если Escape проигнорировали.
                win32api.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)
                closed.append(f"{cls!r}: {title!r}")
            except Exception:
                pass

        win32gui.EnumWindows(_enum, None)
        if not closed:
            return "Открытых диалогов Office не найдено"
        return f"Закрыто диалогов: {len(closed)}\n" + "\n".join(closed)
    except Exception as e:
        return f"Ошибка office_close_dialogs: {e}"


@mcp.tool
def office_user_folder(kind: str = "desktop") -> str:
    """Возвращает реальный путь к пользовательской папке (учитывает OneDrive-редирект).

    kind: 'desktop' | 'documents'
    """
    return _known_folder(kind)


def _open_excel(file_path: str) -> Tuple[object, object, bool]:
    """
    Открывает Excel и workbook.
    Возвращает (excel_app, workbook, created_new_instance).
    """
    import win32com.client
    abs_path = _resolve_office_path(file_path) if file_path else ""
    try:
        excel = win32com.client.GetActiveObject("Excel.Application")
        created = False
    except Exception:
        excel = win32com.client.Dispatch("Excel.Application")
        excel.Visible = False
        excel.DisplayAlerts = False
        created = True

    if abs_path and os.path.exists(abs_path):
        # Check if already open
        for wb in excel.Workbooks:
            if os.path.normcase(wb.FullName) == os.path.normcase(abs_path):
                return excel, wb, created
        wb = excel.Workbooks.Open(abs_path)
    elif abs_path:
        # File not found on disk — try matching an already-open workbook by name
        name_only = os.path.basename(abs_path).lower()
        for wb in excel.Workbooks:
            try:
                if os.path.basename(wb.Name).lower() == name_only:
                    return excel, wb, created
            except Exception:
                pass
        # Fall back to active workbook
        try:
            wb = excel.ActiveWorkbook
            if wb is not None:
                return excel, wb, created
        except Exception:
            pass
        return excel, None, created
    else:
        wb = excel.ActiveWorkbook
    return excel, wb, created


def _open_word(file_path: str) -> Tuple[object, object, bool]:
    """
    Открывает Word и document.
    Возвращает (word_app, document, created_new_instance).
    """
    import win32com.client
    abs_path = _resolve_office_path(file_path) if file_path else ""
    try:
        word = win32com.client.GetActiveObject("Word.Application")
        created = False
    except Exception:
        word = win32com.client.Dispatch("Word.Application")
        word.Visible = False
        word.DisplayAlerts = False
        created = True

    if abs_path and os.path.exists(abs_path):
        for doc in word.Documents:
            if os.path.normcase(doc.FullName) == os.path.normcase(abs_path):
                return word, doc, created
        doc = word.Documents.Open(abs_path)
    elif abs_path:
        # File not found on disk — try matching an already-open document by name
        name_only = os.path.basename(abs_path).lower()
        for doc in word.Documents:
            try:
                doc_name = os.path.basename(doc.Name).lower()
                if doc_name == name_only:
                    return word, doc, created
            except Exception:
                pass
        # Nothing matched — fall back to active document if any
        try:
            doc = word.ActiveDocument
            if doc is not None:
                return word, doc, created
        except Exception:
            pass
        return word, None, created
    else:
        doc = word.ActiveDocument
    return word, doc, created


def _normalize_excel_data(data) -> list:
    """Приводит результат Value к списку строк (список списков)."""
    if data is None:
        return []
    if not isinstance(data, tuple):
        return [[data]]
    if not data:
        return []
    if not isinstance(data[0], tuple):
        return [list(data)]
    return [list(row) for row in data]


# ─────────────────────────────────────────────────────────────────
#  Excel
# ─────────────────────────────────────────────────────────────────

@mcp.tool
def excel_get_sheets(file_path: str) -> str:
    """Возвращает список листов Excel-файла.

    file_path: путь к Excel-файлу (.xlsx, .xls)
    """
    try:
        excel, wb, created = _open_excel(file_path)
        if wb is None:
            return f"Файл не найден: {file_path}"
        sheets = [wb.Sheets(i + 1).Name for i in range(wb.Sheets.Count)]
        if created:
            try:
                wb.Close(False)
                excel.Quit()
            except Exception:
                pass
        return "Листы: " + ", ".join(sheets)
    except Exception as e:
        return f"Ошибка excel_get_sheets: {e}"


@mcp.tool
def excel_read_sheet(file_path: str, sheet_name: str = "", range_addr: str = "") -> str:
    """Читает данные из листа Excel и возвращает в виде таблицы с разделителями.

    file_path: путь к Excel-файлу
    sheet_name: имя листа (если пусто — активный лист)
    range_addr: диапазон ячеек например 'A1:D10' (если пусто — весь используемый диапазон)
    """
    try:
        excel, wb, created = _open_excel(file_path)
        if wb is None:
            return f"Файл не найден: {file_path}"
        ws = wb.Sheets(sheet_name) if sheet_name else wb.ActiveSheet
        rng = ws.Range(range_addr) if range_addr else ws.UsedRange
        data = rng.Value
        rows = _normalize_excel_data(data)
        if not rows:
            return "Диапазон пуст"
        lines = ["\t".join(str(v) if v is not None else "" for v in row) for row in rows]
        result = "\n".join(lines)
        if created:
            try:
                wb.Close(False)
                excel.Quit()
            except Exception:
                pass
        return result
    except Exception as e:
        return f"Ошибка excel_read_sheet: {e}"


@mcp.tool
def excel_write_cell(file_path: str, sheet_name: str, cell: str, value: str) -> str:
    """Записывает значение в ячейку Excel и сохраняет файл.

    file_path: путь к Excel-файлу
    sheet_name: имя листа
    cell: адрес ячейки например 'A1', 'B3'
    value: значение для записи
    """
    try:
        excel, wb, created = _open_excel(file_path)
        if wb is None:
            return f"Файл не найден: {file_path}"
        ws = wb.Sheets(sheet_name) if sheet_name else wb.ActiveSheet
        ws.Range(cell).Value = value
        wb.Save()
        if created:
            try:
                wb.Close(True)
                excel.Quit()
            except Exception:
                pass
        return f"Записано '{value}' в {cell} листа '{ws.Name}'"
    except Exception as e:
        return f"Ошибка excel_write_cell: {e}"


@mcp.tool
def excel_write_range(file_path: str, sheet_name: str, start_cell: str, csv_data: str) -> str:
    """Записывает таблицу в формате CSV в диапазон Excel начиная с указанной ячейки.

    file_path: путь к Excel-файлу
    sheet_name: имя листа
    start_cell: стартовая ячейка например 'A1'
    csv_data: данные в формате CSV (строки разделены \\n, колонки — запятой или табом)
    """
    try:
        excel, wb, created = _open_excel(file_path)
        if wb is None:
            return f"Файл не найден: {file_path}"
        ws = wb.Sheets(sheet_name) if sheet_name else wb.ActiveSheet
        rng_start = ws.Range(start_cell)
        start_row = rng_start.Row
        start_col = rng_start.Column

        sep = "\t" if "\t" in csv_data else ","
        rows_written = 0
        for i, line in enumerate(csv_data.splitlines()):
            if not line.strip():
                continue
            cells = line.split(sep)
            for j, val in enumerate(cells):
                ws.Cells(start_row + i, start_col + j).Value = val.strip()
            rows_written += 1

        wb.Save()
        if created:
            try:
                wb.Close(True)
                excel.Quit()
            except Exception:
                pass
        return f"Записано {rows_written} строк начиная с {start_cell}"
    except Exception as e:
        return f"Ошибка excel_write_range: {e}"


@mcp.tool
def excel_apply_formula(file_path: str, sheet_name: str, cell: str, formula: str) -> str:
    """Вставляет формулу в ячейку Excel.

    file_path: путь к Excel-файлу
    sheet_name: имя листа
    cell: адрес ячейки например 'C1'
    formula: формула Excel например '=SUM(A1:B1)' или '=A1*2'
    """
    try:
        excel, wb, created = _open_excel(file_path)
        if wb is None:
            return f"Файл не найден: {file_path}"
        ws = wb.Sheets(sheet_name) if sheet_name else wb.ActiveSheet
        ws.Range(cell).Formula = formula
        wb.Save()
        if created:
            try:
                wb.Close(True)
                excel.Quit()
            except Exception:
                pass
        return f"Формула '{formula}' записана в {cell}"
    except Exception as e:
        return f"Ошибка excel_apply_formula: {e}"


# ─────────────────────────────────────────────────────────────────
#  Word
# ─────────────────────────────────────────────────────────────────

@mcp.tool
def word_create_document(file_path: str, text: str = "") -> str:
    """Создаёт новый Word-документ (.docx) и сохраняет по указанному пути.

    file_path: путь к .docx. Поддерживается '~', 'Desktop/...', 'Documents/...'
               (автоматически разворачиваются с учётом OneDrive-редиректа).
    text: начальный текст документа (опционально).
    """
    try:
        import win32com.client
        abs_path = _resolve_office_path(file_path)
        if not abs_path.lower().endswith((".docx", ".doc")):
            abs_path += ".docx"
        try:
            word = win32com.client.GetActiveObject("Word.Application")
        except Exception:
            word = win32com.client.Dispatch("Word.Application")
        word.DisplayAlerts = 0
        doc = word.Documents.Add()
        if text:
            doc.Content.Text = text
        # 16 = wdFormatXMLDocument (.docx)
        doc.SaveAs2(abs_path, FileFormat=16)
        return f"Создан документ: {abs_path}"
    except Exception as e:
        return f"Ошибка word_create_document: {e}"


@mcp.tool
def excel_create_workbook(file_path: str) -> str:
    """Создаёт новую Excel-книгу (.xlsx) и сохраняет по указанному пути.

    file_path: путь к .xlsx. Поддерживается '~', 'Desktop/...', 'Documents/...'.
    """
    try:
        import win32com.client
        abs_path = _resolve_office_path(file_path)
        if not abs_path.lower().endswith((".xlsx", ".xls", ".xlsm")):
            abs_path += ".xlsx"
        try:
            excel = win32com.client.GetActiveObject("Excel.Application")
        except Exception:
            excel = win32com.client.Dispatch("Excel.Application")
        excel.DisplayAlerts = False
        wb = excel.Workbooks.Add()
        # 51 = xlOpenXMLWorkbook (.xlsx)
        wb.SaveAs(abs_path, FileFormat=51)
        return f"Создана книга: {abs_path}"
    except Exception as e:
        return f"Ошибка excel_create_workbook: {e}"


@mcp.tool
def word_read_document(file_path: str) -> str:
    """Читает весь текст из Word-документа.

    file_path: путь к Word-документу (.docx, .doc)
    """
    try:
        word, doc, created = _open_word(file_path)
        if doc is None:
            return f"Файл не найден: {file_path}"
        text = doc.Content.Text
        if created:
            try:
                doc.Close(False)
                word.Quit()
            except Exception:
                pass
        return text if text else "(документ пуст)"
    except Exception as e:
        return f"Ошибка word_read_document: {e}"


@mcp.tool
def word_write_text(file_path: str, text: str, position: str = "end") -> str:
    """Вставляет текст в Word-документ и сохраняет.

    file_path: путь к Word-документу
    text: текст для вставки
    position: 'end' — в конец документа, 'start' — в начало
    """
    try:
        word, doc, created = _open_word(file_path)
        if doc is None:
            return f"Файл не найден: {file_path}"
        rng = doc.Content
        if position == "start":
            rng.Collapse(1)  # wdCollapseStart
        else:
            rng.Collapse(0)  # wdCollapseEnd
        rng.InsertAfter(text)
        doc.Save()
        if created:
            try:
                doc.Close(True)
                word.Quit()
            except Exception:
                pass
        return f"Текст вставлен в позиции '{position}'"
    except Exception as e:
        return f"Ошибка word_write_text: {e}"


@mcp.tool
def word_find_replace(file_path: str, find_text: str, replace_text: str) -> str:
    """Находит и заменяет текст в Word-документе.

    file_path: путь к Word-документу
    find_text: текст для поиска
    replace_text: текст для замены
    """
    try:
        word, doc, created = _open_word(file_path)
        if doc is None:
            return f"Файл не найден: {file_path}"
        find = doc.Content.Find
        find.ClearFormatting()
        find.Replacement.ClearFormatting()
        find.Text = find_text
        find.Replacement.Text = replace_text
        # wdReplaceAll = 2
        replaced = find.Execute(Replace=2)
        doc.Save()
        if created:
            try:
                doc.Close(True)
                word.Quit()
            except Exception:
                pass
        return f"Замена '{find_text}' → '{replace_text}' выполнена"
    except Exception as e:
        return f"Ошибка word_find_replace: {e}"


@mcp.tool
def word_get_tables(file_path: str) -> str:
    """Возвращает содержимое всех таблиц Word-документа.

    file_path: путь к Word-документу
    """
    try:
        word, doc, created = _open_word(file_path)
        if doc is None:
            return f"Файл не найден: {file_path}"
        result = []
        for t_idx in range(doc.Tables.Count):
            table = doc.Tables(t_idx + 1)
            result.append(f"\n=== Таблица {t_idx + 1} ===")
            for r in range(table.Rows.Count):
                row_cells = []
                for c in range(table.Columns.Count):
                    try:
                        cell_text = table.Cell(r + 1, c + 1).Range.Text.rstrip("\r\x07")
                        row_cells.append(cell_text)
                    except Exception:
                        row_cells.append("")
                result.append(" | ".join(row_cells))
        if created:
            try:
                doc.Close(False)
                word.Quit()
            except Exception:
                pass
        return "\n".join(result) if result else "Таблиц не найдено"
    except Exception as e:
        return f"Ошибка word_get_tables: {e}"


# ─────────────────────────────────────────────────────────────────
#  App management (Excel / Word / PowerPoint / Outlook)
# ─────────────────────────────────────────────────────────────────

@mcp.tool
def office_available_apps() -> str:
    """Возвращает список установленных Office-приложений (из поддерживаемых: Word/Excel/PowerPoint/Outlook)."""
    apps = Officer.available_apps()
    return ", ".join(apps) if apps else "Не найдено ни одного поддерживаемого Office-приложения"


@mcp.tool
def office_running_apps() -> str:
    """Возвращает список запущенных Office-приложений."""
    apps = Officer.running_apps()
    return ", ".join(apps) if apps else "Нет запущенных Office-приложений"


@mcp.tool
def office_is_available(app_name: str) -> str:
    """Проверяет, установлено ли приложение.

    app_name: Word | Excel | PowerPoint | Outlook
    """
    if app_name not in SUPPORTED_APPS:
        return f"Неподдерживаемое приложение '{app_name}'. Допустимы: {', '.join(SUPPORTED_APPS)}"
    return "Да" if Officer.is_available(app_name) else "Нет"


@mcp.tool
def office_launch(app_name: str, visible: bool = True) -> str:
    """Запускает Office-приложение (или берёт уже запущенный экземпляр).

    app_name: Word | Excel | PowerPoint | Outlook
    visible: показывать окно приложения (для Outlook игнорируется)
    """
    if app_name not in SUPPORTED_APPS:
        return f"Неподдерживаемое приложение '{app_name}'. Допустимы: {', '.join(SUPPORTED_APPS)}"
    app = Officer.application(app_name)
    if app is None:
        return f"Не удалось запустить {app_name} (не установлено?)"
    try:
        app.Visible = visible
    except Exception:
        pass
    return f"{app_name} запущен (visible={visible})"


@mcp.tool
def office_quit(app_name: str, force: bool = False) -> str:
    """Закрывает Office-приложение.

    app_name: Word | Excel | PowerPoint | Outlook
    force: если True — убивает процесс через TerminateProcess
    """
    ok = Officer.quit(app_name, force=force)
    return f"{app_name} закрыт" if ok else f"Не удалось закрыть {app_name}"


@mcp.tool
def office_visible(app_name: str, visible: bool) -> str:
    """Переключает видимость окна Office-приложения.

    app_name: Word | Excel | PowerPoint | Outlook
    visible: True/False
    """
    value = Officer.visible(app_name, visible)
    return f"{app_name}.Visible = {value}"


@mcp.tool
def office_run_python(code: str, data: str = "") -> str:
    """Выполняет произвольный Python-код для управления Office-приложениями через COM.

    ВАЖНО: используй Officer.Excel/.Word/.PowerPoint/.Outlook — это уже
    запущенные COM-приложения. НЕ делай win32com.client.Dispatch сам,
    это создаст дубль процесса.

    Для путей используй resolve_path(p) — она разворачивает '~', 'Desktop/...',
    'Documents/...' с учётом OneDrive-редиректа и создаёт родительскую папку.

    В namespace доступны:
      Officer        — singleton (.Excel / .Word / .PowerPoint / .Outlook)
      resolve_path(p) — корректный абсолютный путь (OneDrive-aware)
      data           — входная строка (параметр этого вызова)
      output         — значение, которое будет возвращено

    Если не уверен в API — СНАЧАЛА вызови office_docs_search(query=...),
    в хранилище загружена документация Office COM.

    ВНИМАНИЕ: код выполняется в текущем процессе без песочницы.
    """
    namespace = {
        "Officer": Officer,
        "resolve_path": _resolve_office_path,
        "data": data,
        "output": "",
        "__builtins__": __builtins__,
    }
    try:
        exec(code, namespace)
        return f"OK: {namespace.get('output', '')}"
    except Exception as e:
        return f"Ошибка office_run_python: {type(e).__name__}: {e}"


# ─────────────────────────────────────────────────────────────────
#  PowerPoint
# ─────────────────────────────────────────────────────────────────

def _open_ppt(file_path: str):
    """Открывает PowerPoint и презентацию. Возвращает (app, presentation, created_new_instance)."""
    import pywintypes
    import win32com.client
    abs_path = _resolve_office_path(file_path) if file_path else ""
    try:
        ppt = win32com.client.GetActiveObject("PowerPoint.Application")
        created = False
    except pywintypes.com_error:
        ppt = win32com.client.Dispatch("PowerPoint.Application")
        created = True
    # PowerPoint требует Visible=True перед любой операцией (особенность COM-API)
    try:
        ppt.Visible = True
    except Exception:
        pass

    if abs_path and os.path.exists(abs_path):
        for i in range(ppt.Presentations.Count):
            pres = ppt.Presentations(i + 1)
            if os.path.normcase(pres.FullName) == os.path.normcase(abs_path):
                return ppt, pres, created
        pres = ppt.Presentations.Open(abs_path)
        return ppt, pres, created
    if abs_path:
        return ppt, None, created
    try:
        pres = ppt.ActivePresentation
    except Exception:
        pres = None
    return ppt, pres, created


@mcp.tool
def ppt_create(file_path: str) -> str:
    """Создаёт новую PowerPoint-презентацию и сохраняет по указанному пути.

    file_path: путь к .pptx (будет перезаписан, если существует)
    """
    try:
        import win32com.client, pywintypes
        try:
            ppt = win32com.client.GetActiveObject("PowerPoint.Application")
        except pywintypes.com_error:
            ppt = win32com.client.Dispatch("PowerPoint.Application")
        ppt.Visible = True
        abs_path = _resolve_office_path(file_path)
        if not abs_path.lower().endswith((".pptx", ".ppt")):
            abs_path += ".pptx"
        pres = ppt.Presentations.Add()
        pres.SaveAs(abs_path)
        return f"Создана презентация: {abs_path}"
    except Exception as e:
        return f"Ошибка ppt_create: {e}"


@mcp.tool
def ppt_add_slide(file_path: str, layout: int = 12, position: int = 0) -> str:
    """Добавляет слайд в презентацию.

    file_path: путь к .pptx
    layout: номер layout'а PowerPoint (1=title, 2=bullet, 12=blank)
    position: позиция вставки (0 — в конец)
    """
    try:
        ppt, pres, _ = _open_ppt(file_path)
        if pres is None:
            return f"Файл не найден: {file_path}"
        idx = pres.Slides.Count + 1 if position <= 0 else position
        pres.Slides.Add(idx, layout)
        pres.Save()
        return f"Слайд добавлен на позицию {idx} (layout={layout}). Всего слайдов: {pres.Slides.Count}"
    except Exception as e:
        return f"Ошибка ppt_add_slide: {e}"


@mcp.tool
def ppt_add_textbox(
    file_path: str,
    slide_index: int,
    text: str,
    left: float = 100,
    top: float = 100,
    width: float = 600,
    height: float = 80,
    font_size: int = 18,
    bold: bool = False,
) -> str:
    """Добавляет текстовый блок на слайд.

    file_path: путь к .pptx
    slide_index: номер слайда (1-based)
    text: текст
    left/top/width/height: координаты и размер в pt
    font_size: размер шрифта
    bold: жирный
    """
    try:
        ppt, pres, _ = _open_ppt(file_path)
        if pres is None:
            return f"Файл не найден: {file_path}"
        slide = pres.Slides(slide_index)
        # 1 = msoTextOrientationHorizontal
        shape = slide.Shapes.AddTextbox(1, left, top, width, height)
        tr = shape.TextFrame.TextRange
        tr.Text = text
        tr.Font.Size = font_size
        tr.Font.Bold = bold
        pres.Save()
        return f"Текст добавлен на слайд {slide_index}"
    except Exception as e:
        return f"Ошибка ppt_add_textbox: {e}"


@mcp.tool
def ppt_read_slides(file_path: str) -> str:
    """Возвращает текст всех слайдов презентации.

    file_path: путь к .pptx
    """
    try:
        ppt, pres, _ = _open_ppt(file_path)
        if pres is None:
            return f"Файл не найден: {file_path}"
        out = []
        for i in range(pres.Slides.Count):
            slide = pres.Slides(i + 1)
            out.append(f"=== Слайд {i + 1} ===")
            for j in range(slide.Shapes.Count):
                shape = slide.Shapes(j + 1)
                try:
                    if shape.HasTextFrame and shape.TextFrame.HasText:
                        out.append(shape.TextFrame.TextRange.Text)
                except Exception:
                    continue
        return "\n".join(out) if out else "Презентация пуста"
    except Exception as e:
        return f"Ошибка ppt_read_slides: {e}"


# ─────────────────────────────────────────────────────────────────
#  Outlook
# ─────────────────────────────────────────────────────────────────

@mcp.tool
def outlook_send_mail(
    to: str,
    subject: str,
    body: str,
    cc: str = "",
    attachment_path: str = "",
    html: bool = False,
) -> str:
    """Отправляет email через Outlook (использует уже настроенную учётку пользователя).

    to: адрес получателя (через ';' если несколько)
    subject: тема
    body: текст письма
    cc: копия (опционально)
    attachment_path: путь к файлу-вложению (опционально)
    html: если True — тело письма HTML, иначе plain text
    """
    try:
        app = Officer.application("Outlook")
        if app is None:
            return "Outlook не установлен или не запускается"
        # 0 = olMailItem
        mail = app.CreateItem(0)
        mail.To = to
        if cc:
            mail.CC = cc
        mail.Subject = subject
        if html:
            mail.HTMLBody = body
        else:
            mail.Body = body
        if attachment_path:
            abs_path = _resolve_office_path(attachment_path)
            if not os.path.exists(abs_path):
                return f"Вложение не найдено: {abs_path}"
            mail.Attachments.Add(abs_path)
        mail.Send()
        return f"Письмо отправлено: '{subject}' → {to}"
    except Exception as e:
        return f"Ошибка outlook_send_mail: {e}"


@mcp.tool
def outlook_list_inbox(limit: int = 10, unread_only: bool = False) -> str:
    """Возвращает последние письма из Inbox.

    limit: сколько последних писем вернуть (по убыванию даты)
    unread_only: если True — только непрочитанные
    """
    try:
        app = Officer.application("Outlook")
        if app is None:
            return "Outlook не установлен или не запускается"
        ns = app.GetNamespace("MAPI")
        # 6 = olFolderInbox
        inbox = ns.GetDefaultFolder(6)
        items = inbox.Items
        items.Sort("[ReceivedTime]", True)
        out = []
        count = 0
        for item in items:
            if count >= limit:
                break
            try:
                if unread_only and not item.UnRead:
                    continue
                sender = getattr(item, "SenderName", "") or getattr(item, "SenderEmailAddress", "")
                subject = getattr(item, "Subject", "")
                received = getattr(item, "ReceivedTime", "")
                unread_mark = "●" if getattr(item, "UnRead", False) else " "
                out.append(f"{unread_mark} [{received}] {sender}: {subject}")
                count += 1
            except Exception:
                continue
        return "\n".join(out) if out else "Нет писем"
    except Exception as e:
        return f"Ошибка outlook_list_inbox: {e}"
