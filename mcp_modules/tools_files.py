"""
Инструменты для работы с файловой системой.
Включает: создание, удаление, копирование, переименование файлов и папок.
"""

import os
import sys
import shutil
import datetime
from typing import Union
from send2trash import send2trash
try:
    import win32com.client
    import pythoncom
    HAS_WIN32COM = True
except ImportError:
    HAS_WIN32COM = False

# Добавляем родительскую папку в sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .mcp_core import mcp, logger, get_system_path
from database import cache_put, cache_get, cache_list, history_push, history_pop, history_get_last, history_remove_last


@mcp.tool
def execute_open_file(file_id: Union[int, str]) -> str:
    """
    Открывает файл или директорию по идентификатору из кэша или полному пути.

    Args:
        file_id (int | str): Идентификатор файла в кэше или полный путь к файлу.

    Returns:
        str: Сообщение об успешном открытии файла или описание возникшей ошибки.

    Examples:
        execute_open_file(5) -> "Открыт C:\\Users\\User\\Desktop\\file.txt"
        execute_open_file('C:\\Users\\User\\Desktop\\file.txt') -> "Открыт C:\\Users\\User\\Desktop\\file.txt"

    Note:
        - Если передан int, использует кэш для получения пути к файлу.
        - Если передан str, используется как полный путь напрямую.
        - Если файл не найден, возвращает сообщение об ошибке.
    """
    # Если это ID из кэша
    if isinstance(file_id, int):
        path = cache_get(file_id)
        if not path:
            return f"Файл с id={file_id} не найден в кэше."
    else:
        path = file_id

    clean_path = os.path.normpath(path)
    if not os.path.exists(clean_path):
        return f"Файл {clean_path} не существует."

    # Возвращаем СПЕЦИАЛЬНУЮ команду, которую перехватит main.py
    return f"__OPEN_FILE_COMMAND__:{clean_path}"


@mcp.tool
def open_folder(folder_id: Union[int, str]) -> str:
    """
    Открывает папку в проводнике Windows по идентификатору из кэша или полному пути.

    Args:
        folder_id (int | str): Идентификатор папки в кэше или полный путь к папке.

    Returns:
        str: Сообщение об успешном открытии папки или описание ошибки.

    Examples:
        open_folder(1) -> "Открыта папка D:\\Desktop\\Projects"
        open_folder('D:\\Desktop\\Projects') -> "Открыта папка D:\\Desktop\\Projects"

    Note:
        - Если передан int, использует кэш для получения пути к папке.
        - Если передан str, используется как полный путь напрямую.
        - Если папка не найдена или это не папка, возвращает ошибку.
        - Открывает папку в Windows Explorer через os.startfile().
    """
    # Если это ID из кэша
    if isinstance(folder_id, int):
        path = cache_get(folder_id)
        if not path:
            return f"Папка с id={folder_id} не найдена в кэше."
    else:
        path = folder_id

    clean_path = os.path.normpath(path)
    if not os.path.exists(clean_path):
        return f"Папка {clean_path} не существует."

    if not os.path.isdir(clean_path):
        return f"Путь '{clean_path}' не является папкой. Используйте execute_open_file для открытия файлов."

    # Возвращаем СПЕЦИАЛЬНУЮ команду, которую перехватит main.py
    return f"__OPEN_FOLDER_COMMAND__:{clean_path}"


