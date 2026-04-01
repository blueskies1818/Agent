"""
mods/passwd/cache.py — In-memory credential store.

Lives only in RAM for the lifetime of the process.
Never written to disk, never logged, never embedded.

Two core operations used by the rest of the framework:
  interpolate(text) — replace <<NAME>> with stored value before execution
  scrub(text)       — replace stored values with <<NAME>> in any outgoing text
"""

from __future__ import annotations

import re

_store: dict[str, str] = {}

# Matches <<CREDENTIAL_NAME>> — names must be uppercase word chars
_PLACEHOLDER_RE = re.compile(r"<<([A-Z0-9_]+)>>")


# ── Store operations ──────────────────────────────────────────────────────────

def set(name: str, value: str) -> None:
    """Store a credential. Name is uppercased for consistency."""
    _store[name.upper()] = value


def get(name: str) -> str | None:
    return _store.get(name.upper())


def list_names() -> list[str]:
    """Return stored credential names only — never values."""
    return sorted(_store.keys())


def clear(name: str) -> bool:
    """Remove one entry. Returns True if it existed."""
    return _store.pop(name.upper(), None) is not None


def clear_all() -> int:
    """Wipe everything. Returns number of entries cleared."""
    count = len(_store)
    _store.clear()
    return count


def load_file(path: str) -> tuple[int, list[str]]:
    """
    Load credentials from a key=value file.

    Returns (count_loaded, list_of_names).
    Lines starting with # are ignored. Blank lines are ignored.
    """
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f".passwd file not found: {path}")

    loaded: list[str] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        name, _, value = line.partition("=")
        name = name.strip().upper()
        value = value.strip()
        if name and value:
            _store[name] = value
            loaded.append(name)

    return len(loaded), loaded


# ── Framework utilities ───────────────────────────────────────────────────────

def interpolate(text: str) -> str:
    """
    Replace <<NAME>> placeholders with stored credential values.

    Called by the framework before any command is executed.
    Unknown placeholders are left as-is so the agent sees an error
    rather than silently passing an empty string.
    """
    if "<<" not in text:
        return text

    def _replace(m: re.Match) -> str:
        name = m.group(1)
        value = _store.get(name)
        if value is None:
            return m.group(0)   # leave unknown placeholder intact
        return value

    return _PLACEHOLDER_RE.sub(_replace, text)


def scrub(text: str) -> str:
    """
    Replace any stored credential values in text with their <<NAME>> placeholder.

    Called by the framework on all outgoing text before it reaches the LLM,
    context window, embeddings, or log files.
    """
    if not _store:
        return text
    for name, value in _store.items():
        if value and value in text:
            text = text.replace(value, f"<<{name}>>")
    return text
