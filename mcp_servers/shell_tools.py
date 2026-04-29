"""
mcp_servers/shell_tools.py — Shell / sandbox tools.

Agent commands intercepted:
    run_shell -cmd "ls -la"          (or any raw shell command as first arg)
    read_file -path /workspace/foo.py
    write_file -path /tmp/out.txt -content "hello"
"""

from __future__ import annotations


def register_tools(mcp) -> None:

    @mcp.tool
    def run_shell(args: str = "") -> str:
        """Execute a shell command in the sandbox. Pass the full command via args."""
        from engine.sandbox import run_command
        import shlex
        try:
            tokens = shlex.split(args)
        except ValueError:
            tokens = args.split()
        # Strip -cmd flag if present
        if tokens and tokens[0].lower().lstrip("-") == "cmd":
            tokens = tokens[1:]
        cmd = " ".join(tokens) if tokens else args
        if not cmd:
            return "[ERROR] run_shell requires a command."
        return run_command(cmd)

    @mcp.tool
    def read_file(path: str) -> str:
        """Read a file from the sandbox and return its contents."""
        from engine.sandbox import run_command
        if not path:
            return "[ERROR] read_file requires a path."
        return run_command(f"cat {path}")

    @mcp.tool
    def write_file(path: str, content: str) -> str:
        """Write content to a file in the sandbox."""
        from engine.sandbox import run_command
        import shlex
        if not path:
            return "[ERROR] write_file requires a path."
        safe = content.replace("'", "'\\''")
        run_command(f"printf '%s' '{safe}' > {shlex.quote(path)}")
        return f"Written to {path}"