@mcp.tool
def list_directory(directory: Union[str, int]) -> str:
    """
    Получает и добавляет в кэш список файлов и папок в указанной директории.

    Если ты уже получал список файлов в этой папке, используй ID из кэша для доступа к внутренним файлам.
    Функция поддерживает системные папки по названиям, ID из кэша, и полные пути к директориям.
    Использует Windows API для получения путей к системным папкам.

    Args:
        directory (str | int): 
            - Название системной папки: 'desktop', 'рабочий стол', 'documents', 'документы', и т.д.
            - ID папки из кэша (целое число): например, 1, 2, 5
            - Полный путь: 'C:\\Users\\User\\Documents'

    Returns:
        str: Строка с полным путем к директории и списком содержащихся в ней файлов/папок.
             Ограничивается первыми 50 элементами.
             В случае ошибки возвращает описание проблемы.

    Examples:
        list_directory('desktop') -> "Список файлов в C:\\Users\\User\\Desktop:\\n1: C:\\Users\\User\\Desktop\\file1.txt\\n2: C:\\Users\\User\\Desktop\\folder1"
        list_directory(1) -> "Список файлов в D:\\Desktop\\AI_assistant_MCP:\\n3: D:\\Desktop\\AI_assistant_MCP\\report.txt"
        list_directory('C:\\Windows\\System32') -> "Список файлов в C:\\Windows\\System32:\\n1: C:\\Windows\\System32\\kernel32.dll\\n..."

    Note:
        - ИСПОЛЬЗУЙ ID ИЗ КЭША для доступа к папкам! Это критически важно для работы.
        - Если список содержит ID, используй это ID в следующем вызове list_directory
        - Например: получил "1: AI_assistant_MCP" -> следующий вызов list_directory(1)
        - Ограничение в 50 элементов предотвращает слишком длинный вывод
        - Не пытайся открывать папки имея только их название, если это не системная папка. Всегда используй ID из кэша для доступа к папкам!
    """
    # Если это ID из кэша
    if isinstance(directory, int):
        path = cache_get(directory)
        if not path:
            return f"ID {directory} не найдено в кэше."
    else:
        path = get_system_path(directory)
    
    if not path or not os.path.exists(path):
        return f"Папка {directory} не найдена или доступ закрыт."

    if not os.path.isdir(path):
        return f"Путь '{path}' не является папкой."

    try:
        files = os.listdir(path)
        result = []

        for f in files:
            full = os.path.join(path, f)
            idx = cache_put(full)
            # Нормализуем путь для отображения (прямые слеши)
            normalized_path = full.replace('\\', '/')
            result.append(f"{idx}: {normalized_path}")

        return f"Список файлов в {path}:\n" + "\n".join(result)
    except Exception as e:
        return f"Ошибка при чтении: {e}"


@mcp.tool
def view_cache() -> str:
    """
    Возвращает текущие элементы кэша в формате:
    id: preview (например, полный путь файла).

    Returns:
        str: Список элементов кэша или сообщение о пустом кэше.

    Examples:
        view_cache() -> "1: C:\\Users\\User\\Documents\\file1.txt\n2: C:\\Users\\User\\Documents\\file2.docx"
    """
    items = cache_list()
    lines = [f"{k}: {v}" for k, v in items.items()]
    return "\n".join(lines) if lines else "Кэш пуст"


@mcp.tool
def create_item(directory: str, name: str, is_folder: bool = False) -> str:
    """
    Создает новый пустой файл или папку в указанной директории.

    Эта функция позволяет создавать новые файлы и папки в системе. 
    Поддерживает как системные папки (desktop, documents и т.д.), 
    так и полные пути. Созданный элемент автоматически добавляется в кэш 
    для последующей работы.

    Args:
        directory (str): Системная папка ('desktop', 'documents') или полный путь, 
                        где нужно создать элемент.
        name (str): Имя нового файла (с расширением, например 'test.txt') или папки.
        is_folder (bool): True, если нужно создать папку. False, если файл.
                         По умолчанию False (создаст файл).

    Returns:
        str: Сообщение об успехе с ID нового элемента в кэше, 
             или описание ошибки если элемент уже существует.

    Examples:
        create_item('desktop', 'report.txt', False) 
            -> "Успех! Файл 'report.txt' создан. Его ID в кэше: 42"
        create_item('documents', 'My Folder', True) 
            -> "Успех! Папка 'My Folder' создана. Его ID в кэше: 43"

    Note:
        - Если файл/папка с таким именем уже существует, функция вернет ошибку
        - Файлы создаются пустыми
        - Папки создаются пустыми
        - ID можно использовать в других функциях (read_file, delete_item и т.д.)
    """
    path = get_system_path(directory)
    if not path or not os.path.exists(path):
        return f"Целевая директория '{directory}' не найдена."

    full_path = os.path.join(path, name)
    
    if os.path.exists(full_path):
        return f"Ошибка: '{name}' уже существует в этой папке."

    try:
        if is_folder:
            os.makedirs(full_path)
            item_type = "Папка"
        else:
            with open(full_path, 'w', encoding='utf-8') as f:
                pass
            item_type = "Файл"
            
        new_id = cache_put(full_path)
        history_push("create", {"path": full_path, "is_folder": is_folder})
        return f"Успех! {item_type} '{name}' создан. Его ID в кэше: {new_id}"
    except Exception as e:
        return f"Ошибка при создании: {e}"


