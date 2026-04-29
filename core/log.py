"""
core/log.py — Unified logging singleton.

Import and call anywhere with no setup:

    from core.log import log

    log.info("sandbox ready", source="sandbox")
    log.error("mod failed to load", source="mod_router")
    log.fatal("provider not found — cannot continue")   # logs then raises SystemExit

Output format:
    [14:32:01] [INFO]  [sandbox] Container started
    [14:32:05] [ERROR] [mod_router] Failed to load mods/debug_ui
    [14:32:09] [FATAL] Provider not found — cannot continue

No third-party dependencies.
"""

import sys
from datetime import datetime


class _Logger:
    _RESET  = "\033[0m"
    _DIM    = "\033[2m"
    _YELLOW = "\033[33m"
    _RED    = "\033[31m"
    _BOLD   = "\033[1m"

    def info(self, msg: str, source: str = "") -> None:
        """Normal operational message — stdout, dim white."""
        ts  = datetime.now().strftime("%H:%M:%S")
        src = f"[{source}] " if source else ""
        print(
            f"{self._DIM}[{ts}] [INFO]  {src}{msg}{self._RESET}",
            flush=True,
        )

    def error(self, msg: str, source: str = "") -> None:
        """Non-fatal error — stderr, yellow. Logs and continues."""
        ts  = datetime.now().strftime("%H:%M:%S")
        src = f"[{source}] " if source else ""
        print(
            f"{self._YELLOW}[{ts}] [ERROR] {src}{msg}{self._RESET}",
            file=sys.stderr,
            flush=True,
        )

    def fatal(self, msg: str, source: str = "") -> None:
        """Fatal error — stderr, bold red. Logs then raises SystemExit(1)."""
        ts  = datetime.now().strftime("%H:%M:%S")
        src = f"[{source}] " if source else ""
        print(
            f"{self._BOLD}{self._RED}[{ts}] [FATAL] {src}{msg}{self._RESET}",
            file=sys.stderr,
            flush=True,
        )
        raise SystemExit(1)


log = _Logger()
