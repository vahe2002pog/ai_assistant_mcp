# -*- coding: utf-8 -*-
"""Инструменты для операций с паттернами (Invoke, Toggle, Scroll и т.д.)."""

import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP

import uiautomation as auto
from uiautomation import ScrollAmount

from ..core import (
    get_control_by_handle,
    format_error,
    create_confirmation,
    confirm_operation,
    check_admin,
    init_com,
)
from ..config import config

logger = logging.getLogger(__name__)

# Инициализируем COM при загрузке модуля (требуется для UIAutomation)
init_com()


def register_pattern_tools(mcp: FastMCP):
    """Зарегистрировать инструменты паттернов на MCP сервере."""

    @mcp.tool()
    def ui_invoke(handle: int) -> dict:
        """Выполнить Invoke на контроле - активировать элемент как будто на него кликнули.

        InvokePattern - это стандартный паттерн для активации элементов. Используется для
        кнопок, пунктов меню и других элементов, которые можно \"активировать\".
        Это надёжнее, чем ручной клик, потому что работает через интерфейс Accessibility.

        Args:
            handle: Дескриптор контрола (обычно кнопа или пункт меню)
                    Должен поддерживать InvokePattern

        Returns:
            {\"success\": true, \"data\": {\"action\": \"invoke\"}}

        Примеры:
            - ui_invoke(handle=ok_button) - активировать кнопку OK
            - ui_invoke(handle=menu_item) - активировать пункт меню
            - ui_invoke(handle=link) - активировать гиперссылку

        Совет:
            Сначала попробуйте ui_invoke, если вернёт ошибку про Pattern - используйте ui_click.
        """
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                )

            pattern = control.GetInvokePattern()
            if not pattern:
                return format_error(
                    "PATTERN_NOT_SUPPORTED",
                    "Контрол не поддерживает InvokePattern",
                    ["Попытайтесь использовать ui_click для выполнения нажатия"],
                )

            pattern.Invoke()
            return {"success": True, "data": {"action": "invoke"}}

        except Exception as e:
            logger.exception("ui_invoke failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_toggle(handle: int) -> dict:
        """Переключить состояние контрола (TogglePattern)."""
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                )

            pattern = control.GetTogglePattern()
            if not pattern:
                return format_error(
                    "PATTERN_NOT_SUPPORTED",
                    "Контрол не поддерживает TogglePattern",
                )

            pattern.Toggle()
            return {"success": True, "data": {"action": "toggle", "state": str(pattern.ToggleState)}}

        except Exception as e:
            logger.exception("ui_toggle failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_expand_collapse(
        handle: int,
        action: Optional[str] = None,
    ) -> dict:
        """Развернуть или свернуть контрол (ExpandCollapsePattern).

        Args:
            handle: Дескриптор контрола
            action: Действие: "expand" или "collapse"

        Returns:
            Результат выполнения
        """
        check_admin()

        # Обработка значения по умолчанию
        if action is None:
            action = "expand"

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                )

            pattern = control.GetExpandCollapsePattern()
            if not pattern:
                return format_error(
                    "PATTERN_NOT_SUPPORTED",
                    "Контрол не поддерживает ExpandCollapsePattern",
                )

            if action == "expand":
                pattern.Expand()
            elif action == "collapse":
                pattern.Collapse()
            else:
                return format_error(
                    "INVALID_ACTION",
                    f"Некорректная операция: {action}",
                    ["Поддерживаемые операции: expand, collapse"],
                )

            return {"success": True, "data": {"action": action}}

        except Exception as e:
            logger.exception("ui_expand_collapse failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_select_item(handle: int) -> dict:
        """Выбрать элемент с помощью SelectionItemPattern."""
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                )

            pattern = control.GetSelectionItemPattern()
            if not pattern:
                return format_error(
                    "PATTERN_NOT_SUPPORTED",
                    "Контрол не поддерживает SelectionItemPattern",
                )

            pattern.Select()
            return {"success": True, "data": {"action": "select", "isSelected": True}}

        except Exception as e:
            logger.exception("ui_select_item failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_scroll(
        handle: int,
        direction: Optional[str] = None,
        amount: Optional[str] = None,
    ) -> dict:
        """Прокрутить контрол с помощью ScrollPattern или колесика мыши.

        Args:
            handle: Дескриптор контрола
            direction: Направление прокрутки (up, down, left, right)
            amount: Объём прокрутки (large, small)

        Returns:
            Результат выполнения
        """
        check_admin()

        # Обработка значений по умолчанию
        if direction is None:
            direction = "down"
        if amount is None:
            amount = "large"

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                )

            pattern = control.GetScrollPattern()
            if not pattern:
                # Try mouse wheel as fallback
                if direction in ("up", "down"):
                    wheel_times = 3 if amount == "large" else 1
                    if direction == "up":
                        control.WheelUp(wheelTimes=wheel_times)
                    else:
                        control.WheelDown(wheelTimes=wheel_times)
                    return {"success": True, "data": {"action": "scroll", "method": "wheel"}}
                return format_error(
                    "PATTERN_NOT_SUPPORTED",
                    "Контрол не поддерживает ScrollPattern",
                )

            # Map direction and amount to ScrollAmount
            scroll_amount = ScrollAmount.LargeIncrement if amount == "large" else ScrollAmount.SmallIncrement

            if direction == "up":
                pattern.Scroll(ScrollAmount.NoAmount, scroll_amount)
            elif direction == "down":
                pattern.Scroll(ScrollAmount.NoAmount, scroll_amount)
            elif direction == "left":
                pattern.Scroll(scroll_amount, ScrollAmount.NoAmount)
            elif direction == "right":
                pattern.Scroll(scroll_amount, ScrollAmount.NoAmount)
            else:
                return format_error(
                    "INVALID_DIRECTION",
                    f"Некорректное направление: {direction}",
                    ["Поддерживаемые направления: up, down, left, right"],
                )

            return {"success": True, "data": {"action": "scroll", "direction": direction}}

        except Exception as e:
            logger.exception("ui_scroll failed")
            return format_error("INTERNAL_ERROR", str(e))
