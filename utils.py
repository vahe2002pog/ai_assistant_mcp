import os
from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
import subprocess
import webbrowser
import pyautogui


async def get_all_tools():
    """Возвращает список инструментов MCP (используется в main)."""
    mcp_config = {
        "pc_modules": {
            "command": "python",
            "args": ["launch_mcp.py"],
            "transport": "stdio",
        },
    }
    mcp_client = MultiServerMCPClient(mcp_config)
    return await mcp_client.get_tools()



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
