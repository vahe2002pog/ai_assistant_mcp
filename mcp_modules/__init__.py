"""
MCP модули для управления ПК.
"""

from .mcp_core import mcp, get_system_path
from . import tools_apps
from . import tools_files

__all__ = ['mcp', 'get_system_path', 'tools_apps', 'tools_files']
