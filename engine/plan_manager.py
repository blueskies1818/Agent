"""
engine/plan_manager.py — Plan file operations for the planner/worker split.

The plan file is the source of truth for task progress. It travels with the
workspace (.agent/plan.md) or lives in workspace/.agent/plans/<task_id>.md for local tasks.

Plan file format
────────────────
---
task_id:    2026-04-07_refactor-auth
status:     active          # active | paused | complete | failed
workspace:  /home/user/my-app
created_at: 2026-04-07T14:32:00Z
updated_at: 2026-04-07T15:01:00Z
---

# Refactor auth middleware

## Steps
- [x] Read existing auth code
- [ ] Rewrite token storage   ← CURRENT
- [ ] Write tests

## Notes
(discoveries and blockers captured here)

Usage
─────
    from engine.plan_manager import PlanManager

    pm = PlanManager(workspace="/home/user/my-app")
    pm.write_plan("Fix auth", ["Read code", "Rewrite storage", "Write tests"])

    # Advance progress
    pm.step_done(1)

    # Worker context injection
    log = pm.generate_project_log()
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path

from config import BASE_DIR
from core.log import log


# ── Paths ─────────────────────────────────────────────────────────────────────

_PLANS_DIR  = BASE_DIR / "workspace" / ".agent" / "plans"
_INDEX_FILE = _PLANS_DIR / "index.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _task_id_from_title(title: str) -> str:
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    return f"{date}_{slug}"


# ── Index helpers ─────────────────────────────────────────────────────────────

def _read_index() -> dict:
    if not _INDEX_FILE.exists():
        return {}
    try:
        return json.loads(_INDEX_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_index(index: dict) -> None:
    _PLANS_DIR.mkdir(parents=True, exist_ok=True)
    _INDEX_FILE.write_text(
        json.dumps(index, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


# ── Frontmatter helpers ───────────────────────────────────────────────────────

def _parse_frontmatter(content: str) -> tuple[dict, str]:
    """Return (fields_dict, body_without_frontmatter)."""
    if not content.startswith("---"):
        return {}, content
    end = content.find("---", 3)
    if end == -1:
        return {}, content
    fm_text = content[3:end]
    body    = content[end + 3:].lstrip("\n")
    fields: dict[str, str] = {}
    for line in fm_text.splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            fields[k.strip()] = v.strip()
    return fields, body


def _build_frontmatter(fields: dict) -> str:
    lines = ["---"]
    for k, v in fields.items():
        lines.append(f"{k}: {v}")
    lines.append("---\n")
    return "\n".join(lines)


# ── Step parsing helpers ──────────────────────────────────────────────────────

_STEP_RE = re.compile(r"^(\s*-\s*)\[( |x|X)\]\s*(.*?)(\s*←\s*CURRENT)?\s*$")


def _parse_steps_section(body: str) -> list[tuple[bool, str, bool]]:
    """
    Extract steps from the ## Steps section.
    Returns list of (done, text, is_current).
    """
    in_steps = False
    results  = []
    for line in body.splitlines():
        if line.strip().startswith("## Steps"):
            in_steps = True
            continue
        if in_steps and line.strip().startswith("## "):
            break
        if in_steps:
            m = _STEP_RE.match(line)
            if m:
                done       = m.group(2).lower() == "x"
                text       = m.group(3).strip()
                is_current = bool(m.group(4))
                results.append((done, text, is_current))
    return results


def _replace_steps_section(body: str, new_steps_block: str) -> str:
    """Replace the content of the ## Steps section in body."""
    lines     = body.splitlines(keepends=True)
    out       = []
    in_steps  = False
    replaced  = False
    for line in lines:
        if line.strip().startswith("## Steps") and not replaced:
            in_steps = True
            out.append(line)
            out.append(new_steps_block)
            continue
        if in_steps:
            if line.strip().startswith("## "):
                in_steps = False
                replaced = True
                out.append(line)
            # else: skip old step lines
        else:
            out.append(line)
    return "".join(out)


def _steps_to_block(steps: list[tuple[bool, str, bool]]) -> str:
    """Render steps list back to markdown checkboxes."""
    lines = []
    for done, text, is_current in steps:
        checkbox = "[x]" if done else "[ ]"
        marker   = "   ← CURRENT" if is_current else ""
        lines.append(f"- {checkbox} {text}{marker}")
    return "\n".join(lines) + "\n"


# ── PlanManager ───────────────────────────────────────────────────────────────

