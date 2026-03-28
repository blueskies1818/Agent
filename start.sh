#!/usr/bin/env bash
# ── Agent Launcher ────────────────────────────────────────────────────────────
#
# Usage:
#   ./start.sh                                    # local mode, default workspace
#   PROJECT=/home/mint/my-app ./start.sh          # local mode, project directory
#   SANDBOX=docker ./start.sh                     # docker mode, default volume
#   PROJECT=/home/mint/my-app SANDBOX=docker ./start.sh  # docker + project sync
#   PROVIDER=claude TIER=fast ./start.sh          # override provider/tier
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# ── Colors ────────────────────────────────────────────────────────────────────
BOLD="\033[1m"
DIM="\033[2m"
GREEN="\033[32m"
YELLOW="\033[33m"
BLUE="\033[34m"
RED="\033[31m"
RESET="\033[0m"

info()  { echo -e "${BLUE}[start]${RESET} $1"; }
ok()    { echo -e "${GREEN}[start]${RESET} $1"; }
warn()  { echo -e "${YELLOW}[start]${RESET} $1"; }
err()   { echo -e "${RED}[start]${RESET} $1"; }

# ── 1. Environment file ──────────────────────────────────────────────────────
if [ ! -f .env ]; then
    if [ -f .env.example ]; then
        warn ".env not found — copying from .env.example"
        cp .env.example .env
        warn "Fill in your API keys in .env before continuing."
        exit 1
    else
        warn "No .env file found. API keys must be set as environment variables."
    fi
fi

if [ -f .env ]; then
    set -a
    source .env
    set +a
fi

# ── 2. Python virtual environment ────────────────────────────────────────────
VENV_DIR=".venv"

if [ ! -d "$VENV_DIR" ]; then
    info "Creating virtual environment..."
    python3 -m venv "$VENV_DIR"
    ok "Virtual environment created at $VENV_DIR/"
fi

source "$VENV_DIR/bin/activate"

# ── 3. Dependencies ──────────────────────────────────────────────────────────
REQ_HASH_FILE="$VENV_DIR/.requirements_hash"
CURRENT_HASH=$(md5sum requirements.txt 2>/dev/null | cut -d' ' -f1 || echo "none")
LAST_HASH=$(cat "$REQ_HASH_FILE" 2>/dev/null || echo "")

if [ "$CURRENT_HASH" != "$LAST_HASH" ]; then
    info "Installing dependencies..."
    pip install -q -r requirements.txt
    echo "$CURRENT_HASH" > "$REQ_HASH_FILE"
    ok "Dependencies installed."
else
    info "Dependencies up to date."
fi

# ── 4. Docker sandbox (if SANDBOX=docker) ────────────────────────────────────
SANDBOX_MODE="${SANDBOX:-local}"
PROJECT_DIR="${PROJECT:-}"
CONTAINER_NAME="agent-sandbox"
IMAGE_NAME="agent-sandbox"

