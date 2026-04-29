"""
engine/nodes.py — LangGraph node functions: planner, actor, reflector, replanner.

V2 changes:
  - planner uses soul_planner.md + core_ref.md, writes plan via PlanManager
  - actor uses soul_worker.md + core_ref.md, resets worker context each invocation
  - replanner handles escalations, injects steps, or surfaces question to user
  - reflector/should_continue checks state["blocked"] and state["escalation"]
"""

from __future__ import annotations

import re
from pathlib import Path

from config import (
    CONVERSATIONAL_PLAN_KEYWORDS,
    GRAPH_TURN_LIMIT,
    RAG_CANDIDATE_K,
    RAG_MIN_SCORE,
    SKILLS_DIR,
)
from core.context_window import ContextWindow
from core.log import log
from core.xml_parser import (
    Action,
    format_result,
    parse_response,
)
from engine.media import (
    MediaAttachment,
    build_message as _build_media_message,
    strip_attachments_from_history,
    strip_all_but_last_image,
    strip_images_if_over_budget,
)
from engine.mod_api import ModResult
from engine.plan_manager import PlanManager
from engine.sandbox import run_command
from engine.state import AgentState
from memory.memory import write_memory, read_memory
from memory.embedder import embed_conversation_turn
from memory.rag import MemoryRetriever
from engine.mcp_router import MCPRouter


# ── ANSI colours ──────────────────────────────────────────────────────────────

