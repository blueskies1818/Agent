"""
mods/ — Dynamically loaded command modules.

Each subdirectory in mods/ is a self-contained mod package.  The router
scans every subfolder for a .py file that defines NAME + handle(), and
registers it as an interceptable shell command.

Handler return types
────────────────────
Mod handlers can return either:
  - str           → text-only result (backward compatible)
  - ModResult     → text + optional image attachments

The router normalizes both to ModResult before returning to the caller.

Layout
──────
    mods/
    ├── __init__.py
    ├── memory/
    │   └── memory.py
    ├── web_search/
    │   ├── web_search.py
    │   └── web_search_tool.py
    └── debug_ui/
        └── debug_ui.py
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path
from typing import Callable

from engine.mod_api import ModResult


class ModRouter:
    """
    Discovers and dispatches to mod command handlers.

    Scans each subdirectory of mods/ for .py files that define:
        NAME: str            — the command prefix (e.g. "memory")
        DESCRIPTION: str     — one-line description
        handle(args, raw) -> str | ModResult
    """

    def __init__(self, mods_dir: str | Path | None = None) -> None:
        if mods_dir is None:
            mods_dir = Path(__file__).parent
        else:
            mods_dir = Path(mods_dir)

        self._handlers: dict[str, Callable] = {}
        self._descriptions: dict[str, str] = {}
        self._load_mods(mods_dir)

    # ── Public API ─────────────────────────────────────────────────────────

    def try_handle(self, command: str) -> tuple[bool, ModResult]:
        """
        Check if `command` starts with a registered mod name.

        Returns:
            (True,  ModResult)          if a mod handled the command
            (False, ModResult(text="")) if no mod matched
        """
        command = command.strip()
        if not command:
            return False, ModResult(text="")

        first_token = command.split()[0].lower()

        handler = self._handlers.get(first_token)
        if handler is None:
            return False, ModResult(text="")

        parts = command.split()
        args = parts[1:] if len(parts) > 1 else []

        try:
            raw_result = handler(args, command)
            return True, _normalize(raw_result)
        except Exception as e:
            return True, ModResult(text=f"[ERROR] mod '{first_token}' failed: {e}")

    @property
    def registered(self) -> dict[str, str]:
        """Return {name: description} for all loaded mods."""
        return dict(self._descriptions)

    def mod_index(self) -> str:
        """Build a compact index of available mods for the system prompt."""
        if not self._descriptions:
            return "No mods available."
        lines = []
        for name, desc in sorted(self._descriptions.items()):
            lines.append(f"  - {name}: {desc}")
        return "\n".join(lines)

    # ── Loader ─────────────────────────────────────────────────────────────

    def _load_mods(self, mods_dir: Path) -> None:
        if not mods_dir.is_dir():
            return

        for subdir in sorted(mods_dir.iterdir()):
            if not subdir.is_dir():
                continue
            if subdir.name.startswith("_"):
                continue

            for py_file in sorted(subdir.glob("*.py")):
                if py_file.name.startswith("_"):
                    continue

                module_name = f"mods.{subdir.name}.{py_file.stem}"
                try:
                    if module_name in sys.modules:
                        mod = importlib.reload(sys.modules[module_name])
                    else:
                        mod = importlib.import_module(module_name)

                    name = getattr(mod, "NAME", None)
                    handler = getattr(mod, "handle", None)
                    desc = getattr(mod, "DESCRIPTION", "(no description)")

                    if name and callable(handler):
                        self._handlers[name.lower()] = handler
                        self._descriptions[name.lower()] = desc
                    # Files without NAME + handle() are internal helpers — skip silently

                except ImportError:
                    # Missing optional dependency — skip silently.
                    # This is expected for helper files with deps not in the venv
                    # (e.g. viewer.py needing tkinter).
                    pass
                except Exception as e:
                    # Only warn for unexpected errors
                    print(f"[warn] Failed to load mod {subdir.name}/{py_file.name}: {e}")


def _normalize(result) -> ModResult:
    """Normalize a handler return value to ModResult."""
    if isinstance(result, ModResult):
        return result
    if isinstance(result, str):
        return ModResult(text=result)
    # Unexpected type — stringify
    return ModResult(text=str(result))