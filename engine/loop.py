"""
engine/loop.py — Thin session wrapper around the LangGraph.
"""

from pathlib import Path

from agents.base import BaseAgent
from config import (
    MAX_CONTEXT_TOKENS,
    MODS_DIR,
    RAG_MIN_SCORE,
    RAG_TOP_K,
    RECENCY_WEIGHT,
    RELEVANCE_WEIGHT,
    SKILLS_DIR,
    SOUL_FILE,
)
from core.context_window import ContextWindow
from core.prompt_evaluator import PromptEvaluator
from core.xml_parser import parse_response
from engine.graph import build_graph
from engine.sandbox import is_docker, ensure_sandbox, get_project_display
from memory.memory import SessionLogger, read_memory
from memory.rag import MemoryRetriever
from mods import ModRouter


# ── Soul loader ───────────────────────────────────────────────────────────────

def _load_soul() -> str:
    path = Path(SOUL_FILE)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return (
        "You are an autonomous AI agent with direct shell access to a computer.\n"
        "Think before acting. Verify your work. "
        "Always emit <action type=\"done\"/> when finished."
    )


# ── Skill index ───────────────────────────────────────────────────────────────

def _skill_index() -> str:
    skills_path = Path(SKILLS_DIR)
    if not skills_path.exists():
        return "No skills available."
    lines = []
    for md_file in sorted(skills_path.glob("*.md")):
        name = md_file.stem
        try:
            first_line = md_file.read_text(encoding="utf-8").strip().splitlines()[0]
            first_line = first_line.lstrip("#").strip()
        except Exception:
            first_line = "(no description)"
        lines.append(f"  - {name}: {first_line}")
    return "\n".join(lines) if lines else "No skills available."


# ── Mod index ─────────────────────────────────────────────────────────────────

def _mod_index() -> str:
    try:
        router = ModRouter(MODS_DIR)
        return router.mod_index()
    except Exception:
        return "No mods available."


# ── Sandbox info ──────────────────────────────────────────────────────────────

def _sandbox_info() -> str:
    """Build the sandbox section for the system prompt."""
    label = get_project_display()
    mode = "Docker container (isolated)" if is_docker() else "Local (host machine)"
    return f"Working directory: {label}\nEnvironment: {mode}"


# ── System prompt ─────────────────────────────────────────────────────────────

def _build_system_prompt(ctx: ContextWindow) -> str:
    used, total = ctx.token_usage
    return f"""{_load_soul()}

## Sandbox
{_sandbox_info()}

## Context window  [{used}/{total} tokens  |  {ctx.page_count} pages]
{ctx.render()}

## Available skills
{_skill_index()}
Request a full definition with: <action type="skill"><n>skill_name</n></action>

## Mod commands
These commands look like shell commands but are intercepted by the system.
Use them inside normal shell actions — they never touch the real shell.
{_mod_index()}
Load the matching skill for full usage details.

## Tags you can use
<think>Internal reasoning — never shown to the user.</think>
<plan>
  1. First step
  2. Last step must always summarise and confirm to the user
</plan>
<work>What you are doing right now — shown as a status line.</work>

## Action format
<action type="shell"><command>ls -la</command></action>
<action type="shell"><command>memory -query "search terms"</command></action>
<action type="shell"><command>search_web -query "search terms"</command></action>
<action type="skill"><n>write</n></action>
<action type="memory"><op>write</op><content>fact to remember</content></action>
<action type="done"/>
"""


# ── Conversation summary extractor ────────────────────────────────────────────

def _extract_summary(messages: list[dict]) -> str:
    for msg in reversed(messages):
        if msg.get("role") == "assistant":
            reasoning, _, _, _, _ = parse_response(msg["content"])
            if reasoning.strip():
                return reasoning.strip()
            return msg["content"][:500].strip()
    return ""


# ── Session loop ──────────────────────────────────────────────────────────────

class AgentLoop:
    """Session wrapper — owns context window, evaluator, logger, and graph."""

    def __init__(self, agent: BaseAgent) -> None:
        self._agent  = agent
        self._logger = SessionLogger()
        self._graph  = build_graph(agent)

        # Ensure sandbox is ready (creates dirs or starts container)
        ensure_sandbox()

        self._ctx = ContextWindow(
            max_tokens=MAX_CONTEXT_TOKENS,
            relevance_weight=RELEVANCE_WEIGHT,
            recency_weight=RECENCY_WEIGHT,
        )

        self._evaluator = PromptEvaluator(
            skills_dir=SKILLS_DIR,
            rag=MemoryRetriever(min_score=RAG_MIN_SCORE),
            min_score=RAG_MIN_SCORE,
            rag_top_k=RAG_TOP_K,
        )

        # Seed context window
        memory = read_memory()
        if memory:
            self._ctx.push(memory, source="memory", relevance_score=0.85)

        self._ctx.push(
            f"Sandbox: {get_project_display()}",
            source="system",
            relevance_score=1.0,
        )

    def run(self, user_input: str) -> None:
        self._logger.log("USER", user_input)
        self._ctx.tick()

        # Clear and refresh system pages
        self._ctx.clear_source("system")
        self._ctx.push(
            f"Sandbox: {get_project_display()}",
            source="system",
            relevance_score=1.0,
        )

        self._ctx.push(
            user_input,
            source="user",
            relevance_score=0.90,
        )

        for page in self._evaluator.evaluate(user_input):
            self._ctx.push(page.content, page.source, page.relevance_score)  # type: ignore[arg-type]

        system = _build_system_prompt(self._ctx)

        initial_state = {
            "messages":     [{"role": "user", "content": user_input}],
            "plan":         [],
            "actor_turn":   0,
            "done":         False,
            "system":       system,
            "last_actions": [],
        }

        final_state = self._graph.invoke(initial_state)

        for result in final_state.get("last_actions", []):
            if result and not result.startswith("[ERROR]"):
                self._ctx.push(result, source="agent", relevance_score=0.75)

        final_messages = final_state.get("messages", [])
        summary = _extract_summary(final_messages)
        if summary:
            self._ctx.push(
                f"Agent replied: {summary}",
                source="agent",
                relevance_score=0.80,
            )

        self._logger.log("ASSISTANT", summary or "(no summary)")

    def close(self) -> None:
        used, total = self._ctx.token_usage
        print(f"\n[context] {used}/{total} tokens  |  {self._ctx.page_count} pages")
        self._logger.close()
        print(f"[session log: {self._logger.path}]")