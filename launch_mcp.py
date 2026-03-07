"""
Launcher для MCP сервера.
Запускает mcp_server как модуль пакета.
"""

import sys
from mcp_modules.mcp_server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio")
