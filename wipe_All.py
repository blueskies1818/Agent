#!/usr/bin/env python3
"""
wipe_All.py — Full wipe of agent memory, logs, plans, and vectors.

Clears:
  • workspace/.agent/plans/  — all plan files, index.json reset to {}
  • memory/logs/             — all session transcripts
  • memory/chroma/           — ChromaDB vector store (rebuilt automatically on next run)
  • memory/agent.db          — all SQLite tables (long_term, conversation, sessions,
                               task_blobs, blob_index, node_messages, tasks,
                               queue_tasks, skill_log)
  • scheduled/               — all scheduled task JSON files  [optional, --scheduled]
  • workspace/vault/         — bucketed knowledge vault       [optional, --vault]
  • workspace/               — entire sandbox workspace        [optional, --workspace]

Usage:
    python wipe_All.py              # wipe memory, logs, plans, vectors
    python wipe_All.py --scheduled  # also wipe scheduled tasks
    python wipe_All.py --vault      # also wipe vault knowledge base
    python wipe_All.py --workspace  # also wipe entire workspace
    python wipe_All.py --all        # everything above
    python wipe_All.py --yes        # skip confirmation prompt
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).parent

PLANS_DIR     = ROOT / "workspace" / ".agent" / "plans"
LOGS_DIR      = ROOT / "memory" / "logs"
CHROMA_DIR    = ROOT / "memory" / "chroma"
DB_PATH       = ROOT / "memory" / "agent.db"
SCHEDULED_DIR = ROOT / "scheduled"
VAULT_DIR     = ROOT / "workspace" / "vault"
WORKSPACE_DIR = ROOT / "workspace"

_SQLITE_TABLES = [
    "long_term",
    "conversation",
    "sessions",
    "task_blobs",
    "blob_index",
    "node_messages",
    "tasks",
    "queue_tasks",
    "skill_log",
]

_RESET = "\033[0m"
_RED   = "\033[31m"
_GREEN = "\033[32m"
_CYAN  = "\033[36m"
_DIM   = "\033[2m"
_BOLD  = "\033[1m"


def _ok(msg: str) -> None:
    print(f"  {_GREEN}✓{_RESET}  {msg}")


def _info(msg: str) -> None:
    print(f"  {_DIM}{msg}{_RESET}")


def _err(msg: str) -> None:
    print(f"  {_RED}✗  {msg}{_RESET}", file=sys.stderr)


def _wipe_plans() -> None:
    removed = 0
    for f in PLANS_DIR.glob("*.md"):
        f.unlink()
        removed += 1
    (PLANS_DIR / "index.json").write_text("{}", encoding="utf-8")
    _ok(f"plans — {removed} file{'s' if removed != 1 else ''} removed, index.json reset")


def _wipe_logs() -> None:
    removed = 0
    for f in LOGS_DIR.glob("*"):
        if f.is_file():
            f.unlink()
            removed += 1
    _ok(f"logs — {removed} session transcript{'s' if removed != 1 else ''} removed")


def _wipe_chroma() -> None:
    if CHROMA_DIR.exists():
        shutil.rmtree(CHROMA_DIR)
        _ok("chroma — vector store wiped (will rebuild on next run)")
    else:
        _info("chroma — already empty")


def _wipe_db() -> None:
    if not DB_PATH.exists():
        _info("agent.db — not found, skipping")
        return
    conn = sqlite3.connect(DB_PATH)
    cleared = []
    for table in _SQLITE_TABLES:
        try:
            conn.execute(f"DELETE FROM {table}")
            cleared.append(table)
        except sqlite3.OperationalError:
            pass
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    _ok(f"agent.db — cleared: {', '.join(cleared)}")


def _wipe_scheduled() -> None:
    removed = 0
    for f in SCHEDULED_DIR.glob("*.json"):
        f.unlink()
        removed += 1
    _ok(f"scheduled — {removed} task file{'s' if removed != 1 else ''} removed")


_VAULT_PROTECTED = frozenset({"internals", ".obsidian"})


def _wipe_vault() -> None:
    if not VAULT_DIR.exists():
        _info("vault — not found, skipping")
        return

    removed = 0
    index_path = VAULT_DIR / "index.json"

    for child in VAULT_DIR.iterdir():
        if child.name in _VAULT_PROTECTED or child.name == "index.json":
            continue
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1

    # Trim index.json — keep only built-in bucket entries
    if index_path.exists():
        try:
            data = json.loads(index_path.read_text(encoding="utf-8"))
            buckets = data.get("buckets", {})
            kept = {k: v for k, v in buckets.items()
                    if str(v.get("path", "")).startswith("internals")}
            data["buckets"] = kept
            index_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception:
            pass

    _ok(f"vault — {removed} user bucket{'s' if removed != 1 else ''} removed, internals preserved")


def _wipe_workspace() -> None:
    if not WORKSPACE_DIR.exists():
        _info("workspace — not found, skipping")
        return
    removed = 0
    for child in WORKSPACE_DIR.iterdir():
        if child.is_dir():
            shutil.rmtree(child)
        else:
            child.unlink()
        removed += 1
    _ok(f"workspace — {removed} item{'s' if removed != 1 else ''} removed")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Full wipe of agent memory, logs, plans, and vectors.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage:")[1],
    )
    parser.add_argument("--scheduled",  action="store_true", help="Also wipe scheduled/ tasks")
    parser.add_argument("--vault",      action="store_true", help="Also wipe workspace/vault/")
    parser.add_argument("--workspace",  action="store_true", help="Also wipe entire workspace/")
    parser.add_argument("--all",        action="store_true", help="Wipe everything")
    parser.add_argument("--yes", "-y",  action="store_true", help="Skip confirmation prompt")
    args = parser.parse_args()

    if args.all:
        args.scheduled = True
        args.workspace = True  # workspace includes vault

    targets = ["workspace/.agent/plans", "memory/logs", "memory/chroma", "agent.db"]
    if args.scheduled:
        targets.append("scheduled/")
    if args.workspace:
        targets.append("workspace/  (entire)")
    elif args.vault:
        targets.append("workspace/vault/")

    print(f"\n{_BOLD}wipe_All{_RESET} — the following will be permanently deleted:\n")
    for t in targets:
        print(f"  {_RED}•{_RESET}  {t}")
    print()

    if not args.yes:
        try:
            answer = input("  Proceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  Aborted.")
            sys.exit(0)
        if answer not in ("y", "yes"):
            print("  Aborted.")
            sys.exit(0)

    print()

    _wipe_plans()
    _wipe_logs()
    _wipe_chroma()
    _wipe_db()

    if args.scheduled:
        _wipe_scheduled()

    if args.workspace:
        _wipe_workspace()
    elif args.vault:
        _wipe_vault()

    print(f"\n  {_BOLD}Done.{_RESET}\n")


if __name__ == "__main__":
    main()
