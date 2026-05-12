"""
Tools for system volume and media-key control on Windows.

The functions execute the action directly. Older code returned sentinel strings
like ``__VOLUME_COMMAND__`` for a GUI layer to intercept, but the current
ToolAgent loop calls tools in-process, so sentinels never reached Windows.
"""

from __future__ import annotations

import math
import time

from .mcp_core import mcp


def _clamp01(value: float) -> float:
    if not math.isfinite(value):
        return 0.0
    return max(0.0, min(1.0, value))


def _amount_to_scalar(amount: float, default: float = 0.1) -> float:
    try:
        value = float(amount)
    except Exception:
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    # Accept both fractions (0.3) and percents (30).
    if value > 1:
        value = value / 100.0
    return _clamp01(value)


def _pct(value: float) -> int:
    return int(round(_clamp01(value) * 100))


def _endpoint_volume():
    import comtypes
    from ctypes import POINTER, cast
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume

    try:
        comtypes.CoInitialize()
    except Exception:
        pass
    devices = AudioUtilities.GetSpeakers()
    # pycaw 2024+ exposes the endpoint directly on AudioDevice.
    endpoint = getattr(devices, "EndpointVolume", None)
    if endpoint is not None:
        return endpoint
    # Older pycaw examples use IMMDevice.Activate.
    interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
    return cast(interface, POINTER(IAudioEndpointVolume))


def _press_vk(vk_code: int, times: int = 1) -> None:
    import ctypes

    user32 = ctypes.windll.user32
    KEYEVENTF_KEYUP = 0x0002
    for _ in range(max(1, int(times))):
        user32.keybd_event(vk_code, 0, 0, 0)
        time.sleep(0.02)
        user32.keybd_event(vk_code, 0, KEYEVENTF_KEYUP, 0)
        time.sleep(0.03)


def _fallback_volume_keys(action: str, amount: float) -> str:
    VK_VOLUME_MUTE = 0xAD
    VK_VOLUME_DOWN = 0xAE
    VK_VOLUME_UP = 0xAF

    delta = _amount_to_scalar(amount)
    steps = max(1, int(round(delta * 50)))  # Windows media keys are ~2% steps.
    if action == "up":
        _press_vk(VK_VOLUME_UP, steps)
        return f"Громкость увеличена примерно на {_pct(delta)}%."
    if action == "down":
        _press_vk(VK_VOLUME_DOWN, steps)
        return f"Громкость уменьшена примерно на {_pct(delta)}%."
    if action in ("mute", "toggle_mute"):
        _press_vk(VK_VOLUME_MUTE, 1)
        return "Звук переключен mute/unmute."
    raise RuntimeError("fallback supports only up/down/mute")


@mcp.tool
def control_volume(action: str, amount: float = 0.1) -> str:
    """
    Управление системной громкостью.
    Args:
        action: 'up', 'down', 'mute', 'unmute', 'toggle_mute', 'set', 'get'
        amount: 0.0..1.0 или 0..100. Для 'set' это целевой уровень, для up/down — шаг.
    """
    action_norm = (action or "").strip().lower().replace("-", "_")
    aliases = {
        "increase": "up",
        "raise": "up",
        "louder": "up",
        "decrease": "down",
        "lower": "down",
        "quieter": "down",
        "toggle": "toggle_mute",
        "togglemute": "toggle_mute",
        "get_volume": "get",
    }
    action_norm = aliases.get(action_norm, action_norm)

    if action_norm not in {"up", "down", "mute", "unmute", "toggle_mute", "set", "get"}:
        return "Ошибка: action должен быть up, down, mute, unmute, toggle_mute, set или get."

    try:
        vol = _endpoint_volume()
        current = float(vol.GetMasterVolumeLevelScalar())
        muted = bool(vol.GetMute())

        if action_norm == "get":
            return f"Громкость: {_pct(current)}%, mute: {'да' if muted else 'нет'}."
        if action_norm == "set":
            target = _amount_to_scalar(amount, default=current)
            vol.SetMasterVolumeLevelScalar(target, None)
            if muted and target > 0:
                vol.SetMute(0, None)
            return f"Громкость установлена на {_pct(target)}%."
        if action_norm == "up":
            delta = _amount_to_scalar(amount)
            target = _clamp01(current + delta)
            vol.SetMasterVolumeLevelScalar(target, None)
            if muted and target > 0:
                vol.SetMute(0, None)
            return f"Громкость увеличена до {_pct(target)}%."
        if action_norm == "down":
            delta = _amount_to_scalar(amount)
            target = _clamp01(current - delta)
            vol.SetMasterVolumeLevelScalar(target, None)
            return f"Громкость уменьшена до {_pct(target)}%."
        if action_norm == "mute":
            vol.SetMute(1, None)
            return "Звук выключен."
        if action_norm == "unmute":
            vol.SetMute(0, None)
            return f"Звук включен. Громкость: {_pct(current)}%."
        if action_norm == "toggle_mute":
            vol.SetMute(0 if muted else 1, None)
            return "Звук включен." if muted else "Звук выключен."
    except Exception as e:
        if action_norm in {"up", "down", "mute", "toggle_mute"}:
            try:
                return _fallback_volume_keys(action_norm, amount)
            except Exception as fallback_error:
                return f"Ошибка управления громкостью: {e}; fallback тоже не сработал: {fallback_error}"
        return f"Ошибка управления громкостью: {e}"

    return "Ошибка: действие громкости не выполнено."


@mcp.tool
def control_media(action: str) -> str:
    """
    Управление воспроизведением системными медиа-клавишами.
    Args:
        action: 'playpause', 'play_pause', 'next', 'prev', 'stop'
    """
    action_norm = (action or "").strip().lower().replace("-", "_")
    aliases = {
        "play": "playpause",
        "pause": "playpause",
        "toggle": "playpause",
        "play_pause": "playpause",
        "previous": "prev",
        "back": "prev",
    }
    action_norm = aliases.get(action_norm, action_norm)
    key_map = {
        "next": 0xB0,
        "prev": 0xB1,
        "stop": 0xB2,
        "playpause": 0xB3,
    }
    vk = key_map.get(action_norm)
    if vk is None:
        return "Ошибка: action должен быть playpause, next, prev или stop."
    try:
        _press_vk(vk, 1)
        labels = {
            "playpause": "Воспроизведение переключено.",
            "next": "Включён следующий трек.",
            "prev": "Включён предыдущий трек.",
            "stop": "Воспроизведение остановлено.",
        }
        return labels[action_norm]
    except Exception as e:
        return f"Ошибка управления медиа: {e}"