_RESET  = "\033[0m"
_DIM    = "\033[2m"
_CYAN   = "\033[36m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_BOLD   = "\033[1m"
_BLUE   = "\033[34m"
_RED    = "\033[31m"


# ── Singletons ────────────────────────────────────────────────────────────────

# MCPRouter is injected by AgentLoop via build_graph().
# Fallback singleton used if called outside that context (e.g. tests).
_mcp_router: MCPRouter | None = None


def _get_mod_router() -> MCPRouter:
    global _mcp_router
    if _mcp_router is None:
        _mcp_router = MCPRouter()
        _mcp_router.connect_all()
    return _mcp_router


def set_mcp_router(router: MCPRouter) -> None:
    """Called by AgentLoop to inject the session-level MCPRouter."""
    global _mcp_router
    _mcp_router = router


# ── Session-level plan manager (one per session) ──────────────────────────────
# Nodes receive this via closure through the graph — but we use a module-level
# instance because plan state needs to persist across multiple graph invocations
# within the same session.
_plan_manager: PlanManager | None = None
_current_session: str | None = None  # Glass AI conversation ID for the running task


def _get_plan_manager() -> PlanManager:
    global _plan_manager
    if _plan_manager is None:
        from config import PROJECT_DIR
        _plan_manager = PlanManager(workspace=PROJECT_DIR if PROJECT_DIR else None)
    return _plan_manager


# ── Display helpers ───────────────────────────────────────────────────────────

def _print_think(content: str) -> None:
    for line in content.splitlines():
        if line.strip():
            print(f"[thinking] {line}", flush=True)

def _print_plan(steps: list[str]) -> None:
    for i, step in enumerate(steps, 1):
        print(f"[plan] {i}. {step}", flush=True)

def _print_work(content: str) -> None:
    print(f"\n{_YELLOW}[work]{_RESET} {content}", flush=True)

def _print_escalation(reason: str, level: str) -> None:
    color = _RED if level == "user" else _YELLOW
    print(f"\n{color}{_BOLD}[blocked]{_RESET} {reason}", flush=True)


# ── Shell / skill / memory helpers ────────────────────────────────────────────

def _run_shell(command: str) -> ModResult:
    try:
        from mods.passwd.cache import interpolate, scrub
        command = interpolate(command)
        _scrub = scrub
    except Exception:
        _scrub = lambda t: t  # noqa: E731

    router = _get_mod_router()
    hit, result = router.try_handle(command)
    if hit:
        return ModResult(text=_scrub(result.text), attachments=result.attachments)

    # Detect mod commands buried in compound commands (&&, ;, ||, |).
    # Only check the FIRST TOKEN of each pipeline/chain segment — a mod name
    # that appears as a path component or argument is NOT an interception target.
    if router.registered:
        for seg in re.split(r'(?:&&|\|\||[;|])', command)[1:]:
            first = seg.strip().split()[0].lower() if seg.strip() else ""
            if first in router.registered:
                return ModResult(text=(
                    f"[ERROR] '{first}' is an intercepted mod command — it does not exist "
                    f"in the sandbox shell. It MUST be the only command in its shell action. "
                    f"Do not chain it with &&, ;, ||, or |. "
                    f"Use a separate <action type=\"shell\"> for every mod command call."
                ))

    output = run_command(command)
    return ModResult(text=_scrub(output))


def _load_skill(name: str) -> ModResult:
    base = Path(SKILLS_DIR)
    hyphen = name.replace("_", "-")
    for filename in (f"{name}.md", f"skill-{hyphen}.md", f"{hyphen}.md"):
        path = base / filename
        if path.exists():
            return ModResult(text=path.read_text(encoding="utf-8"))
    return ModResult(text=f"[ERROR] Skill '{name}' not found.")


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


def _handle_plan_action(data: dict) -> ModResult:
    """Execute a <action type='plan'> operation via PlanManager."""
    pm  = _get_plan_manager()
    op  = data.get("op", "").strip().lower()

    if not op:
        return ModResult(text="(plan action had no op — skipped)")

    if op == "write":
        title    = data.get("title", "Untitled task").strip()
        raw_steps = data.get("steps", "")
        # Parse numbered or plain newline-separated steps
        step_lines = []
        for line in raw_steps.strip().splitlines():
            line = re.sub(r"^\s*\d+[\.\)]\s*", "", line).strip()
            if line:
                step_lines.append(line)
        task_id = pm.write_plan(title=title, steps=step_lines, session=_current_session)
        return ModResult(text=f"Plan written. task_id: {task_id}")

    elif op == "step_done":
        n = int(data.get("step", "1") or "1")
        pm.step_done(n)
        return ModResult(text=f"Step {n} marked complete.")

    elif op == "note":
        content = data.get("content", "").strip()
        if content:
            pm.add_note(content)
        return ModResult(text="Note added.")

    elif op == "read":
        return ModResult(text=pm.read_plan())

    elif op == "status":
        value = data.get("value", "active").strip()
        pm.set_status(value)
        return ModResult(text=f"Plan status set to '{value}'.")

    elif op == "list":
        plans = pm.list_plans()
        if not plans:
            return ModResult(text="(no plans found)")
        lines = []
        for p in plans:
            lines.append(
                f"  [{p.get('status', '?')}] {p.get('task_id', '?')} — {p.get('title', '')}"
            )
        return ModResult(text="\n".join(lines))

    elif op == "resume":
        task_id = data.get("task_id", "").strip()
        content = pm.resume(task_id)
        return ModResult(text=content)

    return ModResult(text=f"[ERROR] Unknown plan op '{op}'.")


def _execute_action(action: Action) -> tuple[ModResult, bool]:
    """
    Execute an action and return (ModResult, is_done).
    """
    if action.type == "shell":
        cmd = action.data.get("command", "").strip()
        if not cmd:
            return ModResult(text="[ERROR] shell requires <command>."), False
        return _run_shell(cmd), False
    elif action.type == "skill":
        op = action.data.get("op", "load")
        if op == "load":
            name = action.data.get("n", "").strip()
            if not name:
                return ModResult(text="[ERROR] skill requires <n>name</n>."), False
            return _load_skill(name), False
        # search / request_creation handled by planner node directly
        return ModResult(text=f"[ERROR] skill op '{op}' not valid in actor context."), False
    elif action.type == "memory":
        return _handle_memory(action.data), False
    elif action.type == "plan":
        return _handle_plan_action(action.data), False
    elif action.type == "done":
        msg = action.data.get("message", "").strip()
        return ModResult(text=msg), True
    elif action.type == "escalate":
        # Handled at node level — not dispatched here
        return ModResult(text="[escalation received]"), False
    return ModResult(text=f"[ERROR] Unknown action type '{action.type}'."), False


# ── Skill search helper (planner only) ───────────────────────────────────────

def _skill_search(query: str) -> str:
    """Search skills directory by simple keyword match (Phase 1 fallback)."""
    skills_path = Path(SKILLS_DIR)
    if not skills_path.exists():
        return "(no skills available)"
    results = []
    q = query.lower()
    for md_file in sorted(skills_path.glob("*.md")):
        try:
            text = md_file.read_text(encoding="utf-8")
        except Exception:
            continue
        if q in text.lower() or q in md_file.stem.lower():
            # Extract name and description from frontmatter
            skill_name = md_file.stem
            desc = skill_name
            if text.startswith("---"):
                end = text.find("---", 3)
                if end != -1:
                    for line in text[3:end].splitlines():
                        if line.startswith("name:"):
                            skill_name = line[len("name:"):].strip()
                        elif line.startswith("description:"):
                            desc = line[len("description:"):].strip()
            results.append(f"  - {skill_name}: {desc}")
    if not results:
        return f"(no skills matched '{query}')"
    return "\n".join(results)


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

_TRIVIAL_STEP_WORDS = ("reply", "respond", "greet", "answer", "acknowledge")


def _is_trivial_plan(steps: list[str]) -> bool:
    """True for single-step conversational plans that need no persistent file."""
    if len(steps) != 1:
        return False
    s = steps[0].lower()
    return any(w in s for w in _TRIVIAL_STEP_WORDS)


def _readable_mod_output(text: str) -> str:
    """Strip image/binary blocks from JSON mod output; keep only text content."""
    import json as _json
    stripped = text.strip()
    if not (stripped.startswith("[") or stripped.startswith("{")):
        return text
    try:
        blocks = _json.loads(stripped)
        if isinstance(blocks, list):
            parts = [
                b.get("text", "")
                for b in blocks
                if isinstance(b, dict) and b.get("type") == "text"
            ]
            return "\n".join(p for p in parts if p) or text
        if isinstance(blocks, dict) and blocks.get("type") == "text":
            return blocks.get("text", text)
    except Exception:
        pass
    return text


def _is_mod_command(command: str) -> bool:
    router = _get_mod_router()
    first_token = command.strip().split()[0].lower() if command.strip() else ""
    return first_token in router.registered  # works for both ModRouter and MCPRouter


# ── Provider helpers ──────────────────────────────────────────────────────────

def _worker_provider() -> str:
    """Return the provider name for the worker agent (used by media pipeline)."""
    from config import AGENTS
    return AGENTS["worker"]["provider"]


# ── Streaming call helper ─────────────────────────────────────────────────────

def _stream_call(agent, messages: list[dict], system: str) -> str:
    print("[work] Generating…", flush=True)
    chunks: list[str] = []
    for chunk in agent.stream(messages, system):
        chunks.append(chunk)
    return "".join(chunks)


# ── Node: planner ─────────────────────────────────────────────────────────────

def planner(
    state: AgentState,
    agent,
    ctx: ContextWindow,
    soul: str,
    core_ref: str,
) -> dict:
    """
    Planner node — reasons, discovers skills, writes plan.

    Uses soul_planner.md + core_ref.md for system prompt.
    Runs a skill-discovery loop (max 3 iterations) before writing the plan.
    Writes the plan to disk via PlanManager.
    """
    from engine.loop import _build_planner_system_prompt

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
        "IMPORTANT: GUI sessions (browser, app) do NOT persist between agent sessions. "
        "'Open a browser' always means LAUNCH it fresh — never plan to 'confirm it is open' "
        "or 'check if it is running'. Always plan to START it.\n\n"
        "Only persist the plan with <action type=\"plan\"> if the task requires "
        "REAL WORK (shell commands, file operations, web search, memory writes, "
        "multi-step execution). DO NOT persist for purely conversational replies "
        "(greetings, thanks, small talk, or a single 'respond to user' step).\n\n"
        "When persistence IS needed:\n"
        "<action type=\"plan\"><op>write</op><title>Task title</title>"
        "<steps>1. first step\n2. next step</steps></action>\n\n"
        "Do not act yet — the plan is all that is needed from you right now."
    )

    messages = strip_attachments_from_history(list(state["messages"])) + [
        {"role": "user", "content": planning_prompt}
    ]

    system = _build_planner_system_prompt(ctx, soul, core_ref)

    # ── Skill discovery loop (max 3 iterations) ───────────────────────────────
    skill_loop_messages = list(messages)
    for _iteration in range(3):
        raw = _stream_call(agent, skill_loop_messages, system)
        _, actions, thinks, plans, works = parse_response(raw)

        for t in thinks:
            _print_think(t.content)
        for p in plans:
            _print_plan(p.steps)

        # Check for skill search or request_creation actions
        skill_actions = [
            a for a in actions
            if a.type == "skill" and a.data.get("op") in ("search", "request_creation")
        ]

        if not skill_actions:
            # No skill discovery needed — break out with this response
            break

        # Execute skill actions and append results
        result_parts = []
        for sa in skill_actions:
            op = sa.data.get("op")
            if op == "search":
                query   = sa.data.get("query", "")
                results = _skill_search(query)
                result_parts.append(
                    format_result(sa, f"Skill search results for '{query}':\n{results}")
                )
            elif op == "request_creation":
                name   = sa.data.get("name", "")
                reason = sa.data.get("reason", "")
                result_parts.append(
                    format_result(sa, f"Skill '{name}' requested. Reason: {reason}\n"
                                      f"(Not yet created — continue planning without it.)")
                )

        skill_loop_messages = skill_loop_messages + [
            {"role": "assistant", "content": raw},
            {"role": "user",      "content": "\n\n".join(result_parts) + "\n\nContinue planning."},
        ]
    else:
        # Loop exhausted — use the last raw response
        _, actions, thinks, plans, works = parse_response(raw)
        for t in thinks:
            _print_think(t.content)
        for p in plans:
            _print_plan(p.steps)

    steps = plans[0].steps if plans else []

    # ── Execute plan write action if present (skip for trivial/conversational) ─
    plan_actions = [a for a in actions if a.type == "plan" and a.data.get("op") == "write"]
    if not _is_trivial_plan(steps):
        if plan_actions:
            for pa in plan_actions:
                _handle_plan_action(pa.data)
        elif steps:
            # Model emitted <plan> steps but omitted <action type="plan"><op>write</op>.
            # Auto-persist so the actor has a current_step to execute against.
            user_text = next(
                (m["content"] for m in reversed(state["messages"])
                 if m.get("role") == "user" and isinstance(m.get("content"), str)
                 and "Before acting, use <think>" not in m["content"]),
                "Task",
            )
            title = user_text[:60].strip()
            _get_plan_manager().write_plan(title=title, steps=steps, session=_current_session)

    # If planner wrote a plan action, also update state steps
    if plan_actions and steps:
        pass  # steps already extracted from <plan> tag

    new_messages: list[dict] = [
        {"role": "user",      "content": planning_prompt},
        {"role": "assistant", "content": raw},
    ]

    return {
        "messages":   new_messages,
        "plan":       steps,
        "plan_step":  0,
    }


