"""
mcp_servers/passwd_tools.py — Session-scoped credential manager tool.

Credentials are stored in Python process RAM via mods/passwd/cache.py.
They are NEVER written to disk, logs, or embeddings.
Use <<NAME>> placeholders in any command — the framework substitutes before
execution and scrubs values from all output.

Agent command intercepted:
    passwd -set GITHUB_TOKEN ghp_xxxx
    passwd -load
    passwd -list
    passwd -clear GITHUB_TOKEN
    passwd -clear-all
"""

from __future__ import annotations


def register_tools(mcp) -> None:

    @mcp.tool
    def passwd(args: str = "") -> str:
        """Session-scoped credential cache. Credentials stored in RAM only — never logged.

        Args syntax:
          -set NAME value      Store a credential (use <<NAME>> in commands)
          -load [path]         Load credentials from .passwd file
          -list                Show stored names (never values)
          -clear NAME          Remove one credential
          -clear-all           Wipe the entire cache
        """
        from mods.passwd.passwd import handle
        import shlex
        try:
            parsed = shlex.split(args)
        except ValueError:
            parsed = args.split() if args else []
        return handle(parsed, f"passwd {args}")
