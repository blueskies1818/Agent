"""
Context builder — STUB for Phase 5.

Returns minimal context: the user prompt as the only message, and the
system prompt loaded from the correct agent behavior file.  This is
sufficient to get the intake → classify → trivial loop working.

Replaced in Phase 4 with the full per-phase injection system.
"""

from pathlib import Path

from config import BASE_DIR

_AGENT_FILES_DIR = BASE_DIR / "data" / "agent_files"

_PHASE_TO_AGENT_FILE = {
    "intake":    "intakeAgent.md",
    "planning":  "plannerAgent.md",
    "work_node": "workNodeAgent.md",
    "summarize": "summaryAgent.md",
    "session":   "sessionAgent.md",
}


def _load_system_prompt(phase: str) -> str:
    """Load the behavior .md file for the given phase."""
    filename = _PHASE_TO_AGENT_FILE.get(phase)
    if not filename:
        return "You are a helpful AI assistant. Respond only in valid JSON."

    path = _AGENT_FILES_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8")

    return (
        f"You are the {phase} agent. Follow your instructions carefully. "
        "Respond only in valid JSON."
    )


def build_context(
    conn,
    phase: str,
    session_id: str,
    task_id: str | None = None,
    extra: dict | None = None,
) -> tuple[list[dict], str]:
    """
    Assemble (messages, system_prompt) for a given agent phase.

    Stub implementation — returns only the user prompt from extra and
    the system prompt from the behavior file.  The full Phase 4
    implementation adds preferences, skill names, blob index, and
    conversation memory.
    """
    extra = extra or {}
    system = _load_system_prompt(phase)
    user_prompt = extra.get("user_prompt", "")

    messages = []
    if user_prompt:
        messages.append({"role": "user", "content": user_prompt})

    return messages, system