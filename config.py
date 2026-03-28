"""
Central configuration. Change these values to reshape the whole system.
Nothing else in the codebase should hardcode paths, models, or provider names.
"""

import os
from pathlib import Path

# ── Provider ──────────────────────────────────────────────────────────────────
ACTIVE_PROVIDER = os.getenv("PROVIDER", "openai")
ACTIVE_TIER     = os.getenv("TIER", "smart")

PROVIDERS = {
    "claude": {
        "models": {
            "fast":  "claude-haiku-4-5-20251001",
            "smart": "claude-sonnet-4-6",
        }
    },
    "openai": {
        "models": {
            "fast":  "gpt-5.4-nano",
            "smart": "gpt-5.4-mini",
        }
    },
}


# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR     = Path(os.path.dirname(os.path.abspath(__file__)))
SKILLS_DIR   = str(BASE_DIR / "skills")
MODS_DIR     = str(BASE_DIR / "mods")
MEMORY_FILE  = str(BASE_DIR / "memory" / "memory.txt")
LOGS_DIR     = str(BASE_DIR / "memory" / "logs")
SOUL_FILE    = str(BASE_DIR / "soul.md")
 
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
RAG_TOP_K = 5
 
# ── Web search ────────────────────────────────────────────────────────────────
WEB_SEARCH_SOURCES   = 3
WEB_SEARCH_SEMANTIC  = False