"""
Сканер установленных приложений Windows.
Собирает .exe и .lnk из Start Menu, Desktop, реестра.
Запускается при старте ассистента.
"""
import os
import glob
import winreg
import re
import json
from database import apps_clear, apps_put_many, apps_count, apps_add_aliases_bulk
_ALIAS_MODEL = "gpt-oss:120b-cloud"

BASE_DIR = os.path.dirname(__file__)
_LLM_CACHE_PATH = os.path.join(BASE_DIR, "llm_aliases_cache.json")


def _load_llm_cache() -> dict:
    """Загружает кэш LLM-алиасов из файла. {path: [aliases]}."""
    if os.path.exists(_LLM_CACHE_PATH):
        try:
            with open(_LLM_CACHE_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def _save_llm_cache(cache: dict) -> None:
    """Сохраняет кэш LLM-алиасов в файл."""
    with open(_LLM_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


# Папки для сканирования ярлыков (.lnk)
_SHORTCUT_DIRS = [
    os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
    os.path.join(os.environ.get("PROGRAMDATA", ""), r"Microsoft\Windows\Start Menu\Programs"),
    os.path.join(os.environ.get("USERPROFILE", ""), "Desktop"),
    os.path.join(os.environ.get("PUBLIC", ""), "Desktop"),
]

# Реестр: ключи с установленными программами
_REG_KEYS = [
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
    (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
]

# Папки с .exe для прямого сканирования
_EXE_DIRS = [
    os.environ.get("PROGRAMFILES", r"C:\Program Files"),
    os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
    os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs"),
]

# Исключения — системные/служебные exe которые не нужны
_SKIP_NAMES = {
    "uninstall", "uninst", "update", "updater", "setup", "installer",
    "crash", "helper", "service", "daemon", "agent", "repair",
    "migrate", "elevate", "launcher",
}


def _resolve_lnk(lnk_path: str) -> str | None:
    """Извлекает целевой путь из .lnk ярлыка через COM."""
    try:
        import win32com.client
        shell = win32com.client.Dispatch("WScript.Shell")
        shortcut = shell.CreateShortCut(lnk_path)
        target = shortcut.Targetpath
        if target and target.lower().endswith(".exe") and os.path.isfile(target):
            return target
    except Exception:
        pass
    return None


def _name_from_path(path: str) -> str:
    """Извлекает человекочитаемое имя из пути к exe."""
    return os.path.splitext(os.path.basename(path))[0]


def _is_useful(name: str) -> bool:
    """Фильтрует служебные exe."""
    lower = name.lower()
    return not any(skip in lower for skip in _SKIP_NAMES)


def _scan_shortcuts() -> list:
    """Сканирует .lnk файлы из Start Menu и Desktop."""
    results = []
    for base_dir in _SHORTCUT_DIRS:
        if not os.path.isdir(base_dir):
            continue
        for lnk in glob.iglob(os.path.join(base_dir, "**", "*.lnk"), recursive=True):
            target = _resolve_lnk(lnk)
            if target:
                name = os.path.splitext(os.path.basename(lnk))[0]
                if _is_useful(name):
                    results.append((name, target))
    return results


def _scan_registry() -> list:
    """Сканирует реестр Windows для установленных программ."""
    results = []
    for hive, key_path in _REG_KEYS:
        try:
            key = winreg.OpenKey(hive, key_path)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, i)
                    i += 1
                except OSError:
                    break
                try:
                    subkey = winreg.OpenKey(key, subkey_name)
                    try:
                        name = winreg.QueryValueEx(subkey, "DisplayName")[0]
                    except OSError:
                        name = None
                    try:
                        icon = winreg.QueryValueEx(subkey, "DisplayIcon")[0]
                    except OSError:
                        icon = None
                    try:
                        install_loc = winreg.QueryValueEx(subkey, "InstallLocation")[0]
                    except OSError:
                        install_loc = None
                    subkey.Close()

                    # Пытаемся извлечь exe из DisplayIcon
                    exe_path = None
                    if icon:
                        icon_clean = icon.split(",")[0].strip('"').strip()
                        if icon_clean.lower().endswith(".exe") and os.path.isfile(icon_clean):
                            exe_path = icon_clean

                    if not exe_path and install_loc and os.path.isdir(install_loc):
                        # Ищем exe в корне InstallLocation
                        for f in os.listdir(install_loc):
                            if f.lower().endswith(".exe") and _is_useful(os.path.splitext(f)[0]):
                                candidate = os.path.join(install_loc, f)
                                if os.path.isfile(candidate):
                                    exe_path = candidate
                                    break

                    if name and exe_path and _is_useful(name):
                        results.append((name, exe_path))
                except OSError:
                    pass
        finally:
            key.Close()
    return results


def _scan_exe_dirs() -> list:
    """Сканирует Program Files (1 уровень вложенности) для exe."""
    results = []
    for base_dir in _EXE_DIRS:
        if not os.path.isdir(base_dir):
            continue
        try:
            for app_dir in os.listdir(base_dir):
                app_path = os.path.join(base_dir, app_dir)
                if not os.path.isdir(app_path):
                    continue
                try:
                    for f in os.listdir(app_path):
                        if f.lower().endswith(".exe") and _is_useful(os.path.splitext(f)[0]):
                            full = os.path.join(app_path, f)
                            if os.path.isfile(full):
                                results.append((os.path.splitext(f)[0], full))
                except PermissionError:
                    pass
        except PermissionError:
            pass
    return results


# Известные алиасы: exe_name_lower -> [алиасы]
_KNOWN_ALIASES = {
    "chrome": ["хром", "гугл", "google", "браузер"],
    "firefox": ["фаерфокс", "мозилла", "mozilla"],
    "msedge": ["edge", "эдж", "едж"],
    "opera": ["опера"],
    "brave": ["брейв"],
    "yandex": ["яндекс"],
    "telegram": ["телеграм", "телега", "тг"],
    "discord": ["дискорд", "дс"],
    "slack": ["слак"],
    "notepad++": ["нотпад", "нотепад", "блокнот++"],
    "notepad": ["блокнот"],
    "code": ["vscode", "вскод", "код", "visual studio code"],
    "explorer": ["проводник"],
    "calc": ["калькулятор", "calculator"],
    "mspaint": ["paint", "рисование", "пейнт"],
    "winword": ["word", "ворд", "текстовый редактор"],
    "excel": ["эксель", "таблицы"],
    "powerpnt": ["powerpoint", "поверпоинт", "презентации"],
    "outlook": ["аутлук", "почта"],
    "onenote": ["уаннот"],
    "teams": ["тимс", "тимз"],
    "spotify": ["спотифай"],
    "vlc": ["влс", "медиаплеер"],
    "obs64": ["obs", "обс", "запись экрана"],
    "steam": ["стим"],
    "cmd": ["командная строка", "терминал", "консоль"],
    "powershell": ["повершелл", "пш"],
    "wt": ["windows terminal", "виндовс терминал"],
    "taskmgr": ["диспетчер задач", "task manager"],
    "mstsc": ["удалённый рабочий стол", "rdp"],
    "snippingtool": ["ножницы", "скриншот"],
    "gimp-2": ["gimp", "гимп"],
    "photoshop": ["фотошоп"],
    "illustrator": ["иллюстратор"],
    "figma": ["фигма"],
    "postman": ["постман"],
    "filezilla": ["файлзилла"],
    "putty": ["путти"],
    "7zfm": ["7zip", "7зип", "архиватор"],
    "winrar": ["винрар", "рар"],
    "skype": ["скайп"],
    "zoom": ["зум"],
    "thunderbird": ["тандерберд"],
    "blender": ["блендер"],
    "audacity": ["аудасити"],
    "idea64": ["intellij", "идея", "intellij idea"],
    "pycharm64": ["pycharm", "пайчарм"],
    "webstorm64": ["webstorm", "вебшторм"],
    "datagrip64": ["datagrip", "датагрип"],
    "rider64": ["rider", "райдер"],
    "clion64": ["clion", "клион"],
    "goland64": ["goland", "голанд"],
}


def _generate_aliases_basic(name: str, path: str) -> list:
    """Генерирует базовые алиасы (без LLM): exe name, shortcut name, CamelCase, known aliases."""
    aliases = set()
    exe_name = os.path.splitext(os.path.basename(path))[0].lower()

    aliases.add(exe_name)
    aliases.add(name.lower())

    # Разбиваем CamelCase и пробелы
    for w in re.split(r'[\s\-_]+', name):
        if len(w) >= 3:
            aliases.add(w.lower())
    for w in re.findall(r'[A-Z][a-z]+|[a-z]+|[A-Z]+', name):
        if len(w) >= 3:
            aliases.add(w.lower())

    # Известные алиасы по exe_name (точное совпадение)
    for key, vals in _KNOWN_ALIASES.items():
        if key == exe_name or exe_name == key.rstrip("0123456789"):
            aliases.update(vals)
            break

    # Известные алиасы по имени ярлыка (по целым словам)
    name_lower = name.lower()
    name_words = set(re.split(r'[\s\-_]+', name_lower))
    for key, vals in _KNOWN_ALIASES.items():
        if key in name_words or key == name_lower:
            aliases.update(vals)

    return [a for a in aliases if len(a) >= 2]


_LLM_ALIAS_PROMPT = """Ты генерируешь поисковые алиасы для приложений Windows.
Для каждого приложения придумай все возможные названия, которые пользователь может произнести голосом, включая:
- Русские транслитерации (chrome → хром)
- Сокращения (telegram → тг, телега)
- Разговорные названия
- Английские альтернативы

Входные данные — JSON массив объектов с полями "name" и "exe".
Верни JSON объект где ключ — имя приложения (name), значение — массив алиасов.

ВАЖНО: Возвращай ТОЛЬКО JSON без маркдаун-разметки и пояснений.

Пример:
Вход: [{"name": "Google Chrome", "exe": "chrome.exe"}, {"name": "Telegram", "exe": "Telegram.exe"}]
Выход: {"Google Chrome": ["хром", "гугл", "браузер", "google", "chrome"], "Telegram": ["телеграм", "телега", "тг", "мессенджер"]}"""

_LLM_BATCH_SIZE = 15


def _generate_aliases_llm(apps: list) -> dict:
    """Генерирует алиасы через LLM для списка [(name, path), ...].
    Возвращает {path: [aliases]}."""
    from langchain_ollama import ChatOllama

    os.environ.setdefault("NO_PROXY", "localhost,127.0.0.1,::1")
    os.environ.setdefault("no_proxy", "localhost,127.0.0.1,::1")
    llm = ChatOllama(model=_ALIAS_MODEL, temperature=0, num_predict=4096)
    result = {}

    for i in range(0, len(apps), _LLM_BATCH_SIZE):
        batch = apps[i:i + _LLM_BATCH_SIZE]
        items = [
            {"name": name, "exe": os.path.basename(path)}
            for name, path in batch
        ]

        prompt = f"{_LLM_ALIAS_PROMPT}\n\nВход: {json.dumps(items, ensure_ascii=False)}"

        try:
            response = llm.invoke(prompt)
            content = response.content.strip()
            # Убираем <think>...</think> блоки (qwen3.5 thinking mode)
            content = re.sub(r'<think>.*?</think>', '', content, flags=re.DOTALL).strip()
            # Убираем markdown-обёртку если есть
            if content.startswith("```"):
                content = re.sub(r'^```\w*\n?', '', content)
                content = re.sub(r'\n?```$', '', content)
            # Извлекаем JSON если он в тексте
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                content = json_match.group()

            aliases_map = json.loads(content)

            for name, path in batch:
                llm_aliases = aliases_map.get(name, [])
                if isinstance(llm_aliases, list):
                    result[path] = [a.lower().strip() for a in llm_aliases if isinstance(a, str) and len(a.strip()) >= 2]
        except Exception as e:
            print(f"LLM alias generation error (batch {i}): {e}")

    return result


def scan_and_save() -> int:
    """Сканирует все источники и сохраняет в БД. Возвращает количество найденных."""
    all_apps = {}

    # Приоритет: ярлыки > реестр > exe-папки (ярлыки дают лучшие имена)
    for name, path in _scan_exe_dirs():
        norm = os.path.normpath(path).lower()
        if norm not in all_apps:
            all_apps[norm] = (name, os.path.normpath(path))

    for name, path in _scan_registry():
        norm = os.path.normpath(path).lower()
        all_apps[norm] = (name, os.path.normpath(path))

    for name, path in _scan_shortcuts():
        norm = os.path.normpath(path).lower()
        all_apps[norm] = (name, os.path.normpath(path))

    if not all_apps:
        apps_clear()
        return 0

    # Загружаем кэш LLM-алиасов (переживает пересканирование)
    llm_cache = _load_llm_cache()

    apps_clear()
    apps_put_many(list(all_apps.values()))

    # Базовые алиасы для ВСЕХ приложений (быстро, без LLM)
    alias_data = []
    for name, path in all_apps.values():
        aliases = _generate_aliases_basic(name, path)
        if aliases:
            alias_data.append((path, aliases))
    if alias_data:
        apps_add_aliases_bulk(alias_data)

    # Восстанавливаем LLM-алиасы из кэша для существующих приложений
    current_paths = {path for _, path in all_apps.values()}
    cached_data = [(path, aliases) for path, aliases in llm_cache.items()
                   if path in current_paths and aliases]
    if cached_data:
        apps_add_aliases_bulk(cached_data)

    # LLM-алиасы только для НОВЫХ приложений (которых нет в кэше)
    new_apps = [
        (name, path) for name, path in all_apps.values()
        if path not in llm_cache
    ]

    if new_apps:
        print(f"Генерация алиасов через LLM для {len(new_apps)} новых приложений...")
        llm_aliases = _generate_aliases_llm(new_apps)
        if llm_aliases:
            # Сохраняем в кэш и в БД
            llm_cache.update(llm_aliases)
            _save_llm_cache(llm_cache)
            llm_data = [(path, aliases) for path, aliases in llm_aliases.items() if aliases]
            if llm_data:
                apps_add_aliases_bulk(llm_data)
                print(f"LLM сгенерировал алиасы для {len(llm_data)} приложений.")
    else:
        print("Все приложения уже имеют LLM-алиасы.")

    return len(all_apps)
