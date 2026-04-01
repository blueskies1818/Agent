"""
mods/skill_forge/validator.py — Validate and sanitize agent-authored skill files.

Enforces structure requirements and strips content that could cause
prompt injection when the skill is loaded into the context window.
"""

from __future__ import annotations

import re

# Skill names the agent cannot overwrite
_PROTECTED_SKILLS = {
    "read", "write", "edit", "delete",
    "memory", "web_search", "debug_ui", "skill_forge",
}

_MAX_BYTES = 10_240  # 10 KB

# XML tags that could inject instructions when the skill is loaded as context
_STRIP_TAGS_RE = re.compile(
    r"<action[^>]*>.*?</action>"
    r"|<think>.*?</think>"
    r"|<plan>.*?</plan>"
    r"|<work>.*?</work>",
    re.DOTALL | re.IGNORECASE,
)


class ValidationError(ValueError):
    pass


def validate(content: str, skill_name: str) -> str:
    """
    Validate and sanitize skill file content.

    Returns the sanitized content string on success.
    Raises ValidationError with a human-readable message on failure.
    """
    if skill_name.lower() in _PROTECTED_SKILLS:
        raise ValidationError(
            f"'{skill_name}' is a protected built-in skill and cannot be overwritten."
        )

    if len(content.encode("utf-8")) > _MAX_BYTES:
        raise ValidationError(
            f"Skill file exceeds the 10 KB limit ({len(content.encode())} bytes)."
        )

    if not content.strip():
        raise ValidationError("Skill file is empty.")

    frontmatter, body = _split_frontmatter(content)

    if frontmatter is None:
        raise ValidationError(
            "Skill file must begin with a --- frontmatter block containing 'keywords:'."
        )

    if "keywords:" not in frontmatter:
        raise ValidationError(
            "Frontmatter must include a 'keywords:' field (comma-separated list)."
        )

    has_title = any(line.strip().startswith("#") for line in body.splitlines())
    if not has_title:
        raise ValidationError(
            "Skill file must have a markdown heading line (e.g. # MyTool — description)."
        )

    # Strip any embedded action/think/plan/work tags from the body
    clean_body = _STRIP_TAGS_RE.sub("", body).strip()

    return f"---\n{frontmatter.strip()}\n---\n{clean_body}\n"


def parse_keywords(content: str) -> list[str]:
    """
    Extract the keywords list from a skill file's frontmatter.
    Returns an empty list if no frontmatter or no keywords field.
    """
    frontmatter, _ = _split_frontmatter(content)
    if not frontmatter:
        return []
    for line in frontmatter.splitlines():
        if line.strip().startswith("keywords:"):
            value = line.split(":", 1)[1].strip()
            return [kw.strip().lower() for kw in value.split(",") if kw.strip()]
    return []


def _split_frontmatter(content: str) -> tuple[str | None, str]:
    """
    Split content into (frontmatter_body, markdown_body).
    Returns (None, content) if no valid frontmatter block is found.
    """
    stripped = content.strip()
    if not stripped.startswith("---"):
        return None, content

    end = stripped.find("\n---", 3)
    if end == -1:
        return None, content

    frontmatter = stripped[3:end].strip()
    body = stripped[end + 4:].strip()
    return frontmatter, body
