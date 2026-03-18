# -*- coding: utf-8 -*-
"""Инструменты запроса для получения информации о контролах."""

import logging
import os
import time
from typing import Optional, List

from mcp.server.fastmcp import FastMCP

import uiautomation as auto

from ..core import get_control_by_handle, format_error, check_admin, init_com
from ..config import config

logger = logging.getLogger(__name__)

# Инициализируем COM при загрузке модуля (требуется для UIAutomation)
init_com()


def register_query_tools(mcp: FastMCP):
    """Зарегистрировать инструменты запросов на MCP сервере."""

    @mcp.tool()
    def ui_get_properties(
        handle: int,
        properties: Optional[List[str]] = None,
    ) -> dict:
        """Получить свойства контрола.

        Args:
            handle: Дескриптор контрола
            properties: Список конкретных свойств для получения (по умолчанию — все)
                Возможные: name, className, controlType, automationId, processId,
                enabled, visible, rect, helpText, frameworkId

        Returns:
            Словарь со свойствами контрола
        """
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                    ["Дескриптор может быть устаревшим, переищите контрол"],
                )

            all_props = {
                "name": control.Name,
                "className": control.ClassName,
                "controlType": control.ControlTypeName,
                "automationId": control.AutomationId,
                "processId": control.ProcessId,
                "enabled": control.IsEnabled,
                "visible": not control.IsOffscreen,
                "helpText": control.HelpText,
                "frameworkId": control.FrameworkId,
                "handle": control.NativeWindowHandle,
            }

            try:
                rect = control.BoundingRectangle
                all_props["rect"] = {
                    "left": rect.left,
                    "top": rect.top,
                    "right": rect.right,
                    "bottom": rect.bottom,
                    "width": rect.width(),
                    "height": rect.height(),
                }
            except Exception:
                all_props["rect"] = None

            if properties:
                result = {k: v for k, v in all_props.items() if k in properties}
            else:
                result = all_props

            return {"success": True, "data": result}

        except Exception as e:
            logger.exception("ui_get_properties failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_get_text(handle: int) -> dict:
        """Получить текстовое содержимое контрола.

        Args:
            handle: Дескриптор контрола

        Returns:
            Текст в виде строки
        """
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                    ["Дескриптор может быть устаревшим, переищите контрол"],
                )

            # Сначала пробуем ValuePattern
            try:
                pattern = control.GetValuePattern()
                if pattern:
                    return {"success": True, "data": {"text": pattern.Value}}
            except Exception:
                pass

            # Пробуем TextPattern
            try:
                pattern = control.GetTextPattern()
                if pattern:
                    return {"success": True, "data": {"text": pattern.DocumentRange.GetText(-1)}}
            except Exception:
                pass

            # Пробуем LegacyIAccessiblePattern
            try:
                pattern = control.GetLegacyIAccessiblePattern()
                if pattern:
                    return {"success": True, "data": {"text": pattern.Value}}
            except Exception:
                pass

            # Запасной вариант — свойство Name
            return {"success": True, "data": {"text": control.Name or ""}}

        except Exception as e:
            logger.exception("ui_get_text failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_get_rect(handle: int) -> dict:
        """Получить ограничивающий прямоугольник контрола.

        Args:
            handle: Дескриптор контрола

        Returns:
            Координаты прямоугольника
        """
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                    ["Дескриптор может быть устаревшим, переищите контрол"],
                )

            rect = control.BoundingRectangle
            return {
                "success": True,
                "data": {
                    "left": rect.left,
                    "top": rect.top,
                    "right": rect.right,
                    "bottom": rect.bottom,
                    "width": rect.width(),
                    "height": rect.height(),
                    "centerX": rect.xcenter(),
                    "centerY": rect.ycenter(),
                }
            }

        except Exception as e:
            logger.exception("ui_get_rect failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_screenshot(
        handle: int,
        savePath: Optional[str] = None,
        captureCursor: Optional[bool] = None,
    ) -> dict:
        """Сделать снимок экрана указанного контрола.

        Args:
            handle: Дескриптор контрола
            savePath: Путь для сохранения изображения (по умолчанию — авто)
            captureCursor: Захватить ли курсор на снимке

        Returns:
            Путь к сохранённому изображению
        """
        check_admin()

        # Обработка значения по умолчанию
        if captureCursor is None:
            captureCursor = False

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                    ["Дескриптор может быть устаревшим, переищите контрол"],
                )

            # Генерируем путь по умолчанию
            if not savePath:
                os.makedirs(config.screenshot_dir, exist_ok=True)
                timestamp = time.strftime("%Y%m%d_%H%M%S")
                savePath = os.path.join(config.screenshot_dir, f"screenshot_{timestamp}.png")

            control.CaptureToImage(savePath, captureCursor=captureCursor)
            return {"success": True, "data": {"path": savePath}}

        except Exception as e:
            logger.exception("ui_screenshot failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_exists(
        handle: int,
        timeout: Optional[float] = None,
    ) -> dict:
        """Проверить, существует ли контрол.

        Args:
            handle: Дескриптор контрола
            timeout: Время ожидания в секундах

        Returns:
            Флаг существования контрола
        """
        check_admin()

        # Обработка значения по умолчанию
        if timeout is None:
            timeout = 0

        try:
            control = get_control_by_handle(handle)
            if not control:
                return {"success": True, "data": {"exists": False}}

            if timeout > 0:
                exists = control.Exists(maxSearchSeconds=timeout)
            else:
                exists = control.Exists()

            return {"success": True, "data": {"exists": exists}}

        except Exception as e:
            logger.exception("ui_exists failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_wait_for(
        condition: str,
        timeout: Optional[float] = None,
        parentHandle: Optional[int] = None,
    ) -> dict:
        """Ожидать выполнения условия.

        Args:
            condition: Тип условия (control_exists, control_disappear, window_active)
            timeout: Таймаут в секундах
            parentHandle: Дескриптор родителя для проверки условия

        Returns:
            Информация, выполнено ли условие
        """
        check_admin()

        # Обработка значения по умолчанию
        if timeout is None:
            timeout = 10

        try:
            start = time.time()

            while time.time() - start < timeout:
                if condition == "control_exists" and parentHandle:
                    control = get_control_by_handle(parentHandle)
                    if control and control.Exists():
                        return {"success": True, "data": {"met": True, "condition": condition}}

                elif condition == "control_disappear" and parentHandle:
                    control = get_control_by_handle(parentHandle)
                    if not control or not control.Exists():
                        return {"success": True, "data": {"met": True, "condition": condition}}

                elif condition == "window_active":
                    fg = auto.GetForegroundControl()
                    if parentHandle and fg and fg.NativeWindowHandle == parentHandle:
                        return {"success": True, "data": {"met": True, "condition": condition}}

                time.sleep(0.5)

            return {"success": True, "data": {"met": False, "condition": condition, "timeout": timeout}}

        except Exception as e:
            logger.exception("ui_wait_for failed")
            return format_error("INTERNAL_ERROR", str(e))
