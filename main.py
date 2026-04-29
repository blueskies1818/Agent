"""
main.py — Glass Harness + Glass AI launcher and maintenance console.

Starts all services in order:
    1. Glass Harness HTTP server  (FastAPI / uvicorn — port 8765)
    2. Scheduler                  (polls scheduled/ for task files)
    3. Reactive sources           (webhooks, file watchers, etc.)
    4. Glass AI UI server         (Node / Express — port 3000)

Logs from every service stream into a shared ring buffer and are written
to memory/logs/agent.log.  The maintenance console drains the ring on each
prompt so new log lines appear inline without interrupting input.

Usage:
    ./start.sh                                   recommended — handles venv, deps, Node
    python main.py                               direct — assumes venv + node are ready
    SERVER_HOST=0.0.0.0 SERVER_PORT=9000 python main.py
    PLANNER_PROVIDER=claude WORKER_PROVIDER=openai python main.py
    GLASS_AI_PORT=4000 python main.py            run Glass AI on a different port

Maintenance commands:
    status              Server health, queue depth, recent sessions
    sessions [n]        List last n past sessions (default 10)
    tasks [n]           List recent task queue entries (default 10)
    logs [n]            Print last n log lines (default 30)
    send <message>      Submit a task and stream the response
    vault list|reindex  Inspect or rebuild vault ChromaDB collections
    wipe [target]       Selective data wipe — memory / logs / vectors / all
    help                Show this command list
    quit / exit         Shut down all services and exit
"""

import ast
import atexit
import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm
from rich.table import Table

from config import LOGS_DIR, SERVER_HOST, SERVER_PORT

SERVER_URL    = f"http://{SERVER_HOST}:{SERVER_PORT}"
GLASS_AI_PORT = int(os.getenv("GLASS_AI_PORT", "3000"))
GLASS_AI_URL  = f"http://localhost:{GLASS_AI_PORT}"
FRONTEND_DIR  = Path(__file__).parent / "front end"
AGENT_LOG     = Path(LOGS_DIR) / "agent.log"

console = Console(highlight=False)


# ── Shared log ring ───────────────────────────────────────────────────────────

_log_ring:   deque[str] = deque(maxlen=1000)
_log_cursor: int        = 0
_log_lock                = threading.Lock()


