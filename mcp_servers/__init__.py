"""
mcp_servers/ — Built-in MCP tool server.

All built-in tools run in-process as a single FastMCP server.
External servers are configured in mcp_config.json.

Usage:
    from mcp_servers import get_builtin_server
    server = get_builtin_server()   # FastMCP instance, lazy-initialised
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastmcp import FastMCP

_server = None


def get_builtin_server():
    """Return the singleton built-in FastMCP server (lazy-init)."""
    global _server
    if _server is None:
        from fastmcp import FastMCP
        _server = FastMCP("agent-builtin")
        _register_all(_server)
    return _server


def _register_all(mcp) -> None:
    from mcp_servers.shell_tools import register_tools as reg_shell
    from mcp_servers.memory_tools import register_tools as reg_memory
    from mcp_servers.web_tools import register_tools as reg_web
    from mcp_servers.ui_tools import register_tools as reg_ui
    from mcp_servers.passwd_tools import register_tools as reg_passwd
    from mcp_servers.vault_tools import register_tools as reg_vault
    from mcp_servers.schedule_tools import register_tools as reg_schedule

    reg_shell(mcp)
    reg_memory(mcp)
    reg_web(mcp)
    reg_ui(mcp)
    reg_passwd(mcp)
    reg_vault(mcp)
    reg_schedule(mcp)
