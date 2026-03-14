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
        button: Optional[str] = None,
        double: Optional[bool] = None,
    ) -> dict:
        """Клик по элементу или по экранным координатам. Основной инструмент для взаимодействия.

        Поддерживает два режима работы:
        1. Клик по элементу (через handle) - клик по его центру
        2. Клик по абсолютным координатам - прямой клик в точку экрана

        Args:
            handle: Дескриптор контрола для клика по его центру
                    Может быть из ui_find_control или ui_find_window
                    ВАЖНО: Если handle=0 (Modern UI), используйте координаты x/y вместо этого
            x: X-координата для клика
                    При наличии handle: смещение от центра контрола
                    При отсутствии handle: абсолютная координата на экране (в пикселях)
                    СОВЕТ: Для Modern UI используйте center_x из ui_get_children
            y: Y-координата для клика
                    При наличии handle: смещение от центра контрола
                    При отсутствии handle: абсолютная координата на экране (в пикселях)
                    СОВЕТ: Для Modern UI используйте center_y из ui_get_children
            button: Кнопка мыши для клика (по умолчанию: \"left\")
                    Варианты: \"left\" (левая), \"right\" (правая), \"middle\" (средняя)
                    Пример: button=\"right\" для контекстного меню
            double: Двойной клик? (по умолчанию: false)
                    true - двойной клик (открыть файл, развернуть окно)
                    false - одиночный клик

        Returns:
            {\"success\": true, \"data\": {\"action\": \"click\", ...}}

        Примеры использования:
            - ui_click(handle=button_handle) 
              Клик по кнопке по её центру
            - ui_click(x=512, y=384)
              Клик по абсолютным координатам (1024x768 экран, центр)
            - ui_click(handle=document, button=\"right\")
              Правый клик для контекстного меню
            - ui_click(handle=file_item, double=True)
              Двойной клик для открытия файла/папки
            - ui_click(x=center_x, y=center_y) где центр из Modern UI
              Клик по элементу Office Ribbon (которые имеют handle=0)

        ⚠️ ВАЖНО для Modern UI (Office Ribbon, Fluent UI):
            Эти элементы имеют handle=0 и не поддерживают стандартное взаимодействие.
            Решение: используйте координаты центра {\"center_x\": X, \"center_y\": Y}
            которые возвращает ui_get_children.
        """
        check_admin()

        # Обработка значений по умолчанию
        if button is None:
            button = "left"
        if double is None:
            double = False

        try:
            # Click at absolute coordinates (handle=0 или явно заданы x/y)
            if (handle is None or handle == 0) and x is not None and y is not None:
                if button == "right":
                    auto.RightClick(x, y)
                elif button == "middle":
                    auto.MiddleClick(x, y)
                elif double:
                    auto.DoubleClick(x, y)
                else:
                    auto.Click(x, y)
                return {"success": True, "data": {"action": "click", "x": x, "y": y, "method": "coordinates"}}

            # Click on control (only if handle is not 0)
            if handle == 0:
                return format_error(
                    "INVALID_HANDLE",
                    "Контрол имеет handle=0 (Modern UI элемент). Передайте координаты x/y вместо handle.",
                    ["Используйте rect координаты из ui_get_children: центр = (left+right)/2, (top+bottom)/2"]
                )

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

            return {"success": True, "data": {"action": "click", "handle": handle, "method": "control"}}

        except Exception as e:
            logger.exception("ui_click failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_send_keys(
        handle: int,
        text: str,
        interval: Optional[float] = None,
    ) -> dict:
        """Отправить клавиатурный ввод текста и команд. Полностью поддерживает горячие клавиши.

        Позволяет отправить текст, специальные клавиши и комбинации в фокусированный элемент.
        Очень полезен для ввода текста в текстовые поля без использования буфера обмена.

        Args:
            handle: Дескриптор контрола (обычно текстовое поле, редактор)
                    Сначала убедитесь, что контрол в фокусе через ui_click
            text: Текст для отправки или комбинации клавиш
                    Обычный текст: "Hello World"
                    Специальные клавиши: {Enter}, {Backspace}, {Delete}, {Escape}
                    Функциональные: {Home}, {End}, {PageUp}, {PageDown}
                    Комодификаторы: {Ctrl}, {Shift}, {Alt}, {LWin}
                    Комбинации: {Ctrl}a (выделить всё), {Ctrl}c (копировать), {Ctrl}v (вставить)
            interval: Интервал между нажатиями символов в секундах (по умолчанию: 0.05)
                    Используйте больший интервал если приложение требует паузы

        Returns:
            {"success": true, "data": {"action": "send_keys", "text": text}}

        Примеры: ui_send_keys(handle=field, text="{Ctrl}a{Delete}")
        """
        check_admin()

        # Обработка значения по умолчанию
        if interval is None:
            interval = 0.05

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
        """Установить значение элемента напрямую через ValuePattern (директное взаимодействие).

        Более надёжный метод для установки значения, чем ui_send_keys. Работает через
        интерфейс AccessibleValue, поэтому не требует фокуса и имитации нажатий.
        Идеален для текстовых полей, слайдеров, спиннеров и элементов ввода.

        Args:
            handle: Дескриптор контрола (обычно текстовое поле, число, дата)
                    Должен поддерживать ValuePattern
            value: Новое значение для установки

        Returns:
            {"success": true, "data": {"action": "set_value", "value": value}}

        Совет:
            Сначала попробуйте ui_set_value (быстрее), если ошибка про Pattern - 
            используйте ui_send_keys.
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
    def ui_close_window(handle: int) -> dict:
        """Закрыть окно или приложение без запроса подтверждения.

        Закрывает окно, используя WindowPattern.Close или Alt+F4 как fallback.
        Полезен для закрытия приложений после завершения работы с ними.

        Args:
            handle: Дескриптор окна (из ui_find_window, ui_list_windows)
                    ВНИМАНИЕ: Закрет окно насильно без сохранения данных!

        Returns:
            {"success": true, "data": {"action": "close_window"}}

        Примеры:
            - ui_close_window(handle=notepad_handle) - закрыть Блокнот
            - ui_close_window(handle=word_window) - закрыть Word

        ⚠️ Будьте осторожны - окно будет закрыто БЕЗ СОХРАНЕНИЯ ДАННЫХ!
        Используйте ui_send_keys(handle, "{Ctrl}s") перед этим если нужно сохранить.
        """
        check_admin()

        try:
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор окна: {handle}",
                )

            # Close the window
            pattern = control.GetWindowPattern()
            if pattern:
                try:
                    pattern.Close()
                except Exception:
                    # Fallback to Alt+F4
                    control.SetFocus()
                    auto.SendKeys("{Alt}{F4}")
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
        """Переместить окно в новую позицию и/или изменить его размер.

        Позволяет контролировать положение и размеры окна приложения. Полезно для
        организации нескольких окон на экране или подготовки к скриншотам.

        Args:
            handle: Дескриптор окна
            x: Новая X-позиция (левый край в пикселях, от левого края экрана)
                По умолчанию: не меняется
            y: Новая Y-позиция (верхний край в пикселях, от верхнего края экрана)
                По умолчанию: не меняется
            width: Новая ширина окна в пикселях
                По умолчанию: не меняется
            height: Новая высота окна в пикселях
                По умолчанию: не меняется

        Returns:
            {"success": true, "data": {...}}

        Примеры:
            - ui_move_window(handle=window, x=100, y=100) - переместить с левый-верх
            - ui_move_window(handle=window, width=800, height=600) - изменить размер
            - ui_move_window(handle=window, x=0, y=0, width=512, height=384) - всё вместе
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
        """Свернуть окно в панель задач (minimize/hide window).

        Скрывает окно приложения из вида, оставляя его запущенным в фоне. Окно
        остаётся доступно через панель задач или Alt+Tab.

        Args:
            handle: Дескриптор окна

        Returns:
            {"success": true, "data": {"action": "minimize_window"}}

        Примеры:
            - ui_minimize_window(handle=notepad) - свернуть Блокнот
            - Часто используется для быстрого доступа к рабочему столу
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
        """Развернуть окно на весь экран (maximize/fullscreen mode).

        Увеличивает окно на максимальный размер доступного экрана. Окно займёт
        всё пространство рабочей области (исключая панель задач).

        Args:
            handle: Дескриптор окна

        Returns:
            {"success": true, "data": {"action": "maximize_window"}}

        Примеры:
            - ui_maximize_window(handle=word_doc) - развернуть Word на весь экран
            - Противоположность ui_minimize_window
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
        """Восстановить окно к нормальному размеру (из свёрнутого или развёрнутого состояния).

        Возвращает окно из minimize или maximize в нормальное состояние - окно
        займёт средний размер и позицию.

        Args:
            handle: Дескриптор окна

        Returns:
            {"success": true, "data": {"action": "restore_window"}}

        Примеры:
            - ui_restore_window(handle=window) - вернуть обычный размер
            - Используется обычно после ui_maximize_window или ui_minimize_window
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
