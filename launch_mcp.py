"""
Launcher для MCP сервера (stdio transport).
WS bridge запускается отдельно из main.py.
"""
import sys
import os
import builtins

# MCP stdio transport использует stdout для JSONRPC и пишет туда напрямую
# через transport layer — не через print().
# uiautomation и другие библиотеки при ошибках печатают в stdout через print(),
# что ломает JSONRPC-поток. Перенаправляем ВСЕ print() в stderr раз и навсегда.
_original_print = builtins.print

def _print_to_stderr(*args, **kwargs):
    kwargs.setdefault("file", sys.stderr)
    _original_print(*args, **kwargs)

builtins.print = _print_to_stderr

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp_modules.mcp_server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
