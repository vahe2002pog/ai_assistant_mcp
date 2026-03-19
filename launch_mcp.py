"""
Launcher для MCP сервера (stdio transport).
WS bridge запускается отдельно из main.py.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mcp_modules.mcp_server import mcp

if __name__ == "__main__":
    mcp.run(transport="stdio", show_banner=False)
