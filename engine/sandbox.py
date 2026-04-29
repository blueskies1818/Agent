"""
engine/sandbox.py — Shell execution backend + file transfer.

Abstracts where commands run.  Two backends:

  local   — subprocess.run() in SANDBOX_ROOT (current behavior, no Docker)
  docker  — docker exec into the sandbox container

Mods that need container access import directly from here:

    from engine.sandbox import run_command, pull_file, push_file, is_docker

    # Run a command inside the container (or locally)
    output = run_command("xdotool mousemove 640 400 click 1")

    # Copy a file FROM the container to the host
    pull_file("/workspace/screenshot.png", "/tmp/screenshot.png")

    # Copy a file FROM the host into the container
    push_file("/tmp/config.json", "/workspace/config.json")

All functions work transparently in both local and docker mode.
In local mode, file transfers are just copies within the host filesystem.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from config import (
    PROJECT_DIR,
    SANDBOX_MODE,
    SANDBOX_ROOT,
    SHELL_TIMEOUT,
    DOCKER_CONTAINER_NAME,
    DOCKER_SHELL,
    DOCKER_WORKDIR,
)
from core.log import log


# ── Command execution ────────────────────────────────────────────────────────

def run_command(command: str, timeout: int | None = None) -> str:
    """
    Execute a shell command in the configured sandbox.

    This is the primary entry point for mods that need to run commands
    inside the container (or locally).  Returns combined stdout + stderr.
    Errors are returned as [ERROR] strings, never raised.

    Args:
        command:  Shell command string.
        timeout:  Override the default SHELL_TIMEOUT (seconds).
    """
    t = timeout or SHELL_TIMEOUT
    if SANDBOX_MODE == "docker":
        return _run_docker(command, t)
    return _run_local(command, t)


# ── File transfer ────────────────────────────────────────────────────────────

def pull_file(container_path: str, host_path: str) -> bool:
    """
    Copy a file FROM the sandbox TO the host.

    In docker mode: runs `docker cp container:path host_path`
    In local mode:  copies within the host filesystem

    Args:
        container_path: Path inside the sandbox (e.g. "/workspace/screenshot.png")
        host_path:      Destination on the host (e.g. "/tmp/screenshot.png")

    Returns:
        True if successful, False otherwise.
    """
    try:
        Path(host_path).parent.mkdir(parents=True, exist_ok=True)

        if SANDBOX_MODE == "docker":
            result = subprocess.run(
                ["docker", "cp",
                 f"{DOCKER_CONTAINER_NAME}:{container_path}",
                 host_path],
                capture_output=True, text=True, timeout=15,
            )
            return result.returncode == 0
        else:
            # Local mode: resolve container_path relative to SANDBOX_ROOT
            src = _resolve_local_path(container_path)
            if src.exists():
                shutil.copy2(str(src), host_path)
                return True
            return False
    except Exception:
        return False


def push_file(host_path: str, container_path: str) -> bool:
    """
    Copy a file FROM the host INTO the sandbox.

    In docker mode: runs `docker cp host_path container:path`
    In local mode:  copies within the host filesystem

    Args:
        host_path:      Source on the host (e.g. "/tmp/config.json")
        container_path: Destination inside the sandbox (e.g. "/workspace/config.json")

    Returns:
        True if successful, False otherwise.
    """
    try:
        if SANDBOX_MODE == "docker":
            result = subprocess.run(
                ["docker", "cp",
                 host_path,
                 f"{DOCKER_CONTAINER_NAME}:{container_path}"],
                capture_output=True, text=True, timeout=15,
            )
            return result.returncode == 0
        else:
            dst = _resolve_local_path(container_path)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(host_path, str(dst))
            return True
    except Exception:
        return False


def read_file(container_path: str) -> bytes | None:
    """
    Read a file's contents from the sandbox and return as bytes.

    Useful for reading binary files like screenshots without saving
    to a temp file first.

    In docker mode: `docker exec cat <path>` with binary capture
    In local mode:  direct file read

    Returns None if the file doesn't exist or can't be read.
    """
    try:
        if SANDBOX_MODE == "docker":
            result = subprocess.run(
                ["docker", "exec", DOCKER_CONTAINER_NAME,
                 "cat", container_path],
                capture_output=True, timeout=10,
            )
            if result.returncode == 0:
                return result.stdout  # bytes
            return None
        else:
            path = _resolve_local_path(container_path)
            if path.exists():
                return path.read_bytes()
            return None
    except Exception:
        return None


# ── Status queries ────────────────────────────────────────────────────────────

def is_docker() -> bool:
    """Check if the sandbox is running in Docker mode."""
    return SANDBOX_MODE == "docker"


def container_running() -> bool:
    """Check if the Docker sandbox container is alive."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f", "{{.State.Running}}", DOCKER_CONTAINER_NAME],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "true"
    except Exception:
        return False


def get_project_display() -> str:
    """Return a human-readable label for the current workspace."""
    if PROJECT_DIR:
        return f"{PROJECT_DIR} (project)"
    if is_docker():
        return f"{SANDBOX_ROOT} (Docker sandbox)"
    return SANDBOX_ROOT