@mcp.tool
def rename_item(file_id: int, new_name: str) -> str:
    """
    Переименовывает файл или папку по идентификатору из кэша.

    Функция меняет имя файла или папки, сохраняя их в той же директории.
    Старое имя удаляется, а новый путь добавляется в кэш.

    Args:
        file_id (int): Идентификатор исходного файла/папки в кэше.
        new_name (str): НОВОЕ ИМЯ файла с расширением (без пути!). 
                       Например: 'report.docx' или 'My New Folder'

    Returns:
        str: Сообщение об успешном переименовании с новым ID кэша, 
             или описание ошибки если имя уже занято.

    Examples:
        rename_item(42, 'report_final.txt') 
            -> "Успешно переименовано в 'report_final.txt'. Новый ID: 44"
        rename_item(5, 'Photos_2024') 
            -> "Успешно переименовано в 'Photos_2024'. Новый ID: 45"

    Note:
        - Если файл с новым именем уже существует, операция будет отменена
        - Расширение файла должно быть указано в new_name для файлов
        - ID в кэше изменяется после переименования
    """
    old_path = cache_get(file_id)
    if not old_path or not os.path.exists(old_path):
        return f"Элемент с id={file_id} не найден."

    parent_dir = os.path.dirname(old_path)
    new_path = os.path.join(parent_dir, new_name)

    if os.path.exists(new_path):
        return f"Ошибка: Имя '{new_name}' уже занято."

    try:
        os.rename(old_path, new_path)
        new_id = cache_put(new_path)
        history_push("rename", {"old_path": old_path, "new_path": new_path})
        return f"Успешно переименовано в '{new_name}'. Новый ID: {new_id}"
    except Exception as e:
        return f"Ошибка при переименовании: {e}"


@mcp.tool
def copy_item(file_id: int, destination_folder: str) -> str:
    """
    Копирует файл или папку (со всем содержимым) в указанную директорию.

    Функция создает полную копию файла или папки (включая все вложенные файлы).
    Если файл с таким именем уже существует в папке назначения, 
    к копии добавляется суффикс '_копия'.

    Args:
        file_id (int): ID копируемого файла/папки в кэше.
        destination_folder (str): Папка назначения (системное имя 'desktop' 
                                или полный путь).

    Returns:
        str: Сообщение об успешном копировании с путем и ID копии, 
             или описание ошибки если исходный элемент не найден.

    Examples:
        copy_item(42, 'documents') 
            -> "Успешно скопировано в 'C:\\\\Users\\\\User\\\\Documents\\\\report.txt'. ID копии: 46"
        copy_item(10, 'desktop') 
            -> "Успешно скопировано в 'C:\\\\Users\\\\User\\\\Desktop\\\\Photos_копия'. ID копии: 47"

    Note:
        - При копировании папок копируется все содержимое рекурсивно
        - Сохраняются все атрибуты файлов (дата создания, изменения и т.д.)
        - Если имя занято, файл будет переименован в 'name_копия.ext'
        - Копия добавляется в кэш для последующего использования
    """
    source_path = cache_get(file_id)
    if not source_path or not os.path.exists(source_path):
        return f"Исходный элемент с id={file_id} не найден."

    dest_dir = get_system_path(destination_folder)
    if not dest_dir or not os.path.exists(dest_dir):
        return f"Папка назначения '{destination_folder}' не найдена."

    base_name = os.path.basename(source_path)
    dest_path = os.path.join(dest_dir, base_name)

    if os.path.exists(dest_path):
        name, ext = os.path.splitext(base_name)
        dest_path = os.path.join(dest_dir, f"{name}_копия{ext}")

    try:
        if os.path.isdir(source_path):
            shutil.copytree(source_path, dest_path)
        else:
            shutil.copy2(source_path, dest_path)
            
        new_id = cache_put(dest_path)
        history_push("copy", {"dest_path": dest_path, "is_folder": os.path.isdir(source_path)})
        return f"Успешно скопировано в '{dest_path}'. ID копии: {new_id}"
    except Exception as e:
        return f"Ошибка копирования: {e}"


