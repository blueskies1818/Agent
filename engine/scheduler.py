"""
engine/scheduler.py — Scheduled task dispatcher.

Standalone process spawned by main.py at startup. Polls the scheduled/
directory every 60 seconds and posts due tasks to the HTTP queue server.

Supported schedule types:
    once      — fires once at next_run, then deleted or tracked by pending_task_id
    interval  — repeating: '12h', '30m', '7d'
    cron      — 5-field cron expression: 'MIN HOUR DOM MON DOW'

Termination types:
    never            — runs indefinitely
    after_completion — deleted once the dispatched task reaches complete/failed
    on_date          — deleted once termination.date has passed

Run directly (for testing):
    python engine/scheduler.py [--server http://127.0.0.1:8765]
Or spawned by main.py:
    subprocess.Popen([sys.executable, "engine/scheduler.py"])
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Project root on sys.path so imports work when spawned as a subprocess
sys.path.insert(0, str(Path(__file__).parent.parent))

from config import SCHEDULED_DIR, SERVER_HOST, SERVER_PORT
from core.log import log

# ── Constants ─────────────────────────────────────────────────────────────────

PARENT_PID  = os.getppid()
SCAN_INTERVAL = 60  # seconds between scans

# Resolved at startup; may be overridden via --server CLI arg
_server_url: str = f"http://{SERVER_HOST}:{SERVER_PORT}"


# ── Parent-alive guard ────────────────────────────────────────────────────────

def _parent_alive() -> bool:
    """Return False the moment the parent process no longer exists."""
    try:
        os.kill(PARENT_PID, 0)
        return True
    except (ProcessLookupError, OSError):
        return False


# ── Time helpers ──────────────────────────────────────────────────────────────

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str | None) -> datetime | None:
    """Parse an ISO-8601 datetime string (with or without trailing Z)."""
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def _fmt_dt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ── Interval / cron helpers ───────────────────────────────────────────────────

def _interval_seconds(value: str) -> int:
    """Parse '12h', '30m', '7d', '90s' into total seconds."""
    value = value.strip()
    if value.endswith("d"):
        return int(value[:-1]) * 86_400
    if value.endswith("h"):
        return int(value[:-1]) * 3_600
    if value.endswith("m"):
        return int(value[:-1]) * 60
    if value.endswith("s"):
        return int(value[:-1])
    raise ValueError(f"Unknown interval format: {value!r}")


def _cron_expand(field: str, lo: int, hi: int) -> list[int]:
    """Expand one cron field into a sorted list of matching integers."""
    result: set[int] = set()
    for part in field.split(","):
        part = part.strip()
        if part == "*":
            result.update(range(lo, hi + 1))
        elif part.startswith("*/"):
            step = int(part[2:])
            result.update(range(lo, hi + 1, step))
        elif "-" in part and "/" in part:
            rng, step_s = part.split("/", 1)
            a, b = rng.split("-", 1)
            result.update(range(int(a), int(b) + 1, int(step_s)))
        elif "-" in part:
            a, b = part.split("-", 1)
            result.update(range(int(a), int(b) + 1))
        else:
            result.add(int(part))
    return sorted(result)


# Cron day-of-week → Python weekday(): cron 0=Sun, Python 0=Mon
_CRON_DOW_TO_PY = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}


def _cron_next(expr: str, after: datetime) -> datetime:
    """
    Return the next datetime >= after+1min that satisfies the 5-field cron
    expression.  Supports numbers, *, */N, ranges (a-b), and range/step.
    """
    fields = expr.strip().split()
    if len(fields) != 5:
        raise ValueError(f"Expected 5 cron fields, got {len(fields)}: {expr!r}")

    f_min  = _cron_expand(fields[0], 0, 59)
    f_hour = _cron_expand(fields[1], 0, 23)
    f_dom  = _cron_expand(fields[2], 1, 31)
    f_mon  = _cron_expand(fields[3], 1, 12)
    f_dow_py = {_CRON_DOW_TO_PY[d] for d in _cron_expand(fields[4], 0, 7)}

    dom_restricted = fields[2] != "*"
    dow_restricted = fields[4] != "*"

    candidate = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    limit = candidate + timedelta(days=366 * 4)

    while candidate < limit:
        # ── Month check ──────────────────────────────────────────────────
        if candidate.month not in f_mon:
            # Jump to first of the next month
            m = candidate.month + 1
            y = candidate.year
            if m > 12:
                m, y = 1, y + 1
            candidate = candidate.replace(year=y, month=m, day=1, hour=0, minute=0)
            continue

        # ── Day check (DOM / DOW) ────────────────────────────────────────
        dom_ok = candidate.day in f_dom
        dow_ok = candidate.weekday() in f_dow_py

        if dom_restricted and dow_restricted:
            day_ok = dom_ok or dow_ok   # cron OR semantics
        elif dom_restricted:
            day_ok = dom_ok
        elif dow_restricted:
            day_ok = dow_ok
        else:
            day_ok = True

        if not day_ok:
            candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
            continue

        # ── Hour check ───────────────────────────────────────────────────
        if candidate.hour not in f_hour:
            next_h = next((h for h in f_hour if h > candidate.hour), None)
            if next_h is None:
                candidate = candidate.replace(hour=0, minute=0) + timedelta(days=1)
            else:
                candidate = candidate.replace(hour=next_h, minute=f_min[0])
            continue

        # ── Minute check ─────────────────────────────────────────────────
        if candidate.minute not in f_min:
            next_m = next((m for m in f_min if m > candidate.minute), None)
            if next_m is None:
                candidate = candidate.replace(minute=0) + timedelta(hours=1)
            else:
                candidate = candidate.replace(minute=next_m)
            continue

        return candidate  # all fields satisfied

    raise ValueError(f"No valid next run found for cron expression: {expr!r}")


def _compute_next_run(task: dict, after: datetime) -> datetime | None:
    """Return the next fire time, or None if this is a one-shot task."""
    sched = task.get("schedule", {})
    stype = sched.get("type", "once")
    sval  = sched.get("value", "")

    if stype == "once":
        return None
    if stype == "interval":
        return after + timedelta(seconds=_interval_seconds(sval))
    if stype == "cron":
        return _cron_next(sval, after)

    log.error(f"Unknown schedule type {stype!r} — treating as once", source="scheduler")
    return None


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _post_task(task: dict) -> str | None:
    """POST task prompt to /queue; return the queue task_id or None on error."""
    payload = {
        "prompt":   task["prompt"],
        "session":  task.get("session", "new"),
        "skills":   task.get("skills", []),
        "priority": task.get("priority", 1),
        "source":   f"scheduler:{task.get('task_id', 'unknown')}",
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{_server_url}/queue",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())["task_id"]
    except Exception as exc:
        log.error(f"POST /queue failed: {exc}", source="scheduler")
        return None


def _get_task_status(queue_task_id: str) -> str | None:
    """GET /tasks/<id>; return status string or None on error."""
    try:
        with urllib.request.urlopen(
            f"{_server_url}/tasks/{queue_task_id}", timeout=10
        ) as resp:
            return json.loads(resp.read()).get("status")
    except Exception:
        return None


# ── File helpers ──────────────────────────────────────────────────────────────

def _rewrite(path: Path, task: dict) -> None:
    path.write_text(json.dumps(task, indent=2, default=str), encoding="utf-8")


# ── Per-file dispatch logic ───────────────────────────────────────────────────

def _process_file(path: Path) -> None:
    """Evaluate one scheduled task JSON file and dispatch if due."""
    try:
        task = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        log.error(f"Cannot parse {path.name}: {exc}", source="scheduler")
        return

    task_id  = task.get("task_id", path.stem)
    term     = task.get("termination", {})
    term_type = term.get("type", "never")

    # ── Termination by date ──────────────────────────────────────────────
    if term_type == "on_date":
        deadline = _parse_dt(term.get("date"))
        if deadline and _now() >= deadline:
            log.info(f"Task {task_id!r}: on_date expired — removing", source="scheduler")
            path.unlink(missing_ok=True)
            return

    # ── Pending task status check ────────────────────────────────────────
    pending_id = task.get("pending_task_id")
    if pending_id:
        status = _get_task_status(pending_id)
        if status in ("complete", "failed"):
            if term_type == "after_completion":
                log.info(
                    f"Task {task_id!r}: after_completion ({status}) — removing",
                    source="scheduler",
                )
                path.unlink(missing_ok=True)
                return
            # Recurring task — clear pending flag so next run can proceed
            task.pop("pending_task_id", None)
            _rewrite(path, task)
        elif status in ("queued", "running"):
            return  # still in flight — skip this cycle
        # Unknown / unreachable status — clear and retry next cycle
        else:
            task.pop("pending_task_id", None)
            _rewrite(path, task)

    # ── Due check ────────────────────────────────────────────────────────
    next_run = _parse_dt(task.get("next_run"))
    now = _now()

    if next_run is None or now < next_run:
        return  # not due yet

    log.info(f"Dispatching {task_id!r}", source="scheduler")

    queue_id = _post_task(task)
    if queue_id is None:
        return  # will retry next scan

    task["last_run"] = _fmt_dt(now)

    next_dt = _compute_next_run(task, now)

    if next_dt is None:
        # One-shot: record pending so next scan can check completion
        task["next_run"] = None
        task["pending_task_id"] = queue_id
        _rewrite(path, task)
        return

    task["next_run"] = _fmt_dt(next_dt)

    # Track pending for after_completion termination on recurring tasks too
    if term_type == "after_completion":
        task["pending_task_id"] = queue_id

    _rewrite(path, task)


# ── Main scan loop ────────────────────────────────────────────────────────────

def _scan_and_dispatch() -> None:
    scheduled_dir = Path(SCHEDULED_DIR)
    if not scheduled_dir.exists():
        return
    for path in sorted(scheduled_dir.glob("*.json")):
        try:
            _process_file(path)
        except Exception as exc:
            log.error(f"Unhandled error processing {path.name}: {exc}", source="scheduler")


def main() -> None:
    log.info(
        f"Scheduler started (parent={PARENT_PID}, server={_server_url})",
        source="scheduler",
    )

    while True:
        if not _parent_alive():
            log.info("Main process gone — scheduler exiting", source="scheduler")
            break

        try:
            _scan_and_dispatch()
        except Exception as exc:
            log.error(f"Scan loop error: {exc}", source="scheduler")

        time.sleep(SCAN_INTERVAL)


# ── CLI entry point ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Agent task scheduler")
    parser.add_argument(
        "--server",
        default=f"http://{SERVER_HOST}:{SERVER_PORT}",
        help="HTTP server base URL (default from config)",
    )
    args = parser.parse_args()
    _server_url = args.server.rstrip("/")

    main()
