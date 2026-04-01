"""
main.py — Entry point for the AI shell agent.

Starts an interactive REPL where each line you type is processed by the
agentic loop. The AI can run shell commands, load skills, persist memory,
and reason through multi-step tasks autonomously.

Usage:
    python main.py                   # uses ACTIVE_PROVIDER from config.py
    PROVIDER=openai python main.py   # override provider via env var
    TIER=fast python main.py         # use the cheaper/faster model tier
"""

import sys

from config import ACTIVE_PROVIDER, SANDBOX_ROOT
from providers import load_provider
from engine.loop import AgentLoop


def main() -> None:
    print(f"[agent] Loading provider: {ACTIVE_PROVIDER}")

    try:
        agent = load_provider(ACTIVE_PROVIDER)
    except (ValueError, EnvironmentError) as e:
        print(f"[error] Failed to load provider: {e}", file=sys.stderr)
        sys.exit(1)

    loop = AgentLoop(agent)

    print(f"[agent] Sandbox: {SANDBOX_ROOT}")
    print("[agent] Type your message. Press Ctrl+C or Ctrl+D to quit.\n")

    try:
        while True:
            try:
                user_input = input("you> ").strip()
            except EOFError:
                break

            if not user_input:
                continue

            if user_input.lower() in {"exit", "quit", "q"}:
                break

            loop.run(user_input)

    except KeyboardInterrupt:
        print("\n[agent] Interrupted.")
    finally:
        loop.close()


if __name__ == "__main__":
    main()