# ── Node: actor ───────────────────────────────────────────────────────────────

def actor(
    state:        AgentState,
    agent,
    worker_ctx:   ContextWindow,
    soul:         str,
    core_ref:     str,
    loaded_skills: set[str],
) -> dict:
    """
    Actor node — executes the current plan step.

    Worker context resets each invocation — re-seeded with:
      - project log (compact summary from plan file)
      - current step instruction
      - RAG memory hits

    Uses soul_worker.md + core_ref.md for system prompt.
    Handles new <action type="escalate"> to signal the replanner.
    """
    from engine.loop import _build_worker_system_prompt

    pm = _get_plan_manager()

    # ── Reset and re-seed worker context ──────────────────────────────────────
    worker_ctx.clear()
    project_log   = pm.generate_project_log()
    current_step  = pm.current_step_text()

    # RAG hits for current step
    rag_pages: list[tuple[str, float]] = []
    if current_step:
        try:
            rag = MemoryRetriever(min_score=RAG_MIN_SCORE)
            rag_pages = rag.retrieve(current_step, top_k=RAG_CANDIDATE_K)
        except Exception as e:
            log.error(f"worker RAG retrieval failed: {e}", source="actor")

    system = _build_worker_system_prompt(
        project_log=project_log or "(no plan active yet)",
        current_step=current_step or "(no current step)",
        rag_pages=rag_pages,
        soul_worker=soul,
        core_ref=core_ref,
    )

    # ── Build message list ────────────────────────────────────────────────────
    # Keep at most 1 image (the most recent screenshot) — strip the rest.
    # Then drop that image too if the total token estimate exceeds the budget.
    from config import PROVIDER_CONTEXT_LIMIT
    messages = strip_all_but_last_image(list(state["messages"]))
    messages = strip_images_if_over_budget(messages, system, PROVIDER_CONTEXT_LIMIT)
    plan     = state.get("plan", [])
    plan_text = " ".join(plan).lower()

    no_step = not current_step.strip()
    plan_is_conversational = len(plan) == 0 or (
        len(plan) == 1
        and any(w in plan_text for w in CONVERSATIONAL_PLAN_KEYWORDS)
    )
    is_conversational = no_step or plan_is_conversational

    if is_conversational:
        user_messages = [
            m for m in messages
            if m.get("role") == "user"
            and "Before acting, use <think>" not in m.get("content", "")
        ]
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
            "   results are back.\n"
            "7. If you are BLOCKED and cannot continue, use:\n"
            "   <action type=\"escalate\"><level>planner</level><reason>specific reason</reason>"
            "<need>clarification|research|skill</need></action>\n"
            "8. Before using any mod command (from the Mod commands list in your system prompt), "
            "load its skill first — it contains exact usage and important constraints:\n"
            "   <action type=\"skill\"><n>command_name</n></action>"
        )
        messages = messages + [{"role": "user", "content": execute_prompt}]

    raw = _stream_call(agent, messages, system)
    reasoning, actions, thinks, plans, works = parse_response(raw)

    for t in thinks:
        _print_think(t.content)
    for p in plans:
        _print_plan(p.steps)
    for w in works:
        _print_work(w.content)

    # ── Check for escalation ──────────────────────────────────────────────────
    escalate_action = next((a for a in actions if a.type == "escalate"), None)
    if escalate_action:
        level  = escalate_action.data.get("level", "planner")
        reason = escalate_action.data.get("reason", "")
        need   = escalate_action.data.get("need", "clarification")

        _print_escalation(reason, level)

        escalation = {"level": level, "reason": reason, "need": need}

        return {
            "messages":   [{"role": "assistant", "content": raw}],
            "actor_turn": state["actor_turn"] + 1,
            "done":       False,
            "blocked":    True,
            "escalation": escalation,
            "last_actions": [],
        }

    # ── Implicit done: no actions ─────────────────────────────────────────────
    if not actions:
        if reasoning:
            print(f"\n{reasoning}", flush=True)
        return {
            "messages":     [{"role": "assistant", "content": raw}],
            "actor_turn":   state["actor_turn"] + 1,
            "done":         True,
            "blocked":      False,
            "escalation":   None,
            "last_actions": [],
        }

    # ── Separate done from work actions ───────────────────────────────────────
    work_actions = [a for a in actions if a.type not in ("done", "escalate")]
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
    all_attachments: list[MediaAttachment] = []

    for action in work_actions:
        # Auto-inject skill on first mod command use so the worker knows full syntax.
        if action.type == "shell":
            cmd = action.data.get("command", "")
            if _is_mod_command(cmd):
                mod_name = cmd.strip().split()[0].lower()
                if mod_name not in loaded_skills:
                    skill_result = _load_skill(mod_name)
                    if not skill_result.text.startswith("[ERROR]"):
                        loaded_skills.add(mod_name)
                        print(f"\n[skill auto-loaded: {mod_name}]", flush=True)
                        result_parts.append(format_result(
                            Action(type="skill", data={"n": mod_name}),
                            skill_result.text,
                        ))

        result, _ = _execute_action(action)
        all_attachments.extend(result.attachments)

        if action.type == "shell":
            cmd = action.data.get("command", "")
            if _is_mod_command(cmd):
                mod_name = cmd.strip().split()[0]
                print(f"[shell] [{mod_name}] {cmd}", flush=True)
                display = _readable_mod_output(result.text)
                out_lines = display.strip().splitlines()
                for line in out_lines[:40]:
                    if line.strip():
                        print(f"[detail] {line}", flush=True)
                if len(out_lines) > 40:
                    print(f"[detail] … ({len(out_lines) - 40} more lines)", flush=True)
                if result.attachments:
                    print(f"[detail] [{len(result.attachments)} attachment(s)]", flush=True)
            else:
                print(f"\n{_GREEN}${_RESET} {cmd}", flush=True)
                for line in result.text.strip().splitlines():
                    if line.strip():
                        print(f"[detail] {line}", flush=True)

        elif action.type == "skill" and not result.text.startswith("[ERROR]"):
            name = action.data.get("n", "")
            if name not in loaded_skills:
                print(f"[work] Loaded skill: {name}", flush=True)
                loaded_skills.add(name)
            else:
                result = ModResult(text=f"(skill '{name}' already in context)")

        result_parts.append(format_result(action, result.text))

    done = done_action is not None

    if done and work_actions:
        done = False
        done_action = None

    # ── Auto-verify writes ────────────────────────────────────────────────────
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
            verify_cmd    = f"cat {filename}"
            verify_result = _run_shell(verify_cmd)
            print(f"\n{_GREEN}${_RESET} {verify_cmd}", flush=True)
            for line in verify_result.text.strip().splitlines():
                if line.strip():
                    print(f"[detail] {line}", flush=True)
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

        screenshot_hint = ""
        if all_attachments:
            screenshot_hint = (
                "\n- Screenshot(s) attached above. Describe what you see on screen "
                "(text, buttons, URL bar, page content). Use that to determine "
                "coordinates and next actions. For browser navigation you do NOT need "
                "to locate the address bar visually — just use the keyboard shortcut "
                "to focus it, type the URL, and press Return."
            )

        continue_text = (
            f"{result_text}{write_warning}{skill_reminder}\n\n"
            "Continue. Rules:\n"
            "- Remaining steps: emit actions now, no summary yet.\n"
            "- NEVER say 'I have done X' until you have seen the results above.\n"
            f"- After ALL steps done and results confirmed: write a plain-text summary "
            f"then <action type=\"done\"/>."
            f"{screenshot_hint}"
        )
        provider = _worker_provider()
        # Only send the last screenshot — sending all of them blows past token limits.
        last_attachments = all_attachments[-1:] if all_attachments else []
        new_messages.append(_build_media_message(continue_text, last_attachments, provider))

    # ── Embed this exchange into ChromaDB ─────────────────────────────────────
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
        log.error(f"actor turn embedding failed: {e}", source="nodes")

    return {
        "messages":     new_messages,
        "actor_turn":   state["actor_turn"] + 1,
        "done":         done,
        "blocked":      False,
        "escalation":   None,
        "last_actions": result_parts,
    }