if [ "$SANDBOX_MODE" = "docker" ]; then
    # Check Docker is installed
    if ! command -v docker &>/dev/null; then
        err "Docker not found. Install Docker or use SANDBOX=local"
        exit 1
    fi

    # Detect compose command — prefer v2 ("docker compose") over v1 ("docker-compose")
    if docker compose version &>/dev/null; then
        COMPOSE="docker compose"
    elif command -v docker-compose &>/dev/null; then
        warn "docker-compose v1 detected — this may cause errors."
        warn "Install docker compose v2: sudo apt-get install docker-compose-plugin"
        COMPOSE="docker-compose"
    else
        err "Neither 'docker compose' nor 'docker-compose' found."
        err "Install the compose plugin: sudo apt-get install docker-compose-plugin"
        exit 1
    fi

    # ── Build image if needed ─────────────────────────────────────────────
    IMAGE_EXISTS=$(docker images -q "$IMAGE_NAME" 2>/dev/null)
    if [ -z "$IMAGE_EXISTS" ]; then
        info "Building sandbox image (first time)..."
        docker build -t "$IMAGE_NAME" .
        ok "Image built: $IMAGE_NAME"
    else
        DOCKERFILE_HASH_FILE="$VENV_DIR/.dockerfile_hash"
        DF_CURRENT=$(md5sum Dockerfile 2>/dev/null | cut -d' ' -f1 || echo "none")
        DF_LAST=$(cat "$DOCKERFILE_HASH_FILE" 2>/dev/null || echo "")
        if [ "$DF_CURRENT" != "$DF_LAST" ]; then
            info "Dockerfile changed, rebuilding image..."
            docker build -t "$IMAGE_NAME" .
            echo "$DF_CURRENT" > "$DOCKERFILE_HASH_FILE"
            ok "Image rebuilt."
        fi
    fi

    # ── Start container ───────────────────────────────────────────────────
    RUNNING=$(docker inspect -f '{{.State.Running}}' "$CONTAINER_NAME" 2>/dev/null || echo "false")

    if [ "$RUNNING" = "true" ]; then
        # Check if the mount matches the current PROJECT_DIR
        if [ -n "$PROJECT_DIR" ]; then
            CURRENT_MOUNT=$(docker inspect -f '{{range .Mounts}}{{.Source}} {{end}}' "$CONTAINER_NAME" 2>/dev/null || echo "")
            if [[ "$CURRENT_MOUNT" != *"$PROJECT_DIR"* ]]; then
                warn "Project changed → recreating container..."
                docker rm -f "$CONTAINER_NAME" &>/dev/null || true
                RUNNING="false"
            fi
        fi
    fi

    if [ "$RUNNING" != "true" ]; then
        # Clean up any stopped container with the same name
        docker rm -f "$CONTAINER_NAME" &>/dev/null || true

        # Build the volume argument — always bind-mount a host directory
        if [ -n "$PROJECT_DIR" ]; then
            PROJECT_DIR="$(cd "$PROJECT_DIR" 2>/dev/null && pwd || echo "$PROJECT_DIR")"
            if [ ! -d "$PROJECT_DIR" ]; then
                err "Project directory does not exist: $PROJECT_DIR"
                exit 1
            fi
            HOST_DIR="$PROJECT_DIR"
            info "Mounting project: $HOST_DIR"
        else
            HOST_DIR="$SCRIPT_DIR/workspace"
            mkdir -p "$HOST_DIR"
            info "Mounting workspace: $HOST_DIR"
        fi
        VOLUME_ARG="$HOST_DIR:/workspace"

        docker run -d \
            --name "$CONTAINER_NAME" \
            --memory 1g \
            --cpus 1.0 \
            --pids-limit 512 \
            --cap-drop ALL \
            --cap-add CHOWN \
            --cap-add DAC_OVERRIDE \
            --cap-add FOWNER \
            --cap-add FSETID \
            --cap-add SETGID \
            --cap-add SETUID \
            --cap-add KILL \
            --cap-add NET_BIND_SERVICE \
            --security-opt no-new-privileges:true \
            -v "$VOLUME_ARG" \
            "$IMAGE_NAME" \
            >/dev/null

        ok "Sandbox container started."
    else
        ok "Sandbox container already running."
    fi

    # Show sandbox status
    if [ -n "$PROJECT_DIR" ]; then
        echo -e "${DIM}[sandbox] mode=docker  project=$PROJECT_DIR${RESET}"
    else
        echo -e "${DIM}[sandbox] mode=docker  workspace=$SCRIPT_DIR/workspace${RESET}"
    fi
else
    # Local mode
    if [ -n "$PROJECT_DIR" ]; then
        if [ ! -d "$PROJECT_DIR" ]; then
            err "Project directory does not exist: $PROJECT_DIR"
            exit 1
        fi
        echo -e "${DIM}[sandbox] mode=local  project=$PROJECT_DIR${RESET}"
    else
        mkdir -p workspace
        echo -e "${DIM}[sandbox] mode=local  workspace=./workspace${RESET}"
    fi
fi

# ── 5. Launch ─────────────────────────────────────────────────────────────────
echo ""
exec python main.py "$@"