"""
mods/passwd/passwd.py — Session-scoped credential manager.

Credentials are stored in RAM only and never written to disk, logs,
or memory.  Use <<NAME>> placeholders in any command — the framework
substitutes the value before execution and scrubs it from all output.

Intercepted shell syntax:
    passwd -set GITHUB_TOKEN ghp_xxxx        Store a credential
    passwd -load                             Load from .passwd file
    passwd -list                             Show stored names (never values)
    passwd -clear GITHUB_TOKEN               Remove one entry
    passwd -clear-all                        Wipe entire cache
"""

from __future__ import annotations

from pathlib import Path

def handle(args: list[str], raw: str) -> str:
    if not args:
        return _usage()

    flag = args[0].lower().lstrip("-")

    if flag == "set":
        if len(args) < 3:
            return "[ERROR] passwd -set requires <NAME> <value>\n" + _usage()
        name  = args[1].upper()
        value = " ".join(args[2:])
        return _set(name, value)

    elif flag == "load":
        path = args[1] if len(args) > 1 else None
        return _load(path)

    elif flag == "list":
        return _list()

    elif flag == "clear":
        if len(args) < 2:
            return "[ERROR] passwd -clear requires <NAME>\n" + _usage()
        return _clear(args[1].upper())

    elif flag == "clear-all":
        return _clear_all()

    else:
        return f"[ERROR] Unknown passwd operation: '{flag}'\n" + _usage()


# ── Operations ─────────────────────────────────────────────────────────────────

def _set(name: str, value: str) -> str:
    from mods.passwd.cache import set as cache_set
    cache_set(name, value)
    return f"Credential stored: {name}  (use <<{name}>> in commands)"


def _load(path: str | None) -> str:
    from mods.passwd.cache import load_file
    from config import BASE_DIR

    target = path or str(Path(BASE_DIR) / ".passwd")
    try:
        count, names = load_file(target)
    except FileNotFoundError as e:
        return f"[ERROR] {e}"
    except Exception as e:
        return f"[ERROR] Failed to read .passwd file: {e}"

    if count == 0:
        return f"No credentials loaded from {target} (file empty or all lines invalid)"

    return f"Loaded {count} credential(s): {', '.join(names)}"


def _list() -> str:
    from mods.passwd.cache import list_names
    names = list_names()
    if not names:
        return "(no credentials in cache)"
    return "Stored credentials (names only):\n" + "\n".join(f"  <<{n}>>" for n in names)


def _clear(name: str) -> str:
    from mods.passwd.cache import clear as cache_clear
    if cache_clear(name):
        return f"Credential removed: {name}"
    return f"(no credential named '{name}' in cache)"


def _clear_all() -> str:
    from mods.passwd.cache import clear_all
    count = clear_all()
    return f"Cache cleared ({count} credential(s) removed)"


def _usage() -> str:
    return (
        "Usage:\n"
        "  passwd -set <NAME> <value>   Store a credential in the session cache\n"
        "  passwd -load                 Load credentials from .passwd file\n"
        "  passwd -list                 Show stored credential names (never values)\n"
        "  passwd -clear <NAME>         Remove one credential\n"
        "  passwd -clear-all            Wipe the entire cache\n\n"
        "Use <<NAME>> anywhere in a command — the framework substitutes before execution:\n"
        "  curl -H \"Authorization: Bearer <<GITHUB_TOKEN>>\" https://api.github.com\n"
        "  debug_ui -type <<GMAIL_PASSWORD>>"
    )
