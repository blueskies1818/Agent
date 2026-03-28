"""
core/xml_parser.py — Parse structured tags from AI responses.

The AI speaks in mixed format:
  - Plain text     → reasoning shown to the user
  - <think>        → internal monologue (shown dimmed, not fed back)
  - <plan>         → step-by-step breakdown (shown to user, stored in state)
  - <work>         → current activity status line (shown to user)
  - <action>       → executable actions the loop runs

Think / plan / work tags
────────────────────────
<think>
  Internal reasoning. Never shown in full — only a dimmed header.
  Not fed back into the conversation.
</think>

<plan>
  1. Check the current directory
  2. Write the file
  3. Verify output
</plan>

<work>Loading the write skill before creating the file.</work>

Action tags (executable)
────────────────────────
<action type="shell"><command>ls -la</command></action>
<action type="skill"><n>write</n></action>
<action type="memory"><op>write</op><content>fact</content></action>
<action type="done"/>
"""

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field


# ── Data models ───────────────────────────────────────────────────────────────

@dataclass
class Action:
    type: str
    data: dict = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"<Action type={self.type!r} data={self.data}>"


@dataclass
class ThinkBlock:
    content: str


@dataclass
class PlanBlock:
    content: str
    steps: list[str]


@dataclass
class WorkBlock:
    content: str


# ── Regexes ───────────────────────────────────────────────────────────────────

_ACTION_RE = re.compile(
    r"<action\b[^>]*/>"
    r"|"
    r"<action\b[^>]*>.*?</action>",
    re.DOTALL | re.IGNORECASE,
)

_THINK_RE = re.compile(r"<think\b[^>]*>(.*?)</think>", re.DOTALL | re.IGNORECASE)
_PLAN_RE  = re.compile(r"<plan\b[^>]*>(.*?)</plan>",   re.DOTALL | re.IGNORECASE)
_WORK_RE  = re.compile(r"<work\b[^>]*>(.*?)</work>",   re.DOTALL | re.IGNORECASE)

_ALL_TAGS_RE = re.compile(
    r"<think\b[^>]*>.*?</think>"
    r"|<plan\b[^>]*>.*?</plan>"
    r"|<work\b[^>]*>.*?</work>"
    r"|<action\b[^>]*/>"
    r"|<action\b[^>]*>.*?</action>",
    re.DOTALL | re.IGNORECASE,
)


# ── Parsers ───────────────────────────────────────────────────────────────────

def _extract_attr(tag_text: str, attr: str) -> str:
    """Extract an attribute value from an opening XML tag using regex."""
    m = re.search(rf'{attr}=["\']([^"\']*)["\']', tag_text, re.IGNORECASE)
    return m.group(1).strip() if m else ""


def _extract_children_regex(raw: str) -> dict:
    """
    Regex-based child tag extractor used when ET.fromstring fails.

    Handles shell commands containing >, <, &, heredocs, etc.
    """
    data: dict[str, str] = {}
    for m in re.finditer(r"<(?!action\b)(\w+)>(.*?)</\1>", raw, re.DOTALL | re.IGNORECASE):
        tag     = m.group(1)
        content = m.group(2)
        content = (content
                   .replace("&lt;",  "<")
                   .replace("&gt;",  ">")
                   .replace("&amp;", "&")
                   .replace("&quot;", '"'))
        data[tag] = content.strip()
    return data


def _parse_plan_steps(text: str) -> list[str]:
    """Extract numbered or bulleted steps, stripping XML tags before bullets."""
    steps = []
    for line in text.strip().splitlines():
        line = re.sub(r"<[^>]+>", "", line)
        line = re.sub(r"^\s*(\d+[\.\)]\s*|[-*•]\s*)", "", line)
        line = line.strip()
        if line:
            steps.append(line)
    return steps


def parse_response(
    text: str,
) -> tuple[str, list[Action], list[ThinkBlock], list[PlanBlock], list[WorkBlock]]:
    """
    Split an AI response into its components.

    Returns:
        reasoning   — plain text with all tags stripped
        actions     — executable Action objects in document order
        thinks      — ThinkBlock objects (internal reasoning)
        plans       — PlanBlock objects (step breakdowns)
        works       — WorkBlock objects (status lines)
    """
    actions: list[Action]     = []
    thinks:  list[ThinkBlock] = []
    plans:   list[PlanBlock]  = []
    works:   list[WorkBlock]  = []

    for m in _THINK_RE.finditer(text):
        thinks.append(ThinkBlock(content=m.group(1).strip()))

    for m in _PLAN_RE.finditer(text):
        raw = m.group(1).strip()
        plans.append(PlanBlock(content=raw, steps=_parse_plan_steps(raw)))

    for m in _WORK_RE.finditer(text):
        works.append(WorkBlock(content=m.group(1).strip()))

    for m in _ACTION_RE.finditer(text):
        raw = m.group()
        action_type = _extract_attr(raw, "type")
        if not action_type:
            continue
        try:
            root = ET.fromstring(raw)
            data = {child.tag: (child.text or "").strip() for child in root}
        except ET.ParseError:
            data = _extract_children_regex(raw)
        actions.append(Action(type=action_type, data=data))

    reasoning = _ALL_TAGS_RE.sub("", text).strip()
    return reasoning, actions, thinks, plans, works


def format_result(action: Action, output: str) -> str:
    """Wrap an action result so the AI can read it in the next turn."""
    lines = [
        f"[{action.type.upper()} RESULT]",
        output.strip() if output.strip() else "(no output)",
        f"[/{action.type.upper()} RESULT]",
    ]
    return "\n".join(lines)