@mcp.tool
def move_file(file_id: int, destination_folder: str) -> str:
    """
    Перемещает файл из одного места в другое с использованием идентификатора из кэша.

    Args:
        file_id (int): Идентификатор файла в кэше.
        destination_folder (str): Название системной папки назначения или полный путь к директории.

    Returns:
        str: Сообщение об успешном перемещении или описание возникшей ошибки.

    Examples:
        move_file(5, 'documents') -> "Файл успешно перемещен."

    Note:
        - Использует кэш для получения пути к исходному файлу.
        - Если файл не найден в кэше, возвращает сообщение об ошибке.
    """
    source_path = cache_get(file_id)
    if not source_path:
        return "Файл не найден в кеше"

    dest_path = get_system_path(destination_folder)
    if not dest_path: dest_path = destination_folder

    try:
        final_dest = os.path.join(dest_path, os.path.basename(source_path))
        shutil.move(source_path, final_dest)
        history_push("move", {"old_path": source_path, "new_path": final_dest})
        return "Файл успешно перемещен."
    except Exception as e:
        return f"Ошибка перемещения: {e}"


@mcp.tool
def read_file(file_id: int) -> str:
    """
    Читает текстовое содержимое файла по его ID из кэша.

    Функция предназначена для чтения текстовых файлов (.txt, .py, .md, .csv, .json и т.д.).
    Включает защиту от чтения огромных файлов (максимум 5000 символов) 
    и от бинарных файлов (картинки, PDF, EXE и т.д.).
    
    Поддерживает кодировки UTF-8 и Windows-1251 (cp1251).

    Args:
        file_id (int): ID файла в кэше.

    Returns:
        str: Содержимое файла с заголовком, или сообщение об ошибке 
             если это папка, бинарный файл или она слишком велика.

    Examples:
        read_file(42) 
            -> "--- Содержимое файла (report.txt) ---\\nЭто содержимое моего файла"
        read_file(100) 
            -> "Ошибка: Это папка, а не файл. Используйте list_directory."
        read_file(15) 
            -> "Ошибка: Это бинарный файл (например, картинка, pdf или exe). Невозможно прочитать."

    Note:
        - Максимальный размер для чтения: 5000 символов
        - Если файл больше, показывается отметка "[ФАЙЛ СЛИШКОМ БОЛЬШОЙ, ПОКАЗАНА ТОЛЬКО ЧАСТЬ]"
        - Пустые файлы возвращают сообщение "Файл пуст."
        - Автоматически пробует кодировки UTF-8 и cp1251
    """
    path = cache_get(file_id)
    if not path or not os.path.exists(path):
        return f"Файл с id={file_id} не найден."

    if os.path.isdir(path):
        return "Ошибка: Это папка, а не файл. Используйте list_directory."

    MAX_CHARS = 5000

    try:
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read(MAX_CHARS)
        except UnicodeDecodeError:
            with open(path, 'r', encoding='cp1251') as f:
                content = f.read(MAX_CHARS)
                
        if not content.strip():
            return "Файл пуст."
            
        result = f"--- Содержимое файла ({os.path.basename(path)}) ---\n{content}"
        if len(content) == MAX_CHARS:
            result += "\n\n... [ФАЙЛ СЛИШКОМ БОЛЬШОЙ, ПОКАЗАНА ТОЛЬКО ЧАСТЬ] ..."
            
        return result
    except UnicodeDecodeError:
        return "Ошибка: Это бинарный файл (например, картинка, pdf или exe). Невозможно прочитать как текст."
    except Exception as e:
        return f"Ошибка чтения файла: {e}"


