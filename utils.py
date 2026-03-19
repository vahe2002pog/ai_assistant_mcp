import os
from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
import subprocess
import webbrowser
import pyautogui
from contextlib import asynccontextmanager

def build_mcp_config() -> dict:
    config = {
        "pc_modules": {
            "command": "python",
            "args": ["launch_mcp.py"],
            "transport": "stdio",
        },
    }

    return config


@asynccontextmanager
async def get_mcp_client():
    """Контекстный менеджер — правильно открывает и закрывает клиент."""
    async with MultiServerMCPClient(build_mcp_config()) as client:
        yield client


async def get_all_tools():
    """Если нужно просто получить список инструментов разово."""
    async with MultiServerMCPClient(build_mcp_config()) as client:
        return await client.get_tools()


def set_system_volume(action, amount):
    """Изменяет системную громкость через pycaw (используется в main).

    action: 'up'|'down'
    amount: float
    """
    from ctypes import cast, POINTER
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    device = AudioUtilities.GetSpeakers()
    if device is None:
        raise RuntimeError("Не удалось получить устройство воспроизведения")

    volume = device.EndpointVolume
    current_vol = volume.GetMasterVolumeLevelScalar()

    if action == 'up':
        volume.SetMasterVolumeLevelScalar(min(1.0, current_vol + amount), None)
    elif action == 'down':
        volume.SetMasterVolumeLevelScalar(max(0.0, current_vol - amount), None)


def open_file(path: str) -> str:
    """Открыть файл в проводнике (Windows).

    Возвращает короткое сообщение при успехе или возбуждает исключение при ошибке.
    """
    os.startfile(path)
    return path


def open_folder(path: str) -> str:
    """Открыть папку в проводнике."""
    os.startfile(path)
    return path


def open_app(app_path: str) -> str:
    """Запустить приложение через shell 'start'."""
    # Нормализация: убираем двойные слэши и лишние кавычки от LLM
    app_path = app_path.replace('\\\\', '\\').strip('"')
    app_path = os.path.normpath(app_path)
    subprocess.Popen(f'start "" "{app_path}"', shell=True)
    return app_path


def open_url(url: str) -> str:
    """Открыть URL в браузере по умолчанию."""
    webbrowser.open(url)
    return url


def handle_media_command(action: str) -> str:
    """Выполнить медиа-действие через `pyautogui`."""
    key_map = {'playpause': 'playpause', 'next': 'nexttrack', 'prev': 'prevtrack', 'stop': 'stop'}
    pyautogui.press(key_map.get(action, 'playpause'))
    return action


def handle_volume_command(action: str, amount: float) -> str:
    """Обёртка вокруг `set_system_volume` для единообразия вызовов."""
    set_system_volume(action, amount)
    return f"{action}:{amount}"


async def handle_tool_command(tool_text: str):
    """Обрабатывает специальные команды из результатов MCP-инструментов.

    MCP работает в отдельном процессе, поэтому действия вроде открытия файлов
    выполняются здесь, в основном процессе.
    """
    import asyncio
    TOOL_TIMEOUT = 15.0

    def _extract_value(text: str, marker: str) -> str:
        """Извлекает значение после маркера, очищая JSON-хвосты."""
        raw = text.split(marker)[1]
        # Обрезаем по первому символу-разделителю JSON (кавычки, запятые)
        for sep in ("'", '"', "',", '",'):
            if sep in raw:
                raw = raw.split(sep)[0]
        return raw.strip()

    if "__OPEN_FILE_COMMAND__:" in tool_text:
        path_to_open = _extract_value(tool_text, "__OPEN_FILE_COMMAND__:")
        try:
            res = await asyncio.wait_for(asyncio.to_thread(open_file, path_to_open), timeout=TOOL_TIMEOUT)
            print(f"  [Система] Файл открыт ({res})")
        except Exception as e:
            print(f"  [Система] Ошибка: {e}")

    elif "__OPEN_FOLDER_COMMAND__:" in tool_text:
        folder_to_open = _extract_value(tool_text, "__OPEN_FOLDER_COMMAND__:")
        try:
            res = await asyncio.wait_for(asyncio.to_thread(open_folder, folder_to_open), timeout=TOOL_TIMEOUT)
            print(f"  [Система] Папка открыта ({res})")
        except Exception as e:
            print(f"  [Система] Ошибка: {e}")

    elif "__OPEN_APP_COMMAND__:" in tool_text:
        app_to_open = _extract_value(tool_text, "__OPEN_APP_COMMAND__:")
        try:
            res = await asyncio.wait_for(asyncio.to_thread(open_app, app_to_open), timeout=TOOL_TIMEOUT)
            print(f"  [Система] Приложение запущено ({res})")
        except Exception as e:
            print(f"  [Система] Ошибка: {e}")

    elif "__OPEN_URL_COMMAND__:" in tool_text:
        url_to_open = _extract_value(tool_text, "__OPEN_URL_COMMAND__:")
        try:
            res = await asyncio.wait_for(asyncio.to_thread(open_url, url_to_open), timeout=TOOL_TIMEOUT)
            print(f"  [Система] Открыт веб-сайт ({res})")
        except Exception as e:
            print(f"  [Система] Ошибка: {e}")

    elif "__VOLUME_COMMAND__:" in tool_text:
        try:
            _, action, amount = tool_text.split(":")
            await asyncio.wait_for(
                asyncio.to_thread(handle_volume_command, action, float(amount)),
                timeout=TOOL_TIMEOUT,
            )
            print(f"  [Система] Громкость: {action}")
        except Exception as e:
            print(f"  [Система] Ошибка звука: {e}")

    elif "__MEDIA_COMMAND__:" in tool_text:
        try:
            action = tool_text.split(":")[1]
            await asyncio.wait_for(
                asyncio.to_thread(handle_media_command, action),
                timeout=TOOL_TIMEOUT,
            )
            print(f"  [Система] Медиа: {action}")
        except Exception as e:
            print(f"  [Система] Ошибка медиа: {e}")