def ensure_sandbox() -> None:
    """
    Make sure the sandbox is ready.  Call once at startup.

    In local mode:  creates SANDBOX_ROOT if it doesn't exist.
    In docker mode: ensures host directory exists, starts the container
                    with the correct bind mount.
    """
    import os

    if SANDBOX_MODE != "docker":
        os.makedirs(SANDBOX_ROOT, exist_ok=True)
        return

    # Ensure the host directory exists before mounting
    host_dir = PROJECT_DIR or SANDBOX_ROOT
    os.makedirs(host_dir, exist_ok=True)

    if container_running():
        # Check if the running container has the right mount
        if not _container_has_mount(host_dir):
            log.info(f"Mount changed to {host_dir}, recreating container...", source="sandbox")
            _stop_container()
            _start_container()
        return

    _start_container()


# ── Local backend ─────────────────────────────────────────────────────────────

def _run_local(command: str, timeout: int) -> str:
    """Execute via subprocess on the host machine."""
    try:
        result = subprocess.run(
            command, shell=True, cwd=SANDBOX_ROOT,
            capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            output += f"\n[exit code: {result.returncode}]"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[ERROR] Command timed out after {timeout}s"
    except Exception as e:
        return f"[ERROR] {e}"


def _resolve_local_path(container_path: str) -> Path:
    """
    Convert a container-style path to a host path in local mode.

    /workspace/foo.txt → SANDBOX_ROOT/foo.txt
    foo.txt            → SANDBOX_ROOT/foo.txt
    """
    p = container_path.strip()
    if p.startswith(DOCKER_WORKDIR):
        p = p[len(DOCKER_WORKDIR):].lstrip("/")
    return Path(SANDBOX_ROOT) / p


# ── Docker backend ────────────────────────────────────────────────────────────

def _run_docker(command: str, timeout: int) -> str:
    """Execute via `docker exec` inside the sandbox container."""
    # umask 0000 ensures files created by the container (running as root)
    # are world-writable on the host bind-mount — no read-only surprises.
    docker_cmd = [
        "docker", "exec",
        "-w", DOCKER_WORKDIR,
        DOCKER_CONTAINER_NAME,
        DOCKER_SHELL, "-c", f"umask 0000; {command}",
    ]

    try:
        result = subprocess.run(
            docker_cmd,
            capture_output=True, text=True, timeout=timeout,
        )
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0:
            if "No such container" in output or "is not running" in output:
                return (
                    "[ERROR] Sandbox container is not running.\n"
                    "Start it with: docker compose up -d"
                )
            output += f"\n[exit code: {result.returncode}]"
        return output.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"[ERROR] Command timed out after {timeout}s"
    except FileNotFoundError:
        return (
            "[ERROR] Docker not found on this system.\n"
            "Install Docker or set SANDBOX_MODE='local' in config.py"
        )
    except Exception as e:
        return f"[ERROR] {e}"


# ── Container lifecycle ──────────────────────────────────────────────────────

def _start_container() -> None:
    """Start the sandbox container with the correct volume mount."""
    subprocess.run(
        ["docker", "rm", "-f", DOCKER_CONTAINER_NAME],
        capture_output=True, timeout=30,
    )

    # Always bind-mount a host directory so files sync both ways.
    # PROJECT_DIR overrides the default workspace/ path.
    host_dir = PROJECT_DIR or SANDBOX_ROOT
    volume_arg = f"{host_dir}:{DOCKER_WORKDIR}"

    cmd = [
        "docker", "run", "-d",
        "--name", DOCKER_CONTAINER_NAME,
        "--memory", "1g",
        "--cpus", "1.0",
        "--pids-limit", "512",
        # Drop all capabilities, add back safe ones for package management
        "--cap-drop", "ALL",
        "--cap-add", "CHOWN",
        "--cap-add", "DAC_OVERRIDE",
        "--cap-add", "FOWNER",
        "--cap-add", "FSETID",
        "--cap-add", "SETGID",
        "--cap-add", "SETUID",
        "--cap-add", "KILL",
        "--cap-add", "NET_BIND_SERVICE",
        "--security-opt", "no-new-privileges:true",
        "-v", volume_arg,
        "agent-sandbox",
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            mount_desc = PROJECT_DIR or SANDBOX_ROOT
            log.info(f"Container started → {mount_desc}", source="sandbox")
            # Open permissions on the bind-mounted workspace so both the
            # container (root) and the host user can read/write freely.
            subprocess.run(
                ["docker", "exec", DOCKER_CONTAINER_NAME,
                 "chmod", "-R", "a+rwX", DOCKER_WORKDIR],
                capture_output=True, timeout=15,
            )
        else:
            err = (result.stderr or result.stdout).strip()
            log.error(f"Failed to start container: {err}", source="sandbox")
    except FileNotFoundError:
        log.error("Docker not found. Install Docker or use SANDBOX=local", source="sandbox")
    except Exception as e:
        log.error(f"Error starting container: {e}", source="sandbox")


def _stop_container() -> None:
    """Stop and remove the sandbox container."""
    try:
        subprocess.run(
            ["docker", "rm", "-f", DOCKER_CONTAINER_NAME],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


def _container_has_mount(host_path: str) -> bool:
    """Check if the running container has `host_path` mounted at /workspace."""
    try:
        result = subprocess.run(
            ["docker", "inspect", "-f",
             "{{range .Mounts}}{{.Source}}:{{.Destination}} {{end}}",
             DOCKER_CONTAINER_NAME],
            capture_output=True, text=True, timeout=5,
        )
        mounts = result.stdout.strip()
        return f"{host_path}:{DOCKER_WORKDIR}" in mounts
    except Exception:
        return False