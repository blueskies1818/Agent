"""
engine/mcp_router.py — Drop-in replacement for ModRouter using MCP.

The agent still writes plain shell commands:
    search_web -query "best pizza" -sources 5

MCPRouter intercepts by first token, converts the remaining tokens to an
args dict (via cli_parser), and dispatches via MCP JSON-RPC to the
appropriate server.

Built-in tools run in-process (no subprocess). External servers are loaded
from mcp_config.json and communicate over stdio or HTTP.

Public interface matches ModRouter exactly so nodes.py and loop.py require
minimal changes.
"""

from __future__ import annotations

import asyncio
import json
import threading
from pathlib import Path
from typing import Any

from core.log import log
from engine.cli_parser import parse_command
from engine.mcp_client import MCPClient, ToolDef
from engine.mod_api import ModResult


class MCPRouter:
    """
    Discovers all MCP tools (built-in + external) and dispatches
    shell commands to them, falling back to the sandbox when no tool matches.

    Thread-safe: try_handle() is synchronous and can be called from any thread.
    A private background event loop handles all async MCP operations.
    """

    def __init__(self) -> None:
        self._registry: dict[str, tuple[MCPClient, ToolDef]] = {}
        self._clients: list[MCPClient] = []

        # Background event loop for sync→async bridging
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def _run(self, coro) -> Any:
        """Submit a coroutine to the background loop and block until done."""
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        return future.result(timeout=120)

    def connect_all(self) -> None:
        """Connect all configured servers and build the tool registry."""
        self._run(self._connect_all_async())

    async def _connect_all_async(self) -> None:
        from config import MCP_BUILTIN_ENABLED, MCP_CONFIG_FILE

        # ── Built-in in-process server ────────────────────────────────────────
        if MCP_BUILTIN_ENABLED:
            try:
                from mcp_servers import get_builtin_server
                builtin = get_builtin_server()
                client = MCPClient("inprocess", server=builtin)
                await client.connect()
                self._clients.append(client)
                for tool in client.list_tools():
                    self._registry[tool.name] = (client, tool)
                log.info(
                    f"built-in MCP server ready: {len(client.list_tools())} tools",
                    source="mcp_router",
                )
            except Exception as e:
                log.error(f"built-in MCP server failed to start: {e}", source="mcp_router")

        # ── External servers from mcp_config.json ────────────────────────────
        config_path = Path(MCP_CONFIG_FILE)
        if config_path.exists():
            try:
                cfg = json.loads(config_path.read_text(encoding="utf-8"))
                for entry in cfg.get("servers", []):
                    await self._connect_external(entry)
            except Exception as e:
                log.error(f"mcp_config.json load failed: {e}", source="mcp_router")

    async def _connect_external(self, entry: dict) -> None:
        name     = entry.get("name", "?")
        transport = entry.get("transport", "stdio")
        try:
            if transport == "http":
                client = MCPClient("http", url=entry["url"])
            elif transport == "stdio":
                client = MCPClient(
                    "stdio",
                    command=entry["command"],
                    args=entry.get("args", []),
                    env=entry.get("env"),
                )
            else:
                log.error(f"unknown transport '{transport}' for server '{name}'", source="mcp_router")
                return

            await client.connect()
            self._clients.append(client)
            new_tools = client.list_tools()
            for tool in new_tools:
                self._registry[tool.name] = (client, tool)
            log.info(
                f"external MCP server '{name}' ready: {len(new_tools)} tools",
                source="mcp_router",
            )
        except Exception as e:
            log.error(f"external MCP server '{name}' failed: {e}", source="mcp_router")

    def shutdown(self) -> None:
        """Stop the background event loop."""
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)

    # ── Dispatch ───────────────────────────────────────────────────────────────

    def try_handle(self, command: str) -> tuple[bool, ModResult]:
        """
        Check whether `command` maps to a registered MCP tool.

        Returns:
            (True,  ModResult)           — tool handled the command
            (False, ModResult(text=""))  — no match; caller should sandbox-exec
        """
        command = command.strip()
        if not command:
            return False, ModResult(text="")

        name, raw_args, args_dict = parse_command(command)

        entry = self._registry.get(name)
        if entry is None:
            return False, ModResult(text="")

        client, tool_def = entry

        # Choose how to pass args based on the tool's input schema
        call_args = _build_call_args(tool_def, raw_args, args_dict)

        try:
            result = self._run(client.call_tool(name, call_args))
            return True, result
        except Exception as e:
            return True, ModResult(text=f"[ERROR] MCP tool '{name}' raised: {e}")

    # ── Introspection (mod-router compatible) ──────────────────────────────────

    @property
    def registered(self) -> dict[str, str]:
        """Return {tool_name: description} for all registered tools."""
        return {name: td.description for name, (_, td) in self._registry.items()}

    def mod_index(self) -> str:
        """Compact one-liner-per-tool index for system prompt injection."""
        if not self._registry:
            return "No tools available."
        lines = []
        for name, (_, td) in sorted(self._registry.items()):
            desc = td.description.split("\n")[0].strip()
            lines.append(f"  shell: `{name} ...`  — {desc}")
        return "\n".join(lines)


# ── Arg-building helper ───────────────────────────────────────────────────────

def _build_call_args(tool_def: ToolDef, raw_args: str, args_dict: dict) -> dict:
    """
    Decide how to pass arguments to a tool:

    - If the tool has a single 'args: string' parameter → pass raw_args as {"args": raw_args}.
      This is the pattern used by all built-in tools that wrap existing mod handlers.

    - Otherwise → pass the parsed args_dict directly (for external tools with
      structured schemas).
    """
    schema_props: dict = tool_def.input_schema.get("properties", {})

    if len(schema_props) == 1 and "args" in schema_props:
        return {"args": raw_args}

    return args_dict
