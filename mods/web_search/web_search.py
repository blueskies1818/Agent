"""
mods/web_search/web_search.py — Web search command.

Intercepted shell syntax:
    search_web -query "what is PyQt6"
    search_web -query "python asyncio tutorial" -sources 5
    search_web -url "https://docs.python.org/3/library/asyncio.html"

Wraps web_search_tool.py (bundled in this package) to search the internet,
fetch pages, extract relevant chunks, and return focused context.
"""

from __future__ import annotations

from mods._shared import extract_quoted as _extract_quoted


def handle(args: list[str], raw: str) -> str:
    """Dispatch to web search or URL fetch based on flags."""
    if not args:
        return _usage()

    flag = args[0].lower().lstrip("-")

    if flag == "query":
        query = _extract_quoted(args[1:], raw, "-query")
        if not query:
            return "[ERROR] search_web -query requires a search string.\n" + _usage()
        num_sources = _extract_int(args, raw, "-sources", default=None)
        return _search(query, num_sources)

    elif flag == "url":
        url = _extract_quoted(args[1:], raw, "-url")
        if not url:
            return "[ERROR] search_web -url requires a URL.\n" + _usage()
        query = _extract_quoted(args[1:], raw, "-about") or ""
        return _fetch_url(url, query)

    else:
        # Treat the entire args as a query (forgiving syntax)
        query = " ".join(args).strip("\"'")
        if query:
            return _search(query)
        return _usage()


def _extract_int(args: list[str], raw: str, flag: str, default: int | None = None) -> int | None:
    """Extract an integer value after a flag."""
    val = _extract_quoted(args, raw, flag)
    if val:
        try:
            return max(1, min(10, int(val)))
        except ValueError:
            pass
    return default


# ── Operations ────────────────────────────────────────────────────────────────

def _search(query: str, num_sources: int | None = None) -> str:
    """Run a web search and return relevant excerpts."""
    try:
        # Import from the same package
        from mods.web_search.web_search_tool import web_search
    except ImportError:
        return (
            "[ERROR] web_search_tool.py not found in mods/web_search/.\n"
            "Make sure it exists and install deps:\n"
            "  pip install requests beautifulsoup4 duckduckgo-search"
        )

    if num_sources is None:
        try:
            from config import WEB_SEARCH_SOURCES
            num_sources = WEB_SEARCH_SOURCES
        except ImportError:
            num_sources = 3

    try:
        from config import WEB_SEARCH_SEMANTIC
        semantic = WEB_SEARCH_SEMANTIC
    except ImportError:
        semantic = False

    try:
        return web_search(query, num_sources=num_sources, semantic=semantic)
    except Exception as e:
        return f"[ERROR] Web search failed: {e}"


def _fetch_url(url: str, query: str = "") -> str:
    """Fetch a specific URL, parse it, and return relevant text."""
    try:
        from mods.web_search.web_search_tool import scrape_url
    except ImportError:
        return "[ERROR] web_search_tool.py not found in mods/web_search/."

    try:
        result = scrape_url(url, query or url)
        if result:
            return f"## Content from: {url}\n\n{result}"
        return f"[ERROR] Could not extract content from {url}"
    except Exception as e:
        return f"[ERROR] Failed to fetch {url}: {e}"


# ── Usage ─────────────────────────────────────────────────────────────────────

def _usage() -> str:
    return """Usage:
  search_web -query "search terms"               Search the web (3 sources)
  search_web -query "search terms" -sources 5    Search with more sources
  search_web -url "https://example.com"          Fetch and parse a specific URL"""