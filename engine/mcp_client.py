"""
engine/mcp_client.py — Async client for a single MCP server.

Wraps the FastMCP Client to provide tool discovery and invocation,
and converts MCP content blocks back to ModResult.

Transports:
  "inprocess"  — FastMCP server instance running in the same process (no subprocess)
  "http"        — HTTP/SSE endpoint, e.g. "http://localhost:8080/mcp"
  "stdio"       — External subprocess via stdin/stdout
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from typing import Any

from fastmcp import Client
from mcp import StdioServerParameters

from engine.media import MediaAttachment
from engine.mod_api import ModResult


@dataclass
class ToolDef:
    """Metadata for a single MCP tool."""
    name: str
    description: str
    input_schema: dict = field(default_factory=dict)


class MCPClient:
    """
    Async client for one MCP server.

    Usage:
        client = MCPClient("inprocess", server=my_fastmcp_instance)
        await client.connect()           # discovers tools
        result = await client.call_tool("search_web", {"args": "-query foo"})
        await client.disconnect()
    """

    def __init__(self, transport: str, **kwargs: Any) -> None:
        """
        Args:
            transport: "inprocess" | "http" | "stdio"
            server:    (inprocess) FastMCP instance
            url:       (http) server URL string
            command:   (stdio) executable path
            args:      (stdio) list of args for the subprocess
            env:       (stdio) optional env dict
        """
        self._transport = transport
        self._kwargs = kwargs
        self._tools: list[ToolDef] = []
        self._target: Any = None

    # ── Public API ─────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Discover tools by opening a short-lived connection."""
        self._target = self._build_target()
        async with Client(self._target) as client:
            raw_tools = await client.list_tools()
            self._tools = [
                ToolDef(
                    name=t.name,
                    description=t.description or "",
                    input_schema=dict(t.inputSchema) if t.inputSchema else {},
                )
                for t in raw_tools
            ]


    async def disconnect(self) -> None:
        """No-op for per-call connections; here for symmetry."""
        pass

    def list_tools(self) -> list[ToolDef]:
        return list(self._tools)

    async def call_tool(self, name: str, args: dict) -> ModResult:
        """Call a tool and return a ModResult."""
        async with Client(self._target) as client:
            raw = await client.call_tool(name, args)
        return _parse_mcp_result(raw)


    # ── Target builder ─────────────────────────────────────────────────────────

    def _build_target(self) -> Any:
        if self._transport == "inprocess":
            return self._kwargs["server"]

        if self._transport == "http":
            return self._kwargs["url"]

        if self._transport == "stdio":
            # FastMCP 3.x accepts a dict matching StdioServerParameters
            # or the raw ServerParameters object from the mcp SDK.
            return StdioServerParameters(
                command=self._kwargs["command"],
                args=self._kwargs.get("args", []),
                env=self._kwargs.get("env"),
            )

        raise ValueError(f"Unknown MCP transport: {self._transport!r}")


# ── Result converter ──────────────────────────────────────────────────────────

def _parse_mcp_result(raw: Any) -> ModResult:
    """
    Convert whatever FastMCP call_tool returns into a ModResult.

    Handles: list[Content], CallToolResult, str, and plain values.
    """
    # Unwrap CallToolResult if needed
    if hasattr(raw, "isError") and raw.isError:
        content_text = _extract_text(getattr(raw, "content", raw))
        return ModResult(text=f"[ERROR] {content_text}")

    if hasattr(raw, "content"):
        raw = raw.content

    # Plain string
    if isinstance(raw, str):
        return ModResult(text=raw)

    # List of content blocks
    if isinstance(raw, list):
        text_parts: list[str] = []
        attachments: list[MediaAttachment] = []

        for item in raw:
            item_type = getattr(item, "type", None) or (
                item.get("type") if isinstance(item, dict) else None
            )

            if item_type == "text":
                text = getattr(item, "text", None) or (
                    item.get("text", "") if isinstance(item, dict) else ""
                )
                text_parts.append(str(text))

            elif item_type == "image":
                data_b64 = getattr(item, "data", None) or (
                    item.get("data") if isinstance(item, dict) else None
                )
                mime = getattr(item, "mimeType", None) or (
                    item.get("mimeType", "image/png") if isinstance(item, dict) else "image/png"
                )
                if data_b64:
                    try:
                        img_bytes = base64.b64decode(data_b64)
                        attachments.append(
                            MediaAttachment(type="image", data=img_bytes, mime_type=mime)
                        )
                    except Exception:
                        pass

        return ModResult(text="\n".join(text_parts), attachments=attachments)

    # Fallback
    return ModResult(text=str(raw))


def _extract_text(obj: Any) -> str:
    if isinstance(obj, str):
        return obj
    if isinstance(obj, list):
        parts = []
        for item in obj:
            t = getattr(item, "text", None) or (
                item.get("text", "") if isinstance(item, dict) else str(item)
            )
            parts.append(str(t))
        return "\n".join(parts)
    return str(obj)
