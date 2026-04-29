"""
mcp_servers/memory_tools.py — Memory tools.

Wraps the memory mod. Agent commands intercepted:
    memory -query "search terms"
    memory -read
    memory -write "fact to remember"
    memory -prefs
    memory -pref key value
    memory -blobs
    memory -blob name
"""

from __future__ import annotations


def register_tools(mcp) -> None:

    @mcp.tool
    def memory(args: str = "") -> str:
        """Query, read, or write persistent memory and vault knowledge across sessions.

        Args syntax:
          -query "text"                Semantic search across all memory stores + vault
          -vault <bucket> "text"       Semantic search within a specific vault bucket
          -vault * "text"              Semantic search across ALL vault buckets
          -read                        List recent long-term memories
          -write "fact"                Persist a new fact
          -prefs                       List long-term preferences
          -pref key value              Set a preference
          -blobs                       List recent task blobs
          -blob name                   Load full blob by name
        """
        from mods.memory.memory import handle
        import shlex
        try:
            parsed = shlex.split(args)
        except ValueError:
            parsed = args.split() if args else []
        return handle(parsed, f"memory {args}")
