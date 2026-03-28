"""
wipe.py — Selectively wipe agent data.

Targets
───────
  memory    memory/memory.txt  (persistent facts)
  logs      memory/logs/*.log  (session transcripts)
  vectors   memory/chroma/     (ChromaDB vector store)
  workspace workspace/*        (AI sandbox files)
  all       everything above

Usage
─────
  python wipe.py                  # wipe memory + logs + vectors (default)
  python wipe.py all              # wipe everything including workspace
  python wipe.py logs             # wipe only logs
  python wipe.py memory vectors   # wipe memory and vectors only
  python wipe.py all --yes        # no confirmation prompt
"""

import argparse
import glob
import os
import shutil
import sys

ROOT        = os.path.dirname(os.path.abspath(__file__))
MEMORY_FILE = os.path.join(ROOT, "memory", "memory.txt")
LOGS_DIR    = os.path.join(ROOT, "memory", "logs")
CHROMA_DIR  = os.path.join(ROOT, "memory", "chroma")
WORKSPACE   = os.path.join(ROOT, "workspace")

VALID_TARGETS = {"memory", "logs", "vectors", "workspace", "all"}
DEFAULT_TARGETS = {"memory", "logs", "vectors"}


# ── Target handlers ───────────────────────────────────────────────────────────

def _inventory_memory() -> list[tuple[str, str]]:
    """Returns list of (display_label, kind) pairs."""
    if os.path.exists(MEMORY_FILE) and os.path.getsize(MEMORY_FILE) > 0:
        return [(os.path.relpath(MEMORY_FILE), "memory_file")]
    return []

def _inventory_logs() -> list[tuple[str, str]]:
    files = sorted(glob.glob(os.path.join(LOGS_DIR, "*.log")))
    return [(os.path.relpath(f), "log_file") for f in files]

def _inventory_vectors() -> list[tuple[str, str]]:
    if os.path.exists(CHROMA_DIR) and os.listdir(CHROMA_DIR):
        return [(os.path.relpath(CHROMA_DIR) + "/", "chroma_dir")]
    return []

def _inventory_workspace() -> list[tuple[str, str]]:
    if not os.path.exists(WORKSPACE):
        return []
    entries = [
        e for e in os.listdir(WORKSPACE)
        if e != ".gitkeep"
    ]
    return [(os.path.relpath(os.path.join(WORKSPACE, e)), "workspace_entry") for e in sorted(entries)]


def _wipe_memory() -> int:
    if not os.path.exists(MEMORY_FILE):
        return 0
    open(MEMORY_FILE, "w").close()
    print(f"  cleared  memory/memory.txt")
    return 1

def _wipe_logs() -> int:
    files = glob.glob(os.path.join(LOGS_DIR, "*.log"))
    for f in files:
        os.remove(f)
        print(f"  deleted  {os.path.relpath(f)}")
    return len(files)

def _wipe_vectors() -> int:
    if not os.path.exists(CHROMA_DIR):
        return 0
    shutil.rmtree(CHROMA_DIR)
    os.makedirs(CHROMA_DIR)           # recreate empty so ChromaDB doesn't error on next start
    print(f"  wiped    memory/chroma/")
    return 1

def _wipe_workspace() -> int:
    if not os.path.exists(WORKSPACE):
        return 0
    count = 0
    for entry in os.listdir(WORKSPACE):
        if entry == ".gitkeep":
            continue
        path = os.path.join(WORKSPACE, entry)
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        print(f"  deleted  {os.path.relpath(path)}")
        count += 1
    return count


# ── Main ──────────────────────────────────────────────────────────────────────

def wipe(targets: set[str], skip_confirm: bool = False) -> None:
    if "all" in targets:
        targets = {"memory", "logs", "vectors", "workspace"}

    # Build inventory
    inventory: list[tuple[str, str]] = []
    if "memory"    in targets: inventory.extend(_inventory_memory())
    if "logs"      in targets: inventory.extend(_inventory_logs())
    if "vectors"   in targets: inventory.extend(_inventory_vectors())
    if "workspace" in targets: inventory.extend(_inventory_workspace())

    if not inventory:
        print("[wipe] Nothing to delete — already clean.")
        return

    # Show what will be deleted
    print(f"[wipe] Targets: {', '.join(sorted(targets))}\n")
    for label, _ in inventory:
        print(f"  {label}")
    print()

    # Confirm
    if not skip_confirm:
        try:
            answer = input("[wipe] Proceed? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n[wipe] Aborted.")
            sys.exit(0)
        if answer != "y":
            print("[wipe] Aborted.")
            sys.exit(0)

    # Execute
    print()
    total = 0
    if "memory"    in targets: total += _wipe_memory()
    if "logs"      in targets: total += _wipe_logs()
    if "vectors"   in targets: total += _wipe_vectors()
    if "workspace" in targets: total += _wipe_workspace()

    print(f"\n[wipe] Done — {total} item(s) wiped.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Wipe agent data.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "targets:\n"
            "  memory     memory/memory.txt\n"
            "  logs       memory/logs/*.log\n"
            "  vectors    memory/chroma/\n"
            "  workspace  workspace/*\n"
            "  all        everything above\n\n"
            "default (no targets): memory + logs + vectors"
        ),
    )
    parser.add_argument(
        "targets",
        nargs="*",
        metavar="TARGET",
        help="What to wipe (default: memory logs vectors)",
    )
    parser.add_argument(
        "--yes", "-y",
        action="store_true",
        help="Skip confirmation prompt",
    )
    args = parser.parse_args()

    # Validate targets
    chosen = set(args.targets) if args.targets else DEFAULT_TARGETS
    invalid = chosen - VALID_TARGETS
    if invalid:
        print(f"[wipe] Unknown target(s): {', '.join(invalid)}")
        print(f"       Valid: {', '.join(sorted(VALID_TARGETS))}")
        sys.exit(1)

    wipe(chosen, skip_confirm=args.yes)