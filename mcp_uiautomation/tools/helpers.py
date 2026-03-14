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
                if filter and filter.lower() not in proc.Name.lower():
                    continue
                result.append({
                    "name": proc.Name,
                    "pid": proc.Id,
                    "exePath": proc.ExecutablePath or "",
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
