"""
mods/schedule/schedule.py — Schedule tasks for future execution.

Intercepted shell syntax:
    schedule -add "prompt" -type once    -value "2026-05-01T10:00:00Z"
    schedule -add "prompt" -type once    -value 2h          (relative: now + 2h)
    schedule -add "prompt" -type interval -value 12h
    schedule -add "prompt" -type cron    -value "0 9 * * 1"
    schedule -add "..." -type once -value 1d -stop after_completion
    schedule -add "..." -type cron -value "0 0 * * *" -stop on_date -until "2026-12-31"
    schedule -list
    schedule -remove <task_id>
    schedule -show   <task_id>

Schedule types:
    once      fires once at the given datetime, then remains with next_run=null
    interval  repeating: '12h', '30m', '7d', '90s'
    cron      5-field cron expression: 'MIN HOUR DOM MON DOW'

Termination (--stop):
    never             runs indefinitely (default for interval/cron)
    after_completion  deleted once the dispatched task reaches complete/failed
    on_date           deleted once --until date has passed (requires -until)
"""

from __future__ import annotations

import json
import re
import shlex
from datetime import datetime, timedelta, timezone
from pathlib import Path

from config import SCHEDULED_DIR


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _slugify(text: str, maxlen: int = 32) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower().strip())
    return slug.strip("-")[:maxlen]


def _make_task_id(prompt: str) -> str:
    date_part = _now().strftime("%Y-%m-%d")
    return f"{date_part}_{_slugify(prompt)}"


def _parse_relative(value: str) -> datetime | None:
    """Parse a relative offset like '2h', '30m', '7d', '90s' into an absolute datetime."""
    value = value.strip()
    mapping = {"d": 86_400, "h": 3_600, "m": 60, "s": 1}
    if value and value[-1] in mapping:
        try:
            seconds = int(value[:-1]) * mapping[value[-1]]
            return _now() + timedelta(seconds=seconds)
        except ValueError:
            pass
    return None


def _parse_once_value(value: str) -> datetime:
    """
    Accept either an absolute ISO datetime or a relative offset (2h, 1d, …).
    Raises ValueError if neither format is recognised.
    """
    rel = _parse_relative(value)
    if rel:
        return rel
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"Cannot parse schedule value {value!r}. "
            "Use an ISO datetime (2026-05-01T10:00:00Z) or a relative offset (2h, 1d)."
        )


def _interval_seconds(value: str) -> int:
    """Parse '12h', '30m', '7d', '90s' into seconds. Mirrors scheduler logic."""
    value = value.strip()
    mapping = {"d": 86_400, "h": 3_600, "m": 60, "s": 1}
    if value and value[-1] in mapping:
        return int(value[:-1]) * mapping[value[-1]]
    raise ValueError(f"Unknown interval format: {value!r}")


def _cron_next_run(expr: str) -> datetime:
    """Import the scheduler's cron parser to compute the next fire time."""
    from engine.scheduler import _cron_next
    return _cron_next(expr, _now())


def _scheduled_dir() -> Path:
    p = Path(SCHEDULED_DIR)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_task(task_id: str) -> tuple[Path, dict] | None:
    path = _scheduled_dir() / f"{task_id}.json"
    if not path.exists():
        return None
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _save_task(task: dict) -> Path:
    path = _scheduled_dir() / f"{task['task_id']}.json"
    path.write_text(json.dumps(task, indent=2, default=str), encoding="utf-8")
    return path


# ── Command handlers ──────────────────────────────────────────────────────────

