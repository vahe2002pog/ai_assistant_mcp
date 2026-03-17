import os
from langchain_core.messages import HumanMessage
from langchain_mcp_adapters.client import MultiServerMCPClient
import subprocess
import webbrowser
import pyautogui


async def get_all_tools():
    """Возвращает список инструментов MCP (используется в main)."""
    # Путь к локальной копии mcp-server-browser-use в рабочей папке проекта
    browser_use_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "mcp-browser-use"))

    # Попробуем взять имя модели из переменных окружения, иначе из config.MODEL_NAMES
    try:
        from config import MODEL_NAMES

        default_model = MODEL_NAMES[0]
    except Exception:
        default_model = os.getenv("MODEL_NAME", "qwen2.5vl:3b")

    MODEL_NAME = os.getenv("MCP_MODEL_NAME", default_model)

    mcp_config = {
        "pc_modules": {
            "command": "python",
            "args": ["launch_mcp.py"],
            "transport": "stdio",
        },
        "browser-use": {
            "command": "uv",
            "args": ["--directory", browser_use_path, "run", "mcp-server-browser-use"],
            "env": {
                "MCP_MODEL_PROVIDER": "ollama",
                "OLLAMA_ENDPOINT": "http://localhost:11434",
                "MCP_MODEL_NAME": MODEL_NAME,
                "BROWSER_HEADLESS": "false",
                "BROWSER_DISABLE_SECURITY": "false",
                "MCP_USE_VISION": "false",
            },
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
