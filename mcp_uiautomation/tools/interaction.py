# -*- coding: utf-8 -*-
"""Инструменты для взаимодействия с элементами интерфейса (клики, ввод и т.д.)."""

import logging
from typing import Optional
import ctypes

from mcp.server.fastmcp import FastMCP

import uiautomation as auto

from ..core import (
    get_control_by_handle,
    format_error,
    create_confirmation,
    confirm_operation,
    is_dangerous_tool,
    check_admin,
    init_com,
)
from ..config import config
from ..models import MouseButton

logger = logging.getLogger(__name__)

# Инициализируем COM при загрузке модуля (требуется для UIAutomation)
init_com()

# Store pending confirmations for dangerous operations
_pending_confirms = {}


def register_interaction_tools(mcp: FastMCP):
    """Зарегистрировать инструменты взаимодействия на MCP сервере."""

    @mcp.tool()
    def ui_click(
        handle: Optional[int] = None,
        x: Optional[int] = None,
        y: Optional[int] = None,
        button: str = "left",
        double: bool = False,
    ) -> dict:
        """Клик по контролу или по экранным координатам.

        Args:
            handle: Дескриптор контрола (клик по центру, если не заданы x/y)
            x: Относительный X от центра контрола или абсолютный при отсутствии handle
            y: Относительный Y от центра контрола или абсолютный при отсутствии handle
            button: Кнопка мыши (left, right, middle)
            double: Двойной клик

        Returns:
            Результат выполнения (успех/ошибка)
        """
        check_admin()

        try:
            # Click at absolute coordinates
            if handle is None and x is not None and y is not None:
                if button == "right":
                    auto.RightClick(x, y)
                elif button == "middle":
                    auto.MiddleClick(x, y)
                elif double:
                    auto.DoubleClick(x, y)
                else:
                    auto.Click(x, y)
                return {"success": True, "data": {"action": "click", "x": x, "y": y}}

            # Click on control
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                )

            # Determine click method
            if button == "right":
                control.RightClick(x, y)
            elif button == "middle":
                control.MiddleClick(x, y)
            elif double:
                control.DoubleClick(x, y)
            else:
                control.Click(x, y)

            return {"success": True, "data": {"action": "click", "handle": handle}}

        except Exception as e:
            logger.exception("ui_click failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_send_keys(
        handle: int,
        text: str,
        interval: float = 0.05,
    ) -> dict:
        """Отправить клавиатурный ввод в контрол.

        Args:
            handle: Дескриптор контрола
            text: Текст/комбинации клавиш (например, {Ctrl}, {Enter})
            interval: Интервал между нажатиями в секундах

        Returns:
            Результат выполнения
        """
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                )

            control.SendKeys(text, interval=interval)
            return {"success": True, "data": {"action": "send_keys", "text": text}}

        except Exception as e:
            logger.exception("ui_send_keys failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_set_value(
        handle: int,
        value: str,
    ) -> dict:
        """Установить значение текста в контроле с помощью ValuePattern.

        Args:
            handle: Дескриптор контрола
            value: Устанавливаемое значение

        Returns:
            Результат выполнения
        """
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                )

            pattern = control.GetValuePattern()
            if not pattern:
                return format_error(
                    "PATTERN_NOT_SUPPORTED",
                    "Контрол не поддерживает ValuePattern",
                    ["Попытайтесь использовать ui_send_keys для отправки команд клавиатуры"],
                )

            pattern.SetValue(value)
            return {"success": True, "data": {"action": "set_value", "value": value}}

        except Exception as e:
            logger.exception("ui_set_value failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_close_window(
        handle: int,
        confirmationToken: Optional[str] = None,
    ) -> dict:
        """Закрыть окно. Требует подтверждения.

        Args:
            handle: Дескриптор окна
            confirmationToken: Токен подтверждения (если требуется)

        Returns:
            Запрос на подтверждение, успешный результат или ошибка
        """
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор окна: {handle}",
                )

            # Check if confirmation is needed
            if config.confirmation_enabled and not confirmationToken:
                request = create_confirmation(
                    "ui_close_window",
                    {"windowName": control.Name, "handle": handle},
                    f"В основном, закрыть окно «{control.Name}», нужно подтверждение?",
                )
                return {"success": False, "requiresConfirmation": True, "confirmation": request.model_dump()}

            # Verify confirmation token
            if config.confirmation_enabled and confirmationToken:
                result = confirm_operation(confirmationToken, True)
                if not result:
                    return format_error("INVALID_CONFIRMATION", "Токен подтверждения невалидный или истек")

            # Close the window
            pattern = control.GetWindowPattern()
            if pattern:
                pattern.Close()
            else:
                # Fallback to Alt+F4
                control.SetFocus()
                auto.SendKeys("{Alt}{F4}")

            return {"success": True, "data": {"action": "close_window", "handle": handle}}

        except Exception as e:
            logger.exception("ui_close_window failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_move_window(
        handle: int,
        x: Optional[int] = None,
        y: Optional[int] = None,
        width: Optional[int] = None,
        height: Optional[int] = None,
    ) -> dict:
        """Переместить и/или изменить размер окна.

        Args:
            handle: Дескриптор окна
            x: Новая позиция X (необязательно)
            y: Новая позиция Y (необязательно)
            width: Новая ширина (необязательно)
            height: Новая высота (необязательно)

        Returns:
            Результат выполнения
        """
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор окна: {handle}",
                )

            control.MoveWindow(x, y, width, height)
            return {
                "success": True,
                "data": {
                    "action": "move_window",
                    "x": x,
                    "y": y,
                    "width": width,
                    "height": height,
                }
            }

        except Exception as e:
            logger.exception("ui_move_window failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_minimize_window(handle: int) -> dict:
        """Свернуть окно в панель задач.

        Args:
            handle: Дескриптор окна

        Returns:
            Результат выполнения
        """
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор окна: {handle}",
                )

            # Используем Windows API для отправки команды минимизации (наиболее надёжный способ)
            # WM_SYSCOMMAND = 0x0112, SC_MINIMIZE = 0xF020
            WM_SYSCOMMAND = 0x0112
            SC_MINIMIZE = 0xF020
            
            try:
                # Получаем native handle окна
                native_handle = control.NativeWindowHandle
                if native_handle:
                    # Отправляем WM_SYSCOMMAND сообщение
                    ctypes.windll.user32.PostMessageW(native_handle, WM_SYSCOMMAND, SC_MINIMIZE, 0)
                    return {"success": True, "data": {"action": "minimize_window", "handle": handle}}
            except Exception as e:
                logger.debug(f"PostMessageW failed: {e}")
            
            # Fallback 1: через WindowPattern
            pattern = control.GetWindowPattern()
            if pattern:
                try:
                    pattern.SetWindowVisualState(1)  # 1 = Minimized
                    return {"success": True, "data": {"action": "minimize_window", "handle": handle}}
                except Exception as e:
                    logger.debug(f"SetWindowVisualState failed: {e}")
            
            # Fallback 2: клик по кнопке сворачивания в заголовке окна
            try:
                rect = control.BoundingRectangle
                if rect:
                    # Кнопка сворачивания находится в правом верхнем углу, примерно за 75px до края
                    minimize_btn_x = rect.right - 75
                    minimize_btn_y = rect.top + 10
                    auto.Click(minimize_btn_x, minimize_btn_y)
                    return {"success": True, "data": {"action": "minimize_window", "handle": handle}}
            except Exception as e:
                logger.debug(f"Click fallback failed: {e}")

            return format_error("MINIMIZE_FAILED", "Не удалось свернуть окно, все методы исчерпаны")

        except Exception as e:
            logger.exception("ui_minimize_window failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_maximize_window(handle: int) -> dict:
        """Развернуть окно на весь экран.

        Args:
            handle: Дескриптор окна

        Returns:
            Результат выполнения
        """
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор окна: {handle}",
                )

            # Используем Windows API для отправки команды максимизации
            # WM_SYSCOMMAND = 0x0112, SC_MAXIMIZE = 0xF030
            WM_SYSCOMMAND = 0x0112
            SC_MAXIMIZE = 0xF030
            
            try:
                native_handle = control.NativeWindowHandle
                if native_handle:
                    ctypes.windll.user32.PostMessageW(native_handle, WM_SYSCOMMAND, SC_MAXIMIZE, 0)
                    return {"success": True, "data": {"action": "maximize_window", "handle": handle}}
            except Exception as e:
                logger.debug(f"PostMessageW failed: {e}")
            
            # Fallback 1: через WindowPattern
            pattern = control.GetWindowPattern()
            if pattern:
                try:
                    pattern.SetWindowVisualState(2)  # 2 = Maximized
                    return {"success": True, "data": {"action": "maximize_window", "handle": handle}}
                except Exception as e:
                    logger.debug(f"SetWindowVisualState failed: {e}")
            
            # Fallback 2: комбинация клавиш (Windows + Up)
            try:
                auto.SendKeys("{LWin}{Up}")
                return {"success": True, "data": {"action": "maximize_window", "handle": handle}}
            except Exception as e:
                logger.debug(f"SendKeys fallback failed: {e}")

            return format_error("MAXIMIZE_FAILED", "Не удалось развернуть окно")

        except Exception as e:
            logger.exception("ui_maximize_window failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_restore_window(handle: int) -> dict:
        """Восстановить окно из свёрнутого или развёрнутого состояния.

        Args:
            handle: Дескриптор окна

        Returns:
            Результат выполнения
        """
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор окна: {handle}",
                )

            # Используем Windows API для отправки команды восстановления
            # WM_SYSCOMMAND = 0x0112, SC_RESTORE = 0xF120
            WM_SYSCOMMAND = 0x0112
            SC_RESTORE = 0xF120
            
            try:
                native_handle = control.NativeWindowHandle
                if native_handle:
                    ctypes.windll.user32.PostMessageW(native_handle, WM_SYSCOMMAND, SC_RESTORE, 0)
                    return {"success": True, "data": {"action": "restore_window", "handle": handle}}
            except Exception as e:
                logger.debug(f"PostMessageW failed: {e}")
            
            # Fallback 1: через WindowPattern
            pattern = control.GetWindowPattern()
            if pattern:
                try:
                    pattern.SetWindowVisualState(0)  # 0 = Normal
                    return {"success": True, "data": {"action": "restore_window", "handle": handle}}
                except Exception as e:
                    logger.debug(f"SetWindowVisualState failed: {e}")
            
            return format_error("RESTORE_FAILED", "Не удалось восстановить окно")

        except Exception as e:
            logger.exception("ui_restore_window failed")
            return format_error("INTERNAL_ERROR", str(e))
