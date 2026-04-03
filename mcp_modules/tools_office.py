"""
Office COM API tools — Excel и Word через win32com.client.

Используются OfficeAgent для прямой работы с файлами Office без GUI.
"""

from __future__ import annotations

import os
from typing import Tuple


def _open_excel(file_path: str) -> Tuple[object, object, bool]:
    """
    Открывает Excel и workbook.
    Возвращает (excel_app, workbook, created_new_instance).
    """
    import win32com.client
    abs_path = os.path.abspath(file_path) if file_path else ""
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
    abs_path = os.path.abspath(file_path) if file_path else ""
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