@mcp.tool
def edit_file(file_id: int, content: str, mode: str = "append") -> str:
    """
    Редактирует текстовый файл.
    
    Args:
        file_id (int): ID файла в кэше.
        content (str): Текст для записи.
        mode (str): Режим работы: 
            'append' — добавить в конец файла (с новой строки).
            'overwrite' — полностью заменить содержимое файла.
            
    Returns:
        str: Результат операции.
    """
    path = cache_get(file_id)
    if not path or not os.path.exists(path):
        return f"Файл с id={file_id} не найден."
    
    if os.path.isdir(path):
        return "Ошибка: Это папка, а не файл."

    try:
        # Сохраняем состояние для Undo
        with open(path, 'r', encoding='utf-8') as f:
            old_content = f.read()
            
        if mode == "overwrite":
            new_content = content
        else:
            new_content = old_content + "\n" + content
            
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
            
        history_push("edit", {"path": path, "old_content": old_content})
        return f"Файл '{os.path.basename(path)}' успешно обновлен."
    except Exception as e:
        return f"Ошибка редактирования: {e}"


@mcp.tool
def get_file_info(file_id: int) -> str:
    """
    Возвращает подробную информацию о файле или папке.

    Функция предоставляет всю необходимую информацию о файле или папке:
    имя, тип, полный путь, размер, дату создания и последнего изменения.

    Args:
        file_id (int): ID файла или папки в кэше.

    Returns:
        str: Форматированная информация о файле/папке с полями:
             - Имя
             - Тип (Папка или Файл)
             - Путь
             - Размер (в Байтах, КБ или МБ)
             - Дата создания
             - Дата последнего изменения
             
             Или сообщение об ошибке если элемент не найден.

    Examples:
        get_file_info(42) 
            -> "Имя: report.txt
                Тип: Файл
                Путь: C:\\\\Users\\\\User\\\\Documents\\\\report.txt
                Размер: 15.50 КБ
                Создан: 2024-03-07 10:30:45
                Изменен: 2024-03-07 14:22:10"

    Note:
        - Для папок размер не вычисляется (указывается как "Вычисляется динамически")
        - Размер автоматически форматируется в удобный формат (Б, КБ, МБ)
        - Даты показываются в формате YYYY-MM-DD HH:MM:SS
    """
    path = cache_get(file_id)
    if not path or not os.path.exists(path):
        return f"Элемент с id={file_id} не найден."

    try:
        stat = os.stat(path)
        is_dir = os.path.isdir(path)
        
        size_bytes = stat.st_size
        if size_bytes < 1024:
            size_str = f"{size_bytes} Байт"
        elif size_bytes < 1024**2:
            size_str = f"{size_bytes / 1024:.2f} КБ"
        else:
            size_str = f"{size_bytes / (1024**2):.2f} МБ"

        if is_dir:
            size_str = "<Вычисляется динамически для папок>"

        created = datetime.datetime.fromtimestamp(stat.st_ctime).strftime('%Y-%m-%d %H:%M:%S')
        modified = datetime.datetime.fromtimestamp(stat.st_mtime).strftime('%Y-%m-%d %H:%M:%S')

        info = [
            f"Имя: {os.path.basename(path)}",
            f"Тип: {'Папка' if is_dir else 'Файл'}",
            f"Путь: {path}",
            f"Размер: {size_str}",
            f"Создан: {created}",
            f"Изменен: {modified}"
        ]
        
        return "\n".join(info)
    except Exception as e:
        return f"Ошибка получения информации: {e}"


@mcp.tool
def delete_item(file_id: int) -> str:
    """
    Помещает файл или папку в КОРЗИНУ по идентификатору из кэша.
    
    ВНИМАНИЕ: ПЕРЕД ВЫЗОВОМ ЭТОГО ИНСТРУМЕНТА ТЫ ОБЯЗАН СПРОСИТЬ У ПОЛЬЗОВАТЕЛЯ 
    ПОДТВЕРЖДЕНИЕ, ОН ДОЛЖЕН ЯВНО ОТВЕТИТЬ ПОЛОЖИТЕЛЬНО! 
    Не вызывай этот инструмент, пока пользователь явно не ответит "Да", "Удаляй" или подобное.
    
    Args:
        file_id (int): Идентификатор файла или папки в кэше.
    
    Returns:
        str: Сообщение об успешном перемещении в Корзину или описание ошибки.
    """
    path = cache_get(file_id)
    if not path or not os.path.exists(path):
        return f"Элемент с id={file_id} не найден."

    try:
        # Безопасное удаление в системную Корзину
        send2trash(os.path.normpath(path))
        
        # Записываем в историю БД
        history_push("delete", {"path": path})
        
        return f"Успех: Элемент '{os.path.basename(path)}' перемещен в КОРЗИНУ."
    except Exception as e:
        return f"Ошибка при перемещении в корзину: {e}"


