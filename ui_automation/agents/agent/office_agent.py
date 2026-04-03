"""
OfficeAgent — автоматизация Excel и Word через COM API (win32com).

Используется HostAgent для задач, требующих работы с содержимым Office-файлов:
чтение/запись ячеек Excel, редактирование Word-документов, формулы, таблицы.

Инструменты:
  mcp_modules.tools_office       — COM API: excel_*, word_*
  mcp_modules.tools_uiautomation — UI fallback (диалоги, окна)
  mcp_modules.tools_files        — файловые операции
  mcp_modules.tools_apps         — запуск приложений
"""

from ui_automation.agents.agent.tool_agent import ToolAgent


class OfficeAgent(ToolAgent):
    """Sub-agent for Excel and Word automation via COM API."""

    TOOLS_MODULES = [
        "mcp_modules.tools_office",
        "mcp_modules.tools_uiautomation",
        "mcp_modules.tools_files",
        "mcp_modules.tools_apps",
    ]

    SYSTEM_PROMPT = """Ты — агент для работы с документами Office (Excel, Word).

=== Excel (COM API) ===
- excel_get_sheets(file_path)                           — список листов
- excel_read_sheet(file_path, sheet_name, range_addr)   — читать данные листа
- excel_write_cell(file_path, sheet_name, cell, value)  — записать в ячейку (cell='A1')
- excel_write_range(file_path, sheet_name, start_cell, csv_data) — записать таблицу
- excel_apply_formula(file_path, sheet_name, cell, formula) — вставить формулу

=== Word (COM API) ===
- word_read_document(file_path)                         — прочитать текст документа
- word_write_text(file_path, text, position)            — вставить текст ('end'/'start')
- word_find_replace(file_path, find_text, replace_text) — найти и заменить
- word_get_tables(file_path)                            — содержимое таблиц

=== Поиск файлов ===
- list_directory(path)  — список файлов в папке
- read_file(path)       — прочитать текстовый файл

=== Запуск приложений ===
- open_app(app_name)    — запустить Excel, Word и т.д.

=== UI (только для диалогов и GUI-элементов) ===
- ui_list_windows, ui_focus_window, ui_click_element, ui_send_keys

ПРАВИЛА:
1. Для чтения и записи данных — ВСЕГДА используй COM API (excel_*, word_*)
2. Если в списке открытых окон виден нужный файл (например "Doc1.docx - Word") — передавай просто имя файла: word_write_text(file_path='Doc1.docx', ...). COM API найдёт его среди открытых документов.
3. Если файл не открыт — указывай полный путь. Если путь не знаешь — используй list_directory для поиска.
4. UI инструменты (ui_*) — только для диалогов сохранения, форматирования и т.п.
5. После завершения — вызови task_done(summary="...")
"""
