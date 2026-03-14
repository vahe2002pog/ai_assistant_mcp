# -*- coding: utf-8 -*-
"""Ядро: обёртки и вспомогательные функции для операций UIAutomation.

Содержит функции проверки прав администратора, управление запросами
подтверждения опасных операций, преобразование контролов в модели и
общие форматы ответов для инструментов.
"""

import ctypes
import logging
import uuid
from typing import Optional, Any, Dict, List

import uiautomation as auto

from .config import config
from .models import ControlInfo, ControlSelector, ConfirmationRequest

logger = logging.getLogger(__name__)


def init_com() -> None:
    """Инициализирует COM для текущего потока (требуется для UIAutomation на Windows)."""
    try:
        ctypes.windll.ole32.CoInitialize(None)
    except Exception as e:
        logger.debug(f"Ошибка CoInitialize (может быть уже инициализирован): {e}")


def uninit_com() -> None:
    """Завершает использование COM для текущего потока."""
    try:
        ctypes.windll.ole32.CoUninitialize()
    except Exception as e:
        logger.debug(f"Ошибка CoUninitialize: {e}")


def is_admin() -> bool:
    """Проверяет, запущен ли процесс с правами администратора."""
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def check_admin() -> None:
    """Проверяет права администратора и выводит предупреждение при их отсутствии."""
    # Инициализируем COM если ещё не инициализирован
    init_com()
    if config.admin_check and not is_admin():
        logger.warning(
            "Запущено без прав администратора. Некоторые операции могут завершиться неудачей. "
            "Рассмотрите запуск с правами администратора."
        )


# Хранилище ожидающих подтверждений
_pending_confirmations: Dict[str, ConfirmationRequest] = {}


def create_confirmation(tool: str, details: Dict[str, Any], message: str) -> ConfirmationRequest:
    """Создаёт запрос подтверждения для опасной операции.

    Args:
        tool: Имя инструмента, запрашивающего подтверждение.
        details: Детали операции (например, дескриптор окна, PID).
        message: Читаемое сообщение для пользователя с описанием операции.

    Returns:
        Объект `ConfirmationRequest` с уникальным токеном для отслеживания.
    """
    token = str(uuid.uuid4())[:8]
    request = ConfirmationRequest(
        tool=tool,
        details=details,
        message=message,
        confirmation_token=token,
    )
    _pending_confirmations[token] = request
    return request


def confirm_operation(token: str, approved: bool) -> Optional[ConfirmationRequest]:
    """Подтверждает или отклоняет ожидающий запрос по токену.

    Args:
        token: Токен подтверждения из первоначального запроса.
        approved: Флаг одобрения операции.

    Returns:
        Возвращает `ConfirmationRequest`, если операция одобрена, иначе `None`.
    """
    request = _pending_confirmations.pop(token, None)
    if request and approved:
        return request
    return None


def get_pending_confirmation(token: str) -> Optional[ConfirmationRequest]:
    """Возвращает ожидающий запрос подтверждения по токену без удаления."""
    return _pending_confirmations.get(token)


def clear_pending_confirmations() -> None:
    """Очищает все ожидающие запросы подтверждений."""
    _pending_confirmations.clear()


def is_dangerous_tool(tool_name: str) -> bool:
    """Проверяет, требует ли инструмент подтверждения.

    Возвращает True для инструментов, считающихся "опасными" (например, закрытие
    окна или завершение процесса).
    """
    return tool_name in ["ui_close_window", "ui_terminate_process"]


def control_to_info(control: auto.Control) -> ControlInfo:
    """Преобразует объект `Control` из `uiautomation` в модель `ControlInfo`.

    Извлекает основные свойства и прямоугольник (если доступен).
    """
    try:
        rect = control.BoundingRectangle
        rect_dict = {
            "left": rect.left,
            "top": rect.top,
            "right": rect.right,
            "bottom": rect.bottom,
        } if rect else None
    except Exception:
        rect_dict = None

    return ControlInfo(
        handle=control.NativeWindowHandle or 0,
        name=control.Name or "",
        class_name=control.ClassName or "",
        control_type=control.ControlTypeName or "",
        automation_id=control.AutomationId or "",
        process_id=control.ProcessId or 0,
        enabled=control.IsEnabled if control.IsEnabled is not None else True,
        visible=not control.IsOffscreen if control.IsOffscreen is not None else True,
        rect=rect_dict,
    )


def find_control(selector: ControlSelector) -> Optional[auto.Control]:
    """Находит контрол по параметрам селектора.

    Возвращает объект `Control` или `None`, если ничего не найдено.
    """
    # Build search parameters
    search_params: Dict[str, Any] = {}
    if selector.name:
        search_params["Name"] = selector.name
    if selector.name_contains:
        search_params["SubName"] = selector.name_contains
    if selector.name_regex:
        search_params["RegexName"] = selector.name_regex
    if selector.class_name:
        search_params["ClassName"] = selector.class_name
    if selector.automation_id:
        search_params["AutomationId"] = selector.automation_id
    if selector.control_type:
        search_params["ControlType"] = selector.control_type
    if selector.depth != 0xFFFFFFFF:
        search_params["searchDepth"] = selector.depth
    if selector.index > 1:
        search_params["foundIndex"] = selector.index

    # Get parent or root
    if selector.parent_handle:
        parent = auto.ControlFromHandle(selector.parent_handle)
        if not parent:
            return None
        search_params["searchFromControl"] = parent
    else:
        search_params["searchFromControl"] = auto.GetRootControl()

    # Find using generic Control
    return auto.Control(**search_params)


def get_control_by_handle(handle: int) -> Optional[auto.Control]:
    """Получить объект контроля по его дескриптору (handle)."""
    if not handle:
        return None
    try:
        return auto.ControlFromHandle(handle)
    except Exception:
        return None


def format_error(
    code: str,
    message: str,
    suggestions: Optional[List[str]] = None,
    context: Optional[Dict[str, Any]] = None
) -> Dict[str, Any]:
    """Форматирует ответ об ошибке в едином виде.

    Полезно для унификации структуры ошибок, возвращаемых инструментами.
    """
    return {
        "success": False,
        "error": {
            "code": code,
            "message": message,
            "suggestions": suggestions or [],
            "context": context or {},
        }
    }


def format_success(data: Any = None) -> Dict[str, Any]:
    """Форматирует успешный ответ в едином виде."""
    return {
        "success": True,
        "data": data,
    }