def _append_log(line: str) -> None:
    with _log_lock:
        _log_ring.append(line)
    try:
        AGENT_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(AGENT_LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _drain_logs() -> list[str]:
    """Return log lines received since the last drain call."""
    global _log_cursor
    with _log_lock:
        snapshot = list(_log_ring)
    new = snapshot[_log_cursor:]
    _log_cursor = len(snapshot)
    return new


def _print_log_line(line: str) -> None:
    if "[FATAL]" in line:
        console.print(f"[bold red]{line}[/bold red]")
    elif "[ERROR]" in line:
        console.print(f"[yellow]{line}[/yellow]")
    else:
        console.print(f"[dim]{line}[/dim]")


def _pipe_to_ring(stream, prefix: str = "") -> None:
    """Background thread: forward subprocess output into the ring buffer."""
    try:
        for raw in stream:
            line = raw.decode("utf-8", errors="replace").rstrip()
            if line:
                _append_log(f"{prefix}{line}" if prefix else line)
    except Exception:
        pass


# ── Service launchers ─────────────────────────────────────────────────────────

def _start_server() -> subprocess.Popen:
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn",
            "engine.server:app",
            "--host", SERVER_HOST,
            "--port", str(SERVER_PORT),
            "--log-level", "warning",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    atexit.register(proc.terminate)
    threading.Thread(target=_pipe_to_ring, args=(proc.stdout,), daemon=True).start()
    return proc


def _wait_for_health(timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"{SERVER_URL}/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.25)
    return False


def _start_scheduler() -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "engine/scheduler.py"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    atexit.register(proc.terminate)
    threading.Thread(target=_pipe_to_ring, args=(proc.stdout,), daemon=True).start()
    return proc


def _start_glass_ai() -> subprocess.Popen | None:
    """Start the Glass AI Node server from the 'front end/' directory."""
    if not FRONTEND_DIR.exists():
        return None
    node_bin = "node"
    try:
        subprocess.run([node_bin, "--version"], capture_output=True, check=True)
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None

    env = {**os.environ, "PORT": str(GLASS_AI_PORT)}
    proc = subprocess.Popen(
        [node_bin, "server.js"],
        cwd=str(FRONTEND_DIR),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=env,
    )
    atexit.register(proc.terminate)
    threading.Thread(
        target=_pipe_to_ring, args=(proc.stdout, "[glass-ai] "), daemon=True
    ).start()
    return proc


def _wait_for_glass_ai(timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(GLASS_AI_URL, timeout=2) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


def _has_reactive_interface(path: Path) -> bool:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    has_name = has_run = False
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id == "NAME":
                    has_name = True
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name == "run":
                has_run = True
    return has_name and has_run


def _start_reactives() -> list[subprocess.Popen]:
    reactive_dir = Path("reactive")
    if not reactive_dir.exists():
        return []
    procs: list[subprocess.Popen] = []
    for path in sorted(reactive_dir.glob("*.py")):
        if path.name.startswith("_") or not _has_reactive_interface(path):
            continue
        proc = subprocess.Popen(
            [sys.executable, str(path), "--server", SERVER_URL],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        atexit.register(proc.terminate)
        threading.Thread(target=_pipe_to_ring, args=(proc.stdout,), daemon=True).start()
        procs.append(proc)
    return procs


# ── HTTP helpers ──────────────────────────────────────────────────────────────

def _get(path: str) -> dict | list | None:
    try:
        with urllib.request.urlopen(f"{SERVER_URL}{path}", timeout=5) as resp:
            return json.loads(resp.read())
    except Exception:
        return None


def _post(path: str, body: dict) -> dict | None:
    try:
        data = json.dumps(body).encode()
        req  = urllib.request.Request(
            f"{SERVER_URL}{path}", data=data,
            headers={"Content-Type": "application/json"}, method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        console.print(f"[red]HTTP error: {exc}[/red]")
        return None


# ── Maintenance commands ──────────────────────────────────────────────────────

def cmd_status() -> None:
    health   = _get("/health")
    running  = _get("/tasks?status=running")  or []
    queued   = _get("/tasks?status=queued")   or []

    server_s = "[green]online[/green]" if health else "[red]unreachable[/red]"

    try:
        from memory.sessions import list_sessions
        recent = list_sessions(5)
    except Exception:
        recent = []

    try:
        urllib.request.urlopen(GLASS_AI_URL, timeout=2)
        ui_s = "[green]online[/green]"
    except Exception:
        ui_s = "[dim]offline[/dim]"

    grid = Table.grid(padding=(0, 2))
    grid.add_column(style="dim", min_width=14)
    grid.add_column()
    grid.add_row("Harness",  f"{server_s}  {SERVER_URL}")
    grid.add_row("Glass AI", f"{ui_s}  {GLASS_AI_URL}")
    grid.add_row("Running",  str(len(running)))
    grid.add_row("Queued",   str(len(queued)))
    grid.add_row("Log file", str(AGENT_LOG))

    console.print(Panel(grid, title="[bold]Glass Harness Status[/bold]", border_style="blue"))

    if recent:
        console.print("[dim]Recent sessions:[/dim]")
        for s in recent:
            sid  = s["id"]
            summ = (s.get("summary") or "").strip()[:70]
            ended = (s.get("ended_at") or "")[:16].replace("T", " ")
            console.print(f"  [cyan]{sid}[/cyan]  [dim]{ended}[/dim]  {summ}")


def cmd_sessions(args: list[str]) -> None:
    limit = int(args[0]) if args and args[0].isdigit() else 10
    try:
        from memory.sessions import list_sessions
        sessions = list_sessions(limit)
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        return

    if not sessions:
        console.print("[dim](no past sessions recorded)[/dim]")
        return

    t = Table(title=f"Sessions — last {limit}", border_style="dim")
    t.add_column("Session ID",  style="cyan", no_wrap=True)
    t.add_column("Started",     style="dim")
    t.add_column("Ended",       style="dim")
    t.add_column("Summary")
    for s in sessions:
        t.add_row(
            s["id"],
            (s.get("started_at") or "")[:16].replace("T", " "),
            (s.get("ended_at")   or "—")[:16].replace("T", " "),
            (s.get("summary")    or "")[:60],
        )
    console.print(t)


def cmd_tasks(args: list[str]) -> None:
    limit = int(args[0]) if args and args[0].isdigit() else 10
    rows  = _get("/tasks")
    if rows is None:
        console.print("[red]Could not reach server.[/red]")
        return

    rows = (rows if isinstance(rows, list) else [])[:limit]
    if not rows:
        console.print("[dim](no tasks in queue)[/dim]")
        return

    _STATUS_COLOR = {
        "complete": "green", "running": "yellow",
        "queued": "blue",    "failed": "red",
        "cancelled": "dim",
    }

    t = Table(title=f"Task queue — last {limit}", border_style="dim")
    t.add_column("ID",      style="dim",  no_wrap=True, max_width=10)
    t.add_column("Status",  no_wrap=True)
    t.add_column("Prompt",  max_width=55)
    t.add_column("Created", style="dim",  no_wrap=True)
    for row in rows:
        status = row.get("status", "?")
        color  = _STATUS_COLOR.get(status, "white")
        t.add_row(
            row["id"][:8] + "…",
            f"[{color}]{status}[/{color}]",
            (row.get("prompt") or "")[:55],
            (row.get("created_at") or "")[:16],
        )
    console.print(t)


def cmd_logs(args: list[str]) -> None:
    n = int(args[0]) if args and args[0].isdigit() else 30
    with _log_lock:
        lines = list(_log_ring)[-n:]
    if not lines:
        console.print("[dim](no logs yet)[/dim]")
        return
    for line in lines:
        _print_log_line(line)


def cmd_send(args: list[str]) -> None:
    """Submit a task to the HTTP queue and stream its output."""
    prompt = " ".join(args).strip()
    if not prompt:
        console.print("[red]Usage:  send <message>[/red]")
        return

    result = _post("/queue", {"prompt": prompt})
    if not result:
        return

    task_id = result.get("task_id", "?")
    console.print(f"[dim]Task {task_id} — waiting for output…[/dim]")

    try:
        with urllib.request.urlopen(
            f"{SERVER_URL}/stream/{task_id}", timeout=300
        ) as resp:
            for raw in resp:
                line = raw.decode("utf-8", errors="replace").rstrip()
                if not line or line.startswith(":"):
                    continue
                if line.startswith("data: "):
                    try:
                        ev = json.loads(line[6:])
                    except json.JSONDecodeError:
                        console.print(line[6:])
                        continue
                    ev_type = ev.get("type", "output")
                    content = ev.get("content", "")
                    if ev_type == "done":
                        break
                    elif ev_type == "error":
                        console.print(f"[red]{content}[/red]")
                        break
                    elif ev_type == "work":
                        console.print(f"[dim]  {content}[/dim]")
                    elif ev_type == "shell":
                        console.print(f"[cyan]  $ {content}[/cyan]")
                    else:
                        console.print(content)
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted — task still running on server.[/dim]")
    except urllib.error.URLError as exc:
        console.print(f"[red]Stream error: {exc}[/red]")


def cmd_vault(args: list[str]) -> None:
    sub = args[0].lower() if args else ""
    if sub == "reindex":
        console.print("[dim]Reindexing vault buckets from disk…[/dim]")
        try:
            from memory.vault import reindex_all_buckets
            result = reindex_all_buckets(skip_if_indexed=False)
            console.print(f"[green]{result}[/green]")
        except Exception as exc:
            console.print(f"[red]Reindex failed: {exc}[/red]")
    elif sub == "list":
        try:
            from memory.vault import list_buckets, _read_index, _get_bucket_collection
            buckets = list_buckets()
            if not buckets:
                console.print("[dim](no vault buckets registered)[/dim]")
                return
            index = _read_index()
            t = Table(title="Vault buckets", border_style="dim")
            t.add_column("Bucket",  style="cyan")
            t.add_column("Path",    style="dim")
            t.add_column("Docs",    style="dim", justify="right")
            t.add_column("Indexed", style="dim", justify="right")
            for b in buckets:
                entry   = index.get("buckets", {}).get(b, {})
                path    = entry.get("path", b)
                n_docs  = str(entry.get("content_count", "?"))
                try:
                    n_idx = str(_get_bucket_collection(b).count())
                except Exception:
                    n_idx = "?"
                t.add_row(b, path, n_docs, n_idx)
            console.print(t)
        except Exception as exc:
            console.print(f"[red]Error: {exc}[/red]")
    else:
        console.print(
            "[dim]Usage:[/dim]  [cyan]vault list[/cyan]    — show buckets and indexed doc counts\n"
            "         [cyan]vault reindex[/cyan] — re-embed all vault docs from disk"
        )


def cmd_wipe(args: list[str]) -> None:
    _VALID = {"memory", "logs", "vectors", "all"}
    target = args[0] if args else "memory"
    if target not in _VALID:
        console.print(
            f"[red]Unknown target '{target}'.[/red]  "
            f"Choose from: {', '.join(sorted(_VALID))}"
        )
        return
    if not Confirm.ask(f"[yellow]Wipe '{target}'?[/yellow]", default=False):
        console.print("[dim]Cancelled.[/dim]")
        return
    out = subprocess.run(
        [sys.executable, "wipe_All.py", target, "--yes"],
        capture_output=True, text=True,
    )
    console.print(out.stdout.strip() or out.stderr.strip() or "[green]Done.[/green]")


def cmd_help() -> None:
    t = Table(title="Maintenance commands", border_style="dim", show_header=False)
    t.add_column(style="cyan", min_width=20)
    t.add_column(style="dim")
    for cmd, desc in [
        ("status",           "Harness + Glass AI health, queue depth, recent sessions"),
        ("sessions [n]",     "List past sessions — default 10"),
        ("tasks [n]",        "List task queue entries — default 10"),
        ("logs [n]",         "Print last n log lines (harness + Glass AI) — default 30"),
        ("send <message>",   "Submit a task to the harness and stream its output"),
        ("vault list",       "Show vault buckets and how many docs are indexed"),
        ("vault reindex",    "Re-embed all vault docs from disk into ChromaDB"),
        ("wipe <target>",    "Wipe memory / logs / vectors / all"),
        ("help",             "Show this list"),
        ("quit",             "Shut down all services and exit"),
    ]:
        t.add_row(cmd, desc)
    console.print(t)
    console.print(
        f"\n[dim]Harness API:[/dim] POST [cyan]{SERVER_URL}/queue[/cyan]  "
        f"GET [cyan]{SERVER_URL}/stream/<task_id>[/cyan]\n"
        f"[dim]Glass AI:[/dim]    [cyan]{GLASS_AI_URL}[/cyan]"
    )


# ── Startup banner ────────────────────────────────────────────────────────────

def _print_banner(glass_ai_up: bool = False) -> None:
    from config import SANDBOX_MODE
    sandbox  = "docker" if SANDBOX_MODE == "docker" else "local"
    ui_line  = (
        f"[cyan]{GLASS_AI_URL}[/cyan]"
        if glass_ai_up
        else "[dim]not started (node not found)[/dim]"
    )
    console.rule("[bold blue]Glass Harness[/bold blue]")
    console.print(
        f"  [dim]Harness:[/dim]  [cyan]{SERVER_URL}[/cyan]\n"
        f"  [dim]Glass AI:[/dim] {ui_line}\n"
        f"  [dim]Sandbox:[/dim]  {sandbox}\n"
        f"  [dim]Log:[/dim]      {AGENT_LOG}",
    )
    console.rule(style="dim")
    console.print("[dim]Type [bold]help[/bold] for maintenance commands.[/dim]\n")


# ── Main ──────────────────────────────────────────────────────────────────────

def main() -> None:
    # ── Start services ────────────────────────────────────────────────────────
    console.print("[bold blue]Starting Glass Harness…[/bold blue]")

    console.print("  [dim]→ Harness API[/dim]  ", end="")
    _start_server()
    if not _wait_for_health():
        console.print(
            f"\n[bold red]Harness server did not become healthy within 30s.[/bold red]"
        )
        sys.exit(1)
    console.print(f"[green]ready[/green]  [dim]{SERVER_URL}[/dim]")

    console.print("  [dim]→ Scheduler[/dim]    ", end="")
    _start_scheduler()
    console.print("[green]started[/green]")

    reactives = _start_reactives()
    if reactives:
        console.print(
            f"  [dim]→ Reactives[/dim]   [green]{len(reactives)} process(es)[/green]"
        )

    console.print("  [dim]→ Glass AI[/dim]     ", end="")
    glass_ai_proc = _start_glass_ai()
    if glass_ai_proc is None:
        console.print("[yellow]skipped[/yellow]  [dim](node not found — install Node.js 18+)[/dim]")
        glass_ai_up = False
    else:
        glass_ai_up = _wait_for_glass_ai()
        if glass_ai_up:
            console.print(f"[green]ready[/green]  [dim]{GLASS_AI_URL}[/dim]")
        else:
            console.print("[yellow]slow start[/yellow]  [dim](server process running but not yet responding)[/dim]")

    _print_banner(glass_ai_up)

    # ── Maintenance console ────────────────────────────────────────────────────
    while True:
        for line in _drain_logs():
            _print_log_line(line)

        try:
            cmd_line = input("agent> ").strip()
        except (EOFError, KeyboardInterrupt):
            console.print("\n[dim]Shutting down…[/dim]")
            break

        if not cmd_line:
            continue

        parts = cmd_line.split()
        cmd   = parts[0].lower().lstrip("/")
        args  = parts[1:]

        if cmd in ("quit", "exit", "q"):
            console.print("[dim]Shutting down…[/dim]")
            break
        elif cmd == "status":
            cmd_status()
        elif cmd == "sessions":
            cmd_sessions(args)
        elif cmd == "tasks":
            cmd_tasks(args)
        elif cmd == "logs":
            cmd_logs(args)
        elif cmd == "send":
            cmd_send(args)
        elif cmd == "vault":
            cmd_vault(args)
        elif cmd == "wipe":
            cmd_wipe(args)
        elif cmd == "help":
            cmd_help()
        else:
            console.print(
                f"[red]Unknown command: '{cmd}'[/red]  —  type [bold]help[/bold]"
            )


if __name__ == "__main__":
    main()
