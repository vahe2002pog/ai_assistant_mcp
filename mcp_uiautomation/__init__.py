"""Локальная копия инструментов UIAutomation для интеграции.

Этот пакет представляет собой локальное зеркало MCP-инструментов
`uiautomation`, адаптированное для импорта в рабочую область под
именем `mcp_uiautomation`.
"""


__version__ = "0.1.0"

__all__ = ["__version__"]

# Отключаем создание @AutomationLog.txt библиотекой uiautomation
try:
    import uiautomation
    uiautomation.Logger.SetLogFile("")
except Exception:
    pass
