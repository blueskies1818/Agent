"""
mcp_servers/vault_tools.py — Vault navigation tools.

Wraps the vault mod. Agent commands intercepted:
    vault -list
    vault -contents <bucket>
    vault -reindex  <bucket>

Reading vault docs — use shell directly: cat workspace/vault/index.json
Semantic search — use memory mod: memory -vault <bucket|*> "query"
"""

from __future__ import annotations


def register_tools(mcp) -> None:

    @mcp.tool
    def vault(args: str = "") -> str:
        """Navigate and maintain vault knowledge buckets.

        Args syntax:
          -list                  Show all buckets with descriptions and doc counts
          -contents <bucket>     List docs in a bucket with file paths
          -reindex  <bucket>     Re-embed all docs in a bucket from disk

        To read vault content: cat workspace/vault/<path>/<doc>.md
        To search semantically: memory -vault <bucket|*> "query"
        """
        from mods.vault.vault import handle
        import shlex
        try:
            parsed = shlex.split(args)
        except ValueError:
            parsed = args.split() if args else []
        return handle(parsed, f"vault {args}")
