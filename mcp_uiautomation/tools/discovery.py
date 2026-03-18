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

        Этот инструмент используется для поиска главных окон приложений.
        Поддерживает множество критериев поиска: точное имя, класс окна, ID процесса.

        Args:
            name: Заголовок окна (точное совпадение)
                Примеры: "Word", "Microsoft Excel", "Блокнот"
                ⚠️ ВАЖНО: Используйте точное имя из строки заголовка окна
            className: Класс окна в Windows (например, "XLMAIN", "Notepad")
                Внутреннее имя окна, не видно пользователю
            processId: ID процесса (PID) приложения
                Можно получить из ui_list_processes
            handle: Дескриптор окна (если передан — возвращается напрямую)
                Используется при повторном обращении к известному окну

        Returns:
            {"success": true, "data": {window_info}}
            Содержит: handle, name, className, processId, rect, и прочие свойства

        Примеры использования:
            - ui_find_window(name="Word") — найти открытый документ Word
            - ui_find_window(className="Notepad") — найти Блокнот по классу
            - ui_find_window(processId=1234) — найти окно процесса с ID 1234

        Совет:
            Если поиск по имени не работает, используйте ui_list_windows для
            просмотра всех открытых окон и их точных названий.
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
    def ui_list_windows(
        filter: Optional[str] = None,
        excludeHidden: Optional[bool] = None,
    ) -> dict:
        """Получить список всех открытых окон верхнего уровня (desktop-уровня).

        Полезен для обзора всех активных приложений и окон, открытых в системе.
        Позволяет выбрать нужное окно по названию или другим параметрам.

        Args:
            filter: Фильтр по имени окна (подстрока, чувствительно к регистру)
                Примеры: "Word" найдет "Document1 - Word"
                Пусто = все окна
            excludeHidden: Исключить скрытые/невидимые окна из результатов
                По умолчанию: True (показываем только видимые)

        Returns:
            {
                "success": true,
                "data": {
                    "windows": [{window_info}, ...],
                    "count": 5,
                    "filter": "Word",
                    "excludeHidden": true
                }
            }
            Каждое окно содержит: handle, name, className, processId, rect

        Примеры использования:
            - ui_list_windows() — все активные окна
            - ui_list_windows(filter="Word") — только окна Word
            - ui_list_windows(excludeHidden=False) — включить скрытые окна

        Совет:
            Используйте эту команду для диагностики, чтобы увидеть точные имена
            окон перед поиском через ui_find_window.
        """
        check_admin()

        # Обработка значения по умолчанию
        if excludeHidden is None:
            excludeHidden = True

        try:
            windows = []
            
            # Получаем все верхнеуровневые окна
            root = auto.GetRootControl()
            
            # Перебираем все window controls с глубиной 1 (только top-level)
            for child in root.GetChildren():
                try:
                    # Пропускаем скрытые окна если нужно
                    if excludeHidden and child.IsOffscreen:
                        continue
                    
                    # Применяем фильтр по имени
                    if filter and filter not in (child.Name or ""):
                        continue
                    
                    window_info = control_to_info(child).model_dump()
                    windows.append(window_info)
                except Exception as e:
                    logger.debug(f"Ошибка обработки окна: {e}")
                    continue
            
            return {
                "success": True,
                "data": {
                    "windows": windows,
                    "count": len(windows),
                    "filter": filter,
                    "excludeHidden": excludeHidden,
                }
            }

        except Exception as e:
            logger.exception("ui_list_windows failed")
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
        depth: Optional[int] = None,
        index: Optional[int] = None,
    ) -> dict:
        """Найти элемент управления (контрол) внутри окна или другого контрола.

        Основной инструмент для обнаружения кнопок, текстовых полей, меню,
        списков и других элементов интерфейса. Поддерживает гибкий поиск
        с множеством критериев.

        Args:
            parentHandle: Дескриптор родителя
                По умолчанию: None (поиск с корня всей системы)
                Совет: Сначала найдите окно ui_find_window, затем используйте его handle
            controlType: Тип контрола (название класса UIAutomation)
                Примеры: "ButtonControl", "EditControl", "TextControl", 
                "MenuItemControl", "ListItemControl", "CheckBoxControl"
            name: Точное совпадение имени контрола
                Примеры: "OK", "Отменить", "Сохранить"
            nameContains: Подстрока имени (более гибкий поиск)
                Примеры: nameContains="Save" найдет "Save Document"
            nameRegex: Регулярное выражение для имени
                Примеры: ".*Button.*" найдет все кнопки
            className: Класс контрола Windows
                Редко используется, специфично для каждого приложения
            automationId: Уникальный Automation ID элемента
                Используется для поиска по уникальному идентификатору
            depth: Глубина поиска в дереве элементов
                По умолчанию: 0xFFFFFFFF (весь подъём)
                1 = только прямые потомки
            index: N-й найденный элемент (1-based индекс)
                По умолчанию: 1 (первый найденный)

        Returns:
            {"success": true, "data": {control_info}}
            Для Modern UI элементов (handle=0) также возвращает:
            - center_x, center_y: координаты центра для клика
            - note: "handle=0 (Modern UI элемент)"

        Примеры использования:
            - ui_find_control(parentHandle=window_handle, name="OK")
            - ui_find_control(controlType="ButtonControl", nameContains="Save")
            - ui_find_control(nameRegex=".*Delete.*", index=2) — вторая кнопка Delete

        Совет для Modern UI (Office Ribbon etc):
            Если handle=0, используйте возвращённые center_x/center_y для ui_click
            вместо самого handle.
        """
        check_admin()

        # Обработка значений по умолчанию
        if depth is None:
            depth = 0xFFFFFFFF
        if index is None:
            index = 1

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

            control_info = control_to_info(control).model_dump()
            
            # Для элементов с handle=0 добавляем координаты центра
            if control_info.get("handle") == 0 and control_info.get("rect"):
                rect = control_info["rect"]
                center_x = (rect["left"] + rect["right"]) // 2
                center_y = (rect["top"] + rect["bottom"]) // 2
                control_info["rect"]["center_x"] = center_x
                control_info["rect"]["center_y"] = center_y
                control_info["note"] = "handle=0 (Modern UI элемент). Для клика используйте center_x/center_y"

            return {"success": True, "data": control_info}

        except Exception as e:
            logger.exception("ui_find_control failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_get_children(
        handle: int,
        depth: Optional[int] = None,
    ) -> dict:
        """Получить дочерние элементы контрола (разворот дерева элементов).

        Очень полезен для исследования структуры интерфейса приложения.
        Позволяет увидеть все элементы внутри окна и их иерархию.

        Args:
            handle: Дескриптор родительского контрола
                Обычно: дескриптор окна из ui_find_window
            depth: Глубина обхода дерева элементов
                По умолчанию: 1 (только непосредственные дети)
                Примеры: 2 = внуки, 3 = правнуки
                Совет: Используйте сдержанно, чтобы не перегружать вывод

        Returns:
            {
                "success": true,
                "data": [
                    {control_info_1},
                    {control_info_2},
                    ...
                ]
            }

        Примеры использования:
            - ui_get_children(handle=window_handle, depth=1)
              — все кнопки, текстовые поля и др. на главном уровне
            - ui_get_children(handle=window_handle, depth=2)
              — включить элементы на один уровень глубже

        Совет для Modern UI (Office Ribbon etc):
            Элементы с handle=0 — это Modern UI компоненты.
            Возвращаемые center_x/center_y используйте для ui_click вместо handle.

        Использование для исследования:
            1. Найдите окно: handle = ui_find_window(...)["handle"]
            2. Посмотрите детей: ui_get_children(handle=handle, depth=1)
            3. Скопируйте нужный контрол из результата
            4. Взаимодействуйте через ui_click, ui_send_keys, etc.
        """
        check_admin()

        # Обработка значения по умолчанию
        if depth is None:
            depth = 1

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
                    child_info = control_to_info(child).model_dump()
                    
                    # Для элементов с handle=0 добавляем координаты центра
                    if child_info.get("handle") == 0 and child_info.get("rect"):
                        rect = child_info["rect"]
                        center_x = (rect["left"] + rect["right"]) // 2
                        center_y = (rect["top"] + rect["bottom"]) // 2
                        child_info["rect"]["center_x"] = center_x
                        child_info["rect"]["center_y"] = center_y
                        child_info["note"] = "handle=0 (Modern UI элемент). Для клика используйте center_x/center_y"
                    
                    children.append(child_info)
                    walk(child, current_depth + 1)

            walk(control, 1)
            return {"success": True, "data": children}

        except Exception as e:
            logger.exception("ui_get_children failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_get_focused() -> dict:
        """Получить элемент управления, имеющий фокус ввода.

        Фокус — это состояние, когда элемент получает клавиатурный ввод.
        Обычно элемент с фокусом выделен синей рамкой или подсветкой.

        Returns:
            {"success": true, "data": {control_info}}
            Информация о контроле, который сейчас ждёт ввода

        Примеры использования:
            - После клика по текстовому полю, ui_get_focused вернёт это поле
            - Помогает динамически обнаружить, какой элемент активен

        Совет:
            Используйте для проверки, что ui_click поставил фокус на нужный элемент.
        """
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
        """Получить переднее (активное, видимое пользователю) окно.

        Foreground — это окно, которое находится поверх всех остальных
        и готово к взаимодействию. Обычно это окно, на которое пользователь
        в настоящий момент смотрит.

        Returns:
            {"success": true, "data": {window_info}}
            Информация об активном в данный момент окне

        Примеры использования:
            - Проверить, какое приложение пользователь открыл
            - Убедиться, что требуемое окно активно перед взаимодействием

        Совет:
            Используйте перед ui_click для проверки, что окно активно.
        """
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
        """Получить контрол под указанной точкой на экране.

        Очень полезен для определения, что находится под курсором мыши.
        Помогает найти элемент, если известны его экранные координаты.

        Args:
            x: X-координата на экране (в пиксельях от левого края)
            y: Y-координата на экране (в пиксельях от верхнего края)

        Returns:
            {"success": true, "data": {control_info}}
            Информация о самом верхнем контроле в указанной точке

        Примеры использования:
            - ui_control_from_point(x=512, y=384)  — что под центром экрана?
            - Определить кнопку по её визуальной позиции на скриншоте

        Совет:
            Полезно использовать после ui_screenshot:
            1. Сделаете скрин: ui_screenshot(...)
            2. Посмотрите координаты нужного элемента на скрине
            3. Передайте x, y в ui_control_from_point для получения handle
            4. Используйте handle для взаимодействия
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
