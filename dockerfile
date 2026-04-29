# ── Agent Workspace Sandbox ───────────────────────────────────────────────────
#
# This container provides an isolated filesystem and process space for the
# agent's shell commands.  It runs idle and receives commands via `docker exec`.
#
# The agent code itself runs on the HOST — only shell execution happens here.
#
# The AI runs as root INSIDE the container so it can install packages, manage
# services, and configure the environment freely.  This is safe because:
#   - The only host path visible is the /workspace bind mount
#   - Dangerous capabilities (SYS_ADMIN, SYS_PTRACE, etc.) are dropped
#   - no-new-privileges prevents escalation
#   - The container has its own filesystem, process tree, and network
#
# Build:   docker build --no-cache -t agent-sandbox .

FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive
# Disable Firefox's internal sandbox — it needs SYS_ADMIN/user namespaces
# which we intentionally drop for container security.  Firefox still works
# fine without it since the Docker container IS the sandbox.
ENV MOZ_DISABLE_CONTENT_SANDBOX=1
ENV MOZ_DISABLE_SANDBOX=1

# ── Core tools ────────────────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
    bash \
    coreutils \
    findutils \
    grep \
    sed \
    gawk \
    file \
    tree \
    jq \
    zip \
    unzip \
    curl \
    wget \
    ca-certificates \
    git \
    python3 \
    python3-pip \
    python3-venv \
    nodejs \
    npm \
    xvfb \
    xdotool \
    imagemagick \
    x11-utils \
    # Firefox dependencies
    dbus \
    dbus-x11 \
    libgtk-3-0 \
    libdbus-glib-1-2 \
    # Keep apt lists so the AI can install packages at runtime with root
    && echo "apt lists preserved for runtime installs"

# ── Browser ───────────────────────────────────────────────────────────────────
# Ubuntu 24.04 ships Firefox as a snap, which doesn't work in Docker.
# Install from Mozilla's official APT repo instead.
RUN install -d -m 0755 /etc/apt/keyrings \
    && wget -q https://packages.mozilla.org/apt/repo-signing-key.gpg \
       -O /etc/apt/keyrings/packages.mozilla.org.asc \
    && echo "deb [signed-by=/etc/apt/keyrings/packages.mozilla.org.asc] https://packages.mozilla.org/apt mozilla main" \
       > /etc/apt/sources.list.d/mozilla.list \
    && printf 'Package: *\nPin: origin packages.mozilla.org\nPin-Priority: 1000\n' \
       > /etc/apt/preferences.d/mozilla \
    && apt-get update && apt-get install -y firefox

# ── Workspace ─────────────────────────────────────────────────────────────────
RUN mkdir -p /workspace

# ── Default umask ─────────────────────────────────────────────────────────────
# All files created inside the container (including via `docker exec bash -c`)
# get 0666/0777 permissions so the host user can read/write them on the
# bind-mounted workspace without having to sudo or chmod.
RUN echo 'umask 0000' > /etc/bash_env.sh
ENV BASH_ENV=/etc/bash_env.sh

# ── Idle entrypoint ───────────────────────────────────────────────────────────
WORKDIR /workspace
CMD ["tail", "-f", "/dev/null"]