# -*- coding: utf-8 -*-
"""Вспомогательные инструменты для общих операций (буфер обмена, процессы и т.д.)."""

import logging
from typing import Optional

from mcp.server.fastmcp import FastMCP

import uiautomation as auto

from ..core import format_error, init_com

logger = logging.getLogger(__name__)

# Инициализируем COM при загрузке модуля (требуется для UIAutomation)
init_com()


def register_helper_tools(mcp: FastMCP):
    """Зарегистрировать вспомогательные инструменты на MCP сервере."""

    @mcp.tool()
    def ui_clipboard_get() -> dict:
        """Получить текст из буфера обмена.

        Returns:
            Текст из буфера обмена
        """
        try:
            text = auto.GetClipboardText()
            return {"success": True, "data": {"text": text}}

        except Exception as e:
            logger.exception("ui_clipboard_get failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_clipboard_set(text: str) -> dict:
        """Установить текст в буфер обмена.

        Args:
            text: Текст для установки

        Returns:
            Результат выполнения
        """
        try:
            success = auto.SetClipboardText(text)
            return {"success": success, "data": {"text": text}}

        except Exception as e:
            logger.exception("ui_clipboard_set failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_clipboard_append(text: str) -> dict:
        """Добавить текст к существующему в буфере обмена (конкатенация).

        Читает текущее содержимое буфера обмена и добавляет новый текст к концу.

        Args:
            text: Текст для добавления к существующему

        Returns:
            Результат выполнения (новое содержимое буфера)
        """
        try:
            current_text = auto.GetClipboardText()
            new_text = current_text + text
            success = auto.SetClipboardText(new_text)
            return {"success": success, "data": {"text": new_text, "appended": text}}

        except Exception as e:
            logger.exception("ui_clipboard_append failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_clipboard_paste(handle: int) -> dict:
        """Вставить содержимое буфера обмена в контрол (Ctrl+V).

        Вставляет текст из буфера обмена в фокусированный или указанный контрол.
        Это эквивалент нажатия Ctrl+V.

        Args:
            handle: Дескриптор контрола для вставки
                    Убедитесь что контрол в фокусе перед вызовом

        Returns:
            Результат выполнения с текстом который был вставлен
        """
        try:
            from ..core import get_control_by_handle
            
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                )

            # Получить текст из буфера перед вставкой
            clipboard_text = auto.GetClipboardText()
            
            # Вставить через Ctrl+V
            auto.SendKeys("{Ctrl}v")
            
            return {"success": True, "data": {"action": "paste", "text": clipboard_text}}

        except Exception as e:
            logger.exception("ui_clipboard_paste failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_paste_text(handle: int, text: str) -> dict:
        """Вставить текст в контрол через буфер обмена (БЫСТРО для больших текстов).

        Это быстрее чем ui_send_keys, особенно для больших текстов или содержимого с 
        форматированием. Автоматически копирует текст в буфер и вставляет через Ctrl+V.

        РЕКОМЕНДУЕТСЯ вместо ui_send_keys для:
        - Больших блоков текста (более 100 символов)
        - Текстов с новыми строками и спецсимволами
        - Когда важна скорость выполнения

        Args:
            handle: Дескриптор контрола для вставки
                    Убедитесь что контрол в фокусе перед вызовом, или используйте ui_click
            text: Текст для вставки (любой объем, может содержать многострочный текст)

        Returns:
            Результат выполнения с информацией о вставленном тексте

        Примеры:
            # Вставить большой текст:
            ui_paste_text(handle=editor, text=\"\"\"Line 1
            Line 2
            Line 3\"\"\")

            # Вставить JSON:
            ui_paste_text(handle=code_field, text=json_string)

            # После клика на поле автоматически вставляет:
            ui_click(handle=text_field)
            ui_paste_text(handle=text_field, text=\"Hello World\")
        """
        try:
            from ..core import get_control_by_handle
            
            control = get_control_by_handle(handle)
            if not control:
                return format_error(
                    "CONTROL_NOT_FOUND",
                    f"Некорректный дескриптор контрола: {handle}",
                )

            # Копируем текст в буфер обмена
            auto.SetClipboardText(text)
            
            # Даём небольшую паузу для синхронизации буфера
            import time
            time.sleep(0.05)
            
            # Вставляем через Ctrl+V
            auto.SendKeys("{Ctrl}v")
            
            return {"success": True, "data": {"action": "paste_text", "text": text, "length": len(text)}}

        except Exception as e:
            logger.exception("ui_paste_text failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_list_processes(filter: Optional[str] = None) -> dict:
        """Получить список запущенных процессов.

        Args:
            filter: Фильтр по имени процесса (подстрока)

        Returns:
            Список процессов
        """
        try:
            processes = auto.GetProcesses(detailedInfo=True)
            result = []

            for proc in processes:
                # Получаем имя процесса (может быть Name или name атрибут)
                proc_name = getattr(proc, 'Name', None) or getattr(proc, 'name', None) or "Unknown"
                
                if filter and filter.lower() not in proc_name.lower():
                    continue
                
                result.append({
                    "name": proc_name,
                    "pid": proc.Id if hasattr(proc, 'Id') else proc.id if hasattr(proc, 'id') else 0,
                    "exePath": getattr(proc, 'ExecutablePath', '') or "",
                })

            return {"success": True, "data": {"processes": result, "count": len(result)}}

        except Exception as e:
            logger.exception("ui_list_processes failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_show_desktop() -> dict:
        """Показать рабочий стол (свернуть все окна)."""
        try:
            auto.ShowDesktop()
            return {"success": True, "data": {"action": "show_desktop"}}

        except Exception as e:
            logger.exception("ui_show_desktop failed")
            return format_error("INTERNAL_ERROR", str(e))

    @mcp.tool()
    def ui_get_screen_size() -> dict:
        """Получить размер экрана (ширина и высота)."""
        try:
            width, height = auto.GetScreenSize()
            return {"success": True, "data": {"width": width, "height": height}}

        except Exception as e:
            logger.exception("ui_get_screen_size failed")
            return format_error("INTERNAL_ERROR", str(e))
