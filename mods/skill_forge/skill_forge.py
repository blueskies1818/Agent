"""
mods/skill_forge/skill_forge.py — Self-authoring skill registration mod.

The agent writes a skill .md file to the workspace, then calls this mod
to validate and move it into the skills directory.  The workspace file is
deleted on success (move semantics, not copy).

Intercepted shell syntax:
    skill_forge -register ffmpeg.md ffmpeg      Move + register a workspace skill file
    skill_forge -list                           List all skills (agent-created flagged)
    skill_forge -remove ffmpeg                  Remove an agent-created skill
    skill_forge -audit                          Show only agent-created skills
"""

from __future__ import annotations

from pathlib import Path

NAME        = "skill_forge"
DESCRIPTION = "Register, list, and manage agent-authored skill files"


def handle(args: list[str], raw: str) -> str:
    if not args:
        return _usage()

    flag = args[0].lower().lstrip("-")

    if flag == "register":
        if len(args) < 3:
            return "[ERROR] skill_forge -register requires <workspace_file> <skill_name>\n" + _usage()
        workspace_file = args[1]
        skill_name = args[2].lower().replace(" ", "_")
        return _register(workspace_file, skill_name)

    elif flag == "list":
        return _list_skills()

    elif flag == "remove":
        if len(args) < 2:
            return "[ERROR] skill_forge -remove requires <skill_name>\n" + _usage()
        return _remove(args[1].lower())

    elif flag == "audit":
        return _audit()

    else:
        return f"[ERROR] Unknown operation: '{flag}'\n" + _usage()


# ── Operations ─────────────────────────────────────────────────────────────────

def _register(workspace_filename: str, skill_name: str) -> str:
    from config import SANDBOX_ROOT, SKILLS_DIR
    from mods.skill_forge.validator import validate, ValidationError

    workspace_path = Path(SANDBOX_ROOT) / workspace_filename
    if not workspace_path.exists():
        return (
            f"[ERROR] File not found in workspace: {workspace_filename}\n"
            f"  Expected path: {workspace_path}"
        )

    try:
        content = workspace_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"[ERROR] Could not read workspace file: {e}"

    # Inject agent_created + created date into frontmatter before validation
    content = _inject_metadata(content)

    try:
        clean_content = validate(content, skill_name)
    except ValidationError as e:
        return f"[VALIDATION ERROR] {e}\nWorkspace file left in place for editing."

    dest = Path(SKILLS_DIR) / f"{skill_name}.md"
    try:
        dest.write_text(clean_content, encoding="utf-8")
    except Exception as e:
        return f"[ERROR] Could not write to skills directory: {e}"

    # Move semantics — delete the workspace source after successful write
    try:
        workspace_path.unlink()
    except Exception as e:
        return (
            f"Skill '{skill_name}' registered at skills/{skill_name}.md\n"
            f"  [WARNING] Could not remove workspace file: {e}"
        )

    return (
        f"Skill '{skill_name}' registered successfully.\n"
        f"  Destination: skills/{skill_name}.md\n"
        f"  Workspace file removed: {workspace_filename}"
    )


def _list_skills() -> str:
    from config import SKILLS_DIR

    skills_path = Path(SKILLS_DIR)
    if not skills_path.exists():
        return "(no skills directory found)"

    lines = []
    for md_file in sorted(skills_path.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            agent_created = "agent_created: true" in content
            first_heading = next(
                (ln.lstrip("#").strip() for ln in content.splitlines() if ln.startswith("#")),
                md_file.stem,
            )
            tag = " [agent]" if agent_created else ""
            lines.append(f"  {md_file.stem}{tag} — {first_heading}")
        except Exception:
            lines.append(f"  {md_file.stem}")

    if not lines:
        return "(no skills found)"

    return "Skills:\n" + "\n".join(lines)


def _remove(skill_name: str) -> str:
    from config import SKILLS_DIR
    from mods.skill_forge.validator import _PROTECTED_SKILLS

    if skill_name in _PROTECTED_SKILLS:
        return f"[ERROR] '{skill_name}' is a protected built-in skill and cannot be removed."

    skill_path = Path(SKILLS_DIR) / f"{skill_name}.md"
    if not skill_path.exists():
        return f"[ERROR] Skill '{skill_name}' not found."

    try:
        content = skill_path.read_text(encoding="utf-8")
    except Exception as e:
        return f"[ERROR] Could not read skill file: {e}"

    if "agent_created: true" not in content:
        return (
            f"[ERROR] '{skill_name}' is not an agent-created skill.\n"
            "Only agent-created skills can be removed via skill_forge."
        )

    skill_path.unlink()
    return f"Skill '{skill_name}' removed."


def _audit() -> str:
    from config import SKILLS_DIR

    skills_path = Path(SKILLS_DIR)
    lines = []

    for md_file in sorted(skills_path.glob("*.md")):
        try:
            content = md_file.read_text(encoding="utf-8")
            if "agent_created: true" not in content:
                continue
            created = ""
            for ln in content.splitlines():
                if ln.strip().startswith("created:"):
                    created = " — created " + ln.split(":", 1)[1].strip()
                    break
            first_heading = next(
                (ln.lstrip("#").strip() for ln in content.splitlines() if ln.startswith("#")),
                md_file.stem,
            )
            lines.append(f"  {md_file.stem}{created} — {first_heading}")
        except Exception:
            pass

    if not lines:
        return "(no agent-created skills found)"

    return "Agent-created skills:\n" + "\n".join(lines)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _inject_metadata(content: str) -> str:
    """Ensure frontmatter contains agent_created: true and a created date."""
    from datetime import datetime

    today = datetime.now().strftime("%Y-%m-%d")
    stripped = content.strip()

    if not stripped.startswith("---"):
        # No frontmatter — validation will reject this, but return as-is
        return content

    end = stripped.find("\n---", 3)
    if end == -1:
        return content

    frontmatter = stripped[3:end]
    body = stripped[end + 4:]

    additions: list[str] = []
    if "agent_created:" not in frontmatter:
        additions.append("agent_created: true")
    if "created:" not in frontmatter:
        additions.append(f"created: {today}")

    if additions:
        frontmatter = frontmatter.rstrip() + "\n" + "\n".join(additions)

    return f"---{frontmatter}\n---{body}"


def _usage() -> str:
    return (
        "Usage:\n"
        "  skill_forge -register <file> <name>   Register skill from workspace (file is deleted)\n"
        "  skill_forge -list                     List all skills (agent-created marked [agent])\n"
        "  skill_forge -remove <name>            Remove an agent-created skill\n"
        "  skill_forge -audit                    Show only agent-created skills"
    )
