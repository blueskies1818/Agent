"""
mods/_shared.py — Shared argument parsing utilities for all mods.

Keeps common logic in one place so each mod doesn't reimplement it.
"""

from __future__ import annotations

import re


def extract_quoted(args: list[str], raw: str, flag: str) -> str:
    """
    Extract the value after a CLI-style flag from the raw command string.

    Handles all common forms:
        -flag "double quoted"
        -flag 'single quoted'
        -flag unquoted value
        -flag multi word value (stops at next flag)

    Args:
        args: Tokenised argument list (everything after the command name).
        raw:  The original un-tokenised command string (for regex matching).
        flag: The flag to search for, e.g. "-query" or "-url".

    Returns:
        The extracted value, or "" if not found.
    """
    # Double-quoted value
    m = re.search(rf'{re.escape(flag)}\s+"([^"]+)"', raw)
    if m:
        return m.group(1)

    # Single-quoted value
    m = re.search(rf"{re.escape(flag)}\s+'([^']+)'", raw)
    if m:
        return m.group(1)

    # Unquoted: collect tokens after the flag until the next flag
    flag_clean = flag.lstrip("-")
    try:
        for i, a in enumerate(args):
            if a.lower().lstrip("-") == flag_clean:
                parts = []
                for j in range(i + 1, len(args)):
                    if args[j].startswith("-"):
                        break
                    parts.append(args[j])
                if parts:
                    return " ".join(parts).strip("\"'")
    except Exception:
        pass

    # Final fallback: everything after the flag index
    try:
        idx = next(i for i, a in enumerate(args) if a.lower().lstrip("-") == flag_clean)
        remaining = args[idx + 1:]
        if remaining:
            return " ".join(remaining).strip("\"'")
    except (StopIteration, IndexError):
        pass

    return ""
