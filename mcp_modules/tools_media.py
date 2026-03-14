"""
Инструменты для управления звуком и медиаконтролем.

Этот модуль регистрирует два инструмента в MCP:
- `control_volume(action, amount)` — управление системной громкостью через pycaw
- `control_media(action)` — управление воспроизведением через pyautogui
"""

from .mcp_core import mcp


@mcp.tool
def control_volume(action: str, amount: float = 0.1) -> str:
    """
    Управление громкостью системы.
    Args:
        action: 'up', 'down', 'mute', 'set'
        amount: от 0.0 до 1.0 (насколько изменить)
    """
    return f"__VOLUME_COMMAND__:{action}:{amount}"


@mcp.tool
def control_media(action: str) -> str:
    """
    Управление воспроизведением.
    Всегда вызывай, если пользователь запросил действие, не смотря на то, что ты уже выполнял его ранее, так как пользователь может сам изменить состояние медиа.
    Args:
        action: 'playpause', 'next', 'prev', 'stop'
    """
    return f"__MEDIA_COMMAND__:{action}"
