"""
mcp_servers/ui_tools.py — Headless GUI automation tools.

Wraps the debug_ui mod. Requires Docker sandbox mode.
Agent commands intercepted:
    debug_ui -start "python app.py"
    debug_ui -screenshot
    debug_ui -click 640 400
    debug_ui -type "hello"
    debug_ui -key Return
    debug_ui -scroll up
    debug_ui -drag 100 200 300 400
    debug_ui -close
"""

from __future__ import annotations

import base64


def register_tools(mcp) -> None:

    @mcp.tool
    def debug_ui(args: str = "") -> list:
        """Launch and interact with GUI applications via headless virtual display.

        Requires Docker sandbox mode (SANDBOX=docker).

        Args syntax:
          -start "cmd"          Launch app, return screenshot
          -screenshot           Capture current screen
          -click X Y            Left-click at (x, y)
          -double-click X Y     Double-click
          -right-click X Y      Right-click
          -type "text"          Type text at current focus
          -key KEY              Press key (Return, Tab, Escape, ctrl+s, ...)
          -scroll up|down       Scroll
          -drag X1 Y1 X2 Y2     Drag
          -close                Kill app and stop display
        """
        from mods.debug_ui.debug_ui import handle
        import shlex
        try:
            parsed = shlex.split(args)
        except ValueError:
            parsed = args.split() if args else []

        result = handle(parsed, f"debug_ui {args}")

        # Convert ModResult → MCP content list (text + optional images)
        content: list = [{"type": "text", "text": result.text}]
        for att in result.attachments:
            if att.data:
                content.append({
                    "type":     "image",
                    "data":     base64.b64encode(att.data).decode(),
                    "mimeType": att.mime_type or "image/png",
                })
        return content
