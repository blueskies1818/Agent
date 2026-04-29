"""
engine/cli_parser.py — Convert CLI-style command strings into dicts for MCP dispatch.

Handles the shell-command syntax the agent writes:
    search_web -query "best pizza" -sources 5
    memory -write "user prefers dark mode"
    debug_ui -click 640 400

Rules:
  - Flags starting with - or -- strip the dash(es) and become dict keys
  - A flag followed by a non-flag value → {key: value}
  - A bare flag with no following value → {key: True}
  - Remaining un-flagged tokens → {"_args": [...]}
  - Uses shlex.split so quoted strings survive: -query "foo bar" → {"query": "foo bar"}
"""

from __future__ import annotations

import shlex


def parse_command(raw: str) -> tuple[str, str, dict]:
    """
    Split a full command string into (tool_name, raw_args_str, args_dict).

    tool_name     — first token, lowercased
    raw_args_str  — everything after the first token (preserves quoting)
    args_dict     — CLI flags parsed into a dict

    Example:
        parse_command('search_web -query "best pizza" -sources 5')
        → ('search_web', '-query "best pizza" -sources 5', {'query': 'best pizza', 'sources': '5'})
    """
    raw = raw.strip()
    if not raw:
        return "", "", {}

    try:
        tokens = shlex.split(raw)
    except ValueError:
        tokens = raw.split()

    if not tokens:
        return "", "", {}

    name = tokens[0].lower()
    rest_tokens = tokens[1:]

    # Reconstruct raw args string (after the command name)
    space_idx = raw.find(" ")
    raw_args = raw[space_idx + 1:].strip() if space_idx != -1 else ""

    return name, raw_args, _parse_tokens(rest_tokens)


def parse_cli_args(tokens: list[str]) -> dict:
    """
    Convert a list of already-split tokens into a dict.

    ["-query", "foo", "-sources", "5"]  → {"query": "foo", "sources": "5"}
    ["-read"]                           → {"read": True}
    ["-register", "file.md", "name"]    → {"register": True, "_args": ["file.md", "name"]}
    """
    return _parse_tokens(tokens)


# ── Internal ──────────────────────────────────────────────────────────────────

def _parse_tokens(tokens: list[str]) -> dict:
    result: dict = {}
    positional: list[str] = []
    i = 0

    while i < len(tokens):
        tok = tokens[i]

        if tok.startswith("--") and len(tok) > 2:
            key = tok[2:]
            if i + 1 < len(tokens) and not _is_flag(tokens[i + 1]):
                result[key] = tokens[i + 1]
                i += 2
            else:
                result[key] = True
                i += 1

        elif tok.startswith("-") and len(tok) > 1 and not _looks_like_number(tok):
            key = tok[1:]
            if i + 1 < len(tokens) and not _is_flag(tokens[i + 1]):
                result[key] = tokens[i + 1]
                i += 2
            else:
                result[key] = True
                i += 1

        else:
            positional.append(tok)
            i += 1

    if positional:
        result["_args"] = positional

    return result


def _is_flag(tok: str) -> bool:
    if not tok.startswith("-"):
        return False
    return not _looks_like_number(tok)


def _looks_like_number(tok: str) -> bool:
    try:
        float(tok)
        return True
    except ValueError:
        return False