class PlanManager:
    """
    Manages the plan file for a session.

    workspace  — path to the active project directory, or None for local mode.
                 In workspace mode the plan lives at <workspace>/.agent/plan.md.
                 In local mode it lives at memory/plans/<task_id>.md.
    """

    def __init__(self, workspace: str | None = None) -> None:
        self._workspace = Path(workspace) if workspace else None
        self._task_id: str | None = None

    # ── Path resolution ───────────────────────────────────────────────────────

    @property
    def _plan_path(self) -> Path | None:
        if self._task_id is None:
            return None
        if self._workspace:
            return self._workspace / ".agent" / "plan.md"
        return _PLANS_DIR / f"{self._task_id}.md"

    def _resolved_path(self, workspace_override: str | None = None) -> Path | None:
        ws = Path(workspace_override) if workspace_override else self._workspace
        if self._task_id is None:
            return None
        if ws:
            return ws / ".agent" / "plan.md"
        return _PLANS_DIR / f"{self._task_id}.md"

    # ── Write plan ────────────────────────────────────────────────────────────

    def write_plan(
        self,
        title: str,
        steps: list[str],
        workspace: str | None = None,
        session: str | None = None,
    ) -> str:
        """
        Create or overwrite the plan file.

        session — Glass AI conversation ID that triggered this plan, if any.
                  Stored in frontmatter and index so plans can be cleaned up
                  when their conversation is deleted.

        Returns the task_id.
        """
        if workspace:
            self._workspace = Path(workspace)

        task_id    = _task_id_from_title(title)
        self._task_id = task_id
        now        = _now_iso()

        ws_str  = str(self._workspace) if self._workspace else ""
        sess_str = session or ""

        fields = {
            "task_id":    task_id,
            "status":     "active",
            "workspace":  ws_str,
            "session":    sess_str,
            "created_at": now,
            "updated_at": now,
        }

        # Build steps block — first step is CURRENT
        step_lines = []
        for i, step in enumerate(steps):
            marker = "   ← CURRENT" if i == 0 else ""
            step_lines.append(f"- [ ] {step}{marker}")

        body = f"# {title}\n\n## Steps\n" + "\n".join(step_lines) + "\n\n## Notes\n"

        content = _build_frontmatter(fields) + body

        path = self._plan_path
        if path is None:
            log.error("plan path could not be resolved", source="plan_manager")
            return task_id

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

        # Update global index
        index = _read_index()
        index[task_id] = {
            "task_id":    task_id,
            "title":      title,
            "status":     "active",
            "workspace":  ws_str,
            "session":    sess_str,
            "plan_path":  str(path),
            "created_at": now,
            "updated_at": now,
        }
        _write_index(index)

        log.info(f"plan written: {task_id}", source="plan_manager")
        return task_id

    # ── Read plan ─────────────────────────────────────────────────────────────

    def read_plan(self, workspace: str | None = None) -> str:
        path = self._resolved_path(workspace)
        if path is None or not path.exists():
            return "(no active plan)"
        return path.read_text(encoding="utf-8")

    # ── Step done ─────────────────────────────────────────────────────────────

    def step_done(self, n: int) -> None:
        """
        Mark step n (1-indexed) complete and advance ← CURRENT to the next step.
        """
        path = self._plan_path
        if path is None or not path.exists():
            return

        content        = path.read_text(encoding="utf-8")
        fields, body   = _parse_frontmatter(content)
        steps          = _parse_steps_section(body)

        if not steps:
            return

        idx = n - 1
        if idx < 0 or idx >= len(steps):
            return

        # Mark done, clear current
        new_steps = [(done, text, False) for done, text, _ in steps]
        done_flag, step_text, _ = new_steps[idx]
        new_steps[idx] = (True, step_text, False)

        # Advance CURRENT to next undone step
        for j in range(idx + 1, len(new_steps)):
            if not new_steps[j][0]:
                d, t, _ = new_steps[j]
                new_steps[j] = (d, t, True)
                break

        new_block = _steps_to_block(new_steps)
        new_body  = _replace_steps_section(body, new_block)

        fields["updated_at"] = _now_iso()
        path.write_text(_build_frontmatter(fields) + new_body, encoding="utf-8")
        self._update_index_status(fields.get("task_id", ""), fields.get("status", "active"))

    # ── Inject step ───────────────────────────────────────────────────────────

    def inject_step(self, after_n: int, content_text: str) -> None:
        """
        Insert a new [INJECTED] step after step n (1-indexed).
        The injected step becomes the new CURRENT.
        """
        path = self._plan_path
        if path is None or not path.exists():
            return

        content      = path.read_text(encoding="utf-8")
        fields, body = _parse_frontmatter(content)
        steps        = _parse_steps_section(body)

        if not steps:
            return

        # Clear existing CURRENT markers
        new_steps = [(done, text, False) for done, text, _ in steps]

        # Insert injected step after after_n (1-indexed)
        insert_at = min(after_n, len(new_steps))
        injected  = (False, f"[INJECTED] {content_text}", True)
        new_steps.insert(insert_at, injected)

        new_block = _steps_to_block(new_steps)
        new_body  = _replace_steps_section(body, new_block)

        fields["updated_at"] = _now_iso()
        path.write_text(_build_frontmatter(fields) + new_body, encoding="utf-8")

    # ── Add note ──────────────────────────────────────────────────────────────

    def add_note(self, note_content: str) -> None:
        """Append a note to the ## Notes section."""
        path = self._plan_path
        if path is None or not path.exists():
            return

        content      = path.read_text(encoding="utf-8")
        fields, body = _parse_frontmatter(content)

        if "## Notes" in body:
            body = body.rstrip() + f"\n- {note_content}\n"
        else:
            body = body.rstrip() + f"\n\n## Notes\n- {note_content}\n"

        fields["updated_at"] = _now_iso()
        path.write_text(_build_frontmatter(fields) + body, encoding="utf-8")

    # ── Set status ────────────────────────────────────────────────────────────

    def set_status(self, status: str) -> None:
        """Update the frontmatter status field: active | paused | complete | failed."""
        path = self._plan_path
        if path is None or not path.exists():
            return

        content      = path.read_text(encoding="utf-8")
        fields, body = _parse_frontmatter(content)

        fields["status"]     = status
        fields["updated_at"] = _now_iso()
        path.write_text(_build_frontmatter(fields) + body, encoding="utf-8")

        tid = fields.get("task_id") or self._task_id
        if tid:
            self._update_index_status(tid, status)

    # ── Generate project log ──────────────────────────────────────────────────

    def generate_project_log(self) -> str:
        """
        Build a compact worker-facing summary from plan progress.
        Shows completed steps and identifies the current step.

        Example output:
            [DONE] Step 1 — Read config directory
            [DONE] Step 2 — Identified correct file
            [CURRENT] Step 3 — Edit nginx upstream block
        """
        path = self._plan_path
        if path is None or not path.exists():
            return "(no plan active)"

        content    = path.read_text(encoding="utf-8")
        _, body    = _parse_frontmatter(content)
        steps      = _parse_steps_section(body)

        if not steps:
            return "(plan has no steps)"

        lines = []
        for i, (done, text, is_current) in enumerate(steps, 1):
            if done:
                lines.append(f"[DONE] Step {i} — {text}")
            elif is_current:
                lines.append(f"[CURRENT] Step {i} — {text}")
        return "\n".join(lines) if lines else "(no progress yet)"

    # ── List plans ────────────────────────────────────────────────────────────

    def list_plans(self) -> list[dict]:
        """Return all plans from the global index."""
        return list(_read_index().values())

    # ── Resume plan ───────────────────────────────────────────────────────────

    def resume(self, task_id: str) -> str:
        """Load a plan by task_id and return its contents."""
        index = _read_index()
        entry = index.get(task_id)
        if not entry:
            return f"(no plan found for task_id '{task_id}')"

        plan_path = Path(entry["plan_path"])
        if not plan_path.exists():
            return f"(plan file missing: {plan_path})"

        self._task_id  = task_id
        ws = entry.get("workspace", "")
        self._workspace = Path(ws) if ws else None

        return plan_path.read_text(encoding="utf-8")

    # ── Current step text ─────────────────────────────────────────────────────

    def current_step_text(self) -> str:
        """Return the text of the step currently marked ← CURRENT."""
        path = self._plan_path
        if path is None or not path.exists():
            return ""

        content    = path.read_text(encoding="utf-8")
        _, body    = _parse_frontmatter(content)
        steps      = _parse_steps_section(body)

        for _, text, is_current in steps:
            if is_current:
                return text
        # Fall back to first undone step
        for _, text, done in steps:
            if not done:
                return text
        return ""

    def current_step_index(self) -> int:
        """Return the 1-indexed position of the ← CURRENT step, or 0 if none."""
        path = self._plan_path
        if path is None or not path.exists():
            return 0

        content    = path.read_text(encoding="utf-8")
        _, body    = _parse_frontmatter(content)
        steps      = _parse_steps_section(body)

        for i, (_, _, is_current) in enumerate(steps, 1):
            if is_current:
                return i
        return 0

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _update_index_status(self, task_id: str, status: str) -> None:
        if not task_id:
            return
        index = _read_index()
        if task_id in index:
            index[task_id]["status"]     = status
            index[task_id]["updated_at"] = _now_iso()
            _write_index(index)
