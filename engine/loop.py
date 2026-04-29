"""
engine/loop.py — Thin session wrapper around the LangGraph.

V2 changes:
  - Two agent instances: planner_agent (smart) and worker_agent (fast)
  - Two ContextWindow instances: planner_ctx (per-session) and worker_ctx (reset each node)
  - Planner context seeded from ChromaDB RAG on session start (no flat file)
  - build_graph() receives both agents
"""

import base64
from pathlib import Path

from providers.base import BaseAgent
from config import (
    AGENTS,
    EVICTION_SAVE_THRESHOLD,
    PLANNER_CONTEXT_TOKENS,
    RAG_CANDIDATE_K,
    RAG_MIN_SCORE,
    RECENCY_WEIGHT,
    RELEVANCE_WEIGHT,
    WORKER_CONTEXT_TOKENS,
)
from core.log import log
from core.context_window import ContextWindow, Page
from core.prompt_evaluator import PromptEvaluator
from core.xml_parser import parse_response
from engine.graph import build_graph
from engine.mcp_router import MCPRouter
from engine.sandbox import is_docker, ensure_sandbox, get_project_display
from memory.embedder import embed_conversation_turn
from memory.memory import SessionLogger
from memory.rag import MemoryRetriever, SkillRetriever, VaultRetriever
from memory.sessions import open_session, log_turn as _log_session_turn, close_session
from providers import load_provider


# ── Eviction handler ─────────────────────────────────────────────────────────

_SAVEABLE_SOURCES = {"memory", "skill", "user"}


def _on_evict(page: Page) -> None:
    """
    Called by ContextWindow just before a page is dropped.
    Saves the page content to long-term memory if it was important enough.
    """
    if page.source not in _SAVEABLE_SOURCES:
        return
    if page.relevance_score < EVICTION_SAVE_THRESHOLD:
        return
    try:
        from engine.mod_api import save_fact
        save_fact(page.content)
    except Exception as e:
        log.error(f"eviction save failed: {e}", source="loop")


# ── Soul / reference loaders ──────────────────────────────────────────────────

def _load_file(path_str: str, fallback: str = "") -> str:
    path = Path(path_str)
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return fallback


# ── Tool index (filled in once MCPRouter connects) ────────────────────────────

_active_mcp_router: MCPRouter | None = None


def _mod_index() -> str:
    if _active_mcp_router is not None:
        return _active_mcp_router.mod_index()
    return "Tools not yet initialised."


# ── Sandbox info ──────────────────────────────────────────────────────────────

def _sandbox_info() -> str:
    label = get_project_display()
    mode = "Docker container (isolated)" if is_docker() else "Local (host machine)"
    return f"Working directory: {label}\nEnvironment: {mode}"


# ── Planner system prompt ─────────────────────────────────────────────────────

def _build_planner_system_prompt(ctx: ContextWindow, soul: str, core_ref: str) -> str:
    used, total = ctx.token_usage
    return f"""{soul}

---

{core_ref}

## Sandbox
{_sandbox_info()}

## Context window  [{used}/{total} tokens  |  {ctx.page_count} pages]
{ctx.render()}

## Mod commands
These commands look like shell commands but are intercepted by the system.
Use them inside normal shell actions — they do specific tasks via a mod script.
{_mod_index()}
"""


# ── Worker system prompt (built fresh each node) ──────────────────────────────

def _build_worker_system_prompt(
    project_log: str,
    current_step: str,
    rag_pages: list[tuple[str, float]],
    soul_worker: str,
    core_ref: str,
) -> str:
    rag_section = ""
    if rag_pages:
        items = "\n".join(f"  - {content}" for content, _ in rag_pages)
        rag_section = f"\n## Relevant memory\n{items}\n"

    return f"""{soul_worker}

---

{core_ref}

## Sandbox
{_sandbox_info()}

## Project log (what has been done this session)
{project_log}

## Current step
{current_step}
{rag_section}
## Mod commands
{_mod_index()}
"""


# ── Message stats (for context_map image token reporting) ────────────────────

