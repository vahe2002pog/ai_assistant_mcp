# -*- coding: utf-8 -*-
"""Инструменты обнаружения контролов."""

import logging
from typing import Optional, List

from mcp.server.fastmcp import FastMCP

import uiautomation as auto

from ..core import (
    control_to_info,
    find_control,
    get_control_by_handle,
    format_error,
    check_admin,
    init_com,
)
from ..models import ControlSelector, ControlInfo

logger = logging.getLogger(__name__)

# Инициализируем COM при загрузке модуля (требуется для UIAutomation)
init_com()


def register_discovery_tools(mcp: FastMCP):
    """Зарегистрировать инструменты обнаружения на MCP сервере."""

    @mcp.tool()
    def ui_find_window(
        name: Optional[str] = None,
        className: Optional[str] = None,
        processId: Optional[int] = None,
        handle: Optional[int] = None,
    ) -> dict:
        """Найти верхнеуровневое окно по имени, классу, PID или дескриптору.

        Args:
            name: Заголовок окна (точное совпадение)
            className: Класс окна в Windows
            processId: ID процесса
            handle: Дескриптор окна (если передан — возвращается напрямую)

        Returns:
            Информация о найденном окне или ошибка
        """
        check_admin()

        try:
            if handle:
                control = auto.ControlFromHandle(handle)
            else:
                search_params = {"searchDepth": 1}
                if name:
                    search_params["Name"] = name
                if className:
                    search_params["ClassName"] = className
                if processId:
                    search_params["ProcessId"] = processId
                control = auto.WindowControl(**search_params)

            if not control or not control.Exists():
                return format_error(
                    "WINDOW_NOT_FOUND",
                    f"Окно не найдено: name={name}, className={className}, processId={processId}",
                    [
                        "Осмотрите запущенные процессы с помощью ui_list_processes",
                        "Проверьте, что окно открыто и видимо",
                        "Пытайтесь выполнить поиск по целым частям имен окна",
                    ]
                )

            return {"success": True, "data": control_to_info(control).model_dump()}

        except Exception as e:
            logger.exception("ui_find_window failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_find_control(
        parentHandle: Optional[int] = None,
        controlType: Optional[str] = None,
        name: Optional[str] = None,
        nameContains: Optional[str] = None,
        nameRegex: Optional[str] = None,
        className: Optional[str] = None,
        automationId: Optional[str] = None,
        depth: int = 0xFFFFFFFF,
        index: int = 1,
    ) -> dict:
        """Найти дочерний контрол внутри родительского.

        Args:
            parentHandle: Дескриптор родителя (по умолчанию поиск с корня)
            controlType: Тип контрола (например, ButtonControl, EditControl)
            name: Точное совпадение имени
            nameContains: Подстрока имени для поиска
            nameRegex: Регулярное выражение для имени
            className: Класс окна Windows
            automationId: Automation ID
            depth: Глубина поиска (по умолчанию — неограниченно)
            index: N-й подходящий контрол (1-based)

        Returns:
            Информация о контроле или ошибка
        """
        check_admin()

        try:
            selector = ControlSelector(
                parent_handle=parentHandle,
                control_type=controlType,
                name=name,
                name_contains=nameContains,
                name_regex=nameRegex,
                class_name=className,
                automation_id=automationId,
                depth=depth,
                index=index,
            )

            control = find_control(selector)

            if not control or not control.Exists():
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Контрол не найден: {selector.model_dump()}",
                    [
                        "Пытайтесь использовать nameContains для приблизительного поиска",
                        "Увеличьте глубину поиска depth",
                        "Осмотрите доступные контролы с помощью ui_get_children",
                    ],
                    {"searchParams": selector.model_dump()},
                )

            return {"success": True, "data": control_to_info(control).model_dump()}

        except Exception as e:
            logger.exception("ui_find_control failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_get_children(
        handle: int,
        depth: int = 1,
    ) -> dict:
        """Получить дочерние элементы контрола.

        Args:
            handle: Дескриптор родительского контрола
            depth: Глубина обхода (по умолчанию 1 — только непосредственные дети)

        Returns:
            Список дочерних контролов
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

            children = []

            def walk(ctrl: auto.Control, current_depth: int):
                if current_depth > depth:
                    return
                for child in ctrl.GetChildren():
                    children.append(control_to_info(child).model_dump())
                    walk(child, current_depth + 1)

            walk(control, 1)
            return {"success": True, "data": children}

        except Exception as e:
            logger.exception("ui_get_children failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_get_focused() -> dict:
        """Получить текущий контрол, имеющий фокус."""
        check_admin()

        try:
            control = auto.GetFocusedControl()
            if not control:
                return format_error("NO_FOCUSED_CONTROL", "Не удалось получить элемент с фокусом")

            return {"success": True, "data": control_to_info(control).model_dump()}

        except Exception as e:
            logger.exception("ui_get_focused failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_get_foreground() -> dict:
        """Получить переднее (активное) окно."""
        check_admin()

        try:
            control = auto.GetForegroundControl()
            if not control:
                return format_error("NO_FOREGROUND_WINDOW", "Не удалось получить активное окно")

            return {"success": True, "data": control_to_info(control).model_dump()}

        except Exception as e:
            logger.exception("ui_get_foreground failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_control_from_point(
        x: int,
        y: int,
    ) -> dict:
        """Получить контрол по экранным координатам.

        Args:
            x: X-координата на экране
            y: Y-координата на экране

        Returns:
            Информация о контроле в указанной точке
        """
        check_admin()

        try:
            control = auto.ControlFromPoint(x, y)
            if not control:
                return format_error(
                    "NO_CONTROL_AT_POINT",
                    f"Контрол не найден в  координатах ({x}, {y})",
                )

            return {"success": True, "data": control_to_info(control).model_dump()}

        except Exception as e:
            logger.exception("ui_control_from_point failed")
            return format_error("INTERNAL_ERROR", str(e))
