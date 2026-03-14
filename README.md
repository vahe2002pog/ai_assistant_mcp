# AI Assistant MCP — Документация инструментов

Полная документация по всем 55 доступным инструментам для управления Windows, работы с файлами, веб-поиском и UI-автоматизацией.

**Содержание:**
1. [Требования](#требования)
2. [Установка](#установка)
3. [Инструменты UIAutomation](#инструменты-uiautomation-31-инструмент)
4. [Инструменты файловой системы](#инструменты-файловой-системы-14-инструментов)
5. [Инструменты веб-поиска](#инструменты-веб-поиска-6-инструментов)
6. [Приложения и медиа](#приложения-и-медиа-3-инструмента)

---

## Требования

- **ОС:** Windows 10/11 с правами администратора
- **Python:** 3.10+
- **Ollama:** Для выполнения LLM запросов (http://localhost:11434)
- **UIAutomation.py:** Встроено в проект (требует COM инициализацию)

## Установка

```bash
# 1. Клонируем репозиторий
git clone <repository>
cd AI_assistant_MCP

# 2. Создаём виртуальное окружение
python -m venv venv
venv\Scripts\activate

# 3. Устанавливаем зависимости
pip install -r requirements.txt

# 4. Устанавливаем переменные окружения (если требуется)
set PYTHONIOENCODING=utf-8

# 5. Запускаем основное приложение
python main.py
```

---

## Инструменты UIAutomation (31 инструмент)

Инструменты для управления UI интерфейсами Windows приложений через UIAutomation API.

### 1️⃣ Обнаружение (Discovery) — 7 инструментов

#### `ui_find_window`
Находит окно приложения по имени, классу, ID процесса или дескриптору.

```python
ui_find_window(
    name: str = None,          # Часть имени окна
    class_name: str = None,    # Класс окна
    pid: int = None,           # PID процесса
    handle: int = None         # Дескриптор окна
) -> dict
# Возвращает: {"success": true, "data": {"handle": 1234567890, "name": "Калькулятор", ...}}
```

#### `ui_list_windows`
Получает список всех открытых Windows окон с фильтрацией.

```python
ui_list_windows(
    filter_name: str = None    # Фильтр по названию окна (опционально)
) -> dict
# Возвращает:
# {"success": true, "data": {
#    "windows": [
#      {"handle": 1234, "name": "Notepad", "class": "Notepad", "pid": 5678, 
#       "rect": {"left": 0, "top": 0, "right": 800, "bottom": 600},
#       "enabled": true, "visible": true},
#      ...
#    ],
#    "count": 5,
#    "filter": "Notepad"
#  }}
```

#### `ui_find_control`
Находит дочерний элемент управления по селектору.

```python
ui_find_control(
    parent_handle: int,        # Дескриптор родительского окна
    selector: str,             # Селектор (имя, класс, роль)
    index: int = 0             # Индекс элемента
) -> dict
# Возвращает: {"success": true, "data": {"handle": 987654321, "name": "Button", ...}}
```

#### `ui_get_children`
Получает список всех дочерних элементов.

```python
ui_get_children(
    handle: int,               # Дескриптор окна/элемента
    depth: int = 1,            # Глубина рекурсии
    filter_type: str = None    # Фильтр по типу элемента
) -> dict
# Возвращает:
# {"success": true, "data": {"children": [
#    {"handle": 111, "name": "OK", "type": "Button"},
#    {"handle": 222, "name": "Cancel", "type": "Button"},
#    ...
#  ], "count": 5}}
```

#### `ui_get_focused`
Получает активный элемент управления в фокусе.

```python
ui_get_focused() -> dict
# Возвращает: {"success": true, "data": {"handle": 555555, "name": "TextBox", ...}}
```

#### `ui_get_foreground`
Получает активное (переднее) окно.

```python
ui_get_foreground() -> dict
# Возвращает: {"success": true, "data": {"handle": 777777, "name": "Пуск", ...}}
```

#### `ui_control_from_point`
Получает элемент управления под курсором мыши.

```python
ui_control_from_point(
    x: int,                    # X координата
    y: int                     # Y координата
) -> dict
# Возвращает: {"success": true, "data": {"handle": 888888, "name": "Button", ...}}
```

---

### 2️⃣ Взаимодействие (Interaction) — 8 инструментов

#### `ui_click`
Клик по элементу управления или координатам.

```python
ui_click(
    handle: int = None,        # Дескриптор элемента (или x, y)
                               # Если handle=0, используйте x/y вместо этого
    x: int = None,             # X координата (для Modern UI элементов с handle=0)
    y: int = None,             # Y координата (для Modern UI элементов с handle=0)
    button: str = "left",      # "left", "right", "middle"
    double: bool = False       # Двойной клик
) -> dict
# Возвращает: {"success": true, "data": {"action": "click", "method": "control" или "coordinates"}}

# Примеры:
ui_click(handle=1234567890)  # Клик по центру элемента
ui_click(x=500, y=300)       # Клик по координатам (для handle=0)
ui_click(handle=123, button="right")  # Правый клик
ui_click(x=500, y=300, double=True)   # Двойной клик по координатам
```

#### `ui_send_keys`
Отправляет нажатия клавиш.

```python
ui_send_keys(
    keys: str,                 # Строка с клавишами
    interval: float = 0.05     # Интервал между нажатиями (сек)
) -> dict
# Примеры строк: "Hello", "{Tab}", "{Enter}", "{Ctrl}+c", "{Alt}+F4"
# Возвращает: {"success": true, "data": {"action": "send_keys", "keys": "Hello"}}
```

#### `ui_set_value`
Устанавливает значение элемента (текстовое поле, ползунок и т.д.).

```python
ui_set_value(
    handle: int,               # Дескриптор элемента
    value: str                 # Новое значение
) -> dict
# Возвращает: {"success": true, "data": {"action": "set_value", "value": "текст"}}
```

#### `ui_close_window`
Закрывает окно по дескриптору.

```python
ui_close_window(
    handle: int                # Дескриптор окна
) -> dict
# Возвращает: {"success": true, "data": {"action": "close_window", "handle": 123}}
```

#### `ui_move_window`
Перемещает или изменяет размер окна.

```python
ui_move_window(
    handle: int,               # Дескриптор окна
    x: int = None,             # X координата (опционально)
    y: int = None,             # Y координата (опционально)
    width: int = None,         # Ширина (опционально)
    height: int = None         # Высота (опционально)
) -> dict
# Возвращает: {"success": true, "data": {"action": "move_window", "rect": {...}}}
```

#### `ui_minimize_window`
Сворачивает окно (использует Windows API).

```python
ui_minimize_window(
    handle: int                # Дескриптор окна
) -> dict
# Возвращает: {"success": true, "data": {"action": "minimize_window"}}
```

#### `ui_maximize_window`
Разворачивает окно на весь экран.

```python
ui_maximize_window(
    handle: int                # Дескриптор окна
) -> dict
# Возвращает: {"success": true, "data": {"action": "maximize_window"}}
```

#### `ui_restore_window`
Восстанавливает окно к нормальному размеру.

```python
ui_restore_window(
    handle: int                # Дескриптор окна
) -> dict
# Возвращает: {"success": true, "data": {"action": "restore_window"}}
```

---

### 3️⃣ Запросы (Query) — 6 инструментов

#### `ui_get_properties`
Получает все свойства элемента управления.

```python
ui_get_properties(
    handle: int                # Дескриптор элемента
) -> dict
# Возвращает:
# {"success": true, "data": {
#    "name": "OK", "class_name": "Button", "type": "Button", 
#    "rect": {"left": 100, "top": 200, "right": 200, "bottom": 250},
#    "enabled": true, "visible": true, ...
#  }}
```

#### `ui_get_text`
Получает текстовое содержимое элемента.

```python
ui_get_text(
    handle: int                # Дескриптор элемента
) -> dict
# Возвращает: {"success": true, "data": {"text": "Нажми меня"}}
```

#### `ui_get_rect`
Получает координаты границ элемента.

```python
ui_get_rect(
    handle: int                # Дескриптор элемента
) -> dict
# Возвращает:
# {"success": true, "data": {
#    "left": 100, "top": 200, "right": 300, "bottom": 250
#  }}
```

#### `ui_screenshot`
Создает снимок элемента или окна и сохраняет в PNG файл.

```python
ui_screenshot(
    handle: int,               # Дескриптор элемента
    filename: str = None       # Имя файла (опционально)
) -> dict
# Возвращает: {"success": true, "data": {"filename": "screenshot.png", "path": "..."}}
```

#### `ui_exists`
Проверяет существование элемента.

```python
ui_exists(
    handle: int                # Дескриптор элемента
) -> dict
# Возвращает: {"success": true, "data": {"exists": true}}
```

#### `ui_wait_for`
Ждет условие (появление/исчезновение элемента).

```python
ui_wait_for(
    handle: int,               # Дескриптор элемента
    condition: str = "exist",  # "exist" или "disappear"
    timeout: int = 10          # Таймаут в секундах
) -> dict
# Возвращает: {"success": true, "data": {"condition": "exist", "elapsed": 2.5}}
```

---

### 4️⃣ Паттерны (Patterns) — 6 инструментов

#### `ui_invoke`
Вызывает действие элемента (например, нажатие кнопки).

```python
ui_invoke(
    handle: int                # Дескриптор кнопки/элемента
) -> dict
# Возвращает: {"success": true, "data": {"action": "invoke"}}
```

#### `ui_toggle`
Переключает состояние элемента (checkbox, типа).

```python
ui_toggle(
    handle: int                # Дескриптор элемента
) -> dict
# Возвращает: {"success": true, "data": {"state": "on"}}
```

#### `ui_expand_collapse`
Разворачивает или сворачивает TreeItem/GroupBox.

```python
ui_expand_collapse(
    handle: int,               # Дескриптор элемента
    action: str = "expand"     # "expand" или "collapse"
) -> dict
# Возвращает: {"success": true, "data": {"action": "expand"}}
```

#### `ui_select_item`
Выбирает элемент в списке/комбо-боксе.

```python
ui_select_item(
    handle: int,               # Дескриптор ListItem
    select: bool = True        # True = выбрать, False = отменить выбор
) -> dict
# Возвращает: {"success": true, "data": {"selected": true}}
```

#### `ui_scroll`
Прокручивает элемент (список, скролл-панель).

```python
ui_scroll(
    handle: int,               # Дескриптор элемента
    direction: str = "down",   # "up", "down", "left", "right"
    amount: int = 5            # Количество прокруток
) -> dict
# Возвращает: {"success": true, "data": {"direction": "down", "amount": 5}}
```

#### `ui_terminate_process`
Завершает процесс по PID (если требуется).

```python
ui_terminate_process(
    pid: int                   # PID процесса
) -> dict
# Возвращает: {"success": true, "data": {"pid": 1234, "terminated": true}}
```

---

### 5️⃣ Помощники (Helpers) — 5 инструментов

#### `ui_clipboard_get`
Получает текст из буфера обмена.

```python
ui_clipboard_get() -> dict
# Возвращает: {"success": true, "data": {"text": "скопированный текст"}}
```

#### `ui_clipboard_set`
Устанавливает текст в буфер обмена.

```python
ui_clipboard_set(
    text: str                  # Текст для копирования
) -> dict
# Возвращает: {"success": true, "data": {"text": "установлено в буфер"}}
```

#### `ui_list_processes`
Получает список запущенных процессов с фильтрацией.

```python
ui_list_processes(
    filter_name: str = None    # Фильтр по имени процесса
) -> dict
# Возвращает:
# {"success": true, "data": {
#    "processes": [
#      {"pid": 1234, "name": "notepad.exe", "memory_mb": 15.5},
#      ...
#    ],
#    "count": 50
#  }}
```

#### `ui_show_desktop`
Минимизирует все окна и показывает рабочий стол.

```python
ui_show_desktop() -> dict
# Возвращает: {"success": true, "data": {"action": "show_desktop"}}
```

#### `ui_get_screen_size`
Получает размеры экрана.

```python
ui_get_screen_size() -> dict
# Возвращает:
# {"success": true, "data": {
#    "width": 1920, "height": 1080, "monitors": 2
#  }}
```

---

## Инструменты файловой системы (14 инструментов)

Полное управление файлами и папками на диске.

#### `execute_open_file`
Открывает файл по ID из кэша или полному пути.

```python
execute_open_file(
    file_id: int | str         # ID кэша или полный путь
) -> str
# Возвращает: "Открыт C:\\Users\\User\\Desktop\\file.txt"
```

#### `open_folder`
Открывает папку в Проводнике.

```python
open_folder(
    folder_id: int | str       # ID кэша или полный путь
) -> str
# Возвращает: "Открыта папка D:\\Desktop\\Projects"
```

#### `list_directory`
Получает список файлов и папок в директории (первые 50).

```python
list_directory(
    directory: str | int       # Системная папка: 'desktop', 'documents' 
                               # или ID кэша, или полный путь
) -> str
# Возвращает:
# "Список файлов в C:\\Users\\User\\Desktop:
#  1: C:\\Users\\User\\Desktop\\file1.txt
#  2: C:\\Users\\User\\Desktop\\folder1
#  ..."
```

#### `view_cache`
Показывает текущие элементы в кэше (ID → путь).

```python
view_cache() -> str
# Возвращает:
# "1: C:\\Users\\User\\Documents\\file1.txt
#  2: C:\\Users\\User\\Documents\\file2.docx
#  ..."
```

#### `create_item`
Создает новый файл или папку.

```python
create_item(
    directory: str,            # Целевая папка
    name: str,                 # Имя файла/папки
    is_folder: bool = False    # True = папка, False = файл
) -> str
# Возвращает: "Успех! Файл 'report.txt' создан. Его ID в кэше: 42"
```

#### `rename_item`
Переименовывает файл или папку.

```python
rename_item(
    file_id: int,              # ID элемента в кэше
    new_name: str              # НОВОЕ ИМЯ с расширением
) -> str
# Возвращает: "Успешно переименовано в 'report_final.txt'. Новый ID: 44"
```

#### `copy_item`
Копирует файл или папку (со всем содержимым).

```python
copy_item(
    file_id: int,              # ID файла/папки в кэше
    destination_folder: str    # Папка назначения
) -> str
# Возвращает: "Успешно скопировано в 'D:\\Documents\\report.txt'. ID копии: 46"
```

#### `move_file`
Перемещает файл из одной папки в другую.

```python
move_file(
    file_id: int,              # ID файла в кэше
    destination_folder: str    # Папка назначения
) -> str
# Возвращает: "Файл успешно перемещен."
```

#### `read_file`
Читает текстовое содержимое файла (максимум 5000 символов).

```python
read_file(
    file_id: int               # ID файла в кэше
) -> str
# Возвращает:
# "--- Содержимое файла (report.txt) ---
#  Это содержимое моего файла..."
```

#### `edit_file`
Редактирует текстовый файл (добавить или перезаписать).

```python
edit_file(
    file_id: int,              # ID файла в кэше
    content: str,              # Текст для записи
    mode: str = "append"       # "append" или "overwrite"
) -> str
# Возвращает: "Файл 'report.txt' успешно обновлен."
```

#### `get_file_info`
Получает полную информацию о файле (размер, дата, тип).

```python
get_file_info(
    file_id: int               # ID файла в кэше
) -> str
# Возвращает:
# "Имя: report.txt
#  Тип: Файл
#  Путь: C:\\Users\\User\\Documents\\report.txt
#  Размер: 15.50 КБ
#  Создан: 2024-03-07 10:30:45
#  Изменен: 2024-03-07 14:22:10"
```

#### `delete_item`
Перемещает файл или папку в Корзину (с подтверждением).

```python
delete_item(
    file_id: int               # ID файла/папки в кэше
) -> str
# Возвращает: "Успех: Элемент 'file.txt' перемещен в КОРЗИНУ."
```

#### `undo_last_action`
Отменяет последнее действие с файлом (создание, переименование, копирование, перемещение).

```python
undo_last_action() -> str
# Возвращает: "Отменено: созданный элемент 'file.txt' удален."
```

#### `open_recycle_bin`
Открывает Корзину Windows.

```python
open_recycle_bin() -> str
# Возвращает: "Корзина Windows открыта."
```

---

## Инструменты веб-поиска (6 инструментов)

Интеграция с Tavily API для поиска информации в интернете.

#### `tavily_search`
Поиск в интернете с релевантными, сжатыми результатами.

```python
tavily_search(
    query: str,                # Поисковый запрос
    max_results: int = 5,      # Количество результатов (макс 10)
    search_depth: str = "advanced"  # "basic" или "advanced"
) -> str
# Возвращает:
# "Результаты поиска Tavily по запросу 'погода':
#  📌 Погода в Москве (Score: 0.95)
#  🔗 https://weather.com/...
#  📝 Сегодня в Москве...
#  ..."
```

#### `tavily_extract`
Получает полное содержимое одного или нескольких URL-адресов.

```python
tavily_extract(
    urls: list[str]            # Список URL-адресов
) -> str
# Возвращает: "Извлеченный контент в формате Markdown..."
```

#### `tavily_crawl`
Сканирует веб-сайт и извлекает ссылки (веб-скрейпинг).

```python
tavily_crawl(
    url: str,                  # URL сайта для сканирования
    max_requests_per_minute: int = 10  # Ограничение запросов
) -> str
# Возвращает: "Сканирование сайта https://example.com:
#  📌 Ссылка 1
#  📌 Ссылка 2
#  ..."
```

#### `tavily_map`
Создает структурированную карту содержимого сайта.

```python
tavily_map(
    url: str,                  # URL сайта
    max_pages: int = 100       # Максимум страниц
) -> str
# Возвращает: "Карта сайта https://example.com:
#  📌 Заголовок 1
#  🔗 https://example.com/page1
#  ..."
```

#### `open_url`
Открывает URL-адрес в браузере по умолчанию.

```python
open_url(
    url: str                   # URL адрес (полный или домен)
) -> str
# Примеры: open_url('https://youtube.com'), open_url('vk.com')
# Возвращает: "__OPEN_URL_COMMAND__:https://youtube.com"
```

#### `browser_search`
Выполняет поиск в браузере через Google.

```python
browser_search(
    query: str                 # Строка поиска
) -> str
# Возвращает: "__OPEN_URL_COMMAND__:https://www.google.com/search?q=..."
```

---

## Приложения и медиа (3 инструмента)

#### `open_app`
Запускает системное приложение по имени.

```python
open_app(
    app_name: str              # Имя исполняемого файла без .exe
) -> str
# Примеры: 'calc', 'notepad', 'explorer', 'mspaint', 'cmd', 'powershell'
# Возвращает: "__OPEN_APP_COMMAND__:calc"
```

#### `control_volume`
Управление системной громкостью.

```python
control_volume(
    action: str,               # "up", "down", "mute", "set"
    amount: float = 0.1        # От 0.0 до 1.0
) -> str
# Возвращает: "__VOLUME_COMMAND__:up:0.1"
```

#### `control_media`
Управление воспроизведением медиа.

```python
control_media(
    action: str                # "playpause", "next", "prev", "stop"
) -> str
# Возвращает: "__MEDIA_COMMAND__:playpause"
```

#### `get_weather`
Получает текущую погоду для города.

```python
get_weather(
    city: str                  # Название города (напр., 'Москва', 'London')
) -> str
# Возвращает:
# "Погода в Москве:
#  Температура: 15°С (ощущается как 12°С)
#  Ветер: 7 м/с с Юго-Запада
#  Влажность: 65%"
```

---

## Примеры использования

### Пример 1: Поиск и закрытие окна

```python
# 1. Находим окно Notepad
window = ui_find_window(name="Notepad")
handle = window['data']['handle']

# 2. Убеждаемся, что окно существует
exists = ui_exists(handle)

# 3. Закрываем окно
ui_close_window(handle)
```

### Пример 2: Работа с файлами

```python
# 1. Список файлов на рабочем столе
files = list_directory('desktop')

# 2. Читаем первый файл (ID=1)
content = read_file(1)

# 3. Редактируем файл
edit_file(1, "Новый текст для добавления", mode="append")

# 4. Переименовываем
rename_item(1, "report_final.txt")
```

### Пример 3: Веб-поиск

```python
# 1. Ищем информацию
results = tavily_search("как готовить пасту", max_results=3)

# 2. Извлекаем полный текст из найденного сайта
urls = ["https://example.com/recipe"]
content = tavily_extract(urls)

# 3. Открываем в браузере
open_url("https://example.com/recipe")
```

### Пример 4: UI автоматизация

```python
# 1. Находим калькулятор
calc = ui_find_window(name="Калькулятор")
handle = calc['data']['handle']

# 2. Находим кнопку "7"
button = ui_find_control(handle, selector="7")

# 3. Кликаем на кнопку
ui_click(button['data']['handle'])

# 4. Отправляем символ "+"
ui_send_keys("+")

# 5. Получаем размер окна
rect = ui_get_rect(handle)
```

---

## Архитектура

```
AI_assistant_MCP/
├── main.py              # Главная точка входа
├── loop.py              # Граф Langraph с 120-сек таймаутом
├── agents.py            # Граф выполнения инструментов
├── config.py            # Конфигурация
├── database.py          # Кэш файлов и история действий
├── models.py            # Pydantic модели
├── utils.py             # Утилиты
├── mcp_modules/
│   ├── mcp_core.py      # FastMCP initialization
│   ├── mcp_server.py    # Регистрация всех инструментов
│   ├── tools_apps.py    # 1 инструмент (open_app)
│   ├── tools_files.py   # 14 инструментов
│   ├── tools_web.py     # 6 инструментов
│   ├── tools_media.py   # 2 инструмента
│   ├── tools_weather.py # 1 инструмент
│   └── mcp_uiautomation/ # 31 инструмент UIAutomation
│       ├── core.py
│       ├── config.py
│       ├── models.py
│       ├── server.py
│       └── tools/
│           ├── discovery.py   # 7 инструментов
│           ├── interaction.py # 8 инструментов
│           ├── query.py       # 6 инструментов
│           ├── patterns.py    # 6 инструментов
│           └── helpers.py     # 5 инструментов
```

---

## Таблица всех инструментов (55 шт.)

| # | Категория | Инструмент | Описание |
|---|-----------|-----------|---------|
| **UIAutomation — Discovery (7)** | | | |
| 1 | Discovery | `ui_find_window` | Найти окно по имени/классу/PID |
| 2 | Discovery | `ui_list_windows` | Список всех открытых окон |
| 3 | Discovery | `ui_find_control` | Найти дочерний элемент |
| 4 | Discovery | `ui_get_children` | Получить все дочерние элементы |
| 5 | Discovery | `ui_get_focused` | Получить активный элемент |
| 6 | Discovery | `ui_get_foreground` | Получить активное окно |
| 7 | Discovery | `ui_control_from_point` | Получить элемент под курсором |
| **UIAutomation — Interaction (8)** | | | |
| 8 | Interaction | `ui_click` | Клик по элементу |
| 9 | Interaction | `ui_send_keys` | Отправить нажатия клавиш |
| 10 | Interaction | `ui_set_value` | Установить значение элемента |
| 11 | Interaction | `ui_close_window` | Закрыть окно |
| 12 | Interaction | `ui_move_window` | Переместить/изменить размер окна |
| 13 | Interaction | `ui_minimize_window` | Свернуть окно |
| 14 | Interaction | `ui_maximize_window` | Развернуть окно |
| 15 | Interaction | `ui_restore_window` | Восстановить окно |
| **UIAutomation — Query (6)** | | | |
| 16 | Query | `ui_get_properties` | Получить все свойства элемента |
| 17 | Query | `ui_get_text` | Получить текст элемента |
| 18 | Query | `ui_get_rect` | Получить координаты элемента |
| 19 | Query | `ui_screenshot` | Создать снимок элемента |
| 20 | Query | `ui_exists` | Проверить существование элемента |
| 21 | Query | `ui_wait_for` | Ждать условие (появление/исчезновение) |
| **UIAutomation — Patterns (6)** | | | |
| 22 | Patterns | `ui_invoke` | Вызвать действие (нажать кнопку) |
| 23 | Patterns | `ui_toggle` | Переключить состояние (checkbox) |
| 24 | Patterns | `ui_expand_collapse` | Развернуть/свернуть элемент |
| 25 | Patterns | `ui_select_item` | Выбрать элемент в списке |
| 26 | Patterns | `ui_scroll` | Прокрутить элемент |
| 27 | Patterns | `ui_terminate_process` | Завершить процесс |
| **UIAutomation — Helpers (5)** | | | |
| 28 | Helpers | `ui_clipboard_get` | Получить текст из буфера обмена |
| 29 | Helpers | `ui_clipboard_set` | Установить текст в буфер обмена |
| 30 | Helpers | `ui_list_processes` | Список запущенных процессов |
| 31 | Helpers | `ui_show_desktop` | Минимизировать все окна |
| 32 | Helpers | `ui_get_screen_size` | Получить размер экрана |
| **Файловая система (14)** | | | |
| 33 | Files | `execute_open_file` | Открыть файл |
| 34 | Files | `open_folder` | Открыть папку в Проводнике |
| 35 | Files | `list_directory` | Список файлов в папке |
| 36 | Files | `view_cache` | Показать кэш файлов |
| 37 | Files | `create_item` | Создать файл или папку |
| 38 | Files | `rename_item` | Переименовать файл |
| 39 | Files | `copy_item` | Копировать файл |
| 40 | Files | `move_file` | Переместить файл |
| 41 | Files | `read_file` | Прочитать содержимое файла |
| 42 | Files | `edit_file` | Редактировать файл |
| 43 | Files | `get_file_info` | Получить информацию о файле |
| 44 | Files | `delete_item` | Удалить файл в Корзину |
| 45 | Files | `undo_last_action` | Отменить последнее действие |
| 46 | Files | `open_recycle_bin` | Открыть Корзину |
| **Веб-поиск (6)** | | | |
| 47 | Web | `tavily_search` | Поиск в интернете |
| 48 | Web | `tavily_extract` | Извлечь полный текст с сайта |
| 49 | Web | `tavily_crawl` | Сканировать сайт (веб-скрейпинг) |
| 50 | Web | `tavily_map` | Создать карту сайта |
| 51 | Web | `open_url` | Открыть URL в браузере |
| 52 | Web | `browser_search` | Поиск в браузере (Google) |
| **Приложения и медиа (3)** | | | |
| 53 | Apps | `open_app` | Запустить приложение |
| 54 | Media | `control_volume` | Управление громкостью |
| 55 | Media | `control_media` | Управление медиа (play/pause/next) |
| 56 | Weather | `get_weather` | Получить погоду для города |

---

## Часто задаваемые вопросы

**Q: Что такое handle=0 в UIAutomation?**

A: Элементы с `handle=0` — это Modern UI элементы (Ribbon в Office, WPF приложения и т.д.), у которых нет традиционного Windows дескриптора. 

**Решение:** Для таких элементов инструменты автоматически вычисляют координаты центра:
```python
ui_get_children(handle)  # Возвращает элемент с handle=0 и center_x, center_y в rect
ui_click(x=center_x, y=center_y)  # Кликаем по координатам вместо handle
```

**Q: Как кликнуть на элемент типа "Вкладка Файл" в Word?**

A: 
1. Получите проект Word: `ui_find_window(name="Word")`
2. Получите дочерние элементы: `ui_get_children(handle=<Word handle>)`
3. Найдите элемент "Файл" с note="handle=0" и координатами center_x, center_y
4. Кликните: `ui_click(x=center_x, y=center_y)`

**Q: Как использовать инструменты с ID кэша?**

A: После первого вызова `list_directory()` файлы получают ID. Используйте эти ID в других функциях:

```python
list_directory('desktop')     # Получаем ID: 1, 2, 3, ...
read_file(1)                  # Читаем файл с ID=1
edit_file(1, "текст")         # Редактируем файл с ID=1
```

**Q: Требуются ли права администратора?**

A: Да, для UIAutomation инструментов требуются права администратора. Некоторые операции с Проводником могут работать без админа.

**Q: Какой таймаут для инструментов?**

A: Инструменты UI имеют таймаут 15 сек, общий таймаут сессии — 60 секунд. Если операция не завершится за это время, вернется ошибка timeout.

**Q: Как добавить свой инструмент?**

A: Создайте функцию с декоратором `@mcp.tool` в одном из `tools_*.py` файлов и зарегистрируйте в `mcp_server.py`.

---

## Поддержка

Для проблем и ошибок откройте issue на GitHub или обратитесь к команде разработки.

**Версия документации:** 2.0  
**Дата обновления:** 2026-03-15  
**Всего инструментов:** 55