def _compute_messages_stats(messages: list[dict]) -> dict:
    """
    Walk state["messages"] and tally text + image token estimates.
    Uses len // 4 consistently with the rest of the codebase.
    Image base64 length is what actually gets sent in the API payload.
    """
    image_sizes: list[int] = []
    text_chars = 0

    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            text_chars += len(content)
        elif isinstance(content, list):
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type", "")
                if btype == "text":
                    text_chars += len(block.get("text", ""))
                elif btype == "image":
                    b64 = block.get("source", {}).get("data", "")
                    if b64:
                        image_sizes.append(len(b64))
                elif btype == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    if "base64," in url:
                        image_sizes.append(len(url.split("base64,", 1)[1]))

    return {
        "message_count":    len(messages),
        "image_count":      len(image_sizes),
        "image_tokens_est": sum(n // 4 for n in image_sizes),
        "text_tokens_est":  text_chars // 4,
        "total_tokens_est": (text_chars + sum(image_sizes)) // 4,
    }


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
    """Session wrapper — owns planner + worker contexts, logger, and graph."""

    def __init__(self) -> None:
        # ── Load agents from AGENTS config ───────────────────────────────────
        planner_cfg = AGENTS["planner"]
        worker_cfg  = AGENTS["worker"]

        log.info(f"loading planner: {planner_cfg['provider']}", source="loop")
        self._planner_agent: BaseAgent = load_provider(planner_cfg["provider"])

        log.info(f"loading worker: {worker_cfg['provider']}", source="loop")
        self._worker_agent: BaseAgent = load_provider(worker_cfg["provider"])

        self._logger = SessionLogger()
        self._last_summary: str = ""
        open_session(self._logger.session_id)

        # Ensure sandbox is ready
        ensure_sandbox()

        # ── Soul / core reference files ───────────────────────────────────────
        from config import AGENTS_DIR
        from pathlib import Path as _Path
        _ad = _Path(AGENTS_DIR)
        self._soul         = _load_file(str(_ad / "soul.md"))
        self._soul_planner = _load_file(str(_ad / "planner.md"))
        self._soul_worker  = _load_file(str(_ad / "worker.md"))
        self._core_ref     = _load_file(str(_ad / "core_refs.md"))

        # ── Planner context window (per-session) ──────────────────────────────
        self._planner_ctx = ContextWindow(
            max_tokens=PLANNER_CONTEXT_TOKENS,
            relevance_weight=RELEVANCE_WEIGHT,
            recency_weight=RECENCY_WEIGHT,
            on_evict=_on_evict,
        )

        # ── Worker context window (reset each node — referenced by nodes.py) ──
        self._worker_ctx = ContextWindow(
            max_tokens=WORKER_CONTEXT_TOKENS,
            relevance_weight=RELEVANCE_WEIGHT,
            recency_weight=RECENCY_WEIGHT,
            on_evict=_on_evict,
        )

        # ── Memory retriever for RAG ──────────────────────────────────────────
        self._rag = MemoryRetriever(min_score=RAG_MIN_SCORE)

        # ── Skill retriever (semantic, not keyword) ───────────────────────────
        self._skill_rag = SkillRetriever(min_score=RAG_MIN_SCORE)

        # ── Vault retriever (lazy bootstrap from disk on first use) ───────────
        self._vault_rag = VaultRetriever(min_score=RAG_MIN_SCORE)

        # ── Prompt evaluator ──────────────────────────────────────────────────
        self._evaluator = PromptEvaluator(
            rag=self._rag,
            skill_rag=self._skill_rag,
            vault_rag=self._vault_rag,
            min_score=RAG_MIN_SCORE,
            rag_top_k=RAG_CANDIDATE_K,
        )

        # ── Build graph — pass both agents and both context windows ───────────
        self._graph = build_graph(
            planner_agent=self._planner_agent,
            worker_agent=self._worker_agent,
            planner_ctx=self._planner_ctx,
            worker_ctx=self._worker_ctx,
            soul=self._soul,
            soul_planner=self._soul_planner,
            soul_worker=self._soul_worker,
            core_ref=self._core_ref,
        )

        # ── MCP router — connects built-in + external tool servers ───────────
        global _active_mcp_router
        self._mcp_router = MCPRouter()
        self._mcp_router.connect_all()
        _active_mcp_router = self._mcp_router

        # Inject router into nodes module so _run_shell() uses it
        from engine import nodes as _nodes_mod
        _nodes_mod.set_mcp_router(self._mcp_router)

        # ── Last screenshot — loaded from workspace for cross-turn persistence ─
        self._last_screenshot: bytes | None = None
        try:
            self._last_screenshot = self._sandbox_read_screenshot()
        except Exception:
            pass

        # ── Seed planner context from ChromaDB RAG ────────────────────────────
        self._planner_ctx.push(
            f"Sandbox: {get_project_display()}",
            source="system",
            relevance_score=1.0,
        )

    def _sandbox_read_screenshot(self) -> bytes | None:
        """Load the latest debug_ui screenshot from workspace (if any)."""
        from engine.sandbox import read_file
        return read_file("/workspace/.agent/screenshots/latest.png")

    def _extract_screenshots(self, messages: list[dict]) -> list[bytes]:
        """Pull image bytes out of message history (Anthropic + OpenAI formats)."""
        out: list[bytes] = []
        for msg in reversed(messages):
            content = msg.get("content", "")
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                # Anthropic
                if block.get("type") == "image":
                    src = block.get("source", {})
                    if src.get("type") == "base64":
                        try:
                            out.append(base64.b64decode(src["data"]))
                        except Exception:
                            pass
                # OpenAI
                elif block.get("type") == "image_url":
                    url = block.get("image_url", {}).get("url", "")
                    if "base64," in url:
                        try:
                            out.append(base64.b64decode(url.split("base64,", 1)[1]))
                        except Exception:
                            pass
        return out

    def run(self, user_input: str) -> None:
        self._logger.log("USER", user_input)
        _log_session_turn(self._logger.session_id, "user", user_input)
        self._planner_ctx.tick()

        # Refresh sandbox page
        self._planner_ctx.clear_source("system")
        self._planner_ctx.push(
            f"Sandbox: {get_project_display()}",
            source="system",
            relevance_score=1.0,
        )

        self._planner_ctx.push(
            user_input,
            source="user",
            relevance_score=0.90,
        )

        # Inject RAG memory hits + skill hints into planner context
        for page in self._evaluator.evaluate(user_input):
            self._planner_ctx.push(page.content, page.source, page.relevance_score)  # type: ignore[arg-type]

        system = _build_planner_system_prompt(
            self._planner_ctx,
            self._soul_planner,
            self._core_ref,
        )

        initial_messages: list[dict] = [{"role": "user", "content": user_input}]

        initial_state = {
            "messages":     initial_messages,
            "plan":         [],
            "plan_step":    0,
            "actor_turn":   0,
            "done":         False,
            "blocked":      False,
            "escalation":   None,
            "system":       system,
            "last_actions": [],
        }

        final_state = self._graph.invoke(initial_state)

        # Update last screenshot from this turn's messages
        new_shots = self._extract_screenshots(final_state.get("messages", []))
        if new_shots:
            self._last_screenshot = new_shots[0]

        # Push action results into planner context for session continuity
        for result in final_state.get("last_actions", []):
            if result and not result.startswith("[ERROR]"):
                self._planner_ctx.push(result, source="agent", relevance_score=0.75)

        final_messages = final_state.get("messages", [])
        summary = _extract_summary(final_messages)
        if summary:
            self._planner_ctx.push(
                f"Agent replied: {summary}",
                source="agent",
                relevance_score=0.80,
            )

        self._logger.log("ASSISTANT", summary or "(no summary)")
        if summary:
            _log_session_turn(self._logger.session_id, "assistant", summary)
            self._last_summary = summary

        if user_input and summary:
            try:
                embed_conversation_turn(
                    user=user_input,
                    assistant=summary,
                    metadata={"source": "session_turn"},
                )
            except Exception as e:
                log.error(f"session turn embedding failed: {e}", source="loop")

        try:
            from engine.context_state import write_snapshot
            write_snapshot(
                self._planner_ctx,
                self._worker_ctx,
                planner_injected={
                    "soul":      self._soul_planner,
                    "core_ref":  self._core_ref,
                    "sandbox":   _sandbox_info(),
                    "mod_index": _mod_index(),
                },
                worker_injected={
                    "soul":      self._soul_worker,
                    "core_ref":  self._core_ref,
                    "sandbox":   _sandbox_info(),
                    "mod_index": _mod_index(),
                },
                messages_stats=_compute_messages_stats(final_state.get("messages", [])),
            )
        except Exception as e:
            log.error(f"context snapshot failed: {e}", source="loop")

    def close(self) -> None:
        used, total = self._planner_ctx.token_usage
        print(f"\n[context] planner: {used}/{total} tokens  |  {self._planner_ctx.page_count} pages")
        self._logger.close()
        if self._logger.path:
            print(f"[session log: {self._logger.path}]")
        close_session(self._logger.session_id, summary=self._last_summary)
        self._mcp_router.shutdown()
