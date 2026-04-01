"""
engine/nodes.py — LangGraph node functions: planner, actor, reflector.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

from config import (
    ACTIVE_PROVIDER,
    ACTIVE_TIER,
    GRAPH_TURN_LIMIT,
    MODS_DIR,
    SKILLS_DIR,
)
from core.xml_parser import (
    Action,
    format_result,
    parse_response,
)
from engine.mod_api import ModResult
from engine.sandbox import run_command
from engine.state import AgentState
from memory.memory import write_memory, read_memory
from memory.embedder import embed_conversation_turn
from mods import ModRouter


# ── ANSI colours ──────────────────────────────────────────────────────────────

_RESET  = "\033[0m"
_DIM    = "\033[2m"
_CYAN   = "\033[36m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_BOLD   = "\033[1m"
_BLUE   = "\033[34m"


# ── Singletons ───────────────────────────────────────────────────────────────

_mod_router: ModRouter | None = None


def _get_mod_router() -> ModRouter:
    global _mod_router
    if _mod_router is None:
        _mod_router = ModRouter(MODS_DIR)
    return _mod_router


# ── Display helpers ───────────────────────────────────────────────────────────

def _print_think(_content: str) -> None:
    print(f"{_DIM}[thinking...]{_RESET}", flush=True)

def _print_plan(steps: list[str]) -> None:
    if not steps:
        return
    print(f"\n{_CYAN}{_BOLD}[plan]{_RESET}", flush=True)
    for i, step in enumerate(steps, 1):
        print(f"  {_CYAN}{i}.{_RESET} {step}", flush=True)

def _print_work(content: str) -> None:
    print(f"\n{_YELLOW}[work]{_RESET} {content}", flush=True)


# ── Shell / skill / memory helpers ────────────────────────────────────────────

def _run_shell(command: str) -> ModResult:
    """
    Execute a shell command.  Returns ModResult (text + optional images).

    Interpolates <<CREDENTIAL>> placeholders before execution and scrubs
    credential values from output before returning — the LLM never sees
    raw values in either direction.
    """
    try:
        from mods.passwd.cache import interpolate, scrub
        command = interpolate(command)
        _scrub = scrub
    except Exception:
        _scrub = lambda t: t  # noqa: E731

    router = _get_mod_router()
    hit, result = router.try_handle(command)
    if hit:
        return ModResult(text=_scrub(result.text), images=result.images)

    output = run_command(command)
    return ModResult(text=_scrub(output))


def _load_skill(name: str) -> ModResult:
    path = Path(SKILLS_DIR) / f"{name}.md"
    if not path.exists():
        return ModResult(text=f"[ERROR] Skill '{name}' not found.")
    return ModResult(text=path.read_text(encoding="utf-8"))


def _handle_memory(data: dict) -> ModResult:
    op      = data.get("op", "").strip().lower()
    content = data.get("content", "").strip()
    if op == "write":
        if not content:
            return ModResult(text="[ERROR] memory write requires <content>.")
        write_memory(content)
        return ModResult(text="Memory written.")
    elif op == "read":
        mem = read_memory()
        return ModResult(text=mem if mem else "(memory is empty)")
    return ModResult(text=f"[ERROR] Unknown memory op '{op}'.")


def _execute_action(action: Action) -> tuple[ModResult, bool]:
    """
    Execute an action and return (ModResult, is_done).

    ModResult carries text output and optional image attachments.
    The actor node uses this to build multimodal LLM messages
    when images are present.
    """
    if action.type == "shell":
        cmd = action.data.get("command", "").strip()
        if not cmd:
            return ModResult(text="[ERROR] shell requires <command>."), False
        return _run_shell(cmd), False
    elif action.type == "skill":
        name = action.data.get("n", "").strip()
        if not name:
            return ModResult(text="[ERROR] skill requires <n>name</n>."), False
        return _load_skill(name), False
    elif action.type == "memory":
        return _handle_memory(action.data), False
    elif action.type == "done":
        msg = action.data.get("message", "").strip()
        return ModResult(text=msg), True
    return ModResult(text=f"[ERROR] Unknown action type '{action.type}'."), False


# ── Auto-verify helpers ──────────────────────────────────────────────────────

_WRITE_TARGET_RE = re.compile(
    r"""
      (?:printf\s+.*?>\s*)([^\s;|&]+)
    | (?:cat\s+>\s*)([^\s;|&<]+)
    | (?:tee\s+)([^\s;|&]+)
    """,
    re.VERBOSE,
)


def _extract_write_target(commands: list[str]) -> str | None:
    for cmd in commands:
        m = _WRITE_TARGET_RE.search(cmd)
        if m:
            return next(g for g in m.groups() if g is not None)
    return None


# ── Mod detection ────────────────────────────────────────────────────────────

def _is_mod_command(command: str) -> bool:
    router = _get_mod_router()
    first_token = command.strip().split()[0].lower() if command.strip() else ""
    return first_token in router.registered


# ── Conversational detection ─────────────────────────────────────────────────

_TASK_KEYWORDS = (
    "read", "write", "create", "make", "delete", "remove", "run",
    "execute", "edit", "modify", "fix", "build", "install", "show me",
    "find", "search", "list", "open", "save", "update", "check",
    "file", "script", "directory", "folder", "code", "command",
    "download", "upload", "copy", "move", "rename", "compile",
    "deploy", "test", "debug", "log", "parse", "generate", "setup",
    "look up", "google", "web search", "search for", "search the",
    "what is", "what's", "how to", "how do",
    "remember", "recall", "memory", "forget",
    "ui", "gui", "window", "screen", "click", "launch", "display",
    ".py", ".txt", ".md", ".js", ".json", ".sh", ".csv", ".html",
)


def _user_wants_action(messages: list[dict]) -> bool:
    for msg in messages:
        if msg.get("role") == "user":
            content = msg.get("content", "")
            # Multimodal messages have content as a list of blocks —
            # extract text from the text blocks only.
            if isinstance(content, list):
                content = " ".join(
                    block.get("text", "") for block in content
                    if isinstance(block, dict) and block.get("type") == "text"
                )
            if not isinstance(content, str):
                continue
            if "Before acting, use <think>" in content:
                continue
            lower = content.lower()
            if any(kw in lower for kw in _TASK_KEYWORDS):
                return True
    return False


# ── Premature summary detection ──────────────────────────────────────────────

_PREMATURE_RE = re.compile(
    r"(?:^|\n)\s*(?:"
    r"yes\b.*?\bi (?:read|wrote|created|made|deleted|edited|built|ran|executed|fixed|updated|searched|found|clicked|launched)"
    r"|done\b"
    r"|all done\b"
    r"|i've completed"
    r"|i have completed"
    r"|i've finished"
    r"|successfully created"
    r"|successfully wrote"
    r"|successfully built"
    r"|here's a summary"
    r"|here is a summary"
    r"|task complete"
    r"|created [`'\"]"
    r"|i(?:'ve| have) (?:read|written|created|made|deleted|removed|edited|set up|searched|found|clicked|launched)"
    r")",
    re.IGNORECASE,
)


# ── Multimodal message builder ───────────────────────────────────────────────

def _build_message(text: str, images: list[bytes]) -> dict:
    """
    Build a user message dict, using multimodal content blocks when
    images are present.  This is generic — any mod that returns images
    gets them included automatically.

    Handles provider-specific image formats:
      Anthropic: {"type": "image", "source": {"type": "base64", ...}}
      OpenAI:    {"type": "image_url", "image_url": {"url": "data:image/png;base64,..."}}

    When no images: {"role": "user", "content": "text"}
    With images:    {"role": "user", "content": [text_block, img_block, ...]}
    """
    if not images:
        return {"role": "user", "content": text}

    # Filter out invalid images — must be a real PNG with content.
    # A valid PNG starts with the 8-byte magic header.
    _PNG_HEADER = b"\x89PNG\r\n\x1a\n"
    valid_images = [
        img for img in images
        if img and len(img) > 1000 and img[:8] == _PNG_HEADER
    ]

    if not valid_images:
        return {"role": "user", "content": text}

    blocks: list[dict] = [{"type": "text", "text": text}]
    for img_bytes in valid_images:
        b64 = base64.b64encode(img_bytes).decode("ascii")
        blocks.append(_image_block(b64))
    return {"role": "user", "content": blocks}


def _image_block(b64_data: str) -> dict:
    """Build a provider-appropriate image content block."""
    if ACTIVE_PROVIDER == "openai":
        return {
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{b64_data}",
                "detail": "low",
            },
        }
    # Anthropic (and default fallback)
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": "image/png",
            "data": b64_data,
        },
    }


# ── Streaming call helper ─────────────────────────────────────────────────────

def _stream_call(agent, messages: list[dict], system: str) -> str:
    print(f"\n{_DIM}[agent]{_RESET} ", end="", flush=True)
    chunks: list[str] = []
    for chunk in agent.stream(messages, system, ACTIVE_TIER):
        print(".", end="", flush=True)
        chunks.append(chunk)
    print(flush=True)
    return "".join(chunks)


# ── Node: planner ─────────────────────────────────────────────────────────────

def planner(state: AgentState, agent) -> dict:
    planning_prompt = (
        "Before acting, use <think> to reason about the task internally, "
        "then use <plan> to list the concrete steps you will take. "
        "The LAST step of every plan must always be a respond step — "
        "e.g. 'Summarise what was built and confirm completion to the user' "
        "or 'Reply to the user with the answer'. "
        "If the message is purely conversational (greeting, thanks, small talk "
        "with NO task implied) the entire plan can be just that one respond step.\n\n"
        "IMPORTANT: 'Can you [task]?' or 'Could you [task]?' is a TASK REQUEST, "
        "not a question. Plan the actual steps to do the task — do NOT plan to "
        "'reply asking if they want me to do it'. Just plan to DO IT.\n\n"
        "Do not act yet — the plan is all that is needed from you right now."
    )

    messages = list(state["messages"]) + [
        {"role": "user", "content": planning_prompt}
    ]

    raw = _stream_call(agent, messages, state["system"])
    _, _, thinks, plans, works = parse_response(raw)

    for t in thinks:
        _print_think(t.content)
    for p in plans:
        _print_plan(p.steps)

    steps = plans[0].steps if plans else []

    return {
        "messages": [
            {"role": "user",      "content": planning_prompt},
            {"role": "assistant", "content": raw},
        ],
        "plan": steps,
    }


# ── Node: actor ───────────────────────────────────────────────────────────────

def actor(state: AgentState, agent, loaded_skills: set[str]) -> dict:
    messages = list(state["messages"])

    plan = state.get("plan", [])
    plan_text = " ".join(plan).lower()

    user_has_task = _user_wants_action(state["messages"])

    is_conversational = (
        not user_has_task
        and (
            len(plan) == 0
            or (
                len(plan) == 1
                and any(w in plan_text for w in ("reply", "respond", "greet", "answer", "acknowledge"))
            )
        )
    )

    if is_conversational:
        user_messages = [m for m in messages if m.get("role") == "user"
                         and "Before acting, use <think>" not in m.get("content", "")]
        messages = user_messages[-1:] if user_messages else messages

    elif state["actor_turn"] == 0:
        execute_prompt = (
            "The plan is set. Execute it now.\n\n"
            "RULES:\n"
            "1. For conversational replies — just write plain text. No shell commands.\n"
            "2. Emit shell actions FIRST for real tasks. No summary until results are back.\n"
            "3. To write FILE CONTENT use printf (NOT heredoc, NOT echo for multi-line):\n"
            "   <action type=\"shell\"><command>printf 'line one\\nline two\\n' > filename.txt</command></action>\n"
            "   printf is ONLY for writing to files — never use it to print your reply to the user.\n"
            "   After writing, verify with: cat filename.txt\n"
            "4. Chain all actions back to back. Use <work> before each step.\n"
            "5. Do not re-plan. Start acting immediately.\n"
            "6. NEVER say 'I have done X' or 'Created X' until AFTER you have seen\n"
            "   the command results. Text summary comes ONLY in a later turn after\n"
            "   results are back."
        )
        messages = messages + [{"role": "user", "content": execute_prompt}]

    raw = _stream_call(agent, messages, state["system"])
    reasoning, actions, thinks, plans, works = parse_response(raw)

    for t in thinks:
        _print_think(t.content)
    for p in plans:
        _print_plan(p.steps)
    for w in works:
        _print_work(w.content)

    # ── Implicit done: no actions ─────────────────────────────────────────
    if not actions:
        if reasoning:
            print(f"\n{reasoning}", flush=True)
        return {
            "messages":     [{"role": "assistant", "content": raw}],
            "actor_turn":   state["actor_turn"] + 1,
            "done":         True,
            "last_actions": [],
        }

    # ── Separate done from work actions ───────────────────────────────────
    work_actions = [a for a in actions if a.type != "done"]
    done_action  = next((a for a in actions if a.type == "done"), None)

    # Deduplicate
    seen_actions: set[str] = set()
    deduped: list = []
    for a in work_actions:
        sig = f"{a.type}:{a.data}"
        if sig not in seen_actions:
            seen_actions.add(sig)
            deduped.append(a)
    work_actions = deduped

    # Suppress premature summaries
    if reasoning:
        should_suppress = False
        if work_actions:
            should_suppress = bool(_PREMATURE_RE.search(reasoning))
        if not should_suppress:
            print(f"\n{reasoning}", flush=True)

    result_parts: list[str] = []
    all_images: list[bytes] = []

    for action in work_actions:
        result, _ = _execute_action(action)

        # Collect images from any mod that returned them
        all_images.extend(result.images)

        if action.type == "shell":
            cmd = action.data.get("command", "")
            if _is_mod_command(cmd):
                mod_name = cmd.strip().split()[0]
                print(f"\n{_BLUE}[{mod_name}]{_RESET} {cmd}")
                lines = result.text.strip().splitlines()
                if len(lines) > 20:
                    preview = "\n".join(lines[:5])
                    print(f"{preview}\n  ... ({len(lines)} lines total)", flush=True)
                else:
                    print(result.text, flush=True)
                if result.images:
                    print(f"  {_DIM}[{len(result.images)} image(s) attached]{_RESET}", flush=True)
            else:
                print(f"\n{_GREEN}${_RESET} {cmd}")
                print(result.text, flush=True)

        elif action.type == "skill" and not result.text.startswith("[ERROR]"):
            name = action.data.get("n", "")
            if name not in loaded_skills:
                print(f"\n[skill loaded: {name}]", flush=True)
                loaded_skills.add(name)
            else:
                result = ModResult(text=f"(skill '{name}' already in context)")

        result_parts.append(format_result(action, result.text))

    done = done_action is not None

    # Cancel done if emitted alongside work actions
    if done and work_actions:
        done = False
        done_action = None

    # ── Auto-verify writes ────────────────────────────────────────────────
    commands_run = [
        a.data.get("command", "") for a in work_actions if a.type == "shell"
    ]
    real_shell_commands = [c for c in commands_run if not _is_mod_command(c)]

    write_happened = any(
        any(w in cmd for w in ("printf ", "cat >", "cat>", "tee "))
        for cmd in real_shell_commands
    )
    readback_happened = any(
        cmd.strip().startswith("cat ") and ">" not in cmd
        for cmd in real_shell_commands
    )

    if write_happened and not readback_happened:
        filename = _extract_write_target(real_shell_commands)

        if filename:
            verify_cmd = f"cat {filename}"
            verify_result = _run_shell(verify_cmd)
            print(f"\n{_GREEN}${_RESET} {verify_cmd}")
            print(verify_result.text, flush=True)
            result_parts.append(format_result(
                Action(type="shell", data={"command": verify_cmd}),
                verify_result.text,
            ))
            done = False
            done_action = None

    closing = ""
    if done and done_action:
        closing = done_action.data.get("message", "").strip()
        if closing:
            print(f"\n{closing}", flush=True)

    result_text = "\n\n".join(result_parts)
    new_messages: list[dict] = [{"role": "assistant", "content": raw}]
    if result_text:
        plan_mentions_write = any(
            w in " ".join(state.get("plan", [])).lower()
            for w in ("write", "create", "save", "add", "insert", "put")
        )
        results_have_write = any(
            any(w in cmd for w in ("cat >", "cat>", "tee ", "printf ", ">> "))
            for cmd in real_shell_commands
        )
        write_warning = ""
        if plan_mentions_write and not results_have_write and real_shell_commands:
            write_warning = (
                "\n\nWARNING: Your plan included writing/creating something but "
                "no write command appeared in the actions above. "
                "You must emit a shell action to actually write the file."
            )

        skills_just_loaded = [
            a.data.get("n", "") for a in work_actions
            if a.type == "skill" and a.data.get("n", "") != ""
        ]
        skill_reminder = ""
        if skills_just_loaded:
            skill_reminder = (
                f"\nThe skill(s) {skills_just_loaded} are now loaded. "
                "Use them — emit the shell actions needed to complete the task.\n"
            )

        continue_text = (
            f"{result_text}{write_warning}{skill_reminder}\n\n"
            "Continue. Rules:\n"
            "- Remaining steps: emit actions now, no summary yet.\n"
            "- NEVER say 'I have done X' until you have seen the results above.\n"
            "- After ALL steps done and results confirmed: write a plain-text summary "
            "then <action type=\"done\"/>."
        )

        # Build multimodal message if any action returned images.
        # This is generic — not specific to any mod.
        new_messages.append(_build_message(continue_text, all_images))

    # ── Embed this exchange into ChromaDB ─────────────────────────────────
    # Fire on every actor turn so intermediate work steps are also retrievable.
    # The "intermediate" flag lets callers filter for final-only results if needed.
    try:
        original_user = next(
            (m["content"] for m in state["messages"]
             if m["role"] == "user" and isinstance(m["content"], str)),
            "",
        )
        assistant_reply = (reasoning or closing) if done else reasoning
        if original_user and assistant_reply:
            embed_conversation_turn(
                user=original_user,
                assistant=assistant_reply,
                metadata={"actor_turn": state["actor_turn"], "intermediate": not done},
            )
    except Exception as e:
        import sys
        print(f"[warn] actor turn embedding failed: {e}", file=sys.stderr)

    return {
        "messages":     new_messages,
        "actor_turn":   state["actor_turn"] + 1,
        "done":         done,
        "last_actions": result_parts,
    }


# ── Node: reflector ───────────────────────────────────────────────────────────

def reflector(state: AgentState) -> dict:
    return {}


def should_continue(state: AgentState) -> str:
    if state["done"]:
        return "end"
    if GRAPH_TURN_LIMIT is not None and state["actor_turn"] >= GRAPH_TURN_LIMIT:
        print(f"\n[graph] Turn limit reached ({GRAPH_TURN_LIMIT}). Stopping.", flush=True)
        return "end"
    return "actor"