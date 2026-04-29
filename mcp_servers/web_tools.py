"""
mcp_servers/web_tools.py — Web search and URL fetch tools.

Wraps the web_search mod. Agent commands intercepted:
    search_web -query "search terms"
    search_web -query "search terms" -sources 5
    search_web -url "https://example.com"
"""

from __future__ import annotations


def register_tools(mcp) -> None:

    @mcp.tool
    def search_web(args: str = "") -> str:
        """Search the internet and return relevant text excerpts.

        Args syntax:
          -query "terms"              Search the web (uses WEB_SEARCH_SOURCES from config)
          -query "terms" -sources N   Search with N sources (1–10)
          -url "https://..."          Fetch and parse a specific URL
          -url "https://..." -about "topic"  Fetch URL with focused extraction
        """
        from mods.web_search.web_search import handle
        import shlex
        try:
            parsed = shlex.split(args)
        except ValueError:
            parsed = args.split() if args else []
        return handle(parsed, f"search_web {args}")
