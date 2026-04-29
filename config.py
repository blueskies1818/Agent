"""
Central configuration. Change these values to reshape the whole system.
Nothing else in the codebase should hardcode paths, models, or provider names.
"""

import os
from pathlib import Path

# ── Providers ─────────────────────────────────────────────────────────────────
PROVIDERS = {
    "claude": {
        "model": os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6"),
        # API image block format and supported MIME types for the media pipeline.
        "media_format": "anthropic",
        "media_caps":   ["image/png", "image/jpeg", "image/webp"],
    },
    "openai": {
        "model": os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
        "media_format": "openai",
        "media_caps":   ["image/png", "image/jpeg", "image/webp"],
    },
}

# ── Agent roles — each slot is independently configurable ─────────────────────
AGENTS = {
    "planner": {
        "provider": os.getenv("PLANNER_PROVIDER", "openai"),
    },
    "worker": {
        "provider": os.getenv("WORKER_PROVIDER", "openai"),
    },
}


# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR   = str(BASE_DIR / "workspace" / "vault" / "internals" / "skills")
AGENTS_DIR   = str(BASE_DIR / "agents")
MODS_DIR     = str(BASE_DIR / "mods")   # used by mod handlers for internal paths
LOGS_DIR     = str(BASE_DIR / "memory" / "logs")

# ── Memory (SQLite) ──────────────────────────────────────────────────────────
MEMORY = {
    "db_path": BASE_DIR / "memory" / "agent.db",
}
 
# ── Project directory ─────────────────────────────────────────────────────────
_project_env = os.getenv("PROJECT", "").strip()
PROJECT_DIR: str | None = _project_env if _project_env else None
 
# ── Sandbox ───────────────────────────────────────────────────────────────────
# "local"  → subprocess.run() on the host machine (default, no Docker needed)
# "docker" → docker exec into the sandbox container
SANDBOX_MODE = os.getenv("SANDBOX", "local")
SANDBOX_ROOT = PROJECT_DIR if (PROJECT_DIR and SANDBOX_MODE == "local") else str(BASE_DIR / "workspace")
VAULT_DIR    = str(Path(SANDBOX_ROOT) / "vault")
 
# Docker settings (only used when SANDBOX_MODE == "docker")
DOCKER_CONTAINER_NAME = "agent-sandbox"
DOCKER_SHELL          = "/bin/bash"
DOCKER_WORKDIR        = "/workspace"
 
# ── Virtual display ──────────────────────────────────────────────────────────
# Used by mods that need a headless GUI (Xvfb inside the container).
DISPLAY_RESOLUTION = "1280x800x24"
DISPLAY_NUMBER     = ":99"
UI_SETTLE_DELAY    = 1.5   # seconds to wait after actions before screenshot
 
# ── Frame server ─────────────────────────────────────────────────────────────
# HTTP server that serves live screenshots of whatever the agent is looking at.
# Any mod can register a capture source — the viewer shows frames in real time.
# Open http://localhost:9222 in a browser, or run: python viewer.py
FRAME_SERVER_PORT = 9222
 
# ── Loop limits ───────────────────────────────────────────────────────────────
MAX_TURNS = 30
STREAM    = True
 
# ── Shell ─────────────────────────────────────────────────────────────────────
SHELL_TIMEOUT = 30
 
# ── LangGraph ─────────────────────────────────────────────────────────────────
GRAPH_TURN_LIMIT: int | None = None
 
# ── Context window ────────────────────────────────────────────────────────────
MAX_CONTEXT_TOKENS = 8_000
RELEVANCE_WEIGHT = 0.6
RECENCY_WEIGHT   = 0.4
RAG_MIN_SCORE = 0.4
# Pages with relevance_score >= this threshold are saved to long-term memory
# when evicted under token pressure. Sources "agent" and "system" are excluded
# (raw shell output and sandbox state aren't worth persisting).
EVICTION_SAVE_THRESHOLD = 0.65

# ── Dual context window sizes (Phase 2 — dual agent) ─────────────────────────
# Planner accumulates session history — needs more headroom.
# Worker resets each node and only needs a lean working set.
PLANNER_CONTEXT_TOKENS = 24_000
WORKER_CONTEXT_TOKENS  = 8_000

# ── Provider API context limit ────────────────────────────────────────────────
# Safe ceiling for total tokens sent to the provider (system + messages).
# Set below the hard API limit (e.g. Anthropic's 272k) so images are dropped
# before the request is rejected.  Tune down if you hit limit errors.
PROVIDER_CONTEXT_LIMIT = 200_000

# ── RAG — token budget retrieval ─────────────────────────────────────────────
# RAG_CANDIDATE_K: how many candidates to pull from ChromaDB before budget filtering.
# RAG_TOKEN_BUDGET: max tokens worth of results to inject (~22% of planner context).
# SKILL_TOKEN_BUDGET: separate cap for skill hint injection (~10% of planner context).
RAG_CANDIDATE_K    = 10
RAG_TOKEN_BUDGET   = int(PLANNER_CONTEXT_TOKENS * 0.22)   # ≈ 5 280
SKILL_TOKEN_BUDGET = int(PLANNER_CONTEXT_TOKENS * 0.10)   # ≈ 2 400
VAULT_TOKEN_BUDGET = int(PLANNER_CONTEXT_TOKENS * 0.15)   # ≈ 3 600

# ── Embeddings ────────────────────────────────────────────────────────────────
OLLAMA_EMBED_MODEL = "nomic-embed-text"
 
# ── Web search ────────────────────────────────────────────────────────────────
WEB_SEARCH_SOURCES   = 3
WEB_SEARCH_SEMANTIC  = False

# ── HTTP server (Phase 6) ─────────────────────────────────────────────────────
SERVER_HOST = os.getenv("SERVER_HOST", "127.0.0.1")
SERVER_PORT = int(os.getenv("SERVER_PORT", "8765"))

# ── Scheduler ─────────────────────────────────────────────────────────────────
SCHEDULED_DIR = str(BASE_DIR / "scheduled")

# ── MCP (Model Context Protocol) ──────────────────────────────────────────────
# Path to the user-editable external server list.
MCP_CONFIG_FILE    = str(BASE_DIR / "mcp_config.json")
# Set False to disable built-in tools and use only external MCP servers.
MCP_BUILTIN_ENABLED = True

# ── Conversational routing ─────────────────────────────────────────────────────
# A single-step plan whose text contains any of these words is treated as a
# conversational reply, not an execution task.  Add words here rather than
# editing the engine — the planner is the authority on intent.
CONVERSATIONAL_PLAN_KEYWORDS: tuple[str, ...] = (
    "reply", "respond", "greet", "answer", "acknowledge",
)