# ── Node: reflector ───────────────────────────────────────────────────────────

def reflector(state: AgentState) -> dict:
    return {}


def should_continue(state: AgentState) -> str:
    """Route from reflector: replanner | actor | end."""
    if state.get("blocked") and state.get("escalation"):
        level = state["escalation"].get("level", "planner")
        if level == "user":
            # Surface to user — terminate session gracefully
            return "end"
        return "replanner"

    if state["done"]:
        return "end"

    if GRAPH_TURN_LIMIT is not None and state["actor_turn"] >= GRAPH_TURN_LIMIT:
        print(f"\n[graph] Turn limit reached ({GRAPH_TURN_LIMIT}). Stopping.", flush=True)
        return "end"

    return "actor"


# ── Node: replanner ───────────────────────────────────────────────────────────

def replanner(
    state:   AgentState,
    agent,
    ctx:     ContextWindow,
    soul:    str,
    core_ref: str,
) -> dict:
    """
    Replanner node — handles actor escalations.

    Receives state["escalation"] = {level, reason, need}.
    If can resolve: injects a new step into the plan and clears escalation.
    If cannot resolve: surfaces a specific question to the user and sets done=True.
    """
    from engine.loop import _build_planner_system_prompt

    pm         = _get_plan_manager()
    escalation = state.get("escalation") or {}
    level      = escalation.get("level", "planner")
    reason     = escalation.get("reason", "")
    need       = escalation.get("need", "clarification")
    plan_text  = pm.read_plan()
    step_idx   = pm.current_step_index()
    project_log = pm.generate_project_log()

    replanner_prompt = (
        f"The worker was blocked at step {step_idx}.\n\n"
        f"Reason: {reason}\n"
        f"Need: {need}\n\n"
        f"Current plan:\n{plan_text}\n\n"
        f"Progress so far:\n{project_log}\n\n"
        "Options:\n"
        "1. If you can resolve this internally (add an information-gathering step, "
        "   reframe the current step, etc.) — inject a new step using:\n"
        "   <action type=\"plan\"><op>inject_step</op>"
        f"<after>{step_idx}</after><content>New step text</content></action>\n\n"
        "   Then confirm the worker can continue.\n\n"
        "2. If you genuinely cannot proceed without user input — ask a SPECIFIC "
        "   question using:\n"
        "   <action type=\"escalate\"><level>user</level>"
        "<reason>Precise, answerable question for the user</reason></action>\n\n"
        "   Do not ask vague questions. Give an either/or or name exactly what is missing."
    )

    system   = _build_planner_system_prompt(ctx, soul, core_ref)
    messages = strip_attachments_from_history(list(state["messages"])) + [
        {"role": "user", "content": replanner_prompt}
    ]

    raw = _stream_call(agent, messages, system)
    reasoning, actions, thinks, _, _ = parse_response(raw)

    for t in thinks:
        _print_think(t.content)

    if reasoning:
        print(f"\n{_CYAN}[replanner]{_RESET} {reasoning}", flush=True)

    # ── Check what the replanner decided ─────────────────────────────────────
    user_escalation = next(
        (a for a in actions if a.type == "escalate" and a.data.get("level") == "user"),
        None,
    )

    if user_escalation:
        # Cannot resolve — surface to user
        user_reason = user_escalation.data.get("reason", reason)
        print(f"\n{_RED}{_BOLD}[blocked]{_RESET} {user_reason}", flush=True)
        print(f"{_DIM}(Task is paused. Reply to continue.){_RESET}", flush=True)

        pm.add_note(f"Paused: {user_reason}")
        pm.set_status("paused")

        return {
            "messages":   [{"role": "assistant", "content": raw}],
            "done":       True,
            "blocked":    True,
            "escalation": {"level": "user", "reason": user_reason, "need": "user"},
        }

    # ── Handle inject_step action ─────────────────────────────────────────────
    inject_actions = [
        a for a in actions
        if a.type == "plan" and a.data.get("op") == "inject_step"
    ]
    step_injected = False
    for ia in inject_actions:
        after        = int(ia.data.get("after", str(step_idx)) or str(step_idx))
        content_text = ia.data.get("content", "").strip()
        if content_text:
            pm.inject_step(after_n=after, content_text=content_text)
            print(f"\n{_CYAN}[replanner]{_RESET} Injected step after {after}: {content_text}", flush=True)
            step_injected = True

    # ── Handle regular plan actions (step_done, note, etc.) ───────────────────
    for a in actions:
        if a.type == "plan" and a.data.get("op") not in ("inject_step", "write"):
            _handle_plan_action(a.data)

    if not step_injected:
        # Replanner answered conversationally — no new step to run, task is done.
        return {
            "messages":   [{"role": "assistant", "content": raw}],
            "done":       True,
            "blocked":    False,
            "escalation": None,
        }

    # Step was injected — clear blocked state, return to actor.
    return {
        "messages":   [
            {"role": "user",      "content": replanner_prompt},
            {"role": "assistant", "content": raw},
        ],
        "blocked":    False,
        "escalation": None,
    }


# ── Premature summary detection ───────────────────────────────────────────────

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