def _add(args: list[str], raw: str) -> str:
    # Pull the prompt (first quoted string or bare token after -add)
    prompt = ""
    rest = list(args)
    if rest and not rest[0].startswith("-"):
        prompt = rest.pop(0).strip("\"'")
    else:
        # try extracting quoted prompt from raw
        from mods._shared import extract_quoted as _eq
        prompt = _eq(rest, raw, "-add")

    if not prompt:
        return "[ERROR] schedule -add requires a prompt string.\n" + _usage()

    # Parse remaining flags
    flags: dict = {}
    i = 0
    while i < len(rest):
        tok = rest[i].lstrip("-")
        if i + 1 < len(rest) and not rest[i + 1].startswith("-"):
            flags[tok] = rest[i + 1]
            i += 2
        else:
            flags[tok] = True
            i += 1

    stype = flags.get("type", "once")
    value = flags.get("value", "")
    stop  = flags.get("stop", "never")
    until = flags.get("until", "")

    if not value:
        return "[ERROR] schedule -add requires -value.\n" + _usage()

    # ── Compute next_run ─────────────────────────────────────────────────
    try:
        if stype == "once":
            next_run = _fmt(_parse_once_value(str(value)))
        elif stype == "interval":
            secs = _interval_seconds(str(value))
            next_run = _fmt(_now() + timedelta(seconds=secs))
        elif stype == "cron":
            next_run = _fmt(_cron_next_run(str(value)))
        else:
            return f"[ERROR] Unknown schedule type {stype!r}. Use once, interval, or cron."
    except ValueError as e:
        return f"[ERROR] {e}"

    # ── Termination ──────────────────────────────────────────────────────
    termination: dict = {"type": stop}
    if stop == "on_date":
        if not until:
            return "[ERROR] -stop on_date requires -until <ISO-date>.\n" + _usage()
        termination["date"] = until if "T" in until else f"{until}T00:00:00Z"

    task_id = _make_task_id(prompt)

    task = {
        "task_id":         task_id,
        "prompt":          prompt,
        "schedule":        {"type": stype, "value": str(value)},
        "next_run":        next_run,
        "last_run":        None,
        "pending_task_id": None,
        "termination":     termination,
        "session":         "new",
        "skills":          [],
        "priority":        1,
        "created_at":      _fmt(_now()),
    }

    path = _save_task(task)
    return (
        f"Scheduled task created.\n"
        f"  task_id:  {task_id}\n"
        f"  type:     {stype}  ({value})\n"
        f"  next_run: {next_run}\n"
        f"  stop:     {stop}\n"
        f"  file:     {path.name}"
    )


def _list_tasks() -> str:
    tasks = sorted(_scheduled_dir().glob("*.json"))
    if not tasks:
        return "(no scheduled tasks)"
    lines = []
    for path in tasks:
        try:
            t = json.loads(path.read_text(encoding="utf-8"))
            sched = t.get("schedule", {})
            lines.append(
                f"  {t['task_id']}\n"
                f"    prompt:   {t.get('prompt', '')[:60]}\n"
                f"    type:     {sched.get('type')}  ({sched.get('value')})\n"
                f"    next_run: {t.get('next_run') or '(done)'}\n"
                f"    stop:     {t.get('termination', {}).get('type', 'never')}"
            )
        except Exception as e:
            lines.append(f"  {path.stem}  [parse error: {e}]")
    return f"Scheduled tasks ({len(tasks)}):\n\n" + "\n\n".join(lines)


def _remove(task_id: str) -> str:
    result = _load_task(task_id)
    if result is None:
        return f"[ERROR] No task found with id '{task_id}'."
    path, _ = result
    path.unlink()
    return f"Task '{task_id}' removed."


def _show(task_id: str) -> str:
    result = _load_task(task_id)
    if result is None:
        return f"[ERROR] No task found with id '{task_id}'."
    _, task = result
    return json.dumps(task, indent=2, default=str)


# ── Dispatch ──────────────────────────────────────────────────────────────────

def handle(args: list[str], raw: str) -> str:
    if not args:
        return _usage()

    flag = args[0].lower().lstrip("-")

    if flag == "add":
        return _add(args[1:], raw)
    elif flag == "list":
        return _list_tasks()
    elif flag == "remove":
        if len(args) < 2:
            return "[ERROR] schedule -remove requires a task_id.\n" + _usage()
        return _remove(args[1])
    elif flag == "show":
        if len(args) < 2:
            return "[ERROR] schedule -show requires a task_id.\n" + _usage()
        return _show(args[1])
    else:
        return f"[ERROR] Unknown schedule operation '{flag}'.\n" + _usage()


# ── Usage ─────────────────────────────────────────────────────────────────────

def _usage() -> str:
    return """Usage:
  schedule -add "prompt" -type once     -value "2026-05-01T10:00:00Z"
  schedule -add "prompt" -type once     -value 2h            (relative: now + 2h)
  schedule -add "prompt" -type interval -value 12h
  schedule -add "prompt" -type cron     -value "0 9 * * 1"  (every Monday 9am)
  schedule -add "..." -type once     -value 1d -stop after_completion
  schedule -add "..." -type cron     -value "0 0 * * *" -stop on_date -until "2026-12-31"
  schedule -list
  schedule -remove <task_id>
  schedule -show   <task_id>

Schedule types:
  once      fires once at the given datetime (ISO or relative offset: 2h, 1d, 30m)
  interval  repeating interval: 12h, 30m, 7d, 90s
  cron      5-field cron: 'MIN HOUR DOM MON DOW'  e.g. '0 9 * * 1' = Mon 9am

Termination (-stop):
  never             runs indefinitely (default for interval/cron)
  after_completion  removed once the dispatched task completes
  on_date           removed after -until date (ISO date required)"""