@mcp.tool
def undo_last_action() -> str:
    """
    Отменяет последнее действие ассистента (создание, переименование, копирование, перемещение).
    
    Функция извлекает последнее действие из историй БД и пытается его отменить.
    Поддерживается отмена следующих действий:
    - create: Удаляет созданный файл/папку
    - rename: Возвращает файл/папку на исходное имя
    - copy: Удаляет скопированный файл/папку
    - move: Перемещает файл обратно в исходное местоположение
    - delete: Восстанавливает файл из Корзины
    - restore: Удаляет файл обратно в Корзину
    
    Returns:
        str: Сообщение об успешно отмене или описание ошибки.
    """
    action = history_get_last()
    if not action:
        return "В базе данных нет истории действий для отмены."
        
    type_ = action.get("type")
    payload = action.get("payload", {})
    
    logger.info(f"undo_last_action: отмена {type_}")
    
    try:
        if type_ == "create":
            path = payload.get("path")
            if path and os.path.exists(path):
                if payload.get("is_folder"):
                    shutil.rmtree(path)  # удаляет папку со всем содержимым
                else:
                    os.remove(path)
            history_remove_last()
            logger.info(f"undo_last_action: успешно отменено create для {path}")
            return f"Отменено: созданный элемент '{os.path.basename(path)}' удален."
            
        elif type_ == "rename":
            old_path = payload.get("old_path")
            new_path = payload.get("new_path")
            if new_path and os.path.exists(new_path):
                os.rename(new_path, old_path)
            history_remove_last()
            logger.info(f"undo_last_action: успешно отменено rename")
            return f"Отменено: имя '{os.path.basename(new_path)}' возвращено на исходное."
            
        elif type_ == "copy":
            dest_path = payload.get("dest_path")
            if dest_path and os.path.exists(dest_path):
                if payload.get("is_folder"):
                    shutil.rmtree(dest_path)
                else:
                    os.remove(dest_path)
            history_remove_last()
            logger.info(f"undo_last_action: успешно отменено copy")
            return f"Отменено: скопированный элемент '{os.path.basename(dest_path)}' удален."
            
        elif type_ == "move":
            old_path = payload.get("old_path")
            new_path = payload.get("new_path")
            if new_path and os.path.exists(new_path):
                shutil.move(new_path, old_path)
            history_remove_last()
            logger.info(f"undo_last_action: успешно отменено move")
            return f"Отменено: файл перемещен обратно в исходную папку."

        elif type_ == "edit":
            path = payload.get("path")
            old_content = payload.get("old_content")
            if path and os.path.exists(path):
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(old_content)
            history_remove_last()
            return "Отменено: содержимое файла возвращено к предыдущей версии."
            
        elif type_ == "delete":
            # Отмена удаления не поддерживается, файл находится в Корзине
            return "Отмена удаления не поддерживается программой. Файл находится в Корзине Windows. Восстановите его вручную из Корзины или используйте стороннее ПО для восстановления."
            
    except Exception as e:
        logger.error(f"undo_last_action: критическая ошибка при отмене {type_}: {e}")
        return f"Не удалось выполнить отмену: {e}"
        
    return "Неизвестное действие в истории."


@mcp.tool
def open_recycle_bin() -> str:
    """
    Открывает Корзину Windows в Проводнике.

    Returns:
        str: Сообщение об успешном открытии Корзины или описание возникшей ошибки.
    """
    print("Вызван open_recycle_bin")
    try:
        os.startfile("shell:RecycleBinFolder")
        logger.info("open_recycle_bin: Корзина открыта успешно")
        return "Корзина Windows открыта."
    except Exception as e:
        logger.error(f"open_recycle_bin: ошибка при открытии Корзины: {e}")
        return f"Не удалось открыть Корзину: {e}"