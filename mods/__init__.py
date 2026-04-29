"""
mods/ — Handler logic for built-in tools.

Each subdirectory contains the implementation for one tool.
Tools are exposed via the MCP server layer (mcp_servers/) which wraps
these handlers and dispatches them through MCPRouter.

Layout
──────
    mods/
    ├── _shared.py           shared arg-parsing utilities
    ├── memory/
    │   └── memory.py        memory query/read/write handler
    ├── web_search/
    │   ├── web_search.py    search/fetch handler
    │   └── web_search_tool.py   DuckDuckGo + scraping internals
    ├── debug_ui/
    │   └── debug_ui.py      headless GUI automation handler
    └── passwd/
        ├── passwd.py        credential manager handler
        └── cache.py         in-memory credential store (framework utility)
"""
